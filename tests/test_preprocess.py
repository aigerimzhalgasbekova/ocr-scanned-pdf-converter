import numpy as np
from PIL import Image

from ocr_ptr_pdf_converter.preprocess import to_binary


def test_to_binary_returns_uint8_2d_with_only_0_and_255():
    img = Image.new("RGB", (200, 200), "white")
    arr = to_binary(img)
    assert arr.dtype == np.uint8
    assert arr.ndim == 2
    assert set(np.unique(arr).tolist()).issubset({0, 255})


def test_to_binary_keeps_dark_text_dark():
    img = Image.new("RGB", (100, 100), "white")
    for x in range(20, 80):
        for y in range(40, 60):
            img.putpixel((x, y), (0, 0, 0))
    arr = to_binary(img)
    assert arr[50, 50] == 0
    assert arr[5, 5] == 255
