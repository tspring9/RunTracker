import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date

st.set_page_config(page_title="50 States Race Tracker", layout="wide")

# -------------------------------------------------
# Sample preloaded data
# -------------------------------------------------
SAMPLE_DATA = [
    {
        "state": "IL",
        "state_name": "Illinois",
        "runner_name": "Tom",
        "race_type": "Half Marathon",
        "race_name": "Chicago Spring Half",
        "race_date": "2025-04-13",
        "finish_time": "1:49:32",
        "city": "Chicago",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "IL",
        "state_name": "Illinois",
        "runner_name": "Wife",
        "race_type": "10K",
        "race_name": "Lakefront 10K",
        "race_date": "2024-09-21",
        "finish_time": "0:58:14",
        "city": "Chicago",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "WI",
        "state_name": "Wisconsin",
        "runner_name": "Tom",
        "race_type": "5K",
        "race_name": "Madison Summer 5K",
        "race_date": "2024-06-15",
        "finish_time": "0:24:48",
        "city": "Madison",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "WI",
        "state_name": "Wisconsin",
        "runner_name": "Wife",
        "race_type": "Half Marathon",
        "race_name": "Door County Half",
        "race_date": "2025-05-04",
        "finish_time": "2:03:45",
        "city": "Door County",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "MN",
        "state_name": "Minnesota",
        "runner_name": "Tom",
        "race_type": "10K",
        "race_name": "Twin Cities 10K",
        "race_date": "2023-10-01",
        "finish_time": "0:49:35",
        "city": "Minneapolis",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "MN",
        "state_name": "Minnesota",
        "runner_name": "Wife",
        "race_type": "5K",
        "race_name": "St. Paul Classic 5K",
        "race_date": "2023-08-11",
        "finish_time": "0:27:50",
        "city": "St. Paul",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "TX",
        "state_name": "Texas",
        "runner_name": "Tom",
        "race_type": "Half Marathon",
        "race_name": "Austin Half",
        "race_date": "2025-02-16",
        "finish_time": "1:46:58",
        "city": "Austin",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "TX",
        "state_name": "Texas",
        "runner_name": "Wife",
        "race_type": "10K",
        "race_name": "Dallas Dash 10K",
        "race_date": "2025-03-08",
        "finish_time": "0:56:40",
        "city": "Dallas",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "CO",
        "state_name": "Colorado",
        "runner_name": "Tom",
        "race_type": "5K",
        "race_name": "Denver Peaks 5K",
        "race_date": "2024-07-20",
        "finish_time": "0:23:59",
        "city": "Denver",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "CO",
        "state_name": "Colorado",
        "runner_name": "Wife",
        "race_type": "Half Marathon",
        "race_name": "Boulder Half",
        "race_date": "2024-09-14",
        "finish_time": "2:01:15",
        "city": "Boulder",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "FL",
        "state_name": "Florida",
        "runner_name": "Tom",
        "race_type": "10K",
        "race_name": "Orlando 10K",
        "race_date": "2023-12-02",
        "finish_time": "0:50:22",
        "city": "Orlando",
        "notes": "",
        "status": "Completed",
    },
    {
        "state": "FL",
        "state_name": "Florida",
        "runner_name": "Wife",
        "race_type": "5K",
        "race_name": "Sunrise 5K",
        "race_date": "2023-12-02",
        "finish_time": "0:29:18",
        "city": "Orlando",
        "notes": "",
        "status": "Completed",
    },
]

DISTANCE_MILES = {
    "5K": 3.10686,
    "10K": 6.21371,
    "10 Mile": 10,
    "Half Marathon": 13.1094,
}

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

REQUIRED_COLUMNS = [
    "state",
    "state_name",
    "runner_name",
    "race_type",
    "race_name",
    "race_date",
    "finish_time",
    "city",
    "notes",
    "status",
]

VALID_RACE_TYPES = ["5K", "10K", "10 Mile", "Half Marathon"]
VALID_STATUSES = ["Completed", "Registered", "Interested"]
VALID_STATE_CODES = {code for code, _ in ALL_STATES}
STATE_NAME_LOOKUP = {code: name for code, name in ALL_STATES}
STATE_CODE_LOOKUP = {name: code for code, name in ALL_STATES}
STATUS_COLOR_VALUE = {"Empty": 0, "Interested": 1, "Registered": 2, "Completed": 3}
STATUS_COLOR_SCALE = [
    [0.00, "#f1f5f9"],
    [0.33, "#d9ead3"],
    [0.66, "#fce5cd"],
    [1.00, "#6fa8dc"],
]

