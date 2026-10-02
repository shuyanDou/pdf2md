# pdf2md

Batch-convert a folder of academic PDF papers into compact Markdown that an LLM can read with few tokens.

Built on [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm) layout mode, with extra cleaning:

- page headers, footers and page numbers removed
- figures dropped, figure/table captions kept
- display formulas re-extracted as plain text (`Eq: ...`); pymupdf4llm's layout mode drops them by default
- reference list and acknowledgements removed (appendices kept unless `--drop-appendix`)
- bold/italic markup, unmapped math-font control characters and extra whitespace stripped

Typically 10–35% fewer tokens than the plain pymupdf4llm output, without losing the equations.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python pdf2md.py D:/papers                  # scan D:/papers recursively
python pdf2md.py                            # scan the folder that contains pdf2md.py
python pdf2md.py D:/papers --force          # reconvert everything
python pdf2md.py D:/papers --drop-appendix  # also drop appendices after the reference list
python pdf2md.py D:/papers --include-books  # include folders whose name contains "Reference_Books" (skipped by default)
```

Later runs only reconvert PDFs that are new or have changed.

## Output

All output goes to `<folder>/_md/`:

| File | Content |
|---|---|
| `ALL_PAPERS.md` | every paper concatenated; each starts with `# [Pxx] Title` |
| `INDEX.md` | table of papers: title, folder, pages, token estimate, warnings |
| `papers/*.md` | one cleaned file per paper, named `<two-digit folder prefix>__<pdf name>.md` |

For a large collection, give the AI `INDEX.md` first, then point it to individual papers instead of the whole `ALL_PAPERS.md`.

## Limitations

- Formulas come out as plain text, not LaTeX. Use [marker](https://github.com/datalab-to/marker) or [MinerU](https://github.com/opendatalab/MinerU) if you need LaTeX.
- Some older PDFs embed formulas as images. They cannot be extracted, and `INDEX.md` flags these papers.
- No OCR. Scanned PDFs are flagged with "very little text".
- Token counts are estimated as characters / 4.

## License

MIT, see [LICENSE](LICENSE).
