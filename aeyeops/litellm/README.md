# AEyeOps LiteLLM Bridge

This directory tracks AEyeOps-owned planning and examples for running a local LiteLLM proxy as a bridge for Hermes/Hindsight.

Scope:
- Keep deployment notes, config examples, and service examples here.
- Keep any upstream LiteLLM reference checkout outside this repo.
- Do not vendor LiteLLM source into Hermes.
- Do not commit runtime secrets, OAuth tokens, or master keys.

Auth rebind:
- After the local Codex CLI has been refreshed, use
  `../scripts/rebind-codex-auth-to-litellm.py` to converge Codex, Hermes, and
  the local LiteLLM bridge onto the same already-minted token.
- Put host-local defaults in `../.env` by copying `../.env.example`; the real
  env file is ignored by git.
- The rebind script does not log in and does not print token values. It backs
  up touched auth files, updates Hermes auth, writes the LiteLLM ChatGPT auth
  file, and can restart/validate the bridge.

Current candidate:
- Install from pinned LiteLLM upstream commit recorded in `deploy-plan.md` until PyPI has the needed release.
- Use localhost-only proxy binding for smoke and deployment.
