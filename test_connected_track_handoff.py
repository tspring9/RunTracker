"""The connected track's handoff into the anonymous track's sweep (SPR-20).

``connected_track`` replaces discovery only: its candidates are consumed by
``results_search.sweep_for_runner``, turned into rows by
``runsignup_results.result_to_tracker_row``, and classified by
``runner_matching.classify_match``. None of that is this module's code, which is
the point (SPR-15: no second matching engine) -- but it does mean the *shape* of
a candidate is a contract with code living in another branch.

**These tests skip on ``main`` and activate when PR #5 (SPR-15) merges.**
``results_search`` and ``runner_matching`` do not exist on ``main``, so there is
nothing to check against yet. The contract was verified by hand against
``feature/spr-24-signup-v2`` before this was written; this file exists so that
verification becomes a real test the moment the dependency lands, rather than a
claim in a ticket comment nobody re-runs.

The specific thing it guards: a candidate must carry **both** ``event_date``
(the frozen SPR-30 contract) and ``date`` (what ``result_to_tracker_row``
reads). Emit one and not the other and every row comes out with a blank race
date -- which presents as a data problem, not a wiring one, so it is the kind of
bug that survives a long time.

    python -m pytest test_connected_track_handoff.py -v
"""

from datetime import date

import pytest

import connected_track as ct
import runsignup_oauth as oauth
import runsignup_results as rsu

results_search = pytest.importorskip(
    "results_search", reason="arrives with PR #5 (SPR-15); not on main yet"
)
runner_matching = pytest.importorskip(
    "runner_matching", reason="arrives with PR #5 (SPR-15); not on main yet"
)


# An annual race with eight runnings -- more than
# rsu.DEFAULT_DISCOVERY_EVENT_DAYS, so the oldest are exactly what a naive
# discover_result_sets call would lose.
EVENTS = [
    {
        "event_id": 100 + offset,
        "race_event_days_id": 200 + offset,
        "name": "Half Marathon",
        "date": f"{year}-05-04",
        "distance": "13.1",
        "race_type": "Half Marathon",
        "virtual": False,
    }
    for offset, year in enumerate(range(2019, 2027))
]

RACE = {
    "name": "Lincoln Half Marathon",
    "url": "https://runsignup.com/Race/NE/Lincoln/LincolnHalf",
    "address": {"city": "Lincoln", "state": "NE"},
    "events": [],
}

# The payload shape the live endpoint actually returned, 2026-10-11.
PAYLOAD = {
    "user_registered_races": [
        {
            "race": {
                "race_id": 85066,
                "name": "Lincoln Half Marathon",
                "next_date": "05/04/2021",
                "address": {"city": "Lincoln", "state": "NE"},
            }
        }
    ]
}

TODAY = date(2026, 10, 11)
DOB = date(1980, 1, 15)


@pytest.fixture
def wired(monkeypatch):
    """Stub every HTTP call, in both modules, and return the candidates."""

    def fetch_results(race_id, event_id, result_set_id, first_name="", last_name="",
                      results_per_page=100, max_pages=1):
        # A real match plus a same-surname stranger, so the sweep's name filter
        # has something to actually reject.
        return [
            {"first_name": "Tom", "last_name": "Spring", "chip_time": "1:55:36",
             "place": 42, "bib": "1234", "age": 41},
            {"first_name": "Gerald", "last_name": "Spring", "chip_time": "2:40:00",
             "place": 900, "bib": "9999", "age": 72},
        ]

    monkeypatch.setattr(rsu, "fetch_race", lambda race_id: RACE)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(EVENTS))
    monkeypatch.setattr(
        rsu,
        "list_result_sets",
        lambda race_id, event_id: [
            {"result_set_id": event_id * 10, "name": "Overall", "public": True,
             "preliminary": False}
        ],
    )
    monkeypatch.setattr(rsu, "fetch_results", fetch_results)
    # results_search holds its own reference to the client module.
    monkeypatch.setattr(results_search.rsu, "fetch_results", fetch_results)
    monkeypatch.setattr(results_search.rsu, "fetch_race", lambda race_id: RACE)
    # _index_built_at reads the offline index, which is irrelevant here: these
    # candidates did not come from it. Stubbed so the test needs no SQLite.
    monkeypatch.setattr(results_search, "_index_built_at", lambda scope: "")
    monkeypatch.setattr(oauth, "registered_races", lambda token, api_registration=None: PAYLOAD)

    return ct.discover_connected_candidates("fake-token", max_calls=60, today=TODAY)


def _sweep(candidates):
    scope = results_search.SearchScope(
        states=("NE",), start_year=2019, end_year=2026, max_calls=50
    )
    return results_search.sweep_for_runner("Tom", "Spring", candidates, scope)


def test_the_registered_year_is_the_one_discovered(wired):
    # The 2021 registration must resolve to the 2021 event out of eight
    # runnings -- not to a recent one, and not to nothing.
    assert [c["event_date"] for c in wired.candidates] == ["2021-05-04"]


def test_the_real_sweep_accepts_these_candidates_unmodified(wired):
    matches, coverage = _sweep(wired.candidates)
    assert coverage.result_sets_checked == 1
    # The name filter kept Tom and dropped Gerald.
    assert len(matches) == 1


def test_the_row_comes_out_dated(wired):
    # The regression that motivates this file. A candidate missing `date` still
    # sweeps fine and still matches -- it just produces a row with no race date.
    (row,) = _sweep(wired.candidates)[0]
    assert row["race_date"] == "2021-05-04"
    assert row["race_name"] == "Lincoln Half Marathon"
    assert row["state"] == "NE"
    assert row["city"] == "Lincoln"
    assert row["finish_time"] == "1:55:36"


def test_the_real_matcher_confirms_the_row(wired):
    (row,) = _sweep(wired.candidates)[0]
    name_signal = runner_matching.name_agreement(
        "Tom", "Spring", row["_raw"]["first_name"], row["_raw"]["last_name"]
    )
    verdict = runner_matching.classify_match(
        dob=DOB,
        # Parsed back out of the row, so this asserts the date the connected
        # track derived is not merely present but usable by the matcher.
        event_date=date.fromisoformat(row["race_date"]),
        raw_age=row["_raw"]["age"],
        name_signal=name_signal,
    )
    assert verdict.bucket == runner_matching.CERTAIN
    assert verdict.age_signal == "corroborated"


def test_a_blank_race_date_would_downgrade_the_verdict(wired):
    # Why the date matters beyond display: with no event date the matcher cannot
    # corroborate age, so a confirmable row becomes a "could be you". This is
    # the user-visible cost of dropping the `date` key.
    (row,) = _sweep(wired.candidates)[0]
    undated = runner_matching.classify_match(
        dob=DOB,
        event_date=None,
        raw_age=row["_raw"]["age"],
        name_signal="exact",
    )
    assert undated.bucket != runner_matching.CERTAIN
    assert undated.age_signal == "no-race-date"


def test_provenance_survives_into_the_row_context(wired):
    # The card needs to say "from your registrations" rather than "someone with
    # your name", so the flag has to reach the row, not just the candidate.
    (row,) = _sweep(wired.candidates)[0]
    assert row["_context"]["from_registration"] is True
