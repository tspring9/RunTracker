"""Results-search orchestrator for the name-first "Sign Up" search (SPR-15/17/30).

SPR-30 moves discovery offline (SPR-26 section 4): walking ``/races`` and the
public result-set catalog no longer happens inside a user's search. Instead:

1. :func:`build_index` -- **CLI only**, never reachable from a page load.
   Walks ``runsignup_results.search_races_in_window`` (adaptive date-window
   splitting, never ``/races`` page >= 2 -- SPR-36) and the public
   result-set catalog, writing both into the existing SQLite tables
   (``storage.py``). A human runs this when the index needs refreshing.

2. :func:`indexed_candidates` -- SQLite read only, **zero HTTP**. Turns a
   :class:`SearchScope` into one candidate per ``(race_id, event_id)``,
   already collapsed across duplicate result sets, ranked newest-first.

3. :func:`sweep_for_runner` -- one ``get-results`` call per candidate, name
   filters only, re-confirmed locally with ``runner_matching.names_match``.
   This is the only HTTP a user's search makes; its entire budget is
   ``scope.max_calls`` (SPR-41: previously each stage got its own budget and
   together they could exceed the advertised cap).

:func:`candidate_result_sets` (the pre-SPR-30 online discovery path) stays
for callers not yet migrated onto ``indexed_candidates`` / ``build_index``,
but is no longer called by anything in this module.

PRIVACY: nothing in this module accepts, logs, or persists a date of birth
or an email. ``sweep_for_runner`` takes no DOB and no age -- it returns the
raw RunSignUp ``age`` field (not a DOB) inside ``_raw`` for the *caller* to
classify with ``runner_matching.classify_match`` after any caching layer,
because ``@st.cache_data`` keys are inspectable.

CALL BUDGET: a single :class:`CallBudget`, sized from ``scope.max_calls``,
is spent by every HTTP call a stage makes and can never be exceeded.

CONCURRENCY: at most 2 requests in flight at once (the contract's hard
limit), reusing ``runsignup_results``'s shared retrying session.

No background or scheduled crawling. ``build_index`` only runs when a human
invokes the CLI (see ``__main__`` below); everything else only runs inside a
synchronous function call triggered by something (ultimately a button
press).
"""

from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import runner_matching
import runsignup_results as rsu
import storage

MAX_CONCURRENT_REQUESTS = 2


@dataclass
class CallBudget:
    """One counter, shared by every call a search (or an index build) makes.

    ``spend`` is the only way ``used`` moves, and it refuses once that would
    exceed ``limit`` -- so no caller can accidentally make a call the budget
    didn't authorize (SPR-41: two independently-tracked stage budgets
    together exceeded the advertised cap).

    SPR-46: ``spend`` is called from the sweep's worker threads, so the
    read-check-write has to be atomic as a whole -- a plain ``if`` guard
    lets two concurrent callers both read ``used`` before either writes
    it back, authorizing more than ``limit`` calls. Compare-and-swap under
    a lock closes that window: the write only lands if nothing else moved
    ``used`` since this call's read, otherwise it re-reads and retries.
    """

    limit: int
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def spend(self, n: int = 1) -> bool:
        while True:
            seen = self.used
            candidate = seen + n
            if candidate > self.limit:
                return False
            with self._lock:
                if self.used != seen:
                    continue
                self.used = candidate
                return True


@dataclass
class SearchScope:
    states: tuple[str, ...]
    start_year: int
    end_year: int
    include_virtual: bool = False
    max_calls: int = 120  # TOTAL for the whole search, every stage (SPR-26 §4/§8)


