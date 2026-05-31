#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEEYEOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: install-authelia-portal-auth.sh --auth-domain DOMAIN --dashboard-domain DOMAIN --webui-domain DOMAIN --mc-domain DOMAIN --user USER [--user-password-hash-file PATH | --user-password-file PATH] [options]

Installs/configures the AEyeOps Authelia portal login stack behind Caddy.
Caddy remains the public TLS reverse proxy. Authelia is the only generated
browser-facing authentication gate; this script does not create alternate
challenge snippets, backup-auth snippets, or alternate routes.

Required unless provided by aeyeops/.env or environment:
  --auth-domain DOMAIN              Authelia portal hostname, e.g. auth.example.com
  --dashboard-domain DOMAIN         Dashboard hostname
  --webui-domain DOMAIN             WebUI hostname
  --mc-domain DOMAIN                Mission Control hostname
  --user USER                       Initial Authelia username
  --user-password-hash-file PATH    File containing Authelia-compatible password hash

Options:
  --user-password-hash HASH         Password hash directly; use only for tests/dry-run
  --user-password-file PATH         Plaintext password file; hashed locally with Authelia
  --user-email EMAIL                Initial user email (default: USER@example.invalid)
  --display-name NAME               Initial user display name (default: USER)
  --domain-root DOMAIN              Cookie domain (default: last two labels of auth domain)
  --default-redirect-url URL        Default post-login URL (default: dashboard URL)
  --authelia-port PORT              Loopback Authelia port (default: 9091)
  --dashboard-port PORT             Local dashboard port (default: 9119)
  --webui-port PORT                 Local WebUI port (default: 8787)
  --mc-port PORT                    Local Mission Control port (default: 3000)
  --hermes-home PATH                Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --config-dir PATH                 Host-local Authelia config dir
  --install-authelia                Install Authelia from official APT repo when missing
  --skip-authelia-install           Do not attempt package install (default)
  --write-auth-portal               Write the auth hostname Caddy snippet
  --write-app-snippets              Write all app Caddy snippets with forward_auth
  --validate-only                   Validate generated config/snippets without writing live files
  --skip-caddy-reload               Write files but do not reload Caddy
  --dry-run                         Print actions/config with secrets redacted
  --yes                             Accepted for headless callers; no prompts are used
  -h, --help                        Show this help

Useful host-local env keys:
  AEX_AUTHELIA_DOMAIN, AEX_DASHBOARD_DOMAIN, AEX_WEBUI_DOMAIN, AEX_MC_DOMAIN,
  AEX_AUTHELIA_USER, AEX_AUTHELIA_USER_EMAIL, AEX_AUTHELIA_DISPLAY_NAME,
  AEX_AUTHELIA_USER_PASSWORD_HASH_FILE, AEX_AUTHELIA_USER_PASSWORD_HASH,
  AEX_AUTHELIA_USER_PASSWORD_FILE,
  AEX_AUTHELIA_DOMAIN_ROOT, AEX_AUTHELIA_PORT, AEX_AUTHELIA_CONFIG_DIR,
  AEX_DASHBOARD_PORT, AEX_WEBUI_PORT, AEX_MC_PORT, HERMES_HOME
USAGE
}

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { echo "$*" >&2; exit 1; }

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

DRY_RUN=0
VALIDATE_ONLY=0
INSTALL_AUTHELIA=0
WRITE_AUTH_PORTAL=0
WRITE_APP_SNIPPETS=0
SKIP_CADDY_RELOAD=0

