#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEEYEOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SETUP_SCRIPT="$AEEYEOPS_DIR/portal/scripts/setup-dashboard-proxy.sh"
VERIFY_SCRIPT="$AEEYEOPS_DIR/portal/scripts/verify-dashboard-proxy.sh"

usage() {
  cat <<'USAGE'
Usage: install-caddy-dashboard-proxy.sh --domain DOMAIN --user USER [--password-file PATH | --password-hash HASH | --password-hash-file PATH] [options]

Installs Caddy from the official stable package repository on Debian-family
systems, configures a loopback-only Hermes dashboard service, and publishes it
through Caddy HTTPS + Basic Auth. Intended to be repeatable and safe for a
public fork: hostnames, paths, hashes, and passwords come from arguments or
host-local aeyeops/.env only.

Required unless provided by aeyeops/.env or environment:
  --domain DOMAIN              Public hostname, e.g. dashboard.example.com
  --user USER                  Basic Auth username
  --password-file PATH         File containing plaintext Basic Auth password;
                               hashed locally after Caddy is installed
  --password-hash HASH         Output from: caddy hash-password; less preferred
  --password-hash-file PATH    File containing the Caddy password hash

Options:
  --hermes-home PATH        Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --repo-dir PATH           Hermes repo dir (default: HERMES_HOME/hermes-agent)
  --port PORT               Local dashboard port (default: 9119)
  --path-prefix PREFIX      Optional public path prefix, e.g. /hermes
  --enable-tui              Enable embedded dashboard TUI/chat
  --skip-caddy-install      Do not install/upgrade Caddy; only configure it
  --skip-verify             Skip the post-install local verification probe
  --dry-run                 Print planned commands/configuration only
  --yes                     Accepted for CI/headless callers; no prompts are used
  -h, --help                Show this help

Useful host-local env keys loaded from aeyeops/.env when present:
  AEX_PORTAL_DOMAIN, AEX_PORTAL_AUTH_USER, AEX_PORTAL_PASSWORD_FILE,
  AEX_PORTAL_PASSWORD_HASH_FILE, AEX_PORTAL_PASSWORD_HASH,
  AEX_PORTAL_PATH_PREFIX, AEX_PORTAL_PORT, HERMES_HOME, HERMES_REPO_DIR
USAGE
}

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

