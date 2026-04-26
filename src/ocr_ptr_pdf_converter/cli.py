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
    rows_from_cell_texts,
)
from ocr_ptr_pdf_converter.grid import detect_grid
from ocr_ptr_pdf_converter.header import pick_date_notified
from ocr_ptr_pdf_converter.markdown import render as render_markdown
from ocr_ptr_pdf_converter.ocr import CellKind, ocr_cell
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


def _crop_pil(image: PILImage.Image, rect: tuple[int, int, int, int]) -> PILImage.Image:
    return image.crop(rect)


def _crop_binary(binary: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return binary[y0:y1, x0:x1]


def _process_page(
    page_image: PILImage.Image, page_number: int
) -> tuple[PageResult, list[str]]:
    """Returns (page_result, date_notified_column_values_for_this_page)."""
    rotation, oriented = best_rotation(page_image)
    binary = to_binary(oriented)
    grid = detect_grid(binary)

    if not grid.rows or not grid.cols:
        return (PageResult(page_number=page_number, rotation=rotation, rows=()), [])

    header_y0, header_y1 = grid.rows[0]
    header_texts = [
        pytesseract.image_to_string(
            _crop_pil(oriented, (x0, header_y0, x1, header_y1)), config="--psm 6"
        ).strip()
        for x0, x1 in grid.cols
    ]
    roles = classify_header(header_texts)

    cell_rows: list[list[str]] = []
    for y0, y1 in grid.rows[1:]:
        row_texts: list[str] = []
        for (x0, x1), role in zip(grid.cols, roles, strict=True):
            rect = (x0, y0, x1, y1)
            kind = _ROLE_TO_KIND[role]
            row_texts.append(
                ocr_cell(_crop_pil(oriented, rect), _crop_binary(binary, rect), kind)
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
    out_path = Path(args.output) if args.output else Path("output") / f"{in_path.stem}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)

    if all(len(p.rows) == 0 for p in doc.pages):
        print("error: no recognizable table on any page", file=sys.stderr)
        return 3
    return 0
