"""Independent contract tests for the capped RunSignUp results sweep."""

from threading import Lock
import ast
import inspect
import re
import time

import results_search as search


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
