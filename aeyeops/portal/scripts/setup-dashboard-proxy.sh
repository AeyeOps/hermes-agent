#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: setup-dashboard-proxy.sh --domain DOMAIN --user USER [--password-hash HASH | --password-hash-file PATH] [options]

Configures a loopback-only Hermes dashboard behind Caddy HTTPS + Basic Auth.
No secrets are read from the repo. Prefer --password-hash-file or
AEX_PORTAL_PASSWORD_HASH_FILE over passing hashes on the command line.

Required:
  --domain DOMAIN              Public hostname, e.g. dashboard.example.com
  --user USER                  Basic Auth username
  --password-hash HASH         Output from: caddy hash-password; less preferred
  --password-hash-file PATH    File containing the Caddy password hash

Options:
  --hermes-home PATH        Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --repo-dir PATH           Hermes repo dir (default: HERMES_HOME/hermes-agent)
  --port PORT               Local dashboard port (default: 9119)
  --enable-tui              Enable embedded dashboard TUI/chat
  --dry-run                 Print and log planned changes only
  --yes                     Accepted for CI/headless callers; no prompts are used
  -h, --help                Show this help
USAGE
}

DOMAIN=""
AUTH_USER=""
PASSWORD_HASH="${AEX_PORTAL_PASSWORD_HASH:-}"
PASSWORD_HASH_FILE="${AEX_PORTAL_PASSWORD_HASH_FILE:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
REPO_DIR=""
PORT="9119"
ENABLE_TUI=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --user) AUTH_USER="${2:-}"; shift 2 ;;
    --password-hash) PASSWORD_HASH="${2:-}"; shift 2 ;;
    --password-hash-file) PASSWORD_HASH_FILE="${2:-}"; shift 2 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift 2 ;;
    --repo-dir) REPO_DIR="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --enable-tui) ENABLE_TUI=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "$PASSWORD_HASH_FILE" ]]; then
  if [[ ! -r "$PASSWORD_HASH_FILE" ]]; then
    echo "Password hash file is not readable: $PASSWORD_HASH_FILE" >&2
    exit 2
  fi
  PASSWORD_HASH="$(tr -d '\r\n' < "$PASSWORD_HASH_FILE")"
fi

if [[ -z "$DOMAIN" || -z "$AUTH_USER" || -z "$PASSWORD_HASH" ]]; then
  echo "Missing required --domain, --user, and password hash source." >&2
  usage >&2
  exit 2
fi

if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "--port must be numeric: $PORT" >&2
  exit 2
fi

REPO_DIR="${REPO_DIR:-$HERMES_HOME/hermes-agent}"
LOG_DIR="$HERMES_HOME/logs"
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/aeyeops-portal-setup-$TS.log"
CADDYFILE="/etc/caddy/Caddyfile"
CADDY_SITE_FILE="/etc/caddy/conf.d/hermes-dashboard.caddy"
SYSTEMD_UNIT="/etc/systemd/system/hermes-dashboard.service"
LOGROTATE_FILE="/etc/logrotate.d/aeyeops-hermes-portal"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
run() {
  log "+ $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}
write_file() {
  local path="$1"
  local mode="$2"
  local redact_pattern="${3:-}"
  local tmp
  tmp="$(mktemp)"
  cat > "$tmp"
  log "write $path"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    install -D -m "$mode" "$tmp" "$path"
  else
    if [[ -n "$redact_pattern" ]]; then
      sed "s#${redact_pattern}#<redacted>#g" "$tmp" | sed 's/^/    | /'
    else
      sed 's/^/    | /' "$tmp"
    fi
  fi
  rm -f "$tmp"
}
ensure_caddy_import() {
  local import_line='import /etc/caddy/conf.d/*.caddy'
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "ensure $CADDYFILE contains: $import_line"
    return 0
  fi
  install -d -m 0755 /etc/caddy/conf.d
  if [[ ! -e "$CADDYFILE" ]]; then
    printf '%s\n' "$import_line" > "$CADDYFILE"
    chmod 0644 "$CADDYFILE"
    return 0
  fi
  if ! grep -Fxq "$import_line" "$CADDYFILE"; then
    printf '\n# AeyeOps managed site snippets\n%s\n' "$import_line" >> "$CADDYFILE"
  fi
}
protect_caddy_site_file() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "protect $CADDY_SITE_FILE as root:caddy 0640 when caddy group exists"
    return 0
  fi
  if getent group caddy >/dev/null 2>&1; then
    chgrp caddy "$CADDY_SITE_FILE"
    chmod 0640 "$CADDY_SITE_FILE"
  else
    echo "Missing caddy group; refusing to leave password hash in a broadly readable site file." >&2
    echo "Create the caddy group/package service or adjust the deployment manually." >&2
    exit 1
  fi
}
backup_if_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    run cp -p "$path" "$path.aeyeops-portal-bak-$TS"
  fi
}

