# ADR-013: Auth model for post-DEMO Google Chat capabilities

**Status**: Proposed
**Date**: 2026-04-24

## Context

ADR-008 intentionally chose the simplest initial adapter auth model:
service-account app authentication with `chat.bot`. That is enough for
DEMO-1 text I/O, but the post-DEMO roadmap asks for capabilities that are
not all covered by `chat.bot`:

- Native outbound file upload uses `media.upload`, whose reference lists
  user-auth message scopes and not `chat.bot`.
- Outbound reactions use the `spaces.messages.reactions` resource, whose
  create/delete methods list user-auth reaction/message scopes and not
  `chat.bot`.
- Mention-free group-space delivery requires Workspace Events API
  subscriptions. Public Workspace Events docs currently describe Chat app
  authentication with administrator approval for space subscriptions, and
  the scope guide still marks that path as Developer Preview.

Checked sources on 2026-04-24:

- Google Chat `media.upload`: <https://developers.google.com/workspace/chat/api/reference/rest/v1/media/upload>
- Google Chat reactions create: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.reactions/create>
- Google Chat app auth: <https://developers.google.com/workspace/chat/authenticate-authorize-chat-app>
- Workspace Events Chat subscriptions: <https://developers.google.com/workspace/events/guides/events-chat>
- Workspace Events scopes: <https://developers.google.com/workspace/events/guides/auth>

## Decision

Prefer administrator-approved `chat.app.*` app-auth scopes where Google
supports them, because that keeps the bot acting as the bot and avoids
domain-wide delegation. Domain-wide delegation remains the fallback if a
roadmap item is only available through user authentication.

This ADR stays **Proposed** until the live Workspace verifies the exact
scope behavior. Do not widen `CHAT_API_SCOPES` from `chat.bot` based only
on this document.

## Required live verification

Before accepting this ADR:

1. Verify whether `chat.app.messages` or another app-auth scope authorizes
   `POST /upload/v1/{parent}/attachments:upload`.
2. Verify whether any app-auth scope authorizes reaction create/delete.
3. Create a Workspace Events subscription with app auth,
   `includeResource=true`, and read the actual expiration time.

These checks require the `sa-mm-gchatbot` project, the service-account key,
and Workspace administrator approval for the candidate app scopes.

## Verification log

### 2026-04-24 — R1 app-auth read-only probe

Probe:

```bash
python aeyeops/googlechat/verify/r1_app_scope_probe.py
```

Candidate scope:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

Target:

```text
space: spaces/AAQA2N6jyoA
topic: projects/sa-mm-gchatbot/topics/chat-events
event: google.workspace.chat.message.v1.created
payloadOptions.includeResource: true
```

Result: `403 PERMISSION_DENIED`.

Google returned:

```text
The administrator must grant the app the required OAuth authorization scope for this action.
```

Interpretation: this is not a DWD requirement yet. It is the expected
admin-approval gate for `chat.app.*` app-auth scopes. The next least-
privilege action is to grant only `chat.app.messages.readonly` for the
Mood Media Assistant Chat app, then rerun the same probe.

Result file:

```text
aeyeops/googlechat/verify/results/r1-app-scope-20260424-010016.json
```

## Consequences

- M7 work remains unblocked because inbound media download and native Chat
  threading still work with `chat.bot`.
- R2b, R3, and R1 stay gated until this ADR is accepted or explicitly
  revised toward DWD.
- ADR-008 remains valid for the initial adapter. This ADR narrows its
  follow-up path: use admin-approved app scopes first, DWD only when app
  scopes cannot satisfy the capability.
