#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEEYEOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BACKUP_ROOT="${1:-${BACKUP_ROOT:-$AEEYEOPS_DIR/local/hermes-instance-backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT%/}/${STAMP}"

files=(
  .env
  .update_check
  auth.json
  config.yaml
  gateway.json
  gateway_state.json
  channel_directory.json
  SOUL.md
  state.db
  kanban.db
  response_store.db
  processes.json
  slack_tokens.json
  sticker_cache.json
  feishu_seen_message_ids.json
  feishu_comment_rules.json
  feishu_comment_pairing.json
  context_length_cache.yaml
  models_dev_cache.json
  .hermes_history
  .restart_last_processed.json
  .skills_prompt_snapshot.json
)

dirs=(
  .omx
  auth
  checkpoints
  skills
  memories
  plugins
  hooks
  cron
  scripts
  pairing
  whatsapp
  pastes
  plans
)

mkdir -p "$DEST"

copy_path() {
  local rel="$1"
  local src="$HERMES_HOME/$rel"
  [ -e "$src" ] || return 0
  mkdir -p "$DEST/hermes-home/$(dirname "$rel")"
  rsync -a --delete-excluded \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'venv/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'logs/' \
    --exclude 'output/' \
    --exclude 'bridge.log' \
    --exclude 'bridge.pid' \
    --exclude '.curator_backups/' \
    --exclude '.archive/' \
    "$src" "$DEST/hermes-home/$(dirname "$rel")/"
}

backup_sqlite() {
  local rel="$1"
  local src="$HERMES_HOME/$rel"
  local out="$DEST/hermes-home/$rel"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$out")"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$src" ".backup '$out'"
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$src" "$out" <<'PY_SQLITE_BACKUP'
import sqlite3
import sys

src, out = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as source, sqlite3.connect(out) as dest:
    source.backup(dest)
PY_SQLITE_BACKUP
  else
    cp -a "$src" "$out"
  fi
}

for rel in "${files[@]}"; do
  case "$rel" in
    *.db) backup_sqlite "$rel" ;;
    *) copy_path "$rel" ;;
  esac
done

if [ -e "$HERMES_HOME/sessions/sessions.json" ]; then
  copy_path "sessions/sessions.json"
fi

for rel in "${dirs[@]}"; do
  copy_path "$rel"
done

printf 'backup=%s\n' "$DEST"
