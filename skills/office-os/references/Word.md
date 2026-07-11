# Word workflow

Use this reference for .docx work. Recognize .doc and .docm, but request conversion before writing.

## Chunk model

Use semantic structure for editing:

1. title and heading outline;
2. one heading/topic and its paragraphs, tables, figures, footnotes, or comments;
3. linked cross-references, headers/footers, numbering, and styles.

Page boundaries are rendering outcomes, not the primary content model. Use pages only during visual QA.

## Editing

Preserve section properties, styles, numbering, fields, hyperlinks, tables, headers, footers, footnotes, endnotes, comments, and language settings unless the task changes them.

Edit at the smallest semantic topic that preserves meaning. Keep terminology and defined terms consistent across the whole document. When one change affects a cross-reference, table of contents, numbering chain, header/footer, or page count, include that dependency surface in validation.

Use the source or template as visual authority. For a new neutral document, use a restrained heading hierarchy, readable body text, consistent spacing, accessible tables, and explicit figure/table captions.

## QA routing

Fast:

- package opens and semantic outline remains valid;
- changed topics read coherently;
- changed tables keep headers and row/column meaning;
- render an overview and inspect changed or flagged pages full size.

Enhanced:

- verify section breaks, numbering, fields, cross-references, headers/footers, table-of-contents effects, and changed pagination;
- inspect all pages touched by reflow plus the boundary page before and after;
- check terminology and claim consistency across related headings.

Full:

- use for explicit request, legal/regulatory/high-risk delivery, or repeated layout/content anomalies;
- review every relevant topic and rendered page while still grouping findings by semantic topic.

## Review findings

Report heading/topic, paragraph or table locator, issue, impact, and proposed wording. Separate factual uncertainty from style preference.

## Sources

- [Microsoft: Structure of a WordprocessingML document](https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document)
- [Microsoft: Work with paragraphs](https://learn.microsoft.com/en-us/office/open-xml/word/how-to-retrieve-the-paragraphs-from-a-word-processing-document)
- [Microsoft: Accessibility in Word](https://support.microsoft.com/en-us/office/make-your-word-documents-accessible-to-people-with-disabilities-d9bf3683-87ac-47ea-b91a-78dcacb3c66d)
