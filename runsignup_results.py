"""RunSignUp results client for RunTracker.

This module pulls *real* finisher results from RunSignUp so the app no longer has
to rely on hard-coded rows in SAMPLE_DATA.

Three things to know, because they are what blocked earlier attempts:

1. Results live on ``runsignup.com/rest/...``, not ``api.runsignup.com``.
2. The request name is ``get-results``. ``get-race-results`` is rejected.
3. Public result sets need **no api_key / api_secret**. Credentials are only
   needed for the registration-side endpoints, so the whole results feature
   works without secrets configured.

Everything here is plain ``requests`` + dicts so it can be unit tested and run
from the command line without Streamlit:

    python runsignup_results.py --race-id 85066 --last-name Springhower
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
RESULTS_API_HOST = "https://runsignup.com/rest"
DEFAULT_TIMEOUT = 30

# Hospital Hill Run -- the race we are building against first.
HOSPITAL_HILL_RACE_ID = 85066

# RunSignUp caps a results page at 1000 rows.
MAX_RESULTS_PER_PAGE = 1000
DEFAULT_DISCOVERY_EVENT_DAYS = 5

# Mirrors RunTracker's VALID_RACE_TYPES. Longest/most specific patterns first so
# "Virtual Half Marathon" does not get mistaken for a 10K etc.
RACE_TYPE_PATTERNS = [
    ("Half Marathon", (r"half\s*marathon", r"\bhalf\b", r"13\.1")),
    ("10 Mile", (r"10\s*-?\s*mile", r"10\s*miler", r"\b10m\b")),
    ("10K", (r"\b10\s*-?k\b", r"6\.2")),
    ("5K", (r"\b5\s*-?k\b", r"3\.1")),
]


class RunSignUpError(RuntimeError):
    """Raised when RunSignUp returns an API-level error payload."""


# -------------------------------------------------
# Low-level request helper
# -------------------------------------------------
def _build_session() -> requests.Session:
    """Create a connection-pooled client with bounded transient retries."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.headers.update({"User-Agent": "RunTracker/1.0 (+public RunSignUp client)"})
    session.mount("https://", adapter)
    return session


_SESSION = _build_session()


