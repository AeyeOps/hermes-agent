# KB: Google Chat → Pub/Sub events (April 2026)

**Captured 2026-04-20.** Sources below, all dated 2026-04-xx (current).

## TL;DR
Workspace Events API is canonical for Chat → Pub/Sub in April 2026. Service-account (Chat app) auth requires `chat.app.*` scopes + Workspace admin pre-approval. Topic needs `roles/pubsub.publisher` granted to `chat-api-push@system.gserviceaccount.com`. User OAuth cannot create subscriptions on Chat spaces where an app is installed (empirically confirmed: 403 "Permission denied to perform the requested action on the specified resource").

## Mechanism: Workspace Events API (canonical)

- Service: `workspaceevents.googleapis.com` — must be enabled in the project that *authenticates* the call (this is the OAuth client's project for user-creds, or the SA's project for SA creds).
- Endpoint: `POST https://workspaceevents.googleapis.com/v1/subscriptions`

### Body shape
```json
{
  "targetResource": "//chat.googleapis.com/spaces/SPACE_ID",
  "eventTypes": [
    "google.workspace.chat.message.v1.created",
    "google.workspace.chat.message.v1.updated",
    "google.workspace.chat.message.v1.deleted",
    "google.workspace.chat.membership.v1.created",
    "google.workspace.chat.membership.v1.updated",
    "google.workspace.chat.membership.v1.deleted",
    "google.workspace.chat.reaction.v1.created"
  ],
  "notificationEndpoint": {
    "pubsubTopic": "projects/PROJECT_ID/topics/TOPIC"
  },
  "payloadOptions": {"includeResource": true}
}
```

Batch variants auto-generate: `google.workspace.chat.message.v1.batchCreated` etc.

## Auth matrix

| Auth mode | Scopes | Extras | Can create sub on Chat space? |
|---|---|---|---|
| User OAuth | `chat.spaces.readonly`, `chat.messages.readonly`, `chat.memberships.readonly` | Quota project must have WEV API enabled | **No** for spaces with an installed Chat app — gives 403 "resource doesn't exist" |
| SA (Chat app) | `chat.app.messages.readonly`, `chat.app.spaces.readonly`, `chat.app.memberships` | Workspace admin must pre-approve scopes for the app | **Yes**, once admin grant is in place |
| DWD | Same as user OAuth | Admin DWD binding required | Out of scope for hermes adapter per ADR-008 |

- Chat user scopes (`chat.messages`, `chat.memberships`, `chat.spaces`) do NOT support SA auth — error: *"Ensure the scopes you are using support app authentication with a service account"*.
- Chat app scopes (`chat.app.*`) WORK with SA but require admin grant — error before grant: *"The administrator must grant the app the required OAuth authorization scope for this action"*.

## Pub/Sub topic IAM

Grant `roles/pubsub.publisher` on the target topic to:
- **Standard Chat app**: `chat-api-push@system.gserviceaccount.com`
- **Chat add-on**: per-app SA shown on Chat API config page (differs from standard)
- **Meet / Drive / Calendar events**: other system SAs (not relevant to hermes today)

Permission propagation: "a few minutes" after granting.

## Admin grant (how to approve `chat.app.*` scopes for a Chat app)

**Two distinct steps, in order.**

### Step 1 — Developer declares scopes (Cloud Console)
- Google Cloud Console → APIs & Services → enable **Google Workspace Marketplace SDK** API if not already.
- Open the **Google Workspace Marketplace SDK → App Configuration** page.
- Under **OAuth scopes**: list every scope the app uses, including each `chat.app.*` scope.
- Save. Scopes are now *declared* but not yet *approved*.

### Step 2 — Workspace admin approves (admin.google.com)
Per `knowledge.workspace.google.com/admin/chat/set-up-app-authorization-for-chat` (last updated 2026-04-17):

- admin.google.com → Menu → **Apps** → **Google Workspace Marketplace apps** → **Apps list**.
- Click **Install app**.
- Search for the Chat app by name.
- Click **Admin install** → **Continue**.
- Review the data-access requirements (this is where the declared OAuth scopes appear for approval).
- Select **Everyone at your organization** → **Finish**.

Propagation: up to 24h, typically faster. Approval is per-app (one install covers all declared scopes for that app).

### If the app is already installed
Adding new `chat.app.*` scopes to an app that's already admin-installed may require **re-install** (uninstall + reinstall) to pick up the new scope set. Docs don't confirm, but that's the default behavior for Marketplace apps.

### Verification
No gcloud or API surface documented as of April 2026. Best verification is functional: retry the `subscriptions.create` call with the SA — if the scope error is gone, grant propagated.

## Pub/Sub message envelope (what the adapter receives)

Based on WEV + Chat docs:

