from ocr_ptr_pdf_converter.extract import (
    ColumnRole,
    _normalize_asset,
    classify_header,
    rows_from_cell_texts,
)
from ocr_ptr_pdf_converter.schema import TransactionRow


def test_normalize_asset_inserts_space_in_cla():
    assert (
        _normalize_asset("MASTERCARD INCORPORATED CLA")
        == "MASTERCARD INCORPORATED CL A"
    )


def test_normalize_asset_inserts_space_for_initial():
    assert _normalize_asset("ARTHURJ GALLAGHER & CO") == "ARTHUR J GALLAGHER & CO"


def test_normalize_asset_strips_trailing_letter_after_inc():
    assert _normalize_asset("INTUIT INC A") == "INTUIT INC"


def test_normalize_asset_keeps_real_suffix():
    assert _normalize_asset("BAYER AG SPON ADR") == "BAYER AG SPON ADR"


def test_normalize_asset_substitutes_curly_brace_for_i():
    assert _normalize_asset("LP {NV") == "LP INV"


def test_normalize_asset_keeps_short_numeric_after_inv():
    assert (
        _normalize_asset("CEDAR HOLDINGS LP INV 1292")
        == "CEDAR HOLDINGS LP INV 1292"
    )


def test_normalize_asset_keeps_short_numeric_after_usd1():
    # USD1 followed by 00 (the cent fragment) is a real OCR pattern; keep it.
    assert (
        _normalize_asset("GENUINE PARTS CO COM USD1 00")
        == "GENUINE PARTS CO COM USD1 00"
    )


def test_normalize_asset_strips_trailing_digit_after_inc():
    # Trailing single digit after INC is OCR bleed, not a real share class.
    assert _normalize_asset("INTUIT INC 7") == "INTUIT INC"


def test_normalize_asset_strips_trailing_digit_after_lp():
    # Same pattern after LP.
    assert _normalize_asset("Mays Allocate 2025 LP 7") == "Mays Allocate 2025 LP"


def test_normalize_asset_keeps_short_numeric_after_inv_regression():
    # Regression guard for the protection branch — must still fire on INV.
    assert (
        _normalize_asset("CEDAR HOLDINGS LP INV 1292")
        == "CEDAR HOLDINGS LP INV 1292"
    )


def test_normalize_asset_keeps_short_numeric_after_usd1_regression():
    # Regression guard for the protection branch — must still fire on USD1.
    assert (
        _normalize_asset("GENUINE PARTS CO COM USD1 00")
        == "GENUINE PARTS CO COM USD1 00"
    )


def test_classify_header_single_tx_type_layout():
    headers = [
        "Holder",
        "Asset",
        "Transaction type",
        "Date of transaction",
        "Amount code",
    ]
    roles = classify_header(headers)
    assert roles == [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]


def test_classify_header_split_layout():
    headers = [
        "Holder",
        "Asset",
        "Purchase",
        "Sale",
        "Partial Sale",
        "Exchange",
        "Date of transaction",
        "Date notified of transaction",
        "Amount",
    ]
    roles = classify_header(headers)
    assert roles == [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.PURCHASE,
        ColumnRole.SALE,
        ColumnRole.PARTIAL_SALE,
        ColumnRole.EXCHANGE,
        ColumnRole.DATE_TX,
        ColumnRole.DATE_NOTIFIED,
        ColumnRole.AMOUNT,
    ]


def test_classify_header_is_case_insensitive_and_word_bounded():
    # lowercase should still classify; "RESALE" must NOT match SALE.
    roles = classify_header(["holder", "asset", "RESALE", "sale", "date"])
    assert roles == [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.OTHER,
        ColumnRole.SALE,
        ColumnRole.DATE_TX,
    ]