load_local_env

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AUTH_DOMAIN="${AEX_AUTHELIA_DOMAIN:-}"
DASHBOARD_DOMAIN="${AEX_DASHBOARD_DOMAIN:-}"
WEBUI_DOMAIN="${AEX_WEBUI_DOMAIN:-}"
MC_DOMAIN="${AEX_MC_DOMAIN:-}"
AUTH_USER="${AEX_AUTHELIA_USER:-}"
AUTH_EMAIL="${AEX_AUTHELIA_USER_EMAIL:-}"
DISPLAY_NAME="${AEX_AUTHELIA_DISPLAY_NAME:-}"
PASSWORD_HASH_FILE="${AEX_AUTHELIA_USER_PASSWORD_HASH_FILE:-}"
PASSWORD_HASH="${AEX_AUTHELIA_USER_PASSWORD_HASH:-}"
PASSWORD_FILE="${AEX_AUTHELIA_USER_PASSWORD_FILE:-}"
DOMAIN_ROOT="${AEX_AUTHELIA_DOMAIN_ROOT:-}"
AUTHELIA_PORT="${AEX_AUTHELIA_PORT:-9091}"
DASHBOARD_PORT="${AEX_DASHBOARD_PORT:-9119}"
WEBUI_PORT="${AEX_WEBUI_PORT:-8787}"
MC_PORT="${AEX_MC_PORT:-3000}"
CONFIG_DIR="${AEX_AUTHELIA_CONFIG_DIR:-}"
DEFAULT_REDIRECT_URL="${AEX_AUTHELIA_DEFAULT_REDIRECT_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auth-domain) AUTH_DOMAIN="${2:-}"; shift 2 ;;
    --dashboard-domain) DASHBOARD_DOMAIN="${2:-}"; shift 2 ;;
    --webui-domain) WEBUI_DOMAIN="${2:-}"; shift 2 ;;
    --mc-domain) MC_DOMAIN="${2:-}"; shift 2 ;;
    --user) AUTH_USER="${2:-}"; shift 2 ;;
    --user-email) AUTH_EMAIL="${2:-}"; shift 2 ;;
    --display-name) DISPLAY_NAME="${2:-}"; shift 2 ;;
    --user-password-hash-file) PASSWORD_HASH_FILE="${2:-}"; shift 2 ;;
    --user-password-hash) PASSWORD_HASH="${2:-}"; shift 2 ;;
    --user-password-file) PASSWORD_FILE="${2:-}"; shift 2 ;;
    --domain-root) DOMAIN_ROOT="${2:-}"; shift 2 ;;
    --default-redirect-url) DEFAULT_REDIRECT_URL="${2:-}"; shift 2 ;;
    --authelia-port) AUTHELIA_PORT="${2:-}"; shift 2 ;;
    --dashboard-port) DASHBOARD_PORT="${2:-}"; shift 2 ;;
    --webui-port) WEBUI_PORT="${2:-}"; shift 2 ;;
    --mc-port) MC_PORT="${2:-}"; shift 2 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:-}"; shift 2 ;;
    --install-authelia) INSTALL_AUTHELIA=1; shift ;;
    --skip-authelia-install) INSTALL_AUTHELIA=0; shift ;;
    --write-auth-portal) WRITE_AUTH_PORTAL=1; shift ;;
    --write-app-snippets) WRITE_APP_SNIPPETS=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    --skip-caddy-reload) SKIP_CADDY_RELOAD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

CONFIG_DIR="${CONFIG_DIR:-$HERMES_HOME/aeyeops-portal/authelia}"
AUTH_EMAIL="${AUTH_EMAIL:-$AUTH_USER@example.invalid}"
DISPLAY_NAME="${DISPLAY_NAME:-$AUTH_USER}"
DEFAULT_REDIRECT_URL="${DEFAULT_REDIRECT_URL:-https://$DASHBOARD_DOMAIN/}"

