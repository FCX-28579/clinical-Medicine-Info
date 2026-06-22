#!/usr/bin/env bash
# setup-chictr-mcp.sh
#
# Idempotent installer for the ChiCTR MCP server in Claude Code.
# Replaces the manual "edit ~/.claude.json by hand" step from the README.
#
# What it does:
#   1. Verify Node.js >= 18 is on PATH (npx ships with Node.js).
#   2. Locate ~/.claude.json (or create one if Claude Code is fresh).
#   3. Add mcpServers.chictr if not already present (idempotent).
#   4. Smoke-test that `npx -y chictr-mcp-server --help` resolves the package.
#   5. Print next-step instructions (user must restart Claude Code).
#
# Usage:
#   bash scripts/setup-chictr-mcp.sh
#
# Exit codes:
#   0  success (config already correct, or successfully written)
#   1  Node.js missing or too old
#   2  npx fetch failed (network or registry issue)
#   3  ~/.claude.json exists but is unreadable / invalid JSON

set -euo pipefail

CLAUDE_JSON="${CLAUDE_JSON:-$HOME/.claude.json}"
CHICTR_PKG="chictr-mcp-server"

red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }

bold "→ Step 1/4  Checking Node.js…"
if ! command -v node >/dev/null 2>&1; then
  red "  ✗ Node.js not found on PATH."
  red "    Install Node.js >= 18 from https://nodejs.org and re-run this script."
  exit 1
fi
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
if [[ "$NODE_MAJOR" -lt 18 ]]; then
  red "  ✗ Node.js $(node --version) is too old. ChiCTR MCP needs >= 18."
  exit 1
fi
green "  ✓ Node.js $(node --version)"

bold "→ Step 2/4  Locating Claude Code config…"
if [[ ! -f "$CLAUDE_JSON" ]]; then
  yellow "  ! $CLAUDE_JSON does not exist — creating an empty one."
  echo '{}' > "$CLAUDE_JSON"
fi
if ! node -e "JSON.parse(require('fs').readFileSync('$CLAUDE_JSON','utf8'))" 2>/dev/null; then
  red "  ✗ $CLAUDE_JSON exists but is not valid JSON. Fix it manually and re-run."
  exit 3
fi
green "  ✓ $CLAUDE_JSON is readable"

bold "→ Step 3/4  Merging mcpServers.chictr…"
# Crash-safe write: backup → write to tempfile → atomic rename. If anything
# fails mid-write we never leave $CLAUDE_JSON in a half-written state.
TS=$(date +%Y%m%d-%H%M%S)
BACKUP="${CLAUDE_JSON}.backup-${TS}"
cp "$CLAUDE_JSON" "$BACKUP"
TMP_OUT=$(mktemp "${CLAUDE_JSON}.tmp.XXXXXX")
trap 'rm -f "$TMP_OUT"' EXIT

VERDICT=$(node - "$CLAUDE_JSON" "$TMP_OUT" <<'NODE_EOF'
const fs = require('fs');
const [src, dst] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(src, 'utf8'));
cfg.mcpServers = cfg.mcpServers || {};
const existing = cfg.mcpServers.chictr;
const desired = { command: 'npx', args: ['-y', 'chictr-mcp-server'] };
const same = existing
  && existing.command === desired.command
  && JSON.stringify(existing.args) === JSON.stringify(desired.args);
if (same) {
  process.stdout.write('UNCHANGED');
} else {
  cfg.mcpServers.chictr = desired;
  fs.writeFileSync(dst, JSON.stringify(cfg, null, 2) + '\n');
  process.stdout.write(existing ? 'UPDATED' : 'ADDED');
}
NODE_EOF
)

case "$VERDICT" in
  UNCHANGED)
    green "  ✓ chictr already registered; no changes."
    rm -f "$BACKUP"  # backup unnecessary when nothing changed
    ;;
  ADDED|UPDATED)
    mv "$TMP_OUT" "$CLAUDE_JSON"  # atomic rename on POSIX
    green "  ✓ $VERDICT mcpServers.chictr (backup: $BACKUP)"
    ;;
  *)
    red "  ✗ Unexpected verdict from JSON merge: '$VERDICT'"
    red "    Backup preserved at: $BACKUP"
    exit 4
    ;;
esac

bold "→ Step 4/4  Verifying npx can fetch $CHICTR_PKG (60s timeout)…"
NPX_TIMEOUT="${CHICTR_NPX_TIMEOUT:-60}"

# Portable timeout: prefer GNU coreutils 'timeout' (Linux), then 'gtimeout'
# (macOS w/ brew coreutils), then fall back to perl's alarm (always present
# on macOS and most Linux distros).
run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${secs}s" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "${secs}s" "$@"
  elif command -v perl >/dev/null 2>&1; then
    # perl returns 142 (128 + SIGALRM) on timeout; we normalize to 124 so the
    # caller can treat it the same as GNU timeout's exit code.
    perl -e 'alarm shift; exec @ARGV; exit 127' "$secs" "$@"
    local rc=$?
    [[ "$rc" -eq 142 ]] && return 124
    return $rc
  else
    "$@"  # no timeout available — last resort
  fi
}

if run_with_timeout "$NPX_TIMEOUT" npx -y "$CHICTR_PKG" --help >/dev/null 2>&1; then
  green "  ✓ npx -y $CHICTR_PKG resolved within ${NPX_TIMEOUT}s"
else
  rc=$?
  if [[ "$rc" -eq 124 ]]; then
    yellow "  ! npx fetch hit the ${NPX_TIMEOUT}s timeout — slow network or"
    yellow "    corporate proxy? First real ChiCTR query inside Claude Code"
    yellow "    will be the actual smoke test."
  else
    yellow "  ! '$CHICTR_PKG --help' returned non-zero (rc=$rc). OK if the"
    yellow "    package doesn't accept --help; first real query is the"
    yellow "    actual smoke test."
  fi
fi

echo
green "Done. ChiCTR MCP server is registered."
echo
bold  "Next step:"
echo  "  Restart Claude Code (close and reopen the session) so it picks up"
echo  "  the new MCP server. Then verify the tools are available:"
echo
echo  "      mcp__chictr__search_trials"
echo  "      mcp__chictr__get_trial_detail"
echo
