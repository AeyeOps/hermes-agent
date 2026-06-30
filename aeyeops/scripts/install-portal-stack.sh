#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AEEYEOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTHELIA_INSTALLER="$AEEYEOPS_DIR/scripts/install-authelia-portal-auth.sh"
CLOUDFLARE_DNS_HELPER="$AEEYEOPS_DIR/scripts/ensure-cloudflare-dns.py"
VERIFY_SCRIPT="$AEEYEOPS_DIR/portal/scripts/verify-portal-stack.sh"

usage() {
	cat <<'USAGE'
Usage: install-portal-stack.sh --dashboard-domain DOMAIN --webui-domain DOMAIN --mc-domain DOMAIN --auth-domain DOMAIN --user USER --authelia-password-hash-file PATH [options]

Installs/configures the AEyeOps web portal stack behind Caddy HTTPS and an
Authelia web login page:
  dashboard  -> built-in Hermes dashboard on loopback
  webui      -> nesquena/hermes-webui on loopback, optionally via Hermes Gateway /v1
  mc         -> builderz-labs Mission Control on loopback
  auth       -> Authelia on loopback
  callback   -> optional machine callback host with provider-token auth

The script is repeatable and public-fork safe: real domains, password hashes,
API keys, local install paths, and service choices come from arguments or
host-local ./aeyeops/.env only. Caddy obtains public TLS certificates
automatically once DNS points at this host and ports 80/443 are reachable.

Required unless provided by aeyeops/.env or environment:
  --dashboard-domain DOMAIN         Public hostname for Hermes dashboard
  --webui-domain DOMAIN             Public hostname for Hermes WebUI
  --mc-domain DOMAIN                Public hostname for Mission Control
  --auth-domain DOMAIN              Public hostname for Authelia login
  --callback-domain DOMAIN          Optional machine callback hostname
  --user USER                       Authelia username
  --authelia-password-hash-file PATH
                                    File containing Authelia-compatible password hash

Options:
  --authelia-password-hash HASH     Authelia user password hash; use env/file when possible
  --authelia-password-file PATH     Plaintext password file; hashed locally with Authelia
  --ensure-cloudflare-dns           Upsert dashboard/webui/mc/auth A records using Cloudflare
  --skip-cloudflare-dns             Do not attempt Cloudflare DNS automation
  --portal-origin-ip IP|auto        DNS A-record target (default: auto public IP)
  --hermes-home PATH                Hermes home (default: $HERMES_HOME or $HOME/.hermes)
  --repo-dir PATH                   Hermes repo dir (default: HERMES_HOME/hermes-agent)
  --dashboard-port PORT             Local dashboard port (default: 9119)
  --webui-port PORT                 Local WebUI port (default: 8787)
  --mc-port PORT                    Local Mission Control port (default: 3000)
  --api-port PORT                   Hermes API Server port (default: 8642)
  --authelia-port PORT              Local Authelia port (default: 9091)
  --install-root PATH               App checkout root (default: $HOME/.local/share/aeyeops)
  --webui-dir PATH                  Hermes WebUI checkout dir
  --mc-dir PATH                     Mission Control checkout dir
  --webui-repo URL                  Hermes WebUI git repo URL
  --mc-repo URL                     Mission Control git repo URL
  --webui-ref REF                   Hermes WebUI git ref (default: master)
  --mc-ref REF                      Mission Control git ref (default: main)
  --api-key-file PATH               File containing Hermes API_SERVER_KEY for WebUI bridge
  --google-chat-project-number NUM  Audience for Chat API interaction-event HTTP callbacks
  --google-chat-addon-service-account-email EMAIL
                                    Workspace add-on callback token subject
  --google-chat-http-events-service-account-email EMAIL
                                    Chat API HTTP event token subject
  --install-authelia                Install Authelia when missing (default)
  --skip-authelia-install           Require an existing Authelia binary
  --restart-gateway                 Restart hermes-gateway after enabling API server
  --skip-caddy-install              Do not install/enable Caddy; only configure it
  --skip-app-install                Do not clone/build apps; only write units/proxy config
  --skip-mc-build                   Skip pnpm install/build for Mission Control
  --skip-verify                     Skip local post-install verification probes
  --dry-run                         Print planned commands/configuration only
  --yes                             Accepted for CI/headless callers; no prompts are used
  -h, --help                        Show this help

Useful host-local env keys loaded from aeyeops/.env when present:
  AEX_DASHBOARD_DOMAIN, AEX_WEBUI_DOMAIN, AEX_MC_DOMAIN, AEX_AUTHELIA_DOMAIN,
  AEX_CALLBACK_DOMAIN,
  AEX_AUTHELIA_USER, AEX_AUTHELIA_USER_PASSWORD_HASH_FILE,
  AEX_AUTHELIA_USER_PASSWORD_HASH, AEX_AUTHELIA_USER_PASSWORD_FILE,
  AEX_INSTALL_AUTHELIA, AEX_CLOUDFLARE_DNS,
  AEX_CLOUDFLARE_KEYS_FILE, AEX_PORTAL_ORIGIN_IP,
  AEX_DASHBOARD_PORT, AEX_WEBUI_PORT, AEX_MC_PORT, AEX_HERMES_API_PORT,
  AEX_AUTHELIA_PORT, AEX_INSTALL_ROOT, AEX_WEBUI_DIR, AEX_MC_DIR,
  AEX_WEBUI_REPO, AEX_MC_REPO, AEX_WEBUI_REF, AEX_MC_REF,
  AEX_HERMES_API_KEY_FILE, AEX_GOOGLE_CHAT_PROJECT_NUMBER,
  AEX_GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL,
  AEX_GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL,
  AEX_GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE, AEX_GOOGLE_CLOUD_PROJECT_NUMBER,
  HERMES_HOME, HERMES_REPO_DIR
USAGE
}

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() {
	echo "$*" >&2
	exit 1
}

