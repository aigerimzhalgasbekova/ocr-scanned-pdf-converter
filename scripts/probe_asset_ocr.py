"""Probe variants for asset-cell OCR to fix the space-collapse problem.

Symptom: assets like 'COMCAST CORP' come out as 'COMCASTCORP' under the
production --psm 6 path, blocking ~20 rows from matching the golden fixture.

This script renders page 1 of the golden PDF, runs the production grid +
role pipeline, then for each ASSET cell whose baseline OCR contains a
suspect collapsed token, re-OCRs the same crop under two variants:

  baseline : current production (--psm 6, no padding)
  padded   : --psm 6, crop padded by 8px on all sides (white fill)
  psm4     : --psm 4, no padding

Prints a side-by-side table so we can pick the variant that best preserves
spaces without regressing other behavior.

Usage:
    uv run python scripts/probe_asset_ocr.py
"""

from __future__ import annotations

from pathlib import Path

import pytesseract
from PIL import Image as PILImage

from ocr_ptr_pdf_converter.cli import _orient_and_grid, _resolve_roles
from ocr_ptr_pdf_converter.extract import ColumnRole
from ocr_ptr_pdf_converter.render import render_pdf

REPO = Path(__file__).resolve().parents[1]
FIXTURE_PDF = REPO / "tests" / "fixtures" / "9115728.pdf"
PAD = 8


def _pad(image: PILImage.Image, px: int) -> PILImage.Image:
    w, h = image.size
    canvas = PILImage.new(image.mode, (w + 2 * px, h + 2 * px), color=255)
    canvas.paste(image, (px, px))
    return canvas


def _ocr(image: PILImage.Image, config: str) -> str:
    return pytesseract.image_to_string(image, config=config).strip().replace("\n", " ")


def _looks_collapsed(text: str) -> bool:
    """Heuristic: any longest-token >= 10 chars with letters suggests
    space collapse ('COMCASTCORP', 'INSIGHTENTERPRISESINC')."""
    if not text:
        return False
    longest = max(text.split(), key=len, default="")
    return len(longest) >= 10 and any(c.isalpha() for c in longest)


def main() -> None:
    images = render_pdf(FIXTURE_PDF, dpi=300, pages=[1, 2, 3])

    samples: list[tuple[str, PILImage.Image, str]] = []
    for page_num, page in zip([1, 2, 3], images, strict=True):
        _rotation, oriented, _binary, grid = _orient_and_grid(page)
        roles = _resolve_roles(grid, oriented)
        asset_col_idx = next(
            (i for i, r in enumerate(roles) if r is ColumnRole.ASSET), None
        )
        if asset_col_idx is None:
            continue
        x0, x1 = grid.cols[asset_col_idx]
        for ri, (y0, y1) in enumerate(grid.rows[1:], start=1):
            crop = oriented.crop((x0, y0, x1, y1))
            if crop.width <= 1 or crop.height <= 1:
                continue
            baseline = _ocr(crop, "--psm 6")
            if _looks_collapsed(baseline):
                samples.append((f"p{page_num}r{ri}", crop, baseline))
            if len(samples) >= 6:
                break
        if len(samples) >= 6:
            break

    if not samples:
        print("no collapsed-looking asset cells found on page 1")
        return

    variants = {
        "baseline (psm6)": "--psm 6",
        "psm6 +preserve_iws": "--psm 6 -c preserve_interword_spaces=1",
        "psm4": "--psm 4",
        "psm4 +preserve_iws": "--psm 4 -c preserve_interword_spaces=1",
    }
    for loc, crop, baseline in samples:
        print(f"\n[{loc}]")
        for name, cfg in variants.items():
            print(f"  {name:<22}  {_ocr(crop, cfg)!r}")


if __name__ == "__main__":
    main()