@dataclass
class Coverage:
    """What the search actually checked, so the UI can say "searched 120 of
    1,240 known results" instead of "no results" (SPR-26 §4, SPR-32/SPR-40).
    """

    result_sets_known: int
    result_sets_checked: int
    races_known: int
    budget_limit: int
    budget_used: int
    complete: bool  # checked == known
    index_built_at: str  # ISO, or "" if never built
    cursor: str  # opaque resume token; pass back as resume_cursor

    # --- Back-compat shim for pre-SPR-30 callers (the old ``SearchProgress``
    # field names) -- ``candidate_result_sets`` and ``app.py``'s shared
    # progress callback read these until SPR-32 migrates them onto the
    # fields above directly. Computed, not stored, so there is exactly one
    # source of truth.
    @property
    def calls_used(self) -> int:
        return self.budget_used

    @property
    def calls_budget(self) -> int:
        return self.budget_limit

    @property
    def candidates_total(self) -> int:
        return self.result_sets_known

    @property
    def candidates_checked(self) -> int:
        return self.result_sets_checked

    @property
    def capped(self) -> bool:
        return bool(self.cursor)

    @property
    def stage(self) -> str:
        if self.complete:
            return "done"
        return "capped" if self.cursor else "sweep"


# SPR-30: Coverage replaces SearchProgress. Kept as a name, not a distinct
# type, in case anything still imports the old name directly.
SearchProgress = Coverage


