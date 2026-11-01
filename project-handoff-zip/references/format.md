# Profile And Archive Format

`profile.json` belongs to one work directory. Relative paths resolve against that
directory, not the skill installation. The implementation uses Python 3.9+
stdlib and has no service or account dependency. The archive format stays
`project-handoff/v1`; new profile fields are optional for older projects.

```json
{
  "version": 1,
  "project_name": "project",
  "timezone": "local",
  "include_paths": null,
  "context_dir": ".handoff/context",
  "exclude_globs": [],
  "required_paths": [],
  "previews": [
    {"source": "outputs/index.html", "name": "report.html"}
  ],
  "external_sources": [
    {"source": "/explicit/source/file", "destination": ".handoff/inputs/file", "required": false}
  ],
  "conversation": {
    "enabled": true,
    "use_local_sessions": true,
    "include_ancestors": true,
    "thread_ids": [],
    "session_store": "~/.codex/sessions",
    "allowed_cwds": [],
    "imports": []
  }
}
```

## Selecting Materials

`include_paths: null` includes the whole work directory, subject to exclusions.
An array includes only those explicit files/subdirectories; paths are not globs.
An empty array includes only handoff context and prior handoff history. The
profile, configured context directory and `.handoff/history` are always selected.
Explicit `external_sources` are additional materials in either selection mode.
Required selected paths fail if missing. `exclude_globs` can narrow either mode.

`init --workspace <new-empty-directory> --name <name>` creates an isolated,
selected-materials profile without copying the caller's current directory.
The caller's CWD is authorized only as the source CWD of an explicitly chosen
local thread. This is useful for conversation-only work or scattered documents.
It refuses to initialize a nonempty workspace or scan a filesystem/home/account
root. `init --project-root <existing-directory>` selects the whole directory.

The timezone defaults to the local machine's current offset. Set an IANA name
such as `UTC` or `Asia/Shanghai` when a stable project timezone is needed; existing
profiles keep their timezone.

## Conversation Sources

An empty `thread_ids` uses `CODEX_THREAD_ID`. Metadata is searched only for the
requested thread/segment IDs. `history_base.thread_id` is preferred over the
logical fork ID, because Codex can have multiple on-disk segments with the same
logical session ID. `end_byte_offset` is a physical file bound; a cumulative
ordinal must never be interpreted as a local line number. If only an ordinal is
available, the exporter records an incomplete boundary rather than guessing.
Only the event stream's visible human/agent messages are exported. Both legacy
`user_message`/`agent_message` and current `item_completed` UserMessage/AgentMessage
are supported; Reasoning, CommandExecution and tool items are never exported.

Set `conversation.enabled: false` to skip all new conversation export. Set
`use_local_sessions: false` when only supplied exports are available. Neither
option removes prior `.handoff/history` already present in the work directory.

For a user-provided export or another host's visible-message API, use a local
normalized input:

```json
{
  "enabled": true,
  "use_local_sessions": false,
  "imports": [
    {
      "source": ".handoff/inputs/conversation.jsonl",
      "format": "jsonl",
      "label": "Design discussion",
      "coverage": "Visible messages supplied by the user; earlier thread unavailable",
      "required": true
    }
  ]
}
```

The object above is the `conversation` section, not an entire profile.
The input path must be inside the work directory or exactly match the destination
of an explicitly registered external file. It is never an arbitrary account
store. `required` fails if neither the input nor its prior sanitized handoff
snapshot is available. Missing optional files and malformed records are reported
in `chat/coverage.json`, not reconstructed.

JSONL has one object per line:

```jsonl
{"role":"user","timestamp":"2026-10-31T09:00:00Z","text":"Please compare the two design options."}
{"role":"assistant","text":"The draft comparison is in notes/options.md."}
```

JSON accepts an array of those objects or `{"messages":[...]}`. `content` may
replace `text` only when it is a string. Timestamps are optional; repeated
untimestamped messages remain distinct. Only `user` and `assistant` roles are
retained, excluding `analysis`/`reasoning` phases. Other fields are not exported.
Inputs should contain only visible text, not raw provider logs. Existing
platform-specific exports require an explicit conversion from known visible
message fields; do not infer conversations from hidden prompts or tool payloads.
Markdown notes belong in context/materials as summaries, not fabricated messages.
Attach non-text materials explicitly as selected files or external sources.

Raw imports are excluded from the payload even when inside the selected tree.
Sanitized visible imports are preserved under `chat/imports/`, with source paths,
declared coverage, filtering and parse counts. Full restore retains them inside
`.handoff/history`; subsequent packs can reuse them when the original input no
longer exists. History availability is not proof that every original message
was obtained.

## Scenario Examples

- **Application development:** select the repository directory; summarize changed
  code, local validation and next steps. Rebuild dependencies after restore.
- **Writing or research:** select drafts, source documents, notes and citations;
  preserve originals and describe unresolved claims without requiring code.
- **Planning or pure conversation:** use `--workspace`; capture decisions, open
  questions and next actions, plus available messages. No project files required.
- **Several scattered inputs:** use a dedicated workspace and explicit external
  file mappings; do not choose their shared home directory as the project root.
- **Remote-only work:** preserve authorized URLs and context. A link is not an
  offline export; missing remote contents remain explicit until separately
  obtained through an authorized tool.

## Archive Layout

The ZIP contains:

- `00_START_HERE.md`, `NEW_CHAT_PROMPT.txt`, and `context/`: human handoff.
- `chat/transcript.md`, `chat/transcript.jsonl`, `chat/coverage.json`: available
  visible messages, provenance and omissions. Raw session logs are not included.
- `chat/attachments/`: whitelisted user-provided inline images, or available
  local image inputs explicitly registered in the profile. No remote downloads
  or arbitrary image-path reads are performed; missing references are listed.
- `chat/imports/`: normalized, redacted visible-message imports, never their
  original platform export files.
- `FILE_MANIFEST.json`: every restored project file, size, SHA256 and payload,
  plus included directory paths so empty directories survive.
- `payload/objects/<SHA256>`: losslessly deduplicated original file bytes.
- `AUDIT.json`: selection scope, excluded paths, scan limits and optional inputs.
- `PATH_MAP.json`: original root and explicitly registered external snapshots.
- `preview/`: selected byte-identical artifacts readable before full restore.
- `skill/project-handoff-zip/`: reusable skill and tests.
- `RESTORE.py`: the same stdlib helper, runnable without an installed skill.
- `BUNDLE_MANIFEST.json`: hashes for every other ZIP member.

`verify` checks all member paths, inventory, sizes and SHA256s, not just ZIP CRC.
It does not assert authorship or provide a cryptographic signature. The ZIP is
not encrypted and can contain private or restricted materials; retain existing access
boundaries.

`restore` requires a fresh/empty target, or `--resume` with byte-identical existing
files. It never overwrites conflicting files or follows symlinks. `--only` is a
sample restore and is labeled accordingly. A full restore additionally saves
handoff context and transcripts under `.handoff/history/<bundle-id>/` so later
archives retain the history even if the original Codex session store is absent.
Known path/content conflicts are checked before writing. Unexpected I/O failure
can still leave partial new files; retry with `--resume` after checking the cause.
Restored permissions are owner-only (0600/0700); executable bits are retained,
but original mode/timestamp metadata is not claimed to be reproduced exactly.

The scanner is a practical credential safeguard, not a complete DLP guarantee.
It checks explicit credential paths, strong token/header/private-key patterns,
quoted credential assignments and nested ZIP/GZIP content within declared
limits. Oversized/unsupported containers are reported, not silently certified.
