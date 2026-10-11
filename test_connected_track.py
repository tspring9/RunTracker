"""Unit tests for connected_track.py -- no network, no credentials.

Every HTTP entry point (``fetch_race``, ``list_result_sets``) is monkeypatched,
so these run on a laptop with no OAuth app registered.

Four areas carry the real risk:

* **Date targeting.** ``race_id`` names a recurring race *series*, so picking
  the wrong running of it returns someone else's race -- or, worse, silently
  returns nothing for an old race, which is exactly the history this feature
  exists to find. :func:`select_event_days` is where that is decided.
* **The two date keys.** ``sweep_for_runner``'s consumers read ``event_date``
  (SPR-30 contract) and ``result_to_tracker_row`` reads ``date``. Emitting one
  and not the other produces rows with a blank race date, which looks like a
  data problem rather than a wiring one.
* **Budget accounting.** "This race published no results" and "we ran out of
  calls before checking" are opposite messages. The tests pin that they can
  never be reported as the same thing.
* **Past/upcoming split.** Spending calls on a race that has not happened is
  pure waste, and counting it as searched would be a lie.

    python -m pytest test_connected_track.py -v
"""

from datetime import date

import pytest

import connected_track as ct
import runsignup_oauth as oauth
import runsignup_results as rsu


def registration(race_id=1, race_date="2024-05-04", name="Lincoln Half", city="Lincoln", state="NE"):
    return oauth.Registration(
        race_id=race_id, race_name=name, race_date=race_date, city=city, state=state
    )


def event(event_id, date_text, name="Half Marathon", virtual=False, days_id=None):
    return {
        "event_id": event_id,
        "race_event_days_id": days_id,
        "name": name,
        "date": date_text,
        "distance": "13.1",
        "race_type": rsu.race_type_from_event_name(name),
        "virtual": virtual,
    }


def result_set(result_set_id, public=True, name="Overall", preliminary=False):
    return {
        "result_set_id": result_set_id,
        "name": name,
        "public": public,
        "preliminary": preliminary,
    }


class FakeApi:
    """Stands in for the two HTTP calls, and counts them."""

    def __init__(self, events, result_sets=None, race_name="Lincoln Half", city="Lincoln", state="NE"):
        self.events = events
        # event_id -> list of result sets; missing event_id raises, matching
        # list_result_sets' real behaviour for an event with no results.
        self.result_sets = result_sets or {}
        self.race_name = race_name
        self.city = city
        self.state = state
        self.fetch_race_calls = 0
        self.list_result_set_calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(rsu, "fetch_race", self.fetch_race)
        monkeypatch.setattr(rsu, "list_result_sets", self.list_result_sets)
        return self

    def fetch_race(self, race_id):
        self.fetch_race_calls += 1
        return {
            "name": self.race_name,
            "url": "https://runsignup.com/Race/NE/Lincoln/LincolnHalf",
            "address": {"city": self.city, "state": self.state},
            # list_events reads this; start_time is what parse_event_date wants,
            # but we patch list_events' input by supplying events directly below.
            "events": [],
        }

    def list_result_sets(self, race_id, event_id):
        self.list_result_set_calls.append((race_id, event_id))
        if event_id not in self.result_sets:
            raise rsu.RunSignUpError(f"no result sets for event {event_id}")
        return self.result_sets[event_id]


@pytest.fixture
def api(monkeypatch):
    """A race with three annual runnings, results published for each."""
    events = [event(10, "2022-05-01"), event(20, "2023-05-07"), event(30, "2024-05-04")]
    fake = FakeApi(
        events,
        result_sets={10: [result_set(100)], 20: [result_set(200)], 30: [result_set(300)]},
    ).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))
    return fake


# -------------------------------------------------
# Date targeting -- the reason this module exists
# -------------------------------------------------
def test_targets_the_exact_event_date():
    events = [event(10, "2022-05-01"), event(20, "2023-05-07")]
    assert [e["event_id"] for e in ct.select_event_days(events, "2023-05-07")] == [20]


