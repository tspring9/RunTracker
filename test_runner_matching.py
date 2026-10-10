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


# 952 is not hypothetical: it is a real published age observed in RunSignUp
# result set race=2017/event=4002/set=10 on 2026-10-09.
@pytest.mark.parametrize("raw", [952, "952", 121, 1000])
def test_parse_result_age_treats_implausible_ages_as_missing(raw):
    assert matching.parse_result_age(raw) is None


@pytest.mark.parametrize("raw", [1, 120])
def test_parse_result_age_keeps_the_plausible_boundaries(raw):
    assert matching.parse_result_age(raw) == raw


def test_implausible_published_age_is_possible_not_rejected():
    """A junk age must not hide a row that might be yours.

    Before the range check, age=952 against a real age of 40 classified
    Rejected ("off by 912 years"), collapsing a potentially genuine result
    out of sight. Unknown is the honest verdict.
    """
    confidence, reason = matching.classify_confidence(40, matching.parse_result_age(952))
    assert confidence == matching.POSSIBLE
    assert "no age" in reason.lower()


# -------------------------------------------------
# normalize_name – signup input validators
# -------------------------------------------------
def test_normalize_name_casefolds_and_collapses_whitespace():
    assert matching.normalize_name("  Mary  Jane \t") == "mary jane"
    assert matching.names_match("thomas", "spring", "Thomas", "Spring")


def test_normalize_name_empty_string():
    assert matching.normalize_name("") == ""


def test_normalize_name_only_whitespace():
    assert matching.normalize_name("   ") == ""


def test_normalize_name_one_character():
    assert matching.normalize_name("A") == "a"


def test_normalize_name_leading_trailing_spaces():
    assert matching.normalize_name("  Hello  ") == "hello"


def test_normalize_name_internal_double_spaces():
    assert matching.normalize_name("First   Last") == "first last"


def test_normalize_name_mixed_case():
    assert matching.normalize_name("hElLo WoRLd") == "hello world"


def test_normalize_name_apostrophe():
    assert matching.normalize_name("O'Brien") == "o'brien"


def test_normalize_name_hyphen():
    assert matching.normalize_name("Smith-Jones") == "smith-jones"


def test_normalize_name_accents():
    # SPR-26 §2: accent folding so "Núñez" and "Nunez" are the same name.
    assert matching.normalize_name("Núñez") == "nunez"
    assert matching.normalize_name("Núñez") == matching.normalize_name("Nunez")


@pytest.mark.parametrize(
    "with_letter,ascii_form",
    [
        ("Jørgensen", "Jorgensen"),
        ("Straße", "Strasse"),
        ("Wałęsa", "Walesa"),
        ("Ærøe", "AEroe"),
        ("Guðmundsson", "Gudmundsson"),
    ],
)
def test_normalize_name_folds_letters_nfkd_cannot_decompose(with_letter, ascii_form):
    # SPR-45: o-slash, sharp-s, l-stroke, ae and eth have no base-plus-
    # combining-mark decomposition, so plain NFKD (unlike for "Núñez" above)
    # leaves them untouched. normalize_name must still agree with the ASCII
    # spelling, or the local matching rule silently stops covering these names.
    assert matching.normalize_name(with_letter) == matching.normalize_name(ascii_form)


def test_fold_to_ascii_matches_the_measured_letters():
    assert matching.fold_to_ascii("ø") == "o"
    assert matching.fold_to_ascii("Ø") == "O"
    assert matching.fold_to_ascii("ß") == "ss"
    assert matching.fold_to_ascii("ł") == "l"
    assert matching.fold_to_ascii("Ł") == "L"
    assert matching.fold_to_ascii("æ") == "ae"
    assert matching.fold_to_ascii("Æ") == "AE"
    assert matching.fold_to_ascii("ð") == "d"
    assert matching.fold_to_ascii("Ð") == "D"


def test_normalize_name_strips_trailing_suffix():
    assert matching.normalize_name("Smith Jr") == "smith"
    assert matching.normalize_name("Smith Jr.") == "smith"
    assert matching.normalize_name("Smith Sr") == "smith"
    assert matching.normalize_name("Smith III") == "smith"
    assert matching.normalize_name("Smith") == matching.normalize_name("Smith Jr")


def test_normalize_name_very_long_string():
    long_name = "A" * 1000
    assert matching.normalize_name(long_name) == long_name.lower()


def test_normalize_name_none_safely():
    # value or "" short-circuits None to empty string.
    assert matching.normalize_name(None) == ""


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


# -------------------------------------------------
# name_agreement -- exact / nickname / initial / none
# -------------------------------------------------
def test_name_agreement_exact():
    assert matching.name_agreement("Tom", "Spring", "Tom", "Spring") == "exact"
    assert matching.name_agreement("José", "Muñoz", "Jose", "Munoz") == "exact"


def test_name_agreement_requires_exact_last_name():
    assert matching.name_agreement("Tom", "Spring", "Tom", "Springhower") == "none"


@pytest.mark.parametrize(
    "query_first,result_first",
    [("Tom", "Thomas"), ("Mike", "Michael"), ("Liz", "Elizabeth"), ("Bob", "Robert")],
)
def test_name_agreement_known_nickname_pairs_either_direction(query_first, result_first):
    assert matching.name_agreement(query_first, "Spring", result_first, "Spring") == "nickname"
    assert matching.name_agreement(result_first, "Spring", query_first, "Spring") == "nickname"


