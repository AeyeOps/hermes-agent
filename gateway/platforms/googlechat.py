"""
Google Chat platform adapter (Workspace).

Transport: Cloud Pub/Sub pull subscription (ADR-007). Chat API publishes
space events (MESSAGE, ADDED_TO_SPACE, REMOVED_FROM_SPACE, CARD_CLICKED)
to a topic; this adapter pulls from a subscription bound to that topic.

Authentication: Google Cloud service account JSON key (ADR-008). The
operator provisions the service account and grants roles/pubsub.subscriber
on the subscription; the adapter never creates IAM bindings.

Scope: I/O only per ADR-003 — connect, receive+normalize, dispatch via
self.handle_message(), send. Plugins, hooks, skills, and tools are owned
by the gateway core.

Required env (see aeyeops/googlechat/design.md):
    GOOGLECHAT_SERVICE_ACCOUNT_JSON — path to the SA key JSON
    GOOGLECHAT_PUBSUB_PROJECT       — GCP project containing the subscription
    GOOGLECHAT_PUBSUB_SUBSCRIPTION  — subscription ID (without project prefix)
    GOOGLECHAT_HOME_CHANNEL         — default spaces/<id> for cron/notifications
    GOOGLECHAT_BOT_USER_ID          — optional users/<id> for self-message filter
"""

import asyncio
import io
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from google.cloud import pubsub_v1  # noqa: F401
    from google.oauth2 import service_account  # noqa: F401
    from googleapiclient import discovery  # noqa: F401
    from googleapiclient.http import (  # noqa: F401
        MediaIoBaseDownload,
        MediaIoBaseUpload,
    )

    GOOGLECHAT_AVAILABLE = True
except ImportError:
    GOOGLECHAT_AVAILABLE = False
    pubsub_v1 = None  # type: ignore[assignment]
    service_account = None  # type: ignore[assignment]
    discovery = None  # type: ignore[assignment]
    MediaIoBaseDownload = None  # type: ignore[assignment]
    MediaIoBaseUpload = None  # type: ignore[assignment]

# Google's public Chat API reference does not document an explicit limit on
# the `text` field as of 2026-04-20; the practical ceiling cited by Chat
# Community threads is ~32,000 bytes for the whole message resource. We
# chunk at 4,096 characters by default — comfortably under 32 KB even when
# the content is multi-byte UTF-8 — and let operators tune via
# config.extra["max_message_length"] or GOOGLECHAT_MAX_MESSAGE_LENGTH when
# Google eventually publishes an exact number.
#
# Refs:
#   https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create
#   https://support.google.com/chat/thread/228198957
GOOGLE_CHAT_MAX_MESSAGE_LENGTH = 4096

# Per-space write quota documented by Google is 1 write/sec (see reference
# above). Multi-chunk sends pace themselves at this interval so they don't
# immediately trigger 429 on their own tail. Single-chunk sends pay zero
# added latency. The base adapter's _send_with_retry handles retries for
# 429 on the first chunk via the retryable flag returned in SendResult.
_PER_SPACE_QPS_DELAY_SECONDS = 1.1

# Baseline scope — ADR-008, works for send() and media.download.
CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"

# Capability-gated scopes per ADR-013. Each requires one-time Workspace admin
# authorization of the Chat app; until that's granted they'll 403 at runtime.
# The adapter keeps them off by default so a fresh deployment on chat.bot
# alone continues to work; enabling a capability widens the scope set only
# for that feature's flag.
CHAT_APP_MESSAGES_SCOPE = "https://www.googleapis.com/auth/chat.app.messages"
CHAT_APP_MESSAGES_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/chat.app.messages.readonly"
)
CHAT_APP_SPACES_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/chat.app.spaces.readonly"
)
CHAT_APP_MEMBERSHIPS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/chat.app.memberships.readonly"
)

CHAT_API_SCOPES = [CHAT_BOT_SCOPE]

# Workspace Events API base URL. The endpoint is distinct from Chat's own
# REST base, so we build a separate discovery client for it when R1 is on.
WORKSPACE_EVENTS_API = "workspaceevents"
WORKSPACE_EVENTS_VERSION = "v1"
WORKSPACE_EVENTS_MESSAGE_CREATED = "google.workspace.chat.message.v1.created"
WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS = 24 * 3600
WORKSPACE_EVENTS_RENEWAL_WINDOW_SECONDS = 3600
WORKSPACE_EVENTS_RENEWAL_CHECK_SECONDS = 300

# Lifecycle reaction emoji (R3). Defaults chosen to match Discord/Matrix —
# the enterprise majority — rather than Telegram's 👍/👎. Configurable via
# env for operators who disagree after dogfood.
REACTION_EMOJI_PROCESSING = "\U0001f440"  # 👀
REACTION_EMOJI_SUCCESS = "✅"  # ✅
REACTION_EMOJI_FAILURE = "❌"  # ❌

# Size cap for in-flight reaction state. One entry per user message currently
# being processed; 1000 matches MessageDeduplicator's default window.
REACTION_STATE_MAX_SIZE = 1000

GOOGLECHAT_PLACEHOLDER_TEXT = "Working..."
GOOGLECHAT_PLACEHOLDER_STOPPED_TEXT = "Stopped."

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    SUPPORTED_DOCUMENT_TYPES,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
    cache_video_from_bytes,
    resolve_channel_prompt,
)
from gateway.platforms.helpers import MessageDeduplicator

logger = logging.getLogger(__name__)

_MARKDOWN_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_MARKDOWN_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^*\n][\s\S]*?[^*\n])\*\*")
_MARKDOWN_STRIKE_RE = re.compile(r"~~([^~\n][\s\S]*?[^~\n])~~")


def format_googlechat_markdown(content: str) -> str:
    """Translate common Markdown into Google Chat's limited text markup."""
    if not content:
        return content

    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00GC{len(protected) - 1}\x00"

    text = _MARKDOWN_CODE_BLOCK_RE.sub(_protect, content)
    text = _MARKDOWN_LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)
    text = _MARKDOWN_HEADER_RE.sub(lambda m: f"*{m.group(2).strip()}*", text)
    text = _MARKDOWN_BOLD_RE.sub(lambda m: f"*{m.group(1)}*", text)
    text = _MARKDOWN_STRIKE_RE.sub(lambda m: f"~{m.group(1)}~", text)

    for idx, value in enumerate(protected):
        text = text.replace(f"\x00GC{idx}\x00", value)
    return text


