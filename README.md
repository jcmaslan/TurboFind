# TurboFind

TurboFind is a local semantic search CLI designed for AI-assisted large codebase migrations. It uses a frontier LLM (Claude) to analyze the architectural intent and hidden coupling of source files at index time, then stores the results in a local vector database (usearch) for fast retrieval at query time.

This addresses a specific limitation of lexical search (`grep`, `ripgrep`): files that are structurally coupled but share no keyword overlap are invisible to text matching. TurboFind surfaces them by indexing *what the code does architecturally*, not just what it contains.

## Prerequisites
- Python 3.9+
- [Ollama](https://ollama.com) installed and running locally
- Anthropic API Key

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/jcmaslan/TurboFind/main/scripts/install.sh | bash
```

This downloads TurboFind to `~/.turbofind`, installs the Python package, and pulls the embedding model if Ollama is running. Then set your API key:

```bash
export ANTHROPIC_API_KEY="your-key-here"
```

<details>
<summary>Manual installation</summary>

```bash
git clone https://github.com/jcmaslan/TurboFind.git
pip install -e TurboFind
ollama pull nomic-embed-text
export ANTHROPIC_API_KEY="your-key-here"
```
</details>

<details>
<summary>Optional environment overrides</summary>

```bash
export TURBOFIND_MODEL="claude-sonnet-4-6-20260215"  # default: claude-haiku-4-5-20251001
export OLLAMA_HOST="localhost:11434"                  # default
export TURBOFIND_HOME="/custom/install/path"          # default: ~/.turbofind
```
</details>

## Workflow

### 1. Initialize
In your project directory, run:
```bash
tf-init
```
This appends TurboFind's migration protocol instructions to `CLAUDE.md` (creating it if needed). Running it again is safe — it will not duplicate the instructions.

### 2. Index the Codebase
Build the initial semantic index for the codebase you are migrating:
```bash
tf-upsert .
```
This recursively finds all source files, filters through `.gitignore`, and indexes them with the batch guardrails applied.

Preview what would be indexed without calling any APIs:
```bash
tf-upsert . --dry-run
```

#### Optional: `repo_map.txt`
If you create a `repo_map.txt` in your project root describing the high-level architecture and service boundaries, TurboFind will include it as cached context in every Claude synthesis call. This improves Claude's ability to detect cross-service coupling but is not required — indexing works without it. See [demo_repo/repo_map.txt](demo_repo/repo_map.txt) for an example.

### 3. Let Claude Run
Launch Claude Code and ask it to refactor structural elements. Claude will automatically use `tf-search` to understand the codebase and `tf-upsert` to keep the vector database synced as Claude makes changes.

## Commands

- `tf-init` — Initialize TurboFind in the current project (appends instructions to `CLAUDE.md`)
- `tf-init --remove` — Remove TurboFind instructions from `CLAUDE.md` of the current project
- `tf-search "<query>"` — Semantic intent search across the indexed codebase
- `tf-upsert <path>` — Index a file, directory, or glob pattern
- `tf-upsert . --dry-run` — Preview indexing without calling APIs
- `tf-upsert . --max-files 50` — Limit batch size
- `tf-upsert . --cost-limit 10.0` — Set cost confirmation threshold
- `tf-viz` — Launch an interactive browser view of the current project's `graph.json` (nodes grouped by file, edges colored by `imports`/`calls`/`extends`). The launcher is stdlib-only; the in-browser viewer loads Cytoscape from a CDN on first use, so the page needs network access.

## A/B Testing

To compare Claude Code's file discovery performance with and without TurboFind, run the A/B test script from inside your indexed repo directory:

```bash
cd demo_repo && bash ../scripts/ab_test.sh
```

This runs Claude Code twice on the same prompt — once without TurboFind (standard tools only) and once with `tf-search` available — then scores both runs against a ground truth set of hard-to-find files and generates an evaluation report.

To run against your own codebase, create a `.ab_test.conf` file in your repo root with a custom prompt and ground truth:

```bash
PROMPT="Find all files involved in payment processing, including indirect dependencies."
GROUND_TRUTH=(
  "src/payments/stripe_client.py"
  "src/webhooks/invoice_handler.py"
  "src/utils/currency.py"
)
```

Then run `bash path/to/scripts/ab_test.sh` from your repo directory. The script loads `.ab_test.conf` if present, otherwise falls back to the demo defaults.
