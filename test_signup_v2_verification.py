"""Independent acceptance tests for the SPR-26 signup-search v2 contract.

These tests intentionally exercise public contracts rather than mirror the
implementation.  All identities are synthetic aliases.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import ast
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

import app
import results_search as search
import runner_matching as matching
import runsignup_results as rsu


@pytest.mark.parametrize(
    "query_last,result_last",
    [
        ("O'Brien", "OBrien"),
        ("O'Brien", "O Brien"),
        ("Smith-Jones", "Smith Jones"),
        ("Smith-Jones", "SmithJones"),
        ("Mc Donald", "McDonald"),
        ("Núñez", "Nunez"),
        ("Jørgensen", "Jorgensen"),
    ],
)
def test_equivalent_surname_spellings_survive_the_name_gate(query_last, result_last):
    assert matching.name_agreement("Runner", query_last, "Runner", result_last) != "none"


@pytest.mark.parametrize(
    "query_last,result_last",
    [("O'Brien", "O'Briens"), ("Smith-Jones", "Smith")],
)
def test_distinct_surnames_remain_separate(query_last, result_last):
    assert matching.name_agreement("Runner", query_last, "Runner", result_last) == "none"


def test_name_fields_are_not_reconfirmed_with_bare_case_folding():
    """All application-level name comparisons use runner_matching's contract."""
    violations = []
    for path in Path(".").glob("*.py"):
        if path.name == "runner_matching.py" or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for comparison in (node for node in ast.walk(tree) if isinstance(node, ast.Compare)):
            expressions = [comparison.left, *comparison.comparators]
            folded_name_fields = []
            for expression in expressions:
                if not (
                    isinstance(expression, ast.Call)
                    and isinstance(expression.func, ast.Attribute)
                    and expression.func.attr in {"lower", "casefold"}
                ):
                    continue
                rendered = ast.unparse(expression.func.value).lower()
                if "first_name" in rendered or "last_name" in rendered:
                    folded_name_fields.append(rendered)
            if len(folded_name_fields) >= 2:
                violations.append((path.name, comparison.lineno, folded_name_fields))

    assert violations == []


@pytest.mark.parametrize(
    "case,dob,name_signal,event_date,raw_age,expected_bucket,expected_age_signal",
    [
        ("no name agreement", date(1981, 3, 7), "none", date(2014, 3, 7), 33, None, None),
        ("race before DOB", date(2020, 1, 1), "exact", date(2014, 3, 7), 33, matching.IMPOSSIBLE, None),
        ("exact and corroborated", date(1990, 1, 1), "exact", date(2020, 1, 1), 30, matching.CERTAIN, "corroborated"),
        ("exact and unknown age", date(1990, 1, 1), "exact", date(2020, 1, 1), 952, matching.MAYBE, "unknown"),
        ("exact and contradicted", date(1990, 1, 1), "exact", date(2020, 1, 1), 28, matching.RULED_OUT, "contradicted"),
        ("nickname and corroborated", date(1990, 1, 1), "nickname", date(2020, 1, 1), 30, matching.MAYBE, "corroborated"),
        ("initial and unknown age", date(1990, 1, 1), "initial", date(2020, 1, 1), None, matching.MAYBE, "unknown"),
        ("nickname and contradicted", date(1990, 1, 1), "nickname", date(2020, 1, 1), 28, matching.RULED_OUT, "contradicted"),
        ("unknown race date", date(1990, 1, 1), "exact", None, 30, matching.MAYBE, "no-race-date"),
    ],
)
def test_plan_decision_table_cell_by_cell(
    case, dob, name_signal, event_date, raw_age, expected_bucket, expected_age_signal
):
    # The first row is the name gate: classify_match must not manufacture a
    # confirmable verdict for something name_agreement already discarded.
    if name_signal == "none":
        assert matching.name_agreement("Runner", "A", "Runner", "B") == "none"
        return

    verdict = matching.classify_match(dob, event_date, raw_age, name_signal)
    assert verdict.bucket == expected_bucket, case
    if expected_age_signal is not None:
        assert verdict.age_signal == expected_age_signal, case


