import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

st.set_page_config(page_title="50 States Race Tracker", layout="wide")

# -------------------------------------------------
# Sample preloaded data
# -------------------------------------------------
SAMPLE_DATA = [
    {"state": "IL", "state_name": "Illinois", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Chicago Spring Half", "race_date": "2025-04-13", "finish_time": "1:49:32"},
    {"state": "IL", "state_name": "Illinois", "runner_name": "Wife", "race_type": "10K", "race_name": "Lakefront 10K", "race_date": "2024-09-21", "finish_time": "0:58:14"},
    {"state": "WI", "state_name": "Wisconsin", "runner_name": "Tom", "race_type": "5K", "race_name": "Madison Summer 5K", "race_date": "2024-06-15", "finish_time": "0:24:48"},
    {"state": "WI", "state_name": "Wisconsin", "runner_name": "Wife", "race_type": "Half Marathon", "race_name": "Door County Half", "race_date": "2025-05-04", "finish_time": "2:03:45"},
    {"state": "MN", "state_name": "Minnesota", "runner_name": "Tom", "race_type": "10K", "race_name": "Twin Cities 10K", "race_date": "2023-10-01", "finish_time": "0:49:35"},
    {"state": "MN", "state_name": "Minnesota", "runner_name": "Wife", "race_type": "5K", "race_name": "St. Paul Classic 5K", "race_date": "2023-08-11", "finish_time": "0:27:50"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Tom", "race_type": "Half Marathon", "race_name": "Austin Half", "race_date": "2025-02-16", "finish_time": "1:46:58"},
    {"state": "TX", "state_name": "Texas", "runner_name": "Wife", "race_type": "10K", "race_name": "Dallas Dash 10K", "race_date": "2025-03-08", "finish_time": "0:56:40"},
    {"state": "CO", "state_name": "Colorado", "runner_name": "Tom", "race_type": "5K", "race_name": "Denver Peaks 5K", "race_date": "2024-07-20", "finish_time": "0:23:59"},
    {"state": "CO", "state_name": "Colorado", "runner_name": "Wife", "race_type": "Half Marathon", "race_name": "Boulder Half", "race_date": "2024-09-14", "finish_time": "2:01:15"},
    {"state": "FL", "state_name": "Florida", "runner_name": "Tom", "race_type": "10K", "race_name": "Orlando 10K", "race_date": "2023-12-02", "finish_time": "0:50:22"},
    {"state": "FL", "state_name": "Florida", "runner_name": "Wife", "race_type": "5K", "race_name": "Sunrise 5K", "race_date": "2023-12-02", "finish_time": "0:29:18"},
]

DISTANCE_MILES = {
    "5K": 3.10686,
    "10K": 6.21371,
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
]

VALID_RACE_TYPES = {"5K", "10K", "Half Marathon"}
VALID_STATE_CODES = {code for code, _ in ALL_STATES}
STATE_NAME_LOOKUP = {code: name for code, name in ALL_STATES}


def build_template_df():
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def build_sample_df():
    df = pd.DataFrame(SAMPLE_DATA)

    # Make sure optional columns exist even in sample data
    for col in ["city", "notes"]:
        if col not in df.columns:
            df[col] = ""

    return df[REQUIRED_COLUMNS]


def validate_uploaded_csv(df: pd.DataFrame):
    errors = []

    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {', '.join(missing_cols)}")
        return errors

    # Drop fully blank rows
    df = df.dropna(how="all").copy()

    invalid_states = sorted(set(df.loc[~df["state"].isin(VALID_STATE_CODES), "state"].dropna().astype(str)))
    if invalid_states:
        errors.append(f"Invalid state codes: {', '.join(invalid_states)}")

    invalid_race_types = sorted(set(df.loc[~df["race_type"].isin(VALID_RACE_TYPES), "race_type"].dropna().astype(str)))
    if invalid_race_types:
        errors.append(f"Invalid race types: {', '.join(invalid_race_types)}")

    # Validate dates
    try:
        pd.to_datetime(df["race_date"], errors="raise")
    except Exception:
        errors.append("One or more race_date values are invalid. Use YYYY-MM-DD.")

    # Validate finish_time format
    for i, value in enumerate(df["finish_time"], start=1):
        try:
            time_to_seconds(str(value))
        except Exception:
            errors.append(f"Invalid finish_time on row {i}: {value}")

    return errors


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def time_to_seconds(time_str: str) -> int:
    parts = [int(p) for p in time_str.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid time format: {time_str}")
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_hms(total_seconds: int) -> str:
    hours = total_seconds // 3600
    remainder = total_seconds % 3600
    minutes = remainder // 60
    seconds = remainder % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def seconds_to_pace(total_seconds: float, miles: float) -> str:
    pace_seconds = round(total_seconds / miles)
    pace_minutes = pace_seconds // 60
    pace_remainder = pace_seconds % 60
    return f"{pace_minutes}:{pace_remainder:02d} /mi"


def prepare_race_df(source_df: pd.DataFrame) -> pd.DataFrame:
    df = source_df.copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["finish_seconds"] = df["finish_time"].astype(str).apply(time_to_seconds)
    df["distance_miles"] = df["race_type"].map(DISTANCE_MILES)
    df["avg_mile_pace"] = df.apply(
        lambda row: seconds_to_pace(row["finish_seconds"], row["distance_miles"]), axis=1
    )
    df["race_date_display"] = df["race_date"].dt.strftime("%Y-%m-%d")
    return df


def prepare_map_df(race_df: pd.DataFrame) -> pd.DataFrame:
    states_df = pd.DataFrame(ALL_STATES, columns=["state", "state_name"])
    summary = (
        race_df.groupby(["state", "state_name"])
        .agg(
            total_races=("race_name", "count"),
            unique_runners=("runner_name", "nunique")
        )
        .reset_index()
    )

    map_df = states_df.merge(summary, on=["state", "state_name"], how="left")
    map_df["total_races"] = map_df["total_races"].fillna(0).astype(int)
    map_df["unique_runners"] = map_df["unique_runners"].fillna(0).astype(int)

    def status_label(runners: int) -> str:
        if runners == 0:
            return "No races yet"
        if runners == 1:
            return "One of us ran here"
        return "Both ran here"

    map_df["status"] = map_df["unique_runners"].apply(status_label)
    map_df["color_value"] = map_df["unique_runners"]
    return map_df


def best_time_for_group(group: pd.DataFrame) -> str:
    idx = group["finish_seconds"].idxmin()
    row = group.loc[idx]
    return f"{row['runner_name']} - {row['finish_time']} ({row['race_type']})"


# -------------------------------------------------
# Session-backed source data
# -------------------------------------------------
if "source_data" not in st.session_state:
    st.session_state.source_data = build_sample_df()

# -------------------------------------------------
# Data Management
# -------------------------------------------------
with st.expander("Data Management", expanded=False):
    st.markdown("Download a blank template, export current data, or upload a CSV to replace the current session data.")

    template_df = build_template_df()
    current_df = st.session_state.source_data.copy()

    st.download_button(
        label="Download Blank CSV Template",
        data=template_df.to_csv(index=False).encode("utf-8"),
        file_name="race_results_template.csv",
        mime="text/csv",
    )

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
                cleaned_df = uploaded_df.dropna(how="all").copy()

                # Fill missing optional fields
                for col in ["city", "notes"]:
                    cleaned_df[col] = cleaned_df[col].fillna("")

                st.session_state.source_data = cleaned_df[REQUIRED_COLUMNS].copy()
                st.success("Current session data replaced successfully.")
                st.rerun()

# -------------------------------------------------
# Data prep
# -------------------------------------------------
race_df = prepare_race_df(st.session_state.source_data)
map_df = prepare_map_df(race_df)


# -------------------------------------------------
# Sidebar filters
# -------------------------------------------------
st.sidebar.title("Filters")
runner_filter = st.sidebar.multiselect(
    "Runner",
    options=sorted(race_df["runner_name"].unique()),
    default=sorted(race_df["runner_name"].unique())
)
race_type_filter = st.sidebar.multiselect(
    "Race Type",
    options=sorted(race_df["race_type"].unique()),
    default=sorted(race_df["race_type"].unique())
)

filtered_race_df = race_df[
    race_df["runner_name"].isin(runner_filter) & race_df["race_type"].isin(race_type_filter)
].copy()
filtered_map_df = prepare_map_df(filtered_race_df) if not filtered_race_df.empty else prepare_map_df(pd.DataFrame(columns=race_df.columns))


# -------------------------------------------------
# Header + stats
# -------------------------------------------------
st.title("🏃 50 States Race Tracker")
st.caption("An interactive Streamlit MVP for tracking 5Ks, 10Ks, and Half Marathons across the United States.")

col1, col2, col3 = st.columns(3)
col1.metric("States Completed", int((filtered_map_df["total_races"] > 0).sum()))
col2.metric("Total Logged Races", len(filtered_race_df))
col3.metric("Runners Included", filtered_race_df["runner_name"].nunique())


# -------------------------------------------------
# Main map
# -------------------------------------------------
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
        "status": True,
    },
    color_continuous_scale="Blues",
    range_color=(0, 2),
)

fig.update_layout(
    margin=dict(l=0, r=0, t=10, b=0),
    coloraxis_colorbar_title="Coverage",
    height=550,
)

fig.update_traces(marker_line_color="white", marker_line_width=1)

st.subheader("US Map")
selected = st.plotly_chart(fig, use_container_width=True, on_select="rerun", selection_mode="points")


# -------------------------------------------------
# State selection handling
# -------------------------------------------------
selected_state = None
if selected and selected.get("selection") and selected["selection"].get("points"):
    point = selected["selection"]["points"][0]
    selected_state = point.get("location")

st.markdown("### State Details")
state_options = ["Select a state..."] + [name for _, name in ALL_STATES]
state_name_to_code = {name: code for code, name in ALL_STATES}
code_to_state_name = {code: name for code, name in ALL_STATES}

# If user clicked a state, default the dropdown to that state
if selected_state:
    default_state_name = code_to_state_name[selected_state]
    default_index = state_options.index(default_state_name)
else:
    default_index = 0

chosen_state_name = st.selectbox("Choose a state", state_options, index=default_index)
if chosen_state_name != "Select a state...":
    selected_state = state_name_to_code[chosen_state_name]

if selected_state:
    state_runs = filtered_race_df[filtered_race_df["state"] == selected_state].sort_values(["race_date", "runner_name"], ascending=[False, True])

    st.write(f"**{code_to_state_name[selected_state]}**")

    if state_runs.empty:
        st.info("No matching race data for this state under the current filters.")
    else:
        s1, s2, s3 = st.columns(3)
        s1.metric("Races in State", len(state_runs))
        s2.metric("Runners", state_runs["runner_name"].nunique())
        s3.metric("Best Time", best_time_for_group(state_runs))

        display_df = state_runs[[
            "runner_name",
            "race_type",
            "race_name",
            "race_date_display",
            "finish_time",
            "avg_mile_pace",
        ]].rename(columns={
            "runner_name": "Runner",
            "race_type": "Race Type",
            "race_name": "Race Name",
            "race_date_display": "Date",
            "finish_time": "Finish Time",
            "avg_mile_pace": "Avg Mile Pace",
        })

        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.markdown("#### Quick Summary")
        by_runner = (
            state_runs.groupby("runner_name")
            .agg(
                races=("race_name", "count"),
                best_time_seconds=("finish_seconds", "min")
            )
            .reset_index()
        )
        by_runner["Best Time"] = by_runner["best_time_seconds"].apply(seconds_to_hms)
        by_runner = by_runner.rename(columns={"runner_name": "Runner", "races": "Races"})[["Runner", "Races", "Best Time"]]
        st.dataframe(by_runner, use_container_width=True, hide_index=True)
else:
    st.info("Click a state on the map or choose one from the dropdown to view race details.")


# -------------------------------------------------
# Footer note
# -------------------------------------------------
st.markdown("---")
st.caption(
    "Next upgrade ideas: SQLite backend, Excel import, household/user accounts, medals/badges, public profiles, and monetized premium plans."
)
