<!-- turbofind -->
## TurboFind Migration Protocol

You have access to semantic search tools. Use them directly in the terminal:

- `tf-search "<query>"` — Semantic vector search across the indexed codebase
- `tf-upsert <filepath>` — Updates the semantic index after an edit or creation
- `tf-upsert --remove <filepath>` — Removes a deleted file from the index

### PRE-EDIT RULE (Investigation)
When investigating the codebase, planning a migration, or locating dependencies:
1. Read `.turbofind/graph.json` to understand the structural layout of the codebase (classes, functions, imports, and their relationships).
2. Execute `tf-search "<semantic intent>"` to find relevant files. The graph context can help you refine your queries with specific class names, function signatures, or import chains when appropriate.

### POST-EDIT RULE (Synchronization)
After modifying, refactoring, or creating any file:
IMMEDIATELY execute `tf-upsert <filepath>` before your next step.

### POST-DELETE RULE (Cleanup)
After deleting any file:
IMMEDIATELY execute `tf-upsert --remove <filepath>` before your next step.
<!-- /turbofind -->