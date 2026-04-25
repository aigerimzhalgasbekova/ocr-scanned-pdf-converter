from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract

_HEADER_TOKEN_RE = re.compile(
    r"^(holder|owner|asset|transaction|purchase|sale|exchange|date|amount)$",
    re.IGNORECASE,
)
_MIN_COLS = 4


@dataclass(frozen=True)
class Grid:
    rows: list[tuple[int, int]]
    cols: list[tuple[int, int]]

    def cells(self) -> list[tuple[int, int, int, int]]:
        out: list[tuple[int, int, int, int]] = []
        for y0, y1 in self.rows:
            for x0, x1 in self.cols:
                out.append((x0, y0, x1, y1))
        return out


def _line_positions(mask: np.ndarray, axis: int, min_run: int) -> list[int]:
    proj = (mask == 255).sum(axis=axis)
    positions: list[int] = []
    i = 0
    n = len(proj)
    while i < n:
        if proj[i] >= min_run:
            j = i
            while j < n and proj[j] >= min_run:
                j += 1
            positions.append((i + j - 1) // 2)
            i = j
        else:
            i += 1
    return positions


def _bands(positions: list[int]) -> list[tuple[int, int]]:
    if len(positions) < 2:
        return []
    return [(positions[i], positions[i + 1]) for i in range(len(positions) - 1)]


def cols_from_header_text(binary: np.ndarray) -> list[tuple[int, int]]:
    """Synthesize column bands from header-token x-positions when grid lines
    are too faint to detect. Searches the top 25% of the page for tokens that
    match known header keywords (case-insensitive), then builds bands from
    midpoints between consecutive token left edges."""
    h, w = binary.shape
    header_band = binary[: max(1, h // 4), :]
    data = pytesseract.image_to_data(
        header_band, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    xs: list[int] = []
    for text, left, width in zip(data["text"], data["left"], data["width"], strict=True):
        if text and _HEADER_TOKEN_RE.match(text.strip()):
            xs.append(int(left))
            xs.append(int(left) + int(width))
    xs = sorted(set(xs))
    if len(xs) < 2:
        return []
    # Build bands: leading edge = first token left, trailing edge = page width.
    edges = [0, *xs, w]
    edges = sorted(set(edges))
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def detect_grid(binary: np.ndarray) -> Grid:
    inv = cv2.bitwise_not(binary)
    h, w = inv.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 30), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 30)))
    h_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel)
    horizontal_y = _line_positions(h_lines, axis=1, min_run=int(w * 0.5))
    vertical_x = _line_positions(v_lines, axis=0, min_run=int(h * 0.5))
    rows = _bands(horizontal_y)
    cols = _bands(vertical_x)
    if len(cols) < _MIN_COLS:
        fallback = cols_from_header_text(binary)
        if len(fallback) >= _MIN_COLS:
            cols = fallback
    return Grid(rows=rows, cols=cols)
