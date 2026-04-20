# Design — Google Chat adapter

Architecture design for the Google Chat platform adapter.

## Target surface

**Google Workspace first.** The adapter targets Google Chat within Google
Workspace (Business / Enterprise tenants). Consumer-mode Google Chat is
out of scope for the initial implementation. Every decision below
assumes Workspace primitives: GCP project, service account identity,
Workspace admin-installed bot.

If consumer-mode support later turns out to be a trivially compatible
delta, we broaden the adapter transparently. Otherwise it gets its own
module.

## Fixed surface

The adapter owns four things and nothing else:

1. **Connect** — establish the chosen transport to Google Chat.
2. **Receive** — convert inbound events to `MessageEvent`.
3. **Dispatch** — call `self.handle_message(event)`.
4. **Send** — outbound text/media via base-class primitives.

Everything else (tools, hooks, skills, cron, redaction, media caching)
comes from the base class and the plugin system, inherited for free.

## Conformance constraints (givens, not decisions)

Requirements the adapter must satisfy, inherited from
`gateway/platforms/ADDING_A_PLATFORM.md` § "Key patterns to follow" and
the base-class contract. No real alternative exists for any of them.

### Required method signatures

- `__init__(self, config)` — calls `super().__init__(config, Platform.GOOGLECHAT)`.
- `connect() -> bool`, `disconnect()`, `send(chat_id, text, …) -> SendResult`,
  `get_chat_info(chat_id) -> dict`, `send_typing(chat_id)`,
  `send_image(chat_id, image_url, caption) -> SendResult`.
- Module-level `check_googlechat_requirements() -> bool` — called by the
  factory before instantiation.

### Integration patterns

- Use `self.build_source(…)` to construct `SessionSource` objects.
- Dispatch inbound events via `self.handle_message(event)`.
- Use `MessageEvent`, `MessageType`, `SendResult` from `base`.
- Use `cache_image_from_bytes`, `cache_audio_from_bytes`,
  `cache_document_from_bytes` for inbound attachments — stash local
  paths before surfacing, so expiring platform URLs don't break tool
  access later.

### Streaming / reliability

- Reconnect with exponential backoff + jitter on transient failures
  (`ADDING_A_PLATFORM.md:52`). Reference constants:
  `gateway/platforms/slack.py`, `mattermost.py`, `signal.py`.
- Deduplicate inbound events via `helpers.MessageDeduplicator`
  (`gateway/platforms/helpers.py`) keyed on event IDs — reconnect can
  redeliver.

### Security / governance

- **Token locks**: `acquire_scoped_lock()` from `gateway.status` in
  `connect()`/`start()`; `release_scoped_lock()` in `disconnect()`/`stop()`.
  Canonical pattern: `gateway/platforms/telegram.py` (per `AGENTS.md:478–481`).
- **Redaction**: if Google Chat IDs are sensitive, add a regex to
  `agent/redact.py` so they're masked in all log output, not just the
  adapter's. (Checklist item 14.)
- **Self-message filter**: drop events authored by our bot identity to
  prevent reply loops.
- **Sync/echo filter**: drop platform-level echo messages if they exist.

### UX

- **Typing indicator keepalive**: wrap long operations in a task that
  refreshes typing every ~2s; always `stop_typing` in a `finally` block
  (base-class pattern).
- **`MAX_MESSAGE_LENGTH`**: set if Google Chat enforces per-message size
  limits.

## Hierarchy

Google Chat's conversation containers, as they appear on the Chat API
and in the data we capture for session and cron affinity:

```
Space ─────────────┐
   │               │
   ├── DIRECT_MESSAGE (DM)        → flat; no threads
   ├── GROUP_CHAT (unnamed group) → flat; no threads
   └── SPACE (named room)         → threading on or off
           │
           └── Thread (only in threaded rooms)
                     │
                     └── Message
```

Resource names:
- Space: `spaces/AAA…`
- Thread: `spaces/AAA…/threads/BBB…` (only in threaded rooms)
- Message: `spaces/AAA…/messages/CCC…` or `.../threads/BBB…/messages/CCC…`

