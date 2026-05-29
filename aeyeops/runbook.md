# AeyeOps Runbook — hermes-agent fork

Operational notes distilled from running this fork against a live gateway.
Everything here is generic — no host names, user names, IPs, paths beyond
`$HERMES_HOME`, or tenant identifiers — so the file stays safe in a public
fork. Keep it that way when extending.

## Where hermes actually logs

Hermes installs its own `logging.FileHandler`, so `journalctl -u
hermes-gateway` only shows what reaches stdout/stderr *around* the
application — systemd state transitions, crashes that bypass the handler,
subprocess output.

Application-level events land in files:

| File | Contents |
|---|---|
| `$HERMES_HOME/logs/agent.log` | INFO/DEBUG from every adapter, router, tool |
| `$HERMES_HOME/logs/errors.log` | ERROR + tracebacks |

Practical consequence: post-restart health checks anchored to `journalctl
-u hermes-gateway \| grep -iE 'ERROR\|Traceback'` will miss real adapter
failures *and* produce false positives (shutdown-tail noise plus `ps`-style
journal lines that echo the monitor command's own argv as literal text).
Use the files.

## Post-restart verification sequence

```bash
# 1. Anchor to the new start time — NOT a relative "5 minutes ago" window.
ts=$(systemctl show hermes-gateway -p ActiveEnterTimestamp --value)

# 2. Each configured platform must transition to "connected" in
#    gateway_state.json within ~30s (WhatsApp bridge subprocess is slowest).
sudo cat $HERMES_HOME/gateway_state.json | python3 -m json.tool

# 3. Scan application errors *since* the new start.
sudo awk -v ts="$ts" 'index($0, substr(ts,1,19)) || p; /'$(date -d "$ts" +%Y-%m-%d)'/{p=1}' \
    $HERMES_HOME/logs/errors.log
```

Expected benign noise to filter:

- `[Whatsapp] WhatsApp bridge process exited unexpectedly (code -15)` at
  shutdown — the previous instance's bridge receiving SIGTERM during the
  restart handshake. Not an indicator of anything wrong with the new
  instance.

## Env config lifecycle

`$HERMES_HOME/.env` is the single source of truth for platform credentials
and provider API keys. The systemd unit does NOT declare `EnvironmentFile=`
— hermes loads the file programmatically via
`hermes_cli.env_loader.load_hermes_dotenv`.

Consequences when debugging:

- `/proc/<pid>/environ` on the running gateway shows **only** systemd's
  direct `Environment=` lines (`HOME`, `PATH`, `HERMES_HOME`, …). It does
  not show `TELEGRAM_BOT_TOKEN`, `GOOGLECHAT_SERVICE_ACCOUNT_JSON`, etc.
  That absence is not a bug.
- To test whether an env change "takes" without restarting, invoke
  `load_hermes_dotenv()` explicitly in a Python interpreter with the same
  `HERMES_HOME`, then import `load_gateway_config()` and inspect
  `cfg.get_connected_platforms()`.

A platform is declaratively on/off based on its auth env vars. Empty
`KEY=` is equivalent to absent — no feature activation.

## Safe `.env` edits

1. **Back up before mutating:**
   ```
   sudo cp -p $HERMES_HOME/.env \
       $HERMES_HOME/.env.bak-pre-<op>-$(date +%Y%m%d-%H%M%S)
   ```
2. Prefer targeted ops (`sed -i '/^KEY=/d'`, appended heredocs via `tee
   -a`) over full-file reads. `cat`/`tail` of the whole file echoes every
   API key into whatever terminal or transcript is watching.
3. Rotate any secret that surfaces in visible output. Terminal output can
   be cached, paged, or recorded; assume one accidental `tail` is
   compromise.

## Platform locks

Every adapter acquires a named lock at connect-time on the external
resource it binds to (Pub/Sub subscription, WhatsApp session dir, Telegram
webhook, …). This prevents two gateway instances from racing on the same
resource.

"Lock acquisition failed" at connect-time means another hermes process is
alive and holding it. `ps -ef | grep hermes-agent` before debugging the
config.

## Adding or removing a platform mid-deployment

Minimum change for either direction:

1. Append (or delete) the platform's env vars in `$HERMES_HOME/.env`.
2. `sudo systemctl restart hermes-gateway`.
3. Confirm `gateway_state.json` reflects the new platform set — existing
   platforms should remain `connected` without flapping.

No systemd unit edit is needed. `get_connected_platforms()` reads env
state at startup and is the single gate.

## Codex auth rebind for LiteLLM-backed local memory

When Codex is refreshed on a machine, there are three local auth consumers that
must converge:

1. Codex CLI auth state.
2. Hermes `openai-codex` auth state.
3. The local LiteLLM bridge's ChatGPT auth state.

`hermes auth status openai-codex` is a useful check, but by itself it does not
copy a freshly minted Codex CLI token into LiteLLM. Run the fork-local rebind
script after a Codex relogin or whenever the bridge needs to adopt a fresh
already-minted token:

```bash
cp aeyeops/.env.example aeyeops/.env
$EDITOR aeyeops/.env
./aeyeops/scripts/rebind-codex-auth-to-litellm.py
```

The local `aeyeops/.env` file is ignored by git. It supplies host-specific
values such as `<HERMES_HOME>`, `<CODEX_HOME>`,
`<LITELLM_CHATGPT_AUTH_JSON>`, `<LITELLM_ENV_FILE>`, `<HERMES_BIN>`, and
`<LITELLM_SERVICE>`. Set `CODEX_REBIND_VALIDATE=true` when the normal run
should validate the LiteLLM model alias after restart.

Use `--dry-run` first when checking a new host or service layout:

```bash
./aeyeops/scripts/rebind-codex-auth-to-litellm.py --prefer newest --dry-run
```

Expected successful evidence:

- selected source is the newest usable local `codex` or `hermes` token;
- Hermes auth status reports `openai-codex: logged in`;
- the LiteLLM service restarts cleanly when `--restart-service` is used;
- validation sees the configured model alias in `/v1/models`;
- validation receives the exact chat response `rebind-ok`.

The script intentionally does **not** run a device-code login or browser login.
If it reports that the selected token is missing or expiring too soon, refresh
Codex first, then rerun the rebind.

## Rollback artifacts

When swapping the install tree (upstream → fork, fork → different fork,
version → version), preserve the prior tree **with its built venv** next
to the new one. The pre-built venv is the fast-rollback value — source is
reconstructable from backups, but the venv goes stale as upstream moves.

Convention:

```
$HERMES_HOME/hermes-agent/                       # current
$HERMES_HOME/hermes-agent.pre-<label>-<date>/    # prior, with venv intact
```

Drop a marker file at the root of the old tree (`RETIRE_ON_<date>.txt`) so
a future session has an explicit expiry. Retirement is a plain `sudo rm
-rf` on the dated directory.
