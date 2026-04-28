# Accuracy Batch 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three accuracy buckets (garbage rows, AMOUNT drift, tx-mark confusion) to raise golden exact-match from 44.3% (43/97) toward ≥95%.

**Architecture:** Three targeted changes: (1) a `_is_garbage` predicate in `extract.py` that silently drops owner-block bleed rows; (2+3) two diagnostic probe scripts that expose ink density for AMOUNT and TX mark columns, followed by a single threshold/margin calibration in `cli.py::_resolve_competing_marks`. All changes are TDD. Each fix is committed separately so `diagnose_golden.py` can measure its individual impact.

**Tech Stack:** Python 3.14, uv, pytest, existing `ocr_ptr_pdf_converter` module stack.

---

## File Structure

**Modified:**
- `src/ocr_ptr_pdf_converter/extract.py` — add `_is_garbage`, call it in `rows_from_cell_texts`
- `src/ocr_ptr_pdf_converter/cli.py` — calibrate `_MARK_WINNER_DENSITY` / add `_MARK_WINNER_MARGIN` in `_resolve_competing_marks`
- `tests/test_extract.py` — add garbage-filter tests
- `tests/test_cli.py` — add `_resolve_competing_marks` unit tests

**Created:**
- `scripts/probe_letter_cells.py` — shows ink density of all AMOUNT columns per row; flags low-margin winners
- `scripts/probe_mark_density.py` — shows ink density of all TX mark columns per row; flags low-margin winners

---

## Task 1: Garbage row filter (`extract.py`)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py`
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_extract.py`:

```python
def test_garbage_row_with_leaked_tx_type_is_dropped():
    """Owner-block rows where OCR leaks a tx-type mark but has no holder/date."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 2006 DESCENDANT TRUST", "Purchase", "", "K"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == []


def test_garbage_row_with_leaked_amount_only_is_dropped():
    """Owner-block rows with a leaked amount code but no holder/date/tx."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "DE FULL", "", "", "A"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == []


def test_asset_only_row_is_not_garbage_becomes_section_header():
    """Asset-only rows (no tx, no amount) are family holder names — must survive as section headers."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 1999 EXEMPT TRUST", "", "", ""]]
    rows = rows_from_cell_texts(cells, roles)
    assert len(rows) == 1
    assert rows[0].is_section_header
    assert rows[0].asset == "LINDA MAYS MCCAUL 1999 EXEMPT TRUST"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
uv run python -m pytest tests/test_extract.py::test_garbage_row_with_leaked_tx_type_is_dropped tests/test_extract.py::test_garbage_row_with_leaked_amount_only_is_dropped tests/test_extract.py::test_asset_only_row_is_not_garbage_becomes_section_header -v
```

Expected: 2 FAIL (garbage rows are not yet dropped), 1 PASS (section header already works).

- [ ] **Step 3: Add `_is_garbage` to `extract.py`**

After the existing `_is_placeholder` function (around line 412), add:

```python
def _is_garbage(row: TransactionRow) -> bool:
    return (
        not row.holder
        and not row.date_of_transaction
        and bool(row.transaction_type or row.amount_code)
    )
```

- [ ] **Step 4: Call `_is_garbage` in `rows_from_cell_texts`**

In `rows_from_cell_texts`, add the call after `_is_placeholder` and before `_is_orphan`. The full function body becomes:

```python
def rows_from_cell_texts(
    cell_rows: list[list[str]], roles: list[ColumnRole]
) -> list[TransactionRow]:
    out: list[TransactionRow] = []
    for texts in cell_rows:
        row = _row_from_cells(texts, roles)
        if _is_empty(row):
            continue
        if _is_placeholder(row):
            continue
        if _is_garbage(row):
            continue
        if _is_orphan(row):
            if out and not out[-1].is_section_header and not _is_orphan(out[-1]):
                prev = out[-1]
                merged = TransactionRow(
                    holder=prev.holder,
                    asset=f"{prev.asset} {row.asset}".strip(),
                    transaction_type=prev.transaction_type,
                    date_of_transaction=prev.date_of_transaction,
                    amount_code=prev.amount_code,
                )
                out[-1] = merged
            else:
                out.append(TransactionRow.section_header(row.asset))
        else:
            out.append(row)
    return out
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
uv run python -m pytest tests/test_extract.py -v
```

Expected: all existing tests + 3 new tests = green.

- [ ] **Step 6: Measure Fix 1 impact**

```bash
uv run python scripts/diagnose_golden.py
```

Expected: `extra_actual_rows` count drops from 12 toward 0. Note new exact-match percentage.

- [ ] **Step 7: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "fix(extract): drop garbage rows (no holder+date but leaked mark/amount)"
```

---

## Task 2: AMOUNT column density probe

**Files:**
- Create: `scripts/probe_letter_cells.py`

This probe reveals why AMOUNT letters drift A→B. The `_resolve_competing_marks` function in `cli.py` picks the highest-density AMOUNT column; when ink bleed in column B exceeds column A's density, B wins. The probe shows all column densities so we know the gap to calibrate.

- [ ] **Step 1: Write `scripts/probe_letter_cells.py`**

```python
"""Probe: show ink density of every AMOUNT-role column per data row.
Flags rows where the winning column's margin over the runner-up is < 0.03
(likely wrong winner). Use output to calibrate _MARK_WINNER_DENSITY /
_MARK_WINNER_MARGIN in cli.py.

