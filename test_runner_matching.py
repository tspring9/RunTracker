"""Unit tests for the pure matching engine (runner_matching.py).

No network, no I/O. Run with: python -m pytest test_runner_matching.py -v
"""

from datetime import date

import pytest

import runner_matching as rm


# -------------------------------------------------
# normalize_name / names_match
# -------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Tom   Jones  ", "tom jones"),
        ("THOMAS", "thomas"),
        ("Jo-Ann", "jo-ann"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_name(raw, expected):
    assert rm.normalize_name(raw) == expected


def test_names_match_exact_case_insensitive():
    assert rm.names_match("Thomas", "Springhower", "  thomas ", "SPRINGHOWER")


def test_names_match_requires_both_parts():
    assert not rm.names_match("", "Springhower", "Thomas", "Springhower")
    assert not rm.names_match("Thomas", "", "Thomas", "Springhower")


def test_names_match_rejects_partial_overlap():
    # Unlike the old server-side partial lookup, a first-name-only hit is not
    # a match here.
    assert not rm.names_match("Tom", "Jones", "Thomas", "Jones")


# -------------------------------------------------
# age_on_date
# -------------------------------------------------
def test_age_on_date_before_birthday_this_year():
    assert rm.age_on_date(date(1990, 6, 15), date(2026, 6, 14)) == 35


def test_age_on_date_on_birthday_counts_as_new_age():
    assert rm.age_on_date(date(1990, 6, 15), date(2026, 6, 15)) == 36


def test_age_on_date_after_birthday_this_year():
    assert rm.age_on_date(date(1990, 6, 15), date(2026, 12, 31)) == 36


def test_age_on_date_feb29_resolves_to_mar1_in_non_leap_year():
    # Non-leap 2026: the Feb-29 birthday has not happened yet on Feb 28,
    # and resolves to Mar 1.
    assert rm.age_on_date(date(2000, 2, 29), date(2026, 2, 28)) == 25
    assert rm.age_on_date(date(2000, 2, 29), date(2026, 3, 1)) == 26


def test_age_on_date_feb29_in_a_leap_year_uses_the_real_date():
    assert rm.age_on_date(date(2000, 2, 29), date(2024, 2, 28)) == 23
    assert rm.age_on_date(date(2000, 2, 29), date(2024, 2, 29)) == 24


def test_age_on_date_event_before_birth_raises():
    with pytest.raises(ValueError):
        rm.age_on_date(date(2000, 1, 1), date(1999, 1, 1))


# -------------------------------------------------
# parse_result_age
# -------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("33", 33),
        (33, 33),
        (33.0, 33),
        ("  33 ", 33),
        ("", None),
        (None, None),
        ("DNS", None),
        ("0", None),
        ("-5", None),
        (True, None),
    ],
)
def test_parse_result_age(raw, expected):
    assert rm.parse_result_age(raw) == expected


# -------------------------------------------------
# classify_confidence
# -------------------------------------------------
def test_classify_confidence_no_published_age():
    confidence, reason = rm.classify_confidence(30, None)
    assert confidence == rm.POSSIBLE
    assert "no age" in reason.lower()


def test_classify_confidence_no_race_date():
    confidence, reason = rm.classify_confidence(None, 30)
    assert confidence == rm.POSSIBLE
    assert "race date" in reason.lower()


@pytest.mark.parametrize("age_on_race_date, result_age", [(30, 30), (30, 29), (30, 31)])
def test_classify_confidence_within_tolerance_is_high(age_on_race_date, result_age):
    confidence, _ = rm.classify_confidence(age_on_race_date, result_age)
    assert confidence == rm.HIGH


@pytest.mark.parametrize("age_on_race_date, result_age", [(30, 28), (30, 32), (30, 20)])
def test_classify_confidence_outside_tolerance_is_rejected(age_on_race_date, result_age):
    confidence, reason = rm.classify_confidence(age_on_race_date, result_age)
    assert confidence == rm.REJECTED
    assert "off by" in reason.lower()


# -------------------------------------------------
# valid_email
# -------------------------------------------------
@pytest.mark.parametrize("value", ["a@example.com", "first.last@sub.example.co"])
def test_valid_email_accepts_plausible_addresses(value):
    assert rm.valid_email(value)


@pytest.mark.parametrize("value", ["", None, "not-an-email", "a@b", "a@.com", "   "])
def test_valid_email_rejects_implausible_values(value):
    assert not rm.valid_email(value)
