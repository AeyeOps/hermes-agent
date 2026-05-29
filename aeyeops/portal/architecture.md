# Secured Hermes Dashboard Architecture

## Goal

Expose one Hermes web application over straight HTTPS from a Linux host, with
no VPN requirement, while keeping the Hermes dashboard bound to loopback and
placing durable authentication and TLS in front of it. The same pattern can
serve Hermes at a dedicated hostname root or at a Mission-Control-style path
prefix such as `/hermes`.

Example public hostname for rollout docs: `dashboard.example.com`.

## Non-goals

- Do not commit secrets, password hashes for live users, DNS provider tokens, or
  private IP inventory.
- Do not bind Hermes directly to `0.0.0.0`; only Caddy listens publicly.
- Do not enable the embedded TUI/chat surface until the base dashboard exposure
  has been verified.

## Why a reverse proxy is required

Hermes dashboard defaults to `127.0.0.1:9119` and its own code warns before
non-local binds because the dashboard can expose API keys and config. The web
server also uses a per-process session token injected into the SPA and performs
Host-header validation for DNS-rebinding defense.

A reverse proxy lets us keep those local assumptions intact while adding the
public-facing pieces Hermes should not own directly:

- ACME TLS certificates and renewal.
- A stable public hostname.
- Authentication before any request reaches Hermes, including Hermes endpoints
  that are intentionally public/read-only on localhost.
- Centralized access logs for review.

## Chosen design

```text
Browser
  -> https://dashboard.example.com
  -> Caddy :443 / :80
     - HTTPS certificate via ACME
     - Basic Auth for every route
     - security headers
     - access/error logs
     - upstream Host rewritten to 127.0.0.1:9119
     - optional path prefix stripped before proxying
     - optional `X-Forwarded-Prefix` injected for SPA/assets
  -> http://127.0.0.1:9119
     - Hermes dashboard bound to loopback only
```

### Caddy Host-header behavior

Hermes validates the request `Host` against the host it bound. If the dashboard
binds to `127.0.0.1`, proxied requests with `Host: dashboard.example.com` may be
rejected by Hermes. The Caddy upstream config therefore sets:

```caddyfile
header_up Host 127.0.0.1:9119
```

The browser still sees `dashboard.example.com`; this only affects the internal
proxy-to-Hermes hop.

## Web surfaces covered

The Hermes dashboard is the container for the current web surfaces:

- Core dashboard pages: config, auth/providers, models, sessions, logs, cron,
  skills, plugins, profiles, and analytics.
- Dashboard plugins discovered at startup, including Kanban (`/kanban`) and
  Achievements (`/achievements`) when present in the checkout.
- Optional embedded Chat/TUI over WebSocket when the service is started with
  `--tui`; keep this off until base exposure is proven.

Do not create separate public listeners for Kanban or dashboard plugins. They
are mounted under the same dashboard origin and inherit the same Caddy login.

## Mission Control path-prefix mode

For a shared public host such as `mission-control.example.com`, expose Hermes at
`/hermes` rather than consuming the site root. Caddy should strip `/hermes`
before proxying and set `X-Forwarded-Prefix: /hermes`. Hermes rewrites SPA
asset URLs and plugin asset URLs from that header, so `/hermes/kanban`,
`/hermes/api/...`, and `/hermes/dashboard-plugins/...` continue to work.

## Authentication options and staged hardening

### Phase 1: Caddy Basic Auth

Use this first because it is local, auditable, and headless. Store only the
hashed password in the host-local Caddy site file; never commit the hash to the
repo. The setup script writes the managed site file as `root:caddy` `0640` when
the `caddy` group exists. Use a long randomly generated passphrase.

### Phase 2: Authelia in front of all portal hostnames

Authelia is the preferred next local authentication layer when Basic Auth's
browser-owned modal dialog becomes too awkward or when multiple operators,
session cookies, and MFA are needed. It is additive: Caddy remains the public
TLS terminator, reverse proxy, logger, and security-header layer. Authelia runs
as a loopback-only service and Caddy delegates authorization with its standard
`forward_auth` directive.

Do not cut over one portal permanently until all three portal hostnames have
passed the Authelia proof gate below:

```text
Browser
  -> Caddy :443
     - ACME TLS
     - security headers and access logs
     - forward_auth to Authelia on 127.0.0.1:9091
  -> Authelia login/session/MFA policy
  -> loopback upstream:
     - dashboard: 127.0.0.1:9119
     - webui:     127.0.0.1:8787
     - mc:        127.0.0.1:3000
```

Local vetting already required before implementation:

- Caddy version must satisfy Authelia's documented Caddy requirement.
- `caddy validate` must accept an Authelia `forward_auth` Caddyfile shape for
  `dashboard`, `webui`, and `mc`.
- Authelia must bind only to loopback, for example `127.0.0.1:9091`.
- `auth.example.com` must render the Authelia portal through Caddy.
- Each protected hostname must redirect unauthenticated browser requests to
  Authelia and must return `200` after login.
- Direct unauthenticated requests to each protected hostname must not reach the
  upstream application.
- Direct public access to `9119`, `8787`, `3000`, `8642`, and `9091` must be
  closed or loopback-only.

Migration sequence:

1. Install Authelia with a host-local config directory and secrets under
   `$HERMES_HOME/aeyeops-portal/authelia/` or another ignored host-local path.
2. Publish `auth.example.com` through Caddy while leaving the three portal sites
   on Basic Auth.
