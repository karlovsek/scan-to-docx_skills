#!/usr/bin/env python3
"""Link transcript words to their position on the scan.

Usage: python3 word_boxes.py [<source.pdf>] <name>_review_data.js

Word boxes come from the PDF's own OCR text layer (`pdftotext -bbox`, Poppler). Without a PDF
or without a text layer (copier scans, JPG/PNG input) they come from Tesseract run on the page
images, if `tesseract` is installed. The boxes are aligned with the transcript in the page files;
<stem>.words.js is written next to each page file and "wordFiles" is set in the index. The editor
then highlights a clicked word on the scan. With neither source it changes nothing.

Also uses the OCR rows for the transcript's line breaks ("\n" in segment text = end of a scan
line): a page without any is filled in (its page file is rewritten), a page with them is only
checked and the differences are printed. OCR gives positions only, never text.
"""
import html, json, os, re, shutil, statistics, subprocess, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

MIN_SIM = 0.6   # minimum similarity for two words to count as the same word
MERGE_SIM = 0.8   # stricter minimum when words are joined (split words, missing spaces)
BAND = 80       # alignment only looks this many words off the diagonal
PAD = 0.003     # padding around each box, in page fractions
ROW_GAP = 0.4   # a new OCR row starts where word centres jump by more than this x median word height
INTERLINE = 0.6  # a short row closer than this x line pitch to a full row is interlinear handwriting
PAGE_NO = re.compile(r"^[\s\-–—.]*\d+[\s\-–—.]*$")  # a paragraph with only a page number, e.g. "- 8 -"


def load_js(path):
    s = Path(path).read_text(encoding="utf-8")
    m = re.search(r"=\s*\{", s)
    return json.JSONDecoder().raw_decode(s[m.end() - 1:])[0]


def pdf_words(pdf):
    out = subprocess.run(["pdftotext", "-bbox", pdf, "-"], capture_output=True, text=True, check=True).stdout
    pages = []
    for pg in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', out, re.S):
        W, H = float(pg[1]), float(pg[2])
        words = []
        for w in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', pg[3]):
            x0, y0, x1, y1 = (float(w[i]) for i in range(1, 5))
            words.append({"text": html.unescape(w[5]), "box": (x0 / W, y0 / H, x1 / W, y1 / H)})
        pages.append(words)
    return pages


TESS_LANG = {"sl": "slv", "en": "eng", "de": "deu", "hr": "hrv", "sr": "srp", "it": "ita", "fr": "fra", "hu": "hun"}


