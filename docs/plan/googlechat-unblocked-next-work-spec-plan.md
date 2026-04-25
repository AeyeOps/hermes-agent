# Implementation Plan: Google Chat Unblocked Next Work Spec

## Spec Source

docs/roadmap/googlechat-unblocked-next-work-spec.md

## Plan Digest

### Goals

- Shape Google Chat agent output with a platform-specific prompt hint.
- Ship Google Chat Card v2 send support through the `hermes-googlechat` toolset.
- Route `CARD_CLICKED` events into ordinary agent turns without pending-card state.
- Complete unblocked operator polish for cron targeting, redaction, status, and setup visibility.
- Add text-first streaming after cards.

### Non-goals

- Workspace administrator approval, Marketplace scope changes, or tenant configuration.
- Live enablement for admin-gated R1/R2b/R3 capabilities.
- Generic cross-platform cards or a runtime-active-platform tool gating framework.
- `cardsV2` streaming finalization in the first streaming pass unless later planning proves it low-risk.
- Upstream website/public documentation changes.

### Dependencies

- Existing Google Chat adapter send path and Chat REST client setup.
- Existing `hermes-googlechat` toolset.
- Existing `MessageEvent`, `BasePlatformAdapter`, `send_typing`, `stop_typing`, `edit_message`, and `REQUIRES_EDIT_FINALIZE` contracts.
- Existing `_send_googlechat` text delivery helper for cron delivery.
- ADR-012 allowed Card v2 widget subset.

### Risks

- The unresolved `CARD_CLICKED` text format can block implementation of click synthesis.
- Streaming can duplicate messages or hit Chat write-rate limits if edit cadence is not conservative.
- Operator UI can accidentally imply admin-gated features are enabled.
- Redaction patterns can overmatch ordinary text if they are too broad.
- Project policy currently avoids adding automated tests unless the user explicitly asks for tests.

## M1 - Platform Hint And Card Send Surface

### Objective

Add the platform hint and Google Chat-specific card sending path without introducing generic card abstractions or dynamic tool gating.

### REQ coverage

- REQ-001
- REQ-002
- REQ-003
- REQ-004
- REQ-005

### Tasks

- [x] T-001 Add a `googlechat` entry to `PLATFORM_HINTS` describing Chat markdown, media delivery expectations, and card availability.
- [x] T-002 Create `tools/send_chat_card_tool.py` with a Google Chat-specific `CardSpec` for the ADR-012 allowed widget subset.
- [x] T-003 Implement `CardSpec` validation for allowed widgets, widget ordering, required submit buttons for selectable inputs, and unsupported-widget rejection.
- [x] T-004 Implement `CardSpec` to `cardsV2` translation, reusing the Google Chat markdown translator for text widgets where applicable.
- [x] T-005 Implement the `send_chat_card` handler using the existing Google Chat REST client and `spaces.messages.create`.
- [x] T-006 Register `send_chat_card` in `hermes-googlechat` only; do not add a new runtime-active-platform gating system.
- [x] T-007 Ensure validation failures return before any Chat API call and Chat REST failures return structured errors.

### Verification

- [ ] Manual: confirm a Google Chat toolset session exposes `send_chat_card`.
- [ ] Manual: confirm a generic/non-Google Chat toolset does not expose `send_chat_card`.
- [ ] Manual: send a simple card to a configured Google Chat DM or space and confirm Chat renders it.
- [ ] Manual: attempt an unsupported widget and confirm the tool returns a clear validation error before sending.
- [ ] Contract/manual: inspect the generated `cardsV2` payload for preserved widget order, header fields, submit buttons, and action parameters.
- [ ] Automated: no new automated tests unless the user explicitly requests tests.

### Rollback/flag notes

- Roll back by removing the toolset entry and card tool file if card sending causes issues.
- No new feature flag is required because exposure is scoped to `hermes-googlechat`.

## M2 - Card Click Synthesis

### Objective

Convert Google Chat `CARD_CLICKED` events into restart-tolerant `MessageEvent(TEXT)` turns after the synthesized text format is settled.

### REQ coverage

- REQ-006
- REQ-007

### Tasks

- [x] T-008 Resolve OQ-001 by choosing the exact synthesized text format for action name, action parameters, and form selections.
- [x] T-009 Implement a payload normalizer for `CARD_CLICKED` action method name, action parameters, and `common.formInputs` selections.
- [x] T-010 Replace the current `CARD_CLICKED` TODO branch in `gateway/platforms/googlechat.py` with synthesis into `MessageEvent(TEXT)`.
- [x] T-011 Route synthesized click events through the existing `handle_message` path.
- [x] T-012 Ensure missing optional click fields degrade to a useful standalone synthesized message when enough action context exists.
- [x] T-013 Ensure click handling does not require or create pending-card state.
- [x] T-014 Add non-sensitive logs that identify card-click handling and synthesized action context without dumping raw payloads.

### Verification

