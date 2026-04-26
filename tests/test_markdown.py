from ocr_ptr_pdf_converter.markdown import render
from ocr_ptr_pdf_converter.schema import Document, PageResult, TransactionRow


def test_render_minimal_document():
    row = TransactionRow(
        holder="SP",
        asset="EQT CORP COM",
        transaction_type="PURCHASE",
        date_of_transaction="3/31/2026",
        amount_code="A",
    )
    section = TransactionRow.section_header("LINDA MAYS MCCAUL")
    doc = Document(
        source_filename="9115728.pdf",
        date_notified="4/6/2026",
        pages=(PageResult(page_number=1, rotation=0, rows=(section, row)),),
    )
    md = render(doc)
    assert md.startswith("# OCR conversion for 9115728.pdf\n")
    assert "**Date notified:** 4/6/2026" in md
    assert "## Amount code legend" in md
    assert "| K | Spouse/DC Amount over $1,000,000 |" in md
    assert "## Page 1" in md
    assert "| Holder | Asset | Transaction type | Date of transaction | Amount code |" in md
    assert "|  | LINDA MAYS MCCAUL |  |  |  |" in md
    # tx_type is rendered in human form (capitalized) even though it is
    # stored uppercase on the schema.
    assert "| SP | EQT CORP COM | Purchase | 3/31/2026 | A |" in md


def test_render_omits_date_notified_when_empty():
    doc = Document(source_filename="x.pdf", date_notified="", pages=())
    md = render(doc)
    assert "**Date notified:**" not in md


def test_render_multiple_pages():
    row = TransactionRow("SP", "X", "SALE", "3/1/2026", "B")
    doc = Document(
        source_filename="x.pdf",
        date_notified="4/6/2026",
        pages=(
            PageResult(1, 0, (row,)),
            PageResult(2, 0, (row,)),
        ),
    )
    md = render(doc)
    assert "## Page 1" in md
    assert "## Page 2" in md