validate_domain() {
  local label="$1" domain="$2"
  [[ -n "$domain" ]] || fail "$label is required."
  if [[ ${#domain} -gt 253 || ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
    fail "$label must be a clean hostname like auth.example.com."
  fi
}

validate_port() {
  if [[ ! "$2" =~ ^[0-9]+$ ]] || (( $2 < 1 || $2 > 65535 )); then
    fail "$1 must be 1-65535."
  fi
}
validate_user() { [[ "$1" =~ ^[A-Za-z0-9._~@-]{1,64}$ ]] || fail "--user contains unsupported characters."; }
validate_hash() { [[ "$1" =~ ^\$[A-Za-z0-9./$,+=_-]{20,300}$ ]] || fail "Authelia password hash must look like Argon2/bcrypt/scrypt/PBKDF2 output and contain no whitespace."; }

last_two_labels() {
  local d="$1" rest last second
  last="${d##*.}"; rest="${d%.*}"; second="${rest##*.}"
  printf '%s.%s\n' "$second" "$last"
}

[[ -n "$DOMAIN_ROOT" ]] || DOMAIN_ROOT="$(last_two_labels "$AUTH_DOMAIN")"
[[ -n "$AUTH_USER" ]] || fail "--user or AEX_AUTHELIA_USER is required."
validate_domain "--auth-domain" "$AUTH_DOMAIN"
validate_domain "--dashboard-domain" "$DASHBOARD_DOMAIN"
validate_domain "--webui-domain" "$WEBUI_DOMAIN"
validate_domain "--mc-domain" "$MC_DOMAIN"
validate_domain "--domain-root" "$DOMAIN_ROOT"
validate_port "--authelia-port" "$AUTHELIA_PORT"
validate_port "--dashboard-port" "$DASHBOARD_PORT"
validate_port "--webui-port" "$WEBUI_PORT"
validate_port "--mc-port" "$MC_PORT"
validate_user "$AUTH_USER"

resolve_password_hash() {
  if [[ -n "$PASSWORD_HASH" ]]; then
    validate_hash "$PASSWORD_HASH"
    return 0
  fi
  if [[ -n "$PASSWORD_HASH_FILE" ]]; then
    [[ -r "$PASSWORD_HASH_FILE" ]] || fail "Password hash file is not readable: $PASSWORD_HASH_FILE"
    PASSWORD_HASH="$(tr -d '
' < "$PASSWORD_HASH_FILE")"
    validate_hash "$PASSWORD_HASH"
    return 0
  fi
  if [[ -n "$PASSWORD_FILE" ]]; then
    if [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]]; then
      # shellcheck disable=SC2016 # Literal disposable dry-run hash.
      PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$YWJjZGVmZ2hpamtsbW5vcA$YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo'
      return 0
    fi
    [[ -r "$PASSWORD_FILE" ]] || fail "Password file is not readable: $PASSWORD_FILE"
    command -v authelia >/dev/null 2>&1 || fail "authelia is not installed; cannot hash --user-password-file."
    local generated_hash_file="${AEX_AUTHELIA_GENERATED_PASSWORD_HASH_FILE:-$HERMES_HOME/aeyeops-portal/authelia-password-hash}"
    if [[ -s "$generated_hash_file" ]]; then
      PASSWORD_HASH="$(tr -d '
' < "$generated_hash_file")"
    else
      local tmp pw
      tmp="$(mktemp)"
      pw="$(<"$PASSWORD_FILE")"
      authelia crypto hash generate argon2 --password "$pw" --no-confirm | awk '/Digest:/ {print $2}' > "$tmp"
      unset pw
      [[ -s "$tmp" ]] || { rm -f "$tmp"; fail "Failed to generate Authelia password hash."; }
      install -D -m 0600 "$tmp" "$generated_hash_file"
      rm -f "$tmp"
      PASSWORD_HASH="$(tr -d '
' < "$generated_hash_file")"
    fi
    validate_hash "$PASSWORD_HASH"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]]; then
    # shellcheck disable=SC2016 # Literal disposable dry-run hash.
    PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$YWJjZGVmZ2hpamtsbW5vcA$YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo'
    return 0
  fi
  fail "A password hash file, hash, or password file is required."
}

AUTH_SNIPPET="/etc/caddy/conf.d/aeyeops-authelia.caddy"
DASH_SNIPPET="/etc/caddy/conf.d/hermes-dashboard.caddy"
WEBUI_SNIPPET="/etc/caddy/conf.d/aeyeops-webui.caddy"
MC_SNIPPET="/etc/caddy/conf.d/aeyeops-mission-control.caddy"
UNIT_FILE="/etc/systemd/system/aeyeops-authelia.service"
ENV_FILE="$CONFIG_DIR/authelia.env"
CONFIG_FILE="$CONFIG_DIR/configuration.yml"
USERS_FILE="$CONFIG_DIR/users_database.yml"
SECRETS_DIR="$CONFIG_DIR/secrets"

redact() { sed -E 's#(password: ).*#\1<redacted>#; s#(AUTHELIA_.*_FILE=).*#\1<redacted>#'; }
run() { log "+ $*"; [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]] || "$@"; }

write_file() {
  local path="$1" mode="$2" tmp
  tmp="$(mktemp)"; cat >"$tmp"
  log "write $path"
  if [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]]; then
    sed -E "s#${PASSWORD_HASH//\/\\}#<redacted>#g" "$tmp" | redact | sed 's/^/    | /'
  else
    install -D -m "$mode" "$tmp" "$path"
  fi
  rm -f "$tmp"
}

