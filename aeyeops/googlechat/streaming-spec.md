# Streaming, Markdown Rendering, and Thinking-Ack — Spec

**Status:** Draft
**Date:** 2026-04-20
**Scope:** Three user-visible behaviours we want on the `googlechat` adapter, on top of the shipped DEMO-1 baseline.

---

## Problem

DEMO-1 reply UX is "user sends → long silence → full answer appears in one `spaces.messages.create` call." Three gaps:

1. **No acknowledgement** while the server accepts the message and before the LLM starts producing tokens. Users can't tell whether anything is happening.
2. **No progressive delivery.** The whole reply buffers in the gateway and lands as one message, even when generation runs 10–30s.
3. **Markdown doesn't render.** The agent emits GFM (`**bold**`, tables, lists). Google Chat's plain-text field interprets a *different* dialect, and the richest formatting requires the cardsV2 widget family with an HTML-ish subset. Our current adapter passes text unchanged, so `**bold**` renders as literal asterisks.

The gateway and stream consumer infrastructure to solve all three is already in place — this spec pins how the `googlechat` adapter plugs into it.

---

## Existing hooks we plug into

| Hook | Source | What it's for |
|---|---|---|
| `adapter.send_typing(chat_id, metadata)` | `gateway/run.py:8864` fires it **before** the inference HTTP request. `_keep_typing` (`gateway/platforms/base.py:1406`) re-fires every 2s during the turn. | The thinking-ack hook. Called once immediately after the message is accepted, before any LLM tokens. |
| `adapter.stop_typing(chat_id)` | `gateway/run.py:4309,4603` on turn-end / error. | Clears placeholder state. |
| `adapter.send(chat_id, content, ...)` | `GatewayStreamConsumer._send_new_chunk` (`gateway/stream_consumer.py:497`) on first delta — captures `message_id`. | Creates the streaming message. |
| `adapter.edit_message(chat_id, message_id, content, *, finalize)` | `GatewayStreamConsumer._send_or_edit` on each subsequent delta, with `finalize=True` on `got_done`. | Progressive updates. |
| `adapter.REQUIRES_EDIT_FINALIZE` | Class attr read by the stream consumer (`gateway/stream_consumer.py:109`). | Forces an explicit final edit even when mid-stream edit already shipped the final bytes (DingTalk AI Cards precedent). |
| `adapter.MAX_MESSAGE_LENGTH` | Read by stream consumer at `gateway/stream_consumer.py:269`. | Already exposed on `GoogleChatAdapter` via `_max_message_length`. |

No gateway or base-class changes are required for any of this — adapter-local work only, preserving ADR-003's I/O-only scope.

---

## Google Chat API surface (verified 2026-04-20)

Confirmed from current Google Chat REST reference:

| Fact | Source |
|---|---|
| `PATCH v1/{message.name}` supports `updateMask=text`, `attachment`, `cards`, `cardsV2`, `accessoryWidgets`. | `developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/patch` |
| Scope `chat.bot` (already configured) covers patch. | same |
| **Per-space write cap = 1 write/sec** for `create` **and** `patch` combined. | `developers.google.com/workspace/chat/limits` |
| Plain-text message dialect: `*bold*`, `_italic_`, `~strike~`, `` `code` ``, fenced code, `* item` / `- item` lists, `> blockquote`, `<url\|text>` links, `<users/id>` mentions. | `developers.google.com/workspace/chat/format-messages` |
| `cardsV2.textParagraph` HTML subset: `<b>`, `<i>`, `<u>`, `<s>`, `<font color>`, `<a href>`, `<time>`, `<br>`, `<code>`, `<pre>`, `<ul>`, `<ol>`, `<li>`. | same |

Not confirmed from docs — needs smoke-verify at implementation time:

- Whether `PATCH updateMask=cardsV2` on a message originally created with `text` works cleanly (replacing text with cards) or throws. Fallback plan: if Chat rejects the cross-form patch, delete+create instead on the finalize boundary.
- Whether a single message can carry `text` and `cardsV2` simultaneously (the REST schema permits both fields; runtime behaviour not confirmed).