def test_name_agreement_single_initial():
    assert matching.name_agreement("T", "Spring", "Thomas", "Spring") == "initial"
    assert matching.name_agreement("Thomas", "Spring", "T", "Spring") == "initial"


def test_name_agreement_unrelated_first_names_is_none():
    assert matching.name_agreement("Tom", "Spring", "Jerry", "Spring") == "none"


def test_name_agreement_suffix_does_not_block_last_name_match():
    assert matching.name_agreement("Tom", "Spring", "Tom", "Spring Jr") == "exact"


@pytest.mark.parametrize(
    "query_first,query_last,result_first,result_last",
    [
        ("Tom", "Spring", "Thomas", "Spring"),
        ("Tom", "Spring", "Tom", "Springhower"),
        ("", "Spring", "Tom", "Spring"),
        ("Tom", "", "Tom", "Spring"),
    ],
)
def test_name_agreement_empty_or_mismatched_parts(
    query_first, query_last, result_first, result_last
):
    # "Thomas"/"Tom" is a nickname pair, not "none" -- names_match (exact-only)
    # still rejects it, which is what test_names_match_requires_both_* checks.
    assert matching.name_agreement(query_first, query_last, result_first, result_last) != "exact"


# -------------------------------------------------
# classify_match -- the SPR-26 §2 four-bucket decision table
# -------------------------------------------------
def test_classify_match_exact_name_corroborated_age_is_certain():
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), 36, "exact")
    assert verdict.bucket == matching.CERTAIN
    assert verdict.age_signal == "corroborated"


def test_classify_match_exact_name_unknown_age_is_maybe():
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), None, "exact")
    assert verdict.bucket == matching.MAYBE
    assert "no usable age" in verdict.reason


def test_classify_match_exact_name_contradicted_age_is_ruled_out():
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), 20, "exact")
    assert verdict.bucket == matching.RULED_OUT
    assert verdict.age_signal == "contradicted"


@pytest.mark.parametrize("name_signal", ["nickname", "initial"])
def test_classify_match_weak_name_signal_is_never_certain(name_signal):
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), 36, name_signal)
    assert verdict.bucket == matching.MAYBE
    assert verdict.name_signal == name_signal


@pytest.mark.parametrize("name_signal", ["nickname", "initial"])
def test_classify_match_weak_name_signal_contradicted_age_is_ruled_out(name_signal):
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), 20, name_signal)
    assert verdict.bucket == matching.RULED_OUT


def test_classify_match_race_before_birth_is_impossible():
    verdict = matching.classify_match(date(2010, 1, 1), date(2005, 1, 1), 40, "exact")
    assert verdict.bucket == matching.IMPOSSIBLE
    assert "before" in verdict.reason


def test_classify_match_implied_age_under_five_is_impossible():
    verdict = matching.classify_match(date(2024, 1, 1), date(2026, 1, 1), 2, "exact")
    assert verdict.bucket == matching.IMPOSSIBLE


def test_classify_match_unknown_race_date_is_maybe_regardless_of_age():
    verdict = matching.classify_match(date(1990, 6, 1), None, 36, "exact")
    assert verdict.bucket == matching.MAYBE
    assert verdict.age_signal == "no-race-date"
    assert "don't know when this race was held" in verdict.reason


def test_classify_match_implausible_published_age_is_treated_as_unknown():
    # age=952 is real observed junk data (see parse_result_age docstring).
    verdict = matching.classify_match(date(1990, 6, 1), date(2026, 6, 1), 952, "exact")
    assert verdict.bucket == matching.MAYBE
    assert verdict.result_age is None


# valid_email -- finish the job from SPR-27
# -------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        "runner@example.com",
        "user+tag@domain.com",            # plus-addressing
        "first.last@sub.example.org",    # subdomains
        "UPPER@CASE.COM",                # uppercase
        "user@host.co.uk",               # multi-dot TLD
        " a@b.com ",                     # leading/trailing whitespace stripped
        "user+long+tag@sub.domain.example.com",
    ],
)
def test_valid_email_accepts_valid_addresses(value):
    assert matching.valid_email(value)


@pytest.mark.parametrize(
    "value",
    [
        "",                              # empty
        None,                             # None
        "nope",                           # missing @
        "@nodomain.com",                  # missing local part
        "user@",                          # missing domain
        "user@host",                      # missing TLD
        "a@@b.com",                       # double @
        "a b@c.com",                      # embedded whitespace
        "   ",                            # whitespace only
    ],
)
def test_valid_email_rejects_invalid_addresses(value):
    assert not matching.valid_email(value)


# Double dots are an edge-case that the practical regex does not catch.
# The function is a format gate, not an RFC 5322 parser -- it rejects the
# common mistakes (@, domain, TLD) and lets obscurely-formed addresses
# through. Tested here so we know what the function does and what it
# doesn't do.
def test_valid_email_does_not_catch_double_dots():
    # Both pass the current regex. That is acceptable for a practical gate.
    assert matching.valid_email("a.b..c@d.com")
    assert matching.valid_email("a@b..com")


# Python's str.strip() removes tabs and newlines, so tab-wrapped input
# becomes a clean address after strip -- the function accepts it.
def test_valid_email_strips_tabs_and_newlines():
    assert matching.valid_email("\tuser@example.com\n")


def test_valid_email_very_long_input():
    # A 500-character local part is absurd but should parse, not crash.
    long_value = "a" * 500 + "@example.com"
    assert matching.valid_email(long_value)


def test_valid_email_unicode_local_part():
    # Unicode in the local part passes the regex (it only blocks @ and whitespace).
    assert matching.valid_email("josé@correo.méxico")


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
