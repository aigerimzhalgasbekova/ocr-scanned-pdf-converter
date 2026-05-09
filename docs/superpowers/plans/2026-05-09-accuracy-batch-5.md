# Accuracy Batch 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push golden-test exact-match rate from 80.6% (79/98) to ≥89% (≥87/98) by addressing three independent failure clusters surfaced by `scripts/diagnose_golden.py`.

**Architecture:** Three independent fixes, one per module. Fix 2 in `extract.py:_normalize_asset` (asset trim extension). Fix 3 in `ocr.py:ocr_cell` (date OCR fallback chain). Fix 1 in `cli.py:_process_page` (mark baseline subtraction — wires existing unused `_compute_col_baselines`/`_is_single_tx_page` helpers). Implementation order is ascending risk: Fix 2 → Fix 3 → Fix 1 (which has a Phase A diagnostic gate before any code change).

**Tech Stack:** Python 3.13, pytest, pytesseract, PIL/numpy. Project uses `uv` for environment management — every Python invocation must be `uv run ...` (per `CLAUDE.md`).

**Branch:** `feat/accuracy-batch-5` (already created from `main`).

**Spec:** `docs/superpowers/specs/2026-05-09-accuracy-batch-5-design.md`

---

## Task 1: Fix 2 — Asset trim extension (Cluster C, target +3 rows)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/extract.py:248-306` (the `_normalize_asset` right-side `while tokens:` strip loop)
- Test: `tests/test_extract.py` (append three positive cases + three new regression guards)

**Background:** Current trim handles single-letter-after-company-suffix (`INTUIT INC A` → `INTUIT INC`) and single-digit-after-anchor (`INTUIT INC 7` → `INTUIT INC`). Three patterns still leak through:
- `MAYS ALLOCATE LP 7 A` (digit + single A-K letter)
- `EQT CORP COM - J` (dash + single A-K letter)
- `PTC INC ; BD` (`;` + 2-letter token where `BD` is in `_REAL_SHORT_SUFFIXES`)

The first two share a structural fix: extend the existing `_AK_LETTERS` branch to also pop a trailing single A-K letter when the previous token has no letters (digit, dash, etc.). The third needs a new branch that strips `<suffix> ; <2-3 letters>` together, since `BD` is a protected real-suffix token in isolation.

- [ ] **Step 1: Read the current trim loop to confirm the design hooks**

Run: `uv run python -c "from ocr_ptr_pdf_converter.extract import _normalize_asset; print(_normalize_asset('MAYS ALLOCATE LP 7 A')); print(_normalize_asset('EQT CORP COM - J')); print(_normalize_asset('PTC INC ; BD'))"`

Expected (confirms the bug): each input is returned with the trailing junk still attached.

- [ ] **Step 2: Write the failing positive tests**

Append to `tests/test_extract.py`:

```python
def test_normalize_asset_strips_digit_letter_pair_after_lp():
    # OCR bleed: digit + single A-K letter trailing a company suffix.
    assert _normalize_asset("MAYS ALLOCATE LP 7 A") == "MAYS ALLOCATE LP"


def test_normalize_asset_strips_dash_letter_after_com():
    # OCR bleed: dash + single A-K letter trailing a company suffix.
    assert _normalize_asset("EQT CORP COM - J") == "EQT CORP COM"


def test_normalize_asset_strips_semicolon_short_suffix_after_inc():
    # OCR bleed: "; BD" appended after a real suffix. BD is a protected
    # real suffix in isolation, but this configuration is OCR junk.
    assert _normalize_asset("PTC INC ; BD") == "PTC INC"
```

- [ ] **Step 3: Write three new regression-guard tests**

Append:

```python
def test_normalize_asset_preserves_cl_a_share_class():
    # "CL A" share-class designator must survive — prev is "CL", not a
    # company suffix or a no-letters token.
    assert _normalize_asset("AON PLC SHS CL A") == "AON PLC SHS CL A"


def test_normalize_asset_preserves_inv_numeric_tail_regression_b5():
    # The new digit+letter pop must not interact with the INV numeric-tail
    # protection branch.
    assert (
        _normalize_asset("CEDAR HOLDINGS LP INV 1292")
        == "CEDAR HOLDINGS LP INV 1292"
    )


def test_normalize_asset_preserves_real_bd_suffix():
    # BD on its own (no preceding semicolon) is a real suffix and must stay.
    assert _normalize_asset("SOMETHING REV BD") == "SOMETHING REV BD"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v -k "strips_digit_letter or strips_dash_letter or strips_semicolon_short or preserves_cl_a or preserves_inv_numeric_tail_regression_b5 or preserves_real_bd"`

Expected: 3 FAIL (the three positive cases), 3 PASS (the regression guards already pass under current behavior).

- [ ] **Step 5: Implement the trim extensions**

