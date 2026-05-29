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
- `googlechat/` — design notes, spec, architecture decisions, and runbooks for
  the Google Chat platform adapter.
- `portal/` — secured HTTPS exposure plan, Caddy/systemd templates, and
  headless setup/verify scripts for the Hermes dashboard.
- `scripts/` — fork-local operational scripts for managing an AEyeOps Hermes
  instance without touching upstream-owned script surfaces.


## Portal stack: dashboard, WebUI, and Mission Control

`aeyeops/scripts/install-portal-stack.sh` is the repeatable bootstrap for the
three web surfaces we expose from a Hermes host:

- `dashboard` — the built-in Hermes dashboard, loopback-only behind Caddy.
- `webui` — `nesquena/hermes-webui`, configured to use Hermes Gateway `/v1`.
- `mc` — Builderz Mission Control, loopback-only behind Caddy.

All three are fronted by Caddy HTTPS. Fresh AEyeOps installs should use
`--auth-mode authelia` so the active browser gate is an Authelia login page.
The installer still writes the Basic Auth Caddy snippets first so Authelia can
save them as rollback artifacts before it canaries all portal hostnames. Use
`--auth-mode basic` only for a simple fallback or emergency rollback baseline.

The script also ensures the Hermes API Server is enabled on loopback so WebUI
uses the same active Hermes model/provider configuration as normal Hermes
sessions instead of carrying a separate model config. Hostnames, install
directories, password files, and API keys belong in host-local `aeyeops/.env`,
not in the public fork.

Authelia behind Caddy is documented in `aeyeops/portal/authelia-plan.md`.

Dry-run first:

```bash
sudo aeyeops/scripts/install-portal-stack.sh \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --auth-domain auth.example.com \
  --auth-mode authelia \
  --user operator \
  --password-file "$HERMES_HOME/.portal-password" \
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
  --auth-mode authelia \
  --user operator \
  --password-file "$HERMES_HOME/.portal-password" \
  --hermes-home "$HERMES_HOME" \
  --restart-gateway
```

Caddy obtains certificates automatically for the three app hostnames once DNS
points at the host and inbound 80/443 reach Caddy. Authelia also needs DNS for
the auth hostname. In Authelia mode the stack installs Authelia when missing by
default (`AEX_INSTALL_AUTHELIA=1`); use `--skip-authelia-install` only when the
binary is already installed. The Authelia user password hash is read from
host-local `AEX_AUTHELIA_USER_PASSWORD_HASH_FILE`,
`AEX_AUTHELIA_USER_PASSWORD_HASH`, `--authelia-password-hash-file`, or
`--authelia-password-hash`; keep that value out of git.

To install only the Basic Auth fallback baseline, pass `--auth-mode basic`.
Existing Basic Auth-only hosts should set `AEX_PORTAL_AUTH_MODE=basic` before
routine reruns unless they intentionally want to cut over all portal hostnames
to Authelia.

## Authelia login-page staging

`aeyeops/scripts/install-authelia-portal-auth.sh` implements the Authelia
staging plan from `aeyeops/portal/authelia-plan.md`. The stack installer calls
it automatically in `--auth-mode authelia`; run it directly for focused
validation, canary, or rollback work. It keeps Caddy in place and preserves
Basic Auth snippets as the rollback path.

Validate the generated Authelia and Caddy shape without writing live files:

```bash
sudo aeyeops/scripts/install-authelia-portal-auth.sh \
  --auth-domain auth.example.com \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --user operator \
  --user-password-hash-file "$HERMES_HOME/.authelia-password-hash" \
  --validate-only
```

Canary one portal only after the auth portal is installed and reachable:

```bash
sudo aeyeops/scripts/install-authelia-portal-auth.sh \
  --auth-domain auth.example.com \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --user operator \
  --user-password-hash-file "$HERMES_HOME/.authelia-password-hash" \
  --write-auth-portal \
  --canary mc
```

Rollback restores saved Basic Auth snippets:

```bash
sudo aeyeops/scripts/install-authelia-portal-auth.sh \
  --auth-domain auth.example.com \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --user operator \
  --rollback-to-basic-auth \
  --canary all
```

## Hermes dashboard / Caddy portal bootstrap

Use the repeatable installer when bringing up the web dashboard on a host. It
loads optional defaults from ignored `aeyeops/.env`, installs Caddy from the
official stable package repository on Debian-family systems, configures the
loopback Hermes dashboard systemd unit, writes the AEyeOps Caddy snippet, and
runs a local verification probe:

```bash
sudo aeyeops/scripts/install-caddy-dashboard-proxy.sh \
  --domain dashboard.example.com \
  --path-prefix /hermes \
  --user operator \
  --password-file "$HERMES_HOME/.portal-password" \
  --hermes-home "$HERMES_HOME"
```

Use `--dry-run` first. Keep passwords, hashes, real hostnames, and local paths
in `aeyeops/.env` or other ignored host-local files, not in tracked docs.

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
