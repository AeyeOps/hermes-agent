# AEyeOps Portal Exposure

Fork-only operational assets for exposing Hermes web surfaces over public HTTPS
without publishing the upstream application services themselves to the network.

This directory intentionally contains no secrets, host-specific IP addresses, or
live credentials. Operator-provided values are passed to the setup script at run
time and written to host-local system files.

## Target shape

```text
Internet HTTPS :443
  -> Caddy on the target host (TLS + security headers + access logs)
  -> Authelia on 127.0.0.1:9091 for web portal login
  -> loopback-only upstream services:
     - Hermes dashboard on 127.0.0.1:9119
     - Hermes WebUI on 127.0.0.1:8787
     - Mission Control on 127.0.0.1:3000
```

Supported public URL shape:

```text
https://auth.example.com/       -> Authelia login portal
https://dashboard.example.com/  -> Hermes dashboard
https://webui.example.com/      -> Hermes WebUI
https://mc.example.com/         -> Mission Control
```

The dashboard is powerful: it can inspect sessions, edit config, manage provider
auth state, and optionally expose an embedded TUI/chat surface. Treat it as an
admin console, not a public app.

## Files

- `architecture.md` — security model and current target architecture.
- `Caddyfile.example` — placeholder-only Caddy reverse proxy config using
  Authelia `forward_auth`.
- `systemd/hermes-dashboard.service` — localhost-only dashboard unit template.
- `systemd/aeyeops-hermes-portal.logrotate` — template for bounded setup/verify
  log retention.
- `scripts/verify-portal-stack.sh` — headless verification probe for the
  dashboard, WebUI, and Mission Control behind Authelia.
- `../scripts/install-portal-stack.sh` — repeatable end-to-end bootstrap for
  Caddy, Authelia, dashboard, WebUI, and Mission Control.
- `../scripts/install-authelia-portal-auth.sh` — Authelia config/service/Caddy
  snippet writer used by the stack installer.

## Clean-clone install recipe

1. Copy `aeyeops/.env.example` to ignored `aeyeops/.env`.
2. Set the dashboard, WebUI, Mission Control, and Authelia hostnames.
3. Store either an Authelia-compatible password hash in a host-local file
   referenced by `AEX_AUTHELIA_USER_PASSWORD_HASH_FILE`, or store the existing
   portal password in a host-local file referenced by `AEX_AUTHELIA_USER_PASSWORD_FILE`
   so the installer can generate the hash locally after installing Authelia.
4. Optional but recommended: set `AEX_CLOUDFLARE_DNS=auto` and
   `AEX_CLOUDFLARE_KEYS_FILE` to a host-local file containing a scoped
   Cloudflare token. The installer will idempotently upsert the app and auth
   A records before Caddy requests certificates.
5. Leave `AEX_INSTALL_AUTHELIA=1` for a fresh host, or set it to `0` only when
   Authelia is already installed and should not be managed by this installer.
6. Dry-run the full stack:
   ```bash
   sudo aeyeops/scripts/install-portal-stack.sh --dry-run
   ```
7. Apply after DNS and host-local values are ready:
   ```bash
   sudo aeyeops/scripts/install-portal-stack.sh
   ```

The install path goes directly to the Authelia web login page. It writes the current Authelia-protected Caddy snippets directly.

## Password hash generation

Generate the hash on the host and store only the hash file path in
`aeyeops/.env`. Prefer Authelia's own hash generator when available:

```bash
install -d -m 0700 "$HERMES_HOME/aeyeops-portal"
authelia crypto hash generate argon2 --password 'replace-with-secret' \
  | awk '/Digest:/ {print $2}' \
  > "$HERMES_HOME/aeyeops-portal/authelia-password-hash"
chmod 0600 "$HERMES_HOME/aeyeops-portal/authelia-password-hash"
```

If `AEX_AUTHELIA_USER_PASSWORD_FILE` is set and no hash file exists, the installer can
create the hash locally after installing Authelia. Do not commit plaintext
passwords or password hashes.

## Verification

After apply and DNS/TLS readiness, verify the complete portal gate:

```bash
sudo aeyeops/portal/scripts/verify-portal-stack.sh \
  --dashboard-domain dashboard.example.com \
  --webui-domain webui.example.com \
  --mc-domain mc.example.com \
  --auth-domain auth.example.com \
  --hermes-home "$HERMES_HOME"
```

Expected unauthenticated behavior is an HTTP redirect to the Authelia portal,
not a browser-owned authentication modal.

## Embedded TUI/chat

Leave embedded TUI/chat off for first exposure. After HTTPS/auth/firewall checks
are proven, update the dashboard service intentionally if the browser-based chat
surface is wanted.
