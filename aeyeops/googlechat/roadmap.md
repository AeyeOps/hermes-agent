# Google Chat adapter — post-DEMO-1 roadmap

Future work we've consciously deferred. Items land here when a follow-up
would otherwise disappear into session context. Each entry records the
motivating observation, the option space, and the decision gate — not a
commitment.

## 2026-04-24 Stage 1 status

The R1-R4 feasibility pass landed as separate artifacts under
`aeyeops/googlechat/roadmap/`.

| Item | Status | Artifact |
|---|---|---|
| R1 mention-free group-space delivery | Feasible with caveats; gated on ADR-013 live auth verification | [`roadmap/R1-stage1.md`](./roadmap/R1-stage1.md) |
| R2a inbound media hydration | Feasible under current `chat.bot`; M7 implementation path active | [`roadmap/R2-stage1.md`](./roadmap/R2-stage1.md) |
| R2b outbound native upload | Feasible with caveats; gated on ADR-013 live auth verification | [`roadmap/R2-stage1.md`](./roadmap/R2-stage1.md) |
| R3 lifecycle reactions | Feasible with caveats; gated on ADR-013 live auth verification | [`roadmap/R3-stage1.md`](./roadmap/R3-stage1.md) |
| R4 thread-context parity | Resolved: native Chat threading is sufficient; no adapter cache | [`roadmap/R4-stage1.md`](./roadmap/R4-stage1.md), [`adr/014-native-chat-threading-sufficient.md`](./adr/014-native-chat-threading-sufficient.md) |

ADR-013 is **Proposed**, not accepted. Public docs checked on
2026-04-24 still require live verification for the exact app-auth scope
coverage and Workspace Events subscription TTL in the target tenant.

## 2026-04-25 unblocked implementation status

The non-admin-gated next work from
`docs/roadmap/googlechat-unblocked-next-work-spec.md` is implemented locally
pending live Google Chat verification:

- Google Chat platform prompting now describes Chat markup, media expectations,
  and the `send_chat_card` card path.
- `send_chat_card` is scoped to the `hermes-googlechat` toolset and posts a
  constrained Card v2 payload through the existing Chat REST client.
- `CARD_CLICKED` now synthesizes a normal text `MessageEvent` with action,
  parameters, and form selections; no pending-card registry was added.
- Cron delivery, `send_message`, redaction, CLI status, and gateway setup now
  include Google Chat `spaces/...` targets and service-account/home-channel
  visibility.
- Streaming is text-first: placeholder creation hands off to progressive
  `spaces.messages.patch(updateMask=text)` edits with per-space pacing.

Still not done here: live tenant smoke verification for card rendering, click
delivery, cron delivery, and progressive edits. `cardsV2` streaming finalization
remains a later optional sub-scope.

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

### Gate criteria

A stage is cleared when its artifact meets these bars; a vague "seems
fine" is not a pass.

- **Stage 1 clears (green)** when the artifact contains: (1) at least
  one working Chat API endpoint identified by URL and HTTP method,
  (2) the required OAuth scope(s) documented and checked against the
  service-account's current grant, (3) at least one peer-adapter
  parallel (what does telegram / slack / discord / matrix do at the
  same layer?), (4) size / quota / rate-limit facts sourced from
  documentation with fetch date.
- **Stage 1 clears with caveats (yellow)** when the above bars are met
  but at least one significant constraint surfaces (e.g., elevated
  scope needed, or an R1-style transport-layer dependency). Stage 2
  proceeds but carries the caveat forward.
- **Stage 1 closes the item** (**no** Stage 2) when research surfaces
  either that the capability is infeasible under our auth / transport
  / scope envelope, or that it's feasible but the complexity cost is
  judged unworth the UX gain. Close with an ADR recording the
  decision.
- **Stage 2 clears** when the artifact identifies the concrete
  base-class hooks to override, the peer adapter used as a design
  template, the adapter-side state (if any), and any ADR amendments
  implied. Hand-waving cross-references ("like slack does") don't
  pass — cite the file + line range.
- **Stage 3 clears** when the artifact lists the commit sequence
  (C##), each commit's scope and test plan, the milestone placement,
  and how DEMO verification exercises it.

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
signal, bluebubbles) shows every one of them overrides the
media-sender family for native upload. `gateway/platforms/googlechat.py`
overrides none of them — it inherits the base class's URL-as-text
fallback (`gateway/platforms/base.py:1147-1164` for `send_image`; the
other senders share the pattern). So outbound media "works" in the
degenerate sense — the URL goes through as plain text — but there is
no native upload, no image widget promotion, no Drive-attachment path.

On the inbound side, `_handle_message_event` at
`gateway/platforms/googlechat.py:279-327` constructs a
`MessageEvent(message_type=TEXT)` from `message.text` only; it never
populates `media_urls` / `media_types` (the `MessageEvent` fields for
media, per `base.py:710-711`) from `message.attachment[]`. Inbound
attachments are silently dropped today.

