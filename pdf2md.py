"""Batch-convert all PDF papers under a folder (recursively) into token-lean Markdown for AI reading.

Output (in <folder>/_md/):
  papers/<category>__<name>.md   one cleaned file per paper (re-used on later runs if the PDF is unchanged)
  ALL_PAPERS.md                  every paper concatenated, one "# [Pxx] Title" heading per paper
  INDEX.md                       table of papers with page counts and token estimates

Cleaning: page headers/footers removed, figures dropped (captions kept), display formulas
re-extracted as plain text, reference list removed, markdown emphasis stripped, whitespace collapsed.

Usage:
  python pdf2md.py                  # convert new/changed PDFs, rebuild ALL_PAPERS.md and INDEX.md
  python pdf2md.py D:/papers        # use another folder (default: the folder containing this script)
  python pdf2md.py --force          # reconvert everything
  python pdf2md.py --include-books  # also convert folders whose name contains "Reference_Books"
  python pdf2md.py --drop-appendix  # also drop appendices that follow the reference list
"""

import argparse
import re
import sys
import time
from pathlib import Path

import pymupdf
import pymupdf4llm

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_md"
PAPERS = OUT / "papers"
BOOK_DIR_MARK = "Reference_Books"

SKIP_CLASSES = {"page-header", "page-footer"}
CAPTION_RE = re.compile(r"^\s*(Fig\.?|Figure|FIGURE|FIG\.?|Table|TABLE)\s*[0-9IVX]+", re.I)
REF_HEADING_RE = re.compile(
    r"^\s*(#{1,6}\s*)?(\*\*)?\s*([0-9IVX]+\.?\s+)?(\*\*)?\s*"
    r"(references( and notes)?|bibliography|literature cited|works cited|参考文献)\s*(\*\*)?\s*:?\s*$",
    re.I,
)
APPENDIX_RE = re.compile(r"appendi|supplementa|^#{1,6}\s*[A-H](\.\d*)?\s", re.I)
ACK_HEADING_RE = re.compile(r"^#{1,6}\s*(\*\*)?\s*([0-9IVX]+\.?\s+)?acknowledge?ments?\b", re.I)
EMAIL_LINE_RE = re.compile(r"^[\s\w.,{}*|+-]*@[\w.-]+\.\w+[\s,;]*$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # unmapped math-font glyphs
EQ_NUMBER_ONLY_RE = re.compile(r"^Eq: \(\w+\)$", re.M)


# ---------------------------------------------------------------- conversion

def caption_from_picture(text):
    """Keep only figure/table captions from a picture block's extracted text."""
    segs = [s.strip() for s in re.split(r"<br>|\n", text.replace("<!-- Start of picture text -->", "")
                                       .replace("<!-- End of picture text -->", "")) if s.strip()]
    out, keep = [], 0
    for s in segs:
        if CAPTION_RE.match(s):
            out.append(s)
            keep = 4  # captions can wrap over a few segments
        elif keep and out:
            out[-1] += " " + s
            keep -= 1
        if out and out[-1].endswith(".") and keep:
            keep = 0
    return "\n\n".join(out)


def formula_text(page, bbox):
    raw = page.get_text("text", clip=pymupdf.Rect(bbox))
    raw = " ".join(raw.split())
    return f"Eq: {raw}" if raw else ""


def convert_pdf(pdf_path):
    """Return (markdown, n_pages, raw_chars) for one PDF, before text cleaning."""
    doc = pymupdf.open(pdf_path)
    chunks = pymupdf4llm.to_markdown(
        doc, page_chunks=True, header=False, footer=False,
        use_ocr=False, write_images=False, show_progress=False,
    )
    pages_out, raw_chars = [], 0
    for i, chunk in enumerate(chunks):
        text = chunk["text"]
        raw_chars += len(text)
        page = doc[chunk.get("metadata", {}).get("page_number", i + 1) - 1]
        parts = []
        for box in chunk.get("page_boxes", []):
            cls = box["class"]
            start, end = box["pos"]
            piece = text[start:end]
            if cls in SKIP_CLASSES:
                continue
            if cls == "picture":
                piece = caption_from_picture(piece)
            elif cls == "formula":
                piece = formula_text(page, box["bbox"])
            if piece.strip():
                parts.append(piece.strip())
        pages_out.append("\n\n".join(parts))
    n_pages = len(doc)
    meta_title = (doc.metadata or {}).get("title", "")
    doc.close()
    return "\n\n".join(pages_out), n_pages, raw_chars, meta_title


# ---------------------------------------------------------------- cleaning

def remove_references(md, drop_appendix):
    lines = md.split("\n")
    hits = [i for i, l in enumerate(lines) if REF_HEADING_RE.match(l) and i > len(lines) * 0.3]
    if not hits:
        return md, False
    start = hits[-1]
    end = len(lines)
    if not drop_appendix:
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("#") and APPENDIX_RE.search(lines[j]):
                end = j
                break
    return "\n".join(lines[:start] + lines[end:]), True


def remove_acknowledgements(md):
    lines = md.split("\n")
    out, skipping = [], False
    for line in lines:
        if ACK_HEADING_RE.match(line):
            skipping = True
            continue
        if skipping and line.startswith("#"):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


def strip_markup(md):
    md = CONTROL_CHARS_RE.sub("", md)
    md = md.replace("**", "")
    for _ in range(2):  # nested italics like _a_<sup>_b_</sup>
        md = re.sub(r"(?<![\w\\])_([^_\n]+?)_(?!\w)", r"\1", md)
    md = re.sub(r"<sup>\s*(.*?)\s*</sup>", lambda m: "^" + (m[1] if len(m[1]) <= 1 else f"({m[1]})"), md)
    md = re.sub(r"<sub>\s*(.*?)\s*</sub>", lambda m: "_" + (m[1] if len(m[1]) <= 1 else f"({m[1]})"), md)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)              # leftover image links
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)             # html comments
    return md


