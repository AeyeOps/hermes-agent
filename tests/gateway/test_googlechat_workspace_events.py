"""Workspace Events migration tests (R1 / M10).

Covers the core pieces of C48-C57: envelope normalization across the three
inbound shapes, the in-memory subscription registry, the subscribe /
unsubscribe round-trip against a stubbed workspaceevents client, and dedup
via MessageDeduplicator keyed on `message.name`.
"""
from __future__ import annotations

import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.googlechat import (
    GoogleChatAdapter,
    WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS,
    WORKSPACE_EVENTS_MESSAGE_CREATED,
    WORKSPACE_EVENTS_RENEWAL_WINDOW_SECONDS,
    _SubscriptionRegistry,
    _normalize_chat_envelope,
    _parse_iso_epoch,
    _space_target_resource,
)


def _make_adapter(admin_approved: bool = True) -> GoogleChatAdapter:
    config = PlatformConfig(
        enabled=True,
        extra={
            "service_account_json": "/tmp/key.json",
            "pubsub_project": "test-chat-project",
            "pubsub_subscription": "chat-events-sub",
            "pubsub_topic": "projects/test-chat-project/topics/chat-events",
            "admin_approved_scopes": admin_approved,
        },
    )
    return GoogleChatAdapter(config)


# ---------------------------------------------------------------------------
# Envelope normalizer
# ---------------------------------------------------------------------------


class TestEnvelopeNormalizer:
    def test_legacy_direct_shape_passes_through(self):
        payload = {
            "type": "MESSAGE",
            "space": {"name": "spaces/S"},
            "message": {"name": "spaces/S/messages/M1"},
        }
        assert _normalize_chat_envelope(payload) == payload

    def test_chat_app_event_envelope_is_unwrapped(self):
        inner = {
            "type": "MESSAGE",
            "space": {"name": "spaces/S"},
            "message": {"name": "spaces/S/messages/M2"},
        }
        out = _normalize_chat_envelope({"chat": {"messagePayload": inner}})
        assert out == inner

    def test_workspace_events_by_atype_is_projected_to_legacy_shape(self):
        out = _normalize_chat_envelope(
            {
                "@type": "type.googleapis.com/google.chat.v1.MessageCreatedEventData",
                "subscription": "subscriptions/foo",
                "message": {
                    "name": "spaces/S/messages/M3",
                    "space": {"name": "spaces/S"},
                },
            }
        )
        assert out["type"] == "MESSAGE"
        assert out["message"]["name"] == "spaces/S/messages/M3"
        assert out["space"]["name"] == "spaces/S"

    def test_workspace_events_with_data_wrapper_unwraps_and_projects(self):
        out = _normalize_chat_envelope(
            {
                "subscription": "subscriptions/foo",
                "data": {
                    "message": {"name": "spaces/S/messages/M4"},
                    "space": {"name": "spaces/S"},
                },
            }
        )
        assert out["type"] == "MESSAGE"
        assert out["message"]["name"] == "spaces/S/messages/M4"

    def test_non_dict_payload_is_returned_as_empty_dict(self):
        assert _normalize_chat_envelope(None) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SubscriptionRegistry
# ---------------------------------------------------------------------------


class TestSubscriptionRegistry:
    def test_add_and_remove_round_trip(self):
        reg = _SubscriptionRegistry()
        reg.add("spaces/A", "subscriptions/Sa", 1000.0)
        reg.add("spaces/B", "subscriptions/Sb", 2000.0)

        assert sorted(reg.spaces()) == ["spaces/A", "spaces/B"]
        assert reg.get("spaces/A") == ("subscriptions/Sa", 1000.0)
        assert reg.remove("spaces/A") == "subscriptions/Sa"
        assert reg.get("spaces/A") is None

    def test_needs_renewal_within_returns_expiring_spaces(self):
        reg = _SubscriptionRegistry()
        now = 10_000.0
        reg.add("spaces/SOON", "subscriptions/a", now + 100)      # within window
        reg.add("spaces/LATER", "subscriptions/b", now + 99_999)  # outside window

        expiring = reg.needs_renewal_within(
            WORKSPACE_EVENTS_RENEWAL_WINDOW_SECONDS, now
        )
        assert expiring == ["spaces/SOON"]


# ---------------------------------------------------------------------------
# Target-resource helper
# ---------------------------------------------------------------------------


class TestSpaceTargetResource:
    def test_bare_space_id_gets_chat_prefix(self):
        assert _space_target_resource("spaces/S") == (
            "//chat.googleapis.com/spaces/S"
        )

    def test_already_prefixed_passes_through(self):
        assert _space_target_resource(
            "//chat.googleapis.com/spaces/S"
        ) == "//chat.googleapis.com/spaces/S"


