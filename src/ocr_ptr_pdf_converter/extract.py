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


# Number of amount-code mark cells in a standard PTR form: A through K.
_AMOUNT_MARK_COLS = 11
# Letters mapped position-by-position to those amount mark columns.
_AMOUNT_LETTERS = "ABCDEFGHIJK"
# Title-cased transaction types corresponding to the narrow mark columns
# between ASSET and DATE_TX in the standard PTR layout. The form has at most
# four such columns: Purchase, Sale, Partial sale, Exchange.
_TX_MARK_ROLES = (
    ColumnRole.PURCHASE,
    ColumnRole.SALE,
    ColumnRole.PARTIAL_SALE,
    ColumnRole.EXCHANGE,
)


def infer_roles_by_position(
    cols: list[tuple[int, int]], existing: list[ColumnRole]
) -> list[ColumnRole]:
    """Infer column roles purely from cell widths/positions when header-text
    classification failed (most roles == OTHER). Returns the unchanged list
    when the column shape doesn't match the known PTR form, so the caller can
    decide whether to fall back.

    Expected layout (left-to-right, portrait page):
        [HOLDER (narrow)] [ASSET (very wide)]
        [N x TX_TYPE marks (narrow)]
        [DATE_TX (medium)] [DATE_NOTIFIED (medium)]
        [11 x AMOUNT marks (narrow)] -> A..K
    """
    n = len(cols)
    if n < 2 + _AMOUNT_MARK_COLS:
        return existing

    widths = [x1 - x0 for x0, x1 in cols]
    # Asset col is the widest by far. Require it to be >= 800 px (300dpi page).
    asset_idx = max(range(n), key=lambda i: widths[i])
    if widths[asset_idx] < 800:
        return existing

    # Holder col is the cell immediately to the left of asset; must be narrow
    # but not microscopic (>=80 px) so we don't pick a stray sliver.
    if asset_idx == 0 or widths[asset_idx - 1] < 80:
        return existing
    holder_idx = asset_idx - 1

    # The last `_AMOUNT_MARK_COLS` columns are amount marks.
    if n - asset_idx - 1 < _AMOUNT_MARK_COLS + 2:
        return existing
    amount_start = n - _AMOUNT_MARK_COLS

    # The two columns immediately before amount marks are the dates: tx then
    # notified. Both should be wider than mark cells (>=120 px each).
    date_notified_idx = amount_start - 1
    date_tx_idx = amount_start - 2
    if widths[date_tx_idx] < 120 or widths[date_notified_idx] < 120:
        return existing

    roles: list[ColumnRole] = [ColumnRole.OTHER] * n
    roles[holder_idx] = ColumnRole.HOLDER
    roles[asset_idx] = ColumnRole.ASSET
    roles[date_tx_idx] = ColumnRole.DATE_TX
    roles[date_notified_idx] = ColumnRole.DATE_NOTIFIED
    for k in range(_AMOUNT_MARK_COLS):
        roles[amount_start + k] = ColumnRole.AMOUNT

    # Narrow mark columns between asset and date_tx are tx-type marks. Map
    # them left-to-right to (Purchase, Sale, Partial sale, Exchange). If the
    # form has fewer than 4 cells here we just truncate; if more we still map
    # the first 4 and leave the rest as OTHER (defensive).
    tx_mark_indices = list(range(asset_idx + 1, date_tx_idx))
    for slot, idx in enumerate(tx_mark_indices[: len(_TX_MARK_ROLES)]):
        roles[idx] = _TX_MARK_ROLES[slot]
    return roles


def amount_letter_for_index(roles: list[ColumnRole], col_index: int) -> str:
    """Return the letter A..K for an AMOUNT column at `col_index`, computed
    from position among AMOUNT-role columns left-to-right."""
    seen = -1
    for i, r in enumerate(roles):
        if r is ColumnRole.AMOUNT:
            seen += 1
            if i == col_index:
                if 0 <= seen < len(_AMOUNT_LETTERS):
                    return _AMOUNT_LETTERS[seen]
                return ""
    return ""


