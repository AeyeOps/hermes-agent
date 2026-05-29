# ADR-003: Adapter scope is I/O only

**Status**: Accepted
**Date**: 2026-04-20

## Context

Platform adapters sit above the plugin system:

```
User ↔ Platform Adapter → Gateway/Agent Core → Plugins (tools, hooks, memory, skills) → Model + builtin toolsets
```

`BasePlatformAdapter.handle_message` funnels every inbound event into
`self._message_handler(event)`, which is wired by `GatewayRunner` after
adapter creation. Because agent-core dispatch happens at that single
point, a new adapter inherits — for free — every plugin-registered tool,
every lifecycle hook, every `optional-skills/` bundle, plus
`channel_prompts`, cron delivery, `send_message_tool` routing, redaction,
interrupt support, and media caching.

Expanding the adapter's scope beyond I/O (e.g., reaching into tool
registration, modifying compression, or bypassing the handler) breaks
this inheritance and widens the rebase surface.

## Decision

The Google Chat adapter is responsible for exactly four things:

1. **Connect** to Google Chat (transport chosen in ADR-011).
2. **Receive** inbound events and normalize them into `MessageEvent`.
3. **Dispatch** via `self.handle_message(event)`.
4. **Send** outbound text/media via base-class primitives.

Nothing else. If a feature feels like it belongs in the adapter but
involves plugins, hooks, memory, or session management, it belongs
upstream — or it's already there and we just need to wire it up.

## Consequences

- Adapter surface stays small and reviewable.
- Cross-cutting features come free.
- If Google Chat needs something the base class doesn't expose, we file
  an upstream discussion rather than extend the adapter laterally.

## References

- `gateway/platforms/base.py` — `BasePlatformAdapter` and `handle_message`.
- `gateway/run.py` — `GatewayRunner._create_adapter` and handler wiring.
- `CLAUDE.md` `<architecture-context>`.
- ADR-002 (subclassing posture).
