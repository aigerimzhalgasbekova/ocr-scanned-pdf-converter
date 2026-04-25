from ocr_ptr_pdf_converter.converter import (
    extract_dates,
    is_probable_data_row,
    nearest,
    records_to_markdown,
)


def test_nearest_picks_closest_key():
    mapping = {"A": 100.0, "B": 200.0, "C": 300.0}
    assert nearest(115, mapping) == "A"
    assert nearest(240, mapping) == "B"
    assert nearest(290, mapping) == "C"


def test_extract_dates_returns_first_two_matches():
    group = [
        {"text": "Asset"},
        {"text": "01/02/2025"},
        {"text": "filler"},
        {"text": "12/12/2025"},
        {"text": "07/07/2026"},
    ]
    assert extract_dates(group) == ["01/02/2025", "12/12/2025"]


def test_is_probable_data_row_filters_header_text():
    junk = {
        "Holder": "",
        "Asset": "Full Asset Name",
        "Transaction type": "",
        "Date of transaction": "",
        "Date notified": "",
        "Amount code": "",
    }
    real = {
        "Holder": "JT",
        "Asset": "Acme Corp Common Stock",
        "Transaction type": "Purchase",
        "Date of transaction": "01/02/2025",
        "Date notified": "",
        "Amount code": "C",
    }
    assert not is_probable_data_row(None)
    assert not is_probable_data_row(junk)
    assert is_probable_data_row(real)


def test_records_to_markdown_renders_table():
    records = [
        {
            "Holder": "JT",
            "Asset": "Acme | Corp",
            "Transaction type": "Sale",
            "Date of transaction": "03/04/2025",
            "Date notified": "04/04/2025",
            "Amount code": "D",
        }
    ]
    md = records_to_markdown(records, page_num=2)
    assert "## Page 2" in md
    assert "| Holder | Asset |" in md
    assert "Acme \\| Corp" in md
    assert "| JT | Acme \\| Corp | Sale | 03/04/2025 | 04/04/2025 | D |" in md