For any persistent affinity we capture at event time — session keying,
cron scheduling, `send_message` targeting — the unit is the tuple
`(space_id, thread_id_or_None)`. A DM or flat room → `thread_id = None`.
A threaded room → `thread_id` set when the event arrived inside a
thread. This keeps mechanisms like cron delivery posting into the same
thread they were born in, rather than surfacing unrelated top-of-space
messages.

## Design decisions

### Transport

**Pub/Sub pull subscription.** The adapter connects outbound to a GCP
project, pulls events from a subscription, acknowledges them, and
responds asynchronously via the Chat API. No inbound HTTPS required —
works behind NAT/firewall, parallel to our Slack adapter's Socket Mode
choice (`gateway/platforms/slack.py:183`).

Required GCP resources: project with billing, Chat API + Pub/Sub API
enabled, one topic (Chat publishes here), one pull subscription (we
consume here), service account with `Pub/Sub Subscriber` role on the
subscription and `chat.bot` OAuth scope for posting replies.

Python dependency: `google-cloud-pubsub` — enters
`check_googlechat_requirements()`.

Demultiplexing: the subscription carries events for every space the
bot is installed in; the adapter routes by `space.name` on each event.

**HTTP endpoint transport is permanently out of scope**, not just
deferred. Runtime inspection of the fork's deployment host
(`<host>`) confirms every messaging adapter in use today (Telegram
long-poll, WhatsApp bridge → Meta, Slack Socket Mode when enabled,
Discord Gateway, etc.) receives messages over an outbound long-lived
connection. No adapter depends on inbound HTTPS from the internet.
Adding HTTP transport for Google Chat would introduce an
inbound-ingress pattern unique to this one adapter, for no capability
the Pub/Sub path doesn't already deliver.

### Authentication

**Bot-identity service account.** One service account handles both
Google Chat and Pub/Sub auth, with:

- OAuth scope: `https://www.googleapis.com/auth/chat.bot` for posting.
- IAM role `roles/pubsub.subscriber` on the subscription for consuming
  events.

Operators also grant `roles/pubsub.publisher` on the topic to
`chat-api-push@system.gserviceaccount.com` as a one-time setup so
Google Chat can publish into our topic.

Credential delivery prefers Application Default Credentials (ADC) and
falls back to an explicit service-account JSON key path via env var.

Domain-wide delegation and per-user OAuth 2.0 are out of scope for the
initial adapter — the bot posts as the bot, matching Slack/Telegram
behavior. If a future feature needs user-scoped Drive/Gmail/Calendar
reads, that code path gets its own auth flow, not a whole-adapter
retrofit.

### Event → `MessageEvent` mapping

Google Chat event types: `MESSAGE`, `ADDED_TO_SPACE`,
`REMOVED_FROM_SPACE`, `CARD_CLICKED`.

The reference adapters (`gateway/platforms/slack.py:950`,
`gateway/platforms/discord.py:2941`) both use a single unified
inbound-event handler with if/elif dispatch. Our adapter follows the
same shape: one entry point, if/elif on event type. `MessageEvent` is
produced for user-originated messages and for card-click interactions
routed back to the agent; space-lifecycle events are handled as
in-adapter side effects or dropped.

Our mapping:

- `MESSAGE` → `MessageEvent(text, chat_id=space_id, …)`.
- `ADDED_TO_SPACE` → in-adapter side effect. No greeting by default;
  cache space metadata only if we find a use for it. No `MessageEvent`.
- `REMOVED_FROM_SPACE` → dropped or minimal cleanup. No `MessageEvent`.
- `CARD_CLICKED` → synthesized `MessageEvent(MessageType.TEXT)` whose
  `text` folds the click's `action.actionMethodName`,
  `action.parameters`, and any `common.formInputs` into a
  natural-language summary, then dispatched through
  `self.handle_message(event)` like any user message. Correlation rides
  on Google Chat's native parameter round-trip — the adapter keeps no
  pending-card state. The agent's session history carries the "I sent
  this card" context from when the card was composed. Reference adapter
  alternatives — Slack Block Kit buttons (`slack.py:208-215, 1302`) and
  Discord button views (`discord.py:3305-3689`) — both resolve locally
  without reaching the agent; that pattern is appropriate for
  predefined approval flows and is not what Google Chat cards here do.

