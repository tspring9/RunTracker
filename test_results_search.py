"""Independent contract tests for the capped RunSignUp results sweep."""

from threading import Lock
import ast
import inspect
import os
import re
import tempfile
import time
import unittest
import unittest.mock

import results_search as search
import storage


def candidates(count):
    return [
        {
            "race_id": 1000 + index,
            "event_id": 2000 + index,
            "result_set_id": 3000 + index,
            "race_name": f"Race {index}",
            "event_date": "2026-06-01",
            "state": "NE",
            "city": "Omaha",
            "url": f"https://example.test/races/{index}",
            "race_type": "5K",
        }
        for index in range(count)
    ]


def scope(max_calls):
    return search.SearchScope(states=("NE",), start_year=2026, end_year=2026, max_calls=max_calls)


def test_sweep_honors_call_cap(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(search.rsu, "fetch_results", fake_fetch)
    _, progress = search.sweep_for_runner("Tom", "Spring", candidates(50), scope(5))

    assert len(calls) <= 5
    assert progress.capped is True
    assert progress.stage == "capped"
    assert progress.calls_used <= progress.calls_budget == 5


def test_resume_cursor_continues_without_rechecking_candidates(monkeypatch):
    seen = []

    def fake_fetch(*args, **kwargs):
        seen.append(args[2] if len(args) > 2 else kwargs["result_set_id"])
        return []

    monkeypatch.setattr(search.rsu, "fetch_results", fake_fetch)
    all_candidates = candidates(8)
    _, first = search.sweep_for_runner("Tom", "Spring", all_candidates, scope(3))
    first_seen = set(seen)
    assert first.cursor

    seen.clear()
    _, second = search.sweep_for_runner(
        "Tom", "Spring", all_candidates, scope(3), resume_cursor=first.cursor
    )

    assert first_seen.isdisjoint(seen)
    assert second.candidates_checked > first.candidates_checked


def test_partial_server_hit_is_excluded_by_local_reconfirmation(monkeypatch):
    monkeypatch.setattr(
        search.rsu,
        "fetch_results",
        lambda *args, **kwargs: [
            {
                "first_name": "Thomas",
                "last_name": "Springhower",
                "chip_time": "0:20:00",
                "age": 40,
            }
        ],
    )

    results, _ = search.sweep_for_runner("Tom", "Spring", candidates(1), scope(1))
    assert results == []


def test_sweep_never_exceeds_two_requests_in_flight(monkeypatch):
    lock = Lock()
    current = 0
    observed_max = 0

    def fake_fetch(*args, **kwargs):
        nonlocal current, observed_max
        with lock:
            current += 1
            observed_max = max(observed_max, current)
        time.sleep(0.02)
        with lock:
            current -= 1
        return []

    monkeypatch.setattr(search.rsu, "fetch_results", fake_fetch)
    search.sweep_for_runner("Tom", "Spring", candidates(12), scope(12))
    assert observed_max <= 2
    assert observed_max == 2


def test_progress_callback_is_monotonic(monkeypatch):
    updates = []
    monkeypatch.setattr(search.rsu, "fetch_results", lambda *args, **kwargs: [])

    search.sweep_for_runner(
        "Tom", "Spring", candidates(7), scope(4), progress_cb=updates.append
    )

    assert updates
    checked = [update.candidates_checked for update in updates]
    calls = [update.calls_used for update in updates]
    assert checked == sorted(checked)
    assert calls == sorted(calls)


def test_sweep_signature_and_source_exclude_private_identity_fields():
    parameters = inspect.signature(search.sweep_for_runner).parameters
    forbidden = re.compile(r"(?:^|_)(?:dob|birth|email)(?:_|$)", re.IGNORECASE)

    assert not any(forbidden.search(name) for name in parameters)
    tree = ast.parse(inspect.getsource(search))
    source_identifiers = {
        value.lower()
        for node in ast.walk(tree)
        for value in (
            [node.id] if isinstance(node, ast.Name) else
            [node.arg] if isinstance(node, ast.arg) else
            [node.attr] if isinstance(node, ast.Attribute) else []
        )
    }
    assert not ({"dob", "birth", "email"} & source_identifiers)


# ---------------------------------------------------------------------------
# De-duplication across result sets (SPR-15 follow-up).
#
# Pinned to a real measurement: RunSignUp race 6606 / event 16127 publishes
# four result sets (1313, 1317, 1334, 1335) and every finisher appears in all
# four. Candidates are (race_id, event_id, result_set_id) triples, so without
# collapsing, one race shows up four times -- each with its own "This is me"
# button, i.e. four tracker entries for one finish.
# ---------------------------------------------------------------------------


def _match(result_set_id, bib="101", age=40, first="Justin", last="Spring", finish="22:15"):
    return {
        "race_name": "Real Race",
        "finish_time": finish,
        "_raw": {"first_name": first, "last_name": last, "age": age, "bib": bib},
        "_context": {"race_id": 6606, "event_id": 16127, "result_set_id": result_set_id},
    }


def test_same_finish_in_four_result_sets_collapses_to_one_row():
    rows = [_match(sid) for sid in (1313, 1317, 1334, 1335)]

    deduped = search.dedupe_matches(rows)

    assert len(deduped) == 1


def test_dedupe_keys_on_bib_within_an_event_not_on_result_set():
    # Same event, same name, different bib -> two different people, both kept.
    rows = [_match(1313, bib="101"), _match(1317, bib="202")]

    assert len(search.dedupe_matches(rows)) == 2


def test_dedupe_does_not_merge_across_different_events():
    first = _match(1313)
    second = _match(1313)
    second["_context"] = {"race_id": 6606, "event_id": 99999, "result_set_id": 1313}

    assert len(search.dedupe_matches([first, second])) == 2


def test_dedupe_without_bib_keeps_two_same_name_runners_with_different_times():
    rows = [
        _match(1313, bib="", finish="22:15"),
        _match(1317, bib="", finish="31:48"),
    ]

    assert len(search.dedupe_matches(rows)) == 2


def test_dedupe_without_bib_collapses_identical_unbibbed_finishes():
    rows = [_match(1313, bib=""), _match(1317, bib="")]

    assert len(search.dedupe_matches(rows)) == 1


def test_dedupe_prefers_the_duplicate_that_carries_a_usable_age():
    # Age is the only signal that can lift a row out of "Possible", so the
    # informative duplicate must win regardless of arrival order.
    without_age = _match(1313, age="")
    with_age = _match(1317, age=40)

    assert search.dedupe_matches([without_age, with_age])[0]["_raw"]["age"] == 40
    assert search.dedupe_matches([with_age, without_age])[0]["_raw"]["age"] == 40


def test_dedupe_treats_junk_age_as_unusable_when_choosing_a_winner():
    # age=952 is a real published value; it must not beat a usable age.
    junk = _match(1313, age=952)
    usable = _match(1317, age=40)

    assert search.dedupe_matches([junk, usable])[0]["_raw"]["age"] == 40


def test_dedupe_is_order_stable_and_handles_empty_input():
    assert search.dedupe_matches([]) == []

    a = _match(1313, bib="1")
    b = _match(1313, bib="2")
    c = _match(1313, bib="3")
    order = [row["_raw"]["bib"] for row in search.dedupe_matches([a, b, c, a, b])]

    assert order == ["1", "2", "3"]


def test_dedupe_tolerates_rows_missing_raw_and_context():
    rows = [{}, {"_raw": None, "_context": None}]

    assert len(search.dedupe_matches(rows)) == 1


def test_match_identity_ignores_result_set_id():
    assert search.match_identity(_match(1313)) == search.match_identity(_match(9999))


# ---------------------------------------------------------------------------
# CallBudget (SPR-30/SPR-41) -- one counter, shared by every call a search
# or an index build makes. The whole point is that it can never be spent
# past its limit.
# ---------------------------------------------------------------------------
def test_call_budget_spend_refuses_past_the_limit():
    budget = search.CallBudget(limit=2)
    assert budget.spend() is True
    assert budget.spend() is True
    assert budget.spend() is False
    assert budget.used == 2


def test_call_budget_spend_n_is_atomic_all_or_nothing():
    budget = search.CallBudget(limit=3)
    assert budget.spend(2) is True
    assert budget.used == 2
    assert budget.spend(2) is False  # would be 4, over the limit
    assert budget.used == 2  # refused spend does not partially apply


def test_search_scope_default_max_calls_is_120():
    scope = search.SearchScope(states=("MO",), start_year=2026, end_year=2026)
    assert scope.max_calls == 120


# ---------------------------------------------------------------------------
# Coverage -- SPR-30 replaces SearchProgress with this. The back-compat
# properties exist so app.py's shared progress callback (and this test
# file's pre-SPR-30 assertions) keep working unmigrated.
# ---------------------------------------------------------------------------
def test_coverage_back_compat_properties_mirror_the_real_fields():
    coverage = search.Coverage(
        result_sets_known=10, result_sets_checked=4, races_known=3,
        budget_limit=5, budget_used=4, complete=False,
        index_built_at="2026-10-08T00:00:00+00:00", cursor="4",
    )
    assert coverage.candidates_total == 10
    assert coverage.candidates_checked == 4
    assert coverage.calls_budget == 5
    assert coverage.calls_used == 4
    assert coverage.capped is True
    assert coverage.stage == "capped"


def test_coverage_stage_is_done_when_complete():
    coverage = search.Coverage(
        result_sets_known=10, result_sets_checked=10, races_known=3,
        budget_limit=5, budget_used=5, complete=True,
        index_built_at="", cursor="",
    )
    assert coverage.capped is False
    assert coverage.stage == "done"


def test_search_progress_is_an_alias_for_coverage():
    assert search.SearchProgress is search.Coverage


# ---------------------------------------------------------------------------
# sweep_for_runner -- a single CallBudget means it can never spend more
# than scope.max_calls, no matter how many candidates are offered.
# ---------------------------------------------------------------------------
def test_sweep_never_spends_more_than_the_single_budget(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        return []

    monkeypatch.setattr(search.rsu, "fetch_results", fake_fetch)
    _, coverage = search.sweep_for_runner("Tom", "Spring", candidates(37), scope(13))

    assert len(calls) == 13
    assert coverage.budget_used == 13
    assert coverage.budget_used <= coverage.budget_limit


# ---------------------------------------------------------------------------
# indexed_candidates -- SQLite read only, zero HTTP. One row per
# (race_id, event_id); duplicate result sets for the same event collapse
# before any get-results call is spent.
# ---------------------------------------------------------------------------
class IndexedCandidatesTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["RUNTRACKER_DB_PATH"] = self.db_path
        storage.reset_backend_cache()

    def tearDown(self):
        storage.reset_backend_cache()
        os.environ.pop("RUNTRACKER_DB_PATH", None)
        os.remove(self.db_path)

    def test_empty_index_returns_no_candidates_and_no_http(self):
        result = search.indexed_candidates(scope(10))
        self.assertEqual(result, [])

    def test_makes_zero_http_calls(self):
        # No monkeypatch of rsu at all -- if this function ever reached the
        # network, the test would hang or fail on DNS/connection, not assert.
        storage.upsert_race_catalog({"race_id": 6606, "name": "Real Race", "city": "KC", "state": "MO", "url": ""})
        storage.upsert_result_set_catalog(
            {
                "race_id": 6606, "event_id": 16127, "result_set_id": 1313,
                "race_name": "Real Race", "event_date": "2026-06-01", "state": "MO",
                "last_modified_ts": 100,
            }
        )
        result = search.indexed_candidates(
            search.SearchScope(states=("MO",), start_year=2026, end_year=2026, max_calls=10)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["race_id"], 6606)
        self.assertEqual(result[0]["city"], "KC")

    def test_collapses_duplicate_result_sets_for_the_same_event(self):
        storage.upsert_race_catalog({"race_id": 6606, "name": "Real Race", "city": "KC", "state": "MO", "url": ""})
        storage.upsert_result_set_catalog(
            [
                {
                    "race_id": 6606, "event_id": 16127, "result_set_id": rsid,
                    "race_name": "Real Race", "event_date": "2026-06-01", "state": "MO",
                    "last_modified_ts": ts,
                }
                for rsid, ts in [(1313, 100), (1317, 300), (1334, 200), (1335, 50)]
            ]
        )
        result = search.indexed_candidates(
            search.SearchScope(states=("MO",), start_year=2026, end_year=2026, max_calls=10)
        )
        self.assertEqual(len(result), 1)
        # The most-recently-modified duplicate (1317, ts=300) wins.
        self.assertEqual(result[0]["result_set_id"], 1317)

    def test_ranks_preferred_states_first_then_newest_date_within_each_group(self):
        storage.upsert_race_catalog(
            [
                {"race_id": 1, "name": "A", "city": "", "state": "KS", "url": ""},
                {"race_id": 2, "name": "B", "city": "", "state": "MO", "url": ""},
                {"race_id": 3, "name": "C", "city": "", "state": "MO", "url": ""},
            ]
        )
        storage.upsert_result_set_catalog(
            [
                {"race_id": 1, "event_id": 10, "result_set_id": 100, "race_name": "A", "event_date": "2026-06-01", "state": "KS", "last_modified_ts": 1},
                {"race_id": 2, "event_id": 20, "result_set_id": 200, "race_name": "B", "event_date": "2026-01-01", "state": "MO", "last_modified_ts": 1},
                {"race_id": 3, "event_id": 30, "result_set_id": 300, "race_name": "C", "event_date": "2026-09-01", "state": "MO", "last_modified_ts": 1},
            ]
        )
        result = search.indexed_candidates(
            search.SearchScope(states=("MO", "KS"), start_year=2026, end_year=2026, max_calls=10),
            preferred_states=("MO",),
        )
        self.assertEqual([row["race_id"] for row in result], [3, 2, 1])


# ---------------------------------------------------------------------------
# build_index -- CLI-only offline builder. Never requests /races page >= 2
# (SPR-36) and never spends more than the given budget.
# ---------------------------------------------------------------------------
class BuildIndexTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["RUNTRACKER_DB_PATH"] = self.db_path
        storage.reset_backend_cache()

    def tearDown(self):
        storage.reset_backend_cache()
        os.environ.pop("RUNTRACKER_DB_PATH", None)
        os.remove(self.db_path)

    def test_build_index_writes_catalog_and_reports_coverage(self):
        def fake_window(state, start_date, end_date, budget):
            budget.spend()
            return [
                {
                    "race_id": 6606, "name": "Real Race", "city": "KC",
                    "state": "MO", "url": "", "event_dates": ["2026-06-01"],
                }
            ]

        def fake_catalog(modified_since_timestamp=None, page=1, results_per_page=5000):
            if page > 1:
                return []
            return [
                {
                    "race_id": 6606, "event_id": 16127, "result_set_id": 1313,
                    "race_name": "Real Race", "last_modified_ts": 100,
                }
            ]

        with unittest.mock.patch.object(search.rsu, "search_races_in_window", fake_window), \
             unittest.mock.patch.object(search.rsu, "fetch_result_set_catalog", fake_catalog):
            budget = search.CallBudget(limit=10)
            coverage = search.build_index(("MO",), 2026, 2026, budget)

        self.assertEqual(coverage.races_known, 1)
        self.assertEqual(coverage.result_sets_known, 1)
        self.assertTrue(coverage.complete)
        self.assertLessEqual(coverage.budget_used, coverage.budget_limit)

        candidates = search.indexed_candidates(
            search.SearchScope(states=("MO",), start_year=2026, end_year=2026, max_calls=10)
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["race_id"], 6606)

    def test_build_index_never_exceeds_the_budget(self):
        def fake_window(state, start_date, end_date, budget):
            # Simulate a state that would need many splits -- always "full".
            if not budget.spend():
                return []
            return [
                {"race_id": i, "name": f"r{i}", "city": "", "state": state, "url": "", "event_dates": ["2026-06-01"]}
                for i in range(1000)
            ]

        def fake_catalog(modified_since_timestamp=None, page=1, results_per_page=5000):
            return []

        with unittest.mock.patch.object(search.rsu, "search_races_in_window", fake_window), \
             unittest.mock.patch.object(search.rsu, "fetch_result_set_catalog", fake_catalog):
            budget = search.CallBudget(limit=3)
            coverage = search.build_index(("MO", "KS", "NE", "IA"), 2026, 2026, budget)

        self.assertLessEqual(budget.used, 3)
        self.assertEqual(coverage.budget_used, budget.used)
        self.assertFalse(coverage.complete)