def _env_flag(
    extra: Dict[str, Any], extra_key: str, env_var: str, default: bool = False
) -> bool:
    """Coerce a boolean flag out of config.extra or an env var."""
    raw = extra.get(extra_key)
    if raw is None:
        raw = os.getenv(env_var)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def check_googlechat_requirements(config: Optional[PlatformConfig] = None) -> bool:
    """Return True when SDKs are importable and service-account config exists."""
    if not GOOGLECHAT_AVAILABLE:
        return False
    extra = (config.extra or {}) if config else {}
    if not (extra.get("service_account_json") or os.getenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON")):
        return False
    return True


class _SubscriptionRegistry:
    """In-memory tracking of Workspace Events subscriptions per Chat space.

    Value shape: {space_id: (subscription_name, expire_epoch_seconds)}. The
    registry is pure bookkeeping — actual subscription mutation goes through
    the adapter's `_subscribe_space` / `_unsubscribe_space` helpers.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, float]] = {}

    def add(self, space_id: str, subscription_name: str, expire_epoch: float) -> None:
        self._entries[space_id] = (subscription_name, expire_epoch)

    def remove(self, space_id: str) -> Optional[str]:
        entry = self._entries.pop(space_id, None)
        return entry[0] if entry else None

    def get(self, space_id: str) -> Optional[tuple[str, float]]:
        return self._entries.get(space_id)

    def spaces(self) -> list[str]:
        return list(self._entries.keys())

    def items(self) -> list[tuple[str, str, float]]:
        return [
            (space_id, name, expire)
            for space_id, (name, expire) in self._entries.items()
        ]

    def needs_renewal_within(self, window_seconds: float, now_epoch: float) -> list[str]:
        threshold = now_epoch + window_seconds
        return [
            space_id
            for space_id, (_name, expire) in self._entries.items()
            if expire <= threshold
        ]


class GoogleChatAdapter(BasePlatformAdapter):
    """Google Chat (Workspace) adapter — Pub/Sub pull inbound, Chat REST outbound.

    C6+ state: inbound MESSAGE normalized + dispatched; lifecycle events update
    Workspace Events subscriptions when approved; CARD_CLICKED is synthesized as
    a normal text turn. send() and text-first edit streaming use Chat REST.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.GOOGLECHAT)

        extra = config.extra or {}
        self._service_account_path: str = extra.get(
            "service_account_json"
        ) or os.getenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", "")
        self._pubsub_project: str = extra.get("pubsub_project") or os.getenv(
            "GOOGLECHAT_PUBSUB_PROJECT", ""
        )
        self._pubsub_subscription: str = extra.get("pubsub_subscription") or os.getenv(
            "GOOGLECHAT_PUBSUB_SUBSCRIPTION", ""
        )
        # Optional: operator-provided app user resource name for self-filter.
        # When unset, the self-filter silently no-ops — Chat's Pub/Sub stream
        # normally doesn't echo the bot's own REST-sent messages, so this is
        # belt-and-suspenders.
        self._bot_user_id: Optional[str] = extra.get("bot_user_id") or os.getenv(
            "GOOGLECHAT_BOT_USER_ID"
        )

        max_len_raw = extra.get("max_message_length") or os.getenv(
            "GOOGLECHAT_MAX_MESSAGE_LENGTH"
        )
        try:
            self._max_message_length: int = int(max_len_raw) if max_len_raw else GOOGLE_CHAT_MAX_MESSAGE_LENGTH
        except (TypeError, ValueError):
            logger.warning(
                "[%s] invalid max_message_length=%r; falling back to default %d",
                self.platform.value,
                max_len_raw,
                GOOGLE_CHAT_MAX_MESSAGE_LENGTH,
            )
            self._max_message_length = GOOGLE_CHAT_MAX_MESSAGE_LENGTH

        self._dedup = MessageDeduplicator(max_size=1000)
        self._subscriber: Any = None
        self._pull_future: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._chat_service: Any = None

        # Single gate for every capability that depends on Workspace-admin
        # approval of chat.app.* scopes (ADR-013): native attachment upload
        # (R2b), lifecycle reactions API calls (R3), and Workspace Events
        # subscriptions (R1). Default OFF — a fresh chat.bot deployment
        # keeps working, and these features are silently no-ops until the
        # operator flips this flag after admin approval.
        self._admin_approved: bool = _env_flag(
            extra,
            "admin_approved_scopes",
            "GOOGLECHAT_ADMIN_APPROVED_SCOPES",
        )

        # Reactions aesthetic preference (independent of auth). Peer parity:
        # Slack/Discord/Matrix default true; Telegram defaults false; 3/4
        # majority wins for us. _reactions_enabled() AND-combines this with
        # _admin_approved so the adapter never 403s the reactions API
        # because the operator forgot to set the admin-approval flag.
        self._reactions_preferred: bool = _env_flag(
            extra, "reactions", "GOOGLECHAT_REACTIONS", default=True
        )

        # Workspace Events (R1). pubsub_topic is the topic subscriptions
        # deliver into; required only when _admin_approved is on AND the
        # operator actually stands up subscriptions.
        self._pubsub_topic: str = extra.get("pubsub_topic") or os.getenv(
            "GOOGLECHAT_PUBSUB_TOPIC", ""
        )

        # Reaction state (R3). Keyed by user message.name; value is the
        # reactions/* resource name so the in-progress reaction can be
        # removed when processing completes.
        self._processing_reactions: dict[str, str] = {}

        # Workspace Events subscription registry (R1). Populated on connect
        # when _admin_approved is on.
        self._subscription_registry: "_SubscriptionRegistry" = (
            _SubscriptionRegistry()
        )
        self._renewal_task: Optional[asyncio.Task] = None
        self._workspace_events_service: Any = None
        self._typing_placeholders: dict[tuple[str, Optional[str]], str] = {}
        self._last_write_time: dict[tuple[str, Optional[str]], float] = {}

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        if not GOOGLECHAT_AVAILABLE:
            logger.warning(
                "[%s] google-cloud-pubsub / google-api-python-client not installed. "
                "Run: pip install 'hermes-agent[googlechat]'",
                self.name,
            )
            return False
        if not self._service_account_path:
            logger.warning(
                "[%s] GOOGLECHAT_SERVICE_ACCOUNT_JSON is not set",
                self.name,
            )
            return False
        if not self._pubsub_project or not self._pubsub_subscription:
            logger.warning(
                "[%s] GOOGLECHAT_PUBSUB_PROJECT and GOOGLECHAT_PUBSUB_SUBSCRIPTION must be set",
                self.name,
            )
            return False

        scope_identity = self._credential_lock_identity()
        if not self._acquire_platform_lock(
            scope="googlechat_bot_credential",
            identity=scope_identity,
            resource_desc=f"Google Chat bot credential {scope_identity}",
        ):
            return False

        self._loop = asyncio.get_running_loop()

        try:
            self._subscriber = pubsub_v1.SubscriberClient.from_service_account_file(
                self._service_account_path
            )
            subscription_path = self._subscriber.subscription_path(
                self._pubsub_project, self._pubsub_subscription
            )
            self._pull_future = self._subscriber.subscribe(
                subscription_path, callback=self._on_pubsub_message
            )
        except Exception:
            logger.exception("[%s] failed to start Pub/Sub subscriber", self.name)
            self._release_platform_lock()
            return False

        logger.info(
            "[%s] pulling from %s/%s",
            self.name,
            self._pubsub_project,
            self._pubsub_subscription,
        )
        self._running = True

        # R1 bootstrap: list spaces the bot is in, subscribe each to
        # Workspace Events, start the renewal loop. Any failure here is
        # logged and swallowed — the classic @mention/DM path above is
        # what the adapter guarantees; R1 is an additive stream.
        if self._admin_approved:
            try:
                await self._bootstrap_workspace_events()
            except Exception:
                logger.exception(
                    "[%s] Workspace Events bootstrap failed; continuing "
                    "on Pub/Sub-only path",
                    self.name,
                )

        return True

    def _credential_lock_identity(self) -> str:
        """Return a stable lock key for the bot identity, not the subscription."""
        credential_path = os.path.abspath(os.path.expanduser(self._service_account_path))
        try:
            with open(credential_path, "r", encoding="utf-8") as fh:
                client_email = json.load(fh).get("client_email")
            if client_email:
                return str(client_email)
        except Exception:
            logger.debug("[%s] could not read service account identity for lock", self.name, exc_info=True)
        return credential_path

    async def disconnect(self) -> None:
        self._running = False
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            try:
                await self._renewal_task
            except (asyncio.CancelledError, Exception):
                pass
            self._renewal_task = None
        if self._pull_future is not None:
            try:
                self._pull_future.cancel()
                self._pull_future.result(timeout=5)
            except Exception:
                pass
            self._pull_future = None
        if self._subscriber is not None:
            try:
                self._subscriber.close()
            except Exception:
                pass
            self._subscriber = None
        self._release_platform_lock()

    # -- Inbound -----------------------------------------------------------

    def _on_pubsub_message(self, message: Any) -> None:
        """Thread-pool callback — decode, forward to the event loop, ack/nack."""
        try:
            payload = json.loads(message.data.decode("utf-8"))
        except Exception:
            logger.exception(
                "[%s] invalid Pub/Sub payload; acking to avoid poison redeliveries",
                self.name,
            )
            message.ack()
            return

        if self._loop is None:
            logger.error(
                "[%s] no event loop bound; nack so another worker can pick up",
                self.name,
            )
            message.nack()
            return

        future = asyncio.run_coroutine_threadsafe(
            self._handle_chat_event(payload), self._loop
        )
        try:
            future.result()
            message.ack()
        except Exception:
            logger.exception(
                "[%s] handler raised; nack for redelivery", self.name
            )
            message.nack()

    async def _handle_chat_event(self, payload: Dict[str, Any]) -> None:
        """Route a decoded Chat event payload by type.

        Handles three envelope shapes:
          * legacy direct Pub/Sub push (top-level `message` + `space`)
          * Chat App Event envelope (`chat.messagePayload` wraps legacy shape)
          * Workspace Events API delivery (CloudEvents-shaped — has `@type`
            or `ce-type`, message resource nested under `message`)
        """
        payload = _normalize_chat_envelope(payload)

        event_type = payload.get("type") or payload.get("eventType")
        # App Event envelope doesn't carry an eventType string — infer
        # MESSAGE from the presence of the message resource. Lifecycle
        # and card-click inference lands with their M2/M4 fixtures.
        if not event_type and payload.get("message"):
            event_type = "MESSAGE"
        if event_type == "MESSAGE":
            await self._handle_message_event(payload)
        elif event_type == "ADDED_TO_SPACE":
            space_name = (payload.get("space") or {}).get("name")
            logger.info(
                "[%s] lifecycle event=%s space=%s", self.name, event_type, space_name
            )
            if space_name and self._admin_approved:
                await self._subscribe_space_if_needed(space_name)
        elif event_type == "REMOVED_FROM_SPACE":
            space_name = (payload.get("space") or {}).get("name")
            logger.info(
                "[%s] lifecycle event=%s space=%s", self.name, event_type, space_name
            )
            if space_name and self._admin_approved:
                await self._unsubscribe_space_if_needed(space_name)
        elif event_type == "CARD_CLICKED":
            await self._handle_card_clicked_event(payload)
        else:
            logger.debug(
                "[%s] ignoring event type=%r", self.name, event_type
            )

    # -- Workspace Events (R1) --------------------------------------------

    async def _bootstrap_workspace_events(self) -> None:
        """Enumerate bot's spaces, create subscriptions, start renewal loop."""
        if not self._pubsub_topic:
            logger.warning(
                "[%s] skipping Workspace Events bootstrap: "
                "GOOGLECHAT_PUBSUB_TOPIC not set",
                self.name,
            )
            return

        try:
            spaces = await self._list_bot_spaces()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] failed to list bot spaces; Workspace Events idle: %s",
                self.name,
                exc,
            )
            return

        for space_id in spaces:
            try:
                await self._subscribe_space_if_needed(space_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] failed to subscribe space=%s: %s",
                    self.name,
                    space_id,
                    exc,
                )

        if self._renewal_task is None or self._renewal_task.done():
            self._renewal_task = asyncio.create_task(
                self._renew_subscriptions_loop(),
                name=f"{self.name}-we-renewal",
            )

    async def _list_bot_spaces(self) -> list[str]:
        """List every Chat space the bot is a member of.

        Uses spaces.list with filter `spaceType = "SPACE" OR spaceType = "GROUP_CHAT"`;
        DMs are excluded because mention-free listening there is moot.
        """
        service = self._ensure_chat_service()
        if service is None:
            return []
        loop = asyncio.get_running_loop()

        def _list() -> list[str]:
            spaces: list[str] = []
            page_token: Optional[str] = None
            while True:
                kwargs: Dict[str, Any] = {
                    "pageSize": 100,
                    "filter": 'spaceType = "SPACE" OR spaceType = "GROUP_CHAT"',
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = service.spaces().list(**kwargs).execute() or {}
                for space in resp.get("spaces") or []:
                    name = space.get("name")
                    if name:
                        spaces.append(name)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            return spaces

        return await loop.run_in_executor(None, _list)

    async def _subscribe_space_if_needed(self, space_id: str) -> None:
        if self._subscription_registry.get(space_id) is not None:
            return
        await self._subscribe_space(space_id)

    async def _subscribe_space(self, space_id: str) -> None:
        """Create a Workspace Events subscription for `space_id`.

        Uses the MESSAGE_CREATED event type and `includeResource=true` so
        the payload carries the full message body (no second fetch).
        """
        service = self._ensure_workspace_events_service()
        if service is None:
            return

        body = {
            "targetResource": _space_target_resource(space_id),
            "eventTypes": [WORKSPACE_EVENTS_MESSAGE_CREATED],
            "notificationEndpoint": {"pubsubTopic": self._pubsub_topic},
            "payloadOptions": {"includeResource": True},
            "ttl": f"{WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS}s",
        }
        loop = asyncio.get_running_loop()

        def _create() -> Dict[str, Any]:
            op = service.subscriptions().create(body=body).execute() or {}
            # Subscriptions are created through a long-running operation; for
            # simplicity we block on the returned operation if any.
            if op.get("done"):
                return op.get("response") or {}
            name = op.get("name")
            while name:
                op = service.operations().get(name=name).execute() or {}
                if op.get("done"):
                    if op.get("error"):
                        raise RuntimeError(
                            f"subscription create failed: {op['error']}"
                        )
                    return op.get("response") or {}
            return {}

        try:
            response = await loop.run_in_executor(None, _create)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] subscription create rejected for space=%s: %s",
                self.name,
                space_id,
                exc,
            )
            return

        subscription_name = response.get("name")
        expire_time = response.get("expireTime")
        expire_epoch = _parse_iso_epoch(expire_time) or (
            asyncio.get_event_loop().time() + WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS
        )
        if subscription_name:
            self._subscription_registry.add(
                space_id, subscription_name, expire_epoch
            )
            logger.info(
                "[%s] subscribed space=%s subscription=%s expires=%s",
                self.name,
                space_id,
                subscription_name,
                expire_time,
            )

    async def _unsubscribe_space_if_needed(self, space_id: str) -> None:
        name = self._subscription_registry.remove(space_id)
        if not name:
            return
        service = self._ensure_workspace_events_service()
        if service is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: service.subscriptions().delete(name=name).execute(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] failed to delete subscription=%s: %s",
                self.name,
                name,
                exc,
            )

    async def _renew_subscriptions_loop(self) -> None:
        """Background task: patch TTL on any subscription within the renewal window."""
        try:
            while self._running:
                try:
                    await self._renew_expiring_subscriptions()
                except Exception:
                    logger.exception(
                        "[%s] renewal loop iteration failed", self.name
                    )
                await asyncio.sleep(WORKSPACE_EVENTS_RENEWAL_CHECK_SECONDS)
        except asyncio.CancelledError:
            return

    async def _renew_expiring_subscriptions(self) -> None:
        import time

        now_epoch = time.time()
        expiring = self._subscription_registry.needs_renewal_within(
            WORKSPACE_EVENTS_RENEWAL_WINDOW_SECONDS, now_epoch
        )
        if not expiring:
            return

        service = self._ensure_workspace_events_service()
        if service is None:
            return
        loop = asyncio.get_running_loop()

        for space_id in expiring:
            entry = self._subscription_registry.get(space_id)
            if not entry:
                continue
            subscription_name, _ = entry

            def _patch(name: str = subscription_name) -> Dict[str, Any]:
                return (
                    service.subscriptions()
                    .patch(
                        name=name,
                        updateMask="ttl",
                        body={"ttl": f"{WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS}s"},
                    )
                    .execute()
                    or {}
                )

            try:
                response = await loop.run_in_executor(None, _patch)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] renewal patch failed space=%s name=%s: %s",
                    self.name,
                    space_id,
                    subscription_name,
                    exc,
                )
                continue

            new_expire = _parse_iso_epoch(response.get("expireTime")) or (
                now_epoch + WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS
            )
            self._subscription_registry.add(
                space_id, subscription_name, new_expire
            )

    async def _handle_message_event(self, payload: Dict[str, Any]) -> None:
        message = payload.get("message") or {}
        message_name = message.get("name", "") or ""
        if self._dedup.is_duplicate(message_name):
            return

        sender = message.get("sender") or {}
        sender_name = sender.get("name") or ""
        if (
            self._bot_user_id
            and sender_name
            and sender_name == self._bot_user_id
        ):
            # Self-filter: our own REST-sent messages shouldn't loop. Belt-and-
            # suspenders since Chat's Pub/Sub stream normally doesn't echo them.
            return

        space = payload.get("space") or {}
        space_name = space.get("name", "") or ""
        thread = message.get("thread") or {}
        thread_name = thread.get("name") or None
        # Chat API v1 reports spaceType ∈ {DIRECT_MESSAGE, GROUP_CHAT, SPACE};
        # the legacy `type` field (DM/ROOM) is deprecated but still emitted in
        # App Event envelopes, so accept either.
        space_type_raw = space.get("spaceType") or space.get("type") or ""
        chat_type = "dm" if space_type_raw in ("DM", "DIRECT_MESSAGE") else "group"

        source = self.build_source(
            chat_id=space_name,
            chat_name=space.get("displayName") or None,
            chat_type=chat_type,
            user_id=sender_name or None,
            user_name=sender.get("displayName") or None,
            thread_id=thread_name,
        )

        channel_prompt = resolve_channel_prompt(
            self.config.extra or {}, space_name, None
        )

        media_urls, media_types, attachment_message_type = (
            await self._hydrate_attachments(message.get("attachment") or [])
        )
        text = message.get("text") or ""
        message_type = MessageType.TEXT if text else attachment_message_type

        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            message_id=message_name or None,
            raw_message=payload,
            media_urls=media_urls,
            media_types=media_types,
            channel_prompt=channel_prompt,
        )
        await self.handle_message(event)

    async def _handle_card_clicked_event(self, payload: Dict[str, Any]) -> None:
        synthesized = _synthesize_card_click_text(payload)
        if not synthesized:
            logger.debug("[%s] CARD_CLICKED ignored without action context", self.name)
            return

        message = payload.get("message") or {}
        message_name = message.get("name", "") or ""
        dedup_key = f"{message_name}:card_click:{synthesized}" if message_name else ""
        if dedup_key and self._dedup.is_duplicate(dedup_key):
            return

        user = payload.get("user") or message.get("sender") or {}
        space = payload.get("space") or {}
        thread = message.get("thread") or {}
        thread_name = thread.get("name") or None
        space_name = space.get("name", "") or ""
        space_type_raw = space.get("spaceType") or space.get("type") or ""
        chat_type = "dm" if space_type_raw in ("DM", "DIRECT_MESSAGE") else "group"

        source = self.build_source(
            chat_id=space_name,
            chat_name=space.get("displayName") or None,
            chat_type=chat_type,
            user_id=user.get("name") or None,
            user_name=user.get("displayName") or None,
            thread_id=thread_name,
        )
        channel_prompt = resolve_channel_prompt(
            self.config.extra or {}, space_name, None
        )
        logger.info(
            "[%s] CARD_CLICKED synthesized action=%s params=%d selections=%d",
            self.name,
            _card_click_action_name(payload) or "<unknown>",
            len(_card_click_parameters(payload)),
            len(_card_click_form_inputs(payload)),
        )
        await self.handle_message(
            MessageEvent(
                text=synthesized,
                message_type=MessageType.TEXT,
                source=source,
                message_id=message_name or None,
                raw_message=payload,
                channel_prompt=channel_prompt,
            )
        )

    async def _hydrate_attachments(
        self, attachments: list[Dict[str, Any]]
    ) -> tuple[list[str], list[str], MessageType]:
        """Download Chat-uploaded attachments into Hermes media caches."""
        if not attachments:
            return [], [], MessageType.TEXT

        media_urls: list[str] = []
        media_types: list[str] = []
        message_types: list[MessageType] = []
        loop = asyncio.get_running_loop()

        for attachment in attachments:
            source = attachment.get("source")
            if source == "DRIVE_FILE" or attachment.get("driveDataRef"):
                logger.debug(
                    "[%s] skipping Drive-backed Chat attachment name=%s",
                    self.name,
                    attachment.get("name"),
                )
                continue

            data_ref = attachment.get("attachmentDataRef") or {}
            resource_name = data_ref.get("resourceName")
            if not resource_name:
                logger.debug(
                    "[%s] skipping Chat attachment without attachmentDataRef.resourceName name=%s",
                    self.name,
                    attachment.get("name"),
                )
                continue

            content_type = _attachment_content_type(attachment)
            try:
                data = await loop.run_in_executor(
                    None, self._download_attachment, resource_name
                )
                cached_path, message_type = self._cache_attachment(
                    data, attachment, content_type
                )
            except ValueError as exc:
                logger.warning(
                    "[%s] skipping invalid Chat attachment name=%s: %s",
                    self.name,
                    attachment.get("name"),
                    exc,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] failed to hydrate Chat attachment name=%s resource=%s: %s",
                    self.name,
                    attachment.get("name"),
                    resource_name,
                    exc,
                )
                continue

            if cached_path is None:
                continue

            media_urls.append(cached_path)
            media_types.append(content_type or "application/octet-stream")
            message_types.append(message_type)

        if not message_types:
            return media_urls, media_types, MessageType.TEXT
        if len(set(message_types)) == 1:
            return media_urls, media_types, message_types[0]
        return media_urls, media_types, MessageType.DOCUMENT

    def _download_attachment(self, resource_name: str) -> bytes:
        """Download one Chat attachment via media.download."""
        service = self._ensure_chat_service()
        if service is None:
            raise RuntimeError("Google Chat service is not configured")
        if MediaIoBaseDownload is None:
            raise RuntimeError("googleapiclient media downloader is unavailable")

        request = service.media().download_media(resourceName=resource_name)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        return buffer.getvalue()

    def _cache_attachment(
        self,
        data: bytes,
        attachment: Dict[str, Any],
        content_type: str,
    ) -> tuple[Optional[str], MessageType]:
        """Cache attachment bytes and return the local path plus message type."""
        if content_type.startswith("image/"):
            ext = _extension_from_attachment(attachment, ".jpg")
            return cache_image_from_bytes(data, ext), MessageType.PHOTO
        if content_type.startswith("audio/"):
            ext = _extension_from_attachment(attachment, ".ogg")
            return cache_audio_from_bytes(data, ext), MessageType.VOICE
        if content_type.startswith("video/"):
            ext = _extension_from_attachment(attachment, ".mp4")
            return cache_video_from_bytes(data, ext), MessageType.VIDEO

        filename = _document_filename_from_attachment(attachment, content_type)
        if filename is None:
            logger.debug(
                "[%s] skipping unsupported Chat document attachment name=%s contentType=%s",
                self.name,
                attachment.get("name"),
                content_type or "<missing>",
            )
            return None, MessageType.DOCUMENT
        return cache_document_from_bytes(data, filename), MessageType.DOCUMENT

    # -- Outbound ----------------------------------------------------------

    def _compute_scopes(self) -> list[str]:
        """Return the OAuth scope list the adapter should request.

        Widens only when the operator has confirmed Workspace-admin approval
        of the chat.app.* family (ADR-013). Without that confirmation we
        stay on chat.bot so send/read continue to work unchanged.
        """
        if not self._admin_approved:
            return [CHAT_BOT_SCOPE]
        return [
            CHAT_BOT_SCOPE,
            CHAT_APP_MESSAGES_SCOPE,
            CHAT_APP_MESSAGES_READONLY_SCOPE,
            CHAT_APP_SPACES_READONLY_SCOPE,
            CHAT_APP_MEMBERSHIPS_READONLY_SCOPE,
        ]

    def _reactions_enabled(self) -> bool:
        """Lifecycle reactions fire only when the operator both prefers them
        and has admin-approved the chat.app.* scope that authorizes the
        reactions API. AND-combining keeps the feature fail-closed against
        misconfiguration (preferred=true with scopes missing would 403)."""
        return self._admin_approved and self._reactions_preferred

    def _ensure_chat_service(self) -> Any:
        """Lazily build the Chat REST discovery client using SA credentials.

        Returns None when the SDK isn't importable, the SA path isn't set,
        or the credentials can't be loaded — send() maps any of these to
        SendResult(success=False, retryable=False) without crashing.
        """
        if self._chat_service is not None:
            return self._chat_service
        if not GOOGLECHAT_AVAILABLE:
            return None
        if not self._service_account_path:
            return None
        try:
            creds = service_account.Credentials.from_service_account_file(
                self._service_account_path, scopes=self._compute_scopes()
            )
            self._chat_service = discovery.build(
                "chat", "v1", credentials=creds, cache_discovery=False
            )
        except Exception:
            logger.exception("[%s] failed to build Chat REST client", self.name)
            return None
        return self._chat_service

    def _ensure_workspace_events_service(self) -> Any:
        """Build the Workspace Events discovery client on demand (R1 only)."""
        if self._workspace_events_service is not None:
            return self._workspace_events_service
        if not self._admin_approved:
            return None
        if not GOOGLECHAT_AVAILABLE:
            return None
        if not self._service_account_path:
            return None
        try:
            creds = service_account.Credentials.from_service_account_file(
                self._service_account_path, scopes=self._compute_scopes()
            )
            self._workspace_events_service = discovery.build(
                WORKSPACE_EVENTS_API,
                WORKSPACE_EVENTS_VERSION,
                credentials=creds,
                cache_discovery=False,
            )
        except Exception:
            logger.exception(
                "[%s] failed to build Workspace Events REST client", self.name
            )
            return None
        return self._workspace_events_service

    def format_message(self, content: str) -> str:
        return format_googlechat_markdown(content)

    @staticmethod
    def _thread_name(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        return (metadata or {}).get("thread_id")

    @staticmethod
    def _write_key(chat_id: str, thread_name: Optional[str]) -> tuple[str, Optional[str]]:
        return chat_id, thread_name

    async def _pace_write(self, key: tuple[str, Optional[str]]) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        last = self._last_write_time.get(key)
        if last is not None:
            delay = _PER_SPACE_QPS_DELAY_SECONDS - (now - last)
            if delay > 0:
                logger.debug(
                    "[%s] pacing Google Chat write by %.2fs target=%s",
                    self.name,
                    delay,
                    key[0],
                )
                await asyncio.sleep(delay)
        self._last_write_time[key] = loop.time()

    async def _create_message(
        self,
        chat_id: str,
        body: Dict[str, Any],
        thread_name: Optional[str],
        write_key: tuple[str, Optional[str]],
    ) -> Any:
        service = self._ensure_chat_service()
        if service is None:
            raise RuntimeError("Google Chat service is not configured")
        await self._pace_write(write_key)
        loop = asyncio.get_running_loop()

        def _execute() -> Any:
            kwargs: Dict[str, Any] = {"parent": chat_id, "body": body}
            if thread_name:
                kwargs["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
            return service.spaces().messages().create(**kwargs).execute()

        return await loop.run_in_executor(None, _execute)

    async def _patch_message_text(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        write_key: tuple[str, Optional[str]],
    ) -> SendResult:
        service = self._ensure_chat_service()
        if service is None:
            return SendResult(
                success=False,
                error="Google Chat service is not configured",
                retryable=False,
            )

        formatted = self.format_message(content)
        await self._pace_write(write_key)
        loop = asyncio.get_running_loop()

        def _execute() -> Any:
            body = {"name": message_id, "text": formatted}
            return (
                service.spaces()
                .messages()
                .patch(name=message_id, updateMask="text", body=body)
                .execute()
            )

        try:
            response = await loop.run_in_executor(None, _execute)
        except Exception as exc:  # noqa: BLE001
            retryable = _is_retryable_chat_error(exc)
            logger.warning(
                "[%s] edit failed chat_id=%s message=%s retryable=%s: %s",
                self.name,
                chat_id,
                message_id,
                retryable,
                exc,
            )
            return SendResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=retryable,
            )
        return SendResult(
            success=True,
            message_id=(response or {}).get("name") or message_id,
            raw_response=response,
            retryable=False,
        )

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        service = self._ensure_chat_service()
        if service is None:
            return SendResult(
                success=False,
                error="Google Chat service is not configured",
                retryable=False,
            )

        formatted = self.format_message(content)
        chunks = self.truncate_message(
            formatted, max_length=self._max_message_length
        )

        thread_name = self._thread_name(metadata)
        write_key = self._write_key(chat_id, thread_name)
        placeholder_id = self._typing_placeholders.pop(write_key, None)
        last_response: Any = None
        last_message_id: Optional[str] = None

        for index, chunk in enumerate(chunks):
            try:
                if index == 0 and placeholder_id:
                    result = await self._patch_message_text(
                        chat_id, placeholder_id, chunk, write_key
                    )
                    if not result.success:
                        logger.warning(
                            "[%s] placeholder edit failed; not creating fallback message: %s",
                            self.name,
                            result.error,
                        )
                        return result
                    response = result.raw_response
                    last_response = response
                    last_message_id = result.message_id
                    logger.debug(
                        "[%s] finalized Google Chat placeholder message=%s",
                        self.name,
                        last_message_id,
                    )
                    continue

                body: Dict[str, Any] = {"text": chunk}
                if thread_name:
                    body["thread"] = {"name": thread_name}
                response = await self._create_message(
                    chat_id, body, thread_name, write_key
                )
            except Exception as exc:  # noqa: BLE001
                # If we've already posted earlier chunks, force non-retryable
                # so the base class's _send_with_retry doesn't duplicate them.
                retryable = _is_retryable_chat_error(exc) and last_message_id is None
                logger.warning(
                    "[%s] send failed chat_id=%s chunk=%d/%d retryable=%s: %s",
                    self.name,
                    chat_id,
                    index + 1,
                    len(chunks),
                    retryable,
                    exc,
                )
                return SendResult(
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=retryable,
                )

            last_response = response
            last_message_id = (response or {}).get("name") or last_message_id
            if thread_name:
                response_thread_name = ((response or {}).get("thread") or {}).get(
                    "name"
                )
                if response_thread_name and response_thread_name != thread_name:
                    logger.warning(
                        "[%s] Google Chat replied in a different thread than requested "
                        "requested_thread=%s response_thread=%s message=%s",
                        self.name,
                        thread_name,
                        response_thread_name,
                        last_message_id,
                    )

        return SendResult(
            success=True,
            message_id=last_message_id,
            raw_response=last_response,
            retryable=False,
        )

    async def send_card(
        self,
        chat_id: str,
        card: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        thread_name = self._thread_name(metadata)
        write_key = self._write_key(chat_id, thread_name)
        body: Dict[str, Any] = {"cardsV2": [card]}
        if thread_name:
            body["thread"] = {"name": thread_name}
        try:
            response = await self._create_message(
                chat_id, body, thread_name, write_key
            )
        except Exception as exc:  # noqa: BLE001
            retryable = _is_retryable_chat_error(exc)
            logger.warning(
                "[%s] card send failed chat_id=%s retryable=%s: %s",
                self.name,
                chat_id,
                retryable,
                exc,
            )
            return SendResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=retryable,
            )
        return SendResult(
            success=True,
            message_id=(response or {}).get("name"),
            raw_response=response,
            retryable=False,
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        thread_name = self._thread_name(metadata)
        write_key = self._write_key(chat_id, thread_name)
        if write_key in self._typing_placeholders:
            return
        body: Dict[str, Any] = {"text": GOOGLECHAT_PLACEHOLDER_TEXT}
        if thread_name:
            body["thread"] = {"name": thread_name}
        try:
            response = await self._create_message(chat_id, body, thread_name, write_key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] placeholder send failed: %s", self.name, exc)
            return
        message_id = (response or {}).get("name")
        if message_id:
            self._typing_placeholders[write_key] = message_id
            logger.debug(
                "[%s] created Google Chat placeholder message=%s",
                self.name,
                message_id,
            )

    async def stop_typing(self, chat_id: str) -> None:
        # Google Chat has no native typing indicator. Placeholder messages are
        # normally handed off to send()/edit_message() for final text updates.
        # If a turn exits before handoff, transition the placeholder so it does
        # not sit forever as the placeholder text.
        stale = [
            (key, message_id)
            for key, message_id in self._typing_placeholders.items()
            if key[0] == chat_id
        ]
        for key, message_id in stale:
            self._typing_placeholders.pop(key, None)
            await self._patch_message_text(
                chat_id, message_id, GOOGLECHAT_PLACEHOLDER_STOPPED_TEXT, key
            )

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        thread_name = None
        if "/threads/" in message_id:
            thread_name = message_id.rsplit("/messages/", 1)[0]
        result = await self._patch_message_text(
            chat_id,
            message_id,
            content,
            self._write_key(chat_id, thread_name),
        )
        if result.success and finalize:
            logger.debug(
                "[%s] finalized Google Chat streamed text message=%s",
                self.name,
                result.message_id,
            )
        return result

    # -- Outbound media upload (R2b) ---------------------------------------

    def _upload_attachment(
        self,
        space_id: str,
        data: bytes,
        filename: str,
    ) -> Dict[str, Any]:
        """POST to chat.googleapis.com/upload/v1/{space}/attachments:upload.

        Returns the attachmentDataRef dict that a follow-up
        spaces.messages.create call references to actually attach the
        uploaded bytes. Requires chat.app.messages scope (ADR-013).
        """
        if not self._admin_approved:
            raise RuntimeError(
                "native upload requires GOOGLECHAT_ADMIN_APPROVED_SCOPES "
                "after Workspace admin approves chat.app.messages"
            )
        service = self._ensure_chat_service()
        if service is None:
            raise RuntimeError("Google Chat service is not configured")
        if MediaIoBaseUpload is None:
            raise RuntimeError("googleapiclient media uploader is unavailable")

        content_type, _ = mimetypes.guess_type(filename)
        media_body = MediaIoBaseUpload(
            io.BytesIO(data),
            mimetype=content_type or "application/octet-stream",
            resumable=False,
        )
        response = (
            service.media()
            .upload(
                parent=space_id,
                body={"filename": filename},
                media_body=media_body,
            )
            .execute()
        )
        ref = (response or {}).get("attachmentDataRef") or {}
        if not ref.get("resourceName") and not ref.get("attachmentUploadToken"):
            raise RuntimeError(
                f"upload response missing attachmentDataRef: {response!r}"
            )
        return ref

    async def _send_native_attachment(
        self,
        chat_id: str,
        local_path: Optional[str],
        bytes_payload: Optional[bytes],
        filename: Optional[str],
        caption: Optional[str],
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> SendResult:
        """Two-step upload + messages.create; shared code for all send_* overrides."""
        if not self._admin_approved:
            # Scope expansion not yet confirmed — fall back to the base class
            # URL-as-text path so the adapter stays useful pre-ADR-013.
            raise _NativeUploadUnavailable()

        # Materialize bytes + filename from whichever of the two arg shapes
        # the caller used. send_image(url=…) passes bytes=None and we fall
        # back to fetching the URL up front; send_image_file/_voice/_video/_document
        # pass a local path.
        resolved_filename = filename
        data: bytes
        if bytes_payload is not None:
            data = bytes_payload
            if not resolved_filename:
                resolved_filename = "upload.bin"
        elif local_path is not None:
            try:
                path = Path(local_path)
                data = path.read_bytes()
            except FileNotFoundError:
                return SendResult(
                    success=False,
                    error=f"File not found: {local_path}",
                    retryable=False,
                )
            if not resolved_filename:
                resolved_filename = path.name
        else:
            return SendResult(
                success=False,
                error="send_* native upload: no source bytes or path",
                retryable=False,
            )

        loop = asyncio.get_running_loop()
        try:
            ref = await loop.run_in_executor(
                None, self._upload_attachment, chat_id, data, resolved_filename
            )
        except Exception as exc:  # noqa: BLE001
            retryable = _is_retryable_chat_error(exc)
            logger.warning(
                "[%s] attachment upload failed chat_id=%s filename=%s retryable=%s: %s",
                self.name,
                chat_id,
                resolved_filename,
                retryable,
                exc,
            )
            return SendResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=retryable,
            )

        body: Dict[str, Any] = {"attachment": [{"attachmentDataRef": ref}]}
        if caption:
            body["text"] = self.format_message(caption)
        thread_name = (metadata or {}).get("thread_id")
        if thread_name:
            body["thread"] = {"name": thread_name}

        service = self._ensure_chat_service()
        if service is None:
            return SendResult(
                success=False,
                error="Google Chat service is not configured",
                retryable=False,
            )

        def _create() -> Any:
            kwargs: Dict[str, Any] = {"parent": chat_id, "body": body}
            if thread_name:
                kwargs["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
            return (
                service.spaces().messages().create(**kwargs).execute()
            )

        try:
            response = await loop.run_in_executor(None, _create)
        except Exception as exc:  # noqa: BLE001
            retryable = _is_retryable_chat_error(exc)
            logger.warning(
                "[%s] attachment send failed chat_id=%s filename=%s retryable=%s: %s",
                self.name,
                chat_id,
                resolved_filename,
                retryable,
                exc,
            )
            return SendResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                retryable=retryable,
            )

        return SendResult(
            success=True,
            message_id=(response or {}).get("name"),
            raw_response=response,
            retryable=False,
        )

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._admin_approved:
            return await super().send_image(
                chat_id=chat_id,
                image_url=image_url,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(image_url)
                response.raise_for_status()
                data = response.content
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] could not fetch image_url=%s; falling back to text: %s",
                self.name,
                image_url,
                exc,
            )
            return await super().send_image(
                chat_id=chat_id,
                image_url=image_url,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )

        filename = Path(image_url.split("?", 1)[0]).name or "image.bin"
        try:
            return await self._send_native_attachment(
                chat_id=chat_id,
                local_path=None,
                bytes_payload=data,
                filename=filename,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        except _NativeUploadUnavailable:
            return await super().send_image(
                chat_id=chat_id,
                image_url=image_url,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        metadata = kwargs.get("metadata")
        try:
            return await self._send_native_attachment(
                chat_id=chat_id,
                local_path=image_path,
                bytes_payload=None,
                filename=None,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        except _NativeUploadUnavailable:
            return await super().send_image_file(
                chat_id=chat_id,
                image_path=image_path,
                caption=caption,
                reply_to=reply_to,
                **kwargs,
            )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        metadata = kwargs.get("metadata")
        try:
            return await self._send_native_attachment(
                chat_id=chat_id,
                local_path=audio_path,
                bytes_payload=None,
                filename=None,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        except _NativeUploadUnavailable:
            return await super().send_voice(
                chat_id=chat_id,
                audio_path=audio_path,
                caption=caption,
                reply_to=reply_to,
                **kwargs,
            )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        metadata = kwargs.get("metadata")
        try:
            return await self._send_native_attachment(
                chat_id=chat_id,
                local_path=video_path,
                bytes_payload=None,
                filename=None,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        except _NativeUploadUnavailable:
            return await super().send_video(
                chat_id=chat_id,
                video_path=video_path,
                caption=caption,
                reply_to=reply_to,
                **kwargs,
            )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        metadata = kwargs.get("metadata")
        try:
            return await self._send_native_attachment(
                chat_id=chat_id,
                local_path=file_path,
                bytes_payload=None,
                filename=file_name,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        except _NativeUploadUnavailable:
            return await super().send_document(
                chat_id=chat_id,
                file_path=file_path,
                caption=caption,
                file_name=file_name,
                reply_to=reply_to,
                **kwargs,
            )

    # -- Lifecycle reactions (R3) ------------------------------------------

    def _set_reaction(self, message_name: str, emoji_unicode: str) -> Optional[str]:
        """Create a reaction on a Chat message; returns reactions/* resource name."""
        service = self._ensure_chat_service()
        if service is None:
            return None
        response = (
            service.spaces()
            .messages()
            .reactions()
            .create(
                parent=message_name,
                body={"emoji": {"unicode": emoji_unicode}},
            )
            .execute()
        )
        return (response or {}).get("name")

    def _remove_reaction(self, reaction_name: str) -> None:
        service = self._ensure_chat_service()
        if service is None:
            return
        service.spaces().messages().reactions().delete(
            name=reaction_name
        ).execute()

    def _remember_reaction(self, message_name: str, reaction_name: str) -> None:
        """Cap the reaction state dict the same way MessageDeduplicator caps."""
        if len(self._processing_reactions) >= REACTION_STATE_MAX_SIZE:
            # Evict oldest insertion (Python dicts preserve insertion order).
            oldest = next(iter(self._processing_reactions))
            self._processing_reactions.pop(oldest, None)
        self._processing_reactions[message_name] = reaction_name

    async def on_processing_start(self, event: MessageEvent) -> None:
        if not self._reactions_enabled():
            return
        message_id = getattr(event, "message_id", None)
        if not message_id:
            return
        loop = asyncio.get_running_loop()
        try:
            reaction_name = await loop.run_in_executor(
                None,
                self._set_reaction,
                message_id,
                REACTION_EMOJI_PROCESSING,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] failed to set processing reaction on %s: %s",
                self.name,
                message_id,
                exc,
            )
            return
        if reaction_name:
            self._remember_reaction(message_id, reaction_name)

    async def on_processing_complete(
        self, event: MessageEvent, outcome: ProcessingOutcome
    ) -> None:
        if not self._reactions_enabled():
            return
        message_id = getattr(event, "message_id", None)
        if not message_id:
            return
        reaction_name = self._processing_reactions.pop(message_id, None)
        loop = asyncio.get_running_loop()
        if reaction_name:
            try:
                await loop.run_in_executor(
                    None, self._remove_reaction, reaction_name
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] failed to remove processing reaction on %s: %s",
                    self.name,
                    message_id,
                    exc,
                )

        if outcome == ProcessingOutcome.CANCELLED:
            return

        emoji = (
            REACTION_EMOJI_SUCCESS
            if outcome == ProcessingOutcome.SUCCESS
            else REACTION_EMOJI_FAILURE
        )
        try:
            await loop.run_in_executor(
                None, self._set_reaction, message_id, emoji
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] failed to set completion reaction on %s: %s",
                self.name,
                message_id,
                exc,
            )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        raise NotImplementedError("get_chat_info() lands with space lookup support")


class _NativeUploadUnavailable(RuntimeError):
    """Raised internally to trigger base-class URL-as-text fallback in send_*.

    Distinct from plain RuntimeError so the send_* overrides can catch it
    narrowly without swallowing other upload errors that should surface.
    """


def _normalize_chat_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten whichever envelope Chat or Workspace Events delivered.

    Three shapes are expected in the wild:
      1. Legacy direct delivery: top-level `message`, `space`, `type`.
      2. Chat App Events: `payload["chat"]["messagePayload"]` wraps
         shape 1.
      3. Workspace Events API: CloudEvents-shaped. The payload either
         has an `@type` like
         `type.googleapis.com/google.chat.v1.MessageCreatedEventData`,
         or uses `data.message` as the nested Chat message resource.

    Returns a dict in shape 1.
    """
    if not isinstance(payload, dict):
        return payload or {}

    # Shape 2: Chat App Events envelope.
    message_payload = (payload.get("chat") or {}).get("messagePayload")
    if isinstance(message_payload, dict):
        return message_payload

    # Shape 3: Workspace Events API — the MessageCreatedEventData schema
    # puts the Chat Message under `message`. Recognize by any of:
    #   * `@type` starting with google.chat.*
    #   * a top-level `subscription` field (every WE delivery carries it)
    at_type = str(payload.get("@type") or "")
    has_subscription = "subscription" in payload
    if at_type.startswith("type.googleapis.com/google.chat") or has_subscription:
        # Some WE deliveries nest the actual event data under `data`.
        data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        projected = data if data else payload
        return {
            "type": "MESSAGE",
            "message": projected.get("message") or {},
            "space": projected.get("space")
            or (projected.get("message") or {}).get("space")
            or {},
        }

    return payload


def _card_click_action_name(payload: Dict[str, Any]) -> str:
    action = payload.get("action") or {}
    return str(
        action.get("actionMethodName")
        or action.get("function")
        or action.get("methodName")
        or ""
    ).strip()


def _card_click_parameters(payload: Dict[str, Any]) -> Dict[str, str]:
    action = payload.get("action") or {}
    params: Dict[str, str] = {}
    raw_params = action.get("parameters") or []
    if isinstance(raw_params, dict):
        raw_params = [{"key": key, "value": value} for key, value in raw_params.items()]
    for item in raw_params:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("name") or "").strip()
        if not key:
            continue
        params[key] = str(item.get("value", ""))
    return params


def _card_click_form_inputs(payload: Dict[str, Any]) -> Dict[str, list[str]]:
    common = payload.get("common") or {}
    raw_inputs = common.get("formInputs") or {}
    if not isinstance(raw_inputs, dict):
        return {}

    selections: Dict[str, list[str]] = {}
    for name, value in raw_inputs.items():
        if not isinstance(value, dict):
            continue
        selected: list[str] = []
        for input_key in ("stringInputs", "dateTimeInput", "dateInput", "timeInput"):
            input_value = value.get(input_key)
            if not isinstance(input_value, dict):
                continue
            raw_values = input_value.get("value")
            if raw_values is None:
                raw_values = [
                    input_value.get("msSinceEpoch"),
                    input_value.get("hours"),
                    input_value.get("minutes"),
                ]
            if not isinstance(raw_values, list):
                raw_values = [raw_values]
            selected.extend(str(item) for item in raw_values if item is not None)
        if selected:
            selections[str(name)] = selected
    return selections


def _synthesize_card_click_text(payload: Dict[str, Any]) -> str:
    action_name = _card_click_action_name(payload)
    params = _card_click_parameters(payload)
    selections = _card_click_form_inputs(payload)
    if not action_name and not params and not selections:
        return ""

    lines = ["Google Chat card click"]
    if action_name:
        lines.append(f"action: {action_name}")
    if params:
        lines.append("parameters:")
        for key in sorted(params):
            lines.append(f"- {key}: {params[key]}")
    if selections:
        lines.append("selections:")
        for key in sorted(selections):
            lines.append(f"- {key}: {', '.join(selections[key])}")
    return "\n".join(lines)


def _space_target_resource(space_id: str) -> str:
    """Format a space resource name as a Workspace Events target URL."""
    if space_id.startswith("//chat.googleapis.com/"):
        return space_id
    return f"//chat.googleapis.com/{space_id}"


def _parse_iso_epoch(value: Any) -> Optional[float]:
    """Parse an RFC 3339 / ISO 8601 string into POSIX epoch seconds."""
    if not value:
        return None
    import datetime as _dt

    try:
        text = str(value).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(text)
        return dt.timestamp()
    except Exception as exc:
        logger.debug("Invalid Google Chat timestamp %r: %s", value, exc)
        return None


def _is_retryable_chat_error(exc: BaseException) -> bool:
    """Return True for errors the base class should treat as retryable.

    Honors HTTP 429 (rate-limit) and 5xx (server transient) when the Google
    API client raises HttpError. Plain timeouts / connection resets are
    treated as non-retryable (Telegram precedent — the message may have
    landed upstream and we don't want to double-post).
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if isinstance(status, int):
        if status == 429 or 500 <= status < 600:
            return True
    return False


def _normalize_content_type(content_type: Any) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def _attachment_content_type(attachment: Dict[str, Any]) -> str:
    content_type = _normalize_content_type(attachment.get("contentType"))
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed, _encoding = mimetypes.guess_type(str(attachment.get("contentName") or ""))
    return _normalize_content_type(guessed) or content_type


def _extension_from_attachment(attachment: Dict[str, Any], default_ext: str) -> str:
    content_name = str(attachment.get("contentName") or "")
    suffix = Path(content_name).suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(_attachment_content_type(attachment))
    return guessed or default_ext


def _document_filename_from_attachment(
    attachment: Dict[str, Any], content_type: str
) -> Optional[str]:
    content_name = str(attachment.get("contentName") or "").strip()
    suffix = Path(content_name).suffix.lower()
    if suffix in SUPPORTED_DOCUMENT_TYPES:
        return content_name

    for ext, mime in SUPPORTED_DOCUMENT_TYPES.items():
        if content_type == mime:
            stem = Path(content_name).stem if content_name else "attachment"
            return f"{stem}{ext}"
    return None
