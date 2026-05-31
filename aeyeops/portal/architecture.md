# Secured AEyeOps Portal Architecture

## Goal

Expose the AEyeOps Hermes web surfaces over HTTPS from a Linux host, with no VPN
requirement, while keeping all application services bound to loopback and
placing a durable web login in front of every browser-facing route.

Example public hostnames for rollout docs:

- `auth.example.com`
- `dashboard.example.com`
- `webui.example.com`
- `mc.example.com`

## Non-goals

- Do not commit secrets, password hashes for live users, DNS provider tokens, or
  private IP inventory.
- Do not bind Hermes, WebUI, Mission Control, the Hermes API server, or Authelia
  directly to `0.0.0.0`.
- Do not generate browser-owned authentication modal gates as part of the portal
  install path.
- Do not enable the embedded TUI/chat surface until the base dashboard exposure
  has been verified.

## Why Caddy and Authelia are both used

Caddy owns the public edge: ACME TLS, HTTPS listeners, security headers, reverse
proxying, and access logs. Authelia owns the browser login page, session cookie,
and authorization check. The application services stay private on loopback.

```text
Browser
  -> Caddy :443 / :80
     - ACME TLS
     - security headers
     - access logs
     - forward_auth to Authelia on 127.0.0.1:9091
  -> Authelia login/session policy
  -> Caddy upstream proxy
  -> loopback services
```

## Web surfaces covered

- Hermes dashboard: `127.0.0.1:9119`
- Hermes WebUI: `127.0.0.1:8787`
- Mission Control: `127.0.0.1:3000`
- Hermes API Server for WebUI bridge: `127.0.0.1:8642`
- Authelia: `127.0.0.1:9091`

Kanban, Achievements, sessions, logs, cron, config, and optional Chat/TUI are
all tabs/plugins inside the one Hermes dashboard process; they do not need
separate public ports.

## Caddy Host-header behavior

Hermes validates the request `Host` against the host it bound. If the dashboard
binds to `127.0.0.1`, proxied requests with `Host: dashboard.example.com` may be
rejected by Hermes. The dashboard Caddy snippet sets:

```caddyfile
header_up Host 127.0.0.1:9119
```

The browser still sees `dashboard.example.com`; this only affects the internal
proxy-to-Hermes hop.

## Authentication model

The generated protected-host snippets use Caddy `forward_auth`:

```caddyfile
forward_auth 127.0.0.1:9091 {
  uri /api/authz/forward-auth
  copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
}
```

Expected unauthenticated behavior for `dashboard`, `webui`, and `mc` is a
redirect to `https://auth.example.com`, then a successful return to the original
application after login.

## Systemd services

The stack installer owns these units:

- `hermes-dashboard.service`
- `hermes-webui.service`
- `mission-control.service`
- `aeyeops-authelia.service`

All app/auth listeners must bind to loopback. Caddy is the only public listener
for the portal hostnames.

## Logging

Setup and verification scripts write timestamped logs under:

```text
$HERMES_HOME/logs/aeyeops-portal-setup-YYYYmmdd-HHMMSS.log
$HERMES_HOME/logs/aeyeops-portal-verify-YYYYmmdd-HHMMSS.log
```

Runtime logs to inspect:

- `journalctl -u hermes-dashboard`
- `journalctl -u hermes-webui`
- `journalctl -u mission-control`
- `journalctl -u aeyeops-authelia`
- `journalctl -u caddy`
- `/var/log/caddy/*access.log`
- `$HERMES_HOME/logs/agent.log`
- `$HERMES_HOME/logs/errors.log`

## Firewall posture

Public internet:

- Allow TCP `80` and `443` to Caddy.
- Restrict SSH as tightly as practical.

Localhost only:

- Hermes dashboard `127.0.0.1:9119`.
- Hermes WebUI `127.0.0.1:8787`.
- Mission Control `127.0.0.1:3000`.
- Hermes API Server `127.0.0.1:8642`.
- Authelia `127.0.0.1:9091`.

## Verification gates

A portal install is healthy when:

1. `caddy validate --config /etc/caddy/Caddyfile` passes.
2. `aeyeops-authelia.service` is active.
3. Authelia listens only on loopback.
4. `auth.example.com` serves the Authelia portal through Caddy.
5. Unauthenticated requests to `dashboard`, `webui`, and `mc` redirect to
   Authelia and do not reach the upstream apps.
6. Authenticated browser sessions reach the dashboard, WebUI, and Mission
   Control roots and health/status endpoints.
7. Public scans show only Caddy on `80/443`; app ports and Authelia remain
   loopback-only.
8. Logs show no proxy loop and no authorization bypass.
