# Recipes

All examples assume `S="python3 -B -I scripts/gsheet_ops.py"` and `URL` = the spreadsheet link. Examples marked **verified** were run against a real spreadsheet while the skill was built; the others follow the official request shapes and should be tried with `--dry-run` first.

## 1. Highlight the cells you changed (verified)

Goal: after writing corrected data to a tab, mark every changed cell and mark guesses differently.

1. Keep a change log while you build the data: `(row, column letter, confidence)`.
2. Group into runs: one entry per column and color for consecutive rows.
3. Write the spec and apply it:

```json
[{"range": "G2:G40",  "format": {"bg": "light-yellow"}},
 {"range": "N57:N57", "format": {"bg": "light-orange"}}]
```

```bash
$S format-batch "$URL" --sheet "Corrected" --spec spec.json
$S get-format  "$URL" --sheet "Corrected" --range A1:V1338      # counts per color, sample cells
```

4. Verify exactly: `get-format ... --full --output f.json`, then compare each cell's `userEnteredFormat.backgroundColor` (floats 0-1; hex = round(v*255)) with the plan. A 5,896-cell run matched 100%.
5. Tell the user the legend (for example yellow = changed, orange = inferred) in the response, and consider adding it as a note row or a legend tab.

## 2. Write a corrected copy into a new tab (verified)

```bash
$S add-sheet "$URL" --title "Corrected_20261101" --rows 3000 --cols 30
$S write "$URL" --sheet "Corrected_20261101" --range A1 --file rows.json --raw
```

`--raw` stops text such as `=foo`, `+1` or `@name` from being parsed as formulas. `write` grows the grid itself, so `--rows/--cols` are optional.

## 3. Standard header styling (verified)

```bash
$S format "$URL" --range "A1:V1" --bg "#4285f4" --fg white --bold --halign center --valign middle --wrap wrap
$S freeze "$URL" --rows 1
$S size   "$URL" --dim cols --start A --end V --pixels 150
$S filter-set "$URL" --range "A1:V1338"
```

## 4. Conditional formatting (verified: NUMBER_GREATER, CUSTOM_FORMULA, gradient)

```bash
# number rule
$S cf-add "$URL" --range "C2:C500" --type NUMBER_GREATER --values 100 --bg light-red --bold
# text rule
$S cf-add "$URL" --range "B2:B500" --type TEXT_CONTAINS --values "overdue" --bg light-orange
# duplicates in column A
$S cf-add "$URL" --range "A2:A500" --type CUSTOM_FORMULA --values '=COUNTIF($A:$A,$A2)>1' --bg light-red
# empty required cells
$S cf-add "$URL" --range "D2:D500" --type BLANK --bg light-yellow
# whole row by status (formula refers to the first row of the range)
$S cf-add "$URL" --range "A2:H500" --type CUSTOM_FORMULA --values '=$F2="Failed"' --bg light-red
# date relative to today: use a formula, e.g. past due
$S cf-add "$URL" --range "E2:E500" --type CUSTOM_FORMULA --values '=AND($E2<>"",$E2<TODAY())' --bg light-red
# color scale
$S cf-gradient "$URL" --range "C2:C500" --min-color white --mid-color yellow --max-color red
$S cf-list "$URL"                       # shows index of each rule
$S cf-delete "$URL" --index 0 --yes
```

Condition `--type` values: `NUMBER_GREATER, NUMBER_GREATER_THAN_EQ, NUMBER_LESS, NUMBER_LESS_THAN_EQ, NUMBER_EQ, NUMBER_NOT_EQ, NUMBER_BETWEEN, NUMBER_NOT_BETWEEN, TEXT_CONTAINS, TEXT_NOT_CONTAINS, TEXT_STARTS_WITH, TEXT_ENDS_WITH, TEXT_EQ, TEXT_NOT_EQ, TEXT_IS_EMAIL, TEXT_IS_URL, DATE_EQ, DATE_NOT_EQ, DATE_BEFORE, DATE_AFTER, DATE_ON_OR_BEFORE, DATE_ON_OR_AFTER, DATE_BETWEEN, DATE_NOT_BETWEEN, DATE_IS_VALID, ONE_OF_RANGE, ONE_OF_LIST, BLANK, NOT_BLANK, CUSTOM_FORMULA, BOOLEAN`. `--values` takes comma separated values (one formula for CUSTOM_FORMULA). Rules are evaluated top to bottom; `--index 0` puts the new rule first.

