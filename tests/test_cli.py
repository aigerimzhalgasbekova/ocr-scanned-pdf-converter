from pathlib import Path

import pytest

from ocr_ptr_pdf_converter import convert
from ocr_ptr_pdf_converter.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "9115728.pdf"


def test_convert_returns_str():
    if not FIXTURE.exists():
        pytest.skip("fixture missing")
    md = convert(FIXTURE)
    assert isinstance(md, str)
    assert md.startswith("# OCR conversion for 9115728.pdf")


def test_main_writes_to_output_dir(tmp_path, monkeypatch):
    if not FIXTURE.exists():
        pytest.skip("fixture missing")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "out.md"
    rc = main([str(FIXTURE), "-o", str(target)])
    assert rc == 0
    assert target.exists()
    assert target.read_text().startswith("# OCR conversion for 9115728.pdf")


def test_main_missing_input_returns_1(tmp_path):
    rc = main([str(tmp_path / "does-not-exist.pdf")])
    assert rc == 1


def test_main_returns_3_when_no_rows(tmp_path, monkeypatch):
    """Issue 1: PRD §6.2 requires exit code 3 when no page produced any rows."""
    from ocr_ptr_pdf_converter import cli as cli_mod
    from ocr_ptr_pdf_converter.schema import Document, PageResult

    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def _empty_convert_doc(path):
        return Document(source_filename=path.name, date_notified="",
                        pages=(PageResult(1, 0, ()),))

    monkeypatch.setattr(cli_mod, "_convert_to_document", _empty_convert_doc)
    rc = main([str(fake), "-o", str(tmp_path / "out.md")])
    assert rc == 3
