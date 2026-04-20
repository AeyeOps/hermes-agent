# ADR-012: Card v2 interface — synthesized-TEXT inbound, platform-tool outbound, narrower schema

**Status**: Accepted
**Date**: 2026-04-20

## Context

Three real questions the prior ADRs left open:

1. Does `CARD_CLICKED` reach the agent, or resolve locally (Slack/Discord
   style)?
2. How does the agent _emit_ cards — base-class change, or a platform-local
   tool?
3. What widget set is in scope for the initial adapter?

ADR-004 parked `CARD_CLICKED` as an in-adapter side effect, parallel to
Slack Block Kit approval (`gateway/platforms/slack.py:208-215, 1302`) and
Discord button views (`gateway/platforms/discord.py:3305-3689`). ADR-006
parked Card v2 out of scope until dogfooding surfaced a need.

Requirements UC-36 (clicks routed into agent) and UC-37 (agent emits Card
v2 cards: text + buttons + images + common widgets) both pulled into the
initial adapter's scope — so both of those parking decisions need to be
revisited. This ADR revisits them together because they are two halves of
one contract.

### Key mechanism: Google Chat click events are self-describing

Button `action.parameters` set at send time round-trip back on the click
event. `selectionInput` widget values submitted with a button arrive as
`common.formInputs` on the same event. The click event carries the
context the adapter needs to correlate it — no adapter-side pending-card
registry is required. This is the mechanism that makes a stateless
inbound path possible.

### Why a base-class extension was rejected

A considered alternative was extending `SendResult` with a structured
card payload and `MessageEvent` with a typed `CARD_CLICK` variant, so
Slack (Block Kit), Discord (embeds), and Telegram (inline keyboards)
could eventually ride the same rails. Architecturally clean, but:

- Violates the spirit of ADR-003 (adapter scope is I/O only; cross-cutting
  extensions go upstream, not through an adapter).
- Blocks the fork's build phase on an upstream design conversation.
- Hermes-agent's current agent-side flow is model-text-centric — the model
  reasons about stringified events; there is no typed card-event handler.
  The structural crispness of a typed event is largely theoretical until
  an agent-side refactor exists to consume it.

## Decision

### Inbound (click → agent)

On every `CARD_CLICKED` event, the adapter synthesizes a
`MessageEvent(MessageType.TEXT)` whose `text` folds together the click's
`action.actionMethodName`, `action.parameters`, and any
`common.formInputs`. The event is dispatched through
`self.handle_message(event)` like any user message. No base-class change,
no adapter registry — Google Chat round-trips the context on the click
itself.

Example synthesized text:

```
[Card response on 'Deploy options' (ctx=deploy_choice_abc123):
 action=submit_deploy
 selections (radio): environment=staging
 selections (checkbox): flags=notify_slack, notify_email]
```

Self-message filter applies unchanged. The dedup key for a click
cannot be the originating card's `message.name` (a single card can
produce multiple clicks); the concrete key used for
`helpers.MessageDeduplicator` on click events is settled during
implementation against the actual Chat / Pub/Sub event shape.

### Outbound (agent → card)

A new `send_chat_card` tool is registered in `toolsets.py` **only when
`googlechat` is in the active platforms list**. The tool accepts a
narrower pydantic `CardSpec` schema — not raw Card v2 JSON — so the
agent's authorship surface stays bounded and invalid widgets fail at
validation rather than at the Chat API edge.

Conceptual signature:

```python
async def send_chat_card(
    space_id: str,
    thread_id: str | None,
    card: CardSpec,
) -> dict  # {"message_name": "spaces/.../messages/...", "success": bool}
```

`CardSpec` is a discriminated union of the widgets enumerated below. The
adapter translates `CardSpec` → Card v2 JSON at send time, posts via the
Chat API, returns the message name. The message name plus any
`parameters` the agent attached to buttons/submit widgets give the
eventual click event enough context to correlate.

Existing base-class `send()`, `send_image()`, `send_typing()` methods
stay as they are. Text replies continue to use the GFM-translation path
from ADR-006.

### Widget set for the initial adapter

| Widget | Purpose |
|--------|---------|
| `textParagraph` | card text content; reuses the GFM→Chat-markdown translator from ADR-006 |
| `image` | static images |
| `divider` | structural separation |
| `button` / `buttonList` | immediate-click actions |
| `selectionInput` · `RADIO_BUTTON` | pick one of N |
| `selectionInput` · `CHECK_BOX` | pick zero-to-N of N |
| submit `button` | required trailing widget on any card that uses selections; produces the click event carrying `common.formInputs`. `CardSpec` validation enforces presence. |
| `card.header` | title + optional subtitle |

Deferred, each its own follow-up if dogfooding shows the need:

- `textInput` — free-form text input
- `dateTimePicker` — date/time entry
- `decoratedText`, `grid`, `columns` — richer layout
- Modal dialogs (`action_response.type = DIALOG`) — distinct lifecycle
- `selectionInput` · `DROPDOWN` / `SWITCH`
- Cron-delivered cards — cron remains text-only via `_send_googlechat`

### Supersedes and revises

- Revises ADR-004's `CARD_CLICKED → in-adapter side effect` mapping.
  ADR-004 is updated to reflect the agent-routed synthesized-TEXT path.
- Revises ADR-006's "Card v2 out of scope" decision. ADR-006 is updated
  to reflect Card v2 now in scope, pinned to the widget set above.

## Consequences

- Adapter surface grows by one outbound code path (`send_chat_card`) and
  one inbound synthesis step. Base class is untouched. Rebase surface
  does not widen.
- The cherry-pickable diff stays small: new adapter module, new
  platform-aware tool in `toolsets.py`, one-line wiring in
  `_create_adapter`. No base-class changes to review upstream.
- Stateless correlation via Google Chat's `action.parameters` round-trip
  means the adapter carries no pending-card state — no TTLs, no eviction,
  no leaks between cards.
- Session history gives the agent its "I sent this card" context. A click
  that arrives from a session the bot has forgotten (e.g., post-restart
  on a session that didn't persist) degrades gracefully to a standalone
  `MessageEvent(TEXT)` — not a crash, just a "where did this come from"
  turn.
- Radio/checkbox widgets require a trailing submit button. Google Chat
  fires no event until submit. `CardSpec` validation enforces the
  pairing at compose time rather than discovery time.
- Future expansion (textInput, dropdown, dialogs) lands by growing the
  `CardSpec` union and the translator, without touching the
  agent↔adapter contract or the base class.
- If a shared typed card-event model is desired across Slack / Discord /
  Telegram later, that is a separate upstream discussion — not a
  retrofit of this adapter.

## References

- UC-36, UC-37 — `aeyeops/googlechat/requirements.md`.
- ADR-003 — adapter scope is I/O only.
- ADR-004 — event → `MessageEvent` mapping (revised by this ADR for
  `CARD_CLICKED`).
- ADR-006 — message format (revised by this ADR to bring Card v2 into
  scope).
- `gateway/platforms/slack.py:208-215, 1302` — Slack Block Kit
  local-resolve approval. Referenced for what we chose _not_ to do for
  agent-driven cards.
- `gateway/platforms/discord.py:3305-3689` — Discord button-view
  local-resolve approval. Same reference.
- `gateway/platforms/base.py` — `MessageEvent`, `SendResult`,
  `MessageType` definitions; deliberately not modified.
- [Google Chat Card v2 reference](https://developers.google.com/workspace/chat/design-interactive-card-dialog)