- [ ] Manual: send a card, click it, and confirm the next agent turn sees the synthesized action context.
- [ ] Manual: replay or trigger a click without pending adapter state and confirm the gateway does not crash.
- [ ] Manual: confirm logs identify `CARD_CLICKED` handling without raw sensitive payload dumps.
- [ ] Contract/manual: inspect synthesized text for the approved OQ-001 format.
- [ ] Automated: no new automated tests unless the user explicitly requests tests.

### Rollback/flag notes

- If click synthesis misroutes user actions, revert the `CARD_CLICKED` branch to non-dispatch logging while keeping card send support.

## M3 - Unblocked Operator Polish

### Objective

Complete non-admin-gated platform polish for cron targeting, redaction, status, and setup visibility.

### REQ coverage

- REQ-008
- REQ-009
- REQ-010

### Tasks

- [x] T-015 Add Google Chat target awareness to cron platform mapping where missing.
- [x] T-016 Add Google Chat target schema/help text for `googlechat:spaces/...` cron delivery where missing.
- [x] T-017 Keep cron delivery text-only through `_send_googlechat`; do not add card support to cron.
- [x] T-018 Add redaction patterns for `spaces/...`, numeric `users/...`, and `messages/...` resource names.
- [x] T-019 Add Google Chat service-account/home-channel visibility to CLI status surfaces in the same style as other platforms.
- [x] T-020 Add Google Chat setup wizard visibility for local config needs without automating or implying Workspace admin approval.

### Verification

- [ ] Manual: configure a `googlechat:spaces/...` cron target and confirm scheduled text delivery reaches the target.
- [ ] Manual: run representative text containing Chat resource IDs through redaction and confirm only identifiers are scrubbed.
- [ ] Manual: inspect CLI status/setup surfaces and confirm Google Chat configuration is visible and does not imply admin-gated capabilities are enabled.
- [ ] Automated: no new automated tests unless the user explicitly requests tests.

### Rollback/flag notes

- Cron/status/setup additions are list-style integrations and can be reverted independently.
- Redaction changes should be reverted if they overmatch ordinary user text.

## M4 - Text-First Streaming

### Objective

Implement Google Chat placeholder acknowledgement and progressive text edits after card support ships.

### REQ coverage

- REQ-011
- REQ-012
- REQ-013

### Tasks

- [x] T-021 Implement a minimal placeholder acknowledgement path using the existing adapter typing contracts.
- [x] T-022 Track per-turn placeholder/message state without cross-turn persistence.
- [x] T-023 Implement progressive text edits through Chat message patch/edit behavior if confirmed during implementation.
- [x] T-024 Enforce a conservative per-space write cadence for placeholder and edit calls.
- [x] T-025 Ensure final content does not duplicate streamed content.
- [x] T-026 Keep first-pass finalization as Chat markdown text.
- [x] T-027 Treat `cardsV2` finalization as a later optional sub-scope unless implementation proves it low-risk.
- [x] T-028 Add logs for placeholder creation, edit attempts, rate-limit handling, and finalization.

### Verification

- [ ] Manual: run a long Google Chat response and confirm a placeholder appears quickly.
- [ ] Manual: confirm text progressively updates at a safe cadence.
- [ ] Manual: confirm final content is not duplicated.
- [ ] Manual: confirm rate-limit failures are absent or handled without repeated avoidable retries.
- [ ] Automated: no new automated tests unless the user explicitly requests tests.

### Rollback/flag notes

- If streaming causes duplicate or rate-limited messages, roll back to single-shot `send()` behavior.
- `cardsV2` finalization remains out of the first streaming rollback path.

## M5 - Roadmap Cleanup

### Objective

Update fork-local roadmap status after implementation so stale Google Chat planning text does not mislead future sessions.

### REQ coverage

- REQ-014

### Tasks

- [x] T-029 Review `aeyeops/googlechat/roadmap.md` and related fork-local docs for stale statuses after implementation.
- [x] T-030 Update only fork-local roadmap/status docs needed to reflect implemented work.
- [x] T-031 Leave upstream website/public docs unchanged.

### Verification

- [ ] Manual: read the updated roadmap docs and confirm statuses match implemented behavior.
- [ ] Manual: confirm no upstream public documentation files were changed.

### Rollback/flag notes

- Documentation cleanup can be reverted independently from implementation changes.

## Open Questions

1. OQ-001 resolved 2026-04-25: synthesized `CARD_CLICKED` text is a deterministic plain-text envelope with a heading, action line, sorted parameters, and sorted selections.

## Definition of Done

- All Must requirements REQ-001 through REQ-007 are implemented or explicitly deferred with user approval.
- Should requirements REQ-008 through REQ-013 are implemented or explicitly deferred with user approval.
- `send_chat_card` is available only through `hermes-googlechat`.
- Card send and card click paths work in a real Google Chat DM or space.
- Streaming, if implemented in this pass, is text-first and verified manually for placeholder timing, edit cadence, and non-duplicated final output.
- Operator polish does not imply admin-gated capabilities are enabled.
- Verification items listed in each completed milestone have been run and recorded.
- No automated tests are added unless the user explicitly requests tests.