load_local_env() {
	local env_file="${AEYEOPS_ENV_FILE:-$AEEYEOPS_DIR/.env}"
	[[ -f "$env_file" ]] || return 0
	local line key value
	while IFS= read -r line || [[ -n "$line" ]]; do
		line="${line#"${line%%[![:space:]]*}"}"
		line="${line%"${line##*[![:space:]]}"}"
		[[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
		key="${line%%=*}"
		value="${line#*=}"
		key="${key#export }"
		key="${key#"${key%%[![:space:]]*}"}"
		key="${key%"${key##*[![:space:]]}"}"
		[[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
		[[ -z "${!key+x}" ]] || continue
		value="${value#"${value%%[![:space:]]*}"}"
		value="${value%"${value##*[![:space:]]}"}"
		if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
		if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
		export "$key=$value"
	done <"$env_file"
}

run() {
	log "+ $*"
	if [[ "$DRY_RUN" -eq 0 ]]; then
		"$@"
	fi
}

write_file() {
	local path="$1" mode="$2"
	shift 2
	local redacts=("$@") tmp
	tmp="$(mktemp)"
	cat >"$tmp"
	log "write $path"
	if [[ "$DRY_RUN" -eq 0 ]]; then
		install -D -m "$mode" "$tmp" "$path"
	else
		local line redacted_line secret
		while IFS= read -r line || [[ -n "$line" ]]; do
			redacted_line="$line"
			for secret in "${redacts[@]}"; do
				[[ -n "$secret" ]] || continue
				redacted_line="${redacted_line//$secret/<redacted>}"
			done
			printf '    | %s\n' "$redacted_line"
		done <"$tmp"
	fi
	rm -f "$tmp"
}

redacted_args_for_log() {
	local redact_next=0 out=() arg
	for arg in "$@"; do
		if [[ "$redact_next" -eq 1 ]]; then
			out+=("<redacted>")
			redact_next=0
			continue
		fi
		out+=("$arg")
		case "$arg" in
		--user-password-hash | --authelia-password-hash) redact_next=1 ;;
		esac
	done
	printf '%s ' "${out[@]}"
}

discover_google_chat_callback_auth() {
	[[ -n "$CALLBACK_DOMAIN" ]] || return 0
	if [[ -n "$GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL" && -n "$GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE" && -n "$GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL" ]]; then
		return 0
	fi
	if ! command -v gcloud >/dev/null 2>&1; then
		log "Google Chat callback configured; set AEX_GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL and AEX_GOOGLE_CHAT_PROJECT_NUMBER when gcloud is unavailable."
		return 0
	fi
	local auth_json parsed service_account project_number
	if ! auth_json="$(gcloud workspace-add-ons get-authorization --format=json 2>/dev/null)" || [[ -z "$auth_json" ]]; then
		log "Google Chat callback configured; could not auto-read Workspace add-on authorization with gcloud."
		return 0
	fi
	parsed="$(python3 -c 'import json, re, sys; d=json.load(sys.stdin); name=str(d.get("name", "")); m=re.match(r"projects/(\d+)/authorization$", name); print(d.get("serviceAccountEmail", "")); print(m.group(1) if m else "")' <<<"$auth_json")" || return 0
	service_account="$(sed -n '1p' <<<"$parsed")"
	project_number="$(sed -n '2p' <<<"$parsed")"
	if [[ -z "$GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL" && -n "$service_account" ]]; then
		GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL="$service_account"
		log "discovered Google Workspace add-on service account for callback verification"
	fi
	if [[ -z "$GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL" && -n "$service_account" ]]; then
		GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL="$service_account"
		log "using Google Workspace add-on service account for HTTP event verification"
	fi
	if [[ -z "$GOOGLE_CHAT_PROJECT_NUMBER" && -n "$project_number" ]]; then
		GOOGLE_CHAT_PROJECT_NUMBER="$project_number"
		log "discovered Google Cloud project number for optional non-add-on Chat HTTP verification"
	fi
}

log_google_chat_callback_console_steps() {
	[[ -n "$CALLBACK_DOMAIN" ]] || return 0
	log "Google Chat add-on callback host is configured. In Google Chat API configuration, use HTTP endpoint URL https://$CALLBACK_DOMAIN/google-chat/events and Card Interaction URL/common button-click endpoint https://$CALLBACK_DOMAIN/google-chat/actions."
}

validate_domain() {
	local label="$1" domain="$2"
	[[ -n "$domain" ]] || fail "$label is required."
	if [[ ${#domain} -gt 253 || ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
		fail "$label must be a clean hostname like dashboard.example.com."
	fi
}

validate_port() {
	local label="$1" port="$2"
	[[ "$port" =~ ^[0-9]+$ ]] || fail "$label must be numeric."
	((port >= 1 && port <= 65535)) || fail "$label must be between 1 and 65535."
}

validate_password_hash() {
	local hash="$1"
	if [[ ${#hash} -gt 300 || ! "$hash" =~ ^\$[A-Za-z0-9\$./=,+_-]{20,300}$ ]]; then
		fail "Authelia password hash must look like Argon2/bcrypt/scrypt/PBKDF2 output and must not contain whitespace or Caddyfile syntax."
	fi
}

require_root_for_apply() {
	if [[ "$DRY_RUN" -eq 0 && "$(id -u)" -ne 0 ]]; then
		fail "Run as root because this installs apps and writes /etc/systemd/system and /etc/caddy."
	fi
}

install_caddy_debian() {
	if command -v caddy >/dev/null 2>&1; then
		log "caddy already installed: $(caddy version 2>/dev/null || true)"
		run systemctl enable --now caddy
		return 0
	fi
	[[ -r /etc/os-release ]] || fail "Cannot detect OS; install Caddy manually or rerun with --skip-caddy-install."
	# shellcheck disable=SC1091
	source /etc/os-release
	local os_id="${ID:-}" os_like="${ID_LIKE:-}"
	if [[ "$os_id" != "debian" && "$os_id" != "ubuntu" && "$os_like" != *debian* && "$os_like" != *ubuntu* ]]; then
		fail "Automatic Caddy install supports Debian-family systems only. Install Caddy manually, then rerun with --skip-caddy-install."
	fi
	log "installing Caddy from official stable package repository"
	run apt-get update
	run apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates
	run install -d -m 0755 /usr/share/keyrings /etc/apt/sources.list.d
	if [[ "$DRY_RUN" -eq 0 ]]; then
		local key_tmp list_tmp
		key_tmp="$(mktemp)"
		list_tmp="$(mktemp)"
		curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --batch --yes --dearmor -o "$key_tmp"
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

ensure_caddy_import() {
	local import_line='import /etc/caddy/conf.d/*.caddy'
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "ensure /etc/caddy/Caddyfile contains: $import_line"
		return 0
	fi
	install -d -m 0755 /etc/caddy/conf.d
	if [[ ! -e /etc/caddy/Caddyfile ]]; then
		printf '%s\n' "$import_line" >/etc/caddy/Caddyfile
		chmod 0644 /etc/caddy/Caddyfile
	elif ! grep -Fxq "$import_line" /etc/caddy/Caddyfile; then
		printf '\n# AEyeOps managed site snippets\n%s\n' "$import_line" >>/etc/caddy/Caddyfile
	fi
}

ensure_caddy_log_dir() {
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "ensure /var/log/caddy and existing Caddy access logs are writable by caddy"
		return 0
	fi
	install -d -m 0755 /var/log/caddy
	if getent group caddy >/dev/null 2>&1 && id caddy >/dev/null 2>&1; then
		chown caddy:caddy /var/log/caddy
		for log_file in \
			/var/log/caddy/aeyeops-callbacks-access.log \
			/var/log/caddy/hermes-dashboard-access.log \
			/var/log/caddy/hermes-webui-access.log \
			/var/log/caddy/mission-control-access.log; do
			touch "$log_file"
			chown caddy:caddy "$log_file"
			chmod 0644 "$log_file"
		done
		find /var/log/caddy -maxdepth 1 -type f -name '*access.log*' -exec chown caddy:caddy {} + -exec chmod 0644 {} +
	fi
}

upsert_env_value() {
	local file="$1" key="$2" value="$3" mode="${4:-0600}"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		if [[ "$key" == *KEY* || "$key" == *PASSWORD* || "$key" == *SECRET* || "$key" == *EMAIL* || "$key" == *ACCOUNT* ]]; then
			log "would set $key=<redacted> in $file"
		else
			log "would set $key=$value in $file"
		fi
		return 0
	fi
	install -d -m 0700 "$(dirname "$file")"
	touch "$file"
	chmod "$mode" "$file"
	if grep -Eq "^${key}=" "$file"; then
		python3 - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); key=sys.argv[2]; value=sys.argv[3]
lines=p.read_text(encoding='utf-8').splitlines()
out=[]; done=False
for line in lines:
    if line.startswith(key+'=') and not done:
        out.append(f'{key}={value}')
        done=True
    elif line.startswith(key+'='):
        continue
    else:
        out.append(line)
if not done:
    out.append(f'{key}={value}')
p.write_text('\n'.join(out)+'\n', encoding='utf-8')
PY
	else
		printf '%s=%s\n' "$key" "$value" >>"$file"
	fi
}

read_env_value() {
	local file="$1" key="$2"
	[[ -r "$file" ]] || return 1
	awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,"",$0); print; exit}' "$file"
}

random_secret() {
	if command -v openssl >/dev/null 2>&1; then
		openssl rand -hex 32
	else
		python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
	fi
}

ensure_git_checkout() {
	local name="$1" repo="$2" ref="$3" dir="$4"
	if [[ "$SKIP_APP_INSTALL" -eq 1 ]]; then
		log "skipping $name checkout by request: $dir"
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		if [[ -d "$dir/.git" ]]; then
			log "dry-run: would fetch $name in $dir and checkout $ref"
		else
			log "dry-run: would git clone $repo -> $dir and checkout $ref"
		fi
		return 0
	fi
	install -d -m 0755 "$(dirname "$dir")"
	if [[ -d "$dir/.git" ]]; then
		run git -C "$dir" fetch --depth=1 origin "$ref"
		run git -C "$dir" checkout FETCH_HEAD
	else
		run git clone --depth=1 "$repo" "$dir"
		run git -C "$dir" fetch --depth=1 origin "$ref" || true
		run git -C "$dir" checkout "$ref" || run git -C "$dir" checkout FETCH_HEAD
	fi
}

ensure_node_and_pnpm() {
	local node_dir="${AEX_NODE_BIN_DIR:-$HERMES_HOME/node/bin}"
	PORTAL_NODE_PATH="$node_dir:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
	export PATH="$PORTAL_NODE_PATH"

	if [[ -x "$node_dir/node" ]]; then
		if [[ "$DRY_RUN" -eq 1 ]]; then
			log "dry-run: would expose Node toolchain from $node_dir via /usr/local/bin"
		elif [[ "$(id -u)" -eq 0 ]]; then
			install -d -m 0755 /usr/local/bin
			local bin
			for bin in node npm npx corepack; do
				if [[ -e "$node_dir/$bin" ]]; then
					ln -sfn "$node_dir/$bin" "/usr/local/bin/$bin"
				fi
			done
			hash -r || true
		fi
	fi

	if ! command -v node >/dev/null 2>&1; then
		fail "Node.js >=22 is required for Mission Control. Install Node 22 or set AEX_NODE_BIN_DIR, then rerun."
	fi
	local major
	major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
	[[ "$major" -ge 22 ]] || fail "Mission Control requires Node.js >=22; detected $(node --version 2>/dev/null || echo unknown)."
	log "node available: $(command -v node) $(node --version)"

	if command -v pnpm >/dev/null 2>&1; then
		log "pnpm already installed: $(pnpm --version)"
		return 0
	fi
	if ! command -v corepack >/dev/null 2>&1; then
		fail "pnpm not found and corepack is unavailable. Install pnpm or Node.js corepack, then rerun."
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "dry-run: would run corepack enable --install-directory /usr/local/bin"
		log "dry-run: would run corepack prepare pnpm@${PNPM_VERSION} --activate"
	else
		run corepack enable --install-directory /usr/local/bin
		run corepack prepare "pnpm@${PNPM_VERSION}" --activate
	fi
	command -v pnpm >/dev/null 2>&1 || fail "corepack completed but pnpm is still not visible on PATH."
	log "pnpm available: $(command -v pnpm) $(pnpm --version)"
}

build_mission_control() {
	[[ "$SKIP_APP_INSTALL" -eq 0 ]] || return 0
	[[ "$SKIP_MC_BUILD" -eq 0 ]] || {
		log "skipping Mission Control pnpm install/build by request"
		return 0
	}
	ensure_node_and_pnpm
	run bash -lc "export PATH='$PORTAL_NODE_PATH'; cd '$MC_DIR' && pnpm install --frozen-lockfile"
	run bash -lc "export PATH='$PORTAL_NODE_PATH'; cd '$MC_DIR' && MISSION_CONTROL_BUILD_DATA_DIR='$HERMES_HOME/mission-control-build' pnpm build"
}

find_repo_python() {
	if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
		printf '%s\n' "$REPO_DIR/.venv/bin/python"
	elif [[ -x "$REPO_DIR/venv/bin/python" ]]; then
		printf '%s\n' "$REPO_DIR/venv/bin/python"
	elif [[ "$DRY_RUN" -eq 1 ]]; then
		printf '%s\n' "$REPO_DIR/.venv/bin/python"
	else
		fail "Hermes venv python not found/executable under: $REPO_DIR/.venv or $REPO_DIR/venv"
	fi
}

ensure_cloudflare_dns() {
	case "$CLOUDFLARE_DNS" in
	0 | false | no | skip)
		log "skipping Cloudflare DNS automation by request"
		return 0
		;;
	1 | true | yes | auto) ;;
	*) fail "AEX_CLOUDFLARE_DNS must be auto, 1/0, true/false, yes/no, or skip." ;;
	esac
	[[ -x "$CLOUDFLARE_DNS_HELPER" ]] || fail "Cloudflare DNS helper not found/executable: $CLOUDFLARE_DNS_HELPER"
	local dns_args=(
		--keys-file "$CLOUDFLARE_KEYS_FILE"
		--origin-ip "$PORTAL_ORIGIN_IP"
		"$DASHBOARD_DOMAIN"
		"$WEBUI_DOMAIN"
		"$MC_DOMAIN"
		"$AUTHELIA_DOMAIN"
	)
	if [[ -n "$CALLBACK_DOMAIN" ]]; then
		dns_args+=("$CALLBACK_DOMAIN")
	fi
	if [[ -n "$CLOUDFLARE_ZONE_NAME" ]]; then
		dns_args=(--zone-name "$CLOUDFLARE_ZONE_NAME" "${dns_args[@]}")
	fi
	if [[ -n "$CLOUDFLARE_ZONE_ID" ]]; then
		dns_args=(--zone-id "$CLOUDFLARE_ZONE_ID" "${dns_args[@]}")
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "dry-run: would upsert Cloudflare A records for dashboard/webui/mc/auth${CALLBACK_DOMAIN:+/callback} when credentials are available"
		return 0
	fi
	log "+ $CLOUDFLARE_DNS_HELPER --keys-file <host-local> --origin-ip <redacted> $DASHBOARD_DOMAIN $WEBUI_DOMAIN $MC_DOMAIN $AUTHELIA_DOMAIN${CALLBACK_DOMAIN:+ $CALLBACK_DOMAIN}"
	"$CLOUDFLARE_DNS_HELPER" "${dns_args[@]}"
}

preflight_authelia_inputs() {
	[[ -x "$AUTHELIA_INSTALLER" ]] || fail "Authelia installer not found/executable: $AUTHELIA_INSTALLER"
	[[ -n "$AUTHELIA_DOMAIN" ]] || fail "--auth-domain or AEX_AUTHELIA_DOMAIN is required."
	if [[ "$DRY_RUN" -eq 0 ]]; then
		if [[ -n "$AUTHELIA_PASSWORD_HASH_FILE" ]]; then
			[[ -r "$AUTHELIA_PASSWORD_HASH_FILE" ]] || fail "Authelia password hash file is not readable: $AUTHELIA_PASSWORD_HASH_FILE"
		elif [[ -n "$AUTHELIA_PASSWORD_HASH" ]]; then
			:
		elif [[ -n "$AUTHELIA_PASSWORD_FILE" ]]; then
			[[ -r "$AUTHELIA_PASSWORD_FILE" ]] || fail "Authelia password file is not readable: $AUTHELIA_PASSWORD_FILE"
		else
			fail "AEX_AUTHELIA_USER_PASSWORD_HASH_FILE, AEX_AUTHELIA_USER_PASSWORD_HASH, or AEX_AUTHELIA_USER_PASSWORD_FILE is required."
		fi
		if [[ "$INSTALL_AUTHELIA" -eq 0 ]] && ! command -v authelia >/dev/null 2>&1; then
			fail "Authelia is not installed. Remove --skip-authelia-install or install Authelia before applying."
		fi
	fi
}

configure_hermes_api_server() {
	local env_file="$HERMES_HOME/.env"
	API_KEY="${API_SERVER_KEY:-}"
	if [[ -n "$API_KEY_FILE" ]]; then
		[[ -r "$API_KEY_FILE" ]] || fail "API key file is not readable."
		API_KEY="$(tr -d '\r\n' <"$API_KEY_FILE")"
	fi
	if [[ -z "$API_KEY" ]]; then
		API_KEY="$(read_env_value "$env_file" API_SERVER_KEY || true)"
	fi
	if [[ -z "$API_KEY" ]]; then
		if [[ "$DRY_RUN" -eq 1 ]]; then
			API_KEY='DRY_RUN_GENERATED_API_SERVER_KEY'
		else
			API_KEY="$(random_secret)"
		fi
	fi
	upsert_env_value "$env_file" API_SERVER_ENABLED true 0600
	upsert_env_value "$env_file" API_SERVER_HOST 127.0.0.1 0600
	upsert_env_value "$env_file" API_SERVER_PORT "$API_PORT" 0600
	upsert_env_value "$env_file" API_SERVER_KEY "$API_KEY" 0600
	upsert_env_value "$env_file" API_SERVER_MODEL_NAME hermes-agent 0600
	if [[ -n "$CALLBACK_DOMAIN" ]]; then
		local action_url="https://$CALLBACK_DOMAIN/google-chat/actions"
		local event_url="https://$CALLBACK_DOMAIN/google-chat/events"
		upsert_env_value "$env_file" GOOGLE_CHAT_ADDON_CALLBACK_URL "$action_url" 0600
		upsert_env_value "$env_file" GOOGLE_CHAT_ADDON_AUDIENCE "$action_url" 0600
		upsert_env_value "$env_file" GOOGLE_CHAT_HTTP_EVENTS_URL "$event_url" 0600
		upsert_env_value "$env_file" GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE "${GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE:-$event_url}" 0600
		upsert_env_value "$env_file" GOOGLE_CHAT_CARD_ACTION_TRANSPORT addon_http 0600
		if [[ -n "$GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL" ]]; then
			upsert_env_value "$env_file" GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL "$GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL" 0600
		fi
		if [[ -n "$GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL" ]]; then
			upsert_env_value "$env_file" GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL "$GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL" 0600
		fi
	fi
}

write_callback_caddy_snippet() {
	[[ -n "$CALLBACK_DOMAIN" ]] || return 0
	write_file /etc/caddy/conf.d/aeyeops-callbacks.caddy 0644 <<CADDY
$CALLBACK_DOMAIN {
    encode zstd gzip

    log {
        output file /var/log/caddy/aeyeops-callbacks-access.log {
            roll_size 10MiB
            roll_keep 7
            roll_keep_for 168h
        }
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        X-Frame-Options "DENY"
    }

    @google_chat_callbacks {
        method POST
        path /google-chat/actions /google-chat/events
    }
    handle @google_chat_callbacks {
        reverse_proxy 127.0.0.1:$API_PORT {
            header_up X-Forwarded-Proto https
        }
    }

    handle {
        respond "not found" 404
    }
}
CADDY
}

write_dashboard_service() {
	local python_bin dashboard_args
	python_bin="$(find_repo_python)"
	dashboard_args="dashboard --host 127.0.0.1 --port $DASHBOARD_PORT --no-open"
	write_file /etc/systemd/system/hermes-dashboard.service 0644 <<UNIT
[Unit]
Description=Hermes dashboard (loopback only)
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
Environment=HERMES_HOME=$HERMES_HOME
WorkingDirectory=$REPO_DIR
ExecStart=$python_bin -m hermes_cli.main $dashboard_args
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$HERMES_HOME

[Install]
WantedBy=multi-user.target
UNIT
}

write_webui_service() {
	local env_file="$ENV_DIR/hermes-webui.env"
	write_file "$env_file" 0600 "$API_KEY" <<ENV
HERMES_HOME=$HERMES_HOME
HERMES_WEBUI_AGENT_DIR=$REPO_DIR
HERMES_WEBUI_HOST=127.0.0.1
HERMES_WEBUI_PORT=$WEBUI_PORT
HERMES_WEBUI_STATE_DIR=$HERMES_HOME/webui
HERMES_WEBUI_CHAT_BACKEND=gateway
HERMES_WEBUI_GATEWAY_BASE_URL=http://127.0.0.1:$API_PORT
HERMES_WEBUI_GATEWAY_API_KEY=$API_KEY
HERMES_WEBUI_SKIP_ONBOARDING=1
ENV

	write_file /etc/systemd/system/hermes-webui.service 0644 <<UNIT
[Unit]
Description=Hermes WebUI (loopback only)
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$env_file
WorkingDirectory=$WEBUI_DIR
ExecStart=$WEBUI_DIR/start.sh $WEBUI_PORT --host 127.0.0.1 --skip-agent-install
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$HERMES_HOME $WEBUI_DIR

[Install]
WantedBy=multi-user.target
UNIT
}

write_mc_service() {
	local env_file="$ENV_DIR/mission-control.env" mc_home mc_api_key mc_auth_secret
	if [[ "$(basename "$HERMES_HOME")" == ".hermes" ]]; then
		mc_home="$(dirname "$HERMES_HOME")"
	else
		mc_home="$HOME"
		log "WARN: HERMES_HOME does not end in .hermes; Mission Control Hermes scanners expect HOME/.hermes. Set AEX_MC_HOME if needed."
	fi
	mc_home="${AEX_MC_HOME:-$mc_home}"
	mc_api_key="${AEX_MC_API_KEY:-}"
	if [[ -z "$mc_api_key" ]]; then
		if [[ "$DRY_RUN" -eq 1 ]]; then mc_api_key='DRY_RUN_GENERATED_MC_API_KEY'; else mc_api_key="$(random_secret)"; fi
	fi
	mc_auth_secret="${AEX_MC_AUTH_SECRET:-}"
	if [[ -z "$mc_auth_secret" ]]; then
		if [[ "$DRY_RUN" -eq 1 ]]; then mc_auth_secret='DRY_RUN_GENERATED_MC_AUTH_SECRET'; else mc_auth_secret="$(random_secret)"; fi
	fi
	write_file "$env_file" 0600 "$mc_api_key" "$mc_auth_secret" <<ENV
NODE_ENV=production
PORT=$MC_PORT
HOME=$mc_home
MISSION_CONTROL_DATA_DIR=$HERMES_HOME/mission-control
AUTH_SECRET=$mc_auth_secret
API_KEY=$mc_api_key
HERMES_BIN=$HERMES_BIN
MC_URL=https://$MC_DOMAIN
MC_API_KEY=$mc_api_key
ENV

	write_file /etc/systemd/system/mission-control.service 0644 <<UNIT
[Unit]
Description=Mission Control (loopback only)
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$env_file
Environment=PATH=$PORTAL_NODE_PATH
WorkingDirectory=$MC_DIR
ExecStart=/bin/bash -lc 'exec pnpm exec next start --hostname 127.0.0.1 --port $MC_PORT'
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$HERMES_HOME $MC_DIR

[Install]
WantedBy=multi-user.target
UNIT
}

write_logrotate() {
	write_file /etc/logrotate.d/aeyeops-portal-stack 0644 <<LOGROTATE
$HERMES_HOME/logs/aeyeops-portal-*.log /var/log/caddy/aeyeops-*-access.log /var/log/caddy/hermes-dashboard-access.log /var/log/caddy/hermes-webui-access.log /var/log/caddy/mission-control-access.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    dateext
    create 0644 caddy caddy
}
LOGROTATE
}

run_authelia_install() {
	local authelia_args=(
		--auth-domain "$AUTHELIA_DOMAIN"
		--dashboard-domain "$DASHBOARD_DOMAIN"
		--webui-domain "$WEBUI_DOMAIN"
		--mc-domain "$MC_DOMAIN"
		--user "$AUTH_USER"
		--hermes-home "$HERMES_HOME"
		--authelia-port "$AUTHELIA_PORT"
		--dashboard-port "$DASHBOARD_PORT"
		--webui-port "$WEBUI_PORT"
		--mc-port "$MC_PORT"
		--write-auth-portal
		--write-app-snippets
		--yes
	)
	if [[ "$INSTALL_AUTHELIA" -eq 1 ]]; then
		authelia_args+=(--install-authelia)
	else
		authelia_args+=(--skip-authelia-install)
	fi
	if [[ -n "$AUTHELIA_PASSWORD_HASH_FILE" ]]; then
		authelia_args+=(--user-password-hash-file "$AUTHELIA_PASSWORD_HASH_FILE")
	elif [[ -n "$AUTHELIA_PASSWORD_HASH" ]]; then
		authelia_args+=(--user-password-hash "$AUTHELIA_PASSWORD_HASH")
	elif [[ -n "$AUTHELIA_PASSWORD_FILE" ]]; then
		authelia_args+=(--user-password-file "$AUTHELIA_PASSWORD_FILE")
	fi
	[[ "$DRY_RUN" -eq 1 ]] && authelia_args+=(--dry-run)

	log "installing Authelia web login and protected Caddy snippets"
	log "+ $AUTHELIA_INSTALLER $(redacted_args_for_log "${authelia_args[@]}")"
	"$AUTHELIA_INSTALLER" "${authelia_args[@]}"
}

load_local_env

DASHBOARD_DOMAIN="${AEX_DASHBOARD_DOMAIN:-}"
WEBUI_DOMAIN="${AEX_WEBUI_DOMAIN:-}"
MC_DOMAIN="${AEX_MC_DOMAIN:-}"
AUTHELIA_DOMAIN="${AEX_AUTHELIA_DOMAIN:-}"
CALLBACK_DOMAIN="${AEX_CALLBACK_DOMAIN:-}"
AUTH_USER="${AEX_AUTHELIA_USER:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
REPO_DIR="${HERMES_REPO_DIR:-}"
DASHBOARD_PORT="${AEX_DASHBOARD_PORT:-9119}"
WEBUI_PORT="${AEX_WEBUI_PORT:-8787}"
MC_PORT="${AEX_MC_PORT:-3000}"
AUTHELIA_PORT="${AEX_AUTHELIA_PORT:-9091}"
API_PORT="${AEX_HERMES_API_PORT:-8642}"
INSTALL_ROOT="${AEX_INSTALL_ROOT:-$HOME/.local/share/aeyeops}"
WEBUI_DIR="${AEX_WEBUI_DIR:-$INSTALL_ROOT/hermes-webui}"
MC_DIR="${AEX_MC_DIR:-$INSTALL_ROOT/mission-control}"
WEBUI_REPO="${AEX_WEBUI_REPO:-https://github.com/nesquena/hermes-webui.git}"
MC_REPO="${AEX_MC_REPO:-https://github.com/builderz-labs/mission-control.git}"
WEBUI_REF="${AEX_WEBUI_REF:-master}"
MC_REF="${AEX_MC_REF:-main}"
API_KEY_FILE="${AEX_HERMES_API_KEY_FILE:-}"
GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL="${AEX_GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL:-${GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL:-}}"
GOOGLE_CHAT_PROJECT_NUMBER="${AEX_GOOGLE_CHAT_PROJECT_NUMBER:-${AEX_GOOGLE_CLOUD_PROJECT_NUMBER:-${GOOGLE_CHAT_PROJECT_NUMBER:-${GOOGLE_CLOUD_PROJECT_NUMBER:-}}}}"
GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL="${AEX_GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL:-${GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL:-}}"
GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE="${AEX_GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE:-${GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE:-}}"
AUTHELIA_PASSWORD_HASH_FILE="${AEX_AUTHELIA_USER_PASSWORD_HASH_FILE:-}"
AUTHELIA_PASSWORD_HASH="${AEX_AUTHELIA_USER_PASSWORD_HASH:-}"
AUTHELIA_PASSWORD_FILE="${AEX_AUTHELIA_USER_PASSWORD_FILE:-}"
INSTALL_AUTHELIA="${AEX_INSTALL_AUTHELIA:-1}"
CLOUDFLARE_DNS="${AEX_CLOUDFLARE_DNS:-auto}"
CLOUDFLARE_KEYS_FILE="${AEX_CLOUDFLARE_KEYS_FILE:-$HOME/.config/secrets/keys.env}"
CLOUDFLARE_ZONE_NAME="${AEX_CLOUDFLARE_ZONE_NAME:-${CLOUDFLARE_ZONE_NAME:-}}"
CLOUDFLARE_ZONE_ID="${AEX_CLOUDFLARE_ZONE_ID:-${CLOUDFLARE_ZONE_ID:-}}"
PORTAL_ORIGIN_IP="${AEX_PORTAL_ORIGIN_IP:-auto}"
PNPM_VERSION="${AEX_PNPM_VERSION:-10.24.0}"
PORTAL_NODE_PATH="${AEX_NODE_BIN_DIR:-$HERMES_HOME/node/bin}:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SKIP_CADDY_INSTALL=0
SKIP_APP_INSTALL=0
SKIP_MC_BUILD=0
SKIP_VERIFY=0
RESTART_GATEWAY=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dashboard-domain)
		DASHBOARD_DOMAIN="${2:-}"
		shift 2
		;;
	--webui-domain)
		WEBUI_DOMAIN="${2:-}"
		shift 2
		;;
	--mc-domain)
		MC_DOMAIN="${2:-}"
		shift 2
		;;
	--auth-domain)
		AUTHELIA_DOMAIN="${2:-}"
		shift 2
		;;
	--callback-domain)
		CALLBACK_DOMAIN="${2:-}"
		shift 2
		;;
	--user)
		AUTH_USER="${2:-}"
		shift 2
		;;
	--authelia-password-hash-file)
		AUTHELIA_PASSWORD_HASH_FILE="${2:-}"
		shift 2
		;;
	--authelia-password-hash)
		AUTHELIA_PASSWORD_HASH="${2:-}"
		shift 2
		;;
	--authelia-password-file)
		AUTHELIA_PASSWORD_FILE="${2:-}"
		shift 2
		;;
	--ensure-cloudflare-dns)
		CLOUDFLARE_DNS=1
		shift
		;;
	--skip-cloudflare-dns)
		CLOUDFLARE_DNS=0
		shift
		;;
	--portal-origin-ip)
		PORTAL_ORIGIN_IP="${2:-}"
		shift 2
		;;
	--hermes-home)
		HERMES_HOME="${2:-}"
		shift 2
		;;
	--repo-dir)
		REPO_DIR="${2:-}"
		shift 2
		;;
	--dashboard-port)
		DASHBOARD_PORT="${2:-}"
		shift 2
		;;
	--webui-port)
		WEBUI_PORT="${2:-}"
		shift 2
		;;
	--mc-port)
		MC_PORT="${2:-}"
		shift 2
		;;
	--api-port)
		API_PORT="${2:-}"
		shift 2
		;;
	--authelia-port)
		AUTHELIA_PORT="${2:-}"
		shift 2
		;;
	--install-root)
		INSTALL_ROOT="${2:-}"
		WEBUI_DIR="${AEX_WEBUI_DIR:-${2:-}/hermes-webui}"
		MC_DIR="${AEX_MC_DIR:-${2:-}/mission-control}"
		shift 2
		;;
	--webui-dir)
		WEBUI_DIR="${2:-}"
		shift 2
		;;
	--mc-dir)
		MC_DIR="${2:-}"
		shift 2
		;;
	--webui-repo)
		WEBUI_REPO="${2:-}"
		shift 2
		;;
	--mc-repo)
		MC_REPO="${2:-}"
		shift 2
		;;
	--webui-ref)
		WEBUI_REF="${2:-}"
		shift 2
		;;
	--mc-ref)
		MC_REF="${2:-}"
		shift 2
		;;
	--api-key-file)
		API_KEY_FILE="${2:-}"
		shift 2
		;;
	--google-chat-project-number)
		GOOGLE_CHAT_PROJECT_NUMBER="${2:-}"
		GOOGLE_CHAT_HTTP_EVENTS_AUDIENCE="${2:-}"
		shift 2
		;;
	--google-chat-addon-service-account-email)
		GOOGLE_CHAT_ADDON_SERVICE_ACCOUNT_EMAIL="${2:-}"
		shift 2
		;;
	--google-chat-http-events-service-account-email)
		GOOGLE_CHAT_HTTP_EVENTS_SERVICE_ACCOUNT_EMAIL="${2:-}"
		shift 2
		;;
	--install-authelia)
		INSTALL_AUTHELIA=1
		shift
		;;
	--skip-authelia-install)
		INSTALL_AUTHELIA=0
		shift
		;;
	--restart-gateway)
		RESTART_GATEWAY=1
		shift
		;;
	--skip-caddy-install)
		SKIP_CADDY_INSTALL=1
		shift
		;;
	--skip-app-install)
		SKIP_APP_INSTALL=1
		shift
		;;
	--skip-mc-build)
		SKIP_MC_BUILD=1
		shift
		;;
	--skip-verify)
		SKIP_VERIFY=1
		shift
		;;
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--yes) shift ;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "Unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

