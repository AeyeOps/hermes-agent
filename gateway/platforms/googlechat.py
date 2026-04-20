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
"""

import logging
import os
from typing import Any, Dict, Optional

try:
    from google.cloud import pubsub_v1  # noqa: F401
    from googleapiclient import discovery  # noqa: F401

    GOOGLECHAT_AVAILABLE = True
except ImportError:
    GOOGLECHAT_AVAILABLE = False
    pubsub_v1 = None  # type: ignore[assignment]
    discovery = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
)

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

    C4 skeleton: wiring only. Pub/Sub pull loop lands in C6; send() in C8.
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

        scope_identity = f"{self._pubsub_project}/{self._pubsub_subscription}"
        if not self._acquire_platform_lock(
            scope="googlechat_subscription",
            identity=scope_identity,
            resource_desc=f"Pub/Sub subscription {scope_identity}",
        ):
            return False

        self._running = True
        return True

    async def disconnect(self) -> None:
        self._running = False
        self._release_platform_lock()

    # -- Message I/O --------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        raise NotImplementedError("send() lands in C8")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        raise NotImplementedError("get_chat_info() lands with space lookup support")