# Noise-only tokens have no letters or digits at all (pure punctuation).
_NOISE_TOKEN_RE = re.compile(r"^[^A-Za-z0-9]+$")
# A trailing token is "junk" if it has no letters: stray digits like "7" or
# OCR remnants like "_", "~". We trim those because they are typically table
# rule fragments bleeding into the asset cell.
_TRAIL_NOLETTERS_RE = re.compile(r"^[^A-Za-z]+$")
# A trailing token is also junk when it's 1-2 ALL-CAPS chars with vowels in
# odd places (e.g. 'BE', 'EE', 'OS', 'PE') AND the prior token already ends
# the asset name. Whitelist real asset suffixes so we don't munch them.
_REAL_SHORT_SUFFIXES = frozenset(
    {
        "LP",
        "INC",
        "CO",
        "CORP",
        "NV",
        "AG",
        "ADR",
        "ETF",
        "USD",
        "REV",
        "DEV",
        "REF",
        "AUTH",
        "TR",
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "AGY",
        "FIN",
        "FUND",
        "FDS",
        "GO",
        "DEPT",
        "FED",
        "BD",
        "INST",
        "INSTL",
        "LLC",
        "PLC",
        "GST",
        "DC",
        "CL",
        "SP",
        "JT",
        "L",
        "M",
        "N",
        "O",
        "P",
        "Q",
        "R",
        "S",
        "T",
        "U",
        "W",
        "X",
        "Y",
        "Z",
        "MKT",
        "STK",
        "INDEX",
        "INDICES",
        "AOR",
    }
)

# Amount-code letters (A-K) that can appear as trailing OCR junk after a
# company-suffix token (e.g. "INTUIT INC A"). Kept separate from
# _REAL_SHORT_SUFFIXES so they can receive context-sensitive treatment.
_AK_LETTERS = frozenset("ABCDEFGHIJK")
# When an asset ends with a single A-K letter and the preceding token is one
# of these company suffixes, that trailing letter is OCR bleed, not a share
# class. "INTUIT INC A" → "INTUIT INC". "CL A" is preserved (prev="CL").
_COMPANY_TRAILING_SUFFIXES = frozenset(
    {"INC", "LP", "CORP", "LLC", "CO", "PLC", "ADR", "NV", "AG", "ETF"}
)

# Glued OCR tokens we know how to split. Implemented as an exact-token table
# rather than a greedy regex so we cannot accidentally split legitimate words.
_GLUED_TOKEN_SPLITS = {
    "PLCSHS": "PLC SHS",
    "EQUPORTF": "EQU PORTF",
    "EQPORTF": "EQ PORTF",
}

# Tokens that legitimately precede a short digit-only tail in an asset name
# (e.g. "INV 1292", "COM USD1 00"). Used by _normalize_asset's tail-trim loop.
_NUMERIC_TAIL_ANCHORS = frozenset({"INV", "COM", "USD1"})

# Date-column ink density above this means a printed date is present, even if
# OCR couldn't extract a date string. Empirically-set: empty date cells (table
# rules + scan noise) sit at 0.10–0.20 in 9115728.pdf; printed dates ≥ 0.25.
_DATE_INK_PRESENT_DENSITY = 0.22


