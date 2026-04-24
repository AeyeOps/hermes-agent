"""Outbound native media upload tests (R2b / M8).

Covers C39-C44 from the ultraplan:
  * `_upload_attachment` posts to `/upload/v1/{parent}/attachments:upload`
    via the discovery client and returns the attachmentDataRef.
  * `send_image_file` / `send_voice` / `send_video` / `send_document`
    perform the two-step upload when admin approval is on, and fall
    back to the base-class URL-as-text path when it isn't.
  * `FileNotFoundError` yields an explicit SendResult(success=False).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.googlechat import (
    GoogleChatAdapter,
    _NativeUploadUnavailable,
)


def _make_adapter(admin_approved: bool = True) -> GoogleChatAdapter:
    config = PlatformConfig(
        enabled=True,
        extra={
            "service_account_json": "/tmp/key.json",
            "pubsub_project": "test-chat-project",
            "pubsub_subscription": "chat-events-sub",
            "admin_approved_scopes": admin_approved,
        },
    )
    return GoogleChatAdapter(config)


def _stub_upload_service(
    upload_return: Dict[str, Any] | Exception,
    create_return: Dict[str, Any] | Exception | None = None,
) -> MagicMock:
    service = MagicMock()

    if isinstance(upload_return, Exception):
        service.media.return_value.upload.return_value.execute.side_effect = (
            upload_return
        )
    else:
        service.media.return_value.upload.return_value.execute.return_value = (
            upload_return
        )

    if create_return is not None:
        chain = (
            service.spaces.return_value.messages.return_value.create.return_value
        )
        if isinstance(create_return, Exception):
            chain.execute.side_effect = create_return
        else:
            chain.execute.return_value = create_return

    return service


class TestAdminApprovalGate:
    def test_send_image_file_without_admin_approval_falls_back_to_text(self):
        """Base class send_image_file falls through to .send() as text."""
        adapter = _make_adapter(admin_approved=False)

        # send_image_file's base-class fallback ultimately calls self.send(),
        # so stub .send to observe the fallthrough without hitting the wire.
        with patch.object(
            adapter,
            "send",
            return_value=SendResult(success=True, message_id="m1"),
        ) as send:
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                adapter.send_image_file(
                    chat_id="spaces/S",
                    image_path="/tmp/does-not-exist.png",
                    caption="hi",
                )
            )
        assert result.success is True
        send.assert_awaited_once()
        # Fallback surface is documented as "🖼️ Image: <path>"
        sent_text = send.call_args.kwargs.get("content") or send.call_args.args[1]
        assert "/tmp/does-not-exist.png" in sent_text


class TestUploadAttachmentHelper:
    def test_returns_attachment_data_ref_from_media_upload(self):
        adapter = _make_adapter(admin_approved=True)
        service = _stub_upload_service(
            upload_return={
                "attachmentDataRef": {
                    "resourceName": "spaces/S/messages/M/attachments/A",
                    "attachmentUploadToken": "tok123",
                }
            }
        )
        adapter._chat_service = service

        with patch("gateway.platforms.googlechat.MediaIoBaseUpload") as mbu:
            ref = adapter._upload_attachment(
                "spaces/S", b"raw-bytes", "chart.png"
            )

        assert ref == {
            "resourceName": "spaces/S/messages/M/attachments/A",
            "attachmentUploadToken": "tok123",
        }
        mbu.assert_called_once()
        service.media.return_value.upload.assert_called_once()
        kwargs = service.media.return_value.upload.call_args.kwargs
        assert kwargs["parent"] == "spaces/S"
        assert kwargs["body"] == {"filename": "chart.png"}

    def test_raises_when_admin_approval_is_off(self):
        adapter = _make_adapter(admin_approved=False)
        with pytest.raises(RuntimeError, match="GOOGLECHAT_ADMIN_APPROVED_SCOPES"):
            adapter._upload_attachment("spaces/S", b"x", "a.png")


@pytest.mark.asyncio
class TestSendImageFileEndToEnd:
    async def test_uploads_then_creates_message_and_returns_message_id(
        self, tmp_path: Path
    ):
        adapter = _make_adapter(admin_approved=True)
        image = tmp_path / "chart.png"
        image.write_bytes(b"\x89PNGstub-bytes")

        service = _stub_upload_service(
            upload_return={
                "attachmentDataRef": {
                    "resourceName": "spaces/S/messages/M/attachments/A",
                    "attachmentUploadToken": "tok",
                }
            },
            create_return={
                "name": "spaces/S/messages/NEWMSG",
                "thread": {"name": "spaces/S/threads/T1"},
            },
        )
        adapter._chat_service = service

        with patch("gateway.platforms.googlechat.MediaIoBaseUpload"):
            result = await adapter.send_image_file(
                chat_id="spaces/S",
                image_path=str(image),
                caption="look",
                metadata={"thread_id": "spaces/S/threads/T1"},
            )

        assert result.success is True
        assert result.message_id == "spaces/S/messages/NEWMSG"

        create_kwargs = (
            service.spaces.return_value.messages.return_value.create.call_args.kwargs
        )
        assert create_kwargs["parent"] == "spaces/S"
        assert create_kwargs["messageReplyOption"] == (
            "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
        )
        body = create_kwargs["body"]
        assert body["attachment"][0]["attachmentDataRef"]["resourceName"] == (
            "spaces/S/messages/M/attachments/A"
        )
        assert body["text"] == "look"
        assert body["thread"] == {"name": "spaces/S/threads/T1"}

    async def test_file_not_found_returns_explicit_send_result(self):
        adapter = _make_adapter(admin_approved=True)
        # Don't configure a service — the path read should fail before any
        # API call happens.
        result = await adapter.send_image_file(
            chat_id="spaces/S",
            image_path="/tmp/does-not-exist-" + os.urandom(8).hex() + ".png",
        )
        assert result.success is False
        assert "File not found" in (result.error or "")
        assert result.retryable is False

    async def test_upload_http_error_surfaces_non_retryable_400(self, tmp_path: Path):
        from googleapiclient.errors import HttpError

        adapter = _make_adapter(admin_approved=True)
        image = tmp_path / "x.png"
        image.write_bytes(b"\x89PNG")

        fake_resp = MagicMock(status=400, reason="Bad Request")
        http_error = HttpError(resp=fake_resp, content=b"invalid filename")

        service = _stub_upload_service(upload_return=http_error)
        adapter._chat_service = service

        with patch("gateway.platforms.googlechat.MediaIoBaseUpload"):
            result = await adapter.send_image_file(
                chat_id="spaces/S", image_path=str(image)
            )

        assert result.success is False
        assert result.retryable is False
        assert "HttpError" in (result.error or "")

    async def test_upload_429_surfaces_retryable(self, tmp_path: Path):
        from googleapiclient.errors import HttpError

        adapter = _make_adapter(admin_approved=True)
        image = tmp_path / "x.png"
        image.write_bytes(b"\x89PNG")

        fake_resp = MagicMock(status=429, reason="Too Many Requests")
        http_error = HttpError(resp=fake_resp, content=b"slow down")

        service = _stub_upload_service(upload_return=http_error)
        adapter._chat_service = service

        with patch("gateway.platforms.googlechat.MediaIoBaseUpload"):
            result = await adapter.send_image_file(
                chat_id="spaces/S", image_path=str(image)
            )

        assert result.success is False
        assert result.retryable is True


@pytest.mark.asyncio
class TestOtherFormatsRouteThroughSharedHelper:
    """send_voice / send_video / send_document all share _send_native_attachment.
    One happy-path test per format is enough — the error paths are covered
    above and don't depend on format."""

    @pytest.mark.parametrize(
        "method,arg_name,filename",
        [
            ("send_voice", "audio_path", "note.ogg"),
            ("send_video", "video_path", "clip.mp4"),
            ("send_document", "file_path", "report.pdf"),
        ],
    )
    async def test_each_format_uploads_and_posts(
        self, tmp_path: Path, method: str, arg_name: str, filename: str
    ):
        adapter = _make_adapter(admin_approved=True)
        local = tmp_path / filename
        local.write_bytes(b"payload-for-" + filename.encode())

        service = _stub_upload_service(
            upload_return={
                "attachmentDataRef": {
                    "resourceName": f"spaces/S/messages/M/attachments/{filename}",
                    "attachmentUploadToken": "t",
                }
            },
            create_return={"name": "spaces/S/messages/OUT"},
        )
        adapter._chat_service = service

        method_callable = getattr(adapter, method)
        kwargs = {"chat_id": "spaces/S", arg_name: str(local)}
        with patch("gateway.platforms.googlechat.MediaIoBaseUpload"):
            result = await method_callable(**kwargs)

        assert result.success is True
        assert result.message_id == "spaces/S/messages/OUT"
        service.media.return_value.upload.assert_called_once()
