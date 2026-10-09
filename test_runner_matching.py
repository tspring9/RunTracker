"""Specification tests for the pure runner matching engine."""

from datetime import date
import ast
import inspect

import pytest

import runner_matching as matching


@pytest.mark.parametrize(
    "birth_date,event_date,expected",
    [
        (date(1990, 3, 1), date(2026, 6, 1), 36),
        (date(1990, 9, 1), date(2026, 6, 1), 35),
        (date(1990, 6, 1), date(2026, 6, 1), 36),
        (date(2000, 2, 29), date(2026, 2, 28), 25),
        (date(2000, 2, 29), date(2026, 3, 1), 26),
        (date(2000, 2, 29), date(2028, 2, 29), 28),
        (date(2000, 12, 31), date(2026, 12, 31), 26),
        (date(2000, 12, 31), date(2026, 1, 1), 25),
        (date(2000, 1, 1), date(2026, 1, 1), 26),
    ],
)
def test_age_on_date_uses_completed_years(birth_date, event_date, expected):
    # Feb 29 resolves to Mar 1 in non-leap years. Consequently the task's
    # written 26/27 example is arithmetically impossible; the rule yields 25/26.
    assert matching.age_on_date(birth_date, event_date) == expected


def test_age_on_date_rejects_an_event_before_birth():
    with pytest.raises(ValueError):
        matching.age_on_date(date(2020, 1, 2), date(2020, 1, 1))


@pytest.mark.parametrize(
    "computed,published,expected",
    [
        (40, 40, matching.HIGH),
        (40, 39, matching.HIGH),
        (40, 41, matching.HIGH),
        (40, 38, matching.REJECTED),
        (40, 42, matching.REJECTED),
        (40, 75, matching.REJECTED),
        (40, None, matching.POSSIBLE),
        (None, 40, matching.POSSIBLE),
    ],
)
def test_classify_confidence_boundaries_and_reasons(computed, published, expected):
    confidence, reason = matching.classify_confidence(computed, published)
    assert confidence == expected
    assert reason.strip()
    if expected == matching.REJECTED:
        assert str(computed) in reason
        assert str(published) in reason


@pytest.mark.parametrize("raw", ["", None, "abc", 0, -3, "   "])
def test_parse_result_age_rejects_missing_or_invalid_values(raw):
    assert matching.parse_result_age(raw) is None


@pytest.mark.parametrize("raw", ["41", 41, "41 ", 41.0])
def test_parse_result_age_accepts_positive_whole_numbers(raw):
    assert matching.parse_result_age(raw) == 41


def test_normalize_name_casefolds_and_collapses_whitespace():
    assert matching.normalize_name("  Mary  Jane \t") == "mary jane"
    assert matching.names_match("thomas", "spring", "Thomas", "Spring")


@pytest.mark.parametrize(
    "query_first,query_last,result_first,result_last",
    [
        ("Tom", "Spring", "Thomas", "Spring"),
        ("Tom", "Spring", "Tom", "Springhower"),
        ("Tom", "Spring", "Thomas", "Springhower"),
        ("", "Spring", "Tom", "Spring"),
        ("Tom", "", "Tom", "Spring"),
    ],
)
def test_names_match_requires_both_complete_nonempty_names(
    query_first, query_last, result_first, result_last
):
    assert not matching.names_match(query_first, query_last, result_first, result_last)


@pytest.mark.parametrize(
    "first,last", [("O'Brien", "Smith-Jones"), ("José", "Muñoz")]
)
def test_names_match_handles_punctuation_and_non_ascii(first, last):
    assert matching.names_match(first, last, first, last)


@pytest.mark.parametrize("value", ["runner@example.com", "a.b+tag@sub.example.org"])
def test_valid_email_accepts_ordinary_addresses(value):
    assert matching.valid_email(value)


@pytest.mark.parametrize("value", ["nope", "a@", "@b.com", "a b@c.com", ""])
def test_valid_email_rejects_malformed_addresses(value):
    assert not matching.valid_email(value)


def test_matching_module_has_no_network_or_persistence_imports():
    tree = ast.parse(inspect.getsource(matching))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported_roots.isdisjoint({"requests", "storage", "streamlit", "urllib", "httpx"})
