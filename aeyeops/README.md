# aeyeops/

AeyeOps fork workspace. Everything in this directory is fork-only and will be
excluded when we cherry-pick the Google Chat adapter into an upstream PR.

Upstream documentation (`AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `docs/`)
stays untouched until the PR lands. Our design notes, decisions, research, and
scratch work live here instead.

## Layout

- `runbook.md` — generic operational notes for installing, updating, and
  running this fork against a live gateway. Kept deliberately host-agnostic
  so it's safe in a public fork.
- `googlechat/` — design notes, spec, architecture decisions, and runbooks for
  the Google Chat platform adapter.
- `portal/` — secured HTTPS exposure plan, Caddy/systemd templates, and
  headless setup/verify scripts for the Hermes dashboard.

Future fork work (if any) gets its own subdirectory alongside.
