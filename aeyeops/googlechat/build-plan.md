# Phase D Build Plan — Google Chat Adapter (AeyeOps/hermes-agent fork)

> **On approval, first implementing action is `cp <plan-source>
> /opt/dev/aeo/hermes-agent/aeyeops/googlechat/build-plan.md`** — the system plan-mode gate
> only permits editing the temp plan file; the in-repo deliverable lands as the first commit
> of M0 (`docs(googlechat): land Phase D build plan`).

---

## Context

The 12 ADRs in `aeyeops/googlechat/adr/` are settled, `design.md` resolves the 16
upstream-checklist obligations, and `plan.md` enumerates the integration points. This
plan sequences those obligations into atomic conventional commits so the eventual
upstream cherry-pick is mechanical, parallelizes work where ADR boundaries permit it,
and identifies the shortest path to a live bot reply in `spaces/AAQA2N6jyoA`
(`steve-test`) via the provisioned `chat-events-sub` pull subscription.

The repository is a clean slate for Google Chat: `gateway/platforms/googlechat.py`
does not exist, `Platform.GOOGLECHAT` is not in the enum, no run.py / scheduler /
toolset / CLI / redact wiring references it, and `tests/gateway/test_googlechat*.py`
does not exist. Branch `feat/googlechat-main` is clean. `google-cloud-pubsub` and
`google-api-python-client` are not yet declared dependencies.

## Hard Constraints (from CLAUDE.md, ADRs, user brief)

- Tests-first per integration point. Mocked-integration where possible
  (`tests/gateway/test_*.py` pattern); live GCP only at end-of-milestone transport-loop
  verification.
