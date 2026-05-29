# LiteLLM Codex/ChatGPT Bridge Deployment Plan

Status: deployment/runbook template for wiring Hermes memory to a local
OpenAI-compatible LiteLLM bridge backed by an existing Codex/ChatGPT
subscription token.

## Modus operandi for future clean contexts

This setup intentionally runs Hermes memory through a local service chain:

```text
Hermes live agent
  -> Hindsight memory provider
  -> Hindsight local_external daemon on localhost
  -> LiteLLM OpenAI-compatible bridge on localhost
  -> ChatGPT/Codex subscription auth from the live Hermes/Codex token store
```

The important operational rule: **do not start a new `codex login` or LiteLLM
device-code login during a normal rebind.** The intended rebind path consumes an
already-minted Codex/ChatGPT token from the same Hermes/Codex environment and
converts it into LiteLLM's flat ChatGPT auth file shape. A new browser login is
only a last resort when the existing Codex/Hermes auth stores are missing or
terminally invalid.

Use placeholders in this document rather than machine-specific paths:

| Placeholder | Meaning |
| --- | --- |
| `<repo>` | Current repository checkout. |
| `<HERMES_HOME>` | Active Hermes home from `HERMES_HOME`, usually a profile-aware Hermes state directory. |
| `<CODEX_HOME>` | Active Codex home from `CODEX_HOME`, usually the Codex CLI state directory. |
| `<LITELLM_AUTH_FILE>` | LiteLLM ChatGPT auth JSON used by the bridge service. |
| `<LITELLM_ENV_FILE>` | Environment file containing the LiteLLM proxy master key. |
| `<LITELLM_CONFIG>` | LiteLLM proxy YAML config for the bridge. |
| `<LITELLM_RUNTIME_DIR>` | LiteLLM runtime checkout or virtual environment parent. |
| `<LITELLM_VENV>` | Virtual environment that provides the `litellm` executable. |
| `<LITELLM_STATE_DIR>` | Private writable state directory for LiteLLM tokens/logs. |
| `<HINDSIGHT_CONFIG>` | Hermes-side Hindsight provider config JSON. |
| `<HINDSIGHT_HOME>` | Private home/state directory for the Hindsight service user. |
| `<HINDSIGHT_VENV>` | Virtual environment that provides `hindsight-embed`. |
| `<HINDSIGHT_PROFILE>` | Hindsight profile name used by Hermes. |
| `<HINDSIGHT_API_URL>` | Local Hindsight API URL, for example `http://127.0.0.1:8888`. |
| `<LITELLM_BASE_URL>` | Local OpenAI-compatible LiteLLM URL, for example `http://127.0.0.1:4000/v1`. |
| `<MODEL_ALIAS>` | LiteLLM model alias exposed to Hermes, for example `hindsight-codex`. |

The active token sources should be checked in this order:

1. Preferred source: `<HERMES_HOME>/auth.json`
   - `providers.openai-codex.tokens.*`
   - This mirrors how Hermes auth rebinding consumes fresh already-minted Codex
     tokens.
2. Fallback source: `<CODEX_HOME>/auth.json`
   - `tokens.*`
   - This is the Codex CLI auth store for the current Codex session.
3. LiteLLM target: `<LITELLM_AUTH_FILE>`
   - Flat keys expected by LiteLLM's ChatGPT provider:
     `access_token`, `refresh_token`, `id_token`, `expires_at`, `account_id`.

The repo-local helper is intentionally ignored by Git because it touches live
secrets:

```bash
python3 tmp/rebind-litellm-chatgpt-from-hermes-codex.py
```

That helper should do all of the following without printing secrets:

1. Read `<HERMES_HOME>/auth.json` first, then `<CODEX_HOME>/auth.json` only if
   needed.
2. Derive `expires_at` and `account_id` from JWT claims.
3. Back up the current LiteLLM auth file and source auth snapshots under a
   private backup directory outside the repository.
