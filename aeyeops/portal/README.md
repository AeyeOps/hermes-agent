# AeyeOps Portal Exposure

Fork-only operational assets for exposing the Hermes dashboard over public HTTPS
without publishing the dashboard service itself to the network.

This directory intentionally contains no secrets, host-specific IP addresses, or
live credentials. Operator-provided values are passed to the setup script at run
time and written to host-local system files.

## Target shape

```text
Internet HTTPS :443
  -> Caddy on the target host (TLS + authentication)
  -> Hermes dashboard on 127.0.0.1:9119 only
```

The dashboard is powerful: it can inspect sessions, edit config, manage provider
auth state, and optionally expose an embedded TUI/chat surface. Treat it as an
admin console, not a public app.

## Files

- `architecture.md` — security model, threat assumptions, and rollout sequence.
- `Caddyfile.example` — placeholder-only Caddy reverse proxy config.
- `systemd/hermes-dashboard.service` — localhost-only dashboard unit template.
- `systemd/aeyeops-hermes-portal.logrotate` — template for bounded setup/verify log retention.
- `scripts/setup-dashboard-proxy.sh` — headless, logged installer for Caddy +
  systemd wiring.
- `scripts/verify-dashboard-proxy.sh` — headless verification probe.

## Minimal rollout

1. Point DNS for the chosen hostname at the VPS.
2. Generate a Caddy password hash on the target host:
   ```bash
   caddy hash-password
   ```
3. Dry-run setup:
   ```bash
   sudo aeyeops/portal/scripts/setup-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --user operator \
     --password-hash-file $HERMES_HOME/.portal-caddy-hash \
     --hermes-home "$HERMES_HOME" \
     --dry-run
   ```
4. Apply setup after reviewing the plan:
   ```bash
   sudo aeyeops/portal/scripts/setup-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --user operator \
     --password-hash-file $HERMES_HOME/.portal-caddy-hash \
     --hermes-home "$HERMES_HOME"
   ```
5. Verify:
   ```bash
   sudo aeyeops/portal/scripts/verify-dashboard-proxy.sh \
     --domain dashboard.example.com \
     --hermes-home "$HERMES_HOME"
   ```

## Embedded TUI/chat

Leave embedded TUI/chat off for first exposure. After HTTPS/auth/firewall checks
are proven, re-run setup with `--enable-tui` if the browser-based chat surface is
wanted.
