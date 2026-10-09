import os
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, datetime, timedelta

import results_search
import runner_matching
import runsignup_results as rsu
import storage
import discovery_experiment as discovery

st.set_page_config(page_title="50 States Race Tracker", layout="wide")

# -------------------------------------------------
# RunSignUp API settings
# -------------------------------------------------
# This is the same endpoint that worked in the standalone RaceGetRunsignup app.
RUNSIGNUP_API_URL = "https://api.runsignup.com/rest/races"
API_URL = RUNSIGNUP_API_URL  # optional alias, kept for consistency with the prototype

# Credentials live in .streamlit/secrets.toml locally and in the Streamlit Cloud
# secrets manager when deployed. They are read via get_secret() below -- never
# hard-code them here, since this repo is public.
#
# NOTE: the public *results* endpoints need no credentials at all. See
# runsignup_results.py. Only the race-search endpoint below benefits from a key.

# Hospital Hill Run is our pilot race for live results.
PILOT_RACE_ID = rsu.HOSPITAL_HILL_RACE_ID
PILOT_RACE_LABEL = "Hospital Hill Run (Kansas City, MO)"
PILOT_STATE = "MO"

# Board-approved defaults for the Sign Up tab's capped name sweep (SPR-15).
SIGNUP_DEFAULT_LOOKBACK_YEARS = 5
SIGNUP_DEFAULT_MAX_CALLS = 400
SIGNUP_MIN_DOB = date(1920, 1, 1)


