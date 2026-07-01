# MSGRAPH Multi-Tenant Bridge — Design Roadmap

> Status: **design locked, pre-build**. This document supersedes the earlier
> external-bridge sketch. Grounded in Hermes source as of `aeyeops-main`
> commit `9c77d37cf` (v0.17.0), verified by walking the webhook→agent flow
> and a graphify graph of `gateway/` (3,650 nodes / 7,535 edges).

## Problem

Two Microsoft 365 identities — `aeo` (<steve.antonakakis@aeyeops.com>, Entra ID
tenant `02c28aea-...`) and `pers` (<steve_anton@hotmail.com>, consumer MSA) —
need to push Graph events (email, calendar, OneDrive) into a single Hermes
agent, with the agent always able to tell which account an event came from,
and with per-tenant session isolation.

## The shape: V then Q

```
   aeo Graph subscriptions           pers Graph subscriptions
            │                                  │
            ▼                                  ▼
   ┌─────────────────┐                ┌─────────────────┐
   │ listener (aeo)  │                │ listener (pers) │     ← the V (split)
   │ client_state_a  │                │ client_state_p  │       each validates
   │ port/path A     │                │ port/path B     │       its own secret
   └────────┬────────┘                └────────┬────────┘
            │                                  │
            └──────────────┬───────────────────┘
                           ▼
                  ┌─────────────────┐
                  │  core adapter   │              ← consolidation
                  │  (routes map)   │
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │  single agent   │              ← the Q (one queue)
                  │  chat_id routes │                  per-tenant sessions
                  │  by tenant      │                  via chat_id_template
                  └─────────────────┘
```

The **V** is forced: each tenant has its own `client_state`, and the built-in
adapter binds one port + one path + one secret per instance. The **Q** is
preserved by encoding the tenant in `chat_id`, so `build_session_key` routes
each tenant's events into its own session within the single agent.

## Why the split (core + plugin), not core-only

Three concerns bundled in "multi-tenant MSGRAPH":

1. **Receiving + routing** — broadly useful, stable surface (mirrors
   `WebhookAdapter.routes`). Belongs in **core**.
2. **Tenant tagging via `chat_id`** — broadly useful (`chat_id_template`).
   Belongs in **core**.
3. **Subscription lifecycle + MSAL auth + consent flow** — niche, fast-changing,
   reads `~/.azure/` (forbidden for core per the credential-cache boundary).
   Belongs in a **plugin**.

