#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# TurboFind Sync Test
#
# Verifies that Claude Code follows the POST-EDIT
# RULE in CLAUDE.md — running tf-upsert after
# modifying or creating files so the index stays
# in sync.
#
# Prerequisites:
#   - claude CLI installed and authenticated
#   - TurboFind installed (pip install -e .)
#   - Index already built (tf-upsert .)
#   - Run from inside demo_repo/
#
# Usage:
#   cd demo_repo && bash ../scripts/sync_test.sh
# ──────────────────────────────────────────────

META=".turbofind/indexes/code-intent/meta.json"
OUTDIR="../test_results/sync_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

echo "═══════════════════════════════════════════"
echo "  TurboFind Sync Test"
echo "═══════════════════════════════════════════"
echo ""

# Ensure CLAUDE.md has TurboFind instructions
tf-init

# Ensure index exists
if [ ! -f "$META" ]; then
  echo "No index found. Building initial index..."
  tf-upsert .
fi

# ── Snapshot the index before Claude edits ──
cp "$META" "$OUTDIR/meta_before.json"
ENTRIES_BEFORE=$(python3 -c "import json; print(len(json.load(open('$META'))))")
echo "Index before: $ENTRIES_BEFORE entries"
echo ""

# ── Test 1: Modify an existing file ──
echo "── Test 1: Modify existing file ──"
TARGET_FILE="shared/constants.py"
SHA1_BEFORE=$(python3 -c "
import json
meta = json.load(open('$META'))
for v in meta.values():
    if v.get('file_path') == '$TARGET_FILE':
        print(v.get('content_sha1', 'none'))
        break
")
echo "   Target: $TARGET_FILE"
echo "   SHA1 before: $SHA1_BEFORE"

TIMESTAMP=$(date +%s)
EDIT_PROMPT="Add a new constant SYNC_TEST_TOKEN = \"$TIMESTAMP\" to the file $TARGET_FILE. If a SYNC_TEST_TOKEN constant already exists, replace its value. Follow the POST-EDIT RULE in CLAUDE.md after making the change."
echo "   Asking Claude to edit..."
claude -p "$EDIT_PROMPT" --output-format text \
  --allowedTools 'Edit' 'Write' 'Read' 'Bash(tf-upsert:*)' \
  > "$OUTDIR/test1_output.txt" 2>&1

SHA1_AFTER=$(python3 -c "
import json
meta = json.load(open('$META'))
for v in meta.values():
    if v.get('file_path') == '$TARGET_FILE':
        print(v.get('content_sha1', 'none'))
        break
")
echo "   SHA1 after:  $SHA1_AFTER"

if [ "$SHA1_BEFORE" != "$SHA1_AFTER" ]; then
  echo "   PASS: Index was updated after file modification"
  TEST1="PASS"
else
  echo "   FAIL: Index was NOT updated after file modification"
  TEST1="FAIL"
fi
echo ""

# ── Test 2: Create a new file ──
echo "── Test 2: Create new file ──"
NEW_FILE="shared/retry_config.py"
echo "   Target: $NEW_FILE (does not exist yet)"

# Confirm the file isn't already indexed
INDEXED_BEFORE=$(python3 -c "
import json
meta = json.load(open('$META'))
found = any(v.get('file_path') == '$NEW_FILE' for v in meta.values())
print('yes' if found else 'no')
")
echo "   In index before: $INDEXED_BEFORE"

CREATE_PROMPT="Create a new file $NEW_FILE with a RetryConfig class that has max_retries, backoff_factor, and timeout attributes. Follow the POST-EDIT RULE in CLAUDE.md after creating the file."
echo "   Asking Claude to create..."
claude -p "$CREATE_PROMPT" --output-format text \
  --allowedTools 'Edit' 'Write' 'Read' 'Bash(tf-upsert:*)' \
  > "$OUTDIR/test2_output.txt" 2>&1

INDEXED_AFTER=$(python3 -c "
import json
meta = json.load(open('$META'))
found = any(v.get('file_path') == '$NEW_FILE' for v in meta.values())
print('yes' if found else 'no')
")
echo "   In index after:  $INDEXED_AFTER"

if [ "$INDEXED_AFTER" = "yes" ]; then
  echo "   PASS: New file was indexed after creation"
  TEST2="PASS"
else
  echo "   FAIL: New file was NOT indexed after creation"
  TEST2="FAIL"
fi
echo ""

# ── Test 3: Delete a file ──
echo "── Test 3: Delete file ──"
# Use the file created in Test 2
DEL_FILE="$NEW_FILE"
echo "   Target: $DEL_FILE"

INDEXED_BEFORE_DEL=$(python3 -c "
import json
meta = json.load(open('$META'))
found = any(v.get('file_path') == '$DEL_FILE' for v in meta.values())
print('yes' if found else 'no')
")
echo "   In index before: $INDEXED_BEFORE_DEL"

# Delete the file ourselves (Claude Code sandbox blocks rm in non-interactive mode)
rm -f "$DEL_FILE"
echo "   File deleted by test harness"

DELETE_PROMPT="The file $DEL_FILE has been deleted from the repository. Follow the POST-DELETE RULE in CLAUDE.md to clean up the index."
echo "   Asking Claude to update index..."
claude -p "$DELETE_PROMPT" --output-format text \
  --allowedTools 'Bash(tf-upsert:*)' 'Read' \
  > "$OUTDIR/test3_output.txt" 2>&1

INDEXED_AFTER_DEL=$(python3 -c "
import json
meta = json.load(open('$META'))
found = any(v.get('file_path') == '$DEL_FILE' for v in meta.values())
print('yes' if found else 'no')
")
echo "   In index after:  $INDEXED_AFTER_DEL"

if [ "$INDEXED_AFTER_DEL" = "no" ]; then
  echo "   PASS: Deleted file was removed from index"
  TEST3="PASS"
else
  echo "   FAIL: Deleted file was NOT removed from index"
  TEST3="FAIL"
fi
echo ""

# ── Snapshot the index after ──
cp "$META" "$OUTDIR/meta_after.json"
ENTRIES_AFTER=$(python3 -c "import json; print(len(json.load(open('$META'))))")

# ── Results ──
echo "═══════════════════════════════════════════"
echo "  Results"
echo "═══════════════════════════════════════════"
echo ""
echo "  Test 1 (modify existing file): $TEST1"
echo "  Test 2 (create new file):      $TEST2"
echo "  Test 3 (delete file):          $TEST3"
echo ""
echo "  Index entries: $ENTRIES_BEFORE before -> $ENTRIES_AFTER after"
echo ""
echo "  Outputs saved to:"
echo "    $OUTDIR/test1_output.txt"
echo "    $OUTDIR/test2_output.txt"
echo "    $OUTDIR/test3_output.txt"
echo "    $OUTDIR/meta_before.json"
echo "    $OUTDIR/meta_after.json"
echo "═══════════════════════════════════════════"

# Exit with failure if any test failed
if [ "$TEST1" = "FAIL" ] || [ "$TEST2" = "FAIL" ] || [ "$TEST3" = "FAIL" ]; then
  exit 1
fi
