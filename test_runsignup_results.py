import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

import runsignup_results as rsu


class SearchRacesTests(unittest.TestCase):
    @patch("runsignup_results._get")
    def test_future_search_uses_iso_dates_and_filters_stale_listings(self, mock_get):
        future = date.today() + timedelta(days=30)
        mock_get.return_value = {
            "races": [
                {
                    "race": {
                        "race_id": 1,
                        "name": "Real Future Race",
                        "next_date": "01/01/2020",
                        "url": "https://example.test/1",
                        "address": {"city": "Lincoln", "state": "ne"},
                        "events": [{"start_time": f"{future.month}/{future.day}/{future.year} 08:00"}],
                    }
                },
                {
                    "race": {
                        "race_id": 2,
                        "name": "Stale Series",
                        "next_date": None,
                        "events": [{"start_time": "1/1/2020 08:00"}],
                    }
                },
            ]
        }

        results = rsu.search_races("Lincoln", state="ne", include_past=False)

        self.assertEqual([race["race_id"] for race in results], [1])
        _, params = mock_get.call_args
        self.assertRegex(params["start_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(params["end_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(params["events"], "T")
        self.assertEqual(params["state"], "NE")

    def test_blank_name_is_rejected_without_a_request(self):
        with self.assertRaises(ValueError):
            rsu.search_races("  ")


class DiscoveryTests(unittest.TestCase):
    @patch("runsignup_results.list_result_sets")
    @patch("runsignup_results.fetch_race")
    def test_default_limits_requests_to_five_event_days(self, mock_race, mock_sets):
        events = []
        for year in range(2018, 2026):
            for distance in ("5K", "10K"):
                events.append(
                    {
                        "event_id": year * 10 + len(events),
                        "race_event_days_id": year,
                        "name": distance,
                        "start_time": f"5/1/{year} 07:00",
                    }
                )
        mock_race.return_value = {"name": "Test Race", "address": {}, "events": events}
        mock_sets.return_value = []

        rsu.discover_result_sets(123)

        self.assertEqual(mock_sets.call_count, 10)

    @patch("runsignup_results.list_result_sets")
    @patch("runsignup_results.fetch_race")
    def test_none_opts_into_all_event_days(self, mock_race, mock_sets):
        mock_race.return_value = {
            "name": "Test Race",
            "address": {},
            "events": [
                {
                    "event_id": year,
                    "race_event_days_id": year,
                    "name": "5K",
                    "start_time": f"5/1/{year} 07:00",
                }
                for year in range(2018, 2026)
            ],
        }
        mock_sets.return_value = []

        rsu.discover_result_sets(123, max_event_days=None)

        self.assertEqual(mock_sets.call_count, 8)


class ResultSetCatalogTests(unittest.TestCase):
    @patch("runsignup_results._get")
    def test_maps_individual_result_set_id_to_result_set_id(self, mock_get):
        mock_get.return_value = {
            "result_sets": [
                {
                    "individual_result_set_id": 10,
                    "race_id": 2017,
                    "event_id": 4002,
                    "race_name": "No Frills, Just Thrills",
                    "individual_result_set_name": "results",
                    "last_modified_ts": 0,
                }
            ]
        }

        rows = rsu.fetch_result_set_catalog(page=1, results_per_page=5000)

        self.assertEqual(
            rows,
            [
                {
                    "race_id": 2017,
                    "event_id": 4002,
                    "result_set_id": 10,
                    "race_name": "No Frills, Just Thrills",
                    "last_modified_ts": 0,
                }
            ],
        )
        path, params = mock_get.call_args[0][0], mock_get.call_args[1]
        self.assertEqual(path, rsu.RESULT_SET_CATALOG_HOST_PATH)
        self.assertEqual(params["num_per_page"], 5000)
        self.assertNotIn("modified_since_timestamp", params)

    @patch("runsignup_results._get")
    def test_empty_page_is_the_paging_terminator(self, mock_get):
        mock_get.return_value = {"result_sets": []}
        self.assertEqual(rsu.fetch_result_set_catalog(page=99), [])

    @patch("runsignup_results._get")
    def test_modified_since_timestamp_is_forwarded_when_given(self, mock_get):
        mock_get.return_value = {"result_sets": []}
        rsu.fetch_result_set_catalog(modified_since_timestamp=1523774999)
        self.assertEqual(mock_get.call_args[1]["modified_since_timestamp"], 1523774999)


class SearchRacesByStateRangeTests(unittest.TestCase):
    @patch("runsignup_results._get")
    def test_shapes_race_rows_with_event_dates(self, mock_get):
        mock_get.return_value = {
            "races": [
                {
                    "race": {
                        "race_id": 197893,
                        "name": "10K BRIN series training",
                        "address": {"city": "Lincoln", "state": "ne"},
                        "url": "https://runsignup.com/Race/NE/Lincoln/10K",
                        "events": [{"start_time": "1/11/2026 08:00"}],
                    }
                }
            ]
        }

        races = rsu.search_races_by_state_range("ne", "2026-01-01", "2026-12-31")

        self.assertEqual(
            races,
            [
                {
                    "race_id": 197893,
                    "name": "10K BRIN series training",
                    "city": "Lincoln",
                    "state": "NE",
                    "url": "https://runsignup.com/Race/NE/Lincoln/10K",
                    "event_dates": ["2026-01-11"],
                }
            ],
        )
        _, params = mock_get.call_args
        self.assertEqual(params["state"], "NE")
        self.assertEqual(params["events"], "T")
        self.assertEqual(params["start_date"], "2026-01-01")
        self.assertEqual(params["end_date"], "2026-12-31")


class EventHasResultsTests(unittest.TestCase):
    @patch("runsignup_results._get")
    def test_true_when_api_reports_results(self, mock_get):
        mock_get.return_value = {"has_results": "T"}
        self.assertTrue(rsu.event_has_results(2017, 4002))
        path = mock_get.call_args[0][0]
        self.assertEqual(path, "/race/2017/results/has-result-sets")
        self.assertEqual(mock_get.call_args[1]["event_id"], 4002)

    @patch("runsignup_results._get")
    def test_false_when_api_reports_no_results(self, mock_get):
        mock_get.return_value = {"has_results": "F"}
        self.assertFalse(rsu.event_has_results(2017, 4002))

    @patch("runsignup_results._get")
    def test_never_raises_on_api_error(self, mock_get):
        mock_get.side_effect = rsu.RunSignUpError("Event not found.")
        self.assertFalse(rsu.event_has_results(2017, 999999999))


# ---------------------------------------------------------------------------
# _get hardening (SPR-30/SPR-42) -- a raw upstream body must never reach an
# exception message, and specific upstream failures get plain-language text.
# ---------------------------------------------------------------------------
class GetHardeningTests(unittest.TestCase):
    def _fake_response(self, status_code=200, json_payload=None, json_raises=False):
        response = Mock()
        response.status_code = status_code
        if json_raises:
            response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
        else:
            response.json.return_value = json_payload
        response.text = "<html><body>a 10,000-character name produced this</body></html>"
        return response

    @patch("runsignup_results._SESSION")
    def test_non_json_body_raises_generic_message_not_the_html(self, mock_session):
        mock_session.get.return_value = self._fake_response(status_code=200, json_raises=True)
        with self.assertRaises(rsu.RunSignUpError) as ctx:
            rsu._get("/races", state="MO")
        self.assertNotIn("html", str(ctx.exception).lower())
        self.assertNotIn("10,000", str(ctx.exception))

    @patch("runsignup_results._SESSION")
    def test_error_code_13_names_credentials_scope_never_log_in(self, mock_session):
        mock_session.get.return_value = self._fake_response(
            status_code=200,
            json_payload={"error": {"error_code": 13, "error_msg": "You must be logged in to the API as a user"}},
        )
        with self.assertRaises(rsu.RunSignUpError) as ctx:
            rsu._get("/races", state="MO", page=2)
        message = str(ctx.exception).lower()
        self.assertIn("credentials", message)
        self.assertNotIn("log in", message)
        self.assertNotIn("logged in", message)

    @patch("runsignup_results._SESSION")
    def test_http_414_names_the_name_length_not_the_body(self, mock_session):
        mock_session.get.return_value = self._fake_response(status_code=414, json_raises=True)
        with self.assertRaises(rsu.RunSignUpError) as ctx:
            rsu._get("/races", first_name="Q" * 10000)
        self.assertIn("too long", str(ctx.exception).lower())
        self.assertNotIn("html", str(ctx.exception).lower())

    @patch("runsignup_results._SESSION")
    def test_other_non_200_status_never_includes_the_raw_body(self, mock_session):
        mock_session.get.return_value = self._fake_response(status_code=500, json_payload={"ok": False})
        with self.assertRaises(rsu.RunSignUpError) as ctx:
            rsu._get("/races", state="MO")
        self.assertNotIn("html", str(ctx.exception).lower())

    @patch("runsignup_results._SESSION")
    def test_successful_response_passes_through(self, mock_session):
        mock_session.get.return_value = self._fake_response(status_code=200, json_payload={"races": []})
        self.assertEqual(rsu._get("/races", state="MO"), {"races": []})


# ---------------------------------------------------------------------------
# search_races_in_window (SPR-30/SPR-36) -- adaptive date-window splitting;
# must never request /races page >= 2.
# ---------------------------------------------------------------------------
class SearchRacesInWindowTests(unittest.TestCase):
    class _FakeBudget:
        def __init__(self, limit):
            self.limit = limit
            self.used = 0

        def spend(self, n=1):
            if self.used + n > self.limit:
                return False
            self.used += n
            return True

    def test_never_requests_page_two(self):
        seen_pages = []

        def fake_search(state, start_date, end_date, page=1, results_per_page=1000):
            seen_pages.append(page)
            return [{"race_id": 1, "name": "r", "city": "", "state": state, "url": "", "event_dates": [start_date]}]

        with patch("runsignup_results.search_races_by_state_range", side_effect=fake_search):
            rsu.search_races_in_window("MO", "2026-01-01", "2026-12-31", self._FakeBudget(limit=10))

        self.assertTrue(all(page == 1 for page in seen_pages))

    def test_splits_a_full_page_in_half_and_merges_results(self):
        calls = []

        def fake_search(state, start_date, end_date, page=1, results_per_page=1000):
            calls.append((start_date, end_date))
            if (start_date, end_date) == ("2026-01-01", "2026-12-31"):
                return [
                    {"race_id": i, "name": f"r{i}", "city": "", "state": state, "url": "", "event_dates": ["2026-06-01"]}
                    for i in range(rsu.MAX_RESULTS_PER_PAGE)
                ]
            return [{"race_id": 9000, "name": "half", "city": "", "state": state, "url": "", "event_dates": [start_date]}]

        with patch("runsignup_results.search_races_by_state_range", side_effect=fake_search):
            result = rsu.search_races_in_window("MO", "2026-01-01", "2026-12-31", self._FakeBudget(limit=10))

        self.assertEqual(len(calls), 3)  # the full-window call, then two halves
        self.assertTrue(all(start != "2026-01-01" or end != "2026-12-31" for start, end in calls[1:]))
        self.assertEqual({race["race_id"] for race in result}, {9000})

    def test_never_spends_past_the_budget(self):
        def fake_search(state, start_date, end_date, page=1, results_per_page=1000):
            # Always a full page, so this would split forever without a budget.
            return [
                {"race_id": i, "name": f"r{i}", "city": "", "state": state, "url": "", "event_dates": ["2026-06-01"]}
                for i in range(rsu.MAX_RESULTS_PER_PAGE)
            ]

        budget = self._FakeBudget(limit=4)
        with patch("runsignup_results.search_races_by_state_range", side_effect=fake_search):
            rsu.search_races_in_window("MO", "2026-01-01", "2026-12-31", budget)

        self.assertLessEqual(budget.used, 4)


if __name__ == "__main__":
    unittest.main()
