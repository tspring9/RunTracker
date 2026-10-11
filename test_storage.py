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


class ResultSetCatalogTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["RUNTRACKER_DB_PATH"] = self.db_path
        storage.reset_backend_cache()

    def tearDown(self):
        storage.reset_backend_cache()
        os.environ.pop("RUNTRACKER_DB_PATH", None)
        os.remove(self.db_path)

    def sample_result_set(self, **overrides):
        row = {
            "race_id": 85066, "event_id": 1030728, "result_set_id": 644802,
            "race_name": "Hospital Hill Run", "event_date": "2026-05-16",
            "state": "mo", "last_modified_ts": 100,
        }
        row.update(overrides)
        return row

    def test_catalog_starts_empty(self):
        self.assertEqual(storage.load_result_set_catalog(["MO"], 2026, 2026), [])
        self.assertIsNone(storage.catalog_watermark())

    def test_upsert_and_load_scoped_by_state_and_year(self):
        storage.upsert_result_set_catalog(self.sample_result_set())
        storage.upsert_result_set_catalog(
            self.sample_result_set(race_id=1, event_id=2, result_set_id=3, state="ks", event_date="2026-04-11")
        )

        mo_rows = storage.load_result_set_catalog(["MO"], 2026, 2026)
        self.assertEqual(len(mo_rows), 1)
        self.assertEqual(mo_rows[0]["race_id"], 85066)
        # state is normalized to upper-case on write.
        self.assertEqual(mo_rows[0]["state"], "MO")

        both_states = storage.load_result_set_catalog(["MO", "KS"], 2026, 2026)
        self.assertEqual(len(both_states), 2)

        wrong_year = storage.load_result_set_catalog(["MO"], 2020, 2024)
        self.assertEqual(wrong_year, [])

    def test_load_with_no_states_returns_empty_without_querying(self):
        storage.upsert_result_set_catalog(self.sample_result_set())
        self.assertEqual(storage.load_result_set_catalog([], 2026, 2026), [])

    def test_upsert_is_idempotent_on_the_same_identity(self):
        storage.upsert_result_set_catalog(self.sample_result_set(last_modified_ts=100))
        storage.upsert_result_set_catalog(self.sample_result_set(last_modified_ts=200))

        rows = storage.load_result_set_catalog(["MO"], 2026, 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_modified_ts"], 200)

    def test_catalog_watermark_is_the_max_last_modified_ts(self):
        storage.upsert_result_set_catalog(
            [
                self.sample_result_set(result_set_id=1, last_modified_ts=100),
                self.sample_result_set(result_set_id=2, last_modified_ts=500),
            ]
        )
        self.assertEqual(storage.catalog_watermark(), 500)

    def test_upsert_race_catalog_round_trips(self):
        storage.upsert_race_catalog(
            {
                "race_id": 85066, "name": "Hospital Hill Run", "city": "Kansas City",
                "state": "mo", "url": "https://runsignup.com/Race/85066",
                "first_event_date": "2026-05-16", "last_event_date": "2026-05-16",
            }
        )
        with storage.get_backend()._connect() as conn:
            row = conn.execute("SELECT * FROM race_catalog WHERE race_id = ?", (85066,)).fetchone()
        self.assertEqual(row["state"], "MO")
        self.assertEqual(row["city"], "Kansas City")

    def test_clear_catalog_wipes_both_tables(self):
        storage.upsert_race_catalog({"race_id": 1, "name": "Race"})
        storage.upsert_result_set_catalog(self.sample_result_set())

        storage.clear_catalog()

        self.assertEqual(storage.load_result_set_catalog(["MO"], 2026, 2026), [])
        with storage.get_backend()._connect() as conn:
            self.assertIsNone(conn.execute("SELECT * FROM race_catalog").fetchone())

    def test_catalog_has_no_user_id_column(self):
        storage.upsert_result_set_catalog(self.sample_result_set())
        with storage.get_backend()._connect() as conn:
            columns = {col[1] for col in conn.execute("PRAGMA table_info(result_set_catalog)")}
            race_columns = {col[1] for col in conn.execute("PRAGMA table_info(race_catalog)")}
        self.assertNotIn("user_id", columns)
        self.assertNotIn("user_id", race_columns)


if __name__ == "__main__":
    unittest.main()
