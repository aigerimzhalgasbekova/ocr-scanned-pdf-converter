from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pdf2image import convert_from_path
from PIL.Image import Image


def render_pdf(
    pdf_path: Path | str,
    dpi: int = 300,
    pages: Sequence[int] | None = None,
) -> list[Image]:
    images = convert_from_path(str(pdf_path), dpi=dpi)
    if pages is None:
        return list(images)
    return [images[i - 1] for i in pages if 1 <= i <= len(images)]
