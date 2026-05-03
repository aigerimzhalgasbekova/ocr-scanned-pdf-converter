# RESULT: 1x is correct on every flagged row; 2x output never matches a
# neighbor row's 1x. No local 2x bleed observed -> root cause is NOT the
# upscaled crop. Fix E (Task 5 / 2x trim) deferred to Batch 4 pending a
# grid-drift / row-bounds investigation.
"""Diagnostic probe for Fix E (cross-row asset contamination).

The probe walks every page, runs 1x and 2x OCR on every asset cell, and
prints — for every cell where the 2x path triggered (`_looks_collapsed`)
or where 2x output differs from 1x — that row's 1x + 2x asset text plus
the prev-row and next-row 1x asset text from the same column. That gives
the direct local-bleed signature: 2x output of row N matching 1x output
of row N-1 or N+1.

The probe deliberately does NOT filter by expected-asset substring. In the
documented failure mode (`EQT CORP COM` -> `S&P GLOBAL INC COM`) neither
string contains the other, so substring filtering would silently miss the
contamination. Exhaustive enumeration over the 2x code path is the only
sound diagnostic.

Hypothesis: the 2x upscaled crop in cli._process_page pulls ink from the
adjacent row. If contamination appears only at 2x and not at 1x, tighten
the upscale crop. If contamination is already in the 1x pass, root cause
is grid drift -> deferred to Batch 4.

Usage:
    uv run python scripts/probe_cross_row_assets.py <pdf> [page1 page2 ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image as PILImage

from ocr_ptr_pdf_converter.cli import (
    _crop_binary,
    _crop_pil,
    _kind_for_cell,
    _looks_collapsed,
    _orient_and_grid,
    _resolve_roles,
)
from ocr_ptr_pdf_converter.extract import ColumnRole
from ocr_ptr_pdf_converter.ocr import ocr_cell
from ocr_ptr_pdf_converter.render import render_pdf


def _ocr_asset_for_row(
    oriented: PILImage.Image,
    binary,
    grid_cols: list[tuple[int, int]],
    asset_col_idx: int,
    role: ColumnRole,
    col_width: int,
    row: tuple[int, int],
) -> tuple[str, str | None, bool]:
    """Return (text_1x, text_2x_or_None, looks_collapsed_triggered)."""
    x0, x1 = grid_cols[asset_col_idx]
    y0, y1 = row
    rect = (x0, y0, x1, y1)
    crop = _crop_pil(oriented, rect)
    bin_crop = _crop_binary(binary, rect)
    if crop.width <= 1 or crop.height <= 1:
        return ("", None, False)
    kind = _kind_for_cell(role, col_width)
    text_1x = ocr_cell(crop, bin_crop, kind)
    triggered = _looks_collapsed(text_1x)
    # Always run 2x so we can see contamination even on rows where the
    # production code would not have re-OCR'd. Cheap relative to one
    # golden run and removes another false-negative path.
    up = crop.resize(
        (crop.width * 2, crop.height * 2),
        PILImage.Resampling.LANCZOS,
    )
    text_2x = ocr_cell(up, bin_crop, kind)
    return (text_1x, text_2x, triggered)


def probe_page(image: PILImage.Image, page_number: int) -> None:
    rotation, oriented, binary, grid = _orient_and_grid(image)
    if not grid.rows or not grid.cols:
        print(f"page {page_number}: no grid")
        return
    roles = _resolve_roles(grid, oriented)
    asset_indices = [i for i, r in enumerate(roles) if r is ColumnRole.ASSET]
    if not asset_indices:
        print(f"page {page_number}: no asset column")
        return
    col_widths = [x1 - x0 for x0, x1 in grid.cols]
    data_rows = grid.rows[1:]

    # First pass: 1x + 2x asset OCR for every data row, per asset column.
    per_row: list[dict[int, tuple[str, str | None, bool]]] = []
    for row in data_rows:
        col_results: dict[int, tuple[str, str | None, bool]] = {}
        for col_idx in asset_indices:
            col_results[col_idx] = _ocr_asset_for_row(
                oriented, binary, grid.cols, col_idx,
                roles[col_idx], col_widths[col_idx], row,
            )
        per_row.append(col_results)

    # Second pass: emit every row where 2x triggered in production OR where
    # 2x output differs from 1x output (covers contamination cases the 1x
    # path may already exhibit). Always include immediate neighbors so the
    # cross-row signature is visible.
    header_printed = False
    for row_idx, results in enumerate(per_row):
        for col_idx, (t1, t2, triggered) in results.items():
            differs = (t2 or "") != t1
            if not (triggered or differs):
                continue
            if not header_printed:
                print(f"\n=== page {page_number} (rotation={rotation}) ===")
                header_printed = True
            y0, y1 = data_rows[row_idx]
            flag = "TRIG" if triggered else "diff"
            print(f"  [{flag}] row {row_idx:>2} col {col_idx} y=[{y0},{y1}]")
            print(f"    1x={t1!r}")
            print(f"    2x={t2!r}")
            for delta, label in ((-1, "prev"), (1, "next")):
                n = row_idx + delta
                if 0 <= n < len(per_row):
                    nt1, _nt2, _ntr = per_row[n][col_idx]
                    ny0, ny1 = data_rows[n]
                    print(f"    {label} row {n:>2} y=[{ny0},{ny1}] 1x={nt1!r}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    pdf_path = Path(argv[0])
    pages_arg = [int(p) for p in argv[1:]] or None
    images = render_pdf(pdf_path, dpi=300, pages=pages_arg)
    for idx, img in enumerate(images, start=1):
        page_number = pages_arg[idx - 1] if pages_arg else idx
        probe_page(img, page_number)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
