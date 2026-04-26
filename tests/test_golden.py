import re
from pathlib import Path

import pytest

from ocr_ptr_pdf_converter import convert

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "9115728.pdf"
FIXTURE_MD = Path(__file__).parent / "fixtures" / "9115728_expected.md"

ROW_RE = re.compile(
    r"^\|\s*(?P<holder>[^|]*?)\s*\|\s*(?P<asset>[^|]*?)\s*"
    r"\|\s*(?P<tx>[^|]*?)\s*\|\s*(?P<date>[^|]*?)\s*\|\s*(?P<amount>[^|]*?)\s*\|$"
)
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _canonical_date(s: str) -> str:
    # The expected fixture is internally inconsistent: page 1 uses `3/24/2026`
    # while later pages use `03/04/2026`. Canonicalize both sides to no leading
    # zeros so the comparison isn't gated on which page each row came from.
    m = _DATE_RE.match(s)
    if not m:
        return s
    mo, dy, yr = m.groups()
    return f"{int(mo)}/{int(dy)}/{yr}"


def _data_rows(md: str) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for line in md.splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        if m["holder"].lower() in {"holder", "---"}:
            continue
        non_asset = (m["holder"], m["tx"], m["date"], m["amount"])
        if all(c == "" for c in non_asset):
            continue
        rows.append(
            (
                m["holder"].strip(),
                m["asset"].strip().upper(),
                m["tx"].strip(),
                _canonical_date(m["date"].strip()),
                m["amount"].strip(),
            )
        )
    return rows


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="PDF fixture missing")
def test_golden_row_accuracy_at_least_95_percent():
    expected = _data_rows(FIXTURE_MD.read_text())
    actual = _data_rows(convert(FIXTURE_PDF))

    expected_counter: dict[tuple[str, str, str, str, str], int] = {}
    for row in expected:
        expected_counter[row] = expected_counter.get(row, 0) + 1

    matched = 0
    for row in actual:
        if expected_counter.get(row, 0) > 0:
            expected_counter[row] -= 1
            matched += 1

    total = len(expected)
    accuracy = matched / total if total else 0.0
    assert accuracy >= 0.95, (
        f"row accuracy {accuracy:.2%} ({matched}/{total}); "
        f"missed examples: {[r for r, c in expected_counter.items() if c > 0][:5]}"
    )
    assert len(actual) <= len(expected) * 1.5, (
        f"over-generation: {len(actual)} actual rows vs {len(expected)} expected; "
        f"accuracy metric only counts recall, so a flood of garbage rows would "
        f"otherwise be silently accepted."
    )


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="PDF fixture missing")
def test_golden_date_notified_exact():
    md = convert(FIXTURE_PDF)
    assert "**Date notified:** 4/6/2026" in md
