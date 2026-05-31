# aeyeops/

AeyeOps fork workspace. Everything in this directory is fork-only and will be
excluded when we cherry-pick the Google Chat adapter into an upstream PR.

Upstream documentation (`AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `docs/`)
stays untouched until the PR lands. Our design notes, decisions, research, and
scratch work live here instead.

## Layout

- `runbook.md` — generic operational notes for installing, updating, and
  running this fork against a live gateway. Kept deliberately host-agnostic
  so it's safe in a public fork.
- `portal/` — secured HTTPS exposure plan, Caddy/systemd templates, and
  headless setup/verify scripts for the Authelia-protected portal stack.
- `scripts/` — fork-local operational scripts for managing an AEyeOps Hermes
  instance without touching upstream-owned script surfaces.


## Portal stack: dashboard, WebUI, and Mission Control

`aeyeops/scripts/install-portal-stack.sh` is the repeatable bootstrap for the
three web surfaces we expose from a Hermes host:

- `dashboard` — the built-in Hermes dashboard, loopback-only behind Caddy.
- `webui` — `nesquena/hermes-webui`, configured to use Hermes Gateway `/v1`.
- `mc` — Builderz Mission Control, loopback-only behind Caddy.

All three are fronted by Caddy HTTPS and protected by Authelia web login via
Caddy `forward_auth`. The stack installer writes the Authelia-protected
Caddy snippets directly; there is no generated browser-owned auth-challenge stage. It also ensures the Hermes API Server is enabled on
loopback so WebUI uses the same active Hermes model/provider configuration as
normal Hermes sessions instead of carrying a separate model config.

Hostnames, install directories, password/hash files, Cloudflare token paths,
and API keys belong in host-local `aeyeops/.env`, not in the public fork. When
`AEX_CLOUDFLARE_DNS=auto` and a scoped Cloudflare token is available, the stack
installer idempotently upserts the app/auth A records before Caddy requests
certificates.

Dry-run first:

```bash
sudo aeyeops/scripts/install-portal-stack.sh \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --auth-domain auth.example.com \
  --user operator \
  --authelia-password-file "$HERMES_HOME/aeyeops-portal/portal-password" \
  --hermes-home "$HERMES_HOME" \
  --dry-run
```

Apply after DNS is ready, then restart the gateway only during a maintenance
window if the API server was not already enabled:

```bash
sudo aeyeops/scripts/install-portal-stack.sh \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --auth-domain auth.example.com \
  --user operator \
  --authelia-password-file "$HERMES_HOME/aeyeops-portal/portal-password" \
  --hermes-home "$HERMES_HOME" \
  --restart-gateway
```

Caddy obtains certificates automatically for the three app hostnames and the
auth hostname once DNS points at the host and inbound 80/443 reach Caddy. The
stack installs Authelia when missing by default (`AEX_INSTALL_AUTHELIA=1`); use
`--skip-authelia-install` only when the binary is already installed. Keep the Authelia password hash and any plaintext password file out of git.

## Authelia portal auth helper

`aeyeops/scripts/install-authelia-portal-auth.sh` is the lower-level helper used
by the stack installer. Run it directly when only the Authelia config/service or
Caddy protected-host snippets need to be regenerated.

Validate the generated Authelia and Caddy shape without writing live files:

```bash
sudo aeyeops/scripts/install-authelia-portal-auth.sh \
  --auth-domain auth.example.com \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --user operator \
  --user-password-hash-file "$HERMES_HOME/aeyeops-portal/authelia-password-hash" \
  --write-auth-portal \
  --write-app-snippets \
  --validate-only
```

## Codex → Hermes → LiteLLM auth rebind

If Codex has been refreshed on a host, do **not** assume
`hermes auth status openai-codex` will also refresh a LiteLLM bridge. Stock
Hermes status only reports whether Hermes currently has usable credentials.
The AEyeOps bridge needs one explicit convergence step so Hermes and LiteLLM
bind to the same already-minted token:

```bash
cp aeyeops/.env.example aeyeops/.env
$EDITOR aeyeops/.env
./aeyeops/scripts/rebind-codex-auth-to-litellm.py
```

`aeyeops/.env` is ignored by git and should contain host-local paths/service
names only. The script performs no login. It loads `aeyeops/.env`, copies the
newest usable local Codex/Hermes OAuth material into Hermes auth, writes the
LiteLLM ChatGPT auth file, optionally restarts the bridge, and validates the
configured model alias. It backs up touched auth files first and does not print
token values.

Future fork work (if any) gets its own subdirectory alongside.
