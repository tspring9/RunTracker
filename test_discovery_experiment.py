"""Tests for the checked-in SPR-14 Streamlit review data."""

import unittest
import discovery_experiment as discovery


class DiscoveryExperimentTests(unittest.TestCase):
    def test_request_accounting_matches_reported_total(self):
        self.assertEqual(sum(row["Requests"] for row in discovery.REQUEST_COUNTS), 88)

    def test_runner_counts_match_evidence(self):
        for runner in discovery.RUNNERS:
            rows = [row for row in discovery.CANDIDATE_RESULTS if row["Runner"] == runner["Runner"]]
            self.assertEqual(runner["Candidates"], len(rows))
            self.assertEqual(runner["High confidence"], sum(row["Classification"] == "High confidence" for row in rows))

    def test_review_rows_use_only_anonymous_labels(self):
        self.assertEqual({row["Runner"] for row in discovery.RUNNERS}, {"Runner A", "Runner B"})
        self.assertEqual({row["Runner"] for row in discovery.CANDIDATE_RESULTS}, {"Runner A", "Runner B"})


if __name__ == "__main__":
    unittest.main()
