# Google Chat least-privilege verification

This directory contains live verification probes for ADR-013. The probes
are intentionally capability-scoped: each one requests the narrowest
candidate scope for the capability being tested and records the exact API
response.

## R1: mention-free group-space follow-ups

Goal: prove whether Workspace Events can be created for one Chat space
using Chat app authentication and the read-only app scope:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

Run:

```bash
python aeyeops/googlechat/verify/r1_app_scope_probe.py
```

Defaults are read from
`aeyeops/googlechat/provisioning/requirements.yaml` and target the
existing dogfood setup:

- service account: `~/.example-chat-assistant/key.json`
- space: `SPACE_RESOURCE_PLACEHOLDER`
- Pub/Sub topic: `projects/example-gchatbot-project/topics/chat-events`
- event type: `google.workspace.chat.message.v1.created`

The probe creates a temporary Workspace Events subscription with
`payloadOptions.includeResource=true`, records the returned `expireTime`,
then deletes the subscription. If Google returns the administrator-approval
gate, the output records that fact and no subscription is left behind.

The result also records service-account identity and OAuth token introspection
metadata, without writing the access token itself. For targeted diagnostics,
repeat `--scope`:

```bash
python aeyeops/googlechat/verify/r1_app_scope_probe.py \
  --scope https://www.googleapis.com/auth/chat.app.messages.readonly \
  --scope https://www.googleapis.com/auth/chat.bot
```

Result JSON files are written to `aeyeops/googlechat/verify/results/`.
