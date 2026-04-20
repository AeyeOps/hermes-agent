# CLAUDE.md

<fork-intent>
This repo is AeyeOps's fork of `NousResearch/hermes-agent`. It exists for one
outcome: ship a Google Chat platform adapter that can be cherry-picked into an
upstream PR once we've dogfooded it.

Everything else in git history — new providers, TUI changes, skills, platform
fixes — is upstream churn that arrives via rebase. It is not our work. Don't
review it, release-note it, or refactor around it.
</fork-intent>

<architecture-context>
Platform adapters sit ABOVE the plugin system, closer to the user:

```
User ↔ Platform Adapter → Gateway/Agent Core → Plugins (tools, hooks, memory, skills) → Model + builtin toolsets
```

`BasePlatformAdapter.handle_message` funnels every inbound message through
`self._message_handler(event)`, which `GatewayRunner` wires via
`adapter.set_message_handler` after `_create_adapter`. That single dispatch
point is why a new adapter inherits — for free — every plugin-registered tool
(`PluginContext.register_tool`), every lifecycle hook (`pre_tool_call`,
`on_session_start`, `on_session_end`, `post_llm_call`, …), every
`optional-skills/` bundle, plus `channel_prompts`, cron delivery,
`send_message_tool` routing, redaction, interrupt support, and media caching.

A Google Chat adapter's surface area is therefore only I/O:
**connect, receive, normalize into a `MessageEvent`, send.** Nothing else.
</architecture-context>

<rebase-discipline>
We rebase onto upstream regularly so the eventual upstream PR stays small. That
discipline drops out of keeping our diff narrow:

- Work lives in a new platform adapter module — ideally one new file plus
  registration wiring. Resist refactoring shared code "while we're here,"
  because every touch outside the adapter widens the rebase surface.
- If upstream moves a helper we depend on, follow the move. Don't fork the helper.
- Prefer composition over subclassing anything upstream owns, because
  subclasses break silently on rebase and composition breaks loudly.
- When upstream ships something that makes our adapter simpler, take it. When
  upstream ships something unrelated, ignore it — scope creep is the main risk.
</rebase-discipline>

<dogfooding>
We run this fork as our day-to-day hermes-agent so the adapter's rough edges
surface before the upstream PR. Optimize for our actual usage first; the
upstream-acceptable version gets shaped by what dogfooding reveals, not by
guessing what reviewers will want.
</dogfooding>

<branching>
Work directly on `feat/googlechat-main` and commit as you go. This is the fork's
long-lived integration branch — every adapter change lands here, and anything
that branches off eventually flows back into it. Direct check-ins keep the
feedback loop tight and let the git history show how the adapter actually
evolved.

Topology: `upstream/main` (NousResearch) mirrors to `origin/main` (AeyeOps);
`feat/googlechat-main` tracks `origin/main` with rebase-on-pull. So `git pull`
rebases our adapter commits onto the latest upstream, and `git push` lands on
`origin/feat/googlechat-main` — never on `main`.

Short-lived feature branches off `feat/googlechat-main` are fine when isolation
earns its keep (a risky spike, an experiment you might abandon). Merge them
back when they land. Worktrees and PRs are available when the user asks for
them — e.g., to stage the upstream cherry-pick.
</branching>

<out-of-scope>
- Upstream bugs that don't block our adapter — file them upstream, or leave them.
- Tuning provider, TUI, or skill code — we inherit whatever upstream ships.
- Release notes for the fork — we don't ship releases, we ship a PR.
- Parity refactors across existing platform adapters — matching upstream
  conventions is enough.
- Upstream documentation (`AGENTS.md`, `README.md`, `CONTRIBUTING.md`,
  `docs/`) — leave it alone until the upstream PR lands. Our own notes,
  decisions, and scratch work go in `aeyeops/googlechat/` instead, which is
  fork-only and excluded from the cherry-pick.
</out-of-scope>

<adapter-naming>
The Google Chat platform name is `googlechat`, matching upstream's
compression convention (`homeassistant`, `whatsapp`, `qqbot`). Adapter code
lands at `gateway/platforms/googlechat.py` — or `gateway/platforms/googlechat/`
if it grows to a package, per the `qqbot` precedent. Commit scope is
`googlechat` (e.g., `feat(googlechat): …`).
</adapter-naming>

<adapter-work>
Start every session that touches the Google Chat adapter by reading every
ADR in `aeyeops/googlechat/adr/`. Those are the settled decisions the rest
of the fork assumes — `design.md` describes the system and `plan.md`
tracks execution, but neither repeats the ADR rationale because you'll
have just read it. New decisions produce new ADRs.
</adapter-work>

<working-agreements>
- Remotes: `upstream` = `NousResearch/hermes-agent`, `origin` = `AeyeOps/hermes-agent`.
  Pull from upstream, push to origin.
- Keep Google Chat adapter commits atomic and conventionally-prefixed
  (`feat(googlechat): …`, `fix(googlechat): …`) so the cherry-pick to an
  upstream PR is mechanical.
- When a change could live in our adapter or in upstream core, prefer upstream
  — but don't block on it if upstream review would stall our work.
</working-agreements>
