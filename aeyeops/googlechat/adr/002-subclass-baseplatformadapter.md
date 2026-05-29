# ADR-002: Subclass `BasePlatformAdapter`, don't fork an existing adapter

**Status**: Accepted
**Date**: 2026-04-20

## Context

Two tempting ways to start a new adapter:

1. Subclass `BasePlatformAdapter` and implement required methods from scratch.
2. Copy `slack.py` (or another adapter) and modify it in place.

Option 2 feels fast but ships accidental coupling: style, state machines,
helper functions, and quirks that weren't ours to inherit. It also makes
upstream rebases riskier, because upstream changes to the "parent" adapter
don't automatically flow into our copy — we'd maintain a silent fork
inside the fork.

`BasePlatformAdapter` is explicitly designed as the integration seam.
Inbound messages funnel through `self._message_handler(event)`, wired by
`GatewayRunner` after adapter creation. That is the single dispatch point
for every adapter — so subclassing the base class is all the inheritance
we need.

## Decision

The Google Chat adapter subclasses `BasePlatformAdapter` directly and
implements the four required methods (`connect`, `disconnect`, `send`,
`get_chat_info`) from scratch. Reference adapters (Slack, Signal, Discord,
Telegram) are read for conventions, not copied.

## Consequences

- First-pass implementation is slower: we write each method rather than
  cloning an existing adapter.
- Upstream changes to other adapters land cleanly via rebase without
  needing manual merges into a forked copy.
- Style-drift risk — addressed by ADRs 004–010 which pin conventions with
  concrete file:line citations.

## References

- `gateway/platforms/base.py` — `BasePlatformAdapter` definition.
- `CLAUDE.md` `<architecture-context>` block.
