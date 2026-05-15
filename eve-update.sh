#!/usr/bin/env bash
# eve-update.sh — Pull latest vendor-layer + apply to local paths.
#
# Run on each customer box on a schedule (or by hand). Pulls from origin/main,
# rsyncs the vendor-layer files into their canonical locations, regenerates
# CLAUDE.md, and prompts to restart PM2 services if their source changed.
#
# Customer-layer files (CLAUDE.user.md, memory/user/, vault content, credentials)
# are NEVER touched.
#
# Idempotent — safe to re-run.
#
# Usage:  ./eve-update.sh   (from inside the vendor-layer repo)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVE_HOME="${HOME}"
EVE_TOOLS="${EVE_HOME}/.local/eve-tools"
VAULT="${EVE_HOME}/EveBrain"
MEMORY_DIR="${EVE_HOME}/.claude/projects/-home-eve-EveBrain/memory"

info()  { echo -e "\033[34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[32m[ OK ]\033[0m  $*"; }
warn()  { echo -e "\033[33m[WARN]\033[0m  $*"; }
fail()  { echo -e "\033[31m[FAIL]\033[0m  $*" >&2; exit 1; }

cd "${REPO_DIR}"

# ─── 1. Pull latest ──────────────────────────────────────────────────────────
info "Fetching latest vendor-layer from origin/main..."
PREV_COMMIT=$(git rev-parse HEAD)
git fetch --quiet origin main
NEW_COMMIT=$(git rev-parse origin/main)

if [[ "${PREV_COMMIT}" == "${NEW_COMMIT}" ]]; then
  ok "Already at latest (${NEW_COMMIT:0:7}). Nothing to do."
  exit 0
fi

info "Updating ${PREV_COMMIT:0:7} → ${NEW_COMMIT:0:7}"
git rebase --quiet origin/main || fail "Rebase failed. Resolve manually with: git rebase --abort && git pull --rebase"

# ─── 2. Identify what changed (to decide which services to restart) ──────────
CHANGED=$(git diff --name-only "${PREV_COMMIT}" "${NEW_COMMIT}")
info "Files changed:"
echo "${CHANGED}" | sed 's/^/  /'

NEEDS_SHAREDBRAIN_RESTART=false
NEEDS_WHATSAPP_REBUILD=false
NEEDS_CLAUDE_ASSEMBLE=false

echo "${CHANGED}" | grep -q "^bridges/sharedbrain/" && NEEDS_SHAREDBRAIN_RESTART=true
echo "${CHANGED}" | grep -q "^bridges/whatsapp-mcp/whatsapp-bridge/" && NEEDS_WHATSAPP_REBUILD=true
echo "${CHANGED}" | grep -qE "^(CLAUDE\.base\.md|assemble-claude\.sh)$" && NEEDS_CLAUDE_ASSEMBLE=true

# ─── 3. Apply vendor-layer file copies ───────────────────────────────────────

# CLAUDE.base.md → vault root
cp "${REPO_DIR}/CLAUDE.base.md" "${VAULT}/"
ok "CLAUDE.base.md synced"

# assemble-claude.sh + eve-tools/
cp "${REPO_DIR}/assemble-claude.sh" "${EVE_TOOLS}/"
chmod +x "${EVE_TOOLS}/assemble-claude.sh"
# rsync semantics: --delete removes obsolete files in dest BUT only for files
# in the source tree. Generated state (logs, dbs, caches) is in .gitignore'd
# subdirs (vault-chroma/, plaud-state/, etc.) — those aren't in source so
# rsync leaves them alone. Customer-supplied configs (cadence_model.yaml,
# interest_profile.yaml) are also not in source — also untouched.
rsync -a --delete \
  --exclude='*-venv/' \
  --exclude='__pycache__/' \
  --exclude='plaud-cache/' \
  --exclude='plaud-state/' \
  --exclude='vault-chroma/' \
  --exclude='piper-voices/' \
  --exclude='cron-*.log' \
  --exclude='eve-knowledge.db' \
  --exclude='reminder-*.py' \
  --exclude='shawn-vzw-*.sh' \
  --exclude='cadence_model.yaml' \
  --exclude='interest_profile.yaml' \
  --exclude='interest_profile.example.yaml' \
  "${REPO_DIR}/eve-tools/" "${EVE_TOOLS}/"