Supporting filters, mirroring both references:

- **Self-message filter**: drop events where the sender matches our
  bot identity. Slack compares `event.user` to `self._bot_user_id`
  (`slack.py:974-976`); Discord compares `message.author` to
  `self._client.user` (`discord.py:654-655`).
- **Dedup key**: `message.name` (Google Chat's globally unique message
  resource name). Slack uses `event.ts`, Discord uses `str(message.id)` —
  the principle is "the platform's own unique ID for the message,"
  fed to `helpers.MessageDeduplicator`.

### Space/thread → `SessionSource` mapping

Google Chat's space/thread model maps to `SessionSource`
(`gateway/session.py:65-139`) following the Slack pattern:

- `chat_id` = space resource name (e.g., `spaces/AAA…`)
- `thread_id` = thread resource name when in a thread; `None` otherwise
- `chat_type` = `"dm"` for `DIRECT_MESSAGE` spaces, `"group"` for rooms
- `user_id` = sender resource name (e.g., `users/123…`)

Slack (`gateway/platforms/slack.py:1179-1186`) puts the channel in
`chat_id` and the thread in `thread_id` — both enter the session key at
`gateway/session.py:514`, so threads are session-distinct without
pulling thread info into the chat identity. Discord takes the opposite
approach (`gateway/platforms/discord.py:2427`) and uses the thread ID
itself as `chat_id`. Google Chat threads are branches within a parent
space (like Slack), not standalone chats (unlike Discord), so the Slack
pattern fits.

Default session-scope behavior that falls out:

- Group-room threads → shared conversation across users in the thread
  (same as Slack defaults).
- Top-level room messages → shared space-level session.
- DMs → per-user session via `chat_type="dm"`.

No platform-specific fields needed on `SessionSource`; `thread_id` is
already a cross-platform field. Checklist item 5 (session source) is a
no-op for Google Chat.

Mention gating is largely unnecessary in the adapter: Google Chat's
event model delivers bot messages only in spaces the bot is added to,
and in rooms it only delivers messages where the bot is mentioned
(with some DM and thread-followup exceptions). Unlike Slack, where the
adapter filters messages based on `_mentioned_threads` state
(`slack.py:1024-1070`), the platform itself enforces most of the
filtering for us. Adapter still drops self-messages and echo events.

### Message format

Google Chat supports plain text, its own markdown dialect (`*bold*`,
`_italic_`, `~strike~`, backticks, triple-backticks; no tables, no GFM
parity), and Card v2 (rich widgets). The agent emits GFM-style
markdown, so passing through as plain text leaves literal `**bold**`
visible in the rendered message.

Adapter translates GFM → Google Chat markdown in a `format_message()`
override — the same pattern as Slack (`slack.py:437-545`, GFM→mrkdwn)
and Telegram (`telegram.py:1988-2161`, GFM→MarkdownV2). Base class
provides a no-op stub at `base.py:2201-2210`.

Key translations: `**bold**`→`*bold*`, `*italic*`→`_italic_`,
`~~strike~~`→`~strike~`, headers→bold, GFM tables→wrapped in fenced
code blocks (follow `telegram.py:144-196`), links→`<url|text>`. Fenced
code blocks pass through unchanged.

Chunking uses the base-class `truncate_message()` helper
(`base.py:2212-2342`) against a `MAX_MESSAGE_LENGTH` set to Google
Chat's per-message text limit (confirmed during transport research).

**Card v2 is in scope for the initial adapter** (requirements UC-36
and UC-37). Agents emit cards by calling a platform-aware
`send_chat_card` tool registered only when `googlechat` is in the
active platforms list. The tool accepts a narrower pydantic
`CardSpec` — not raw Card v2 JSON — so the agent's authorship surface
is bounded and invalid widgets fail at schema validation rather than
at the Chat API edge. The adapter translates `CardSpec` → Card v2
JSON at send time, posts via `POST spaces/<id>/messages`, and returns
the resulting message resource name so downstream click events can
correlate back to their originating card.

