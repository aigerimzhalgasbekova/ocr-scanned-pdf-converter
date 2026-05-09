# Accuracy Batch 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover ~5-7 real transaction rows currently mis-demoted to section headers, plus trim a class of OCR-noise trailing digits, lifting the golden test from 78.57% toward ~84-85%.

**Architecture:** Two independent fixes in `extract.py` + a wiring change in `cli.py`. Fix 1 threads per-row date-column ink density (already computed in `cli.py:_process_page`) into `rows_from_cell_texts` and uses it as a structural signal to (a) suppress mis-demotion in `_is_noisy_section_header` and (b) relax the SP-default fallback in `_row_from_cells` when a printed date is visually present but failed OCR. Fix 2 is a one-line tightening of the trailing-digit trim guard in `_normalize_asset`.

**Tech Stack:** Python 3.x, uv, pytest. Implementation strictly under `src/ocr_ptr_pdf_converter/` and `tests/`. **All Python commands MUST be invoked via `uv run`.**

**Spec:** `docs/superpowers/specs/2026-05-03-accuracy-batch-4-design.md`
**Branch:** `fix/accuracy-batch-4` (already created and contains the spec + probe).

---

## File Structure

**Modified:**
- `src/ocr_ptr_pdf_converter/extract.py` — add `_DATE_INK_PRESENT_DENSITY` constant; add `date_density` param to `_is_noisy_section_header` and `_row_from_cells`; add `date_densities` param to `rows_from_cell_texts`; tighten the trailing-digit trim branch in `_normalize_asset` (lines 280-288).
- `src/ocr_ptr_pdf_converter/cli.py` — in `_process_page`, capture per-row DATE_TX-column density and pass it as a new positional arg to `rows_from_cell_texts`.
- `tests/test_extract.py` — add 10 tests across Fix 2 trim behaviour and Fix 1 date-density gating.

**Untouched:** `grid.py`, `ocr.py`, `schema.py`, `markdown.py`, `header.py`, `preprocess.py`, `render.py`, `orient.py`, `scripts/probe_orphan_merges.py`.

---

## Task 1: Tighten trailing-digit trim (Fix 2)

Smallest, fully isolated change. Get this committed first so its tests stay green while we refactor signatures in later tasks.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py:280-288`
- Test: `tests/test_extract.py`

- [ ] **Step 1.1: Write four failing tests**

Append to `tests/test_extract.py` (after the existing `test_normalize_asset_keeps_short_numeric_after_usd1` test):

```python
def test_normalize_asset_strips_trailing_digit_after_inc():
    # Trailing single digit after INC is OCR bleed, not a real share class.
    assert _normalize_asset("INTUIT INC 7") == "INTUIT INC"


def test_normalize_asset_strips_trailing_digit_after_lp():
    # Same pattern after LP.
    assert _normalize_asset("Mays Allocate 2025 LP 7") == "Mays Allocate 2025 LP"


def test_normalize_asset_keeps_short_numeric_after_inv_regression():
    # Regression guard for the protection branch — must still fire on INV.
    assert (
        _normalize_asset("CEDAR HOLDINGS LP INV 1292")
        == "CEDAR HOLDINGS LP INV 1292"
    )


def test_normalize_asset_keeps_short_numeric_after_usd1_regression():
    # Regression guard for the protection branch — must still fire on USD1.
    assert (
        _normalize_asset("GENUINE PARTS CO COM USD1 00")
        == "GENUINE PARTS CO COM USD1 00"
    )
