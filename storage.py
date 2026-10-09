"""Persistence layer for RunTracker race data.

Why this module exists: previously every race entry lived only in
``st.session_state``, so a page refresh (or a second visitor) lost or never
saw a user's changes. This module gives ``app.py`` a narrow, backend-agnostic
interface -- ``load_races`` / ``save_race`` / ``update_race`` / ``delete_race``
-- so call sites stay thin and do not care which database is behind them.

Backend: SQLite (stdlib ``sqlite3``, no new infra) is the default, wrapped
behind the ``Backend`` interface below. Streamlit Community Cloud's
filesystem is ephemeral, so a local ``.db`` file is wiped on every
redeploy/restart -- that is a real limitation, not a bug. Two ways out,
both supported by this module's shape:

  1. Point ``RUNTRACKER_DB_PATH`` (a Streamlit secret or env var) at a
     mounted/persistent volume once one is available.
  2. Add a new class that satisfies the ``Backend`` interface (e.g. a
     ``PostgresBackend`` for Supabase/Neon's free tier) and switch
     ``get_backend()`` to construct it. No call site in app.py changes --
     that is the entire point of routing everything through this module
     instead of talking to sqlite3 directly from app.py.

Auth seam: every method takes a ``user_id`` string. There is no login system
yet (multi-user auth is explicitly out of scope for SPR-9). app.py resolves
``user_id`` today from a secret/query param/single-tenant default (see
``resolve_user_id`` in app.py). When real auth lands, only that resolver
needs to change -- nothing here does, since this module never knows or
cares how a user_id was decided.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Iterable

REQUIRED_COLUMNS = [
    "state", "state_name", "runner_name", "race_type", "race_name",
    "race_date", "finish_time", "city", "notes", "status",
]

# Distinguishes how a row entered the system. API-imported rows (RunSignUp
# results/future races) must stay distinguishable from rows a person typed in.
SOURCE_MANUAL = "manual"
SOURCE_SAMPLE = "sample"
SOURCE_API = "api"
SOURCE_CSV = "csv"

DEFAULT_DB_PATH = "runtracker.db"


def get_db_path() -> str:
    """DB file path, overridable via the RUNTRACKER_DB_PATH secret/env var.

    See the module docstring: the default relative path is fine for local
    dev but is NOT durable on Streamlit Community Cloud's ephemeral
    filesystem. Set RUNTRACKER_DB_PATH to a mounted volume path for real
    deployments until/unless a hosted Postgres backend is wired in instead.
    """
    try:
        import streamlit as st

        if "RUNTRACKER_DB_PATH" in st.secrets:
            return str(st.secrets["RUNTRACKER_DB_PATH"])
    except Exception:
        pass
    return os.getenv("RUNTRACKER_DB_PATH", DEFAULT_DB_PATH)


def _coerce_rows(rows) -> list[dict]:
    if isinstance(rows, dict):
        return [rows]
    return list(rows)


class Backend:
    """Storage interface. Implement this to add a new backend."""

    def load_races(self, user_id: str) -> list[dict]:
        raise NotImplementedError

    def save_race(self, user_id: str, rows) -> list[str]:
        """Insert one race (dict) or a batch (iterable of dicts).

        Accepting a batch is what lets a RunSignUp import write many result
        rows in one call instead of one round-trip per row.
        """
        raise NotImplementedError

    def update_race(self, user_id: str, race_id: str, row: dict) -> None:
        raise NotImplementedError

    def delete_race(self, user_id: str, race_id: str) -> None:
        raise NotImplementedError

    def delete_all(self, user_id: str) -> None:
        raise NotImplementedError

    def has_any_rows(self, user_id: str) -> bool:
        raise NotImplementedError

    def is_onboarded(self, user_id: str) -> bool:
        raise NotImplementedError

    def mark_onboarded(self, user_id: str) -> None:
        raise NotImplementedError


class SQLiteBackend(Backend):
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS races (
                    id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '',
                    state_name TEXT NOT NULL DEFAULT '',
                    runner_name TEXT NOT NULL DEFAULT '',
                    race_type TEXT NOT NULL DEFAULT '',
                    race_name TEXT NOT NULL DEFAULT '',
                    race_date TEXT NOT NULL DEFAULT '',
                    finish_time TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Blank',
                    source TEXT NOT NULL DEFAULT 'manual',
                    PRIMARY KEY (user_id, id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_races_user ON races(user_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_meta (
                    user_id TEXT PRIMARY KEY,
                    onboarded INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def load_races(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM races WHERE user_id = ? ORDER BY rowid", (user_id,)
            )
            return [dict(row) for row in cur.fetchall()]

    def save_race(self, user_id: str, rows) -> list[str]:
        rows = _coerce_rows(rows)
        ids = []
        with self._connect() as conn:
            for row in rows:
                race_id = str(row.get("id") or uuid.uuid4())
                ids.append(race_id)
                conn.execute(
                    """
                    INSERT INTO races (
                        id, user_id, state, state_name, runner_name, race_type,
                        race_name, race_date, finish_time, city, notes, status, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, id) DO UPDATE SET
                        state=excluded.state, state_name=excluded.state_name,
                        runner_name=excluded.runner_name, race_type=excluded.race_type,
                        race_name=excluded.race_name, race_date=excluded.race_date,
                        finish_time=excluded.finish_time, city=excluded.city,
                        notes=excluded.notes, status=excluded.status, source=excluded.source
                    """,
                    (
                        race_id,
                        user_id,
                        row.get("state", "") or "",
                        row.get("state_name", "") or "",
                        row.get("runner_name", "") or "",
                        row.get("race_type", "") or "",
                        row.get("race_name", "") or "",
                        row.get("race_date", "") or "",
                        row.get("finish_time", "") or "",
                        row.get("city", "") or "",
                        row.get("notes", "") or "",
                        row.get("status", "Blank") or "Blank",
                        row.get("source", SOURCE_MANUAL) or SOURCE_MANUAL,
                    ),
                )
        return ids

    def update_race(self, user_id: str, race_id: str, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE races SET
                    state=?, state_name=?, runner_name=?, race_type=?, race_name=?,
                    race_date=?, finish_time=?, city=?, notes=?, status=?
                WHERE id = ? AND user_id = ?
                """,
                (
                    row.get("state", "") or "",
                    row.get("state_name", "") or "",
                    row.get("runner_name", "") or "",
                    row.get("race_type", "") or "",
                    row.get("race_name", "") or "",
                    row.get("race_date", "") or "",
                    row.get("finish_time", "") or "",
                    row.get("city", "") or "",
                    row.get("notes", "") or "",
                    row.get("status", "Blank") or "Blank",
                    str(race_id),
                    user_id,
                ),
            )

    def delete_race(self, user_id: str, race_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM races WHERE id = ? AND user_id = ?", (str(race_id), user_id)
            )

    def delete_all(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM races WHERE user_id = ?", (user_id,))

    def has_any_rows(self, user_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM races WHERE user_id = ? LIMIT 1", (user_id,))
            return cur.fetchone() is not None

    def is_onboarded(self, user_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT onboarded FROM user_meta WHERE user_id = ?", (user_id,)
            )
            row = cur.fetchone()
            return bool(row and row["onboarded"])

    def mark_onboarded(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_meta (user_id, onboarded) VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET onboarded = 1
                """,
                (user_id,),
            )


_backend: Backend | None = None


def get_backend() -> Backend:
    """Factory seam: swap backends here without touching any call site."""
    global _backend
    if _backend is None:
        _backend = SQLiteBackend()
    return _backend


def reset_backend_cache() -> None:
    """Test hook -- forces get_backend() to construct a fresh instance."""
    global _backend
    _backend = None


# -------------------------------------------------
# Module-level convenience wrappers -- this is the surface app.py calls.
# -------------------------------------------------
def load_races(user_id: str) -> list[dict]:
    return get_backend().load_races(user_id)


def save_race(user_id: str, rows) -> list[str]:
    return get_backend().save_race(user_id, rows)


def update_race(user_id: str, race_id: str, row: dict) -> None:
    get_backend().update_race(user_id, race_id, row)


def delete_race(user_id: str, race_id: str) -> None:
    get_backend().delete_race(user_id, race_id)


def replace_all_races(user_id: str, rows: Iterable[dict], source: str = SOURCE_CSV) -> list[str]:
    """Wipe a user's races and batch-insert ``rows``. Used by CSV replace."""
    rows = _coerce_rows(rows)
    for row in rows:
        if not row.get("source"):
            row["source"] = source
    backend = get_backend()
    backend.delete_all(user_id)
    return backend.save_race(user_id, rows)


def has_any_rows(user_id: str) -> bool:
    return get_backend().has_any_rows(user_id)


def is_onboarded(user_id: str) -> bool:
    return get_backend().is_onboarded(user_id)


def mark_onboarded(user_id: str) -> None:
    get_backend().mark_onboarded(user_id)
