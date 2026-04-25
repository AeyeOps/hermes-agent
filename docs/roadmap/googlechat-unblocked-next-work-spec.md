# Google Chat Unblocked Next Work Spec

## Status

Implemented locally pending live Google Chat verification - 2026-04-25

## PRD Source

docs/roadmap/googlechat-unblocked-next-work-prd.md

## Goals

- Add Google Chat platform prompting so agent output is shaped for Chat before larger features land.
- Implement Google Chat Card v2 send and click handling without requiring Workspace administrator approval.
- Complete unblocked Google Chat platform polish for cron targeting, redaction, status, and setup visibility.
- Implement Google Chat streaming as a text-first improvement after cards ship.

## Non-goals / Out of scope

- Workspace administrator approval, Marketplace scope updates, or tenant-side configuration.
- Live enablement for R1 mention-free group-space delivery, R2b native upload, or R3 lifecycle reactions when those depend on approved `chat.app.*` scopes.
- A generic cross-platform card abstraction.
- A new runtime-active-platform tool gating framework.
- `cardsV2` streaming/finalization as part of the first streaming scope unless implementation planning proves it is low-risk.
- Upstream website or public documentation changes.

## Assumptions & Constraints

- `send_chat_card` is exposed through the existing `hermes-googlechat` toolset only.
- Card send/click support ships before streaming.
- Streaming starts as placeholder plus progressive text edits.
- `cardsV2` finalization remains optional unless the implementation plan proves it is low-risk after text streaming works.
- Card-click handling must not depend on in-memory pending-card state.
- Administrator-gated paths must remain fail-closed.
- Automated test additions require explicit user approval under the project testing policy; verification planning should favor concrete manual checks unless tests are explicitly requested.

## Requirements

| REQ-ID | Priority | Requirement | Acceptance Criteria | Notes |
|---|---|---|---|---|
| REQ-001 | Must | Add a Google Chat platform prompt hint. | When the active platform is `googlechat`, the prompt builder exposes guidance for Chat markdown formatting, media delivery expectations, and card availability. | Add only a Google Chat hint; do not alter unrelated platform hints. |
| REQ-002 | Must | Implement a constrained `CardSpec` model for Google Chat Card v2 output. | A card can be represented with only the ADR-012 allowed widgets, and unsupported widgets are rejected before any outbound API call is attempted. | Keep this Google Chat-specific. |
| REQ-003 | Must | Translate valid `CardSpec` values into Google Chat `cardsV2` message payloads. | A valid card spec produces a Chat API-compatible `cardsV2` body preserving widget order, header data, submit buttons, and action parameters. | Reuse the existing Google Chat markdown translator where text widgets need formatting. |
| REQ-004 | Must | Add a `send_chat_card` tool for Google Chat. | The tool sends a `cardsV2` message through the existing Chat REST client and returns a structured success/error result including the created message name when available. | Tool schema should be narrow and default-heavy. |
| REQ-005 | Must | Register `send_chat_card` only for the Google Chat toolset/runtime context. | The tool is available through `hermes-googlechat` and is not added to generic/non-Google Chat toolsets. | Resolved decision: no new dynamic runtime-active-platform gating system. |
| REQ-006 | Must | Synthesize `CARD_CLICKED` events into normal text `MessageEvent`s. | A click event with action parameters and form inputs creates a `MessageEvent(message_type=TEXT)` whose text clearly captures the clicked action and selected values, then routes through the existing agent handling path. | Format resolved as deterministic plain text: heading, action, parameters, selections. |
| REQ-007 | Must | Keep card-click handling restart-tolerant. | A click event that arrives without adapter-side pending-card state is still handled as a standalone event and does not crash or require hidden in-memory card registry state. | Preserve ADR-012's no-pending-card-registry decision. |
| REQ-008 | Should | Add Google Chat cron delivery wiring where still missing. | Cron platform mapping and cronjob tool schema allow `googlechat` targets using `spaces/...` chat IDs, and existing `_send_googlechat` handles delivery. | Use existing `_send_googlechat`; do not add card support to cron. |
| REQ-009 | Should | Add Google Chat ID redaction patterns. | Logs/redaction cover `spaces/...`, `users/<numeric-id>`, and `messages/...` resource names without corrupting ordinary text. | Redaction should be specific enough to avoid broad false positives. |
| REQ-010 | Should | Add Google Chat status/setup wizard visibility. | CLI status and gateway setup surfaces include Google Chat service-account/home-channel configuration in the same style as other gateway platforms. | Surface existing config needs; do not automate admin approval. |
| REQ-011 | Should | Implement Google Chat thinking acknowledgement. | For a long-running agent turn, Google Chat receives a placeholder/ack message quickly and clears or transitions it when the final response path begins. | First streaming scope is text-first. |
| REQ-012 | Should | Implement progressive text edits for Google Chat streaming. | Streaming responses update an existing Chat message at a cadence compatible with the per-space write limit, and final content does not duplicate the streamed content. | Use Chat message patch behavior if confirmed during implementation. |
| REQ-013 | Should | Add finalization routing for simple text vs card-rendered complex output. | Simple content finalizes as Chat markdown text; content that needs richer rendering can finalize through `cardsV2` without breaking existing send behavior. | Optional later sub-scope unless planning proves it low-risk. |
| REQ-014 | Could | Add roadmap/doc cleanup after implementation. | Stale `aeyeops/googlechat/roadmap.md` statuses are updated after the implementation lands, without changing upstream docs. | Documentation cleanup should follow implementation. |