REPO_DIR="${REPO_DIR:-$HERMES_HOME/hermes-agent}"
ENV_DIR="$HERMES_HOME/aeyeops-portal"
HERMES_BIN="$REPO_DIR/.venv/bin/hermes"
[[ -x "$HERMES_BIN" ]] || HERMES_BIN="$REPO_DIR/venv/bin/hermes"
[[ -x "$HERMES_BIN" ]] || HERMES_BIN="hermes"

[[ -n "$DASHBOARD_DOMAIN" && -n "$WEBUI_DOMAIN" && -n "$MC_DOMAIN" && -n "$AUTHELIA_DOMAIN" ]] || {
	usage >&2
	fail "Missing one or more required domains."
}
[[ -n "$AUTH_USER" ]] || fail "Missing required --user or AEX_AUTHELIA_USER."
[[ "$AUTH_USER" =~ ^[A-Za-z0-9._~@-]{1,64}$ ]] || fail "--user must contain only A-Z, a-z, 0-9, dot, underscore, tilde, at, or hyphen."
validate_domain "--dashboard-domain" "$DASHBOARD_DOMAIN"
validate_domain "--webui-domain" "$WEBUI_DOMAIN"
validate_domain "--mc-domain" "$MC_DOMAIN"
validate_domain "--auth-domain" "$AUTHELIA_DOMAIN"
if [[ -n "$CALLBACK_DOMAIN" ]]; then
	validate_domain "--callback-domain" "$CALLBACK_DOMAIN"
