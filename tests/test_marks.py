import pytest

from ocr_ptr_pdf_converter.cli import _compute_col_baselines, _is_single_tx_page


def test_baseline_subtraction_picks_low_density_winner():
    # 3 TX cols; PURCHASE has systematic bleed ~0.18-0.20, SALE has one real mark
    # at row 1 (density 0.25). With baselines, SALE should win row 1.
    densities_per_col = [
        [0.20, 0.18, 0.19],   # PURCHASE: consistent bleed
        [0.12, 0.25, 0.11],   # SALE: one real mark at row index 1
        [0.05, 0.06, 0.05],   # EXCHANGE: low throughout
    ]
    baselines = _compute_col_baselines(densities_per_col)
    # Row 1 effective densities (0-indexed row 1)
    eff_row1 = [max(0.0, densities_per_col[col][1] - baselines[col]) for col in range(3)]
    assert eff_row1[1] > eff_row1[0], (
        f"SALE eff={eff_row1[1]:.3f} should exceed PURCHASE eff={eff_row1[0]:.3f} after baseline"
    )


def test_single_tx_section_skips_baseline():
    # 5 rows: col 0 wins 4/5 (80%) → should skip baseline subtraction
    all_row_densities = [
        [0.20, 0.05],
        [0.18, 0.06],
        [0.22, 0.04],
        [0.19, 0.05],
        [0.06, 0.21],  # one row where col 1 wins
    ]
    assert _is_single_tx_page(all_row_densities, [0, 1]) is True


def test_single_tx_section_does_not_skip_when_split():
    # 3/5 rows have col 0 winning = 60% < 80% → do not skip
    all_row_densities = [
        [0.20, 0.05],
        [0.18, 0.06],
        [0.22, 0.04],
        [0.06, 0.21],
        [0.05, 0.20],
    ]
    assert _is_single_tx_page(all_row_densities, [0, 1]) is False


def test_baseline_anchored_at_p25_when_median_high():
    # Col has sparse marks: 3 rows low (0.05), 5 rows high (0.40).
    # median = 0.40, P25 ≈ 0.05 → baseline = min(0.40, 0.05) = 0.05.
    densities_per_col = [[0.05, 0.05, 0.05, 0.40, 0.40, 0.40, 0.40, 0.40]]
    baselines = _compute_col_baselines(densities_per_col)
    assert baselines[0] == pytest.approx(0.05, abs=0.02)
