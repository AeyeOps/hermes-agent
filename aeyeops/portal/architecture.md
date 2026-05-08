# Secured Hermes Dashboard Architecture

## Goal

Expose one Hermes web application over straight HTTPS from a Linux host, with
no VPN requirement, while keeping the Hermes dashboard bound to loopback and
placing durable authentication and TLS in front of it.

Example public hostname for rollout docs: `dashboard.example.com`.

## Non-goals

- Do not commit secrets, password hashes for live users, DNS provider tokens, or
  private IP inventory.
- Do not bind Hermes directly to `0.0.0.0`.
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

## Authentication options

### Phase 1: Caddy Basic Auth

Use this first because it is local, auditable, and headless. Store only the
hashed password in the host-local Caddy site file; never commit the hash to the
repo. The setup script writes the managed site file as `root:caddy` `0640` when
the `caddy` group exists. Use a long randomly generated passphrase.

### Phase 2 option: Cloudflare Access

If the dashboard stays exposed long-term or needs multiple operators, front the
hostname with Cloudflare Access for MFA/SSO. Caddy can remain as the local TLS or
origin proxy layer, but Cloudflare Access becomes the identity gate.

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
