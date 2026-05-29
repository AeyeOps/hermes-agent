# ADR-004: Google Chat event → `MessageEvent` mapping

**Status**: Accepted (CARD_CLICKED handling revised by ADR-012)
**Date**: 2026-04-20

## Context

Google Chat emits four inbound event types the adapter can receive:
`MESSAGE`, `ADDED_TO_SPACE`, `REMOVED_FROM_SPACE`, and `CARD_CLICKED`.
Each needs a decision about whether and how it surfaces to the agent.

Two broad approaches exist:

1. **Unified handler, selective surfacing** — a single entry-point
   handler uses if/elif to route events; `MessageEvent` is emitted only
   for user-originated messages; other event types are handled as
   in-adapter side effects or dropped.
2. **Every event as MessageEvent** — non-message events carry a type
   discriminator and reach the agent; agent is responsible for deciding
   what to do with lifecycle and interactive events.

The `CARD_CLICKED` event is the interesting case. We could follow
Slack Block Kit / Discord button convention (handle click-resolution
locally as an approval or state mutation), or we could surface clicks
to the agent so Google-Chat-card-driven UX becomes agent-programmable.

Reference adapters both use approach (1):
- `gateway/platforms/slack.py:950` — single `_handle_slack_message`
  with if/elif; Block Kit button clicks resolve locally at
  `gateway/platforms/slack.py:208-215, 1302`.
- `gateway/platforms/discord.py:2941` — single `_handle_message`;
  button clicks handled in view callbacks at
  `gateway/platforms/discord.py:3305-3689`.

Neither surfaces non-message events to the agent.

## Decision

The Google Chat adapter follows approach (1). Concretely:

- `MESSAGE` → `MessageEvent(text, chat_id=space_id, …)`.
- `ADDED_TO_SPACE` → in-adapter side effect. Cache space metadata if
  useful; no greeting by default; no `MessageEvent`.
- `REMOVED_FROM_SPACE` → dropped or minimal cleanup; no `MessageEvent`.
- `CARD_CLICKED` → synthesized `MessageEvent(MessageType.TEXT)` routed
  to the agent via `self.handle_message(event)`. The adapter folds the
  click's `action.actionMethodName`, `action.parameters`, and any
  `common.formInputs` into the text. Correlation rides on Google Chat's
  native parameter round-trip — no adapter-side pending-card registry.
  Full interface design in ADR-012.

Entry point is a single handler with if/elif dispatch. Self-message
filter compares `event.user.name` (or equivalent bot-identity field)
against our own. Dedup key is `message.name` (Google Chat's globally
unique message resource name).

## Consequences

- Agent sees a clean user-message stream plus agent-routed card
  interactions; no other platform noise.
- Symmetric code shape with Slack and Discord for lifecycle events;
  card handling diverges intentionally per UC-36 (see ADR-012).
- Space join/leave events don't spawn agent turns, so no accidental
  "say hello" prompts fire when the bot is added to a space.

## References

- `gateway/platforms/slack.py:950` — unified handler entry.
- `gateway/platforms/slack.py:208-215` — approval button action registration.
- `gateway/platforms/slack.py:1302` — approval resolution.
- `gateway/platforms/discord.py:2941` — unified handler entry.
- `gateway/platforms/discord.py:3305-3689` — button view callbacks.
