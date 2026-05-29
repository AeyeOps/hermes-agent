# ADR-001: Follow `ADDING_A_PLATFORM.md` as source of truth

**Status**: Accepted
**Date**: 2026-04-20

## Context

`gateway/platforms/ADDING_A_PLATFORM.md` is a 16-item upstream checklist
covering every integration point a new platform adapter touches — the
adapter module, `Platform` enum, factory, authorization maps, session
sources, prompt hints, toolsets, cron delivery, the `send_message` tool,
cronjob schema, channel directory, status display, setup wizard, ID
redaction, documentation, and tests.

It already reflects upstream's reviewed-and-accepted pattern. Deviating
from it expands our eventual PR surface and creates inconsistency across
adapters.

## Decision

Treat `ADDING_A_PLATFORM.md` as authoritative. Our `plan.md` mirrors its
16-item structure. Our adapter work is organized around it.

## Consequences

- Faster upstream review: reviewers compare our diff against the checklist
  they authored; deviations are obvious and require justification.
- Less architectural freedom: if the checklist prescribes a pattern we'd
  rather avoid, we raise it upstream rather than deviate silently.
- Checklist updates land via rebase; we re-check our adapter against the
  new version each time.

## References

- `gateway/platforms/ADDING_A_PLATFORM.md`
- `aeyeops/googlechat/plan.md` — mirrors the 16-item structure.
