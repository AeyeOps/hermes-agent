# AeyeOps Portal Exposure

Fork-only operational assets for exposing Hermes web surfaces over public HTTPS
without publishing the upstream application services themselves to the network.

This directory intentionally contains no secrets, host-specific IP addresses, or
live credentials. Operator-provided values are passed to the setup script at run
time and written to host-local system files.

## Target shape

```text
Internet HTTPS :443
  -> Caddy on the target host (TLS + authentication)
  -> loopback-only upstream services:
     - Hermes dashboard on 127.0.0.1:9119
     - Hermes WebUI on 127.0.0.1:8787
     - Mission Control on 127.0.0.1:3000

Supported public URL shapes:
- Stack hostnames: `https://dashboard.example.com/`, `https://webui.example.com/`, `https://mc.example.com/`
- Dedicated dashboard hostname: `https://dashboard.example.com/`
- Dashboard path under another app: `https://mission-control.example.com/hermes/`
```

The dashboard is powerful: it can inspect sessions, edit config, manage provider
auth state, and optionally expose an embedded TUI/chat surface. Treat it as an
admin console, not a public app.

## Files

- `architecture.md` — security model, threat assumptions, and rollout sequence.
- `authelia-plan.md` — staged Caddy + Authelia login-page upgrade plan.
- `Caddyfile.example` — placeholder-only Caddy reverse proxy config.
- `systemd/hermes-dashboard.service` — localhost-only dashboard unit template.
- `systemd/aeyeops-hermes-portal.logrotate` — template for bounded setup/verify log retention.
- `scripts/setup-dashboard-proxy.sh` — headless, logged installer for Caddy +
  systemd wiring.
- `scripts/verify-dashboard-proxy.sh` — headless verification probe.
- `../scripts/install-caddy-dashboard-proxy.sh` — repeatable end-to-end
  bootstrap that installs Caddy from the official stable package repository
  on Debian-family hosts, then calls the setup and local verification scripts.
- `../scripts/install-authelia-portal-auth.sh` — Authelia staging installer
  used by the stack installer's `--auth-mode authelia` path; it preserves Basic
  Auth snippets as rollback artifacts before canarying the public hostnames.


## Three-app portal stack

For the full host portal, prefer the stack installer from the repo root:

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

The stack installer:

- installs/enables Caddy on Debian-family hosts unless `--skip-caddy-install` is used;
- configures the built-in Hermes dashboard on loopback;
- clones/configures `nesquena/hermes-webui` on loopback in Gateway-backed mode;
- clones/builds Builderz Mission Control on loopback;
- writes Basic Auth Caddy snippets for the three public hostnames as rollback
  artifacts;
- in `--auth-mode authelia`, installs the Authelia auth hostname and canaries
  all three public app hostnames to Caddy `forward_auth`;
- in `--auth-mode basic`, leaves Basic Auth as the active public gate;
- ensures Hermes API Server settings are present in `$HERMES_HOME/.env`;
- leaves the gateway restart explicit via `--restart-gateway` to avoid surprising
  the live Hermes service.

Use this for the desired public shape:

```text
https://dashboard.example.com/  -> Hermes dashboard
https://webui.example.com/      -> Hermes WebUI
https://mc.example.com/         -> Mission Control
```

Keep the actual domains and local install roots in `aeyeops/.env`; the tracked
examples use placeholders only.

## Authelia login-page mode

Fresh AEyeOps portal installs should use `--auth-mode authelia` when the auth
hostname and host-local Authelia password hash are configured. The stack first
writes Basic Auth snippets so rollback is available, then invokes
`../scripts/install-authelia-portal-auth.sh --write-auth-portal --canary all`
to make Authelia the active browser gate.

Authelia is an additive layer behind Caddy, not a Caddy replacement:

```text
Browser -> Caddy TLS -> Caddy forward_auth -> Authelia -> loopback app
```

Do not remove the saved Basic Auth rollback snippets. During normal Authelia
canaries, Basic Auth should be rollback config rather than an active second gate
so the browser modal does not mask Authelia behavior. Use `--auth-mode basic`
only when you intentionally want the simple Basic Auth fallback.

Clean-clone recipe:

1. Copy `aeyeops/.env.example` to ignored `aeyeops/.env`.
2. Set the dashboard, WebUI, Mission Control, and Authelia hostnames.
3. Set `AEX_PORTAL_AUTH_MODE=authelia`.
4. Store the Caddy Basic Auth password or hash in a host-local file for
   rollback snippet generation.
5. Store the Authelia password hash in a host-local file referenced by
   `AEX_AUTHELIA_USER_PASSWORD_HASH_FILE`.
6. Leave `AEX_INSTALL_AUTHELIA=1` for a fresh host, or set it to `0` only when
   Authelia is already installed and should not be managed by the stack.
7. Dry-run the full stack:
   ```bash
   sudo aeyeops/scripts/install-portal-stack.sh \
     --auth-mode authelia \
     --dry-run
   ```
8. Apply after DNS and host-local values are ready:
   ```bash
   sudo aeyeops/scripts/install-portal-stack.sh \
     --auth-mode authelia
   ```

Existing Basic Auth-only hosts should set `AEX_PORTAL_AUTH_MODE=basic` before
routine reruns unless the rerun is intentionally cutting over to Authelia.

The staging installer also supports focused dry-run/validation, auth-portal
install, per-host canaries, and rollback:

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

## Minimal rollout: install Caddy and configure the dashboard

The one-shot installer is the preferred host bootstrap path. It is idempotent:
if Caddy is already installed it enables/starts the existing service, then
rewrites only the AEyeOps-managed dashboard service, Caddy snippet, and
logrotate file. It does not store secrets in the repo.

1. Point DNS for the chosen hostname at the VPS.
2. Store the Basic Auth password or Caddy password hash in a host-local file
   outside git:
   ```bash
   install -m 0600 /dev/null "$HERMES_HOME/.portal-password"
   $EDITOR "$HERMES_HOME/.portal-password"
   ```
   The end-to-end installer can hash this after installing Caddy. If a hash
   file already exists, use `--password-hash-file` instead.
3. Dry-run the full install/configuration:
   ```bash
   sudo aeyeops/scripts/install-caddy-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --user operator \
     --password-file $HERMES_HOME/.portal-password \
     --hermes-home "$HERMES_HOME" \
     --dry-run
   ```
4. Apply after reviewing the plan:
   ```bash
   sudo aeyeops/scripts/install-caddy-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --user operator \
     --password-file $HERMES_HOME/.portal-password \
     --hermes-home "$HERMES_HOME"
   ```
5. Verify:
   ```bash
   sudo aeyeops/portal/scripts/verify-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --hermes-home "$HERMES_HOME"
   ```

### Mission Control path-prefix rollout

If another Caddy site already owns the hostname, publish Hermes under a path
prefix instead of taking over the root:

```bash
sudo aeyeops/portal/scripts/setup-dashboard-proxy.sh \
  --domain mission-control.example.com \
  --path-prefix /hermes \
  --user operator \
  --password-hash-file $HERMES_HOME/.portal-caddy-hash \
  --hermes-home "$HERMES_HOME"

sudo aeyeops/portal/scripts/verify-dashboard-proxy.sh \
  --domain mission-control.example.com \
  --path-prefix /hermes \
  --hermes-home "$HERMES_HOME"
```

The equivalent end-to-end bootstrap is:

```bash
sudo aeyeops/scripts/install-caddy-dashboard-proxy.sh \
  --domain mission-control.example.com \
  --path-prefix /hermes \
  --user operator \
  --password-file $HERMES_HOME/.portal-password \
  --hermes-home "$HERMES_HOME"
```

Kanban, Achievements, sessions, logs, cron, config, and optional Chat/TUI are
all tabs/plugins inside the one Hermes dashboard process; they do not need
separate public ports.

## Embedded TUI/chat

Leave embedded TUI/chat off for first exposure. After HTTPS/auth/firewall checks
are proven, re-run setup with `--enable-tui` if the browser-based chat surface is
wanted.