def clean_whitespace(md):
    md = re.sub(r"([a-z])-\n\s*([a-z])", r"\1\2", md)          # de-hyphenate across lines
    lines = []
    for line in md.split("\n"):
        line = line.rstrip()
        if not line.startswith("|"):
            line = re.sub(r"(?<=\S) {2,}", " ", line)
        if EMAIL_LINE_RE.match(line) or re.fullmatch(r"\s*\d{1,4}\s*", line):
            continue                                            # bare emails / page numbers
        lines.append(line)
    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def guess_title(md, meta_title, fallback):
    """PDF metadata title if plausible, else first non-section heading, else the file name."""
    meta_title = " ".join(meta_title.split())
    if 15 < len(meta_title) < 250 and " " in meta_title and not re.search(
            r"untitled|microsoft|\.(pdf|docx?|dvi|tex)$|^doi|^arxiv", meta_title, re.I):
        return meta_title
    for line in md.split("\n")[:40]:
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if 15 < len(title) < 250 and not re.match(
                    r"([0-9IVX]+(\.\d+)*\.?\s)|abstract|introduction|contents|supplementa", title, re.I):
                return title
    return fallback


# ---------------------------------------------------------------- driver

def out_name(pdf):
    rel = pdf.relative_to(ROOT)
    category = rel.parts[0][:2] if len(rel.parts) > 1 and rel.parts[0][:2].isdigit() else "00"
    stem = re.sub(r"[^\w.-]+", "_", pdf.stem)[:120]
    return f"{category}__{stem}.md", str(rel.parent) if len(rel.parts) > 1 else "(root)"


def read_meta(md_path):
    """Read the small metadata block written at the top of each per-paper file."""
    head = md_path.read_text(encoding="utf-8").split("\n", 8)
    meta = {}
    for line in head:
        m = re.match(r"^> (\w[\w ]*): (.*)$", line)
        if m:
            meta[m[1]] = m[2]
    return meta


