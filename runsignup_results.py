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

import os
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
DEFAULT_RACE_DATE_TOLERANCE_DAYS = 7

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
    """GET a RunSignUp REST path and raise on both HTTP and API-level errors."""
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


def split_runner_name(runner_name: str) -> tuple[str, str]:
    """Return first/last search terms, leaving last blank for one-word names."""
    parts = str(runner_name or "").split()
    if not parts:
        raise ValueError("Runner name is required.")
    return parts[0], parts[-1] if len(parts) > 1 else ""


def race_match_is_confirmed(
    tracker_race: dict,
    candidate: dict,
    tolerance_days: int = DEFAULT_RACE_DATE_TOLERANCE_DAYS,
) -> bool:
    """Confirm a search hit using state and date, never fuzzy name alone."""
    tracker_state = str(tracker_race.get("state") or "").strip().casefold()
    candidate_state = str(candidate.get("state") or "").strip().casefold()
    if not tracker_state or tracker_state != candidate_state:
        return False

    try:
        tracker_date = date.fromisoformat(str(tracker_race.get("race_date") or ""))
        candidate_date = date.fromisoformat(str(candidate.get("next_date") or ""))
    except ValueError:
        return False
    return abs((candidate_date - tracker_date).days) <= int(tolerance_days)


def unique_tracker_races(rows: Iterable[dict]) -> list[dict]:
    """Collapse duplicate runner rows for the same tracked race occurrence."""
    unique = {}
    for row in rows:
        key = (
            str(row.get("race_name") or "").strip().casefold(),
            str(row.get("state") or "").strip().casefold(),
            str(row.get("race_date") or "").strip(),
        )
        if key[0]:
            unique.setdefault(key, row)
    return list(unique.values())


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
    max_event_days: int | None = DEFAULT_DISCOVERY_EVENT_DAYS,
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
        contexts = discover_result_sets(
            race_id,
            include_virtual,
            since_date=since_date,
            max_event_days=max_event_days,
        )

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


def search_tracker_results(
    runner_name: str,
    tracker_races: Iterable[dict],
    include_virtual: bool = False,
    max_event_days: int | None = DEFAULT_DISCOVERY_EVENT_DAYS,
) -> list[dict]:
    """Search a runner across safely confirmed races already in the tracker."""
    first_name, last_name = split_runner_name(runner_name)
    ambiguous = not bool(last_name)
    reports = []

    for tracker_race in unique_tracker_races(tracker_races):
        report = {
            "race_name": tracker_race.get("race_name", ""),
            "race_date": tracker_race.get("race_date", ""),
            "state": tracker_race.get("state", ""),
            "status": "needs_linking",
            "count": 0,
            "ambiguous": ambiguous,
        }
        try:
            candidates = search_races(
                tracker_race.get("race_name", ""),
                state=tracker_race.get("state") or None,
            )
            confirmed = [
                candidate
                for candidate in candidates
                if race_match_is_confirmed(tracker_race, candidate)
            ]
            if len(confirmed) != 1:
                report["candidate_count"] = len(candidates)
                reports.append(report)
                continue

            match = confirmed[0]
            rows = find_runner_results(
                match["race_id"],
                first_name=first_name,
                last_name=last_name,
                include_virtual=include_virtual,
                max_event_days=max_event_days,
            )
            report.update(
                status="searched",
                count=len(rows),
                race_id=match["race_id"],
                matched_race_name=match.get("name", ""),
            )
        except (RunSignUpError, requests.RequestException, ValueError) as exc:
            report.update(status="error", error=str(exc))
        reports.append(report)
    return reports


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
    parser.add_argument("--runner", default="", help="Runner name to search for.")
    parser.add_argument("--from-tracker", action="store_true", help="Search saved tracker races.")
    parser.add_argument(
        "--user-id",
        default=os.getenv("RUNTRACKER_USER_ID", "default"),
        help="Tracker user id (default: RUNTRACKER_USER_ID or 'default').",
    )
    parser.add_argument(
        "--wide-sweep",
        action="store_true",
        help="Opt into discovery across every historical event day.",
    )
    parser.add_argument("--list-sets", action="store_true", help="List public result sets and exit.")
    args = parser.parse_args()

    if args.from_tracker:
        import storage

        if not args.runner.strip():
            parser.error("--runner is required with --from-tracker")
        reports = search_tracker_results(
            args.runner,
            storage.load_races(args.user_id),
            include_virtual=args.include_virtual,
            max_event_days=None if args.wide_sweep else DEFAULT_DISCOVERY_EVENT_DAYS,
        )
        confident_total = 0
        candidate_total = 0
        for report in reports:
            label = f"{report['race_name']} ({report['race_date']}, {report['state']})"
            if report["status"] == "searched":
                if report["ambiguous"]:
                    candidate_total += report["count"]
                    print(f"AMBIGUOUS {label}: {report['count']} candidate result(s)")
                else:
                    confident_total += report["count"]
                    print(f"{label}: {report['count']} result(s)")
            elif report["status"] == "needs_linking":
                print(f"NEEDS LINKING {label}: no confirmed RunSignUp race")
            else:
                print(f"ERROR {label}: {report['error']}")
        if any(report["ambiguous"] for report in reports):
            print(f"Total: 0 confident result(s); {candidate_total} ambiguous candidate(s)")
        else:
            print(f"Total: {confident_total} result(s)")
        raise SystemExit(1 if any(report["status"] == "error" for report in reports) else 0)
    elif args.list_sets or not (args.first_name or args.last_name):
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