Edit `src/ocr_ptr_pdf_converter/extract.py`. Locate the `while tokens:` loop in `_normalize_asset` (around line 275–303). The current `_AK_LETTERS` branch is:

```python
        # Single A-K letter: drop only when preceded by a company-suffix token.
        # This strips "INTUIT INC A" → "INTUIT INC" while preserving "CL A".
        if len(t) == 1 and t.upper() in _AK_LETTERS:
            prev = tokens[-2].upper() if len(tokens) >= 2 else ""
            if prev in _COMPANY_TRAILING_SUFFIXES:
                tokens.pop()
                continue
            break  # Preceded by something else (e.g. "CL") — keep the letter.
```

Replace with:

```python
        # Single A-K letter: drop when preceded by a company-suffix token,
        # or when preceded by a no-letters token (digit, "-") that is itself
        # OCR bleed after a company suffix. "INTUIT INC A" → "INTUIT INC".
        # "MAYS ALLOCATE LP 7 A" → pop A, then 7 strips on next iteration.
        # "EQT CORP COM - J" → pop J, then "-" strips as noise.
        # "CL A" is preserved (prev="CL", not a suffix and not no-letters).
        if len(t) == 1 and t.upper() in _AK_LETTERS:
            prev = tokens[-2].upper() if len(tokens) >= 2 else ""
            if prev in _COMPANY_TRAILING_SUFFIXES:
                tokens.pop()
                continue
            if prev and _TRAIL_NOLETTERS_RE.match(prev):
                tokens.pop()
                continue
            break  # Preceded by something else (e.g. "CL") — keep the letter.
```

Then, BEFORE the `_NOISE_TOKEN_RE` check at the top of the loop body (right after `t = tokens[-1]`, before `if _NOISE_TOKEN_RE.match(t):`), insert the `; <2-3 letters>` strip:

```python
        # Strip "<real-suffix> ; <2-3 letters>" tail. "BD" alone is a real
        # suffix, but "PTC INC ; BD" is OCR bleed after the suffix. The
        # semicolon is the disambiguator: real assets do not have a
        # semicolon between their final suffix and a trailing token.
        if (
            len(tokens) >= 3
            and len(t) in (2, 3)
            and t.isalpha()
            and _NOISE_TOKEN_RE.match(tokens[-2])
            and tokens[-3].upper() in _REAL_SHORT_SUFFIXES
        ):
            tokens.pop()
            # Next loop iteration will pop the punctuation token as noise.
            continue
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v -k "strips_digit_letter or strips_dash_letter or strips_semicolon_short or preserves_cl_a or preserves_inv_numeric_tail_regression_b5 or preserves_real_bd"`

Expected: 6 PASS.

- [ ] **Step 7: Run the full extract test file to catch regressions**

Run: `uv run pytest tests/test_extract.py -v`

Expected: all tests PASS (existing batch 4 trim tests + new ones).

- [ ] **Step 8: Run ruff + mypy**

Run: `uv run ruff check src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py && uv run mypy src/ocr_ptr_pdf_converter/extract.py`

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/ocr_ptr_pdf_converter/extract.py tests/test_extract.py
git commit -m "$(cat <<'EOF'
feat(extract): extend asset trim for digit+letter, dash+letter, semicolon+suffix bleed

Handles the three trailing-noise patterns that survived batch 4's single-digit
trim: "LP 7 A", "COM - J", and "INC ; BD". Single-letter A-K branch now also
pops when prev token has no letters; new "<real-suffix> ; <2-3 letters>"
strip handles the semicolon case where the trailing token is a protected
suffix in isolation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Fix 3 — Date OCR fallback chain (Cluster D, target +1 alone, +3 stacked)

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/ocr.py:39-61` (the `ocr_cell` function, specifically the `CellKind.DATE` branch)
- Test: `tests/test_ocr.py` (append five mocked tests)

**Background:** Current date OCR is a single `psm 7` pass + strict `_DATE_RE` regex. When the regex misses (degraded scan, character confusions), the cell returns empty even when the row clearly has a printed date. The diagnostic shows ≥4 rows with `date_density ≥ 0.22` (clearly-printed date in cell) but empty `date_tx`. Add a fallback chain: 2× upscale retry, then OCR digit-confusion substitution gated by both "/" and digit presence (so non-date text cannot be fabricated into a date).

- [ ] **Step 1: Write the failing tests with mocked tesseract**

Append to `tests/test_ocr.py`:

```python
from unittest.mock import patch


def test_ocr_date_fast_path_unchanged():
    """First psm 7 pass returns a clean date — must not enter fallback."""
    img = Image.new("RGB", (200, 60), "white")
    with patch(
        "ocr_ptr_pdf_converter.ocr.pytesseract.image_to_string",
        return_value="3/16/2026",
    ) as m:
        out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == "3/16/2026"
    assert m.call_count == 1  # fast path, no fallback


