#!/usr/bin/env python3
"""Probe ADR-013 R1 with the narrowest Chat app read scope.

This script intentionally creates a real Workspace Events subscription in
the dogfood tenant, then deletes it. It avoids DWD and broad user-auth
scopes so a pass proves the read-only app-auth path is enough for R1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient import discovery
from googleapiclient.errors import HttpError


APP_READ_SCOPE = "https://www.googleapis.com/auth/chat.app.messages.readonly"
CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
EVENT_TYPE = "google.workspace.chat.message.v1.created"
DEFAULT_KEY = Path.home() / ".mood-media-assistant" / "key.json"
DEFAULT_SPACE = "spaces/AAQA2N6jyoA"
DEFAULT_TOPIC = "projects/sa-mm-gchatbot/topics/chat-events"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "provisioning" / "requirements.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _space_target(space: str) -> str:
    if space.startswith("//chat.googleapis.com/spaces/"):
        return space
    space_id = space.removeprefix("spaces/")
    return f"//chat.googleapis.com/spaces/{space_id}"


def _build_service(key_path: Path, scopes: list[str]) -> Any:
    credentials = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=scopes
    )
    return discovery.build(
        "workspaceevents", "v1", credentials=credentials, cache_discovery=False
    )


def _load_key_metadata(key_path: Path) -> dict[str, Any]:
    key_data = json.loads(key_path.read_text(encoding="utf-8"))
    return {
        "client_email": key_data.get("client_email"),
        "client_id": key_data.get("client_id"),
        "project_id": key_data.get("project_id"),
        "token_uri": key_data.get("token_uri"),
    }


def _tokeninfo(key_path: Path, scopes: list[str]) -> dict[str, Any]:
    credentials = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=scopes
    )
    credentials.refresh(Request())
    query = urllib.parse.urlencode({"access_token": credentials.token})
    with urllib.request.urlopen(  # noqa: S310 - Google token introspection endpoint.
        f"https://oauth2.googleapis.com/tokeninfo?{query}", timeout=15
    ) as response:
        body = response.read().decode("utf-8")
    token_data = json.loads(body)
    return {
        "issued_to": token_data.get("issued_to"),
        "audience": token_data.get("audience"),
        "scope": token_data.get("scope"),
        "expires_in": token_data.get("expires_in"),
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _manifest_get(manifest: dict[str, Any], keys: list[str], default: Any) -> Any:
    current: Any = manifest
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _default_target_space(manifest: dict[str, Any]) -> str:
    return _manifest_get(manifest, ["spaces", "dogfood", "id"], DEFAULT_SPACE)


def _default_topic(manifest: dict[str, Any]) -> str:
    return _manifest_get(manifest, ["pubsub", "topic"], DEFAULT_TOPIC)


def _default_key(manifest: dict[str, Any]) -> Path:
    raw_path = _manifest_get(
        manifest,
        ["integration", "service_account_key_default"],
        str(DEFAULT_KEY),
    )
    return Path(str(raw_path)).expanduser()


def _r1_scopes(manifest: dict[str, Any]) -> list[str]:
    scopes = _manifest_get(
        manifest,
        ["capability_scopes", "R1_workspace_events_message_created", "candidate_scopes"],
        [APP_READ_SCOPE],
    )
    if isinstance(scopes, list) and scopes:
        return [str(scope) for scope in scopes]
    return [APP_READ_SCOPE]


def _r1_event_type(manifest: dict[str, Any]) -> str:
    event_types = _manifest_get(
        manifest,
        ["capability_scopes", "R1_workspace_events_message_created", "event_types"],
        [EVENT_TYPE],
    )
    if isinstance(event_types, list) and event_types:
        return str(event_types[0])
    return EVENT_TYPE


def _execute(request: Any) -> dict[str, Any]:
    return request.execute()


def _wait_operation(service: Any, operation_name: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        operation = _execute(service.operations().get(name=operation_name))
        if operation.get("done"):
            return operation
        if time.monotonic() >= deadline:
            raise TimeoutError(f"operation did not complete within {timeout_seconds}s: {operation_name}")
        time.sleep(2)


def _subscription_name_from_operation(operation: dict[str, Any]) -> str | None:
    response = operation.get("response") or {}
    if response.get("name", "").startswith("subscriptions/"):
        return response["name"]
    metadata = operation.get("metadata") or {}
    if metadata.get("subscription", "").startswith("subscriptions/"):
        return metadata["subscription"]
    return None


def _classify_http_error(exc: HttpError) -> dict[str, Any]:
    content = exc.content.decode("utf-8", errors="replace") if exc.content else ""
    lowered = content.lower()
    if exc.resp.status == 403 and "administrator" in lowered and "scope" in lowered:
        classification = "admin_approval_required"
    elif exc.resp.status == 403:
        classification = "forbidden"
    elif exc.resp.status == 400:
        classification = "bad_request"
    else:
        classification = "http_error"
    try:
        parsed_content: Any = json.loads(content)
    except json.JSONDecodeError:
        parsed_content = content
    return {
        "classification": classification,
        "status": exc.resp.status,
        "reason": exc.resp.reason,
        "content": parsed_content,
    }


def _write_result(result: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-%f")
    path = RESULTS_DIR / f"r1-app-scope-{stamp}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--space")
    parser.add_argument("--topic")
    parser.add_argument(
        "--scope",
        action="append",
        help="Override candidate scope. Repeat for multi-scope diagnostics.",
    )
    parser.add_argument(
        "--skip-tokeninfo",
        action="store_true",
        help="Skip OAuth token introspection.",
    )
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    key_path = args.key.expanduser() if args.key else _default_key(manifest)
    space = args.space or _default_target_space(manifest)
    topic = args.topic or _default_topic(manifest)
    scopes = args.scope or _r1_scopes(manifest)
    event_type = _r1_event_type(manifest)

    result: dict[str, Any] = {
        "probe": "R1 Workspace Events app-auth read-only scope",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "manifest": str(args.manifest),
        "candidate_scopes": scopes,
        "cleanup_scope": CHAT_BOT_SCOPE,
        "space": space,
        "target_resource": _space_target(space),
        "topic": topic,
        "event_type": event_type,
        "outcome": "unknown",
    }

    if not key_path.exists():
        result["outcome"] = "missing_service_account_key"
        result["error"] = str(key_path)
        path = _write_result(result)
        print(f"result={path}")
        return 2

    result["service_account"] = _load_key_metadata(key_path)
    if not args.skip_tokeninfo:
        try:
            result["tokeninfo"] = _tokeninfo(key_path, scopes)
        except Exception as exc:  # noqa: BLE001
            result["tokeninfo_error"] = f"{type(exc).__name__}: {exc}"

    body = {
        "targetResource": _space_target(space),
        "eventTypes": [event_type],
        "notificationEndpoint": {"pubsubTopic": topic},
        "payloadOptions": {"includeResource": True},
    }

    subscription_name: str | None = None
    try:
        service = _build_service(key_path, scopes)
        operation = _execute(service.subscriptions().create(body=body))
        result["create_operation"] = operation
        operation_name = operation.get("name")
        if operation_name:
            operation = _wait_operation(service, operation_name, args.timeout)
            result["create_operation_final"] = operation
        if operation.get("error"):
            result["outcome"] = "operation_error"
            result["error"] = operation["error"]
        else:
            subscription_name = _subscription_name_from_operation(operation)
            result["subscription_name"] = subscription_name
            response = operation.get("response") or {}
            result["expire_time"] = response.get("expireTime")
            result["authority"] = response.get("authority")
            result["outcome"] = "created"
    except HttpError as exc:
        result["outcome"] = "http_error"
        result["http_error"] = _classify_http_error(exc)
    except Exception as exc:  # noqa: BLE001
        result["outcome"] = "exception"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if subscription_name and not args.no_cleanup:
            try:
                cleanup_service = _build_service(key_path, [CHAT_BOT_SCOPE])
                _execute(cleanup_service.subscriptions().delete(name=subscription_name))
                result["cleanup"] = "deleted"
            except Exception as exc:  # noqa: BLE001
                result["cleanup"] = f"failed: {type(exc).__name__}: {exc}"

    path = _write_result(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"result={path}")
    return 0 if result["outcome"] == "created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
