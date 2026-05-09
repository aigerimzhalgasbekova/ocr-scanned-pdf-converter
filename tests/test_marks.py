from ocr_ptr_pdf_converter.cli import _compute_tx_col_baselines


def test_baseline_subtraction_picks_low_density_winner():
    # 3 rows × 3 tx-mark cols. PURCHASE has consistent bleed (0.18-0.20),
    # SALE has one real mark at row 1 (0.25), EXCH low throughout.
    # On row 1, baseline subtraction must let SALE win over PURCHASE.
    all_row_densities = [
        [0.20, 0.12, 0.05],  # row 0: PURCHASE wins raw
        [0.18, 0.25, 0.06],  # row 1: SALE wins raw (real mark)
        [0.19, 0.11, 0.05],  # row 2: PURCHASE wins raw
    ]
    baselines = _compute_tx_col_baselines(all_row_densities, [0, 1, 2])
    eff_row1 = [
        max(0.0, all_row_densities[1][i] - baselines[i]) for i in range(3)
    ]
    assert eff_row1[1] > eff_row1[0], (
        f"SALE eff={eff_row1[1]:.3f} should exceed "
        f"PURCHASE eff={eff_row1[0]:.3f} after baseline"
    )


def test_baseline_preserves_real_marks_on_70_30_page():
    """Regression guard for the bug that took accuracy from 80.6% to 66.33%:
    a page with 70% rows marked in column A and 30% rows marked in column B.

    The previous min(median, P25) baseline put column A's P25 inside the
    marked-density range (because 7/10 rows had real marks at ~0.20),
    so subtraction zeroed out column A's real marks and the row dropped
    below _MARK_WINNER_DENSITY → no winner → tx_type empty → row dropped.

    Non-winner-based P10 baseline only samples the 3 rows where A wasn't
    the winner (i.e., the bleed-only rows), giving baseline ≈ 0.04. Real
    marks at 0.20 survive at effective ≈ 0.16 — well above threshold."""
    all_row_densities = [
        [0.20, 0.04],  # row 0: A wins (real mark)
        [0.21, 0.04],  # row 1: A wins
        [0.19, 0.04],  # row 2: A wins
        [0.20, 0.04],  # row 3: A wins
        [0.18, 0.04],  # row 4: A wins
        [0.20, 0.04],  # row 5: A wins
        [0.21, 0.04],  # row 6: A wins
        [0.04, 0.20],  # row 7: B wins (real mark)
        [0.04, 0.21],  # row 8: B wins
        [0.04, 0.19],  # row 9: B wins
    ]
    baselines = _compute_tx_col_baselines(all_row_densities, [0, 1])
    # Both baselines must sit in the unmarked floor (~0.04), NOT in the
    # marked range (~0.20).
    assert baselines[0] < 0.06, f"col A baseline {baselines[0]:.3f} too high"
    assert baselines[1] < 0.06, f"col B baseline {baselines[1]:.3f} too high"
    # Real marks must survive subtraction with margin above the 0.05 winner
    # threshold on every row that has one.
    for r in range(7):
        eff_a = max(0.0, all_row_densities[r][0] - baselines[0])
        assert eff_a > 0.10, f"row {r} col A eff={eff_a:.3f} would lose"
    for r in range(7, 10):
        eff_b = max(0.0, all_row_densities[r][1] - baselines[1])
        assert eff_b > 0.10, f"row {r} col B eff={eff_b:.3f} would lose"


def test_baseline_zero_for_dominant_column_on_uniform_page():
    """100% one-tx-type page: the dominant column has 0 non-winner samples,
    so its baseline = 0 and its real marks are not subtracted. The other
    column's baseline uses bleed densities (still low). Replaces the old
    _is_single_tx_page guard — non-winner-based sampling makes that
    safety net obsolete."""
    all_row_densities = [
        [0.20, 0.04],
        [0.21, 0.04],
        [0.19, 0.04],
        [0.20, 0.04],
        [0.20, 0.04],
    ]
    baselines = _compute_tx_col_baselines(all_row_densities, [0, 1])
    assert baselines[0] == 0.0
    assert baselines[1] < 0.06


def test_baseline_empty_inputs_return_empty():
    assert _compute_tx_col_baselines([], [0, 1]) == {}
    assert _compute_tx_col_baselines([[0.1, 0.2]], []) == {}
