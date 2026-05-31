#!/usr/bin/env python3
"""Rebind already-minted Codex auth into Hermes and a local LiteLLM bridge.

This is intentionally a local adaptation script, not Hermes core behavior.
It assumes the operator has already refreshed Codex on the machine when
needed.  The script then makes the rest of the local stack converge:

    Codex CLI auth.json -> Hermes auth.json -> LiteLLM ChatGPT auth.json

No interactive login is performed here.  Secrets are never printed.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import pwd
import grp
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
SCRIPT_DIR = Path(__file__).resolve().parent
AEEYEOPS_DIR = SCRIPT_DIR.parent


def fail(message: str) -> None:
    print(f"[error] {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    load_local_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Sync a fresh local Codex token into Hermes and a LiteLLM ChatGPT "
            "auth file, then optionally restart and validate the bridge."
        )
    )
    parser.add_argument(
        "--hermes-home",
        default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")),
        help="Hermes state directory. Defaults to HERMES_HOME or ~/.hermes.",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        help="Codex CLI state directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--litellm-auth",
        default=os.environ.get("LITELLM_CHATGPT_AUTH", ""),
        help=(
            "LiteLLM ChatGPT auth.json path. May also be supplied with "
            "LITELLM_CHATGPT_AUTH."
        ),
    )
    parser.add_argument(
        "--litellm-env",
        default=os.environ.get("LITELLM_ENV_FILE", ""),
        help=(
            "Optional LiteLLM env file containing LITELLM_MASTER_KEY or "
            "MASTER_KEY. May also be supplied with LITELLM_ENV_FILE."
        ),
    )
    parser.add_argument(
        "--litellm-base-url",
        default=os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1"),
        help="OpenAI-compatible LiteLLM base URL used for validation.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LITELLM_MODEL_ALIAS", "hindsight-codex"),
        help="Model alias expected from the LiteLLM bridge.",
    )
    parser.add_argument(
        "--prefer",
        choices=("newest", "codex", "hermes"),
        default=os.environ.get("CODEX_REBIND_PREFER", "newest"),
        help="Which usable token source to bind when both exist.",
    )
    parser.add_argument(
        "--min-ttl-seconds",
        type=int,
        default=int(os.environ.get("CODEX_REBIND_MIN_TTL_SECONDS", "300")),
        help="Refuse to bind a token expiring sooner than this.",
    )
    parser.add_argument(
        "--backup-root",
        default=os.environ.get("CODEX_REBIND_BACKUP_ROOT", ""),
        help="Backup root. Defaults to <hermes-home>/backups.",
    )
    parser.add_argument(
        "--hermes-bin",
        default=os.environ.get("HERMES_BIN", "hermes"),
        help="Hermes executable for the optional status check.",
    )
    parser.add_argument(
        "--skip-hermes-status",
        action="store_true",
        help="Do not run 'hermes auth status openai-codex' after syncing Hermes auth.",
    )
    parser.add_argument(
        "--restart-service",
        default=os.environ.get("LITELLM_SERVICE", ""),
        help="Optional systemd service name to restart after writing LiteLLM auth.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        default=env_bool("CODEX_REBIND_VALIDATE", False),
        help="Validate /v1/models and one chat completion through LiteLLM.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the planned source and targets without writing files or restarting.",
    )
    return parser.parse_args()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_local_dotenv() -> None:
    """Load AEyeOps local env defaults before argparse reads os.environ.

    The intended local file is ``aeyeops/.env``.  It is ignored by git.  When
    python-dotenv is available (Hermes includes it), use it.  Otherwise fall
    back to a small parser so the script remains runnable from a plain system
    Python while still honoring the same file for simple KEY=value entries.
    """

    configured = os.environ.get("AEYEOPS_ENV_FILE", "").strip()
    env_path = Path(configured).expanduser() if configured else AEEYEOPS_DIR / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_path, override=False)
        return
    except Exception:
        pass
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("\"'")


def utc_now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def decode_jwt(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}


def token_exp(token: str | None) -> int | None:
    exp = decode_jwt(token).get("exp")
    if isinstance(exp, (int, float)):
        return int(exp)
    return None


def iso_from_epoch(value: int | float | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def account_id_from_token(token: str | None) -> str | None:
    claims = decode_jwt(token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict) and isinstance(auth.get("chatgpt_account_id"), str):
        return auth["chatgpt_account_id"]
    return None


def safe_fingerprint(value: str | None) -> str:
    if not value:
        return "unknown"
    return f"...{value[-6:]}"


def parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            return float(raw)
        except ValueError:
            pass
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise
    except Exception as exc:
        fail(f"Could not read JSON from {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"{path} did not contain a JSON object")
    return data


def extract_hermes_tokens(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = read_json(path)
    state = (payload.get("providers") or {}).get("openai-codex")
    if not isinstance(state, dict):
        return None
    tokens = state.get("tokens")
    if not isinstance(tokens, dict):
        return None
    return {
        "name": "hermes",
        "path": path,
        "tokens": dict(tokens),
        "last_refresh": state.get("last_refresh"),
        "mtime": path.stat().st_mtime,
    }


def extract_codex_tokens(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = read_json(path)
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    return {
        "name": "codex",
        "path": path,
        "tokens": dict(tokens),
        "last_refresh": payload.get("last_refresh"),
        "mtime": path.stat().st_mtime,
    }


def validate_source(source: dict[str, Any], min_ttl_seconds: int) -> dict[str, Any]:
    def invalid(message: str) -> None:
        raise ValueError(message)

    tokens = source["tokens"]
    access = str(tokens.get("access_token") or "")
    refresh = str(tokens.get("refresh_token") or "")
    ident = str(tokens.get("id_token") or "")
    if not access or not refresh or not ident:
        invalid(f"{source['name']} auth is missing access_token, refresh_token, or id_token")
    exp = token_exp(access)
    if exp is None:
        invalid(f"{source['name']} access token has no decodable exp claim")
    now = int(time.time())
    if exp <= now + min_ttl_seconds:
        invalid(
            f"{source['name']} access token expires too soon "
            f"({iso_from_epoch(exp)}); refresh Codex first"
        )
    account_id = (
        str(tokens.get("account_id") or "")
        or account_id_from_token(ident)
        or account_id_from_token(access)
    )
    if not account_id:
        invalid(f"{source['name']} token has no decodable ChatGPT account id")
    source = dict(source)
    source["expires_at"] = exp
    source["account_id"] = account_id
    return source


def choose_source(
    hermes_source: dict[str, Any] | None,
    codex_source: dict[str, Any] | None,
    prefer: str,
    min_ttl_seconds: int,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    invalid: dict[str, str] = {}
    for src in (hermes_source, codex_source):
        if src is not None:
            try:
                sources.append(validate_source(src, min_ttl_seconds))
            except ValueError as exc:
                invalid[src["name"]] = str(exc)
    if not sources:
        detail = "; ".join(f"{name}: {reason}" for name, reason in sorted(invalid.items()))
        fail(f"No usable Hermes or Codex auth tokens found{': ' + detail if detail else ''}")
    if prefer != "newest":
        for src in sources:
            if src["name"] == prefer:
                return src
        if prefer in invalid:
            fail(f"Requested --prefer {prefer}, but that source is unusable: {invalid[prefer]}")
        fail(f"Requested --prefer {prefer}, but that source is unavailable")
    for name, reason in sorted(invalid.items()):
        print(f"[observed] skipped_unusable_source={name}: {reason}")
    return max(sources, key=lambda src: parse_time(src.get("last_refresh")) or src.get("mtime", 0.0))


def ensure_backup(paths: list[Path], backup_root: Path) -> Path:
    backup_dir = backup_root / f"codex-litellm-rebind-{utc_now_stamp()}"
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        dest = backup_dir / path.name
        shutil.copy2(path, dest)
        dest.chmod(0o600)
    return backup_dir


def atomic_write_json(path: Path, payload: dict[str, Any], *, owner_from: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    uid = gid = None
    mode = 0o600
    if owner_from and owner_from.exists():
        stat = owner_from.stat()
        uid, gid, mode = stat.st_uid, stat.st_gid, stat.st_mode & 0o777
    owner_spec = os.environ.get("LITELLM_AUTH_OWNER", "").strip()
    group_spec = os.environ.get("LITELLM_AUTH_GROUP", "").strip()
    if owner_spec:
        uid = pwd.getpwnam(owner_spec).pw_uid if not owner_spec.isdigit() else int(owner_spec)
    if group_spec:
        gid = grp.getgrnam(group_spec).gr_gid if not group_spec.isdigit() else int(group_spec)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
            handle.write("\n")
        if uid is not None or gid is not None:
            os.chown(tmp_path, uid if uid is not None else -1, gid if gid is not None else -1)
        tmp_path.chmod(mode or 0o600)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def sync_hermes_auth(hermes_auth: Path, source: dict[str, Any]) -> None:
    payload = read_json(hermes_auth) if hermes_auth.exists() else {"version": 1}
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        fail("Hermes auth providers field is not an object")
    state = providers.setdefault("openai-codex", {})
    if not isinstance(state, dict):
        fail("Hermes openai-codex auth state is not an object")
    tokens = dict(source["tokens"])
    state["tokens"] = tokens
    state["last_refresh"] = source.get("last_refresh") or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    state["auth_mode"] = "chatgpt"
    payload["active_provider"] = payload.get("active_provider") or "openai-codex"
    payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    pool = payload.setdefault("credential_pool", {})
    if isinstance(pool, dict):
        entries = pool.setdefault("openai-codex", [])
        if isinstance(entries, list):
            entry = None
            for item in entries:
                if isinstance(item, dict) and item.get("source") == "device_code":
                    entry = item
                    break
            if entry is None:
                entry = {
                    "id": "device",
                    "label": "device_code",
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": "device_code",
                    "base_url": DEFAULT_CODEX_BASE_URL,
                    "request_count": 0,
                }
                entries.insert(0, entry)
            entry.update(
                {
                    "access_token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                    "base_url": entry.get("base_url") or DEFAULT_CODEX_BASE_URL,
                    "last_refresh": state["last_refresh"],
                    "last_status": None,
                    "last_status_at": None,
                    "last_error_code": None,
                    "last_error_reason": None,
                    "last_error_message": None,
                    "last_error_reset_at": None,
                }
            )

    atomic_write_json(hermes_auth, payload, owner_from=hermes_auth if hermes_auth.exists() else None)


def write_litellm_auth(litellm_auth: Path, source: dict[str, Any]) -> None:
    tokens = source["tokens"]
    target = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "id_token": tokens["id_token"],
        "expires_at": source["expires_at"],
        "account_id": source["account_id"],
    }
    atomic_write_json(litellm_auth, target, owner_from=litellm_auth if litellm_auth.exists() else None)


def load_master_key(env_file: Path | None) -> str | None:
    for key in ("LITELLM_MASTER_KEY", "MASTER_KEY"):
        value = os.environ.get(key)
        if value:
            return value
    if env_file and env_file.is_file():
        for raw in env_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"LITELLM_MASTER_KEY", "MASTER_KEY"}:
                return value.strip().strip("\"'")
    return None


def request_json(url: str, *, master_key: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {master_key}"}
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_for_litellm(base_url: str, master_key: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    base = base_url.rstrip("/")
    while time.time() < deadline:
        try:
            request_json(f"{base}/models", master_key=master_key, timeout=5)
            return
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
        except TimeoutError as exc:
            last_error = str(exc)
        time.sleep(1)
    fail(f"LiteLLM did not become ready within {timeout_seconds}s: {last_error}")


def validate_litellm(base_url: str, model: str, env_file: Path | None) -> None:
    master_key = load_master_key(env_file)
    if not master_key:
        fail("LiteLLM validation requested but no master key was found")
    base = base_url.rstrip("/")
    wait_for_litellm(base, master_key)
    models = request_json(f"{base}/models", master_key=master_key, timeout=30)
    ids = [item.get("id") for item in models.get("data", []) if isinstance(item, dict)]
    if model not in ids:
        fail(f"{model!r} not present in LiteLLM /models response")
    chat = request_json(
        f"{base}/chat/completions",
        master_key=master_key,
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: rebind-ok"}],
            "temperature": 0,
            "max_tokens": 20,
        },
        timeout=180,
    )
    content = (((chat.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if content != "rebind-ok":
        fail("LiteLLM exact-response validation failed")


def main() -> None:
    args = parse_args()
    hermes_home = Path(args.hermes_home).expanduser()
    codex_home = Path(args.codex_home).expanduser()
    hermes_auth = hermes_home / "auth.json"
    codex_auth = codex_home / "auth.json"
    litellm_auth = Path(args.litellm_auth).expanduser() if args.litellm_auth else None
    if litellm_auth is None:
        fail("LiteLLM auth path is required: pass --litellm-auth or LITELLM_CHATGPT_AUTH")
    litellm_env = Path(args.litellm_env).expanduser() if args.litellm_env else None
    backup_root = Path(args.backup_root).expanduser() if args.backup_root else hermes_home / "backups"

    source = choose_source(
        extract_hermes_tokens(hermes_auth),
        extract_codex_tokens(codex_auth),
        args.prefer,
        args.min_ttl_seconds,
    )
    print(f"[observed] selected_source={source['name']}")
    print(f"[observed] selected_source_path={source['path']}")
    print(f"[observed] source_last_refresh={source.get('last_refresh')}")
    print(f"[observed] source_account={safe_fingerprint(source.get('account_id'))}")
    print(f"[observed] source_expires_at_utc={iso_from_epoch(source.get('expires_at'))}")
    print(f"[observed] hermes_auth_target={hermes_auth}")
    print(f"[observed] litellm_auth_target={litellm_auth}")

    if args.dry_run:
        print("[observed] dry_run=PASS")
        return

    backup_dir = ensure_backup([hermes_auth, codex_auth, litellm_auth], backup_root)
    sync_hermes_auth(hermes_auth, source)
    write_litellm_auth(litellm_auth, source)
    print(f"[observed] backup_dir={backup_dir}")
    print("[observed] wrote_hermes_auth=PASS")
    print("[observed] wrote_litellm_auth=PASS")

    if not args.skip_hermes_status:
        subprocess.run([args.hermes_bin, "auth", "status", "openai-codex"], check=True)

    if args.restart_service:
        subprocess.run(["systemctl", "restart", args.restart_service], check=True)
        subprocess.run(["systemctl", "is-active", "--quiet", args.restart_service], check=True)
        print(f"[observed] restarted_service={args.restart_service}")

    if args.validate:
        validate_litellm(args.litellm_base_url, args.model, litellm_env)
        print("[observed] litellm_validation=PASS")

    print("[observed] rebind=PASS")


if __name__ == "__main__":
    main()
