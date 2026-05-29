# ADR-014: Native Chat threading is sufficient

**Status**: Accepted
**Date**: 2026-04-24

## Context

Slack and Telegram carry adapter-side thread/topic state because their
conversation identifiers need local derivation or persistence. Google Chat
already emits a server-owned `Message.thread.name` resource on inbound
message events, and message creation accepts that thread resource when
replying.

The `spaces.messages.create` reference documents
`messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`: Chat replies to
the requested thread and starts a new thread if the reply cannot be placed
there. The response includes the created message resource, including its
thread field.

Checked source on 2026-04-24:

- Google Chat `spaces.messages.create`: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>

## Decision

Do not add a Google Chat thread cache or persistence layer. The adapter
uses the inbound `thread.name` as the session thread id and sends it back
as `body.thread.name` for replies.

The adapter logs a warning when Chat returns a different `thread.name` than
the one requested. That keeps the current fallback behavior but makes
silent thread drift observable in dogfood logs.

## Consequences

- No new state file, migration, cache eviction policy, or cross-restart
  recovery path is needed for Chat threading.
- DMs remain flat from a UX perspective even if the schema includes a
  thread object.
- If dogfood shows frequent fallback warnings, a later change can evaluate
  `REPLY_MESSAGE_OR_FAIL` plus explicit retry as a new top-level post.