def test_ocr_date_upscale_recovers_after_initial_miss():
    """First pass returns junk; 2x upscale pass returns the date."""
    img = Image.new("RGB", (200, 60), "white")
    side_effects = ["", "3/16/2026"]  # 1st: psm 7 fails, 2nd: upscaled psm 7 ok
    with patch(
        "ocr_ptr_pdf_converter.ocr.pytesseract.image_to_string",
        side_effect=side_effects,
    ):
        out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == "3/16/2026"


def test_ocr_date_digit_confusion_recovers():
    """All tesseract passes return I/O confused output; digit substitution
    fixes it. raw text contains both "/" and a digit → gate satisfied."""
    img = Image.new("RGB", (200, 60), "white")
    side_effects = ["3/I6/2O26", "3/I6/2O26"]  # both passes confused
    with patch(
        "ocr_ptr_pdf_converter.ocr.pytesseract.image_to_string",
        side_effect=side_effects,
    ):
        out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == "3/16/2026"


def test_ocr_date_no_slash_no_fabrication():
    """Cell with no '/' (just text) must not fabricate a date even if
    digit substitution would happen to produce one."""
    img = Image.new("RGB", (200, 60), "white")
    # "REV CORP" has no slash → fallback step 3's gate blocks it.
    side_effects = ["REV CORP", "REV CORP"]
    with patch(
        "ocr_ptr_pdf_converter.ocr.pytesseract.image_to_string",
        side_effect=side_effects,
    ):
        out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == ""


def test_ocr_date_no_digit_no_fabrication():
    """Raw text with '/' but no digit (unlikely but possible) must not
    fabricate a date through digit substitution."""
    img = Image.new("RGB", (200, 60), "white")
    side_effects = ["a/b/c", "a/b/c"]
    with patch(
        "ocr_ptr_pdf_converter.ocr.pytesseract.image_to_string",
        side_effect=side_effects,
    ):
        out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ocr.py -v -k "ocr_date_fast_path or ocr_date_upscale or ocr_date_digit_confusion or ocr_date_no_slash or ocr_date_no_digit"`

Expected: `test_ocr_date_fast_path_unchanged` PASS (current behavior), the other four FAIL — they exercise paths that don't exist yet.

- [ ] **Step 3: Implement the fallback chain**

Edit `src/ocr_ptr_pdf_converter/ocr.py`.

(a) Add a new import alongside the existing `from PIL.Image import Image` line. The existing import imports the *class*, not the module, and we need the module for `Resampling.LANCZOS`:

```python
from PIL import Image as PILImage
```

(b) Add module-level constants near the existing `_DATE_RE`:

```python
# OCR digit confusions: characters tesseract commonly emits in place of
# digits when a date cell is degraded. Applied only when a '/' AND a
# pre-existing digit are both present in the raw text — prevents
# fabricating a date out of pure text.
_DIGIT_CONFUSIONS = str.maketrans(
    {"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"}
)
_HAS_DIGIT_RE = re.compile(r"\d")
```

(c) Replace the `CellKind.DATE` branch in `ocr_cell` with:

```python
    if kind is CellKind.DATE:
        # Step 1: psm 7 fast path.
        text = pytesseract.image_to_string(image, config="--psm 7")
        m = _DATE_RE.search(text)
        if m:
            return m.group(0)
        # Step 2: 2x upscale retry. Date cells are small; upscaling rescues
        # marginal scans. Mirrors the asset-cell strategy in cli.py.
        upscaled = image.resize(
            (image.width * 2, image.height * 2),
            PILImage.Resampling.LANCZOS,
        )
        text2 = pytesseract.image_to_string(upscaled, config="--psm 7")
        m = _DATE_RE.search(text2)
        if m:
            return m.group(0)
        # Step 3: digit-confusion substitution on the raw text. Gated by
        # presence of "/" AND a digit so non-date text can't be fabricated.
        for raw in (text2, text):
            if "/" in raw and _HAS_DIGIT_RE.search(raw):
                fixed = raw.translate(_DIGIT_CONFUSIONS)
                m = _DATE_RE.search(fixed)
                if m:
                    return m.group(0)
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ocr.py -v -k "ocr_date_fast_path or ocr_date_upscale or ocr_date_digit_confusion or ocr_date_no_slash or ocr_date_no_digit"`

Expected: 5 PASS.

- [ ] **Step 5: Run the full ocr test file**

Run: `uv run pytest tests/test_ocr.py -v`

Expected: all PASS, no regressions.

- [ ] **Step 6: Run ruff + mypy**

Run: `uv run ruff check src/ocr_ptr_pdf_converter/ocr.py tests/test_ocr.py && uv run mypy src/ocr_ptr_pdf_converter/ocr.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/ocr_ptr_pdf_converter/ocr.py tests/test_ocr.py
git commit -m "$(cat <<'EOF'
feat(ocr): date OCR fallback chain — 2x upscale + digit-confusion substitution

