"""Probe: show ink density of every AMOUNT-role column per data row.
Flags rows where the winning column's margin over the runner-up is < 0.03
(likely wrong winner). Use output to calibrate _MARK_WINNER_DENSITY /
_MARK_WINNER_MARGIN in cli.py.

Usage: uv run python scripts/probe_letter_cells.py
"""
from __future__ import annotations

from pathlib import Path

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
