# ADR-011: Target Google Chat within Google Workspace as the primary surface

**Status**: Accepted
**Date**: 2026-04-20

## Context

Google Chat exists in two meaningfully different surfaces:

- **Google Workspace** (Business / Enterprise tenants) — bots are
  installable apps registered against a GCP project, authenticate with
  service accounts, and can use either HTTP webhooks or Pub/Sub for event
  delivery. This is where Workspace admins install and govern apps for
  their org.
- **Consumer Google accounts** (Chat bundled with personal Gmail) — bot
  capabilities have historically lagged or been absent entirely, with a
  different app-distribution model and narrower API surface.

Hermes-agent's realistic deployment target inside an org is Workspace.
Designing for the Workspace surface first keeps authentication,
transport, and event-handling decisions coherent with one well-documented
platform instead of splitting across two.

## Decision

The Google Chat adapter targets **Google Workspace** as its primary and
initial integration surface. All downstream ADRs (ADR-012 transport,
ADR-013 authentication, ADR-014 event mapping, etc.) assume Workspace
primitives: GCP project, service account identity, Pub/Sub or HTTP
webhook delivery, Workspace admin-installed bot.

Consumer-mode Google Chat is **out of scope for the initial adapter**.
If consumer support later turns out to be a small delta — same event
shape, compatible auth — we can broaden the adapter transparently. If
it requires a materially different model, it gets its own later ADR
and likely its own adapter module.

## Consequences

- Authentication, transport, and event handling are designed against
  Workspace primitives only. Simpler, less conditional code.
- Dogfooding target is a Workspace tenant, not a personal Google account.
- User-facing language in docs (post-PR) refers to "Google Chat via
  Google Workspace," not bare "Google Chat."
- If Google expands consumer bot capabilities in the future, this ADR
  is revisited rather than silently stretched.

## References

- `aeyeops/googlechat/design.md` — Workspace target called out up front.
- ADR-012 (transport), ADR-013 (authentication) — both assume Workspace.