def main():
    global ROOT, OUT, PAPERS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=str(ROOT), help="folder to scan for PDFs (default: script folder)")
    ap.add_argument("--force", action="store_true", help="reconvert even if the .md is up to date")
    ap.add_argument("--include-books", action="store_true", help=f"also convert folders containing '{BOOK_DIR_MARK}'")
    ap.add_argument("--drop-appendix", action="store_true", help="drop appendices after the reference list")
    args = ap.parse_args()
    ROOT = Path(args.folder).resolve()
    OUT = ROOT / "_md"
    PAPERS = OUT / "papers"

    PAPERS.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(p for p in ROOT.rglob("*.pdf")
                  if OUT not in p.parents and (args.include_books or BOOK_DIR_MARK not in str(p)))
    print(f"Found {len(pdfs)} PDFs\n")

    entries, used_names, failures = [], set(), []
    for n, pdf in enumerate(pdfs, 1):
        name, folder = out_name(pdf)
        base = name
        k = 2
        while name in used_names:
            name = base.replace(".md", f"_{k}.md")
            k += 1
        used_names.add(name)
        md_path = PAPERS / name
        tag = f"[{n}/{len(pdfs)}] {pdf.relative_to(ROOT)}"

        if md_path.exists() and not args.force and md_path.stat().st_mtime >= pdf.stat().st_mtime:
            print(f"{tag}  (up to date)")
            entries.append((md_path, read_meta(md_path)))
            continue

        t0 = time.time()
        try:
            md, n_pages, raw_chars, meta_title = convert_pdf(pdf)
        except Exception as e:  # keep going on broken PDFs
            print(f"{tag}  FAILED: {e}")
            failures.append((pdf, str(e)))
            continue
        md, refs_removed = remove_references(md, args.drop_appendix)
        md = clean_whitespace(strip_markup(remove_acknowledgements(md)))
        title = guess_title(md, meta_title, pdf.stem)

        warn = []
        if len(md) < 300 * n_pages * 0.2:
            warn.append("very little text: scanned PDF? needs OCR")
        if not refs_removed:
            warn.append("reference heading not found")
        n_eq = md.count("\nEq: ")
        if n_eq >= 5 and len(EQ_NUMBER_ONLY_RE.findall(md)) > n_eq / 2:
            warn.append("formulas are embedded as images, see PDF")
        meta = {
            "Title": title, "Source": str(pdf.relative_to(ROOT)).replace("\\", "/"), "Folder": folder,
            "Pages": str(n_pages), "Tokens": str(len(md) // 4), "RawTokens": str(raw_chars // 4),
            "Warnings": "; ".join(warn) or "-",
        }
        header = "\n".join(f"> {k}: {v}" for k, v in meta.items())
        md_path.write_text(f"{header}\n\n{md}", encoding="utf-8")
        entries.append((md_path, meta))
        print(f"{tag}  {n_pages}p  ~{int(meta['RawTokens']):,} -> ~{int(meta['Tokens']):,} tokens"
              f"  {time.time() - t0:.1f}s" + (f"  ! {meta['Warnings']}" if warn else ""))

    # remove stale per-paper files whose PDF no longer exists
    for f in PAPERS.glob("*.md"):
        if f.name not in used_names:
            f.unlink()

    # combined file: one top-level heading per paper, paper's own headings demoted by one level
    combined = ["# All papers\n\nEach paper starts with a line `# [Pxx] Title`; "
                "references, figures and page headers/footers are removed. Formulas are plain text marked `Eq:`.\n"]
    index = ["# Paper index\n", "| ID | Title | Folder | Pages | Tokens | Warnings |", "|---|---|---|---|---|---|"]
    total = 0
    for i, (md_path, meta) in enumerate(entries, 1):
        pid = f"P{i:02d}"
        body = md_path.read_text(encoding="utf-8").split("\n\n", 1)[1]
        body = re.sub(r"^(#{1,5}) ", r"#\1 ", body, flags=re.M)
        combined.append(f"# [{pid}] {meta.get('Title', md_path.stem)}\n\n"
                        f"> Source: {meta.get('Source', '?')}\n\n{body}")
        tokens = int(meta.get("Tokens", 0))
        total += tokens
        index.append(f"| {pid} | {meta.get('Title', '?')} | {meta.get('Folder', '?')} | {meta.get('Pages', '?')} "
                     f"| {tokens:,} | {meta.get('Warnings', '-')} |")
    index.append(f"\nTotal: {len(entries)} papers, ~{total:,} tokens (estimated as characters / 4).")
    (OUT / "ALL_PAPERS.md").write_text("\n\n".join(combined), encoding="utf-8")
    (OUT / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    print(f"\nDone: {len(entries)} papers, ~{total:,} tokens -> {OUT / 'ALL_PAPERS.md'}")
    if failures:
        print(f"{len(failures)} failed:")
        for pdf, err in failures:
            print(f"  {pdf.relative_to(ROOT)}: {err}")


if __name__ == "__main__":
    main()
