# Excel workflow

Use this reference for .xlsx work. Recognize .xls and .xlsm, but request conversion before writing.

## Chunk model

Route work through:

1. workbook and sheet inventory;
2. table or used-range boundary;
3. formula family, named range, chart, pivot, or validation dependency;
4. changed cells and their downstream checks.

A useful ChunkLocator names sheet, range, object kind, and dependency keys. Avoid treating an entire workbook as one chunk unless it is genuinely small.

## Editing

Preserve workbook structure, styles, number formats, formulas, validations, merges, hidden states, defined names, print settings, and chart sources unless the task changes them.

Use a supplied workbook or template as the visual authority. For new work without a template, use restrained formatting: one clear header hierarchy, consistent number formats, frozen headers where useful, readable widths, and no decorative chart that obscures the decision.

Write only to the derived .xlsx candidate. Keep a formula as a formula when the source expresses logic; do not replace it with its cached value.

## QA routing

Fast:

- open/package validation;
- changed ranges contain expected values and formulas;
- neighboring labels, formats, and totals remain coherent;
- workbook overview for obviously broken sheets or charts.

Enhanced:

- trace formula-family references into and out of changed ranges;
- verify units, date serial interpretation, duplicate join keys, and blank/error behavior;
- inspect affected charts, named ranges, validations, pivots, and cross-sheet totals;
- compare key totals against source or stated business rules.

Full:

- use only when requested or when high-risk financial/regulatory output or repeated anomalies justify it;
- inspect every relevant sheet and dependency surface, not every physically empty cell.

Escalate automatically for formula changes, cross-file joins, chart-source changes, global formatting, pivots, external links, or low confidence.

## Review findings

Report findings with sheet and range, observed value or formula family, business impact, and suggested action. Distinguish a suspicious value from a proven error.

## Sources

- [Microsoft: Structure of a SpreadsheetML document](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document)
- [Microsoft: Working with formulas](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/how-to-insert-a-formula-into-a-cell-in-a-spreadsheet)
- [Microsoft: Accessibility in Excel](https://support.microsoft.com/en-us/office/make-your-excel-documents-accessible-to-people-with-disabilities-6cc05fc5-1314-48b5-8eb3-683e49b3e593)