ok "eve-tools/ synced"

# memory/vendor/ — also a clean sync (user/ is .gitignored on source side and untouched here)
mkdir -p "${MEMORY_DIR}/vendor"
rsync -a --delete "${REPO_DIR}/memory/vendor/" "${MEMORY_DIR}/vendor/"
ok "memory/vendor/ synced"

# bridges/sharedbrain → ~/sharedbrain  (preserves node_modules; npm install only if package.json changed)
if [[ -d "${EVE_HOME}/sharedbrain" ]]; then
  rsync -a \
    --exclude='node_modules/' \
    --exclude='credentials.json' \
    --exclude='gdrive-token.json' \
    "${REPO_DIR}/bridges/sharedbrain/" "${EVE_HOME}/sharedbrain/"
  if echo "${CHANGED}" | grep -q "^bridges/sharedbrain/package.json$"; then
    info "package.json changed — running npm install"
    (cd "${EVE_HOME}/sharedbrain" && npm install --silent)
  fi
  ok "sharedbrain synced"
fi

# bridges/whatsapp-mcp → ~/whatsapp-mcp  (preserves .venv + store/ + wake-config.json + compiled binary)
if [[ -d "${EVE_HOME}/whatsapp-mcp" ]]; then
  rsync -a \
    --exclude='.venv/' \
    --exclude='store/' \
    --exclude='__pycache__/' \
    --exclude='whatsapp-bridge/whatsapp-bridge' \
    --exclude='whatsapp-bridge/wake-config.json' \
    --exclude='whatsapp-bridge/current-*' \
    --exclude='whatsapp-bridge/wa-pair-*' \
    "${REPO_DIR}/bridges/whatsapp-mcp/" "${EVE_HOME}/whatsapp-mcp/"
  ok "whatsapp-mcp synced"
fi

# ─── 4. Conditional rebuilds + assembly ──────────────────────────────────────

if [[ "${NEEDS_WHATSAPP_REBUILD}" == "true" ]]; then
  info "Go bridge source changed — rebuilding..."
  (cd "${EVE_HOME}/whatsapp-mcp/whatsapp-bridge" && go build -o whatsapp-bridge .)
  ok "whatsapp-bridge rebuilt"
fi

if [[ "${NEEDS_CLAUDE_ASSEMBLE}" == "true" ]]; then
  if [[ -f "${VAULT}/CLAUDE.user.md" ]]; then
    bash "${EVE_TOOLS}/assemble-claude.sh"
    ok "CLAUDE.md reassembled"
  else
    warn "CLAUDE.base.md changed but CLAUDE.user.md not present — assembly skipped"
  fi
fi

# ─── 5. Service restarts (PM2) ───────────────────────────────────────────────

if [[ "${NEEDS_SHAREDBRAIN_RESTART}" == "true" ]] || [[ "${NEEDS_WHATSAPP_REBUILD}" == "true" ]]; then
  if command -v pm2 >/dev/null; then
    info "Restarting affected PM2 services..."
    [[ "${NEEDS_SHAREDBRAIN_RESTART}" == "true" ]] && pm2 restart sharedbrain 2>/dev/null || true
    [[ "${NEEDS_WHATSAPP_REBUILD}" == "true" ]] && pm2 restart whatsapp-bridge 2>/dev/null || true
    ok "PM2 services restarted"
  else
    warn "pm2 not on PATH — skipping service restart"
  fi
fi

ok "Update complete: ${PREV_COMMIT:0:7} → ${NEW_COMMIT:0:7}"
