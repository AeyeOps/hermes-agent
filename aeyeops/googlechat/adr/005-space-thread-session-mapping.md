# ADR-005: Space/thread → `SessionSource` mapping

**Status**: Accepted
**Date**: 2026-04-20

## Context

Google Chat has two identity layers the adapter has to fold into a
`SessionSource`:

- **Spaces** (`spaces/AAA…`): DMs, group DMs, and named rooms.
- **Threads** (`spaces/AAA.../threads/BBB…`): optional branch within a
  room. Top-level (unthreaded) messages also exist in rooms that
  support them; DMs are flat.

Two reference patterns already live in the codebase:

- **Slack** (`gateway/platforms/slack.py:1179-1186`): `chat_id` is the
  channel ID; `thread_id` is `thread_ts`. Threads modify the channel
  identity — both enter the session key at `gateway/session.py:514`. By
  default (`thread_sessions_per_user=False`), users in the same thread
  share one conversation.
- **Discord** (`gateway/platforms/discord.py:2427`): `chat_id` becomes
  the thread ID itself when the message is in a thread (not the parent
  channel). Threads are separate chats with natural isolation.

Google Chat threads are semantically closer to Slack's — they're
branches within a parent space, not standalone conversation entities.
The thread ID is meaningful only in combination with its space; a
Google Chat thread without its space ID isn't a usable handle.

`SessionSource` (`gateway/session.py:65-139`) already carries a
cross-platform `thread_id` field — no new fields needed.

## Decision

Follow the Slack pattern for Google Chat:

- `chat_id` = space resource name (e.g., `spaces/AAA…`).
- `thread_id` = thread resource name when the message is in a thread;
  `None` for top-level room messages and DMs.
- `chat_type` =
  - `"dm"` when the Google Chat space type is `DIRECT_MESSAGE`,
  - `"group"` otherwise.
- `user_id` = sender resource name (e.g., `users/123…`).
- `user_name`, `chat_name` populated from the Chat API when available.

Assistant-thread-equivalent override (Slack's `chat_type="dm"` trick at
`slack.py:928`) is **not applicable**: Google Chat has no analogous
assistant-lifecycle event, so no per-user isolation override is needed.

## Consequences

- Threads in group rooms share conversation history across users,
  matching Slack defaults.
- Top-level room messages share the space-level session across users.
- DM sessions are naturally per-user via `chat_type="dm"` (same as
  Slack and Discord).
- No changes required to `SessionSource`; the existing `thread_id`
  field covers us — no need to touch `gateway/session.py` (checklist
  item 5).
- If dogfooding reveals that shared-thread UX doesn't fit how people
  use Google Chat rooms, we revisit with a new ADR to flip to
  `thread_sessions_per_user=True` for this platform or to switch to
  the Discord-style approach.

## References

- `gateway/platforms/slack.py:1179-1186` — canonical `build_source`
  call.
- `gateway/platforms/discord.py:2427` — contrasting thread-as-chat
  pattern.
- `gateway/session.py:65-139` — `SessionSource` shape.
- `gateway/session.py:470-526` — session-key derivation.
- `gateway/platforms/base.py:2159-2188` — `build_source` signature.
