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


# Framework factory functions that create instances used by decorators
# e.g., app = Flask(__name__) -> @app.route() uses 'app'
FRAMEWORK_FACTORIES = {
    # Flask
    "Flask",
    "Blueprint",
    # Celery
    "Celery",
    # FastAPI
    "FastAPI",
    "APIRouter",
    # Flask-RESTX
    "Api",
    "Namespace",
    # Common factory function names
    "create_app",
    "make_app",
    "create_celery",
    "make_celery",
    "app_factory",
}


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

        # For tracking current class's parent_classes (inheritance chain)
        # This allows methods to know what class they're defined in
        self.current_class_parents: list[str] = []
        self.class_parents_stack: list[list[str]] = []

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

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Extract the function name from a Call node."""
        match node.func:
            case ast.Name(id=name):
                return name
            case ast.Attribute(attr=attr):
                return attr
        return None

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
            parent_scope_type=self.current_scope.type,
            # For methods, inherit the containing class's parent_classes
            # This enables is_implicit_name() to detect Pydantic validators, etc.
            parent_classes=self.current_class_parents if is_method else [],
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

        # Visit type annotations (recursively to capture nested types like Optional[X])
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.annotation:
                self._visit_type_annotation(arg.annotation)

        if node.returns:
            self._visit_type_annotation(node.returns)

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
            parent_scope_type=self.current_scope.type,
        )
        self.definitions[qname] = symbol

        # Visit decorators
        for decorator in node.decorator_list:
            self._record_decorator_usage(decorator)

        # Record base class usages
        for base in node.bases:
            self._record_usage(base, UsageContext.INHERITANCE)

        # Visit class body in new scope
        # Save and set class parent_classes so methods can access them
        self.class_parents_stack.append(self.current_class_parents)
        self.current_class_parents = parent_classes

        self._push_scope(node.name, "class")

        for stmt in node.body:
            self.visit(stmt)

        self._pop_scope()

        # Restore previous class parent_classes
        self.current_class_parents = self.class_parents_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track variable assignments."""
        for target in node.targets:
            self._record_assignment(target)

            # Track registry patterns: HANDLERS['key'] = func
            # When assigning to a dict subscript, record the value as used
            if isinstance(target, ast.Subscript) and isinstance(node.value, ast.Name):
                self._record_usage(node.value, UsageContext.REFERENCE)

            # Track framework factory patterns: app = Flask(__name__)
            # These instances are implicitly used by decorators like @app.route()
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                factory_name = self._get_call_name(node.value)
                if factory_name in FRAMEWORK_FACTORIES:
                    # Record the variable as used since decorators will reference it
                    self.usages.append(
                        Usage(
                            symbol_name=target.id,
                            context=UsageContext.REFERENCE,
                            location=self._make_location(target),
                            caller=None,  # Module level
                        )
                    )

        # Visit the value for usages
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Track annotated assignments."""
        if node.target:
            self._record_assignment(node.target)
        if node.value:
            self.visit(node.value)
        # Visit the annotation to capture all nested types (e.g., Optional[SomeClass])
        if node.annotation:
            self._visit_type_annotation(node.annotation)

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
                        parent_scope_type=self.current_scope.type,
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
                parent_scope_type=self.current_scope.type,
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
                parent_scope_type=self.current_scope.type,
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
            # Check for Model.query pattern (Flask-SQLAlchemy)
            self._check_model_query_pattern(node)
            # Also record the base module/object as used
            # This handles `functools.wraps`, `typing.Union`, etc.
            if isinstance(node.value, ast.Name):
                # Record the module name itself as used (e.g., 'functools' in functools.wraps)
                self._record_usage(node.value, UsageContext.ATTRIBUTE)
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

        # Handle signal.connect(handler) patterns (Blinker, Flask signals)
        # This catches signal.connect(func) where func appears unused
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ("connect", "connect_via"):
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        self._record_usage(arg, UsageContext.REFERENCE)

            # Handle registry patterns: registry.register(func), handlers.append(func), etc.
            # These patterns register functions/classes that may appear unused
            if node.func.attr in ("register", "add", "append", "extend", "update", "include"):
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        self._record_usage(arg, UsageContext.REFERENCE)
                    elif isinstance(arg, ast.List):
                        # handlers.extend([func1, func2])
                        for elt in arg.elts:
                            if isinstance(elt, ast.Name):
                                self._record_usage(elt, UsageContext.REFERENCE)
                    elif isinstance(arg, ast.Dict):
                        # handlers.update({'key': func})
                        for value in arg.values:
                            if isinstance(value, ast.Name):
                                self._record_usage(value, UsageContext.REFERENCE)

        # Check for SQLAlchemy ORM patterns
        self._check_sqlalchemy_patterns(node)

        # Visit the function/attribute chain to properly traverse chained calls
        # e.g., User.query.filter_by().all() needs to visit User.query
        self.visit(node.func)

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

    def _visit_type_annotation(self, node: ast.expr) -> None:
        """
        Recursively visit type annotation to record all type references.

        Handles nested types like Optional[SomeClass], List[Dict[str, MyModel]], etc.
        This is critical for Pydantic models where field types reference other models.
        """
        match node:
            case ast.Name():
                # Simple type: SomeClass
                self._record_usage(node, UsageContext.TYPE_HINT)
            case ast.Attribute():
                # Qualified type: module.SomeClass
                self._record_usage(node, UsageContext.TYPE_HINT)
            case ast.Subscript(value=value, slice=slice_):
                # Generic type: Optional[X], List[Y], Dict[K, V]
                self._visit_type_annotation(value)
                # Handle the slice (type arguments)
                if isinstance(slice_, ast.Tuple):
                    # Multiple type args: Dict[str, int]
                    for elt in slice_.elts:
                        self._visit_type_annotation(elt)
                else:
                    # Single type arg: Optional[SomeClass]
                    self._visit_type_annotation(slice_)
            case ast.BinOp(left=left, right=right):
                # Union types with | syntax: int | str
                self._visit_type_annotation(left)
                self._visit_type_annotation(right)
            case ast.Constant():
                # String annotations like "ForwardRef" - skip, they're just strings
                pass
            case _:
                # Other nodes (None, Ellipsis, etc.) - skip
                pass

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

    # === SQLAlchemy ORM Pattern Detection ===

    def _check_model_query_pattern(self, node: ast.Attribute) -> None:
        """Check for Model.query.* pattern and record the Model as ORM-used."""
        # Looking for: ModelName.query or ModelName.query.filter(...)
        # Node structure for Model.query: Attribute(value=Name(ModelName), attr='query')
        if node.attr == "query" and isinstance(node.value, ast.Name):
            # Direct Model.query access - record Model as ORM usage
            self._record_orm_usage(node.value.id, node)

    def _check_sqlalchemy_patterns(self, node: ast.Call) -> None:
        """Check for SQLAlchemy ORM usage patterns in function calls."""
        # Pattern 1: session.query(Model) or db.session.query(Model)
        if self._is_session_query_call(node):
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    self._record_orm_usage(arg.id, node)

        # Pattern 2: relationship("ModelName") or relationship(ModelName)
        if self._is_relationship_call(node):
            self._extract_relationship_target(node)

        # Pattern 3: ForeignKey("tablename.field")
        if self._is_foreignkey_call(node):
            self._extract_foreignkey_target(node)

        # Pattern 4: backref("name")
        if self._is_backref_call(node):
            self._extract_backref_target(node)

    def _is_session_query_call(self, node: ast.Call) -> bool:
        """Check if this is a session.query() call."""
        match node.func:
            case ast.Attribute(attr="query", value=value):
                # session.query(...) or db.session.query(...)
                if isinstance(value, ast.Name) and value.id in ("session", "Session"):
                    return True
                if isinstance(value, ast.Attribute) and value.attr == "session":
                    return True
        return False

    def _is_relationship_call(self, node: ast.Call) -> bool:
        """Check if this is a relationship() call."""
        match node.func:
            case ast.Name(id="relationship"):
                return True
            case ast.Attribute(attr="relationship"):
                return True
        return False

    def _is_foreignkey_call(self, node: ast.Call) -> bool:
        """Check if this is a ForeignKey() call."""
        match node.func:
            case ast.Name(id="ForeignKey"):
                return True
            case ast.Attribute(attr="ForeignKey"):
                return True
        return False

    def _is_backref_call(self, node: ast.Call) -> bool:
        """Check if this is a backref() call."""
        match node.func:
            case ast.Name(id="backref"):
                return True
            case ast.Attribute(attr="backref"):
                return True
        return False

    def _extract_relationship_target(self, node: ast.Call) -> None:
        """Extract model name from relationship() call."""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                # relationship("ModelName")
                self._record_orm_usage(arg.value, node)
            elif isinstance(arg, ast.Name):
                # relationship(ModelName)
                self._record_orm_usage(arg.id, node)

        # Also check for backref keyword argument: relationship("Model", backref="name")
        for kw in node.keywords:
            if kw.arg == "backref":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    # backref="name"
                    self._record_orm_usage(kw.value.value, node)

    def _extract_foreignkey_target(self, node: ast.Call) -> None:
        """Extract table name from ForeignKey() call."""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                # ForeignKey("tablename.field") - extract tablename
                table_ref = arg.value.split(".")[0]
                self._record_orm_usage(table_ref, node)

    def _extract_backref_target(self, node: ast.Call) -> None:
        """Extract backref name."""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                # backref("name") - record the backref name
                self._record_orm_usage(arg.value, node)

    def _record_orm_usage(self, name: str, node: ast.AST) -> None:
        """Record an ORM usage."""
        self.usages.append(
            Usage(
                symbol_name=name,
                context=UsageContext.ORM_REFERENCE,
                location=self._make_location(node),
                caller=self._get_current_caller(),
            )
        )


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