def test_an_old_running_of_an_annual_race_is_still_found():
    # The regression this module exists to prevent: discover_result_sets scans
    # only the 5 most recent race days, so on a race with 6+ annual runnings the
    # oldest fall off the end and a 2019 registration finds nothing.
    events = [event(i * 10, f"{year}-05-04") for i, year in enumerate(range(2019, 2027), start=1)]
    assert len(events) > rsu.DEFAULT_DISCOVERY_EVENT_DAYS

    targeted = ct.select_event_days(events, "2019-05-04")
    assert [e["event_id"] for e in targeted] == [10]


def test_a_race_weekend_falls_back_to_nearby_days():
    # Registration stamped with the race-level date, events a day or two off.
    events = [event(10, "2024-05-04"), event(20, "2024-05-05")]
    targeted = ct.select_event_days(events, "2024-05-03")
    assert {e["event_id"] for e in targeted} == {10, 20}


def test_the_tolerance_does_not_swallow_a_different_running():
    events = [event(10, "2023-05-07"), event(20, "2024-05-04")]
    targeted = ct.select_event_days(events, "2024-05-04")
    assert [e["event_id"] for e in targeted] == [20]


def test_falls_back_to_the_same_calendar_year_when_the_date_is_well_off():
    # Beyond the weekend tolerance but clearly the same running of the race.
    events = [event(10, "2023-09-09"), event(20, "2024-03-02")]
    targeted = ct.select_event_days(events, "2024-06-01")
    assert [e["event_id"] for e in targeted] == [20]


def test_no_year_match_targets_nothing_rather_than_guessing():
    events = [event(10, "2019-05-04")]
    assert ct.select_event_days(events, "2024-05-04") == []


def test_an_undated_registration_targets_nothing():
    events = [event(10, "2024-05-04")]
    assert ct.select_event_days(events, "") == []


def test_undated_events_are_ignored_not_crashed_on():
    events = [event(10, ""), event(20, "2024-05-04")]
    assert [e["event_id"] for e in ct.select_event_days(events, "2024-05-04")] == [20]


# -------------------------------------------------
# One registration -> candidates
# -------------------------------------------------
def test_candidate_carries_both_date_keys_from_one_value(api):
    budget = ct.CallBudget(limit=10)
    found = ct.candidates_for_registration(registration(race_date="2024-05-04"), budget)

    assert len(found.candidates) == 1
    candidate = found.candidates[0]
    # event_date is the SPR-30 contract; date is what result_to_tracker_row
    # reads. Both, or rows come out with a blank race_date.
    assert candidate["event_date"] == "2024-05-04"
    assert candidate["date"] == "2024-05-04"


def test_candidate_has_every_key_the_sweep_and_row_builder_need(api):
    budget = ct.CallBudget(limit=10)
    candidate = ct.candidates_for_registration(registration(), budget).candidates[0]

    # sweep_for_runner indexes these three directly.
    for key in ("race_id", "event_id", "result_set_id"):
        assert key in candidate, key
    # result_to_tracker_row reads these off the context.
    for key in ("race_name", "city", "state", "date", "race_type"):
        assert key in candidate, key


def test_row_built_from_a_candidate_has_a_real_date(api):
    # The end-to-end version of the two-date-keys test: prove a tracker row
    # comes out dated, rather than asserting on our own dict shape.
    budget = ct.CallBudget(limit=10)
    candidate = ct.candidates_for_registration(registration(race_date="2024-05-04"), budget).candidates[0]

    row = rsu.result_to_tracker_row(
        {"first_name": "Tom", "last_name": "Spring", "chip_time": "1:55:36"}, candidate
    )
    assert row["race_date"] == "2024-05-04"
    assert row["race_name"] == "Lincoln Half"
    assert row["state"] == "NE"


def test_targets_only_the_registered_year_not_every_running(api):
    budget = ct.CallBudget(limit=10)
    found = ct.candidates_for_registration(registration(race_date="2023-05-07"), budget)

    assert [c["result_set_id"] for c in found.candidates] == [200]
    # One get-result-sets call, for the targeted day only -- not all three.
    assert api.list_result_set_calls == [(1, 20)]


