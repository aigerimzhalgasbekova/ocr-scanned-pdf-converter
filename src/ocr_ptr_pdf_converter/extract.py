from __future__ import annotations

import re
from enum import Enum

from ocr_ptr_pdf_converter.schema import (
    AMOUNT_CODES,
    HOLDERS,
    TX_TYPES,
    TransactionRow,
)


class ColumnRole(Enum):
    HOLDER = "holder"
    ASSET = "asset"
    TX_TYPE = "tx_type"
    PURCHASE = "purchase"
    SALE = "sale"
    PARTIAL_SALE = "partial_sale"
    EXCHANGE = "exchange"
    DATE_TX = "date_tx"
    DATE_NOTIFIED = "date_notified"
    AMOUNT = "amount"
    OTHER = "other"


# Word-boundary, case-insensitive patterns. Order matters: longer / more
# specific phrases come first so e.g. "PARTIAL SALE" is not consumed by SALE,
# and "DATE NOTIFIED" is not consumed by DATE.
_ROLE_PATTERNS: list[tuple[ColumnRole, re.Pattern[str]]] = [
    (ColumnRole.TX_TYPE, re.compile(r"\btransaction\s+type\b", re.IGNORECASE)),
    (ColumnRole.DATE_NOTIFIED, re.compile(r"\bdate\s+notified\b", re.IGNORECASE)),
    (ColumnRole.PARTIAL_SALE, re.compile(r"\bpartial\s+sale\b", re.IGNORECASE)),
    (ColumnRole.HOLDER, re.compile(r"\b(holder|owner)\b", re.IGNORECASE)),
    (ColumnRole.PURCHASE, re.compile(r"\bpurchase\b", re.IGNORECASE)),
    (ColumnRole.SALE, re.compile(r"\bsale\b", re.IGNORECASE)),
    (ColumnRole.EXCHANGE, re.compile(r"\bexchange\b", re.IGNORECASE)),
    (ColumnRole.DATE_TX, re.compile(r"\bdate\b", re.IGNORECASE)),
    (ColumnRole.AMOUNT, re.compile(r"\bamount\b", re.IGNORECASE)),
    (ColumnRole.ASSET, re.compile(r"\basset\b", re.IGNORECASE)),
]

# Case-insensitive lookup for transaction-type values.
_TX_TYPE_BY_UPPER = {t.upper(): t for t in TX_TYPES}


def classify_header(headers: list[str]) -> list[ColumnRole]:
    roles: list[ColumnRole] = []
    for h in headers:
        matched = ColumnRole.OTHER
        for role, pattern in _ROLE_PATTERNS:
            if pattern.search(h):
                matched = role
                break
        roles.append(matched)
    return roles


def _normalize_holder(text: str) -> str:
    cleaned = text.strip().upper()
    return cleaned if cleaned in HOLDERS else ""


def _normalize_amount(text: str) -> str:
    cleaned = text.strip().upper()
    return cleaned if cleaned in AMOUNT_CODES else ""


def _normalize_tx_type(text: str) -> str:
    return _TX_TYPE_BY_UPPER.get(text.strip().upper(), "")


def _is_marked(text: str) -> bool:
    return "X" in text.strip().upper()


def collect_column(
    cell_rows: list[list[str]], roles: list[ColumnRole], target: ColumnRole
) -> list[str]:
    """Return the values of every cell whose role matches `target`,
    row-major, including blanks. Used by cli.py to gather DATE_NOTIFIED
    values across pages for header.pick_date_notified."""
    indices = [i for i, r in enumerate(roles) if r is target]
    out: list[str] = []
    for texts in cell_rows:
        for i in indices:
            if i < len(texts):
                out.append(texts[i])
    return out


def _row_from_cells(
    texts: list[str], roles: list[ColumnRole]
) -> TransactionRow:
    holder = ""
    asset_parts: list[str] = []
    tx_type = ""
    date_tx = ""
    amount = ""
    purchase = sale = partial_sale = exchange = False

    for text, role in zip(texts, roles, strict=True):
        if role is ColumnRole.HOLDER:
            holder = _normalize_holder(text)
        elif role is ColumnRole.ASSET:
            stripped = text.strip()
            if stripped:
                asset_parts.append(stripped)
        elif role is ColumnRole.TX_TYPE:
            tx_type = _normalize_tx_type(text)
        elif role is ColumnRole.PURCHASE:
            purchase = _is_marked(text)
        elif role is ColumnRole.SALE:
            sale = _is_marked(text)
        elif role is ColumnRole.PARTIAL_SALE:
            partial_sale = _is_marked(text)
        elif role is ColumnRole.EXCHANGE:
            exchange = _is_marked(text)
        elif role is ColumnRole.DATE_TX:
            date_tx = text.strip()
        elif role is ColumnRole.AMOUNT:
            amount = _normalize_amount(text)
        # DATE_NOTIFIED and OTHER are intentionally ignored at the row level;
        # DATE_NOTIFIED is harvested separately via collect_column.

    if not tx_type:
        if partial_sale:
            tx_type = "PARTIAL SALE"
        elif purchase:
            tx_type = "PURCHASE"
        elif sale:
            tx_type = "SALE"
        elif exchange:
            tx_type = "EXCHANGE"

    asset = " ".join(asset_parts)
    return TransactionRow(
        holder=holder,
        asset=asset,
        transaction_type=tx_type,
        date_of_transaction=date_tx,
        amount_code=amount,
    )


def _is_orphan(row: TransactionRow) -> bool:
    return (
        not row.holder
        and not row.transaction_type
        and not row.date_of_transaction
        and not row.amount_code
        and bool(row.asset)
    )


def rows_from_cell_texts(
    cell_rows: list[list[str]], roles: list[ColumnRole]
) -> list[TransactionRow]:
    out: list[TransactionRow] = []
    for texts in cell_rows:
        row = _row_from_cells(texts, roles)
        if _is_orphan(row):
            if out and not out[-1].is_section_header and not _is_orphan(out[-1]):
                prev = out[-1]
                merged = TransactionRow(
                    holder=prev.holder,
                    asset=f"{prev.asset} {row.asset}".strip(),
                    transaction_type=prev.transaction_type,
                    date_of_transaction=prev.date_of_transaction,
                    amount_code=prev.amount_code,
                )
                out[-1] = merged
            else:
                out.append(TransactionRow.section_header(row.asset))
        else:
            out.append(row)
    return out