When the psm 7 strict-regex fast path misses on a date cell, retry on a 2x
upscale, then on the raw text after applying OCR digit confusions
(l/I/| → 1, O/o → 0, S → 5, B → 8). The digit-confusion step is gated
by presence of both '/' and a digit so non-date text cannot be fabricated.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Fix 1 Phase A — Diagnostic probe for tx-mark drift

**Files:**
- Create: `scripts/probe_tx_marks.py`

**Background:** Before changing `cli.py`, verify the hypothesis that PURCHASE column ink baseline is consistently higher than SALE/PARTIAL_SALE/EXCHANGE on the 6 tx-drift rows. Acceptance gate: ≥4 of 6 rows must show "PURCHASE wins on raw density, SALE wins on baseline-subtracted density." If the gate fails, stop and re-diagnose before any cli.py change.

The 6 tx-drift rows from `diagnose_golden.py` output:
- `('SP', 'ABBOTT LABORATORIES', 'Sale', '3/4/2026', 'A')`
- `('SP', 'HILTON WORLDWIDE HLDGS INC', 'Sale', '3/12/2026', 'B')`
- `('SP', 'AON PLC SHS CL A', 'Sale', '3/12/2026', 'B')`
- `('SP', 'PLEXUS CORP', 'Sale', '3/4/2026', 'C')`
- `('SP', 'LPL FINANCIAL HOLDINGS INC', 'Sale', '3/2/2026', 'C')`
- `('SP', 'HEALTHPEAK PROPERTIES INC', 'Sale', '3/23/2026', 'D')`

The probe must reuse the OCR pipeline so densities match what `_process_page` sees. Modeled on `scripts/probe_orphan_merges.py` (existing). Reference that file for the standard probe scaffolding.

- [ ] **Step 1: Read the existing probe to copy its scaffolding**

Run: `cat scripts/probe_orphan_merges.py | head -80`

This shows how the existing probe loads the fixture, runs OCR per page, and pulls per-cell densities. The new probe follows the same pattern, but instead of dumping orphan-merge candidates it dumps tx-mark densities for rows whose extracted asset matches the 6 known SALE rows.

- [ ] **Step 2: Write the probe**

Create `scripts/probe_tx_marks.py`:

