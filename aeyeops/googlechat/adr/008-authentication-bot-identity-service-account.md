# ADR-008: Authentication — bot-identity service account

**Status**: Accepted
**Date**: 2026-04-20

**Follow-up**: ADR-013 proposes administrator-approved `chat.app.*`
app-auth scopes for post-DEMO capabilities. This does not change the
initial adapter auth model: `chat.bot` remains the accepted baseline, and
domain-wide delegation remains out of scope unless ADR-013 verification
proves app-auth scopes cannot satisfy a follow-up feature.

## Context

In Google Workspace, a Chat app has three realistic authentication
models:

1. **Bot-identity service account** — the bot acts as itself. Messages
   post from the bot account; reads happen with the bot's privileges.
   Simplest governance, no per-user consent.
2. **Service account with domain-wide delegation (DWD)** — the bot
   impersonates specific users on API calls. Requires Workspace
   domain-admin configuration to authorize delegation. Messages can be
   attributed to the impersonated user, and reads respect that user's
   access.
3. **OAuth 2.0 per-user authorization** — each user grants interactive
   consent; the bot exchanges a refresh token per user. Required if
   the bot needs to access user-specific Drive/Gmail/Calendar data
   under that user's identity.

The transport decision (Pub/Sub pull subscription) layers on
Pub/Sub-level auth independent of Chat auth: the consumer needs
`Pub/Sub Subscriber` on the subscription. Google Chat's system account
(`chat-api-push@system.gserviceaccount.com`) needs `Pub/Sub Publisher`
on the topic — that's a one-time IAM binding, not an ongoing concern.

For our use case:

- The agent posts responses to Chat and doesn't read user-owned Drive
  or Gmail content under a user's identity. No need for DWD or OAuth 2.0.
- Attribution-to-user isn't a dogfooding requirement; the bot showing
  up as the bot is correct and expected.
- DWD adds real governance burden (domain-admin involvement per
  deployment) that would slow dogfooding and complicate upstream
  reviewer setup.

## Decision

**Bot-identity service account.** The adapter authenticates to Google
Chat and Pub/Sub using a single service account with these bindings:

- **OAuth scope**: `https://www.googleapis.com/auth/chat.bot` — posts
  messages to Chat as the bot identity.
- **IAM role on the subscription**: `roles/pubsub.subscriber` — consumes
  events.
- (Operator-side, one-time): `roles/pubsub.publisher` on the topic for
  `chat-api-push@system.gserviceaccount.com` so Google Chat can publish
  into our topic.

Credential delivery:

- Prefer **Application Default Credentials** (ADC) when the bot runs
  somewhere ADC works (GCE/GKE metadata server, `gcloud` user auth for
  local dev).
- Fall back to an explicit service-account JSON key path via env var
  (e.g., `GOOGLE_APPLICATION_CREDENTIALS` or a bot-specific
  `GOOGLECHAT_KEY_PATH` — final name settled during implementation).

Domain-wide delegation and per-user OAuth 2.0 are out of scope for the
initial adapter. If a future feature needs user-scoped reads, a
follow-up ADR introduces DWD or OAuth 2.0 for that code path
specifically — we don't retrofit the whole adapter.

## Consequences

- Single credential manages both Chat and Pub/Sub auth — one env var
  to set, one key file to rotate.
- Bot posts as the bot; no per-user attribution, which matches Slack
  and Telegram behavior.
- Operators need a GCP service account and one IAM binding on the
  Pub/Sub subscription. Admin complexity is low.
- If upstream reviewers ask for DWD or per-user OAuth support, it
  becomes a follow-up rather than a refactor — the core auth path
  stays bot-identity.
- `check_googlechat_requirements()` verifies that credentials resolve
  (ADC or explicit key path) and fails cleanly with a helpful message
  if neither is configured.

## References

- [Google Chat Pub/Sub quickstart](https://developers.google.com/workspace/chat/quickstart/pub-sub) — scope + IAM requirements.
- ADR-007 — Pub/Sub transport, which this auth model assumes.