CITY_COORDS = {
    "Chicago": (41.8781, -87.6298),
    "Madison": (43.0731, -89.4012),
    "Door County": (44.8331, -87.3770),
    "Minneapolis": (44.9778, -93.2650),
    "St. Paul": (44.9537, -93.0900),
    "Austin": (30.2672, -97.7431),
    "Dallas": (32.7767, -96.7970),
    "Denver": (39.7392, -104.9903),
    "Boulder": (40.0150, -105.2705),
    "Orlando": (28.5383, -81.3792),
}

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def build_template_df():
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def build_sample_df():
    return normalize_source_df(pd.DataFrame(SAMPLE_DATA))


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

    df = df[REQUIRED_COLUMNS]
    df = df.dropna(how="all").copy()
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


def seconds_to_hms(total_seconds: int) -> str:
    total_seconds = int(total_seconds)
    hours = total_seconds // 3600
    remainder = total_seconds % 3600
    minutes = remainder // 60
    seconds = remainder % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


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
        errors.append("One or more race_date values are invalid. Use YYYY-MM-DD.")

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


def add_race_entry(entry: dict):
    new_row = pd.DataFrame([entry])
    st.session_state.source_data = normalize_source_df(pd.concat([st.session_state.source_data, new_row], ignore_index=True))


def update_race_entry(index: int, entry: dict):
    for col, value in entry.items():
        st.session_state.source_data.at[index, col] = value
    st.session_state.source_data = normalize_source_df(st.session_state.source_data)


def delete_race_entry(index: int):
    st.session_state.source_data = st.session_state.source_data.drop(index=index).reset_index(drop=True)


def get_city_points(race_df: pd.DataFrame) -> pd.DataFrame:
    if race_df.empty:
        return pd.DataFrame(columns=["city", "state", "lat", "lon", "race_count", "hover_text"])

    city_df = (
        race_df.groupby(["city", "state"], dropna=False)
        .agg(race_count=("race_name", "count"), runners=("runner_name", lambda s: ", ".join(sorted(set(s)))), statuses=("status", lambda s: ", ".join(sorted(set(s)))))
        .reset_index()
    )
    city_df = city_df[city_df["city"].astype(str).str.strip() != ""].copy()
    city_df["lat"] = city_df["city"].map(lambda c: CITY_COORDS.get(c, (None, None))[0])
    city_df["lon"] = city_df["city"].map(lambda c: CITY_COORDS.get(c, (None, None))[1])
    city_df = city_df.dropna(subset=["lat", "lon"])
    city_df["hover_text"] = city_df.apply(
        lambda r: f"{r['city']}, {r['state']}<br>Races: {r['race_count']}<br>Runners: {r['runners']}<br>Status: {r['statuses']}",
        axis=1,
    )
    return city_df


