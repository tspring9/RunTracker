import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="50 States Race Tracker", layout="wide")

# -------------------------------------------------
# RunSignup API settings
# -------------------------------------------------
RUNSIGNUP_API_URL = "https://api.runsignup.com/rest/races"
RUNSIGNUP_TEST_STATES = ["CO", "SD"]


def get_secret(name: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then environment variables."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


# -------------------------------------------------
# Sample preloaded data
# -------------------------------------------------
SAMPLE_DATA = [
    {"state":"NE","state_name":"Nebraska","runner_name":"Rachel","race_type":"Half Marathon","race_name":"Lincoln Half Marathon","race_date":"2024-05-04","finish_time":"2:06:45","city":"Lincoln","notes":"","status":"Completed"},
    {"state":"TX","state_name":"Texas","runner_name":"Rachel","race_type":"Half Marathon","race_name":"BMW Dallas Half Marathon","race_date":"2024-12-15","finish_time":"2:05:35","city":"Dallas","notes":"","status":"Completed"},
    {"state":"NV","state_name":"Nevada","runner_name":"Rachel","race_type":"Half Marathon","race_name":"Rock 'n' Roll Las Vegas Half Marathon","race_date":"2025-02-23","finish_time":"2:22:16","city":"Las Vegas","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Tom","race_type":"Half Marathon","race_name":"OmaHalf","race_date":"2022-04-16","finish_time":"2:41:00","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Tom","race_type":"Half Marathon","race_name":"Lincoln Half Marathon","race_date":"2024-05-04","finish_time":"1:55:36","city":"Lincoln","notes":"","status":"Completed"},
    {"state":"TX","state_name":"Texas","runner_name":"Tom","race_type":"Half Marathon","race_name":"BMW Dallas Half Marathon","race_date":"2024-12-15","finish_time":"1:54:34","city":"Dallas","notes":"","status":"Completed"},
    {"state":"NV","state_name":"Nevada","runner_name":"Tom","race_type":"Half Marathon","race_name":"Rock 'n' Roll Las Vegas Half Marathon","race_date":"2025-02-23","finish_time":"2:24:04","city":"Las Vegas","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Rachel","race_type":"10 Mile","race_name":"Early Bird Run","race_date":"2026-04-04","finish_time":"1:32:42","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Tom","race_type":"10 Mile","race_name":"Early Bird Run","race_date":"2024-04-06","finish_time":"1:31:21","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Tom","race_type":"10 Mile","race_name":"Early Bird Run","race_date":"2026-04-04","finish_time":"1:28:07","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Rachel","race_type":"5K","race_name":"OmaHalf","race_date":"2022-04-16","finish_time":"0:30:33","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Tom","race_type":"5K","race_name":"Gator Fun Run","race_date":"2026-04-25","finish_time":"0:24:30","city":"Omaha","notes":"","status":"Completed"},
    {"state":"IN","state_name":"Indiana","runner_name":"Tom","race_type":"Half Marathon","race_name":"Indi Mini","race_date":"2026-05-02","finish_time":"1:53:20","city":"Indianapolis","notes":"","status":"Completed"},
    {"state":"IN","state_name":"Indiana","runner_name":"Rachel","race_type":"Half Marathon","race_name":"Indi Mini","race_date":"2026-05-02","finish_time":"2:01:40","city":"Indianapolis","notes":"","status":"Completed"},
    {"state":"IN","state_name":"Indiana","runner_name":"Olivia","race_type":"Half Marathon","race_name":"Indi Mini","race_date":"2026-05-02","finish_time":"2:20:50","city":"Indianapolis","notes":"","status":"Completed"},
    {"state":"IN","state_name":"Indiana","runner_name":"Olivia","race_type":"Half Marathon","race_name":"Indi Mini","race_date":"2019-05-04","finish_time":"2:41:43","city":"Indianapolis","notes":"","status":"Completed"},
    {"state":"TX","state_name":"Texas","runner_name":"Olivia","race_type":"Half Marathon","race_name":"BMW Dallas Half Marathon","race_date":"2025-12-14","finish_time":"2:27:28","city":"Dallas","notes":"","status":"Completed"},
    {"state":"TX","state_name":"Texas","runner_name":"Olivia","race_type":"Half Marathon","race_name":"BMW Dallas Half Marathon","race_date":"2024-12-15","finish_time":"2:18:01","city":"Dallas","notes":"","status":"Completed"},
    {"state":"NE","state_name":"Nebraska","runner_name":"Olivia","race_type":"Half Marathon","race_name":"OmaHalf","race_date":"2022-04-16","finish_time":"2:26:17","city":"Omaha","notes":"","status":"Completed"},
    {"state":"NV","state_name":"Nevada","runner_name":"Olivia","race_type":"Half Marathon","race_name":"Rock 'n' Roll Las Vegas Half Marathon","race_date":"2025-02-23","finish_time":"2:20:47","city":"Las Vegas","notes":"","status":"Completed"},
    {"state":"CO","state_name":"Colorado","runner_name":"","race_type":"Half Marathon","race_name":"All-Out Runapalooza","race_date":"2026-08-08","finish_time":"","city":"Denver","notes":"","status":"Interested"},
    {"state":"MO","state_name":"Missouri","runner_name":"Tom","race_type":"Half Marathon","race_name":"Hospital Hill Run","race_date":"2026-05-16","finish_time":"1:56:24","city":"Kansas City","notes":"","status":"Completed"},
    {"state":"MO","state_name":"Missouri","runner_name":"Rachel","race_type":"Half Marathon","race_name":"Hospital Hill Run","race_date":"2026-05-16","finish_time":"2:09:57","city":"Kansas City","notes":"","status":"Completed"},
]

DISTANCE_MILES = {"5K": 3.10686, "10K": 6.21371, "10 Mile": 10, "Half Marathon": 13.1094}
ALL_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"),
    ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"),
    ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"),
    ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"),
    ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"),
    ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
    ("WI", "Wisconsin"), ("WY", "Wyoming"),
]

