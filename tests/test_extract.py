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


def _row_for_section_header_test(asset: str = "LONG ASSET NAME HERE") -> TransactionRow:
    # A row matching the OLD section-header trigger: no holder, no date,
    # asset >= 12 chars, and a tx_type/amount mark set.
    return TransactionRow(
        holder="",
        asset=asset,
        transaction_type="PURCHASE",
        date_of_transaction="",
        amount_code="C",
    )


def test_is_noisy_section_header_demotes_when_date_ink_low():
    from ocr_ptr_pdf_converter.extract import _is_noisy_section_header

    row = _row_for_section_header_test()
    # Low date-column ink → genuine section header, demote.
    assert _is_noisy_section_header(row, date_density=0.10) is True


def test_is_noisy_section_header_preserves_when_date_ink_high():
    from ocr_ptr_pdf_converter.extract import _is_noisy_section_header

    row = _row_for_section_header_test()
    # High date-column ink (printed date present, OCR just failed) → real row.
    assert _is_noisy_section_header(row, date_density=0.30) is False


def test_row_from_cells_sp_default_fires_on_date_ink():
    from ocr_ptr_pdf_converter.extract import _row_from_cells

    # Cell layout: [HOLDER, ASSET, TX_TYPE, DATE_TX, AMOUNT]
    # Holder text is empty (OCR failed), date text is empty (OCR failed),
    # but date_density signals the printed date is visually present.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    texts = ["", "ACME CORP COM", "PURCHASE", "", "C"]
    row = _row_from_cells(texts, roles, date_density=0.30)
    assert row.holder == "SP"


def test_row_from_cells_sp_default_skipped_on_empty_date_column():
    from ocr_ptr_pdf_converter.extract import _row_from_cells

    # Same shape but the date column has no ink — the SP-default must NOT
    # invent a holder, since there's no evidence the row is real.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    texts = ["", "ACME CORP COM", "PURCHASE", "", "C"]
    row = _row_from_cells(texts, roles, date_density=0.05)
    assert row.holder == ""


def test_rows_from_cell_texts_preserves_real_row_with_date_ink():
    # Row matches OLD section-header trigger (no holder, no date string,
    # long asset, tx_type set) BUT date_density is high → must NOT be demoted.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cell_rows = [["", "VANGUARD INDEX FUNDS S&P 500 ETF USD", "PURCHASE", "", "A"]]
    date_densities = [0.28]
    out = rows_from_cell_texts(cell_rows, roles, date_densities)
    assert len(out) == 1
    assert out[0].is_section_header is False
    assert out[0].asset == "VANGUARD INDEX FUNDS S&P 500 ETF USD"
    assert out[0].holder == "SP"  # SP-default fallback fired on date ink
    assert out[0].transaction_type == "PURCHASE"
    assert out[0].amount_code == "A"


def test_rows_from_cell_texts_demotes_genuine_section_header():
    # Same shape but date_density is low → genuine section header, demote.
    roles = [
        ColumnRole.HOLDER,
        ColumnRole.ASSET,
        ColumnRole.TX_TYPE,
        ColumnRole.DATE_TX,
        ColumnRole.AMOUNT,
    ]
    cell_rows = [["", "LLM FAMILY INVESTMENTS II LP", "PURCHASE", "", "C"]]
    date_densities = [0.12]
    out = rows_from_cell_texts(cell_rows, roles, date_densities)
    assert len(out) == 1
    assert out[0].is_section_header is True
    assert out[0].asset == "LLM FAMILY INVESTMENTS II LP"
