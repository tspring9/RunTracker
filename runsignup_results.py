"""RunSignUp results client for RunTracker.

This module pulls *real* finisher results from RunSignUp so the app no longer has
to rely on hard-coded rows in SAMPLE_DATA.

Three things to know, because they are what blocked earlier attempts:

1. Results live on ``runsignup.com/rest/...``, not ``api.runsignup.com``.
2. The request name is ``get-results``. ``get-race-results`` is rejected.
3. Public result sets need **no api_key / api_secret**. Credentials are only
   needed for the registration-side endpoints, so the whole results feature
   works without secrets configured.

API caller registration (hard deadline 2027-01-01)
--------------------------------------------------
Point 3 above stops being the whole story on **2027-01-01**. RunSignUp now
requires every API caller to register, and from that date calls without a
valid registration token are rejected -- including the unauthenticated public
results calls this module makes today. See
https://info.runsignup.com/2026/07/17/new-api-registration-requirements/

Registration is free and is a *user* action (RunSignUp login -> API Keys ->
"Register as an API caller"). It yields two values, which this module reads
from Streamlit secrets or environment variables:

    RUNSIGNUP_API_REG_TOKEN   -> sent as the ``rsu_api_reg`` GET parameter
    RUNSIGNUP_API_REG_SECRET  -> sent as the ``X-RSU-API-REG-SECRET`` header

Both are optional *until* the cutover: with neither set every call goes out
exactly as it did before, so local dev and the test suite keep working
unauthenticated. That is deliberate -- it lets this plumbing merge well ahead
of the deadline instead of being a flag-day change.

See docs/runsignup-api-registration.md for where to put the two values and
how to verify them.

Two other limits from the same announcement, worth knowing before you add
callers: the API allows only **2 concurrent calls** (everything here issues
requests sequentially, so that is headroom, not a constraint -- but do not
fan these calls out in threads), and data requests are documented as limited
to **one year back**. That second one is not enforced on the public results
endpoints as of 2026-10-09: ``--check-data-window`` pulled finisher rows from
2.4 years back on an unregistered caller. See :func:`probe_data_window`, and
re-run it registered to confirm the limit is not applied per-registration.

Everything here is plain ``requests`` + dicts so it can be unit tested and run
from the command line without Streamlit:

    python runsignup_results.py --race-id 85066 --last-name Springhower
    python runsignup_results.py --check-registration
    python runsignup_results.py --check-data-window
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

# -------------------------------------------------
# API caller registration (see module docstring)
# -------------------------------------------------
# Wire names are fixed by RunSignUp; the *_SETTING names are ours.
API_REG_TOKEN_PARAM = "rsu_api_reg"
API_REG_SECRET_HEADER = "X-RSU-API-REG-SECRET"
API_REG_TOKEN_SETTING = "RUNSIGNUP_API_REG_TOKEN"
API_REG_SECRET_SETTING = "RUNSIGNUP_API_REG_SECRET"

# The date unregistered calls start being rejected.
API_REG_ENFORCEMENT_DATE = "2027-01-01"

# RunSignUp's error_code for "Invalid API caller credentials." (HTTP 400).
API_REG_INVALID_ERROR_CODE = 17

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


class RunSignUpRegistrationError(RunSignUpError):
    """Raised when RunSignUp rejects our API caller registration.

    A subclass of ``RunSignUpError`` so existing ``except RunSignUpError``
    handlers keep working, but distinguishable because the fix is completely
    different: this one is never transient and never about the race being
    asked for -- it is bad config.
    """


# -------------------------------------------------
# API caller registration config
# -------------------------------------------------
def _setting(name: str, default: str = "") -> str:
    """Read a setting from Streamlit secrets first, then environment variables.

    Same pattern as ``get_db_path`` in storage.py. Importing streamlit lazily
    (and swallowing the failure) is what keeps this module usable from the CLI
    and from pytest, where there is no Streamlit runtime at all.
    """
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return str(os.getenv(name, default)).strip()


def api_registration() -> tuple[str, str]:
    """``(token, secret)`` for this app's RunSignUp API caller registration.

    ``("", "")`` means unregistered, which is still valid until
    ``API_REG_ENFORCEMENT_DATE``.
    """
    return _setting(API_REG_TOKEN_SETTING), _setting(API_REG_SECRET_SETTING)


def registration_notes() -> list[str]:
    """Human-readable warnings about the current registration config.

    Returned rather than logged so the CLI, app.py, and tests can each decide
    how loudly to surface them.
    """
    token, secret = api_registration()
    notes = []
    if not token and not secret:
        notes.append(
            f"Not registered as a RunSignUp API caller. Calls still work today but are "
            f"rejected from {API_REG_ENFORCEMENT_DATE}. Register (free) at RunSignUp -> "
            f"API Keys -> 'Register as an API caller', then set {API_REG_TOKEN_SETTING} "
            f"and {API_REG_SECRET_SETTING}."
        )
    elif not token:
        notes.append(f"{API_REG_SECRET_SETTING} is set but {API_REG_TOKEN_SETTING} is missing; calls go out unregistered.")
    elif not secret:
        notes.append(f"{API_REG_TOKEN_SETTING} is set but {API_REG_SECRET_SETTING} is missing; RunSignUp will reject the token.")
    elif "." not in token:
        # Documented format is "<id>.<token>". Warn, don't block -- a format
        # change on RunSignUp's side should not take the app down.
        notes.append(f"{API_REG_TOKEN_SETTING} does not look like the documented '<id>.<token>' format.")
    return notes


# -------------------------------------------------
# Low-level request helper
# -------------------------------------------------
def _apply_api_registration(session: requests.Session, params: dict) -> dict:
    """Attach the API caller registration to one outgoing request.

    A clean no-op when nothing is configured: no parameter, no header, and any
    stale header from an earlier config is dropped. Called from ``_get`` (not
    just ``_build_session``) because the module-level session is built at import
    time, before Streamlit secrets are necessarily readable.
    """
    token, secret = api_registration()

    if token:
        params.setdefault(API_REG_TOKEN_PARAM, token)
    if secret:
        session.headers[API_REG_SECRET_HEADER] = secret
    else:
        session.headers.pop(API_REG_SECRET_HEADER, None)

    return params


def raise_for_bad_registration(response: requests.Response) -> None:
    """Turn RunSignUp's registration rejection into an actionable error.

    Verified live against both endpoints on 2026-10-09: sending a token that
    RunSignUp does not recognise returns HTTP 400 with ``error_code`` 17,
    ``"Invalid API caller credentials."`` -- on *every* call. So a mis-pasted
    token is strictly worse than no token at all, and it must not be reported
    as a generic "RunSignUp HTTP 400", which reads like a RunSignUp outage and
    sends whoever is on call looking in the wrong place.
    """
    if response.status_code != 400:
        return
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        return
    if error.get("error_code") != API_REG_INVALID_ERROR_CODE:
        return

    token, secret = api_registration()
    reason = str(error.get("error_msg") or "invalid credentials").rstrip(".")
    raise RunSignUpRegistrationError(
        f"RunSignUp rejected our API caller registration: {reason}. "
        f"Check {API_REG_TOKEN_SETTING} (currently {'set' if token else 'UNSET'}) and "
        f"{API_REG_SECRET_SETTING} (currently {'set' if secret else 'UNSET'}) against RunSignUp -> API Keys. "
        f"Clearing both restores unregistered access until {API_REG_ENFORCEMENT_DATE}."
    )


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
    _apply_api_registration(session, {})
    return session


_SESSION = _build_session()


def _get(path: str, **params) -> dict:
    """GET a RunSignUp REST path and raise on both HTTP and API-level errors."""
    params.setdefault("format", "json")
    _apply_api_registration(_SESSION, params)
    response = _SESSION.get(f"{RESULTS_API_HOST}{path}", params=params, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        raise_for_bad_registration(response)
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
# Data-window probe (the "one year back" question)
# -------------------------------------------------
# How many events to try per year before giving up on that year. Most race
# years have several distances; the first few are enough to tell "this year is
# retrievable" from "this year is walled off", and the cap keeps the probe from
# fanning out into hundreds of calls on a long-running series.
DATA_WINDOW_EVENTS_PER_YEAR = 6


def probe_data_window(
    race_id: int = HOSPITAL_HILL_RACE_ID,
    max_years: int = 12,
    events_per_year: int = DATA_WINDOW_EVENTS_PER_YEAR,
) -> dict:
    """Measure how far back RunSignUp actually serves results, year by year.

    RunSignUp's API Developer Contract says data requests are limited to **one
    year back**, but the published docs do not say whether that applies to the
    public results endpoints -- and unregistered calls demonstrably return much
    older seasons today. Rather than reason about it, this walks a real race's
    history newest-to-oldest and records, per year, whether finisher rows can
    still be pulled.

    Run it before and after configuring registration: if the one-year limit is
    enforced per-registration, the registered run's history collapses to the
    last 12 months while the unregistered baseline does not. That comparison is
    the whole point -- a single run in isolation proves nothing.

    Read-only, and bounded to ``max_years`` years x ``events_per_year`` events.
    """
    token, secret = api_registration()
    race = fetch_race(race_id)
    events = [event for event in list_events(race_id, race) if event["date"]]

    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(int(event["date"][:4]), []).append(event)

    today = date.today()
    probes = []
    for year in sorted(by_year, reverse=True)[:max_years]:
        candidates = [event for event in by_year[year] if not event["virtual"]]
        candidates.sort(key=lambda event: event["date"], reverse=True)

        probe = {
            "year": year,
            "date": candidates[0]["date"] if candidates else by_year[year][0]["date"],
            "event_id": None,
            "rows": 0,
            "status": "no-public-result-set",
            "detail": "",
        }
        for event in candidates[:events_per_year]:
            try:
                sets = [entry for entry in list_result_sets(race_id, event["event_id"]) if entry["public"]]
            except RunSignUpRegistrationError:
                # Never a data-window answer -- the config itself is rejected,
                # so every row after this one would be noise. Let it out.
                raise
            except RunSignUpError as exc:
                probe["status"] = "error"
                probe["detail"] = str(exc)[:200]
                continue
            if not sets:
                continue

            probe["event_id"] = event["event_id"]
            probe["date"] = event["date"]
            try:
                rows = fetch_results(
                    race_id,
                    event["event_id"],
                    sets[0]["result_set_id"],
                    results_per_page=1,
                    max_pages=1,
                )
            except RunSignUpRegistrationError:
                raise
            except RunSignUpError as exc:
                probe["status"] = "error"
                probe["detail"] = str(exc)[:200]
                break
            probe["rows"] = len(rows)
            probe["status"] = "ok" if rows else "empty"
            probe["detail"] = ""
            break
        probes.append(probe)

    retrievable = [probe for probe in probes if probe["status"] == "ok"]
    oldest_retrievable = min((probe["date"] for probe in retrievable), default="")
    oldest_event = min((event["date"] for event in events), default="")

    return {
        "race_id": race_id,
        "race_name": race.get("name", ""),
        "registered": bool(token or secret),
        "probed_on": today.isoformat(),
        "oldest_event_date": oldest_event,
        "oldest_retrievable_date": oldest_retrievable,
        "days_back": (today - date.fromisoformat(oldest_retrievable)).days if oldest_retrievable else 0,
        "probes": probes,
    }


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
    parser.add_argument(
        "--check-registration",
        action="store_true",
        help="Report the API caller registration config, make one live call, and exit.",
    )
    parser.add_argument(
        "--check-data-window",
        action="store_true",
        help="Probe how far back results are actually retrievable, and exit. "
        "Run once unregistered and once registered, then compare.",
    )
    args = parser.parse_args()

    if args.check_registration:
        token, secret = api_registration()
        print("RunSignUp API caller registration")
        # Only the id half of "<id>.<token>" is printed; never the secret.
        print(f"  {API_REG_TOKEN_SETTING}:  {token.split('.')[0] + '.***' if token else '(unset)'}")
        print(f"  {API_REG_SECRET_SETTING}: {'(set)' if secret else '(unset)'}")
        print(f"  sending {API_REG_TOKEN_PARAM} param:        {'yes' if token else 'no'}")
        print(f"  sending {API_REG_SECRET_HEADER} header: {'yes' if secret else 'no'}")
        for note in registration_notes():
            print(f"  ! {note}")
        try:
            race = fetch_race(args.race_id)
            print(f"  live call OK: race {args.race_id} -> {race.get('name', '?')}")
        except Exception as exc:  # noqa: BLE001 - CLI smoke test, report anything
            print(f"  live call FAILED: {type(exc).__name__}: {exc}")
            raise SystemExit(1)
        raise SystemExit(0)

    if args.check_data_window:
        try:
            report = probe_data_window(args.race_id)
        except RunSignUpRegistrationError as exc:
            # Config, not a data-window answer. A traceback here buries the
            # one line that says what to fix.
            print(f"Cannot probe the data window: {exc}")
            raise SystemExit(1) from None
        print(f"RunSignUp data window -- race {report['race_id']} {report['race_name']}")
        print(f"  probed on:   {report['probed_on']}")
        print(f"  registered:  {'yes' if report['registered'] else 'no (baseline)'}")
        print(f"  oldest event RunSignUp lists: {report['oldest_event_date'] or '(none)'}")
        for probe in report["probes"]:
            marker = {"ok": "ok   ", "empty": "empty", "error": "ERROR"}.get(probe["status"], "none ")
            print(f"  {probe['year']}  {marker}  {probe['date'] or '??????????'}  {probe['detail']}".rstrip())
        if report["oldest_retrievable_date"]:
            years = report["days_back"] / 365.25
            print(
                f"  oldest retrievable results:   {report['oldest_retrievable_date']} "
                f"({report['days_back']} days / {years:.1f} years back)"
            )
            verdict = (
                "one-year limit NOT enforced on these endpoints"
                if report["days_back"] > 400
                else "consistent with a one-year limit -- compare against the unregistered baseline"
            )
            print(f"  verdict: {verdict}")
        else:
            print("  no retrievable results in the probed range.")
        raise SystemExit(0)

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