Usage: uv run python scripts/probe_letter_cells.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytesseract

from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    classify_header,
    infer_roles_by_position,
)
from ocr_ptr_pdf_converter.grid import Grid, detect_grid
from ocr_ptr_pdf_converter.ocr import ink_density
from ocr_ptr_pdf_converter.orient import best_rotation
from ocr_ptr_pdf_converter.preprocess import to_binary
from ocr_ptr_pdf_converter.render import render_pdf

FIXTURE_PDF = Path("tests/fixtures/9115728.pdf")
_AMOUNT_LETTERS = "ABCDEFGHIJK"
_WINNER_THRESHOLD = 0.05
_MIN_COL_PX = 30


def _filter_cols(grid: Grid) -> Grid:
    cols = [(x0, x1) for x0, x1 in grid.cols if (x1 - x0) >= _MIN_COL_PX]
    return Grid(rows=grid.rows, cols=cols)


images = render_pdf(FIXTURE_PDF, dpi=300)

for page_idx, img in enumerate(images, start=1):
    _, oriented = best_rotation(img)
    binary = to_binary(oriented)
    grid = _filter_cols(detect_grid(binary))

    if not grid.rows or not grid.cols:
        continue

    h_y0, h_y1 = grid.rows[0]
    header_texts = [
        pytesseract.image_to_string(
            oriented.crop((x0, h_y0, x1, h_y1)), config="--psm 6"
        ).strip()
        for x0, x1 in grid.cols
    ]
    roles = classify_header(header_texts)
    if sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles)) > 0.5:
        roles = infer_roles_by_position(grid.cols, roles)

    amt_indices = [i for i, r in enumerate(roles) if r is ColumnRole.AMOUNT]
    if not amt_indices:
        continue

    n = len(amt_indices)
    col_labels = "  ".join(f"{_AMOUNT_LETTERS[k]:>6}" for k in range(n))
    print(f"\n=== Page {page_idx} — {n} AMOUNT cols ===")
    print(f"{'row':>3} | {col_labels} | winner  margin")

    for row_idx, (y0, y1) in enumerate(grid.rows[1:], start=1):
        densities: list[float] = []
        for col_idx in amt_indices:
            x0, x1 = grid.cols[col_idx]
            bc = binary[y0:y1, x0:x1]
            densities.append(ink_density(bc))

        best_d = max(densities)
        best_pos = densities.index(best_d)
        winner = _AMOUNT_LETTERS[best_pos] if best_d >= _WINNER_THRESHOLD else ""

        if not winner:
            continue

        sorted_d = sorted(densities, reverse=True)
        margin = sorted_d[0] - sorted_d[1] if len(sorted_d) >= 2 else sorted_d[0]
        flag = "  ***LOW_MARGIN" if margin < 0.03 else ""
        dens_str = "  ".join(f"{d:>6.3f}" for d in densities)
        print(f"{row_idx:>3} | {dens_str} | {winner:>6}  {margin:>6.3f}{flag}")
