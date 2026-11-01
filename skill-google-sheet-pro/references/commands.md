# Command reference

Every command: `python3 -B -I scripts/gsheet_ops.py <command> <ss> [options]`. Every command also accepts `--dry-run`, `--sheet`, `--auth`, `--output` (unless listed). `*` = required option. Exact usage: `<command> --help`.

## Credential & read

- `auth-check` - show which credential method is used, which methods are available, granted scopes and remaining lifetime (never prints a credential)
- `info` <ss> - spreadsheet title/locale/time zone, every tab with size, freeze, hidden, tab color, and named ranges
- `inspect` <ss> - merges, conditional formats, filters, banding, protected ranges, charts, groups of a tab (or all tabs)
- `read` <ss> - read values of a range. Options: --range, --render (default FORMATTED_VALUE), --dim (default ROWS), --datetime
- `batch-read` <ss> - read several ranges at once. Options: --ranges*, --render (default FORMATTED_VALUE)
- `get-format` <ss> - read cell formatting of a range (summary by background color, or --full). Options: --range*, --full

## Values

- `write` <ss> - overwrite values from --values/--file (JSON 2D array); grows the grid and chunks rows automatically. Options: --range (default A1), --values, --file, --raw, --no-grow, --chunk-rows (default 500), --dim (default ROWS)
- `append` <ss> - append rows after the last row of the table (rows are inserted). Options: --range, --values, --file, --raw
- `clear` <ss> - clear values of a range (formats stay). Options: --range*, --yes
- `batch-write` <ss> - write many ranges: --file JSON [{"range":"A1","values":[[...]]},...]. Options: --file*, --raw
- `batch-clear` <ss> - clear several ranges. Options: --ranges*, --yes
- `find-replace` <ss> - find and replace text. Options: --find*, --replace*, --range, --all-sheets, --regex, --match-case, --entire-cell, --formulas

## Spreadsheet

- `create` - create a spreadsheet. Options: --title*, --sheets, --locale, --timezone
- `set-properties` <ss> - rename the spreadsheet / change locale or time zone. Options: --title, --locale, --timezone
- `export` <ss> - export the whole spreadsheet through Drive (xlsx, ods, pdf, csv=first tab, tsv, html=zip). Options: --format=CSV|HTML|ODS|PDF|TSV|XLSX, --output*
- `export-tab` <ss> - export ONE tab as csv/tsv/pdf/xlsx (by --sheet or the gid of the URL). Options: --format=CSV|PDF|TSV|XLSX, --output*
- `copy-spreadsheet` <ss> - make a copy of the whole spreadsheet in Drive. Options: --title*, --folder

## Tabs

- `add-sheet` <ss> - add a tab. Options: --title*, --index, --rows, --cols, --tab-color, --hidden, --freeze-rows, --freeze-cols
- `rename-sheet` <ss> - rename a tab. Options: --new-title*
- `duplicate-sheet` <ss> - duplicate a tab inside the spreadsheet. Options: --new-title, --index
- `delete-sheet` <ss> - delete a tab. Options: --yes
- `copy-sheet-to` <ss> - copy a tab into another spreadsheet. Options: --dest*
- `move-sheet` <ss> - move a tab to a position (0-based index). Options: --index*
- `hide-sheet` <ss> - hide a tab
- `show-sheet` <ss> - unhide a tab
- `tab-color` <ss> - set the tab color. Options: --color*
- `resize-grid` <ss> - set the number of rows/columns of a tab. Options: --rows, --cols
- `freeze` <ss> - freeze rows/columns (0 unfreezes). Options: --rows, --cols
- `gridlines` <ss> - show or hide the gridlines of a tab. Options: --mode*=HIDE|SHOW

## Rows, columns & cells

- `insert-dim` <ss> - insert rows/columns before --start. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --count (default 1), --inherit-before
- `delete-dim` <ss> - delete rows/columns. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end, --yes
- `move-dim` <ss> - move rows/columns so they start before --to. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end, --to*
- `hide-dim` <ss> - hide rows/columns. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end
- `show-dim` <ss> - unhide rows/columns. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end
- `size` <ss> - set row height / column width in pixels. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end, --pixels*
- `autosize` <ss> - auto-fit rows/columns to content. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end
- `group-dim` <ss> - group rows/columns (collapsible outline). Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end
- `ungroup-dim` <ss> - remove a row/column group. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end
- `group-toggle` <ss> - collapse or expand a row/column group. Options: --dim*=COL|COLS|ROW|ROWS|columns, --start*, --end, --collapsed*=FALSE|TRUE, --depth (default 1)
- `append-dim` <ss> - append empty rows or columns at the end of a tab. Options: --dim*=COL|COLS|ROW|ROWS|columns, --count*
- `insert-range` <ss> - insert blank cells and shift the existing cells down or right. Options: --range*, --shift=DOWN|RIGHT
- `delete-range` <ss> - delete cells and shift the rest up or left. Options: --range*, --shift=LEFT|UP, --yes

## Formatting

