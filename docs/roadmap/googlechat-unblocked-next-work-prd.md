# Google Chat Unblocked Next Work PRD

## Status

Draft (roadmap) — 2026-04-25

## Summary

Define the next Google Chat platform work that can proceed without Workspace administrator approval: platform prompting, Card v2 send/click support, unblocked operational polish, and streaming/thinking-ack behavior.

## Background / Problem

The Google Chat adapter has core message handling, send support, Workspace Events scaffolding, media hydration/upload gates, and lifecycle reaction gates. Workspace administrator approval is not currently available, so work that depends on `chat.app.*` tenant approval should not be the next implementation focus. The remaining unblocked backlog contains user-facing and operator-facing gaps: the agent is not prompted with Google Chat-specific output guidance, Card v2 interactions are planned but not implemented, several platform polish items are missing, and streaming behavior is still draft/spec-only.

## Goals

- Make Google Chat responses better shaped for the platform before adding larger features.
- Add Card v2 outbound and `CARD_CLICKED` inbound behavior that works through existing Chat app event delivery.
- Complete unblocked platform polish for cron targeting, redaction, status, and setup visibility.
- Implement streaming/thinking-ack behavior that improves long-running response UX without requiring administrator approval.

## Non-goals / Out of scope

- Workspace administrator approval, Marketplace scope changes, or tenant-side configuration.
- R1 live mention-free group-space delivery through Workspace Events beyond already-gated code paths.
- R2b real native upload enablement when it depends on approved `chat.app.messages` scope.
- R3 real lifecycle reactions when they depend on approved `chat.app.messages` scope.
- New broad platform abstractions unless required by the existing Google Chat work.
- Upstream website or public documentation changes.

## Users / Personas

- Google Chat end users interacting with Hermes in DMs or spaces.
- Hermes operators configuring and verifying the Google Chat gateway.
- Hermes maintainers reviewing the Google Chat adapter for upstreamability.

## Requirements

| REQ-ID | Priority | Requirement | Acceptance Criteria | Notes |
|---|---|---|---|---|
| REQ-001 | Must | Add a Google Chat platform prompt hint. | When the active platform is `googlechat`, the prompt builder exposes guidance for Chat markdown formatting, media delivery expectations, and card availability. | Corresponds to build-plan C12. |
| REQ-002 | Must | Implement a constrained `CardSpec` model for Google Chat Card v2 output. | A card can be represented with only the ADR-012 allowed widgets, and unsupported widgets are rejected before any outbound API call is attempted. | Allowed widgets come from ADR-012/build-plan M4. |
| REQ-003 | Must | Translate valid `CardSpec` values into Google Chat `cardsV2` message payloads. | A valid card spec produces a Chat API-compatible `cardsV2` body preserving widget order, header data, submit buttons, and action parameters. | Reuse the existing Google Chat markdown translator where text widgets need formatting. |
| REQ-004 | Must | Add a `send_chat_card` tool for Google Chat. | The tool sends a `cardsV2` message through the existing Chat REST client and returns a structured success/error result including the created message name when available. | Should be scoped to Google Chat, not a generic card API. |
| REQ-005 | Must | Register `send_chat_card` only for the Google Chat toolset/runtime context. | The tool is available when Google Chat is enabled and absent from non-Google Chat toolsets. | The current build plan calls for a conditional registration pattern if needed. |
| REQ-006 | Must | Synthesize `CARD_CLICKED` events into normal text `MessageEvent`s. | A click event with action parameters and form inputs creates a `MessageEvent(message_type=TEXT)` whose text clearly captures the clicked action and selected values, then routes through the existing agent handling path. | Existing code currently logs this as a TODO. |
| REQ-007 | Must | Keep card-click handling restart-tolerant. | A click event that arrives without adapter-side pending-card state is still handled as a standalone event and does not crash or require hidden in-memory card registry state. | Aligns with ADR-012 no pending-card registry decision. |
| REQ-008 | Should | Add Google Chat cron delivery wiring where still missing. | Cron platform mapping and cronjob tool schema allow `googlechat` targets using `spaces/...` chat IDs, and existing `_send_googlechat` handles delivery. | This is unblocked polish from item 3. |
| REQ-009 | Should | Add Google Chat ID redaction patterns. | Logs/redaction cover `spaces/...`, `users/<numeric-id>`, and `messages/...` resource names without corrupting ordinary text. | Corresponds to build-plan C27. |
| REQ-010 | Should | Add Google Chat status/setup wizard visibility. | CLI status and gateway setup surfaces include Google Chat service-account/home-channel configuration in the same style as other gateway platforms. | Corresponds to build-plan C28. |
| REQ-011 | Should | Implement Google Chat thinking acknowledgement. | For a long-running agent turn, Google Chat receives a placeholder/ack message quickly and clears or transitions it when the final response path begins. | From M6 streaming spec; exact text should stay minimal. |
| REQ-012 | Should | Implement progressive text edits for Google Chat streaming. | Streaming responses update an existing Chat message at a cadence compatible with the per-space write limit, and final content does not duplicate the streamed content. | Uses `spaces.messages.patch` per M6 spec if confirmed in implementation. |
| REQ-013 | Should | Add finalization routing for simple text vs card-rendered complex output. | Simple content finalizes as Chat markdown text; content that needs richer rendering can finalize through `cardsV2` without breaking existing send behavior. | Depends on the M6 finalize design and base `REQUIRES_EDIT_FINALIZE` semantics. |
| REQ-014 | Could | Add roadmap/doc cleanup after implementation. | Stale `aeyeops/googlechat/roadmap.md` statuses are updated after the implementation lands, without changing upstream docs. | Keep this separate from feature implementation if it grows. |

## Risks / Constraints

- Administrator-gated features must remain fail-closed and should not be treated as available during this work.
- Card support should not create a broad generic card abstraction without a concrete cross-platform requirement.
- Streaming must respect Google Chat's per-space write cadence to avoid avoidable API errors.
- `docs/roadmap` PRDs describe unimplemented work; implementation details should be promoted to a spec before code changes.
- Existing project testing policy in AGENTS.md limits test creation unless explicitly requested.

## Open Questions

1. Resolved 2026-04-25: synthesized `CARD_CLICKED` messages use deterministic plain text with a heading, action line, sorted parameters, and sorted selections.

## Decisions

- `send_chat_card` is exposed through the `hermes-googlechat` toolset only. This work does not introduce a runtime-active-platform gating system.
- M4 card send/click support comes first. M6 should initially target placeholder plus progressive text edits. `cardsV2` finalization remains optional unless the implementation plan proves it is low-risk after text streaming works.

## Next Step

Promote to spec via aeodlc-prd-to-spec docs/roadmap/googlechat-unblocked-next-work-prd.md