4. Atomically write `<LITELLM_AUTH_FILE>` as the LiteLLM service user with mode
   `0600`.
5. Restart only the LiteLLM bridge service.
6. Validate `/v1/models` and an exact-response `/v1/chat/completions` smoke.

If this file is being read in a clean clone and the ignored helper is absent,
recreate the same behavior rather than using an interactive login flow. The
minimal safe algorithm is:

```text
load <HERMES_HOME>/auth.json providers.openai-codex.tokens
require access_token + refresh_token + id_token
decode access_token JWT exp -> expires_at
decode id_token or access_token claim
  https://api.openai.com/auth.chatgpt_account_id -> account_id
backup <LITELLM_AUTH_FILE> outside the repository
atomic-write <LITELLM_AUTH_FILE> as the LiteLLM service user with mode 0600
restart the LiteLLM bridge service
validate localhost LiteLLM and Hermes/Hindsight
```

Never paste token values into logs, docs, tickets, commits, or PRs. It is fine
to print token lengths, account IDs, expiry timestamps, paths expressed through
placeholders, and pass/fail statuses.

## Boot expectations

The LiteLLM bridge and Hindsight daemon should be installed as service-manager
units and enabled to start automatically after reboot. Use deployment-local unit
names consistently; examples below use `<LITELLM_SERVICE>` and
`<HINDSIGHT_SERVICE>`.

```bash
systemctl is-enabled <LITELLM_SERVICE> <HINDSIGHT_SERVICE>
systemctl is-active  <LITELLM_SERVICE> <HINDSIGHT_SERVICE>
```

Expected output is two `enabled` lines and two healthy `active` states. If Hermes
memory appears down after reboot, check these services before changing Hermes
config:

```bash
systemctl status --no-pager -l <LITELLM_SERVICE>
systemctl status --no-pager -l <HINDSIGHT_SERVICE>
journalctl -u <LITELLM_SERVICE> -n 120 --no-pager
journalctl -u <HINDSIGHT_SERVICE> -n 120 --no-pager
```

If the Hindsight unit starts a child daemon and exits with `RemainAfterExit`, an
`active (exited)` unit can be normal. Verify the daemon and API directly:

```bash
pgrep -a -f 'hindsight-api|postgres'
curl -fsS -m 5 <HINDSIGHT_API_URL>/docs ><scratch-file>
```

## Service installation recipe

Install the bridge and memory daemons as boot-enabled services. Keep all runtime
state, auth files, env files, and generated units outside the Git checkout.
Before using these templates, replace every `<placeholder>` with deployment-local
values.

Create dedicated service users when they do not already exist:

```bash
useradd --system --create-home --home-dir <LITELLM_STATE_DIR> --shell <nologin-shell> litellm
useradd --system --create-home --home-dir <HINDSIGHT_HOME> --shell <nologin-shell> hindsight
```

Create private state and config locations:

```bash
install -d -m 0750 -o litellm -g litellm <LITELLM_STATE_DIR>
install -d -m 0750 -o litellm -g litellm "$(dirname <LITELLM_AUTH_FILE>)"
install -d -m 0750 -o hindsight -g hindsight <HINDSIGHT_HOME>
install -d -m 0750 -o hindsight -g hindsight "$(dirname <HINDSIGHT_CONFIG>)"
```

Render the LiteLLM bridge unit:

```ini
[Unit]
Description=LiteLLM Codex/ChatGPT bridge for Hermes
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=litellm
Group=litellm
WorkingDirectory=<LITELLM_RUNTIME_DIR>
EnvironmentFile=<LITELLM_ENV_FILE>
Environment=CHATGPT_TOKEN_DIR=<directory-containing-LITELLM_AUTH_FILE>
ExecStart=<LITELLM_VENV>/bin/litellm --config <LITELLM_CONFIG> --host 127.0.0.1 --port 4000 --telemetry False
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=<LITELLM_STATE_DIR> <LITELLM_RUNTIME_DIR>

[Install]
WantedBy=multi-user.target
```

