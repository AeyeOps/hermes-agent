# R4 Stage 1: Thread-context parity

**Verdict**: Resolved. Native Google Chat threading is sufficient.

**Checked**: 2026-04-24 against public Google Chat documentation.

## Findings

Google Chat messages carry a server-owned thread resource. When creating a
message, the adapter can include `body.thread.name` and
`messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`.

The create endpoint documents that fallback mode replies to the requested
thread when possible and starts a new thread if the reply cannot be placed
there. The response contains the created message object, so the adapter can
compare the returned `thread.name` with the requested one.

## Decision

No adapter-side thread cache or state migration is needed. Keep using Chat
thread resource names directly.

M7 adds one small observability improvement: when the response thread
differs from the requested thread, log a warning with the requested thread,
response thread, and message resource name.

## Source

- Message create and reply options: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>
