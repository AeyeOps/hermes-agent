"""Integration tests for GoogleChatAdapter._handle_chat_event against real
Chat App Event envelopes.

The DM_ENVELOPE and ROOM_ENVELOPE literals below are redacted copies of
actual Pub/Sub payloads captured from a live Google Chat integration.
They exercise the nested `chat.messagePayload` unwrap plus the
spaceType/type chat_type derivation. Unredacted originals live under
aeyeops/googlechat/fixtures/ (fork-only, not cherry-picked upstream).
"""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.platforms.googlechat import GoogleChatAdapter


DM_ENVELOPE = { 'commonEventObject': { 'userLocale': 'en',
                             'hostApp': 'CHAT',
                             'platform': 'WEB',
                             'timeZone': {'id': 'America/New_York', 'offset': -14400000}},
      'chat': { 'user': { 'name': 'users/111000000000000000001',
                          'displayName': 'Test User',
                          'avatarUrl': 'https://example.com/avatar.png',
                          'email': 'test-user@example.com',
                          'type': 'HUMAN',
                          'domainId': 'testdomain'},
                'eventTime': '2026-04-20T21:56:46.474671Z',
                'messagePayload': { 'space': { 'name': 'spaces/DIRMSG00001',
                                               'type': 'DM',
                                               'singleUserBotDm': True,
                                               'spaceThreadingState': 'THREADED_MESSAGES',
                                               'spaceType': 'DIRECT_MESSAGE',
                                               'spaceHistoryState': 'HISTORY_ON',
                                               'lastActiveTime': '2026-04-20T21:56:46.474671Z',
                                               'membershipCount': {'joinedDirectHumanUserCount': 1},
                                               'spaceUri': 'https://chat.google.com/dm/vU8MIyAAAAE?cls=11'},
                                    'message': { 'name': 'spaces/DIRMSG00001/messages/MSG001.MSG001',
                                                 'sender': { 'name': 'users/111000000000000000001',
                                                             'displayName': 'Test User',
                                                             'avatarUrl': 'https://example.com/avatar.png',
                                                             'email': 'test-user@example.com',
                                                             'type': 'HUMAN',
                                                             'domainId': 'testdomain'},
                                                 'createTime': '2026-04-20T21:56:46.474671Z',
                                                 'text': 'capture dm',
                                                 'thread': { 'name': 'spaces/DIRMSG00001/threads/THR001',
                                                             'retentionSettings': { 'state': 'PERMANENT'}},
                                                 'space': { 'name': 'spaces/DIRMSG00001',
                                                            'type': 'DM',
                                                            'singleUserBotDm': True,
                                                            'spaceThreadingState': 'THREADED_MESSAGES',
                                                            'spaceType': 'DIRECT_MESSAGE',
                                                            'spaceHistoryState': 'HISTORY_ON',
                                                            'lastActiveTime': '2026-04-20T21:56:46.474671Z',
                                                            'membershipCount': { 'joinedDirectHumanUserCount': 1},
                                                            'spaceUri': 'https://chat.google.com/dm/vU8MIyAAAAE?cls=11'},
                                                 'argumentText': 'capture dm',
                                                 'retentionSettings': {'state': 'PERMANENT'},
                                                 'messageHistoryState': 'HISTORY_ON',
                                                 'formattedText': 'capture dm'},
                                    'configCompleteRedirectUri': 'https://chat.google.com/api/bot_config_complete?token=REDACTED_OAUTH_TOKEN'}}}


