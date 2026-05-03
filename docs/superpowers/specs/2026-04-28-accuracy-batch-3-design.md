# Accuracy Batch 3 — Asset Spacing, Trailing Numerics, Cross-Row Contamination, Margin-Gated Baseline

**Status:** Design approved 2026-04-28. Branch: `feat/accuracy-batch-3`.

## Goal

Raise golden row accuracy from the current 76.53% (75/98) by addressing the five
failure categories identified in the Batch 2 post-mortem. No category-level
floor — partial wins are acceptable as long as the final number exceeds 76.53%.

## Success Criteria

- `tests/test_golden.py` accuracy strictly greater than 76.53%.
- All non-golden tests green at every commit.
- Page 1 (single-tx PURCHASE block) shows zero regressions when the new
  baseline subtraction lands. This is the exact failure mode that killed
  Task 6 in Batch 2 and is the single most important guardrail.
- `scripts/probe_baseline_marks.py` re-run before committing the baseline
  change confirms page 1 winners are unchanged from the pre-batch state.

## Non-Goals

- Hitting the 95% golden gate. Realistic batch outcome is 80–90%; any
  remaining gap is deferred to Batch 4.
- Changes to the grid detection module. If Fix E's probe shows the root cause
  is grid drift, Fix E is deferred to Batch 4 rather than touching grid logic.
- Re-running OCR with different DPIs or engines.

## Architecture

Five fixes, ordered cheapest-first so the risky one lands last with the
others already banked:

| # | Fix | Scope | Risk | Est. rows |
|---|-----|-------|------|-----------|
| B | Asset spacing rules | `_normalize_asset` regex additions | Low | ~5 |
| C | Preserve short trailing numerics | `_normalize_asset` tail-trim predicate | Low | ~2 |
| E | Cross-row asset contamination | Probe-gated; `cli._process_page` if local | Medium (may defer) | ~3-5 |
| A+D | Margin-gated baseline subtraction | `cli._process_page` resolve loop | High | ~8 + ~3 |
| — | Lint, mypy, golden | — | — | — |

Fix A+D replaces Task 6's failed "always subtract baseline (with single-tx
skip)" with a per-row margin gate: subtract baseline only when the raw winner
margin over the runner-up is below a threshold. Page 1 stays correct because
PURCHASE wins by a wide margin; page 3 SALE rows get fixed because PURCHASE
only edges SALE by a thin baseline-ink margin.

## Component Design

### Fix B — asset spacing

Add regex rules to `_normalize_asset` covering:

- `[A-Z]{4,}(INC|LLC|CORP|PLC)\b` → `\1 \2` (e.g. `INTUITINC` → `INTUIT INC`,
  `PTCINC` → `PTC INC`).
- Glued-suffix split: `PLCSHS` → `PLC SHS`, `EQUPORTF` → `EQU PORTF`,
  `EQPORTF` → `EQ PORTF`. Implement as a small token table rather than a
  greedy regex to avoid splitting legitimate tokens.
- Strip trailing standalone punctuation tokens (`;`, `:`) and stray
  short fragments like `; BD` after suffix tokens.

All new behavior covered by unit tests in `tests/test_extract.py`.

### Fix C — short trailing numerics

The current tail-trim loop drops `1292` (from `CEDAR HOLDINGS LP INV 1292`)
and `00` (from `GENUINE PARTS CO COM USD1 00`). New rule:

> When the trailing token is digits-only and `len(token) ≤ 4`, AND the
> immediately preceding token is in `_REAL_SHORT_SUFFIXES` (or matches a
> known asset-tail pattern like `USD1`, `INV`, `COM`), keep it.

Tests for both example assets in `tests/test_extract.py`.

### Fix E — cross-row asset contamination

Three rows have the wrong asset entirely (e.g. `EQT CORP COM` came out as
`S&P GLOBAL INC COM`). Hypothesis: either grid row Y bounds drift, or the
`_looks_collapsed` 2× re-OCR upscaled crop pulls ink from an adjacent row.

Process:

1. Create `scripts/probe_cross_row_assets.py` that, for each of the
   misattributed rows, prints: grid row Y bounds, raw OCR text, 2× re-OCR
   text (if `_looks_collapsed` triggered), and the asset OCR for the row
   immediately above and below.