```python
"""Diagnostic probe for batch 5 Fix 1 Phase A.

For each page of the fixture, runs the OCR pipeline up to the point where
per-row densities are available, then for every row whose normalized asset
matches one of the 6 known SALE-drift assets, dumps:
- raw densities for PURCHASE, SALE, PARTIAL_SALE, EXCHANGE columns
- per-column baseline (min(median, P25) over all rows on that page)
- baseline-subtracted densities
- raw winner vs. baseline-subtracted winner

Usage:
    uv run python scripts/probe_tx_marks.py
"""

from __future__ import annotations

from pathlib import Path

from ocr_ptr_pdf_converter.cli import (
    _compute_col_baselines,
    _crop_binary,
    _crop_pil,
    _kind_for_cell,
    _orient_and_grid,
    _resolve_roles,
)
from ocr_ptr_pdf_converter.extract import ColumnRole, _normalize_asset
from ocr_ptr_pdf_converter.ocr import ink_density, ocr_cell
from ocr_ptr_pdf_converter.render import render_pdf

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "9115728.pdf"

# The 6 expected-SALE rows from diagnose_golden.py (asset uppercased).
TARGET_ASSETS = {
    "ABBOTT LABORATORIES",
    "HILTON WORLDWIDE HLDGS INC",
    "AON PLC SHS CL A",
    "PLEXUS CORP",
    "LPL FINANCIAL HOLDINGS INC",
    "HEALTHPEAK PROPERTIES INC",
}

TX_MARK_ROLES = (
    ColumnRole.PURCHASE,
    ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE,
    ColumnRole.EXCHANGE,
)


def main() -> None:
    images = render_pdf(FIXTURE, dpi=300)
    raw_winners_purchase = 0
    baseline_winners_sale = 0
    confirmed_rows = 0
    matched_rows = 0

    for page_no, img in enumerate(images, start=1):
        rotation, oriented, binary, grid = _orient_and_grid(img)
        if not grid.rows or not grid.cols:
            continue
        roles = _resolve_roles(grid, oriented)
        col_widths = [x1 - x0 for x0, x1 in grid.cols]

        tx_col_indices = [i for i, r in enumerate(roles) if r in TX_MARK_ROLES]
        tx_col_index_to_role = {i: roles[i] for i in tx_col_indices}
        if not tx_col_indices:
            continue

        # Pass 1: collect per-row densities and per-row OCR text for asset col.
        all_row_densities: list[list[float]] = []
        per_row_assets: list[str] = []

        for y0, y1 in grid.rows[1:]:
            row_densities: list[float] = []
            asset_text = ""
            for (x0, x1), role, width in zip(
                grid.cols, roles, col_widths, strict=True
            ):
                rect = (x0, y0, x1, y1)
                bin_crop = _crop_binary(binary, rect)
                if x1 - x0 <= 1 or y1 - y0 <= 1:
                    row_densities.append(0.0)
                    continue
                row_densities.append(
                    ink_density(bin_crop) if bin_crop.size else 0.0
                )
                if role is ColumnRole.ASSET:
                    crop = _crop_pil(oriented, rect)
                    if crop.width > 1 and crop.height > 1:
                        kind = _kind_for_cell(role, width)
                        asset_text = ocr_cell(crop, bin_crop, kind)
            all_row_densities.append(row_densities)
            per_row_assets.append(asset_text)

        # Compute baselines (pivot to per-col).
        n_cols = len(grid.cols)
        densities_per_col: list[list[float]] = [[] for _ in range(n_cols)]
        for r in all_row_densities:
            for i in range(n_cols):
                densities_per_col[i].append(r[i] if i < len(r) else 0.0)
        baselines = _compute_col_baselines(densities_per_col)

        for row_idx, (densities, asset_text) in enumerate(
            zip(all_row_densities, per_row_assets, strict=True)
        ):
            normalized = _normalize_asset(asset_text).upper()
            if normalized not in TARGET_ASSETS:
                continue
            matched_rows += 1
            print(f"\n=== page {page_no} row {row_idx}  asset={normalized} ===")
            raw = {tx_col_index_to_role[i].name: densities[i] for i in tx_col_indices}
            adj = {
                tx_col_index_to_role[i].name: max(0.0, densities[i] - baselines[i])
                for i in tx_col_indices
            }
            base = {tx_col_index_to_role[i].name: baselines[i] for i in tx_col_indices}
            print(f"  raw      : {raw}")
            print(f"  baseline : {base}")
            print(f"  adjusted : {adj}")
            raw_winner = max(raw, key=lambda k: raw[k])
            adj_winner = max(adj, key=lambda k: adj[k])
            print(f"  winner raw      : {raw_winner}")
            print(f"  winner adjusted : {adj_winner}")
            if raw_winner == "PURCHASE":
                raw_winners_purchase += 1
            if adj_winner == "SALE":
                baseline_winners_sale += 1
            if raw_winner == "PURCHASE" and adj_winner == "SALE":
                confirmed_rows += 1

    print("\n" + "=" * 60)
    print(f"matched rows           : {matched_rows} / {len(TARGET_ASSETS)} expected")
    print(f"raw PURCHASE winners   : {raw_winners_purchase}")
    print(f"baseline SALE winners  : {baseline_winners_sale}")
    print(f"per-row confirmations  : {confirmed_rows}  (raw=PURCHASE AND adj=SALE)")
    print()
    print(
        "ACCEPTANCE GATE:\n"
        f"  Hypothesis is confirmed for {confirmed_rows} of {matched_rows} rows.\n"
        "  PROCEED to Fix 1 Phase B only if >= 4 of the 6 target rows confirm.\n"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the probe**

Run: `uv run python scripts/probe_tx_marks.py 2>&1 | tee /tmp/batch5_probe.txt`

Expected: 6 per-row blocks, each showing raw and adjusted densities and winners. The summary at the end reports matched rows count and the overall raw-PURCHASE / baseline-SALE counts.

- [ ] **Step 4: Verify the acceptance gate by hand**

Open `/tmp/batch5_probe.txt`. Count the rows where `winner raw == PURCHASE` AND `winner adjusted == SALE`. Record the count.

**ACCEPTANCE GATE:**
- If count ≥ 4: hypothesis confirmed. Continue to Task 4 (Phase B).
- If count < 4: hypothesis is wrong (or partially wrong). STOP. Do not modify cli.py. Report findings to the user with the per-row block that failed the gate, and ask whether to re-investigate or scope-down the batch.

- [ ] **Step 5: Commit the probe (regardless of gate outcome)**

```bash
git add scripts/probe_tx_marks.py
git commit -m "$(cat <<'EOF'
chore: add tx-mark drift probe for batch 5 Fix 1 Phase A

Dumps per-row raw densities, baselines, and baseline-subtracted densities
for the 6 known SALE-drift rows. Used to verify the hypothesis that
PURCHASE column carries higher baseline ink than SALE before wiring
baseline subtraction into _process_page.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Fix 1 Phase B — Wire baseline subtraction in _process_page

**Precondition:** Task 3 acceptance gate passed (≥4 of 6 rows confirmed). If the gate failed, this task does not run.

