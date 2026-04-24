# R1 Stage 1: Mention-free group-space delivery

**Verdict**: Feasible with caveats. Gated on ADR-013.

**Checked**: 2026-04-24 against public Google Workspace documentation.

## Findings

Workspace Events API subscriptions can deliver Chat message events for a
space, including `google.workspace.chat.message.v1.created`. The Chat
events overview distinguishes this subscription path from interaction
events such as @mentions and app invocations.

Space subscriptions support user authentication and Chat app authentication
with administrator approval. The scope guide still labels app-auth support
for Chat Workspace Events as Developer Preview, so this item needs live
tenant verification before implementation.

Subscription expiration is real operational state. The subscriptions
reference says payloads without resource data can last up to seven days;
payloads with resource data last up to four hours, or up to 24 hours when
access is granted through domain-wide delegation. The docs do not give a
separate maximum for app-auth plus `includeResource=true`, so the adapter
must measure it in the target tenant.

## Implementation Gate

R1 cannot move past Stage 1 until ADR-013 verifies:

- app-auth subscription creation for the target spaces;
- actual `expireTime` for `includeResource=true`;
- byte-identical `message.name` between Chat App Events and Workspace
  Events for an @mention, so existing dedup can safely suppress duplicates.

## 2026-04-24 least-privilege cycle

The first probe requested only:

```text
https://www.googleapis.com/auth/chat.app.messages.readonly
```

The API returned the expected administrator-approval gate:

```text
403 PERMISSION_DENIED: The administrator must grant the app the required OAuth authorization scope for this action.
```

No DWD escalation is justified by this result. The next action is a
one-scope admin approval for the Chat app, followed by rerunning
`aeyeops/googlechat/verify/r1_app_scope_probe.py`.

## Sources

- Chat event overview: <https://developers.google.com/workspace/chat/events-overview>
- Workspace Events Chat subscriptions: <https://developers.google.com/workspace/events/guides/events-chat>
- Workspace Events scopes: <https://developers.google.com/workspace/events/guides/auth>
- Subscription resource and expiration rules: <https://developers.google.com/workspace/events/reference/rest/v1/subscriptions>
