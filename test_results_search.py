"""Unit tests for results_search.py.

No live network: runsignup_results's HTTP-calling functions are mocked.
candidate_result_sets exercises the real SQLite storage layer (temp db) so
the cache-merge-and-reload path is tested end to end.

    python -m pytest test_results_search.py -v
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import results_search as rs
import storage


class CandidateResultSetsTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["RUNTRACKER_DB_PATH"] = self.db_path
        storage.reset_backend_cache()

    def tearDown(self):
        storage.reset_backend_cache()
        os.environ.pop("RUNTRACKER_DB_PATH", None)
        os.remove(self.db_path)

    @patch("results_search.rsu.fetch_result_set_catalog")
    @patch("results_search.rsu.search_races_by_state_range")
    def test_filters_to_in_scope_non_virtual_races(self, mock_races, mock_catalog):
        mock_races.return_value = [
            {
                "race_id": 1, "name": "Lincoln 5K", "city": "Lincoln", "state": "NE",
                "url": "https://runsignup.com/Race/1", "event_dates": ["2026-05-01"],
            },
            {
                "race_id": 2, "name": "Virtual Series 5K", "city": "", "state": "NE",
                "url": "https://runsignup.com/Race/2", "event_dates": ["2026-06-01"],
            },
        ]
        mock_catalog.return_value = [
            {"race_id": 1, "event_id": 100, "result_set_id": 200, "race_name": "Lincoln 5K", "last_modified_ts": 10},
            {"race_id": 2, "event_id": 101, "result_set_id": 201, "race_name": "Virtual Series 5K", "last_modified_ts": 20},
            {"race_id": 999, "event_id": 500, "result_set_id": 600, "race_name": "Unrelated", "last_modified_ts": 30},
        ]

        scope = rs.SearchScope(states=("NE",), start_year=2026, end_year=2026, max_calls=50)
        candidates = rs.candidate_result_sets(scope)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["race_id"], 1)
        self.assertEqual(candidate["event_id"], 100)
        self.assertEqual(candidate["result_set_id"], 200)
        self.assertEqual(candidate["city"], "Lincoln")
        self.assertEqual(candidate["url"], "https://runsignup.com/Race/1")
        self.assertEqual(candidate["event_date"], "2026-05-01")
        self.assertEqual(candidate["race_type"], "5K")

        # catalog_watermark() is storage.py's "max last_modified_ts" over
        # PERSISTED rows (per the SPR-17 contract) -- and only in-scope rows
        # get persisted, so it reflects the in-scope max (10), not the
        # highest ts actually walked (30, from the out-of-scope race_id=999
        # row). A scope that stays empty across searches will therefore
        # re-walk the same already-seen-but-irrelevant catalog span each
        # time rather than skipping past it -- a known limitation of the
        # ticket's literal "derive the watermark from storage" contract.
        self.assertEqual(storage.catalog_watermark(), 10)

    @patch("results_search.rsu.fetch_result_set_catalog")
    @patch("results_search.rsu.search_races_by_state_range")
    def test_include_virtual_keeps_virtual_races(self, mock_races, mock_catalog):
        mock_races.return_value = [
            {
                "race_id": 2, "name": "Virtual Series 5K", "city": "", "state": "NE",
                "url": "https://runsignup.com/Race/2", "event_dates": ["2026-06-01"],
            },
        ]
        mock_catalog.return_value = [
            {"race_id": 2, "event_id": 101, "result_set_id": 201, "race_name": "Virtual Series 5K", "last_modified_ts": 20},
        ]

        scope = rs.SearchScope(states=("NE",), start_year=2026, end_year=2026, include_virtual=True, max_calls=50)
        candidates = rs.candidate_result_sets(scope)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["race_id"], 2)

    @patch("results_search.rsu.fetch_result_set_catalog")
    @patch("results_search.rsu.search_races_by_state_range")
    def test_zero_call_budget_never_makes_a_request(self, mock_races, mock_catalog):
        scope = rs.SearchScope(states=("NE",), start_year=2026, end_year=2026, max_calls=0)

        progress_calls = []
        candidates = rs.candidate_result_sets(scope, progress_cb=progress_calls.append)

        self.assertEqual(candidates, [])
        mock_races.assert_not_called()
        mock_catalog.assert_not_called()
        self.assertTrue(progress_calls[-1].capped)
        self.assertEqual(progress_calls[-1].stage, "capped")

    @patch("results_search.rsu.fetch_result_set_catalog")
    @patch("results_search.rsu.search_races_by_state_range")
    def test_never_exceeds_the_call_cap(self, mock_races, mock_catalog):
        # Each page claims to be full (1000 rows), which would page forever
        # without a cap -- this asserts the cap actually stops it.
        mock_races.return_value = [{"race_id": i, "name": "R", "city": "", "state": "NE",
                                     "url": "", "event_dates": ["2026-01-01"]} for i in range(1000)]
        mock_catalog.return_value = []

        scope = rs.SearchScope(states=("NE",), start_year=2026, end_year=2026, max_calls=3)
        rs.candidate_result_sets(scope)

        self.assertLessEqual(mock_races.call_count, 3)

    @patch("results_search.rsu.fetch_result_set_catalog")
    @patch("results_search.rsu.search_races_by_state_range")
    def test_cached_result_survives_a_second_call_with_no_new_races_stage(self, mock_races, mock_catalog):
        mock_races.return_value = [
            {"race_id": 1, "name": "Lincoln 5K", "city": "Lincoln", "state": "NE",
             "url": "https://x/1", "event_dates": ["2026-05-01"]},
        ]
        mock_catalog.return_value = [
            {"race_id": 1, "event_id": 100, "result_set_id": 200, "race_name": "Lincoln 5K", "last_modified_ts": 10},
        ]
        scope = rs.SearchScope(states=("NE",), start_year=2026, end_year=2026, max_calls=50)
        first = rs.candidate_result_sets(scope)
        self.assertEqual(len(first), 1)

        # Second call: no new catalog rows, but the first call's result is
        # still readable from storage and still carries its city/url.
        mock_catalog.return_value = []
        second = rs.candidate_result_sets(scope)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["city"], "Lincoln")


class SweepForRunnerTests(unittest.TestCase):
    def _candidate(self, race_id, event_id, result_set_id=1):
        return {
            "race_id": race_id, "event_id": event_id, "result_set_id": result_set_id,
            "race_name": "Test Race", "event_date": "2026-05-01", "state": "NE",
            "city": "Lincoln", "url": "", "race_type": "5K",
        }

    def test_requires_both_names(self):
        scope = rs.SearchScope(states=(), start_year=2026, end_year=2026)
        with self.assertRaises(ValueError):
            rs.sweep_for_runner("", "Springhower", [], scope)
        with self.assertRaises(ValueError):
            rs.sweep_for_runner("Thomas", "", [], scope)

    @patch("results_search.rsu.fetch_results")
    def test_returns_only_locally_reconfirmed_exact_matches(self, mock_fetch):
        results_by_event = {
            100: [{"first_name": "Thomas", "last_name": "Springhower", "age": 33, "chip_time": "1:20:00"}],
            101: [{"first_name": "Thom", "last_name": "Springhower", "age": 30}],  # server partial match, local reject
        }
        mock_fetch.side_effect = lambda race_id, event_id, result_set_id, **kw: results_by_event.get(event_id, [])

        candidates = [self._candidate(1, 100), self._candidate(2, 101)]
        scope = rs.SearchScope(states=(), start_year=2026, end_year=2026, max_calls=400)

        matches, progress = rs.sweep_for_runner("Thomas", "Springhower", candidates, scope)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["runner_name"], "Thomas Springhower")
        self.assertEqual(matches[0]["_raw"]["age"], 33)
        self.assertEqual(matches[0]["_context"], candidates[0])
        self.assertEqual(progress.stage, "done")
        self.assertFalse(progress.capped)
        self.assertEqual(progress.calls_used, 2)
        self.assertEqual(progress.candidates_checked, 2)
        self.assertEqual(progress.cursor, "")

    @patch("results_search.rsu.fetch_results")
    def test_no_dob_or_email_anywhere_in_a_match_row(self, mock_fetch):
        mock_fetch.return_value = [
            {"first_name": "Thomas", "last_name": "Springhower", "age": 33, "dob": "1993-01-01", "email": "t@example.com"}
        ]
        candidates = [self._candidate(1, 100)]
        scope = rs.SearchScope(states=(), start_year=2026, end_year=2026, max_calls=400)

        matches, _ = rs.sweep_for_runner("Thomas", "Springhower", candidates, scope)

        # The raw record is handed back unmodified (whatever RunSignUp
        # sent) -- sweep_for_runner's job is to never itself ask for,
        # compute with, or special-case DOB/email, not to scrub the raw
        # payload it is displaying.
        self.assertEqual(matches[0]["_raw"]["age"], 33)
        # Nothing on the shaped tracker row is DOB/email-shaped.
        self.assertNotIn("dob", matches[0])
        self.assertNotIn("email", matches[0])

    @patch("results_search.rsu.fetch_results")
    def test_never_exceeds_the_call_cap_and_resume_cursor_continues(self, mock_fetch):
        mock_fetch.return_value = []
        candidates = [self._candidate(i, i) for i in range(5)]
        scope = rs.SearchScope(states=(), start_year=2026, end_year=2026, max_calls=2)

        _, first_progress = rs.sweep_for_runner("A", "B", candidates, scope)
        self.assertTrue(first_progress.capped)
        self.assertEqual(first_progress.stage, "capped")
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(first_progress.cursor, "2")

        mock_fetch.reset_mock()
        scope2 = rs.SearchScope(states=(), start_year=2026, end_year=2026, max_calls=10)
        _, second_progress = rs.sweep_for_runner(
            "A", "B", candidates, scope2, resume_cursor=first_progress.cursor
        )
        self.assertEqual(mock_fetch.call_count, 3)
        self.assertFalse(second_progress.capped)
        self.assertEqual(second_progress.stage, "done")
        self.assertEqual(second_progress.candidates_checked, 5)

    @patch("results_search.rsu.fetch_results")
    def test_empty_candidates_is_a_trivial_done(self, mock_fetch):
        scope = rs.SearchScope(states=(), start_year=2026, end_year=2026, max_calls=400)
        matches, progress = rs.sweep_for_runner("A", "B", [], scope)
        self.assertEqual(matches, [])
        self.assertEqual(progress.stage, "done")
        self.assertEqual(progress.calls_used, 0)
        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
