# Accuracy Batch 2 — Asset Cleanup, Mark Baseline, Page 4 Recovery

**Status:** Draft for review
**Branch:** `feat/v0.2.0-tdd-pipeline` (continuation)
**Goal:** Move golden row accuracy from **68.4%** (67/98) toward **≥95%** by fixing three independent root causes uncovered by Batch 1 diagnostics.

---

## 1. Context

### 1.1 Where we are

After fixing the expected fixture in Batch 1 (the original markdown had wrong tx-types and amount codes for ~22 rows), the pipeline now matches **67/98 expected rows** against `tests/fixtures/9115728_expected.md`. The remaining 31 unmatched rows fall into three independent failure modes:

| Failure mode | Count | Root cause |
|---|---|---|
| `asset_only_drift` | 10 | OCR text quality on the asset cell (kerning, char substitution, trailing junk) |
| `missing_entirely` | 6 | Page 4 rows dropped before they reach markdown |
| `tx_only_drift` / `amount_only_drift` | 5 (2 + 3) | Wrong mark winner caused by vertical-text header bleed |
| `two_field_drift(asset+tx)` | 3 | Asset error stacks on top of wrong-Sale-Purchase mark |
| `two_field_drift(asset+date)` + `many_field_drift(3)` | 7 | Diagnostic-pairing artifacts; collapse when underlying causes are fixed |

### 1.2 Form structure (verified from screenshots)

PTR form has **18 columns**, identical layout on every page:

```
Holder | Asset | PURCHASE | SALE | EXCHANGE | Date_TX | Date_Notified | A | B | C | D | E | F | G | H | I | J | K
```

- Holder column has JT/SP/DC sub-rows; only one is checked per transaction.
- Column header text is **rotated 90° (vertical)** for the 3 TX mark columns and the 11 amount mark columns. That vertical text bleeds DOWN into every data-row crop, contributing baseline ink density per column. This is the root cause of mark-detection drift.
- The form has **no PARTIAL_SALE column** — `_TX_MARK_ROLES` in `extract.py` currently lists 4 TX roles, so `infer_roles_by_position` mislabels EXCHANGE as PARTIAL_SALE. Harmless for Purchase/Sale but should be corrected for cleanliness.

### 1.3 Out of scope for this batch

