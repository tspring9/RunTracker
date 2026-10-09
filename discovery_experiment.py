"""Static, anonymized review data from the bounded SPR-14 experiment."""

EXPERIMENT_SUMMARY = {
    "experiment_date": "2026-10-09", "source_race": "2026 Hospital Hill Run Half Marathon",
    "source_date": "2026-05-16", "eligible_rows": 1139,
    "sample_seed": "SPR-14|85066|644802|2026-10-09", "external_requests": 88,
    "lookback_floor": "2021-10-09", "candidate_cap_per_runner": 10, "race_day_cap": 5,
}

RUNNERS = [
    {"Runner": "Runner A", "Source profile": "Age 25; female; Lees Summit, MO", "Candidates": 1,
     "High confidence": 0, "Outcome": "No confident history matches"},
    {"Runner": "Runner B", "Source profile": "Age 44; female; Overland Park, KS", "Candidates": 2,
     "High confidence": 2, "Outcome": "Two high-confidence candidate matches"},
]

CANDIDATE_RESULTS = [
    {"Runner": "Runner A", "Candidate": "Lake Cumberland Run/Walk Series, female 20-29", "Date": "2026",
     "Evidence": "Exact-name row; age 23; female; Somerset, KY",
     "Corroborators / conflicts": "Sex agrees; age differs by 2; city and state conflict",
     "Classification": "Ambiguous/rejected", "Public URL": "https://runsignup.com/Series/LCRW/F20-29Results"},
    {"Runner": "Runner B", "Candidate": "Rock The Parkway Half Marathon and 5K", "Date": "2026-04-11",
     "Evidence": "Half Marathon; age 44; female; Overland Park, KS",
     "Corroborators / conflicts": "Age, sex, city, and state agree", "Classification": "High confidence",
     "Public URL": "https://runsignup.com/Race/Results/11488#resultSetId-640128"},
    {"Runner": "Runner B", "Candidate": "Garmin Olathe Marathon, Half Marathon and 10K", "Date": "2026-04-25",
     "Evidence": "Half Marathon; age 44; female; Overland Park, KS",
     "Corroborators / conflicts": "Age, sex, city, and state agree", "Classification": "High confidence",
     "Public URL": "https://runsignup.com/Race/Results/123302#resultSetId-644855"},
]

REQUEST_COUNTS = [
    {"Operation": "Source race capped result-set discovery", "Requests": 19},
    {"Operation": "Source result-set pagination", "Requests": 2},
    {"Operation": "Exact-name site-restricted discovery queries", "Requests": 4},
    {"Operation": "Candidate race resolution", "Requests": 2},
    {"Operation": "Candidate result-set discovery and lookups", "Requests": 58},
    {"Operation": "Public page verification", "Requests": 3},
]


def validate_experiment_data() -> None:
    """Raise ValueError when the review data loses its experiment bounds."""
    if sum(row["Requests"] for row in REQUEST_COUNTS) != EXPERIMENT_SUMMARY["external_requests"]:
        raise ValueError("Request accounting does not match the experiment total")
    for runner in RUNNERS:
        if runner["Candidates"] > EXPERIMENT_SUMMARY["candidate_cap_per_runner"]:
            raise ValueError(f"{runner['Runner']} exceeds the candidate cap")


validate_experiment_data()