log "AeyeOps Hermes dashboard proxy setup"
log "domain=$DOMAIN hermes_home=$HERMES_HOME repo_dir=$REPO_DIR port=$PORT dry_run=$DRY_RUN enable_tui=$ENABLE_TUI"
log "log_file=$LOG_FILE"

if [[ "$DRY_RUN" -eq 0 && "$(id -u)" -ne 0 ]]; then
  echo "Run as root because this writes /etc/caddy and /etc/systemd/system." >&2
  exit 1
fi

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repo dir not found: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -x "$REPO_DIR/venv/bin/python" ]]; then
  echo "Hermes venv python not found/executable: $REPO_DIR/venv/bin/python" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 0 ]] && ! command -v caddy >/dev/null 2>&1; then
  echo "caddy is not installed. Install Caddy first, then re-run." >&2
  exit 1
fi

DASHBOARD_ARGS="dashboard --host 127.0.0.1 --port $PORT --no-open"
if [[ "$ENABLE_TUI" -eq 1 ]]; then
  DASHBOARD_ARGS="$DASHBOARD_ARGS --tui"
fi

backup_if_exists "$CADDYFILE"
backup_if_exists "$CADDY_SITE_FILE"
backup_if_exists "$SYSTEMD_UNIT"
backup_if_exists "$LOGROTATE_FILE"

write_file "$SYSTEMD_UNIT" 0644 <<UNIT
[Unit]
Description=Hermes dashboard (loopback only)
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
Environment=HERMES_HOME=$HERMES_HOME
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/venv/bin/python -m hermes_cli.main $DASHBOARD_ARGS
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$HERMES_HOME

[Install]
WantedBy=multi-user.target
UNIT

ensure_caddy_import
write_file "$CADDY_SITE_FILE" 0640 "$PASSWORD_HASH" <<CADDY
$DOMAIN {
	encode zstd gzip

	log {
		output file /var/log/caddy/hermes-dashboard-access.log {
			roll_size 10MiB
			roll_keep 5
			roll_keep_for 720h
		}
		format console
	}

	basic_auth {
		$AUTH_USER $PASSWORD_HASH
	}

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		X-Frame-Options "DENY"
	}

	reverse_proxy 127.0.0.1:$PORT {
		header_up Host 127.0.0.1:$PORT
		header_up X-Forwarded-Proto https
	}
}
CADDY
protect_caddy_site_file

write_file "$LOGROTATE_FILE" 0644 <<LOGROTATE
$HERMES_HOME/logs/aeyeops-portal-*.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    dateext
    create 0640 root root
}
LOGROTATE

if [[ "$DRY_RUN" -eq 0 ]]; then
  run systemctl daemon-reload
  run systemctl enable --now hermes-dashboard.service
  run caddy validate --config "$CADDYFILE"
  run systemctl reload caddy
  run systemctl is-active --quiet hermes-dashboard.service
  run systemctl is-active --quiet caddy
else
  log "dry-run: skipped systemctl/caddy changes"
fi

log "Setup complete. Next: run verify-dashboard-proxy.sh --domain $DOMAIN --hermes-home $HERMES_HOME"
