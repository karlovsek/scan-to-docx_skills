---
name: scan-to-docx
description: Transcribe scanned documents (PDF pages, JPG/PNG scans, typewritten or printed pages, often with handwritten corrections) into an editable Word .docx, carefully applying manual edits (strike-throughs, caret insertions, margin notes), checking grammar/spelling, and producing a local review app where the user resolves uncertain passages and exports the .docx. Use this whenever the user wants to OCR, transcribe, "convert to text" or "convert to Word/docx" a scan, photo of a page, or scanned PDF — in any language (Slovene, English, German, ...) — even if they don't mention handwriting, review, or an app. Also use when they say things like "pretvori v besedilo", "prepiši ta PDF", "scan to Word", or ask to do "the same as sample1" for other files.
---

# Scan → DOCX with human review

Goal: a faithful transcription of scanned pages into .docx, where every place you are not sure about is visible and decidable by the user, rather than silently guessed. Old typescripts with handwritten corrections are the typical input; the hard part is the handwriting, not the typed text.

**You read every page yourself, with your own vision.** Never use Python, OCR engines or a PDF's text layer to get the text. OCR engines misread handwritten corrections, which is exactly what matters here. The only tool you run on the scan for reading is `pdftoppm` (Poppler), to make page images (step 1); OCR is used later only for word positions (5d).

Documents are usually long, so everything is **per page**: one image and one transcription file for each page, side by side. The deliverables, all next to the source file:
- `<name>_pages/p-1.png`, `p-2.png`, ...: the page images (rendered from the PDF)
- `<name>_pages/p-1.js`, `p-2.js`, ...: the transcription of each page with flagged spots, same stem as its image
- `<name>_review_data.js`: small index file (title, language, document-wide notes, list of page files)
- `<name>_editor.html`: the review app (copied from `assets/editor.html`)
- `<name>.docx`: created by the user with the **Export .docx** button in the editor

## 1. Render the pages, then read the images

- **PDF: render first.** Render every page once, before reading anything:
  `mkdir "<name>_pages"` and `pdftoppm -r 150 -png "<name>.pdf" "<name>_pages/p"`
  List the folder to get the exact file names: pdftoppm pads page numbers by page count (`p-1.png` for <10 pages, `p-01.png` for 10–99, `p-001.png` for 100+). Use those stems for the page data files too (`p-01.png` ↔ `p-01.js`).
  If `pdftoppm` isn't on PATH yet (installed during this session), call it by full path; winget puts it under `%LOCALAPPDATA%\Microsoft\WinGet\Packages\oschwartz10612.Poppler_*\poppler-*\Library\bin\pdftoppm.exe`.
- **Read the rendered PNGs, one page per Read call**, in order, never skipping one. One page at a time gives each page full resolution and your full attention.
- **Zoom into handwriting.** At 150 dpi small handwritten insertions are hard to read. Render zoomed crops of the areas with handwriting at 300 dpi into the scratchpad (not the `_pages` folder) and Read those:
  `pdftoppm -r 300 -png -f N -l N -x 0 -y <top> -W <width> -H <height> "<name>.pdf" "<scratch>/zN_a"`
  Coordinates are in 300-dpi pixels (twice the 150-dpi page image coordinates). Note that with `-f/-l` pdftoppm still appends the page number, so the output is `zN_a-N.png` (padded like above); list the folder if unsure. Typically 3 horizontal bands per page cover everything.
