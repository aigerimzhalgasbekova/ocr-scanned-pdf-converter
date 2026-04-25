from __future__ import annotations

from ocr_ptr_pdf_converter.schema import Document, TransactionRow

_LEGEND = """## Amount code legend

| Code | Amount range |
|---|---|
| A | $1,000-$15,000 |
| B | $15,001-$50,000 |
| C | $50,001-$100,000 |
| D | $100,001-$250,000 |
| E | $250,001-$500,000 |
| F | $500,001-$1,000,000 |
| G | $1,000,001-$5,000,000 |
| H | $5,000,001-$25,000,000 |
| I | $25,000,001-$50,000,000 |
| J | Over $50,000,000 |
| K | Spouse/DC Amount over $1,000,000 |"""

_TABLE_HEADER = (
    "| Holder | Asset | Transaction type | Date of transaction | Amount code |\n"
    "|---|---|---|---|---|"
)


def _row_to_md(row: TransactionRow) -> str:
    return (
        f"| {row.holder} | {row.asset} | {row.transaction_type} "
        f"| {row.date_of_transaction} | {row.amount_code} |"
    )


def render(doc: Document) -> str:
    parts: list[str] = [f"# OCR conversion for {doc.source_filename}", ""]
    if doc.date_notified:
        parts.append(f"**Date notified:** {doc.date_notified}")
        parts.append("")
    parts.append(_LEGEND)
    parts.append("")
    for page in doc.pages:
        parts.append(f"## Page {page.page_number}")
        parts.append("")
        parts.append(_TABLE_HEADER)
        for row in page.rows:
            parts.append(_row_to_md(row))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
