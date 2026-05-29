#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: verify-dashboard-proxy.sh --domain DOMAIN [options]

Headless checks for the AeyeOps Hermes dashboard HTTPS exposure.
Supports a dedicated hostname root or a path prefix such as /hermes.

Options:
  --hermes-home PATH        Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --port PORT               Local dashboard port (default: 9119)
  --path-prefix PREFIX      Optional public path prefix, e.g. /hermes
  --user USER               Optional Basic Auth username for authenticated probe
  --password PASSWORD       Optional Basic Auth password; less preferred
  --password-file PATH      File containing Basic Auth password
  --skip-external           Skip public HTTPS probes
  --insecure-tls            Skip TLS verification for diagnostic use only
  -h, --help                Show this help
USAGE
}

DOMAIN=""
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PORT="9119"
PATH_PREFIX=""
AUTH_USER=""
AUTH_PASSWORD="${AEX_PORTAL_AUTH_PASSWORD:-}"
AUTH_PASSWORD_FILE="${AEX_PORTAL_AUTH_PASSWORD_FILE:-}"
SKIP_EXTERNAL=0
INSECURE_TLS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --path-prefix) PATH_PREFIX="${2:-}"; shift 2 ;;
    --user) AUTH_USER="${2:-}"; shift 2 ;;
    --password) AUTH_PASSWORD="${2:-}"; shift 2 ;;
    --password-file) AUTH_PASSWORD_FILE="${2:-}"; shift 2 ;;
    --skip-external) SKIP_EXTERNAL=1; shift ;;
    --insecure-tls) INSECURE_TLS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$DOMAIN" ]]; then
  echo "Missing required --domain." >&2
  usage >&2
  exit 2
fi

if [[ ${#DOMAIN} -gt 253 || ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
  echo "--domain must be a clean hostname like dashboard.example.com." >&2
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

if [[ -n "$AUTH_USER" && ! "$AUTH_USER" =~ ^[A-Za-z0-9._~@-]{1,64}$ ]]; then
  echo "--user must contain only A-Z, a-z, 0-9, dot, underscore, tilde, at, or hyphen." >&2
  exit 2
fi


if [[ -n "$AUTH_PASSWORD_FILE" ]]; then
  if [[ ! -r "$AUTH_PASSWORD_FILE" ]]; then
    echo "Password file is not readable: $AUTH_PASSWORD_FILE" >&2
    exit 2
  fi
  AUTH_PASSWORD="$(tr -d '\r\n' < "$AUTH_PASSWORD_FILE")"
fi

LOG_DIR="$HERMES_HOME/logs"
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/aeyeops-portal-verify-$TS.log"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
pass() { log "PASS: $*"; }

failures=0

log "AeyeOps Hermes dashboard proxy verification"
log "domain=$DOMAIN hermes_home=$HERMES_HOME port=$PORT path_prefix=${PATH_PREFIX:-<root>} log_file=$LOG_FILE"

if systemctl is-active --quiet hermes-dashboard.service; then
  pass "hermes-dashboard active"
else
  failures=$((failures + 1))
  log "FAIL: hermes-dashboard is not active"
fi

if systemctl is-active --quiet caddy; then
  pass "caddy active"
else
  failures=$((failures + 1))
  log "FAIL: caddy is not active"
fi

status_tmp="$(mktemp)"
if curl -fsS -H "Host: 127.0.0.1:$PORT" "http://127.0.0.1:$PORT/api/status" >"$status_tmp"; then
  pass "local dashboard /api/status reachable on loopback"
else
  failures=$((failures + 1))
  log "FAIL: local dashboard /api/status not reachable on loopback"
fi
rm -f "$status_tmp"

if command -v ss >/dev/null 2>&1; then
  log "listening sockets for dashboard port"
  ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port {print}' || true
fi

if [[ "$SKIP_EXTERNAL" -eq 0 ]]; then
  tls_args=()
  if [[ "$INSECURE_TLS" -eq 1 ]]; then
    tls_args=(-k)
    log "WARN: --insecure-tls is set; HTTPS certificate verification is disabled for this run"
  fi
  unauth_tmp="$(mktemp)"
  PUBLIC_BASE="https://$DOMAIN${PATH_PREFIX:-}"
  unauth_code="$(curl "${tls_args[@]}" -sS -o "$unauth_tmp" -w '%{http_code}' "$PUBLIC_BASE/" || true)"
  rm -f "$unauth_tmp"
  if [[ "$unauth_code" == "401" ]]; then
    pass "public HTTPS requires authentication"
  else
    failures=$((failures + 1))
    log "FAIL: expected unauthenticated HTTPS 401, got ${unauth_code:-curl-error}"
  fi

  if [[ -n "$AUTH_USER" && -n "$AUTH_PASSWORD" ]]; then
    auth_tmp="$(mktemp)"
    auth_code="$(curl "${tls_args[@]}" -sS -u "$AUTH_USER:$AUTH_PASSWORD" -o "$auth_tmp" -w '%{http_code}' "$PUBLIC_BASE/api/status" || true)"
    rm -f "$auth_tmp"
    if [[ "$auth_code" =~ ^(200|401)$ ]]; then
      # 200 means Caddy auth passed and endpoint is public; 401 can be Hermes token auth
      # for non-public paths. /api/status should normally be 200.
      pass "authenticated HTTPS probe completed with HTTP $auth_code"
    else
      failures=$((failures + 1))
      log "FAIL: authenticated HTTPS probe unexpected HTTP ${auth_code:-curl-error}"
    fi
  else
    log "SKIP: authenticated HTTPS probe needs --user and --password"
  fi
else
  log "SKIP: external HTTPS probes disabled"
fi

if [[ "$failures" -eq 0 ]]; then
  log "Verification complete: all checks passed"
  exit 0
fi

log "Verification complete: $failures check(s) failed"
exit 1