def test_private_result_sets_are_dropped(monkeypatch):
    events = [event(30, "2024-05-04")]
    FakeApi(events, result_sets={30: [result_set(300, public=False), result_set(301)]}).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    found = ct.candidates_for_registration(registration(), ct.CallBudget(limit=10))
    assert [c["result_set_id"] for c in found.candidates] == [301]


def test_virtual_events_are_excluded_by_default_and_optional(monkeypatch):
    events = [event(30, "2024-05-04", name="Virtual 5K", virtual=True)]
    FakeApi(events, result_sets={30: [result_set(300)]}).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    assert ct.candidates_for_registration(registration(), ct.CallBudget(limit=10)).candidates == []
    opted_in = ct.candidates_for_registration(
        registration(), ct.CallBudget(limit=10), include_virtual=True
    )
    assert len(opted_in.candidates) == 1


def test_an_event_with_no_results_is_skipped_not_fatal(monkeypatch):
    # list_result_sets raises for an event with no results; that is a normal
    # outcome for a race whose organiser never published any.
    events = [event(30, "2024-05-04")]
    FakeApi(events, result_sets={}).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    found = ct.candidates_for_registration(registration(), ct.CallBudget(limit=10))
    assert found.candidates == []
    assert found.truncated is False  # no results found, but we did look


def test_race_level_fields_fall_back_to_the_registration(monkeypatch):
    # A /race payload with no address should not blank out city/state we already
    # know from the registration.
    events = [event(30, "2024-05-04")]
    fake = FakeApi(events, result_sets={30: [result_set(300)]})
    fake.city = ""
    fake.state = ""
    fake.race_name = ""
    fake.install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    candidate = ct.candidates_for_registration(
        registration(name="Lincoln Half", city="Lincoln", state="ne"), ct.CallBudget(limit=10)
    ).candidates[0]
    assert candidate["city"] == "Lincoln"
    assert candidate["state"] == "NE"
    assert candidate["race_name"] == "Lincoln Half"


# -------------------------------------------------
# Budget: "no results" must never mean "we stopped looking"
# -------------------------------------------------
def test_budget_exhausted_before_a_race_reports_truncated_not_empty(api):
    spent = ct.CallBudget(limit=0)
    found = ct.candidates_for_registration(registration(), spent)
    assert found.candidates == []
    assert found.truncated is True
    assert api.fetch_race_calls == 0  # did not even start


def test_budget_exhausted_mid_race_reports_truncated(api):
    # 1 call for fetch_race, nothing left for get-result-sets.
    budget = ct.CallBudget(limit=1)
    found = ct.candidates_for_registration(registration(), budget)
    assert found.candidates == []
    assert found.truncated is True


def test_a_truncated_race_is_not_counted_as_having_no_results(api):
    # The distinction that matters to the UI: with a budget of 1, the single
    # race was cut short. That is "not checked", never "published nothing".
    result = ct.candidates_from_registrations(
        [registration(race_date="2024-05-04")], max_calls=1, today=date(2026, 10, 11)
    )
    coverage = result.coverage
    assert coverage.races_without_results == 0
    assert coverage.races_skipped_for_budget == 1
    assert coverage.complete is False
    assert coverage.capped is True


def test_an_empty_race_payload_does_not_cause_a_second_unbudgeted_fetch(monkeypatch):
    # list_events treats a falsy `race` as "not supplied" and re-fetches it. That
    # second call would never be counted against the budget, so the empty-payload
    # case is guarded rather than left to chance.
    calls = {"n": 0}

    def empty_race(race_id):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(rsu, "fetch_race", empty_race)
    monkeypatch.setattr(rsu, "list_result_sets", lambda race_id, event_id: [])

    budget = ct.CallBudget(limit=10)
    found = ct.candidates_for_registration(registration(), budget)
    assert found.candidates == []
    assert calls["n"] == 1
    assert budget.used == 1


def test_budget_is_never_exceeded(api):
    budget = ct.CallBudget(limit=2)
    ct.candidates_for_registration(registration(), budget)
    assert budget.used <= 2


def test_spend_reserves_nothing_when_it_would_overrun():
    budget = ct.CallBudget(limit=3)
    assert budget.spend(2) is True
    assert budget.spend(2) is False
    assert budget.used == 2  # the failed spend did not partially consume