REQUIRED_COLUMNS = ["state", "state_name", "runner_name", "race_type", "race_name", "race_date", "finish_time", "city", "notes", "status"]
VALID_RACE_TYPES = ["5K", "10K", "10 Mile", "Half Marathon"]
VALID_STATUSES = ["Completed", "Registered", "Interested"]
VALID_STATE_CODES = {code for code, _ in ALL_STATES}
STATE_NAME_LOOKUP = {code: name for code, name in ALL_STATES}
STATE_CODE_LOOKUP = {name: code for code, name in ALL_STATES}
STATUS_COLOR_VALUE = {"Empty": 0, "Interested": 1, "Registered": 2, "Completed": 3}
STATUS_COLOR_SCALE = [[0.00, "#f1f5f9"], [0.33, "#d9ead3"], [0.66, "#fce5cd"], [1.00, "#6fa8dc"]]


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def build_template_df():
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def normalize_status(value):
    if pd.isna(value) or str(value).strip() == "":
        return "Completed"
    cleaned = str(value).strip().title()
    return cleaned if cleaned in VALID_STATUSES else "Completed"


def normalize_source_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "Completed" if col == "status" else ""
    df = df[REQUIRED_COLUMNS].dropna(how="all").copy()
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    df["state_name"] = df["state"].map(STATE_NAME_LOOKUP).fillna(df["state_name"])
    for col in ["runner_name", "race_type", "race_name", "race_date", "finish_time", "city", "notes"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["status"] = df["status"].apply(normalize_status)
    return df


def build_sample_df():
    return normalize_source_df(pd.DataFrame(SAMPLE_DATA))


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
        for col in ["total_races", "completed_races", "registered_races", "interested_races", "unique_runners"]:
            states_df[col] = 0
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
            unique_runners=("runner_name", "nunique"),
        )
        .reset_index()
    )

    map_df = states_df.merge(summary, on=["state", "state_name"], how="left")
    for col in ["total_races", "completed_races", "registered_races", "interested_races", "unique_runners"]:
        map_df[col] = map_df[col].fillna(0).astype(int)

    def status_label(row) -> str:
        if row["completed_races"] > 0:
            return "Completed"
        if row["registered_races"] > 0:
            return "Registered"
        if row["interested_races"] > 0:
            return "Interested"
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