def display_race_table(df: pd.DataFrame):
    if df.empty:
        st.info("No matching race entries.")
        return
    display_df = df[
        [
            "status",
            "state",
            "state_name",
            "runner_name",
            "race_type",
            "race_name",
            "city",
            "race_date_display",
            "finish_time",
            "avg_mile_pace",
            "notes",
        ]
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
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# -------------------------------------------------
# Session-backed source data
# -------------------------------------------------
if "source_data" not in st.session_state:
    st.session_state.source_data = build_sample_df()
else:
    st.session_state.source_data = normalize_source_df(st.session_state.source_data)

race_df = prepare_race_df(st.session_state.source_data)

# -------------------------------------------------
# Header + filters
# -------------------------------------------------
st.title("50 States Race Tracker")
st.caption("Track completed races, registered future races, and interested future races across the United States.")

with st.sidebar:
    st.title("Filters")
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

# Three mobile-friendly sections.
map_page, graphs_page, manage_page = st.tabs(["🗺️ Map", "📊 Graphs", "🛠️ Data Management"])

# -------------------------------------------------
# Page 1: Map
# -------------------------------------------------
with map_page:
    st.subheader("US Map")
    st.caption("Map priority: Completed beats Registered, Registered beats Interested, and states with no entries stay blank.")

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
            "total_races": True,
            "completed_races": True,
            "registered_races": True,
            "interested_races": True,
            "map_status": True,
        },
        color_continuous_scale=STATUS_COLOR_SCALE,
        range_color=(0, 3),
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        coloraxis_colorbar=dict(
            title="Status",
            tickvals=[0, 1, 2, 3],
            ticktext=["Empty", "Interested", "Registered", "Completed"],
        ),
        height=520,
    )
    fig.update_traces(marker_line_color="white", marker_line_width=1)

    selected = st.plotly_chart(fig, use_container_width=True, on_select="rerun", selection_mode="points")

    city_points = get_city_points(filtered_race_df)
    if not city_points.empty:
        st.markdown("#### City Race Bubbles")
        city_fig = px.scatter_geo(
            city_points,
            lat="lat",
            lon="lon",
            size="race_count",
            scope="usa",
            hover_name="city",
            hover_data={"lat": False, "lon": False, "state": True, "race_count": True},
        )
        city_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=420)
        st.plotly_chart(city_fig, use_container_width=True)
        st.caption("Cities need coordinates in CITY_COORDS before they can appear as bubbles. Add more city coordinates as your real CSV grows.")

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
            s1.metric("Entries in State", len(state_runs))
            s2.metric("Completed", int((state_runs["status"] == "Completed").sum()))
            s3.metric("Future", int((state_runs["status"].isin(["Registered", "Interested"])).sum()))
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
            upcoming_display = upcoming_df[["status", "runner_name", "race_type", "race_name", "city", "state", "race_date_display"]].rename(
                columns={
                    "status": "Status",
                    "runner_name": "Runner",
                    "race_type": "Race Type",
                    "race_name": "Race Name",
                    "city": "City",
                    "state": "State",
                    "race_date_display": "Date",
                }
            )
            st.dataframe(upcoming_display, use_container_width=True, hide_index=True)

        st.markdown("#### All Race Entries")
        display_race_table(filtered_race_df.sort_values(["race_date", "runner_name"], ascending=[False, True]))

# -------------------------------------------------
# Page 3: Data management, add, edit, delete
# -------------------------------------------------
with manage_page:
    st.subheader("Data Management")
    st.markdown("Download a blank template, export current data, or upload a CSV to replace the current session data.")

    template_df = build_template_df()
    current_df = st.session_state.source_data.copy()

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            label="Download Blank CSV Template",
            data=template_df.to_csv(index=False).encode("utf-8"),
            file_name="race_results_template.csv",
            mime="text/csv",
        )
    with c2:
        st.download_button(
            label="Download Current Data",
            data=current_df.to_csv(index=False).encode("utf-8"),
            file_name="race_results_current.csv",
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
            if st.button("Replace Current Data With Uploaded CSV"):
                st.session_state.source_data = normalize_source_df(uploaded_df)
                st.success("Current session data replaced successfully.")
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
            add_status = st.selectbox("Status", VALID_STATUSES)
            add_finish_time = st.text_input("Finish Time", placeholder="Required only for completed races")
            add_notes = st.text_area("Notes", height=100)

        add_submitted = st.form_submit_button("Add Race")
        if add_submitted:
            if not add_runner_name.strip():
                st.error("Runner Name is required.")
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
                    VALID_STATUSES,
                    index=VALID_STATUSES.index(selected_row["status"]) if selected_row["status"] in VALID_STATUSES else 0,
                )
                edit_finish_time = st.text_input("Finish Time", value=selected_row["finish_time"])
                edit_notes = st.text_area("Notes", value=selected_row["notes"], height=100)

            save_submitted = st.form_submit_button("Save Changes")
            if save_submitted:
                if not edit_runner_name.strip():
                    st.error("Runner Name is required.")
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
                        selected_index,
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
            delete_race_entry(selected_index)
            st.success("Race deleted.")
            st.rerun()

st.markdown("---")
st.caption("Next upgrade ideas: SQLite backend, Excel import, household/user accounts, medals/badges, public profiles, and monetized premium plans.")