**Files:**
- Modify: `src/ocr_ptr_pdf_converter/cli.py:217-274` (the `_process_page` function)
- Test: `tests/test_marks.py` (append two integration tests for the new wiring)

**Background:** `_compute_col_baselines` and `_is_single_tx_page` are already defined in cli.py and unit-tested in `tests/test_marks.py`, but `_process_page` does not call them. The fix is to restructure the per-row loop into two passes:
1. Pass 1 (existing): per-row OCR + raw densities, accumulated into a list-of-lists.
2. Pass 2 (new): pivot to per-column densities, compute baselines, gate by `_is_single_tx_page`, and apply baseline-subtracted densities to `_resolve_competing_marks` for tx-mark roles.

Amount-mark resolution stays unchanged for this batch (Cluster B is deferred).

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_marks.py`. These tests patch already-existing cli internals (`_orient_and_grid`, `_resolve_roles`, `ocr_cell`, `ink_density`) so they exercise only the new two-pass logic in `_process_page`.

```python
import numpy as np
from PIL import Image as PILImage
from unittest.mock import patch

from ocr_ptr_pdf_converter.extract import ColumnRole
from ocr_ptr_pdf_converter.grid import Grid


def test_process_page_applies_tx_baseline_subtraction():
    """When PURCHASE has consistent ~0.18 bleed across rows and SALE has a
    real mark of 0.25 on row 1, baseline subtraction lets SALE win on row 1.
    Rows 0 and 2 must NOT spuriously gain a SALE/PARTIAL_SALE winner since
    they have no real mark anywhere — baseline subtraction zeros their
    PURCHASE bleed and leaves no winner above _MARK_WINNER_DENSITY."""
    from ocr_ptr_pdf_converter import cli as cli_mod

    densities_grid = [
        [0.18, 0.06, 0.04],  # row 0 — no real mark
        [0.18, 0.25, 0.05],  # row 1 — real SALE mark
        [0.18, 0.05, 0.04],  # row 2 — no real mark
    ]
    fake_image = PILImage.new("RGB", (300, 300), "white")
    fake_grid = Grid(
        rows=[(0, 10), (10, 30), (30, 50), (50, 70)],
        cols=[(0, 100), (100, 200), (200, 300)],
    )
    fake_roles = [ColumnRole.PURCHASE, ColumnRole.SALE, ColumnRole.PARTIAL_SALE]
    bin_arr = np.full((300, 300), 255, dtype=np.uint8)
    density_iter = iter(d for row in densities_grid for d in row)

    with (
        patch.object(
            cli_mod,
            "_orient_and_grid",
            return_value=(0, fake_image, bin_arr, fake_grid),
        ),
        patch.object(cli_mod, "_resolve_roles", return_value=fake_roles),
        patch.object(cli_mod, "ocr_cell", side_effect=lambda *a, **k: ""),
        patch.object(
            cli_mod,
            "ink_density",
            side_effect=lambda b: next(density_iter),
        ),
    ):
        result, _ = cli_mod._process_page(fake_image, page_number=1)

    rows = result.rows
    assert len(rows) == 3
    assert rows[1].transaction_type == "SALE", (
        f"row 1 expected SALE, got {rows[1].transaction_type!r}"
    )
    assert rows[0].transaction_type == "", (
        f"row 0 should have no tx winner after baseline subtraction, "
        f"got {rows[0].transaction_type!r}"
    )
    assert rows[2].transaction_type == "", (
        f"row 2 should have no tx winner after baseline subtraction, "
        f"got {rows[2].transaction_type!r}"
    )


