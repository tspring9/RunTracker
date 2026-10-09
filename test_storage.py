"""Unit tests for storage.py -- run with: python -m unittest test_storage"""

import os
import tempfile
import unittest

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["RUNTRACKER_DB_PATH"] = self.db_path
        storage.reset_backend_cache()

    def tearDown(self):
        storage.reset_backend_cache()
        os.environ.pop("RUNTRACKER_DB_PATH", None)
        os.remove(self.db_path)

    def sample_row(self, **overrides):
        row = {
            "state": "NE", "state_name": "Nebraska", "runner_name": "Tom",
            "race_type": "5K", "race_name": "Test Run", "race_date": "2026-01-01",
            "finish_time": "0:25:00", "city": "Omaha", "notes": "", "status": "Completed",
        }
        row.update(overrides)
        return row

    def test_new_user_has_no_rows_and_is_not_onboarded(self):
        self.assertEqual(storage.load_races("alice"), [])
        self.assertFalse(storage.has_any_rows("alice"))
        self.assertFalse(storage.is_onboarded("alice"))

    def test_save_single_race_round_trips(self):
        ids = storage.save_race("alice", self.sample_row())
        self.assertEqual(len(ids), 1)
        rows = storage.load_races("alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["race_name"], "Test Run")
        self.assertEqual(rows[0]["source"], storage.SOURCE_MANUAL)

    def test_save_race_batch(self):
        batch = [self.sample_row(race_name="Race A"), self.sample_row(race_name="Race B")]
        ids = storage.save_race("alice", batch)
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(storage.load_races("alice")), 2)

    def test_update_race(self):
        [race_id] = storage.save_race("alice", self.sample_row())
        storage.update_race("alice", race_id, self.sample_row(finish_time="0:20:00"))
        rows = storage.load_races("alice")
        self.assertEqual(rows[0]["finish_time"], "0:20:00")

    def test_delete_race(self):
        [race_id] = storage.save_race("alice", self.sample_row())
        storage.delete_race("alice", race_id)
        self.assertEqual(storage.load_races("alice"), [])

    def test_users_are_isolated(self):
        storage.save_race("alice", self.sample_row(race_name="Alice Race"))
        storage.save_race("bob", self.sample_row(race_name="Bob Race"))
        alice_rows = storage.load_races("alice")
        bob_rows = storage.load_races("bob")
        self.assertEqual(len(alice_rows), 1)
        self.assertEqual(len(bob_rows), 1)
        self.assertEqual(alice_rows[0]["race_name"], "Alice Race")
        self.assertEqual(bob_rows[0]["race_name"], "Bob Race")

    def test_identical_ids_across_users_do_not_collide(self):
        # Simulates two users whose uploaded CSVs happen to carry the same
        # surrogate id -- each user's row must stay independent.
        storage.save_race("alice", self.sample_row(id="shared-id", race_name="Alice Race"))
        storage.save_race("bob", self.sample_row(id="shared-id", race_name="Bob Race"))
        self.assertEqual(storage.load_races("alice")[0]["race_name"], "Alice Race")
        self.assertEqual(storage.load_races("bob")[0]["race_name"], "Bob Race")

    def test_delete_race_does_not_affect_other_users(self):
        [alice_id] = storage.save_race("alice", self.sample_row(id="shared-id"))
        storage.save_race("bob", self.sample_row(id="shared-id"))
        storage.delete_race("alice", alice_id)
        self.assertEqual(storage.load_races("alice"), [])
        self.assertEqual(len(storage.load_races("bob")), 1)

    def test_replace_all_races_wipes_then_inserts(self):
        storage.save_race("alice", self.sample_row(race_name="Old Race"))
        storage.replace_all_races("alice", [self.sample_row(race_name="New Race")])
        rows = storage.load_races("alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["race_name"], "New Race")
        self.assertEqual(rows[0]["source"], storage.SOURCE_CSV)

    def test_onboarding_flag_persists(self):
        self.assertFalse(storage.is_onboarded("alice"))
        storage.mark_onboarded("alice")
        self.assertTrue(storage.is_onboarded("alice"))


if __name__ == "__main__":
    unittest.main()