def display_race_table(df: pd.DataFrame):
    if df.empty:
        st.info("No matching race entries.")
        return
    display_df = df[["status", "state", "state_name", "runner_name", "race_type", "race_name", "city", "race_date_display", "finish_time", "avg_mile_pace", "notes"]].rename(
        columns={
            "status": "Status", "state": "State", "state_name": "State Name", "runner_name": "Runner",
            "race_type": "Race Type", "race_name": "Race Name", "city": "City", "race_date_display": "Date",
            "finish_time": "Finish Time", "avg_mile_pace": "Avg Mile Pace", "notes": "Notes",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# -------------------------------------------------
# RunSignup mock integration
# -------------------------------------------------
def guess_race_type_from_events(events_text: str) -> str:
    text = str(events_text or "").lower()
    if "half" in text or "13.1" in text:
        return "Half Marathon"
    if "10k" in text or "10 k" in text or "6.2" in text:
        return "10K"
    if "10 mile" in text or "10-mile" in text:
        return "10 Mile"
    if "5k" in text or "5 k" in text or "3.1" in text:
        return "5K"
    return "Half Marathon"


def flatten_runsignup_race(item: dict, fallback_state: str) -> dict:
    race = item.get("race", item)
    address = race.get("address") or {}

    event_names = []
    event_distances = []
    for event_wrapper in race.get("events") or []:
        event = event_wrapper.get("event", event_wrapper)
        if event.get("name"):
            event_names.append(str(event.get("name")))
        distance = event.get("distance")
        units = event.get("distance_units") or event.get("distance_unit")
        if distance:
            event_distances.append(f"{distance} {units or ''}".strip())

    event_summary = "; ".join(event_names + event_distances)
    state_code = str(address.get("state") or fallback_state).upper()
    race_url = race.get("url") or race.get("external_race_url") or ""

    return {
        "state": state_code,
        "state_name": STATE_NAME_LOOKUP.get(state_code, state_code),
        "runner_name": "API Future Race",
        "race_type": guess_race_type_from_events(event_summary),
        "race_name": race.get("name", ""),
        "race_date": race.get("next_date", ""),
        "finish_time": "",
        "city": address.get("city", ""),
        "notes": race_url,
        "status": "Interested",
    }


def fetch_runsignup_future_races_for_state(
    state_code: str,
    api_key: str,
    api_secret: str,
    max_pages: int = 5,
    results_per_page: int = 1000,
) -> pd.DataFrame:
    rows = []
    start_date = date.today()
    end_date = start_date + timedelta(days=365)

    for page in range(1, max_pages + 1):
        params = {
            "format": "json",
            "api_key": api_key,
            "api_secret": api_secret,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "state": state_code,
            "events": "T",
            "page": page,
            "results_per_page": results_per_page,
            "sort": "date ASC",
        }

        response = requests.get(RUNSIGNUP_API_URL, params=params, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"RunSignup returned HTTP {response.status_code}:\n\n{response.text[:2000]}")

        data = response.json()
        page_races = data.get("races", [])
        if not page_races:
            break

        for item in page_races:
            rows.append(flatten_runsignup_race(item, fallback_state=state_code))

        if len(page_races) < results_per_page:
            break

    return normalize_source_df(pd.DataFrame(rows))


def fetch_runsignup_mock_states(api_key: str, api_secret: str) -> pd.DataFrame:
    state_dfs = []
    for state_code in RUNSIGNUP_TEST_STATES:
        state_dfs.append(fetch_runsignup_future_races_for_state(state_code, api_key, api_secret))
    if not state_dfs:
        return normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))
    return normalize_source_df(pd.concat(state_dfs, ignore_index=True))


