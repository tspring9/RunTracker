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

STATUS_PRIORITY = {
    "Completed": 3,
    "Registered": 2,
    "Interested": 1,
    "Empty": 0,
}

STATUS_COLOR_VALUE = {
    "Empty": 0,
    "Interested": 1,
    "Registered": 2,
    "Completed": 3,
}

STATUS_COLOR_SCALE = [
    [0.00, "#f1f5f9"],  # Empty
    [0.33, "#d9ead3"],  # Interested
    [0.66, "#fce5cd"],  # Registered
    [1.00, "#6fa8dc"],  # Completed
]

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def build_template_df():
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def build_sample_df():
    df = pd.DataFrame(SAMPLE_DATA)
    return normalize_source_df(df)


def normalize_status(value):
    if pd.isna(value) or str(value).strip() == "":
        return "Completed"
    value = str(value).strip().title()
    if value not in VALID_STATUSES:
        return "Completed"
    return value


def normalize_source_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            if col == "status":
                df[col] = "Completed"
            else:
                df[col] = ""

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


def validate_uploaded_csv(df: pd.DataFrame):
    errors = []

    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {', '.join(missing_cols)}")
        return errors

    df = df.dropna(how="all").copy()
    df["state"] = df["state"].astype(str).str.strip().str.upper()
    df["race_type"] = df["race_type"].astype(str).str.strip()
    df["status"] = df["status"].apply(normalize_status)

    invalid_states = sorted(set(df.loc[~df["state"].isin(VALID_STATE_CODES), "state"].dropna().astype(str)))
    if invalid_states:
        errors.append(f"Invalid state codes: {', '.join(invalid_states)}")

    invalid_race_types = sorted(set(df.loc[~df["race_type"].isin(VALID_RACE_TYPES), "race_type"].dropna().astype(str)))
    if invalid_race_types:
        errors.append(f"Invalid race types: {', '.join(invalid_race_types)}")

    invalid_statuses = sorted(set(df.loc[~df["status"].isin(VALID_STATUSES), "status"].dropna().astype(str)))
    if invalid_statuses:
        errors.append(f"Invalid statuses: {', '.join(invalid_statuses)}")

    try:
        pd.to_datetime(df["race_date"], errors="raise")
    except Exception:
        errors.append("One or more race_date values are invalid. Use YYYY-MM-DD.")

    completed_rows = df[df["status"] == "Completed"].copy()
    for i, value in completed_rows["finish_time"].items():
        try:
            time_to_seconds(str(value))
        except Exception:
            errors.append(f"Invalid finish_time on row {i + 1}: {value}")

    return errors


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

    pace_seconds = round(total_seconds / miles)
    pace_minutes = pace_seconds // 60
    pace_remainder = pace_seconds % 60
    return f"{pace_minutes}:{pace_remainder:02d} /mi"


