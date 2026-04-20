"""Tests for Google Chat platform configuration wiring."""

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    _apply_env_overrides,
)


class TestPlatformEnum:
    def test_googlechat_member_present(self):
        assert Platform.GOOGLECHAT.value == "googlechat"


class TestEnvLoader:
    def test_service_account_env_enables_platform(self, monkeypatch):
        monkeypatch.setenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", "/tmp/key.json")
        monkeypatch.delenv("GOOGLECHAT_PUBSUB_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLECHAT_PUBSUB_SUBSCRIPTION", raising=False)
        monkeypatch.delenv("GOOGLECHAT_HOME_CHANNEL", raising=False)

        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.GOOGLECHAT in config.platforms
        pconfig = config.platforms[Platform.GOOGLECHAT]
        assert pconfig.enabled is True
        assert pconfig.extra["service_account_json"] == "/tmp/key.json"
        assert "pubsub_project" not in pconfig.extra
        assert "pubsub_subscription" not in pconfig.extra
        assert pconfig.home_channel is None

    def test_all_four_env_vars_populate_extras(self, monkeypatch):
        monkeypatch.setenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", "/tmp/key.json")
        monkeypatch.setenv("GOOGLECHAT_PUBSUB_PROJECT", "sa-mm-gchatbot")
        monkeypatch.setenv("GOOGLECHAT_PUBSUB_SUBSCRIPTION", "chat-events-sub")
        monkeypatch.setenv("GOOGLECHAT_HOME_CHANNEL", "spaces/AAQA2N6jyoA")

        config = GatewayConfig()
        _apply_env_overrides(config)

        pconfig = config.platforms[Platform.GOOGLECHAT]
        assert pconfig.extra["service_account_json"] == "/tmp/key.json"
        assert pconfig.extra["pubsub_project"] == "sa-mm-gchatbot"
        assert pconfig.extra["pubsub_subscription"] == "chat-events-sub"
        assert pconfig.home_channel is not None
        assert pconfig.home_channel.platform == Platform.GOOGLECHAT
        assert pconfig.home_channel.chat_id == "spaces/AAQA2N6jyoA"

    def test_missing_service_account_leaves_platform_unconfigured(self, monkeypatch):
        monkeypatch.delenv("GOOGLECHAT_SERVICE_ACCOUNT_JSON", raising=False)

        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.GOOGLECHAT not in config.platforms


class TestGetConnectedPlatforms:
    def test_connected_when_service_account_in_extras(self):
        config = GatewayConfig(
            platforms={
                Platform.GOOGLECHAT: PlatformConfig(
                    enabled=True,
                    extra={"service_account_json": "/tmp/key.json"},
                ),
            },
        )
        assert Platform.GOOGLECHAT in config.get_connected_platforms()

    def test_not_connected_when_extras_missing_service_account(self):
        config = GatewayConfig(
            platforms={
                Platform.GOOGLECHAT: PlatformConfig(enabled=True, extra={}),
            },
        )
        assert Platform.GOOGLECHAT not in config.get_connected_platforms()

    def test_not_connected_when_disabled(self):
        config = GatewayConfig(
            platforms={
                Platform.GOOGLECHAT: PlatformConfig(
                    enabled=False,
                    extra={"service_account_json": "/tmp/key.json"},
                ),
            },
        )
        assert Platform.GOOGLECHAT not in config.get_connected_platforms()
