"""Inbound media hydration tests for GoogleChatAdapter.

Exercises `_hydrate_attachments`, `_download_attachment`, and
`_handle_chat_event` against the captured fixtures under
`aeyeops/googlechat/fixtures/inbound-*-attachment-*.json`.

The tests stub both the Chat REST discovery client (so
`_download_attachment` never hits the wire) and `handle_message` (so a
successful dispatch can be asserted without spawning the real pipeline).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.platforms.googlechat import GoogleChatAdapter


FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "aeyeops"
    / "googlechat"
    / "fixtures"
)

PNG_STUB = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
)
OGG_STUB = b"OggS" + b"\x00" * 60
MP4_STUB = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 48
PDF_STUB = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 64


def _load_fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


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


def _intercepted(adapter: GoogleChatAdapter) -> AsyncMock:
    mock = AsyncMock()
    adapter.handle_message = mock  # type: ignore[assignment]
    return mock


# ---------------------------------------------------------------------------
# C35 — fixture shape (pure data assertions, no adapter invocation)
# ---------------------------------------------------------------------------


class TestFixtureShapes:
    """Confirm every captured inbound-attachment fixture matches the shape
    `_handle_chat_event` expects before any test consumes it."""

    @pytest.mark.parametrize(
        "fixture,expected_content_type,expected_source",
        [
            (
                "inbound-image-attachment-20260424.json",
                "image/png",
                "UPLOADED_CONTENT",
            ),
            (
                "inbound-voice-attachment-20260424.json",
                "audio/ogg",
                "UPLOADED_CONTENT",
            ),
            (
                "inbound-video-attachment-20260424.json",
                "video/mp4",
                "UPLOADED_CONTENT",
            ),
            (
                "inbound-document-attachment-20260424.json",
                "application/pdf",
                "UPLOADED_CONTENT",
            ),
            (
                "inbound-drive-attachment-20260424.json",
                "application/pdf",
                "DRIVE_FILE",
            ),
        ],
    )
    def test_envelope_has_required_fields(
        self, fixture: str, expected_content_type: str, expected_source: str
    ):
        envelope = _load_fixture(fixture)
        assert envelope["type"] == "MESSAGE"
        assert envelope["message"]["attachment"], "attachment[] must be present"
        attachment = envelope["message"]["attachment"][0]
        assert attachment["source"] == expected_source
        assert attachment["contentType"] == expected_content_type
        if expected_source == "UPLOADED_CONTENT":
            assert attachment["attachmentDataRef"]["resourceName"]
        else:
            assert attachment["driveDataRef"]["driveFileId"]


# ---------------------------------------------------------------------------
# C36 — _download_attachment unit test (stubbed discovery client)
# ---------------------------------------------------------------------------


class TestDownloadAttachment:
    def test_returns_bytes_from_mediaiobasedownload(self):
        adapter = _make_adapter()

        fake_service = MagicMock()
        fake_service.media.return_value.download_media.return_value = (
            "fake-request"
        )
        adapter._chat_service = fake_service

        def fake_downloader_factory(buffer, request):
            assert request == "fake-request"

            class FakeDownloader:
                def __init__(self, _buf):
                    self._buf = _buf
                    self._done = False

                def next_chunk(self):
                    if not self._done:
                        self._buf.write(PNG_STUB)
                        self._done = True
                    return None, True

            return FakeDownloader(buffer)

        with patch(
            "gateway.platforms.googlechat.MediaIoBaseDownload",
            side_effect=fake_downloader_factory,
        ):
            data = adapter._download_attachment(
                "spaces/S/messages/M/attachments/A/media"
            )

        assert data == PNG_STUB
        fake_service.media.return_value.download_media.assert_called_once_with(
            resourceName="spaces/S/messages/M/attachments/A/media"
        )


# ---------------------------------------------------------------------------
# C37 — end-to-end image hydration through _handle_chat_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestImageHydration:
    async def test_image_fixture_populates_media_urls_and_photo_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        with patch.object(
            adapter, "_download_attachment", return_value=PNG_STUB
        ) as download:
            await adapter._handle_chat_event(
                _load_fixture("inbound-image-attachment-20260424.json")
            )

        download.assert_called_once_with(
            "spaces/EXAMPLE123XYZ/messages/ATTACHIMG.ATTACHIMG/attachments/A1/media"
        )
        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]

        # text is present alongside the image → MessageType.TEXT wins
        assert event.message_type == MessageType.TEXT
        assert event.text == "image attached"

        assert event.media_urls, "media_urls must be populated"
        assert event.media_urls[0].endswith(".png")
        assert Path(event.media_urls[0]).exists()
        assert Path(event.media_urls[0]).read_bytes() == PNG_STUB

        assert event.media_types == ["image/png"]
        assert event.source.platform == Platform.GOOGLECHAT
        assert event.source.chat_id == "spaces/EXAMPLE123XYZ"


# ---------------------------------------------------------------------------
# C38 — document + voice + video + Drive-attachment skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNonImageHydration:
    async def test_voice_fixture_caches_audio_and_picks_voice_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        with patch.object(
            adapter, "_download_attachment", return_value=OGG_STUB
        ):
            await adapter._handle_chat_event(
                _load_fixture("inbound-voice-attachment-20260424.json")
            )

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        # Voice fixture has no text; message_type matches the attachment
        assert event.message_type == MessageType.VOICE
        assert event.media_urls and event.media_urls[0].endswith(".ogg")
        assert Path(event.media_urls[0]).read_bytes() == OGG_STUB
        assert event.media_types == ["audio/ogg"]

    async def test_video_fixture_caches_video_and_picks_video_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        with patch.object(
            adapter, "_download_attachment", return_value=MP4_STUB
        ):
            await adapter._handle_chat_event(
                _load_fixture("inbound-video-attachment-20260424.json")
            )

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        assert event.message_type == MessageType.VIDEO
        assert event.media_urls and event.media_urls[0].endswith(".mp4")
        assert Path(event.media_urls[0]).read_bytes() == MP4_STUB
        assert event.media_types == ["video/mp4"]

    async def test_document_fixture_caches_pdf_and_picks_document_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        with patch.object(
            adapter, "_download_attachment", return_value=PDF_STUB
        ):
            await adapter._handle_chat_event(
                _load_fixture("inbound-document-attachment-20260424.json")
            )

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        # Doc fixture has text "document attached" → TEXT wins; media still hydrated
        assert event.message_type == MessageType.TEXT
        assert event.text == "document attached"
        assert event.media_urls and event.media_urls[0].endswith(".pdf")
        assert Path(event.media_urls[0]).read_bytes() == PDF_STUB
        assert event.media_types == ["application/pdf"]

    async def test_drive_fixture_skips_download_and_yields_empty_media(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        with patch.object(adapter, "_download_attachment") as download:
            await adapter._handle_chat_event(
                _load_fixture("inbound-drive-attachment-20260424.json")
            )

        download.assert_not_called()
        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        assert event.media_urls == []
        assert event.media_types == []
        # text present → MessageType.TEXT
        assert event.message_type == MessageType.TEXT
        assert event.text == "drive file attached"

    async def test_corrupt_image_bytes_skip_cleanly_with_warning(self, caplog):
        """cache_image_from_bytes raises ValueError when bytes don't start
        with a known magic sequence. The adapter must catch it, log a
        warning, and continue — no crash, no stray MessageEvent."""
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        corrupt = b"<html>not an image</html>"
        with patch.object(
            adapter, "_download_attachment", return_value=corrupt
        ):
            with caplog.at_level("WARNING"):
                await adapter._handle_chat_event(
                    _load_fixture("inbound-image-attachment-20260424.json")
                )

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        assert event.media_urls == []
        assert event.media_types == []
        assert any(
            "skipping invalid Chat attachment" in rec.message
            for rec in caplog.records
        )
