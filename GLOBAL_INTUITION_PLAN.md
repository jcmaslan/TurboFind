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

### Tooling: `tf-verify`
We introduced the `tf-verify` CLI specifically designed for the frontier model to introspect data schemas:
- **Usage**: `tf-verify check-node <filepath> -n <node_id_or_path>`
- **Current behavior**: A simple placeholder that dumps the stored AST JSON for a file. The `--node` flag is accepted but not yet functional beyond displaying the full file AST. This stub will be replaced once the verification API protocol is decided.
- **Future Expansions**: This tool will eventually expand beyond strict syntax checking to validate abstract design patterns and cross-service relationships (like PubSub triggers or Redis coupling) allowing the model to perform high-resolution "pre-flight checks" prior to issuing edit prompts.

### Application Lifecycle (Prototype Phase)
As a functioning prototype, `tf-upsert` will synchronously rewrite or update the `graph.json` payload inside of upon every individual file generation loop. This ensures that cross-dependency maps inside a localized scope always represent the exact disk structure when Claude evaluates Test-Time contexts. 

## Workflow Integration
This methodology operates adjacent to the traditional search functionalities. A frontier agent acting as a developer is granted both:
- **Global Contextual Navigation**: Supplied passively inside `<global_ast_graph>`.
- **Local AST Pre-Commit Verification**: Supported by ad-hoc calls to `tf-verify`.
