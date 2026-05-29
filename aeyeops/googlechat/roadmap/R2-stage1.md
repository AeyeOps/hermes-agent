# R2 Stage 1: Google Chat media

**Verdict**:

- R2a inbound media hydration: feasible under current `chat.bot` auth.
- R2b outbound native upload: feasible with caveats, gated on ADR-013.

**Checked**: 2026-04-24 against public Google Chat documentation.

## R2a Inbound

Inbound Chat message attachments include metadata such as `contentName`,
`contentType`, `source`, `attachmentDataRef`, and `driveDataRef`.
Google's attachment reference warns that Chat apps should not use
`downloadUri` to fetch bytes; apps should use the attachment data reference
with the media API.

The media download endpoint returns uploaded media bytes and supports
`chat.bot`, `chat.messages`, and `chat.messages.readonly`. It explicitly
excludes Google Drive files, which must be handled through Drive APIs.

Implementation path:

- download `attachmentDataRef.resourceName` with `media.download`;
- cache bytes with existing Hermes cache helpers;
- populate `MessageEvent.media_urls` and `media_types`;
- skip Drive-backed attachments for now with a debug log;
- skip unsupported document types rather than writing arbitrary binary
  data into the document cache.

## R2b Outbound

Native outbound upload is a two-step flow: upload bytes through
`media.upload`, then create a message whose `attachment` list references
the returned attachment data. The upload endpoint accepts files up to
200 MB and cannot be used with `chat.bot` according to the current method
reference; listed scopes are message/import user-auth scopes.

ADR-013 must verify whether a `chat.app.*` app-auth scope now covers this
method in the target Workspace. If not, outbound upload requires DWD/user
auth or remains out of scope.

## Sources

- Attachment resource: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.attachments>
- Attachment metadata get: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.attachments/get>
- Media download: <https://developers.google.com/workspace/chat/api/reference/rest/v1/media/download>
- Media upload: <https://developers.google.com/workspace/chat/api/reference/rest/v1/media/upload>
- Chat app auth: <https://developers.google.com/workspace/chat/authenticate-authorize-chat-app>