load_local_env() {
  local env_file="${AEYEOPS_ENV_FILE:-$AEEYEOPS_DIR/.env}"
  [[ -f "$env_file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    line="${line%${line##*[![:space:]]}}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#${key%%[![:space:]]*}}"
    key="${key%${key##*[![:space:]]}}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -z "${!key+x}" ]] || continue
    value="${value#${value%%[![:space:]]*}}"
    value="${value%${value##*[![:space:]]}}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$env_file"
}

run() {
  log "+ $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

redacted_args_for_log() {
  local redact_next=0
  local out=()
  local arg
  for arg in "$@"; do
    if [[ "$redact_next" -eq 1 ]]; then
      out+=("<redacted>")
      redact_next=0
      continue
    fi
    out+=("$arg")
    if [[ "$arg" == "--password-hash" ]]; then
      redact_next=1
    fi
  done
  printf '%s ' "${out[@]}"
}

require_root_for_apply() {
  if [[ "$DRY_RUN" -eq 0 && "$(id -u)" -ne 0 ]]; then
    echo "Run as root because this installs Caddy and writes /etc/systemd/system and /etc/caddy." >&2
    exit 1
  fi
}

validate_inputs() {
  if [[ ${#DOMAIN} -gt 253 || ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
    echo "--domain must be a clean hostname like dashboard.example.com." >&2
    exit 2
  fi
  if [[ ! "$AUTH_USER" =~ ^[A-Za-z0-9._~@-]{1,64}$ ]]; then
    echo "--user must contain only A-Z, a-z, 0-9, dot, underscore, tilde, at, or hyphen." >&2
    exit 2
  fi
  if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
    echo "--port must be numeric." >&2
    exit 2
  fi
  if [[ -n "$PATH_PREFIX" ]]; then
    [[ "$PATH_PREFIX" == /* ]] || PATH_PREFIX="/$PATH_PREFIX"
    PATH_PREFIX="${PATH_PREFIX%/}"
    if [[ ! "$PATH_PREFIX" =~ ^/[A-Za-z0-9._~/-]+$ || "$PATH_PREFIX" == "/" || "$PATH_PREFIX" == *"//"* || "$PATH_PREFIX" == *".."* ]]; then
      echo "--path-prefix must be a clean prefix like /hermes." >&2
      exit 2
    fi
  fi
  if [[ -n "$PASSWORD_HASH" ]]; then
    validate_password_hash "$PASSWORD_HASH"
  fi
  if [[ -n "$PASSWORD_HASH_FILE" ]]; then
    if [[ ! -r "$PASSWORD_HASH_FILE" ]]; then
      echo "Password hash file is not readable." >&2
      exit 2
    fi
    validate_password_hash "$(tr -d '\r\n' < "$PASSWORD_HASH_FILE")"
  fi
}

validate_password_hash() {
  local hash="$1"
  if [[ ${#hash} -gt 256 || ! "$hash" =~ ^\$[A-Za-z0-9\$./=,+_-]{10,255}$ ]]; then
    echo "Password hash must look like Caddy hash-password output and must not contain whitespace or Caddyfile syntax." >&2
    exit 2
  fi
}

install_caddy_debian() {
  if command -v caddy >/dev/null 2>&1; then
    log "caddy already installed: $(caddy version 2>/dev/null || true)"
    run systemctl enable --now caddy
    return 0
  fi

  if [[ ! -r /etc/os-release ]]; then
    echo "Cannot detect OS; install Caddy manually or rerun with --skip-caddy-install." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  local os_id="${ID:-}"
  local os_like="${ID_LIKE:-}"
  if [[ "$os_id" != "debian" && "$os_id" != "ubuntu" && "$os_like" != *debian* && "$os_like" != *ubuntu* ]]; then
    echo "Automatic Caddy install currently supports Debian-family systems only; detected ID=${os_id:-unknown} ID_LIKE=${os_like:-unknown}." >&2
    echo "Install Caddy manually from https://caddyserver.com/docs/install, then rerun with --skip-caddy-install." >&2
    exit 1
  fi

  log "installing Caddy from official stable package repository"
  run apt-get update
  run apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates
  run install -d -m 0755 /usr/share/keyrings /etc/apt/sources.list.d
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local key_tmp list_tmp
    key_tmp="$(mktemp)"
    list_tmp="$(mktemp)"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o "$key_tmp"
    install -m 0644 "$key_tmp" /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o "$list_tmp"
    install -m 0644 "$list_tmp" /etc/apt/sources.list.d/caddy-stable.list
    rm -f "$key_tmp" "$list_tmp"
  else
    log "+ curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    log "+ curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list"
  fi
  run chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  run apt-get update
  run apt-get install -y caddy
  run systemctl enable --now caddy
}

load_local_env

DOMAIN="${AEX_PORTAL_DOMAIN:-}"
AUTH_USER="${AEX_PORTAL_AUTH_USER:-}"
PASSWORD_HASH="${AEX_PORTAL_PASSWORD_HASH:-}"
PASSWORD_HASH_FILE="${AEX_PORTAL_PASSWORD_HASH_FILE:-}"
PASSWORD_FILE="${AEX_PORTAL_PASSWORD_FILE:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
REPO_DIR="${HERMES_REPO_DIR:-}"
PORT="${AEX_PORTAL_PORT:-9119}"
PATH_PREFIX="${AEX_PORTAL_PATH_PREFIX:-}"
ENABLE_TUI=0
SKIP_CADDY_INSTALL=0
SKIP_VERIFY=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --user) AUTH_USER="${2:-}"; shift 2 ;;
    --password-file) PASSWORD_FILE="${2:-}"; shift 2 ;;
    --password-hash) PASSWORD_HASH="${2:-}"; shift 2 ;;
    --password-hash-file) PASSWORD_HASH_FILE="${2:-}"; shift 2 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift 2 ;;
    --repo-dir) REPO_DIR="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --path-prefix) PATH_PREFIX="${2:-}"; shift 2 ;;
    --enable-tui) ENABLE_TUI=1; shift ;;
    --skip-caddy-install) SKIP_CADDY_INSTALL=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$DOMAIN" || -z "$AUTH_USER" || ( -z "$PASSWORD_HASH" && -z "$PASSWORD_HASH_FILE" && -z "$PASSWORD_FILE" ) ]]; then
  echo "Missing required domain, user, and password hash source." >&2
  usage >&2
  exit 2
fi

validate_inputs

if [[ ! -x "$SETUP_SCRIPT" ]]; then
  echo "Setup script not found/executable: $SETUP_SCRIPT" >&2
  exit 1
fi
if [[ ! -x "$VERIFY_SCRIPT" ]]; then
  echo "Verify script not found/executable: $VERIFY_SCRIPT" >&2
  exit 1
fi

require_root_for_apply

log "AeyeOps Caddy + Hermes dashboard installation"
log "domain=$DOMAIN hermes_home=$HERMES_HOME repo_dir=${REPO_DIR:-<default>} port=$PORT path_prefix=${PATH_PREFIX:-<root>} dry_run=$DRY_RUN skip_caddy_install=$SKIP_CADDY_INSTALL"

if [[ "$SKIP_CADDY_INSTALL" -eq 0 ]]; then
  install_caddy_debian
else
  log "skipping Caddy install by request"
fi

if [[ -n "$PASSWORD_FILE" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry-run: would generate Caddy password hash from password file: $PASSWORD_FILE"
    PASSWORD_HASH='$DRY_RUN_GENERATED_PASSWORD_HASH'
    PASSWORD_HASH_FILE=""
  else
    if [[ ! -r "$PASSWORD_FILE" ]]; then
      echo "Password file is not readable: $PASSWORD_FILE" >&2
      exit 2
    fi
    if ! command -v caddy >/dev/null 2>&1; then
      echo "caddy is not installed; cannot hash --password-file." >&2
      exit 1
    fi
    log "generating Caddy password hash from password file"
    PASSWORD_HASH="$(caddy hash-password < "$PASSWORD_FILE" | tr -d '\r\n')"
    validate_password_hash "$PASSWORD_HASH"
    PASSWORD_HASH_FILE=""
  fi
fi

setup_args=(
  --domain "$DOMAIN"
  --user "$AUTH_USER"
  --hermes-home "$HERMES_HOME"
  --port "$PORT"
  --yes
)
if [[ -n "$PASSWORD_HASH_FILE" ]]; then
  setup_args+=(--password-hash-file "$PASSWORD_HASH_FILE")
else
  setup_args+=(--password-hash "$PASSWORD_HASH")
fi
if [[ -n "$REPO_DIR" ]]; then
  setup_args+=(--repo-dir "$REPO_DIR")
fi
if [[ -n "$PATH_PREFIX" ]]; then
  setup_args+=(--path-prefix "$PATH_PREFIX")
fi
if [[ "$ENABLE_TUI" -eq 1 ]]; then
  setup_args+=(--enable-tui)
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  setup_args+=(--dry-run)
fi

log "+ $SETUP_SCRIPT $(redacted_args_for_log "${setup_args[@]}")"
"$SETUP_SCRIPT" "${setup_args[@]}"

if [[ "$DRY_RUN" -eq 0 && "$SKIP_VERIFY" -eq 0 ]]; then
  verify_args=(--domain "$DOMAIN" --hermes-home "$HERMES_HOME" --port "$PORT" --skip-external)
  if [[ -n "$PATH_PREFIX" ]]; then
    verify_args+=(--path-prefix "$PATH_PREFIX")
  fi
  run "$VERIFY_SCRIPT" "${verify_args[@]}"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: skipped post-install verification"
else
  log "post-install verification skipped by request"
fi

log "Install complete. Run verify-dashboard-proxy.sh without --skip-external after DNS and credentials are ready."
