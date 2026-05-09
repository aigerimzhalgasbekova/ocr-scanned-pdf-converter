# Accuracy Batch 4 — Date-Density Section Recovery + Trailing-Digit Trim

**Goal:** Recover real transaction rows that are currently being mis-demoted to section headers, and trim a class of OCR-noise digits left on asset names. Target ~+5-7 rows on the golden test (78.57% → ~84-85%).

**Status:** Brainstormed 2026-05-03 against `tests/fixtures/9115728.pdf`. Single fixture; rule design uses a structural ink-density signal that should generalize, with one empirically-set threshold documented inline.

---

## Problem

`scripts/probe_orphan_merges.py` (added in this batch) shows the orphan-merge path in `rows_from_cell_texts` never fires on the fixture. Every section header is caught earlier by `_is_noisy_section_header` — but that branch is too aggressive and **demotes 5 real rows** to section headers:

| Page | Asset (as OCR'd) | Real classification |
|------|------------------|--------------------|
| 1 | `LOS ANGELES CALIF DEPT ARPTS REV` | row, purchase, 03/16/2026, C |
| 1 | `Mays Allocate LP 7 a` | row, purchase, 03/31/2026, C |
| 1 | `EQT CORP COM - j` | row, purchase, 03/31/2026, B |
| 3 | `CHEVRON CORP.` | row, sale, 03/23/2026, B |
| 4 | `VANGUARD INDEX FUNDS S&P 500 ETF USD` | row, purchase, 03/31/2026, A |

The current rule fires when both `holder` and `date_of_transaction` OCR fail and the asset is long. That's the failure shape for "real row whose holder + date both happened to fail OCR" — exactly the rows above.

### Probe-confirmed dead ends

- **`tx_type set XOR amount set`** is not a discriminator: both genuine section headers and the demoted rows have BOTH set after mark-winner resolution.
- **Holder OCR text** is not a discriminator: `holder_text` is empty (`""`) for ~95% of all rows including the surviving real rows. ROWs survive today only because the SP-default fallback in `_row_from_cells` fires when `date_tx` extracts cleanly.

### The signal that works: date-column ink density

`scripts/probe_orphan_merges.py` dumps per-row date-column density:

```
Real demoted rows  : 0.289, 0.264, 0.271, 0.266, 0.277  (all > 0.25)
Genuine section hdrs: 0.195, 0.141, 0.115, 0.115, 0.113, 0.100, 0.096  (all ≤ 0.20)
```

Clean gap between 0.20 and 0.25. The non-zero "empty cell" baseline (~0.10–0.20) is contributed by table rules + scan noise; a real printed date pushes density to ≥0.25. Threshold **0.22** sits in the gap and perfectly separates the two groups in this fixture. (LLM FAMILY INVESTMENTS LP at 0.195 is the genuine-header value closest to the boundary; still cleanly below 0.22.)

The density is already computed per cell inside `cli.py:_process_page` (the `densities[]` array used for mark-winner resolution). It is currently discarded after that step.

### Orthogonal bug — trailing-digit trim is too permissive

`extract.py:280-288` accepts `prev_upper in _REAL_SHORT_SUFFIXES` as a digit-protection trigger. Since `_REAL_SHORT_SUFFIXES` includes `INC`, `LP`, `CORP`, the protection branch keeps OCR junk like `INTUIT INC 7` and `Mays Allocate 2025 LP 7`. The protection should fire only for the narrow `_NUMERIC_TAIL_ANCHORS` set (`INV`, `COM`, `USD1`).

---

## Design

Two independent changes. Apply Fix 2 (trailing-digit) first — one-line change, isolated test surface. Then Fix 1 (date-density signal), which has more moving parts.

### Fix 1 — Use date-column ink density to gate section-header demotion

**`cli.py:_process_page`**

After the per-row OCR + mark-resolution loop, find the index of the DATE_TX column from `roles` (there is at most one). For each row in `cell_rows`, capture `date_densities[i] = densities_for_row_i[date_tx_idx]`, defaulting to 0.0 when no DATE_TX column exists. Pass `date_densities` as a new positional arg to `rows_from_cell_texts`.

**`extract.py`**

1. Add module-level constant:
   ```python
   # Date-column ink density above this means a printed date is present, even if
   # OCR couldn't extract a date string. Empirically-set: empty date cells (table
   # rules + scan noise) sit at 0.10–0.20 in 9115728.pdf; printed dates ≥ 0.25.
   _DATE_INK_PRESENT_DENSITY = 0.22
   ```

2. Change `_is_noisy_section_header(row)` to `_is_noisy_section_header(row, date_density)`:
   ```python
   def _is_noisy_section_header(row, date_density):
       return (
           not row.holder
           and not row.date_of_transaction
           and date_density < _DATE_INK_PRESENT_DENSITY  # NEW
           and len(row.asset) >= 12
           and bool(row.transaction_type or row.amount_code)
       )
   ```

3. Change `_row_from_cells(texts, roles)` to `_row_from_cells(texts, roles, date_density)`. Relax the SP-default fallback to fire when date ink is present even if the date string didn't extract:
   ```python
   if not holder and asset_parts and tx_type and (
       date_tx or date_density >= _DATE_INK_PRESENT_DENSITY
   ):
       holder = "SP"
   ```

4. Change `rows_from_cell_texts(cell_rows, roles)` to `rows_from_cell_texts(cell_rows, roles, date_densities)`. Iterate with `for texts, date_density in zip(cell_rows, date_densities, strict=True)` and pass `date_density` into both `_row_from_cells` and `_is_noisy_section_header`.

### Fix 2 — Tighten trailing-digit trim

**`extract.py:280-288`**

Replace:
```python
if (
    t.isdigit()
    and len(t) <= 4
    and (
        prev_upper in _REAL_SHORT_SUFFIXES
        or prev_upper in _NUMERIC_TAIL_ANCHORS
    )
):
    break
```

With:
```python
if t.isdigit() and len(t) <= 4 and prev_upper in _NUMERIC_TAIL_ANCHORS:
    break
```

Trims `INTUIT INC 7` → `INTUIT INC` and `Mays Allocate 2025 LP 7` → `Mays Allocate 2025 LP`, while preserving `Cedar Holdings LP INV 1292` (anchor `INV`) and `GENUINE PARTS CO COM USD1 00` (anchor `USD1`).

---

## Tests

**`tests/test_extract.py`** — additions:

1. `_normalize_asset("INTUIT INC 7")` → `"INTUIT INC"` (Fix 2)
2. `_normalize_asset("Mays Allocate 2025 LP 7")` → `"Mays Allocate 2025 LP"` (Fix 2)
3. `_normalize_asset("Cedar Holdings LP INV 1292")` → unchanged (Fix 2 regression guard)
4. `_normalize_asset("GENUINE PARTS CO COM USD1 00")` → unchanged (Fix 2 regression guard)
5. `_is_noisy_section_header(row_shaped_for_old_rule, date_density=0.30)` → False (Fix 1: high date ink overrides)
6. `_is_noisy_section_header(row_shaped_for_old_rule, date_density=0.10)` → True (Fix 1: low date ink → still treated as section header, regression guard for genuine headers)
7. `_row_from_cells(texts_with_no_holder_no_date_string, roles, date_density=0.30)` → returned row has `holder == "SP"` (Fix 1: SP-default fallback fires on date ink)
8. `_row_from_cells(texts_with_no_holder_no_date_string, roles, date_density=0.05)` → returned row has `holder == ""` (Fix 1: regression guard, fallback should not fire on empty date column)
9. End-to-end via `rows_from_cell_texts`: synthetic 3-row scenario where a row matches the old section-header trigger but has high date_density — assert it is preserved as a real row, not demoted (Fix 1 integration)
10. End-to-end via `rows_from_cell_texts`: a genuine section-header row (low date_density, no holder, no date string) — assert it is still classified as section header (Fix 1 regression guard)

No `cli.py` test changes required — caller signature update is purely structural (one extra arg threaded through). Existing `cli.py` tests should still pass.

---

## Verification

1. Re-run `scripts/probe_orphan_merges.py`. Expect each of the 5 demoted rows to flip from `SECTION(noisy)` to `ROW`. Genuine section headers (LLM FAMILY × 2, LINDA MAYS MCCAUL × 5) MUST still be `SECTION(noisy)`.
2. Run golden test: `uv run pytest tests/test_golden.py -v` (~10 min). Expect ~+5-7 row recovery beyond current 78.57%.
3. Diff the markdown output before/after on the fixture. Genuine section headers MUST still appear as section headers.

---

## Risks

**Threshold 0.22 is fixture-tuned.** Other scans could have different empty-cell baselines (darker scans → higher floor; lighter scans → lower printed-date density). With only one fixture available, an empirically-set constant is honest. A future batch can derive the threshold dynamically from per-page baseline (e.g., 25th-percentile date density across rows on the page, plus a margin) once we have a second fixture.

**Vertical bleed into a section-header row's date cell.** A genuine section-header row whose date cell catches ink from a neighbor row's date could cross 0.22 and be misclassified as a real row. After failing the section-header branch, it would fall through to `_is_orphan` (no holder code, no date string, has asset) → orphan path. Probe data shows the genuine section headers in this fixture have date_density ≤ 0.195, well below 0.22, so this risk does not materialize on the fixture. Verification step 3 covers regression detection.

**Trim now strips digits after every company suffix.** If a real asset name legitimately has the form `<COMPANY> INC 7` or `<COMPANY> LP 4`, that digit will be lost. No such asset exists in the fixture; all 5 known instances of trailing digits after INC/LP are OCR noise. Future PTRs introducing one would need a more discriminating condition.

---

## Out of Scope

- Page-3 grid drift investigation — probe data shows page 3 row alignment is correct.
- Amount-column letter margin probe — original analysis hypothesis was based on misread row data; revisit only if these fixes don't recover the relevant rows.
- TX-column SALE recovery on page 3 — same; deferred until post-batch diagnose.
- Hardcoded `_AMOUNT_MARK_COLS = 11` audit — separate batch if any page yields zero rows after this batch.
- Per-page dynamic threshold for `_DATE_INK_PRESENT_DENSITY` — gated on a second fixture.

---

## File Manifest

**Modified:**
- `src/ocr_ptr_pdf_converter/extract.py` — add `_DATE_INK_PRESENT_DENSITY` constant; add `date_density` param to `_is_noisy_section_header` and `_row_from_cells`; add `date_densities` param to `rows_from_cell_texts`; tighten trailing-digit trim in `_normalize_asset`.
- `src/ocr_ptr_pdf_converter/cli.py` — in `_process_page`, capture per-row date-column density and pass to `rows_from_cell_texts`.
- `tests/test_extract.py` — add 10 tests listed above.

**Created (already added during brainstorm):**
- `scripts/probe_orphan_merges.py` — diagnostic probe; kept for future regression triage.

**Untouched:**
- `grid.py`, `ocr.py`, `schema.py`, `markdown.py`, `header.py`, `preprocess.py`, `render.py`, `orient.py`.
