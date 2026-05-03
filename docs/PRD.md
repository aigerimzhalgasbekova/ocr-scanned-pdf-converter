# PRD: ocr-scanned-pdf-converter

## 1. Overview

**Product:** `ocr-scanned-pdf-converter` (package: `ocr_ptr_pdf_converter`, CLI: `ocr-ptr-convert`)

**Summary:** A Python CLI that converts scanned U.S. House Periodic Transaction Report (PTR) PDFs into structured Markdown tables, using OCR plus OpenCV-based table-grid detection.

**Problem:** PTR filings are published as low-quality scanned PDFs with a fixed table layout. The data inside is structured (per-row transactions across fixed columns) but is not machine-readable without OCR. Naive OCR collapses the cells into noise on rotated, grid-heavy scans. There is no off-the-shelf tool that turns a PTR PDF into a clean, parseable table.

**Target audience:**

- The project owner, who needs to extract transaction data from PTR scans for downstream analysis.
- Any technical user (researcher, journalist, developer) running the CLI on a PTR-family scanned PDF.

**Reference form:** [House Ethics CY 2025 PTR Form](https://ethics.house.gov/wp-content/uploads/2026/02/Final-CY-2025-PTR-Form-1.pdf). The valid holder codes per the official form are `JT`, `SP`, `DC`. Amount codes are `A` through `K`.

## 2. Architecture

### 2.1 Pipeline (per-page)

```
+-------------+     +-------------+     +-----------------+     +------------------+
|  PDF page   | --> |  Render to  | --> |  Auto-orient    | --> |  Preprocess      |
|             |     |  300dpi PNG |     |  (rotate 0/90/  |     |  (grayscale,     |
|             |     |  (pdf2image)|     |  180/270, score)|     |   denoise, bin)  |
+-------------+     +-------------+     +-----------------+     +------------------+
                                                                          |
                                                                          v
                                              +-------------------------------------------+
                                              |  Detect table grid (OpenCV)               |
                                              |  - horizontal & vertical line masks       |
                                              |  - intersect to find cell rectangles      |
                                              |  - cluster into rows × columns            |
                                              +-------------------------------------------+
                                                                          |
                                                                          v
                                              +-------------------------------------------+
                                              |  OCR each cell (tesseract)                |
                                              |  - per-cell PSM tuned to content type     |
                                              |    (text vs. single mark vs. date)        |
                                              +-------------------------------------------+
                                                                          |
                                                                          v
                                              +-------------------------------------------+
                                              |  Map cells -> logical row schema          |
                                              |  (Holder, Asset, Tx type, Date tx,        |
                                              |   Date notified, Amount code)             |
                                              |  - normalize Purchase/Sale/Exchange       |
                                              |    columns into single Transaction type   |
                                              |  - merge wrapped asset cells              |
                                              |  - detect section-header rows             |
                                              +-------------------------------------------+
                                                                          |
                                                                          v
                                              +-------------------------------------------+
                                              |  Render Markdown table per page           |
                                              +-------------------------------------------+
```

### 2.2 Document-level flow

1. Render all pages.
2. Detect document-level `Date notified` once (form header field) and reuse for every row.
3. Run per-page pipeline.
4. Concatenate page tables under a shared header (Amount code legend) into one Markdown file written to `output/<input-stem>.md`.

### 2.3 Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.14 |
| Package mgr | uv (PEP 621 `pyproject.toml`, hatchling backend) |
| PDF rendering | `pdf2image` + system `poppler` |
| OCR | `pytesseract` + system `tesseract` |
| Table-grid detection | `opencv-python` |
| Numerical helpers | `numpy` |
| Tabular data | `pandas` (already in deps; used for OCR token frames) |
| Image handling | `Pillow` |
| Linting | `ruff` (line length 88, py314 target) |
| Type checking | `mypy` (strict) |
| Tests | `pytest` + `pytest-cov` |
| Hooks | `pre-commit`, `commitizen` (conventional commits) |
| Distribution | hatch wheel from `src/ocr_ptr_pdf_converter` |

### 2.4 Project layout

```
ocr-scanned-pdf-converter/
├── docs/
│   └── PRD.md
├── input-pdf/                 # gitignored sample inputs
│   └── 9115728.pdf
├── output/                    # gitignored CLI output dir
├── src/ocr_ptr_pdf_converter/
│   ├── __init__.py            # __version__
│   ├── cli.py                 # arg parsing, orchestration, file I/O
│   ├── render.py              # PDF → page images
│   ├── orient.py              # rotation detection
│   ├── preprocess.py          # grayscale, denoise, binarize
│   ├── grid.py                # OpenCV cell-rectangle detection
│   ├── ocr.py                 # cell-level OCR
│   ├── schema.py              # Holder/Tx/Amount enums, row dataclass
│   ├── extract.py             # cell-grid → logical rows
│   ├── markdown.py            # row[] → markdown
│   └── header.py              # document-level Date notified
├── tests/
│   ├── fixtures/
│   │   ├── 9115728.pdf
│   │   └── 9115728_expected.md
│   ├── test_cli.py
│   ├── test_grid.py
│   ├── test_orient.py
│   ├── test_extract.py
│   ├── test_markdown.py
│   └── test_golden.py         # integration test
├── main.py
├── pyproject.toml
├── .pre-commit-config.yaml
└── README.md
```

## 3. User Stories

### US-1: Convert a single PTR PDF
**As** a researcher, **I want** to run one CLI command on a PTR PDF, **so that** I get a structured Markdown table I can grep, diff, or import into a spreadsheet.

Acceptance criteria:
- `uv run ocr-ptr-convert input-pdf/9115728.pdf` produces `output/9115728.md` (path = `output/<input-stem>.md`).
- Output Markdown matches the schema in §6.1.
- ≥95% of data rows in `output/9115728.md` exactly match the corresponding rows in `tests/fixtures/9115728_expected.md` on the five critical fields (Holder, Asset, Transaction type, Date of transaction, Amount code) — measured row-level (a row is correct only if all five match).
- The document-level `Date notified` value detected from the form header is rendered once at the top of the file (`**Date notified:** <date>`). Detection accuracy on the fixture must be exact.

### US-2: Override output path
**As** a user with a custom workflow, **I want** to specify an output path with `-o`, **so that** I can integrate the tool into pipelines that expect specific filenames.

Acceptance criteria:
- `-o <path>` writes to that path verbatim and bypasses the default `output/` directory.
- Parent directory is created if it does not exist.

### US-3: Tune render quality
**As** a power user dealing with a borderline scan, **I want** to set DPI, **so that** I can trade speed for OCR accuracy.

Acceptance criteria:
- `--dpi N` controls `pdf2image` rendering. Default `300`. Allowed range `150`–`600`.

### US-4: Verbose / debug output
**As** a developer triaging a misparse, **I want** to see what each pipeline stage decided, **so that** I can locate the failing stage.

Acceptance criteria:
- `-v/--verbose` prints per-page progress: rotation, detected row count, detected column count, header date.
- `--debug DIR` (optional flag) dumps intermediate artifacts to `DIR/`: rotated page PNG, binarized PNG, grid-overlay PNG (cells drawn over original), and a JSON of per-cell OCR text.

### US-5: Restrict pages
**As** a user with a multi-page PTR where one page is misaligned, **I want** to convert only specific pages, **so that** I can re-run after fixing input or skip junk pages.

Acceptance criteria:
- `--pages 1-3` or `--pages 1,3,5` selects pages (1-indexed).
- Output Markdown contains only the selected pages.

### US-6: Section headers preserved
**As** a downstream consumer, **I want** trust/account section headers preserved as table rows with only the Asset cell filled, **so that** I can keep all rows in a single parseable table while still seeing structural context.

Acceptance criteria:
- A row whose only non-empty value is the asset/section name (e.g. `LINDA MAYS MCCAUL 1999 EXEMPT TRUST`) is emitted with empty Holder, Transaction type, Date of transaction, Amount code, and the document-level Date notified left **blank** (not filled).
- Best-effort: section detection is not part of the ≥95% accuracy bar; misclassified section rows do not fail the golden test as long as the data-row bar holds.

## 4. Features

### 4.1 OCR + table-grid detection (v1, core)
- Auto-rotation: render at the chosen DPI, OCR a quick pass at 0/90/180/270°, score by presence of expected header tokens (`PURCHASE`, `SALE`, `EXCHANGE`, `AMOUNT`, `DATE`, `FULL ASSET NAME`) plus date-regex matches; pick highest score.
- Preprocessing: convert to grayscale, contrast-stretch, median-filter, adaptive-binarize.
- Grid detection: morphological opens with horizontal and vertical kernels to isolate ruling lines; bitwise-and to find intersections; cluster intersections into a row × column lattice; the lattice defines the cell rectangles.
- Cell OCR: crop each rectangle, dilate slightly, run tesseract with PSM tuned to the column type:
  - text columns (Asset, Owner section): `--psm 6`.
  - mark columns (Purchase/Sale/Exchange): `--psm 10` looking for an `X`.
  - date columns: `--psm 7` with date regex post-filter.
  - amount-code columns: `--psm 10` looking for a single A–K letter.
- Row-merge: adjacent grid rows whose Asset cell continues a wrapped name (no holder, no date, no marks) are merged into the previous row.

### 4.2 Schema normalization (v1, core)
- The reference form has separate `Purchase`, `Sale`, `Exchange` columns each holding an X. The output collapses these into a single `Transaction type` column with values `Purchase | Sale | Exchange | ""`.
- All pages emit the same schema regardless of source-page layout variations.

### 4.3 Document-level header detection (v1, core)
- Detect `Date notified` once from the form header (top of page 1) and apply to every data row.
- If detection fails, leave `Date notified` empty and warn in verbose mode.

### 4.4 CLI (v1, core)
```
ocr-ptr-convert <input.pdf> [-o OUTPUT] [--dpi N] [--pages SPEC] [-v] [--debug DIR]
```

### 4.5 Future (v2+, out of v1 scope)
- Batch mode (directory of PDFs).
- CSV / JSON output.
- Confidence scores per cell in output.
- Web UI / API wrapper.
- Native (non-OCR) PDF text extraction fast path when the PDF is already digital.

## 5. Data Model

### 5.1 `TransactionRow` (internal dataclass)

| Field | Type | Description |
|---|---|---|
| `holder` | `Literal["JT", "SP", "DC", ""]` | Owner code. Empty for section-header rows. |
| `asset` | `str` | Asset description, possibly multi-word. |
| `transaction_type` | `Literal["Purchase", "Sale", "Exchange", ""]` | Normalized from the three source columns. |
| `date_of_transaction` | `str` | `M/D/YYYY` or `MM/DD/YYYY` exactly as OCR'd, validated against `\b\d{1,2}/\d{1,2}/\d{4}\b`. Empty for section-header rows. |
| `amount_code` | `Literal["A", "B", ..., "K", ""]` | Empty for section-header rows. |
| `is_section_header` | `bool` | `True` when only `asset` is set; not rendered to Markdown but used internally. |

`Date notified` is not a row field — it is a document-level value carried on `Document` (see §5.4).

### 5.2 `PageResult`
| Field | Type | Description |
|---|---|---|
| `page_number` | `int` | 1-indexed. |
| `rotation` | `int` | One of `0/90/180/270`. |
| `rows` | `list[TransactionRow]` | Ordered as in source. |

### 5.3 `Document`
| Field | Type | Description |
|---|---|---|
| `source_filename` | `str` | Input PDF filename (no path). |
| `date_notified` | `str` | Detected once from form header. Empty if detection failed. |
| `pages` | `list[PageResult]` | Ordered. |

### 5.4 Document constants

| Constant | Value |
|---|---|
| `HOLDERS` | `{"JT", "SP", "DC"}` |
| `TX_TYPES` | `("Purchase", "Sale", "Exchange")` |
| `AMOUNT_CODES` | `tuple("ABCDEFGHIJK")` |

## 6. API / Interface Design

### 6.1 Output Markdown format

```markdown
# OCR conversion for <input-filename>

**Date notified:** 4/6/2026

## Amount code legend

| Code | Amount range |
|---|---|
| A | $1,000-$15,000 |
| B | $15,001-$50,000 |
| ... | ... |
| K | Spouse/DC Amount over $1,000,000 |

## Page 1

| Holder | Asset | Transaction type | Date of transaction | Amount code |
|---|---|---|---|---|
| SP | Cedar Holdings LP INV 1292 | Purchase | 3/24/2026 | F |
| ... |

## Page 2

| Holder | Asset | Transaction type | Date of transaction | Amount code |
|---|---|---|---|---|
| ... |
```

Section-header rows render as:

```markdown
|  | LINDA MAYS MCCAUL 1999 EXEMPT TRUST |  |  |  |
```

`Date notified` is detected once per document from the form header and rendered at the top — not as a column.

### 6.2 CLI surface

```
ocr-ptr-convert INPUT [OPTIONS]

Positional:
  INPUT                         Path to the scanned PTR PDF.

Options:
  -o, --output PATH             Output Markdown path. Default: output/<input-stem>.md
  --dpi INT                     Rendering DPI (150-600). Default: 300.
  --pages SPEC                  Page selector e.g. "1-3" or "1,3,5". Default: all.
  -v, --verbose                 Per-page progress and decisions.
  --debug DIR                   Dump intermediate artifacts to DIR.
  --version                     Print version and exit.
  -h, --help                    Show help.
```

Exit codes: `0` success, `1` input file missing / unreadable, `2` invalid argument, `3` no recognizable table on any page.

### 6.3 Public Python API (importable)

```python
from ocr_ptr_pdf_converter import convert
md = convert(pdf_path: Path | str, dpi: int = 300, pages: list[int] | None = None) -> str
```

`convert` returns the Markdown string (does not write to disk).

## 7. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Accuracy on `9115728.pdf` | ≥95% data rows correct (row-level, all 5 critical fields exact) against `tests/fixtures/9115728_expected.md`. |
| Runtime | ≤30 s per page on a typical laptop at default DPI 300 (5-page PDF in ≤2.5 min). Soft target. |
| Memory | Peak <2 GB at default DPI. Soft target. |
| Code quality | Ruff clean (`E,F,I,UP,B,C4,SIM`, line 88), mypy strict on `src/`, all pre-commit hooks pass. |
| Test coverage | ≥80% line coverage across `src/ocr_ptr_pdf_converter/` (excluding `cli.py` which is mostly arg plumbing). |
| Determinism | Same input → same output bit-for-bit (no randomness, fixed thresholds). |
| Logging | Use `logging` module; default `WARNING`; `-v` → `INFO`; `--debug` → `DEBUG` and writes artifacts. |
| Observability | `--debug` lets a developer reproduce a misparse offline by inspecting dumped artifacts. |

## 8. Out of Scope (v1)

- Batch processing of a directory of PDFs.
- CSV / JSON / Excel output formats.
- A web UI or HTTP API.
- Digital (non-scanned) PDF fast path.
- OCR of fields outside the transactions table (filer name, signature, page footer beyond `Date notified`).
- Internationalization (PTR forms are English-only by definition).
- Form variants outside the U.S. House PTR family (Senate STR, FD, etc.).
- Continuous re-training or ML model fine-tuning.

## 9. Milestones

### v0.1.0 — Project scaffold (DONE, branch `chore/project-scaffold`)
1. uv-managed package with hatch build.
2. Ruff, mypy, pytest, commitizen, pre-commit configured.
3. Existing `converter.py` moved to package; thin `main.py`; basic unit tests pass.

### v0.2.0 — TDD pipeline build-out
1. Move `9115728.pdf` and a normalized `9115728_expected.md` into `tests/fixtures/`. Delete `draft/`.
2. Add `opencv-python` + `numpy` deps.
3. Red-green-refactor each pipeline module in this order: `schema`, `markdown`, `header`, `orient`, `preprocess`, `grid`, `ocr`, `extract`, `cli`.
4. Build the golden integration test (`test_golden.py`) — initially failing — and iterate the pipeline until ≥95% row-level match.

### v0.3.0 — UX polish
1. `--pages`, `-v`, `--debug` flags.
2. `convert()` public Python API.
3. README updated with examples and accuracy note.

### Future
- Senate / FD / OGE form variants.
- CSV / JSON output.
- Optional PDFMiner fast path for digital PDFs.
- Confidence scoring.

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OCR mis-detects single `X` marks (faded / off-center) | Wrong `Transaction type` and/or `Amount code` → falls below ≥95% bar | Use morphological opening on cell crop to amplify the X; classify as "marked" by ink-density threshold rather than tesseract output for mark cells. |
| Grid lines too faint or broken on some scans | No cell rectangles detected → empty output | Fallback: synthesize column bounds from header-token x-positions (current heuristic), use it as v0.1 fallback when grid detection yields fewer than 4 columns. |
| Asset names wrapping to 2+ lines | Wrapped tail loses parent row → split records | Detect "orphan" rows (no holder, no date, no marks, no amount) and merge into previous data row's Asset. |
| Section-header rows misclassified as data | Junk rows in output | Treat any row missing date AND missing all marks as a section header; section rows excluded from accuracy metric. |
| Different PTR variants ship with different column orders | Tool emits shifted data | v1 targets the U.S. House PTR layout only; document this explicitly; fail fast with exit 3 if header tokens are not detected. |
| Rotated input pages | Garbage OCR | Existing 4-way rotation scoring already in `converter.py`; carry over, expand keyword set. |
| `pdf2image` requires `poppler` system dep | Install friction | README documents `brew`/`apt-get` install lines; CI uses Linux runner with poppler/tesseract preinstalled. |
| Test golden file drift over time | Tests break on innocuous formatting changes | Normalize whitespace and case-insensitive compare on Asset; exact compare on the other 4 critical fields; section rows excluded. |
| Scope creep into other federal disclosure forms | Slips v1 | Out-of-scope section explicitly excludes them. |

---

**Assumptions to flag for review:**

- `Date notified` is treated as a single document-level value (form header field). If on some PTRs it's per-row, this design needs a small rework.
- `output/` is the default output directory and is gitignored. Inputs live in `input-pdf/` (also gitignored).
- The accuracy bar is measured against the *normalized* expected fixture (single Transaction type column on every page), not the raw `9115728_cleaned.md` which has 8-column tables on pages 3–5.
- Section-header detection is best-effort and excluded from the ≥95% bar.
