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


if __name__ == "__main__":
    unittest.main()