def _normalize_asset(raw: str) -> str:
    """Clean a single OCR'd asset cell: strip leading/trailing junk pipes,
    collapse whitespace, apply pattern fixes, drop trailing OCR remnants."""
    s = raw.strip().lstrip("|}]").rstrip("|}]").strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return ""
    # { or } adjacent to a letter is an OCR curly-brace/I confusion.
    s = re.sub(r"(?<=[A-Za-z])[{}]|[{}](?=[A-Za-z])", "I", s)
    # "CLA" / "SHSCLA" → "CL A" / "SHS CL A" (share-class designator).
    s = re.sub(r"\bCL([A-K])\b", r"CL \1", s)
    # Split glued company suffix: "INTUITINC" → "INTUIT INC", "PTCINC" → "PTC INC".
    # Allow prefix ≥ 2 chars to catch short tickers like "PTC". The suffix list is
    # closed (INC|LLC|CORP|PLC) so this can't munch real words.
    s = re.sub(r"\b([A-Z]{2,})(INC|LLC|CORP|PLC)\b", r"\1 \2", s)
    # "ARTHURJ" → "ARTHUR J": last-name initial appended without space.
    # Require prefix ≥ 6 chars so real surnames like "MCCAUL" are not split.
    # Restricted to J only: broader chars like L/M/P/T cause false splits in
    # real words (e.g. ADMIRAL → ADMIRA L).
    s = re.sub(r"\b([A-Z]{6,})J\b", r"\1 J", s)
    tokens = []
    for tok in s.split(" "):
        replacement = _GLUED_TOKEN_SPLITS.get(tok.upper())
        if replacement:
            tokens.extend(replacement.split(" "))
        else:
            tokens.append(tok)
    while tokens:
        t = tokens[-1]
        if _NOISE_TOKEN_RE.match(t):
            tokens.pop()
            continue
        if _TRAIL_NOLETTERS_RE.match(t):
            # Protect a short digit-only tail when the previous token is a
            # known asset-tail anchor (e.g. "INV 1292", "USD1 00"). These
            # are real fragments of asset descriptions, not table-rule junk.
            prev_upper = tokens[-2].upper() if len(tokens) >= 2 else ""
            if t.isdigit() and len(t) <= 4 and prev_upper in _NUMERIC_TAIL_ANCHORS:
                break
            tokens.pop()
            continue
        if len(t) <= 2 and t.upper() not in _REAL_SHORT_SUFFIXES and not t.isalpha():
            tokens.pop()
            continue
        # Single A-K letter: drop only when preceded by a company-suffix token.
        # This strips "INTUIT INC A" → "INTUIT INC" while preserving "CL A".
        if len(t) == 1 and t.upper() in _AK_LETTERS:
            prev = tokens[-2].upper() if len(tokens) >= 2 else ""
            if prev in _COMPANY_TRAILING_SUFFIXES:
                tokens.pop()
                continue
            break  # Preceded by something else (e.g. "CL") — keep the letter.
        if len(t) <= 2 and t.isalpha() and t.upper() not in _REAL_SHORT_SUFFIXES:
            tokens.pop()
            continue
        break
    while tokens and _NOISE_TOKEN_RE.match(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens).strip()