# -------------------------------------------------
# Session-backed source data
# -------------------------------------------------
if "source_data" not in st.session_state:
    st.session_state.source_data = build_sample_df()
else:
    st.session_state.source_data = normalize_source_df(st.session_state.source_data)

if "runsignup_future_races" not in st.session_state:
    st.session_state.runsignup_future_races = normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))

# -------------------------------------------------
# Header + API controls
# -------------------------------------------------
st.title("50 States Race Tracker")
st.caption("Track completed races, registered future races, interested future races, and test future race planning data from RunSignup.")

with st.sidebar:
    st.title("Filters")

    st.markdown("### RunSignup Prototype")
    api_key = st.text_input("RunSignup API Key", value=get_secret("RUNSIGNUP_API_KEY"), type="password")
    api_secret = st.text_input("RunSignup API Secret", value=get_secret("RUNSIGNUP_API_SECRET"), type="password")

    show_runsignup_future = st.toggle(
        "Show CO + SD RunSignup future races",
        value=False,
        help="Prototype only: pulls 12 months of future races for Colorado and South Dakota and treats them as Interested.",
    )

    if st.button("Pull CO + SD future races"):
        if not api_key or not api_secret:
            st.error("Add your RunSignup API key and secret first.")
        else:
            with st.spinner("Pulling Colorado and South Dakota races from RunSignup..."):
                try:
                    st.session_state.runsignup_future_races = fetch_runsignup_mock_states(api_key, api_secret)
                    st.success(f"Pulled {len(st.session_state.runsignup_future_races):,} future race rows.")
                except Exception as exc:
                    st.error("RunSignup pull failed.")
                    st.code(str(exc))

    if not st.session_state.runsignup_future_races.empty:
        st.caption(f"Current API cache: {len(st.session_state.runsignup_future_races):,} rows")
        if st.button("Clear RunSignup API cache"):
            st.session_state.runsignup_future_races = normalize_source_df(pd.DataFrame(columns=REQUIRED_COLUMNS))
            st.rerun()

# Combine normal source data with API mock data only when toggle is on.
combined_source_df = st.session_state.source_data.copy()
if show_runsignup_future and not st.session_state.runsignup_future_races.empty:
    combined_source_df = normalize_source_df(
        pd.concat([combined_source_df, st.session_state.runsignup_future_races], ignore_index=True)
    )

race_df = prepare_race_df(combined_source_df)

with st.sidebar:
    st.divider()
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

col1, col2, col3, col4 = st.columns(4)
col1.metric("States Completed", int((filtered_map_df["map_status"] == "Completed").sum()))
col2.metric("States Registered", int((filtered_map_df["map_status"] == "Registered").sum()))
col3.metric("States Interested", int((filtered_map_df["map_status"] == "Interested").sum()))
col4.metric("Total Entries", len(filtered_race_df))

map_page, graphs_page, manage_page = st.tabs(["Map", "Graphs", "Data Management"])

