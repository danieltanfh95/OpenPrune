help me design an interactive cli application that prunes old code from python. first lets explore ways to do this. 

problem statememt is that we have a very large and old flask application. 

my initial design is to first analyse the repo for the type of application (like flask+celery) then from there find commonly used entrypoints like src/app.py and src/task.py. then we also gather linting used etc. the initial data (detected_app_types, entrypoints, ignore_markers like #noqa etc) collected is then output into a open-prune.json that can be edited or re-run in the future. if the user accepts the open-prune.json change then the application begins. 

next we use the entrypoints + ast to build a dependency tree of imports and all functions so we can pinpoint orphaned imports and functions/class methods/classes/files. if we need to, we can mark each node with a suspicion score. this tree is then dumped to a results file (not sure how to structure it for now) so we can re-run this step in the future / offer it to plugins to work with in the future.