Conceptual tool signature:

```python
async def send_chat_card(
    space_id: str,
    thread_id: str | None,
    card: CardSpec,
) -> dict  # {"message_name": "spaces/.../messages/...", "success": bool}
```

Widget set for the initial adapter:

| Widget | Purpose |
|--------|---------|
| `textParagraph` | card text content; reuses the GFM → Chat-markdown translator above |
| `image` | static images |
| `divider` | structural separation |
| `button` / `buttonList` | immediate-click actions |
| `selectionInput` · `RADIO_BUTTON` | pick one of N |
| `selectionInput` · `CHECK_BOX` | pick zero-to-N of N |
| submit `button` | required trailing widget when selections are present; Google Chat fires no event until submit, and `CardSpec` validation enforces the pairing |
| `card.header` | title + optional subtitle |

Deferred as follow-ups if dogfooding shows the need: `textInput`,
`dateTimePicker`, `decoratedText` / `grid` / `columns`, modal dialogs
(`action_response.type = DIALOG`), `selectionInput` · `DROPDOWN` /
`SWITCH`, and cron-delivered cards (cron stays text-only through
`_send_googlechat`).

Text replies continue to use the GFM-translation path above; cards
are additive, not a replacement. The base-class `send()`,
`send_image()`, and `send_typing()` methods are untouched — the card
path is a separate tool, not an override of existing send primitives.

### Memory and context hierarchy

Hermes layers memory and context across several scopes. The adapter's
job is to surface the Google-Chat-side primitives — space, thread,
user — so every layer can key correctly. Most of this is inherited
from the base class; the adapter just has to set the right fields on
`SessionSource` / `MessageEvent`.

Assumes the default Hermes setup (SQLite `SessionDB` for conversation
history plus whatever memory plugins are installed, such as Honcho).
Third-party memory plugins that install later will tie to the same
primitives — so as long as the adapter populates space, thread, and
user correctly, plugins work without Google-Chat-specific code.

**Google Chat primitives the adapter captures on every event:**

| Primitive | Value | Source |
|-----------|-------|--------|
| `platform` | `googlechat` | `Platform` enum |
| `space_id` | `spaces/AAA…` | event `space.name` |
| `thread_id` | `spaces/AAA…/threads/BBB…` or `None` | event `message.thread.name` |
| `user_id` | `users/123…` | event `message.sender.name` |
| `chat_type` | `"dm"` or `"group"` | event `space.type` |

**Context layers, from widest to narrowest:**

| Layer | Keyed by | Scope |
|-------|----------|-------|
| Global system prompt | — | everything, every adapter |
| `PLATFORM_HINTS["googlechat"]` | platform | all Google Chat turns, any space |
| `channel_prompts` (per-space persona) | `chat_id` = space | one space; threads inherit from parent space |
| User memory (Honcho, etc.) | `user_id` | cross-session, cross-space, per-user |
| Session history (`SessionDB`) | `session_key` derived from `(platform, chat_type, space, thread, user)` | single conversation |
| Turn | ephemeral | single agent turn + its tool calls |

**Session-key cases that fall out:**

- DM: `googlechat:dm:<space>:<user>` — per-user DM history.
- Threaded room, message in a thread:
  `googlechat:group:<space>:<thread>` — shared across users in the
  thread.
- Threaded room, top-level message (no thread):
  `googlechat:group:<space>` — shared across users in the space.
- Unnamed group chat (no threading): same as top-level room.

**Cron is an intentional exception.** A cron-fired agent turn runs in
a **fresh session** (`cron/scheduler.py:740`, session id
`cron_<jobid>_<timestamp>`), not a continuation of the user session
that created it. Implication:

- **Session history layer** resets — the cron agent does not see the
  prior conversation unless a tool explicitly resumes a session id.
- **Space / thread persona layer** still applies, because `channel_prompts`
  is keyed on `space_id` which the cron captured at creation.
