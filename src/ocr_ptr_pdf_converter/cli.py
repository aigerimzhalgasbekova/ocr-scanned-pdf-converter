from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image as PILImage

from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    classify_header,
    collect_column,
    infer_roles_by_position,
    rows_from_cell_texts,
)
from ocr_ptr_pdf_converter.grid import Grid, detect_grid
from ocr_ptr_pdf_converter.header import pick_date_notified
from ocr_ptr_pdf_converter.markdown import render as render_markdown
from ocr_ptr_pdf_converter.ocr import CellKind, ink_density, ocr_cell
from ocr_ptr_pdf_converter.orient import best_rotation
from ocr_ptr_pdf_converter.preprocess import to_binary
from ocr_ptr_pdf_converter.render import render_pdf
from ocr_ptr_pdf_converter.schema import Document, PageResult

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

# Minimum width (px at 300 DPI) of a column we'll treat as a TEXT cell. Below
# this we OCR it as a MARK cell to avoid feeding tesseract tiny slivers that
# return junk like 'P|' / 'a' / '<<<'.
_MIN_TEXT_COL_PX = 100
# Drop spurious sub-pixel "columns" that come out of the morphological line
# detector when two table rules sit very close together. They confuse the
# position-based role inference because they shift index alignment.
_MIN_COL_PX = 30
# Roles whose mark cells compete within a single row: only the highest-ink
# cell (above threshold) should win, otherwise multi-mark noise wins.
_TX_MARK_ROLE_SET = frozenset(
    {
        ColumnRole.PURCHASE,
        ColumnRole.SALE,
        ColumnRole.PARTIAL_SALE,
        ColumnRole.EXCHANGE,
    }
)
# Per-cell ink density above this counts as a mark when picking "winners"
# among competing tx-type or amount mark columns. A bit higher than the raw
# ocr.MARK threshold so we don't pick winners on slight bleed-through.
_MARK_WINNER_DENSITY = 0.05


def _crop_pil(image: PILImage.Image, rect: tuple[int, int, int, int]) -> PILImage.Image:
    return image.crop(rect)


