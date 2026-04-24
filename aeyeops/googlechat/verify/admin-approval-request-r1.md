# Admin approval request: R1 mention-free Google Chat follow-ups

## Request

Grant the Mood Media Assistant Chat app one additional app-auth scope:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

Project and app identifiers:

```text
Cloud project: sa-mm-gchatbot
Project number: 662239065768
Chat app: Mood Media Assistant
Service account: mood-media-assistant@sa-mm-gchatbot.iam.gserviceaccount.com
Service account OAuth2 client ID: 110945615708798543371
```

## Why this scope

This is the narrowest candidate scope for R1. It lets the Chat app create
Workspace Events subscriptions for Chat message events as the app, without
domain-wide delegation and without granting message write permissions.

The current baseline `chat.bot` scope remains unchanged for ordinary bot
message sending.

## What we verified

On 2026-04-24, the R1 probe attempted to create a temporary Workspace
Events subscription for:

```text
space: spaces/AAQA2N6jyoA
topic: projects/sa-mm-gchatbot/topics/chat-events
event: google.workspace.chat.message.v1.created
payloadOptions.includeResource: true
```

The probe used only:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

Token introspection confirmed the minted access token contained that scope.

Google returned:

```text
403 PERMISSION_DENIED
The administrator must grant the app the required OAuth authorization scope for this action.
```

A control probe using only `https://www.googleapis.com/auth/chat.bot` returns
`ACCESS_TOKEN_SCOPE_INSUFFICIENT`, so `chat.bot` does not cover this Workspace
Events subscription path.

## After approval

Rerun:

```bash
python aeyeops/googlechat/verify/r1_app_scope_probe.py
```

Expected successful output:

- `outcome: "created"`
- a `subscription_name`
- an `expire_time`
- `cleanup: "deleted"`

If that passes, ADR-013 can accept app-auth read-only scope for R1 only.
R2b outbound upload and R3 reactions remain unapproved and unimplemented.

## If approval was already granted but the probe still returns 403

Verify the approval landed on the same app instance the probe uses:

1. Cloud project `sa-mm-gchatbot` has the Chat API configuration for
   `Mood Media Assistant`.
2. The service account above has a Google Workspace Marketplace-compatible
   OAuth client.
3. The Marketplace SDK App Configuration is saved as a private Chat app and
   its OAuth scopes include exactly:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

4. The Workspace Admin installed/approved that Marketplace app, not a stale
   draft or previous Chat app instance.

Useful URLs:

- Chat API configuration:
  <https://console.developers.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=sa-mm-gchatbot>
- Marketplace SDK App Configuration:
  <https://console.cloud.google.com/apis/api/appsmarket-component.googleapis.com/googleapps_sdk?project=sa-mm-gchatbot>
- Google Admin Marketplace apps list:
  <https://admin.google.com/ac/apps/gmail/marketplace/apps>