# -------------------------------------------------
# Past / upcoming / undated
# -------------------------------------------------
def test_partition_splits_on_today():
    today = date(2026, 10, 11)
    past, upcoming, undated = ct.partition_registrations(
        [
            registration(race_id=1, race_date="2021-07-19"),
            registration(race_id=2, race_date="2027-01-01"),
            registration(race_id=3, race_date=""),
        ],
        today=today,
    )
    assert [r.race_id for r in past] == [1]
    assert [r.race_id for r in upcoming] == [2]
    assert [r.race_id for r in undated] == [3]


def test_a_race_today_is_upcoming_not_past():
    # Matches oauth.summarize_registrations' rule, so the probe's verdict and
    # this split can never disagree about the same account.
    today = date(2026, 10, 11)
    past, upcoming, _ = ct.partition_registrations(
        [registration(race_date="2026-10-11")], today=today
    )
    assert past == []
    assert len(upcoming) == 1


def test_past_registrations_come_back_newest_first():
    past, _, _ = ct.partition_registrations(
        [
            registration(race_id=1, race_date="2021-07-19"),
            registration(race_id=2, race_date="2026-05-09"),
            registration(race_id=3, race_date="2023-04-01"),
        ],
        today=date(2026, 10, 11),
    )
    assert [r.race_id for r in past] == [2, 3, 1]


def test_upcoming_registrations_cost_no_api_calls(api):
    result = ct.candidates_from_registrations(
        [registration(race_date="2027-05-04")], max_calls=10, today=date(2026, 10, 11)
    )
    assert api.fetch_race_calls == 0
    assert result.candidates == []
    assert result.coverage.registrations_upcoming == 1
    # Nothing was skipped for budget -- there was nothing worth checking.
    assert result.coverage.complete is True


def test_undated_registrations_are_checked_after_dated_ones(monkeypatch):
    events = [event(30, "2024-05-04")]
    fake = FakeApi(events, result_sets={30: [result_set(300)]}).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    # Budget for exactly one race. The dated one must win.
    result = ct.candidates_from_registrations(
        [registration(race_id=99, race_date=""), registration(race_id=7, race_date="2024-05-04")],
        max_calls=2,
        today=date(2026, 10, 11),
    )
    assert [c["race_id"] for c in result.candidates] == [7]
    assert result.coverage.registrations_undated == 1


# -------------------------------------------------
# The whole pass
# -------------------------------------------------
def test_candidates_are_ordered_newest_race_first(monkeypatch):
    events = [event(10, "2022-05-01"), event(30, "2024-05-04")]
    FakeApi(events, result_sets={10: [result_set(100)], 30: [result_set(300)]}).install(monkeypatch)
    monkeypatch.setattr(rsu, "list_events", lambda race_id, race=None: list(events))

    result = ct.candidates_from_registrations(
        [registration(race_id=1, race_date="2022-05-01"), registration(race_id=2, race_date="2024-05-04")],
        max_calls=20,
        today=date(2026, 10, 11),
    )
    assert [c["event_date"] for c in result.candidates] == ["2024-05-04", "2022-05-01"]


def test_coverage_counts_add_up(api):
    result = ct.candidates_from_registrations(
        [
            registration(race_id=1, race_date="2024-05-04"),
            registration(race_id=2, race_date="2027-01-01"),
        ],
        max_calls=20,
        today=date(2026, 10, 11),
    )
    coverage = result.coverage
    assert coverage.registrations_total == 2
    assert coverage.registrations_past == 1
    assert coverage.registrations_upcoming == 1
    assert coverage.races_covered == 1
    assert coverage.candidates_found == len(result.candidates)
    assert coverage.complete is True


def test_progress_callback_gets_counts_only(api):
    seen = []
    ct.candidates_from_registrations(
        [registration(race_id=1, race_date="2024-05-04")],
        max_calls=20,
        today=date(2026, 10, 11),
        progress_cb=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 1)]
    # Counts, not race names -- a callback is an easy place for personal data
    # to escape into a log.
    assert all(isinstance(value, int) for pair in seen for value in pair)


