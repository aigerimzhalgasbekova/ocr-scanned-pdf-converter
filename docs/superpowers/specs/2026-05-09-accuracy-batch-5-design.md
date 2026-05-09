# Accuracy Batch 5 — Mark Baseline Subtraction + Asset Trim Extension + Date OCR Fallback

**Goal:** Push golden-test exact-match rate from 80.6% (79/98) to ≥89% (≥87/98), with a stretch ceiling of ~91% if all three fixes land cleanly by fixing the three highest-yield independent failure clusters surfaced by `scripts/diagnose_golden.py`.

**Status:** Brainstormed 2026-05-09 against `tests/fixtures/9115728.pdf` (still the only fixture). Builds on batch 4's date-density signal; targets clusters batch 4 explicitly deferred.

---

## Problem

A fresh `diagnose_golden.py --refresh` against the post-batch-4 build shows:

```
exact matches: 79/98 = 80.6%
unmatched expected: 19
unmatched actual  : 18
extra_actual_rows : 0
```

19 mismatching rows, grouped by root cause:

| Cluster | Count | Root cause hypothesis |
|---|---|---|
| **A. SALE→PURCHASE drift** | 6 | All `SALE` rows extracted as `PURCHASE`; same-direction systemic bias |
| **B. Amount-mark wrong** | 3 | Off by 1+ columns in A–K (deferred to batch 6) |
| **C. Asset trailing noise** | 3 | OCR junk after company suffix not trimmed by current rule |
| **D. Empty date string** | 4+ | `date_density` correctly flags as ROW, but `_DATE_RE` regex misses on degraded OCR |
| **E/F/G. Page-3 alignment** | ~4 | Speculative; deferred to batch 6 |

This batch addresses A, C, and D. B and E/F/G are deferred — each requires its own investigation.

### Why these three clusters together

- **A (+6)** is the highest-yield cluster on the table and has a strong, testable hypothesis (Fix 1).
- **C (+3 direct)** unblocks 2 rows in cluster D, so C and D compound — fixing both stacks gain.
- **D (+1 alone, +3 stacked on C)** is a bounded change in `ocr.py` only.

Each fix lives in exactly one module. None depend on the others. Failure of one does not block the others.

### Cluster A — verified hypothesis seed

`cli.py:180-191` defines `_compute_col_baselines` and `cli.py:194-214` defines `_is_single_tx_page`. Both have unit tests in `tests/test_marks.py` but **neither is called from `_process_page`**. Git log confirms (`9c6ec74 feat(cli): add _compute_col_baselines and _is_single_tx_page helpers for mark baseline subtraction`) the helpers were added *for* mark baseline subtraction and never wired up.

The systemic SALE→PURCHASE drift is consistent with the failure mode these helpers exist to fix: the PURCHASE column accumulates baseline ink (vertical-text label bleed from the form header, scan rules) at a higher level than SALE/PARTIAL_SALE/EXCHANGE. Without baseline subtraction, `_resolve_competing_marks` picks PURCHASE as the density winner whenever the actual SALE mark is faint.

We commit to verifying the hypothesis in implementation Phase A (probe) before changing any code path. Acceptance gate: ≥4 of 6 SALE rows must show "PURCHASE wins on raw density, SALE wins after baseline subtraction" before proceeding to Phase B.

---

## Design

Three independent fixes. Implementation order: **Fix 2 → Fix 3 → Fix 1** (ascending risk and surface area).

### Fix 1 — Mark baseline subtraction (Cluster A, target +6)

**Phase A — Diagnostic probe** (`scripts/probe_tx_marks.py`, new):

For each of the 6 tx-type-drift rows, dump:
- Page number, row index, expected vs actual `tx_type`
- Per-tx-mark column raw ink density (PURCHASE, SALE, PARTIAL_SALE, EXCHANGE)
- Per-tx-mark column baseline (from `_compute_col_baselines` over all rows on that page)
- Per-tx-mark column baseline-subtracted density
- The winner under each scheme

**Acceptance gate:** ≥4 of the 6 rows must show "PURCHASE wins on raw, SALE wins on baseline-subtracted". If <4, the hypothesis is wrong; stop, report, ask before any cli.py change.

