---
name: skill-google-sheet-pro
description: Complete Google Sheets toolkit through the Sheets API v4, covering what the basic skynet-base gsheet commands cannot do. Use for any Google Sheets link/ID request that needs cell formatting (background/font colors, borders, number formats, merges, alignment), highlighting changed cells, conditional formatting, color scales, banding, freeze panes, column widths, hiding/grouping rows or columns, inserting/deleting/moving rows and columns, tab management (rename, duplicate, move, hide, tab color, resize, copy to another spreadsheet), filters and filter views, data validation (dropdowns, checkboxes), sort, dedupe, trim, split text, find/replace, copy/cut paste, protected ranges, named ranges, charts, cell notes, auto-fill, insert/delete cells with shifting, raw batchUpdate (pivots, slicers, tables), reading cell formatting back, exporting a spreadsheet or one tab to xlsx/pdf/csv, and large writes that exceed the default grid. Works with five credential methods (Skynet login, access token, OAuth refresh token, service account, gcloud). Also covers plain read/write/append/clear and creating spreadsheets.
---

# Google Sheets - full operations

One script, `scripts/gsheet_ops.py`, covers the whole Sheets API surface that matters in practice: **79 commands**, standard library only, no setup. It supports **five credential methods** (Skynet login, access token, OAuth refresh token, service-account key, gcloud) and picks the first available one automatically; the Skynet login is used when nothing else is configured. The credential is used inside the script, sent only to Google, and never printed. Details: [references/auth.md](references/auth.md).

Invoke as (from the skill directory, or with its absolute path):

```bash
python3 -B -I scripts/gsheet_ops.py <command> "<spreadsheet-url-or-id>" [options]
```

`-B` keeps Python from writing cache files into the skill folder; `-I` isolates it from the current directory.

## When to use which tool

| Need | Use |
|---|---|
| Read a range, quick write/append/clear of a few values | either `skynet-base gsheet ...` or this script |
| Colors, borders, number formats, merges, freeze, widths, conditional formats, validation, filters | **this script** |
| Insert/delete/move rows or columns, tab operations, sort, dedupe, find/replace | **this script** |
| Writes bigger than the grid (the CLI fails with "exceeds grid limits") or thousands of rows | **this script** (`write` grows the grid and chunks rows) |
| Import an `.xlsx` as a native Google Sheet | `skynet-base gdrive upload --mime-type application/vnd.google-apps.spreadsheet` |
| Share a spreadsheet, move it between folders, delete the whole file | Google Drive skill (`skynet-base gdrive ...`) |

## Before any operation

1. **Check the credential once per task**: `python3 -B -I scripts/gsheet_ops.py auth-check` -> `can_edit_sheets` must be `true`; `method_in_use` and `available_methods` show which of the five methods is active. If it fails with the Skynet method, run `skynet-base gsheet list-sheets "<url>"` (a harmless read through the official CLI that renews or re-authorizes) and retry; with another method, read the error and [references/auth.md](references/auth.md). Force a method with `--auth <method>` or `GSHEET_OPS_AUTH`.
2. **Look before you change**: `info "<url>"` lists tabs (title, sheetId, size, freeze, hidden). `inspect` shows merges, conditional formats, filters, banding, protected ranges and charts. `get-format --range ...` shows existing colors.
3. **Pick the target tab explicitly** with `--sheet "<title>"` or `--sheet gid:<id>`, or put the tab in the range (`'My Tab'!A1:C5`). Without either, the gid in the URL is used, then the first tab. Tab titles with spaces or non-ASCII characters are fine.
4. **Preview risky or large changes** with `--dry-run`: the exact API request is printed and nothing is sent.

## Safety rules (follow them every time)

