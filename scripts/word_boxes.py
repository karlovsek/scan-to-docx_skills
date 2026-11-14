#!/usr/bin/env python3
"""Link transcript words to their position on the scan, using the PDF's own OCR text layer.

Usage: python3 word_boxes.py <source.pdf> <name>_review_data.js

Reads word boxes with `pdftotext -bbox` (Poppler), aligns them with the transcript in the
page files, writes <stem>.words.js next to each page file and sets "wordFiles" in the index.
The editor then highlights a clicked word on the scan. Needs a PDF with a text layer
(Adobe Scan, most phone scanner apps); without one it reports 0 words and changes nothing.
"""
import html, json, re, subprocess, sys, unicodedata
from difflib import SequenceMatcher
from pathlib import Path

MIN_SIM = 0.6   # minimum similarity for two words to count as the same word
MERGE_SIM = 0.8   # stricter minimum when words are joined (split words, missing spaces)
BAND = 80       # alignment only looks this many words off the diagonal
PAD = 0.003     # padding around each box, in page fractions


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


FOLD = str.maketrans({"đ": "d", "ð": "d", "ł": "l", "ø": "o", "ı": "i", "0": "o", "1": "l"})


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
                    if j + k <= m:
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


def padded(b):
    x0, y0, x1, y1 = b
    x0, y0, x1, y1 = max(0, x0 - PAD), max(0, y0 - PAD), min(1, x1 + PAD), min(1, y1 + PAD)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def same_line(a, b):
    ha, hb = a[3] - a[1], b[3] - b[1]
    return abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < max(ha, hb) / 2


def page_entries(page, ocr):
    toks = transcript_tokens(page)
    pairs = align([norm(t) for _, t in toks], [norm(w["text"]) for w in ocr])
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


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pdf, index = sys.argv[1], Path(sys.argv[2])
    base = index.parent
    idx = load_js(index)
    if "pageFiles" not in idx:
        sys.exit("Index has no pageFiles (old single-file format); not supported.")
    ocr_pages = pdf_words(pdf)
    if not any(ocr_pages):
        print("The PDF has no text layer: no word positions available. Nothing changed.")
        return
    word_files = []
    for i, f in enumerate(idx["pageFiles"]):
        page = load_js(base / f)
        pno = page.get("page") or i + 1
        ocr = ocr_pages[pno - 1] if pno <= len(ocr_pages) else []
        entries, plain, exact, approx = page_entries(page, ocr)
        stem = Path(f).stem
        wf = re.sub(r"\.js$", ".words.js", f, flags=re.I)
        (base / wf).write_text(f'(window.REVIEW_WORDS = window.REVIEW_WORDS || {{}})["{stem}"] = '
                               + json.dumps(entries, separators=(",", ":")) + ";\n", encoding="utf-8")
        word_files.append(wf)
        print(f"{f}: {exact}/{plain} words matched, {approx} more placed between neighbours, "
              f"{plain - exact - approx} without position ({len(ocr)} OCR words)")
    idx["wordFiles"] = word_files
    index.write_text("window.REVIEW = " + json.dumps(idx, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


if __name__ == "__main__":
    main()
