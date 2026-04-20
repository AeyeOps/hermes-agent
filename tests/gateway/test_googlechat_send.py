"""Tests for GoogleChatAdapter.send() outbound via stubbed Chat API client."""
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.googlechat import GoogleChatAdapter


def _make_adapter() -> GoogleChatAdapter:
    config = PlatformConfig(
        enabled=True,
        extra={
            "service_account_json": "/tmp/key.json",
            "pubsub_project": "test-chat-project",
            "pubsub_subscription": "chat-events-sub",
        },
    )
    return GoogleChatAdapter(config)


def _install_chat_service_stub(
    adapter: GoogleChatAdapter,
    response: dict,
) -> MagicMock:
    """Wire a stubbed chat service onto the adapter and return the create() mock."""
    service = MagicMock()
    create_call = MagicMock()
    create_call.execute.return_value = response
    service.spaces.return_value.messages.return_value.create.return_value = create_call
    adapter._chat_service = service
    return service


@pytest.mark.asyncio
class TestGoogleChatSend:
    async def test_basic_send_posts_to_space(self):
        adapter = _make_adapter()
        response = {
            "name": "spaces/EXAMPLE123XYZ/messages/abc123.abc123",
            "text": "hello",
            "createTime": "2026-04-20T15:00:00Z",
        }
        service = _install_chat_service_stub(adapter, response)

        result = await adapter.send(
            chat_id="spaces/EXAMPLE123XYZ", content="hello"
        )

        assert result.success is True
        assert result.message_id == "spaces/EXAMPLE123XYZ/messages/abc123.abc123"
        assert result.raw_response == response

        create_mock = service.spaces.return_value.messages.return_value.create
        create_mock.assert_called_once()
        kwargs = create_mock.call_args.kwargs
        assert kwargs["parent"] == "spaces/EXAMPLE123XYZ"
        assert kwargs["body"]["text"] == "hello"
        # No thread metadata → no thread field in body
        assert "thread" not in kwargs["body"]
        # And no messageReplyOption — reserving that for thread-reply posts
        # avoids accidentally forcing reply mode on first-turn messages.
        assert "messageReplyOption" not in kwargs

    async def test_send_with_thread_id_metadata_threads_reply(self):
        adapter = _make_adapter()
        response = {
            "name": "spaces/EXAMPLE123XYZ/messages/xyz.xyz",
            "text": "reply",
            "thread": {"name": "spaces/EXAMPLE123XYZ/threads/T1"},
        }
        service = _install_chat_service_stub(adapter, response)

        result = await adapter.send(
            chat_id="spaces/EXAMPLE123XYZ",
            content="reply",
            metadata={"thread_id": "spaces/EXAMPLE123XYZ/threads/T1"},
        )

        assert result.success is True
        create_mock = service.spaces.return_value.messages.return_value.create
        kwargs = create_mock.call_args.kwargs
        assert kwargs["body"]["thread"] == {"name": "spaces/EXAMPLE123XYZ/threads/T1"}
        # Chat API v1 silently ignores body.thread without this option, so the
        # two MUST be sent together or the reply starts a new thread.
        assert kwargs["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

    async def test_send_error_returns_failure_result_no_retry(self):
        """Transient API timeout surfaces as failure but not retryable — per
        Telegram precedent the message may have been delivered, so we don't
        want the base class to double-post."""
        adapter = _make_adapter()
        service = MagicMock()
        create_call = MagicMock()
        create_call.execute.side_effect = TimeoutError("upstream timeout")
        service.spaces.return_value.messages.return_value.create.return_value = create_call
        adapter._chat_service = service

        result = await adapter.send(chat_id="spaces/EXAMPLE123XYZ", content="x")

        assert result.success is False
        assert result.retryable is False
        assert result.error is not None
        assert "timeout" in result.error.lower() or "TimeoutError" in result.error

    async def test_send_without_connected_service_returns_failure(self):
        adapter = _make_adapter()
        adapter._chat_service = None

        result = await adapter.send(chat_id="spaces/x", content="y")

        assert result.success is False
        assert result.retryable is False

    async def test_send_truncates_to_chat_max(self, monkeypatch):
        """Body text respects _max_message_length; content longer than the
        limit dispatches multiple spaces.messages.create calls."""
        from gateway.platforms import googlechat as mod

        # Skip the per-space pacing sleep so the test is fast.
        monkeypatch.setattr(mod, "_PER_SPACE_QPS_DELAY_SECONDS", 0)

        adapter = _make_adapter()
        adapter._max_message_length = 40

        response = {"name": "spaces/x/messages/a.a", "text": "chunk"}
        service = MagicMock()
        create_mock = service.spaces.return_value.messages.return_value.create
        create_mock.return_value.execute.return_value = response
        adapter._chat_service = service

        # 200 chars of content, 40 chars/chunk → base.truncate_message chunks it.
        result = await adapter.send(chat_id="spaces/x", content="a" * 200)

        assert result.success is True
        assert create_mock.call_count >= 2
