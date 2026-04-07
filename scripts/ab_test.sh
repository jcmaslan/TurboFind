#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# TurboFind A/B Test
#
# Compares Claude Code's file discovery with and
# without TurboFind's semantic search protocol.
#
# Prerequisites:
#   - claude CLI installed and authenticated
#   - TurboFind installed (pip install -e .)
#   - Index already built (tf-upsert .)
#   - Topology graph exists (.turbofind/graph.json)
#   - Run from inside demo_repo/
#
# Usage:
#   cd demo_repo && bash ../scripts/ab_test.sh
#
# Custom prompt and ground truth:
#   Create a .ab_test.conf file in your repo with:
#     PROMPT="your custom prompt here"
#     GROUND_TRUTH=("path/to/file1.py" "path/to/file2.py")
# ──────────────────────────────────────────────

# Defaults (tuned for demo_repo)
PROMPT="List every file in this codebase that contains logic related to user identity, authentication, authorization, session handling, or permission checks. For each file, explain what identity/auth-related logic it contains. Be thorough — include files that may not use obvious keywords like \"auth\" or \"login\" but still participate in identity or access control decisions. Output your findings as a simple list of file paths. Before your final list, briefly describe the investigation steps you took (which tools you called, how many files you read, etc.)."

GROUND_TRUTH=(
  "services/gateway/middleware.py"
  "services/gateway/rate_limiter.py"
  "services/billing/token_reader.py"
  "services/auth/state_utils.py"
  "services/analytics/tracker.py"
)

# Override defaults with user config if present
if [ -f .ab_test.conf ]; then
  # shellcheck source=/dev/null
  source .ab_test.conf
  echo "Loaded custom config from .ab_test.conf"
fi

OUTDIR="../test_results/ab_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

# ── Pre-flight checks ──
if [ ! -f .turbofind/graph.json ]; then
  echo "ERROR: .turbofind/graph.json not found."
  echo "Run 'tf-upsert . --graph-only' to build the topology graph (fast, no API calls)."
  exit 1
fi

echo "═══════════════════════════════════════════"
echo "  TurboFind A/B Test"
echo "═══════════════════════════════════════════"
echo ""
echo "Output directory: $OUTDIR"
echo ""

# ── Run A: Without TurboFind ──
echo "── Run A: WITHOUT TurboFind ──"
tf-init --remove 2>/dev/null || true
echo "   CLAUDE.md TurboFind block removed."
echo "   Running Claude Code..."
claude -p "$PROMPT" --output-format text > "$OUTDIR/run_a_without.txt" 2>&1
echo "   Done. Output saved to run_a_without.txt"
echo ""

# ── Run B: With TurboFind ──
echo "── Run B: WITH TurboFind ──"
tf-init
echo "   Running Claude Code..."
claude -p "$PROMPT" --output-format text --allowedTools 'Bash(tf-search:*)' > "$OUTDIR/run_b_with.txt" 2>&1
echo "   Done. Output saved to run_b_with.txt"
echo ""

# ── Score both runs ──
echo "═══════════════════════════════════════════"
echo "  Scoring against ground truth (${#GROUND_TRUTH[@]} hidden coupling files)"
echo "═══════════════════════════════════════════"
echo ""

SCORE_SUMMARY=""

for run_label in "A (without)" "B (with)"; do
  if [[ "$run_label" == "A (without)" ]]; then
    file="$OUTDIR/run_a_without.txt"
  else
    file="$OUTDIR/run_b_with.txt"
  fi

  found=0
  missed=()
  for trap in "${GROUND_TRUTH[@]}"; do
    # Check if the trap file path appears anywhere in Claude's output
    if grep -q "$trap" "$file" 2>/dev/null; then
      found=$((found + 1))
    else
      missed+=("$trap")
    fi
  done

  score_line="Run $run_label: $found / ${#GROUND_TRUTH[@]} hidden files found"
  echo "$score_line"
  SCORE_SUMMARY+="$score_line"$'\n'
  if [[ ${#missed[@]} -gt 0 ]]; then
    for m in "${missed[@]}"; do
      echo "   ✗ MISSED: $m"
      SCORE_SUMMARY+="   MISSED: $m"$'\n'
    done
  else
    echo "   ✓ All hidden coupling files identified"
    SCORE_SUMMARY+="   All hidden coupling files identified"$'\n'
  fi
  echo ""
done

# ── Generate evaluation ──
echo "── Generating run_eval.md ──"

EVAL_FILE="$OUTDIR/_eval_prompt.txt"

# Write the eval prompt to a file to avoid quoting issues with shell expansion
cat > "$EVAL_FILE" <<'EVALEOF'
You are comparing two runs of an A/B test. Run A used standard Claude Code tools (grep, file reads). Run B had access to TurboFind semantic search (tf-search) in addition to standard tools. Both were given the same prompt asking them to find all identity/auth-related files in a codebase.

Below are the two outputs, followed by a ground truth set of "hidden coupling" files that are especially hard to find because they participate in identity/auth flows without obvious naming. Also included are the automated scoring results showing which ground truth files each run found or missed.

Produce a concise markdown evaluation covering:

1. **Coverage** — a table comparing files identified, investigation tools used, and unique files found. List any files one run found that the other missed.
2. **Ground Truth Recall** — report each run score against the ground truth set. For any missed files, explain why they are hard to find and what search strategy might have caught them.
3. **Depth of Analysis** — compare the quality/insight of the descriptions for files both runs found. Give specific examples.
4. **Efficiency** — compare the number of tool calls / file reads each run needed.
5. **Verdict** — summarize whether TurboFind made a measurable difference on this (small) repo and note how the advantage might scale.

Output ONLY the markdown content, no preamble.
EVALEOF

# Append ground truth, scores, and run outputs
{
  echo ""
  echo "--- GROUND TRUTH FILES ---"
  for trap in "${GROUND_TRUTH[@]}"; do
    echo "- $trap"
  done
  echo "--- SCORING RESULTS ---"
  printf '%s\n' "$SCORE_SUMMARY"
  echo "--- RUN A (without TurboFind) ---"
  cat "$OUTDIR/run_a_without.txt"
  echo ""
  echo "--- RUN B (with TurboFind) ---"
  cat "$OUTDIR/run_b_with.txt"
} >> "$EVAL_FILE"

claude -p "$(< "$EVAL_FILE")" --output-format text > "$OUTDIR/run_eval.md" 2>&1

echo "   Done. Evaluation saved to run_eval.md"
echo ""

# ── Summary ──
echo "═══════════════════════════════════════════"
echo "  Full outputs saved to:"
echo "    $OUTDIR/run_a_without.txt"
echo "    $OUTDIR/run_b_with.txt"
echo "    $OUTDIR/run_eval.md"
echo "═══════════════════════════════════════════"
