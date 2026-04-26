# Pipeline accuracy fixes — batch 1

**Date:** 2026-04-26
**Branch:** feat/v0.2.0-tdd-pipeline
**Baseline:** 43/97 = 44.3% exact match, 108 actual rows vs 97 expected

## Goal

Improve row-level accuracy on the golden fixture by fixing three buckets:
1. 12 spurious extra rows (garbage filter)
2. 10 LETTER column drift cases (A→B systematic shift)
3. 6 tx_only_drift cases (Sale↔Purchase confusion)

These three fixes address ~28 mismatches and are expected to raise accuracy meaningfully before tackling asset-name kerning and grid row-alignment issues in batch 2.

---

## Fix 1 — Garbage row filter

**File:** `src/ocr_ptr_pdf_converter/extract.py`

**Problem:** 12 extra actual rows with no expected near-match. Examples:
- `('', 'LINDA MAYS MCCAUL 2006 DESCENDANT TRUST', 'Purchase', '', 'K')`
- `('', 'DE FULL', '', '', 'A')`
- `('', 'LLM FAMILY INVESTMENTS LP', 'Purchase', '', 'K')`

These are owner-block names, footer fragments, and OCR noise. They slip through because `_is_empty` only rejects fully-blank rows and `_is_orphan` requires all four structural fields (holder, tx_type, date_tx, amount_code) to be absent.

**Fix:** Add `_is_garbage` predicate:

```
def _is_garbage(row: TransactionRow) -> bool:
    return (
        not row.holder
        and not row.date_of_transaction
        and bool(row.transaction_type or row.amount_code)
    )
```

A row with no holder and no date but with a leaked mark signal (tx_type or amount_code) is structurally useless — these come from owner-block rows where OCR reads marks that bleed from adjacent data columns. Called in `rows_from_cell_texts` before `_is_empty` and `_is_orphan`. Garbage rows are silently dropped.

**What this does NOT change:** Asset-only rows (`transaction_type=''`, `amount_code=''`) are NOT matched by this predicate, so they still flow into `_is_orphan` and get converted to section headers (family holder names like "LINDA MAYS MCCAUL 1999 EXEMPT TRUST"). Legitimate orphan merging is untouched.

**Tests:** Add to `tests/test_extract.py`:
- Row with `holder='', tx_type='Purchase', date_tx='', amount_code='K'` → dropped (leaked mark)
- Row with `holder='', tx_type='', date_tx='', amount_code='A'` → dropped (leaked amount)
- Row with `holder='', tx_type='', date_tx='', amount_code=''`, `asset='LINDA MAYS MCCAUL 1999 EXEMPT TRUST'` → NOT dropped, becomes section header via `_is_orphan`
- Valid row with `holder='SP', asset='...', tx_type='PURCHASE', date_tx='3/24/2026', amount_code='A'` → kept
- Existing tests must all still pass

---

## Fix 2 — LETTER column diagnosis and PSM fix

**Files:** `scripts/probe_letter_cells.py` (new), `src/ocr_ptr_pdf_converter/ocr.py` (conditional)

**Problem:** 10 `amount_only_drift` cases, all A→B with one D→K. Systematic +1 shift strongly suggests a crop-offset issue rather than a random PSM failure. Root cause is unknown until we inspect the crops.

### Step 1 — Probe

Write `scripts/probe_letter_cells.py`:
- Run the full pipeline on `tests/fixtures/9115728.pdf`
- For every LETTER cell, save the PIL crop to `/tmp/letter_cells/<page>_<row>_<col>.png`
- Print a table: `page | row_idx | col_idx | expected | actual | tesseract_raw`
- Expected letters come from matching rows in `tests/fixtures/9115728_expected.md`

This confirms whether the crop images contain the correct cell or are offset by one column.

### Step 2 — Fix (conditional on probe findings)

**If crops are correct but PSM wrong:** Switch `--psm 10` to `--psm 8` in `ocr.py::ocr_cell` for `CellKind.LETTER`. Keep the `tessedit_char_whitelist=ABCDEFGHIJK` restriction. The existing `text in _AMOUNT_CODES` guard means a wrong read returns `""` safely.

**If crops are offset:** Fix the column-index mapping in `cli.py::_process_page` — the LETTER column index passed to `_crop_pil` / `_crop_binary` may be off by one.

**Tests:** Add to `tests/test_ocr.py`:
- Render a small image with a capital "A" and confirm `ocr_cell(..., CellKind.LETTER)` returns `"A"` or `""` (tesseract may miss tiny default-font text, so the test is a guard not a strict assertion)
- Existing `test_ocr_letter_restricts_to_amount_codes` must still pass

---

## Fix 3 — MARK density calibration

**Files:** `scripts/probe_mark_density.py` (new), `src/ocr_ptr_pdf_converter/ocr.py`

**Problem:** 6 `tx_only_drift` cases are Sale↔Purchase swaps. Current `_MARK_DENSITY_THRESHOLD = 0.06` may be mis-classifying faint or bleed ink in the wrong mark column.

### Step 1 — Probe

Write `scripts/probe_mark_density.py`:
- Run the pipeline on `tests/fixtures/9115728.pdf`
- For every row where expected tx_type is known (from golden fixture), extract binary crops for the PURCHASE and SALE columns
- Compute `ink_density()` for each crop
- Print table: `page | row_idx | expected_tx | P_density | S_density | actual_tx`
- Highlight rows where expected_tx != actual_tx

This gives the empirical density distribution for marked vs. blank cells.

### Step 2 — Fix (based on probe findings)

**If bimodal with clean separation:** Update `_MARK_DENSITY_THRESHOLD` to the midpoint between the two clusters (e.g. if blanks are ≤0.03 and marks are ≥0.10, set threshold to 0.06 or raise to 0.07).

**If marked and blank overlap (ink bleed):** Keep threshold as-is but add a "pick louder mark" tie-breaker in `extract.py::_row_from_cells`: if both PURCHASE and SALE fire above threshold, assign the transaction type to whichever column has the higher density. Store raw densities on the way through — this requires passing density values up through `_row_from_cells`, or passing both the binary crop and the already-computed mark result.

**Tests:** Add to `tests/test_ocr.py`:
- `ink_density` on a mostly-dark array returns higher value than on a mostly-white array (ordering test)
- If threshold changes, update any hardcoded threshold references in comments

---

## Sequencing

| Step | What | File(s) | Gate |
|---|---|---|---|
| 1 | Implement garbage filter | `extract.py`, `test_extract.py` | Unit tests pass |
| 2 | Commit Fix 1, re-run diagnose | — | Measure row delta |
| 3 | Write + run letter probe | `scripts/probe_letter_cells.py` | Read findings |
| 4 | Apply Fix 2 based on findings | `ocr.py` or `cli.py`, `test_ocr.py` | Unit tests pass |
| 5 | Commit Fix 2, re-run diagnose | — | Measure row delta |
| 6 | Write + run mark density probe | `scripts/probe_mark_density.py` | Read findings |
| 7 | Apply Fix 3 based on findings | `ocr.py`, `extract.py` (if tie-breaker needed), `test_ocr.py` | Unit tests pass |
| 8 | Commit Fix 3, re-run diagnose | — | Final batch 1 score |

After each commit: `uv run python scripts/diagnose_golden.py` to measure impact.