- **Never print, log, or write a credential.** Do not run `skynet-base key get` yourself; the script does it internally. If you must show an error, it has already been scrubbed.
- **Destructive commands need `--yes`**, and you should only pass it after the user has confirmed the exact target: `delete-sheet`, `delete-dim`, `delete-range`, `clear`, `batch-clear`, `dedupe`, `unprotect`, `cf-delete`, `banding-delete`, `filter-view-delete`, `named-range-delete`, `chart-delete`, `batch-update` (raw).
- **Overwrites are silent**: `write`, `find-replace`, `sort`, `cut-paste`, `text-to-columns`, `format` replace what is there. Read the range first when it is not empty, and tell the user what will be replaced.
- **Prefer a new tab over editing the original.** For corrected or derived data, `add-sheet` and write there; leave the source tab untouched unless asked.
- **Spreadsheet changes are shared** with everyone who has access. Do not delete tabs, protect ranges, or change sharing-related settings on someone else's document without being asked.
- Keep local files (`--file`, `--spec`, `--output`) inside the working directory.

## Conventions

- Rows and columns on the command line are **1-based and inclusive**: `--start 3 --end 5` is rows 3-5; columns can be letters (`--start B --end D`). `insert-dim --start 3` inserts before row 3; `move-dim --to 7` moves the block so it starts before row 7.
- Ranges are A1 notation: `A1`, `A1:C10`, `A:A` (whole column), `2:5` (whole rows), `A2:V` (open-ended), `'My Tab'!A1:B2`.
- Colors: `#RRGGBB`, `#RGB`, or a name: `white black red green blue yellow orange gray purple pink cyan light-gray light-yellow light-orange light-red light-green light-blue`.
- `write`/`append` default to `USER_ENTERED` (text that starts with `=`, `+`, `-`, `@` can become a formula, numbers and dates are parsed). Add `--raw` to store text exactly as given.
- Output is JSON on stdout (`--output file.json` to save large results). Exit code 0 means success; errors go to stderr as `error: ...`.

## Command catalog

Full option lists: [references/commands.md](references/commands.md). Worked examples: [references/recipes.md](references/recipes.md).

| Group | Commands |
|---|---|
| Credential & read | `auth-check`, `info`, `inspect`, `read`, `batch-read`, `get-format` |
| Values | `write`, `append`, `clear`, `batch-write`, `batch-clear`, `find-replace` |
| Spreadsheet | `create`, `set-properties` (title/locale/time zone), `export` (whole file: xlsx, ods, pdf, csv=first tab, tsv, html), `export-tab` (one tab: csv, tsv, pdf, xlsx), `copy-spreadsheet` |
| Tabs | `add-sheet`, `rename-sheet`, `duplicate-sheet`, `delete-sheet`, `copy-sheet-to`, `move-sheet`, `hide-sheet`, `show-sheet`, `tab-color`, `resize-grid`, `freeze`, `gridlines` |
| Rows, columns & cells | `insert-dim`, `delete-dim`, `move-dim`, `hide-dim`, `show-dim`, `size` (row height / column width), `autosize`, `group-dim`, `ungroup-dim`, `group-toggle` (collapse/expand), `append-dim`, `insert-range` / `delete-range` (shift cells) |
| Formatting | `format`, `format-batch`, `clear-format`, `borders`, `merge`, `unmerge`, `banding-add`, `banding-delete`, `note-set` (cell notes), `cf-add`, `cf-update`, `cf-gradient`, `cf-list`, `cf-delete` |
| Filters & validation | `filter-set`, `filter-clear`, `filter-view-add`, `filter-view-delete`, `validate-set`, `validate-clear` |
| Data operations | `sort`, `dedupe`, `trim`, `text-to-columns`, `copy-paste`, `cut-paste`, `autofill` |
| Protection, names, charts, raw | `protect`, `protect-update`, `unprotect`, `named-range-add`, `named-range-update`, `named-range-delete`, `chart-add`, `chart-update`, `chart-delete`, `batch-update` |

### Most common calls

