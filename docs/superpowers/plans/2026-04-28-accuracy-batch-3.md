# Accuracy Batch 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise golden row accuracy from 76.53% by fixing five OCR/extraction failure categories: asset spacing glue-words, dropped trailing numerics, cross-row asset contamination (probe-only), and per-row margin-gated mark baseline subtraction (replacing Task 6's failed page-level heuristic).

**Architecture:** Five fixes, ordered cheapest-first so the risky one (margin-gated baseline) lands last with the others already banked. Each fix is one commit. Fix B/C live in `extract._normalize_asset`. Fix E ships a probe script and conditionally a small `_looks_collapsed` adjustment in `cli.py` (deferred to Batch 4 if root cause is grid drift). Fix A+D replaces `_resolve_competing_marks` calls in `cli._process_page` with a margin-gated resolver: subtract baseline only when the raw winner-vs-runner-up margin is below `MARGIN_THRESHOLD` (initial 0.05).

**Tech Stack:** Python 3.x, uv, pytest, ruff, mypy, NumPy, Pillow, pytesseract.

**Reference spec:** `docs/superpowers/specs/2026-04-28-accuracy-batch-3-design.md`.

**Branch:** `feat/accuracy-batch-3` (already created from `main`, design spec already committed).

---

## File Structure

**Modified:**
- `src/ocr_ptr_pdf_converter/extract.py` — add spacing rules + numeric-tail predicate to `_normalize_asset`.
- `src/ocr_ptr_pdf_converter/cli.py` — replace `_resolve_competing_marks` call sites with margin-gated resolver; remove `_is_single_tx_page`. Optional small `_looks_collapsed` crop tweak (Fix E, gated).
- `tests/test_extract.py` — six new normalization tests.
- `tests/test_marks.py` — three new margin-gate tests; remove the two tests that exercise the now-deleted `_is_single_tx_page`.

**Created:**
- `scripts/probe_cross_row_assets.py` — diagnostic probe for Fix E.

---

## Task 1: Fix B — Asset spacing rule for glued company suffix (`INC`/`LLC`/`CORP`/`PLC`)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py:231` (`_normalize_asset`)
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_extract.py`:

```python
def test_normalize_asset_splits_glued_inc_suffix():
    assert _normalize_asset("INTUITINC") == "INTUIT INC"


def test_normalize_asset_splits_glued_inc_short_prefix():
    # PTC has only 3 letters before INC — must still split.
    assert _normalize_asset("PTCINC") == "PTC INC"


def test_normalize_asset_splits_glued_corp_suffix():
    assert _normalize_asset("ACMECORP") == "ACME CORP"


def test_normalize_asset_splits_glued_llc_suffix():
    assert _normalize_asset("FOOLLC") == "FOO LLC"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_extract.py::test_normalize_asset_splits_glued_inc_suffix tests/test_extract.py::test_normalize_asset_splits_glued_inc_short_prefix tests/test_extract.py::test_normalize_asset_splits_glued_corp_suffix tests/test_extract.py::test_normalize_asset_splits_glued_llc_suffix -v
```

Expected: 4 FAILs (assert mismatch — output still glued).

- [ ] **Step 3: Add the splitting rule**

In `src/ocr_ptr_pdf_converter/extract.py`, inside `_normalize_asset`, after the existing `re.sub(r"\bCL([A-K])\b", r"CL \1", s)` line (around line 241), add:

```python
    # Split glued company suffix: "INTUITINC" → "INTUIT INC", "PTCINC" → "PTC INC".
    # Allow prefix ≥ 2 chars to catch short tickers like "PTC". The suffix list is
    # closed (INC|LLC|CORP|PLC) so this can't munch real words.
    s = re.sub(r"\b([A-Z]{2,})(INC|LLC|CORP|PLC)\b", r"\1 \2", s)
```

- [ ] **Step 4: Run the new tests, verify pass**

```bash
uv run pytest tests/test_extract.py -v
```

Expected: all extract tests PASS (existing + 4 new).

- [ ] **Step 5: Run the golden test and record the accuracy delta**

The spec requires the golden run after every fix so any per-fix regression is caught at the commit boundary, not after later risky work lands.

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_task1.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_task1.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_task1.txt
```