def test_unknown_age_and_unknown_race_date_have_truthful_distinct_reasons():
    unknown_age = matching.classify_match(
        date(1990, 1, 1), date(2020, 1, 1), 952, "exact"
    )
    unknown_date = matching.classify_match(date(1990, 1, 1), None, 30, "exact")

    assert unknown_age.reason != unknown_date.reason
    assert "age" in unknown_age.reason.lower()
    assert "when" in unknown_date.reason.lower()


def test_call_budget_is_atomic_under_the_two_request_sweep_concurrency():
    budget = search.CallBudget(limit=1)

    class YieldingZero:
        """Force both workers between CallBudget's read and write."""

        def __init__(self):
            self.barrier = Barrier(2)

        def __add__(self, amount):
            self.barrier.wait(timeout=2)
            return amount

    budget.used = YieldingZero()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: budget.spend(), range(2)))

    assert outcomes.count(True) == 1
    assert budget.used == budget.limit


def test_qa_controlled_cap_shape_cannot_recreate_401_calls(monkeypatch):
    """Reuse QA's 1 + 399 + 1 boundary counters without live calls.

    Interactive discovery must now be SQLite-only, so the first two counters
    remain zero; only the supplied result candidate can spend one HTTP call.
    """
    boundary_calls = {"races": 0, "catalog": 0, "results": 0}

    def forbidden_races(*args, **kwargs):
        boundary_calls["races"] += 1
        raise AssertionError("interactive search called /races")

    def forbidden_catalog(*args, **kwargs):
        boundary_calls["catalog"] += 1
        raise AssertionError("interactive search walked the catalog")

    def runner_a_result(*args, **kwargs):
        boundary_calls["results"] += 1
        return []

    monkeypatch.setattr(search.rsu, "search_races_by_state_range", forbidden_races)
    monkeypatch.setattr(search.rsu, "fetch_result_set_catalog", forbidden_catalog)
    monkeypatch.setattr(search.rsu, "fetch_results", runner_a_result)
    scope = search.SearchScope(states=("MO",), start_year=2014, end_year=2014, max_calls=400)
    qa_candidate = [{
        "race_id": 6606, "event_id": 16127, "result_set_id": 1313,
        "race_name": "QA controlled race", "event_date": "2014-03-07",
        "state": "MO", "city": "", "url": "", "race_type": "5K",
    }]

    _, coverage = search.sweep_for_runner("Runner", "A", qa_candidate, scope)

    assert boundary_calls == {"races": 0, "catalog": 0, "results": 1}
    assert coverage.budget_used == 1 <= coverage.budget_limit
    assert coverage.complete is True


def test_capped_sweep_can_never_claim_complete(monkeypatch):
    monkeypatch.setattr(search.rsu, "fetch_results", lambda *args, **kwargs: [])
    candidates = [
        {
            "race_id": 1, "event_id": event_id, "result_set_id": event_id,
            "race_name": f"Race {event_id}", "event_date": "2020-01-01",
            "state": "MO", "city": "", "url": "", "race_type": "5K",
        }
        for event_id in (1, 2)
    ]
    _, coverage = search.sweep_for_runner(
        "Runner", "A", candidates,
        search.SearchScope(states=("MO",), start_year=2020, end_year=2020, max_calls=1),
    )

    assert coverage.result_sets_checked < coverage.result_sets_known
    assert coverage.complete is False


def test_index_coverage_complete_obeys_checked_equals_known(monkeypatch):
    """Coverage has one meaning in the plan, including from build_index."""
    race = {
        "race_id": 6606, "name": "QA controlled race", "city": "",
        "state": "MO", "url": "", "event_dates": ["2014-03-07"],
    }
    result_set = {
        "race_id": 6606, "event_id": 16127, "result_set_id": 1313,
        "race_name": "QA controlled race", "event_date": "2014-03-07",
        "state": "MO", "last_modified_ts": 1,
        "refreshed_at": "2026-10-09T00:00:00+00:00",
    }

    def fake_window(state, start_date, end_date, budget):
        assert budget.spend()
        return [race]

    catalog_pages = iter([[result_set], []])
    monkeypatch.setattr(search.rsu, "search_races_in_window", fake_window)
    monkeypatch.setattr(search.rsu, "fetch_result_set_catalog", lambda **kwargs: next(catalog_pages))
    monkeypatch.setattr(search.storage, "catalog_watermark", lambda: 0)
    monkeypatch.setattr(search.storage, "upsert_race_catalog", lambda rows: None)
    monkeypatch.setattr(search.storage, "upsert_result_set_catalog", lambda rows: None)
    monkeypatch.setattr(search.storage, "load_result_set_catalog", lambda *args: [result_set])

    coverage = search.build_index(("MO",), 2014, 2014, search.CallBudget(limit=10))

    assert coverage.complete is (coverage.result_sets_checked == coverage.result_sets_known)


