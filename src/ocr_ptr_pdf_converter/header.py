from __future__ import annotations

from collections.abc import Iterable


def pick_date_notified(values: Iterable[str]) -> str:
    for v in values:
        s = v.strip()
        if s:
            return s
    return ""
