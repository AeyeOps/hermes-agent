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
import json
import logging
import os
from typing import Any, Dict, Optional

try:
    from google.cloud import pubsub_v1  # noqa: F401
    from google.oauth2 import service_account  # noqa: F401
    from googleapiclient import discovery  # noqa: F401

    GOOGLECHAT_AVAILABLE = True
except ImportError:
    GOOGLECHAT_AVAILABLE = False
    pubsub_v1 = None  # type: ignore[assignment]
    service_account = None  # type: ignore[assignment]
    discovery = None  # type: ignore[assignment]

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

CHAT_API_SCOPES = ["https://www.googleapis.com/auth/chat.bot"]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    resolve_channel_prompt,
)
from gateway.platforms.helpers import MessageDeduplicator

logger = logging.getLogger(__name__)


def check_googlechat_requirements() -> bool:
    """Return True when SDKs are importable and the service-account env is set."""
    if not GOOGLECHAT_AVAILABLE:
        return False
    if not os.getenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON"):
        return False
    return True


class GoogleChatAdapter(BasePlatformAdapter):
    """Google Chat (Workspace) adapter — Pub/Sub pull inbound, Chat REST outbound.

    C6 state: inbound MESSAGE normalized + dispatched; ADDED/REMOVED/CARD_CLICKED
    ack'd but not surfaced (lifecycle lands in C14, cards in C24). send() lands
    in C8.
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

        scope_identity = f"{self._pubsub_project}/{self._pubsub_subscription}"
        if not self._acquire_platform_lock(
            scope="googlechat_subscription",
            identity=scope_identity,
            resource_desc=f"Pub/Sub subscription {scope_identity}",
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
        return True

    async def disconnect(self) -> None:
        self._running = False
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

        # TEMP DEMO-1 diagnostic: capture the raw envelope shape so we can
        # fix the unwrap if the current assumption is wrong. Remove once
        # DEMO 1 round-trips cleanly.
        chat_keys = list((payload.get("chat") or {}).keys()) if isinstance(payload, dict) else []
        logger.info(
            "[%s] RAW inbound top_keys=%s chat_keys=%s payload=%s",
            self.name,
            list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            chat_keys,
            json.dumps(payload)[:1200],
        )

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
        """Route a decoded Chat event payload by type."""
        # Chat's App Event envelope nests the legacy fields under
        # payload["chat"]["messagePayload"]. Unwrap so the routing below
        # works against both the legacy and current shapes.
        message_payload = (payload.get("chat") or {}).get("messagePayload")
        if message_payload:
            payload = message_payload

        event_type = payload.get("type") or payload.get("eventType")
        # App Event envelope doesn't carry an eventType string — infer
        # MESSAGE from the presence of the message resource. Lifecycle
        # and card-click inference lands with their M2/M4 fixtures.
        if not event_type and payload.get("message"):
            event_type = "MESSAGE"
        if event_type == "MESSAGE":
            await self._handle_message_event(payload)
        elif event_type in ("ADDED_TO_SPACE", "REMOVED_FROM_SPACE"):
            # TODO M2 C14: lifecycle handling (space-metadata cache / invalidate)
            logger.info(
                "[%s] lifecycle event=%s space=%s",
                self.name,
                event_type,
                (payload.get("space") or {}).get("name"),
            )
        elif event_type == "CARD_CLICKED":
            # TODO M4 C24: synthesize MessageEvent(TEXT) per ADR-012
            logger.debug(
                "[%s] CARD_CLICKED received (synthesis lands in C24)",
                self.name,
            )
        else:
            logger.debug(
                "[%s] ignoring event type=%r", self.name, event_type
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
        chat_type = "dm" if space.get("type") == "DIRECT_MESSAGE" else "group"

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

        event = MessageEvent(
            text=message.get("text") or "",
            message_type=MessageType.TEXT,
            source=source,
            message_id=message_name or None,
            raw_message=payload,
            channel_prompt=channel_prompt,
        )
        await self.handle_message(event)

    # -- Outbound ----------------------------------------------------------

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
                self._service_account_path, scopes=CHAT_API_SCOPES
            )
            self._chat_service = discovery.build(
                "chat", "v1", credentials=creds, cache_discovery=False
            )
        except Exception:
            logger.exception("[%s] failed to build Chat REST client", self.name)
            return None
        return self._chat_service

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

        thread_name = (metadata or {}).get("thread_id")
        last_response: Any = None
        last_message_id: Optional[str] = None
        loop = asyncio.get_running_loop()

        for index, chunk in enumerate(chunks):
            if index > 0:
                # Respect Google Chat's per-space 1-write/sec cap so chunk N
                # doesn't 429 on chunk N-1's own heels.
                await asyncio.sleep(_PER_SPACE_QPS_DELAY_SECONDS)

            body: Dict[str, Any] = {"text": chunk}
            if thread_name:
                body["thread"] = {"name": thread_name}

            def _execute() -> Any:
                return (
                    service.spaces()
                    .messages()
                    .create(parent=chat_id, body=body)
                    .execute()
                )

            try:
                response = await loop.run_in_executor(None, _execute)
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

        return SendResult(
            success=True,
            message_id=last_message_id,
            raw_response=last_response,
            retryable=False,
        )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        raise NotImplementedError("get_chat_info() lands with space lookup support")


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
