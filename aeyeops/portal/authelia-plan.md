# AEyeOps Authelia Portal Auth Plan

This plan adds Authelia to the existing Caddy portal stack. It does **not**
replace Caddy. Caddy remains responsible for ACME TLS, reverse proxying,
security headers, access logs, and loopback-only upstream routing.

## Decision

Use Authelia as the next authentication layer only after it is proven against
all three AEyeOps portal hostnames:

- `dashboard.example.com` -> Hermes dashboard on `127.0.0.1:9119`
- `webui.example.com` -> Hermes WebUI on `127.0.0.1:8787`
- `mc.example.com` -> Mission Control on `127.0.0.1:3000`

Authelia should run on loopback only, normally `127.0.0.1:9091`, and Caddy
should use its built-in `forward_auth` directive against Authelia's forward-auth
authorization endpoint.

Minimum protected-site Caddy shape:

```caddyfile
forward_auth 127.0.0.1:9091 {
  uri /api/authz/forward-auth
  copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
}
```

## Drivers

- Keep the current working Caddy install and TLS automation.
- Replace the browser-owned Basic Auth modal with a real login page.
- Allow session cookies and MFA without buying an external access product.
- Preserve loopback-only upstreams and public-fork-safe configuration.
- Avoid cutting over partially: all three portals must be proven before Basic
  Auth rollback snippets are removed as the operational fallback.

## Vetted compatibility

- Authelia documents Caddy as a supported reverse proxy via Caddy's official
  forward-auth integration.
- Caddy documents `forward_auth` as the standard authentication delegation
  directive and includes an Authelia example.
- The live host's Caddy version is `v2.11.2`.
- A temporary Caddyfile using `forward_auth 127.0.0.1:9091` for all three
  portal hostnames validates successfully with `caddy validate`.

## Target request flow

```text
Browser
  -> https://dashboard.example.com / webui.example.com / mc.example.com
  -> Caddy
     - TLS and security headers
     - forward_auth 127.0.0.1:9091 /api/authz/forward-auth
  -> Authelia
     - login page
     - session cookie
     - optional MFA policy
  -> Caddy upstream proxy
  -> loopback app
```

The Authelia portal itself is exposed as a separate hostname:

```text
https://auth.example.com -> Caddy -> 127.0.0.1:9091
```

## Rollout gates

Do not remove the saved Caddy Basic Auth rollback snippets until every gate
passes:

1. `caddy validate` accepts the generated Authelia snippets.
2. `auth.example.com` serves the Authelia login page through Caddy.
3. Authelia binds only to loopback.
4. Unauthenticated requests to `dashboard`, `webui`, and `mc` redirect to
   Authelia and do not reach the upstream apps.
5. Authenticated browser sessions reach:
   - dashboard root and `/api/status`
   - WebUI root and `/health`
   - Mission Control root and `/api/status?action=health`
6. Public scans show only Caddy on `80/443`; app ports and Authelia remain
   loopback-only.
7. Logs show no proxy loop and no authorization bypass.
8. Rollback from Authelia snippets to Basic Auth snippets is documented and
   tested with `caddy validate` before live reload.
9. Host-local Authelia config has valid session cookie settings for the shared
   domain family, Authelia public URL, default redirect behavior, and
   access-control policy entries for all three protected hostnames.

## Migration sequence

1. Keep current Basic Auth snippets as the known-good rollback fallback.
2. Install Authelia and generate host-local secrets/config outside git.
3. Add `auth.example.com` to Caddy and verify the login page.
4. Create Authelia-protected Caddy snippets in a temporary directory and validate
   them with the live Caddy binary.
5. Canary `mc` first.
6. Canary `webui` second.
7. Canary `dashboard` last.
8. Run the all-three outside-in verification harness.
9. Remove Basic Auth only after all-three success evidence is captured.

During Authelia canaries, Basic Auth should normally be rollback config, not an
active second gate. If both gates are intentionally enabled, use explicit Caddy
`route` ordering and adjust tests to expect the Basic Auth browser challenge
before any Authelia redirect.

## Rollback

Rollback is Caddy-only if the apps remain unchanged:

1. Restore the last known-good Basic Auth Caddy snippets.
2. Run `caddy validate --config /etc/caddy/Caddyfile`.
3. Reload Caddy.
4. Verify unauthenticated requests return `401` and authenticated requests return
   `200` for all three portals.
5. Leave Authelia stopped or running on loopback until its logs are reviewed.

## Installer script shape

The implementation is a separate script:

```text
aeyeops/scripts/install-authelia-portal-auth.sh
```

It should be explicit and non-destructive:

- `--dry-run`
- `--auth-domain`
- `--dashboard-domain`
- `--webui-domain`
- `--mc-domain`
- `--authelia-port`
- `--keep-basic-auth`
- `--canary mc|webui|dashboard|all`
- `--rollback-to-basic-auth`

The existing `install-portal-stack.sh` should not silently replace Basic Auth.
Any Authelia cutover must be opt-in and validated outside-in.
