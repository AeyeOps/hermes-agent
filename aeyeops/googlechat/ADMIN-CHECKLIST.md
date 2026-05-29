# Google Chat adapter — admin approval checklist

Everything in `feat/googlechat-main` is code-complete and tested. The
pieces that require Workspace-admin action (R1 mention-free delivery,
R2b native attachment upload, R3 lifecycle reactions) are gated behind a
single env var — `GOOGLECHAT_ADMIN_APPROVED_SCOPES`. Until that's on,
the adapter runs exactly as before (chat.bot + @mention/DM + text I/O).

Hand this file to the `example.com` Workspace admin, then flip the
flag once they've completed the three actions below.

## What the admin has to do (one-time, ~15 minutes)

### 1. Add OAuth scopes to the Marketplace SDK app configuration

**Where:** <https://console.cloud.google.com/apis/api/appsmarket-component.googleapis.com/googleapps_sdk?project=example-gchatbot-project>

On the **App Configuration** tab, find the **OAuth Scopes** section and
add all four of these (one per line; the two `userinfo.*` scopes that are
already there should stay untouched):

```
https://www.googleapis.com/auth/chat.app.messages
https://www.googleapis.com/auth/chat.app.messages.readonly
https://www.googleapis.com/auth/chat.app.spaces.readonly
https://www.googleapis.com/auth/chat.app.memberships.readonly
```

**Why each one:**

| Scope | Unlocks |
|---|---|
| `chat.app.messages` | Outbound native attachment upload (R2b) and reaction create/delete (R3). |
| `chat.app.messages.readonly` | Workspace Events API subscription to message-created events (R1). |
| `chat.app.spaces.readonly` | Boot-time enumeration of the spaces the bot is a member of, needed to auto-subscribe all of them. |
| `chat.app.memberships.readonly` | Same enumeration path — Google requires it paired with spaces.readonly. |

Save. No verification step by Google — scopes land as "configured but not
yet authorized".

### 2. Authorize the app for the tenant

**Where:** <https://admin.google.com/ac/apps/gmail/marketplace/apps>

Find the **Example Chat Assistant** listing (project
`example-gchatbot-project`, service-account client ID `SERVICE_ACCOUNT_OAUTH_CLIENT_ID_PLACEHOLDER`).
Open it, click **Grant data access** / **Manage data access**, and
approve the four `chat.app.*` scopes listed above.

If the app isn't already installed at the tenant level (step 1 only
configures it in the Cloud console; step 2 is the Marketplace install),
install it first from the private-app listing — same screen.

### 3. Confirm the dogfood space exists and the bot is a member

**Where:** Google Chat client — open the space named **example-test-space**.

The space already exists (`SPACE_RESOURCE_PLACEHOLDER` — verified
2026-04-24). Confirm that **Example Chat Assistant** is in the member
list. If it was removed during earlier testing, re-add via `@Example Org
Assistant`.

## What to tell the operator once admin is done

Once the three steps above are complete, the operator flips the adapter
feature flag:

```bash
export GOOGLECHAT_ADMIN_APPROVED_SCOPES=true
# Optional — tune the reaction UX (default true):
export GOOGLECHAT_REACTIONS=true
# Required for R1 — the Workspace Events subscriptions deliver here:
export GOOGLECHAT_PUBSUB_TOPIC=projects/example-gchatbot-project/topics/chat-events
```

and restarts the adapter. On next boot:

- `_compute_scopes()` widens from `chat.bot` only to the full list.
- `_bootstrap_workspace_events()` lists the bot's group spaces and
  creates a Workspace Events subscription per space, 24h TTL.
- The renewal task starts; it patches TTL 1h before expiry.
- `send_image_file` / `send_voice` / `send_video` / `send_document`
  switch from URL-as-text fallback to native uploads.
- `on_processing_start` / `on_processing_complete` start posting
  👀 / ✅ / ❌ lifecycle reactions.

## How to verify each capability worked

All three probes use the service account key at
`~/.example-chat-assistant/key.json` and don't create lasting tenant
state. They run in sequence against the existing `example-test-space` space.

### R1 — Workspace Events subscription (mention-free delivery)

```bash
python aeyeops/googlechat/verify/r1_app_scope_probe.py
```

Expected — success:

```json
{
  "outcome": "created",
  "subscription_name": "subscriptions/…",
  "expire_time": "2026-04-25T…Z",
  "authority": "serviceAccountAuthority"
}
```

Outcome `admin_approval_required` means step 2 above hasn't completed
yet.

### R2b — Native attachment upload

No scratch probe is needed — the pytest suite exercises the happy path
against stubs, and the first real upload is the demo:

```bash
# From a working Hermes install with the adapter enabled and the flag on:
# DM or @mention the bot: "please send me a chart"
# The bot's tool pipeline routes through send_image_file / send_document.
# Verify Chat renders the image inline (click-to-full), and the Files tab
# lists the uploaded asset.
```

If the upload path hits a 403 here, re-check that
`chat.app.messages` is in the Admin Marketplace approved-scopes list.

### R3 — Lifecycle reactions

```bash
# DM the bot with any prompt that takes >2s (an agent task with tool calls).
# Expected:
#   1. 👀 appears on the user message within ~1s of processing start.
#   2. ✅ replaces 👀 on completion.
#   3. For a failure (bad prompt, tool error), ❌ replaces 👀.
#   4. Interrupting mid-stream removes 👀, no final reaction.
```

If `👀` never appears but `send` replies come through, toggle
`GOOGLECHAT_REACTIONS=false` and confirm it stops — that isolates
the gate from the API permission.

## Rollback

If any of R1/R2b/R3 misbehaves in dogfood, set
`GOOGLECHAT_ADMIN_APPROVED_SCOPES=false` and restart. The adapter
drops back to chat.bot / @mention path with zero admin action required
— the scopes remain authorized on Google's side, they're just not
requested at token-mint time.

## Why the whole stack is behind ONE flag

`chat.app.*` is a scope family. Workspace admin approval is one gesture
that covers everything in the family. From the adapter's perspective,
capabilities inside that family either all work or all 403, and there's
no useful middle state — enabling upload but not subscriptions, for
example, saves nothing because they share the same underlying
authorization. A single flag reflects that reality; feature-specific
flags would just offer ways to misconfigure.

Reactions has its own secondary flag (`GOOGLECHAT_REACTIONS`) because
some operators don't want emoji chrome on messages for aesthetic
reasons, independently of the auth question. That matches how the Slack,
Discord, Matrix, and Telegram adapters treat reactions too.