```

- [ ] **Step 2: Run the probe**

```bash
uv run python scripts/probe_letter_cells.py 2>/dev/null
```

Expected: table of densities per page. Note which rows have `***LOW_MARGIN` flags and whether those are rows where the wrong letter is winning (compare with `output/9115728_actual.md` if available, or run `diagnose_golden.py` to see amount_only_drift cases).

- [ ] **Step 3: Record findings**

Read the probe output and note:
- Typical density of the correct (marked) AMOUNT column: ______
- Typical density of blank (unmarked) AMOUNT columns: ______
- Are LOW_MARGIN rows corresponding to known wrong-letter rows? (yes/no)
- Is there a clean density gap? (e.g., marked ≥ 0.10, blank ≤ 0.04)

These findings drive the fix in Task 4.

---

## Task 3: TX mark column density probe

**Files:**
- Create: `scripts/probe_mark_density.py`

Same mechanism as Task 2 but for PURCHASE/SALE columns. Reveals whether Sale↔Purchase confusion is caused by the wrong TX column winning the density race.

- [ ] **Step 1: Write `scripts/probe_mark_density.py`**

```python
"""Probe: show ink density of every TX mark column (P/S/PS/EX) per data row.
Flags rows where winning column's margin over runner-up is < 0.02.
Use output to calibrate _MARK_WINNER_DENSITY / _MARK_WINNER_MARGIN in cli.py.

Usage: uv run python scripts/probe_mark_density.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytesseract

from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    classify_header,
    infer_roles_by_position,
)
from ocr_ptr_pdf_converter.grid import Grid, detect_grid
from ocr_ptr_pdf_converter.ocr import ink_density
from ocr_ptr_pdf_converter.orient import best_rotation
from ocr_ptr_pdf_converter.preprocess import to_binary
from ocr_ptr_pdf_converter.render import render_pdf

FIXTURE_PDF = Path("tests/fixtures/9115728.pdf")
_TX_ROLES = (
    ColumnRole.PURCHASE,
    ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE,
    ColumnRole.EXCHANGE,
)
_ROLE_LABEL = {
    ColumnRole.PURCHASE: "P",
    ColumnRole.SALE: "S",
    ColumnRole.PARTIAL_SALE: "PS",
    ColumnRole.EXCHANGE: "EX",
}
_WINNER_THRESHOLD = 0.05
_MIN_COL_PX = 30


def _filter_cols(grid: Grid) -> Grid:
    cols = [(x0, x1) for x0, x1 in grid.cols if (x1 - x0) >= _MIN_COL_PX]
    return Grid(rows=grid.rows, cols=cols)


images = render_pdf(FIXTURE_PDF, dpi=300)

for page_idx, img in enumerate(images, start=1):
    _, oriented = best_rotation(img)
    binary = to_binary(oriented)
    grid = _filter_cols(detect_grid(binary))

    if not grid.rows or not grid.cols:
        continue

    h_y0, h_y1 = grid.rows[0]
    header_texts = [
        pytesseract.image_to_string(
            oriented.crop((x0, h_y0, x1, h_y1)), config="--psm 6"
        ).strip()
        for x0, x1 in grid.cols
    ]
    roles = classify_header(header_texts)
    if sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles)) > 0.5:
        roles = infer_roles_by_position(grid.cols, roles)

    tx_pairs = [(i, r) for i, r in enumerate(roles) if r in _TX_ROLES]
    if not tx_pairs:
        continue

    col_labels = "  ".join(f"{_ROLE_LABEL[r]:>6}" for _, r in tx_pairs)
    print(f"\n=== Page {page_idx} — TX cols: {col_labels} ===")
    print(f"{'row':>3} | {col_labels} | winner  margin")

    for row_idx, (y0, y1) in enumerate(grid.rows[1:], start=1):
        densities: list[float] = []
        for col_idx, _ in tx_pairs:
            x0, x1 = grid.cols[col_idx]
            bc = binary[y0:y1, x0:x1]
            densities.append(ink_density(bc))

        best_d = max(densities)
        best_pos = densities.index(best_d)
        _, best_role = tx_pairs[best_pos]
        winner = _ROLE_LABEL[best_role] if best_d >= _WINNER_THRESHOLD else ""

        if not winner:
            continue

        sorted_d = sorted(densities, reverse=True)
        margin = sorted_d[0] - sorted_d[1] if len(sorted_d) >= 2 else sorted_d[0]
        flag = "  ***LOW_MARGIN" if margin < 0.02 else ""
        dens_str = "  ".join(f"{d:>6.3f}" for d in densities)
        print(f"{row_idx:>3} | {dens_str} | {winner:>6}  {margin:>6.3f}{flag}")
```

- [ ] **Step 2: Run the probe**

```bash
uv run python scripts/probe_mark_density.py 2>/dev/null
```

Expected: table of P/S/PS/EX densities per page. Focus on pages 3–5 (split-mark layout). Note LOW_MARGIN rows and whether those correspond to the 6 tx_only_drift cases.

- [ ] **Step 3: Record findings**

Read the probe output and note:
- Typical density of a correctly-marked tx column: ______
- Typical density of blank tx columns: ______
- Are LOW_MARGIN rows in the same pages/rows as the known tx_only_drift cases? (yes/no)
- What threshold cleanly separates marked from blank? ______

Combine with Task 2 findings to decide which variant to apply in Task 4.

---

## Task 4: Apply density fix (`cli.py`)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/cli.py`
- Test: `tests/test_cli.py`

**Before starting:** review the probe findings from Tasks 2 and 3, then choose:
- **Variant A** (raise threshold only): use when wrong winners have density < 0.10 and correct winners have density ≥ 0.10 — clean bimodal split.
- **Variant B** (add margin requirement): use when correct and incorrect winners sit at similar densities (e.g., 0.07 vs 0.06) — no clean split.
- Apply both if unsure; the margin check is additive and harmless.

- [ ] **Step 1: Write failing tests for `_resolve_competing_marks`**

Add to `tests/test_cli.py`:

```python
from ocr_ptr_pdf_converter.cli import _resolve_competing_marks
from ocr_ptr_pdf_converter.extract import ColumnRole

_TX_MARK_ROLE_SET = frozenset(
    {ColumnRole.PURCHASE, ColumnRole.SALE, ColumnRole.PARTIAL_SALE, ColumnRole.EXCHANGE}
)
_AMT_ROLE_SET = frozenset({ColumnRole.AMOUNT})


def test_resolve_marks_clear_winner():
    """High-density PURCHASE wins; others zeroed."""
    roles = [ColumnRole.PURCHASE, ColumnRole.SALE, ColumnRole.EXCHANGE]
    row_texts = ["", "", ""]
    densities = [0.20, 0.02, 0.01]
    _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
    assert row_texts == ["X", "", ""]


def test_resolve_marks_all_below_threshold_no_winner():
    """Nothing wins when best density is below the threshold."""
    roles = [ColumnRole.PURCHASE, ColumnRole.SALE]
    row_texts = ["", ""]
    densities = [0.03, 0.02]
    _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
    assert row_texts == ["", ""]


def test_resolve_marks_amount_winner_by_position():
    """For AMOUNT columns, highest density wins."""
    roles = [ColumnRole.AMOUNT, ColumnRole.AMOUNT, ColumnRole.AMOUNT]
    row_texts = ["", "", ""]
    densities = [0.03, 0.20, 0.04]
    _resolve_competing_marks(row_texts, densities, roles, _AMT_ROLE_SET)
    assert row_texts == ["", "X", ""]
```

- [ ] **Step 2: Run tests — expect PASS (these test existing behavior)**

```bash
uv run python -m pytest tests/test_cli.py::test_resolve_marks_clear_winner tests/test_cli.py::test_resolve_marks_all_below_threshold_no_winner tests/test_cli.py::test_resolve_marks_amount_winner_by_position -v
```

Expected: all 3 PASS (tests confirm current behavior, which is already correct for clear cases).

- [ ] **Step 3: Write failing test for margin case (Variant B only — skip if using Variant A)**

Add to `tests/test_cli.py`:

```python
def test_resolve_marks_low_margin_no_winner():
    """When winner and runner-up are within _MARK_WINNER_MARGIN, no winner fires."""
    roles = [ColumnRole.PURCHASE, ColumnRole.SALE]
    row_texts = ["", ""]
    # Both above threshold but too close together — after fix, neither should win
    densities = [0.09, 0.08]
    _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
    assert row_texts == ["", ""]
```

Run:

```bash
uv run python -m pytest tests/test_cli.py::test_resolve_marks_low_margin_no_winner -v
```

Expected: FAIL (current code picks the winner at 0.09 regardless of margin).

- [ ] **Step 4: Apply the fix to `cli.py`**

**Variant A only** — raise threshold (use when probe shows clean bimodal split):

In `cli.py`, change:
```python
_MARK_WINNER_DENSITY = 0.05
```
to (use the midpoint between the two probe clusters — default recommendation 0.08 if unsure):
```python
_MARK_WINNER_DENSITY = 0.08
```

**Variant B (recommended if LOW_MARGIN rows exist)** — add margin + optionally raise threshold.

In `cli.py`, add after `_MARK_WINNER_DENSITY`:
```python
_MARK_WINNER_MARGIN = 0.03
```

Replace the entire `_resolve_competing_marks` function:
```python
def _resolve_competing_marks(
    row_texts: list[str],
    densities: list[float],
    roles: list[ColumnRole],
    role_set: frozenset[ColumnRole],
) -> None:
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

- [ ] **Step 5: Run all tests — expect pass**

```bash
uv run python -m pytest tests/test_cli.py -v
```

Expected: all tests including the new `test_resolve_marks_low_margin_no_winner` pass (with Variant B), or all except that test pass (with Variant A only — in that case delete the low-margin test).

- [ ] **Step 6: Run full unit suite**

```bash
uv run python -m pytest tests/ --ignore=tests/test_golden.py -v
```

Expected: all pass.

- [ ] **Step 7: Measure Fix 2+3 impact**

```bash
uv run python scripts/diagnose_golden.py
```

Expected: `amount_only_drift` count drops from 10 and `tx_only_drift` count drops from 6. Note new exact-match percentage.

If accuracy improved but some cases remain: re-examine the probe output and consider tweaking the threshold/margin values incrementally (re-run Step 4 with different constants, then Steps 5–7).

- [ ] **Step 8: Commit**

```bash
git add src/ocr_ptr_pdf_converter/cli.py tests/test_cli.py
git commit -m "fix(cli): calibrate _resolve_competing_marks threshold/margin to fix amount+tx drift"
```

---

## Self-Review

**Spec coverage:**
- Fix 1 garbage filter → Task 1 (predicate, placement, tests) ✓
- Fix 2 AMOUNT density probe → Task 2 (complete probe script) ✓
- Fix 2 AMOUNT fix (conditional) → Task 4 Variant A/B ✓
- Fix 3 TX mark density probe → Task 3 (complete probe script) ✓
- Fix 3 TX mark fix → Task 4 (same `_resolve_competing_marks` change) ✓
- Measure after each fix → Steps 6+7 in Task 1 and Task 4 ✓

**Placeholder scan:** No TBDs. Probe output recording (Tasks 2/3 Step 3) has blank fields intentionally — the developer fills those in from actual probe output. All code blocks are complete.

**Type consistency:** `_is_garbage` uses `row.holder`, `row.date_of_transaction`, `row.transaction_type`, `row.amount_code` — all match `TransactionRow` field names in `schema.py`. `_resolve_competing_marks` signature is unchanged.