# -------------------------------------------------
# Page 1: Map
# -------------------------------------------------
with map_page:
    st.subheader("US Map")
    st.caption("Status priority: Completed beats Registered, Registered beats Interested, and empty states stay blank.")

    if show_runsignup_future:
        st.info("RunSignup prototype data is included on the map as Interested rows for Colorado and South Dakota.")
        if not st.session_state.runsignup_future_races.empty:
            with st.expander("Preview pulled RunSignup future races"):
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
        },
        color_continuous_scale=STATUS_COLOR_SCALE,
        range_color=(0, 3),
    )
    fig.update_traces(marker_line_color="white", marker_line_width=1)
    fig.update_geos(scope="usa", visible=False, projection_scale=1.1, center={"lat": 38.5, "lon": -96})
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
            tickvals=[0, 1, 2, 3],
            ticktext=["Empty", "Interested", "Registered", "Completed"],
        ),
    )

    selected = st.plotly_chart(fig, use_container_width=True, on_select="rerun", selection_mode="points")

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
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Entries", len(state_runs))
            s2.metric("Completed", int((state_runs["status"] == "Completed").sum()))
            s3.metric("Future", int(state_runs["status"].isin(["Registered", "Interested"]).sum()))
            s4.metric("Best Time", best_time_for_group(state_runs))
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
        future_df = filtered_race_df[filtered_race_df["status"].isin(["Registered", "Interested"])].copy()

        st.markdown("#### Entries by Status")
        status_counts = filtered_race_df.groupby("status").size().reset_index(name="count")
        status_fig = px.bar(status_counts, x="status", y="count", text="count")
        status_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Status", yaxis_title="Entries")
        st.plotly_chart(status_fig, use_container_width=True)

        st.markdown("#### Race Types")
        race_type_counts = filtered_race_df.groupby("race_type").size().reset_index(name="count")
        race_type_fig = px.bar(race_type_counts, x="race_type", y="count", text="count")
        race_type_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Race Type", yaxis_title="Entries")
        st.plotly_chart(race_type_fig, use_container_width=True)

        st.markdown("#### Completed Races by Year")
        if completed_df.empty:
            st.info("No completed races available for this chart.")
        else:
            yearly_counts = completed_df.groupby("race_year").size().reset_index(name="count")
            yearly_fig = px.bar(yearly_counts, x="race_year", y="count", text="count")
            yearly_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Year", yaxis_title="Completed Races")
            st.plotly_chart(yearly_fig, use_container_width=True)

        st.markdown("#### Upcoming Registered / Interested Races")
        today_ts = pd.Timestamp(date.today())
        upcoming_df = future_df[future_df["race_date"] >= today_ts].sort_values("race_date")
        if upcoming_df.empty:
            st.info("No upcoming registered or interested races found.")
        else:
            upcoming_display = upcoming_df[["status", "runner_name", "race_type", "race_name", "city", "state", "race_date_display", "notes"]].rename(
                columns={"status": "Status", "runner_name": "Runner", "race_type": "Race Type", "race_name": "Race Name", "city": "City", "state": "State", "race_date_display": "Date", "notes": "Notes / URL"}
            )
            st.dataframe(upcoming_display, use_container_width=True, hide_index=True)

        st.markdown("#### All Race Entries")
        display_race_table(filtered_race_df.sort_values(["race_date", "runner_name"], ascending=[False, True]))

# -------------------------------------------------
# Page 3: Data management
# -------------------------------------------------
with manage_page:
    st.subheader("Data Management")
    st.markdown("Download a blank template, export current manual data, or upload a CSV to replace the current manual session data.")

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download Blank CSV Template", data=build_template_df().to_csv(index=False).encode("utf-8"), file_name="race_results_template.csv", mime="text/csv")
    with c2:
        st.download_button("Download Current Manual Data", data=st.session_state.source_data.to_csv(index=False).encode("utf-8"), file_name="race_results_current.csv", mime="text/csv")

    if not st.session_state.runsignup_future_races.empty:
        st.download_button(
            "Download Current RunSignup Prototype Data",
            data=st.session_state.runsignup_future_races.to_csv(index=False).encode("utf-8"),
            file_name="runsignup_future_races_mock.csv",
            mime="text/csv",
        )

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded_file is not None:
        uploaded_df = pd.read_csv(uploaded_file)
        st.write("Preview of uploaded file:")
        st.dataframe(uploaded_df, use_container_width=True, hide_index=True)
        errors = validate_uploaded_csv(uploaded_df)
        if errors:
            st.error("CSV validation failed:")
            for err in errors:
                st.write(f"- {err}")
        else:
            st.success("CSV looks valid.")
            if st.button("Replace Current Manual Data With Uploaded CSV"):
                st.session_state.source_data = normalize_source_df(uploaded_df)
                st.success("Current manual session data replaced successfully.")
                st.rerun()

    st.divider()
    st.info("For this prototype, RunSignup data is only stored in session_state. It is not saved permanently until we add SQLite or another database.")

st.caption("Next upgrade ideas: SQLite backend, Excel import, household/user accounts, medals/badges, public profiles, and monetized premium plans.")


