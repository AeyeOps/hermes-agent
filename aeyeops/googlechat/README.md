# googlechat

Workspace for the Google Chat platform adapter.

## Adapter location (in upstream convention)

- Code: `gateway/platforms/googlechat.py` (or `gateway/platforms/googlechat/`
  if it grows to a package, following the `qqbot` precedent).
- Token-lock contract: `acquire_scoped_lock()` in `connect()`/`start()` and
  `release_scoped_lock()` in `disconnect()`/`stop()`. See
  `gateway/platforms/telegram.py` as the canonical pattern.

## What lives here (fork-only, excluded from upstream PR)

- Design notes and spec drafts for the adapter.
- Transport/auth research (Google Chat API, Pub/Sub vs webhook delivery, scopes).
- Event normalization notes — how Google Chat events map to `MessageEvent`.
- Dogfooding runbooks and rough edges discovered while using the fork.
- ADRs for adapter-specific decisions that don't need to land upstream.

## Docs index

- `requirements.md` — UC catalogue that shapes adapter scope.
- `design.md` — the 16-integration-point design.
- `plan.md` — plan-level summary of the integration points.
- `build-plan.md` — milestone/commit sequencing (C0–C34 across M0–M6).
- `streaming-spec.md` — M6 spec: streaming, HTML rendering, thinking-ack.
- `roadmap.md` — deferred items, staged through feasibility → impl-path →
  impl-plan gates. R1: Workspace Events API migration. R2: outbound media
  senders. R3: reactions. R4: thread-context parity assessment.
- `adr/` — adopted architectural decisions (001–012).
- `fixtures/` — captured Chat event samples for test reuse.

## What does not live here

- Adapter code itself — that lives at `gateway/platforms/googlechat*`.
- Changes to upstream files — those are out of scope until the PR.
