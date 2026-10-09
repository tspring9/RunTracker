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


# ---------------------------------------------------------------------------
# De-duplication across result sets (SPR-15 follow-up).
#
# Pinned to a real measurement: RunSignUp race 6606 / event 16127 publishes
# four result sets (1313, 1317, 1334, 1335) and every finisher appears in all
# four. Candidates are (race_id, event_id, result_set_id) triples, so without
# collapsing, one race shows up four times -- each with its own "This is me"
# button, i.e. four tracker entries for one finish.
# ---------------------------------------------------------------------------


def _match(result_set_id, bib="101", age=40, first="Justin", last="Spring", finish="22:15"):
    return {
        "race_name": "Real Race",
        "finish_time": finish,
        "_raw": {"first_name": first, "last_name": last, "age": age, "bib": bib},
        "_context": {"race_id": 6606, "event_id": 16127, "result_set_id": result_set_id},
    }


def test_same_finish_in_four_result_sets_collapses_to_one_row():
    rows = [_match(sid) for sid in (1313, 1317, 1334, 1335)]

    deduped = search.dedupe_matches(rows)

    assert len(deduped) == 1


def test_dedupe_keys_on_bib_within_an_event_not_on_result_set():
    # Same event, same name, different bib -> two different people, both kept.
    rows = [_match(1313, bib="101"), _match(1317, bib="202")]

    assert len(search.dedupe_matches(rows)) == 2


def test_dedupe_does_not_merge_across_different_events():
    first = _match(1313)
    second = _match(1313)
    second["_context"] = {"race_id": 6606, "event_id": 99999, "result_set_id": 1313}

    assert len(search.dedupe_matches([first, second])) == 2


def test_dedupe_without_bib_keeps_two_same_name_runners_with_different_times():
    rows = [
        _match(1313, bib="", finish="22:15"),
        _match(1317, bib="", finish="31:48"),
    ]

    assert len(search.dedupe_matches(rows)) == 2


def test_dedupe_without_bib_collapses_identical_unbibbed_finishes():
    rows = [_match(1313, bib=""), _match(1317, bib="")]

    assert len(search.dedupe_matches(rows)) == 1


def test_dedupe_prefers_the_duplicate_that_carries_a_usable_age():
    # Age is the only signal that can lift a row out of "Possible", so the
    # informative duplicate must win regardless of arrival order.
    without_age = _match(1313, age="")
    with_age = _match(1317, age=40)

    assert search.dedupe_matches([without_age, with_age])[0]["_raw"]["age"] == 40
    assert search.dedupe_matches([with_age, without_age])[0]["_raw"]["age"] == 40


def test_dedupe_treats_junk_age_as_unusable_when_choosing_a_winner():
    # age=952 is a real published value; it must not beat a usable age.
    junk = _match(1313, age=952)
    usable = _match(1317, age=40)

    assert search.dedupe_matches([junk, usable])[0]["_raw"]["age"] == 40


def test_dedupe_is_order_stable_and_handles_empty_input():
    assert search.dedupe_matches([]) == []

    a = _match(1313, bib="1")
    b = _match(1313, bib="2")
    c = _match(1313, bib="3")
    order = [row["_raw"]["bib"] for row in search.dedupe_matches([a, b, c, a, b])]

    assert order == ["1", "2", "3"]


def test_dedupe_tolerates_rows_missing_raw_and_context():
    rows = [{}, {"_raw": None, "_context": None}]

    assert len(search.dedupe_matches(rows)) == 1


def test_match_identity_ignores_result_set_id():
    assert search.match_identity(_match(1313)) == search.match_identity(_match(9999))
