"""Probe: show every row's classification across all pages, with special
focus on the orphan-merge path. For each page, prints:
  - row index, classification (empty/placeholder/section/orphan-merge/
    orphan-as-header/garbage/normal)
  - for orphan-merge: the previous row's asset and the resulting merged asset
  - for section: whether it came from the orphan-as-header branch or the
    noisy-section-header branch

Goal: confirm whether section-header rows like "LLM FAMILY INVESTMENTS II LP"
or "LINDA MAYS MCCAUL ... TRUST" are being incorrectly merged into prior
real rows on pages 3 and 4.

Usage: uv run python scripts/probe_orphan_merges.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytesseract

from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    _is_empty,
    _is_garbage,
    _is_noisy_section_header,
    _is_orphan,
    _is_placeholder,
    _row_from_cells,
    classify_header,
    infer_roles_by_position,
)
from ocr_ptr_pdf_converter.grid import Grid, detect_grid
from ocr_ptr_pdf_converter.ocr import CellKind, ink_density, ocr_cell
from ocr_ptr_pdf_converter.preprocess import to_binary
from ocr_ptr_pdf_converter.render import render_pdf
from ocr_ptr_pdf_converter.schema import TransactionRow

FIXTURE_PDF = Path("tests/fixtures/9115728.pdf")
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


def _grid_quality(cols):
    if not cols:
        return 0
    widths = [x1 - x0 for x0, x1 in cols]
    widest = max(widths)
    widest_idx = widths.index(widest)
    page_w = cols[-1][1]
    asset_left_bonus = 500 if widest_idx < len(cols) // 2 else 0
    asset_bonus = widest if widest >= 800 else 0
    return len(cols) * 10 + asset_bonus + asset_left_bonus + (page_w // 100)


def _orient_and_grid(page_image):
    candidates = []
    for angle in (0, 90, 180, 270):
        rotated = page_image if angle == 0 else page_image.rotate(-angle, expand=True)
        binary = to_binary(rotated)
        grid = detect_grid(binary)
        cols = [(x0, x1) for x0, x1 in grid.cols if (x1 - x0) >= _MIN_COL_PX]
        grid = Grid(rows=grid.rows, cols=cols)
        candidates.append((_grid_quality(grid.cols), angle, rotated, binary, grid))
    candidates.sort(key=lambda t: -t[0])
    _q, angle, rotated, binary, grid = candidates[0]
    return angle, rotated, binary, grid


def _resolve_roles(grid, oriented):
    cols = grid.cols
    rows = grid.rows
    if rows:
        header_y0, header_y1 = rows[0]
        header_texts = []
        for x0, x1 in cols:
            crop = oriented.crop((x0, header_y0, x1, header_y1))
            if crop.width <= 1 or crop.height <= 1:
                header_texts.append("")
                continue
            header_texts.append(
                pytesseract.image_to_string(crop, config="--psm 6").strip()
            )
        roles = classify_header(header_texts)
    else:
        roles = [ColumnRole.OTHER] * len(cols)
    other_ratio = sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles))
    if other_ratio > 0.5:
        roles = infer_roles_by_position(cols, roles)
    return roles


def _kind_for_cell(role, col_width):
    kind = _ROLE_TO_KIND[role]
    if kind is CellKind.TEXT and col_width < _MIN_TEXT_COL_PX:
        return CellKind.MARK
    return kind


def _resolve_marks(row_texts, densities, roles, role_set):
    cands = [(densities[i], i) for i, r in enumerate(roles) if r in role_set]
    if not cands:
        return
    best_d, best_i = max(cands, key=lambda t: t[0])
    above = best_d >= _MARK_WINNER_DENSITY
    for _d, i in cands:
        row_texts[i] = "X" if (i == best_i and above) else ""


def _extract_cell_rows(page_image):
    angle, oriented, binary, grid = _orient_and_grid(page_image)
    if not grid.rows or not grid.cols:
        return [], [], []
    roles = _resolve_roles(grid, oriented)
    col_widths = [x1 - x0 for x0, x1 in grid.cols]
    cell_rows = []
    all_densities = []
    for y0, y1 in grid.rows[1:]:
        row_texts = []
        densities = []
        for (x0, x1), role, width in zip(grid.cols, roles, col_widths, strict=True):
            crop = oriented.crop((x0, y0, x1, y1))
            bin_crop = binary[y0:y1, x0:x1]
            if crop.width <= 1 or crop.height <= 1:
                row_texts.append("")
                densities.append(0.0)
                continue
            kind = _kind_for_cell(role, width)
            text = ocr_cell(crop, bin_crop, kind)
            row_texts.append(text)
            densities.append(ink_density(bin_crop) if bin_crop.size else 0.0)
        _resolve_marks(row_texts, densities, roles, _TX_ROLES)
        _resolve_marks(row_texts, densities, roles, frozenset({ColumnRole.AMOUNT}))
        cell_rows.append(row_texts)
        all_densities.append(densities)
    return cell_rows, roles, all_densities


def _classify_and_print(page_num, cell_rows, roles, all_densities):
    print(f"\n========== PAGE {page_num} ==========")
    date_idx = next(
        (i for i, r in enumerate(roles) if r is ColumnRole.DATE_TX), None
    )
    out: list[TransactionRow] = []
    for idx, texts in enumerate(cell_rows):
        date_density = (
            all_densities[idx][date_idx] if date_idx is not None else 0.0
        )
        row = _row_from_cells(texts, roles, date_density)
        tag = ""
        detail = ""
        if _is_empty(row):
            tag = "EMPTY"
        elif _is_placeholder(row):
            tag = "PLACEHOLDER"
            detail = f"asset={row.asset!r}"
        elif _is_noisy_section_header(row, date_density):
            tag = "SECTION(noisy)"
            detail = (
                f"date_dens={date_density:.3f} asset={row.asset!r} "
                f"tx={row.transaction_type!r} amt={row.amount_code!r}"
            )
            out.append(TransactionRow.section_header(row.asset))
        elif _is_garbage(row):
            tag = "GARBAGE"
            detail = (
                f"asset={row.asset!r} tx={row.transaction_type!r} "
                f"amount={row.amount_code!r}"
            )
        elif _is_orphan(row):
            if out and not out[-1].is_section_header and not _is_orphan(out[-1]):
                prev = out[-1]
                merged_asset = f"{prev.asset} {row.asset}".strip()
                tag = "ORPHAN->MERGED"
                detail = (
                    f"orphan_asset={row.asset!r} "
                    f"prev_asset={prev.asset!r} "
                    f"=> merged={merged_asset!r}"
                )
                out[-1] = TransactionRow(
                    holder=prev.holder,
                    asset=merged_asset,
                    transaction_type=prev.transaction_type,
                    date_of_transaction=prev.date_of_transaction,
                    amount_code=prev.amount_code,
                )
            else:
                tag = "ORPHAN->SECTION"
                detail = f"asset={row.asset!r}"
                out.append(TransactionRow.section_header(row.asset))
        else:
            tag = "ROW"
            detail = (
                f"date_dens={date_density:.3f} holder={row.holder!r} "
                f"asset={row.asset!r} tx={row.transaction_type!r} "
                f"date={row.date_of_transaction!r} amt={row.amount_code!r}"
            )
            out.append(row)
        print(f"  [{idx:02d}] {tag:18s} {detail}")
    return out


def main():
    images = render_pdf(FIXTURE_PDF, dpi=300)
    for i, img in enumerate(images, start=1):
        cell_rows, roles, densities = _extract_cell_rows(img)
        if not cell_rows:
            print(f"\n========== PAGE {i} ========== (no rows)")
            continue
        _classify_and_print(i, cell_rows, roles, densities)


if __name__ == "__main__":
    main()