def _crop_binary(binary: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return binary[y0:y1, x0:x1]


def _filter_grid(grid: Grid) -> Grid:
    """Drop sub-pixel column slivers that confuse position-based role
    inference (sometimes the morphological line detector emits two adjacent
    boundaries a couple of pixels apart)."""
    cols = [(x0, x1) for x0, x1 in grid.cols if (x1 - x0) >= _MIN_COL_PX]
    return Grid(rows=grid.rows, cols=cols)


def _grid_quality(cols: list[tuple[int, int]]) -> int:
    """Heuristic 'goodness' score for a detected column layout. Rewards a
    standard PTR shape: many columns with at least one very wide ASSET cell
    that sits in the LEFT half of the page (reading order)."""
    if not cols:
        return 0
    widths = [x1 - x0 for x0, x1 in cols]
    widest = max(widths)
    widest_idx = widths.index(widest)
    page_w = cols[-1][1]
    # Bonus when the widest column (the asset cell) is in the left half of
    # the page. This breaks ties between a real orientation and its 180-deg
    # mirror, which would otherwise place the asset on the right.
    asset_left_bonus = 500 if widest_idx < len(cols) // 2 else 0
    asset_bonus = widest if widest >= 800 else 0
    return len(cols) * 10 + asset_bonus + asset_left_bonus + (page_w // 100)


def _orient_and_grid(
    page_image: PILImage.Image,
) -> tuple[int, PILImage.Image, np.ndarray, Grid]:
    """Pick the rotation that produces the best-looking grid. Falls back to
    the orient-module choice if no rotation yields a usable structure."""
    candidates = []
    rot_default, _ = best_rotation(page_image)
    for angle in (0, 90, 180, 270):
        rotated = page_image if angle == 0 else page_image.rotate(-angle, expand=True)
        binary = to_binary(rotated)
        grid = _filter_grid(detect_grid(binary))
        candidates.append((_grid_quality(grid.cols), angle, rotated, binary, grid))
    # Prefer the highest-quality grid; on ties prefer the rotation chosen by
    # the orientation scorer to keep behavior stable for normal pages.
    candidates.sort(key=lambda t: (-t[0], 0 if t[1] == rot_default else 1, t[1]))
    _q, angle, rotated, binary, grid = candidates[0]
    return angle, rotated, binary, grid


def _resolve_roles(grid: Grid, oriented: PILImage.Image) -> list[ColumnRole]:
    cols = grid.cols
    rows = grid.rows
    if rows:
        header_y0, header_y1 = rows[0]
        header_texts: list[str] = []
        for x0, x1 in cols:
            crop = _crop_pil(oriented, (x0, header_y0, x1, header_y1))
            if crop.width <= 1 or crop.height <= 1:
                header_texts.append("")
                continue
            header_texts.append(
                pytesseract.image_to_string(crop, config="--psm 6").strip()
            )
        roles = classify_header(header_texts)
    else:
        roles = [ColumnRole.OTHER] * len(cols)

    # If header text was unreadable (most cols got OTHER), fall back to
    # position-based inference using known PTR form structure.
    other_ratio = sum(1 for r in roles if r is ColumnRole.OTHER) / max(1, len(roles))
    if other_ratio > 0.5:
        roles = infer_roles_by_position(cols, roles)
    return roles


def _kind_for_cell(role: ColumnRole, col_width: int) -> CellKind:
    """Pick the OCR kind for a cell. Narrow text-classified cells (e.g. the
    HOLDER column on a packed PTR form) get downgraded to MARK so we don't
    feed tesseract slivers."""
    kind = _ROLE_TO_KIND[role]
    if kind is CellKind.TEXT and col_width < _MIN_TEXT_COL_PX:
        return CellKind.MARK
    return kind


def _resolve_competing_marks(
    row_texts: list[str],
    densities: list[float],
    roles: list[ColumnRole],
    role_set: frozenset[ColumnRole],
) -> None:
    """Within a single row, only the highest-ink cell among `role_set`
    columns wins (and only if above the winner threshold). Mutates row_texts
    in place. Columns whose role is not in role_set are untouched."""
    candidates = [(densities[i], i) for i, r in enumerate(roles) if r in role_set]
    if not candidates:
        return
    best_density, best_idx = max(candidates, key=lambda t: t[0])
    above_threshold = best_density >= _MARK_WINNER_DENSITY
    for _d, i in candidates:
        row_texts[i] = "X" if (i == best_idx and above_threshold) else ""


def _process_page(
    page_image: PILImage.Image, page_number: int
) -> tuple[PageResult, list[str]]:
    """Returns (page_result, date_notified_column_values_for_this_page)."""
    rotation, oriented, binary, grid = _orient_and_grid(page_image)

    if not grid.rows or not grid.cols:
        return (PageResult(page_number=page_number, rotation=rotation, rows=()), [])

    roles = _resolve_roles(grid, oriented)
    col_widths = [x1 - x0 for x0, x1 in grid.cols]

    cell_rows: list[list[str]] = []
    for y0, y1 in grid.rows[1:]:
        row_texts: list[str] = []
        densities: list[float] = []
        for (x0, x1), role, width in zip(grid.cols, roles, col_widths, strict=True):
            rect = (x0, y0, x1, y1)
            crop = _crop_pil(oriented, rect)
            bin_crop = _crop_binary(binary, rect)
            if crop.width <= 1 or crop.height <= 1:
                row_texts.append("")
                densities.append(0.0)
                continue
            kind = _kind_for_cell(role, width)
            row_texts.append(ocr_cell(crop, bin_crop, kind))
            densities.append(ink_density(bin_crop) if bin_crop.size else 0.0)

        # Pick a single tx-type mark winner per row to suppress multi-mark
        # noise (the form's vertical-text headers leak ink into adjacent
        # narrow cells, so several would otherwise all read as marked).
        _resolve_competing_marks(row_texts, densities, roles, _TX_MARK_ROLE_SET)
        # Same for amount: only one A..K cell can be the "real" mark.
        _resolve_competing_marks(
            row_texts, densities, roles, frozenset({ColumnRole.AMOUNT})
        )
        cell_rows.append(row_texts)

    rows = rows_from_cell_texts(cell_rows, roles)
    date_notified_values = collect_column(cell_rows, roles, ColumnRole.DATE_NOTIFIED)
    return (
        PageResult(page_number=page_number, rotation=rotation, rows=tuple(rows)),
        date_notified_values,
    )


def _convert_to_document(
    pdf_path: Path,
    dpi: int = 300,
    pages: Sequence[int] | None = None,
) -> Document:
    images = render_pdf(pdf_path, dpi=dpi, pages=pages)
    page_results: list[PageResult] = []
    all_date_notified: list[str] = []
    for idx, img in enumerate(images, start=1):
        page_number = pages[idx - 1] if pages else idx
        result, dn_values = _process_page(img, page_number)
        page_results.append(result)
        all_date_notified.extend(dn_values)
    return Document(
        source_filename=pdf_path.name,
        date_notified=pick_date_notified(all_date_notified),
        pages=tuple(page_results),
    )


def convert(
    pdf_path: Path | str,
    dpi: int = 300,
    pages: Sequence[int] | None = None,
) -> str:
    return render_markdown(_convert_to_document(Path(pdf_path), dpi=dpi, pages=pages))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-ptr-convert")
    parser.add_argument("input", help="Path to scanned PTR PDF.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output Markdown path. Default: output/<input-stem>.md",
        default=None,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"error: input not found: {in_path}", file=sys.stderr)
        return 1

    doc = _convert_to_document(in_path)
    md = render_markdown(doc)
    out_path = (
        Path(args.output) if args.output else Path("output") / f"{in_path.stem}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)

    if all(len(p.rows) == 0 for p in doc.pages):
        print("error: no recognizable table on any page", file=sys.stderr)
        return 3
    return 0