---

## Design

### Three phases, one message

The adapter drives one logical message per agent turn through three phases, reusing the same `message.name` throughout:

```
phase 1 (thinking)         phase 2 (streaming)           phase 3 (finalize)
──────────────────         ───────────────────           ───────────────────
create: text="💭 …"   ──►  patch: updateMask=text   ──►  patch: (see routing)
                           body.text=<chat-md>           body.{text|cardsV2}=<rendered>
                           cursor "▉" appended
```

The `message_id` captured in phase 1 is carried through phase 2 (as the target of `edit_message`) and phase 3 (as the target of the final patch). This collapses the stream-consumer's normal "first `send()` creates the message" into a patch, which we handle by having `send()` check for an existing placeholder.

### Phase 1 — thinking ack (`send_typing`)

Override `send_typing(chat_id, metadata)` so that the **first** call per (chat_id, thread_id) in a turn:

1. Posts a placeholder via `spaces.messages.create`, `thread.name` set from `metadata["thread_id"]` when present (reusing the existing `messageReplyOption` path).
2. Stores `{(chat_id, thread_id): message_name}` in an adapter-local dict `_thinking_placeholders`.
3. Returns.

Subsequent `send_typing` calls for the same chat+thread (from `_keep_typing`'s 2s loop) are **no-ops** — Google Chat has no native typing indicator and re-creating would spam the space. The already-posted placeholder stays visible until phase 2 overwrites it or phase 3 finalizes it.

Placeholder content options (pick one during implementation — user-visible, keep it short to avoid wasted write-quota if the turn resolves quickly):

- `💭 _Thinking…_` (simplest, italic via Chat-markdown)
- `_MMAI is thinking…_`
- Animated-looking unicode (⠋⠙⠹…) — rejected: would require periodic edits = wasted quota

`stop_typing(chat_id)` clears the `_thinking_placeholders` entry; if the turn errored before any streaming happened, we additionally patch the placeholder to an error string or delete it (decision in implementation — deleting is cleaner but costs 1 write).

### Phase 2 — streaming text (`send` + `edit_message`)

Override `send()` so that when a placeholder exists for the target chat+thread:

1. Look up `message_name` in `_thinking_placeholders`.
2. Transfer ownership: remove from `_thinking_placeholders`, record in `_streaming_messages[(chat_id, thread_id)] = message_name`.
3. Call `spaces.messages.patch` with `updateMask=text`, `body.text = <chat-markdown-translated chunk>`.
4. Return `SendResult(success=True, message_id=message_name)` so the stream consumer threads subsequent edits to it.

Override `edit_message(chat_id, message_id, content, *, finalize)`:

- `finalize=False` (mid-stream): `PATCH updateMask=text` with the accumulated content translated to Chat markdown (GFM→Chat dialect).
- `finalize=True`: hand off to phase 3 routing (below).

**Rate-limit coordination.** The stream consumer default `edit_interval=1.0s` already matches Chat's 1 write/sec cap — but phase 1's `create` counts against the same budget, so the first `edit_message` after the placeholder must wait at least 1s. Simplest: in `send()` / `edit_message()`, track `_last_write_time[(chat_id, thread_id)]` and `await asyncio.sleep(...)` to maintain the gap. This also protects against back-to-back patches from the consumer's buffer-threshold-triggered flushes (40-char default).

**Overflow.** Chat's practical text-field ceiling (our `GOOGLE_CHAT_MAX_MESSAGE_LENGTH = 4096`) is enforced via the existing `truncate_message` splitter. Stream consumer already handles this — when accumulated text exceeds limit, it splits into new chunks and calls `adapter.send()` again, creating fresh messages. Our `send()` override must treat those as new messages (no placeholder lookup) since the first overflow chunk is the "spillover" and shouldn't claim the placeholder a second time.

### Phase 3 — markdown → final render

The hard question: **do we ship the final reply as plain-text (Chat markdown) or as a cardsV2 textParagraph (HTML subset)?**

Plain-text wins on:

- Inline look — renders as a normal chat message, not a bordered card widget.
- Supports mentions natively (`<users/id>` syntax).
- Supports bulleted lists (`* item`), blockquotes (`> text`) — more than originally recognised in ADR-006.
- Cheaper (one patch, no card construction).

cardsV2 wins on:

- `<ul>` / `<ol>` / `<li>` — only HTML lists render correctly; Chat markdown bullets lack nesting.
- `<font color>` — not available in plain text at all.
- `<pre><code>` blocks — identical rendering but allow language-class hooks if we ever add syntax styling.
- `<table>`-ish rendering via HTML is **not** in the supported subset either, so neither format renders real tables — ADR-006's "wrap tables in fenced code" remains the fallback for both.

**Routing decision: content-aware on finalize.**

```
def choose_final_form(accumulated_markdown) -> "text" | "cardsV2":
    if contains_any(nested_lists, font_color_directive, language_specific_code_fence):
        return "cardsV2"
    return "text"
```

Concretely, the router returns `cardsV2` when the accumulated markdown contains any of:

- A nested list (a `-` or `*` list item with >2 space indent under another list item)
- A fenced code block with a language tag (```` ```python ````, ```` ```json ````, etc.)
- Explicit HTML we want to pass through (`<b>`, `<font color>`, etc.) — the agent won't normally emit this, but the translator should preserve it if it appears

All simpler content (bold/italic/strike/inline code/bulleted lists without nesting/blockquotes/plain fenced code) stays in the text field, where Chat's native markdown dialect handles it.

**Finalize behaviour.**

- If final form is `text`: `PATCH updateMask=text`, `body.text = <final Chat-markdown>`. Trailing cursor `▉` stripped. `REQUIRES_EDIT_FINALIZE = False` is sufficient — the mid-stream edit path already delivers the bytes, so the stream consumer short-circuits.
- If final form is `cardsV2`: `PATCH updateMask=cardsV2,text`, `body.cardsV2 = [<card>]`, `body.text = ""` (clears the streamed text so the message carries only the card, assuming Chat accepts this — needs smoke-verify).
  - This is the case where `REQUIRES_EDIT_FINALIZE = True` matters: without it, the stream consumer would skip the final edit and leave the rendered message stuck in text form. Set the flag.
  - The GFM→HTML translator is a small pure function; see Translator Design below.

### Translator Design

Two translators, both pure-string:

1. **GFM → Chat markdown** (for phase 2 streaming + phase 3 text finalize). Already scoped by ADR-006 / C10–C11:
   - `**x**` → `*x*`; `*x*` / `_x_` → `_x_`; `~~x~~` → `~x~`
   - `[t](u)` → `<u|t>`
   - `# header` / `## header` / ... → `*header*` (bold — Chat has no headers)
   - `- item` / `* item` → `* item` (canonicalised — Chat accepts both but normalising keeps the stream buffer stable across edits)
   - Fenced code: unchanged (triple-backticks work)
   - Tables: wrap in fenced code (telegram precedent)

2. **GFM → card HTML subset** (for phase 3 cardsV2 finalize). New:
   - `**x**` → `<b>x</b>`; `*x*` / `_x_` → `<i>x</i>`; `~~x~~` → `<s>x</s>`
   - `[t](u)` → `<a href="u">t</a>`
   - `# h` → `<b>h</b><br>` (Chat cards also have no header semantics)
   - `- a\n  - b\n- c` → `<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>`
   - Fenced code with lang → `<pre><code>...</code></pre>` (lang hint lost — Chat doesn't render it)
   - Inline code `` `x` `` → `<code>x</code>`
   - Links, `<br>` for hard breaks, etc.
   - Tables still wrap in `<pre>` — HTML `<table>` isn't in the subset.
   - Output is wrapped in a `cardsV2` envelope with a single `textParagraph` widget. Example:
     ```json
     [{"cardId": "hermes-reply-<msgid>",
       "card": {"sections": [{"widgets": [{"textParagraph": {"text": "<...>"}}]}]}}]
     ```

Both translators are small enough (~150 lines each) to live inside `gateway/platforms/googlechat.py`. The card-HTML translator is unique to this adapter; the Chat-markdown translator overlaps with C10–C11's existing scope and can share a code path.

**Streaming and the card form.** During phase 2 we always stream in the text field — building up an HTML card incrementally would cost more state and more risk. The card form only materialises on finalize, paid for by a single final patch.

### Rate-limit accounting per turn

| Phase | Writes | Notes |
|---|---|---|
| 1 — placeholder create | 1 | Immediate. |
| 2 — progressive edits | N | Capped by stream consumer's `edit_interval=1.0s` + Chat's 1/sec quota. Worst case = one edit per second of generation. A 10s response = ~10 edits. |
| 3 — final patch | 1 | The mid-stream edit may have already delivered the final text (stream consumer short-circuits when `not REQUIRES_EDIT_FINALIZE`). The card-form path always costs one extra write. |

Total per turn: `1 + N (≤ seconds-of-generation) + 0-or-1`. At 1 write/sec that's the Chat-API-imposed limit anyway — we're not paying extra.

If the agent generates faster than 1 token/sec worth of deltas, the stream consumer coalesces via its internal buffer, so we don't hit 429.

### Interrupt / cancel during stream (inherited, no adapter work)

A second inbound message during an in-flight turn already interrupts the
running agent via `running_agent.interrupt(event.text)` in
`gateway/run.py:1536`, with the new message queued as the next turn's
input. The stream consumer's `asyncio.CancelledError` path
(`gateway/stream_consumer.py:452`) makes a best-effort final edit to strip
the cursor from the partial message. The partial stays visible; the new
turn creates a fresh placeholder → fresh streamed message.

This works on Google Chat today for DMs (every DM dispatches) and for
ROOMs whenever the second message @-mentions the bot (same R1 mention
constraint that gates all inbound in group spaces). No Cancel button,
no `/stop` registration, no adapter-side state needed — the base-class
interrupt wiring is enough.

---

## Open questions

Call these out as `NEEDS_VERIFY` at implementation time:

1. **Patch text→cardsV2 cross-form** — does a single `patch` with `updateMask=cardsV2,text` and `body.text=""` successfully replace a streamed text message with a card? Or does Chat reject the cross-form change? Fallback: delete the streamed message and create a new card message on finalize. Costs +1 write, loses message identity (thread position unchanged).
2. **Reaction / edit-metadata on streamed message** — if a user reacts to or quotes a message while we're still editing it, does `patch` succeed? (Chat's `quotedMessageMetadata` is removal-only via patch — unclear how active edits interact with user reactions.)
3. **Placeholder UX tuning** — is `💭 _Thinking…_` the right default, or should it say `MMAI` explicitly? Try both in steve-test after ship.
4. **Rate-limit headroom on rapid turns** — if a user sends two messages back-to-back in the same space, the per-space 1/sec cap covers *both* turns' writes combined. Second turn's thinking-ack may have to wait. Acceptable for DM; confirm for busy ROOM.
5. **Card width** — cardsV2 default width may feel narrower than inline text. Consider `headerImageAltText`/header to give it visual anchor, or accept default. Smoke-verify in steve-test.

---

## Sequencing — new milestone M6

All three concerns sit in one adapter file (`gateway/platforms/googlechat.py`) plus tests. The commits below slot *after* the existing C10–C14 plan (format translator + lifecycle) because:

- C10/C11 ship the GFM → Chat-markdown translator that M6's phases 2 and 3-text both depend on.
- C12 ships PLATFORM_HINTS so the agent's prompt-level output matches what Chat can actually render — reduces the translator's workload.

M6 commits (proposed IDs C29–C34):

| # | Commit | Depends on |
|---|---|---|
| C29 | `test(googlechat): thinking-ack placeholder lifecycle` | C11 |
| C30 | `feat(googlechat): send_typing/stop_typing — placeholder message, per-chat idempotent` | C29 |
| C31 | `test(googlechat): streaming edit_message — patch cadence + cursor strip + overflow split` | C30 |
| C32 | `feat(googlechat): edit_message via spaces.messages.patch + placeholder→streaming handoff in send()` | C31 |
| C33 | `test(googlechat): finalize routing — text vs cardsV2 + HTML translator` | C32 |
| C34 | `feat(googlechat): cardsV2 finalize path + REQUIRES_EDIT_FINALIZE + GFM→HTML translator` | C33 |

**DEMO #4** (after M6 lands): in `steve-test`, send a prompt likely to produce a nested list or code block. Verify:
- Placeholder appears <1s after user sends.
- Text body progressively grows (edits at ~1 Hz).
- Final message renders the nested list correctly — either as chat bullets (if simple) or as a card (if nested).

---

## Roadmap items this spec closes or creates

Closes or reshapes:
- R1 (Workspace Events API migration) — **unchanged**, independent concern.

Creates (add to `roadmap.md` after M6 lands if they prove real):
- **R2** — Syntax-highlighted code blocks in card rendering (currently lang hints are lost; cardsV2 has no code-language widget).
- **R3** — Interactive "regenerate" button on final message (cardsV2 button, clicks already route via ADR-012's CARD_CLICKED path).
- **R4** — Progressive-card streaming: build the card incrementally during phase 2 rather than swapping on finalize. Would require streaming into `cardsV2` updateMask from delta #1 and is costlier; only worth revisiting if users complain about the text→card "flash" on finalize.

---

## Out of scope for M6

- Streaming **attachments** (images, documents). Those fire via base `send_image` / `cache_*_from_bytes` and are not part of the progressive text pipeline.
- Streaming inside **cards we already sent** (the ADR-012 `send_chat_card` tool path). That's a separate concern — the agent explicitly chose a card there, not a text reply.
- Cross-turn state. Each turn owns one placeholder and one streamed message; no buffering across turns.
- Rate-limit retry shaping. The stream consumer's existing `_flood_strikes` backoff handles 429; if we see recurring issues, adjust `_PER_SPACE_QPS_DELAY_SECONDS` rather than introduce a new backoff layer.
- Changes to `send_chat_card`, `_send_googlechat`, or any cron path. Cron messages are already single-shot sends and get the GFM→Chat-markdown translator from C11 for free.

---

## References

- `gateway/platforms/base.py:1037` — `send` abstract contract.
- `gateway/platforms/base.py:1067–1094` — `edit_message` contract + `REQUIRES_EDIT_FINALIZE` semantics.
- `gateway/platforms/base.py:1096–1111` — `send_typing` / `stop_typing` contract.
- `gateway/platforms/base.py:1406–1450` — `_keep_typing` refresh loop.
- `gateway/stream_consumer.py:266–470` — `run()` — the progressive-edit driver.
- `gateway/run.py:8864` — inference-phase `send_typing` wiring.
- `gateway/platforms/dingtalk.py:149, 1006` — `REQUIRES_EDIT_FINALIZE=True` precedent + `edit_message` signature.
- `aeyeops/googlechat/adr/003-adapter-scope-io-only.md` — scope guard.
- `aeyeops/googlechat/adr/006-message-format-translate-markdown.md` — translator scope (M6 extends, doesn't replace).
- `aeyeops/googlechat/adr/012-card-v2-interface.md` — card outbound path the HTML finalize reuses in shape.
- Google Chat REST: `spaces.messages.patch`, `format-messages`, `limits` pages (fetched 2026-04-20).
