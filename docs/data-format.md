# Persistent data format

DualModel Engine stores no credentials. API keys, proxy credentials, base URLs, and model credentials stay in SillyTavern Connection Profiles. Preset export contains only declarative preset data and optional initial state; it excludes credentials, chat history, and audit data.

## Namespace and versions

Global extension settings live at `getContext().extensionSettings.dualModelEngine`; `customPresets` is an array in that global settings object. Character defaults live at `character.data.extensions.dualModelEngine` (including `rulePresetId` and `presetVersion`). Chat-level state is `chatMetadata.dualModelEngine`; current-message identity is `message.extra.dualModelEngine`; every swipe has `message.swipe_info[swipeId].extra.dualModelEngine`.

`schemaVersion` is the persistence layout version, `stateVersion` is the active branch's narrative snapshot version, `headRevision` is the monotonic compare-and-swap revision, and a preset definition's `presetVersion` is its independent rules version. The pinned envelope spelling is `preset.version`, which persists that definition's `presetVersion`. They are deliberately not interchangeable.

```json
{
  "dualModelEngine": {
    "schemaVersion": 1,
    "stateVersion": 18,
    "headRevision": 42,
    "preset": { "id": "narrative", "version": 1 },
    "initialSnapshot": {},
    "activeSnapshot": {},
    "activeRef": { "messageId": "msg-assistant-0182", "swipeId": 0, "branchId": "branch-0182-0" },
    "configOverrides": {},
    "taskStatus": { "state": "idle", "requestId": null },
    "lastCommittedRequestId": "req-0182"
  }
}
```

The first initialization pins `preset.id` and `preset.version`. Global and character preset changes affect new chats only. Changing an existing chat requires explicit raw-data export, confirmation, and a reset transaction; a failed save restores memory. Migration works on a deep copy. If it fails, the original namespace remains available for export and the chat enters read-only diagnostics.

An imported definition is stored under global `customPresets`; its `presetVersion` becomes the chat envelope's `preset.version` when it is pinned:

```json
{
  "id": "my-campaign",
  "name": "My Campaign",
  "presetVersion": 3,
  "compatibleDataVersions": { "minimum": 1, "maximum": 1 }
}
```

## Stable identities and branches

Message indexes are transient and never persisted as identity. Every relevant assistant message gets a stable UUID in both `message.extra.dualModelEngine` and the selected swipe's `swipe_info[swipeId].extra.dualModelEngine`; each swipe owns an independent `branch`.

```json
{
  "messageId": "msg-assistant-0182",
  "branch": {
    "branchId": "branch-0182-0",
    "baseStateVersion": 17,
    "baseSnapshot": {},
    "segments": [
      {
        "requestId": "req-0182",
        "userMessageId": "msg-user-0181",
        "assistantTextHash": "sha256:...",
        "checks": [],
        "patch": {},
        "postSnapshot": {}
      }
    ],
    "status": "committed"
  }
}
```

The first segment is the initial generation; later segments are `continue` additions and contain only appended text and new tool records. `activeRef` identifies the selected message/swipe/branch that created `activeSnapshot`. `assistantTextHash` detects host or extension rewrites; it is not an authentication mechanism.

The normal writer uses branch status `committed`, `pending`, or `stale`; recovery and compatibility checks also recognize `invalidated` and `failed` branch or segment status. A stale/invalidated/failed branch cannot become the active state until it is recalculated or recovery restores the last valid committed snapshot. A cancelled generation, stale result, invalid Recorder patch, or failed save does not produce a partial commit.

## Checks and rollback

`segments[].checks` is the immutable audit record for D20 checks and damage effects. Formal checks have a stable `checkId`; ordinary regeneration reuses its completed record, while an explicit reroll creates a new record that records lineage to the replaced `checkId`. The same committed record is used for tool output, branch audit, and Recorder input.

Each transaction validates the patch and next snapshot, compares `headRevision`, updates the branch and envelope together, saves once, and restores the pre-transaction in-memory data if saving fails. Selecting a swipe restores its branch; deleting a message restores the last valid branch or `initialSnapshot`; editing invalidates descendants and requires confirmed sequential recalculation. Migration failure preserves raw data for export rather than silently changing its interpretation.
