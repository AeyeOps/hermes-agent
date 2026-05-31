#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEEYEOPS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: verify-portal-stack.sh --dashboard-domain DOMAIN --webui-domain DOMAIN --mc-domain DOMAIN --auth-domain DOMAIN [options]

Headless checks for the AEyeOps HTTPS portal stack behind Authelia.
Verifies loopback services and, unless skipped, unauthenticated public HTTPS
requests redirect to the Authelia login portal instead of exposing an app.

Options:
  --dashboard-domain DOMAIN  Hermes dashboard hostname
  --webui-domain DOMAIN      Hermes WebUI hostname
  --mc-domain DOMAIN         Mission Control hostname
  --auth-domain DOMAIN       Authelia login hostname
  --hermes-home PATH         Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --dashboard-port PORT      Local dashboard port (default: 9119)
  --webui-port PORT          Local WebUI port (default: 8787)
  --mc-port PORT             Local Mission Control port (default: 3000)
  --authelia-port PORT       Local Authelia port (default: 9091)
  --skip-external            Skip public HTTPS probes
  --public-ip IP             Use explicit public IP for HTTPS probes
  --insecure-tls             Skip TLS verification for diagnostic use only
  -h, --help                 Show this help
USAGE
}


load_local_env() {
  local env_file="${AEYEOPS_ENV_FILE:-$AEEYEOPS_DIR/.env}"
  [[ -f "$env_file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"; value="${line#*=}"; key="${key#export }"
    key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -z "${!key+x}" ]] || continue
    value="${value#"${value%%[![:space:]]*}"}"; value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
    if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
    export "$key=$value"
  done < "$env_file"
}

load_local_env
DASHBOARD_DOMAIN="${AEX_DASHBOARD_DOMAIN:-}"
WEBUI_DOMAIN="${AEX_WEBUI_DOMAIN:-}"
MC_DOMAIN="${AEX_MC_DOMAIN:-}"
AUTH_DOMAIN="${AEX_AUTHELIA_DOMAIN:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DASHBOARD_PORT="${AEX_DASHBOARD_PORT:-9119}"
WEBUI_PORT="${AEX_WEBUI_PORT:-8787}"
MC_PORT="${AEX_MC_PORT:-3000}"
AUTHELIA_PORT="${AEX_AUTHELIA_PORT:-9091}"
PUBLIC_IP="${AEX_PORTAL_PUBLIC_IP:-}"
SKIP_EXTERNAL=0
INSECURE_TLS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard-domain) DASHBOARD_DOMAIN="${2:-}"; shift 2 ;;
    --webui-domain) WEBUI_DOMAIN="${2:-}"; shift 2 ;;
    --mc-domain) MC_DOMAIN="${2:-}"; shift 2 ;;
    --auth-domain) AUTH_DOMAIN="${2:-}"; shift 2 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift 2 ;;
    --dashboard-port) DASHBOARD_PORT="${2:-}"; shift 2 ;;
    --webui-port) WEBUI_PORT="${2:-}"; shift 2 ;;
    --mc-port) MC_PORT="${2:-}"; shift 2 ;;
    --authelia-port) AUTHELIA_PORT="${2:-}"; shift 2 ;;
    --public-ip) PUBLIC_IP="${2:-}"; shift 2 ;;
    --skip-external) SKIP_EXTERNAL=1; shift ;;
    --insecure-tls) INSECURE_TLS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