def _get(path: str, **params) -> dict:
    """GET a RunSignUp REST path and raise on both HTTP and API-level errors.

    ``path`` can be any ``{RESULTS_API_HOST}``-relative path, including the
    ``/v2/...`` result-set catalog, which already ends in ``.json``. The
    ``format=json`` default is skipped for those paths -- it is redundant
    with the extension, and verified live to be harmless either way, but
    skipping it keeps the request shape unambiguous rather than relying on
    RunSignUp silently tolerating both.
    """
    if not path.endswith(".json"):
        params.setdefault("format", "json")
    response = _SESSION.get(f"{RESULTS_API_HOST}{path}", params=params, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        raise RunSignUpError(f"RunSignUp HTTP {response.status_code} for {path}: {response.text[:500]}")

    payload = response.json()

    # RunSignUp returns HTTP 200 with an {"error": {...}} body on bad params,
    # so a status check alone is not enough.
    if isinstance(payload, dict) and "error" in payload:
        error = payload["error"]
        raise RunSignUpError(f"RunSignUp API error for {path}: {error.get('error_msg', error)}")

    return payload


# -------------------------------------------------
# Parsing helpers
# -------------------------------------------------
def race_type_from_event_name(event_name: str) -> str:
    """Map a RunSignUp event name onto one of RunTracker's race types."""
    text = (event_name or "").lower()
    for race_type, patterns in RACE_TYPE_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            return race_type
    return ""


def is_virtual_event(event_name: str) -> bool:
    return "virtual" in (event_name or "").lower()


def normalize_finish_time(raw_time: str) -> str:
    """Turn a RunSignUp time into the ``H:MM:SS`` / ``MM:SS`` form RunTracker parses.

    RunSignUp returns hundredths (``1:56:24.47``, ``36:12.57``). RunTracker's
    ``time_to_seconds`` calls ``int()`` on each part, so the fractional seconds
    have to go or the import blows up. Seconds are truncated, not rounded, to
    match how RunSignUp displays them on the public results page.
    """
    text = str(raw_time or "").strip()
    if not text:
        return ""

    parts = text.split(":")
    try:
        cleaned = [int(float(part)) for part in parts]
    except ValueError:
        return ""

    if len(cleaned) == 3:
        hours, minutes, seconds = cleaned
    elif len(cleaned) == 2:
        hours = 0
        minutes, seconds = cleaned
    elif len(cleaned) == 1:
        hours = minutes = 0
        seconds = cleaned[0]
    else:
        return ""

    # Normalize overflow (a 75:20 time means 1:15:20).
    total = hours * 3600 + minutes * 60 + seconds
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_event_date(start_time: str) -> str:
    """``'5/16/2026 07:00'`` -> ``'2026-05-16'``."""
    text = str(start_time or "").strip()
    if not text:
        return ""
    date_part = text.split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _race_event_dates(race: dict) -> list[str]:
    """Return sorted, unique event dates from a Get Races listing."""
    dates = {
        parsed
        for event in race.get("events") or []
        if (parsed := parse_event_date(event.get("start_time")))
    }
    return sorted(dates)


# -------------------------------------------------
# Race / event / result-set discovery
# -------------------------------------------------
def search_races(name: str, state: str | None = None, include_past: bool = True) -> list[dict]:
    """Find public races by name without RunSignUp credentials.

    RunSignUp's ``next_date`` and server-side date filtering are unreliable for
    series-style listings. Event details are requested and the returned
    ``next_date`` is derived from ``events[].start_time`` instead.
    """
    query = str(name or "").strip()
    if not query:
        raise ValueError("Race name is required.")

    params = {
        "name": query,
        "events": "T",
        "page": 1,
        "results_per_page": 100,
        "sort": "date ASC",
    }
    if state:
        params["state"] = str(state).strip().upper()

    window_start = date.today()
    window_end = window_start + timedelta(days=365)
    if not include_past:
        params["start_date"] = window_start.isoformat()
        params["end_date"] = window_end.isoformat()

    payload = _get("/races", **params)
    matches = []
    for wrapper in payload.get("races") or []:
        race = wrapper.get("race", wrapper)
        event_dates = _race_event_dates(race)
        if not include_past:
            event_dates = [
                event_date
                for event_date in event_dates
                if window_start.isoformat() <= event_date <= window_end.isoformat()
            ]
            if not event_dates:
                continue

        future_dates = [event_date for event_date in event_dates if event_date >= window_start.isoformat()]
        derived_date = min(future_dates) if future_dates else (max(event_dates) if event_dates else "")
        address = race.get("address") or {}
        matches.append(
            {
                "race_id": race.get("race_id"),
                "name": race.get("name", ""),
                "city": address.get("city", ""),
                "state": (address.get("state") or "").upper(),
                "next_date": derived_date,
                "url": race.get("url") or race.get("external_race_url") or "",
            }
        )
    return matches


def fetch_race(race_id: int) -> dict:
    """Full race record, including every historical event."""
    return _get(f"/race/{race_id}", future_events_only="F")["race"]


def list_events(race_id: int, race: dict | None = None) -> list[dict]:
    """Flatten a race's events into ``{event_id, name, date, race_type, virtual}``."""
    race = race or fetch_race(race_id)
    events = []
    for event in race.get("events") or []:
        events.append(
            {
                "event_id": event.get("event_id"),
                "race_event_days_id": event.get("race_event_days_id"),
                "name": event.get("name", ""),
                "date": parse_event_date(event.get("start_time")),
                "distance": event.get("distance", ""),
                "race_type": race_type_from_event_name(event.get("name", "")),
                "virtual": is_virtual_event(event.get("name", "")),
            }
        )
    return events


def list_result_sets(race_id: int, event_id: int) -> list[dict]:
    """Public result sets for one event. Returns [] when the event has none."""
    payload = _get(f"/race/{race_id}/results/get-result-sets", event_id=event_id)
    sets = payload.get("individual_results_sets") or []
    return [
        {
            "result_set_id": item.get("individual_result_set_id"),
            "name": item.get("individual_result_set_name", ""),
            "public": item.get("public_results") == "T",
            "preliminary": item.get("preliminary_results") == "T",
        }
        for item in sets
    ]


def discover_result_sets(
    race_id: int,
    include_virtual: bool = False,
    since_date: str = "",
    max_event_days: int | None = DEFAULT_DISCOVERY_EVENT_DAYS,
) -> list[dict]:
    """Public result sets from the most recent race days.

    ``race_event_days_id`` groups all distances held on one day. Discovery is
    bounded to five recent days by default; pass ``max_event_days=None`` to opt
    into the full historical scan. Existing positional calls remain compatible.
    ``since_date`` can further restrict the scan.
    """
    race = fetch_race(race_id)
    race_name = race.get("name", "")
    address = race.get("address") or {}

    events = list_events(race_id, race)
    events.sort(key=lambda event: event["date"], reverse=True)
    if max_event_days is not None:
        if int(max_event_days) < 1:
            raise ValueError("max_event_days must be at least 1 or None.")
        selected_days = []
        for event in events:
            day_key = event["race_event_days_id"] or event["date"] or event["event_id"]
            if day_key not in selected_days:
                selected_days.append(day_key)
            if len(selected_days) == int(max_event_days):
                break
        selected_day_set = set(selected_days)
        events = [
            event
            for event in events
            if (event["race_event_days_id"] or event["date"] or event["event_id"]) in selected_day_set
        ]

    discovered = []
    for event in events:
        if not include_virtual and event["virtual"]:
            continue
        if since_date and (not event["date"] or event["date"] < since_date):
            continue
        try:
            sets = list_result_sets(race_id, event["event_id"])
        except RunSignUpError:
            # Events with no results raise rather than returning empty; skip them.
            continue
        for result_set in sets:
            if not result_set["public"]:
                continue
            discovered.append(
                {
                    **event,
                    "race_id": race_id,
                    "race_name": race_name,
                    "city": address.get("city", ""),
                    "state": (address.get("state") or "").upper(),
                    "result_set_id": result_set["result_set_id"],
                    "result_set_name": result_set["name"],
                    "preliminary": result_set["preliminary"],
                }
            )
    return discovered


# -------------------------------------------------
# Results
# -------------------------------------------------
def fetch_results(
    race_id: int,
    event_id: int,
    result_set_id: int,
    first_name: str = "",
    last_name: str = "",
    results_per_page: int = MAX_RESULTS_PER_PAGE,
    max_pages: int = 25,
) -> list[dict]:
    """Fetch finisher rows, paging until RunSignUp returns a short page.

    ``first_name`` / ``last_name`` are honoured server-side, which is what makes
    "find my result in a 1,156-person race" cheap -- pass them whenever you can
    instead of downloading the whole field and filtering locally.
    """
    results: list[dict] = []
    page_size = max(1, min(int(results_per_page), MAX_RESULTS_PER_PAGE))

    for page in range(1, max_pages + 1):
        params = {
            "event_id": event_id,
            "result_set_id": result_set_id,
            "page": page,
            "results_per_page": page_size,
        }
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name

        payload = _get(f"/race/{race_id}/results/get-results", **params)
        sets = payload.get("individual_results_sets") or []
        if not sets:
            break

        page_rows = sets[0].get("results") or []
        results.extend(page_rows)

        if len(page_rows) < page_size:
            break

    return results


def result_to_tracker_row(result: dict, context: dict) -> dict:
    """Shape one RunSignUp result row like a RunTracker source row.

    ``context`` is an entry from :func:`discover_result_sets` (or any dict with
    ``race_name``/``city``/``state``/``date``/``race_type``).
    """
    first = str(result.get("first_name", "") or "").strip()
    last = str(result.get("last_name", "") or "").strip()
    finish_time = normalize_finish_time(result.get("chip_time") or result.get("clock_time"))

    note_bits = []
    if result.get("place"):
        note_bits.append(f"Place {result['place']}")
    if result.get("bib"):
        note_bits.append(f"Bib {result['bib']}")
    if result.get("pace"):
        note_bits.append(f"{result['pace']}/mi")
    note_bits.append("via RunSignUp results API")

    return {
        "state": context.get("state", ""),
        "state_name": context.get("state_name", ""),
        "runner_name": f"{first} {last}".strip(),
        "race_type": context.get("race_type", ""),
        "race_name": context.get("race_name", ""),
        "race_date": context.get("date", ""),
        "finish_time": finish_time,
        "city": context.get("city", ""),
        "notes": " | ".join(note_bits),
        "status": "Completed",
    }


def find_runner_results(
    race_id: int,
    first_name: str = "",
    last_name: str = "",
    include_virtual: bool = False,
    result_sets: Iterable[dict] | None = None,
    since_date: str = "",
) -> list[dict]:
    """Search every public result set on a race for one runner.

    Returns tracker-shaped rows, newest race date first. Each row also carries
    ``_raw`` with the untouched RunSignUp record for display.
    """
    if not first_name and not last_name:
        raise ValueError("Provide at least a first or last name to search for.")

    if result_sets is not None:
        contexts = list(result_sets)
    else:
        contexts = discover_result_sets(race_id, include_virtual, since_date=since_date)

    rows = []
    for context in contexts:
        matches = fetch_results(
            race_id,
            context["event_id"],
            context["result_set_id"],
            first_name=first_name,
            last_name=last_name,
            results_per_page=200,
        )
        for match in matches:
            # Server-side name filters are prefix/partial matches, so confirm
            # the hit locally before presenting it as "your" result.
            if last_name and last_name.strip().lower() != str(match.get("last_name", "")).strip().lower():
                continue
            if first_name and first_name.strip().lower() != str(match.get("first_name", "")).strip().lower():
                continue
            row = result_to_tracker_row(match, context)
            row["_raw"] = match
            rows.append(row)

    rows.sort(key=lambda row: row.get("race_date", ""), reverse=True)
    return rows


# -------------------------------------------------
# Scoped result-set catalog + state/year race search (SPR-15/17)
# -------------------------------------------------
RESULT_SET_CATALOG_HOST_PATH = "/v2/results/updated-result-sets.json"
RESULT_SET_CATALOG_PAGE_SIZE = 5000


def fetch_result_set_catalog(
    modified_since_timestamp: int | None = None,
    page: int = 1,
    results_per_page: int = RESULT_SET_CATALOG_PAGE_SIZE,
) -> list[dict]:
    """GET /v2/results/updated-result-sets.json -- the public result-set catalog.

    -> [{"race_id", "event_id", "result_set_id", "race_name", "last_modified_ts"}]

    Returns [] on an empty page (that is the paging terminator). Verified
    live against the real endpoint: it is sorted ascending by
    ``last_modified_ts`` (ties broken by id), which is what makes
    ``modified_since_timestamp`` a usable incremental-sync watermark, and
    its page-size query param is ``num_per_page`` (not ``results_per_page``
    -- that name is kept on this function only to match RunTracker's own
    paging convention) capped at 5000.
    """
    params = {
        "page": page,
        "num_per_page": results_per_page,
    }
    if modified_since_timestamp is not None:
        params["modified_since_timestamp"] = modified_since_timestamp

    payload = _get(RESULT_SET_CATALOG_HOST_PATH, **params)
    rows = payload.get("result_sets") or []
    return [
        {
            "race_id": row.get("race_id"),
            "event_id": row.get("event_id"),
            "result_set_id": row.get("individual_result_set_id"),
            "race_name": row.get("race_name", ""),
            "last_modified_ts": row.get("last_modified_ts"),
        }
        for row in rows
    ]


def search_races_by_state_range(
    state: str, start_date: str, end_date: str, page: int = 1, results_per_page: int = 1000
) -> list[dict]:
    """GET /races for one state/year window -- a whole state-year in 1-2 calls.

    -> [{"race_id", "name", "city", "state", "url", "event_dates": [iso, ...]}]
    """
    params = {
        "state": str(state or "").strip().upper(),
        "start_date": start_date,
        "end_date": end_date,
        "events": "T",
        "page": page,
        "results_per_page": results_per_page,
    }
    payload = _get("/races", **params)
    races = []
    for wrapper in payload.get("races") or []:
        race = wrapper.get("race", wrapper)
        address = race.get("address") or {}
        races.append(
            {
                "race_id": race.get("race_id"),
                "name": race.get("name", ""),
                "city": address.get("city", ""),
                "state": (address.get("state") or "").upper(),
                "url": race.get("url") or race.get("external_race_url") or "",
                "event_dates": _race_event_dates(race),
            }
        )
    return races


def event_has_results(race_id: int, event_id: int) -> bool:
    """GET /race/{race_id}/results/has-result-sets -> has_results == "T".

    Cheap pruning filter. Never raises -- return False on RunSignUpError.

    Deviation from the SPR-17 ticket's documented signature
    ``event_has_results(event_id: int) -> bool``: RunSignUp's real endpoint
    is race-scoped (``/race/{race_id}/results/has-result-sets``), confirmed
    live -- the bare ``/results/has-result-sets`` path the ticket describes
    returns ``{"error": {"error_code": 1, "error_msg": "Unknown method"}}``
    for every call. Dropping race_id would make this pruning filter always
    return False (silently useless) rather than ever pruning anything, so
    race_id was added as a required parameter. Flagged in the SPR-17
    done-comment for the dependent issues to confirm.
    """
    try:
        payload = _get(f"/race/{race_id}/results/has-result-sets", event_id=event_id)
    except RunSignUpError:
        return False
    return payload.get("has_results") == "T"


# -------------------------------------------------
# Monetization: affiliate links
# -------------------------------------------------
def affiliate_race_url(race_url: str, affiliate_token: str = "") -> str:
    """Append a RunSignUp affiliate token to a race URL.

    RunSignUp's Affiliate Program shares 15% of processing-fee revenue on
    registrations that happen within 30 days of a click on a tokenized link.
    With no token configured this is a no-op, so it is always safe to call.
    """
    url = str(race_url or "").strip()
    token = str(affiliate_token or "").strip()
    if not url or not token:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}afmc={token}"


