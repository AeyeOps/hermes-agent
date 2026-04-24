"""Lifecycle reactions tests (R3 / M9).

Covers C45-C47 from the ultraplan — `_set_reaction` / `_remove_reaction`
helpers, and the `on_processing_start` / `on_processing_complete` hooks
that swap 👀 for ✅ / ❌ depending on outcome.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import ProcessingOutcome
from gateway.platforms.googlechat import (
    REACTION_EMOJI_FAILURE,
    REACTION_EMOJI_PROCESSING,
    REACTION_EMOJI_SUCCESS,
    REACTION_STATE_MAX_SIZE,
    GoogleChatAdapter,
)


def _make_adapter(
    admin_approved: bool = True, reactions_preferred: bool = True
) -> GoogleChatAdapter:
    config = PlatformConfig(
        enabled=True,
        extra={
            "service_account_json": "/tmp/key.json",
            "pubsub_project": "test-chat-project",
            "pubsub_subscription": "chat-events-sub",
            "admin_approved_scopes": admin_approved,
            "reactions": reactions_preferred,
        },
    )
    return GoogleChatAdapter(config)


def _stub_reactions_service() -> MagicMock:
    service = MagicMock()
    service.spaces.return_value.messages.return_value.reactions.return_value.create.return_value.execute.return_value = {
        "name": "spaces/S/messages/M/reactions/R-EYES"
    }
    service.spaces.return_value.messages.return_value.reactions.return_value.delete.return_value.execute.return_value = {}
    return service


def _event(message_id: str = "spaces/S/messages/M"):
    return SimpleNamespace(
        message_id=message_id,
        source=SimpleNamespace(chat_id="spaces/S"),
    )


class TestReactionsEnabledGate:
    def test_both_flags_on_returns_true(self):
        adapter = _make_adapter(admin_approved=True, reactions_preferred=True)
        assert adapter._reactions_enabled() is True

    def test_admin_off_returns_false_even_when_preferred(self):
        adapter = _make_adapter(admin_approved=False, reactions_preferred=True)
        assert adapter._reactions_enabled() is False

    def test_preference_off_returns_false_even_when_approved(self):
        adapter = _make_adapter(admin_approved=True, reactions_preferred=False)
        assert adapter._reactions_enabled() is False


class TestReactionHelpers:
    def test_set_reaction_returns_resource_name(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()
        name = adapter._set_reaction("spaces/S/messages/M", "👀")
        assert name == "spaces/S/messages/M/reactions/R-EYES"
        create_mock = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        create_mock.assert_called_once_with(
            parent="spaces/S/messages/M",
            body={"emoji": {"unicode": "👀"}},
        )

    def test_remove_reaction_calls_delete(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()
        adapter._remove_reaction("spaces/S/messages/M/reactions/R1")
        delete_mock = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.delete
        )
        delete_mock.assert_called_once_with(
            name="spaces/S/messages/M/reactions/R1"
        )


@pytest.mark.asyncio
class TestLifecycleHooks:
    async def test_processing_start_adds_eyes_and_remembers_state(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()

        await adapter.on_processing_start(_event())

        create = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        create.assert_called_once()
        body = create.call_args.kwargs["body"]
        assert body["emoji"]["unicode"] == REACTION_EMOJI_PROCESSING
        assert adapter._processing_reactions["spaces/S/messages/M"] == (
            "spaces/S/messages/M/reactions/R-EYES"
        )

    async def test_processing_start_noop_when_reactions_disabled(self):
        adapter = _make_adapter(reactions_preferred=False)
        adapter._chat_service = _stub_reactions_service()

        await adapter.on_processing_start(_event())

        create = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        create.assert_not_called()
        assert adapter._processing_reactions == {}

    async def test_processing_complete_success_swaps_eyes_for_check(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()
        adapter._processing_reactions["spaces/S/messages/M"] = (
            "spaces/S/messages/M/reactions/EYES"
        )

        await adapter.on_processing_complete(_event(), ProcessingOutcome.SUCCESS)

        delete = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.delete
        )
        create = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        delete.assert_called_once_with(name="spaces/S/messages/M/reactions/EYES")
        create.assert_called_once()
        assert (
            create.call_args.kwargs["body"]["emoji"]["unicode"]
            == REACTION_EMOJI_SUCCESS
        )
        assert "spaces/S/messages/M" not in adapter._processing_reactions

    async def test_processing_complete_failure_swaps_eyes_for_x(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()
        adapter._processing_reactions["spaces/S/messages/M"] = (
            "spaces/S/messages/M/reactions/EYES"
        )

        await adapter.on_processing_complete(_event(), ProcessingOutcome.FAILURE)

        create = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        assert (
            create.call_args.kwargs["body"]["emoji"]["unicode"]
            == REACTION_EMOJI_FAILURE
        )

    async def test_processing_complete_cancelled_only_removes_eyes(self):
        adapter = _make_adapter()
        adapter._chat_service = _stub_reactions_service()
        adapter._processing_reactions["spaces/S/messages/M"] = (
            "spaces/S/messages/M/reactions/EYES"
        )

        await adapter.on_processing_complete(_event(), ProcessingOutcome.CANCELLED)

        delete = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.delete
        )
        create = (
            adapter._chat_service.spaces.return_value.messages.return_value.reactions.return_value.create
        )
        delete.assert_called_once()
        # No final success/failure reaction on CANCELLED.
        create.assert_not_called()
        assert "spaces/S/messages/M" not in adapter._processing_reactions


class TestStateCap:
    def test_remember_reaction_evicts_oldest_at_cap(self):
        adapter = _make_adapter()
        # Fill right at the cap.
        for idx in range(REACTION_STATE_MAX_SIZE):
            adapter._remember_reaction(f"msg/{idx}", f"react/{idx}")
        assert len(adapter._processing_reactions) == REACTION_STATE_MAX_SIZE

        adapter._remember_reaction("msg/overflow", "react/overflow")

        assert len(adapter._processing_reactions) == REACTION_STATE_MAX_SIZE
        # Oldest (index 0) was evicted; newest is present.
        assert "msg/0" not in adapter._processing_reactions
        assert "msg/overflow" in adapter._processing_reactions
