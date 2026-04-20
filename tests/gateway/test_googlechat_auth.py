"""Tests for _is_user_authorized handling of Platform.GOOGLECHAT.

Google Chat spaces use the same per-user allowlist as DMs (Slack/Discord
pattern) — no separate group-space env var. GOOGLECHAT_ALLOW_ALL_USERS
bypasses.
"""
from types import SimpleNamespace

from gateway.config import Platform
from gateway.session import SessionSource


def _make_bare_runner():
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(
        is_approved=lambda *_a, **_kw: False
    )
    return runner


def _source(user_id: str, chat_type: str = "dm") -> SessionSource:
    return SessionSource(
        platform=Platform.GOOGLECHAT,
        chat_id="spaces/AAQA2N6jyoA",
        chat_type=chat_type,
        user_id=user_id,
    )


def test_allowed_user_in_dm(monkeypatch):
    monkeypatch.delenv("GOOGLECHAT_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("GOOGLECHAT_ALLOWED_USERS", "users/111,users/222")

    runner = _make_bare_runner()
    assert runner._is_user_authorized(_source("users/111")) is True
    assert runner._is_user_authorized(_source("users/222")) is True


def test_unlisted_user_in_dm_rejected(monkeypatch):
    monkeypatch.delenv("GOOGLECHAT_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("GOOGLECHAT_ALLOWED_USERS", "users/111")

    runner = _make_bare_runner()
    assert runner._is_user_authorized(_source("users/999")) is False


def test_group_space_honors_same_user_allowlist(monkeypatch):
    """No separate GOOGLECHAT_GROUP_ALLOWED_USERS — group spaces use the
    same per-user allowlist as DMs, matching the Slack/Discord convention."""
    monkeypatch.delenv("GOOGLECHAT_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("GOOGLECHAT_ALLOWED_USERS", "users/111")

    runner = _make_bare_runner()
    assert (
        runner._is_user_authorized(_source("users/111", chat_type="group"))
        is True
    )
    assert (
        runner._is_user_authorized(_source("users/999", chat_type="group"))
        is False
    )


def test_allow_all_env_bypasses_allowlist(monkeypatch):
    monkeypatch.delenv("GOOGLECHAT_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("GOOGLECHAT_ALLOW_ALL_USERS", "true")

    runner = _make_bare_runner()
    assert runner._is_user_authorized(_source("users/anyone")) is True
