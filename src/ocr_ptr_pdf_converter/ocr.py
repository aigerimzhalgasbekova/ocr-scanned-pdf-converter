from __future__ import annotations

import re
from enum import Enum

import numpy as np
import pytesseract
from PIL.Image import Image

_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_AMOUNT_CODES = set("ABCDEFGHIJK")
_MARK_DENSITY_THRESHOLD = 0.06
# psm 4 sometimes catches column rules and stray ink at the cell edges
# (e.g. 'ARTHUR J GALLAGHER & CO -' or '|] LANSING ...'). Strip leading and
# trailing runs of those punctuation/whitespace artifacts.
_EDGE_NOISE_RE = re.compile(r"^[\s|\[\]:\-=_*<>/\\]+|[\s|\[\]:\-=_*<>/\\]+$")
_WS_RUN_RE = re.compile(r"\s+")


def _clean_text_cell(text: str) -> str:
    cleaned = _EDGE_NOISE_RE.sub("", text)
    cleaned = _WS_RUN_RE.sub(" ", cleaned).strip()
    return cleaned


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
        # psm 4 (single column) preserves inter-word spaces better than psm 6
        # for tightly-kerned PTR asset names where psm 6 collapses
        # 'INSIGHT ENTERPRISES INC' -> 'INSIGHTENTERPRISESINC'.
        raw = pytesseract.image_to_string(image, config="--psm 4")
        return _clean_text_cell(raw)
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