def test_race_window_transport_never_asks_for_page_two(monkeypatch):
    pages = []

    def full_then_empty(state, start_date, end_date, page=1, results_per_page=1000):
        pages.append(page)
        if len(pages) == 1:
            return [
                {"race_id": i, "name": "Race", "city": "", "state": state,
                 "url": "", "event_dates": [start_date]}
                for i in range(1000)
            ]
        return []

    monkeypatch.setattr(rsu, "search_races_by_state_range", full_then_empty)
    rsu.search_races_in_window(
        "MO", "2020-01-01", "2020-12-31", search.CallBudget(limit=3)
    )
    assert pages and set(pages) == {1}


@pytest.mark.parametrize(
    "status,payload",
    [
        (200, ValueError("not json")),
        (414, ValueError("URI too long")),
        (200, {"error": {"error_code": 13, "error_msg": "You must be logged in"}}),
    ],
)
def test_raw_upstream_body_never_escapes(monkeypatch, status, payload):
    response = Mock(status_code=status, text="<html>nginx private upstream body</html>")
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    monkeypatch.setattr(rsu._SESSION, "get", Mock(return_value=response))

    with pytest.raises(rsu.RunSignUpError) as exc:
        rsu._get("/races", first_name="Runner")
    message = str(exc.value).lower()
    assert "<html>" not in message
    assert "nginx" not in message
    assert "logged in" not in message


# ---------------------------------------------------------------------------
# SPR-49 -- an unbuilt index (known == 0) is its own terminal UI state, not
# a degenerate "searched everything, found nothing" (which reads as
# "we checked all 0 known results" -- a search that never happened).
# ---------------------------------------------------------------------------
FORBIDDEN_PHRASES = ("no results", "none match", "all 0")


def _contains_forbidden_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in FORBIDDEN_PHRASES)


def test_unbuilt_index_message_names_the_way_out_without_forbidden_wording():
    message = app.describe_signup_unbuilt_index_state("MO", 2014, 2026)

    assert not _contains_forbidden_phrase(message)
    assert "results_search.build_index" in message


@pytest.mark.parametrize("complete,capped", [(True, False), (True, True), (False, True), (False, False)])
def test_unbuilt_index_state_is_chosen_on_known_zero_regardless_of_complete_or_capped(complete, capped):
    """signup_done_state only ever looks at known -- complete/capped are not
    even parameters -- so the empty-index branch is structurally unreachable
    to anything but known == 0, matching the SPR-48 Coverage.complete ruling
    that known == 0 is always `complete` on its own.
    """
    del complete, capped  # not inputs to signup_done_state; that's the point
    assert app.signup_done_state(known=0) == app.SIGNUP_STATE_UNBUILT_INDEX


@pytest.mark.parametrize("known", [1, 2, 400])
def test_no_match_state_is_chosen_whenever_known_is_nonzero(known):
    assert app.signup_done_state(known=known) == app.SIGNUP_STATE_NO_MATCH


def test_no_match_message_unaffected_by_spr_49_and_never_says_all_0():
    # A real no-match result legitimately says "none match" (that's the
    # accurate outcome of a completed search) -- SPR-49 only forbids "all 0"
    # and the unbuilt-index wording, not the §4 no-match state's own text.
    message = app.describe_signup_no_match_state(known=3, capped=False, state_text="MO", start_year=2014, end_year=2026)

    assert "all 0" not in message.lower()
    assert "3 known result" in message


def test_no_streamlit_cache_data_function_accepts_dob_or_email():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    forbidden = {"dob", "date_of_birth", "birth_date", "email"}
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
        if not any("cache_data" in decorator for decorator in decorators):
            continue
        parameters = {argument.arg.lower() for argument in node.args.args}
        if parameters & forbidden:
            violations.append((node.name, sorted(parameters & forbidden)))

    assert violations == []
