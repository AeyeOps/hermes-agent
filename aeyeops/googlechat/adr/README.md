# ADRs — Google Chat adapter

Architecture Decision Records for the Google Chat platform adapter. Fork-only;
excluded from the upstream cherry-pick.

## Two kinds of decisions

1. **Convention anchors** — decisions to adopt an upstream pattern (token
   locks, backoff, dedupe, media caching, redaction). These are **Accepted**
   from the start because they codify existing upstream house style. They
   exist so later decisions can cite them instead of re-deriving the
   rationale.
2. **Google-Chat-specific decisions** — choices where Google Chat differs
   from other platforms and we're picking among real alternatives
   (transport, authentication, event mapping). These may start **Proposed**
   and move to **Accepted** once we dogfood.

## Layout

- `template.md` — Nygard-style ADR template.
- `NNN-<title-kebab>.md` — numbered ADRs. Numbering is monotonic and never
  reused; supersede by writing a new ADR that references the old one.

## When to write one

- About to adopt an upstream convention and want a reference anchor other
  decisions can cite.
- Picking between real alternatives for Google Chat and the reasoning will
  matter to future-you or an upstream reviewer.

## When not to write one

- Obvious mechanical follow-through ("add a `Platform` enum entry").
- Small style choices inside the adapter file — those live in code comments
  at most.