fi
validate_port "--dashboard-port" "$DASHBOARD_PORT"
validate_port "--webui-port" "$WEBUI_PORT"
validate_port "--mc-port" "$MC_PORT"
validate_port "--api-port" "$API_PORT"
validate_port "--authelia-port" "$AUTHELIA_PORT"
case "$INSTALL_AUTHELIA" in
0 | 1) ;;
true | yes) INSTALL_AUTHELIA=1 ;;
false | no) INSTALL_AUTHELIA=0 ;;
*) fail "AEX_INSTALL_AUTHELIA must be 1/0, true/false, or yes/no." ;;
esac
[[ "$DASHBOARD_PORT" != "$WEBUI_PORT" && "$DASHBOARD_PORT" != "$MC_PORT" && "$DASHBOARD_PORT" != "$AUTHELIA_PORT" && "$WEBUI_PORT" != "$MC_PORT" && "$WEBUI_PORT" != "$AUTHELIA_PORT" && "$MC_PORT" != "$AUTHELIA_PORT" ]] || fail "Local app/auth ports must be distinct."
[[ -d "$REPO_DIR" || "$DRY_RUN" -eq 1 ]] || fail "Hermes repo dir not found: $REPO_DIR"
if [[ -n "$AUTHELIA_PASSWORD_HASH" ]]; then validate_password_hash "$AUTHELIA_PASSWORD_HASH"; fi

