# Google Chat adapter — post-DEMO-1 roadmap

Future work we've consciously deferred. Items land here when a follow-up
would otherwise disappear into session context. Each entry records the
motivating observation, the option space, and the decision gate — not a
commitment.

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
