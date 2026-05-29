# Plan — Google Chat adapter

Execution plan. Organized around `gateway/platforms/ADDING_A_PLATFORM.md`.
Design phase is complete pending sign-off; Phase D (build) opens on
explicit user sign-off of the design artifacts.

Status: `[ ]` not started · `[~]` in progress · `[x]` complete

## Done

- Framing, scope, and transport/auth/event/session/markdown decisions (see `adr/`)
- `design.md` — conformance constraints, hierarchy, decided design areas, and
  sequence diagrams for inbound + cron flows
- Card interface — synthesized-TEXT inbound path for `CARD_CLICKED`,
  platform-aware `send_chat_card` tool outbound with a narrower
  `CardSpec` schema, initial widget set fixed (text, image, divider,
  button, radio/checkbox selection, submit button, card header). See
  `adr/`.

## Design phase — pending sign-off

No remaining open design items. The design phase closes on explicit
user sign-off; Phase D (build) does not open until that happens.

## Build (16 integration points)

Tracked against `gateway/platforms/ADDING_A_PLATFORM.md`. Each line is
its own atomic commit scoped `feat(googlechat):` or `test(googlechat):`
so the eventual upstream cherry-pick is mechanical.

1.  [ ] Core adapter (`gateway/platforms/googlechat.py`)
2.  [ ] Platform enum (`gateway/config.py`)
3.  [ ] Adapter factory (`gateway/run.py::_create_adapter`)
4.  [ ] Authorization maps (`gateway/run.py::_is_user_authorized`)
5.  [ ] Session source (`gateway/session.py`) — only if extra fields needed
6.  [ ] System prompt hints (`agent/prompt_builder.py::PLATFORM_HINTS`)
7.  [ ] Toolset (`toolsets.py`)
8.  [ ] Cron delivery (`cron/scheduler.py::platform_map`)
9.  [ ] Send message tool (`tools/send_message_tool.py`)
10. [ ] Cronjob tool schema (`tools/cronjob_tools.py`)
11. [ ] Channel directory (`gateway/channel_directory.py`)
12. [ ] Status display (`hermes_cli/status.py`)
13. [ ] Gateway setup wizard (`hermes_cli/gateway.py::_PLATFORMS`)
14. [ ] ID redaction (`agent/redact.py`) — if sensitive IDs
15. [ ] Documentation — deferred; upstream docs are out of scope until PR
16. [ ] Tests (`tests/gateway/test_googlechat.py`)

## Dogfood

Run the fork as our day-to-day hermes-agent, collect rough edges, iterate
until we're comfortable. Then prepare the upstream PR — a cherry-pick of
the build commits, with fork-only content (`aeyeops/`, `CLAUDE.md`)
staying out of the PR.
