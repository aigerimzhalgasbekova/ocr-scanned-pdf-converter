from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HOLDERS = frozenset({"JT", "SP", "DC"})
TX_TYPES: tuple[str, str, str, str] = ("PURCHASE", "SALE", "PARTIAL SALE", "EXCHANGE")
AMOUNT_CODES: tuple[str, ...] = tuple("ABCDEFGHIJK")

Holder = Literal["JT", "SP", "DC", ""]
TxType = Literal["PURCHASE", "SALE", "PARTIAL SALE", "EXCHANGE", ""]
AmountCode = Literal[
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", ""
]


@dataclass(frozen=True)
class TransactionRow:
    holder: Holder
    asset: str
    transaction_type: TxType
    date_of_transaction: str
    amount_code: AmountCode
    is_section_header: bool = False

    @classmethod
    def section_header(cls, name: str) -> TransactionRow:
        return cls(
            holder="",
            asset=name,
            transaction_type="",
            date_of_transaction="",
            amount_code="",
            is_section_header=True,
        )


@dataclass(frozen=True)
class PageResult:
    page_number: int
    rotation: int
    rows: tuple[TransactionRow, ...] = ()


@dataclass(frozen=True)
class Document:
    source_filename: str
    date_notified: str
    pages: tuple[PageResult, ...] = ()