def _emit(progress_cb, coverage: Coverage) -> None:
    if progress_cb is not None:
        progress_cb(coverage)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
                    progress_cb,
                    Coverage(
                        result_sets_known=0, result_sets_checked=0, races_known=len(race_map),
                        budget_limit=scope.max_calls, budget_used=calls_used,
                        complete=False, index_built_at="", cursor="capped",
                    ),
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
                progress_cb,
                Coverage(
                    result_sets_known=0, result_sets_checked=0, races_known=len(race_map),
                    budget_limit=scope.max_calls, budget_used=calls_used,
                    complete=False, index_built_at="", cursor="",
                ),
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
            progress_cb,
            Coverage(
                result_sets_known=found_result_sets, result_sets_checked=0, races_known=len(race_map),
                budget_limit=scope.max_calls, budget_used=calls_used,
                complete=False, index_built_at="", cursor="",
            ),
        )

        if len(rows) < rsu.RESULT_SET_CATALOG_PAGE_SIZE:
            break
        page += 1
    else:
        hit_cap = True

    _emit(
        progress_cb,
        Coverage(
            result_sets_known=found_result_sets, result_sets_checked=0, races_known=len(race_map),
            budget_limit=scope.max_calls, budget_used=calls_used,
            complete=not hit_cap, index_built_at="", cursor="capped" if hit_cap else "",
        ),
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


def _index_built_at(scope: SearchScope) -> str:
    """Freshest ``refreshed_at`` among this scope's indexed result sets.

    "" means ``build_index`` has never covered this exact states/years
    combination -- the honest answer for Coverage.index_built_at, since
    there is nothing to report a date for.
    """
    rows = storage.load_result_set_catalog(scope.states, scope.start_year, scope.end_year)
    timestamps = [row["refreshed_at"] for row in rows if row.get("refreshed_at")]
    return max(timestamps) if timestamps else ""


def indexed_candidates(scope: SearchScope, preferred_states: tuple[str, ...] = ()) -> list[dict]:
    """Scope -> candidate result sets. SQLite read only, zero HTTP.

    Only ``build_index`` writes the tables this reads -- a stale or empty
    index here just means fewer (or no) candidates, never an HTTP call.

    One row per ``(race_id, event_id)``: when an event published several
    result sets (QA measured 3.7% do, with every finisher in every one),
    the newest-modified is kept and the rest are dropped *before* any
    ``get-results`` call is spent, rather than deduping the results after
    paying for every duplicate.

    Ranked: states in ``preferred_states`` (normally the states already in
    the caller's tracker) first, then everything else; newest event date
    first within each group. ``preferred_states`` defaults to empty, which
    collapses to a single newest-first group.
    """
    rows = storage.load_result_set_catalog(scope.states, scope.start_year, scope.end_year)
    if not rows:
        return []

    race_ids = {row["race_id"] for row in rows}
    race_catalog = {race["race_id"]: race for race in storage.load_race_catalog(race_ids)}

    best_by_event: dict[tuple, dict] = {}
    for row in rows:
        key = (row["race_id"], row["event_id"])
        incumbent = best_by_event.get(key)
        if incumbent is None or (row["last_modified_ts"] or 0) > (incumbent["last_modified_ts"] or 0):
            best_by_event[key] = row

    candidates = []
    for (race_id, event_id), row in best_by_event.items():
        race = race_catalog.get(race_id, {})
        candidates.append(
            {
                "race_id": race_id,
                "event_id": event_id,
                "result_set_id": row["result_set_id"],
                "race_name": row["race_name"],
                "event_date": row["event_date"],
                "state": row["state"],
                "city": race.get("city", ""),
                "url": race.get("url", ""),
                "race_type": rsu.race_type_from_event_name(row["race_name"]),
            }
        )

    preferred = {str(state).upper() for state in preferred_states}
    candidates.sort(key=lambda candidate: candidate["event_date"], reverse=True)
    candidates.sort(key=lambda candidate: 0 if candidate["state"] in preferred else 1)
    return candidates


def build_index(
    states: tuple[str, ...],
    start_year: int,
    end_year: int,
    budget: CallBudget,
    progress_cb=None,
) -> Coverage:
    """CLI-only offline index builder -- not reachable from a page load.

    Walks ``runsignup_results.search_races_in_window`` (adaptive date-window
    splitting, never ``/races`` page >= 2 -- SPR-36) and the public
    result-set catalog, writing both into the existing ``race_catalog`` /
    ``result_set_catalog`` SQLite tables. A user's own search then reads
    those tables through :func:`indexed_candidates` and makes zero
    discovery HTTP calls -- this is the only place in the codebase that
    walks ``/races`` or the catalog.
    """
    start_date, end_date = f"{int(start_year):04d}-01-01", f"{int(end_year):04d}-12-31"
    race_map: dict[int, dict] = {}
    races_capped = False

    for state in states:
        if budget.used >= budget.limit:
            races_capped = True
            break
        for race in rsu.search_races_in_window(state, start_date, end_date, budget):
            if not race["event_dates"]:
                continue
            race_map[race["race_id"]] = race
        _emit(
            progress_cb,
            Coverage(
                result_sets_known=0, result_sets_checked=0, races_known=len(race_map),
                budget_limit=budget.limit, budget_used=budget.used,
                complete=False, index_built_at="", cursor="",
            ),
        )
        if budget.used >= budget.limit:
            races_capped = True
            break

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

    # Same forward-from-watermark catalog walk as the pre-SPR-30
    # candidate_result_sets, just spending from the shared budget instead of
    # a call counter scoped to this function alone.
    watermark = storage.catalog_watermark()
    page = 1
    found_result_sets = 0
    catalog_capped = races_capped  # budget already gone; do not even start

    while not catalog_capped:
        if not budget.spend():
            catalog_capped = True
            break

        rows = rsu.fetch_result_set_catalog(
            modified_since_timestamp=watermark, page=page, results_per_page=rsu.RESULT_SET_CATALOG_PAGE_SIZE
        )
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
            progress_cb,
            Coverage(
                result_sets_known=found_result_sets, result_sets_checked=0, races_known=len(race_map),
                budget_limit=budget.limit, budget_used=budget.used,
                complete=False, index_built_at="", cursor="",
            ),
        )

        if len(rows) < rsu.RESULT_SET_CATALOG_PAGE_SIZE:
            break
        page += 1

    complete = not races_capped and not catalog_capped
    scope = SearchScope(states=tuple(states), start_year=start_year, end_year=end_year, max_calls=budget.limit)
    final_rows = storage.load_result_set_catalog(scope.states, scope.start_year, scope.end_year)
    coverage = Coverage(
        result_sets_known=len(final_rows),
        result_sets_checked=0,
        races_known=len(race_map),
        budget_limit=budget.limit,
        budget_used=budget.used,
        complete=complete,
        index_built_at=_index_built_at(scope),
        cursor="capped" if not complete else "",
    )
    _emit(progress_cb, coverage)
    return coverage