Capture the accuracy number from the `accuracy=X.XX%` line. Compute the delta vs the pre-batch baseline of 76.53% and use it in the commit message below. If accuracy *decreased*, do **not** commit — investigate the regression first (the suffix splitter may be munching tokens the SUSPECTS list didn't anticipate). Tighten the regex (e.g. lower-bound prefix length to 3) and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "feat(extract): split glued company suffix in asset names

Adds a regex in _normalize_asset that turns INTUITINC → INTUIT INC,
PTCINC → PTC INC, etc. Suffix list is closed (INC|LLC|CORP|PLC) so
the rule cannot split legitimate tokens.

Fix B from accuracy batch 3.
Golden accuracy: <X.XX%> (Δ vs 76.53% baseline: <+/-Y.YY%>)."
```

---

## Task 2: Fix B — Glued share-class / portfolio token table

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py:231` (`_normalize_asset`)
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_extract.py`:

```python
def test_normalize_asset_splits_plcshs_token():
    assert _normalize_asset("AON PLCSHS CL A") == "AON PLC SHS CL A"


def test_normalize_asset_splits_equportf_token():
    assert _normalize_asset("ALPHA EQUPORTF") == "ALPHA EQU PORTF"


def test_normalize_asset_splits_eqportf_token():
    assert _normalize_asset("BETA EQPORTF") == "BETA EQ PORTF"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_extract.py::test_normalize_asset_splits_plcshs_token tests/test_extract.py::test_normalize_asset_splits_equportf_token tests/test_extract.py::test_normalize_asset_splits_eqportf_token -v
```

Expected: 3 FAILs.

- [ ] **Step 3: Add a small static substitution table**

In `src/ocr_ptr_pdf_converter/extract.py`, after the `_COMPANY_TRAILING_SUFFIXES` definition (around line 228), add:

```python
# Glued OCR tokens we know how to split. Implemented as an exact-token table
# rather than a greedy regex so we cannot accidentally split legitimate words.
_GLUED_TOKEN_SPLITS = {
    "PLCSHS": "PLC SHS",
    "EQUPORTF": "EQU PORTF",
    "EQPORTF": "EQ PORTF",
}
```

Then inside `_normalize_asset`, after the new `INC|LLC|CORP|PLC` splitter from Task 1, add a token-level pass before the trailing-trim loop. Locate the line:

```python
    tokens = s.split(" ")
```

Replace it with:

```python
    tokens = []
    for tok in s.split(" "):
        replacement = _GLUED_TOKEN_SPLITS.get(tok.upper())
        if replacement:
            tokens.extend(replacement.split(" "))
        else:
            tokens.append(tok)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_extract.py -v
```

Expected: all extract tests PASS.

- [ ] **Step 5: Run the golden test and record the accuracy delta**

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_task2.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_task2.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_task2.txt
```

Capture the accuracy number. Compare against `/tmp/golden_task1.txt` from Task 1. If accuracy decreased relative to Task 1, do **not** commit — the substitution table is munching a token it shouldn't. Narrow the table or remove the offending entry, then re-run.

- [ ] **Step 6: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "feat(extract): split known glued OCR tokens (PLCSHS, EQUPORTF, EQPORTF)

Adds a small static substitution table inside _normalize_asset so
glued tokens we observed in golden output expand back to their
canonical multi-word forms. Table is exact-match so it cannot munch
legitimate words.

Fix B (continued) from accuracy batch 3.
Golden accuracy: <X.XX%> (Δ vs prior task: <+/-Y.YY%>)."
```

---

## Task 3: Fix C — Preserve short trailing numerics

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py:231` (`_normalize_asset` tail-trim loop)
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_extract.py`:

```python
def test_normalize_asset_keeps_short_numeric_after_inv():
    assert _normalize_asset("CEDAR HOLDINGS LP INV 1292") == "CEDAR HOLDINGS LP INV 1292"


def test_normalize_asset_keeps_short_numeric_after_usd1():
    # USD1 followed by 00 (the cent fragment) is a real OCR pattern; keep it.
    assert (
        _normalize_asset("GENUINE PARTS CO COM USD1 00")
        == "GENUINE PARTS CO COM USD1 00"
    )
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_extract.py::test_normalize_asset_keeps_short_numeric_after_inv tests/test_extract.py::test_normalize_asset_keeps_short_numeric_after_usd1 -v
```

Expected: 2 FAILs (the trailing `1292` / `00` get stripped).

- [ ] **Step 3: Tighten the trailing-trim predicate**

In `src/ocr_ptr_pdf_converter/extract.py`, inside `_normalize_asset`, the trailing-trim loop currently starts:

```python
    while tokens:
        t = tokens[-1]
        if _NOISE_TOKEN_RE.match(t) or _TRAIL_NOLETTERS_RE.match(t):
            tokens.pop()
            continue
```

Replace just the `_TRAIL_NOLETTERS_RE` arm so digit-only short tails are protected when the prior token is a known asset-tail anchor. Change those four lines to:

```python
    _NUMERIC_TAIL_ANCHORS = ("INV", "COM", "USD1")
    while tokens:
        t = tokens[-1]
        if _NOISE_TOKEN_RE.match(t):
            tokens.pop()
            continue
        if _TRAIL_NOLETTERS_RE.match(t):
            # Protect a short digit-only tail when the previous token is a
            # known asset-tail anchor (e.g. "INV 1292", "USD1 00"). These
            # are real fragments of asset descriptions, not table-rule junk.
            prev_upper = tokens[-2].upper() if len(tokens) >= 2 else ""
            if (
                t.isdigit()
                and len(t) <= 4
                and (
                    prev_upper in _REAL_SHORT_SUFFIXES
                    or prev_upper in _NUMERIC_TAIL_ANCHORS
                )
            ):
                break
            tokens.pop()
            continue
```

(Define `_NUMERIC_TAIL_ANCHORS` once at module scope just below `_REAL_SHORT_SUFFIXES` instead of inside the function — the inline placement above is for visibility; move it to module scope when wiring up. Final placement:)

In module scope, just after `_COMPANY_TRAILING_SUFFIXES` (around line 228), add:

```python
# Tokens that legitimately precede a short digit-only tail in an asset name
# (e.g. "INV 1292", "COM USD1 00"). Used by _normalize_asset's tail-trim loop.
_NUMERIC_TAIL_ANCHORS = frozenset({"INV", "COM", "USD1"})
```

And remove the `_NUMERIC_TAIL_ANCHORS = (...)` line from inside the function.

- [ ] **Step 4: Run all extract tests, verify pass**

```bash
uv run pytest tests/test_extract.py -v
```

Expected: all PASS (including the two new ones plus all prior tests — verify the prior tail-trim behavior for non-anchored numerics like a stray trailing `7` is unaffected).

- [ ] **Step 5: Run the golden test and record the accuracy delta**

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_task3.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_task3.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_task3.txt
```

Capture the accuracy number. Compare against `/tmp/golden_task2.txt`. If accuracy decreased relative to Task 2, do **not** commit — the predicate is over-protecting numerics elsewhere. Tighten the anchor list (e.g. drop `COM` if it triggers on table-rule junk) and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "feat(extract): preserve short digit-only tail after asset-anchor token

The previous tail-trim loop dropped legitimate tail numerics like
'1292' in 'CEDAR HOLDINGS LP INV 1292' and '00' in 'GENUINE PARTS
CO COM USD1 00'. New rule: keep digit-only trailing tokens of length
≤ 4 when the prior token is in _REAL_SHORT_SUFFIXES or one of the
explicit numeric-tail anchors (INV, COM, USD1).

Fix C from accuracy batch 3.
Golden accuracy: <X.XX%> (Δ vs prior task: <+/-Y.YY%>)."
```

---

## Task 4: Fix E — Probe script for cross-row asset contamination

**Files:**
- Create: `scripts/probe_cross_row_assets.py`

- [ ] **Step 1: Capture the actual-vs-expected diff for cross-reference**

Per-row coordinates are not available from `tests/test_golden.py` (it emits aggregate accuracy + up to 5 missed-row tuples and uses multiset comparison). Substring-matching expected asset strings against OCR output is also unsafe: in the stated failure mode (e.g. `EQT CORP COM` came out as `S&P GLOBAL INC COM`), neither string is a substring of the other, so any such filter would silently miss the contamination by construction.

The probe in Step 2 therefore does **not** filter by suspect strings at all. It enumerates every asset cell on every page, runs both the 1x and 2x OCR paths unconditionally, and emits the 1x text, 2x text, and the prev/next-row 1x text for the same column whenever the 2x output differs from the 1x output OR whenever `_looks_collapsed(text_1x)` triggered. That is exhaustive over the only code path that can cause local contamination per the design hypothesis, so the diagnostic cannot have false negatives — including the fully-substituted-neighbor case where neither expected nor actual asset string is a substring of the other.

Capture the diagnose output anyway as cross-reference for inspection — it tells you which expected/actual asset strings to look for in the probe output:

```bash
uv run python scripts/diagnose_golden.py --refresh 2>&1 | tee /tmp/diagnose.txt
```

`diagnose_golden.py` prints both unmatched-expected rows and extra-actual rows. Note both lists for use during Step 4 inspection: a contamination signature is when a row's 2x OCR text matches a neighbor's 1x text, AND that row's content appears in the extra-actual list while a neighbor's expected asset appears in the missing-expected list.

- [ ] **Step 2: Write the probe script**

Create `scripts/probe_cross_row_assets.py`:

```python
"""Diagnostic probe for Fix E (cross-row asset contamination).

The probe walks every page, runs 1x and 2x OCR on every asset cell, and
prints — for every cell where the 2x path triggered (`_looks_collapsed`)
or where 2x output differs from 1x — that row's 1x + 2x asset text plus
the prev-row and next-row 1x asset text from the same column. That gives
the direct local-bleed signature: 2x output of row N matching 1x output
of row N-1 or N+1.

The probe deliberately does NOT filter by expected-asset substring. In the
documented failure mode (`EQT CORP COM` -> `S&P GLOBAL INC COM`) neither
string contains the other, so substring filtering would silently miss the
contamination. Exhaustive enumeration over the 2x code path is the only
sound diagnostic.

Hypothesis: the 2x upscaled crop in cli._process_page pulls ink from the
adjacent row. If contamination appears only at 2x and not at 1x, tighten
the upscale crop. If contamination is already in the 1x pass, root cause
is grid drift -> deferred to Batch 4.

Usage:
    uv run python scripts/probe_cross_row_assets.py <pdf> [page1 page2 ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image as PILImage

from ocr_ptr_pdf_converter.cli import (
    _crop_binary,
    _crop_pil,
    _kind_for_cell,
    _looks_collapsed,
    _orient_and_grid,
    _resolve_roles,
)
from ocr_ptr_pdf_converter.extract import ColumnRole
from ocr_ptr_pdf_converter.ocr import ocr_cell
from ocr_ptr_pdf_converter.render import render_pdf


def _ocr_asset_for_row(
    oriented: PILImage.Image,
    binary,
    grid_cols: list[tuple[int, int]],
    asset_col_idx: int,
    role: ColumnRole,
    col_width: int,
    row: tuple[int, int],
) -> tuple[str, str | None, bool]:
    """Return (text_1x, text_2x_or_None, looks_collapsed_triggered)."""
    x0, x1 = grid_cols[asset_col_idx]
    y0, y1 = row
    rect = (x0, y0, x1, y1)
    crop = _crop_pil(oriented, rect)
    bin_crop = _crop_binary(binary, rect)
    if crop.width <= 1 or crop.height <= 1:
        return ("", None, False)
    kind = _kind_for_cell(role, col_width)
    text_1x = ocr_cell(crop, bin_crop, kind)
    triggered = _looks_collapsed(text_1x)
    # Always run 2x so we can see contamination even on rows where the
    # production code would not have re-OCR'd. Cheap relative to one
    # golden run and removes another false-negative path.
    up = crop.resize(
        (crop.width * 2, crop.height * 2),
        PILImage.Resampling.LANCZOS,
    )
    text_2x = ocr_cell(up, bin_crop, kind)
    return (text_1x, text_2x, triggered)


def probe_page(image: PILImage.Image, page_number: int) -> None:
    rotation, oriented, binary, grid = _orient_and_grid(image)
    if not grid.rows or not grid.cols:
        print(f"page {page_number}: no grid")
        return
    roles = _resolve_roles(grid, oriented)
    asset_indices = [i for i, r in enumerate(roles) if r is ColumnRole.ASSET]
    if not asset_indices:
        print(f"page {page_number}: no asset column")
        return
    col_widths = [x1 - x0 for x0, x1 in grid.cols]
    data_rows = grid.rows[1:]

    # First pass: 1x + 2x asset OCR for every data row, per asset column.
    per_row: list[dict[int, tuple[str, str | None, bool]]] = []
    for row in data_rows:
        col_results: dict[int, tuple[str, str | None, bool]] = {}
        for col_idx in asset_indices:
            col_results[col_idx] = _ocr_asset_for_row(
                oriented, binary, grid.cols, col_idx,
                roles[col_idx], col_widths[col_idx], row,
            )
        per_row.append(col_results)

    # Second pass: emit every row where 2x triggered in production OR where
    # 2x output differs from 1x output (covers contamination cases the 1x
    # path may already exhibit). Always include immediate neighbors so the
    # cross-row signature is visible.
    header_printed = False
    for row_idx, results in enumerate(per_row):
        for col_idx, (t1, t2, triggered) in results.items():
            differs = (t2 or "") != t1
            if not (triggered or differs):
                continue
            if not header_printed:
                print(f"\n=== page {page_number} (rotation={rotation}) ===")
                header_printed = True
            y0, y1 = data_rows[row_idx]
            flag = "TRIG" if triggered else "diff"
            print(f"  [{flag}] row {row_idx:>2} col {col_idx} y=[{y0},{y1}]")
            print(f"    1x={t1!r}")
            print(f"    2x={t2!r}")
            for delta, label in ((-1, "prev"), (1, "next")):
                n = row_idx + delta
                if 0 <= n < len(per_row):
                    nt1, _nt2, _ntr = per_row[n][col_idx]
                    ny0, ny1 = data_rows[n]
                    print(f"    {label} row {n:>2} y=[{ny0},{ny1}] 1x={nt1!r}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    pdf_path = Path(argv[0])
    pages_arg = [int(p) for p in argv[1:]] or None
    images = render_pdf(pdf_path, dpi=300, pages=pages_arg)
    for idx, img in enumerate(images, start=1):
        page_number = pages_arg[idx - 1] if pages_arg else idx
        probe_page(img, page_number)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 3: Run the probe**

```bash
uv run python scripts/probe_cross_row_assets.py tests/fixtures/9115728.pdf 2>&1 | tee /tmp/cross_row_probe.txt
```

(`9115728.pdf` is the fixture used by `tests/test_golden.py`.)

- [ ] **Step 4: Inspect output and decide**

Compare the misattributed rows' 1x and 2x outputs:

- **If 1x is correct and 2x is wrong** → Fix E is local. Proceed to Task 5.
- **If 1x is already wrong** → root cause is grid drift. Skip Task 5; document the finding in the commit message and defer Fix E to Batch 4.

Record the decision (1x-correct vs 1x-wrong) in a one-line `# RESULT:` comment at the top of `scripts/probe_cross_row_assets.py`.

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_cross_row_assets.py
git commit -m "chore(scripts): add cross-row asset contamination probe (Fix E)

Probe for accuracy batch 3 Fix E. Captures 1x and 2x re-OCR text for
asset cells around suspected misattributed rows so we can tell whether
the contamination originates in the upscaled crop (local fix) or in
grid row Y bounds (deferred to Batch 4).

Result recorded in the script header."
```

---

## Task 5 (CONDITIONAL): Fix E — Tighten 2x re-OCR crop

**Skip this task entirely if Task 4's probe showed contamination is already present in the 1x pass.** In that case, the root cause is grid drift and Fix E is deferred to Batch 4 per the spec.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/cli.py:243-250` (the `_looks_collapsed` upscaled re-OCR block in `_process_page`)
- Test: covered by golden test (no unit test — there is no unit-testable behavior; the change is a crop-margin tweak and the only verifier is the golden run).

- [ ] **Step 1: Shrink the upscale crop by 1px on top and bottom before resize**

In `src/ocr_ptr_pdf_converter/cli.py`, locate the block in `_process_page`:

```python
            if role is ColumnRole.ASSET and _looks_collapsed(text):
                upscaled = crop.resize(
                    (crop.width * 2, crop.height * 2),
                    PILImage.Resampling.LANCZOS,
                )
```

Replace with:

```python
            if role is ColumnRole.ASSET and _looks_collapsed(text):
                # Trim 1px top and bottom before upscaling so the resampled
                # crop cannot pull ink from the row above/below — observed
                # cause of cross-row asset contamination on page-3 SALE rows.
                trim_top = 1 if crop.height > 4 else 0
                trim_bot = 1 if crop.height > 4 else 0
                trimmed = crop.crop((0, trim_top, crop.width, crop.height - trim_bot))
                upscaled = trimmed.resize(
                    (trimmed.width * 2, trimmed.height * 2),
                    PILImage.Resampling.LANCZOS,
                )
```

- [ ] **Step 2: Re-run the probe to verify**

```bash
uv run python scripts/probe_cross_row_assets.py tests/fixtures/9115728.pdf 2>&1 | tee /tmp/cross_row_after.txt
```

Compare against the pre-fix `/tmp/cross_row_probe.txt`. The misattributed rows' 2x output should now match the surrounding 1x text instead of the adjacent-row text.

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest --ignore=tests/test_golden.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run the golden test and record the accuracy delta**

Fix E touches the production OCR path, so the golden run is required before this commit (per the spec's per-fix verification rule).

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_task5.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_task5.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_task5.txt
```

Capture the accuracy number. Compare against `/tmp/golden_task3.txt`. If accuracy decreased, do **not** commit — the 1px trim is removing in-row ink on tight crops. Restore the file with `git restore --source=HEAD --worktree src/ocr_ptr_pdf_converter/cli.py` and skip Task 5 (defer Fix E to Batch 4).

- [ ] **Step 5: Commit**

```bash
git add src/ocr_ptr_pdf_converter/cli.py
git commit -m "fix(cli): trim asset crop by 1px before 2x re-OCR upscale

Probe (scripts/probe_cross_row_assets.py) showed the 2x upscaled crop
was pulling ink from the adjacent row. Trimming 1px top/bottom before
LANCZOS resize removes the bleed without affecting the in-row glyphs.

Fix E from accuracy batch 3.
Golden accuracy: <X.XX%> (Δ vs prior task: <+/-Y.YY%>)."
```

---

## Task 6: Fix A+D — Margin-gated baseline subtraction (the risky one)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/cli.py:162-261` (introduce `_MARGIN_THRESHOLD`, replace `_resolve_competing_marks` callers, delete `_is_single_tx_page`)
- Test: `tests/test_marks.py`

This task lands last so the other fixes are already banked. Per the spec's revert protocol: if any page-1 regression appears after this commit, revert just this commit — Fixes B/C/E remain banked.

- [ ] **Step 1: Remove the obsolete `_is_single_tx_page` tests**

The helper is being deleted, so its two tests must go. In `tests/test_marks.py`, delete the functions `test_single_tx_section_skips_baseline` and `test_single_tx_section_does_not_skip_when_split` (lines 25-46) and remove `_is_single_tx_page` from the import on line 3 so it reads:

```python
from ocr_ptr_pdf_converter.cli import _compute_col_baselines
```

(The import will be expanded again in Step 2 to add the new helper.)

- [ ] **Step 2: Write failing tests for the new margin-gated resolver**

Append to `tests/test_marks.py`. First update the import line to:

```python
from ocr_ptr_pdf_converter.cli import (
    _MARGIN_THRESHOLD,
    _compute_col_baselines,
    _resolve_competing_marks_gated,
)
from ocr_ptr_pdf_converter.extract import ColumnRole
```

Then append the three tests:

```python
def test_margin_gate_keeps_raw_winner_when_margin_high():
    # PURCHASE wins by a wide margin -> baseline is not applied,
    # raw winner kept. Regression guard for page 1.
    role_set = frozenset({ColumnRole.PURCHASE, ColumnRole.SALE})
    roles = [ColumnRole.PURCHASE, ColumnRole.SALE]
    row_texts = ["", ""]
    densities = [0.30, 0.05]  # margin = 0.25 >> _MARGIN_THRESHOLD
    col_baselines = [0.20, 0.04]  # if applied, eff_purchase=0.10, eff_sale=0.01
    _resolve_competing_marks_gated(row_texts, densities, roles, role_set, col_baselines)
    assert row_texts == ["X", ""]


def test_margin_gate_applies_baseline_when_margin_low():
    # PURCHASE only narrowly edges SALE on raw ink, but both columns share a
    # high baseline. After subtraction SALE wins. Regression guard for page 3.
    role_set = frozenset({ColumnRole.PURCHASE, ColumnRole.SALE})
    roles = [ColumnRole.PURCHASE, ColumnRole.SALE]
    row_texts = ["", ""]
    densities = [0.22, 0.20]  # margin = 0.02 < _MARGIN_THRESHOLD
    col_baselines = [0.20, 0.10]  # eff_purchase=0.02, eff_sale=0.10 -> SALE
    _resolve_competing_marks_gated(row_texts, densities, roles, role_set, col_baselines)
    assert row_texts == ["", "X"]
    assert _MARGIN_THRESHOLD > 0  # sanity: threshold is configured


def test_margin_gate_single_column_keeps_winner():
    # Only one column in the role-set: no runner-up, treat as infinite margin.
    role_set = frozenset({ColumnRole.AMOUNT})
    roles = [ColumnRole.AMOUNT]
    row_texts = [""]
    densities = [0.10]
    col_baselines = [0.50]  # would zero out under baseline subtraction
    _resolve_competing_marks_gated(row_texts, densities, roles, role_set, col_baselines)
    assert row_texts == ["X"]
```

- [ ] **Step 3: Run the new tests, verify they fail**

```bash
uv run pytest tests/test_marks.py -v
```

Expected: 3 FAILs on the new tests (`_resolve_competing_marks_gated` doesn't exist yet); existing tests for `_compute_col_baselines` still pass.

- [ ] **Step 4: Implement `_MARGIN_THRESHOLD` and `_resolve_competing_marks_gated`**

In `src/ocr_ptr_pdf_converter/cli.py`, just below the existing `_MARK_WINNER_DENSITY = 0.05` line (around line 63), add:

```python
# When the raw winner leads the runner-up by at least this much ink density,
# we trust the raw winner and skip baseline subtraction. When the margin is
# tighter, the columns are likely sharing systematic bleed and we fall back
# to baseline-subtracted effective densities. Initial value chosen so page-1
# PURCHASE rows (wide margin) bypass the gate and page-3 PURCHASE-vs-SALE
# rows (thin margin) trigger subtraction. Calibrated via probe_baseline_marks.
_MARGIN_THRESHOLD = 0.05
```

Then replace the existing `_resolve_competing_marks` function (lines 162-177) with both the legacy raw resolver and a new gated resolver. The gated resolver becomes the production call site; the raw resolver is no longer needed and is deleted.

Delete `_resolve_competing_marks` (lines 162-177) and `_is_single_tx_page` (lines 194-214). Insert in their place:

```python
def _resolve_competing_marks_gated(
    row_texts: list[str],
    densities: list[float],
    roles: list[ColumnRole],
    role_set: frozenset[ColumnRole],
    col_baselines: list[float],
) -> None:
    """Per-row, per-role-set mark winner with margin-gated baseline fallback.

    1. Compute the raw winner (highest density in role_set).
    2. If the winner is below _MARK_WINNER_DENSITY -> clear all role_set cells.
    3. If the winner leads the runner-up by at least _MARGIN_THRESHOLD ->
       keep the raw winner.
    4. Otherwise compute effective densities (raw - baseline, floored at 0)
       and pick the effective winner if it still clears _MARK_WINNER_DENSITY.

    Mutates row_texts in place. Cells outside role_set are untouched.
    """
    candidates = [(densities[i], i) for i, r in enumerate(roles) if r in role_set]
    if not candidates:
        return

    raw_winner_d, raw_winner_idx = max(candidates, key=lambda t: t[0])

    if raw_winner_d < _MARK_WINNER_DENSITY:
        for _d, i in candidates:
            row_texts[i] = ""
        return

    # Single-column role-set: no runner-up, treat as infinite margin.
    if len(candidates) == 1:
        for _d, i in candidates:
            row_texts[i] = "X" if i == raw_winner_idx else ""
        return

    runner_up_d = max(d for d, i in candidates if i != raw_winner_idx)
    margin = raw_winner_d - runner_up_d

    if margin >= _MARGIN_THRESHOLD:
        winner_idx = raw_winner_idx
    else:
        eff = [
            (max(0.0, densities[i] - col_baselines[i]), i)
            for _d, i in candidates
        ]
        eff_winner_d, eff_winner_idx = max(eff, key=lambda t: t[0])
        if eff_winner_d < _MARK_WINNER_DENSITY:
            for _d, i in candidates:
                row_texts[i] = ""
            return
        winner_idx = eff_winner_idx

    for _d, i in candidates:
        row_texts[i] = "X" if i == winner_idx else ""
```

- [ ] **Step 5: Wire up the gated resolver in `_process_page`**

The existing `_process_page` (around line 217) builds `cell_rows` row-by-row inside a single loop and calls `_resolve_competing_marks` immediately. To use baselines we need a two-pass structure: collect all rows' densities first, compute baselines per column, then resolve.

Locate the `for y0, y1 in grid.rows[1:]:` loop (around line 230). Replace from that line through (and including) the `cell_rows.append(row_texts)` line with:

```python
    pending: list[tuple[list[str], list[float]]] = []
    for y0, y1 in grid.rows[1:]:
        row_texts: list[str] = []
        densities: list[float] = []
        for (x0, x1), role, width in zip(grid.cols, roles, col_widths, strict=True):
            rect = (x0, y0, x1, y1)
            crop = _crop_pil(oriented, rect)
            bin_crop = _crop_binary(binary, rect)
            if crop.width <= 1 or crop.height <= 1:
                row_texts.append("")
                densities.append(0.0)
                continue
            kind = _kind_for_cell(role, width)
            text = ocr_cell(crop, bin_crop, kind)
            if role is ColumnRole.ASSET and _looks_collapsed(text):
                upscaled = crop.resize(
                    (crop.width * 2, crop.height * 2),
                    PILImage.Resampling.LANCZOS,
                )
                text_2x = ocr_cell(upscaled, bin_crop, kind)
                if len(text_2x.split()) > len(text.split()):
                    text = text_2x
            row_texts.append(text)
            densities.append(ink_density(bin_crop) if bin_crop.size else 0.0)
        pending.append((row_texts, densities))

    # Per-column baselines computed across all rows on this page.
    if pending:
        n_cols = len(grid.cols)
        densities_per_col: list[list[float]] = [
            [row_d[i] for _t, row_d in pending] for i in range(n_cols)
        ]
        col_baselines = _compute_col_baselines(densities_per_col)
    else:
        col_baselines = [0.0] * len(grid.cols)

    cell_rows: list[list[str]] = []
    for row_texts, densities in pending:
        _resolve_competing_marks_gated(
            row_texts, densities, roles, _TX_MARK_ROLE_SET, col_baselines
        )
        _resolve_competing_marks_gated(
            row_texts,
            densities,
            roles,
            frozenset({ColumnRole.AMOUNT}),
            col_baselines,
        )
        cell_rows.append(row_texts)
```

(If `_looks_collapsed` was modified in Task 5, the inner crop block from Task 5 stays — preserve whatever currently sits in the file.)

- [ ] **Step 6: Run all tests except the golden**

```bash
uv run pytest --ignore=tests/test_golden.py -v
```

Expected: all PASS, including the three new margin-gate tests and the existing `_compute_col_baselines` tests.

- [ ] **Step 7: Run the page-1 regression probe**

```bash
uv run python scripts/probe_baseline_marks.py 2>&1 | tee /tmp/probe_after_ad.txt
```

Inspect the output: every page-1 row's TX winner must be `PURCHASE` (matching the pre-batch state). If any page-1 row flipped to a different winner, **stop** — do not commit. Adjust `_MARGIN_THRESHOLD` upward (e.g. 0.07, 0.10) until page-1 winners are stable, then re-run.

If after reasonable threshold tuning page-1 still regresses, abort Fix A+D with an isolated rollback that preserves the already-committed B/C(/E) work and any unrelated tree state. Restore **only** the two files this task touched — never use `git restore .`:

```bash
git restore --source=HEAD --worktree --staged \
    src/ocr_ptr_pdf_converter/cli.py tests/test_marks.py
```

That brings both files back to their last-committed state (which is the post-Fix-E commit, i.e. Fixes B/C/E banked) and leaves every other file in the working tree alone. Then skip to Task 7 with Fixes B/C(/E) only.

Note: do not commit Task 6 until Step 7's probe and Step 8's golden run both pass. If a regression is discovered *after* the Task 6 commit lands, recover with `git revert <commit-sha>` (single-commit revert) — never with a tree-wide `git restore` or `git reset --hard`. The per-fix-commit architecture in the spec exists precisely so any one fix can be reverted in isolation.

- [ ] **Step 8: Run the golden test**

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_task6.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_task6.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_task6.txt
```

Expected: accuracy strictly greater than 76.53%.

- [ ] **Step 9: Commit**

```bash
git add src/ocr_ptr_pdf_converter/cli.py tests/test_marks.py
git commit -m "feat(cli): margin-gated baseline subtraction for mark resolution

Replaces the failed Task 6 page-level _is_single_tx_page heuristic
with a per-row margin gate: subtract column baselines only when the
raw winner leads the runner-up by less than _MARGIN_THRESHOLD (0.05).

Page 1 PURCHASE rows clear the gate (wide margin), keeping the raw
winner exactly as before. Page 3 PURCHASE-vs-SALE rows fall below the
gate and get baseline-corrected, recovering the SALE winner that
systematic PURCHASE-column bleed was masking.

The same logic is applied to AMOUNT column resolution (Fix D).
_is_single_tx_page is removed.

Fix A+D from accuracy batch 3.
Golden accuracy: <X.XX%> (Δ vs prior task: <+/-Y.YY%>)."
```

---

## Task 7: Lint, mypy, and final golden run

**Files:** none new.

- [ ] **Step 1: Run ruff**

```bash
uv run ruff check src tests scripts
```

Expected: clean. Fix any reported issues with `uv run ruff check --fix src tests scripts` plus manual edits if needed.

- [ ] **Step 2: Run mypy**

```bash
uv run mypy src
```

Expected: clean. Fix any new errors.

- [ ] **Step 3: Run the full test suite (non-golden)**

```bash
uv run pytest --ignore=tests/test_golden.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run the golden test and record accuracy**

```bash
uv run pytest tests/test_golden.py -v > /tmp/golden_final.txt 2>&1 &
until grep -qE "passed|failed|error" /tmp/golden_final.txt 2>/dev/null; do sleep 30; done
grep -oE 'accuracy=[0-9.]+%' /tmp/golden_final.txt
```

Capture the exact accuracy number from the output.

- [ ] **Step 5: Commit lint fixes (if any) with the accuracy delta in the message**

```bash
git add -p  # only if ruff/mypy required source edits
git commit -m "chore: ruff/mypy cleanup for accuracy batch 3

Final golden accuracy: <X.XX%> (was 76.53%)."
```

If no source edits were needed, skip the commit and just record the accuracy in the PR description.

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin feat/accuracy-batch-3
gh pr create --title "feat: accuracy batch 3 (asset spacing, trailing numerics, margin-gated baseline)" --body "$(cat <<'EOF'
## Summary
- Fix B: split glued company suffixes (INTUITINC -> INTUIT INC) and known glued tokens (PLCSHS, EQUPORTF, EQPORTF).
- Fix C: preserve short digit-only trailing tokens after asset-anchor tokens (INV 1292, USD1 00).
- Fix E: probe + (conditionally) trim 1px before 2x re-OCR upscale.
- Fix A+D: replace failed _is_single_tx_page heuristic with per-row margin-gated baseline subtraction for TX and AMOUNT mark resolution.

Final golden accuracy: <X.XX%> (was 76.53%).

Spec: docs/superpowers/specs/2026-04-28-accuracy-batch-3-design.md

## Test plan
- [x] tests/test_extract.py — 6 new normalization tests
- [x] tests/test_marks.py — 3 new margin-gate tests; 2 obsolete _is_single_tx_page tests removed
- [x] scripts/probe_baseline_marks.py re-run; page 1 winners unchanged
- [x] tests/test_golden.py — accuracy strictly greater than 76.53%
- [x] ruff + mypy clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist (already applied)

- **Spec coverage:** every spec section maps to a task. Fix B → Tasks 1+2. Fix C → Task 3. Fix E → Tasks 4 (probe) + 5 (conditional). Fix A+D → Task 6. Lint/mypy/golden → Task 7. Page-1 regression guard → Task 6 Step 7 (probe re-run before commit). Per-fix golden verification → golden-run step in every production-change task before its commit (Tasks 1, 2, 3, 5, 6).
- **Placeholders:** none. Every code block is concrete; the `<X.XX%>` / `<+/-Y.YY%>` markers in commit messages are filled in by the worker from the captured `/tmp/golden_taskN.txt` output.
- **Type consistency:** new helper is named `_resolve_competing_marks_gated` everywhere (definition, import, call sites). `_MARGIN_THRESHOLD`, `_MARK_WINNER_DENSITY`, `_compute_col_baselines`, `_TX_MARK_ROLE_SET`, `ColumnRole.AMOUNT` match existing names in `cli.py`.
- **Conditional logic:** Task 5 has an explicit skip predicate stated up front; Task 6 Step 7 has an explicit single-file rollback procedure that preserves earlier B/C/E commits (no tree-wide `git restore`).
- **Fix E false-negative protection:** Task 4 probe enumerates every 2x-triggered or 1x-vs-2x-differing asset cell rather than substring-filtering against expected asset strings. The fully-substituted-neighbor case (`EQT CORP COM` → `S&P GLOBAL INC COM`) is exposed because both cells appear in the exhaustive emission, regardless of whether either string is a substring of the other.
