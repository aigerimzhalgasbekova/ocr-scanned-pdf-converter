import numpy as np

from ocr_ptr_pdf_converter.grid import Grid, detect_grid


def _draw_grid(rows: int, cols: int, cell_w: int = 60, cell_h: int = 40) -> np.ndarray:
    h = rows * cell_h + 1
    w = cols * cell_w + 1
    img = np.full((h, w), 255, dtype=np.uint8)
    for r in range(rows + 1):
        img[r * cell_h, :] = 0
    for c in range(cols + 1):
        img[:, c * cell_w] = 0
    return img


def test_detect_simple_3x4_grid():
    img = _draw_grid(rows=3, cols=4)
    grid = detect_grid(img)
    assert isinstance(grid, Grid)
    assert len(grid.rows) == 3
    assert len(grid.cols) == 4


def test_grid_cells_returns_row_major_rects():
    img = _draw_grid(rows=2, cols=3)
    grid = detect_grid(img)
    cells = grid.cells()
    assert len(cells) == 6
    # row-major: first 3 cells share the same y-band
    y_bands = {(c[1], c[3]) for c in cells[:3]}
    assert len(y_bands) == 1


def test_returns_empty_grid_when_no_lines():
    img = np.full((100, 100), 255, dtype=np.uint8)
    grid = detect_grid(img)
    assert grid.rows == []
    assert grid.cols == []


def test_cols_from_header_text_synthesizes_bands():
    """Faint-line fallback: when fewer than 4 vertical lines are detected,
    detect_grid uses header-token x-positions to synthesize column bands."""
    from PIL import Image, ImageDraw, ImageFont

    # Render a header band with 5 keyword tokens spread across the width,
    # plus a single horizontal rule below it (so rows >= 1 but cols < 4).
    w, h = 1200, 800
    pil = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except OSError:
        font = ImageFont.load_default()
    for x, label in [
        (40, "Holder"),
        (200, "Asset"),
        (500, "Transaction"),
        (800, "Date"),
        (1050, "Amount"),
    ]:
        d.text((x, 30), label, fill=0, font=font)
    d.line((0, 100, w, 100), fill=0, width=2)  # one horizontal rule
    d.line((0, 180, w, 180), fill=0, width=2)  # another horizontal rule
    binary = np.array(pil, dtype=np.uint8)

    grid = detect_grid(binary)
    assert len(grid.cols) >= 4, (
        f"fallback should synthesize ≥4 cols, got {len(grid.cols)}"
    )