**Phase B — Wire baseline subtraction in `cli.py:_process_page`:**

Today the per-row loop captures `densities[]` but discards them after `_resolve_competing_marks`. Change to:

1. Append each row's `densities` to a page-wide `all_row_densities: list[list[float]]` *before* mark resolution.
2. After the row loop completes, pivot to `densities_per_col` and call `_compute_col_baselines(densities_per_col)` to get one baseline per column.
3. For tx-mark columns: compute `tx_mark_col_indices` (positions of any role in `_TX_MARK_ROLE_SET`). Call `_is_single_tx_page(all_row_densities, tx_mark_col_indices)`:
   - If True (≥80% of rows share the same tx-mark winner) → skip baseline subtraction; the baseline IS the mark on this page (e.g. an all-PURCHASE page).
   - If False → for each row, build an adjusted-densities array where `adjusted[i] = max(0, densities[i] - baselines[i])` for `i` in `tx_mark_col_indices`, leave others unchanged. Pass adjusted densities to `_resolve_competing_marks`.
4. Amount-mark columns are **out of scope** for this batch (Cluster B is deferred). Existing behavior preserved.

**Why two passes:** baselines are page-global. We need every row's density before computing them, so mark resolution moves to a second pass. The existing per-row OCR pass is unchanged.

**Risk: multiple root causes.** If Cluster A is partly baseline-bleed and partly role-mismatch (column position drift on certain pages), Phase A may show 3-4/6 confirmation. The ≥4/6 gate ensures the fix ships only when the hypothesis is the dominant cause; partial confirmation still yields most of the value, and the unconfirmed rows fall to a future investigation.

### Fix 2 — Asset trim extension (Cluster C, target +3)

Current `_normalize_asset` (`extract.py:248-306`) handles trailing single digits when prev token is in `_NUMERIC_TAIL_ANCHORS` (`INV`, `COM`, `USD1`). Three patterns from the diagnostic still leak through:

| Pattern | Actual | Expected |
|---|---|---|
| `<digit> <single-letter>` | `MAYS ALLOCATE LP 7 A` | `MAYS ALLOCATE LP` |
| `- <single-letter>` | `EQT CORP COM - J` | `EQT CORP COM` |
| `; <2-3 letters>` | `PTC INC ; BD` | `PTC INC` |

**Approach** in the existing right-side `while tokens:` strip loop:

