# scan-to-docx

A Claude Code skill that turns scanned documents into editable Word files. It is built for
old typewritten manuscripts full of handwritten edits, but works for any printed scan in any
language.

## What it does

- **Transcribes carefully.** Applies manual corrections: struck-out words, caret insertions,
  margin notes and overtyped letters. Joins words hyphenated at line ends and fixes obvious
  typos while keeping the original spelling and style.
- **Flags instead of guessing.** Each unclear passage gets alternative readings, a suggested
  choice and its location on the page.
- **Creates an offline review editor.** A single HTML file that needs no server or install.
  You review one page at a time with the scan beside the transcript. Click a flagged passage,
  or any word, to highlight it on the scan. Pick a reading or type your own, and edit any text
  directly. Progress is saved in your browser.
- **Exports .docx in one click.** Passages you haven't reviewed yet stay highlighted in Word.
- **Handles long documents.** Each page has its own data file, so pages can be transcribed in
  parallel and any page can be redone on its own.

## Install

Clone into your Claude Code skills folder. The folder must be named `scan-to-docx`:

```bash
git clone git@github.com:karlovsek/scan-to-docx_skills.git ~/.claude/skills/scan-to-docx
```

Then ask Claude Code something like "convert scan.pdf to Word".

## Requirements

- Poppler (`pdftoppm`, `pdftotext`) to render PDF pages and read word positions
- Python 3 for `scripts/word_boxes.py`, which links words to their position on the scan.
  This only works for PDFs that have an OCR text layer, such as those from Adobe Scan.

## Contents

- `SKILL.md`: the workflow and transcription rules Claude follows
- `assets/editor.html`: the review editor template
- `scripts/word_boxes.py`: matches transcript words to their positions on the scan