- **Images (jpg/png) as input:** read each one with the Read tool; the page "image" is the original file, and the data files go into `<name>_pages/` named `p-1.js`, `p-2.js`, ... in reading order.
- **If you can't render** (no pdftoppm, no working shell): read the PDF with `Read(file, pages: "N")`, one page per call. If that also fails ("pdftoppm is not installed"), for a short PDF (up to ~10 pages) Read the whole PDF once without `pages` and go through every page; for a longer PDF ask the user to install Poppler (Windows: `winget install oschwartz10612.Poppler`, then restart Claude Code so the new PATH is picked up) or to export the pages as JPG/PNG. Don't silently work from a partial read. Without rendered images each page points at the PDF itself (`"image": "name.pdf", "page": N`); the editor then shows the PDF at that page but can only describe locations in words, without boxes. Tell the user in step 7.
- If a PDF has a text layer, don't rely on it: text layers ignore handwritten corrections. Read the text from the page image.
- **Long documents (>10 pages):** with the Agent tool available, render all pages yourself first, then split page ranges across subagents (about 5–10 pages each). Give each subagent the rules in sections 1–4 and 5a, its exact page image paths, and the file names it must write. Each subagent writes its own `p-N.js` files directly and returns only a short summary (flags, anything it couldn't read). You then write the index file (5b) and the editor. Because every page is its own file, a page can be redone without touching the others.

## 2. Transcribe carefully

Read each page twice: first the whole page for context, then line by line, paying special attention to margins, the space between lines and anything handwritten, because handwriting is where misreadings happen. When in doubt, look at a zoomed crop of only that part. If the doubt remains, it becomes a choice segment (section 3). Don't guess.

Rules (they exist because the output should be the *author's final intended text*, not the typist's first draft and not your modernized version):
- **Apply handwritten corrections:** a strike-through deletes; text written above the line or in the margin with a caret/slash/hook is inserted at that mark; overtyped letters take the corrected letter; small numbers written above words (1, 2, 3) mark a new word order. Note each correction you applied.
- **Join line-break hyphenation** (`govo-` / `ril` → `govoril`) unless the hyphen is a real one. A word split across a page break goes whole onto the earlier page.
- **Keep the original orthography, word order, capitalization and style.** Don't modernize spelling or "improve" sentences.
- **Keep the scan's lines:** end every line of the scan with `\n` in the segment text, in place of the space at the line end (`"... je izbral\nvedno močne ..."`); a paragraph end needs none. A hyphenated word goes whole onto the line where it starts. An insertion belongs to the line it is inserted into. The editor shows the text line by line, numbered, next to the scan, so the user can compare row N with row N.
- Keep page numbers/headers as their own paragraph (e.g. centered `- 11 -`); keep paragraph breaks and first-line indents. A page that starts mid-sentence keeps its lowercase start.
- Don't invent text you can't see. Illegible → a flagged spot with your best guess plus `[nečitljivo]`/`[illegible]` as an option.

## 3. Flag uncertain spots (the important part)

Anything where a reasonable reader of the scan could choose differently becomes a **choice segment** instead of plain text. Typical cases:
- A handwritten insertion or deletion whose scope or position is unclear (was "tu" deleted or kept? where does a margin word go?)
- Handwriting you can't read with confidence
- A word-order change marked with numbers (offer the reordered and the typed order)
- A literal reading that's ungrammatical where the intended form is probably different (literal `takrat politični` vs intended `takratni politični`): offer both, suggest the likely intended one, and say why
- Punctuation that may have been struck out
- Capitalization that depends on whether something is a proper name
- Optional style improvements. Offer these only as a choice and keep the original as the suggestion unless it's actually wrong

Each choice gets a short title, a note describing **exactly what is visible on the page** (so the user can verify against the scan), 2–4 options, the index of your suggested option, and a `confidence` (0–100): how sure you are that the suggested option is right. Calibrate it honestly (95+ clearly visible and only flagged for form, ~80 likely, ~60 real doubt, below 50 a best guess); the editor shows it next to the spot and marks values below 60 in red, so the user can start with the weakest. Make each option a complete phrase that can stand in the sentence, so spacing works out. Aim for precision: don't flag clear typed text, and don't hide real doubts. Too many flags waste the user's time, but a missed doubt ends up as a silent error.

Also give each choice its **location on the page** as `boxes`: a list of rectangles `{"x","y","w","h"}` in fractions of the page width/height (0–1, origin top-left). Estimate them from the page image (pixel position ÷ image size); err on the side of slightly too large. Use several rectangles when a correction is split (e.g. a margin note plus an insertion above the line). The editor draws the boxes over the scan and scrolls there; the user can fix a wrong box ("Fix location").

## 4. Grammar and spelling check

After transcribing, re-read each page as text in its language:
- Clear typewriter typos (wrong/doubled letter, overstrike, missing space after a comma) → fix them and list each fix in that page's `notes`
- Real grammar issues that may be the author's choice, or that change meaning → choice segment, never a silent fix
- Write `notes` for the user: what you joined, fixed, and checked, and an honest "no other issues found" if that's true

## 5. Write the data files and the editor

### 5a. One file per page

Write `<name>_pages/<stem>.js` for each page, next to its image (`p-1.js` next to `p-1.png`). It registers the page under its stem:

```js
(window.REVIEW_PAGES = window.REVIEW_PAGES || {})["p-1"] = {
  "image": "sample1_pages/p-1.png",
  "notes": ["Joined hyphenation: govo-ril, ob-sojen.", "Fixed typo overstrike 'gobvo-' → 'govoril'."],
  "paragraphs": [
    {"style": "center", "segments": ["- 11 -"]},
    {"style": "indent", "segments": [
      "Zoper Bonača, ki je bil ",
      {"id": "p1c1", "title": "\"tu\" struck, \"na sodniji\" above",
       "note": "\"tu\" has a stroke through it; \"na sodniji\" is written above with an insertion mark.",
       "suggested": 0, "confidence": 85,
       "boxes": [{"x": 0.45, "y": 0.215, "w": 0.13, "h": 0.085}],
       "options": [{"text": "na sodniji"}, {"text": "tu na sodniji"}, {"text": "tu", "label": "typed text only"}]},
      " brez moči\nin premoženja ..."
    ]}
  ]
};
```

- The key (`"p-1"`) must equal the file's stem exactly, including padding (`"p-01"`).
- `image` is relative to the folder of the editor (the source file's folder), so it includes `<name>_pages/`. Without rendered images: `"image": "sample1.pdf", "page": 1`.
- `notes` are per page; the editor shows them prefixed with the page number.
- Write `notes`, `title`, `note` and `label` in the user's language (the language they wrote to you in), not in the language of these instructions or of a subagent brief.
- `style` is `""`, `"indent"` (first-line indent) or `"center"`. `label` is an optional explanation shown in the app but not exported. `boxes` is only for showing the location in the editor.
- `\n` marks the end of a scan line (section 2) and may also appear inside an option's `text` when the line ends within the choice.
- Choice ids must be unique across the whole document: prefix them with the page number (`p12c1`, `p12c2`, ...).
- Use strict JSON after the `=` (no comments in the real file).

### 5b. The index file

Write `<name>_review_data.js` next to the source file, after all page files exist (list the `_pages` folder to build `pageFiles`, in page order):

```js
window.REVIEW = {
  "title": "sample1.pdf",
  "lang": "sl-SI",
  "pageBreaks": true,
  "notes": ["Document-wide notes, e.g. kept spellings 'vtem', 'na.pr.'.", "No other issues found."],
  "pageFiles": ["sample1_pages/p-1.js", "sample1_pages/p-2.js"]
};
```

A missing or broken page file shows up in the editor as a "[page N missing]" paragraph and a note, instead of breaking the whole app. (The editor still accepts the old single-file format with a `"pages"` array.)

Validate before reporting: load all files with Node and check that every page registered and every choice id is unique, e.g.
`node -e 'global.window={};require("./<name>_review_data.js");for(const f of window.REVIEW.pageFiles)require("./"+f);...'`

### 5c. The editor

Copy `assets/editor.html` to `<name>_editor.html` in the source file's folder, replacing the placeholder `__DATA_FILE__` with the index file's name (e.g. `sed 's/__DATA_FILE__/<name>_review_data.js/g'`, or Read the asset and Write the copy).

### 5d. Word positions and line check

Run `python3 <skill dir>/scripts/word_boxes.py <source.pdf> <name>_review_data.js` after 5b (for JPG/PNG input leave out the PDF: `word_boxes.py <name>_review_data.js`). It finds where each transcript word is on the scan, writes `<stem>.words.js` next to each page file and adds `"wordFiles"` to the index; clicking a plain word in the editor then highlights it on the scan. Positions come from the PDF's own OCR layer (Adobe Scan and most phone scanner apps include one; needs Poppler's `pdftotext`); without one (copier scans, images) from Tesseract on the page images, if `tesseract` and the language pack are installed (`sudo apt install tesseract-ocr tesseract-ocr-slv`). With neither it says so and changes nothing.

It also compares your line breaks with the OCR's lines: a page without any `\n` gets them filled in (the page file is rewritten); a page with them is only checked, and the lines that differ are printed. Look at each reported line on the image and fix the transcript only if the scan agrees: around handwritten corrections the OCR often splits or misreads a line, so some reports are false alarms. In every case OCR is used only for positions and lines, never for the text. Rerun it whenever a page file is rewritten, since positions refer to paragraph/segment/word numbers.

## 6. Export from the editor

The .docx is made by the user, not by you. They open the editor, review the yellow spots and click **Export .docx**. The editor shows one page at a time; the page navigation above the scan switches both the scan and the text. The text is shown line by line, numbered like the scan (page-number lines are not counted); the export joins the lines into normal paragraphs. The export uses their reviewed choices; spots they haven't reviewed yet are highlighted yellow in the .docx so they can find them in Word.

## 7. Report back

Tell the user, briefly:
- The full path of the editor, and that the .docx comes from its **Export .docx** button
- The transcription with suggested readings (for short docs; for long ones, a summary)
- The flagged spots, one line each, most meaning-relevant first (for long docs: the count per page plus the most important ones)
- The grammar notes
- Anything you couldn't read or verify, and whether pages were rendered as images and read one at a time (if not, why, and what the user can install)

Reply in the user's language.
