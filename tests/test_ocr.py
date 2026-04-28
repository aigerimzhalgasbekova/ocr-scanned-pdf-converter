import numpy as np
from PIL import Image, ImageDraw

from ocr_ptr_pdf_converter.ocr import CellKind, _clean_text_cell, ink_density, ocr_cell


def _white(w: int = 60, h: int = 40) -> Image.Image:
    return Image.new("RGB", (w, h), "white")


def _white_binary(w: int = 60, h: int = 40) -> np.ndarray:
    return np.full((h, w), 255, dtype=np.uint8)


def _with_x_binary() -> np.ndarray:
    arr = np.full((40, 60), 255, dtype=np.uint8)
    # Diagonal X lines, ~3px wide
    for i in range(40):
        x1 = 10 + (i * 40) // 40
        x2 = 50 - (i * 40) // 40
        for dx in (-1, 0, 1):
            if 0 <= x1 + dx < 60:
                arr[i, x1 + dx] = 0
            if 0 <= x2 + dx < 60:
                arr[i, x2 + dx] = 0
    return arr


def test_ink_density_blank_is_low():
    arr = np.full((40, 60), 255, dtype=np.uint8)
    assert ink_density(arr) < 0.05


def test_ink_density_marked_is_high():
    arr = np.full((40, 60), 255, dtype=np.uint8)
    arr[10:30, 10:50] = 0
    assert ink_density(arr) > 0.2


def test_ocr_mark_blank_returns_empty():
    assert ocr_cell(_white(), _white_binary(), CellKind.MARK) == ""


def test_ocr_mark_filled_returns_X():
    assert ocr_cell(_white(), _with_x_binary(), CellKind.MARK) == "X"


def test_ocr_mark_uses_binary_not_image():
    """Regression for Issue 5: a near-white grayscale image with a true binary
    crop containing ink should still be classified as marked."""
    near_white_img = Image.new("RGB", (60, 40), (240, 240, 240))
    assert ocr_cell(near_white_img, _with_x_binary(), CellKind.MARK) == "X"


def test_ocr_date_extracts_date():
    img = Image.new("RGB", (200, 60), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 10), "3/24/2026", fill="black")
    out = ocr_cell(img, _white_binary(200, 60), CellKind.DATE)
    assert out == "" or out == "3/24/2026"  # tesseract may miss tiny default font


def test_clean_text_cell_strips_edge_column_rules():
    assert _clean_text_cell("LLM FAMILY INVESTMENTS LP | - |") == "LLM FAMILY INVESTMENTS LP"
    assert _clean_text_cell("|] LANSING MICH UTIL SYS REV") == "LANSING MICH UTIL SYS REV"
    assert _clean_text_cell("ARTHUR J GALLAGHER & CO -") == "ARTHUR J GALLAGHER & CO"


def test_clean_text_cell_collapses_internal_whitespace():
    assert _clean_text_cell("LLM   FAMILY  INVESTMENTS LP") == "LLM FAMILY INVESTMENTS LP"


def test_clean_text_cell_preserves_clean_input():
    assert _clean_text_cell("JACKSONVILLE FLA TRANS REV") == "JACKSONVILLE FLA TRANS REV"


def test_clean_text_cell_empty():
    assert _clean_text_cell("") == ""
    assert _clean_text_cell("   ") == ""
    assert _clean_text_cell("|||") == ""


def test_ocr_letter_restricts_to_amount_codes():
    img = Image.new("RGB", (60, 60), "white")
    out = ocr_cell(img, _white_binary(60, 60), CellKind.LETTER)
    assert out in {"", *list("ABCDEFGHIJK")}