- Senate/FD form variants (PRD §8 — out of scope for v0.2.0).
- Confidence scoring (PRD §4.5 — future).
- The remaining structural row-alignment issues (`many_field_drift` artifacts that don't collapse): may need a follow-up Batch 3 if Batch 2 lands at ~85-90%.

---

## 2. Fix 1: Asset OCR cleanup

**Where:** `src/ocr_ptr_pdf_converter/extract.py::_normalize_asset` and a new `_FIX_PATTERNS` table.

**Optional re-OCR path:** `src/ocr_ptr_pdf_converter/cli.py::_process_page` adds a "high-DPI re-crop" step for asset cells whose first-pass OCR returns a suspect collapsed token.

### 2.1 Pattern-based post-processing

Apply in this order inside `_normalize_asset`:

1. **Character substitution table** for OCR confusions in alpha contexts:
   - `{` → `I`
   - `}` → `I`
   - Apply only when the offending char is adjacent to letters (avoid touching legitimate punctuation).

2. **Insert space at known boundaries** via regex rewrites:
   - `r'\bCL([A-K])\b'` → `r'CL \1'` — fixes `CLA` → `CL A`, `SHSCLA` → `SHS CL A`
   - `r'\b([A-Z]{4,})([A-Z])\b(?=\s|$)'` → `r'\1 \2'` *only* when the trailing single letter is one of `J/M/P/L/T` (common middle initials) — fixes `ARTHURJ` → `ARTHUR J`. Whitelist required so we don't break `BRD` / `INC` / `LLC`.

3. **Strip trailing single-letter junk:**
   - If the asset ends with a single A-K letter token AND the previous token is in `{INC, LP, CORP, LLC, CO, PLC, ADR, NV, AG, ETF}`, drop the trailing letter.
   - Fixes `INTUIT INC A` → `INTUIT INC` while preserving legitimate suffixes like `BAYER AG SPON ADR`.

4. **Existing `_REAL_SHORT_SUFFIXES` whitelist:** remove A-K from the always-allowed list. The new rule above replaces them with a context-sensitive check.

### 2.2 High-DPI re-OCR fallback (catastrophic kerning)

`LANSING MICHBRDWTR&LTUTILSYSREV` cannot be recovered with pattern rewrites — too many missing spaces. We re-OCR the asset cell at 2× DPI when the first-pass result triggers the heuristic:

**Trigger:** the asset string contains a token of length ≥ 15 with no internal spaces.

**Action in `cli.py`:**
```
if _looks_collapsed(first_pass_asset):
    crop = oriented.crop(asset_rect)
    upscaled = crop.resize((crop.width*2, crop.height*2), Image.LANCZOS)
    second_pass = pytesseract.image_to_string(upscaled, config="--psm 6")
    use second_pass if it has more space-separated tokens, else keep first_pass
```

**Why this is acceptable cost:** the heuristic only fires on truly collapsed rows (~1-2 per fixture). The cost is one extra tesseract call per such row.

### 2.3 Tests

Unit tests in `tests/test_extract.py`:

- `test_normalize_asset_inserts_space_in_cla` — `MASTERCARD INCORPORATED CLA` → `MASTERCARD INCORPORATED CL A`
- `test_normalize_asset_inserts_space_for_initial` — `ARTHURJ GALLAGHER & CO` → `ARTHUR J GALLAGHER & CO`
- `test_normalize_asset_strips_trailing_letter_after_inc` — `INTUIT INC A` → `INTUIT INC`
- `test_normalize_asset_keeps_real_suffix` — `BAYER AG SPON ADR` unchanged
- `test_normalize_asset_substitutes_curly_brace_for_i` — `LP {NV` → `LP INV`

Integration via the golden test (no new file).

### 2.4 Expected gain

Cases that resolve when CL A spacing is fixed:
- 2× `MASTERCARD INCORPORATED CLA` (asset_only): **+2 rows**
- 3× `AON PLC SHS CLA` (asset+tx — both must fix together with §3): **+3 rows when combined**

Other patterns:
- 1× `ARTHURJ` → space fix: **+1 row**
- 1× `INTUIT INC A` → trailing-junk strip: **+1 row**
- 1× `MICHBRDWTR` → high-DPI re-OCR: **+1 row** (best case)
- 1× `CEDAR LP {NV` → `LP INV`: **+0 rows** (still missing "1292" tail; needs further OCR work)

**Realistic total: +5-8 rows from this fix alone, +8 when combined with §3 for the AON cases.**

---

## 3. Fix 2: Mark baseline subtraction

**Where:** `src/ocr_ptr_pdf_converter/cli.py::_process_page` and `_resolve_competing_marks`.

### 3.1 Algorithm

After computing per-cell ink densities for all data rows on a page:

1. **Per-column baseline:** for each TX-mark column and each AMOUNT-mark column, compute the median ink density across all data rows of the page.
2. **Anchored baseline:** use `min(median, P25)` as the actual baseline. P25 (25th percentile) anchors on the unmarked rows even if a column happens to have ≥50% marked (uncommon but possible on short pages).
3. **Effective density:** for each cell, `effective_density = max(0, raw_density - baseline)`.
4. **Mark winner selection:** `_resolve_competing_marks` uses `effective_density` (not raw) to pick the winner, with the same `_MARK_WINNER_DENSITY = 0.05` threshold meaning "0.05 above baseline".

### 3.2 Single-tx-section fallback

If a page has very few data rows (≤4) OR one column wins with raw density on ≥80% of rows, the median estimator is unreliable. In that case, **skip baseline subtraction** for that page. This prevents pages 1-2 (all Purchase) from regressing if the median estimator over-corrects.

**Detection rule (per page, per role-set):**
- Compute the raw winners with the existing logic.
- If `count(most_common_winner) / total_rows ≥ 0.8` AND that winner survives the threshold for ≥80% of rows: page is "single-tx" — keep raw densities for that role-set.
- Else: apply baseline subtraction.

### 3.3 Probe script

Add `scripts/probe_baseline_marks.py` that, for each page:

```
=== Page 3 — TX cols ===
column         median   P25   baseline
PURCHASE       0.182    0.165 0.165
SALE           0.142    0.120 0.120
EXCHANGE       0.158    0.140 0.140

row | raw P  raw S  raw EX | eff P  eff S  eff EX | winner_raw  winner_eff
  1 | 0.171  0.241  0.157  | 0.006  0.121  0.017  | S           S
  2 | ...
```

So we can verify the subtraction picks correct winners before committing.

### 3.4 Tests

Unit tests in a new `tests/test_marks.py` (or extending `test_extract.py`):

- `test_baseline_subtraction_picks_low_density_winner` — synthetic 3-column case where column A has higher raw density due to bleed but column B is the real mark
- `test_single_tx_section_skips_baseline` — synthetic page where one column wins ≥80% of rows; verify subtraction is bypassed
- `test_baseline_anchored_at_p25_when_median_high` — synthetic case where median > P25; verify baseline = P25

### 3.5 Expected gain

- ABBOTT, HILTON tx fix (page 3 first Sale rows): **+2 rows**
- 3× AON PLC SHS CL A tx fix (combined with §2 asset fix): **+3 rows**
- JACKSONVILLE D vs K (rightmost K column has heavy "Spouse/DC..." text bleed): **+1 row**
- MAYS ALLOCATE 2025 LP / EQUITABLE single-column drifts: **+1-2 rows**

**Realistic total: +6-8 rows.**

### 3.6 Risk

The page-1 "all Purchase" risk is real but mitigated by §3.2 fallback. We will validate by:
1. Running the probe before committing the change.
2. Re-running the golden test and confirming page-1 row count and Purchase tx-type stay correct.

---

## 4. Fix 3: Page 4 row recovery

**Where:** `src/ocr_ptr_pdf_converter/extract.py::rows_from_cell_texts` and `_is_orphan`. Plus a new probe.

### 4.1 Diagnostic step (must run first)

Add `scripts/probe_page4_rows.py`:

1. Render page 4 only.
2. For each grid row, print:
   - Raw OCR cell texts (per role).
   - The `TransactionRow` produced by `_row_from_cells`.
   - The decision in `rows_from_cell_texts`: `data` / `section_header` / `merged_into_prev` / `orphan_dropped` / `placeholder_dropped` / `garbage_dropped` / `empty_dropped`.
   - Total grid rows vs total kept rows.

Compare against the 22 expected data rows + 5 section-header rows = 27 expected entries.

### 4.2 Hypothesised root causes (to confirm via probe)

| Hypothesis | Symptom | Fix |
|---|---|---|
| **Holder OCR failure on a data row** triggers `_is_orphan` → merged into prior row's asset | A `VANGUARD INDEX FUNDS S&P 500 ETF USD` row absorbs into the row above | Tighten `_is_orphan`: require **all** of `not holder AND not date_tx AND not marks AND not amount`. If row has any of those, treat as a data row with the missing fields blank (the SP-default fallback in `_row_from_cells` handles missing holder). |
| **Section header rows lose their pure-asset shape** because OCR captures stray ink in adjacent columns | `LINDA MAYS MCCAUL 1999 EXEMPT TRUST` row has spurious "1" in date column → not classified as orphan, becomes garbage row | Loosen section-header detection: if row has asset of length ≥ 12 chars AND no holder AND amount/date are noise-only (1-2 char garbage), treat as section header rather than data. |
| **Grid detects fewer rows on page 4** than the form has | Page-level row count short by N | Tune `_line_positions` `min_run` threshold for the horizontal projection on page 4 (or add a fallback that subdivides large row gaps). |

### 4.3 Fix-after-probe rule

The actual edits to `_is_orphan` / `rows_from_cell_texts` will be guided by probe output. The spec commits to: probe first, then ONE targeted fix per confirmed hypothesis. No speculative rewrites.

### 4.4 Tests

Add to `tests/test_extract.py`:

- `test_data_row_with_missing_holder_is_kept_as_data` — synthetic row with `holder=""`, `asset="X"`, `tx="Purchase"`, `date="3/1/2026"`, `amount="A"` → kept as data row, not merged
- `test_section_header_with_noisy_amount_cell` — synthetic row with `holder=""`, `asset="LINDA MAYS MCCAUL TRUST"`, `amount="|"` (1-char OCR noise) → classified as section header

### 4.5 Expected gain

**Realistic total: +3-5 rows.** Some of the 6 missing rows may be unrecoverable if the grid simply doesn't see them — those need a v0.3.0 grid-detection improvement.

---

## 5. Combined budget and target

| Fix | Min gain | Max gain |
|---|---|---|
| §2 Asset cleanup (alone) | +5 | +8 |
| §3 Mark baseline (with AON cases from §2) | +6 | +8 |
| §4 Page 4 recovery | +3 | +5 |
| **Total** | **+14** | **+21** |

**Projected accuracy:** 67 + 14 = 83% (low end), 67 + 21 = 90% (high end).

This likely **does not hit 95% on its own**. If we land at ~85-90%, a follow-up Batch 3 will need to address:
- Remaining `many_field_drift` cases (probably row alignment / grid detection on page 3-4)
- The CEDAR HOLDINGS "1292" tail and similar trailing-content losses

We accept this and treat 95% as the v0.2.0 release gate, not the Batch 2 gate.

---

## 6. Implementation order

The three fixes are independent. We will implement and commit them separately, in this order:

1. **Fix 1 (asset cleanup)** — no dependencies; lowest risk; biggest concrete win on the most cases.
2. **Fix 3 probe** — produces diagnostic output before any code change so we have evidence for Fix 3's edits.
3. **Fix 2 (mark baseline)** — requires the probe to validate the median-anchored approach doesn't regress page 1.
4. **Fix 3 implementation** — guided by probe output.

Each step ends with `uv run pytest tests/test_golden.py -v` and a commit. After all four steps land, run the full suite plus `ruff check` and `mypy src`.

---

## 7. Out-of-scope cleanups acknowledged

- `PARTIAL_SALE` mislabel in `_TX_MARK_ROLES` — should become `EXCHANGE` (no PS column on this form). Minor cleanup; doesn't change accuracy. Will fix opportunistically in §3 or defer to Batch 3.
- Date format inconsistency in expected fixture (page 1 uses `3/24/2026`, page 2 uses `03/24/2026`) — already handled by `_canonical_date` in the golden test.
- The "no Partial Sale" finding implies `TX_TYPES` could shrink to 3 entries, but that's a schema change with cascading test updates — defer to v0.3.0.
