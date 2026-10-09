import unittest
from datetime import date, timedelta
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