Shipping all three into core risks rejection as overreach ("should be a
plugin"). The split aligns with the footprint ladder and de-risks the PR.

## The journey (traced from code)

```
1. Graph POSTs notification to listener (aeo or pers)
   → adapter._handle_notification()
   → route resolved from request path → picks the right client_state
   → _verify_client_state (hmac.compare_digest, timing-safe)
   → dedup by receipt_key (TTL ~24h, matches Graph retry window)

2. _build_message_event(notification, route)
   → builds SessionSource:
       platform = MSGRAPH_WEBHOOK
       chat_id = route.chat_id_template.format(subscriptionId, ...) 
                 e.g. "msgraph:aeo:sub-123"  ← THE KEY FIELD
       chat_type = "webhook"
   → MessageEvent(text = rendered prompt with tenant tag, source = above)

3. handle_message(event) → build_session_key(source)
   → "agent:main:msgraph_webhook:webhook:msgraph:aeo:sub-123"
   → this IS the session ID → loads its own history from SessionDB
   → aeo email session ≠ pers email session (automatic isolation)

4. _message_handler(event) → gateway runner → AIAgent
   → event.text is the user prompt (tenant-tagged in rendering)
   → agent runs, persists reply under the tenant-scoped session_key
```

**The routing primitive is `chat_id`**, not `scope_id`. Verified:
`build_session_key` (gateway/session.py) consumes `platform + chat_type +
chat_id + thread_id + participant_id` — `scope_id` is excluded entirely
(it's used for authz/wire-migration, not session routing). So tenant
isolation comes from `chat_id_template`, and the agent's per-tenant memory
isolation comes for free.

## Configuration

### Core adapter (config.yaml) — the V + consolidation

Mirrors `WebhookAdapter.routes` exactly (native Hermes idiom):

```yaml
platforms:
  msgraph_webhook:
    extra:
      # NEW: multi-route support (mirrors webhook platform). Each route is a
      # path → {client_state, chat_id_template, ...}. Backward compat: a bare
      # `client_state` string is treated as an anonymous default route.
      routes:
        aeo:
          client_state: <openssl-rand-hex-32>
          chat_id_template: "msgraph:aeo:{subscriptionId}"
          accepted_resources: ["me/messages", "me/events", "me/drive/root"]
          prompt: |
            📧 [aeo account] New {change_type} on {resource}
            {resource_data}
        pers:
          client_state: <openssl-rand-hex-32>
          chat_id_template: "msgraph:pers:{subscriptionId}"
          accepted_resources: ["me/messages", "me/events"]
          prompt: |
            📧 [pers account] New {change_type} on {resource}
            {resource_data}
      
      # Legacy single-tenant form still works (anonymous default route):
      # client_state: <secret>
      # chat_id_template defaults to "msgraph:{subscriptionId}"
```

### Plugin (`~/.hermes/plugins/msgraph-tenants/`) — the subscription lifecycle

Owns everything core shouldn't:

- Per-tenant config (`aeo`, `pers`): `home_account_id`, resources, renewal window
- Reads `~/.azure/msal_token_cache.json` (selected by `home_account_id` — load-bearing invariant)
- Creates Graph subscriptions per tenant per resource, points them at the core adapter's per-tenant path
- Proactive renewal (email/calendar ~3 days, OneDrive ~30 days)
- Consent flow (one-time per resource type, interactive)
- Fail-loud alerting: renewal failure → explicit error event into Hermes (not silence-detection); systemd-level healthcheck pages a real channel if the plugin process dies
- Dead-letter file per tenant for notifications that fail to POST

## Fail-loud hardening (mandatory)

1. **Dead-letter** per tenant — surface, never drop. Rotated, capped.
2. **Health endpoint** per listener — renewal status, last-notification time, dedup counter.
3. **Active alerting** — renewal failure / tunnel-down emits an explicit error event ("⚠ aeo tenant deaf — renewal failed 3×"). Never rely on the agent noticing silence.
4. **systemd healthcheck** — `OnFailure` or cron pages a real channel if the plugin dies.
5. **Dedup window** matches Graph retry window (~24h).

## Cloudflare Tunnel (the one external gate)

Microsoft's Graph servers must POST to a public URL. One tunnel, N hostnames:
`msg-aeo.ubug.aeyeops.io` → core port/path A, `msg-pers.ubug.aeyeops.io` →
port/path B. ubug stays behind the tailnet with no inbound port.

**Requires a Cloudflare token scoped for `Cloudflare Tunnel:Edit`** — the
existing DNS:Edit token doesn't cover tunnels. The user mints this at the
"wire it live" step. The bridge is buildable + testable locally with
synthetic Graph POSTs (`scripts/simulate-graph-webhook.py` in the
hermes-architecture skill) before the tunnel exists.

## Packaging (matches Hermes conventions)

- Core PR: single file change (`gateway/platforms/msgraph_webhook.py`), uv lock untouched (no new deps), tests in `tests/gateway/test_msgraph_webhook.py`
- Plugin: `~/.hermes/plugins/msgraph-tenants/` with `plugin.yaml` + `__init__.py` + `adapter.py`; own deps (`msal`) pinned `>=X,<next_major`

## Build order

1. **Core PR** — extend `msgraph_webhook`:
   - `routes:` map (mirrors `WebhookAdapter.routes`)
   - per-route `client_state`, `chat_id_template`, `accepted_resources`, `prompt`
   - backward compat: bare `client_state` → anonymous default route
   - register one path per route in `connect()`
   - tests: behavior-contract (route resolution, template rendering, backward compat, dedup)
2. **Local test** with `simulate-graph-webhook.py` against both routes
3. **Open upstream PR** — small, broadly-useful, reviewable
4. **Plugin scaffold** — `~/.hermes/plugins/msgraph-tenants/`
5. **Subscription lifecycle** — MSAL token-select by `home_account_id`, create/renew, consent
6. **Fail-loud hardening** — dead-letter, health, alerting, systemd healthcheck
7. **Cloudflare Tunnel** — provision with user's Tunnel:Edit token
8. **Wire `aeo` live** (already authed), verify end-to-end
9. **Add `pers`** — `az login --tenant <hotmail>` for its MSAL cache, enable second route

## Alternatives considered and rejected

- **Full multi-tenant in core** — rejected: bundles the niche subscription/MSAL concerns into core, high rejection risk, couples core to `~/.azure/`.
- **External bridge (standalone processes)** — rejected: more moving parts than reusing the core adapter's routes; the V→Q is cleaner inside one adapter.
- **`scope_id` for tenant routing** — rejected after walking the flow: `scope_id` is not in `build_session_key`; `chat_id` is the actual lever.
- **Self-describing text preamble without `chat_id` routing** — rejected: would merge tenant sessions into one history (no isolation), leaning on the agent to read a tag (probabilistic).
- **Heartbeat-by-absence** — rejected: replaced by explicit failure events + systemd healthcheck.
- **Two Hermes profiles** — rejected: two agents can't share a conversation; defeats the unified-assistant goal.

## Open gates (user decisions)

1. **Cloudflare Tunnel token** — user mints Tunnel:Edit-scope token at step 7.
2. **`pers` tenant auth** — device-code flow at step 9.
3. **Resource scope for v1** — recommendation: `me/messages` + `me/events` for `aeo` first, add OneDrive + `pers` after.

## Cross-references

- Hermes source: `gateway/platforms/msgraph_webhook.py` (adapter to extend),
  `gateway/platforms/webhook.py` (the `routes` pattern to mirror),
  `gateway/session.py::build_session_key` (the routing primitive — `chat_id`),
  `gateway/platforms/base.py::MessageEvent` (the inbound contract)
- Skill: `~/.pi/agent/skills/hermes-architecture/` — governance, footprint
  decisions, core-vs-plugin, external-credential-caches
- Graph: `aeyeops/graphify-out/` — gateway dependency map (god nodes confirm
  the extension routes through the stable surface)
