# TurboFind Extensibility Design: Multi-Index Architecture

## Vision

TurboFind initially answers one question well: *"Which files are architecturally related to X?"* via a single usearch index built from Claude-synthesized intent.

But the primary objective is broader. An AI agent working daily on a large codebase accumulates insights that are expensive to rediscover: why a bug happened, which files have surprising coupling, what an architectural decision's real motivation was. Today these insights evaporate when the conversation ends. TurboFind should create a **persistent, compounding knowledge layer** that captures them.

This is conceptually similar to the [llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — where an LLM incrementally builds a structured knowledge base rather than rediscovering everything from scratch. TurboFind adapts this idea to code: instead of a wiki of markdown pages, we maintain a collection of **semantic indexes**, each capturing a different dimension of codebase understanding.

---

## Core Concept: Named Indexes

Today TurboFind has one implicit index (`.turbofind.usearch` + `.turbofind.meta.json`). The extension is to support **named indexes**, each with:

- A **name** (e.g., `code-intent`, `debug-insights`, `coupling-map`)
- A **synthesis prompt** that defines how raw input is distilled before embedding
- A **schema** describing what metadata each entry carries
- Its own **usearch index file** and **metadata JSON**

```
.turbofind/
  indexes/
    code-intent/
      index.usearch
      meta.json
      prompt.md          # synthesis prompt for this index
    debug-insights/
      index.usearch
      meta.json
      prompt.md
    coupling-map/
      index.usearch
      meta.json
      prompt.md
  config.toml            # global config + per-index overrides
  lock                   # shared lock file
```

### CLI Surface

The CLI stays as distinct commands (`tf-search`, `tf-upsert`) but gains an `--index` flag:

```bash
# Current behavior (default index = code-intent)
tf-search "authorization logic"

# Query a specific index
tf-search --index debug-insights "race condition in token refresh"

# Query all indexes and merge results
tf-search --index all "session handling"

# Upsert to a specific index
tf-upsert --index debug-insights --input insight.md

# List available indexes
tf-index list

# Create a new index with a custom synthesis prompt
tf-index create coupling-map --prompt coupling_prompt.md
```

---

## Index Types: What Gets Indexed and Why

### 1. Code Intent (current — `code-intent`)

**What it captures:** Architectural function of source files — what the code *does*, its hidden coupling, migration risk.

**Source:** Source code files, synthesized by Claude.

**When it's built:** `tf-upsert .` during initial setup or CI.

**Who queries it:** Claude Code's Explore/Find subagents when investigating "which files relate to X?"

This is the existing index. It becomes the default named index.

### 2. Debug Insights (`debug-insights`)

**What it captures:** Root cause analyses, surprising behaviors, non-obvious failure modes discovered during debugging sessions.

**Source:** Summaries written by Claude Code at the end of a debug session (or extracted from conversation context).

**Example entry:**
```
file: services/gateway/rate_limiter.py
insight: Rate limiter silently exempts requests with X-Internal-Service
         header. This means any service-to-service call bypasses rate
         limits entirely. Root cause of the 2024-03 billing spike was
         analytics service hammering billing API through the gateway
         with this header set.
severity: high
session: 2026-04-03
```

**When it's built:** Incrementally, after debugging sessions. The agent (or a hook) runs:
```bash
tf-upsert --index debug-insights --input - <<< "$INSIGHT_SUMMARY"
```

**Who queries it:** Claude Code when starting a new debug session — "has anyone seen this pattern before?" — or when assessing risk of a change.

### 3. Coupling Map (`coupling-map`)

**What it captures:** Observed runtime and data dependencies between files/services that aren't visible in import graphs — including system-level coupling that spans infrastructure, not just source code.

**Source:** Derived from debug insights, integration test failures, explicit agent analysis, and infrastructure configs (Terraform, K8s manifests, docker-compose, API gateway configs).

**Coupling types:**

| Medium | Example | Why it's invisible to code analysis |
|:---|:---|:---|
| Message bus (PubSub/Kafka/RabbitMQ) | Service A publishes to `user.updated` topic, Service B subscribes | No import chain, possibly different repos |
| Task queue (Celery/Cloud Tasks/SQS) | Service A enqueues `reconcile_billing`, worker in Service B processes it | Coupled through task name string, no shared code |
| Shared state (Redis/DB) | `token_reader.py` reads Redis keys written by `auth_service.py` | Coupled through key schema, no import relationship |
| Implicit API contract (REST/gRPC) | Service A calls Service B at `/internal/validate` with an undocumented payload shape | No shared schema or protobuf — coupled by convention |
| Config-driven routing | Terraform/K8s config wires Service A's output to Service B's input | Coupling lives in infra config, not application code |

**Example entries:**
```
# Source-level coupling
source: services/billing/token_reader.py
target: services/auth/auth_service.py
medium: redis
coupling: token_reader reads Redis keys written by auth_service.
          No import relationship. Coupled through shared Redis
          key schema (user:{uid}:token_state).

# System-level coupling
source: services/analytics/event_publisher.py
target: services/billing/usage_worker.py
medium: pubsub
topic: analytics.usage-events
coupling: analytics publishes raw usage events; billing subscribes
          and aggregates them into invoiceable line items. Coupled
          through the event schema (user_id, resource_type, quantity).
          Schema is implicit — no shared protobuf.
```

**When it's built:** Incrementally as coupling is discovered. Can also be batch-seeded by having Claude analyze infrastructure configs alongside source code.

**Who queries it:** Claude Code when planning a refactor — "what will break if I change this file?" — or when debugging cross-service failures where the dataflow is asynchronous.

**Design implication:** Entries in this index aren't anchored to a single source file. They describe a **relationship between two endpoints** with the medium in between. The synthesis prompt extracts: source, target, medium, directionality, and the schema or contract they share. `tf-upsert` for this index should accept infrastructure configs (Terraform, K8s YAML, docker-compose) via `--input`, not just source code files.

### 4. Decision Log (`decision-log`)

**What it captures:** Architectural decisions and their rationale — the *why* behind code that looks wrong or surprising.

**Source:** Extracted from PR descriptions, code review comments, or explicit developer input.

**Example entry:**
```
file: services/auth/state_utils.py
decision: Uses pickle serialization instead of JSON for identity
          objects. This is intentional — the legacy IDP requires
          pickle-compatible payloads. Do not convert to JSON until
          IDP migration (tracked in JIRA AUTH-2847).
date: 2026-01-15
```

**When it's built:** Incrementally as decisions are made or discovered.

**Who queries it:** Claude Code before suggesting refactors — "is there a known reason this code looks this way?"

### 5. User-Defined Indexes

Users can create custom indexes with their own synthesis prompts for domain-specific needs: security audit findings, performance hotspots, API contract documentation, etc.

---

## How Insights Flow In

The key design question: how do insights get from a Claude Code session into a TurboFind index?

### Option A: Explicit CLI (immediate)

The agent or user manually upsets an insight:

```bash
tf-upsert --index debug-insights --input insight.md
tf-upsert --index debug-insights --input - <<< "the rate limiter bypasses..."
```

This is the simplest path and works today with minimal changes. The `--input` flag accepts a file or stdin instead of scanning source files.

### Option B: Session Hooks (near-term)

Claude Code hooks can trigger at session end:

```json
{
  "hooks": {
    "post_session": [
      "tf-learn --index debug-insights"
    ]
  }
}
```

`tf-learn` would:
1. Read the session transcript/summary
2. Use Claude to extract indexable insights
3. Upsert them to the specified index

### Option C: CLAUDE.md Protocol (near-term)

Extend the TurboFind protocol block in CLAUDE.md to instruct Claude Code when and how to persist learnings:

```markdown
## Persisting Insights

After completing a debugging session where the root cause was non-obvious,
summarize the key finding and run:

    tf-upsert --index debug-insights --input - <<< "<your summary>"

After discovering coupling between files that isn't visible in imports:

    tf-upsert --index coupling-map --input - <<< "<coupling description>"
```

This is the most natural path for Claude Code — it already reads CLAUDE.md and follows tool protocols.

### Option D: Session Hooks with `tf-learn` (near-term)

Claude Code hooks provide a concrete mechanism for automatic insight capture. The `Stop` hook fires every time Claude finishes responding, and receives `transcript_path` — a JSONL file containing the full conversation.

**Hook configuration** (`.claude/settings.json`):
```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "tf-learn --transcript \"$TRANSCRIPT_PATH\"",
        "async": true
      }]
    }]
  }
}
```

**What `tf-learn` does:**
1. Reads the transcript JSONL
2. Uses a cheap/fast model (Haiku) to classify: "did this session produce indexable insights?" — most sessions won't, so this must be cheap
3. If yes, extracts structured insights (debug findings, coupling discoveries, architectural decisions)
4. Upserts them to the appropriate index(es)
5. If no, exits early

**Debouncing:** The `Stop` hook fires per *turn*, not per session. To avoid redundant work:
- `tf-learn` records a watermark (last transcript offset processed) in `.turbofind/learn_state.json`
- On each invocation, it only analyzes the delta since the last run
- It skips entirely if fewer than N new assistant turns have occurred since last extraction

**Limitations of the hook approach:**
- Hooks cannot filter by conversation *content* — only by tool name (via `matcher`). So `tf-learn` must do its own relevance classification after reading the transcript.
- `PostToolUse` hooks do NOT receive tool output, only tool name and input. Reading the transcript file is the only way to see what actually happened.
- The transcript file may be large for long sessions. `tf-learn` should seek to the watermark, not re-read from the beginning.

**Alternative: Prompt-based hook** — Claude Code also supports `"type": "prompt"` hooks that ask an LLM to evaluate whether the hook should proceed. This could replace the Haiku classification step inside `tf-learn`, but adds latency to every turn.

---

## Search: Multi-Index Query Merging

When `tf-search --index all` queries multiple indexes, results need merging:

### Strategy: Weighted Union

Each index has a configurable **weight** (default 1.0). Results from all indexes are:

1. Queried independently (same embedding, each index searched)
2. Scores normalized per-index (since different indexes may have different score distributions)
3. Merged by weighted score, deduped by file path (highest score wins)
4. Filtered by the standard floor + elbow method

```toml
# .turbofind/config.toml
[indexes.code-intent]
weight = 1.0

[indexes.debug-insights]
weight = 1.5  # boost debug insights — they're expensive to rediscover

[indexes.coupling-map]
weight = 1.2
```

### Strategy: Context-Aware Routing (future)

Instead of querying all indexes, the agent's query is classified and routed to the most relevant index(es):

- "Which files handle auth?" → `code-intent`
- "Has this race condition been seen before?" → `debug-insights`
- "What breaks if I change token_reader?" → `coupling-map`

This could be keyword-based initially, then LLM-classified later.

### Typed Result Output

Different index types produce results that Claude Code should act on differently. Each result carries a `kind` field that signals what the result is and how to use it.

| Kind | Source Index | What it tells Claude Code | Expected agent action |
|:---|:---|:---|:---|
| `file` | code-intent | "This file is relevant — read it" | Open and read the file |
| `insight` | debug-insights | "This is a known finding — use it directly" | Incorporate into analysis without re-reading source |
| `coupling` | coupling-map | "These two endpoints are connected via this medium" | Consider both sides when planning changes |
| `decision` | decision-log | "There's a known reason this code looks this way" | Respect the constraint before suggesting refactors |

**Output format** (default, human-readable):
```
[file]     services/auth/auth_service.py:1-45  (0.92)
           Core authentication service — issues and validates JWT tokens.

[insight]  services/gateway/rate_limiter.py  (0.87)
           Rate limiter silently exempts requests with X-Internal-Service
           header. Root cause of 2024-03 billing spike. (2026-04-03)
           ⚠ stale — rate_limiter.py has changed since this insight was recorded

[coupling] analytics/event_publisher.py → billing/usage_worker.py  (0.81)
           Connected via PubSub topic analytics.usage-events.
           Implicit schema: (user_id, resource_type, quantity).

[decision] services/auth/state_utils.py  (0.78)
           Uses pickle intentionally — legacy IDP requires pickle-compatible
           payloads. Do not convert to JSON until IDP migration (AUTH-2847).
```

**Output format** (`--json`, machine-readable for agent consumption):
```json
[
  {
    "kind": "file",
    "index": "code-intent",
    "score": 0.92,
    "file_path": "services/auth/auth_service.py",
    "lines": [1, 45],
    "content_sha1": "a1b2c3...",
    "stale": false,
    "summary": "Core authentication service — issues and validates JWT tokens."
  },
  {
    "kind": "insight",
    "index": "debug-insights",
    "score": 0.87,
    "referenced_files": {"services/gateway/rate_limiter.py": "d4e5f6..."},
    "stale": true,
    "summary": "Rate limiter silently exempts requests with X-Internal-Service header...",
    "session_date": "2026-04-03"
  }
]
```

The `stale` field is derived from the content hash comparison described in Design Decisions. Claude Code can use this to decide whether to trust the result as-is or re-investigate.

**CLAUDE.md protocol implications:** The `tf-init` instructions should teach Claude Code the result kinds and their expected actions, so the agent knows not to blindly `Read` every result — an `insight` is self-contained, a `coupling` entry points to two files, and a `decision` is a constraint to respect.

---

## Synthesis Prompts: The Key Abstraction

Each index has a `prompt.md` that defines how raw input is distilled before embedding. This is analogous to the llm-wiki's "schema" — the instructions that make the LLM a disciplined indexer rather than a generic summarizer.

**Current synthesis prompt** (for `code-intent`) asks Claude to extract:
- `<core_intent>` — architectural summary
- `<legacy_coupling_severity>` — migration risk score
- `<hidden_coupling>` — non-obvious dependencies

**Debug insights prompt** would ask Claude to extract:
- Root cause (1-2 sentences)
- Affected files and their roles in the failure
- Conditions that trigger the bug (not obvious from code alone)
- What search terms would NOT find this file

**Coupling map prompt** would ask Claude to extract:
- Source and target files
- Nature of coupling (shared state, implicit contract, data format dependency)
- How the coupling manifests at runtime

The synthesis prompt is the highest-leverage configuration point. A well-written prompt produces embeddings that surface results a keyword search never would. A generic prompt produces generic embeddings.

---

## Migration Path from Current Architecture

### Phase 1: Multi-Index Storage (minimal change)

- Move from flat files to `.turbofind/indexes/<name>/` directory structure
- Add `--index` flag to `tf-search` and `tf-upsert`
- Backward compat: if `.turbofind.usearch` exists at project root, treat it as the `code-intent` index and offer a migration command
- Default `--index code-intent` when flag is omitted

### Phase 2: Non-Code Input (`--input` flag)

- Allow `tf-upsert --index <name> --input <file-or-stdin>` to index arbitrary text (not just source files)
- Use the index's `prompt.md` for synthesis instead of the hardcoded code-analysis prompt
- This unlocks debug insights, decision logs, and any user-defined index

### Phase 3: CLAUDE.md Protocol Extension

- `tf-init` generates index-aware instructions: which indexes exist, when to query each, when to persist insights
- The protocol becomes the main interface between Claude Code and TurboFind's growing knowledge base

### Phase 4: Multi-Index Search

- `tf-search --index all` with weighted merge
- Per-index weight configuration in `.turbofind/config.toml`

### Phase 5: Session Integration

- `tf-learn` command for post-session insight extraction
- Hook integration for automatic capture

---

## Design Principles

1. **Indexes are cheap.** Creating a new index should be one command. If someone wants a "security-audit" index, they write a synthesis prompt and start upserting.

2. **Synthesis prompts are the product.** The vector math is commodity. The value is in the prompts that extract the right signal from raw input. Ship good defaults; let users customize.

3. **Incremental over batch.** The llm-wiki insight: knowledge compounds. Every debug session, every PR, every incident is a chance to make the index smarter. Optimize for frequent small upserts, not occasional full rebuilds.

4. **The agent is the primary user.** Design for Claude Code reading CLAUDE.md, not a human reading `--help`. The protocol instructions matter more than CLI ergonomics.

5. **Don't boil the ocean.** Phase 1 (multi-index storage) and Phase 2 (non-code input) unlock the full vision. Everything else is iteration.

---

## Design Decisions

- **Embedding model per index:** Use `nomic-embed-text` for everything for now. It's simple and works well, but make the choice of model a per-index option so it can be revisited if retrieval quality diverges across content types.

- **Index lifecycle — content hashing over time-based decay:** All metadata entries include the SHA1 hash of the source content at index time. This applies to both code-intent entries (hash of the source file) and tf-learn entries (hashes of all referenced files). Staleness is detected by comparing stored hashes against current file content — not by age. A file untouched for a year still produces a valid insight; a file changed yesterday invalidates one. This enables:
  - `tf-search` can warn when results reference files whose content has changed since indexing
  - `tf-upsert` can skip files whose hash hasn't changed (avoiding redundant Claude API calls on re-index)
  - `tf-learn` insights that reference source files can be flagged for revalidation when those files change, rather than simply aged out

- **Cross-index linking:** Use a graph database to store relationships between indexes. This allows for complex queries and cross-index analysis.

- **Privacy/scope:** Out of scope for now. Access controls can be added later if needed for sensitive insights (security findings, credentials incidents).

- **Synthesis depth:** Out of scope for now. Keep upsert focused on extract-and-embed. Cross-referencing and contradiction detection (per the llm-wiki pattern) can be layered in later.