def get_secret(name: str, default: str = "") -> str:
    """Read a secret from Streamlit secrets first, then environment variables."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def resolve_user_id() -> str:
    """Resolve the current user id -- the seam where real auth will slot in.

    There is no login system yet (multi-user auth is a separate project). Order:
    an explicit RUNTRACKER_USER_ID secret/env var, then a `?user=` query param,
    then a single-tenant default. Everyone with no override shares "default"'s
    data, same as today, but it is no longer hard-coded SAMPLE_DATA -- it is
    whatever that user has saved. When real auth lands, replace this function's
    body with the authenticated user's id; storage.py never needs to change
    since every call already takes user_id as a parameter.
    """
    secret_user_id = get_secret("RUNTRACKER_USER_ID")
    if secret_user_id:
        return secret_user_id
    query_user_id = st.query_params.get("user")
    if query_user_id:
        return query_user_id
    return "default"


# -------------------------------------------------
# Sample preloaded data
# -------------------------------------------------
SAMPLE_DATA = [
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Rachel", "race_type": "Half Marathon", "race_name": "Lincoln Half Marathon", "race_date": "2024-05-04", "finish_time": "2:06:45", "city": "Lincoln", "notes": "", "status": "Completed"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Rachel", "race_type": "Half Marathon", "race_name": "BMW Dallas Half Marathon", "race_date": "2024-12-15", "finish_time": "2:05:35", "city": "Dallas", "notes": "", "status": "Completed"},
    {"state": "NV", "state_name": "Nevada", "runner_name": "Rachel", "race_type": "Half Marathon", "race_name": "Rock 'n' Roll Las Vegas Half Marathon", "race_date": "2025-02-23", "finish_time": "2:22:16", "city": "Las Vegas", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "OmaHalf", "race_date": "2022-04-16", "finish_time": "2:41:00", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Lincoln Half Marathon", "race_date": "2024-05-04", "finish_time": "1:55:36", "city": "Lincoln", "notes": "", "status": "Completed"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "BMW Dallas Half Marathon", "race_date": "2024-12-15", "finish_time": "1:54:34", "city": "Dallas", "notes": "", "status": "Completed"},
    {"state": "NV", "state_name": "Nevada", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Rock 'n' Roll Las Vegas Half Marathon", "race_date": "2025-02-23", "finish_time": "2:24:04", "city": "Las Vegas", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Rachel", "race_type": "10 Mile", "race_name": "Early Bird Run", "race_date": "2026-04-04", "finish_time": "1:32:42", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Tom", "race_type": "10 Mile", "race_name": "Early Bird Run", "race_date": "2024-04-06", "finish_time": "1:31:21", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Tom", "race_type": "10 Mile", "race_name": "Early Bird Run", "race_date": "2026-04-04", "finish_time": "1:28:07", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Rachel", "race_type": "5K", "race_name": "OmaHalf", "race_date": "2022-04-16", "finish_time": "0:30:33", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Tom", "race_type": "5K", "race_name": "Gator Fun Run", "race_date": "2026-04-25", "finish_time": "0:24:30", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "IN", "state_name": "Indiana", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Indi Mini", "race_date": "2026-05-02", "finish_time": "1:53:20", "city": "Indianapolis", "notes": "", "status": "Completed"},
    {"state": "IN", "state_name": "Indiana", "runner_name": "Rachel", "race_type": "Half Marathon", "race_name": "Indi Mini", "race_date": "2026-05-02", "finish_time": "2:01:40", "city": "Indianapolis", "notes": "", "status": "Completed"},
    {"state": "IN", "state_name": "Indiana", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "Indi Mini", "race_date": "2026-05-02", "finish_time": "2:20:50", "city": "Indianapolis", "notes": "", "status": "Completed"},
    {"state": "IN", "state_name": "Indiana", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "Indi Mini", "race_date": "2019-05-04", "finish_time": "2:41:43", "city": "Indianapolis", "notes": "", "status": "Completed"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "BMW Dallas Half Marathon", "race_date": "2025-12-14", "finish_time": "2:27:28", "city": "Dallas", "notes": "", "status": "Completed"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "BMW Dallas Half Marathon", "race_date": "2024-12-15", "finish_time": "2:18:01", "city": "Dallas", "notes": "", "status": "Completed"},
    {"state": "NE", "state_name": "Nebraska", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "OmaHalf", "race_date": "2022-04-16", "finish_time": "2:26:17", "city": "Omaha", "notes": "", "status": "Completed"},
    {"state": "NV", "state_name": "Nevada", "runner_name": "Olivia", "race_type": "Half Marathon", "race_name": "Rock 'n' Roll Las Vegas Half Marathon", "race_date": "2025-02-23", "finish_time": "2:20:47", "city": "Las Vegas", "notes": "", "status": "Completed"},
    {"state": "CO", "state_name": "Colorado", "runner_name": "", "race_type": "Half Marathon", "race_name": "All-Out Runapalooza", "race_date": "2026-08-08", "finish_time": "", "city": "Denver", "notes": "", "status": "Interested"},
    {"state": "MO", "state_name": "Missouri", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Hospital Hill Run", "race_date": "2026-05-16", "finish_time": "1:56:24", "city": "Kansas City", "notes": "", "status": "Completed"},
    {"state": "MO", "state_name": "Missouri", "runner_name": "Rachel", "race_type": "Half Marathon", "race_name": "Hospital Hill Run", "race_date": "2026-05-16", "finish_time": "2:09:57", "city": "Kansas City", "notes": "", "status": "Completed"},
]

DISTANCE_MILES = {
    "5K": 3.10686,
    "10K": 6.21371,
    "10 Mile": 10,
    "Half Marathon": 13.1094,
}

ALL_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"), ("CA", "California"),
    ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"), ("FL", "Florida"), ("GA", "Georgia"),
    ("HI", "Hawaii"), ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
    ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"), ("MO", "Missouri"),
    ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"),
    ("NM", "New Mexico"), ("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"),
    ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"), ("VT", "Vermont"),
    ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
]

REQUIRED_COLUMNS = ["state", "state_name", "runner_name", "race_type", "race_name", "race_date", "finish_time", "city", "notes", "status"]
# Carried alongside REQUIRED_COLUMNS so edit/delete can address a specific
# stored row and API-imported rows stay distinguishable from manual ones.
# Not part of REQUIRED_COLUMNS: they are DB bookkeeping, not fields the CSV
# template/upload validation should require.
PASSTHROUGH_COLUMNS = ["id", "source"]
VALID_RACE_TYPES = ["5K", "10K", "10 Mile", "Half Marathon"]
VALID_STATUSES = ["Completed", "Registered", "Interested", "Available for Signup", "Blank"]
USER_ENTRY_STATUSES = ["Completed", "Registered", "Interested", "Blank"]
COPY_TARGET_STATUSES = ["Blank", "Interested", "Registered"]
VALID_STATE_CODES = {code for code, _ in ALL_STATES}
STATE_NAME_LOOKUP = {code: name for code, name in ALL_STATES}
STATE_CODE_LOOKUP = {name: code for code, name in ALL_STATES}
STATUS_COLOR_VALUE = {"Empty": 0, "Available for Signup": 1, "Interested": 2, "Registered": 3, "Completed": 4}
STATUS_COLOR_SCALE = [[0.00, "#f1f5f9"], [0.25, "#eeeeee"], [0.50, "#d9ead3"], [0.75, "#fce5cd"], [1.00, "#6fa8dc"]]


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def build_template_df():
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def normalize_status(value):
    if pd.isna(value) or str(value).strip() == "":
        return "Blank"
    raw = str(value).strip()
    status_lookup = {status.lower(): status for status in VALID_STATUSES}
    cleaned = status_lookup.get(raw.lower())
    return cleaned if cleaned else "Blank"


def normalize_source_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "Blank" if col == "status" else ""
    for col in PASSTHROUGH_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[REQUIRED_COLUMNS + PASSTHROUGH_COLUMNS].dropna(subset=REQUIRED_COLUMNS, how="all").copy()
    df["id"] = df["id"].fillna("").astype(str).str.strip()
    df["source"] = df["source"].fillna("").astype(str).str.strip()
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    df["state_name"] = df["state"].map(STATE_NAME_LOOKUP).fillna(df["state_name"])
    df["runner_name"] = df["runner_name"].fillna("").astype(str).str.strip()
    df["race_type"] = df["race_type"].fillna("").astype(str).str.strip()
    df["race_name"] = df["race_name"].fillna("").astype(str).str.strip()
    df["race_date"] = df["race_date"].fillna("").astype(str).str.strip()
    df["finish_time"] = df["finish_time"].fillna("").astype(str).str.strip()
    df["city"] = df["city"].fillna("").astype(str).str.strip()
    df["notes"] = df["notes"].fillna("").astype(str).str.strip()
    df["status"] = df["status"].apply(normalize_status)
    return df


def time_to_seconds(time_str: str) -> int:
    if time_str is None or str(time_str).strip() == "":
        raise ValueError("Finish time is required for completed races.")
    parts = [int(p) for p in str(time_str).split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid time format: {time_str}")
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_pace(total_seconds: float, miles: float) -> str:
    if pd.isna(total_seconds) or pd.isna(miles) or miles == 0:
        return ""
    pace_seconds = round(float(total_seconds) / float(miles))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d} /mi"


def validate_uploaded_csv(df: pd.DataFrame):
    errors = []
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {', '.join(missing_cols)}")
        return errors

    check_df = df.dropna(how="all").copy()
    check_df["state"] = check_df["state"].astype(str).str.strip().str.upper()
    check_df["race_type"] = check_df["race_type"].astype(str).str.strip()
    check_df["status"] = check_df["status"].apply(normalize_status)

    invalid_states = sorted(set(check_df.loc[~check_df["state"].isin(VALID_STATE_CODES), "state"].dropna().astype(str)))
    if invalid_states:
        errors.append(f"Invalid state codes: {', '.join(invalid_states)}")

    invalid_race_types = sorted(set(check_df.loc[~check_df["race_type"].isin(VALID_RACE_TYPES), "race_type"].dropna().astype(str)))
    if invalid_race_types:
        errors.append(f"Invalid race types: {', '.join(invalid_race_types)}")

    try:
        pd.to_datetime(check_df["race_date"], errors="raise")
    except Exception:
        errors.append("One or more race_date values are invalid. Use YYYY-MM-DD or a standard date format.")

    completed_rows = check_df[check_df["status"] == "Completed"].copy()
    for i, value in completed_rows["finish_time"].items():
        try:
            time_to_seconds(str(value))
        except Exception:
            errors.append(f"Invalid finish_time on row {i + 1}: {value}")

    return errors


def prepare_race_df(source_df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_source_df(source_df)
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["distance_miles"] = df["race_type"].map(DISTANCE_MILES)
    df["finish_seconds"] = pd.NA

    completed_mask = df["status"] == "Completed"
    if completed_mask.any():
        df.loc[completed_mask, "finish_seconds"] = df.loc[completed_mask, "finish_time"].astype(str).apply(time_to_seconds)

    df["avg_mile_pace"] = df.apply(lambda row: seconds_to_pace(row["finish_seconds"], row["distance_miles"]), axis=1)
    df["race_date_display"] = df["race_date"].dt.strftime("%Y-%m-%d")
    df["race_year"] = df["race_date"].dt.year
    return df


def prepare_map_df(race_df: pd.DataFrame) -> pd.DataFrame:
    states_df = pd.DataFrame(ALL_STATES, columns=["state", "state_name"])
    if race_df.empty:
        states_df["total_races"] = 0
        states_df["completed_races"] = 0
        states_df["registered_races"] = 0
        states_df["interested_races"] = 0
        states_df["available_signup_races"] = 0
        states_df["unique_runners"] = 0
        states_df["map_status"] = "Empty"
        states_df["color_value"] = 0
        return states_df

    summary = (
        race_df.groupby(["state", "state_name"])
        .agg(
            total_races=("race_name", "count"),
            completed_races=("status", lambda s: (s == "Completed").sum()),
            registered_races=("status", lambda s: (s == "Registered").sum()),
            interested_races=("status", lambda s: (s == "Interested").sum()),
            available_signup_races=("status", lambda s: (s == "Available for Signup").sum()),
            unique_runners=("runner_name", "nunique"),
        )
        .reset_index()
    )

    map_df = states_df.merge(summary, on=["state", "state_name"], how="left")
    for col in ["total_races", "completed_races", "registered_races", "interested_races", "available_signup_races", "unique_runners"]:
        map_df[col] = map_df[col].fillna(0).astype(int)

    def status_label(row) -> str:
        if row["completed_races"] > 0:
            return "Completed"
        if row["registered_races"] > 0:
            return "Registered"
        if row["interested_races"] > 0:
            return "Interested"
        if row["available_signup_races"] > 0:
            return "Available for Signup"
        return "Empty"

    map_df["map_status"] = map_df.apply(status_label, axis=1)
    map_df["color_value"] = map_df["map_status"].map(STATUS_COLOR_VALUE)
    return map_df


def best_time_for_group(group: pd.DataFrame) -> str:
    completed = group[group["status"] == "Completed"].copy()
    if completed.empty:
        return "No completed races yet"
    idx = completed["finish_seconds"].idxmin()
    row = completed.loc[idx]
    return f"{row['runner_name']} - {row['finish_time']} ({row['race_type']})"


def refresh_source_data_from_storage():
    st.session_state.source_data = normalize_source_df(pd.DataFrame(storage.load_races(USER_ID)))


def add_race_entry(entry: dict, source: str = storage.SOURCE_MANUAL):
    storage.save_race(USER_ID, {**entry, "source": source})
    refresh_source_data_from_storage()


def update_race_entry(race_id: str, entry: dict):
    storage.update_race(USER_ID, race_id, entry)
    refresh_source_data_from_storage()


def delete_race_entry(race_id: str):
    storage.delete_race(USER_ID, race_id)
    refresh_source_data_from_storage()


def display_race_table(df: pd.DataFrame):
    if df.empty:
        st.info("No matching race entries.")
        return

    display_df = df[
        ["status", "state", "state_name", "runner_name", "race_type", "race_name", "city", "race_date_display", "finish_time", "avg_mile_pace", "notes"]
    ].rename(
        columns={
            "status": "Status",
            "state": "State",
            "state_name": "State Name",
            "runner_name": "Runner",
            "race_type": "Race Type",
            "race_name": "Race Name",
            "city": "City",
            "race_date_display": "Date",
            "finish_time": "Finish Time",
            "avg_mile_pace": "Avg Mile Pace",
            "notes": "Notes",
        }
    )
    st.dataframe(display_df, width='stretch', hide_index=True)


def detect_race_type_from_events(events) -> str:
    """Try to classify a race as one of the existing RunTracker race types."""
    event_text = " ".join(
        str(event.get("event", event).get("name", ""))
        for event in events or []
    ).lower()

    if "half" in event_text or "13.1" in event_text:
        return "Half Marathon"
    if "10 mile" in event_text or "10-mile" in event_text or "10miler" in event_text:
        return "10 Mile"
    if "10k" in event_text or "10 k" in event_text:
        return "10K"
    if "5k" in event_text or "5 k" in event_text:
        return "5K"

    return "Half Marathon"


def next_event_date_in_window(events, start_date: date, end_date: date) -> str:
    """Earliest event date inside [start_date, end_date], as YYYY-MM-DD, else ""."""
    candidates = []
    for event in events or []:
        event = event.get("event", event)
        parsed = rsu.parse_event_date(event.get("start_time"))
        if not parsed:
            continue
        if start_date.isoformat() <= parsed <= end_date.isoformat():
            candidates.append(parsed)
    return min(candidates) if candidates else ""


def fetch_runsignup_future_races_for_state(state_code: str) -> pd.DataFrame:
    """Pull 12 months of future RunSignUp races for one state and shape them like RunTracker rows."""
    # Credentials are optional here -- /rest/races answers unauthenticated. We
    # still send them when present, since an affiliate key is what earns the
    # registration commission and may lift rate limits.
    api_key = get_secret("RUNSIGNUP_API_KEY")
    api_secret = get_secret("RUNSIGNUP_API_SECRET")
    affiliate_token = get_secret("RUNSIGNUP_AFFILIATE_TOKEN")

    start_date = date.today()
    end_date = start_date + timedelta(days=365)
    rows = []

    for page in range(1, 6):
        params = {
            "format": "json",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "state": state_code,
            "events": "T",
            "page": page,
            "results_per_page": 1000,
            "sort": "date ASC",
        }
        if api_key and api_secret:
            params["api_key"] = api_key
            params["api_secret"] = api_secret

        # FIX: this constant is now defined at the top of the file.
        response = requests.get(RUNSIGNUP_API_URL, params=params, timeout=30)

        if response.status_code != 200:
            raise RuntimeError(f"RunSignUp API error for {state_code}: {response.text[:2000]}")

        data = response.json()
        races = data.get("races", [])
        if not races:
            break

        for item in races:
            race = item.get("race", item)
            address = race.get("address") or {}
            events = race.get("events") or []
            race_url = race.get("url") or race.get("external_race_url") or ""

            # next_date is unreliable -- it comes back null or stale on series and
            # membership listings, which is why this overlay used to show blank and
            # past-dated rows. Prefer the first event that actually falls in our
            # window, and skip the listing entirely if none does.
            race_date = next_event_date_in_window(events, start_date, end_date)
            if not race_date:
                continue

            rows.append(
                {
                    "state": state_code,
                    "state_name": STATE_NAME_LOOKUP.get(state_code, state_code),
                    "runner_name": "Future Races",
                    "race_type": detect_race_type_from_events(events),
                    "race_name": race.get("name", ""),
                    "race_date": race_date,
                    "finish_time": "",
                    "city": address.get("city", ""),
                    "notes": rsu.affiliate_race_url(race_url, affiliate_token),
                    "status": "Available for Signup",
                }
            )

        if len(races) < 1000:
            break

    if not rows:
        return normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))

    df = normalize_source_df(pd.DataFrame(rows))
    df = df.drop_duplicates(subset=["state", "race_name", "race_date", "city"], keep="first")
    return df


# -------------------------------------------------
# Live results (RunSignUp) -- cached, since discovery is one call per event
# -------------------------------------------------
@st.cache_data(ttl=60 * 60, show_spinner=False)
def cached_race_search(name: str, state: str, include_past: bool):
    return rsu.search_races(name, state=state or None, include_past=include_past)


@st.cache_data(ttl=60 * 15, show_spinner=False)
def cached_result_sets(race_id: int, since_date: str, include_virtual: bool, include_older_years: bool = False):
    """Public result sets; short TTL is safe for preliminary race-day data."""
    return rsu.discover_result_sets(
        race_id,
        include_virtual=include_virtual,
        since_date=since_date,
        max_event_days=None if include_older_years else rsu.DEFAULT_DISCOVERY_EVENT_DAYS,
    )


@st.cache_data(ttl=60 * 60, show_spinner=False)
def cached_runner_results(
    race_id: int,
    first_name: str,
    last_name: str,
    since_date: str,
    include_virtual: bool,
    include_older_years: bool = False,
):
    sets = cached_result_sets(race_id, since_date, include_virtual, include_older_years)
    return rsu.find_runner_results(
        race_id,
        first_name=first_name,
        last_name=last_name,
        result_sets=sets,
    )


# -------------------------------------------------
# Name-first Sign Up search (SPR-15/18) -- capped sweep orchestration
# -------------------------------------------------
# PRIVACY: these two functions are the actual cache-key boundary DOB/email
# must never cross. Streamlit cache keys are inspectable, so age
# classification happens in the Sign Up tab, AFTER these calls return, on
# the rows they hand back -- never pass dob/email into either of these.
@st.cache_data(ttl=60 * 30, show_spinner=False)
def cached_candidate_result_sets(states: tuple, start_year: int, end_year: int, max_calls: int, _progress_cb=None):
    scope = results_search.SearchScope(states=tuple(states), start_year=start_year, end_year=end_year, max_calls=max_calls)
    return results_search.candidate_result_sets(scope, progress_cb=_progress_cb)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def cached_sweep_for_runner(
    first_name: str,
    last_name: str,
    states: tuple,
    start_year: int,
    end_year: int,
    max_calls: int,
    resume_cursor: str,
    _progress_cb=None,
):
    scope = results_search.SearchScope(states=tuple(states), start_year=start_year, end_year=end_year, max_calls=max_calls)
    candidates = cached_candidate_result_sets(states, start_year, end_year, max_calls, _progress_cb=_progress_cb)
    return results_search.sweep_for_runner(
        first_name, last_name, candidates, scope, progress_cb=_progress_cb, resume_cursor=resume_cursor
    )


def describe_signup_scope(states, start_year: int, end_year: int, max_calls: int) -> str:
    state_text = ", ".join(sorted(states)) if states else "no states"
    return f"Will search {state_text} for {start_year}–{end_year}, up to {max_calls} API calls."


def render_signup_progress(progress, bar, caption) -> None:
    stage_labels = {
        "races": "Finding races in scope",
        "catalog": "Checking which races have published results",
        "sweep": "Searching results for your name",
        "capped": "Paused at the call cap",
        "done": "Done",
    }
    budget = max(progress.calls_budget, 1)
    fraction = min(1.0, progress.calls_used / budget)
    label = stage_labels.get(progress.stage, progress.stage)
    bar.progress(
        fraction,
        text=f"{label} — {progress.candidates_checked}/{max(progress.candidates_total, 1)} candidates checked",
    )
    caption.caption(f"API calls this step: {progress.calls_used} / {progress.calls_budget}")


def classify_signup_matches(matches: list, dob: date) -> list:
    """Age classification happens here, after the cache boundary above, on
    rows that already left the cache. dob never travels any further than
    this function and the session state it was read from.

    results_search.py's candidates carry "event_date", but
    rsu.result_to_tracker_row (reused by sweep_for_runner) reads
    context.get("date", ""), so every row that comes out of the capped
    sweep currently has an empty race_date. Backfilled here from
    _context["event_date"] rather than touching the frozen SPR-17
    contract; flagged on SPR-18 for the backend owner to fix at the source.
    """
    classified = []
    for row in matches:
        raw = row.get("_raw", {})
        context = row.get("_context", {})
        race_date_str = row.get("race_date") or context.get("event_date", "")
        if not row.get("race_date") and race_date_str:
            row = {**row, "race_date": race_date_str}
        result_age = runner_matching.parse_result_age(raw.get("age"))
        age_on_race_date = None
        try:
            race_date_obj = datetime.strptime(race_date_str, "%Y-%m-%d").date()
            age_on_race_date = runner_matching.age_on_date(dob, race_date_obj)
        except (ValueError, TypeError):
            age_on_race_date = None
        confidence, reason = runner_matching.classify_confidence(age_on_race_date, result_age)
        classified.append({**row, "_confidence": confidence, "_reason": reason, "_result_age": result_age})
    return classified


# -------------------------------------------------
# Persistent, per-user source data
# -------------------------------------------------
# USER_ID is single-tenant for now -- see resolve_user_id() for the auth seam.
USER_ID = resolve_user_id()

if "source_data" not in st.session_state:
    stored_rows = storage.load_races(USER_ID)
    if stored_rows:
        st.session_state.source_data = normalize_source_df(pd.DataFrame(stored_rows))
        st.session_state.needs_onboarding = False
    else:
        st.session_state.source_data = build_template_df()
        # Only prompt once per user: if they are "onboarded" but currently have
        # zero races (e.g. they started empty, or deleted everything), do not
        # nag them with the sample-data prompt again on every refresh.
        st.session_state.needs_onboarding = not storage.is_onboarded(USER_ID)
else:
    st.session_state.source_data = normalize_source_df(st.session_state.source_data)

if "runsignup_future_races" not in st.session_state:
    st.session_state.runsignup_future_races = normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))


# -------------------------------------------------
# First-load onboarding: never silently inject sample data
# -------------------------------------------------
if st.session_state.get("needs_onboarding"):
    st.title("50 States Race Tracker")
    st.subheader("Welcome! No saved races yet for this account.")
    st.caption(
        "Your race data is now stored persistently (it will survive a page refresh), "
        "so let's start it off on the right foot: load the Tom / Rachel / Olivia demo "
        "dataset to explore the app, or start with an empty race list."
    )
    onboard_col1, onboard_col2 = st.columns(2)
    with onboard_col1:
        if st.button("Load Demo Data", type="primary"):
            storage.save_race(USER_ID, [dict(row, source=storage.SOURCE_SAMPLE) for row in SAMPLE_DATA])
            storage.mark_onboarded(USER_ID)
            refresh_source_data_from_storage()
            st.session_state.needs_onboarding = False
            st.rerun()
    with onboard_col2:
        if st.button("Start Empty"):
            storage.mark_onboarded(USER_ID)
            st.session_state.needs_onboarding = False
            st.rerun()
    st.stop()


# -------------------------------------------------
# Header + API prototype controls + filters
# -------------------------------------------------
st.title("50 States Race Tracker")
st.caption("Track completed races, registered future races, and interested future races across the United States.")

with st.sidebar:
    st.title("Filters")
    st.markdown("### Future Race API Test")

    show_api_future_races = st.toggle(
        "Show RunSignUp future races for CO + SD",
        value=False,
        help="Prototype toggle. Pulls 12 months of future RunSignUp races for Colorado and South Dakota and keeps them separate as Available for Signup rows.",
    )

    if st.button("Pull CO + SD Future Races"):
        with st.spinner("Pulling future races from RunSignUp..."):
            try:
                co_df = fetch_runsignup_future_races_for_state("CO")
                sd_df = fetch_runsignup_future_races_for_state("SD")
                st.session_state.runsignup_future_races = normalize_source_df(pd.concat([co_df, sd_df], ignore_index=True))
                st.success(f"Loaded {len(st.session_state.runsignup_future_races):,} future race rows.")
            except Exception as exc:
                st.error("RunSignUp pull failed.")
                st.code(str(exc))

    if not st.session_state.runsignup_future_races.empty:
        st.caption(f"API rows in session: {len(st.session_state.runsignup_future_races):,}")

    if st.button("Clear API Future Races"):
        st.session_state.runsignup_future_races = normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))
        st.rerun()

# Merge local data with API future rows before preparing filters, map, metrics, and graphs.
combined_source_data = st.session_state.source_data.copy()
if show_api_future_races and not st.session_state.runsignup_future_races.empty:
    combined_source_data = normalize_source_df(pd.concat([combined_source_data, st.session_state.runsignup_future_races], ignore_index=True))

race_df = prepare_race_df(combined_source_data)

with st.sidebar:
    st.markdown("---")
    runner_options = sorted(race_df["runner_name"].dropna().unique())
    race_type_options = sorted(race_df["race_type"].dropna().unique())

    runner_filter = st.multiselect("Runner", options=runner_options, default=runner_options)
    race_type_filter = st.multiselect("Race Type", options=race_type_options, default=race_type_options)
    status_filter = st.multiselect("Status", options=VALID_STATUSES, default=VALID_STATUSES)

filtered_race_df = race_df[
    race_df["runner_name"].isin(runner_filter)
    & race_df["race_type"].isin(race_type_filter)
    & race_df["status"].isin(status_filter)
].copy()

filtered_map_df = prepare_map_df(filtered_race_df)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("States Completed", int((filtered_map_df["map_status"] == "Completed").sum()))
col2.metric("States Registered", int((filtered_map_df["map_status"] == "Registered").sum()))
col3.metric("States Interested", int((filtered_map_df["map_status"] == "Interested").sum()))
col4.metric("States Available", int((filtered_map_df["map_status"] == "Available for Signup").sum()))
col5.metric("Total Entries", len(filtered_race_df))

map_page, graphs_page, signup_page, discovery_page, manage_page = st.tabs(
    ["🗺️ Map", "📊 Graphs", "Sign Up", "Discovery Review", "🛠️ Data Management"]
)


# -------------------------------------------------
# Page 1: Map
# -------------------------------------------------
with map_page:
    st.subheader("US Map")
    st.caption("Status priority: Completed beats Registered, Registered beats Interested, then Available for Signup, and empty states stay blank.")

    if show_api_future_races:
        st.info("RunSignUp future race overlay is ON. CO + SD API rows are included as Available for Signup if you have clicked the sidebar pull button.")

    if not st.session_state.runsignup_future_races.empty:
        with st.expander("Preview RunSignUp API future rows"):
            preview_df = prepare_race_df(st.session_state.runsignup_future_races)
            display_race_table(preview_df.sort_values(["state", "race_date", "race_name"]))

    fig = px.choropleth(
        filtered_map_df,
        locations="state",
        locationmode="USA-states",
        color="color_value",
        scope="usa",
        hover_name="state_name",
        hover_data={
            "state": False,
            "color_value": False,
            "map_status": True,
            "total_races": True,
            "completed_races": True,
            "registered_races": True,
            "interested_races": True,
            "available_signup_races": True,
        },
        color_continuous_scale=STATUS_COLOR_SCALE,
        range_color=(0, 4),
    )

    fig.update_traces(marker_line_color="white", marker_line_width=1)

    # -------------------------------------------------
    # Main map mobile tuning
    # -------------------------------------------------
    # projection_scale:
    # Higher = zooms the US map larger.
    # Try 1.10 to 1.35. If Alaska/Hawaii or edges feel cramped, lower it.
    #
    # height:
    # Higher = gives the map more vertical space, especially helpful on mobile.
    # Try 560 to 700.
    #
    # coloraxis_colorbar:
    # This is the map key/legend. It is horizontal below the map so it does
    # not steal right-side width from the US map on mobile.
    #
    # y:
    # Controls how far below the map the legend sits.
    # Less negative, like -0.03, pulls it closer to the map.
    #
    # len:
    # Controls legend width as a percent of the chart width.
    # Smaller, like 0.60 to 0.70, takes less horizontal space.
    #
    # thickness:
    # Controls the height/thickness of the legend bar.
    # Smaller, like 7 to 9, is more compact on mobile.
    fig.update_geos(
        scope="usa",
        visible=False,
        projection_scale=1.1,
        center={"lat": 38.5, "lon": -96},
    )
    fig.update_layout(
        height=800,
        autosize=True,
        margin=dict(l=0, r=0, t=0, b=0),
        coloraxis_colorbar=dict(
            title="",
            orientation="h",
            x=0.5,
            xanchor="center",
            y=-0.03,
            len=0.65,
            thickness=9,
            tickvals=[0, 1, 2, 3, 4],
            ticktext=["Empty", "Available", "Interested", "Registered", "Completed"],
        ),
    )

    selected = st.plotly_chart(fig, width='stretch', on_select="rerun", selection_mode="points")
    st.caption("The legend was moved below the map and made horizontal so the US map has more room on mobile.")

    selected_state = None
    if selected and selected.get("selection") and selected["selection"].get("points"):
        selected_state = selected["selection"]["points"][0].get("location")

    st.markdown("### State Details")
    state_options = ["Select a state..."] + [name for _, name in ALL_STATES]
    state_name_to_code = {name: code for code, name in ALL_STATES}
    code_to_state_name = {code: name for code, name in ALL_STATES}

    default_index = 0
    if selected_state:
        default_state_name = code_to_state_name.get(selected_state)
        if default_state_name in state_options:
            default_index = state_options.index(default_state_name)

    chosen_state_name = st.selectbox("Choose a state", state_options, index=default_index)
    if chosen_state_name != "Select a state...":
        selected_state = state_name_to_code[chosen_state_name]

    if selected_state:
        state_runs = filtered_race_df[filtered_race_df["state"] == selected_state].sort_values(["race_date", "runner_name"], ascending=[False, True])
        st.write(f"**{code_to_state_name[selected_state]}**")
        if state_runs.empty:
            st.info("No matching race data for this state under the current filters.")
        else:
            s1, s2, s3, s4, s5 = st.columns(5)
            s1.metric("Entries", len(state_runs))
            s2.metric("Completed", int((state_runs["status"] == "Completed").sum()))
            s3.metric("Planned", int(state_runs["status"].isin(["Registered", "Interested"]).sum()))
            s4.metric("Available", int((state_runs["status"] == "Available for Signup").sum()))
            s5.metric("Best Time", best_time_for_group(state_runs))
            display_race_table(state_runs)
    else:
        st.info("Click a state on the map or choose one from the dropdown to view race details.")


# -------------------------------------------------
# Page 2: Non-map graphs
# -------------------------------------------------
with graphs_page:
    st.subheader("Race Charts")

    if filtered_race_df.empty:
        st.info("No data available for the selected filters.")
    else:
        completed_df = filtered_race_df[filtered_race_df["status"] == "Completed"].copy()
        future_df = filtered_race_df[filtered_race_df["status"].isin(["Registered", "Interested", "Available for Signup"])].copy()

        st.markdown("#### Entries by Status")
        status_counts = filtered_race_df.groupby("status").size().reset_index(name="count")
        status_fig = px.bar(status_counts, x="status", y="count", text="count")
        status_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Status", yaxis_title="Entries")
        st.plotly_chart(status_fig, width='stretch')

        st.markdown("#### Race Types")
        race_type_counts = filtered_race_df.groupby("race_type").size().reset_index(name="count")
        race_type_fig = px.bar(race_type_counts, x="race_type", y="count", text="count")
        race_type_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Race Type", yaxis_title="Entries")
        st.plotly_chart(race_type_fig, width='stretch')

        st.markdown("#### Completed Races by Year")
        if completed_df.empty:
            st.info("No completed races available for this chart.")
        else:
            yearly_counts = completed_df.groupby("race_year").size().reset_index(name="count")
            yearly_fig = px.bar(yearly_counts, x="race_year", y="count", text="count")
            yearly_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Year", yaxis_title="Completed Races")
            st.plotly_chart(yearly_fig, width='stretch')

        st.markdown("#### Upcoming Registered / Interested / Available Races")
        today_ts = pd.Timestamp(date.today())
        upcoming_df = future_df[future_df["race_date"] >= today_ts].sort_values("race_date")
        if upcoming_df.empty:
            st.info("No upcoming registered, interested, or available signup races found.")
        else:
            upcoming_display = upcoming_df[
                ["status", "runner_name", "race_type", "race_name", "city", "state", "race_date_display", "notes"]
            ].rename(
                columns={
                    "status": "Status",
                    "runner_name": "Runner",
                    "race_type": "Race Type",
                    "race_name": "Race Name",
                    "city": "City",
                    "state": "State",
                    "race_date_display": "Date",
                    "notes": "Notes / URL",
                }
            )
            st.dataframe(upcoming_display, width='stretch', hide_index=True)

        st.markdown("#### All Race Entries")
        display_race_table(filtered_race_df.sort_values(["race_date", "runner_name"], ascending=[False, True]))


# -------------------------------------------------
# Page 3: Name-first Sign Up results search (SPR-15/18)
# -------------------------------------------------
with signup_page:
    st.subheader("Sign Up")
    st.caption(
        "Enter your name and date of birth to find your race history across RunSignUp, instead of "
        "searching one race at a time."
    )

    for key, default in (
        ("signup_matches", []),
        ("signup_resume_cursor", ""),
        ("signup_capped", False),
        ("signup_candidates_checked", 0),
        ("signup_candidates_total", 0),
        ("signup_confirmed_keys", set()),
        ("signup_rejected_keys", set()),
        ("signup_scope", None),
        ("signup_error", ""),
    ):
        st.session_state.setdefault(key, default)

    su1, su2, su3 = st.columns(3)
    with su1:
        signup_first_name = st.text_input("First name", key="signup_first_name")
    with su2:
        signup_last_name = st.text_input("Last name", key="signup_last_name")
    with su3:
        signup_dob = st.date_input(
            "Date of birth",
            value=None,
            min_value=SIGNUP_MIN_DOB,
            max_value=date.today(),
            key="signup_dob_input",
        )

    signup_email = st.text_input(
        "Email (optional)",
        key="signup_email",
        help=(
            "Placeholder only -- this is never sent to RunSignUp, never stored, and never leaves "
            "this browser session."
        ),
    )
    signup_email_valid = True
    if signup_email.strip():
        signup_email_valid = runner_matching.valid_email(signup_email)
        if not signup_email_valid:
            st.caption(":red[That doesn't look like a valid email format.]")

    tracker_states = sorted(
        {s for s in st.session_state.source_data.get("state", pd.Series(dtype=str)).tolist() if s}
    )
    default_states = tracker_states or [PILOT_STATE]
    current_year = date.today().year

    with st.expander("Optional search hints", expanded=False):
        signup_states = st.multiselect(
            "States",
            options=[code for code, _ in ALL_STATES],
            default=default_states,
            key="signup_states",
        )
        signup_year_range = st.slider(
            "Years",
            min_value=2010,
            max_value=current_year,
            value=(max(2010, current_year - (SIGNUP_DEFAULT_LOOKBACK_YEARS - 1)), current_year),
            key="signup_year_range",
        )

    resolved_states = tuple(sorted(signup_states or default_states))
    resolved_start_year, resolved_end_year = signup_year_range
    st.info(describe_signup_scope(resolved_states, resolved_start_year, resolved_end_year, SIGNUP_DEFAULT_MAX_CALLS))

    find_clicked = st.button("Find My Races", type="primary")

    run_token = None
    if find_clicked:
        if not signup_first_name.strip() or not signup_last_name.strip() or signup_dob is None:
            st.session_state.signup_error = "First name, last name, and date of birth are all required."
        elif signup_email.strip() and not signup_email_valid:
            st.session_state.signup_error = "Fix the email format (or clear it) before searching."
        else:
            st.session_state.signup_error = ""
            st.session_state.signup_matches = []
            st.session_state.signup_resume_cursor = ""
            st.session_state.signup_capped = False
            st.session_state.signup_candidates_checked = 0
            st.session_state.signup_candidates_total = 0
            st.session_state.signup_confirmed_keys = set()
            st.session_state.signup_rejected_keys = set()
            st.session_state.signup_scope = {
                "states": resolved_states,
                "start_year": resolved_start_year,
                "end_year": resolved_end_year,
                "max_calls": SIGNUP_DEFAULT_MAX_CALLS,
            }
            run_token = "search"

    if st.session_state.signup_error:
        st.error(st.session_state.signup_error)

    if st.session_state.signup_capped and st.session_state.signup_scope:
        st.warning(
            f"Stopped at the {st.session_state.signup_scope['max_calls']}-call cap; "
            f"{st.session_state.signup_candidates_checked} of {st.session_state.signup_candidates_total} "
            "candidates checked."
        )
        if st.button("Continue searching"):
            run_token = "continue"

    if run_token and st.session_state.signup_scope:
        scope = st.session_state.signup_scope
        progress_bar = st.progress(0.0, text="Starting search...")
        progress_caption = st.empty()

        def _on_progress(progress, _bar=progress_bar, _caption=progress_caption):
            render_signup_progress(progress, _bar, _caption)

        try:
            with st.spinner("Searching RunSignUp..."):
                new_matches, sweep_progress = cached_sweep_for_runner(
                    signup_first_name.strip(),
                    signup_last_name.strip(),
                    scope["states"],
                    scope["start_year"],
                    scope["end_year"],
                    scope["max_calls"],
                    st.session_state.signup_resume_cursor,
                    _progress_cb=_on_progress,
                )
            st.session_state.signup_matches = st.session_state.signup_matches + new_matches
            st.session_state.signup_resume_cursor = sweep_progress.cursor
            st.session_state.signup_capped = sweep_progress.capped
            st.session_state.signup_candidates_checked = sweep_progress.candidates_checked
            st.session_state.signup_candidates_total = sweep_progress.candidates_total
        except Exception as exc:
            st.session_state.signup_error = f"Search failed: {exc}"
            st.error(st.session_state.signup_error)

    if st.session_state.signup_matches:
        classified = classify_signup_matches(st.session_state.signup_matches, signup_dob)
        high = [r for r in classified if r["_confidence"] == runner_matching.HIGH]
        possible = [r for r in classified if r["_confidence"] == runner_matching.POSSIBLE]
        rejected = [r for r in classified if r["_confidence"] == runner_matching.REJECTED]

        affiliate_token = get_secret("RUNSIGNUP_AFFILIATE_TOKEN")

        def _render_match_row(bucket_key: str, idx: int, row: dict, show_reason: bool):
            raw = row.get("_raw", {})
            context = row.get("_context", {})
            race_url = rsu.affiliate_race_url(context.get("url", ""), affiliate_token)
            key_base = (
                f"{bucket_key}_{idx}_{context.get('race_id')}_{context.get('event_id')}_"
                f"{context.get('result_set_id')}_{raw.get('bib', '')}"
            )
            already_confirmed = key_base in st.session_state.signup_confirmed_keys
            already_rejected = key_base in st.session_state.signup_rejected_keys

            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
            race_label = row.get("race_name", "")
            if race_url:
                c1.markdown(f"**[{race_label}]({race_url})**")
            else:
                c1.markdown(f"**{race_label}**")
            c2.write(row.get("race_date", ""))
            c3.write(row.get("race_type", "") or "Unknown type")
            c4.write(row.get("finish_time", "") or "—")
            age_display = row["_result_age"] if row["_result_age"] is not None else "—"
            c5.write(f"Age: {age_display}")
            if show_reason:
                st.caption(row["_reason"])

            if already_confirmed:
                st.caption("Added to your race list.")
            elif already_rejected:
                st.caption("Marked as not you.")
            else:
                b1, b2 = st.columns(2)
                with b1:
                    if st.button("This is me", key=f"confirm_{key_base}"):
                        clean_row = {k: v for k, v in row.items() if not k.startswith("_")}
                        clean_row["state_name"] = STATE_NAME_LOOKUP.get(
                            clean_row.get("state", ""), clean_row.get("state_name", "")
                        )
                        add_race_entry(clean_row, source=storage.SOURCE_API)
                        st.session_state.signup_confirmed_keys.add(key_base)
                        st.rerun()
                with b2:
                    if st.button("Not me", key=f"reject_{key_base}"):
                        st.session_state.signup_rejected_keys.add(key_base)
                        st.rerun()
            st.divider()

        st.markdown(f"#### High confidence ({len(high)})")
        if not high:
            st.caption("No high-confidence matches yet.")
        for idx, row in enumerate(high):
            _render_match_row("high", idx, row, show_reason=False)

        st.markdown(f"#### Possible ({len(possible)})")
        if not possible:
            st.caption("No possible matches yet.")
        for idx, row in enumerate(possible):
            _render_match_row("possible", idx, row, show_reason=True)

        with st.expander(f"Rejected ({len(rejected)})", expanded=False):
            for idx, row in enumerate(rejected):
                _render_match_row("rejected", idx, row, show_reason=True)
    elif run_token:
        st.info("No matching results found in this scope yet.")

    with st.expander("Advanced: search one specific race", expanded=False):
        st.caption(
            "Pulls real finisher results straight from RunSignUp for a single race you already know, "
            "instead of sweeping a whole state/year scope above. Public result sets need no API key."
        )
        search_col, state_col = st.columns([3, 1])
        with search_col:
            race_search_name = st.text_input("Race name", placeholder="Lincoln Half Marathon")
        with state_col:
            race_search_state = st.text_input("State", max_chars=2, placeholder="NE")
        race_search_include_past = st.checkbox("Include past race listings", value=True)
        if st.button("Search Races"):
            if not race_search_name.strip():
                st.error("Enter a race name to search.")
            else:
                try:
                    st.session_state.race_search_results = cached_race_search(
                        race_search_name.strip(), race_search_state.strip().upper(), race_search_include_past
                    )
                    st.session_state.race_search_error = ""
                except Exception as exc:
                    st.session_state.race_search_results = []
                    st.session_state.race_search_error = str(exc)

        if st.session_state.get("race_search_error"):
            st.error("RunSignUp race search failed.")
            st.code(st.session_state.race_search_error)

        race_matches = st.session_state.get("race_search_results") or []
        selected_race_id = None
        if race_matches:
            race_labels = [
                f"{race['name']} — {race['city']}, {race['state']} — {race['next_date'] or 'date unavailable'} "
                f"(ID {race['race_id']})"
                for race in race_matches
            ]
            selected_label = st.selectbox("Matching race", race_labels)
            selected_race = race_matches[race_labels.index(selected_label)]
            selected_race_id = selected_race["race_id"]
            if selected_race["url"]:
                st.link_button("Open on RunSignUp", selected_race["url"])
        elif "race_search_results" in st.session_state and not st.session_state.get("race_search_error"):
            st.info("No matching races found.")

        r1, r2, r3 = st.columns([2, 2, 2])
        with r1:
            lookup_first_name = st.text_input("First Name", key="lookup_first_name", placeholder="Thomas")
        with r2:
            lookup_last_name = st.text_input("Last Name", key="lookup_last_name", placeholder="Springhower")
        with r3:
            if selected_race_id:
                lookup_race_id = int(selected_race_id)
                st.metric("Selected RunSignUp Race ID", lookup_race_id)
            else:
                lookup_race_id = st.number_input(
                    "RunSignUp Race ID",
                    min_value=1,
                    value=PILOT_RACE_ID,
                    step=1,
                    help=f"Defaults to {PILOT_RACE_LABEL}. Race ID is the number in the RunSignUp race URL.",
                )

        o1, o2, o3 = st.columns(3)
        with o1:
            lookup_since_year = st.number_input(
                "Only races since (year)", min_value=2010, max_value=date.today().year, value=2024, step=1,
                help="Discovery costs one API call per event, so narrowing the years keeps the lookup fast.",
            )
        with o2:
            lookup_include_virtual = st.checkbox("Include virtual events", value=False)
        with o3:
            lookup_include_older = st.checkbox(
                "Include older years",
                value=False,
                help=f"Off scans only the {rsu.DEFAULT_DISCOVERY_EVENT_DAYS} most recent race days.",
            )

        if st.button("Search RunSignUp Results", type="primary"):
            if not lookup_first_name.strip() and not lookup_last_name.strip():
                st.error("Enter a first name, a last name, or both.")
            else:
                with st.spinner("Searching RunSignUp result sets..."):
                    try:
                        st.session_state.results_lookup = cached_runner_results(
                            int(lookup_race_id),
                            lookup_first_name.strip(),
                            lookup_last_name.strip(),
                            f"{int(lookup_since_year)}-01-01",
                            lookup_include_virtual,
                            lookup_include_older,
                        )
                        st.session_state.results_lookup_error = ""
                    except Exception as exc:
                        st.session_state.results_lookup = []
                        st.session_state.results_lookup_error = str(exc)

        if st.session_state.get("results_lookup_error"):
            st.error("RunSignUp results lookup failed.")
            st.code(st.session_state.results_lookup_error)

        found_rows = st.session_state.get("results_lookup")
        if found_rows is not None and not st.session_state.get("results_lookup_error"):
            if not found_rows:
                st.warning(
                    "No matching results found. Check the spelling, widen the year range, or confirm the runner "
                    "finished this race. RunSignUp matches on the name used at registration."
                )
            else:
                st.success(f"Found {len(found_rows)} result(s).")

                found_display = pd.DataFrame(
                    [
                        {
                            "Date": row["race_date"],
                            "Race": row["race_name"],
                            "Race Type": row["race_type"],
                            "Runner": row["runner_name"],
                            "Finish Time": row["finish_time"],
                            "Place": row["_raw"].get("place", ""),
                            "Age": row["_raw"].get("age", ""),
                            "Pace": row["_raw"].get("pace", ""),
                            "Hometown": ", ".join(
                                part for part in [row["_raw"].get("city", ""), row["_raw"].get("state", "")] if part
                            ),
                        }
                        for row in found_rows
                    ]
                )
                st.dataframe(found_display, width='stretch', hide_index=True)

                import_labels = [
                    f"{idx}: {row['race_date']} | {row['race_type']} | {row['race_name']} | {row['finish_time']}"
                    for idx, row in enumerate(found_rows)
                ]
                chosen_imports = st.multiselect(
                    "Select results to add to your race list",
                    import_labels,
                    default=import_labels,
                    key="results_import_select",
                )

                if st.button("Add Selected Results to My Race List"):
                    added = 0
                    for label in chosen_imports:
                        row = dict(found_rows[int(label.split(":", 1)[0])])
                        row.pop("_raw", None)
                        if not row.get("race_type"):
                            row["race_type"] = "Half Marathon"
                        add_race_entry(row, source=storage.SOURCE_API)
                        added += 1
                    st.success(f"Added {added} real result(s) from RunSignUp. These replace hand-entered finish times.")
                    st.rerun()

        st.divider()
        st.subheader("Browse a Full Leaderboard")
        st.caption("Useful for sanity-checking a result set, and the basis for future head-to-head comparisons.")

        # Streamlit executes every tab on every rerun, so this discovery call has to
        # stay behind an explicit button -- otherwise each page load fires a burst of
        # RunSignUp requests for visitors who never open this tab.
        if st.button("List Available Result Sets"):
            with st.spinner("Discovering public result sets..."):
                try:
                    st.session_state.available_sets = cached_result_sets(
                        int(lookup_race_id),
                        f"{int(lookup_since_year)}-01-01",
                        lookup_include_virtual,
                        lookup_include_older,
                    )
                    st.session_state.available_sets_error = ""
                except Exception as exc:
                    st.session_state.available_sets = []
                    st.session_state.available_sets_error = str(exc)

        if st.session_state.get("available_sets_error"):
            st.error("Could not list result sets for this race.")
            st.code(st.session_state.available_sets_error)

        available_sets = st.session_state.get("available_sets")
        if available_sets is None:
            st.info("Click **List Available Result Sets** to see which years and distances have published results.")
        elif not available_sets:
            st.info("No public result sets found for this race in the selected year range.")
        else:
            set_labels = [
                f"{idx}: {entry['date']} | {entry['race_type'] or entry['name']} | set {entry['result_set_id']}"
                for idx, entry in enumerate(available_sets)
            ]
            chosen_set_label = st.selectbox("Result set", set_labels, key="leaderboard_set_select")
            chosen_set = available_sets[int(chosen_set_label.split(":", 1)[0])]
            leaderboard_size = st.slider("Rows to show", min_value=10, max_value=200, value=25, step=5)

            if st.button("Load Leaderboard"):
                with st.spinner("Loading results..."):
                    try:
                        leaderboard = rsu.fetch_results(
                            chosen_set["race_id"],
                            chosen_set["event_id"],
                            chosen_set["result_set_id"],
                            results_per_page=int(leaderboard_size),
                            max_pages=1,
                        )
                        st.session_state.leaderboard_rows = leaderboard
                        st.session_state.leaderboard_label = (
                            f"{chosen_set['race_name']} - {chosen_set['name']} ({chosen_set['date']})"
                        )
                    except Exception as exc:
                        st.session_state.leaderboard_rows = []
                        st.error("Leaderboard load failed.")
                        st.code(str(exc))

            if st.session_state.get("leaderboard_rows"):
                st.markdown(f"**{st.session_state.get('leaderboard_label', '')}**")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Place": row.get("place", ""),
                                "Bib": row.get("bib", ""),
                                "Name": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                                "Gender": row.get("gender", ""),
                                "Age": row.get("age", ""),
                                "Hometown": ", ".join(
                                    part for part in [row.get("city", ""), row.get("state", "")] if part
                                ),
                                "Chip Time": row.get("chip_time", ""),
                                "Clock Time": row.get("clock_time", ""),
                                "Pace": row.get("pace", ""),
                            }
                            for row in st.session_state.leaderboard_rows
                        ]
                    ),
                    width='stretch',
                    hide_index=True,
                )


# -------------------------------------------------
# Page 4: Bounded discovery experiment review
# -------------------------------------------------
with discovery_page:
    summary = discovery.EXPERIMENT_SUMMARY
    st.subheader("Bounded RunSignUp Discovery Review")
    st.caption(
        "An anonymized, read-only view of the two-runner experiment. These candidates are not "
        "imported into the tracker and names are intentionally omitted."
    )

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Eligible source rows", f"{summary['eligible_rows']:,}")
    d2.metric("Sampled runners", len(discovery.RUNNERS))
    d3.metric("High-confidence candidates", sum(row["High confidence"] for row in discovery.RUNNERS))
    d4.metric("External requests", summary["external_requests"])

    st.markdown("#### Sample and bounds")
    st.write(
        f"Source: **{summary['source_race']}** on {summary['source_date']}. "
        f"Five-year lookback from {summary['lookback_floor']}; at most "
        f"{summary['candidate_cap_per_runner']} candidate races per runner and "
        f"{summary['race_day_cap']} race days per result-set discovery."
    )
    st.caption("Reproducible sampling seed")
    st.code(summary["sample_seed"], language=None)
    st.dataframe(pd.DataFrame(discovery.RUNNERS), width="stretch", hide_index=True)

    st.markdown("#### Candidate evidence")
    st.dataframe(
        pd.DataFrame(discovery.CANDIDATE_RESULTS), width="stretch", hide_index=True,
        column_config={"Public URL": st.column_config.LinkColumn("RunSignUp evidence", display_text="Open public result")},
    )
    st.info(
        "An exact name is discovery evidence, not identity proof. High confidence requires the exact "
        "case-insensitive full name plus at least two corroborators. Conflicting/name-only rows are rejected."
    )

    with st.expander("Request accounting and experiment limitations"):
        st.dataframe(pd.DataFrame(discovery.REQUEST_COUNTS), width="stretch", hide_index=True)
        st.markdown(
            "- Public RunSignUp pages only; no credentials, contact data, or social enrichment.\n"
            "- Search indexing and the five-race-day cap can miss valid history.\n"
            "- Zero confident matches are a valid outcome.\n"
            "- Candidate history should require user review and must never auto-import."
        )


# -------------------------------------------------
# Page 5: Data management, add, edit, delete
# -------------------------------------------------
with manage_page:
    st.subheader("Data Management")
    st.markdown("Download a blank template, export current data, or upload a CSV to replace the current session data.")

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Download Blank CSV Template",
            data=build_template_df().to_csv(index=False).encode("utf-8"),
            file_name="race_results_template.csv",
            mime="text/csv",
        )
    with c2:
        st.download_button(
            "Download Current Data",
            data=st.session_state.source_data.to_csv(index=False).encode("utf-8"),
            file_name="race_results_current.csv",
            mime="text/csv",
        )

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded_file is not None:
        uploaded_df = pd.read_csv(uploaded_file)
        st.write("Preview of uploaded file:")
        st.dataframe(uploaded_df, width='stretch', hide_index=True)
        errors = validate_uploaded_csv(uploaded_df)
        if errors:
            st.error("CSV validation failed:")
            for err in errors:
                st.write(f"- {err}")
        else:
            st.success("CSV looks valid.")
            if st.button("Replace Current Data With Uploaded CSV"):
                normalized_upload = normalize_source_df(uploaded_df)
                storage.replace_all_races(USER_ID, normalized_upload.to_dict("records"), source=storage.SOURCE_CSV)
                storage.mark_onboarded(USER_ID)
                refresh_source_data_from_storage()
                st.success("Stored data replaced successfully.")
                st.rerun()

    st.divider()
    st.subheader("Copy an API Future Race")
    st.caption("API rows stay separate as Available for Signup. Use this to copy one into your personal race list as Interested or Registered.")

    api_copy_df = st.session_state.runsignup_future_races.copy().reset_index(drop=True)
    if api_copy_df.empty:
        st.info("No API future races loaded yet. Use the sidebar button to pull CO + SD future races first.")
    else:
        api_labels = [
            f"{idx}: {row['race_date']} | {row['state']} | {row['city']} | {row['race_name']}"
            for idx, row in api_copy_df.iterrows()
        ]
        selected_api_label = st.selectbox("Select API Race to Copy", api_labels, key="copy_api_race_select")
        selected_api_index = int(selected_api_label.split(":", 1)[0])
        selected_api_row = api_copy_df.loc[selected_api_index]

        parsed_api_date = pd.to_datetime(selected_api_row["race_date"], errors="coerce")
        default_api_date = parsed_api_date.date() if not pd.isna(parsed_api_date) else date.today()
        all_state_names = [name for _, name in ALL_STATES]
        default_api_state_index = (
            all_state_names.index(selected_api_row["state_name"])
            if selected_api_row["state_name"] in all_state_names
            else 0
        )
        default_api_race_type_index = (
            VALID_RACE_TYPES.index(selected_api_row["race_type"])
            if selected_api_row["race_type"] in VALID_RACE_TYPES
            else 0
        )

        with st.form("copy_api_race_form"):
            c1, c2, c3 = st.columns(3)
            with c1:
                copy_runner_name = st.text_input("Runner Name", value="", key="copy_runner_name")
                copy_race_name = st.text_input("Race Name", value=selected_api_row["race_name"], key="copy_race_name")
                copy_race_date = st.date_input("Race Date", value=default_api_date, key="copy_race_date")
            with c2:
                copy_state_name = st.selectbox("State", all_state_names, index=default_api_state_index, key="copy_state_name")
                copy_city = st.text_input("City", value=selected_api_row["city"], key="copy_city")
                copy_race_type = st.selectbox("Race Type", VALID_RACE_TYPES, index=default_api_race_type_index, key="copy_race_type")
            with c3:
                copy_status = st.selectbox("Status", COPY_TARGET_STATUSES, index=0, key="copy_status")
                copy_finish_time = st.text_input("Finish Time", value="", placeholder="Usually blank for future races", key="copy_finish_time")
                copy_notes = st.text_area("Notes", value=selected_api_row["notes"], height=100, key="copy_notes")

            if st.form_submit_button("Copy to My Race List"):
                if not copy_race_name.strip():
                    st.error("Race Name is required.")
                elif copy_status == "Blank":
                    st.error("Choose Interested or Registered before copying this race into your list.")
                else:
                    copy_state_code = STATE_CODE_LOOKUP[copy_state_name]
                    add_race_entry(
                        {
                            "state": copy_state_code,
                            "state_name": copy_state_name,
                            "runner_name": copy_runner_name,
                            "race_type": copy_race_type,
                            "race_name": copy_race_name,
                            "race_date": copy_race_date.strftime("%Y-%m-%d"),
                            "finish_time": copy_finish_time,
                            "city": copy_city,
                            "notes": copy_notes,
                            "status": copy_status,
                        },
                        source=storage.SOURCE_API,
                    )
                    st.success("API race copied into your personal race list.")
                    st.rerun()

    st.divider()
    st.subheader("Add One Race")
    with st.form("add_race_form", clear_on_submit=True):
        a1, a2, a3 = st.columns(3)
        with a1:
            add_runner_name = st.text_input("Runner Name")
            add_race_name = st.text_input("Race Name")
            add_race_date = st.date_input("Race Date", value=date.today())
        with a2:
            add_state_name = st.selectbox("State", [name for _, name in ALL_STATES])
            add_city = st.text_input("City")
            add_race_type = st.selectbox("Race Type", VALID_RACE_TYPES)
        with a3:
            add_status = st.selectbox("Status", USER_ENTRY_STATUSES)
            add_finish_time = st.text_input("Finish Time", placeholder="Required only for completed races")
            add_notes = st.text_area("Notes", height=100)

        if st.form_submit_button("Add Race"):
            if not add_runner_name.strip() and add_status == "Completed":
                st.error("Runner Name is required for completed races.")
            elif not add_race_name.strip():
                st.error("Race Name is required.")
            elif add_status == "Completed" and not add_finish_time.strip():
                st.error("Finish Time is required for completed races.")
            else:
                if add_status == "Completed":
                    try:
                        time_to_seconds(add_finish_time)
                    except Exception:
                        st.error("Finish Time must use MM:SS or H:MM:SS format.")
                        st.stop()

                state_code = STATE_CODE_LOOKUP[add_state_name]
                add_race_entry(
                    {
                        "state": state_code,
                        "state_name": add_state_name,
                        "runner_name": add_runner_name,
                        "race_type": add_race_type,
                        "race_name": add_race_name,
                        "race_date": add_race_date.strftime("%Y-%m-%d"),
                        "finish_time": add_finish_time,
                        "city": add_city,
                        "notes": add_notes,
                        "status": add_status,
                    }
                )
                st.success("Race added.")
                st.rerun()

    st.divider()
    st.subheader("Edit or Delete an Existing Race")
    editable_df = st.session_state.source_data.copy().reset_index(drop=True)

    if editable_df.empty:
        st.info("No race entries to edit yet.")
    else:
        entry_labels = [
            f"{idx}: {row['race_date']} | {row['state']} | {row['runner_name']} | {row['race_name']} | {row['status']}"
            for idx, row in editable_df.iterrows()
        ]
        selected_label = st.selectbox("Select Entry", entry_labels)
        selected_index = int(selected_label.split(":", 1)[0])
        selected_row = editable_df.loc[selected_index]

        parsed_date = pd.to_datetime(selected_row["race_date"], errors="coerce")
        default_date = parsed_date.date() if not pd.isna(parsed_date) else date.today()
        all_state_names = [name for _, name in ALL_STATES]

        with st.form("edit_race_form"):
            e1, e2, e3 = st.columns(3)
            with e1:
                edit_runner_name = st.text_input("Runner Name", value=selected_row["runner_name"])
                edit_race_name = st.text_input("Race Name", value=selected_row["race_name"])
                edit_race_date = st.date_input("Race Date", value=default_date, key="edit_race_date")
            with e2:
                edit_state_name = st.selectbox(
                    "State",
                    all_state_names,
                    index=all_state_names.index(selected_row["state_name"]) if selected_row["state_name"] in all_state_names else 0,
                )
                edit_city = st.text_input("City", value=selected_row["city"])
                edit_race_type = st.selectbox(
                    "Race Type",
                    VALID_RACE_TYPES,
                    index=VALID_RACE_TYPES.index(selected_row["race_type"]) if selected_row["race_type"] in VALID_RACE_TYPES else 0,
                )
            with e3:
                edit_status = st.selectbox(
                    "Status",
                    USER_ENTRY_STATUSES,
                    index=USER_ENTRY_STATUSES.index(selected_row["status"]) if selected_row["status"] in USER_ENTRY_STATUSES else 0,
                )
                edit_finish_time = st.text_input("Finish Time", value=selected_row["finish_time"])
                edit_notes = st.text_area("Notes", value=selected_row["notes"], height=100)

            if st.form_submit_button("Save Changes"):
                if not edit_runner_name.strip() and edit_status == "Completed":
                    st.error("Runner Name is required for completed races.")
                elif not edit_race_name.strip():
                    st.error("Race Name is required.")
                elif edit_status == "Completed" and not edit_finish_time.strip():
                    st.error("Finish Time is required when converting a race to Completed.")
                else:
                    if edit_status == "Completed":
                        try:
                            time_to_seconds(edit_finish_time)
                        except Exception:
                            st.error("Finish Time must use MM:SS or H:MM:SS format.")
                            st.stop()

                    state_code = STATE_CODE_LOOKUP[edit_state_name]
                    update_race_entry(
                        selected_row["id"],
                        {
                            "state": state_code,
                            "state_name": edit_state_name,
                            "runner_name": edit_runner_name,
                            "race_type": edit_race_type,
                            "race_name": edit_race_name,
                            "race_date": edit_race_date.strftime("%Y-%m-%d"),
                            "finish_time": edit_finish_time,
                            "city": edit_city,
                            "notes": edit_notes,
                            "status": edit_status,
                        },
                    )
                    st.success("Race updated.")
                    st.rerun()

        if st.button("Delete Selected Entry", type="secondary"):
            delete_race_entry(selected_row["id"])
            st.success("Race deleted.")
            st.rerun()

    st.markdown("---")
    st.caption("Next upgrade ideas: real user accounts/auth (see resolve_user_id), scheduled RunSignUp refresh, Excel import, medals/badges, public profiles, and monetized premium plans.")
