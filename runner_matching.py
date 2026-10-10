"""Pure matching engine for the name-first "Sign Up" results search (SPR-31).

No imports of ``requests``, ``storage``, ``streamlit``, or any I/O. This module
must be unit-testable with zero network and zero DB -- callers in
``results_search.py`` and ``app.py`` own the I/O; this module only compares
values it is handed.

Confidence is a product decision, not a certainty score: RunSignUp's published
``age`` is captured at *registration* time, not on race day, so a runner whose
birthday falls between signing up and racing will legitimately show an age
that is off by one from their age on the actual race date.

``classify_match`` implements the SPR-26 design doc's decision table (its
Section 2): a row is CERTAIN, MAYBE, RULED_OUT, or IMPOSSIBLE, never a bare
confidence label. ``names_match`` / ``classify_confidence`` / ``HIGH`` /
``POSSIBLE`` / ``REJECTED`` remain as thin wrappers so existing callers
(``app.py``, ``results_search.py``) and their tests keep working until
SPR-32/SPR-30 migrate onto the four-bucket contract.
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
import unicodedata
from dataclasses import dataclass
from datetime import date

# Legacy three-state labels. Kept for app.py / results_search.py until SPR-32
# migrates the UI onto the bucket names below.
HIGH = "High"
POSSIBLE = "Possible"
REJECTED = "Rejected"

# SPR-26 §2 four-bucket contract.
CERTAIN = "Your races"
MAYBE = "Could be you"
RULED_OUT = "Ruled out"
IMPOSSIBLE = "Impossible"  # counted, never rendered as a row

# Upper bound for a believable finisher age. Anything above this is treated as
# missing data rather than a real age -- see parse_result_age.
MAX_PLAUSIBLE_AGE = 120

# Nobody ran a race before they were five. Below this implied age the row is
# IMPOSSIBLE, the same as a race held before the entered date of birth.
MIN_PLAUSIBLE_FINISH_AGE = 5

_WHITESPACE_RE = re.compile(r"\s+")
# Joining punctuation: deleted outright, so "O'Brien" and "OBrien" are one
# name. Covers the typographic apostrophe and backtick too, because a web form
# and a race-day spreadsheet rarely agree on which one they emit.
_JOINING_PUNCTUATION_RE = re.compile(r"[.,'’‘`´]")
# Separating punctuation: folded to a space, so "Smith-Jones" normalizes the
# same way "Smith Jones" does. Squashing happens in _squash_name, which is what
# then also reaches "SmithJones".
_SEPARATING_PUNCTUATION_RE = re.compile(r"[-‐-―_/\\]+")
_SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv)$")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Common English nickname pairs: canonical full name -> known short forms.
# Checked in both directions by _is_nickname_pair. Not exhaustive -- this is
# a tolerance for the common cases QA flagged, not a linguistic database.
_NICKNAMES: dict[str, set[str]] = {
    "michael": {"mike", "mick", "mikey"},
    "robert": {"bob", "rob", "bobby", "robby"},
    "william": {"will", "bill", "billy", "liam"},
    "thomas": {"tom", "tommy"},
    "james": {"jim", "jimmy", "jamie"},
    "david": {"dave", "davy"},
    "christopher": {"chris", "topher"},
    "matthew": {"matt"},
    "nicholas": {"nick", "nicky"},
    "alexander": {"alex", "xander"},
    "samuel": {"sam", "sammy"},
    "joseph": {"joe", "joey"},
    "daniel": {"dan", "danny"},
    "steven": {"steve", "stevie"},
    "stephen": {"steve", "stevie"},
    "benjamin": {"ben", "benny"},
    "andrew": {"andy", "drew"},
    "edward": {"ed", "eddie", "ted", "teddy"},
    "theodore": {"ted", "teddy", "theo"},
    "richard": {"rick", "ricky", "dick", "rich"},
    "ronald": {"ron", "ronnie"},
    "gregory": {"greg"},
    "lawrence": {"larry"},
    "anthony": {"tony"},
    "zachary": {"zach", "zack"},
    "jonathan": {"jon", "johnny"},
    "john": {"jack", "johnny", "jon"},
    "elizabeth": {"liz", "beth", "betsy", "eliza", "lizzie"},
    "katherine": {"kate", "katie", "kathy", "kay"},
    "catherine": {"kate", "katie", "cathy", "kay"},
    "margaret": {"maggie", "meg", "peggy", "marge"},
    "patricia": {"pat", "patty", "trish", "tricia"},
    "deborah": {"deb", "debbie"},
    "susan": {"sue", "susie"},
    "rebecca": {"becky", "becca"},
    "abigail": {"abby"},
    "victoria": {"vicky", "vicki", "tori"},
    "cynthia": {"cindy"},
    "christine": {"chris", "christy", "chrissy"},
    "christina": {"chris", "christy", "chrissy", "tina"},
    "gabrielle": {"gabby"},
    "gabriel": {"gabe"},
    "barbara": {"barb", "barbie"},
    "jennifer": {"jen", "jenny"},
    "jessica": {"jess", "jessie"},
}

_NAME_SIGNAL_REASON = {
    "nickname": "we matched a shortened or nickname version of the first name",
    "initial": "the result only published a first initial",
}

# Letters with no base-plus-combining-mark decomposition, so NFKD leaves them
# untouched -- unlike "é", which NFKD splits into "e" + a combining accent
# that normalize_name then strips. SPR-45 measured that RunSignUp's own
# get-results name filter folds combining-mark accents but drops a row
# outright for any of these, so a query has to be folded to this table's
# values before the request goes out, or the row never arrives to match
# against locally. Scoped to the letters SPR-45 measured; not exhaustive.
_ASCII_FOLD_MAP = str.maketrans(
    {
        "ø": "o",  # o with stroke
        "Ø": "O",
        "ß": "ss",  # sharp s
        "ł": "l",  # l with stroke
        "Ł": "L",
        "æ": "ae",  # ae ligature
        "Æ": "AE",
        "ð": "d",  # eth
        "Ð": "D",
    }
)


def fold_to_ascii(value: str) -> str:
    """Fold combining-mark accents and the non-decomposable letters above.

    Used both as ``normalize_name``'s first step and directly by
    ``runsignup_results.fetch_results`` to ASCII-fold a query name before it
    is sent to the server -- see the module-level comment on
    ``_ASCII_FOLD_MAP`` for why the transport layer needs this too.
    """
    text = str(value or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.translate(_ASCII_FOLD_MAP)


def normalize_name(value: str) -> str:
    """Casefold, fold accents to their base letters, fold punctuation, strip a
    trailing Jr/Sr/II/III/IV suffix, and collapse whitespace.

    Accent folding means "Núñez" and "Nunez" normalize to the same string --
    QA's accented-name case (SPR-26 §2) was dropped entirely under the old
    exact-casefold rule. It also covers o-slash, sharp-s, l-stroke, ae and
    eth (SPR-45) via :func:`fold_to_ascii`, since NFKD alone does not reach
    those. Suffix stripping means "Smith Jr" and "Smith" agree on last name,
    which is what lets the decision table's last-name check stay a strict
    equality instead of a fuzzy one.

    Punctuation folding is the SPR-26 §2 rule 1 requirement and it is not
    cosmetic: RunSignUp publishes the same surname as "O'Brien", "OBrien" and
    "O Brien" across result sets, so leaving the apostrophe in made a row with
    the other spelling *not a match at all* -- never shown and never counted.
    Joining punctuation is deleted, separating punctuation becomes a space.
    """
    text = str(value or "").strip()
    text = fold_to_ascii(text)
    text = text.casefold()
    text = _JOINING_PUNCTUATION_RE.sub("", text)
    text = _SEPARATING_PUNCTUATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _SUFFIX_RE.sub("", text)
    return text.strip()


def _squash_name(value: str) -> str:
    """``normalize_name`` with every space removed.

    The space-insensitive form of a name, used only as a fallback when the
    normalized forms disagree. It is what makes "Smith-Jones", "Smith Jones"
    and "SmithJones" one surname, and likewise "Mc Donald"/"McDonald" and
    "Mary Ann"/"Maryann". Dropping spaces cannot merge two different surnames
    that were not already a concatenation of each other, and "Your races"
    still requires corroborating age on top of the name.
    """
    return normalize_name(value).replace(" ", "")


def same_name_field(left: str, right: str) -> bool:
    """True when two single name fields are the same name after folding.

    The one place any caller should ask "is this the same first/last name?".
    Transport-level re-filters used to do ``a.strip().lower() == b.strip().lower()``,
    which silently undid the accent folding (SPR-45) and punctuation folding
    (SPR-26 §2 rule 1) this module exists to provide. Empty on either side is
    never a match.
    """
    folded_left, folded_right = normalize_name(left), normalize_name(right)
    if not folded_left or not folded_right:
        return False
    return folded_left == folded_right or _squash_name(left) == _squash_name(right)


def _is_nickname_pair(a: str, b: str) -> bool:
    return b in _NICKNAMES.get(a, ()) or a in _NICKNAMES.get(b, ())


def _is_initial_match(a: str, b: str) -> bool:
    if len(a) == 1 and b:
        return b[0] == a
    if len(b) == 1 and a:
        return a[0] == b
    return False


def name_agreement(query_first: str, query_last: str, result_first: str, result_last: str) -> str:
    """"exact" | "nickname" | "initial" | "none".

    Last name must always match exactly (after normalize_name, or after
    _squash_name when only internal spacing differs) -- SPR-26 §2 rule 1.
    First name may match exactly, as a known nickname pair, or as a single
    initial against a full name. "none" means the row is not a match at all:
    never shown, never counted, never scrolled past.
    """
    q_first, q_last = normalize_name(query_first), normalize_name(query_last)
    r_first, r_last = normalize_name(result_first), normalize_name(result_last)

    if not q_first or not q_last or not r_first or not r_last:
        return "none"
    if not same_name_field(q_last, r_last):
        return "none"
    if same_name_field(q_first, r_first):
        return "exact"
    if _is_nickname_pair(q_first, r_first):
        return "nickname"
    if _is_initial_match(q_first, r_first):
        return "initial"
    return "none"


def names_match(query_first: str, query_last: str, result_first: str, result_last: str) -> bool:
    """Legacy strict helper: True only for "exact" name agreement.

    Kept for callers not yet migrated onto ``name_agreement`` /
    ``classify_match`` (see module docstring).
    """
    return name_agreement(query_first, query_last, result_first, result_last) == "exact"


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
    """RunSignUp 'age' field. "" / None / non-numeric / out-of-range -> None.

    Ages outside ``1..MAX_PLAUSIBLE_AGE`` are treated as *missing*, not as a
    value to compare against. RunSignUp really does publish junk here -- a
    live result set checked on 2026-10-09 contained ``age=952``. Comparing
    against that would classify the row **Rejected** ("off by 912 years") and
    hide a result that might genuinely be yours, when the honest answer is
    that the race published no usable age. Falling back to None routes it to
    "Possible" instead, which is what an unknown age means.
    """
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
    return age if 1 <= age <= MAX_PLAUSIBLE_AGE else None


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


@dataclass
class MatchVerdict:
    bucket: str  # one of CERTAIN, MAYBE, RULED_OUT, IMPOSSIBLE
    reason: str  # one line, user-facing, names the signal that is actually missing
    name_signal: str  # "exact" | "nickname" | "initial"
    age_signal: str  # "corroborated" | "contradicted" | "unknown" | "no-race-date"
    result_age: int | None
    age_on_race_date: int | None


def classify_match(
    dob: date,
    event_date: date | None,
    raw_age,
    name_signal: str,
) -> MatchVerdict:
    """Implements the SPR-26 §2 decision table.

    Caller has already run ``name_agreement`` and confirmed it is not
    "none" -- a non-match is never a row, so it never reaches this function.
    """
    result_age = parse_result_age(raw_age)

    if event_date is None:
        return MatchVerdict(
            bucket=MAYBE,
            reason="we don't know when this race was held",
            name_signal=name_signal,
            age_signal="no-race-date",
            result_age=result_age,
            age_on_race_date=None,
        )

    if event_date < dob:
        return MatchVerdict(
            bucket=IMPOSSIBLE,
            reason="this race was held before the entered date of birth",
            name_signal=name_signal,
            age_signal="unknown",
            result_age=result_age,
            age_on_race_date=None,
        )

    age_on_race_date = age_on_date(dob, event_date)
    if age_on_race_date < MIN_PLAUSIBLE_FINISH_AGE:
        return MatchVerdict(
            bucket=IMPOSSIBLE,
            reason=f"the entered date of birth would make the runner {age_on_race_date} on race day",
            name_signal=name_signal,
            age_signal="unknown",
            result_age=result_age,
            age_on_race_date=age_on_race_date,
        )

    if result_age is None:
        age_signal = "unknown"
    else:
        diff = abs(age_on_race_date - result_age)
        age_signal = "corroborated" if diff <= 1 else "contradicted"

    bucket, reason = _bucket_for(name_signal, age_signal, age_on_race_date, result_age)
    return MatchVerdict(
        bucket=bucket,
        reason=reason,
        name_signal=name_signal,
        age_signal=age_signal,
        result_age=result_age,
        age_on_race_date=age_on_race_date,
    )


def _bucket_for(
    name_signal: str, age_signal: str, age_on_race_date: int, result_age: int | None
) -> tuple[str, str]:
    def contradicted_reason() -> str:
        return (
            f"published age {result_age} does not match the age on race date "
            f"({age_on_race_date}) — off by {abs(age_on_race_date - result_age)} years"
        )

    if name_signal == "exact":
        if age_signal == "corroborated":
            return (
                CERTAIN,
                f"name matches and age on race date ({age_on_race_date}) matches "
                f"the published age ({result_age})",
            )
        if age_signal == "unknown":
            return MAYBE, "this race published no usable age"
        return RULED_OUT, contradicted_reason()

    weak_name_reason = _NAME_SIGNAL_REASON[name_signal]
    if age_signal == "corroborated":
        return MAYBE, weak_name_reason
    if age_signal == "unknown":
        return MAYBE, f"{weak_name_reason}, and this race published no usable age"
    return RULED_OUT, contradicted_reason()


def valid_email(value: str) -> bool:
    """Return *True* if ``value`` looks like an email address.

    This is practical format checking, not an RFC 5322 parser. It requires
    some characters, a single ``@``, a domain, and a dot in the domain
    (so ``user@host`` fails). Leading/trailing whitespace is stripped.

    The value is never sent to any service, never persisted, and never
    logged. It is a local input-quality gate for the Sign Up search.

    Accepts:
        - plain addresses, plus-addressing (``user+tag@domain.com``),
          subdomains, unicode in the local part.

    Rejects:
        - empty / missing ``@`` / missing domain / missing TLD /
          embedded whitespace / more than one ``@``.
    """
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_EMAIL_RE.match(text))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Exercise the SPR-31 matching contract from the command line."
    )
    parser.add_argument("--dob", required=True, help="Date of birth, YYYY-MM-DD")
    parser.add_argument("--event-date", default="", help="Race date, YYYY-MM-DD; omit for 'race date unknown'")
    parser.add_argument("--raw-age", default="", help="Published age, e.g. 40; omit for 'no usable age'")
    parser.add_argument("--query-first", required=True)
    parser.add_argument("--query-last", required=True)
    parser.add_argument("--result-first", required=True)
    parser.add_argument("--result-last", required=True)
    args = parser.parse_args()

    signal = name_agreement(args.query_first, args.query_last, args.result_first, args.result_last)
    if signal == "none":
        print("not a row: names do not agree")
    else:
        dob_value = date.fromisoformat(args.dob)
        event_value = date.fromisoformat(args.event_date) if args.event_date else None
        verdict = classify_match(dob_value, event_value, args.raw_age or None, signal)
        print(f"bucket:           {verdict.bucket}")
        print(f"reason:           {verdict.reason}")
        print(f"name_signal:      {verdict.name_signal}")
        print(f"age_signal:       {verdict.age_signal}")
        print(f"result_age:       {verdict.result_age}")
        print(f"age_on_race_date: {verdict.age_on_race_date}")