def prepare_race_df(source_df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_source_df(source_df)
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["distance_miles"] = df["race_type"].map(DISTANCE_MILES)

    completed_mask = df["status"] == "Completed"
    df["finish_seconds"] = pd.NA
    df.loc[completed_mask, "finish_seconds"] = df.loc[completed_mask, "finish_time"].astype(str).apply(time_to_seconds)

    df["avg_mile_pace"] = df.apply(
        lambda row: seconds_to_pace(row["finish_seconds"], row["distance_miles"]),
        axis=1,
    )
    df["race_date_display"] = df["race_date"].dt.strftime("%Y-%m-%d")
    return df


def prepare_map_df(race_df: pd.DataFrame) -> pd.DataFrame:
    states_df = pd.DataFrame(ALL_STATES, columns=["state", "state_name"])

    if race_df.empty:
        states_df["total_races"] = 0
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
    st.session_state.source_data = normalize_source_df(
        pd.concat([st.session_state.source_data, new_row], ignore_index=True)
    )


def update_race_entry(index: int, entry: dict):
    for col, value in entry.items():
        st.session_state.source_data.at[index, col] = value

    st.session_state.source_data = normalize_source_df(st.session_state.source_data)


def delete_race_entry(index: int):
    st.session_state.source_data = (
        st.session_state.source_data.drop(index=index)
        .reset_index(drop=True)
    )


# -------------------------------------------------
# Session-backed source data
# -------------------------------------------------
if "source_data" not in st.session_state:
    st.session_state.source_data = build_sample_df()
else:
    st.session_state.source_data = normalize_source_df(st.session_state.source_data)

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
                st.session_state.source_data = normalize_source_df(uploaded_df)
                st.success("Current session data replaced successfully.")
                st.rerun()

# -------------------------------------------------
# Manual Add / Edit / Delete
# -------------------------------------------------
with st.expander("Add / Edit Race Entries", expanded=True):
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

        with st.form("edit_race_form"):
            e1, e2, e3 = st.columns(3)

            with e1:
                edit_runner_name = st.text_input("Runner Name", value=selected_row["runner_name"])
                edit_race_name = st.text_input("Race Name", value=selected_row["race_name"])
                edit_race_date = st.date_input("Race Date", value=default_date, key="edit_race_date")

            with e2:
                edit_state_name = st.selectbox(
                    "State",
                    [name for _, name in ALL_STATES],
                    index=[name for _, name in ALL_STATES].index(selected_row["state_name"])
                    if selected_row["state_name"] in [name for _, name in ALL_STATES]
                    else 0,
                )
                edit_city = st.text_input("City", value=selected_row["city"])
                edit_race_type = st.selectbox(
                    "Race Type",
                    VALID_RACE_TYPES,
                    index=VALID_RACE_TYPES.index(selected_row["race_type"])
                    if selected_row["race_type"] in VALID_RACE_TYPES
                    else 0,
                )

            with e3:
                edit_status = st.selectbox(
                    "Status",
                    VALID_STATUSES,
                    index=VALID_STATUSES.index(selected_row["status"])
                    if selected_row["status"] in VALID_STATUSES
                    else 0,
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
    options=sorted(race_df["runner_name"].dropna().unique()),
    default=sorted(race_df["runner_name"].dropna().unique()),
)

race_type_filter = st.sidebar.multiselect(
    "Race Type",
    options=sorted(race_df["race_type"].dropna().unique()),
    default=sorted(race_df["race_type"].dropna().unique()),
)

status_filter = st.sidebar.multiselect(
    "Status",
    options=VALID_STATUSES,
    default=VALID_STATUSES,
)

filtered_race_df = race_df[
    race_df["runner_name"].isin(runner_filter)
    & race_df["race_type"].isin(race_type_filter)
    & race_df["status"].isin(status_filter)
].copy()

filtered_map_df = prepare_map_df(filtered_race_df)

# -------------------------------------------------
# Header + stats
# -------------------------------------------------
st.title("50 States Race Tracker")
st.caption("Track completed races, registered future races, and interested future races across the United States.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("States Completed", int((filtered_map_df["map_status"] == "Completed").sum()))
col2.metric("States Registered", int((filtered_map_df["map_status"] == "Registered").sum()))
col3.metric("States Interested", int((filtered_map_df["map_status"] == "Interested").sum()))
col4.metric("Total Entries", len(filtered_race_df))

# -------------------------------------------------
# Timeline layer
# -------------------------------------------------
st.subheader("Race Timeline")
st.caption(
    "Past completed races are summarized by year, while current and future races stay visible by exact date."
)

if filtered_race_df.empty:
    st.info("No race entries match the current filters.")
else:
    timeline_df = filtered_race_df.copy()
    timeline_df["race_date"] = pd.to_datetime(timeline_df["race_date"], errors="coerce")
    timeline_df = timeline_df.dropna(subset=["race_date"]).sort_values("race_date")
    timeline_df["race_year"] = timeline_df["race_date"].dt.year
    timeline_df["days_from_today"] = (timeline_df["race_date"].dt.date - date.today()).apply(lambda x: x.days)
    timeline_df["is_current_or_future_year"] = timeline_df["race_year"] >= date.today().year
    timeline_df["is_future"] = timeline_df["race_date"].dt.date >= date.today()

    completed_timeline_df = timeline_df[timeline_df["status"] == "Completed"].copy()
    future_timeline_df = timeline_df[
        (timeline_df["status"].isin(["Registered", "Interested"]))
        & (timeline_df["race_date"].dt.date >= date.today())
    ].copy()
    registered_future_df = future_timeline_df[future_timeline_df["status"] == "Registered"].copy()

    next_registered = registered_future_df.sort_values("race_date")
    next_any_future = future_timeline_df.sort_values("race_date")

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Completed Entries", len(completed_timeline_df))
    t2.metric("Registered Future", len(registered_future_df))
    t3.metric("Interested Future", int((future_timeline_df["status"] == "Interested").sum()))

    if not next_registered.empty:
        next_registered_row = next_registered.iloc[0]
        t4.metric("Next Registered Race", f"{int(next_registered_row['days_from_today'])} days")
    elif not next_any_future.empty:
        next_future_row = next_any_future.iloc[0]
        t4.metric("Next Planned Race", f"{int(next_future_row['days_from_today'])} days")
    else:
        t4.metric("Next Race", "None planned")

    past_completed_df = completed_timeline_df[
        completed_timeline_df["race_year"] < date.today().year
    ].copy()

    current_year_completed_df = completed_timeline_df[
        completed_timeline_df["race_year"] >= date.today().year
    ].copy()

    if not past_completed_df.empty:
        yearly_summary_df = (
            past_completed_df.groupby("race_year")
            .agg(
                races=("race_name", "count"),
                states=("state", "nunique"),
                runners=("runner_name", "nunique"),
                race_types=("race_type", lambda x: ", ".join(sorted(set(x)))),
            )
            .reset_index()
            .sort_values("race_year")
        )
        yearly_summary_df["bucket_label"] = yearly_summary_df["race_year"].astype(str)
    else:
        yearly_summary_df = pd.DataFrame(columns=["race_year", "races", "states", "runners", "race_types", "bucket_label"])

    st.markdown("#### Past Years Summary")
    if yearly_summary_df.empty:
        st.info("No prior-year completed races to summarize yet.")
    else:
        fig_yearly = px.bar(
            yearly_summary_df,
            x="bucket_label",
            y="races",
            hover_data={
                "states": True,
                "runners": True,
                "race_types": True,
                "bucket_label": False,
            },
            labels={
                "bucket_label": "Year",
                "races": "Completed Races",
                "states": "States",
                "runners": "Runners",
                "race_types": "Race Types",
            },
            title="Completed Races by Year",
        )
        fig_yearly.update_layout(
            height=300,
            margin=dict(l=0, r=0, t=45, b=0),
            xaxis_title="Year",
            yaxis_title="Completed Races",
        )
        st.plotly_chart(fig_yearly, use_container_width=True)

    st.markdown("#### Current & Future Timeline")

    current_future_df = pd.concat(
        [current_year_completed_df, future_timeline_df],
        ignore_index=True,
    ).sort_values("race_date")

    timeline_view = st.radio(
        "Current/Future View",
        ["All", "Future Only", "Registered Only", "Completed This Year"],
        horizontal=True,
    )

    if timeline_view == "Future Only":
        timeline_chart_df = future_timeline_df.copy()
    elif timeline_view == "Registered Only":
        timeline_chart_df = registered_future_df.copy()
    elif timeline_view == "Completed This Year":
        timeline_chart_df = current_year_completed_df.copy()
    else:
        timeline_chart_df = current_future_df.copy()

    if timeline_chart_df.empty:
        st.info("No current or future timeline entries for this view.")
    else:
        fig_timeline = px.scatter(
            timeline_chart_df,
            x="race_date",
            y="status",
            color="status",
            symbol="race_type",
            size="distance_miles",
            hover_name="race_name",
            hover_data={
                "race_date": "|%Y-%m-%d",
                "runner_name": True,
                "state_name": True,
                "city": True,
                "race_type": True,
                "finish_time": True,
                "avg_mile_pace": True,
                "distance_miles": False,
                "status": False,
            },
            category_orders={"status": ["Completed", "Registered", "Interested"]},
            title="Current & Future Race Timeline",
        )
        fig_timeline.update_traces(marker=dict(line=dict(width=1, color="white")))
        fig_timeline.update_layout(
            height=380,
            margin=dict(l=0, r=0, t=45, b=0),
            xaxis_title="Race Date",
            yaxis_title="Status",
            legend_title="Status / Race Type",
        )
        fig_timeline.add_vline(x=pd.Timestamp(date.today()), line_dash="dash")
        st.plotly_chart(fig_timeline, use_container_width=True)

    if not next_registered.empty:
        st.markdown("#### Registered Race Countdown")
        countdown_df = next_registered.copy()
        countdown_df["Countdown"] = countdown_df["days_from_today"].apply(
            lambda days: "Today" if days == 0 else f"{int(days)} days"
        )
        countdown_display_df = countdown_df[
            [
                "Countdown",
                "runner_name",
                "race_type",
                "race_name",
                "city",
                "state",
                "race_date_display",
                "notes",
            ]
        ].rename(
            columns={
                "runner_name": "Runner",
                "race_type": "Race Type",
                "race_name": "Race Name",
                "city": "City",
                "state": "State",
                "race_date_display": "Date",
                "notes": "Notes",
            }
        )
        st.dataframe(countdown_display_df, use_container_width=True, hide_index=True)
    elif not next_any_future.empty:
        next_future_row = next_any_future.iloc[0]
        st.info(
            f"No registered future races yet. Next interested race: **{next_future_row['race_name']}** "
            f"on **{next_future_row['race_date'].strftime('%Y-%m-%d')}** "
            f"({int(next_future_row['days_from_today'])} days away)."
        )

    upcoming_display_df = next_any_future[
        [
            "status",
            "runner_name",
            "race_type",
            "race_name",
            "city",
            "state",
            "race_date_display",
            "notes",
        ]
    ].rename(
        columns={
            "status": "Status",
            "runner_name": "Runner",
            "race_type": "Race Type",
            "race_name": "Race Name",
            "city": "City",
            "state": "State",
            "race_date_display": "Date",
            "notes": "Notes",
        }
    )

    completed_recent_df = completed_timeline_df.sort_values("race_date", ascending=False).head(10)[
        [
            "status",
            "runner_name",
            "race_type",
            "race_name",
            "city",
            "state",
            "race_date_display",
            "finish_time",
            "avg_mile_pace",
        ]
    ].rename(
        columns={
            "status": "Status",
            "runner_name": "Runner",
            "race_type": "Race Type",
            "race_name": "Race Name",
            "city": "City",
            "state": "State",
            "race_date_display": "Date",
            "finish_time": "Finish Time",
            "avg_mile_pace": "Avg Mile Pace",
        }
    )

    upcoming_tab, completed_tab, yearly_tab = st.tabs(["Upcoming / Planned", "Recent Completed", "Year Buckets"])

    with upcoming_tab:
        if upcoming_display_df.empty:
            st.info("No upcoming registered or interested races yet.")
        else:
            st.dataframe(upcoming_display_df, use_container_width=True, hide_index=True)

    with completed_tab:
        if completed_recent_df.empty:
            st.info("No completed races yet.")
        else:
            st.dataframe(completed_recent_df, use_container_width=True, hide_index=True)

    with yearly_tab:
        if yearly_summary_df.empty:
            st.info("No prior-year completed races to summarize yet.")
        else:
            yearly_display_df = yearly_summary_df.rename(
                columns={
                    "race_year": "Year",
                    "races": "Completed Races",
                    "states": "States",
                    "runners": "Runners",
                    "race_types": "Race Types",
                }
            )[["Year", "Completed Races", "States", "Runners", "Race Types"]]
            st.dataframe(yearly_display_df, use_container_width=True, hide_index=True)

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
    height=550,
)
fig.update_traces(marker_line_color="white", marker_line_width=1)

st.subheader("US Map")
st.caption("Map priority: Completed beats Registered, Registered beats Interested, and states with no entries stay blank.")
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

if selected_state:
    default_state_name = code_to_state_name[selected_state]
    default_index = state_options.index(default_state_name)
else:
    default_index = 0

chosen_state_name = st.selectbox("Choose a state", state_options, index=default_index)

if chosen_state_name != "Select a state...":
    selected_state = state_name_to_code[chosen_state_name]

if selected_state:
    state_runs = filtered_race_df[filtered_race_df["state"] == selected_state].sort_values(
        ["race_date", "runner_name"], ascending=[False, True]
    )

    st.write(f"**{code_to_state_name[selected_state]}**")

    if state_runs.empty:
        st.info("No matching race data for this state under the current filters.")
    else:
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Entries in State", len(state_runs))
        s2.metric("Completed", int((state_runs["status"] == "Completed").sum()))
        s3.metric("Future", int((state_runs["status"].isin(["Registered", "Interested"])).sum()))
        s4.metric("Best Time", best_time_for_group(state_runs))

        display_df = state_runs[
            [
                "status",
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

        completed_state_runs = state_runs[state_runs["status"] == "Completed"].copy()

        if not completed_state_runs.empty:
            st.markdown("#### Quick Summary")
            by_runner = (
                completed_state_runs.groupby("runner_name")
                .agg(
                    races=("race_name", "count"),
                    best_time_seconds=("finish_seconds", "min"),
                )
                .reset_index()
            )
            by_runner["Best Time"] = by_runner["best_time_seconds"].apply(seconds_to_hms)
            by_runner = by_runner.rename(columns={"runner_name": "Runner", "races": "Completed Races"})[
                ["Runner", "Completed Races", "Best Time"]
            ]
            st.dataframe(by_runner, use_container_width=True, hide_index=True)
else:
    st.info("Click a state on the map or choose one from the dropdown to view race details.")

# -------------------------------------------------
# All entries
# -------------------------------------------------
st.markdown("### All Race Entries")
all_display_df = filtered_race_df[
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
st.dataframe(all_display_df, use_container_width=True, hide_index=True)

# -------------------------------------------------
# Footer note
# -------------------------------------------------
st.markdown("---")
st.caption(
    "Next upgrade ideas: timeline layer, SQLite backend, Excel import, household/user accounts, medals/badges, public profiles, and monetized premium plans."
)


