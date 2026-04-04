<!-- turbofind -->
## TurboFind Migration Protocol

You have access to two semantic search tools. Use them directly in the terminal:

- `tf-search "<query>"` — Semantic vector search across the indexed codebase
- `tf-upsert <filepath>` — Updates the semantic index after an edit

### PRE-EDIT RULE (Investigation)
When investigating the codebase, planning a migration, or locating dependencies:
Execute `tf-search "<semantic intent>"` FIRST, then proceed with your normal workflow.

### POST-EDIT RULE (Synchronization)
After modifying, refactoring, or creating any file:
IMMEDIATELY execute `tf-upsert <filepath>` before your next step.
<!-- /turbofind -->