def _normalize_holder(text: str) -> str:
    """Tolerant holder normalization. The PTR holder cell contains a small
    mark beside one of (JT, SP, DC); OCR often returns garbage like '5P', 'sp.',
    'JT.', or just stray punctuation. We try a few common confusions before
    giving up."""
    cleaned = text.strip().upper()
    if cleaned in HOLDERS:
        return cleaned
    # Strip non-letters and re-check (handles "SP.", "| SP |").
    letters = re.sub(r"[^A-Z]", "", cleaned)
    if letters in HOLDERS:
        return letters
    # Common OCR confusions: 5 -> S, 0 -> O, 1 -> I.
    fuzzed = letters.translate(str.maketrans({"5": "S", "0": "O", "1": "I"}))
    if fuzzed in HOLDERS:
        return fuzzed
    # Last resort: if any holder code appears as a substring of the letters,
    # accept it (e.g. "ASPL" would still be too noisy, but "XSP" -> SP).
    for h in HOLDERS:
        if h in fuzzed:
            return h
    return ""


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
    texts: list[str], roles: list[ColumnRole], date_density: float
) -> TransactionRow:
    holder = ""
    asset_parts: list[str] = []
    tx_type = ""
    date_tx = ""
    amount = ""
    purchase = sale = partial_sale = exchange = False
    amount_letters: list[str] = []
    amount_idx = 0

    for text, role in zip(texts, roles, strict=True):
        if role is ColumnRole.HOLDER:
            holder = _normalize_holder(text)
        elif role is ColumnRole.ASSET:
            cleaned = _normalize_asset(text)
            if cleaned:
                asset_parts.append(cleaned)
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
            normalized = _normalize_amount(text)
            if normalized:
                amount = normalized
            elif _is_marked(text) and amount_idx < len(_AMOUNT_LETTERS):
                amount_letters.append(_AMOUNT_LETTERS[amount_idx])
            amount_idx += 1
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

    if not amount and amount_letters:
        # Pick the first marked column. If multiple are marked the leftmost
        # wins (matches how the form is usually filled — a single mark).
        amount = amount_letters[0]

    if not holder and asset_parts and tx_type and (
        date_tx or date_density >= _DATE_INK_PRESENT_DENSITY
    ):
        # Form's holder column is a sub-checkbox grid (JT/SP/DC). When OCR
        # cannot read the label, default to SP for fully-populated rows —
        # SP is the only holder that appears in the v0.2.0 fixture corpus.
        # The row must have asset + tx_type and EITHER a date string OR
        # clearly-printed ink in the date column (so we don't invent holders
        # for noise rows where the date column is also empty).
        holder = "SP"

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


def _is_empty(row: TransactionRow) -> bool:
    return not (
        row.holder
        or row.asset
        or row.transaction_type
        or row.date_of_transaction
        or row.amount_code
    )


# Form template prompt that bleeds through OCR on otherwise-blank rows. The
# canonical text is "PROVIDE FULL NAME NOT TICKER SYMBOL" but OCR drifts the
# trailing words ("TUCKER", "TICKER SYMBO!", etc.) — match on the stable
# "PROVIDE FULL NAME" prefix.
_PLACEHOLDER_RE = re.compile(r"PROVIDE\s+FULL\s+NAME", re.IGNORECASE)


def _is_placeholder(row: TransactionRow) -> bool:
    return bool(_PLACEHOLDER_RE.search(row.asset))


def _is_garbage(row: TransactionRow) -> bool:
    return (
        not row.holder
        and not row.date_of_transaction
        and bool(row.transaction_type or row.amount_code)
    )


def _is_noisy_section_header(row: TransactionRow, date_density: float) -> bool:
    """A long-asset row with no holder and no date, but with OCR bleed in tx_type
    or amount_code from adjacent cells — should be a section header, not garbage.

    `date_density` is the per-row ink density of the DATE_TX column. When the
    date column has clearly-printed ink (>= _DATE_INK_PRESENT_DENSITY) we treat
    this as a real row whose date OCR failed, not a section header."""
    if date_density >= _DATE_INK_PRESENT_DENSITY:
        return False
    return (
        not row.holder
        and not row.date_of_transaction
        and len(row.asset) >= 12
        and bool(row.transaction_type or row.amount_code)
    )


def rows_from_cell_texts(
    cell_rows: list[list[str]],
    roles: list[ColumnRole],
    date_densities: list[float] | None = None,
) -> list[TransactionRow]:
    if date_densities is None:
        date_densities = [0.0] * len(cell_rows)
    out: list[TransactionRow] = []
    for texts, date_density in zip(cell_rows, date_densities, strict=True):
        row = _row_from_cells(texts, roles, date_density)
        if _is_empty(row):
            # Wholly blank row — skip so we don't pollute the markdown with
            # empty separator rows that count against over-generation.
            continue
        if _is_placeholder(row):
            continue
        if _is_noisy_section_header(row, date_density):
            out.append(TransactionRow.section_header(row.asset))
            continue
        if _is_garbage(row):
            continue
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