def image_words(path, lang):
    """Word boxes of one page image from Tesseract (positions only), in reading order."""
    # one thread per process: pages already run in parallel, and nested OpenMP threads thrash
    out = subprocess.run(["tesseract", str(path), "stdout", "-l", lang, "tsv"], capture_output=True, text=True,
                         check=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
    rows = [r.split("\t") for r in out.splitlines()[1:]]
    W, H = next((int(r[8]), int(r[9])) for r in rows if r[0] == "1")
    # Tesseract's boxes hug the ink, so their centres jump with ascenders/descenders: keep its own line ids
    return [{"text": r[11], "box": (int(r[6]) / W, int(r[7]) / H, (int(r[6]) + int(r[8])) / W, (int(r[7]) + int(r[9])) / H),
             "line": (int(r[2]), int(r[3]), int(r[4]))}
            for r in rows if r[0] == "5" and len(r) > 11 and r[11].strip()]


def tesseract_pages(idx, base):
    """Word boxes for every page with an image file, or None without Tesseract."""
    if not shutil.which("tesseract"):
        return None
    have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    lang = TESS_LANG.get((idx.get("lang") or "en").split("-")[0].lower(), "eng")
    lang = "+".join(l for l in (lang, "eng") if l in have) or "eng"
    images = [load_js(base / f).get("image", "") for f in idx["pageFiles"]]
    def reading_order(ws):
        # Tesseract lists handwriting between the lines as separate blocks; the alignment needs
        # reading order: by row, then left to right (an insertion lands where it is inserted)
        rows = scan_rows(ws)
        return [ws[j] for j in sorted(range(len(ws)), key=lambda j: (rows[j], ws[j]["box"][0]))]
    run = lambda im: reading_order(image_words(base / im, lang)) if re.search(r"\.(png|jpe?g|tiff?|bmp|webp)$", im, re.I) and (base / im).exists() else []
    with ThreadPoolExecutor() as ex:
        return list(ex.map(run, images))


FOLD = str.maketrans({"đ": "d", "ð": "d", "ł": "l", "ø": "o", "ı": "i", "0": "o", "1": "l", "2": "z"})


def norm(t):
    t = unicodedata.normalize("NFKD", t.lower().translate(FOLD))
    return "".join(c for c in t if c.isalnum() and not unicodedata.combining(c))


def sim(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) <= 2 or abs(len(a) - len(b)) > max(len(a), len(b)) / 2:
        return 0.0
    r = SequenceMatcher(None, a, b).ratio()
    return r if r >= MIN_SIM else 0.0


def seg_text(s):
    return s if isinstance(s, str) else s["options"][s.get("suggested", 0)]["text"]


def transcript_tokens(page):
    """Words of the page in reading order; ref is (paragraph, segment, word) for plain text, None for choices."""
    toks = []
    for pi, p in enumerate(page.get("paragraphs", [])):
        for si, s in enumerate(p.get("segments", [])):
            if isinstance(s, str):
                toks += [((pi, si, wi), m.group()) for wi, m in enumerate(re.finditer(r"\S+", s))]
            else:
                opt = s["options"][s.get("suggested", 0)]["text"]
                toks += [(None, w) for w in opt.split()]
    return toks


def align(tn, on):
    """Maximise total similarity; a transcript word may match 1-3 OCR words (split words, hyphenation),
    two transcript words may match one OCR word (missing space). Returns {transcript index: [ocr indices]}."""
    n, m = len(tn), len(on)
    NEG = float("-inf")
    best = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        c = i * m / max(n, 1)
        for j in range(max(0, int(c) - BAND), min(m, int(c) + BAND) + 1):
            v = best[i][j]
            if v == NEG:
                continue
            moves = [(i + 1, j, 0.0), (i, j + 1, 0.0)]
            if i < n:
                for k in (1, 2, 3):
                    # a merge needs every piece: one that normalises to nothing ("-") would drag the word's box away
                    if j + k <= m and (k == 1 or all(on[j:j + k])):
                        s = sim(tn[i], "".join(on[j:j + k]))
                        moves.append((i + 1, j + k, s if k == 1 or s >= MERGE_SIM else 0.0))
                if i + 1 < n and j < m and tn[i] and tn[i + 1]:
                    s = sim(tn[i] + tn[i + 1], on[j])
                    moves.append((i + 2, j + 1, s if s >= MERGE_SIM else 0.0))
            for ni, nj, s in moves:
                if ni <= n and nj <= m and (s or (ni - i) + (nj - j) == 1):
                    if v + s > best[ni][nj]:
                        best[ni][nj], back[ni][nj] = v + s, (i, j, s > 0)
    # the end may not be reachable inside the band in pathological cases: take the best reachable cell
    i, j = max(((a, b) for a in range(n + 1) for b in range(m + 1) if best[a][b] > NEG),
               key=lambda ab: (best[ab[0]][ab[1]], ab[0] + ab[1]))
    pairs = {}
    while (i, j) != (0, 0):
        pi_, pj, matched = back[i][j]
        if matched:
            for t in range(pi_, i):
                pairs[t] = list(range(pj, j))
        i, j = pi_, pj
    return pairs


def join_hyphens(tn, ocr, pairs):
    """A word hyphenated over two lines is often matched by its second half alone (OCR misread the first
    half, or junk sits between them). When the match is only the word's end, take in the unused OCR word
    with its beginning a few words back, so the word starts on that line. Returns the pairs and the words
    still matched by their end only: their line is unknown."""
    used = {j for js in pairs.values() for j in js}
    loose = set()
    for t, js in pairs.items():
        got = "".join(norm(ocr[j]["text"]) for j in js)
        if got == tn[t] or not tn[t].endswith(got):
            continue
        rest = tn[t][:-len(got)]
        for i in range(js[0] - 1, max(-1, js[0] - 7), -1):
            ni = norm(ocr[i]["text"])
            if i not in used and ni and (rest.startswith(ni) or ni.endswith(rest)):
                pairs[t] = [i] + js
                used.add(i)
                break
        else:
            loose.add(t)
    return pairs, loose


def padded(b):
    x0, y0, x1, y1 = b
    x0, y0, x1, y1 = max(0, x0 - PAD), max(0, y0 - PAD), min(1, x1 + PAD), min(1, y1 + PAD)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def same_line(a, b):
    ha, hb = a[3] - a[1], b[3] - b[1]
    return abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < max(ha, hb) / 2


def page_entries(toks, pairs, ocr):
    boxes = {i: [ocr[j]["box"] for j in js] for i, js in pairs.items()}
    approx = {}
    # unmatched words between two matched words on the same line: share the gap between them
    i = 0
    while i < len(toks):
        if i in boxes:
            i += 1
            continue
        k = i
        while k < len(toks) and k not in boxes:
            k += 1
        if i > 0 and k < len(toks):
            a, b = boxes[i - 1][-1], boxes[k][0]
            if same_line(a, b) and b[0] - a[2] > 0.01:
                lens = [len(toks[t][1]) for t in range(i, k)]
                x, step = a[2], (b[0] - a[2]) / sum(lens)
                for t, L in zip(range(i, k), lens):
                    approx[t] = [(x, min(a[1], b[1]), x + L * step, max(a[3], b[3]))]
                    x += L * step
        i = k
    out, plain = [], 0
    for t, (ref, _) in enumerate(toks):
        if ref is None:
            continue
        plain += 1
        bx = boxes.get(t) or approx.get(t)
        if bx:
            # one rectangle per line: merged words on the same line become one box
            rects = []
            for b in bx:
                if rects and same_line(rects[-1], b):
                    r = rects[-1]
                    rects[-1] = (min(r[0], b[0]), min(r[1], b[1]), max(r[2], b[2]), max(r[3], b[3]))
                else:
                    rects.append(b)
            out.append([*ref, [padded(r) for r in rects]] + ([1] if t in approx else []))
    return out, plain, sum(1 for t in boxes if toks[t][0] is not None), sum(1 for t in approx if toks[t][0] is not None)


def token_spans(page):
    """(paragraph, segment, start, end) of each transcript word, in the order of transcript_tokens."""
    return [(pi, si, m.start(), m.end())
            for pi, p in enumerate(page.get("paragraphs", []))
            for si, s in enumerate(p.get("segments", []))
            for m in re.finditer(r"\S+", seg_text(s))]


def scan_rows(ocr):
    """Row number of each OCR word, rows clustered by vertical centre. A short row (<= 2 words) close to
    a full row is interlinear handwriting or a mark ("2 3"); its words count to the nearest full row."""
    if not ocr:
        return []
    cy = [(w["box"][1] + w["box"][3]) / 2 for w in ocr]
    if all("line" in w for w in ocr):  # OCR engine's own lines, top to bottom
        by_line = {}
        for j, w in enumerate(ocr):
            by_line.setdefault(w["line"], []).append(j)
        rows = sorted(by_line.values(), key=lambda r: statistics.median(cy[j] for j in r))
    else:
        h = statistics.median(w["box"][3] - w["box"][1] for w in ocr)
        rows, last = [], None
        for j in sorted(range(len(ocr)), key=cy.__getitem__):
            if last is None or cy[j] - last > ROW_GAP * h:
                rows.append([])
            rows[-1].append(j)
            last = cy[j]
    centre = [statistics.median(cy[j] for j in r) for r in rows]
    full = [r for r in range(len(rows)) if len(rows[r]) > 2]
    target = list(range(len(rows)))
    if len(full) > 1:
        pitch = statistics.median(centre[b] - centre[a] for a, b in zip(full, full[1:]))
        for r in range(len(rows)):
            if len(rows[r]) <= 2:
                f = min(full, key=lambda f: abs(centre[f] - centre[r]))
                if abs(centre[f] - centre[r]) < INTERLINE * pitch:
                    target[r] = f
    rank = {r: i for i, r in enumerate(sorted(set(target)))}
    row_of = [0] * len(ocr)
    for r, js in enumerate(rows):
        for j in js:
            row_of[j] = rank[target[r]]
    return row_of


def scan_breaks(spans, pairs, row_of):
    """Words that start a new scan line inside their paragraph (indices into spans), and for each
    such break the earliest word it may also start at: between two recognised words on different
    rows, a run of unrecognised words (e.g. a handwritten insertion) can belong to either line."""
    n = len(spans)
    # a word split over two lines belongs to the row where it starts
    row = [row_of[pairs[t][0]] if pairs.get(t) else None for t in range(n)]
    for t in range(1, n):  # an unmatched word is on the line of the word before it ...
        if row[t] is None and spans[t][0] == spans[t - 1][0]:
            row[t] = row[t - 1]
    for t in range(n - 2, -1, -1):  # ... or, at a paragraph start, of the word after it
        if row[t] is None and spans[t][0] == spans[t + 1][0]:
            row[t] = row[t + 1]
    prev = -1
    for t in range(n):  # rows never go back up the page
        prev = row[t] = max(prev, row[t] if row[t] is not None else prev)
    breaks = {t for t in range(1, n) if spans[t][0] == spans[t - 1][0] and row[t] > row[t - 1]}
    earliest = {}
    for b in breaks:
        lo = b
        while lo > 1 and not pairs.get(lo - 1) and spans[lo - 2][0] == spans[b][0]:
            lo -= 1
        earliest[b] = lo
    return breaks, earliest


def place(page, spans, t):
    """The whitespace a line break before word t replaces, as (paragraph, segment, start, end);
    inside a choice the positions are in its suggested option. None where the words touch."""
    (pi, sa, _, ea), (_, sb, sbeg, _) = spans[t - 1], spans[t]
    segs = page["paragraphs"][pi]["segments"]
    if sa == sb:
        return (pi, sa, ea, sbeg)
    for si in range(sa, sb + 1):
        s = segs[si]
        if not isinstance(s, str):
            continue
        m = re.search(r"\s+$", s) if si == sa else re.match(r"\s+", s) if si == sb else re.fullmatch(r"\s+", s)
        if m:
            return (pi, si, m.start(), m.end())
    return None


def placed(page, spans, breaks):
    """Where the breaks go: each at its own spot, or carried to the next possible spot in the paragraph."""
    out, carry = {}, 0
    for t in range(1, len(spans)):
        if spans[t][0] != spans[t - 1][0]:
            carry = 0  # a paragraph end is a line end anyway
        carry += t in breaks
        if carry and (p := place(page, spans, t)):
            out[t] = p
            carry -= 1
    return out


def put_break(page, spot):
    pi, si, a, b = spot
    segs = page["paragraphs"][pi]["segments"]
    s = segs[si]
    if isinstance(s, str):
        segs[si] = s[:a] + "\n" + s[b:]
        return
    # inside a choice: the suggested option breaks here, another option where its words line up with it
    sug = seg_text(s)
    before, after = sug[:a].split(), sug[b:].split()
    for o in s["options"]:
        ms = list(re.finditer(r"\S+", o["text"]))
        words = [m.group() for m in ms]
        if words[:len(before)] == before:
            k = len(before)
        elif words[len(words) - len(after):] == after:
            k = len(words) - len(after)
        else:
            continue
        if k < len(words):
            o["text"] = o["text"][:ms[k - 1].end() if k else 0] + "\n" + o["text"][ms[k].start():]


def text_breaks(page, spans):
    """Words that start a new line in the transcript as written."""
    out = set()
    for t in range(1, len(spans)):
        (pa, sa, _, ea), (pb, sb, sbeg, _) = spans[t - 1], spans[t]
        if pa != pb:
            continue
        segs = page["paragraphs"][pa]["segments"]
        gap = seg_text(segs[sa])[ea:sbeg] if sa == sb else (
            seg_text(segs[sa])[ea:] + "".join(seg_text(s) for s in segs[sa + 1:sb]) + seg_text(segs[sb])[:sbeg])
        if "\n" in gap:
            out.add(t)
    return out


def line_numbers(page, spans, breaks):
    """The editor's line number of each word; None in a page-number paragraph (the editor doesn't number it)."""
    nonum = [bool(PAGE_NO.match("".join(seg_text(s) for s in p.get("segments", []))))
             for p in page.get("paragraphs", [])]
    nums, ln = [], 0
    for t, (pi, *_) in enumerate(spans):
        if not nonum[pi] and (t == 0 or pi != spans[t - 1][0] or t in breaks):
            ln += 1
        nums.append(None if nonum[pi] else ln)
    return nums


def page_lines(page, spans, pairs, ocr):
    """Fill the line breaks from the scan on a page without any; check them on a page with some.
    Returns (report, changed)."""
    has = any("\n" in seg_text(s) or (not isinstance(s, str) and any("\n" in o["text"] for o in s["options"]))
              for p in page.get("paragraphs", []) for s in p.get("segments", []))
    breaks, earliest = scan_breaks(spans, pairs, scan_rows(ocr))
    if not has:
        spots = placed(page, spans, breaks)
        for spot in sorted(spots.values(), reverse=True):
            put_break(page, spot)
        n = max([x for x in line_numbers(page, spans, set(spots)) if x] or [0])
        return f"{n} lines (filled from scan)", bool(spots)
    actual = text_breaks(page, spans)
    for b, lo in earliest.items():  # a break anywhere in a run of unrecognised words counts as the scan's
        hit = [t for t in actual if lo <= t <= b]
        if len(hit) == 1:
            actual = actual - set(hit) | {b}
    # a break written where the words touch, or carried past that spot, are both fine
    expected = min((breaks, set(placed(page, spans, breaks))), key=lambda e: len(e ^ actual))
    nums = line_numbers(page, spans, actual)
    n = max([x for x in nums if x] or [0])
    if expected == actual:
        return f"{n} lines, matches scan", False
    word = lambda t: seg_text(page["paragraphs"][spans[t][0]]["segments"][spans[t][1]])[spans[t][2]:spans[t][3]]
    diffs = dict.fromkeys(f'line {nums[t]} starts with "{word(t)}", on the scan it continues line {nums[t] - 1}'
             if t in actual else f'line {nums[t]}: on the scan a new line starts at "{word(t)}"'
             for t in sorted(expected ^ actual))
    return f"{n} lines, {len(diffs)} line break{'s' if len(diffs) > 1 else ''} differ from the scan: " + "; ".join(diffs), False


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    pdf, index = (sys.argv[1] if len(sys.argv) == 3 else None), Path(sys.argv[-1])
    base = index.parent
    idx = load_js(index)
    if "pageFiles" not in idx:
        sys.exit("Index has no pageFiles (old single-file format); not supported.")
    ocr_pages = pdf_words(pdf) if pdf else []
    if any(ocr_pages):
        by_file = None  # PDF text layer, indexed by PDF page number
    else:
        print("No PDF text layer: " + ("using Tesseract on the page images." if shutil.which("tesseract")
                                       else "no word positions available (install tesseract-ocr). Nothing changed."))
        by_file = tesseract_pages(idx, base)
        if not by_file or not any(by_file):
            return
    word_files = []
    for i, f in enumerate(idx["pageFiles"]):
        page = load_js(base / f)
        pno = page.get("page") or i + 1
        if by_file is not None:
            ocr = by_file[i]
        else:
            ocr = ocr_pages[pno - 1] if pno <= len(ocr_pages) else []
        ocr = [w for w in ocr if norm(w["text"])]  # punctuation and marks can't match a word
        toks = transcript_tokens(page)
        tn = [norm(t) for _, t in toks]
        pairs, loose = join_hyphens(tn, ocr, align(tn, [norm(w["text"]) for w in ocr]))
        entries, plain, exact, approx = page_entries(toks, pairs, ocr)
        lines, changed = page_lines(page, token_spans(page), {t: js for t, js in pairs.items() if t not in loose}, ocr)
        stem = Path(f).stem
        if changed:
            (base / f).write_text(f'(window.REVIEW_PAGES = window.REVIEW_PAGES || {{}})["{stem}"] = '
                                  + json.dumps(page, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
        wf = re.sub(r"\.js$", ".words.js", f, flags=re.I)
        (base / wf).write_text(f'(window.REVIEW_WORDS = window.REVIEW_WORDS || {{}})["{stem}"] = '
                               + json.dumps(entries, separators=(",", ":")) + ";\n", encoding="utf-8")
        word_files.append(wf)
        print(f"{f}: {exact}/{plain} words matched, {approx} more placed between neighbours, "
              f"{plain - exact - approx} without position ({len(ocr)} OCR words); {lines}")
    idx["wordFiles"] = word_files
    index.write_text("window.REVIEW = " + json.dumps(idx, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


if __name__ == "__main__":
    main()
