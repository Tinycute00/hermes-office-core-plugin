# PDF workflow

PDF is read-only in Office OS v1. Never publish a modified PDF.

## Chunk model

Use:

1. document metadata and page count;
2. relevant page range;
3. visible section, heading, table, figure, annotation, or form region.

Retrieve text first when available. Render only relevant, changed-by-comparison, or visually uncertain pages. For scanned pages, use available OCR capability and label OCR-derived text as lower confidence when appropriate.

## Analysis and review

Preserve page references in every material finding. Distinguish selectable text, OCR text, annotations, and visual interpretation. For tables, confirm headers, units, continuations across pages, and footnotes before comparing values.

Fast:

- inspect relevant sections and page thumbnails;
- open full size only pages supporting findings or showing anomalies.

Enhanced:

- inspect neighboring pages, repeated headers/footers, table continuations, figures, forms, signatures, and cross-references;
- compare rendered page evidence with extracted text.

Full:

- use only when explicit, high risk, or anomalies repeat;
- inspect every relevant page, not necessarily every blank or out-of-scope appendix page.

If the user asks to edit a PDF, offer a derived editable Office format or request the source document. Keep the original PDF unchanged.

## Sources

- [Adobe: PDF specification archive](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdflsdk/)
- [OpenAI bundled PDF workflow documentation](https://learn.chatgpt.com/docs/work-with-files)
- [OfficeBench](https://arxiv.org/abs/2407.19056)