## 5. Data validation (verified: list, checkbox, number, list-range)

```bash
$S validate-set "$URL" --range "D2:D500" --list "Open,In progress,Done" --strict      # dropdown, rejects other values
$S validate-set "$URL" --range "H2:H500" --checkbox
$S validate-set "$URL" --range "I2:I500" --type NUMBER_BETWEEN --values 1,10 --message "1 to 10"
$S validate-set "$URL" --range "J2:J500" --list-range "Config!A2:A20"                 # dropdown from another range
$S validate-clear "$URL" --range "I2:I500"
```

## 6. Number and date formats

```bash
$S format "$URL" --range "C2:C500" --numfmt NUMBER   --pattern "#,##0.00"
$S format "$URL" --range "D2:D500" --numfmt PERCENT  --pattern "0.0%"
$S format "$URL" --range "E2:E500" --numfmt DATE     --pattern "yyyy-mm-dd"
$S format "$URL" --range "F2:F500" --numfmt CURRENCY --pattern '"$"#,##0.00'
$S format "$URL" --range "G2:G500" --numfmt TEXT                                # keep leading zeros
```

## 7. Insert, delete, move (verified)

```bash
$S insert-dim "$URL" --dim rows --start 3 --count 2             # two new rows before row 3
$S delete-dim "$URL" --dim rows --start 3 --end 4 --yes
$S move-dim   "$URL" --dim cols --start E --end E --to B        # column E becomes column B
$S hide-dim   "$URL" --dim rows --start 20 --end 22
$S group-dim  "$URL" --dim rows --start 30 --end 35             # collapsible outline
```

## 8. Sort, dedupe, trim, split, find/replace (verified)

```bash
$S sort "$URL" --range "A2:H500" --by "C:desc,B:asc"            # range excludes the header row
$S dedupe "$URL" --range "A1:H500" --columns A,C --yes          # keeps the first of each duplicate set
$S trim "$URL" --range "B2:B500"
$S text-to-columns "$URL" --range "D2:D500" --delimiter ";"
$S find-replace "$URL" --find "N/A" --replace "" --entire-cell --match-case --sheet Data
```

## 9. Copy and move blocks (verified)

```bash
$S copy-paste "$URL" --src "A1:B10" --dest "D1" --paste PASTE_VALUES     # also PASTE_FORMAT, PASTE_FORMULA, ...
$S cut-paste  "$URL" --src "D1:E10" --dest "G1"
$S copy-sheet-to "$URL" --sheet Data --dest "<other-spreadsheet-url>"
$S duplicate-sheet "$URL" --sheet Data --new-title "Data (backup)"
```

## 10. Borders, merges, banding (verified)

```bash
$S borders "$URL" --range "A1:E5" --edges outer,inner-h --style DASHED --color "#ff0000"
$S merge "$URL" --range "F1:G2" --type all
$S banding-add "$URL" --range "A1:H200" --header "#4285f4" --first white --second "#f3f3f3"
$S inspect "$URL"                        # lists band ids, filter views, protected ranges, charts
```

## 11. Protect ranges, named ranges (verified, except `--editors`)

```bash
$S protect "$URL" --range "A1:E1" --description "header" --warning-only        # warns instead of blocking
$S protect "$URL" --sheet Config --editors "a@example.com,b@example.com"       # whole tab, only these editors
$S named-range-add "$URL" --name ScoreCol --range "C2:C500"
```

## 12. Charts (verified)

Save a ChartSpec as `chart.json` (ranges use the numeric `sheetId` from `info`):

```json
{"title": "Score", "basicChart": {"chartType": "COLUMN", "legendPosition": "BOTTOM_LEGEND",
  "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
  "domains": [{"domain": {"sourceRange": {"sources": [{"sheetId": 0, "startRowIndex": 0, "endRowIndex": 11, "startColumnIndex": 0, "endColumnIndex": 1}]}}}],
  "series": [{"series": {"sourceRange": {"sources": [{"sheetId": 0, "startRowIndex": 0, "endRowIndex": 11, "startColumnIndex": 2, "endColumnIndex": 3}]}}, "targetAxis": "LEFT_AXIS"}],
  "headerCount": 1}}
```