3. Add a canary Caddy snippet for one hostname in a temporary config and validate
   it with `caddy validate`; do not reload live Caddy yet.
4. Enable Authelia for `mc` first because it is the least Hermes-sensitive of
   the three public portal surfaces.
5. Run outside-in checks for `mc`: unauthenticated redirect to Authelia,
   successful login, loopback-only upstream, and no Basic Auth fallback leak.
6. Repeat for `webui`, then `dashboard`.
7. Remove Basic Auth only after all three pass the same outside-in checks.
   Keep the prior Basic Auth snippets as rollback files during migration. Do not
   treat active Basic Auth as the normal second gate while testing Authelia,
   because Caddy directive ordering can cause `basic_auth` to run before
   `forward_auth`, preserving the browser modal and hiding Authelia behavior. If
   an operator intentionally wants both gates active during a canary, the Caddy
   snippet must use explicit `route` ordering and the outside-in expectations
   must account for the Basic Auth challenge.

### Phase 3 option: Cloudflare proxy or Access

If the dashboard stays exposed long-term or needs multiple operators, front the
hostname with Cloudflare Access for MFA/SSO. Caddy can remain as the local TLS or
origin proxy layer, but Cloudflare Access becomes the identity gate.

Do not assume Cloudflare protects the origin unless the portal records are
proxied and Caddy or the host firewall rejects direct-origin requests for the
portal hostnames. If Headscale or another public service intentionally exposes
the same origin IP, an attacker can still reach Caddy directly by IP with a
portal `Host` header unless this origin gate is added.

## Systemd service

Run the dashboard independently from `hermes-gateway`:

- Unit: `hermes-dashboard.service`.
- Working directory: `$HERMES_HOME/hermes-agent`.
- Command: `venv/bin/python -m hermes_cli.main dashboard --host 127.0.0.1 --port
  9119 --no-open`.
- Optional embedded TUI: add `--tui` only after initial hardening checks pass.

## Logging

Setup and verification scripts write timestamped logs under:

```text
$HERMES_HOME/logs/aeyeops-portal-setup-YYYYmmdd-HHMMSS.log
$HERMES_HOME/logs/aeyeops-portal-verify-YYYYmmdd-HHMMSS.log
```

Runtime logs to inspect:

- `journalctl -u hermes-dashboard`
- `journalctl -u caddy`
- `/var/log/caddy/hermes-dashboard-access.log`
- `$HERMES_HOME/logs/agent.log`
- `$HERMES_HOME/logs/errors.log`

Retention controls:

- Caddy dashboard access logs roll at 10 MiB, keep 5 rolled files, and expire
  rolled files after 30 days. This bounds proxy access logs without relying on
  global logrotate behavior.
- Setup/verification logs under `$HERMES_HOME/logs/aeyeops-portal-*.log` are
  registered with logrotate: daily, 14 rotations, compressed. They are one-shot
  command logs, so no long-running process needs signaling after rotation.
- `hermes-dashboard` stdout/stderr goes to journald. Use the host's normal
  journald retention policy rather than adding a second service log file.

## Firewall posture

Public internet:

- Allow TCP `80` and `443` to Caddy.
- Restrict SSH as tightly as practical.
- Do not allow public TCP `9119`.

Localhost only:

- Hermes dashboard `127.0.0.1:9119`.
- Hermes WebUI `127.0.0.1:8787`.
- Mission Control `127.0.0.1:3000`.
- Hermes API Server `127.0.0.1:8642`.
- Future Authelia service `127.0.0.1:9091`.

## Verification checklist

1. DNS resolves the chosen hostname to the host.
2. `systemctl is-active hermes-dashboard` succeeds.
3. `systemctl is-active caddy` succeeds.
4. Local dashboard status works:
   ```bash
   curl -fsS -H 'Host: 127.0.0.1:9119' http://127.0.0.1:9119/api/status
   ```
5. Unauthenticated public HTTPS gets `401` from Caddy.
6. Authenticated public HTTPS reaches the dashboard.
7. Direct public `:9119` is closed or filtered.
8. Caddy logs show expected requests and no proxy loops.

For Authelia cutover, extend the checklist:

9. `auth.example.com` reaches the Authelia login portal through Caddy.
10. `dashboard`, `webui`, and `mc` each redirect unauthenticated users to
    Authelia instead of showing a browser Basic Auth modal.
11. After login, all three portal roots and health/status endpoints return
    success through Caddy.
12. Direct requests without an Authelia session do not reach the upstream apps.
13. Authelia, Hermes API, and all app ports remain loopback-only.
14. Generated portal snippets use the minimum Caddy shape:
    ```caddyfile
    forward_auth 127.0.0.1:9091 {
      uri /api/authz/forward-auth
      copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
    }
    ```
15. Host-local Authelia config defines session cookie settings for the shared
    domain family, the Authelia public URL, default redirect behavior, and
    access-control rules for all three portal hostnames.

## Rollback

The setup script backs up existing Caddy and systemd files before replacing
managed files. Rollback is:

```bash
sudo systemctl disable --now hermes-dashboard
sudo rm -f /etc/systemd/system/hermes-dashboard.service
sudo systemctl daemon-reload
sudo cp -p /path/to/Caddyfile.backup /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If Hermes behavior is suspect after rollback, leave the public proxy disabled
and use the existing gateway runbook to verify platform health from
`$HERMES_HOME/logs/agent.log` and `$HERMES_HOME/logs/errors.log`.