Render the Hindsight daemon unit:

```ini
[Unit]
Description=Hindsight local_external daemon for Hermes memory
After=network-online.target <LITELLM_SERVICE>
Wants=network-online.target
Requires=<LITELLM_SERVICE>

[Service]
Type=oneshot
User=hindsight
Group=hindsight
Environment=HOME=<HINDSIGHT_HOME>
ExecStart=<HINDSIGHT_VENV>/bin/hindsight-embed -p <HINDSIGHT_PROFILE> daemon start
ExecStop=<HINDSIGHT_VENV>/bin/hindsight-embed -p <HINDSIGHT_PROFILE> daemon stop
RemainAfterExit=yes
TimeoutStartSec=120
TimeoutStopSec=60
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=<HINDSIGHT_HOME>

[Install]
WantedBy=multi-user.target
```

Install, enable, and start the units:

```bash
install -m 0644 <rendered-litellm-unit> <systemd-unit-dir>/<LITELLM_SERVICE>
install -m 0644 <rendered-hindsight-unit> <systemd-unit-dir>/<HINDSIGHT_SERVICE>
systemctl daemon-reload
systemctl enable --now <LITELLM_SERVICE>
systemctl enable --now <HINDSIGHT_SERVICE>
```

Immediately validate boot behavior:

```bash
systemctl is-enabled <LITELLM_SERVICE> <HINDSIGHT_SERVICE>
systemctl is-active  <LITELLM_SERVICE> <HINDSIGHT_SERVICE>
```

## Standard validation

Use the smallest live validation path; do not run the full Hermes test suite for
this operational check.

```bash
# 1. Services are up and enabled.
systemctl is-active <LITELLM_SERVICE> <HINDSIGHT_SERVICE>
systemctl is-enabled <LITELLM_SERVICE> <HINDSIGHT_SERVICE>

# 2. Hindsight API is reachable.
curl -fsS -m 5 <HINDSIGHT_API_URL>/docs ><scratch-file>

# 3. Live Hermes/Hindsight/LiteLLM smoke.
<live-hermes-python> tmp/validate-live-hindsight-litellm.py
```

Expected validation markers:

```text
[observed] memory_providers= ['hindsight']
[observed] memory_tools= ['hindsight_recall', 'hindsight_reflect', 'hindsight_retain']
[observed] retain_result= {'result': 'Memory stored successfully.'}
[observed] hermes_response= 'hindsight-hermes-ok'
[observed] validation=PASS
```

If the ignored validation script is missing in a clean clone, recreate a small
script that:

1. Uses the live Hermes Python interpreter.
2. Sets `HERMES_HOME=<HERMES_HOME>`.
3. Instantiates Hermes `AIAgent` against `<LITELLM_BASE_URL>` with model
   `<MODEL_ALIAS>`.
4. Enables the `memory` toolset.
5. Verifies Hindsight tools exist, performs one retain/recall sentinel, and asks
   Hermes to reply exactly `hindsight-hermes-ok`.

## LiteLLM pinning rationale

Reference upstream: `https://github.com/BerriAI/litellm.git`

Candidate pinned commit: `06f6cfc5ae377edc9b6067475f2402fa34161e60`

Observed upstream version: `1.87.0`

Why this pin:

- PyPI `1.86.2` reproduced the ChatGPT/Codex Responses-to-chat bridge failure:
  `ResponsesAPIResponse object has no attribute output`.
- The pinned upstream commit contains `litellm.responses.sse_output_recovery`
  and ChatGPT transformer logic that recovers `OUTPUT_ITEM_DONE` /
  `OUTPUT_TEXT_DONE` into `response_payload["output"]`.

Deployment sequence:

1. Create a separate runtime checkout or virtual environment; do not run the
   bridge from the Hermes repository checkout.