validate_domain() {
  local label="$1" domain="$2"
  if [[ -z "$domain" || ${#domain} -gt 253 || ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
    echo "$label must be a clean hostname like dashboard.example.com." >&2
    exit 2
  fi
}

validate_port() {
  local label="$1" port="$2"
  if [[ ! "$port" =~ ^[0-9]+$ || "$port" -lt 1 || "$port" -gt 65535 ]]; then
    echo "$label must be 1-65535." >&2
    exit 2
  fi
}

for item in \
  "--dashboard-domain:$DASHBOARD_DOMAIN" \
  "--webui-domain:$WEBUI_DOMAIN" \
  "--mc-domain:$MC_DOMAIN" \
  "--auth-domain:$AUTH_DOMAIN"; do
  validate_domain "${item%%:*}" "${item#*:}"
done
for item in \
  "--dashboard-port:$DASHBOARD_PORT" \
  "--webui-port:$WEBUI_PORT" \
  "--mc-port:$MC_PORT" \
  "--authelia-port:$AUTHELIA_PORT"; do
  validate_port "${item%%:*}" "${item#*:}"
done

LOG_DIR="$HERMES_HOME/logs"
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/aeyeops-portal-verify-$TS.log"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
pass() { log "PASS: $*"; }
failures=0

record_failure() {
  failures=$((failures + 1))
  log "FAIL: $*"
}

check_service() {
  local service="$1"
  if systemctl is-active --quiet "$service"; then
    pass "$service active"
  else
    record_failure "$service is not active"
  fi
}

check_loopback() {
  local label="$1" url="$2"
  if curl -fsS --max-time 10 "$url" >/dev/null; then
    pass "$label reachable on loopback"
  else
    record_failure "$label not reachable on loopback at $url"
  fi
}

public_resolve_ip() {
  if [[ -n "$PUBLIC_IP" ]]; then
    printf '%s\n' "$PUBLIC_IP"
    return 0
  fi
  if command -v dig >/dev/null 2>&1; then
    dig +short @1.1.1.1 A "$AUTH_DOMAIN" | awk 'NF {print; exit}'
  fi
}

check_external_redirect() {
  local label="$1" domain="$2" ip="$3" headers code
  headers="$(mktemp)"
  curl_args=(-sS -o /dev/null -D "$headers" -w '%{http_code}' --max-time 20)
  if [[ "$INSECURE_TLS" -eq 1 ]]; then
    curl_args=(-k "${curl_args[@]}")
  fi
  if [[ -n "$ip" ]]; then
    curl_args+=(--resolve "$domain:443:$ip")
  fi
  code="$(curl "${curl_args[@]}" "https://$domain/" || true)"
  if [[ "$code" =~ ^(302|303)$ ]] && grep -qi "^location: https://$AUTH_DOMAIN" "$headers"; then
    pass "public HTTPS redirects unauthenticated $label request to Authelia"
  else
    record_failure "expected Authelia redirect for $label, got HTTP ${code:-curl-error}"
  fi
  rm -f "$headers"
}

log "AEyeOps portal stack verification"
log "dashboard=$DASHBOARD_DOMAIN:$DASHBOARD_PORT webui=$WEBUI_DOMAIN:$WEBUI_PORT mc=$MC_DOMAIN:$MC_PORT auth=$AUTH_DOMAIN:$AUTHELIA_PORT hermes_home=$HERMES_HOME log_file=$LOG_FILE"

check_service caddy.service
check_service aeyeops-authelia.service
check_service hermes-dashboard.service
check_service hermes-webui.service
check_service mission-control.service

check_loopback "dashboard /api/status" "http://127.0.0.1:$DASHBOARD_PORT/api/status"
check_loopback "webui /health" "http://127.0.0.1:$WEBUI_PORT/health"
check_loopback "mission-control health" "http://127.0.0.1:$MC_PORT/api/status?action=health"
check_loopback "authelia" "http://127.0.0.1:$AUTHELIA_PORT/api/health"

if command -v ss >/dev/null 2>&1; then
  log "listening sockets for portal ports"
  ss -ltnp 2>/dev/null | awk -v dp=":$DASHBOARD_PORT" -v wp=":$WEBUI_PORT" -v mp=":$MC_PORT" -v ap=":$AUTHELIA_PORT" '$4 ~ dp || $4 ~ wp || $4 ~ mp || $4 ~ ap {print}' || true
fi

if [[ "$SKIP_EXTERNAL" -eq 0 ]]; then
  if [[ "$INSECURE_TLS" -eq 1 ]]; then
    log "WARN: --insecure-tls is set; HTTPS certificate verification is disabled for this run"
  fi
  probe_ip="$(public_resolve_ip || true)"
  if [[ -n "$probe_ip" ]]; then
    log "using explicit public resolver IP for HTTPS probes"
  else
    log "using system resolver for HTTPS probes"
  fi
  check_external_redirect dashboard "$DASHBOARD_DOMAIN" "$probe_ip"
  check_external_redirect webui "$WEBUI_DOMAIN" "$probe_ip"
  check_external_redirect mission-control "$MC_DOMAIN" "$probe_ip"
else
  log "SKIP: external HTTPS probes disabled"
fi

if [[ "$failures" -eq 0 ]]; then
  log "Verification complete: all checks passed"
  exit 0
fi

log "Verification complete: $failures failure(s)"
exit 1
