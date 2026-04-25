from ocr_ptr_pdf_converter.schema import (
    AMOUNT_CODES,
    HOLDERS,
    TX_TYPES,
    Document,
    PageResult,
    TransactionRow,
)


def test_constants():
    assert HOLDERS == frozenset({"JT", "SP", "DC"})
    assert TX_TYPES == ("PURCHASE", "SALE", "PARTIAL SALE", "EXCHANGE")
    assert AMOUNT_CODES == tuple("ABCDEFGHIJK")


def test_transaction_row_defaults():
    row = TransactionRow(
        holder="SP",
        asset="EQT CORP COM",
        transaction_type="PURCHASE",
        date_of_transaction="3/31/2026",
        amount_code="A",
    )
    assert row.is_section_header is False


def test_section_header_row():
    row = TransactionRow.section_header("LINDA MAYS MCCAUL 1999 EXEMPT TRUST")
    assert row.is_section_header is True
    assert row.holder == ""
    assert row.asset == "LINDA MAYS MCCAUL 1999 EXEMPT TRUST"
    assert row.transaction_type == ""
    assert row.date_of_transaction == ""
    assert row.amount_code == ""


def test_page_and_document():
    page = PageResult(page_number=1, rotation=0, rows=())
    doc = Document(source_filename="x.pdf", date_notified="4/6/2026", pages=(page,))
    assert doc.pages[0].page_number == 1
