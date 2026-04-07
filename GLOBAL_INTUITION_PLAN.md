# Global Intuition and Test-Time Training Plan

This document serves as the primary record of the architectural intent behind integrating explicit AST Graph capabilities into TurboFind. Our goal is to leverage `tree-sitter` to supply a structural graph of the codebase ("Global Intuition") to frontier models, enabling robust Test-Time Training and Local Verification capabilities.

> **Prototype note:** This prototype uses prompt caching to simulate supplying global structural context to a future TTT-capable model. The graph format and extraction pipeline are designed to be TTT-ready; the current delivery mechanism is prompt injection as a stand-in. References to "Test-Time Training" below describe the intended end-state, not the current implementation.

## Architecture Overview

1. **AST Graph Extraction (`tf-upsert`)**:
   - `tf-upsert` directly parses each source file using `tree-sitter` bindings.
   - **Supported languages (prototype):** Python, Java, JavaScript, and TypeScript (via `tree-sitter-python`, `tree-sitter-java`, `tree-sitter-javascript`, `tree-sitter-typescript`). Files in other languages are indexed for semantic search but do not contribute AST entries to the graph.
   - The AST represents structural knowledge (classes, function boundaries, imports).
   - We strip out deep token noise to maintain an information density conducive to context limits.

2. **Supplying the Global Intuition via Prompt Caching**:
   - `tf-upsert` dynamically collects these file-level AST structures and synchronizes them into a global `graph.json`.
   - The compiled global graph is injected as a `<system>` block text segment wrapped in a `<global_ast_graph>` tag with `cache_control: { type: "ephemeral" }` applied during all Claude API inquiries.
   - The synthesis system prompt should instruct the model to treat `<global_ast_graph>` as a supplemental global view of the system — use it as a starting point when exploring cross-file relationships, import chains, and structural dependencies before diving into the source code of any individual file.
   - This provides the model the overarching structure required for deep Test-Time Training navigation and reasoning.

3. **Local Verification (`tf-verify`)**:
   - The "Global Intuition" provides planning power, but before executing modifications, the frontier model must locally verify the AST integrity to avoid blind diff errors.
   - We utilize a new tool `tf-verify` for the model to investigate and interrogate local file states programmatically.

## Implementation Details

### Depth Management and Token Constraints
The AST can expand rapidly in memory. To prevent overflowing prompt-cached limits, we enforce maximum threshold limits out of the box:
- `graph_max_tokens`: Configured with a default of 128K to cap total graph footprint. Users set this single knob; depth is derived automatically.
- `compute_max_depth(graph, budget)`: A heuristic function that returns the appropriate `max_depth` for the next file based on remaining token budget. In the prototype this returns the configured `max_depth` while under budget and `0` (skip AST extraction) once the budget is reached. A future TTT-capable model would dynamically determine the optimal depth based on its own context capacity and the structural importance of each file.

### Tooling: `tf-verify` (Verification Oracle)
`tf-verify` is the ground-truth supervisor for the topology graph. It provides three subcommands:

- **`tf-verify check-node <filepath> [-n <name>] [--json]`**: Displays topology nodes and their typed edges for a given file. When `--node` is provided, filters by substring match against definition IDs.

- **`tf-verify query <predicate> [args] [--json]`**: Queries the topology graph. Available predicates:
  | Predicate | Usage | Returns |
  |-----------|-------|---------|
  | `calls <A> <B>` | Check if A calls/imports B | Boolean + edge details |
  | `callers-of <node>` | Who calls/imports this node? | List of caller nodes with edge types |
  | `deps-of <file>` | What files does this file depend on? | List of dependency files |
  | `dependents-of <file>` | What files depend on this file? | List of dependent files |
  | `impact <node>` | Transitive blast radius | All transitively affected nodes and files |
  | `path <A> <B>` | Shortest dependency path | Ordered node path |

- **`tf-verify assert <predicate> [args]`**: Same as `query` but returns exit code 0 (true), 1 (false), or 2 (error). This is the supervision primitive — it makes ground-truth checks scriptable and composable.

### Edge Types
The topology graph captures three types of structural relationships:
- **`calls`**: Function/method call edges (best-effort, unambiguous name matches only)
- **`imports`**: Import edges from Python `from ... import`, JS/TS `import { } from`, and Java `import` (resolved to definition nodes)
- **`extends`**: Inheritance edges from class definitions to their base classes

### Ground-Truth Supervision
The graph acts as a ground-truth supervisor for the model's structural claims. The verification oracle enables active checking of claims against the symbolic graph:
1. The model's synthesis claims (`<hidden_coupling>`, `<core_intent>`) are written to be precise enough to verify against the graph.
2. The `tf-verify` predicates are the backend for the future Verification API — the model does not call `tf-verify` directly. Instead, the TTT loop's Verification API will invoke these predicates to validate the model's structural understanding.
3. The `assert` subcommand enables scripted verification of structural invariants (e.g., in CI or pre-commit hooks).

In the prompt-caching TTT simulation, this supervision loop will operate through the Verification API layer rather than direct tool calls. The graph is the oracle; `tf-verify` provides the query primitives that the API consumes.

### Application Lifecycle (Prototype Phase)
`tf-upsert` synchronously rebuilds `graph.json` during every upsert cycle (Phase 1). `tf-upsert --graph-only` builds the graph without synthesis/embedding (fast, no API calls). This ensures that cross-dependency maps always represent the exact disk structure when Claude evaluates Test-Time contexts.

## Workflow Integration
This methodology operates adjacent to the traditional search functionalities. A frontier agent acting as a developer is granted both:
- **Global Contextual Navigation**: Supplied passively inside `<global_ast_graph>` (definitions, call/import/extends edges).
- **Active Structural Verification**: Supported by `tf-verify query` and `tf-verify assert` for pre-edit validation and ground-truth supervision.