protect_caddy_site_file() {
  local path="$1"
  [[ "$DRY_RUN" -eq 0 && "$VALIDATE_ONLY" -eq 0 ]] || return 0
  if getent group caddy >/dev/null 2>&1; then
    chown root:caddy "$path"
    chmod 0640 "$path"
  fi
}

install_authelia_if_requested() {
  if command -v authelia >/dev/null 2>&1; then
    log "authelia already available: $(authelia --version 2>/dev/null | head -n1 || true)"
    return
  fi
  [[ "$INSTALL_AUTHELIA" -eq 1 ]] || { log "authelia binary not found; skipping package install by request"; return; }
  [[ -r /etc/os-release ]] || fail "Cannot detect OS; install Authelia manually."
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-} ${ID_LIKE:-}" == *ubuntu* || "${ID:-} ${ID_LIKE:-}" == *debian* ]] || fail "Automatic Authelia install supports Debian-family systems only."
  run apt-get update
  run apt-get install -y ca-certificates curl gnupg
  run install -d -m 0755 /usr/share/keyrings
  run curl -fsSL https://www.authelia.com/keys/authelia-security.gpg -o /usr/share/keyrings/authelia-security.gpg
  if [[ "$DRY_RUN" -eq 0 && "$VALIDATE_ONLY" -eq 0 ]]; then
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/authelia-security.gpg] https://apt.authelia.com stable main" > /etc/apt/sources.list.d/authelia.list
  else
    log "would write /etc/apt/sources.list.d/authelia.list"
  fi
  run apt-get update
  run apt-get install -y authelia
}

write_authelia_files() {
  write_file "$CONFIG_FILE" 0640 <<YAML
server:
  address: 'tcp://127.0.0.1:$AUTHELIA_PORT/'

log:
  level: 'info'

theme: 'auto'

authentication_backend:
  file:
    path: '$USERS_FILE'
    watch: false
    password:
      algorithm: 'argon2'
      argon2:
        variant: 'argon2id'

access_control:
  default_policy: 'deny'
  rules:
    - domain:
        - '$DASHBOARD_DOMAIN'
        - '$WEBUI_DOMAIN'
        - '$MC_DOMAIN'
      policy: 'one_factor'

session:
  cookies:
    - domain: '$DOMAIN_ROOT'
      authelia_url: 'https://$AUTH_DOMAIN'
      default_redirection_url: '$DEFAULT_REDIRECT_URL'
      same_site: 'lax'
      inactivity: '30m'
      expiration: '12h'
      remember_me: '1M'

regulation:
  max_retries: 5
  find_time: '2m'
  ban_time: '5m'

storage:
  local:
    path: '$CONFIG_DIR/db.sqlite3'

notifier:
  filesystem:
    filename: '$CONFIG_DIR/notification.txt'
YAML

  write_file "$USERS_FILE" 0640 <<YAML
users:
  $AUTH_USER:
    displayname: '$DISPLAY_NAME'
    password: '$PASSWORD_HASH'
    email: '$AUTH_EMAIL'
    groups:
      - admins
YAML

  write_file "$ENV_FILE" 0600 <<ENV
AUTHELIA_SESSION_SECRET_FILE=$SECRETS_DIR/session_secret
AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE=$SECRETS_DIR/storage_encryption_key
AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE=$SECRETS_DIR/jwt_secret
ENV

  if [[ "$DRY_RUN" -eq 0 && "$VALIDATE_ONLY" -eq 0 ]]; then
    install -d -m 0700 "$SECRETS_DIR"
    for s in session_secret storage_encryption_key jwt_secret; do
      if [[ ! -s "$SECRETS_DIR/$s" ]]; then
        umask 077; openssl rand -base64 48 > "$SECRETS_DIR/$s"
      fi
    done
  else
    log "would ensure secret files under $SECRETS_DIR (values not printed)"
  fi
}

write_systemd_unit() {
  write_file "$UNIT_FILE" 0644 <<UNIT
[Unit]
Description=AEyeOps Authelia portal auth (loopback only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/authelia --config $CONFIG_FILE
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$CONFIG_DIR

[Install]
WantedBy=multi-user.target
UNIT
}

protected_snippet() {
  local domain="$1" port="$2" name="$3" host_rewrite="${4:-}"
  cat <<CADDY
$domain {
	encode zstd gzip

	log {
		output file /var/log/caddy/$name-access.log {
			roll_size 10MiB
			roll_keep 5
			roll_keep_for 720h
		}
		format console
	}

	forward_auth 127.0.0.1:$AUTHELIA_PORT {
		uri /api/authz/forward-auth
		copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
	}

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		X-Frame-Options "DENY"
	}

	reverse_proxy 127.0.0.1:$port {
CADDY
  [[ -n "$host_rewrite" ]] && printf '\t\theader_up Host %s\n' "$host_rewrite"
  cat <<'CADDY'
		header_up X-Forwarded-Proto https
	}
}
CADDY
}