- `attributes` map includes CloudEvents headers: `ce-type` (= event type string like `google.workspace.chat.message.v1.created`), `ce-source`, `ce-subject`, `ce-time`, `ce-specversion`.
- `data` field (base64-decoded, JSON) with `includeResource=true`:
  ```json
  {
    "message": {
      "name": "spaces/SPACE_ID/messages/MESSAGE_ID",
      "sender": {"name": "users/USER_ID", "type": "HUMAN"},
      "text": "hello",
      "createTime": "2026-04-20T18:11:28.905199Z",
      "thread": {"name": "spaces/.../threads/..."}
    }
  }
  ```
  **No** `"type": "MESSAGE"` top-level key. **No** top-level `"space"` or `"user"` siblings — you get just the Chat resource keyed by its type (`message`, `membership`, `reaction`, …).
- Without `includeResource`: payload contains only resource NAME + event metadata. Adapter would need a round-trip Chat API call to hydrate.

## Impact on `gateway/platforms/googlechat.py`

Current adapter (`_handle_chat_event`, lines 243-265) checks `payload.get("type")` for legacy-style `"MESSAGE"` / `"ADDED_TO_SPACE"` / `"REMOVED_FROM_SPACE"` / `"CARD_CLICKED"`. WEV sends `ce-type` as an attribute AND the event-type string is in `eventType` field of the data wrapper or in the Pub/Sub attributes.

**Adapter change needed when we go WEV**: route on `message.attributes["ce-type"]` (fall back to `payload.get("eventType")`), then extract the Chat resource from `payload["message"]` / `payload["membership"]` / etc.

Mapping table for the adapter:

| Legacy `type` | WEV `ce-type` |
|---|---|
| `MESSAGE` | `google.workspace.chat.message.v1.created` |
| `ADDED_TO_SPACE` | `google.workspace.chat.membership.v1.created` (filter: member is our app) |
| `REMOVED_FROM_SPACE` | `google.workspace.chat.membership.v1.deleted` (same filter) |
| `CARD_CLICKED` | Not in WEV Chat event list — **confirmed gap**. Card interactions may still flow through the legacy app-push or the HTTP endpoint; ADR-012 card-click synthesis may need rework under WEV. Flag for M4 planning. |

## Legacy Chat app-push (Chat API → Configuration → Connection settings)

- Doc fetched (2026-04-01) mentions "Cloud Pub/Sub topic name" as a configurable endpoint but offers no deprecation notice AND offers no operational detail (no publisher SA, no IAM, no payload shape).
- Interpretation: likely still functional for existing apps, but undocumented for new setups. Google appears to be steering everyone to WEV.
- **Not the recommended path for new hermes work in April 2026.**

## Concrete next actions for DEMO 1 on Mood Media Assistant

1. Verify `chat-api-push@system.gserviceaccount.com` has `roles/pubsub.publisher` on `projects/sa-mm-gchatbot/topics/chat-events`. (Briefing says yes; re-verify via Cloud Console → Pub/Sub → Topic → Permissions.)
2. Workspace admin: approve scopes `chat.app.messages.readonly`, `chat.app.spaces.readonly`, `chat.app.memberships` for the Mood Media Assistant Chat app.
3. From SA credentials with those scopes: `POST /v1/subscriptions` with the body shape above, targeting `spaces/AAQA2N6jyoA` + `spaces/vU8MIyAAAAE` (the bot's DM with Steve) — two subscriptions, or one per target resource.
4. Verify subscription shows `state: "ACTIVE"` via `subscriptions.list()` (SA needs read scope too).
5. Steve sends `hello` → Pub/Sub topic receives a message → gateway pulls it → adapter logs it.
6. Adapter almost certainly won't dispatch (payload shape mismatch with legacy parser). Inspect the raw payload in `agent.log`, then update `_handle_chat_event` to handle the WEV envelope (see mapping table above). Commit as a new M1 adapter commit: `fix(googlechat): handle Workspace Events API envelope alongside legacy`.

## Sources

- <https://developers.google.com/workspace/events/guides/events-chat> — Last updated 2026-04-09 UTC. Chat-specific event overview. Event type list came from here.
- <https://developers.google.com/workspace/events/guides/create-subscription> — Last updated 2026-04-03 UTC. Request body + auth matrix + topic IAM recipient.
- <https://developers.google.com/workspace/chat/events> — Last updated 2026-04-01 UTC. Mentions legacy Pub/Sub endpoint but vague on current status.

## Open items to resolve with further fetching

- Exact UI path to declare / admin-approve `chat.app.*` scopes on the Chat API app config in April 2026 (fetch `developers.google.com/workspace/chat/authenticate-authorize-chat-app`).
- CARD_CLICKED equivalent in WEV or lack thereof — check `developers.google.com/workspace/chat/receive-respond-interactions` for the 2026 card-interaction transport.
- Whether `payloadOptions.includeResource=true` has side effects on required scope (e.g., may require the `.readonly` equivalent of the event's resource).