- **User memory layer** (Honcho etc.) still applies, because
  `user_id` was captured at cron creation. The cron agent knows "who
  asked" even without history.
- **Delivery affinity** (where the message lands) is the captured
  `(space_id, thread_id)` tuple — orthogonal to session context.

In practice, a cron update arrives as "here's what you asked for, in
the same thread you asked from" — but the agent composing it is
reasoning from user memory + channel persona + the cron's job
description, not from the original conversation's message log.

**Privacy boundary implied by the hierarchy.** User memory is keyed by
`user_id`, not by session or space. If the same user is in a DM and a
group thread with the bot, Honcho's per-user facts apply in both —
even though the DM is private and the thread is shared. Adapter-side
there's nothing to enforce about this; it's a memory-plugin concern.
Worth flagging so operators know to configure memory plugins with
privacy in mind if that's a concern for their deployment.

### Cron delivery

Cron jobs created inside a conversation retain their conversational
affinity. Cron's delivery path already carries thread identity as a
first-class concept: `cron/scheduler.py:352` reads
`target.get("thread_id")` at delivery time, logs a warning at
`cron/scheduler.py:356` when an origin had a `thread_id` but the
target lost it, and `tools/send_message_tool.py::_send_to_platform`
(line 392) accepts `thread_id` as a parameter routed to
platform-specific senders.

For Google Chat this means the adapter just needs to implement the
matching sender signature:

```python
async def _send_googlechat(
    creds_or_config, chat_id: str, message: str,
    thread_id: str | None = None, media_files=None,
) -> SendResult: ...
```

Creation captures `(space_id, thread_id_or_None)` from the
`SessionSource` of the triggering message and stashes them in the
cron target. Delivery posts via `POST spaces/<id>/messages` with
`thread.name` set to the captured thread when present. A cron
created inside a thread posts back in that thread; a cron created in
a DM or flat room posts at the top of the space.

Out-of-process property worth noting: Google Chat's outbound path is
the REST Chat API for both adapter replies and cron delivery. Unlike
Slack (where in-process replies can use Socket Mode's `say()` but
out-of-process cron must use the Web API), Google Chat has no split —
the same `_send_googlechat` standalone works whether the adapter is
running or not, given credentials plus the tuple above.

## Flows

Sequence diagrams for the two paths that shape the adapter.

### Inbound user message via Pub/Sub

```
User (Chat)   Google Chat    Pub/Sub topic      Adapter          Chat API
    |  msg        |                                |                |
    |------------>|  publish                       |                |
    |             |------------->  [event queued]  |                |
    |             |                  pull          |                |
    |             |                  <-------------|                |
    |             |                  ack --------->|                |
    |             |                                | MessageEvent → |
    |             |                                | agent turn     |
    |             |                                |                |
    |             |                                | POST spaces/../messages
    |             |                                |--------------->|
    |             |                                |<---------------|
    |  reply      |                                                 |
    |<------------|                                                 |
```

### Cron-initiated delivery

```
Cron scheduler    Agent (headless)   _send_googlechat    Google Chat API
     |                  |                  |                   |
     | wake @ schedule  |                  |                   |
     |----------------->|                  |                   |
     |                  | run turn, produce update text        |
     |                  |                  |                   |
     |  _deliver_result: platform_map[googlechat] = Platform.GOOGLECHAT
     |                  |                  |                   |
     |------------------------------------>|                   |
     |                                     | POST spaces/<id>/messages
     |                                     |   (thread.name set if
     |                                     |    cron captured thread_id)
     |                                     |------------------>|
     |                                     |   message.name    |
     |                                     |<------------------|
```

Both paths end in `POST spaces/<space>/messages`. Cron is just a
different trigger, not a different outbound code path.

## Non-goals (from `CLAUDE.md`)

- No changes to upstream provider, TUI, skill, or cron code.
- No refactors of other platform adapters "while we're here."
- No release notes — this is a cherry-pick target, not a release.

## Related

- Upstream checklist: `gateway/platforms/ADDING_A_PLATFORM.md`
- Plan: `plan.md`