2. Inspect output. If contamination appears in the 2× re-OCR but not the
   1× pass, tighten the upscale path (e.g. shrink crop by 1-2px on top/
   bottom before resize).
3. If contamination is already in the 1× pass — root cause is grid drift.
   Defer to Batch 4 with a TODO; do not modify grid module in this batch.

Spec is explicit: Fix E lands only if the fix is local to `cli.py` or
`extract.py`. Otherwise it ships as a probe script + documented finding,
no production change.

### Fix A+D — margin-gated baseline subtraction

Replace the current `_resolve_competing_marks` call sequence in
`_process_page` with a margin-gated path:

```
For each row, for each mark role-set (TX, AMOUNT):
  raw_winner_idx = argmax(densities over role-set)
  raw_winner_d = densities[raw_winner_idx]
  raw_runner_up_d = max(densities over role-set, excluding winner_idx)
  margin = raw_winner_d - raw_runner_up_d

  if raw_winner_d < _MARK_WINNER_DENSITY:
      no winner — clear all
  elif margin >= MARGIN_THRESHOLD:
      keep raw winner — high confidence
  else:
      compute eff_densities = max(0, d - col_baseline)
      eff_winner_idx = argmax(eff over role-set)
      use eff_winner if eff[eff_winner_idx] >= _MARK_WINNER_DENSITY else clear
```

`MARGIN_THRESHOLD` is calibrated from `scripts/probe_baseline_marks.py`
output: pick a value that page-1 PURCHASE rows clear comfortably (so the
gate keeps the raw winner, baseline never applied) and page-3 SALE-vs-
PURCHASE rows fall below (so baseline subtraction kicks in). Initial
target: 0.05 (i.e. winner must lead runner-up by 5 percentage points of
ink density to bypass baseline). Final value confirmed by probe.

`_compute_col_baselines` is reused unchanged from Batch 2. The
`_is_single_tx_page` helper is removed — the margin gate subsumes its
purpose without the 80%-of-rows heuristic.

The same margin-gated logic is applied to AMOUNT column resolution (Fix D).

## Data Flow

No change to overall pipeline shape. The two-pass OCR-then-resolve structure
introduced in Batch 2 is preserved. The change is local to the resolve step
inside `_process_page`.

## Error Handling

- Empty role-set for TX or AMOUNT → no resolution, leave row unchanged.
- All densities below `_MARK_WINNER_DENSITY` → clear all marks (current
  behavior preserved).
- Single column in role-set → no runner-up; treat as infinite margin
  (always keep raw winner).

## Testing

**Unit tests (`tests/test_extract.py`):**
- 4 spacing tests: `INTUITINC`, `PTCINC`, `PLCSHS CL A`, `EQUPORTF`.
- 2 trailing-numeric tests: `CEDAR HOLDINGS LP INV 1292`,
  `GENUINE PARTS CO COM USD1 00`.

**Unit tests (`tests/test_marks.py`):**
- Margin above threshold → raw winner kept (regression guard for page 1).
- Margin below threshold → baseline applied; low-density column wins after
  subtraction (regression guard for page 3 SALE rows).
- Single-column role-set → raw winner kept (no runner-up).

**Probes:**
- `scripts/probe_baseline_marks.py` — re-run after A+D lands; compare
  per-row winners across all pages to pre-batch baseline.
- `scripts/probe_cross_row_assets.py` (new) — diagnostic for Fix E.

**Golden test:**
- Run after each fix; record accuracy delta in commit message.
- Final accuracy must exceed 76.53%.

**Revert protocol:**
If A+D causes any page-1 regression after probe inspection, revert that
single commit. Fixes B, C, E remain banked independently.

## File-Level Changes

**Modified:**
- `src/ocr_ptr_pdf_converter/extract.py` — `_normalize_asset` body (B, C).
- `src/ocr_ptr_pdf_converter/cli.py` — `_process_page` resolve loop, new
  margin-gate helper (A+D); possibly small `_looks_collapsed` adjustment
  (E, gated on probe).

**Created:**
- `scripts/probe_cross_row_assets.py` (Fix E probe).

**Tests:**
- `tests/test_extract.py` — new asset normalization tests (B, C).
- `tests/test_marks.py` — new margin-gate tests (A+D).

## Rollout

Single PR from `feat/accuracy-batch-3` to `main`, one commit per fix so any
regression can be reverted in isolation. Ruff/mypy clean before final commit.
