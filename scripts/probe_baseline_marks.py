"""Probe: show per-column baselines and effective-density TX mark winners per page.
Run after implementing mark baseline subtraction to validate correct winner selection.

Expected: page-1 Purchase winners unchanged (single-tx skip); page-3 Sale rows fixed.

Usage: uv run python scripts/probe_baseline_marks.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
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
_MIN_COL_PX = 30
_MARK_WINNER_DENSITY = 0.05
_TX_ROLES = frozenset({
    ColumnRole.PURCHASE, ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE, ColumnRole.EXCHANGE,
})
_ROLE_LABEL = {
    ColumnRole.PURCHASE: "P",
    ColumnRole.SALE: "S",
    ColumnRole.PARTIAL_SALE: "PS",
    ColumnRole.EXCHANGE: "EX",
}


def _filter(grid: Grid) -> Grid:
    return Grid(rows=grid.rows, cols=[(x0, x1) for x0, x1 in grid.cols if x1 - x0 >= _MIN_COL_PX])


images = render_pdf(FIXTURE_PDF, dpi=300)
for page_idx, img in enumerate(images, start=1):
    _angle, oriented = best_rotation(img)
    binary = to_binary(oriented)
    grid = _filter(detect_grid(binary))
    if not grid.rows or not grid.cols:
        continue

    h_y0, h_y1 = grid.rows[0]
    header_texts = [
        pytesseract.image_to_string(oriented.crop((x0, h_y0, x1, h_y1)), config="--psm 6").strip()
        for x0, x1 in grid.cols
    ]
    roles = classify_header(header_texts)
    other_ratio = sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles))
    if other_ratio > 0.5:
        roles = infer_roles_by_position(grid.cols, roles)

    tx_indices = [i for i, r in enumerate(roles) if r in _TX_ROLES]
    if not tx_indices:
        continue

    # Collect all raw densities for TX cols
    all_tx_dens: list[list[float]] = [[] for _ in tx_indices]
    row_dens_list: list[list[float]] = []
    for y0, y1 in grid.rows[1:]:
        row_dens: list[float] = []
        for j, col_i in enumerate(tx_indices):
            x0, x1 = grid.cols[col_i]
            bin_crop = np.array(binary)[y0:y1, x0:x1]
            d = float((bin_crop < 128).sum()) / float(bin_crop.size) if bin_crop.size else 0.0
            all_tx_dens[j].append(d)
            row_dens.append(d)
        row_dens_list.append(row_dens)

    # Baselines: min(median, P25) per col
    baselines = []
    for col_dens in all_tx_dens:
        if col_dens:
            med = float(np.median(col_dens))
            p25 = float(np.percentile(col_dens, 25))
            baselines.append(min(med, p25))
        else:
            baselines.append(0.0)

    # Single-tx check
    winners_raw = []
    for row_dens in row_dens_list:
        best = max(range(len(row_dens)), key=lambda j: row_dens[j])
        if row_dens[best] >= _MARK_WINNER_DENSITY:
            winners_raw.append(best)
    skip = bool(winners_raw) and max(winners_raw.count(j) for j in set(winners_raw)) / len(row_dens_list) >= 0.8

    labels = [_ROLE_LABEL.get(roles[i], "?") for i in tx_indices]
    print(f"\n=== Page {page_idx} — TX cols {labels} {'(single-tx: skip baseline)' if skip else ''} ===")
    header = "  ".join(f"{lbl:>6}" for lbl in labels)
    print(f"{'col':<6}  {header}")
    print(f"{'median':<6}  {'  '.join(f'{np.median(d):6.3f}' for d in all_tx_dens)}")
    print(f"{'P25':<6}  {'  '.join(f'{np.percentile(d, 25):6.3f}' for d in all_tx_dens)}")
    print(f"{'base':<6}  {'  '.join(f'{b:6.3f}' for b in baselines)}")
    print()
    print(f"{'row':<5}  {'  '.join(f'raw_{lbl:>2}' for lbl in labels)}  |  "
          f"{'  '.join(f'eff_{lbl:>2}' for lbl in labels)}  |  winner_raw  winner_eff")
    for row_i, row_dens in enumerate(row_dens_list):
        eff = [max(0.0, row_dens[j] - (0.0 if skip else baselines[j])) for j in range(len(row_dens))]
        best_raw = max(range(len(row_dens)), key=lambda j: row_dens[j])
        best_eff = max(range(len(eff)), key=lambda j: eff[j])
        w_raw = labels[best_raw] if row_dens[best_raw] >= _MARK_WINNER_DENSITY else "-"
        w_eff = labels[best_eff] if eff[best_eff] >= _MARK_WINNER_DENSITY else "-"
        raws = "  ".join(f"{row_dens[j]:6.3f}" for j in range(len(row_dens)))
        effs = "  ".join(f"{eff[j]:6.3f}" for j in range(len(eff)))
        print(f"row {row_i + 1:<3}  {raws}  |  {effs}  |  {w_raw:>11}  {w_eff:>10}")