def sweep_for_runner(
    first_name: str,
    last_name: str,
    candidates: list[dict],
    scope: SearchScope,
    progress_cb=None,
    resume_cursor: str = "",
) -> tuple[list[dict], Coverage]:
    """One ``get-results`` call per candidate, name filters only.

    Zero discovery HTTP calls -- ``candidates`` comes from
    :func:`indexed_candidates`, a pure SQLite read. This is the only stage
    left in the request path, so ``scope.max_calls`` is its *entire*
    budget (SPR-41), spent through a single :class:`CallBudget`.

    Takes no DOB and no age. Age classification happens in the caller, not
    here -- see the module docstring's PRIVACY note.
    """
    if not first_name or not last_name:
        raise ValueError("Both first and last name are required.")

    total = len(candidates)
    index = min(_parse_cursor(resume_cursor), total)
    matches: list[dict] = []
    budget = CallBudget(limit=scope.max_calls)
    capped = False
    races_known = len({candidate["race_id"] for candidate in candidates})
    built_at = _index_built_at(scope)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        while index < total:
            batch_size = min(MAX_CONCURRENT_REQUESTS, total - index)
            batch = [
                candidate for candidate in candidates[index : index + batch_size] if budget.spend()
            ]
            if not batch:
                capped = True
                break

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
                for raw in future.result():
                    if runner_matching.names_match(
                        first_name, last_name, raw.get("first_name", ""), raw.get("last_name", "")
                    ):
                        row = rsu.result_to_tracker_row(raw, candidate)
                        row["_raw"] = raw
                        row["_context"] = candidate
                        matches.append(row)

            index += len(batch)
            if len(batch) < batch_size:
                capped = True
                break

            _emit(
                progress_cb,
                Coverage(
                    result_sets_known=total, result_sets_checked=index,
                    races_known=races_known,
                    budget_limit=budget.limit, budget_used=budget.used,
                    complete=False, index_built_at=built_at, cursor="",
                ),
            )

    coverage = Coverage(
        result_sets_known=total,
        result_sets_checked=index,
        races_known=races_known,
        budget_limit=budget.limit,
        budget_used=budget.used,
        complete=index >= total,
        index_built_at=built_at,
        cursor=str(index) if capped else "",
    )
    _emit(progress_cb, coverage)
    return matches, coverage


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(
        description="Build the offline race/result-set index (SPR-30). CLI only -- "
        "never called from a page load."
    )
    parser.add_argument("--states", required=True, help="Comma-separated, e.g. MO,KS")
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--max-calls", type=int, default=120)
    args = parser.parse_args()

    states = tuple(s.strip().upper() for s in args.states.split(",") if s.strip())
    budget = CallBudget(limit=args.max_calls)

    def _report(coverage: Coverage) -> None:
        print(
            f"  races_known={coverage.races_known} "
            f"result_sets_known={coverage.result_sets_known} "
            f"calls {coverage.budget_used}/{coverage.budget_limit}"
        )

    started = time.monotonic()
    result = build_index(states, args.start_year, args.end_year, budget, progress_cb=_report)
    elapsed = time.monotonic() - started

    print(
        f"\nIndexed {result.races_known} race(s), {result.result_sets_known} result set(s) "
        f"for {states} {args.start_year}-{args.end_year}."
    )
    print(f"API calls: {result.budget_used}/{result.budget_limit}. Complete: {result.complete}.")
    print(f"Wall clock: {elapsed:.1f}s. Index built at: {result.index_built_at or '(not recorded)'}")