write_caddy_auth_portal() {
  write_file "$AUTH_SNIPPET" 0640 <<CADDY
$AUTH_DOMAIN {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		X-Frame-Options "DENY"
	}
	reverse_proxy 127.0.0.1:$AUTHELIA_PORT
}
CADDY
  protect_caddy_site_file "$AUTH_SNIPPET"
}

write_app_snippets() {
  [[ "$WRITE_APP_SNIPPETS" -eq 1 ]] || return 0
  protected_snippet "$DASHBOARD_DOMAIN" "$DASHBOARD_PORT" hermes-dashboard "127.0.0.1:$DASHBOARD_PORT" | write_file "$DASH_SNIPPET" 0640
  protect_caddy_site_file "$DASH_SNIPPET"
  protected_snippet "$WEBUI_DOMAIN" "$WEBUI_PORT" hermes-webui "" | write_file "$WEBUI_SNIPPET" 0640
  protect_caddy_site_file "$WEBUI_SNIPPET"
  protected_snippet "$MC_DOMAIN" "$MC_PORT" mission-control "" | write_file "$MC_SNIPPET" 0640
  protect_caddy_site_file "$MC_SNIPPET"
}

validate_candidate_caddy() {
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  {
    printf '{\n  admin off\n  auto_https off\n}\n\n'
    printf 'http://%s {\n  reverse_proxy 127.0.0.1:%s\n}\n\n' "$AUTH_DOMAIN" "$AUTHELIA_PORT"
    protected_snippet "$DASHBOARD_DOMAIN" "$DASHBOARD_PORT" hermes-dashboard "127.0.0.1:$DASHBOARD_PORT" | sed "s#^$DASHBOARD_DOMAIN#http://$DASHBOARD_DOMAIN#"
    protected_snippet "$WEBUI_DOMAIN" "$WEBUI_PORT" hermes-webui "" | sed "s#^$WEBUI_DOMAIN#http://$WEBUI_DOMAIN#"
    protected_snippet "$MC_DOMAIN" "$MC_PORT" mission-control "" | sed "s#^$MC_DOMAIN#http://$MC_DOMAIN#"
  } > "$tmp/Caddyfile"
  caddy validate --config "$tmp/Caddyfile" --adapter caddyfile || return 1
  log "temporary Caddy forward_auth validation passed"
}

static_validate_authelia_config() {
  local file="${1:-$CONFIG_FILE}" failures=0
  grep -q "address: 'tcp://127.0.0.1:$AUTHELIA_PORT/'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q "authelia_url: 'https://$AUTH_DOMAIN'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q "default_redirection_url: '$DEFAULT_REDIRECT_URL'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q "domain: '$DOMAIN_ROOT'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q -- "- '$DASHBOARD_DOMAIN'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q -- "- '$WEBUI_DOMAIN'" "$file" 2>/dev/null || failures=$((failures+1))
  grep -q -- "- '$MC_DOMAIN'" "$file" 2>/dev/null || failures=$((failures+1))
  [[ "$failures" -eq 0 ]] || { log "Static Authelia config acceptance gate failed ($failures missing values)."; return 1; }
  log "static Authelia config acceptance gate passed"
}