ADR-012 scoped Card v2 widgets (including `image`) but deliberately did
not touch the base-class media senders — "Existing base-class `send()`,
`send_image()`, `send_typing()` methods stay as they are"
(`adr/012-card-v2-interface.md:102-104`). R2 is the follow-up that
promotes those inherited fallbacks to native Chat upload.

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

**Outbound (bot → user):**

- **Upload surface.** Does `spaces.messages.create` accept binary
  attachments in the request body, or must media be staged through
  `media.upload` (scope: `chat.bot` + `chat.import` or similar) and
  referenced by `attachment.attachmentDataRef.resourceName`?
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

**Inbound (user → bot):**

- **Envelope shape.** For a Chat message carrying an image, audio, or
  file attachment, what does `message.attachment[]` look like in the
  App Event envelope the adapter already receives? Capture a fixture
  (same approach as `fixtures/` for message events).
- **Download surface.** Inbound messages with attachments expose
  `downloadUri` / `thumbnailUri`. Can a service-account bot authenticate
  those URIs with its current `chat.bot` scope, or is
  `chat.messages.readonly` + elevated media scope needed?
- **Gateway media-cache compatibility.** Hermes already caches
  downloaded media for vision-tool access (`media_urls` on
  `MessageEvent`). Does the existing cache accept downloaded Chat
  binaries, or do we need an adapter-side write-through? Cross-reference
  how telegram / discord adapters wire their downloads into the cache.
- **MessageEvent type selection.** If a message carries only an
  attachment (no text), should we still emit `MessageType.TEXT` with a
  placeholder text, or switch to `MessageType.PHOTO` /
  `MessageType.DOCUMENT` / etc.? The photo-burst interrupt-queueing
  path (`base.py:1716-1719`) behaves differently per type.

**Output:** `aeyeops/googlechat/roadmap/R2-stage1.md` — verdict plus
caveats. Cite sources. Note any ADR amendments implied (e.g., ADR-012
scope expansion).

**Status:** pending.

### Stage 2 — Implementation path research (gated on Stage 1)

Gated on Stage 1 landing with **feasible** or **feasible-with-caveats**.
If Stage 1 returns **infeasible**, R2 resolves there with the rationale.

Sketch only, pending Stage 1 verdict:

- Override `send_image` / `send_video` / `send_voice` / `send_document`
  on `GoogleChatAdapter` to replace the inherited URL-as-text fallback
  (`base.py:1147-1310`) with native Chat upload. Signatures already
  fixed by the base class; adapter just supplies a real implementation.
- Inbound: extend `_handle_message_event` (`googlechat.py:279-327`) to
  populate `MessageEvent.media_urls` and `MessageEvent.media_types`
  (per `base.py:710-711`) from `message.attachment[]`, with a fetch
  helper that streams the binary via Chat's media-download surface and
  writes into the gateway's existing media cache the same way telegram
  / discord adapters do (cross-reference at Stage 2 research time).
- Scope grant: if Stage 1 confirms the need, coordinate with the
  service-account grant in the GCP console and document in an ADR
  amendment.
- ADR alignment: ADR-003 already scopes media I/O into the adapter
  (`adr/003-adapter-scope-io-only.md:33`, point 4 — "Send outbound
  text/media via base-class primitives"). R2 overrides an in-scope
  inherited default; no ADR-003 amendment required. If Stage 1 surfaces
  a Drive-attachment path the adapter chooses to support, that may
  warrant an ADR-012 amendment (Card v2 interface widens to include a
  Drive-file widget), not an ADR-003 one.

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
  Events API (`google.workspace.chat.message.v1.*`)? This is the pivot
  that decides R3's shape. The pre-committed disposition (so Stage 1
  doesn't need to re-decide): **if Pub/Sub carries reaction events**,
  proceed with R3 independently. **If only Workspace Events carries
  them**, implement the outbound-only subset (`_add_reaction` /
  `_remove_reaction`) as a thin R3a; block the inbound handler (R3b)
  on R1. Reject "close R3 outright" as a disposition — outbound-only
  reactions are a meaningful UX win and don't require the
  R1-transport refactor.
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
- **`messageReplyOption` branch observability.** Current adapter sets
  `REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD` at
  `gateway/platforms/googlechat.py:398`. Stage 1 should verify: (a)
  does the API response distinguish "threaded into the original thread"
  from "fell back to a new thread"? (b) if not, does the agent need to
  know which branch happened? (c) would switching to
  `REPLY_MESSAGE_OR_FAIL_IF_NOT_FOUND` plus explicit retry-on-failure
  give us a cleaner error surface for the cases where we genuinely want
  the reply to land in the original thread or nowhere?
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
