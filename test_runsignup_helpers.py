"""Unit tests for the pure helpers in runsignup_results.py.

No network. These cover the parsing and formatting that stands between
RunSignUp's response shapes and RunTracker's row shape -- which is where silent
data corruption would otherwise creep in.

    python -m pytest test_runsignup_helpers.py -v
"""

import pytest

import runsignup_results as rsu


# -------------------------------------------------
# normalize_finish_time
# -------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        # RunSignUp reports hundredths; RunTracker's time_to_seconds calls int()
        # on each part, so they have to be stripped or the import raises.
        ("1:56:24.47", "1:56:24"),
        ("2:09:57.11", "2:09:57"),
        # Truncated, not rounded, to match the public results page.
        ("1:16:09.99", "1:16:09"),
        # MM:SS.ss, as used for splits.
        ("36:12.57", "0:36:12"),
        # Already clean.
        ("1:56:24", "1:56:24"),
        ("45:00", "0:45:00"),
        # Minute overflow folds into hours.
        ("75:20", "1:15:20"),
        # Bare seconds.
        ("42", "0:00:42"),
    ],
)
def test_normalize_finish_time_valid(raw, expected):
    assert rsu.normalize_finish_time(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "DNF", "DNS", "a:b:c", "1:2:3:4", "--"])
def test_normalize_finish_time_returns_blank_instead_of_raising(raw):
    # A bad time must not take down an import of 1,000+ rows.
    assert rsu.normalize_finish_time(raw) == ""


# -------------------------------------------------
# race_type_from_event_name
# -------------------------------------------------
@pytest.mark.parametrize(
    "name, expected",
    [
        ("Half Marathon", "Half Marathon"),
        ("Virtual Half Marathon", "Half Marathon"),
        ("13.1 Mile Run", "Half Marathon"),
        ("Half Marathon (K-12 Participants -- Medal Only)", "Half Marathon"),
        ("10K", "10K"),
        ("Virtual 10K", "10K"),
        ("5K", "5K"),
        ("Virtual 5K", "5K"),
        ("10 Mile", "10 Mile"),
        ("10-Mile", "10 Mile"),
        ("10 Miler", "10 Mile"),
    ],
)
def test_race_type_from_event_name(name, expected):
    assert rsu.race_type_from_event_name(name) == expected


@pytest.mark.parametrize("name", ["Kids Fun Run", "Marathon Relay", "", None, "1 Mile"])
def test_unrecognized_race_types_return_blank(name):
    # The old detect_race_type_from_events() defaulted unknown events to
    # "Half Marathon", which quietly filed fun runs as halves. Blank instead,
    # so the caller decides.
    assert rsu.race_type_from_event_name(name) == ""


def test_virtual_half_is_not_mistaken_for_a_shorter_race():
    # Pattern order matters: "Virtual Half Marathon" contains no 10K/5K token,
    # but a naive substring pass over distances could still mis-bucket it.
    assert rsu.race_type_from_event_name("Virtual Half Marathon") == "Half Marathon"


@pytest.mark.parametrize(
    "name, expected",
    [("Virtual 5K", True), ("VIRTUAL Half Marathon", True), ("5K", False), ("", False)],
)
def test_is_virtual_event(name, expected):
    assert rsu.is_virtual_event(name) is expected


# -------------------------------------------------
# parse_event_date
# -------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5/16/2026 07:00", "2026-05-16"),
        ("05/16/2026 07:00", "2026-05-16"),
        ("12/01/2024 08:30", "2024-12-01"),
        ("2026-05-16", "2026-05-16"),
        ("7/1/2020 23:58", "2020-07-01"),
        ("", ""),
        (None, ""),
        ("not a date", ""),
        ("16/05/2026 07:00", ""),  # day-first is not a format RunSignUp sends
    ],
)
def test_parse_event_date(raw, expected):
    assert rsu.parse_event_date(raw) == expected


# -------------------------------------------------
# affiliate_race_url
# -------------------------------------------------
def test_affiliate_token_appended_when_no_query_string():
    assert (
        rsu.affiliate_race_url("https://runsignup.com/Race/MO/KansasCity/HOSPITALHILLRUN", "TOK123")
        == "https://runsignup.com/Race/MO/KansasCity/HOSPITALHILLRUN?afmc=TOK123"
    )


def test_affiliate_token_appended_when_query_string_present():
    assert (
        rsu.affiliate_race_url("https://runsignup.com/Race/1?utm_source=x", "TOK123")
        == "https://runsignup.com/Race/1?utm_source=x&afmc=TOK123"
    )


@pytest.mark.parametrize("token", ["", None, "   "])
def test_no_token_is_a_safe_noop(token):
    # app.py calls this unconditionally, so an unset secret must not mangle URLs.
    url = "https://runsignup.com/Race/1"
    assert rsu.affiliate_race_url(url, token) == url


@pytest.mark.parametrize("url", ["", None, "   "])
def test_blank_url_stays_blank(url):
    assert rsu.affiliate_race_url(url, "TOK123") == ""


# -------------------------------------------------
# result_to_tracker_row
# -------------------------------------------------
def test_result_to_tracker_row_matches_runtracker_columns():
    result = {
        "place": 287,
        "bib": 1012,
        "first_name": "Thomas",
        "last_name": "Springhower",
        "chip_time": "1:56:24.47",
        "clock_time": "1:56:30.00",
        "pace": "8:53",
        "age": 33,
        "city": "Omaha",
        "state": "NE",
    }
    context = {
        "race_name": "Hospital Hill Run",
        "race_type": "Half Marathon",
        "date": "2026-05-16",
        "city": "Kansas City",
        "state": "MO",
    }

    row = rsu.result_to_tracker_row(result, context)

    assert row["runner_name"] == "Thomas Springhower"
    # chip_time wins over clock_time, and loses its hundredths.
    assert row["finish_time"] == "1:56:24"
    assert row["status"] == "Completed"
    # city/state describe the RACE, not the runner's hometown -- the tracker's
    # map colours states by where the race was held.
    assert row["city"] == "Kansas City"
    assert row["state"] == "MO"
    assert row["race_date"] == "2026-05-16"
    assert "Place 287" in row["notes"]
    assert "Bib 1012" in row["notes"]


def test_result_to_tracker_row_falls_back_to_clock_time():
    row = rsu.result_to_tracker_row(
        {"first_name": "A", "last_name": "B", "clock_time": "2:00:00.50"}, {}
    )
    assert row["finish_time"] == "2:00:00"


def test_find_runner_results_requires_a_name():
    with pytest.raises(ValueError):
        rsu.find_runner_results(rsu.HOSPITAL_HILL_RACE_ID)