validate_live_files() {
  if [[ "$DRY_RUN" -eq 0 && "$VALIDATE_ONLY" -eq 0 ]]; then
    static_validate_authelia_config || return 1
    if command -v authelia >/dev/null 2>&1; then
      log "+ authelia validate-config --config $CONFIG_FILE"
      if [[ "$DRY_RUN" -eq 0 && "$VALIDATE_ONLY" -eq 0 ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
        authelia validate-config --config "$CONFIG_FILE" || return 1
      fi
    else
      log "authelia binary unavailable; skipped authelia validate-config"
    fi
    run caddy validate --config /etc/caddy/Caddyfile || return 1
  else
    log "dry-run/validate-only: rendering temporary Caddyfile for validation"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' RETURN
    cat > "$tmp/authelia-config-check.yml" <<YAML
server:
  address: 'tcp://127.0.0.1:$AUTHELIA_PORT/'
access_control:
  rules:
    - domain:
        - '$DASHBOARD_DOMAIN'
        - '$WEBUI_DOMAIN'
        - '$MC_DOMAIN'
session:
  cookies:
    - domain: '$DOMAIN_ROOT'
      authelia_url: 'https://$AUTH_DOMAIN'
      default_redirection_url: '$DEFAULT_REDIRECT_URL'
YAML
    static_validate_authelia_config "$tmp/authelia-config-check.yml" || return 1
    validate_candidate_caddy || return 1
  fi
}

post_apply() {
  [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]] && return 0
  run systemctl daemon-reload || return 1
  if [[ "$WRITE_AUTH_PORTAL" -eq 1 ]]; then
    run systemctl enable --now aeyeops-authelia.service || return 1
  fi
  if [[ "$SKIP_CADDY_RELOAD" -eq 0 ]]; then
    run systemctl reload caddy || return 1
  else
    log "skipped Caddy reload by request"
  fi
}

public_resolve_ip() {
  if [[ -n "${AEX_PORTAL_PUBLIC_IP:-}" ]]; then
    printf '%s
' "$AEX_PORTAL_PUBLIC_IP"
    return 0
  fi
  if command -v dig >/dev/null 2>&1; then
    dig +short @1.1.1.1 A "$AUTH_DOMAIN" | awk 'NF {print; exit}'
  fi
}

curl_resolve_args() {
  local host="$1" ip
  ip="$(public_resolve_ip || true)"
  [[ -n "$ip" ]] && printf '%s
' --resolve "$host:443:$ip"
}

outside_in_checks() {
  [[ "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]] && { log "dry-run/validate-only: skipped outside-in live probes"; return; }
  if [[ "$WRITE_AUTH_PORTAL" -eq 1 ]]; then
    mapfile -t auth_resolve < <(curl_resolve_args "$AUTH_DOMAIN")
    run curl "${auth_resolve[@]}" -fsSI --max-time 20 "https://$AUTH_DOMAIN/" >/dev/null || return 1
  fi
  if [[ "$WRITE_APP_SNIPPETS" -eq 1 ]]; then
    for spec in dashboard:$DASHBOARD_DOMAIN webui:$WEBUI_DOMAIN mc:$MC_DOMAIN; do
      local domain="${spec#*:}" code headers
      headers="$(mktemp)"
      mapfile -t domain_resolve < <(curl_resolve_args "$domain")
      code="$(curl "${domain_resolve[@]}" -sS -o /dev/null -D "$headers" -w '%{http_code}' --max-time 20 "https://$domain/" || true)"
      if [[ ! "$code" =~ ^(302|303)$ ]]; then
        log "Expected Authelia redirect for $domain, got $code"
        rm -f "$headers"
        return 1
      fi
      if ! grep -qi "^location: https://$AUTH_DOMAIN" "$headers"; then
        log "Expected redirect to https://$AUTH_DOMAIN for $domain."
        rm -f "$headers"
        return 1
      fi
      rm -f "$headers"
      log "outside-in unauth check for $domain returned $code"
    done
  fi
}

log "AEyeOps Authelia portal auth installer"
log "auth=$AUTH_DOMAIN port=$AUTHELIA_PORT dashboard=$DASHBOARD_DOMAIN:$DASHBOARD_PORT webui=$WEBUI_DOMAIN:$WEBUI_PORT mc=$MC_DOMAIN:$MC_PORT write_auth=$WRITE_AUTH_PORTAL write_apps=$WRITE_APP_SNIPPETS dry_run=$DRY_RUN validate_only=$VALIDATE_ONLY"

install_authelia_if_requested
resolve_password_hash
write_authelia_files
write_systemd_unit
if [[ "$WRITE_AUTH_PORTAL" -eq 1 || "$DRY_RUN" -eq 1 || "$VALIDATE_ONLY" -eq 1 ]]; then
  write_caddy_auth_portal
fi
write_app_snippets
validate_live_files
post_apply || fail "Post-apply failed. Inspect Authelia, Caddy, and app service logs before retrying."
outside_in_checks || fail "Outside-in check failed. Inspect DNS/TLS/Auth redirects before retrying."
log "Authelia portal auth install complete"
