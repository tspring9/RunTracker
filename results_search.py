"""Capped sweep orchestrator for the name-first "Sign Up" results search (SPR-15/17).

Two-stage pipeline, both stages paying into the same ``scope.max_calls`` cap
and reporting through the same optional ``progress_cb``:

1. :func:`candidate_result_sets` -- turn a :class:`SearchScope` (states +
   year range) into a list of candidate result sets, using
   ``runsignup_results.search_races_by_state_range`` to learn which races
   are in scope and ``runsignup_results.fetch_result_set_catalog`` to walk
   the shared public result-set catalog, filtered to those races. Both the
   race listing and the catalog rows are cached in SQLite (``storage.py``)
   so repeat searches (by this user or anyone else) get cheaper over time --
   the catalog in particular is walked forward from a single shared,
   continuously-advancing watermark (``storage.catalog_watermark()``), never
   re-read from the start.

2. :func:`sweep_for_runner` -- one ``get-results`` call per candidate, name
   filters only, re-confirmed locally with ``runner_matching.names_match``.

PRIVACY: nothing in this module accepts, logs, or persists a date of birth
or an email. ``sweep_for_runner`` takes no DOB and no age -- it returns the
raw RunSignUp ``age`` field (not a DOB) inside ``_raw`` for the *caller* to
classify with ``runner_matching.classify_confidence`` after any caching
layer, because ``@st.cache_data`` keys are inspectable.

CALL BUDGET: both stages count every HTTP call against ``scope.max_calls``
and stop -- never silently exceeding it -- as soon as the budget is spent,
setting ``stage="capped"``. ``sweep_for_runner`` hands back a ``cursor`` an
identical follow-up call can resume from via ``resume_cursor``;
``candidate_result_sets`` has no such parameter, so its own resumability
comes entirely from the SQLite cache making the next call cheaper, not from
an explicit cursor.

CONCURRENCY: at most 2 requests in flight at once (the contract's hard
limit), reusing ``runsignup_results``'s shared retrying session.

No background or scheduled crawling. Every call here is made inside a
synchronous function call that only runs when something (ultimately a
button press) calls it.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

import runner_matching
import runsignup_results as rsu
import storage

MAX_CONCURRENT_REQUESTS = 2


@dataclass
class SearchScope:
    states: tuple[str, ...]
    start_year: int
    end_year: int
    include_virtual: bool = False
    max_calls: int = 400  # board-approved default cap


@dataclass
class SearchProgress:
    calls_used: int
    calls_budget: int
    candidates_total: int
    candidates_checked: int
    stage: str  # "catalog" | "races" | "sweep" | "done" | "capped"
    cursor: str  # opaque resume token; pass back as resume_cursor
    capped: bool


def _emit(progress_cb, **kwargs) -> None:
    if progress_cb is not None:
        progress_cb(SearchProgress(**kwargs))


def _date_window(scope: SearchScope) -> tuple[str, str]:
    return f"{scope.start_year}-01-01", f"{scope.end_year}-12-31"


def _load_candidates(scope: SearchScope, race_map: dict[int, dict]) -> list[dict]:
    """Re-read the merged (cache + freshly discovered) scoped catalog rows
    from SQLite and enrich each with race-level city/url/race_type.

    Reading back through storage (rather than returning race_map/this
    call's catalog rows directly) is what lets a result set discovered in
    an *earlier* search -- one that did not re-run the races stage this
    time -- still come back as a candidate now.
    """
    rows = storage.load_result_set_catalog(scope.states, scope.start_year, scope.end_year)
    if not rows:
        return []

    missing_race_ids = {row["race_id"] for row in rows if row["race_id"] not in race_map}
    if missing_race_ids:
        for race in storage.load_race_catalog(missing_race_ids):
            race_map.setdefault(race["race_id"], race)

    candidates = []
    for row in rows:
        race = race_map.get(row["race_id"], {})
        candidates.append(
            {
                "race_id": row["race_id"],
                "event_id": row["event_id"],
                "result_set_id": row["result_set_id"],
                "race_name": row["race_name"],
                "event_date": row["event_date"],
                "state": row["state"],
                "city": race.get("city", ""),
                "url": race.get("url", ""),
                "race_type": rsu.race_type_from_event_name(row["race_name"]),
            }
        )
    return candidates


def candidate_result_sets(scope: SearchScope, progress_cb=None) -> list[dict]:
    """Scope -> candidate result sets.

    Each dict carries at least: race_id, event_id, result_set_id, race_name,
    event_date, state, city, url, race_type.

    Two simplifications, both necessary to avoid the very per-race/per-event
    discovery calls this feature exists to replace:

    - ``event_date`` is the in-scope race's earliest event date, not a
      precise per-event lookup (that would need a ``/race/{id}`` call per
      race, defeating the catalog's purpose). Wrong only for multi-day
      races with events on different dates.
    - ``include_virtual`` is applied to the *race* name
      (``runsignup_results.is_virtual_event``), since the catalog does not
      carry individual event names.
    """
    calls_used = 0
    start_date, end_date = _date_window(scope)
    race_map: dict[int, dict] = {}

    for state in scope.states:
        page = 1
        while True:
            if calls_used >= scope.max_calls:
                _emit(
                    progress_cb, calls_used=calls_used, calls_budget=scope.max_calls,
                    candidates_total=len(race_map), candidates_checked=0,
                    stage="capped", cursor="", capped=True,
                )
                return _load_candidates(scope, race_map)

            races = rsu.search_races_by_state_range(state, start_date, end_date, page=page)
            calls_used += 1

            for race in races:
                if not race["event_dates"]:
                    continue
                if not scope.include_virtual and rsu.is_virtual_event(race["name"]):
                    continue
                race_map[race["race_id"]] = race

            _emit(
                progress_cb, calls_used=calls_used, calls_budget=scope.max_calls,
                candidates_total=len(race_map), candidates_checked=0,
                stage="races", cursor="", capped=False,
            )

            if len(races) < 1000:
                break
            page += 1

    if race_map:
        storage.upsert_race_catalog(
            [
                {
                    "race_id": race_id,
                    "name": race["name"],
                    "city": race["city"],
                    "state": race["state"],
                    "url": race["url"],
                    "first_event_date": min(race["event_dates"]),
                    "last_event_date": max(race["event_dates"]),
                }
                for race_id, race in race_map.items()
            ]
        )

    # Walk the shared public result-set catalog forward from the stored
    # watermark, keeping only rows whose race_id is in race_map (this
    # search's requested scope). Everything else is looked at once to
    # advance the watermark and then discarded -- never persisted -- per
    # the RunSignUp API Developer Contract's ban on bulk extraction.
    #
    # ``page`` indexes into whatever ``modified_since_timestamp`` filter is
    # applied (verified live), so the filter must stay fixed for the whole
    # call -- shifting it while also incrementing ``page`` would silently
    # skip or repeat rows. catalog_watermark() is re-derived next time from
    # whatever got persisted, so there is nothing to update mid-call here.
    watermark = storage.catalog_watermark()
    page = 1
    found_result_sets = 0
    hit_cap = False
    while calls_used < scope.max_calls:
        rows = rsu.fetch_result_set_catalog(
            modified_since_timestamp=watermark, page=page, results_per_page=rsu.RESULT_SET_CATALOG_PAGE_SIZE
        )
        calls_used += 1

        if not rows:
            break

        in_scope = [row for row in rows if row["race_id"] in race_map]
        if in_scope:
            found_result_sets += len(in_scope)
            storage.upsert_result_set_catalog(
                [
                    {
                        **row,
                        "event_date": min(race_map[row["race_id"]]["event_dates"]),
                        "state": race_map[row["race_id"]]["state"],
                    }
                    for row in in_scope
                ]
            )

        _emit(
            progress_cb, calls_used=calls_used, calls_budget=scope.max_calls,
            candidates_total=found_result_sets, candidates_checked=0,
            stage="catalog", cursor="", capped=False,
        )

        if len(rows) < rsu.RESULT_SET_CATALOG_PAGE_SIZE:
            break
        page += 1
    else:
        hit_cap = True

    _emit(
        progress_cb, calls_used=calls_used, calls_budget=scope.max_calls,
        candidates_total=found_result_sets, candidates_checked=0,
        stage="capped" if hit_cap else "done", cursor="", capped=hit_cap,
    )
    return _load_candidates(scope, race_map)


def _parse_cursor(resume_cursor: str) -> int:
    if not resume_cursor:
        return 0
    try:
        return max(0, int(resume_cursor))
    except (TypeError, ValueError):
        return 0


def match_identity(row: dict) -> tuple:
    """Identity of a single *finish*, independent of which result set published it.

    Candidates are ``(race_id, event_id, result_set_id)`` triples, but a
    single event is routinely published as several result sets -- Overall,
    Age Group, gender splits -- and every finisher appears in all of them.
    Measured live on 2026-10-09: of 1,913 distinct events in a catalog
    sample, 70 (3.7%) carried more than one result set, up to 4 on one
    event, and each runner was present in *every* set (race 6606 / event
    16127, sets 1313/1317/1334/1335). Without collapsing them, one race of
    yours becomes up to four identical rows, each with its own "This is me"
    button -- which would add the same race to the tracker four times.

    ``bib`` is the discriminator when published, since it is unique within
    an event. Falling back to name + age + finish time keeps two genuine
    same-name runners in one event apart (they differ on at least one),
    rather than silently dropping one of them.
    """
    raw = row.get("_raw") or {}
    context = row.get("_context") or {}

    bib = str(raw.get("bib", "") or "").strip()
    if bib:
        identity = f"bib:{bib}"
    else:
        identity = "|".join(
            (
                runner_matching.normalize_name(raw.get("first_name", "")),
                runner_matching.normalize_name(raw.get("last_name", "")),
                str(raw.get("age", "") or "").strip(),
                str(row.get("finish_time", "") or "").strip(),
            )
        )
    return (context.get("race_id"), context.get("event_id"), identity)


def dedupe_matches(matches: list[dict]) -> list[dict]:
    """Collapse rows that are the same finish published in several result sets.

    Order-stable -- the first occurrence keeps its position, which matters
    because the caller accumulates matches across resumed sweeps and the
    list order is what the user reads. The one exception: a row carrying a
    *usable* age replaces an incumbent without one, because age is the only
    signal that can move a row out of "Possible" and into High/Rejected, so
    preferring the informative duplicate strictly improves classification.
    """
    best: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in matches:
        key = match_identity(row)
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = row
            order.append(key)
            continue

        incumbent_age = runner_matching.parse_result_age((incumbent.get("_raw") or {}).get("age"))
        candidate_age = runner_matching.parse_result_age((row.get("_raw") or {}).get("age"))
        if incumbent_age is None and candidate_age is not None:
            best[key] = row

    return [best[key] for key in order]


def sweep_for_runner(
    first_name: str,
    last_name: str,
    candidates: list[dict],
    scope: SearchScope,
    progress_cb=None,
    resume_cursor: str = "",
) -> tuple[list[dict], SearchProgress]:
    """One ``get-results`` call per candidate, name filters only.

    Takes no DOB and no age. Age classification happens in the caller, not
    here -- see the module docstring's PRIVACY note.
    """
    if not first_name or not last_name:
        raise ValueError("Both first and last name are required.")

    total = len(candidates)
    index = min(_parse_cursor(resume_cursor), total)
    matches: list[dict] = []
    calls_used = 0
    capped = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        while index < total:
            remaining_budget = scope.max_calls - calls_used
            if remaining_budget <= 0:
                capped = True
                break

            batch_size = min(MAX_CONCURRENT_REQUESTS, remaining_budget, total - index)
            batch = candidates[index : index + batch_size]

            futures = {
                executor.submit(
                    rsu.fetch_results,
                    candidate["race_id"],
                    candidate["event_id"],
                    candidate["result_set_id"],
                    first_name=first_name,
                    last_name=last_name,
                    results_per_page=100,
                    max_pages=1,
                ): candidate
                for candidate in batch
            }

            for future in concurrent.futures.as_completed(futures):
                candidate = futures[future]
                calls_used += 1
                for raw in future.result():
                    if runner_matching.names_match(
                        first_name, last_name, raw.get("first_name", ""), raw.get("last_name", "")
                    ):
                        row = rsu.result_to_tracker_row(raw, candidate)
                        row["_raw"] = raw
                        row["_context"] = candidate
                        matches.append(row)

            index += len(batch)
            _emit(
                progress_cb, calls_used=calls_used, calls_budget=scope.max_calls,
                candidates_total=total, candidates_checked=index,
                stage="sweep", cursor="", capped=False,
            )

    stage = "capped" if capped else "done"
    cursor = str(index) if capped else ""
    progress = SearchProgress(
        calls_used=calls_used,
        calls_budget=scope.max_calls,
        candidates_total=total,
        candidates_checked=index,
        stage=stage,
        cursor=cursor,
        capped=capped,
    )
    _emit(
        progress_cb, calls_used=progress.calls_used, calls_budget=progress.calls_budget,
        candidates_total=progress.candidates_total, candidates_checked=progress.candidates_checked,
        stage=progress.stage, cursor=progress.cursor, capped=progress.capped,
    )
    return matches, progress
