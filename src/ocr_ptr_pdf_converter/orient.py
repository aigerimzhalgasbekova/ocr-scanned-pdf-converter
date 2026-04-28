from __future__ import annotations

import re

import pytesseract
from PIL.Image import Image

_KEYWORDS = (
    "PURCHASE",
    "SALE",
    "EXCHANGE",
    "AMOUNT",
    "DATE",
    "ASSET",
    "HOLDER",
    "NOTIFIED",
)
_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_ROTATIONS = (0, 90, 180, 270)


def orientation_score(image: Image) -> int:
    text = pytesseract.image_to_string(image, config="--psm 6").upper()
    keyword_hits = sum(text.count(kw) for kw in _KEYWORDS)
    date_hits = len(_DATE_RE.findall(text))
    return keyword_hits * 3 + date_hits


def best_rotation(image: Image) -> tuple[int, Image]:
    scores: list[tuple[int, int, Image]] = []
    for angle in _ROTATIONS:
        rotated = image if angle == 0 else image.rotate(-angle, expand=True)
        scores.append((orientation_score(rotated), angle, rotated))
    scores.sort(key=lambda t: (-t[0], t[1]))
    _score, angle, rotated = scores[0]
    return angle, rotated
