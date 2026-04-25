from ocr_ptr_pdf_converter.header import pick_date_notified


def test_returns_first_non_empty():
    assert pick_date_notified(["", "  ", "4/6/2026", "4/7/2026"]) == "4/6/2026"


def test_strips_whitespace():
    assert pick_date_notified(["  4/6/2026  "]) == "4/6/2026"


def test_returns_empty_when_all_blank():
    assert pick_date_notified(["", "   ", ""]) == ""


def test_returns_empty_for_empty_input():
    assert pick_date_notified([]) == ""