class TestParseIsoEpoch:
    def test_z_suffix_parses(self):
        epoch = _parse_iso_epoch("2026-04-24T12:00:00Z")
        assert epoch is not None and epoch > 0

    def test_none_returns_none(self):
        assert _parse_iso_epoch(None) is None


# ---------------------------------------------------------------------------
# subscribe / unsubscribe round-trip against stubbed workspaceevents client
# ---------------------------------------------------------------------------


def _stub_workspaceevents_service(
    expire_time: str = "2026-04-25T12:00:00Z",
    subscription_name: str = "subscriptions/sub-1",
) -> MagicMock:
    service = MagicMock()
    create = service.subscriptions.return_value.create.return_value
    create.execute.return_value = {
        "done": True,
        "response": {
            "name": subscription_name,
            "expireTime": expire_time,
            "authority": "serviceAccountAuthority",
        },
    }
    service.subscriptions.return_value.delete.return_value.execute.return_value = {}
    return service


@pytest.mark.asyncio
class TestSubscribeSpace:
    async def test_subscribe_populates_registry(self):
        adapter = _make_adapter()
        adapter._workspace_events_service = _stub_workspaceevents_service()

        await adapter._subscribe_space("spaces/S1")

        entry = adapter._subscription_registry.get("spaces/S1")
        assert entry is not None
        name, expire = entry
        assert name == "subscriptions/sub-1"
        assert expire > time.time()  # far-future expire
        create = adapter._workspace_events_service.subscriptions.return_value.create
        create.assert_called_once()
        body = create.call_args.kwargs["body"]
        assert body["targetResource"] == "//chat.googleapis.com/spaces/S1"
        assert body["eventTypes"] == [WORKSPACE_EVENTS_MESSAGE_CREATED]
        assert body["payloadOptions"] == {"includeResource": True}
        assert body["notificationEndpoint"] == {
            "pubsubTopic": "projects/test-chat-project/topics/chat-events"
        }
        assert body["ttl"] == f"{WORKSPACE_EVENTS_DEFAULT_TTL_SECONDS}s"

    async def test_subscribe_space_if_needed_skips_when_already_present(self):
        adapter = _make_adapter()
        adapter._subscription_registry.add(
            "spaces/S1", "subscriptions/existing", time.time() + 3600
        )
        adapter._workspace_events_service = _stub_workspaceevents_service()

        await adapter._subscribe_space_if_needed("spaces/S1")

        create = adapter._workspace_events_service.subscriptions.return_value.create
        create.assert_not_called()

    async def test_unsubscribe_space_if_needed_deletes_and_removes(self):
        adapter = _make_adapter()
        adapter._subscription_registry.add(
            "spaces/S1", "subscriptions/sub-1", time.time() + 3600
        )
        adapter._workspace_events_service = _stub_workspaceevents_service()

        await adapter._unsubscribe_space_if_needed("spaces/S1")

        assert adapter._subscription_registry.get("spaces/S1") is None
        delete = adapter._workspace_events_service.subscriptions.return_value.delete
        delete.assert_called_once_with(name="subscriptions/sub-1")


# ---------------------------------------------------------------------------
# Dedup across Chat App Events + Workspace Events envelopes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDedupAcrossStreams:
    async def test_same_message_name_dispatched_once(self):
        adapter = _make_adapter()
        # Intercept handle_message so we can count dispatches without spawning
        # the real pipeline.
        adapter.handle_message = AsyncMock()  # type: ignore[assignment]

        message_name = "spaces/S/messages/M9"
        chat_app_event = {
            "chat": {
                "messagePayload": {
                    "type": "MESSAGE",
                    "space": {"name": "spaces/S", "spaceType": "SPACE"},
                    "message": {
                        "name": message_name,
                        "sender": {"name": "users/111", "displayName": "U"},
                        "text": "hello",
                        "thread": {"name": "spaces/S/threads/T"},
                    },
                }
            }
        }
        workspace_event = {
            "@type": "type.googleapis.com/google.chat.v1.MessageCreatedEventData",
            "subscription": "subscriptions/sub-1",
            "message": {
                "name": message_name,
                "sender": {"name": "users/111", "displayName": "U"},
                "text": "hello",
                "thread": {"name": "spaces/S/threads/T"},
                "space": {"name": "spaces/S", "spaceType": "SPACE"},
            },
            "space": {"name": "spaces/S", "spaceType": "SPACE"},
        }

        await adapter._handle_chat_event(chat_app_event)
        await adapter._handle_chat_event(workspace_event)

        adapter.handle_message.assert_awaited_once()
