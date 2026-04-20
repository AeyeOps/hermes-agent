"""Tests for Google Chat platform adapter scaffolding."""
from unittest.mock import patch

from gateway.config import Platform, PlatformConfig


class TestGoogleChatRequirements:
    def test_returns_false_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.googlechat.GOOGLECHAT_AVAILABLE", False
        )
        from gateway.platforms.googlechat import check_googlechat_requirements
        assert check_googlechat_requirements() is False

    def test_returns_false_when_service_account_env_missing(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.googlechat.GOOGLECHAT_AVAILABLE", True
        )
        monkeypatch.delenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", raising=False)
        from gateway.platforms.googlechat import check_googlechat_requirements
        assert check_googlechat_requirements() is False

    def test_returns_true_when_sdk_and_env_present(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.googlechat.GOOGLECHAT_AVAILABLE", True
        )
        monkeypatch.setenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", "/tmp/key.json")
        from gateway.platforms.googlechat import check_googlechat_requirements
        assert check_googlechat_requirements() is True


class TestGoogleChatAdapterInit:
    def test_platform_set_to_googlechat(self):
        from gateway.platforms.googlechat import GoogleChatAdapter
        config = PlatformConfig(
            enabled=True,
            extra={
                "service_account_json": "/tmp/key.json",
                "pubsub_project": "sa-mm-gchatbot",
                "pubsub_subscription": "chat-events-sub",
            },
        )
        adapter = GoogleChatAdapter(config)
        assert adapter.platform == Platform.GOOGLECHAT
        assert adapter.config is config

    def test_reads_pubsub_settings_from_extras(self):
        from gateway.platforms.googlechat import GoogleChatAdapter
        config = PlatformConfig(
            enabled=True,
            extra={
                "service_account_json": "/tmp/key.json",
                "pubsub_project": "sa-mm-gchatbot",
                "pubsub_subscription": "chat-events-sub",
            },
        )
        adapter = GoogleChatAdapter(config)
        assert adapter._service_account_path == "/tmp/key.json"
        assert adapter._pubsub_project == "sa-mm-gchatbot"
        assert adapter._pubsub_subscription == "chat-events-sub"
