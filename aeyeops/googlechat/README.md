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

## What does not live here

- Adapter code itself — that lives at `gateway/platforms/googlechat*`.
- Changes to upstream files — those are out of scope until the PR.
