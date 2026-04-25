from pathlib import Path

import pytest
from PIL.Image import Image

from ocr_ptr_pdf_converter.render import render_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "9115728.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture missing")
def test_renders_all_pages():
    pages = render_pdf(FIXTURE, dpi=150)
    assert len(pages) == 5
    assert all(isinstance(p, Image) for p in pages)


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture missing")
def test_pages_filter():
    pages = render_pdf(FIXTURE, dpi=150, pages=[1, 3])
    assert len(pages) == 2