```

- [ ] **Step 1.2: Run new tests, confirm the two new ones fail**

Run: `uv run pytest tests/test_extract.py -v -k "strips_trailing_digit_after_inc or strips_trailing_digit_after_lp or keeps_short_numeric_after_inv_regression or keeps_short_numeric_after_usd1_regression"`

Expected:
- `test_normalize_asset_strips_trailing_digit_after_inc` — **FAIL** (current code keeps the `7` because `prev_upper="INC"` is in `_REAL_SHORT_SUFFIXES`)
- `test_normalize_asset_strips_trailing_digit_after_lp` — **FAIL** (same reason, `prev_upper="LP"`)
- `test_normalize_asset_keeps_short_numeric_after_inv_regression` — **PASS** (already protected via `_NUMERIC_TAIL_ANCHORS`)
- `test_normalize_asset_keeps_short_numeric_after_usd1_regression` — **PASS** (same)

- [ ] **Step 1.3: Tighten the trim guard in `_normalize_asset`**

In `src/ocr_ptr_pdf_converter/extract.py`, replace lines 280-288 (the `if (` block that includes `prev_upper in _REAL_SHORT_SUFFIXES or prev_upper in _NUMERIC_TAIL_ANCHORS`):

Old:
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

New:
```python
            if t.isdigit() and len(t) <= 4 and prev_upper in _NUMERIC_TAIL_ANCHORS:
                break
```

- [ ] **Step 1.4: Run all extract tests, confirm all pass**

Run: `uv run pytest tests/test_extract.py -v`

Expected: every test passes, including the four added in Step 1.1 and all pre-existing trim/regression tests (notably `test_normalize_asset_keeps_short_numeric_after_inv` and `test_normalize_asset_keeps_short_numeric_after_usd1`, which exercise the same protection branch through the `_NUMERIC_TAIL_ANCHORS` path).

- [ ] **Step 1.5: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "fix: tighten trailing-digit trim — only protect _NUMERIC_TAIL_ANCHORS"
```

---

## Task 2: Add `_DATE_INK_PRESENT_DENSITY` constant

Tiny, no behaviour change — just stages the constant for later tasks. Keeping it in its own commit makes the threshold easy to find in `git log`.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py` (add module-level constant near other extract-level constants)

- [ ] **Step 2.1: Add the constant**

In `src/ocr_ptr_pdf_converter/extract.py`, immediately after the `_NUMERIC_TAIL_ANCHORS = frozenset({"INV", "COM", "USD1"})` line (currently line 240), add:

```python
# Date-column ink density above this means a printed date is present, even if
# OCR couldn't extract a date string. Empirically-set: empty date cells (table
# rules + scan noise) sit at 0.10–0.20 in 9115728.pdf; printed dates ≥ 0.25.
_DATE_INK_PRESENT_DENSITY = 0.22
```

- [ ] **Step 2.2: Confirm nothing broke**

Run: `uv run pytest tests/test_extract.py -v`

Expected: all tests still pass (the constant is unused).

- [ ] **Step 2.3: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py
git commit -m "chore: add _DATE_INK_PRESENT_DENSITY constant for batch 4"
```

---

## Task 3: Date-density gate in `_is_noisy_section_header` (Fix 1, part A)

Add the new `date_density` parameter to `_is_noisy_section_header` and gate demotion on it. Update the single existing call site in `rows_from_cell_texts` to pass `0.0` (preserves current behaviour). The full-density wiring lands in Task 5.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py` (`_is_noisy_section_header`, `rows_from_cell_texts` call site)
- Test: `tests/test_extract.py`

- [ ] **Step 3.1: Write two failing tests**

Append to `tests/test_extract.py`:

```python
def _row_for_section_header_test(asset: str = "LONG ASSET NAME HERE") -> TransactionRow:
    # A row matching the OLD section-header trigger: no holder, no date,
    # asset >= 12 chars, and a tx_type/amount mark set.
    return TransactionRow(
        holder="",
        asset=asset,
        transaction_type="PURCHASE",
        date_of_transaction="",
        amount_code="C",
    )


def test_is_noisy_section_header_demotes_when_date_ink_low():
    from ocr_ptr_pdf_converter.extract import _is_noisy_section_header

    row = _row_for_section_header_test()
    # Low date-column ink → genuine section header, demote.
    assert _is_noisy_section_header(row, date_density=0.10) is True


def test_is_noisy_section_header_preserves_when_date_ink_high():
    from ocr_ptr_pdf_converter.extract import _is_noisy_section_header

    row = _row_for_section_header_test()
    # High date-column ink (printed date present, OCR just failed) → real row.
    assert _is_noisy_section_header(row, date_density=0.30) is False
```

- [ ] **Step 3.2: Run new tests, confirm they fail**

Run: `uv run pytest tests/test_extract.py -v -k "is_noisy_section_header"`

Expected: both **FAIL** with `TypeError: _is_noisy_section_header() got an unexpected keyword argument 'date_density'` (signature still single-arg).

- [ ] **Step 3.3: Update `_is_noisy_section_header` signature and body**

In `src/ocr_ptr_pdf_converter/extract.py`, replace the entire function (currently lines 474-482):

Old:
```python
def _is_noisy_section_header(row: TransactionRow) -> bool:
    """A long-asset row with no holder and no date, but with OCR bleed in tx_type
    or amount_code from adjacent cells — should be a section header, not garbage."""
    return (
        not row.holder
        and not row.date_of_transaction
        and len(row.asset) >= 12
        and bool(row.transaction_type or row.amount_code)
    )
```

New:
```python
def _is_noisy_section_header(row: TransactionRow, date_density: float) -> bool:
    """A long-asset row with no holder and no date, but with OCR bleed in tx_type
    or amount_code from adjacent cells — should be a section header, not garbage.

    `date_density` is the per-row ink density of the DATE_TX column. When the
    date column has clearly-printed ink (>= _DATE_INK_PRESENT_DENSITY) we treat
    this as a real row whose date OCR failed, not a section header."""
    if date_density >= _DATE_INK_PRESENT_DENSITY:
        return False
    return (
        not row.holder
        and not row.date_of_transaction
        and len(row.asset) >= 12
        and bool(row.transaction_type or row.amount_code)
    )
```

- [ ] **Step 3.4: Update the single existing call site to pass `0.0`**

In `rows_from_cell_texts` (currently line 497), change:

Old:
```python
        if _is_noisy_section_header(row):
            out.append(TransactionRow.section_header(row.asset))
            continue
```

New:
```python
        if _is_noisy_section_header(row, 0.0):
            out.append(TransactionRow.section_header(row.asset))
            continue
```

(This temporarily preserves current production behaviour. Task 5 replaces the `0.0` with the real per-row density.)

- [ ] **Step 3.5: Run all extract tests, confirm all pass**

Run: `uv run pytest tests/test_extract.py -v`

Expected: every test passes, including the two new `is_noisy_section_header` tests.

- [ ] **Step 3.6: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "feat(extract): gate _is_noisy_section_header on date-column ink density"
```

---

## Task 4: Date-density gate in `_row_from_cells` SP-default (Fix 1, part B)

Add the `date_density` parameter to `_row_from_cells` and relax the SP-default fallback so it fires when date ink is visually present even if the date string didn't OCR. Update the single existing call site in `rows_from_cell_texts` to pass `0.0`. The full wiring lands in Task 5.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py` (`_row_from_cells`, `rows_from_cell_texts` call site)
- Test: `tests/test_extract.py`

- [ ] **Step 4.1: Write two failing tests**

Append to `tests/test_extract.py`:

```python
def test_row_from_cells_sp_default_fires_on_date_ink():
    from ocr_ptr_pdf_converter.extract import _row_from_cells

    # Cell layout: [HOLDER, ASSET, TX_TYPE, DATE_TX, AMOUNT]
    # Holder text is empty (OCR failed), date text is empty (OCR failed),
    # but date_density signals the printed date is visually present.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    texts = ["", "ACME CORP COM", "PURCHASE", "", "C"]
    row = _row_from_cells(texts, roles, date_density=0.30)
    assert row.holder == "SP"


def test_row_from_cells_sp_default_skipped_on_empty_date_column():
    from ocr_ptr_pdf_converter.extract import _row_from_cells

    # Same shape but the date column has no ink — the SP-default must NOT
    # invent a holder, since there's no evidence the row is real.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    texts = ["", "ACME CORP COM", "PURCHASE", "", "C"]
    row = _row_from_cells(texts, roles, date_density=0.05)
    assert row.holder == ""
```

(Note: `ColumnRole` is already imported at the top of the test module, line 1-6 — no extra import needed.)

- [ ] **Step 4.2: Run new tests, confirm they fail**

Run: `uv run pytest tests/test_extract.py -v -k "row_from_cells_sp_default"`

Expected: both **FAIL** with `TypeError: _row_from_cells() got an unexpected keyword argument 'date_density'`.

- [ ] **Step 4.3: Update `_row_from_cells` signature and SP-default condition**

In `src/ocr_ptr_pdf_converter/extract.py`:

Change the signature (currently line 363):

Old:
```python
def _row_from_cells(texts: list[str], roles: list[ColumnRole]) -> TransactionRow:
```

New:
```python
def _row_from_cells(
    texts: list[str], roles: list[ColumnRole], date_density: float
) -> TransactionRow:
```

Change the SP-default fallback (currently lines 417-423). Replace the entire `if not holder and (asset_parts and tx_type and date_tx):` block with:

Old:
```python
    if not holder and (asset_parts and tx_type and date_tx):
        # Form's holder column is a sub-checkbox grid (JT/SP/DC). When OCR
        # cannot read the label, default to SP for fully-populated rows —
        # SP is the only holder that appears in the v0.2.0 fixture corpus.
        # We require the row to have asset + tx_type + date so we don't
        # invent holders for noise-only rows.
        holder = "SP"
```

New:
```python
    if not holder and asset_parts and tx_type and (
        date_tx or date_density >= _DATE_INK_PRESENT_DENSITY
    ):
        # Form's holder column is a sub-checkbox grid (JT/SP/DC). When OCR
        # cannot read the label, default to SP for fully-populated rows —
        # SP is the only holder that appears in the v0.2.0 fixture corpus.
        # The row must have asset + tx_type and EITHER a date string OR
        # clearly-printed ink in the date column (so we don't invent holders
        # for noise rows where the date column is also empty).
        holder = "SP"
```

- [ ] **Step 4.4: Update the single call site in `rows_from_cell_texts` to pass `0.0`**

In `rows_from_cell_texts` (currently line 490), change:

Old:
```python
    for texts in cell_rows:
        row = _row_from_cells(texts, roles)
```

New:
```python
    for texts in cell_rows:
        row = _row_from_cells(texts, roles, 0.0)
```

(This preserves current production behaviour: with `date_density=0.0` the new disjunct in the SP-default condition is false, so the gate reduces to the old `date_tx` check. Task 5 replaces the `0.0` with the real density.)

- [ ] **Step 4.5: Run all extract tests, confirm all pass**

Run: `uv run pytest tests/test_extract.py -v`

Expected: every test passes. In particular, all pre-existing `_row_from_cells` consumers exercised through `rows_from_cell_texts` continue to work because `0.0 < _DATE_INK_PRESENT_DENSITY`, leaving the SP-default conditional behaviourally unchanged.

- [ ] **Step 4.6: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "feat(extract): relax SP-default fallback to fire on visible date ink"
```

---

## Task 5: Wire per-row DATE_TX density through `rows_from_cell_texts` to `cli.py` (Fix 1, part C)

Now that both helpers accept `date_density`, expose `date_densities` on `rows_from_cell_texts` and have `cli.py:_process_page` capture it from the data it already computes.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py` (`rows_from_cell_texts` signature + loop)
- Modify: `src/ocr_ptr_pdf_converter/cli.py:_process_page` (capture per-row DATE_TX density and pass to `rows_from_cell_texts`)
- Test: `tests/test_extract.py`

- [ ] **Step 5.1: Write two failing integration tests for `rows_from_cell_texts`**

Append to `tests/test_extract.py`:

```python
def test_rows_from_cell_texts_preserves_real_row_with_date_ink():
    # Row matches OLD section-header trigger (no holder, no date string,
    # long asset, tx_type set) BUT date_density is high → must NOT be demoted.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cell_rows = [["", "VANGUARD INDEX FUNDS S&P 500 ETF USD", "PURCHASE", "", "A"]]
    date_densities = [0.28]
    out = rows_from_cell_texts(cell_rows, roles, date_densities)
    assert len(out) == 1
    assert out[0].is_section_header is False
    assert out[0].asset == "VANGUARD INDEX FUNDS S&P 500 ETF USD"
    assert out[0].holder == "SP"  # SP-default fallback fired on date ink
    assert out[0].transaction_type == "PURCHASE"
    assert out[0].amount_code == "A"


def test_rows_from_cell_texts_demotes_genuine_section_header():
    # Same shape but date_density is low → genuine section header, demote.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cell_rows = [["", "LLM FAMILY INVESTMENTS II LP", "PURCHASE", "", "C"]]
    date_densities = [0.12]
    out = rows_from_cell_texts(cell_rows, roles, date_densities)
    assert len(out) == 1
    assert out[0].is_section_header is True
    assert out[0].asset == "LLM FAMILY INVESTMENTS II LP"
```

- [ ] **Step 5.2: Run new tests, confirm they fail**

Run: `uv run pytest tests/test_extract.py -v -k "rows_from_cell_texts_preserves_real_row_with_date_ink or rows_from_cell_texts_demotes_genuine_section_header"`

Expected: both **FAIL** with `TypeError: rows_from_cell_texts() takes 2 positional arguments but 3 were given`.

- [ ] **Step 5.3: Add `date_densities` parameter to `rows_from_cell_texts`**

In `src/ocr_ptr_pdf_converter/extract.py`, replace the function definition (currently lines 485-517):

Old:
```python
def rows_from_cell_texts(
    cell_rows: list[list[str]], roles: list[ColumnRole]
) -> list[TransactionRow]:
    out: list[TransactionRow] = []
    for texts in cell_rows:
        row = _row_from_cells(texts, roles, 0.0)
        if _is_empty(row):
            ...
        if _is_placeholder(row):
            continue
        if _is_noisy_section_header(row, 0.0):
            out.append(TransactionRow.section_header(row.asset))
            continue
        ...
```

New (full replacement of the function):
```python
def rows_from_cell_texts(
    cell_rows: list[list[str]],
    roles: list[ColumnRole],
    date_densities: list[float],
) -> list[TransactionRow]:
    out: list[TransactionRow] = []
    for texts, date_density in zip(cell_rows, date_densities, strict=True):
        row = _row_from_cells(texts, roles, date_density)
        if _is_empty(row):
            # Wholly blank row — skip so we don't pollute the markdown with
            # empty separator rows that count against over-generation.
            continue
        if _is_placeholder(row):
            continue
        if _is_noisy_section_header(row, date_density):
            out.append(TransactionRow.section_header(row.asset))
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

- [ ] **Step 5.4: Update `cli.py:_process_page` to capture and pass per-row DATE_TX density**

In `src/ocr_ptr_pdf_converter/cli.py`, in `_process_page` (currently lines 217-269), make two surgical changes.

First, just after `roles = _resolve_roles(grid, oriented)` and `col_widths = ...` (currently line 227), introduce a `date_densities` accumulator and find the DATE_TX column index. Replace this block:

Old:
```python
    roles = _resolve_roles(grid, oriented)
    col_widths = [x1 - x0 for x0, x1 in grid.cols]

    cell_rows: list[list[str]] = []
    for y0, y1 in grid.rows[1:]:
```

New:
```python
    roles = _resolve_roles(grid, oriented)
    col_widths = [x1 - x0 for x0, x1 in grid.cols]
    date_tx_idx = next(
        (i for i, r in enumerate(roles) if r is ColumnRole.DATE_TX), None
    )

    cell_rows: list[list[str]] = []
    date_densities: list[float] = []
    for y0, y1 in grid.rows[1:]:
```

Second, after the per-row mark-resolution calls but before `cell_rows.append(row_texts)` (currently line 262), capture the row's DATE_TX density. Replace:

Old:
```python
        # Pick a single tx-type mark winner per row to suppress multi-mark
        # noise (the form's vertical-text headers leak ink into adjacent
        # narrow cells, so several would otherwise all read as marked).
        _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
        # Same for amount: only one A..K cell can be the "real" mark.
        _resolve_competing_marks(
            row_texts, densities, roles, frozenset({ColumnRole.AMOUNT})
        )
        cell_rows.append(row_texts)

    rows = rows_from_cell_texts(cell_rows, roles)
```

New:
```python
        # Pick a single tx-type mark winner per row to suppress multi-mark
        # noise (the form's vertical-text headers leak ink into adjacent
        # narrow cells, so several would otherwise all read as marked).
        _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
        # Same for amount: only one A..K cell can be the "real" mark.
        _resolve_competing_marks(
            row_texts, densities, roles, frozenset({ColumnRole.AMOUNT})
        )
        cell_rows.append(row_texts)
        date_densities.append(
            densities[date_tx_idx] if date_tx_idx is not None else 0.0
        )

    rows = rows_from_cell_texts(cell_rows, roles, date_densities)
```

- [ ] **Step 5.5: Run the full extract test suite, confirm all pass**

Run: `uv run pytest tests/test_extract.py -v`

Expected: every test passes — the 4 added in Task 1, the 2 added in Task 3, the 2 added in Task 4, and the 2 added in Step 5.1, plus all pre-existing tests.

- [ ] **Step 5.6: Run the full non-golden test suite to catch any regressions in `cli.py` consumers**

Run: `uv run pytest --ignore=tests/test_golden.py -v`

Expected: every test passes. (Skipping the golden test here — that's a separate ~10-min step in Task 6.)

- [ ] **Step 5.7: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py src/ocr_ptr_pdf_converter/cli.py tests/test_extract.py
git commit -m "feat(cli): thread per-row DATE_TX ink density into row classification"
```

---

## Task 6: Empirical verification on the fixture

This is a **read-only** verification gate. No code changes. Confirms the fix actually moves the needle on `tests/fixtures/9115728.pdf` and doesn't regress genuine section headers.

**Files:** none modified.

- [ ] **Step 6.1: Re-run the probe and inspect the classifications**

Run: `uv run python scripts/probe_orphan_merges.py | tee /tmp/batch4-probe-after.txt`

Expected (compare against pre-batch behaviour described in the spec):
- The 5 rows enumerated in the spec table (LOS ANGELES CALIF DEPT ARPTS REV, Mays Allocate LP 7 a, EQT CORP COM, CHEVRON CORP., VANGUARD INDEX FUNDS S&P 500 ETF USD) MUST now be tagged `ROW`, not `SECTION(noisy)`. Their `date_dens` values should be ≥ 0.25 (per the spec: 0.289, 0.264, 0.271, 0.266, 0.277).
- The genuine section headers (LLM FAMILY INVESTMENTS rows × 2; LINDA MAYS MCCAUL trust rows × 5) MUST still be tagged `SECTION(noisy)`. Their `date_dens` values should be ≤ 0.20.
- No row that was `ROW` before should now be classified differently.

If any expectation fails, **stop and call advisor()** before proceeding — the threshold or wiring may need revisiting.

- [ ] **Step 6.2: Hand off the golden test to the user (manual, ~10 min)**

The golden test takes roughly 10 minutes and is run **manually by the user in the background** (not by an agent). Do NOT invoke it inline.

Pause here and ask the user to run:

```
uv run pytest tests/test_golden.py -v
```

in a background shell on their side, then paste the resulting accuracy / row-recovery output back into the conversation.

Expected: row-recovery accuracy ≥ ~83-85% (up from current 78.57%). Spec target: +5-7 rows. The golden test may fail outright if it asserts exact row count — that's fine for this verification step; what matters is the reported recovered-row delta.

If accuracy is significantly lower than expected (e.g., flat or worse), **stop and call advisor()** with the probe output and golden output before proceeding.

- [ ] **Step 6.3: (Optional) Diff the markdown output before/after for spot-checking**

If the repo provides a way to render a fixture to markdown directly (e.g., `uv run ocr-ptr-convert tests/fixtures/9115728.pdf -o /tmp/9115728-after.md`), do so and skim it. Confirm the genuine "LLM FAMILY INVESTMENTS" and "LINDA MAYS MCCAUL ... TRUST" rows still appear as section headers in the output, and that the 5 demoted rows now appear as real transaction rows with the expected purchase/sale + date + amount.

This step is optional and read-only; no commit.

- [ ] **Step 6.4: Open a PR**

After the verification above passes:

```bash
git push -u origin fix/accuracy-batch-4
```

Then open a PR titled `fix: accuracy batch 4 — date-density section recovery + trim` against `main`, with the body summarising the recovered-row count, the new threshold constant, and a link to the spec at `docs/superpowers/specs/2026-05-03-accuracy-batch-4-design.md`.

---

## Self-Review

**Spec coverage:**
- Spec §"Fix 1 — Use date-column ink density to gate section-header demotion" → Tasks 2, 3, 4, 5.
- Spec §"Fix 2 — Tighten trailing-digit trim" → Task 1.
- Spec §"Tests" items 1-4 (Fix 2 trim) → Task 1 Step 1.1.
- Spec §"Tests" items 5-6 (Fix 1 `_is_noisy_section_header`) → Task 3 Step 3.1.
- Spec §"Tests" items 7-8 (Fix 1 `_row_from_cells`) → Task 4 Step 4.1.
- Spec §"Tests" items 9-10 (Fix 1 `rows_from_cell_texts` integration) → Task 5 Step 5.1.
- Spec §"Verification" steps 1-3 → Task 6 Steps 6.1-6.3.

All spec sections are covered. No gaps.

**Placeholder scan:** Searched the plan for "TBD", "TODO", "implement later", "fill in details", "add appropriate", "similar to Task". None found. Every code step contains the actual code.

**Type consistency:**
- `_DATE_INK_PRESENT_DENSITY` (Task 2) is a module-level `float`; referenced by name in Tasks 3, 4, 5. No drift.
- `_is_noisy_section_header(row, date_density)` (Task 3) — signature matches the call site update in Task 3 Step 3.4 and Task 5 Step 5.3.
- `_row_from_cells(texts, roles, date_density)` (Task 4) — signature matches Task 4 Step 4.4 and Task 5 Step 5.3.
- `rows_from_cell_texts(cell_rows, roles, date_densities)` (Task 5) — signature matches the cli.py call site in Step 5.4.
- `date_densities` is a `list[float]` parallel to `cell_rows` (one entry per row); both are appended together inside the row loop in Step 5.4. Length-equal by construction; the `zip(..., strict=True)` in Step 5.3 guards against drift.

No inconsistencies found.