- No upstream doc edits (`AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `docs/`).
- No refactors of other platform adapters (`slack.py`, `discord.py`, `telegram.py`,
  …) — they are read-only references for conventions per ADR-002.
- No new fields on `MessageEvent` / `SendResult` / `BasePlatformAdapter` per ADR-003.
- Subclass `BasePlatformAdapter` from scratch — do NOT fork another adapter
  (ADR-002).
- Single file `gateway/platforms/googlechat.py`; only escalate to package
  (`googlechat/`) if helpers grow ≥ qqbot scale, which the design does not
  anticipate.
- Adapter scope is I/O only (ADR-003): connect, receive+normalize, dispatch via
  `self.handle_message()`, send. Nothing else.
- Use shared helpers — `MessageDeduplicator`, `cache_*_from_bytes`,
  `resolve_channel_prompt`, `truncate_message`, `build_source`, `_send_with_retry`,
  `safe_url_for_log`, `SUPPORTED_DOCUMENT_TYPES` — never reinvent them.
- All adapter commits scoped `feat(googlechat):` / `fix(googlechat):` /
  `test(googlechat):` / `chore(googlechat):` / `docs(googlechat):`.
- Direct commits on `feat/googlechat-main` (no PRs); rebases onto `origin/main`
  expected.
- Card-interface contract is locked to ADR-012: synthesized `MessageEvent(TEXT)` for
  inbound `CARD_CLICKED`; outbound via new `send_chat_card` platform-local tool with
  narrower pydantic `CardSpec` (registered only when `googlechat` is in active
  platforms). Allowed widgets: `textParagraph`, `image`, `divider`,
  `button`/`buttonList`, `selectionInput · RADIO_BUTTON`,
  `selectionInput · CHECK_BOX`, submit `button` (required when selectionInput is
  used), `card.header`. Anything else needs a follow-up ADR.

## Out of Scope (do NOT plan tasks for these)

| Item | Governing ADR | Disposition |
|---|---|---|
| UC-38 HTTP transport | ADR-007 | Permanently OOS |
| UC-39 DWD auth | ADR-008 | OOS for initial adapter |
| UC-40 Per-user OAuth | ADR-008 | OOS for initial adapter |
| UC-41 Consumer Chat | ADR-011 | OOS; separate module if pursued |
| Cron-delivered cards | ADR-012 | Cron stays text-only via `_send_googlechat` |
| Raw Card v2 passthrough | ADR-012 | Rejected; `CardSpec` only |
| `textInput` / `dateTimePicker` / `decoratedText` / `grid` / `columns` / dialogs / `DROPDOWN` / `SWITCH` widgets | ADR-012 | Each needs a follow-up ADR |
| Streaming/in-progress card edits (`REQUIRES_EDIT_FINALIZE`) | n/a | Not in design |
| `aeyeops/googlechat/` content beyond `build-plan.md` | fork-only | Already complete |
| Refactoring other adapters | CLAUDE.md `<rebase-discipline>` | Widens rebase surface |

## Live Infrastructure (already provisioned — do NOT re-create)

```
project_id      sa-mm-gchatbot                  (parent: moodmedia.com org)
topic           projects/sa-mm-gchatbot/topics/chat-events
subscription    projects/sa-mm-gchatbot/subscriptions/chat-events-sub
service_account mood-media-assistant@sa-mm-gchatbot.iam.gserviceaccount.com
key_path        ~/.mood-media-assistant/key.json   (chmod 600, parent dir 0700)
chat_app        "Mood Media Assistant"            (installed in spaces/AAQA2N6jyoA = steve-test)
auth_tools      gcloud (~/google-cloud-sdk/bin/gcloud) as steve.antonakakis@moodmedia.com w/ ADC
                workspace-mm MCP (read-only Workspace/Chat against moodmedia tenant)
```

`chat-api-push@system.gserviceaccount.com` already has `roles/pubsub.publisher` on the
topic; `mood-media-assistant` SA has `roles/pubsub.subscriber` on the subscription. IAM
wiring is preserved across the prior rename. Nothing in `check_googlechat_requirements()`
should automate this — ADR-008 makes it operator-side one-time setup.

---

## Milestone Overview

```
M0 Bootstrap        ──► M1 Demo path ──► M2 Formatting/lifecycle ──► (parallel-ready)
                                    └──► M3 Cron + cross-platform send
                                    └──► M4 Cards
                                    └──► M5 Operational polish
```

M0 → M1 is strictly serial — every later milestone imports `Platform.GOOGLECHAT`
and the adapter class. Once M1 lands and the first live reply is verified, M2/M3/M4/M5
parallelize across worktrees because they touch disjoint files (the only shared file
is `gateway/platforms/googlechat.py`, and its M2/M4/M5 changes are sufficiently
non-overlapping that sequential rebases stay clean).

---

## M0 — Bootstrap (3 commits: 1 docs + 2 functional)

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C0 | `docs(googlechat): land Phase D build plan` | `aeyeops/googlechat/build-plan.md` | `diff <plan-source> aeyeops/googlechat/build-plan.md` returns empty | no |
| C1 | `chore(googlechat): add google-cloud-pubsub + google-api-python-client deps` | `pyproject.toml` (and lockfile if `uv` workflow) | `python -c "import google.cloud.pubsub_v1, googleapiclient.discovery"` after install | no |
| C2 | `feat(googlechat): add GOOGLECHAT to Platform enum + env loader + connected-platforms clause` | `gateway/config.py:48-69` (enum — append `GOOGLECHAT = "googlechat"` after `QQBOT`), `gateway/config.py:825-…` (`_apply_env_overrides` — load `GOOGLECHAT_SERVICE_ACCOUNT_JSON`, `GOOGLECHAT_PUBSUB_PROJECT`, `GOOGLECHAT_PUBSUB_SUBSCRIPTION`, and **`GOOGLECHAT_HOME_CHANNEL`** into `PlatformConfig.extra`; `GOOGLECHAT_HOME_CHANNEL` is the default `spaces/...` resource name for cron/notification delivery, matching the `TELEGRAM_HOME_CHANNEL` / `DISCORD_HOME_CHANNEL` convention in `hermes_cli/status.py:306-311`), `gateway/config.py:268-320` (`get_connected_platforms` — add `elif platform == Platform.GOOGLECHAT and config.extra.get("service_account_json")` clause; verified by direct read that this dispatch is hand-maintained per platform with `extra.get(...)` checks for non-token-auth platforms — GOOGLECHAT auth is service-account JSON, not bot token, so it MUST have an explicit branch like `BLUEBUBBLES`/`FEISHU`/`QQBOT`) | `tests/gateway/test_googlechat_config.py` — enum membership + env→PlatformConfig round-trip (covering all four `GOOGLECHAT_*` vars including the home channel) + `get_connected_platforms()` includes GOOGLECHAT when key present, excludes when absent | no |

**Why two functional commits, not one:** C1 lets reviewers (and the upstream
cherry-pick reviewer) see dependency churn separately from logic. C2 is the
single one-liner that unblocks every later import; isolating it makes the diff
trivially auditable.

Reviewers checking ADR-007 should see C1's deps and confirm
`google-cloud-pubsub` is the only new transport library — no `aiohttp`, no
HTTP server.

---

## M1 — Demo Path (7 commits, strictly serial)

Goal: bot replies in `steve-test` from a live message arriving via
`chat-events-sub`. End of milestone is the first end-to-end smoke test.

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C3 | `test(googlechat): adapter scaffolding (constructor + check_googlechat_requirements)` | `tests/gateway/test_googlechat.py` (new) | constructor wires `Platform.GOOGLECHAT`; `check_googlechat_requirements()` returns False when import patched out, True otherwise | no |
| C4 | `feat(googlechat): GoogleChatAdapter skeleton` | `gateway/platforms/googlechat.py` (new): module-level import guards (`GOOGLECHAT_AVAILABLE` flag + `check_googlechat_requirements()`), `GoogleChatAdapter(BasePlatformAdapter)` with `__init__`, no-op `connect`/`disconnect` calling `_acquire_platform_lock` / `_release_platform_lock`, abstract method stubs that raise `NotImplementedError` | tests from C3 pass | no |
| C5 | `test(googlechat): inbound MESSAGE → MessageEvent mapping` | `tests/gateway/test_googlechat_inbound.py` (new) | feed raw Pub/Sub message dict (inline fixture, capture pattern below); patch `adapter.handle_message = AsyncMock()`; assert `MessageEvent.text`, `.message_id`, `.source.chat_id`, `.source.thread_id`, `.source.user_id`, `.message_type=TEXT`; assert dedup via `MessageDeduplicator`; assert self-message filter against `self._bot_user_id` | no |
| C6 | `feat(googlechat): inbound MESSAGE handling (Pub/Sub pull loop + normalization)` | `gateway/platforms/googlechat.py`: `_pubsub_pull_loop()` background task spawned in `connect()`, `_handle_chat_event(payload)` if/elif on `event["type"]`, MESSAGE branch builds `SessionSource` via `self.build_source(...)`, dedup key = `event["message"]["name"]`, self-filter on `event["message"]["sender"]["name"]`, `resolve_channel_prompt(self.config.extra, space_name, None)`, `await self.handle_message(event)`, ack on success. Helpers: `_dedup = MessageDeduplicator()`, `_bot_user_id` resolved at connect time. ADDED/REMOVED/CARD_CLICKED branches are stubbed with `# TODO M2`/`# TODO M4` comments. | C5 passes | no |
| C7 | `test(googlechat): outbound send via stubbed Chat API client` | `tests/gateway/test_googlechat_send.py` (new) | patch `googleapiclient.discovery.build`; assert `send()` calls `spaces.messages.create` with correct body (text, `thread.name=<thread_id>` when passed in `metadata`); assert `SendResult(success=True, message_id=<spaces/.../messages/...>)`; assert raw response in `raw_response`; assert no retry on transient timeout (per Telegram precedent — message may have been delivered) | no |
| C8 | `feat(googlechat): send() outbound via Chat REST` | `gateway/platforms/googlechat.py`: `send(chat_id, content, reply_to=None, metadata=None) -> SendResult` — chunk via `self.truncate_message(formatted, GOOGLE_CHAT_MAX_MESSAGE_LENGTH)` (constant cited from Chat API docs in code comment), threading via `metadata.get("thread_id")` → `thread.name` body field, error mapping to `SendResult(success=False, error=…, retryable=…)`. `format_message()` is still identity here — translation lands in M2. | C7 passes | no |
| C9 | `feat(googlechat): wire factory + auth maps` | `gateway/run.py:2644-2728` (`_create_adapter` — add `elif platform == Platform.GOOGLECHAT:` branch after FEISHU at `:2728`, importing `GoogleChatAdapter, check_googlechat_requirements`); `gateway/run.py:2823-2840` (`platform_env_map` — add `Platform.GOOGLECHAT: "GOOGLECHAT_ALLOWED_USERS"`); `gateway/run.py:2844-2861` (`platform_allow_all_map` — add `Platform.GOOGLECHAT: "GOOGLECHAT_ALLOW_ALL_USERS"`); **explicit decision on `platform_group_env_map` at `:2841-2843`** — Google Chat spaces use the same per-user allowlist as DMs (matching Slack/Discord), so NO entry in this map; document the decision inline with a one-line code comment to prevent future drift | **two test files, scoped by module under test**: `tests/gateway/test_googlechat_factory.py` — factory returns adapter when reqs met, returns `None` w/ warning when not (covers the `_create_adapter` branch only). `tests/gateway/test_googlechat_auth.py` — `_is_user_authorized` honors `GOOGLECHAT_ALLOWED_USERS` from env; group-space messages honor the same per-user allowlist (no separate `platform_group_env_map` entry); `GOOGLECHAT_ALLOW_ALL_USERS` bypasses the allowlist as expected. Splitting keeps each test file focused on one module so failures surface quickly. | no |

**🟢 DEMO #1 — first live reply** (immediately after C9 lands):

```bash
# 1. Configure the gateway to use the live infra
export GOOGLECHAT_SERVICE_ACCOUNT_JSON=$HOME/.mood-media-assistant/key.json
export GOOGLECHAT_PUBSUB_PROJECT=sa-mm-gchatbot
export GOOGLECHAT_PUBSUB_SUBSCRIPTION=chat-events-sub
export GOOGLECHAT_ALLOWED_USERS=users/<steve_user_id>   # captured during fixture pass

# 2. Start hermes-agent on this branch
hermes-agent gateway --platforms googlechat

# 3. In Google Chat, send a message in the steve-test space (spaces/AAQA2N6jyoA)
# 4. Adapter receives via pull, dispatches to agent, posts reply via Chat REST
# 5. Capture both the inbound payload (from logs) and the outbound API call
#    for use as fixtures in M2-M5 tests (see Fixture-Capture Strategy below)
```

If the reply does not arrive: check `gcloud pubsub subscriptions pull chat-events-sub
--limit=5 --auto-ack=false` to verify the topic is wired (it should be empty if the
adapter is acking correctly), and check Cloud Logging on the project for Chat API
errors.

**DEMO 1 rollback / debug capture** — if the smoke fails, before iterating the
adapter capture both sides of the failed exchange so M2-M5 don't compound on an
unstable transport:

```bash
mkdir -p aeyeops/googlechat/debug
gcloud pubsub subscriptions pull chat-events-sub --limit=5 --auto-ack=false \
    --format=json > aeyeops/googlechat/debug/demo-1-$(date +%Y%m%d-%H%M)-pull.json
# Capture adapter logs (LOG_LEVEL=DEBUG) over the same window
hermes-agent gateway --platforms googlechat 2>&1 \
    | tee aeyeops/googlechat/debug/demo-1-$(date +%Y%m%d-%H%M)-gateway.log
```

The `aeyeops/googlechat/debug/` directory is fork-only and excluded from the
upstream cherry-pick. `.gitignore` entry already covers `aeyeops/`.

End of milestone. M2/M3/M4/M5 can now branch off `feat/googlechat-main` in
worktrees if convenient.

---

## M2 — Formatting + Lifecycle + Toolset Bundle (6 commits, parallelizable with M3/M4/M5)

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C10 | `test(googlechat): GFM → Chat-markdown translator` | `tests/gateway/test_googlechat_format.py` (new) | bold `**x**`→`*x*`; italic `*x*`/`_x_`→`_x_`; strike `~~x~~`→`~x~`; links `[t](u)`→`<u\|t>`; headers→bold; tables→fenced code block; nested formatting handled | no |
| C11 | `feat(googlechat): format_message override (GFM translation)` | `gateway/platforms/googlechat.py`: `format_message(self, content) -> str` — pure-string translator following ADR-006; no shell out, no regex backtracking pitfalls (validated by C10) | C10 passes; existing C7 send tests must still pass with the new formatter wired | no |
| C12 | `feat(googlechat): PLATFORM_HINTS entry` | `agent/prompt_builder.py:291` (verified) — add `"googlechat"` key describing dialect (asterisk bold, underscore italic, tilde strike, no GFM tables, mention syntax) and the `send_chat_card` tool's existence (one-line teaser; full schema lives in tool docstring) | snapshot test confirming hint string contains expected dialect markers | no |
| **C12.5** | `feat(googlechat): hermes-googlechat toolset bundle + platform registry entry` | `toolsets.py` — add `"hermes-googlechat": {"description": "Google Chat bot toolset", "tools": _HERMES_CORE_TOOLS, "includes": []}`; append `"hermes-googlechat"` to `hermes-gateway`'s `includes` list. `hermes_cli/platforms.py:21-41` — add `("googlechat", PlatformInfo(label="💬 Google Chat", default_toolset="hermes-googlechat"))` to the `PLATFORMS` OrderedDict (verified hand-maintained registry consumed by `skills_config` and `tools_config` — NOT auto-derived from the `Platform` enum). | unit test confirms `get_toolset("hermes-googlechat")` resolves to the core tool list; `PLATFORMS["googlechat"].default_toolset == "hermes-googlechat"` | no |
| C13 | `test(googlechat): space lifecycle events (ADDED/REMOVED) cause no agent turn` | `tests/gateway/test_googlechat_lifecycle.py` (new) | feed ADDED_TO_SPACE / REMOVED_FROM_SPACE event dicts; assert `handle_message` is NOT awaited; assert any side-effects (logging, channel directory invalidation if any) happen | no |
| C14 | `feat(googlechat): ADDED_TO_SPACE / REMOVED_FROM_SPACE handling` | `gateway/platforms/googlechat.py`: replace TODO branches in `_handle_chat_event` with side-effect-only handlers per ADR-004; ack the Pub/Sub message; do not call `handle_message` | C13 passes | no |

**Why C12.5 sits in M2, not M4:** This is the *standard* `hermes-googlechat`
toolset bundle from `ADDING_A_PLATFORM.md:143-161` — every platform has one,
gated only on the platform being installed (not on it being active at call time).
C22 in M4 introduces a separate, *conditional* `send_chat_card` registration
that depends on this bundle existing first. Splitting the two is what keeps the
M4 commit narrowly about ADR-012's card contract.

---

## M3 — Cron + Cross-Platform Send (4 commits, parallelizable)

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C15 | `test(googlechat): _send_googlechat standalone (mocked Chat client)` | `tests/gateway/test_send_message_tool_googlechat.py` (new) | `_send_googlechat(pconfig, chat_id, message, thread_id=None, media_files=None)` posts via `spaces.messages.create`; thread_id passes through to `thread.name`; media_files attach via Chat API's `attachment` API; raises clear errors on auth failure | no |
| C16 | `feat(googlechat): _send_googlechat in tools/send_message_tool.py` | **two sites in this file** (verified): `tools/send_message_tool.py:193-211` — add `"googlechat": Platform.GOOGLECHAT` to the public `platform_map`; `tools/send_message_tool.py:392` (`_send_to_platform` def) + dispatch elif chain at `:522-544` — add `elif platform == Platform.GOOGLECHAT: return await _send_googlechat(pconfig, chat_id, message, thread_id, media_files)`. Implement `_send_googlechat(...)` near the existing `_send_*` helpers; reuse the GFM translator from M2 (import from `gateway.platforms.googlechat`) so cron messages render correctly. Update the tool schema `target` description to include a `googlechat:spaces/...` example. | C15 passes | no |
| C17 | `feat(googlechat): cron platform_map + cronjob_tools schema` | `cron/scheduler.py:287-305` (verified) — add `"googlechat": Platform.GOOGLECHAT` to `platform_map`; `tools/cronjob_tools.py` — expose `googlechat` as valid delivery target in tool schema (no card support per ADR-012 OOS) | tests for cron dispatch using stubbed `_send_to_platform` | no |
| C18 | `feat(googlechat): channel directory regression check` | likely zero code change — `gateway/channel_directory.py:83-87` (verified by direct read) iterates `for plat in Platform` and adds any platform not in `_SKIP_SESSION_DISCOVERY = {"local", "api_server", "webhook"}` and not already explicitly built. Once C2 lands `Platform.GOOGLECHAT`, the directory auto-discovers. **`ADDING_A_PLATFORM.md:213-220` shows an outdated hardcoded tuple — ignore it.** Add a regression test confirming `googlechat` sessions appear in `build_channel_directory({})`. If the test passes with no code edit, the commit is test-only and may merge into the M3 test commit; otherwise fix `channel_directory.py`. | regression test | no |

**🟢 DEMO #2 — cron message delivery** (after M3 lands):
Schedule a cronjob via the agent in `steve-test` with delivery to a known
`spaces/...` chat_id; verify the message arrives at the scheduled time.

---

## M4 — Cards (6 commits, parallelizable)

This is the largest milestone and the highest-value differentiator from text-only
chat platforms. Per ADR-012 the contract is locked, so the only design decisions
left are pydantic field ergonomics.

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C19 | `test(googlechat): CardSpec pydantic validation` | `tests/tools/test_card_spec.py` (new) | each allowed widget validates; disallowed widgets reject with clear error; card with `selectionInput` and no trailing submit `button` rejects with explicit message; `card.header` optional; nested widget order preserved | no |
| C20 | `feat(googlechat): CardSpec model + Card v2 JSON translator` | `tools/send_chat_card_tool.py` (new) — `CardSpec` discriminated union of allowed widgets per ADR-012; `to_card_v2(spec) -> dict` returning Chat API `cardsV2` JSON; reuses GFM→Chat-markdown translator from M2 inside `textParagraph` widgets per ADR-006 | C19 passes; round-trip test: validated `CardSpec` → JSON → schema-shaped dict | no |
| C21 | `test(googlechat): send_chat_card tool (stubbed Chat client)` | `tests/tools/test_send_chat_card.py` (new) | tool calls `spaces.messages.create` with `cardsV2`; round-trips `action.parameters` correctly; returns `{"message_name": ..., "success": True}`; on validation failure returns clear error before any API call | no |
| C22 | `feat(googlechat): conditionally register send_chat_card on hermes-googlechat when active` | `toolsets.py` — extend the existing `hermes-googlechat` bundle from C12.5 so that `send_chat_card` is appended to its `tools` list when googlechat is in the runtime active-platforms list. The platform-conditional registration pattern needs to be introduced (no precedent in `toolsets.py` today); the cleanest shape is a `_resolve_googlechat_tools(active_platforms) -> list` helper called when the bundle is materialized, so the static dict literal stays readable. **Depends on C12.5** (the bundle must exist before this commit can extend it). | tool present in resolved bundle when googlechat enabled, absent otherwise; bundle still resolves cleanly when googlechat NOT active | no |
| C23 | `test(googlechat): CARD_CLICKED → synthesized MessageEvent(TEXT)` | `tests/gateway/test_googlechat_card_clicked.py` (new) | feed CARD_CLICKED event with `action.actionMethodName`, `action.parameters`, `common.formInputs.selectionInput` values; assert synthesized `MessageEvent.text` matches ADR-012 example format; assert `message_type=TEXT`; assert dedup key set per implementation choice; assert self-filter still applies; assert orphaned click (post-restart) becomes standalone `MessageEvent(TEXT)` without crash | no |
| C24 | `feat(googlechat): CARD_CLICKED inbound synthesis` | `gateway/platforms/googlechat.py`: replace M1 TODO branch with click-to-text synthesizer per ADR-012; helper `_format_click_text(action, form_inputs) -> str`; ack the Pub/Sub message after `handle_message` returns | C23 passes | no |

**🟢 DEMO #3 — card round-trip** (after M4 lands):
In `steve-test`, ask the agent: "Show me a poll with two options for staging vs.
prod, then react to my choice." Agent calls `send_chat_card` → user clicks →
`CARD_CLICKED` synthesized as text → agent's next turn references the choice from
the same session.

---

## M5 — Operational Polish (4 commits, parallelizable)

| # | Commit | Files | Test | Live GCP? |
|---|---|---|---|---|
| C25 | `feat(googlechat): typing indicator (or documented no-op)` | `gateway/platforms/googlechat.py`: Google Chat REST API has **no public typing-indicator endpoint** as of 2026-04 (verify against current Chat API docs at implementation time — see Research Items below). If still unsupported, keep base `send_typing` no-op and add a one-line code comment `# Chat API has no public typing endpoint as of <date> — keeping base no-op`. NO speculative implementation. | unit test that `send_typing` returns without error | no |
| C26 | `feat(googlechat): inbound media caching` | `gateway/platforms/googlechat.py`: in MESSAGE branch, when `event.message.attachment` is present, call `cache_image_from_bytes` / `cache_audio_from_bytes` / `cache_document_from_bytes` and append to `MessageEvent.media_urls` / `media_types`. Use `SUPPORTED_DOCUMENT_TYPES` from base. Outbound media via `send_image` defers to base default until a real use case appears. | mock attachment download + cache call assertions | no |
| C27 | `feat(googlechat): redaction regexes for spaces/* + users/* IDs` | `agent/redact.py:161-174`: add patterns matching `spaces/[A-Za-z0-9_-]+`, `users/\d{15,25}`, `messages/[A-Za-z0-9_.-]+` (the doubled-ID pattern from the recent-knowledge entry). Add to existing token list. | redaction test cases for each regex | no |
| C28 | `feat(googlechat): hermes_cli/status.py + hermes_cli/gateway.py wizard` | `hermes_cli/status.py:306-311` (verified — dict values are `(TOKEN_VAR, HOME_CHANNEL_VAR)` 2-tuples per `ADDING_A_PLATFORM.md:228-233`) — add `"Google Chat": ("GOOGLECHAT_SERVICE_ACCOUNT_JSON", "GOOGLECHAT_HOME_CHANNEL")` (NOT the Pub/Sub subscription — that's transport config, already surfaced via the config file path; the home-channel slot is the conventional second var, loaded in C2's env-override pass). `hermes_cli/gateway.py:2015` (verified) `_PLATFORMS` — add interactive wizard entry covering GCP project / service account path / subscription name / home-channel space resource name / IAM publisher binding reminder. (The shared `hermes_cli/platforms.py` registry was already updated in C12.5 — no second touch needed here.) | snapshot tests for both; assert the status dict's `"Google Chat"` tuple matches the 2-tuple convention | no |

---

## Dependency Graph (commit-level)

```
C0 ─┬─ C1 ─ C2 ─┬─ C3 ─ C4 ─┬─ C5 ─ C6 ─┬─ C7 ─ C8 ─ C9 ─[DEMO 1]──┐
                │            │           │                            │
                │            │           │                            ├─ C10 ─ C11 ─┐
                │            │           │                            │             ├─ C12
                │            │           │                            │             ├─ C12.5 ─┐
                │            │           │                            │             │         │
                │            │           │                            │             ├─ C13 ─ C14
                │            │           │                            │             │         │
                │            │           │                            ├─ C15 ─ C16 ─┼─ C17    │
                │            │           │                            │             │         │
                │            │           │                            │             ├─ C18 ─[DEMO 2]
                │            │           │                            │                       │
                │            │           │                            ├─ C19 ─ C20 ─┐         │
                │            │           │                            │             ├─ C21 ─ C22 (depends on C12.5)
                │            │           │                            │             │
                │            │           │                            │             ├─ C23 ─ C24 ─[DEMO 3]
                │            │           │                            │
                │            │           │                            ├─ C25
                │            │           │                            ├─ C26
                │            │           │                            ├─ C27
                │            │           │                            └─ C28
```

Hard ordering rules:
- C2 must precede every C≥3 (everything imports `Platform.GOOGLECHAT`).
- C4 must precede every C≥5 (every adapter test imports `GoogleChatAdapter`).
- C6 must precede C5/lifecycle/card tests in landed order (skeleton must be in
  place to register branches).
- C8 must precede C9 (factory has nothing to wire without a working adapter).
- C9 must precede DEMO 1.
- C11 must precede C16 (cron send reuses the M2 translator).
- **C12.5 must precede C22** (the conditional `send_chat_card` registration
  extends the standard `hermes-googlechat` toolset bundle introduced in C12.5).
- C20 must precede C22 (tool implementation must exist before it's registered).
- C24 must precede DEMO 3.

Parallelization windows (post-DEMO 1):
- M2 (6 commits) ‖ M3 (4 commits) ‖ M4 (6 commits) ‖ M5 (4 commits) — but
  C22 in M4 also blocks on C12.5 in M2, so M4 is not fully independent of M2.
- Within M4, C19/C20 are independent of C23/C24; the two pairs can run in
  parallel inside M4.
- M5 commits (C25/C26/C27/C28) touch four disjoint files and parallelize freely.

Worktree recommendation when running parallel: one worktree per milestone using
the `superpowers:using-git-worktrees` workflow; merge each completed milestone
back to `feat/googlechat-main` before opening the next demo. This keeps the
rebase surface small if `upstream/main` moves.

---

## Test Strategy Per Integration Point

**Inherited Hermes pattern** (verified across slack/discord/telegram tests):
1. Construct the adapter with a minimal `PlatformConfig` and `Platform.GOOGLECHAT`.
2. Patch `adapter.handle_message = AsyncMock()` (or `adapter._message_handler =
   AsyncMock(return_value="response")` for end-to-end-mode tests).
3. Call the adapter's internal entrypoint (`_handle_chat_event(raw_dict)` for
   inbound, `send(...)` for outbound) directly; do NOT spin up Pub/Sub or the
   gateway runner.
4. Assert on the constructed `MessageEvent` / `SendResult` / mocked API call.

**No JSON fixture files.** The codebase has zero fixture JSON files in
`tests/`. Inline-dict / `SimpleNamespace` is the convention. Capture from live
traffic for fixture *content*; commit it as a Python literal.

**Live GCP usage:** restricted to manual end-of-milestone smoke (DEMO 1/2/3).
Do NOT add CI jobs that hit live GCP — service account credentials are not in
CI, and per ADR-008 we do not gate the build on operator-provisioned IAM.

---

## Fixture-Capture Strategy

The user constraint is unambiguous: **only capture from bot ↔ self traffic in
`steve-test` once the adapter runs**. No coworker DM payloads.

Capture mechanism:
1. After C9 lands, run the gateway with `LOG_LEVEL=DEBUG` (or wrap the
   Pub/Sub message handler in a one-shot `json.dumps(event, indent=2)` log
   line) and exchange messages with the bot in `spaces/AAQA2N6jyoA`.
2. Copy the logged event into a Python literal at the top of the relevant test
   file:
   ```python
   _MESSAGE_FIXTURE = {
       "type": "MESSAGE",
       "message": {"name": "spaces/AAQA2N6jyoA/messages/...",
                   "sender": {"name": "users/...", "displayName": "Steve",
                              "type": "HUMAN"},
                   "text": "hello",
                   "createTime": "2026-04-20T..."},
       "space": {"name": "spaces/AAQA2N6jyoA", "type": "DM"},
       "user": {"name": "users/..."},
   }
   ```
3. **Redact before committing**: replace the real `users/<id>` with
   `users/111000000000000000001` and the real space ID with
   `spaces/AAQA2N6jyoA` (the steve-test space is already in the published
   memory; not sensitive). Keep the doubled-`messages/<id>.<id>` shape — that
   detail matters per the recent-knowledge entry.
4. Capture each event type once: MESSAGE (text), MESSAGE (with attachment for
   M5/C26), ADDED_TO_SPACE, REMOVED_FROM_SPACE, CARD_CLICKED (with parameters
   + form inputs for M4/C23). Attachment fixture deferred until M5 to avoid
   blocking M2/M3/M4.
5. Outbound fixtures are not captured from the wire — they are constructed
   directly in tests and asserted against the mocked
   `googleapiclient.discovery` builder calls.

**Capture-to-commit mapping** (which test commits depend on captured payloads):

| Test commit | Required captured fixture | Capture window |
|-------------|---------------------------|----------------|
| C5 (inbound MESSAGE) | MESSAGE event in DM (steve-test) | Immediately after DEMO 1 — round-trip a "hello" message and copy the inbound payload from gateway logs |
| C13 (lifecycle) | ADDED_TO_SPACE + REMOVED_FROM_SPACE | Re-add and remove the bot from a transient test space; capture both events |
| C23 (CARD_CLICKED) | CARD_CLICKED with action.parameters + formInputs | After C20 lands a `send_chat_card` impl that the implementer can manually invoke; click the card in steve-test and capture the click event |
| C26 (media) | MESSAGE with `attachment` field | Send a small image to the bot in steve-test; capture |

**For C5**, the implementing agent should NOT invent the payload structure from
Google Chat docs alone — the recent-knowledge entry already pinned one
non-obvious quirk (doubled-ID `spaces/<id>/messages/<msg_id>.<msg_id>`); other
quirks may exist that only show up in real traffic. C5 explicitly lands AFTER
DEMO 1 captures the first inbound payload — the test commit is paired with the
captured fixture in the same atomic commit.

---

## End-to-End Demo Path Summary

| Demo | After commit | What to verify |
|------|--------------|----------------|
| **DEMO 1** — first reply | C9 | Message in `steve-test` → bot replies via Chat REST. Inbound dedup works (no double-reply). Self-filter works (bot's own message doesn't loop). |
| **DEMO 2** — cron delivery | C18 | Agent schedules a job; cron fires; message arrives via `_send_googlechat`. |
| **DEMO 3** — card round-trip | C24 | Agent sends a card with radio + checkbox + submit; user clicks; agent's next turn sees the synthesized text and references the user's choices. |

DEMO 1 is the hard milestone. DEMO 2 and DEMO 3 are sanity checks on the
parallelized milestones.

---

## Reference Index (file:line for the implementing agent)

**Read before each commit:**
- All ADRs in `aeyeops/googlechat/adr/` (especially 001, 002, 003, 004, 005, 006, 007, 008, 011, 012)
- `aeyeops/googlechat/design.md` (full file — ~478 lines)
- `aeyeops/googlechat/plan.md` (53 lines, 16 IPs)

**Base contract:**
- `gateway/platforms/base.py:865` — `BasePlatformAdapter.__init__`
- `gateway/platforms/base.py:1024` (connect), `:1033` (disconnect), `:1037` (send), `:2191` (get_chat_info) — abstract methods
- `gateway/platforms/base.py:656` — `MessageEvent` dataclass (DO NOT extend)
- `gateway/platforms/base.py:733` — `SendResult` dataclass (DO NOT extend)
- `gateway/platforms/base.py:1655` — `handle_message` (call this, never `_message_handler` directly)
- `gateway/platforms/base.py:2159` — `build_source` helper
- `gateway/platforms/base.py:2212` — `truncate_message`
- `gateway/platforms/base.py:824` — `resolve_channel_prompt`
- `gateway/platforms/base.py:344-611` — `cache_image_from_bytes` / `cache_audio_from_bytes` / `cache_document_from_bytes`
- `gateway/platforms/base.py:564-573` — `SUPPORTED_DOCUMENT_TYPES`
- `gateway/platforms/base.py:961` / `:981` — platform locks
- `gateway/platforms/helpers.py:25-65` — `MessageDeduplicator`
- `gateway/platforms/helpers.py:190-248` — `ThreadParticipationTracker`

**Wiring sites** (all line numbers verified by direct read unless noted):
- `gateway/config.py:48-69` — `Platform` enum (verified)
- `gateway/config.py:825-…` — `_apply_env_overrides` (range verified by Grep, exact extent depends on next platform's block)
- `gateway/config.py:268-320` — `get_connected_platforms` (verified — hand-maintained per-platform `elif extra.get(...)` chain; GOOGLECHAT MUST add a branch)
- `gateway/run.py:2644-2728` — `_create_adapter` factory elif-chain (FEISHU is the last branch at `:2728`; insert GOOGLECHAT after it) (verified)
- `gateway/run.py:2823-2840` — `platform_env_map` (verified)
- `gateway/run.py:2841-2843` — `platform_group_env_map` (verified — currently QQBOT-only; GOOGLECHAT does NOT add a branch, see C9)
- `gateway/run.py:2844-2861` — `platform_allow_all_map` (verified)
- `gateway/run.py:1989` — `set_message_handler` wiring (cited from agent survey, not directly re-verified)
- `cron/scheduler.py:287-305` — `platform_map` (verified)
- `tools/send_message_tool.py:193-211` — public `platform_map` (verified)
- `tools/send_message_tool.py:392` — `_send_to_platform` def (verified)
- `tools/send_message_tool.py:522-544` — `_send_to_platform` elif dispatch chain (verified — last entry is QQBOT at `:544`; insert GOOGLECHAT after it)
- `agent/prompt_builder.py:291` — `PLATFORM_HINTS` dict (verified)
- `gateway/channel_directory.py:83-87` — `for plat in Platform` auto-fallthrough (verified — `_SKIP_SESSION_DISCOVERY = {"local", "api_server", "webhook"}` is the only skip list; GOOGLECHAT auto-discovers once enum-registered)
- `agent/redact.py:18-63` and `:161-174` — current redaction patterns (cited from agent survey)
- `hermes_cli/status.py` — CLI status display (cited from agent survey)
- `hermes_cli/gateway.py:2015` — `_PLATFORMS` interactive wizard (verified)
- `hermes_cli/platforms.py:21-41` — `PLATFORMS` OrderedDict (verified — hand-maintained shared registry consumed by `skills_config` and `tools_config`; **MUST add a googlechat entry** in C12.5 or it's invisible to TUI menus and default toolset resolution)
- `toolsets.py` — `_HERMES_CORE_TOOLS` (lines 31-63, verified) and `TOOLSETS` dict (line 68 onwards, verified). No platform-conditional registration pattern exists today — introduce in C22 by extending the bundle from C12.5.

**Convention references (read-only — do NOT copy):**
- `gateway/platforms/slack.py:82-115` (config), `:219` (connect-as-task),
  `:1062` (mention strip), `:1179-1186` (build_source), `:1194-1204` (event-id mapping),
  `:437-545` (markdown translator)
- `gateway/platforms/discord.py:518` (`ThreadParticipationTracker` usage),
  `:751-754` (connect pattern), `:3071-3203` (message construction)
- `gateway/platforms/telegram.py:223` (mention patterns), `:748-780` (connect),
  `:978-1061` (send retry)

---

## IP Disposition Anchor

For implementing agents reading milestone tables only, here's the explicit
mapping from each plan.md integration point to its disposition in this build
plan:

| IP | Title | Disposition |
|----|-------|-------------|
| 1 | Core adapter | C4/C6/C8 (M1) + C11/C14 (M2) + C24 (M4) + C26 (M5) — built incrementally |
| 2 | Platform enum | C2 (M0) |
| 3 | Adapter factory | C9 (M1) |
| 4 | Authorization maps | C9 (M1) |
| 5 | Session source | **No commit needed** — `gateway/session.py` already provides `chat_id` / `thread_id` / `user_id` / `chat_type` (verified in `design.md:235`); IP-1 simply populates them via `build_source()` |
| 6 | System prompt hints | C12 (M2) |
| 7 | Toolset | **Two commits**: C12.5 (M2 — standard `hermes-googlechat` bundle + `hermes_cli/platforms.py` registry) + C22 (M4 — conditional `send_chat_card`) |
| 8 | Cron delivery | C17 (M3) |
| 9 | Send message tool | C16 (M3) |
| 10 | Cronjob tool schema | C17 (M3) |
| 11 | Channel directory | C18 (M3) — verification only; auto-falls-through |
| 12 | Status display | C28 (M5) |
| 13 | Gateway setup wizard | C28 (M5) |
| 14 | ID redaction | C27 (M5) |
| 15 | Documentation | **Out of scope** — upstream docs (`AGENTS.md`, `README.md`, `website/docs/...`) per CLAUDE.md `<out-of-scope>`; revisit at upstream PR time |
| 16 | Tests | Distributed across C3/C5/C7/C10/C13/C15/C19/C21/C23 — every feat commit is preceded by its test commit |

---

## Research Items (resolve at implementation time, not now)

These are the two facts the plan deliberately does not pin, because pinning them from training data would violate the "verify before recommending" principle:

| Item | Affects | Resolution path |
|------|---------|-----------------|
| `GOOGLE_CHAT_MAX_MESSAGE_LENGTH` constant value used by `truncate_message` in C8 | Adapter `send()` chunking | Implementation agent must check `developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages` (or current Chat API quotas page) for the `text` field max length and pin the constant in code with a doc comment + verification date. The implementation MUST NOT assume a value from training data. |
| Chat API typing-indicator support (C25) | Whether `send_typing` is overridden or stays a base no-op | Implementation agent must check current Chat API docs. As of the plan-writing window the public API exposes no typing endpoint; if that has changed, override `send_typing`; otherwise document the no-op with the verification date. |

Both items are isolated to single commits (C8 and C25 respectively) so they
don't block any earlier work.

---

## Verification

After all 5 milestones land:

1. **Static checks** — every commit conforms to the upstream `ADDING_A_PLATFORM.md`
   16-item checklist; gate via `git log --grep='googlechat' feat/googlechat-main`
   showing 30 atomic commits in the documented order.
2. **Test suite** — `pytest tests/gateway/test_googlechat*.py tests/tools/test_card_spec.py
   tests/tools/test_send_chat_card.py tests/gateway/test_send_message_tool_googlechat.py
   -v` is all-green with no live-GCP requirement.
3. **Type check** — `pyright gateway/platforms/googlechat.py tools/send_chat_card_tool.py`
   clean.
4. **Live smoke (DEMO 1)** — bot replies in `steve-test` from a live message.
5. **Live smoke (DEMO 2)** — cron-delivered text message arrives.
6. **Live smoke (DEMO 3)** — card round-trips through CARD_CLICKED.
7. **Per-milestone rebase check** — at the end of each milestone, run
   `git fetch upstream && git merge-base --is-ancestor upstream/main HEAD || echo "rebase needed"`.
   Catching upstream drift early avoids compounding multi-milestone rebase
   pain.
8. **Final rebase rehearsal** — `git fetch upstream && git rebase upstream/main`
   on a throwaway branch after all milestones land; the conflict surface is
   limited to `gateway/config.py`, `gateway/run.py`,
   `tools/send_message_tool.py`, `cron/scheduler.py`, `agent/prompt_builder.py`,
   `agent/redact.py`, `toolsets.py`, `hermes_cli/status.py`,
   `hermes_cli/gateway.py`, `hermes_cli/platforms.py` — all of which are
   conventional list-extension edits that rebase cleanly.
9. **Cherry-pick rehearsal** — `git format-patch upstream/main..feat/googlechat-main
   -- gateway/platforms/googlechat.py gateway/config.py gateway/run.py
   cron/scheduler.py tools/send_message_tool.py tools/send_chat_card_tool.py
   tools/cronjob_tools.py agent/prompt_builder.py agent/redact.py toolsets.py
   gateway/channel_directory.py hermes_cli/status.py hermes_cli/gateway.py
   hermes_cli/platforms.py tests/gateway/test_googlechat*.py
   tests/tools/test_card_spec.py tests/tools/test_send_chat_card.py
   tests/gateway/test_send_message_tool_googlechat.py` produces a clean patch
   series with `aeyeops/` excluded — that's the upstream PR.

---

## Total Commit Count: 30 (1 docs + 2 bootstrap + 7 demo + 6 format/lifecycle/toolset + 4 cron + 6 cards + 4 polish)

> Math: C0 (docs) = 1; M0 functional = C1+C2 = 2; M1 = C3-C9 = 7;
> M2 = C10/C11/C12/C12.5/C13/C14 = 6; M3 = C15-C18 = 4; M4 = C19-C24 = 6;
> M5 = C25-C28 = 4. Total 1+2+7+6+4+6+4 = **30**.

**Time estimate:** Deliberately not given. This is the first Google Chat
adapter on this fork; transport tuning, fixture-capture iterations, and the
Chat API research items in C8/C25 introduce timing variance that previous
adapters (with established calibration data) don't carry. Estimate after M1
lands and the live transport loop is observed.
