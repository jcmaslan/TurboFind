# TurboFind

TurboFind is a local semantic search CLI designed for AI-assisted large codebase migrations. It uses a frontier LLM (Claude) to analyze the architectural intent and hidden coupling of source files at index time, then stores the results in a local vector database (usearch) for fast retrieval at query time.

This addresses a specific limitation of lexical search (`grep`, `ripgrep`): files that are structurally coupled but share no keyword overlap are invisible to text matching. TurboFind surfaces them by indexing *what the code does architecturally*, not just what it contains.

## Prerequisites
- Python 3.9+
- [Ollama](https://ollama.com) installed and running locally:
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
- Anthropic API Key

## Installation

1. Clone this repository or copy its contents.
2. Install the package locally:
   ```bash
   pip install -e .
   ```
3. Pull the embedding model:
   ```bash
   ollama pull nomic-embed-text
   ```
4. Set your API key:
   ```bash
   export ANTHROPIC_API_KEY="your-key-here"
   ```
5. (Optional) Override the Claude model or Ollama host:
   ```bash
   export TURBOFIND_MODEL="claude-sonnet-4-6-20260215"  # default: claude-haiku-4-5-20251001
   export OLLAMA_HOST="localhost:11434"               # default
   ```

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
Launch Claude Code and ask it to refactor structural elements. Claude will automatically use `tf-search` to understand the codebase and `tf-upsert` to keep the vector database perfectly synced as it makes changes.

## Commands

- `tf-init` — Initialize TurboFind in the current project (appends instructions to `CLAUDE.md`)
- `tf-init --remove` — Remove TurboFind instructions from `CLAUDE.md`
- `tf-search "<query>"` — Semantic intent search across the indexed codebase
- `tf-upsert <path>` — Index a file, directory, or glob pattern
- `tf-upsert . --dry-run` — Preview indexing without calling APIs
- `tf-upsert . --max-files 50` — Limit batch size
- `tf-upsert . --cost-limit 10.0` — Set cost confirmation threshold
