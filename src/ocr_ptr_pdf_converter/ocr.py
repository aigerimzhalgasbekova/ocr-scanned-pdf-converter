from __future__ import annotations

import re
from enum import Enum

import numpy as np
import pytesseract
from PIL.Image import Image

_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_AMOUNT_CODES = set("ABCDEFGHIJK")
_MARK_DENSITY_THRESHOLD = 0.06


class CellKind(Enum):
    TEXT = "text"
    MARK = "mark"
    DATE = "date"
    LETTER = "letter"


def ink_density(binary: np.ndarray) -> float:
    if binary.size == 0:
        return 0.0
    return float((binary < 128).sum()) / float(binary.size)


def ocr_cell(image: Image, binary: np.ndarray, kind: CellKind) -> str:
    """OCR a cell. The PIL `image` is used by tesseract (TEXT/DATE/LETTER);
    the `binary` numpy crop (from preprocess.to_binary) is used for MARK
    ink-density classification so the threshold applies to a true binary
    array, not grayscale."""
    if kind is CellKind.MARK:
        return "X" if ink_density(binary) > _MARK_DENSITY_THRESHOLD else ""
    if kind is CellKind.TEXT:
        return pytesseract.image_to_string(image, config="--psm 6").strip()
    if kind is CellKind.DATE:
        text = pytesseract.image_to_string(image, config="--psm 7")
        m = _DATE_RE.search(text)
        return m.group(0) if m else ""
    if kind is CellKind.LETTER:
        text = pytesseract.image_to_string(
            image, config="--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJK"
        ).strip()
        return text if text in _AMOUNT_CODES else ""
    raise ValueError(f"unknown CellKind: {kind!r}")