```bash
S="python3 -B -I scripts/gsheet_ops.py"; URL="https://docs.google.com/spreadsheets/d/<id>/edit?gid=<gid>"

$S info "$URL"                                              # tabs and sizes
$S read "$URL" --range "A1:D10"                             # values
$S write "$URL" --sheet "Report" --range A1 --file rows.json     # rows.json = JSON 2D array; grid grows automatically
$S format "$URL" --range "A1:F1" --bg "#4285f4" --fg white --bold --halign center
$S freeze "$URL" --rows 1
$S size "$URL" --dim cols --start A --end F --pixels 140
$S cf-add "$URL" --range "C2:C500" --type NUMBER_GREATER --values 100 --bg light-red
$S validate-set "$URL" --range "D2:D500" --list "Open,In progress,Done" --strict
$S filter-set "$URL" --range "A1:F500"
$S add-sheet "$URL" --title "Corrected" --rows 2000 --cols 30
```

## Coloring many cells efficiently

Never loop one API call per cell. Build a spec of **contiguous runs** (same column, same color, consecutive rows) and send it once; `format-batch` packs 200 ranges per API call:

```json
[{"range": "A2:A9",  "format": {"bg": "light-yellow"}},
 {"range": "C5:C5",  "format": {"bg": "light-orange", "bold": true}}]
```

```bash
$S format-batch "$URL" --spec spec.json
$S get-format "$URL" --range A1:V1000      # verify: counts per background color and sample cells
```

A real run colored 5,896 cells as 417 ranges in 3 API calls. `get-format --full --output f.json` returns every cell's format for exact verification. Note that `get-format` reports colors set by hand or by this script, not colors produced by conditional-format rules.

## Limits and behavior worth knowing

- **Quota**: about 60 write requests per minute per user (Google's documented default; it can change). The script retries 429/5xx with backoff, and batching keeps you far below the limit.
- **Grid size**: a new tab has 1000 rows x 26 columns. `write` grows it automatically (`--no-grow` disables); other commands need `resize-grid` or `add-sheet --rows/--cols` first.
- **Chunking**: `write` sends 500 rows per request (`--chunk-rows`). For several separate ranges use `batch-write`.
- **`format` only changes the properties you pass**; `clear-format` resets everything.
- **Sorting** needs the header row excluded from `--range`.
- **Coverage**: the commands wrap 47 of the 74 request types of the Sheets API `batchUpdate`. The rest are rarely needed and reachable with `batch-update --requests file.json`: tables (`addTable`, `updateTable`, `deleteTable`), slicers, developer metadata, data sources (BigQuery/Looker), threaded comments, rich-text/hyperlink runs (`updateCells`), `updateFilterView`, `duplicateFilterView`, `updateBanding`, `randomizeRange`, chart position/border. `appendCells` and `pasteData` are covered in practice by `append` and `write`. Use the official request shapes (see recipes) and test with `--dry-run` first.
- Large reads: use `--output file.json` and process the file rather than pasting thousands of cells into the conversation.

## Errors

| Message | Meaning / action |
|---|---|
| `no Google credential found` | The message lists all five methods. Easiest: run `skynet-base gsheet list-sheets "<url>"` once to authorize, then retry |
| `OAuth token request failed: ...` | Google refused the refresh token or service-account key; check the configuration (never print the values) |
| `auth method "..." selected but ...` | A forced method (`--auth` / `GSHEET_OPS_AUTH`) is not configured; remove the override or configure it |
| `HTTP 401` after the automatic renewal | The credential was revoked: re-authorize through `skynet-base` (see references/auth.md) |
| `HTTP 403 ... does not have permission` | The signed-in user cannot edit this spreadsheet; ask the owner for edit access |
| `HTTP 404` | Wrong spreadsheet id, or no access |
| `HTTP 400 ... exceeds grid limits` | Use `write` (auto-grows) or `resize-grid` |
| `HTTP 400 ... Invalid requests[n]` | The index n in the message is the failing request; run the same command with `--dry-run` and compare with [references/commands.md](references/commands.md) |
| `sheet not found: ...; available: [...]` | Tab title typo; use one of the listed titles or `gid:<id>` |
| `... is destructive: re-run with --yes` | Confirm the exact target with the user, then add `--yes` |
| `HTTP 429` | Quota hit; the script already retried. Wait a minute and use `batch-write` / `format-batch` |

More detail: [references/auth.md](references/auth.md), [references/recipes.md](references/recipes.md), [references/commands.md](references/commands.md).