ROOM_ENVELOPE = { 'commonEventObject': { 'userLocale': 'en',
                             'hostApp': 'CHAT',
                             'platform': 'WEB',
                             'timeZone': {'id': 'America/New_York', 'offset': -14400000}},
      'chat': { 'user': { 'name': 'users/111000000000000000001',
                          'displayName': 'Test User',
                          'avatarUrl': 'https://example.com/avatar.png',
                          'email': 'test-user@example.com',
                          'type': 'HUMAN',
                          'domainId': 'testdomain'},
                'eventTime': '2026-04-20T21:58:33.299535Z',
                'messagePayload': { 'space': { 'name': 'spaces/EXAMPLE123XYZ',
                                               'type': 'ROOM',
                                               'displayName': 'test-room',
                                               'spaceThreadingState': 'THREADED_MESSAGES',
                                               'spaceType': 'SPACE',
                                               'spaceHistoryState': 'HISTORY_ON',
                                               'lastActiveTime': '2026-04-20T21:58:33.299535Z',
                                               'membershipCount': {'joinedDirectHumanUserCount': 1},
                                               'customer': 'customers/C0testdomain',
                                               'spaceUri': 'https://chat.google.com/room/AAQA2N6jyoA?cls=11'},
                                    'message': { 'name': 'spaces/EXAMPLE123XYZ/messages/MSG002.MSG002',
                                                 'sender': { 'name': 'users/111000000000000000001',
                                                             'displayName': 'Test User',
                                                             'avatarUrl': 'https://example.com/avatar.png',
                                                             'email': 'test-user@example.com',
                                                             'type': 'HUMAN',
                                                             'domainId': 'testdomain'},
                                                 'createTime': '2026-04-20T21:58:33.299535Z',
                                                 'text': '@Test Bot what is your context here can you '
                                                         'share it and provide both id as well as the '
                                                         'friendly description for each item',
                                                 'annotations': [ { 'type': 'USER_MENTION',
                                                                    'startIndex': 0,
                                                                    'length': 21,
                                                                    'userMention': { 'user': { 'name': 'users/106000000000000000001',
                                                                                               'displayName': 'Test '
                                                                                                              'Bot',
                                                                                               'avatarUrl': 'https://example.com/avatar.png',
                                                                                               'type': 'BOT'},
                                                                                     'type': 'MENTION'}}],
                                                 'thread': { 'name': 'spaces/EXAMPLE123XYZ/threads/THR002',
                                                             'retentionSettings': { 'state': 'PERMANENT'}},
                                                 'space': { 'name': 'spaces/EXAMPLE123XYZ',
                                                            'type': 'ROOM',
                                                            'displayName': 'test-room',
                                                            'spaceThreadingState': 'THREADED_MESSAGES',
                                                            'spaceType': 'SPACE',
                                                            'spaceHistoryState': 'HISTORY_ON',
                                                            'lastActiveTime': '2026-04-20T21:58:33.299535Z',
                                                            'membershipCount': { 'joinedDirectHumanUserCount': 1},
                                                            'customer': 'customers/C0testdomain',
                                                            'spaceUri': 'https://chat.google.com/room/AAQA2N6jyoA?cls=11'},
                                                 'argumentText': ' what is your context here can you '
                                                                 'share it and provide both id as well '
                                                                 'as the friendly description for each '
                                                                 'item',
                                                 'threadReply': True,
                                                 'retentionSettings': {'state': 'PERMANENT'},
                                                 'messageHistoryState': 'HISTORY_ON',
                                                 'formattedText': '@Test Bot what is your context here '
                                                                  'can you share it and provide both '
                                                                  'id as well as the friendly '
                                                                  'description for each item'},
                                    'configCompleteRedirectUri': 'https://chat.google.com/api/bot_config_complete?token=REDACTED_OAUTH_TOKEN'}}}


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


# Intercept at handle_message (base class). _handle_chat_event → _handle_message_event
# → self.handle_message(event) is the contract we want to exercise; going all the way
# through the base class spawns a background task that doesn't complete synchronously
# in a test. AsyncMock captures the normalized MessageEvent without running the pipeline.


def _intercepted(adapter: GoogleChatAdapter) -> AsyncMock:
    mock = AsyncMock()
    adapter.handle_message = mock  # type: ignore[assignment]
    return mock


@pytest.mark.asyncio
class TestHandleChatEventRealEnvelopes:
    async def test_dm_envelope_dispatches_as_message_event_with_dm_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        await adapter._handle_chat_event(DM_ENVELOPE)

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        assert event.message_type == MessageType.TEXT
        assert event.text == "capture dm"
        assert event.source.platform == Platform.GOOGLECHAT
        assert event.source.chat_id == "spaces/DIRMSG00001"
        assert event.source.chat_type == "dm"
        assert event.source.thread_id == "spaces/DIRMSG00001/threads/THR001"
        assert event.source.user_id == "users/111000000000000000001"

    async def test_room_envelope_dispatches_as_message_event_with_group_type(self):
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        await adapter._handle_chat_event(ROOM_ENVELOPE)

        handle_message.assert_awaited_once()
        event: MessageEvent = handle_message.call_args.args[0]
        assert event.message_type == MessageType.TEXT
        assert event.text.startswith("@Test Bot")
        assert event.source.chat_id == "spaces/EXAMPLE123XYZ"
        assert event.source.chat_type == "group"
        assert event.source.thread_id == "spaces/EXAMPLE123XYZ/threads/THR002"
        assert event.source.chat_name == "test-room"

    async def test_dm_envelope_chat_type_when_only_legacy_type_field_present(self):
        """Both space.type (legacy DM/ROOM) and space.spaceType (current
        DIRECT_MESSAGE/SPACE) can appear. chat_type must resolve to 'dm' even
        when only the legacy short form is present."""
        adapter = _make_adapter()
        handle_message = _intercepted(adapter)

        no_spacetype = deepcopy(DM_ENVELOPE)
        no_spacetype["chat"]["messagePayload"]["space"].pop("spaceType", None)
        await adapter._handle_chat_event(no_spacetype)

        event: MessageEvent = handle_message.call_args.args[0]
        assert event.source.chat_type == "dm"
