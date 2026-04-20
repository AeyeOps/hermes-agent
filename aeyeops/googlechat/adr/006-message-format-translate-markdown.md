# ADR-006: Translate GFM → Google Chat markdown; defer Card v2

**Status**: Accepted (Card v2 scope decision revised by ADR-012)
**Date**: 2026-04-20

## Context

Google Chat supports three outbound message shapes:

1. **Plain text** — no formatting.
2. **Google Chat markdown** — `*bold*`, `_italic_`, `~strike~`, backticks
   for inline code, triple-backticks for code blocks. Not GFM-compatible:
   bold uses a single asterisk, not two; no table syntax.
3. **Card v2** — structured cards with widgets (text, images, buttons,
   sections, grids). Richest, most complex.

The agent emits GFM-style markdown by default (the same prompt-builder
output that Slack/Telegram/etc. see). Three real options for the adapter:

- **Pass through as plain text** — simple, but `**bold**` and `_italic_`
  render as literal characters in Google Chat. Agent output looks broken.
- **Translate GFM → Google Chat markdown** — override `format_message()`
  to rewrite bold/italic/strike/links/code. Matches Slack
  (`gateway/platforms/slack.py:437-545`) and Telegram
  (`gateway/platforms/telegram.py:1988-2161`) exactly. Base class
  provides a no-op stub at `gateway/platforms/base.py:2201-2210`.
- **Emit Card v2** — rich but requires per-message structure decisions
  and doesn't match reference-adapter shape.

## Decision

Translate GFM to Google Chat markdown in a `GoogleChatAdapter.format_message()`
override. Handle:

- Bold `**text**` → `*text*`
- Italic `*text*` / `_text_` → `_text_`
- Strikethrough `~~text~~` → `~text~`
- Inline code `` `code` `` → unchanged
- Fenced code blocks ```` ```lang ``` ```` → unchanged (Google Chat
  supports triple-backticks)
- Links `[text](url)` → `<url|text>` (Google Chat link syntax)
- Headers `##` → bold (Google Chat has no header syntax)
- GFM tables → wrap in fenced code block (follow `telegram.py:144-196`
  precedent; Google Chat has no table syntax either)
- Blockquotes `>` → preserve as plain text (Google Chat's support is
  limited; revisit if dogfooding surfaces issues)

Use the base-class `truncate_message()` helper
(`gateway/platforms/base.py:2212-2342`) for chunking when output exceeds
the platform's per-message limit. `MAX_MESSAGE_LENGTH` value is
confirmed during transport research.

**Card v2 scope** — revised by ADR-012. Card v2 is now in scope for
the initial adapter to satisfy UC-36 and UC-37. Rich cards travel
through a separate code path (`send_chat_card` tool + `CardSpec`
schema); this ADR continues to govern plain-text replies, which stay
on the GFM-translation path. The two paths are additive, not
alternatives.

## Consequences

- Agent's markdown output renders as intended in Google Chat.
- Code shape mirrors Slack and Telegram for text replies; new-adapter
  review surfaces no surprises on that path.
- Cards ride a separate code path defined in ADR-012; this ADR's
  translator is reused inside card `textParagraph` widgets so the two
  formatting paths converge on one GFM → Chat-markdown implementation.
- Slight translation cost per message — negligible relative to network
  latency.
- Escape rules for Google Chat markdown (what needs backslash-escaping,
  if anything) get verified during implementation against live Chat API
  behavior.

## References

- `gateway/platforms/slack.py:437-545` — canonical GFM→mrkdwn translation.
- `gateway/platforms/telegram.py:1988-2161` — canonical GFM→MarkdownV2.
- `gateway/platforms/telegram.py:144-196` — table-in-code-block wrapper.
- `gateway/platforms/base.py:2201-2210` — `format_message()` stub.
- `gateway/platforms/base.py:2212-2342` — `truncate_message()` helper.
