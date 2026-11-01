# Skills

Reusable skills for carrying work across conversations and projects.

## Available Skills

| Skill | Version | Description |
| --- | --- | --- |
| [project-handoff-zip](project-handoff-zip/SKILL.md) | 1.1.0 | Package available conversation context, selected materials and completed work into a verifiable, restorable handoff ZIP. |

## project-handoff-zip

Applicable to software development, writing, research, design, data analysis and
conversation-only planning. Business-specific context stays in each work
directory rather than in the skill.

The skill provides:

- Whole-directory or selected-materials packaging.
- A dedicated workspace for work without an existing project directory.
- Selected local conversation export and normalized JSON/JSONL imports.
- Context summaries, continuation instructions and explicit history gaps.
- SHA256 verification, content deduplication and non-overwriting restore.
- Continued work and repackaging after restoration.

The Python helper requires Python 3.9 or newer and uses only the standard library.

## Installation

Place the complete `project-handoff-zip` directory in
`$CODEX_HOME/skills/`, or `~/.codex/skills/` when `CODEX_HOME` is unset.
If an installation already exists, inspect differences before replacing it.

Invoke the skill in a subsequent conversation:

```text
Use $project-handoff-zip to package the current work, available conversation,
source materials, completed outputs and next steps for a new session.
```

See [SKILL.md](project-handoff-zip/SKILL.md) for the workflow and
[format.md](project-handoff-zip/references/format.md) for profile options and
conversation import formats.

## Validation

Run from this repository:

```sh
python3 project-handoff-zip/scripts/test_handoff.py
python3 project-handoff-zip/scripts/handoff.py --help
```

## Boundaries

The repository contains reusable skill source, not project datasets, chat
archives or account credentials. Generated handoff ZIPs are not encrypted.
Credential scanning is a practical safeguard, not a complete DLP guarantee.
The skill reports unavailable history and does not claim access to every
conversation or remote resource.
