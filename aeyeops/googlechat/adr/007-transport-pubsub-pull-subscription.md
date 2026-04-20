# ADR-007: Transport — Pub/Sub pull subscription

**Status**: Accepted
**Date**: 2026-04-20

## Context

Google Chat (Workspace) offers two transport modes for apps, both
supported and documented as of April 2026:

- **HTTP endpoint** — Google Chat `POST`s events to a public HTTPS URL
  the app exposes (typically Cloud Run Functions, but self-hosted is
  allowed with a valid-CA TLS cert). Response can be synchronous in the
  HTTP reply, or the endpoint can ack quickly and respond asynchronously
  via the Chat API. Requires public ingress.
- **Pub/Sub pull subscription** — Google Chat publishes events to a
  Pub/Sub topic; the app connects **outbound** to GCP and pulls from a
  subscription. Response is async via the Chat API. No inbound ingress;
  works behind NAT, firewalls, homelab setups, or anywhere else without
  public HTTPS.

Relevant properties of our dogfooding context:

- The fork's day-to-day host is a headless homelab server
  (`sfspark1`) with no public HTTPS endpoint and no existing
  reverse-proxy / tunnel setup.
- Agent turns can run for minutes (multiple tool calls, model rounds).
  Synchronous HTTP response isn't a fit — we'd need the ack-quick /
  respond-async pattern either way.
- Our Slack adapter (`gateway/platforms/slack.py:183`) uses Socket Mode
  rather than the HTTP Events API — the same philosophy: outbound
  connection from the app, no public endpoint needed.

## Decision

The initial Google Chat adapter uses **Pub/Sub pull subscription** as
its transport. The app connects outbound to a GCP project, pulls events
from a subscription, acknowledges them, and responds asynchronously via
the Chat API.

HTTP endpoint mode is **permanently out of scope for this adapter**, not
deferred. Runtime inspection of the fork's deployment host
(`srv1540558`) confirms every messaging adapter currently running —
Telegram long-poll, WhatsApp bridge → Meta, and any Slack/Discord
adapters that might be enabled — receives messages over outbound
long-lived connections. No adapter depends on inbound HTTPS from the
internet. Adding an HTTP transport only for Google Chat would introduce
an inbound-ingress pattern unique to this adapter for no capability the
outbound path doesn't already deliver.

## Consequences

- **Works behind NAT / firewall** — same property that made Slack
  Socket Mode the right choice there.
- **Consistency with Slack adapter's philosophy** — outbound-only
  transport reduces the infrastructure surface area a user needs to
  operate the bot.
- **GCP footprint is required**: project, Chat API enabled, Pub/Sub
  API enabled, one topic, one pull subscription, one service account.
  Operators without GCP access can't use the initial adapter — a real
  limitation for any org not already in Workspace.
- **Ack-then-respond async pattern** suits long-running agent turns.
  The Pub/Sub subscriber ack tells Google Chat "we got it"; the Chat API
  call delivers the reply whenever the agent finishes.
- **Setup wizard** (`hermes_cli/gateway.py`) needs Pub/Sub-specific
  variables: project ID, subscription path, service-account key path
  (or ADC).
- **Python dependency**: `google-cloud-pubsub` — adds to
  `check_googlechat_requirements()`.
- **Scales flat**: one subscription carries events from every space the
  bot is installed in (not per-space). The adapter demultiplexes by
  `space.name` on each received event.

## References

- [Google Chat Pub/Sub quickstart](https://developers.google.com/workspace/chat/quickstart/pub-sub)
- [Google Chat HTTP quickstart](https://developers.google.com/workspace/chat/quickstart/gcf-app)
- `gateway/platforms/slack.py:183` — Socket Mode precedent.