## Design / Architecture

### Components & boundaries

- `agent/prompt_builder.py`: add a `googlechat` platform hint for Chat markdown, media expectations, and card availability.
- `tools/send_chat_card_tool.py`: own the Google Chat `CardSpec`, validation, `cardsV2` translation, and tool handler.
- `toolsets.py`: expose `send_chat_card` through `hermes-googlechat` only.
- `gateway/platforms/googlechat.py`: handle `CARD_CLICKED` by synthesizing a normal text `MessageEvent`, and later add text-first streaming methods.
- `cron/scheduler.py` and `tools/cronjob_tools.py`: add Google Chat target awareness where still missing, using the existing text delivery path.
- `agent/redact.py`: add resource-name redaction for Google Chat identifiers.
- `hermes_cli/status.py` and `hermes_cli/gateway.py`: add Google Chat visibility in operator-facing setup/status surfaces.

### Data model / state

- `CardSpec` is a Google Chat-specific structured input model for the allowed ADR-012 Card v2 subset.
- `CardSpec` must preserve widget order and action parameters so the outbound card and inbound click can be correlated through visible card payload data.
- `CARD_CLICKED` handling must not require persistent or in-memory pending-card state.
- Streaming state should be per turn and per target Chat message only; no cross-turn state is introduced.

### API / integration contracts

- `send_chat_card` accepts a constrained card specification, target `spaces/...` chat ID, and optional thread metadata if supported by the existing send path.
- `send_chat_card` posts a Chat API `spaces.messages.create` request containing `cardsV2`.
- `CARD_CLICKED` events are normalized into `MessageEvent(TEXT)` and passed through the same `handle_message` path as user text.
- Cron Google Chat delivery continues to use `_send_googlechat` and text messages only.
- Text streaming uses placeholder send plus message patch/edit behavior compatible with the base adapter's `send_typing`, `stop_typing`, `edit_message`, and `REQUIRES_EDIT_FINALIZE` contracts.

### Error handling / timeouts / retries

- Card validation errors return before any Chat API call.
- Chat REST failures from `send_chat_card` return structured errors without crashing the gateway.
- `CARD_CLICKED` payloads missing optional fields should still produce a useful standalone synthesized message when enough action context exists.
- Streaming must honor the per-space write cadence to avoid avoidable rate-limit errors.
- Administrator-gated features remain disabled unless the existing approval flag is set; this spec does not expand that gate.

## Rollout / Migration

1. Ship platform hint and card support first.
2. Add unblocked operator polish after card support or in parallel where files do not overlap.
3. Implement text-first streaming after cards.
4. Treat `cardsV2` streaming finalization as a later optional scope.
5. Update fork-local roadmap docs after implementation if stale status text remains.

## Observability

- Healthy card send: tool result reports success and the created Chat message name.
- Healthy card click: gateway logs identify the `CARD_CLICKED` event type and the synthesized action context without dumping raw sensitive payloads.
- Healthy streaming: logs show placeholder creation, edit attempts, and finalization without repeated avoidable rate-limit failures.
- Healthy operator polish: status/setup surfaces show Google Chat configuration consistently with other gateway platforms.
- Redaction is healthy when Chat resource IDs are scrubbed from logs while preserving enough context to debug platform behavior.

## Verification Strategy

- Manual/card: use a configured Google Chat space or DM to send a card, click it, and confirm the next agent turn receives the synthesized action context.
- Manual/toolset: start a Google Chat toolset session and confirm `send_chat_card` is available; confirm generic/non-Google Chat toolsets do not expose it.
- Manual/cron: configure a `googlechat:spaces/...` target and confirm a scheduled text delivery reaches the target.
- Manual/redaction: run representative log text containing `spaces/...`, `users/<numeric-id>`, and `messages/...` through redaction and confirm only identifiers are scrubbed.
- Manual/streaming: run a long response and confirm placeholder creation, progressive text edits, and non-duplicated final content.
- Automated checks may be added only if explicitly requested by the user.

## Risks & Mitigations

- Risk: a generic card abstraction grows out of a Google Chat-only need. Mitigation: keep `CardSpec` and tool naming Google Chat-specific.
- Risk: card clicks are hard for the agent to interpret. Mitigation: settle the synthesized text format before implementation.
- Risk: streaming creates duplicate or rate-limited messages. Mitigation: ship text-first and enforce write cadence before considering card finalization.
- Risk: operator UI implies admin-gated features are available. Mitigation: setup/status text should distinguish local config from Workspace admin approval.
- Risk: redaction patterns overmatch ordinary text. Mitigation: target Google Chat resource-name shapes specifically.

## Open Questions

Resolved 2026-04-25:

1. `CARD_CLICKED` messages use deterministic plain text:
   - `Google Chat card click`
   - `action: <actionMethodName>`
   - optional sorted `parameters:` lines
   - optional sorted `selections:` lines

This keeps the agent-facing context readable without dumping the raw Chat payload.

## Next Step

Derive a plan via aeodlc-derive-plan docs/roadmap/googlechat-unblocked-next-work-spec.md
