# Requirements — Google Chat adapter

Functional use cases the initial adapter is expected to support. Review
the list; mark anything that should be added, dropped, or moved between
included/deferred before design closes.

Legend: `[·]` included in initial adapter · `[→]` deferred to post-initial · `[×]` out of scope permanently

---

## Conversation (user ↔ bot)

- `[·]` UC-01 — User sends a text message to the bot in a DM; bot replies.
- `[·]` UC-02 — User @mentions the bot in a group room; bot replies in the same space.
- `[·]` UC-03 — User replies in a thread where the bot has posted; bot continues the same conversation.
- `[·]` UC-04 — User invokes a slash command (e.g., `/hermes` or equivalent) that the agent fulfills.
- `[·]` UC-05 — Multiple users in the same group-room thread share one conversation history with the bot.
- `[·]` UC-06 — DM conversations are per-user with their own history.

## Attachments (inbound)

- `[·]` UC-07 — User uploads an image; the bot's vision tools can see it.
- `[·]` UC-08 — User uploads a voice or audio clip; the bot can transcribe/process it.
- `[·]` UC-09 — User uploads a document (PDF, DOCX, etc.); the bot can read it.
- `[·]` UC-10 — User uploads a video; the bot can extract frames/audio via existing tools.

## Replies (outbound)

- `[·]` UC-11 — Bot sends a plain-text reply.
- `[·]` UC-12 — Bot sends a markdown-formatted reply (bold, italic, inline code, code blocks, lists, links) that renders correctly in Google Chat.
- `[·]` UC-13 — Bot sends a reply longer than Google Chat's per-message limit; output is split into multiple messages with chunk indicators.
- `[·]` UC-14 — Bot sends a table; it's wrapped in a fenced code block.
- `[·]` UC-15 — Bot sends an image attachment.
- `[·]` UC-16 — Bot sends a voice or audio attachment.
- `[·]` UC-17 — Bot sends a video attachment.
- `[·]` UC-18 — Bot sends a document/file attachment.

## Interactive UI (Card v2)

- `[·]` UC-36 — Bot receives card-button clicks as interactive events routed into the agent. Interface TBD; requires a design pass before implementation.
- `[·]` UC-37 — Bot emits Card v2 rich cards (text, images, buttons, and common widgets). Interface TBD; agent↔adapter contract for structured output is unresolved and needs its own design.

## Progress and status

- `[·]` UC-19 — During a long-running tool call, the bot shows a typing indicator in the space/thread.
- `[·]` UC-20 — A user can interrupt an in-progress agent turn by sending a new message.

## Space lifecycle

- `[·]` UC-21 — Bot is added to a new space and is immediately ready to respond.
- `[·]` UC-22 — Bot is removed from a space and cleans up without leaving orphan state.
- `[·]` UC-23 — One bot process handles many spaces simultaneously; events are routed correctly by space.

## Agent-initiated messages

- `[·]` UC-24 — A cron job delivers a scheduled message to the space (and thread, when applicable) it was created in. Cron capture is `(space_id, thread_id_or_None)`; delivery passes both through to `_send_googlechat` so an update requested inside a thread posts back in that thread, not at the top of the space.
- `[·]` UC-25 — The agent uses the `send_message` tool to push a message to a specified space from outside the current conversation.
- `[·]` UC-26 — The agent requests human approval for a dangerous action; the user responds and the agent proceeds or aborts.

## Authorization and access control

- `[·]` UC-27 — Bot only responds to users on a configured allowlist.
- `[·]` UC-28 — Bot responds to all Workspace users when "allow all users" mode is enabled.
- `[·]` UC-29 — Bot refuses disallowed users with a clear message rather than silently dropping.

## Operations and setup

- `[·]` UC-30 — `hermes setup` interactively configures Google Chat (GCP project, subscription, service-account key).
- `[·]` UC-31 — `hermes status` reports Google Chat as configured / connected / disconnected.
- `[·]` UC-32 — `hermes tools` lists tools available in the Google Chat context.
- `[·]` UC-33 — Bot reconnects automatically after Pub/Sub transient failures with exponential backoff.
- `[·]` UC-34 — Logs redact sensitive identifiers (space IDs, user IDs, tokens).
- `[·]` UC-35 — Operator can rotate the service-account credential without rebuilding the bot.

---

## Deferred

- `[→]` UC-39 — Per-user OAuth for accessing user-scoped Drive/Gmail/Calendar data. User-scoped data access flows through MCP servers running on the host, not through the chat adapter — adapter-layer OAuth is unnecessary and out of scope.
- `[→]` UC-40 — Domain-wide delegation for per-user attribution.
- `[→]` UC-41 — Consumer (non-Workspace) Google Chat surface.

## Out of scope

- `[×]` UC-38 — HTTP endpoint transport as an alternative to Pub/Sub. Every existing chat-style adapter in the codebase (Telegram, Slack, Discord, WhatsApp, Signal, Matrix, Mattermost, QQBot, DingTalk, Feishu, etc.) receives messages over an outbound long-lived connection. Runtime inspection of the fork's deployment host confirms the bot runs without inbound HTTPS from the internet. Adding HTTP transport would introduce an inbound-ingress pattern unique to Google Chat for no capability the outbound path doesn't already deliver.