2. Install LiteLLM from the pinned commit, not floating `main`:

   ```bash
   python -m pip install \
     'git+https://github.com/BerriAI/litellm.git@06f6cfc5ae377edc9b6067475f2402fa34161e60#egg=litellm[proxy]'
   ```

3. Put ChatGPT token storage outside the repository, with private permissions.
4. Convert/copy Codex auth into LiteLLM token file shape once, or perform a
   one-time LiteLLM device login only when no reusable Codex/Hermes token exists.
5. Run a localhost-only proxy smoke with a master key.
6. Verify `/v1/models` and `/v1/chat/completions` return content for
   `<MODEL_ALIAS>`.
7. Only after smoke, create the service-manager unit and optionally wire
   Hindsight to `<LITELLM_BASE_URL>`.

Reference deployment shape:

- Runtime: separate LiteLLM virtual environment outside the Hermes repository.
- Config: deployment-local LiteLLM YAML outside the repository.
- Environment file: deployment-local env file outside the repository.
- ChatGPT token storage: private LiteLLM state directory outside the repository.
- Service: LiteLLM bridge service enabled at boot.
- Bind: localhost only.
- Model alias: `<MODEL_ALIAS> -> chatgpt/gpt-5.3-codex`.
- Smoke evidence: `/v1/models` returned `<MODEL_ALIAS>`;
  `/v1/chat/completions` returned the exact smoke response.

Operational notes:

- The service user must be able to traverse and execute the runtime Python and
  virtual environment paths. Avoid runtimes located below private home
  directories that the service user cannot traverse.
- Runtime secrets stay outside Git. Do not paste the LiteLLM env file or
  ChatGPT auth JSON into logs, tickets, docs, commits, or PRs.
- A `token_invalidated` 401 from the ChatGPT/Codex backend can mean the LiteLLM
  auth file is bound to an older or different ChatGPT account than live
  Hermes/Codex. The first fix is a local rebind from `<HERMES_HOME>/auth.json`,
  not a new login.

## Hindsight memory activation

- If live Hermes runs as root, Hindsight `local_embedded` mode is not viable:
  embedded PostgreSQL `initdb` refuses to run as root.
- Prefer Hindsight `local_external` mode. Run the local Hindsight daemon under a
  dedicated unprivileged service user, expose it on `<HINDSIGHT_API_URL>`, and
  configure Hermes to connect to that API.
- Hermes-side Hindsight config lives at `<HINDSIGHT_CONFIG>`.
- Hermes config should set `memory.provider: hindsight` in
  `<HERMES_HOME>/config.yaml`.
- The Hindsight profile/env file should point LLM calls at `<LITELLM_BASE_URL>`
  and `<MODEL_ALIAS>`.
- Local setup and validation helpers under repo-local `tmp/` should remain ignored by
  Git because they may touch deployment-local secrets and state.

Validation evidence to collect:

- Hindsight provider activates.
- `hindsight_retain`, `hindsight_recall`, and `hindsight_reflect` tools are
  present.
- A retain call succeeds.
- Recall returns the sentinel.
- Hermes returns exact response `hindsight-hermes-ok` via the LiteLLM Codex
  bridge.

## Rollback

- To undo only the latest Codex/LiteLLM rebind, restore the backed-up
  `<LITELLM_AUTH_FILE>` from the private backup directory and restart the
  LiteLLM bridge service.
- To stop the bridge entirely, disable and stop the LiteLLM bridge service.
- To stop Hindsight memory entirely, disable and stop the Hindsight service.
- Restore `<HERMES_HOME>/config.yaml`, `<HERMES_HOME>/.env`, and
  `<HINDSIGHT_CONFIG>` from the relevant private backup directory if rolling
  back memory config.
- Remove or rotate `<LITELLM_AUTH_FILE>` only when retiring this bridge, not
  during a normal rebind.
