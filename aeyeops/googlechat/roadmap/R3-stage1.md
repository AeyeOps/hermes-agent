# R3 Stage 1: Lifecycle reactions

**Verdict**: Feasible with caveats. Gated on ADR-013.

**Checked**: 2026-04-24 against public Google Chat documentation.

## Findings

The Chat reactions resource supports create, delete, and list operations.
The create method adds a Unicode emoji reaction to a message, and the
delete method removes a reaction by its reaction resource name.

The current method references list user-auth reaction/message/import scopes.
They do not list `chat.bot`. That means the current DEMO-1 auth model
cannot call reaction create/delete.

Peer adapter parity supports outbound lifecycle reactions only:

- add a processing marker when a user message starts processing;
- remove that marker on completion;
- add a success or failure marker for completed turns;
- do not dispatch inbound reactions into the agent pipeline.

## Implementation Gate

R3 cannot move past Stage 1 until ADR-013 verifies whether app-auth scopes
can create and delete reactions in the target Workspace. If not, R3 either
requires DWD/user auth or stays out of scope.

## Sources

- Reactions resource: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.reactions>
- Reaction create: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.reactions/create>
- Reaction delete: <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.reactions/delete>
- Chat app auth: <https://developers.google.com/workspace/chat/authenticate-authorize-chat-app>
