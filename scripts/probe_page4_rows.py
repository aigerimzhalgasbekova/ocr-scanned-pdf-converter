"""Probe: show per-row classification decision for page 4 of the PTR fixture.
Run to diagnose which rows are being dropped or merged before implementing
page-4 row recovery.

Expected (spec): 22 data rows + 5 section-header rows = 27 kept rows.

Usage: uv run python scripts/probe_page4_rows.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytesseract

from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    _is_empty,
    _is_garbage,
    _is_orphan,
    _is_placeholder,
    _row_from_cells,
    classify_header,
    infer_roles_by_position,
)
from ocr_ptr_pdf_converter.grid import Grid, detect_grid
from ocr_ptr_pdf_converter.ocr import CellKind, ink_density, ocr_cell
from ocr_ptr_pdf_converter.orient import best_rotation
from ocr_ptr_pdf_converter.preprocess import to_binary
from ocr_ptr_pdf_converter.render import render_pdf

FIXTURE_PDF = Path("tests/fixtures/9115728.pdf")
PAGE_NUM = 4
_MIN_COL_PX = 30
_MIN_TEXT_COL_PX = 100
_MARK_WINNER_DENSITY = 0.05
_TX_ROLES = frozenset({
    ColumnRole.PURCHASE, ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE, ColumnRole.EXCHANGE,
})
_ROLE_TO_KIND = {
    ColumnRole.HOLDER: CellKind.TEXT,
    ColumnRole.ASSET: CellKind.TEXT,
    ColumnRole.TX_TYPE: CellKind.TEXT,
    ColumnRole.PURCHASE: CellKind.MARK,
    ColumnRole.SALE: CellKind.MARK,
    ColumnRole.PARTIAL_SALE: CellKind.MARK,
    ColumnRole.EXCHANGE: CellKind.MARK,
    ColumnRole.DATE_TX: CellKind.DATE,
    ColumnRole.DATE_NOTIFIED: CellKind.DATE,
    ColumnRole.AMOUNT: CellKind.LETTER,
    ColumnRole.OTHER: CellKind.TEXT,
}


def _filter(grid: Grid) -> Grid:
    return Grid(rows=grid.rows, cols=[(x0, x1) for x0, x1 in grid.cols if x1 - x0 >= _MIN_COL_PX])


def _kind(role: ColumnRole, width: int) -> CellKind:
    k = _ROLE_TO_KIND[role]
    return CellKind.MARK if k is CellKind.TEXT and width < _MIN_TEXT_COL_PX else k


def _resolve_tx(texts: list[str], densities: list[float], roles: list[ColumnRole]) -> None:
    cands = [(densities[i], i) for i, r in enumerate(roles) if r in _TX_ROLES]
    if not cands:
        return
    best_d, best_i = max(cands)
    for _, i in cands:
        texts[i] = "X" if i == best_i and best_d >= _MARK_WINNER_DENSITY else ""


def _resolve_amount(texts: list[str], densities: list[float], roles: list[ColumnRole]) -> None:
    cands = [(densities[i], i) for i, r in enumerate(roles) if r is ColumnRole.AMOUNT]
    if not cands:
        return
    best_d, best_i = max(cands)
    for _, i in cands:
        texts[i] = "X" if i == best_i and best_d >= _MARK_WINNER_DENSITY else ""


image = render_pdf(FIXTURE_PDF, dpi=300, pages=[PAGE_NUM])[0]
_angle, oriented = best_rotation(image)
binary = to_binary(oriented)
grid = _filter(detect_grid(binary))

if not grid.rows or not grid.cols:
    print("No grid detected on page 4")
    raise SystemExit(1)

h_y0, h_y1 = grid.rows[0]
header_texts = [
    pytesseract.image_to_string(
        oriented.crop((x0, h_y0, x1, h_y1)), config="--psm 6"
    ).strip()
    for x0, x1 in grid.cols
]
roles = classify_header(header_texts)
other_ratio = sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles))
if other_ratio > 0.5:
    roles = infer_roles_by_position(grid.cols, roles)
col_widths = [x1 - x0 for x0, x1 in grid.cols]

print(f"=== Page {PAGE_NUM}: {len(grid.rows)} grid rows ({len(grid.rows) - 1} data rows) ===")
print(f"Roles: {[r.value for r in roles]}")
print()

kept = 0
last_kept = None
for row_idx, (y0, y1) in enumerate(grid.rows[1:], start=1):
    row_texts: list[str] = []
    densities: list[float] = []
    for (x0, x1), role, width in zip(grid.cols, roles, col_widths, strict=True):
        crop = oriented.crop((x0, y0, x1, y1))
        bin_crop = binary[y0:y1, x0:x1]
        if crop.width <= 1 or crop.height <= 1:
            row_texts.append("")
            densities.append(0.0)
            continue
        row_texts.append(ocr_cell(crop, bin_crop, _kind(role, width)))
        densities.append(ink_density(bin_crop) if bin_crop.size else 0.0)

    _resolve_tx(row_texts, densities, roles)
    _resolve_amount(row_texts, densities, roles)
    row = _row_from_cells(row_texts, roles)

    if _is_empty(row):
        decision = "empty_dropped"
    elif _is_placeholder(row):
        decision = "placeholder_dropped"
    elif _is_garbage(row):
        decision = "garbage_dropped"
    elif _is_orphan(row):
        if last_kept is not None and not last_kept.is_section_header and not _is_orphan(last_kept):
            decision = "merged_into_prev"
        else:
            decision = "section_header"
            last_kept = row
            kept += 1
    else:
        decision = "data"
        last_kept = row
        kept += 1

    print(
        f"Row {row_idx:2d} [{decision:22s}]  "
        f"holder={row.holder!r:4s}  "
        f"tx={row.transaction_type!r:12s}  "
        f"date={row.date_of_transaction!r:12s}  "
        f"amt={row.amount_code!r:3s}  "
        f"asset={row.asset[:40]!r}"
    )

print(f"\nTotal kept: {kept} / {len(grid.rows) - 1} grid rows  (expected ~27)")