1. Treat standalone `;` as a noise token (single-char, not alphanumeric, not protected). Verify whether `_NOISE_TOKEN_RE` already matches it; if not, extend.
2. Standalone `-` between content and trailing junk: same treatment as `;`. Already handled in some cases by `_TRAIL_NOLETTERS_RE`; verify and extend coverage.
3. Single-letter-after-digit pop: when a trailing single A–Z letter is preceded by what *would* have been a popped digit (i.e. the digit is not protected by `_NUMERIC_TAIL_ANCHORS`), pop the letter first, then re-evaluate the digit on the next loop iteration. This handles `LP 7 A`: pop `A`, then pop `7`, then stop at `LP`.
4. `; <2-3 letters>` strip: when last token is 2–3 alphabetic characters and previous is `;`, pop both — but only when the token before `;` is in `_REAL_SHORT_SUFFIXES` (so we don't damage assets that legitimately end in `; XYZ`).

The exact code shape will fall out of inspecting `_NOISE_TOKEN_RE` and `_TRAIL_NOLETTERS_RE` during implementation; the spec commits to the *behavior* above, not a specific regex.

**Regression guards (must not break):**
- `CEDAR HOLDINGS LP INV 1292` → unchanged (`INV` in `_NUMERIC_TAIL_ANCHORS`)
- `GENUINE PARTS CO COM USD1 00` → unchanged (`USD1` in `_NUMERIC_TAIL_ANCHORS`)
- `INTUIT INC` → unchanged (B4 trim already strips trailing 7)
- `CL A`, `SHS CL A` → unchanged (existing `_AK_LETTERS` branch)
- `Mays Allocate 2025 LP` → unchanged (B4 trim already strips trailing 7)

### Fix 3 — Date OCR fallback chain (Cluster D, target +1 alone, +3 stacked on Fix 2)

Current `ocr.py:ocr_cell` for `CellKind.DATE`: single `psm 7` pass, strict `_DATE_RE` regex; empty string on miss.

**Fallback chain — only enters when initial pass returns empty:**

```
1. psm 7 pass → regex match           (current behavior)
2. if empty: 2× upscale + psm 7 → regex match
3. if still empty AND raw text contains "/" AND raw text contains a digit:
     apply OCR digit-confusion substitutions to raw text
     (l/I/| → 1, O/o → 0, S → 5, B → 8)
     → regex match
4. if still empty: return ""
```

**Why 2× upscale (step 2):** mirrors the asset-cell strategy already in `cli.py:_process_page` (`_looks_collapsed` retry). Date cells are smaller than asset cells; 2× helps small or scan-degraded prints.

**Why digit confusion (step 3):** common failure mode is `3/16/2026` → `3/I6/2026` or `3/I6/2O26` — strict regex fails on `I` for `1` and `O` for `0`. Substituting before re-match recovers these.

**Why the dual gate on step 3 (`/` AND digit):** prevents fabricating a date from non-date text. A cell containing `"REV CORP"` (no `/`) cannot become a fake date. A cell containing `"$1234.56"` (no `/`) cannot become a fake date. Both gates must hold.

**Why no psm 6/8/13 passes:** the date cell is structurally `<digits>/<digits>/<digits>`; psm 7 (single line) is the right mode. Additional psm modes increase cost without targeting the failure modes seen in the diagnostic.

The 2× upscale is implemented inside `ocr.py` (the function receives the PIL image; resize is local to the fallback). No `cli.py` change.

---

## Tests

### Fix 1 (`tests/test_marks.py` — file already exists)

1. Synthetic 4-row page with PURCHASE column carrying high baseline (e.g. 0.04 across all rows) and SALE column with mid mark (e.g. 0.07 on one row, 0.02 elsewhere). Assert SALE wins on the marked row after baseline subtraction. Without subtraction PURCHASE would win.
2. Single-tx page (all 6 rows have PURCHASE > 0.06, SALE ≈ 0.01). Assert `_is_single_tx_page` returns True and the baseline-subtraction branch is skipped, PURCHASE still wins on every row.
3. Mixed page (no clear majority). Assert `_is_single_tx_page` returns False and baseline subtraction runs.
4. Regression: existing `_resolve_competing_marks` cases still pass with the new pre-processing in place.

### Fix 2 (`tests/test_extract.py`)

Positive cases:
1. `_normalize_asset("Mays Allocate LP 7 A")` → `"Mays Allocate LP"`
2. `_normalize_asset("EQT CORP COM - J")` → `"EQT CORP COM"`
3. `_normalize_asset("PTC INC ; BD")` → `"PTC INC"`

Regression guards:
4. `_normalize_asset("Cedar Holdings LP INV 1292")` → unchanged
5. `_normalize_asset("GENUINE PARTS CO COM USD1 00")` → unchanged
6. `_normalize_asset("INTUIT INC")` → unchanged
7. `_normalize_asset("AON PLC SHS CL A")` → unchanged

### Fix 3 (`tests/test_ocr.py`)

By mocking `pytesseract.image_to_string` (no real images needed):

1. First call returns `""`, second call (after upscale) returns `"3/16/2026"` → assert returned `"3/16/2026"`.
2. Both psm 7 passes return `"3/I6/2O26"` (no `/` lost, but I/O confusions): assert digit-confusion path returns `"3/16/2026"`.
3. Both passes return `"REV CORP"` (no `/`, no digits): assert returns `""`.
4. Both passes return `"$1234.56"` (digits, no `/`): assert returns `""`.
5. First call returns `"3/16/2026"` directly: assert returned `"3/16/2026"` (current behavior preserved; fast path doesn't enter fallback).

---

## Verification

1. **Diagnostic probe (Phase A of Fix 1):** Run `scripts/probe_tx_marks.py`. Confirm acceptance gate (≥4 of 6 SALE rows show baseline-subtraction recovery). If gate fails, stop and re-diagnose.
2. **`scripts/diagnose_golden.py --refresh`** after all three fixes land. Expect:
   - `tx_only_drift` drops by ≥4 (Fix 1)
   - `two_field_drift(asset+date)` for MAYS, EQT resolved (Fix 2 + Fix 3 stacked)
   - `asset_only_drift` PTC INC resolved (Fix 2)
   - `two_field_drift(amount+date)` LOS ANGELES: date populated (Fix 3); amount drift remains (deferred)
3. **Golden test:** `uv run pytest tests/test_golden.py -v` (~10 min — run as background task, check completion via output file per CLAUDE.md). Target: 80.6% → ≥89% (≥87/98 exact).
4. **Section-header regression check:** diff `output/9115728_actual.md` before/after on the genuine section-header rows (LLM FAMILY × 2, LINDA MAYS MCCAUL × 5). Must remain section headers; must not regress to ROW.
5. **All existing tests:** `uv run pytest` must pass (38+ tests including B4 regression guards).

---

## Risks

**Fix 1 — single-fixture baseline tuning.** `_compute_col_baselines` is non-parametric (median/P25), so it adapts per-page; no constant to tune. But it is unverified on a second fixture. If a future fixture has a page where one tx-type genuinely covers most rows AND the loser column carries less bleed than the winner, `_is_single_tx_page` saves correctness on that page (≥80% gate). The combined safeguard is the best we can do without a second fixture.

**Fix 1 — partial confirmation.** Phase A may show 3/6 or 5/6 instead of 6/6. The ≥4/6 gate ships at high enough confidence that the fix is the dominant cause. Rows that flip wrong (PURCHASE→SALE on a row that should stay PURCHASE) would show as new `tx_only_drift` mismatches in step 2 verification — caught before merge.

**Fix 2 — over-aggressive trim.** Single-letter-after-digit could clip a legitimate `<COMPANY> <NUMERIC SERIES> <CLASS-LETTER>` pattern like `XYZ FUND 7 A` if such an asset exists. No such asset is in the fixture; if a future PTR introduces one we will need a positive-list of class-letter contexts. The existing `_AK_LETTERS` branch already protects `CL A` style asset names.

**Fix 3 — fabricated dates.** Step 3's digit substitution could in theory turn `1/0/2025` (already passing the regex) or `2/29/2025` (invalid date the regex accepts) into output. The regex itself does not validate semantic dates; that is a pre-existing limitation, not new to this batch. The dual gate (`/` AND digit) prevents fabrication from non-date text.

---

## Out of Scope

- **Cluster B (amount mark):** off-by-many cases (D→K, C→A) suggest a different root cause (probably column-mapping in `_AMOUNT_MARK_COLS`-aware logic). Separate investigation.
- **Clusters E/F/G (page-3 alignment):** AON/CHEVRON/VANGUARD muddling looks like row-grid drift; needs its own probe.
- **Per-page dynamic `_DATE_INK_PRESENT_DENSITY`:** still gated on a second fixture, as in batch 4.
- **Date semantic validation** (rejecting `2/29/2025` etc.): pre-existing, not new.
- **Amount-mark baseline subtraction:** Fix 1's pattern would generalize, but applying it to amount marks at the same time conflates two clusters and complicates regression diagnosis. Defer.

---

## File Manifest

**Modified:**
- `src/ocr_ptr_pdf_converter/cli.py` — wire baseline subtraction for tx-marks in `_process_page` (two-pass restructure: collect all-row densities, compute baselines, apply subtraction conditionally per `_is_single_tx_page`).
- `src/ocr_ptr_pdf_converter/extract.py` — extend `_normalize_asset` right-side trim for `<digit> <letter>`, `- <letter>`, `; <2-3 letters>` patterns.
- `src/ocr_ptr_pdf_converter/ocr.py` — add fallback chain (2× upscale, digit-confusion substitution) inside `CellKind.DATE` branch of `ocr_cell`.
- `tests/test_marks.py` — add 3 new tests + regression guard.
- `tests/test_extract.py` — add 3 positive cases + 4 regression guards.
- `tests/test_ocr.py` — add 5 mocked tests for the fallback chain.

**Created:**
- `scripts/probe_tx_marks.py` — Phase A diagnostic for Fix 1; kept for future regression triage.

**Untouched:**
- `grid.py`, `schema.py`, `markdown.py`, `header.py`, `preprocess.py`, `render.py`, `orient.py`.
