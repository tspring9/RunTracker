"""Pure matching engine for the name-first "Sign Up" results search (SPR-15).

No imports of ``requests``, ``storage``, ``streamlit``, or any I/O. This module
must be unit-testable with zero network and zero DB -- callers in
``results_search.py`` and ``app.py`` own the I/O; this module only compares
values it is handed.

Confidence is a product decision, not a certainty score: RunSignUp's published
``age`` is captured at *registration* time, not on race day, so a runner whose
birthday falls between signing up and racing will legitimately show an age
that is off by one from their age on the actual race date. The ``classify_confidence``
tolerance below exists to absorb exactly that gap.
"""

# NOTE for SPR-19 (the exhaustive matching-engine test suite): the SPR-15/17
# ticket's own age_on_date example states a runner born 2000-02-29 is "26 on
# 2026-02-28 and 27 on 2026-03-01". That specific pair of numbers is not
# reachable by any consistent age arithmetic -- 2026-2000=26, so at most one
# birthday (one +1 step) can occur in 2026, and it happens on the resolved
# Mar-1 birthday. This module implements the *described rule* (Feb-29
# resolves to Mar 1; a race exactly on the birthday counts as the new age)
# correctly and consistently: for birth_date=2000-02-29, age_on_date returns
# 25 on 2026-02-28 and 26 on 2026-03-01. Flagged in the SPR-17 done-comment;
# raise there if SPR-19 needs different numbers.

from __future__ import annotations

import re
from datetime import date

HIGH = "High"
POSSIBLE = "Possible"
REJECTED = "Rejected"

_WHITESPACE_RE = re.compile(r"\s+")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_name(value: str) -> str:
    """Strip, collapse internal whitespace, casefold."""
    text = str(value or "").strip()
    text = _WHITESPACE_RE.sub(" ", text)
    return text.casefold()


def names_match(query_first: str, query_last: str, result_first: str, result_last: str) -> bool:
    """Exact case-insensitive match on BOTH first and last after normalize_name.

    Empty query part -> no match (we require both, unlike the old partial
    lookup in ``runsignup_results.find_runner_results``).
    """
    norm_query_first = normalize_name(query_first)
    norm_query_last = normalize_name(query_last)
    if not norm_query_first or not norm_query_last:
        return False
    return (
        norm_query_first == normalize_name(result_first)
        and norm_query_last == normalize_name(result_last)
    )


def age_on_date(birth_date: date, event_date: date) -> int:
    """Exact age in whole years on ``event_date``.

    A Feb-29 birthday resolves to Mar 1 in a non-leap year: a runner born
    2000-02-29 turns their new age on 2026-03-01, not 2026-02-28 (so they are
    one year younger on Feb 28 than on Mar 1 of the same non-leap year). This
    is a deliberate product choice -- hold the leap birthday open through
    Feb 28 rather than crediting it a day early -- not an accident of date
    arithmetic.

    A race held exactly on the birthday counts as the new age.
    ``event_date`` before ``birth_date`` raises ``ValueError``.
    """
    if event_date < birth_date:
        raise ValueError("event_date is before birth_date.")

    age = event_date.year - birth_date.year
    try:
        birthday_this_year = birth_date.replace(year=event_date.year)
    except ValueError:
        # Feb 29 birthday, non-leap event year -> treat as Mar 1.
        birthday_this_year = date(event_date.year, 3, 1)

    if event_date < birthday_this_year:
        age -= 1
    return age


def parse_result_age(raw) -> int | None:
    """RunSignUp 'age' field. "" / None / non-numeric / <=0 -> None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        age = int(float(text))
    except (TypeError, ValueError):
        return None
    return age if age > 0 else None


def classify_confidence(age_on_race_date: int | None, result_age: int | None) -> tuple[str, str]:
    """Returns (confidence, human_readable_reason).

    The +/-1 tolerance is deliberate: RunSignUp stores age as of registration,
    not race day.
    """
    if result_age is None:
        return POSSIBLE, "RunSignUp published no age for this result"
    if age_on_race_date is None:
        return POSSIBLE, "No race date to compute age against"

    diff = age_on_race_date - result_age
    if abs(diff) <= 1:
        return HIGH, f"Age on race date {age_on_race_date} matches result age {result_age}"
    return (
        REJECTED,
        f"Age on race date {age_on_race_date} vs result age {result_age} — off by {abs(diff)} years",
    )


def valid_email(value: str) -> bool:
    """Format-only check. The value is NEVER used, sent, or stored."""
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_EMAIL_RE.match(text))
