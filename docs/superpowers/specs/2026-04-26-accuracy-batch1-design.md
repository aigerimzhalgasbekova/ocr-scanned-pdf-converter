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

## Fix 2 — LETTER/amount column density calibration

**Files:** `scripts/probe_letter_cells.py` (new), `src/ocr_ptr_pdf_converter/cli.py`

**Problem:** 10 `amount_only_drift` cases, all A→B with one D→K. Root cause: `cli.py::_resolve_competing_marks` picks the highest-ink-density AMOUNT column per row. When ink bleed from the B column slightly exceeds the A column's density, B wins. The tesseract PSM is irrelevant — `_resolve_competing_marks` overwrites every LETTER cell result with "X" or "" based on density, so the final amount letter comes entirely from position (`amount_idx`), not from OCR output.

The current `_MARK_WINNER_DENSITY = 0.05` is too permissive: it allows a near-blank column to win if its bleed density is marginally above 0.05.

### Step 1 — Probe

Write `scripts/probe_letter_cells.py`:
- For each page, detect grid, resolve roles, find AMOUNT column indices
- For each data row, compute `ink_density` on the binary crop of every AMOUNT column
- Determine the current winner (highest density ≥ 0.05) and its position letter (A–K)
- Print: `page | row_idx | densities[A..K] | winner | LOW_MARGIN flag`
- Flag rows where `winner_density - runner_up_density < 0.03` (likely wrong winner)

### Step 2 — Fix (conditional on probe findings)

Both variants edit `_MARK_WINNER_DENSITY` and/or `_resolve_competing_marks` in `cli.py`.

**Variant A — raise threshold only** (use when wrong winners appear with density < 0.08):
```python
_MARK_WINNER_DENSITY = 0.10  # was 0.05
```

**Variant B — add margin requirement** (use when correct and incorrect winners both appear with similar densities):
```python
_MARK_WINNER_MARGIN = 0.03  # winner must beat runner-up by at least this

def _resolve_competing_marks(row_texts, densities, roles, role_set):
    candidates = [(densities[i], i) for i, r in enumerate(roles) if r in role_set]
    if not candidates:
        return
    best_density, best_idx = max(candidates, key=lambda t: t[0])
    if len(candidates) >= 2:
        runner_up = sorted(candidates, reverse=True)[1][0]
        margin_ok = best_density - runner_up >= _MARK_WINNER_MARGIN
    else:
        margin_ok = True
    above_threshold = best_density >= _MARK_WINNER_DENSITY and margin_ok
    for _d, i in candidates:
        row_texts[i] = "X" if (i == best_idx and above_threshold) else ""
```

**Decision rule:** Run the probe, count LOW_MARGIN flagged rows. If most flagged rows have wrong winners with densities 0.05–0.09, raise the threshold (Variant A). If some correct winners also appear at low margin, add the margin requirement (Variant B). Apply both if unsure.

**Tests:** Add to `tests/test_cli.py` (or a new `tests/test_resolve_marks.py`):
- `_resolve_competing_marks` returns winner for clearly dominant cell (density 0.15 vs 0.02)
- `_resolve_competing_marks` returns no winner when all below threshold
- With Variant B: returns no winner when winner and runner-up are within margin

---

## Fix 3 — MARK tx-type density calibration

**Files:** `scripts/probe_mark_density.py` (new), `src/ocr_ptr_pdf_converter/cli.py`

**Problem:** 6 `tx_only_drift` cases are Sale↔Purchase swaps. Same root cause as Fix 2: `_resolve_competing_marks` picks the wrong tx-type mark column when ink bleed from the Sale column's vertical header text slightly exceeds the Purchase column's density, or vice versa.

### Step 1 — Probe

Write `scripts/probe_mark_density.py`:
- For each page, detect grid, resolve roles, find PURCHASE/SALE/PARTIAL_SALE/EXCHANGE column indices
- For each data row, compute `ink_density` on the binary crop of each tx mark column
- Determine current winner and its role name
- Print: `page | row_idx | P_density | S_density | PS_density | EX_density | winner | LOW_MARGIN flag`
- Flag rows where `winner_density - runner_up_density < 0.02`

### Step 2 — Fix (conditional on probe findings)

Same variants as Fix 2, applied to the same `_MARK_WINNER_DENSITY` / `_resolve_competing_marks` in `cli.py`. Fix 2 and Fix 3 share the same mechanism — a single threshold/margin calibration fixes both if the same `_resolve_competing_marks` governs both AMOUNT and TX mark columns (which it does).

**Decision:** Run both probes. If Fix 2's probe already points to a threshold raise that also resolves Fix 3's LOW_MARGIN rows, a single change covers both. If the tx marks need a stricter margin than amount marks, apply Variant B with separate thresholds for each `_resolve_competing_marks` call.

**Tests:** Add to `tests/test_cli.py`:
- `_resolve_competing_marks` for TX roles: correct tx type wins when dominant
- `_resolve_competing_marks` for TX roles: no winner when all below threshold
- (Variant B only) no winner when margin too small between Purchase and Sale columns

---

## Sequencing

| Step | What | File(s) | Gate |
|---|---|---|---|
| 1 | Implement garbage filter | `extract.py`, `test_extract.py` | Unit tests pass |
| 2 | Commit Fix 1, re-run diagnose | — | Measure row delta |
| 3 | Write + run amount density probe | `scripts/probe_letter_cells.py` | Read findings |
| 4 | Write + run tx-mark density probe | `scripts/probe_mark_density.py` | Read findings |
| 5 | Apply Fix 2 + Fix 3 (shared threshold/margin change) | `cli.py`, `test_cli.py` | Unit tests pass |
| 6 | Commit Fix 2+3, re-run diagnose | — | Final batch 1 score |

After each commit: `uv run python scripts/diagnose_golden.py` to measure impact.

**Key insight:** Fixes 2 and 3 share the same mechanism (`_resolve_competing_marks` in `cli.py`) and can be tuned in a single commit after running both probes.