def test_process_page_skips_baseline_on_single_tx_page():
    """All 5 rows have PURCHASE density ~0.20 (real marks across the board).
    _is_single_tx_page returns True → baseline subtraction is skipped →
    every row wins PURCHASE."""
    from ocr_ptr_pdf_converter import cli as cli_mod

    densities_grid = [
        [0.20, 0.05, 0.04],
        [0.21, 0.04, 0.05],
        [0.19, 0.05, 0.04],
        [0.20, 0.06, 0.05],
        [0.22, 0.04, 0.04],
    ]
    fake_image = PILImage.new("RGB", (300, 300), "white")
    fake_grid = Grid(
        rows=[(0, 10)] + [(10 + i * 20, 30 + i * 20) for i in range(5)],
        cols=[(0, 100), (100, 200), (200, 300)],
    )
    fake_roles = [ColumnRole.PURCHASE, ColumnRole.SALE, ColumnRole.PARTIAL_SALE]
    bin_arr = np.full((300, 300), 255, dtype=np.uint8)
    density_iter = iter(d for row in densities_grid for d in row)

    with (
        patch.object(
            cli_mod,
            "_orient_and_grid",
            return_value=(0, fake_image, bin_arr, fake_grid),
        ),
        patch.object(cli_mod, "_resolve_roles", return_value=fake_roles),
        patch.object(cli_mod, "ocr_cell", side_effect=lambda *a, **k: ""),
        patch.object(
            cli_mod,
            "ink_density",
            side_effect=lambda b: next(density_iter),
        ),
    ):
        result, _ = cli_mod._process_page(fake_image, page_number=1)

    assert len(result.rows) == 5
    for i, row in enumerate(result.rows):
        assert row.transaction_type == "PURCHASE", (
            f"row {i} expected PURCHASE on single-tx page, "
            f"got {row.transaction_type!r}"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_marks.py -v -k "applies_tx_baseline or skips_baseline_on_single_tx"`

Expected: 1 FAIL (`applies_tx_baseline` — current code without baseline subtraction would let PURCHASE win row 1 since 0.18 > 0.25 is false but rows 0 and 2 would spuriously pick PURCHASE since 0.18 > _MARK_WINNER_DENSITY 0.05; the assertion `row 0 should have no tx winner` would fail), 1 PASS or FAIL depending on current behavior on `skips_baseline_on_single_tx` (likely PASS — current code already lets PURCHASE win when raw densities are highest).

If the `skips_baseline_on_single_tx` test passes already, that's fine — it acts as a regression guard for the baseline-skip path.

- [ ] **Step 3: Implement the two-pass baseline subtraction in `_process_page`**

Edit `src/ocr_ptr_pdf_converter/cli.py`. Locate the `_process_page` function. The current loop is:

```python
    cell_rows: list[list[str]] = []
    date_densities: list[float] = []
    for y0, y1 in grid.rows[1:]:
        row_texts: list[str] = []
        densities: list[float] = []
        for (x0, x1), role, width in zip(grid.cols, roles, col_widths, strict=True):
            ...
            densities.append(ink_density(bin_crop) if bin_crop.size else 0.0)

        # Pick a single tx-type mark winner per row to suppress multi-mark noise.
        _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
        # Same for amount: only one A..K cell can be the "real" mark.
        _resolve_competing_marks(
            row_texts, densities, roles, frozenset({ColumnRole.AMOUNT})
        )
        cell_rows.append(row_texts)
        date_densities.append(densities[date_tx_idx] if date_tx_idx is not None else 0.0)
```

Replace with a two-pass restructure:

```python
    # Pass 1: per-row OCR. Collect raw densities and texts; defer mark
    # resolution until baselines are available.
    cell_rows: list[list[str]] = []
    all_row_densities: list[list[float]] = []
    date_densities: list[float] = []
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

        cell_rows.append(row_texts)
        all_row_densities.append(densities)
        date_densities.append(
            densities[date_tx_idx] if date_tx_idx is not None else 0.0
        )

    # Pass 2: compute per-column baselines and resolve competing marks.
    # For tx-marks, subtract column baselines unless the page is dominated
    # by a single tx-type winner (preserves correctness on uniform pages).
    n_cols = len(grid.cols)
    densities_per_col: list[list[float]] = [[] for _ in range(n_cols)]
    for r in all_row_densities:
        for i in range(n_cols):
            densities_per_col[i].append(r[i] if i < len(r) else 0.0)
    baselines = _compute_col_baselines(densities_per_col)

    tx_mark_col_indices = [
        i for i, r in enumerate(roles) if r in _TX_MARK_ROLE_SET
    ]
    skip_tx_baseline = _is_single_tx_page(all_row_densities, tx_mark_col_indices)

    for row_texts, densities in zip(cell_rows, all_row_densities, strict=True):
        if skip_tx_baseline or not tx_mark_col_indices:
            tx_densities = densities
        else:
            tx_densities = list(densities)
            for i in tx_mark_col_indices:
                tx_densities[i] = max(0.0, densities[i] - baselines[i])
        _resolve_competing_marks(row_texts, tx_densities, roles, _TX_MARK_ROLE_SET)
        # Amount-mark resolution: unchanged (Cluster B deferred).
        _resolve_competing_marks(
            row_texts, densities, roles, frozenset({ColumnRole.AMOUNT})
        )
```

- [ ] **Step 4: Run the marks tests to verify**

Run: `uv run pytest tests/test_marks.py -v`

Expected: all PASS, including the existing 4 unit tests for `_compute_col_baselines` and `_is_single_tx_page` (no regressions in those — we only added wiring, did not change the helper signatures).

- [ ] **Step 5: Run the full unit test suite (no golden test yet — golden runs in the final task)**

Run: `uv run pytest --ignore=tests/test_golden.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Run ruff + mypy**

Run: `uv run ruff check src/ocr_ptr_pdf_converter/cli.py tests/test_marks.py && uv run mypy src/ocr_ptr_pdf_converter/cli.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/ocr_ptr_pdf_converter/cli.py tests/test_marks.py
git commit -m "$(cat <<'EOF'
feat(cli): wire mark baseline subtraction for tx-mark resolution

Restructures _process_page into two passes: per-row OCR + raw densities,
then per-column baseline computation and mark resolution. For tx-marks
(PURCHASE/SALE/PARTIAL_SALE/EXCHANGE), subtracts column baselines before
picking the winner — counteracts vertical-text label bleed that previously
inflated PURCHASE column above faint real SALE marks. Skipped on pages
where _is_single_tx_page returns True (uniform tx type → baseline IS
the mark). Amount-mark resolution unchanged (Cluster B deferred).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Verification — diagnose, golden test, regression check

**Files:**
- No code changes; this task verifies the three fixes together against the fixture.

**Background:** Per `CLAUDE.md`, the golden test takes ~10 minutes; run it as a background task and poll for completion via the output file. The diagnostic is fast (uses a cached `output/9115728_actual.md`, but we want to refresh it after fixes land).

- [ ] **Step 1: Refresh actual.md and run diagnose**

Run (foreground, ~10 min OCR):

```bash
uv run python scripts/diagnose_golden.py --refresh > /tmp/batch5_post.txt 2>&1
```

Open `/tmp/batch5_post.txt`. Verify:
- `exact matches` ≥ 87/98 (≥88.8%)
- `tx_only_drift` count drops by ≥4 (Fix 1 acceptance)
- `two_field_drift(asset+date)` no longer contains MAYS or EQT
- `asset_only_drift` no longer contains PTC INC
- `two_field_drift(amount+date)` for LOS ANGELES: date populated (amount drift may remain, deferred)

If the exact-match count is below 87, do not proceed — report findings to the user with the new diagnose histogram.

- [ ] **Step 2: Run the golden test as a background task**

Run:

```bash
uv run pytest tests/test_golden.py -v > /tmp/batch5_golden.txt 2>&1; echo "DONE_$?" >> /tmp/batch5_golden.txt &
```

Poll for completion (no naive sleep chain, per `CLAUDE.md`):

```bash
until grep -q "^DONE_" /tmp/batch5_golden.txt 2>/dev/null; do sleep 30; done
```

Open `/tmp/batch5_golden.txt`. Verify the test passes (`DONE_0`) and the reported exact-match rate is ≥ 89% (≥87/98).

- [ ] **Step 3: Section-header regression check**

Open `output/9115728_actual.md` (refreshed in Step 1) and grep for the genuine section headers:

```bash
grep -n "LLM FAMILY\|LINDA MAYS MCCAUL" output/9115728_actual.md
```

Expected: each row appears as a section header (the `is_section_header` markdown convention is `## <text>` or a header-style row — verify by comparing to `tests/fixtures/9115728_expected.md` for the same rows). They must NOT appear as ordinary table rows.

If a section header has regressed to a normal row, this is a Fix 1 regression — investigate before continuing.

- [ ] **Step 4: Run the entire test suite one more time**

Run: `uv run pytest -v`

Expected: every test passes, including the golden test. No skips, no errors.

- [ ] **Step 5: Run ruff + mypy across the whole project**

Run: `uv run ruff check . && uv run mypy src`

Expected: no errors.

- [ ] **Step 6: Final summary commit (docs/notes only — code commits already happened)**

If `tests/fixtures/9115728_expected.md` has not changed (it should not — the fixture is the source of truth) and `output/9115728_actual.md` is gitignored, no extra files need committing. Just verify the branch is clean:

```bash
git status
```

Expected: working tree clean. All four code commits (Tasks 1, 2, 3, 4) are in place.

- [ ] **Step 7: Push the branch and open a PR**

```bash
git push -u origin feat/accuracy-batch-5
gh pr create --title "feat: accuracy batch 5 — mark baseline + asset trim + date OCR fallback" --body "$(cat <<'EOF'
## Summary
- Wires `_compute_col_baselines` / `_is_single_tx_page` into `_process_page` so tx-mark resolution subtracts per-column baseline ink (recovers SALE rows previously lost to PURCHASE column bleed).
- Extends `_normalize_asset` trim to handle three OCR-bleed patterns batch 4 deferred: `<digit> <letter>`, `- <letter>`, `<real-suffix> ; <2-3 letters>`.
- Adds a date OCR fallback chain (2× upscale + digit-confusion substitution, gated by `/` and digit) so dates blocked by minor OCR confusions like `3/I6/2O26` are recovered.

## Test plan
- [ ] `uv run pytest -v` — all unit + integration tests pass
- [ ] `uv run python scripts/diagnose_golden.py` — exact-match ≥ 87/98 (≥89%)
- [ ] `uv run pytest tests/test_golden.py -v` — golden test passes at ≥89%
- [ ] Genuine section headers (LLM FAMILY ×2, LINDA MAYS MCCAUL ×5) still classify as section headers
- [ ] `uv run ruff check . && uv run mypy src` clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL when done.