def test_no_registrations_is_not_an_error(api):
    result = ct.candidates_from_registrations([], max_calls=20, today=date(2026, 10, 11))
    assert result.candidates == []
    assert result.coverage.registrations_total == 0
    assert api.fetch_race_calls == 0


def test_zero_budget_does_no_http(api):
    result = ct.candidates_from_registrations(
        [registration(race_date="2024-05-04")], max_calls=0, today=date(2026, 10, 11)
    )
    assert api.fetch_race_calls == 0
    assert result.coverage.races_skipped_for_budget == 1


def test_negative_budget_is_clamped_not_crashed_on(api):
    result = ct.candidates_from_registrations(
        [registration(race_date="2024-05-04")], max_calls=-5, today=date(2026, 10, 11)
    )
    assert result.coverage.calls_budget == 0
    assert api.fetch_race_calls == 0


# -------------------------------------------------
# The entry point
# -------------------------------------------------
def test_entry_point_parses_the_real_payload_key(monkeypatch, api):
    # The shape the live endpoint actually returned on 2026-10-11.
    payload = {
        "user_registered_races": [
            {
                "race": {
                    "race_id": 1,
                    "name": "Lincoln Half",
                    "next_date": "05/04/2024",
                    "address": {"city": "Lincoln", "state": "NE"},
                }
            }
        ]
    }
    monkeypatch.setattr(oauth, "registered_races", lambda token, api_registration=None: payload)

    result = ct.discover_connected_candidates("token", max_calls=20, today=date(2026, 10, 11))
    assert result.coverage.registrations_past == 1
    assert [c["result_set_id"] for c in result.candidates] == [300]


def test_entry_point_passes_the_caller_registration_through(monkeypatch, api):
    seen = {}

    def fake_registered_races(token, api_registration=None):
        seen["token"] = token
        seen["api_registration"] = api_registration
        return {"user_registered_races": []}

    monkeypatch.setattr(oauth, "registered_races", fake_registered_races)
    ct.discover_connected_candidates("tok", api_registration=("reg", "sec"))
    assert seen == {"token": "tok", "api_registration": ("reg", "sec")}


def test_a_rejected_token_is_raised_not_reported_as_no_results(monkeypatch):
    # "Reconnect your account" and "you have no results" are different
    # messages; merging them would tell a runner their history is empty when
    # really their connection lapsed.
    def reject(token, api_registration=None):
        raise oauth.OAuthFlowError("RunSignUp rejected the access token (error 6).")

    monkeypatch.setattr(oauth, "registered_races", reject)
    with pytest.raises(oauth.OAuthFlowError):
        ct.discover_connected_candidates("stale-token")


# -------------------------------------------------
# Coverage.describe -- counts only, and must not mislead
# -------------------------------------------------
def test_describe_distinguishes_no_registrations_from_none_past():
    empty = ct.candidates_from_registrations([], today=date(2026, 10, 11)).coverage
    assert "no registrations" in empty.describe().lower()


def test_describe_does_not_claim_a_search_that_did_not_happen():
    upcoming_only = ct.candidates_from_registrations(
        [registration(race_date="2027-01-01")], today=date(2026, 10, 11)
    ).coverage
    text = upcoming_only.describe().lower()
    # Must not read as "we searched and found nothing" (cf. SPR-49).
    assert "none are in the past" in text
    assert "no results" not in text.replace("no results to look for", "")


def test_describe_says_so_when_the_cap_was_hit(api):
    capped = ct.candidates_from_registrations(
        [registration(race_id=1, race_date="2024-05-04"), registration(race_id=2, race_date="2023-05-07")],
        max_calls=2,
        today=date(2026, 10, 11),
    ).coverage
    assert capped.capped is True
    assert "call cap" in capped.describe()


def test_describe_never_includes_a_race_name(api):
    coverage = ct.candidates_from_registrations(
        [registration(race_id=1, race_date="2024-05-04", name="Lincoln Half")],
        max_calls=20,
        today=date(2026, 10, 11),
    ).coverage
    assert "Lincoln" not in coverage.describe()