```bash
$S chart-add "$URL" --spec chart.json --anchor K2 --width 600 --height 371
$S chart-delete "$URL" --id <chartId from inspect> --yes
```

`chartType` can be `COLUMN, BAR, LINE, AREA, SCATTER, COMBO, STEPPED_AREA`; pie charts use `pieChart` instead of `basicChart`.

## 13. Pivot tables, slicers, tables, anything else (raw, not exercised by the test suite)

`batch-update` accepts any list of Sheets API requests. Pivot table example (writes a pivot at `E1` of the tab with `sheetId` 0, from `A1:C500`):

```json
[{"updateCells": {"start": {"sheetId": 0, "rowIndex": 0, "columnIndex": 4},
  "rows": [{"values": [{"pivotTable": {
    "source": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 500, "startColumnIndex": 0, "endColumnIndex": 3},
    "rows": [{"sourceColumnOffset": 0, "showTotals": true, "sortOrder": "ASCENDING"}],
    "values": [{"summarizeFunction": "SUM", "sourceColumnOffset": 2}]}}]}],
  "fields": "pivotTable"}}]
```

```bash
$S batch-update "$URL" --requests pivot.json --dry-run     # inspect first
$S batch-update "$URL" --requests pivot.json --yes
```

Reference for every request type: Google's `spreadsheets.batchUpdate` documentation (Request object).

## 14. Links, images, formulas

No special command: write them as formulas with `USER_ENTERED` (the default): `=HYPERLINK("https://example.com","label")`, `=IMAGE("https://.../logo.png")`, `=SUM(B2:B100)`. Use `--raw` only when you want the text stored literally.

## 15. Export and backup

```bash
$S export "$URL" --format xlsx --output out/report.xlsx          # all tabs, formatting kept
$S export "$URL" --format pdf  --output out/report.pdf
$S copy-spreadsheet "$URL" --title "Report backup 2026-11-01"
```

`--format csv` and `tsv` export only the first tab; for another tab use `read --sheet ... --output tab.json`.

## 16. Filters with criteria (not exercised by the test suite)

`filter-set --criteria criteria.json`, where the file maps a zero-based column index (relative to the sheet) to a criteria object, for example hide two values in column C:

```json
{"2": {"hiddenValues": ["Cancelled", "Failed"]}}
```

Named views that do not disturb other users: `filter-view-add --range A1:H500 --title "Open only"`.

## 17. Importing an Excel file as a native sheet

```bash
skynet-base gdrive upload report.xlsx --mime-type application/vnd.google-apps.spreadsheet --name "Report"
```

This creates a new spreadsheet and keeps the Excel colors and formats. To bring an Excel tab into an existing spreadsheet, a user can do File > Import > "Insert new sheet(s)" in the browser.

## 18. Newer commands (all verified against a real spreadsheet)

```bash
$S insert-range "$URL" --range "A2:B3" --shift down           # blank cells, existing cells move down (or --shift right)
$S delete-range "$URL" --range "A2:B3" --shift up --yes       # cells below move up (or --shift left)
$S autofill "$URL" --source "A20:A21" --dim rows --length 5   # continue the series 1,2 -> 3..7
$S autofill "$URL" --range "C40:C45"                          # first filled cells are the pattern, the rest is filled
$S note-set "$URL" --range "A1" --text "Source: warehouse export"     # cell note; --text "" clears it
$S append-dim "$URL" --dim rows --count 500                   # add rows at the end (cols works too)
$S gridlines "$URL" --mode hide                               # or show
$S group-dim "$URL" --dim rows --start 30 --end 35 && $S group-toggle "$URL" --dim rows --start 30 --end 35 --collapsed true
$S named-range-update "$URL" --id <id from info> --name RevenueCol --range "C2:C500"
$S protect-update "$URL" --id <id from inspect> --description "locked" --no-warning-only --editors "a@x.com"
$S chart-update "$URL" --id <chartId from inspect> --spec chart.json
$S cf-update "$URL" --index 0 --range "B2:B9" --type NUMBER_LESS --values 3 --bg light-green   # replaces rule 0
$S export-tab "$URL" --format csv --output out/data.csv       # one tab only (uses --sheet or the gid of the URL)
```

Tip: name a named range so that it cannot be read as a cell address (`Tmp1` is rejected, `TmpNamedA` works). When seeding data for `autofill --range`, write only the pattern cells; cells that hold an empty string count as filled and are not overwritten.
