"""One-off diagnostic for Task 11.

Runs convert() on the golden fixture (cached to output/9115728_actual.md so
re-runs of just the analysis are instant), parses both actual and expected
markdown into 5-tuples, then bins the mismatches by likely cause.

Usage:
    uv run python scripts/diagnose_golden.py            # use cached OCR if present
    uv run python scripts/diagnose_golden.py --refresh  # re-run OCR
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE_PDF = REPO / "tests" / "fixtures" / "9115728.pdf"
FIXTURE_MD = REPO / "tests" / "fixtures" / "9115728_expected.md"
CACHE_MD = REPO / "output" / "9115728_actual.md"

ROW_RE = re.compile(
    r"^\|\s*(?P<holder>[^|]*?)\s*\|\s*(?P<asset>[^|]*?)\s*"
    r"\|\s*(?P<tx>[^|]*?)\s*\|\s*(?P<date>[^|]*?)\s*\|\s*(?P<amount>[^|]*?)\s*\|$"
)
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

FIELDS = ("holder", "asset", "tx", "date", "amount")


def _canonical_date(s: str) -> str:
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


def _ensure_actual(refresh: bool) -> str:
    if CACHE_MD.exists() and not refresh:
        return CACHE_MD.read_text()
    from ocr_ptr_pdf_converter import convert

    md = convert(FIXTURE_PDF)
    CACHE_MD.parent.mkdir(parents=True, exist_ok=True)
    CACHE_MD.write_text(md)
    return md


def _consume_exact_matches(
    expected: list[tuple[str, ...]], actual: list[tuple[str, ...]]
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    """Pop exact matches from both lists; return remaining (expected, actual)."""
    counter = Counter(expected)
    remaining_actual: list[tuple[str, ...]] = []
    consumed_expected: Counter[tuple[str, ...]] = Counter()
    for row in actual:
        if counter[row] > 0:
            counter[row] -= 1
            consumed_expected[row] += 1
        else:
            remaining_actual.append(row)
    remaining_expected: list[tuple[str, ...]] = []
    expected_counter = Counter(expected)
    for row, n in expected_counter.items():
        kept = n - consumed_expected[row]
        remaining_expected.extend([row] * kept)
    return remaining_expected, remaining_actual


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _row_score(exp: tuple[str, ...], act: tuple[str, ...]) -> tuple[int, float]:
    """Return (#exact-field-matches, asset-similarity) — used to find best
    near-match candidate for an expected row among actuals."""
    exact = sum(1 for ef, af in zip(exp, act, strict=True) if ef == af)
    return exact, _similarity(exp[1], act[1])


def _classify_mismatch(exp: tuple[str, ...], act: tuple[str, ...]) -> str:
    diffs = [f for f, ef, af in zip(FIELDS, exp, act, strict=True) if ef != af]
    if len(diffs) == 1:
        return f"{diffs[0]}_only_drift"
    if len(diffs) == 2:
        return f"two_field_drift({'+'.join(sorted(diffs))})"
    return f"many_field_drift({len(diffs)})"


def diagnose(actual_md: str, expected_md: str) -> None:
    actual = _data_rows(actual_md)
    expected = _data_rows(expected_md)

    print(f"actual rows : {len(actual)}")
    print(f"expected rows: {len(expected)}")
    print(f"row-count delta: {len(actual) - len(expected):+d}")
    print()

    rem_exp, rem_act = _consume_exact_matches(expected, actual)
    exact_matches = len(expected) - len(rem_exp)
    print(f"exact matches: {exact_matches}/{len(expected)} = {exact_matches/len(expected):.1%}")
    print(f"unmatched expected: {len(rem_exp)}")
    print(f"unmatched actual  : {len(rem_act)}")
    print()

    # For each remaining expected row, find best near-match in remaining actuals
    # (≥2 exact field matches OR asset similarity ≥0.6). Greedy assignment.
    used_actual: set[int] = set()
    bins: Counter[str] = Counter()
    missing: list[tuple[str, ...]] = []
    pairs: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []

    for exp in rem_exp:
        best_idx = -1
        best_score = (-1, -1.0)
        for i, act in enumerate(rem_act):
            if i in used_actual:
                continue
            score = _row_score(exp, act)
            if score > best_score:
                best_score = score
                best_idx = i
        # Threshold: at least 2 exact field matches OR asset similarity ≥0.6
        if best_idx >= 0 and (best_score[0] >= 2 or best_score[1] >= 0.6):
            used_actual.add(best_idx)
            cls = _classify_mismatch(exp, rem_act[best_idx])
            bins[cls] += 1
            pairs.append((exp, rem_act[best_idx], cls))
        else:
            bins["missing_entirely"] += 1
            missing.append(exp)

    extras = [a for i, a in enumerate(rem_act) if i not in used_actual]

    print("=== mismatch histogram ===")
    for cls, n in sorted(bins.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {cls}")
    print(f"  ----")
    print(f"  {len(extras):4d}  extra_actual_rows (no expected near-match)")
    print()

    print("=== sample drifts (up to 6 per category) ===")
    by_cat: dict[str, list[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
    for exp, act, cls in pairs:
        by_cat.setdefault(cls, []).append((exp, act))
    for cls, items in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{cls}]  ({len(items)} cases)")
        for exp, act in items[:6]:
            print(f"  exp: {exp}")
            print(f"  act: {act}")
            print()

    if missing:
        print("=== expected rows with NO near-match in actual (sample 10) ===")
        for r in missing[:10]:
            print(f"  {r}")
        print(f"  ... ({len(missing)} total)")
        print()

    if extras:
        print("=== extra actual rows with no expected near-match (sample 10) ===")
        for r in extras[:10]:
            print(f"  {r}")
        print(f"  ... ({len(extras)} total)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true", help="re-run OCR (slow)")
    args = p.parse_args()
    actual_md = _ensure_actual(refresh=args.refresh)
    expected_md = FIXTURE_MD.read_text()
    diagnose(actual_md, expected_md)


if __name__ == "__main__":
    main()