def test_rows_split_layout_sale():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.PURCHASE,
        ColumnRole.SALE,
        ColumnRole.PARTIAL_SALE,
        ColumnRole.EXCHANGE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["SP", "ASML HOLDING NV", "", "X", "", "", "03/31/2026", "A"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == [
        TransactionRow("SP", "ASML HOLDING NV", "SALE", "03/31/2026", "A"),
    ]


def test_rows_split_layout_partial_sale():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.PURCHASE,
        ColumnRole.SALE,
        ColumnRole.PARTIAL_SALE,
        ColumnRole.EXCHANGE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["SP", "FOO CORP", "", "", "X", "", "03/31/2026", "B"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == [
        TransactionRow("SP", "FOO CORP", "PARTIAL SALE", "03/31/2026", "B"),
    ]


def test_tx_type_value_is_case_insensitive():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["sp", "FOO CORP", "Partial Sale", "03/31/2026", "a"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == [
        TransactionRow("SP", "FOO CORP", "PARTIAL SALE", "03/31/2026", "A"),
    ]


def test_long_asset_with_leaked_tx_type_becomes_section_header():
    """A trust-name row (long asset) with leaked tx_type but no date or holder
    is rescued as a section header rather than being silently dropped."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 2006 DESCENDANT TRUST", "Purchase", "", "K"]]
    rows = rows_from_cell_texts(cells, roles)
    assert len(rows) == 1
    assert rows[0].is_section_header
    assert rows[0].asset == "LINDA MAYS MCCAUL 2006 DESCENDANT TRUST"


def test_garbage_row_with_leaked_amount_only_is_dropped():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "DE FULL", "", "", "A"]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == []


def test_asset_only_row_is_not_garbage_becomes_section_header():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 1999 EXEMPT TRUST", "", "", ""]]
    rows = rows_from_cell_texts(cells, roles)
    assert len(rows) == 1
    assert rows[0].is_section_header
    assert rows[0].asset == "LINDA MAYS MCCAUL 1999 EXEMPT TRUST"


def test_section_header_row():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 1999 EXEMPT TRUST", "", "", ""]]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == [
        TransactionRow.section_header("LINDA MAYS MCCAUL 1999 EXEMPT TRUST"),
    ]


def test_wrapped_asset_merges_into_previous_data_row():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [
        ["SP", "VANGUARD TOTAL STOCK", "Purchase", "03/27/2026", "A"],
        ["", "MARKET INDEX ADMIRAL", "", "", ""],
    ]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == [
        TransactionRow(
            "SP",
            "VANGUARD TOTAL STOCK MARKET INDEX ADMIRAL",
            "PURCHASE",
            "03/27/2026",
            "A",
        ),
    ]


def test_orphan_after_section_header_stays_section_header():
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [
        ["", "LINDA MAYS MCCAUL", "", "", ""],
        ["", "1999 EXEMPT TRUST", "", "", ""],
    ]
    rows = rows_from_cell_texts(cells, roles)
    # Both treated as section headers — second one not merged into the first
    assert all(r.is_section_header for r in rows)
    assert len(rows) == 2


def test_form_template_placeholder_rows_are_dropped():
    """Blank PTR rows leak the template prompt 'PROVIDE FULL NAME NOT TICKER
    SYMBOL' through OCR (with drift on the trailing tokens). They should not
    survive as data rows or section headers."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [
        ["JT", "PROVIDE FULL NAME NOT TICKER SYMBOL", "Purchase", "", "K"],
        ["DC", "PROVIDE FULL NAME NAT TICKER SYMBOL", "Purchase", "", "K"],
        ["", "provide full name not tucker symbol!", "", "", ""],
    ]
    rows = rows_from_cell_texts(cells, roles)
    assert rows == []


def test_section_header_with_tx_bleed_is_rescued():
    """A trust-name row with no holder and no date is rescued as a section header
    even when tx_type and amount_code have OCR bleed from adjacent cells."""
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.PURCHASE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cells = [["", "LINDA MAYS MCCAUL 1999 EXEMPT TRUST", "X", "", "A"]]
    rows = rows_from_cell_texts(cells, roles)
    assert len(rows) == 1
    assert rows[0].is_section_header
    assert rows[0].asset == "LINDA MAYS MCCAUL 1999 EXEMPT TRUST"


def test_normalize_asset_splits_glued_inc_suffix():
    assert _normalize_asset("INTUITINC") == "INTUIT INC"


def test_normalize_asset_splits_glued_inc_short_prefix():
    # PTC has only 3 letters before INC — must still split.
    assert _normalize_asset("PTCINC") == "PTC INC"


def test_normalize_asset_splits_glued_corp_suffix():
    assert _normalize_asset("ACMECORP") == "ACME CORP"


def test_normalize_asset_splits_glued_llc_suffix():
    assert _normalize_asset("FOOLLC") == "FOO LLC"


def test_normalize_asset_splits_plcshs_token():
    assert _normalize_asset("AON PLCSHS CL A") == "AON PLC SHS CL A"


def test_normalize_asset_splits_equportf_token():
    assert _normalize_asset("ALPHA EQUPORTF") == "ALPHA EQU PORTF"


def test_normalize_asset_splits_eqportf_token():
    assert _normalize_asset("BETA EQPORTF") == "BETA EQ PORTF"
