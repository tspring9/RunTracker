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


class TrackerSearchTests(unittest.TestCase):
    def test_name_split_requires_nonblank_and_preserves_first_and_surname(self):
        self.assertEqual(rsu.split_runner_name(" Thomas   Springhower "), ("Thomas", "Springhower"))
        self.assertEqual(rsu.split_runner_name("Olivia"), ("Olivia", ""))
        with self.assertRaises(ValueError):
            rsu.split_runner_name("  ")

    def test_race_confirmation_requires_matching_state_and_nearby_date(self):
        tracked = {"state": "MO", "race_date": "2026-05-16"}
        self.assertTrue(
            rsu.race_match_is_confirmed(
                tracked, {"state": "mo", "next_date": "2026-05-20"}
            )
        )
        self.assertFalse(
            rsu.race_match_is_confirmed(
                tracked, {"state": "KS", "next_date": "2026-05-16"}
            )
        )
        self.assertFalse(
            rsu.race_match_is_confirmed(
                tracked, {"state": "MO", "next_date": "2026-06-16"}
            )
        )

    @patch("runsignup_results.find_runner_results")
    @patch("runsignup_results.search_races")
    def test_full_name_searches_only_uniquely_confirmed_race(self, mock_search, mock_find):
        mock_search.return_value = [
            {
                "race_id": 85066,
                "name": "Hospital Hill Run",
                "state": "MO",
                "next_date": "2026-05-16",
            }
        ]
        mock_find.return_value = [{"runner_name": "thomas springhower"}]

        reports = rsu.search_tracker_results(
            "Thomas Springhower",
            [
                {
                    "race_name": "Hospital Hill Run",
                    "race_date": "2026-05-16",
                    "state": "MO",
                }
            ],
        )

        self.assertEqual(reports[0]["status"], "searched")
        self.assertEqual(reports[0]["count"], 1)
        self.assertFalse(reports[0]["ambiguous"])
        mock_find.assert_called_once_with(
            85066,
            first_name="Thomas",
            last_name="Springhower",
            include_virtual=False,
            max_event_days=rsu.DEFAULT_DISCOVERY_EVENT_DAYS,
        )

    @patch("runsignup_results.find_runner_results")
    @patch("runsignup_results.search_races")
    def test_first_name_only_reports_candidates_as_ambiguous(self, mock_search, mock_find):
        mock_search.return_value = [
            {
                "race_id": 85066,
                "name": "Hospital Hill Run",
                "state": "MO",
                "next_date": "2026-05-16",
            }
        ]
        mock_find.return_value = [{"runner_name": f"Olivia {index}"} for index in range(22)]

        [report] = rsu.search_tracker_results(
            "Olivia",
            [
                {
                    "race_name": "Hospital Hill Run",
                    "race_date": "2026-05-16",
                    "state": "MO",
                }
            ],
        )

        self.assertTrue(report["ambiguous"])
        self.assertEqual(report["count"], 22)

    @patch("runsignup_results.find_runner_results")
    @patch("runsignup_results.search_races")
    def test_unconfirmed_hit_needs_linking_and_is_not_searched(self, mock_search, mock_find):
        mock_search.return_value = [
            {
                "race_id": 99,
                "name": "Early Bird Run",
                "state": "MD",
                "next_date": "2026-04-04",
            }
        ]

        [report] = rsu.search_tracker_results(
            "Rachel Ballard",
            [
                {
                    "race_name": "Early Bird Run",
                    "race_date": "2026-04-04",
                    "state": "NE",
                }
            ],
        )

        self.assertEqual(report["status"], "needs_linking")
        mock_find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