require_root_for_apply
preflight_authelia_inputs
discover_google_chat_callback_auth
ensure_cloudflare_dns

log "AEyeOps portal stack installation"
log "dashboard=$DASHBOARD_DOMAIN:$DASHBOARD_PORT webui=$WEBUI_DOMAIN:$WEBUI_PORT mc=$MC_DOMAIN:$MC_PORT auth=$AUTHELIA_DOMAIN:$AUTHELIA_PORT${CALLBACK_DOMAIN:+ callback=$CALLBACK_DOMAIN} api=127.0.0.1:$API_PORT hermes_home=$HERMES_HOME repo_dir=$REPO_DIR dry_run=$DRY_RUN"

if [[ "$SKIP_CADDY_INSTALL" -eq 0 ]]; then
	install_caddy_debian
else
	log "skipping Caddy install by request"
fi
configure_hermes_api_server
log_google_chat_callback_console_steps

ensure_git_checkout "Hermes WebUI" "$WEBUI_REPO" "$WEBUI_REF" "$WEBUI_DIR"
ensure_git_checkout "Mission Control" "$MC_REPO" "$MC_REF" "$MC_DIR"
build_mission_control

write_dashboard_service
write_webui_service
write_mc_service
ensure_caddy_import
ensure_caddy_log_dir
write_callback_caddy_snippet
write_logrotate
run_authelia_install

