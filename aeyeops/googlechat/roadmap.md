# Google Chat adapter — post-DEMO-1 roadmap

Future work we've consciously deferred. Items land here when a follow-up
would otherwise disappear into session context. Each entry records the
motivating observation, the option space, and the decision gate — not a
commitment.

## Workflow: feasibility → implementation path → implementation plan

Capability-gap items (R2+) progress through three gates before any code
lands. Each stage must produce an artifact before the next begins; this
keeps us from reinventing adapter design mid-implementation.

1. **Stage 1 — Feasibility against Google Chat.** Pure research. Does
   the Chat API support the capability at the shape our adapter needs?
   Endpoints, scopes, quotas, rate limits, payload constraints. Output:
   a verdict (**feasible** / **feasible-with-caveats** / **infeasible**)
   plus the caveats. Sources cited by URL + fetch date, not memory.
2. **Stage 2 — Implementation path research.** Gated on Stage 1 landing
   with a green or yellow verdict. How do we wire this through our
   adapter without widening upstream diff? Which `BasePlatformAdapter`
   hooks? Which peer patterns do we mirror (slack, telegram, …)? Output:
   a design sketch with cross-references to the peer adapters and ADRs
   it would create or amend.
3. **Stage 3 — Implementation plan.** Gated on Stage 2. Concrete commit
   sequence (C##), milestone placement, tests, DEMO verification.

An R-item stays in this file across all three stages. When Stage 3 lands
and the implementation merges, the entry is kept with a `Resolved`
marker — so future sessions can see why it was considered and how it
closed, not just that it vanished.

R1 predates this workflow; it remains as a narrative decision-gate and
is not re-cast into the three-stage structure.

## R1 — Mention-free follow-ups in group spaces (Workspace Events API migration)

**Observed 2026-04-20.** In group/ROOM spaces, Google Chat only delivers the
following events to an app subscribed via Chat API interaction events over
Cloud Pub/Sub (ADR-007): `MESSAGE` triggered by explicit `@mention` of the
app or a slash command, `CARD_CLICKED`, `ADDED_TO_SPACE`,
`REMOVED_FROM_SPACE`, `APP_HOME`, `SUBMIT_FORM`, `APP_COMMAND`.

A user reply in a thread where the app has already posted **does not**
dispatch. Confirmed empirically with two data points (both in-thread,
both dropped): `followup1` at 23:11:29 in thread `N_6QmSfW8CA`, and
"I just wanted to say hi" at 23:16:10 in thread `udfI6keXHlI`. Agent log
shows zero inbound entries for either.

DM spaces are unaffected — every DM message dispatches (Chat's dispatch
contract always treats DM messages as app-addressed).

**Consequence for group-space UX.** Users must `@mention` the bot on every
turn, even inside an established thread. This is the opposite of the UX
Gemini-in-Chat projects (the UI treats Gemini as a conversation partner in
its own DM, not in shared rooms). The `fix(googlechat): reply into
existing thread via messageReplyOption` change in
[6b6d8e1b] correctly threads the bot's response under the user's
mention — but it cannot, and does not, broaden which user messages Chat
chooses to deliver to the app.

**Option — migrate to Workspace Events API.** The Workspace Events API
(distinct product from Chat API interaction events) exposes a
`google.workspace.chat.message.v1.created` event type that covers *all*
messages in subscribed spaces, not only `@mention`-gated ones. A bot
subscribed per-space via this API would see every message the app is
authorized to read, enabling mention-free thread conversation.

**What changes if we take that path:**

- Transport layer: add a second event source. Keep Pub/Sub interaction
  events for `CARD_CLICKED` / lifecycle, and add a Workspace Events
  subscriber for raw messages — the two streams aren't interchangeable.
- Subscription management: Workspace Events subscriptions are
  per-space, created via `workspaceevents.subscriptions.create`. The
  adapter needs a registry of subscribed spaces and lifecycle handling
  (create on `ADDED_TO_SPACE`, delete on `REMOVED_FROM_SPACE`). Runtime
  state, not config-file.
- Scopes: add `chat.messages.readonly` on the bot's service account;
  possibly `chat.memberships` for subscription management. Current
  `chat.bot` scope doesn't cover reading messages on a subscription.
- Envelope shape: Workspace Events wraps in a different JSON shape than
  the Chat App Event envelope currently unwrapped by
  `_handle_chat_event`. Adapter-side: a branch per envelope source, or
  a normalizer that projects both into the same `MessageEvent` shape.
- De-dup: an `@mention` in a subscribed space fires *both* a Chat App
  Event (via Pub/Sub) AND a Workspace Events message-created event —
  the adapter must not double-process. Candidate keys:
  `message.name` resource name, or a short-TTL set on `message_id`.
- Rate / quota: each subscribed space burns against Workspace Events
  subscription quotas (project-wide, not per-user). Needs an op
  strategy for large numbers of spaces.

**Decision gates:**

1. Is mention-free group-space conversation a dogfooding blocker? DEMO 1
   says no — the demo is DMs plus `@mention`-per-turn in `steve-test`.
2. Does the upstream `NousResearch/hermes-agent` have an opinion on
   which Chat transport to adopt? If the maintainer prefers one path,
   our cherry-pick is shaped by that.
3. Does the de-dup strategy survive both restart and cross-adapter
   replay? Regress-risking if we post responses twice because an
   envelope fell through both streams after a gateway crash.

If those three are answered, this becomes a concrete milestone — slotted
after the current in-flight work (C10–C14 format/lifecycle, then
C29–C34 streaming/rendering/thinking-ack per
[`streaming-spec.md`](./streaming-spec.md)). Until then it stays here.

**Links.** Chat interaction events list:
<https://developers.google.com/workspace/chat/events>. Workspace Events
API: <https://developers.google.com/workspace/events>.

## R2 — Outbound media senders (`send_image` / `send_video` / `send_voice` / `send_document`)

**Observed 2026-04-23.** Differential against the eight close-peer
adapters (slack, discord, telegram, matrix, whatsapp, mattermost,
signal, bluebubbles) shows every one of them implements the media-sender
family. `gateway/platforms/googlechat.py` has zero attachment handling.
ADR-012 confines outbound rendering to text + cardsV2, which covers
*linkified* media via card widgets but does not cover raw file upload
or inbound attachment download.

**Peer cross-reference (outbound surface):**

- slack: `send_image`, `send_video`, `send_voice`, `send_document`,
  `send_image_file` — uploads via `files.upload` and re-links.
- discord: full set plus `send_animation` — uses `discord.File`.
- telegram: full set plus `send_animation` — Bot API multipart.
- matrix: full set — uploads to homeserver media repo, references via
  `mxc://` URI, sends with `m.image` / `m.video` / `m.audio` / `m.file`
  event types.
- whatsapp: `send_image`, `send_video`, `send_document` — bridge-mediated.
- mattermost: full set — `files` endpoint upload then attach.
- signal: full set — signal-cli attachment parameter.
- bluebubbles: full set — `send_attachment` via bb-server endpoint.

**Why this matters.** Media-less is a visible PR-review gap. Chat UX
expectation is that a bot answering about an image can include the
image; a bot returning a generated file (PDF, chart, audio transcript)
should attach it, not link it.

### Stage 1 — Feasibility against Google Chat

Open research questions. Treat each as a verification task against
current Chat API docs (cite URL + fetch date):

- **Upload surface.** Does `spaces.messages.create` accept binary
  attachments in the request body, or must media be staged through
  `media.upload` (scope: `chat.bot` + `chat.import` or similar) and
  referenced by `attachment.attachmentDataRef.resourceName`?
- **Download surface.** Inbound messages with attachments expose
  `message.attachment[].downloadUri` / `thumbnailUri`. Can a
  service-account bot authenticate those URIs with its current
  `chat.bot` scope, or is `chat.messages.readonly` + elevated media
  scope needed?
- **Size limits.** Per-attachment max (suspect 200 MB historically;
  verify). Per-message aggregate max.
- **Rate limits.** Is media upload accounted against the per-space
  1 write/sec bucket, or a separate quota?
- **Bot-as-uploader.** Can the service account upload arbitrary binary
  (PNG generated at runtime, WAV from TTS) as the bot identity, or only
  on-behalf-of a user?
- **Drive-attachment path.** Chat also supports attaching a Drive file
  by `driveDataRef.driveFileId`. What scope grant does the service
  account need to attach a Drive file it owns? Is that a plausible
  fallback for cases where direct upload is restricted?

**Output:** `aeyeops/googlechat/roadmap/R2-stage1.md` — verdict plus
caveats. Cite sources. Note any ADR amendments implied (e.g., ADR-012
scope expansion).

**Status:** pending.

### Stage 2 — Implementation path research (gated on Stage 1)

Gated on Stage 1 landing with **feasible** or **feasible-with-caveats**.
If Stage 1 returns **infeasible**, R2 resolves there with the rationale.

Sketch only, pending Stage 1 verdict:

- Add `send_image` / `send_video` / `send_voice` / `send_document` on
  `GoogleChatAdapter`. Signature should match the existing peers — the
  base class already has `send_image_file`/`send_image` shapes.
- Inbound: extend `_handle_message_event` to hydrate
  `MessageEvent.attachments` (or equivalent) from
  `message.attachment[]`, including a fetch helper that streams the
  binary via `media.download` and caches through the existing media
  cache the gateway already provides for other adapters.
- Scope grant: if Stage 1 confirms the need, coordinate with the
  service-account grant in the GCP console and document in an ADR
  amendment.
- Upstream impact: whether this widens the scope of ADR-003
  ("adapter = I/O only") depends on how much media-handling logic the
  base class already owns vs how much is per-platform. Peer survey will
  answer this.

**Status:** blocked on Stage 1.

### Stage 3 — Implementation plan (gated on Stage 2)

Concrete commit sequence TBD. Probable shape: two-to-four commits — one
for outbound upload plumbing, one for outbound senders, one for inbound
hydration, one for tests + fixtures. Milestone placement: R2 as its own
post-M6 milestone (tentative numbering).

**Status:** blocked on Stage 2.

## R3 — Reactions (outbound add/remove + inbound reaction events)

**Observed 2026-04-23.** Four of eight peers (slack, discord, telegram,
matrix) carry full reaction round-trips. `gateway/platforms/googlechat.py`
has none.

**Peer cross-reference:**

- slack: `_add_reaction`, `_remove_reaction` + Events API reaction events
  (`reaction_added`, `reaction_removed`).
- discord: `_add_reaction`, `_remove_reaction` + `on_reaction_add` /
  `on_reaction_remove`.
- telegram: `_set_reaction` (Bot API `setMessageReaction`) + inbound
  reaction updates in the update stream.
- matrix: `_send_reaction`, `_redact_reaction`, `_on_reaction`
  (m.reaction event type).
- Not implemented: whatsapp, mattermost, signal, bluebubbles. So it's
  not universal — a fork could defend "not implemented" — but it's
  clearly a capability reviewers will notice.

**Why this matters.** Reactions are lightweight acknowledgement — a
bot thumbs-upping the user's message, or a user 👍-ing the bot's
response, has low cost but high UX value. In a corporate Chat
deployment the DM flow especially benefits.

### Stage 1 — Feasibility against Google Chat

Open research questions:

- **Outbound API.** Does Chat expose
  `spaces.messages.reactions.create` / `.delete`? (Believed yes, verify
  endpoint shape and scope requirement.)
- **Emoji schema.** Unicode codepoint, Chat-shortcode, or custom-emoji
  resource name? For custom emoji, what scope reads the space's emoji
  set?
- **Inbound delivery.** Reaction events — delivered over the existing
  Chat-API Pub/Sub interaction events channel, or only via Workspace
  Events API (`google.workspace.chat.message.v1.*`)? If the latter, R3
  entangles with R1 and should not proceed until R1 is resolved.
- **Dispatch gating in ROOMs.** For ROOMs, does the app receive
  reactions on messages it did NOT author? On its own messages? By
  analogy to the R1 `@mention`-dispatch gating, we cannot assume
  symmetric delivery.
- **Dispatch gating in DMs.** Presumably all reactions dispatch in DMs
  — verify.
- **De-dup.** A single user reaction may surface through Chat events
  AND Workspace Events if both are subscribed (see R1 dedup notes).
  Affects this item if R1 migration proceeds.

**Output:** `aeyeops/googlechat/roadmap/R3-stage1.md` — verdict plus
caveats. Explicitly record whether R3 can proceed before R1 or must
wait.

**Status:** pending.

### Stage 2 — Implementation path research (gated on Stage 1)

Gated. Sketch only:

- Outbound: `_add_reaction(channel_id, message_id, emoji)` /
  `_remove_reaction(...)` on `GoogleChatAdapter`. Route through
  `_ensure_chat_service` → `spaces.messages.reactions.create`.
- Inbound: new branch in `_handle_chat_event` for reaction event types,
  mapping to the agent-side contract the four peer-reaction adapters
  already use.
- If Stage 1 concludes Workspace Events API is required, R3 inherits
  R1's subscription-management burden — handle via the subscription
  registry R1 would introduce, not a second one.

**Status:** blocked on Stage 1.

### Stage 3 — Implementation plan (gated on Stage 2)

TBD.

**Status:** blocked on Stage 2.

## R4 — Thread-context parity assessment

**Observed 2026-04-23.** Differential shows googlechat.py at 3 thread
references (`messageReplyOption` wiring landed in `6b6d8e1b`) vs
slack at 85 and telegram at 154. The question this roadmap item tries
to answer is not "implement 85 thread references" — it's: **does Chat's
native thread model obviate the adapter-side state infrastructure
slack/telegram carry, or do we have concrete gaps?**

**Peer cross-reference (what the state-heavy peers carry):**

- slack: `_ThreadContextCache`, `_assistant_thread_key`,
  `_cache_assistant_thread_metadata`,
  `_dm_top_level_threads_as_sessions`, `_fetch_thread_context`,
  `_seed_assistant_thread_session` — full lifecycle for Slack's
  "assistant thread" concept plus DM top-level sessions.
- telegram: `_cache_dm_topic_from_message`, `_create_dm_topic`,
  `_persist_dm_topic_thread_id`, `_message_thread_id_for_send`,
  `_message_thread_id_for_typing`, `_metadata_thread_id`,
  `_reload_dm_topics_from_config` — DM-as-topic persistence across
  restarts, forum-topic integration.
- matrix: `_is_dm_room`, `_resolve_message_context` — moderate;
  relies on Matrix's room-as-thread model.
- whatsapp / mattermost / signal / bluebubbles: thin or absent.

The heavy peers do this because their thread IDs are ephemeral, require
derivation, or map to platform-specific concepts (forum topics,
assistant threads) that the agent doesn't natively model.

**Hypothesis to test.** Chat's `Message.thread.name` is a stable
resource name (e.g. `spaces/<space>/threads/<thread>`) — stable enough
to use directly as the agent's `MessageEvent.thread_id` without
additional adapter-side persistence. If confirmed, R4 resolves with an
ADR capturing the decision. If disconfirmed, we identify the specific
gaps and Stage 2 designs the minimum state layer to close them.

### Stage 1 — Feasibility against Google Chat

Open research questions:

- **Thread name stability.** Is `Message.thread.name` durable across
  our process restarts and across gateway restarts? (Suspect yes —
  it's a resource name, not a session handle.) Verify explicitly.
- **`messageReplyOption` reliability.** When we send with
  `messageReplyOption=REPLY_MESSAGE_OR_FAIL_IF_NOT_FOUND` and a
  stale thread name, what error surface does the API present? Do we
  need retry-with-new-thread semantics, or is the 6b6d8e1b wiring
  sufficient?
- **DM threading model.** A Chat DM — are all messages siblings at the
  space root, or does Chat create threads within DMs too? If root,
  thread context is space context (simple). If nested, we may need to
  distinguish DM top-level from in-thread.
- **Cross-session continuity.** When the agent is restarted mid-
  conversation, can we re-enter the user's thread by the resource name
  we persisted, or does Chat-side thread state expire?
- **ROOM with many concurrent bot threads.** A busy room where the bot
  is @mentioned in multiple parallel threads — does the current adapter
  correctly route each response back to the thread it came from? (Spot
  check through the existing integration fixtures may answer this.)
- **Concurrent replies.** If two users @mention the bot simultaneously
  in the same space but different threads, we rely on `thread_id` in
  MessageEvent to disambiguate. Is that plumbed today?

**Output:** `aeyeops/googlechat/roadmap/R4-stage1.md` — verdict with
one of:

1. **Native Chat threading is sufficient.** No parity work needed;
   capture decision in an ADR and resolve R4.
2. **Concrete gap(s) found.** Enumerate each gap. Each becomes a
   Stage-2 design question.

**Status:** pending.

### Stage 2 — Implementation path research (gated on Stage 1)

Gated. Only proceeds if Stage 1 identifies concrete gaps. The likely
shape of Stage 2 (if it happens) is a small persistence layer — either
(a) in-memory cache keyed by
`(space_id, user_id) → latest_thread_name`, invalidated on restart, or
(b) SQLite-backed if cross-restart continuity is needed. Either would
plug into `_handle_message_event` for inbound resolution and `send` for
outbound targeting.

**Status:** blocked on Stage 1.

### Stage 3 — Implementation plan (gated on Stage 2)

TBD. Possibly empty: if Stage 1 resolves with "native Chat threading is
sufficient", R4 closes at Stage 1 and Stage 3 never drafts.

**Status:** blocked on Stage 2.
