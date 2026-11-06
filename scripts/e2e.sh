#!/usr/bin/env bash
# End-to-end full workflow scenario test script for ClutterXP (A79).
# Runs non-interactively in an isolated clean environment:
# 1. init (creates commented config with 0600)
# 2. rules (add and test rules)
# 3. organize --dry-run (assert zero writes)
# 4. organize (moves files according to rules)
# 5. history (view batch and ops)
# 6. undo (restores files safely)
# 7. watch --once (watch pass)
# 8. duplicates scan (exact duplicates detection)
# 9. doctor (integrity check)
set -euo pipefail

echo "=========================================================="
echo "=== Running ClutterXP End-to-End Workflow Script (E2E) ==="
echo "=========================================================="

TEMP_DIR=$(mktemp -d /tmp/cxp_e2e_XXXXXX)
trap 'rm -rf "$TEMP_DIR"' EXIT

export HOME="$TEMP_DIR"
export XDG_CONFIG_HOME="$TEMP_DIR/.config"
export XDG_DATA_HOME="$TEMP_DIR/.local/share"
export XDG_STATE_HOME="$TEMP_DIR/.local/state"
export XDG_CACHE_HOME="$TEMP_DIR/.cache"

# Locate virtual environment Python or clutterxp executable
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_BIN="/home/adityap/ai/Projects/clutterxp/.venv/bin"

if [ -x "$VENV_BIN/clutterxp" ]; then
    CXP="$VENV_BIN/clutterxp"
elif [ -x "$REPO_ROOT/.venv/bin/clutterxp" ]; then
    CXP="$REPO_ROOT/.venv/bin/clutterxp"
elif command -v clutterxp >/dev/null 2>&1; then
    CXP="clutterxp"
else
    CXP="python3 -m clutterxp.cli.app"
fi

echo "Using executable: $CXP"
echo "Isolated test HOME: $HOME"

# 1. Initialize config
echo "--- Step 1: clutterxp init ---"
$CXP init --minimal
CONFIG_FILE="$XDG_CONFIG_HOME/clutterxp/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file was not created!" >&2
    exit 1
fi
chmod 600 "$CONFIG_FILE"

# 2. Configure rules and watch folders
echo "--- Step 2: Configure Rules ---"
INBOX="$HOME/Downloads"
DOCS="$HOME/Documents/Invoices"
MEDIA="$HOME/Pictures/Wallpapers"
mkdir -p "$INBOX" "$DOCS" "$MEDIA"

cat <<EOF > "$CONFIG_FILE"
version: 1
settings:
  collision: rename
  create_destinations: all
watch:
  - path: $INBOX
rules:
  - name: Invoices
    keywords: [invoice, receipt]
    extensions: [pdf]
    destination: $DOCS
  - name: Wallpapers
    keywords: [nature, space]
    extensions: [jpg, png]
    destination: $MEDIA
EOF

$CXP config validate

# 3. Create test files in inbox
echo "--- Step 3: Populate Inbox ---"
echo "Invoice 001" > "$INBOX/invoice_jan.pdf"
echo "Receipt 002" > "$INBOX/receipt_feb.pdf"
echo "Nature Photo" > "$INBOX/nature_wallpaper.jpg"
echo "Unmatched Notes" > "$INBOX/random_notes.txt"
# Duplicates
echo "Duplicate Content Exact" > "$INBOX/dup_one.txt"
echo "Duplicate Content Exact" > "$INBOX/dup_two.txt"

# 4. Dry Run
echo "--- Step 4: Organize Dry Run ---"
$CXP organize "$INBOX" --dry-run
if [ ! -f "$INBOX/invoice_jan.pdf" ] || [ ! -f "$INBOX/nature_wallpaper.jpg" ]; then
    echo "ERROR: organize --dry-run modified files in inbox!" >&2
    exit 1
fi

# 5. Organize Apply
echo "--- Step 5: Organize Apply ---"
$CXP organize "$INBOX" --yes
if [ -f "$INBOX/invoice_jan.pdf" ]; then
    echo "ERROR: invoice_jan.pdf was not moved!" >&2
    exit 1
fi
if [ ! -f "$DOCS/invoice_jan.pdf" ]; then
    echo "ERROR: invoice_jan.pdf missing from destination!" >&2
    exit 1
fi
if [ ! -f "$MEDIA/nature_wallpaper.jpg" ]; then
    echo "ERROR: nature_wallpaper.jpg missing from destination!" >&2
    exit 1
fi
if [ ! -f "$INBOX/random_notes.txt" ]; then
    echo "ERROR: unmatched file random_notes.txt was moved!" >&2
    exit 1
fi

# 6. History and History Show
echo "--- Step 6: History Verification ---"
$CXP history
$CXP history --batch 1

# 7. Undo
echo "--- Step 7: Undo Batch ---"
$CXP undo --yes
if [ ! -f "$INBOX/invoice_jan.pdf" ]; then
    echo "ERROR: invoice_jan.pdf was not restored by undo!" >&2
    exit 1
fi
if [ -f "$DOCS/invoice_jan.pdf" ]; then
    echo "ERROR: invoice_jan.pdf still exists in destination after undo!" >&2
    exit 1
fi

# 8. Watch Once Mode
echo "--- Step 8: Watch Mode ---"
$CXP watch "$INBOX" --once
if [ -f "$INBOX/invoice_jan.pdf" ]; then
    echo "ERROR: watch --once did not sort invoice_jan.pdf!" >&2
    exit 1
fi
if [ ! -f "$DOCS/invoice_jan.pdf" ]; then
    echo "ERROR: invoice_jan.pdf missing after watch --once!" >&2
    exit 1
fi

# 9. Duplicates Scan
echo "--- Step 9: Duplicates Scan ---"
$CXP duplicates scan "$INBOX" --format json > "$TEMP_DIR/dup_results.json"
if ! grep -q "dup_one.txt" "$TEMP_DIR/dup_results.json"; then
    echo "ERROR: Duplicates scan failed to find duplicate files!" >&2
    exit 1
fi

# 10. Doctor Check
echo "--- Step 10: System Doctor ---"
$CXP doctor

echo "=========================================================="
echo "=== E2E Workflow Completed Successfully with 0 Errors! ==="
echo "=========================================================="