# -------------------------------------------------
# CLI smoke test
# -------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Pull real RunSignUp results.")
    parser.add_argument("--race-id", type=int, default=HOSPITAL_HILL_RACE_ID)
    parser.add_argument("--first-name", default="")
    parser.add_argument("--last-name", default="")
    parser.add_argument("--include-virtual", action="store_true")
    parser.add_argument("--list-sets", action="store_true", help="List public result sets and exit.")
    args = parser.parse_args()

    if args.list_sets or not (args.first_name or args.last_name):
        sets = discover_result_sets(args.race_id, include_virtual=args.include_virtual)
        print(f"{len(sets)} public result set(s) for race {args.race_id}:")
        for entry in sets:
            print(
                f"  {entry['date'] or '??????????':<10} event={entry['event_id']:<8} "
                f"set={entry['result_set_id']:<8} {entry['race_type'] or '?':<13} {entry['name']}"
            )
    else:
        found = find_runner_results(
            args.race_id,
            first_name=args.first_name,
            last_name=args.last_name,
            include_virtual=args.include_virtual,
        )
        print(f"{len(found)} result(s):")
        for row in found:
            raw = row.pop("_raw", {})
            print(json.dumps(row, indent=2))
            print(f"    raw chip_time={raw.get('chip_time')} place={raw.get('place')} age={raw.get('age')}")
