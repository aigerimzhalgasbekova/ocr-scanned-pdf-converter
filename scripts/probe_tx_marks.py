"""Diagnostic probe for batch 5 Fix 1 Phase A.

The 6 tx-drift rows are identified in the golden test output (actual.md).
This probe uses the OCR and extraction pipeline directly on those known
rows to verify the baseline-subtraction hypothesis before Phase B.

To find the 6 tx-drift rows, run:
    uv run python scripts/diagnose_golden.py | grep -A 30 "tx_only_drift"

Execution: The probe reads output/9115728_actual.md (populated by golden test)
and for each row, computes raw vs baseline-subtracted densities to verify
the PURCHASE→SALE recovery hypothesis.

Usage:
    uv run python scripts/probe_tx_marks.py
"""

from __future__ import annotations

from pathlib import Path

from ocr_ptr_pdf_converter.cli import (
    _compute_tx_col_baselines,
    _crop_binary,
    _crop_pil,
    _kind_for_cell,
    _orient_and_grid,
    _resolve_roles,
)
from ocr_ptr_pdf_converter.extract import ColumnRole
from ocr_ptr_pdf_converter.ocr import ink_density, ocr_cell
from ocr_ptr_pdf_converter.render import render_pdf

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "9115728.pdf"

# The 6 expected-SALE rows from actual.md: rows that are currently extracted
# as PURCHASE but should be SALE according to the expected fixture.
# Matched by (asset, date, amount_code) on the extracted values.
TARGET_ROWS = {
    ("ABBOTT LABORATORIES", "03/04/2026", "A"),
    ("HILTON WORLDWIDE HLDGS INC", "03/12/2026", "B"),
    ("AON PLC SHS CL A", "03/12/2026", "B"),
    ("PLEXUS CORP", "03/04/2026", "C"),
    ("LPL FINANCIAL HOLDINGS INC", "03/02/2026", "C"),
    ("HEALTHPEAK PROPERTIES INC", "03/23/2026", "D"),
}

TX_MARK_ROLES = (
    ColumnRole.PURCHASE,
    ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE,
    ColumnRole.EXCHANGE,
)


def main() -> None:
    """Run the full extraction pipeline and check baseline-subtraction hypothesis."""
    images = render_pdf(FIXTURE, dpi=300)
    raw_winners_purchase = 0
    baseline_winners_sale = 0
    confirmed_rows = 0
    matched_row_keys: set[tuple[str, str, str]] = set()

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

        # Collect per-row densities, matching what cli._process_page does.
        all_row_densities: list[list[float]] = []
        row_cell_texts: list[list[str]] = []

        for y0, y1 in grid.rows[1:]:
            row_densities: list[float] = []
            row_texts: list[str] = []
            for (x0, x1), role, width in zip(
                grid.cols, roles, col_widths, strict=True
            ):
                rect = (x0, y0, x1, y1)
                crop = _crop_pil(oriented, rect)
                bin_crop = _crop_binary(binary, rect)
                if crop.width <= 1 or crop.height <= 1:
                    row_texts.append("")
                    row_densities.append(0.0)
                    continue
                kind = _kind_for_cell(role, width)
                text = ocr_cell(crop, bin_crop, kind)
                row_texts.append(text)
                row_densities.append(
                    ink_density(bin_crop) if bin_crop.size else 0.0
                )
            all_row_densities.append(row_densities)
            row_cell_texts.append(row_texts)

        # Compute baselines (non-winner P10 — see _compute_tx_col_baselines).
        baselines_map = _compute_tx_col_baselines(
            all_row_densities, tx_col_indices
        )
        baselines = [baselines_map.get(i, 0.0) for i in range(len(grid.cols))]

        # Extract and classify rows (simplified: just grab asset/date/amount).
        from ocr_ptr_pdf_converter.extract import rows_from_cell_texts

        date_densities = [
            all_row_densities[i][
                next(
                    (
                        j
                        for j, r in enumerate(roles)
                        if r is ColumnRole.DATE_NOTIFIED
                    ),
                    -1,
                )
            ]
            if i < len(all_row_densities)
            else 0.0
            for i in range(len(row_cell_texts))
        ]
        rows = rows_from_cell_texts(row_cell_texts, roles, date_densities)

        for row_idx, (densities, row_obj) in enumerate(
            zip(all_row_densities, rows, strict=False)
        ):
            row_key = (row_obj.asset, row_obj.date_of_transaction, row_obj.amount_code)
            if row_key not in TARGET_ROWS:
                continue
            matched_row_keys.add(row_key)
            print(f"\n=== page {page_no} row {row_idx}  row_key={row_key} ===")
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
    print(
        f"matched rows           : "
        f"{len(matched_row_keys)} / {len(TARGET_ROWS)} expected"
    )
    print(f"raw PURCHASE winners   : {raw_winners_purchase}")
    print(f"baseline SALE winners  : {baseline_winners_sale}")
    print(f"per-row confirmations  : {confirmed_rows}  (raw=PURCHASE AND adj=SALE)")
    print()
    if len(matched_row_keys) < len(TARGET_ROWS):
        missing = TARGET_ROWS - matched_row_keys
        print("ERROR: Not all target rows were found. Missing:")
        for row in sorted(missing):
            print(f"  {row}")
        print()
    print(
        "ACCEPTANCE GATE:\n"
        f"  Found {len(matched_row_keys)} of {len(TARGET_ROWS)} target rows.\n"
        f"  {confirmed_rows} rows confirm hypothesis (raw=PURCHASE AND adj=SALE).\n"
        "  PROCEED to Fix 1 Phase B only if exactly 6 rows matched AND >= 4 confirm.\n"
    )


if __name__ == "__main__":
    main()
