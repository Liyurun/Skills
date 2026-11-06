# Skills

Reusable skills for carrying work across conversations and projects.

## Available Skills

| Skill | Version | Description |
| --- | --- | --- |
| [project-handoff-zip](project-handoff-zip/SKILL.md) | 1.1.0 | Package available conversation context, selected materials and completed work into a verifiable, restorable handoff ZIP. |
| [skill-google-sheet-pro](skill-google-sheet-pro/SKILL.md) | Not specified | Google Sheets operations including values, formatting, tab management, validation, charts and exports. |
| [tech-writing-zh](tech-writing-zh/SKILL.md) | 2.1.0 | Detect and revise common AI phrasing in Chinese prose, with general, data/AI and fiction scopes. |

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

## skill-google-sheet-pro

Google Sheets tools with a Python helper and reference documentation for
authentication, commands and worked examples. Keep Google credentials outside
the repository and configure an authorized account before accessing spreadsheets.

See [SKILL.md](skill-google-sheet-pro/SKILL.md) for the workflow and
[commands.md](skill-google-sheet-pro/references/commands.md) for the command
reference. The supplied package does not declare a version number.

The original package is preserved verbatim. Its frontmatter description has
1,115 characters; validation with a 1,024-character limit requires shortening
that description before installation.

## Installation

Place the complete directory of the skill you need in
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
python3 -m py_compile skill-google-sheet-pro/scripts/gsheet_ops.py
```

Compilation checks syntax only; it does not authenticate or test Google API
operations.

## Boundaries

The repository contains reusable skill source, not project datasets, chat
archives or account credentials. Generated handoff ZIPs are not encrypted.
Credential scanning is a practical safeguard, not a complete DLP guarantee.
The skill reports unavailable history and does not claim access to every
conversation or remote resource.