if [[ "$DRY_RUN" -eq 0 ]]; then
	run systemctl daemon-reload
	run systemctl enable --now hermes-dashboard.service
	run systemctl enable --now hermes-webui.service
	run systemctl enable --now mission-control.service
	if [[ "$RESTART_GATEWAY" -eq 1 ]]; then
		run systemctl restart hermes-gateway.service
	else
		log "Hermes API server env was ensured; restart hermes-gateway.service during a maintenance window if API server was not already enabled."
	fi
	run caddy validate --config /etc/caddy/Caddyfile
	ensure_caddy_log_dir
	run systemctl reload caddy
	if [[ "$SKIP_VERIFY" -eq 0 ]]; then
		run systemctl is-active --quiet hermes-dashboard.service
		run systemctl is-active --quiet hermes-webui.service
		run systemctl is-active --quiet mission-control.service
		run systemctl is-active --quiet aeyeops-authelia.service
		run curl -fsS "http://127.0.0.1:$WEBUI_PORT/health" >/dev/null
		run curl -fsS "http://127.0.0.1:$MC_PORT/api/status?action=health" >/dev/null
		if [[ -x "$VERIFY_SCRIPT" ]]; then
			run "$VERIFY_SCRIPT" --dashboard-domain "$DASHBOARD_DOMAIN" --webui-domain "$WEBUI_DOMAIN" --mc-domain "$MC_DOMAIN" --auth-domain "$AUTHELIA_DOMAIN" --hermes-home "$HERMES_HOME" --dashboard-port "$DASHBOARD_PORT" --webui-port "$WEBUI_PORT" --mc-port "$MC_PORT" --authelia-port "$AUTHELIA_PORT" --skip-external
		fi
		if [[ -n "$CALLBACK_DOMAIN" ]]; then
			callback_status=""
			for callback_path in /google-chat/actions /google-chat/events; do
				callback_status="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "https://$CALLBACK_DOMAIN$callback_path" -H 'Content-Type: application/json' --data '{}' || true)"
				[[ "$callback_status" == "401" ]] || fail "Callback endpoint $callback_path without Google token returned $callback_status; expected 401."
			done
		fi
	fi
else
	log "dry-run: skipped systemctl/caddy changes and local probes"
fi

log "Install complete. After DNS is pointed here, Caddy will request certs for: $DASHBOARD_DOMAIN, $WEBUI_DOMAIN, $MC_DOMAIN, $AUTHELIA_DOMAIN${CALLBACK_DOMAIN:+, $CALLBACK_DOMAIN}."
