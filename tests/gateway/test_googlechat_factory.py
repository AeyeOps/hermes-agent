"""Tests for GatewayRunner._create_adapter handling of Platform.GOOGLECHAT."""
from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import Platform, PlatformConfig


def _make_bare_runner():
    """Skeleton GatewayRunner — skip heavy __init__ (see AGENTS.md pitfall #17).

    _create_adapter reads self.config.group_sessions_per_user and
    thread_sessions_per_user up front, so stub a minimal config.
    """
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=False,
        thread_sessions_per_user=False,
    )
    return runner


def test_create_adapter_returns_googlechat_when_requirements_met():
    runner = _make_bare_runner()
    config = PlatformConfig(
        enabled=True,
        extra={
            "service_account_json": "/tmp/key.json",
            "pubsub_project": "test-chat-project",
            "pubsub_subscription": "chat-events-sub",
        },
    )

    with patch(
        "gateway.platforms.googlechat.check_googlechat_requirements",
        return_value=True,
    ):
        adapter = runner._create_adapter(Platform.GOOGLECHAT, config)

    from gateway.platforms.googlechat import GoogleChatAdapter

    assert isinstance(adapter, GoogleChatAdapter)
    assert adapter.platform == Platform.GOOGLECHAT


def test_create_adapter_returns_none_when_requirements_unmet():
    runner = _make_bare_runner()
    config = PlatformConfig(enabled=True, extra={})

    with patch(
        "gateway.platforms.googlechat.check_googlechat_requirements",
        return_value=False,
    ):
        adapter = runner._create_adapter(Platform.GOOGLECHAT, config)

    assert adapter is None
