from __future__ import annotations

import cv2
import numpy as np
from PIL.Image import Image


def to_binary(image: Image) -> np.ndarray:
    arr = np.array(image.convert("L"), dtype=np.uint8)
    arr = cv2.medianBlur(arr, 3)
    binary = cv2.adaptiveThreshold(
        arr,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=10,
    )
    return binary
