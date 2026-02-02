"""AST visitor for collecting symbol definitions and usages."""

import ast
import tokenize
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from openprune.models.dependency import (
    ImportInfo,
    Location,
    Symbol,
    SymbolType,
    Usage,
    UsageContext,
)


@dataclass
class ScopeInfo:
    """Information about the current scope."""

    name: str
    type: str  # "module", "class", "function"
    parent: "ScopeInfo | None" = None

    @property
    def qualified_name(self) -> str:
        parts: list[str] = []
        scope: ScopeInfo | None = self
        while scope:
            if scope.name:
                parts.append(scope.name)
            scope = scope.parent
        return ".".join(reversed(parts))


@dataclass
class FileAnalysisResult:
    """Result of analyzing a single file."""

    definitions: dict[str, Symbol] = field(default_factory=dict)
    usages: list[Usage] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    line_comments: dict[int, str] = field(default_factory=dict)
    error: str | None = None


def extract_line_comments(source: str) -> dict[int, str]:
    """Extract comments by line number using Python's tokenizer."""
    comments: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string
    except tokenize.TokenizeError:
        pass
    return comments


class DeadCodeVisitor(ast.NodeVisitor):
    """
    AST visitor that collects both definitions and usages.

    Key insight from Vulture: track definitions and usages separately,
    then compare at the end. This is simpler than trying to resolve
    references during the visit.
    """

    def __init__(self, file_path: Path, module_name: str) -> None:
        self.file_path = file_path
        self.module_name = module_name

        # Scope tracking (stack-based like deadcode)
        self.scope_stack: list[ScopeInfo] = [ScopeInfo(name=module_name, type="module")]

        # Collected data
        self.definitions: dict[str, Symbol] = {}
        self.usages: list[Usage] = []
        self.imports: list[ImportInfo] = []

        # For tracking local variables vs globals
        self.local_names: set[str] = set()
        self.local_names_stack: list[set[str]] = []

    @property
    def current_scope(self) -> ScopeInfo:
        return self.scope_stack[-1]

    def _make_location(self, node: ast.AST) -> Location:
        return Location(
            file=self.file_path,
            line=node.lineno,
            column=node.col_offset,
            end_line=getattr(node, "end_lineno", None),
            end_column=getattr(node, "end_col_offset", None),
        )

    def _qualified_name(self, name: str) -> str:
        """Build qualified name from current scope."""
        if self.current_scope.qualified_name:
            return f"{self.current_scope.qualified_name}.{name}"
        return name

    def _get_decorators(self, node: ast.FunctionDef | ast.ClassDef) -> list[str]:
        """Extract decorator names as strings."""
        decorators: list[str] = []
        for dec in node.decorator_list:
            try:
                decorators.append(ast.unparse(dec))
            except Exception:
                decorators.append("<unknown>")
        return decorators

    def _push_scope(self, name: str, scope_type: str) -> None:
        """Push a new scope onto the stack."""
        self.scope_stack.append(
            ScopeInfo(name=name, type=scope_type, parent=self.current_scope)
        )
        self.local_names_stack.append(self.local_names.copy())
        self.local_names = set()

    def _pop_scope(self) -> None:
        """Pop the current scope from the stack."""
        self.scope_stack.pop()
        if self.local_names_stack:
            self.local_names = self.local_names_stack.pop()

    # === Definition Visitors ===

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        is_method = self.current_scope.type == "class"
        decorators = self._get_decorators(node)

        # Determine symbol type
        if is_method:
            sym_type = SymbolType.METHOD
        else:
            sym_type = SymbolType.FUNCTION

        qname = self._qualified_name(node.name)
        symbol = Symbol(
            name=node.name,
            qualified_name=qname,
            type=sym_type,
            location=self._make_location(node),
            scope=self.current_scope.qualified_name,
            decorators=decorators,
            is_dunder=node.name.startswith("__") and node.name.endswith("__"),
            is_private=node.name.startswith("_") and not node.name.startswith("__"),
        )
        self.definitions[qname] = symbol

        # Visit decorators for usages before entering function scope
        for decorator in node.decorator_list:
            self._record_decorator_usage(decorator)

        # Visit function body in new scope
        self._push_scope(node.name, "function")

        # Record parameter names as local (they're defined by the function)
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.arg not in ("self", "cls"):
                self.local_names.add(arg.arg)

        if node.args.vararg:
            self.local_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.local_names.add(node.args.kwarg.arg)

        # Visit type annotations
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.annotation:
                self._record_usage(arg.annotation, UsageContext.TYPE_HINT)

        if node.returns:
            self._record_usage(node.returns, UsageContext.TYPE_HINT)

        # Visit body
        for stmt in node.body:
            self.visit(stmt)

        self._pop_scope()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        # Treat same as FunctionDef
        self.visit_FunctionDef(node)  # type: ignore

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Extract parent class names
        parent_classes = []
        for base in node.bases:
            base_name = self._get_base_name(base)
            if base_name:
                parent_classes.append(base_name)

        qname = self._qualified_name(node.name)
        symbol = Symbol(
            name=node.name,
            qualified_name=qname,
            type=SymbolType.CLASS,
            location=self._make_location(node),
            scope=self.current_scope.qualified_name,
            decorators=self._get_decorators(node),
            parent_classes=parent_classes,
        )
        self.definitions[qname] = symbol

        # Visit decorators
        for decorator in node.decorator_list:
            self._record_decorator_usage(decorator)

        # Record base class usages
        for base in node.bases:
            self._record_usage(base, UsageContext.INHERITANCE)

        # Visit class body in new scope
        self._push_scope(node.name, "class")

        for stmt in node.body:
            self.visit(stmt)

        self._pop_scope()

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track variable assignments."""
        for target in node.targets:
            self._record_assignment(target)

        # Visit the value for usages
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Track annotated assignments."""
        if node.target:
            self._record_assignment(node.target)
        if node.value:
            self.visit(node.value)
        # Type annotation is also a usage
        self._record_usage(node.annotation, UsageContext.TYPE_HINT)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Track augmented assignments (+=, -=, etc.)."""
        # The target is both read and written
        self._record_usage(node.target, UsageContext.REFERENCE)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        """Track for loop variables."""
        self._record_assignment(node.target)
        self.visit(node.iter)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_With(self, node: ast.With) -> None:
        """Track with statement variables."""
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._record_assignment(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Track exception handler variables."""
        if node.type:
            self._record_usage(node.type, UsageContext.REFERENCE)
        if node.name:
            self.local_names.add(node.name)
        for stmt in node.body:
            self.visit(stmt)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Track comprehension variables."""
        self._record_assignment(node.target)
        self.visit(node.iter)
        for if_clause in node.ifs:
            self.visit(if_clause)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Track list comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Track set comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Track dict comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.key)
        self.visit(node.value)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        """Track generator expression."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def _record_assignment(self, target: ast.expr) -> None:
        """Record a variable assignment as a definition."""
        match target:
            case ast.Name(id=name):
                if self.current_scope.type == "module":
                    qname = self._qualified_name(name)
                    # Check if it looks like a constant (ALL_CAPS)
                    sym_type = SymbolType.CONSTANT if name.isupper() else SymbolType.VARIABLE
                    self.definitions[qname] = Symbol(
                        name=name,
                        qualified_name=qname,
                        type=sym_type,
                        location=self._make_location(target),
                        scope=self.current_scope.qualified_name,
                    )
                else:
                    self.local_names.add(name)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                for elt in elts:
                    self._record_assignment(elt)
            case ast.Starred(value=value):
                self._record_assignment(value)

    # === Import Visitors ===

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            qname = self._qualified_name(name)

            self.imports.append(
                ImportInfo(
                    module=alias.name,
                    name=None,
                    alias=alias.asname,
                    location=self._make_location(node),
                    is_relative=False,
                    level=0,
                )
            )

            self.definitions[qname] = Symbol(
                name=name,
                qualified_name=qname,
                type=SymbolType.IMPORT,
                location=self._make_location(node),
                scope=self.current_scope.qualified_name,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                # Star import - can't track individual names
                continue

            name = alias.asname or alias.name
            qname = self._qualified_name(name)

            self.imports.append(
                ImportInfo(
                    module=node.module or "",
                    name=alias.name,
                    alias=alias.asname,
                    location=self._make_location(node),
                    is_relative=node.level > 0,
                    level=node.level,
                )
            )

            self.definitions[qname] = Symbol(
                name=name,
                qualified_name=qname,
                type=SymbolType.IMPORT,
                location=self._make_location(node),
                scope=self.current_scope.qualified_name,
            )

    # === Usage Visitors ===

    def visit_Name(self, node: ast.Name) -> None:
        """Track name usages (not definitions)."""
        match node.ctx:
            case ast.Load() | ast.Del():
                if node.id not in self.local_names:
                    self._record_usage(node, UsageContext.REFERENCE)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Track attribute access."""
        if isinstance(node.ctx, ast.Load):
            self._record_usage(node, UsageContext.ATTRIBUTE)
        # Also visit the value (e.g., for x.y.z, visit x.y)
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        """Track function/method calls."""
        self._record_usage(node.func, UsageContext.CALL)

        # Handle getattr/hasattr/setattr
        if isinstance(node.func, ast.Name):
            if node.func.id in ("getattr", "hasattr", "setattr", "delattr"):
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    # Record the attribute name as used
                    self.usages.append(
                        Usage(
                            symbol_name=str(node.args[1].value),
                            context=UsageContext.ATTRIBUTE,
                            location=self._make_location(node.args[1]),
                            caller=self._get_current_caller(),
                        )
                    )

        # Visit arguments
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Track subscript access (e.g., dict[key])."""
        self.visit(node.value)
        self.visit(node.slice)

    def _get_current_caller(self) -> str | None:
        """Get qualified name of current function/method scope."""
        for scope in reversed(self.scope_stack):
            if scope.type in ("function", "method"):
                return scope.qualified_name
        return None  # Module-level code

    def _record_usage(self, node: ast.expr, context: UsageContext) -> None:
        """Record a usage of a name."""
        name = self._extract_name(node)
        if name:
            self.usages.append(
                Usage(
                    symbol_name=name,
                    context=context,
                    location=self._make_location(node),
                    caller=self._get_current_caller(),
                )
            )

    def _record_decorator_usage(self, decorator: ast.expr) -> None:
        """Record usages from a decorator."""
        match decorator:
            case ast.Call(func=func, args=args, keywords=keywords):
                self._record_usage(func, UsageContext.DECORATOR)
                for arg in args:
                    self.visit(arg)
                for kw in keywords:
                    self.visit(kw.value)
            case ast.Attribute() | ast.Name():
                self._record_usage(decorator, UsageContext.DECORATOR)

    def _extract_name(self, node: ast.expr) -> str | None:
        """Extract the name from various AST nodes."""
        match node:
            case ast.Name(id=name):
                return name
            case ast.Attribute(attr=attr):
                # For x.y.z, return "z" (the attribute)
                return attr
            case _:
                return None

    def _get_base_name(self, node: ast.expr) -> str:
        """Extract full base class name from AST node."""
        match node:
            case ast.Name(id=name):
                return name
            case ast.Attribute():
                # Build full dotted name for things like db.Model
                parts: list[str] = []
                current: ast.expr = node
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                return ".".join(reversed(parts))
        return ""


def analyze_file(file_path: Path, module_name: str | None = None) -> FileAnalysisResult:
    """Analyze a single Python file."""
    if module_name is None:
        module_name = file_path.stem

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))

        # Extract comments for noqa filtering
        line_comments = extract_line_comments(source)

        visitor = DeadCodeVisitor(file_path, module_name)
        visitor.visit(tree)

        return FileAnalysisResult(
            definitions=visitor.definitions,
            usages=visitor.usages,
            imports=visitor.imports,
            line_comments=line_comments,
        )
    except SyntaxError as e:
        return FileAnalysisResult(error=f"Syntax error at line {e.lineno}: {e.msg}")
    except UnicodeDecodeError as e:
        return FileAnalysisResult(error=f"Unicode decode error: {e}")
    except Exception as e:  # noqa: BLE001
        return FileAnalysisResult(error=f"Error: {e}")