- `format` <ss> - apply cell formatting to a range. Options: --range*, --bg, --fg, --bold, --italic, --underline, --strike, --font, --size, --halign=CENTER|LEFT|RIGHT|center, --valign=BOTTOM|MIDDLE|TOP|bottom|middle, --wrap=CLIP|OVERFLOW_CELL|WRAP|overflow_cell, --numfmt, --pattern, --rotation
- `format-batch` <ss> - apply many formats in few API calls: --spec JSON [{"range":"A2:A9","format":{"bg":"#FFF2CC","bold":true}},...]. Options: --spec*, --chunk (default 200)
- `clear-format` <ss> - reset all formatting of a range. Options: --range*
- `borders` <ss> - set borders. Options: --range*, --edges (default all), --style (default SOLID), --color (default black)
- `merge` <ss> - merge cells. Options: --range*, --type=ALL|ROWS|columns
- `unmerge` <ss> - unmerge cells. Options: --range*
- `banding-add` <ss> - alternating row colors. Options: --range*, --header (default #4285f4), --first (default #ffffff), --second (default #f3f3f3)
- `banding-delete` <ss> - remove alternating colors by id (see `inspect`). Options: --id*, --yes
- `note-set` <ss> - set the note (comment bubble) of every cell in a range; empty --text clears it. Options: --range*, --text*
- `cf-add` <ss> - add a conditional-format rule (boolean condition). Options: --range*, --type*, --values, --bg, --fg, --bold, --italic, --strike, --index
- `cf-update` <ss> - replace the conditional-format rule at --index (see cf-list) with a new boolean rule. Options: --range*, --type*, --values, --bg, --fg, --bold, --italic, --strike, --index*
- `cf-gradient` <ss> - add a color-scale conditional format. Options: --range*, --min-color (default #ffffff), --max-color (default #57bb8a), --mid-color, --index
- `cf-list` <ss> - list conditional-format rules of a tab with their index
- `cf-delete` <ss> - delete a conditional-format rule by index (see cf-list). Options: --index*, --yes

## Filters & validation

- `filter-set` <ss> - set the basic filter on a range (header row included). Options: --range*, --criteria
- `filter-clear` <ss> - clear the basic filter of a tab
- `filter-view-add` <ss> - add a named filter view. Options: --range*, --title*
- `filter-view-delete` <ss> - delete a filter view by id (see inspect). Options: --id*, --yes
- `validate-set` <ss> - set data validation (dropdown, checkbox, number/date/text rules, custom formula). Options: --range*, --list, --list-range, --checkbox, --type, --values, --message, --strict, --no-dropdown
- `validate-clear` <ss> - remove data validation. Options: --range*

## Data operations

- `sort` <ss> - sort a range (exclude the header row from --range). Options: --range*, --by*
- `dedupe` <ss> - delete duplicate rows by comparing columns. Options: --range*, --columns*, --yes
- `trim` <ss> - trim leading/trailing whitespace. Options: --range*
- `text-to-columns` <ss> - split text of a column into columns. Options: --range*, --delimiter (default COMMA)
- `copy-paste` <ss> - copy a range to another place. Options: --src*, --dest*, --paste (default PASTE_NORMAL)
- `cut-paste` <ss> - move a range to another place (destination = top-left cell). Options: --src*, --dest*, --paste (default PASTE_NORMAL)
- `autofill` <ss> - fill a series/pattern: --range holds the pattern plus the cells to fill; or --source with --dim and --length. Options: --range, --source, --dim=COL|COLS|ROW|ROWS|columns, --length

## Protection, names, charts, raw

- `protect` <ss> - protect a range or a whole tab. Options: --range, --description, --warning-only, --editors
- `protect-update` <ss> - change the description, warning-only mode or editors of a protected range. Options: --id*, --description, --warning-only, --editors
- `unprotect` <ss> - remove protection by id (see inspect). Options: --id*, --yes
- `named-range-add` <ss> - create a named range. Options: --name*, --range*
- `named-range-update` <ss> - rename a named range or change its range. Options: --id*, --name, --range
- `named-range-delete` <ss> - delete a named range by id (see info). Options: --id*, --yes
- `chart-add` <ss> - add a chart from a ChartSpec JSON file (see references/recipes.md). Options: --spec*, --anchor (default A1), --width (default 600), --height (default 371)
- `chart-update` <ss> - replace the spec of an existing chart (by id, see inspect). Options: --id*, --spec*
- `chart-delete` <ss> - delete a chart by id (see inspect). Options: --id*, --yes
- `batch-update` <ss> - send raw batchUpdate requests (any Sheets API request: pivots, slicers, tables, ...). Options: --requests*, --yes

## Format options (format, cf-add/cf-update subset, format-batch "format" keys)

`--bg --fg --bold/--no-bold --italic/--no-italic --underline/--no-underline --strike/--no-strike --font --size --halign --valign --wrap --numfmt --pattern --rotation`. In a `format-batch` spec use the keys `bg fg bold italic underline strike font size halign valign wrap numfmt pattern rotation`.
