"""Unit tests for RunSignUp API caller registration plumbing.

No network. RunSignUp rejects unregistered calls from 2027-01-01, so these
tests pin down two things that are easy to get silently wrong:

1. When a registration IS configured, the ``rsu_api_reg`` parameter and the
   ``X-RSU-API-REG-SECRET`` header actually reach the wire.
2. When it is NOT configured, nothing is added at all -- the pre-registration
   behaviour is byte-identical, which is what makes this safe to merge months
   before the deadline.

    python -m pytest test_runsignup_registration.py -v
"""

import pytest
import requests

import runsignup_results as rsu


@pytest.fixture(autouse=True)
def clear_registration_env(monkeypatch):
    """Start every test unregistered, regardless of the developer's own env."""
    monkeypatch.delenv(rsu.API_REG_TOKEN_SETTING, raising=False)
    monkeypatch.delenv(rsu.API_REG_SECRET_SETTING, raising=False)


class RecordingSession(requests.Session):
    """A Session that records the request instead of sending it."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(self.headers)})
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"race": {"race_id": 1, "name": "Test Race"}}'
        return response


@pytest.fixture
def recording_session(monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr(rsu, "_SESSION", session)
    return session


# -------------------------------------------------
# api_registration / _setting
# -------------------------------------------------
def test_api_registration_unset_is_empty():
    assert rsu.api_registration() == ("", "")


def test_api_registration_reads_env(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    assert rsu.api_registration() == ("4242.abcdef", "s3cr3t")


def test_api_registration_strips_whitespace(monkeypatch):
    # Copy/pasting out of the RunSignUp API Keys page picks up trailing
    # whitespace/newlines easily; an untrimmed token is rejected server-side.
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "  4242.abcdef\n")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "\ts3cr3t ")
    assert rsu.api_registration() == ("4242.abcdef", "s3cr3t")


# -------------------------------------------------
# _get: the registration actually reaches the wire
# -------------------------------------------------
def test_get_sends_no_registration_when_unset(recording_session):
    rsu._get("/race/1")

    call = recording_session.calls[0]
    assert rsu.API_REG_TOKEN_PARAM not in call["params"]
    assert rsu.API_REG_SECRET_HEADER not in call["headers"]
    # Pre-registration behaviour is untouched.
    assert call["params"] == {"format": "json"}


def test_get_sends_token_param_and_secret_header(monkeypatch, recording_session):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")

    rsu._get("/race/1", future_events_only="F")

    call = recording_session.calls[0]
    assert call["params"][rsu.API_REG_TOKEN_PARAM] == "4242.abcdef"
    assert call["headers"][rsu.API_REG_SECRET_HEADER] == "s3cr3t"
    # Existing params survive.
    assert call["params"]["future_events_only"] == "F"
    assert call["params"]["format"] == "json"


def test_get_sends_token_without_secret(monkeypatch, recording_session):
    """Half-configured still sends what it has rather than failing closed."""
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")

    rsu._get("/race/1")

    call = recording_session.calls[0]
    assert call["params"][rsu.API_REG_TOKEN_PARAM] == "4242.abcdef"
    assert rsu.API_REG_SECRET_HEADER not in call["headers"]


def test_registration_picked_up_after_session_was_built(monkeypatch, recording_session):
    """The module-level session is built at import time, before Streamlit
    secrets are readable. Config appearing later must still be sent."""
    rsu._get("/race/1")
    assert rsu.API_REG_TOKEN_PARAM not in recording_session.calls[0]["params"]

    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    rsu._get("/race/1")

    assert recording_session.calls[1]["params"][rsu.API_REG_TOKEN_PARAM] == "4242.abcdef"
    assert recording_session.calls[1]["headers"][rsu.API_REG_SECRET_HEADER] == "s3cr3t"


def test_stale_secret_header_is_dropped(monkeypatch, recording_session):
    """Clearing the secret must clear the header, not leave the old one set."""
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    rsu._get("/race/1")
    assert recording_session.calls[0]["headers"][rsu.API_REG_SECRET_HEADER] == "s3cr3t"

    monkeypatch.delenv(rsu.API_REG_SECRET_SETTING)
    rsu._get("/race/1")
    assert rsu.API_REG_SECRET_HEADER not in recording_session.calls[1]["headers"]


def test_explicit_param_is_not_overridden(monkeypatch, recording_session):
    """A caller passing rsu_api_reg explicitly wins over the configured one."""
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")

    rsu._get("/race/1", **{rsu.API_REG_TOKEN_PARAM: "9999.override"})

    assert recording_session.calls[0]["params"][rsu.API_REG_TOKEN_PARAM] == "9999.override"


# -------------------------------------------------
# _build_session
# -------------------------------------------------
def test_build_session_has_no_secret_header_when_unset():
    assert rsu.API_REG_SECRET_HEADER not in rsu._build_session().headers


def test_build_session_sets_secret_header(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    assert rsu._build_session().headers[rsu.API_REG_SECRET_HEADER] == "s3cr3t"


# -------------------------------------------------
# registration_notes
# -------------------------------------------------
def test_notes_warn_when_unregistered():
    notes = rsu.registration_notes()
    assert len(notes) == 1
    assert rsu.API_REG_ENFORCEMENT_DATE in notes[0]


def test_notes_warn_on_token_without_secret(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    assert any(rsu.API_REG_SECRET_SETTING in note for note in rsu.registration_notes())


def test_notes_warn_on_secret_without_token(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    assert any(rsu.API_REG_TOKEN_SETTING in note for note in rsu.registration_notes())


def test_notes_warn_on_malformed_token(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "no-dot-here")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    assert any("<id>.<token>" in note for note in rsu.registration_notes())


def test_notes_silent_when_fully_configured(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "s3cr3t")
    assert rsu.registration_notes() == []


# -------------------------------------------------
# raise_for_bad_registration
# -------------------------------------------------
# Captured verbatim from a live call with a deliberately bogus token
# (2026-10-09). Both runsignup.com/rest and api.runsignup.com return this.
INVALID_REGISTRATION_BODY = (
    b'{"error":{"error_code":17,"error_msg":"Invalid API caller credentials."},'
    b'"error_details":[{"error_msg":"API caller identification is invalid."}]}'
)


def _response(status_code, content):
    response = requests.Response()
    response.status_code = status_code
    response._content = content
    return response


def test_raises_registration_error_on_code_17():
    with pytest.raises(rsu.RunSignUpRegistrationError) as excinfo:
        rsu.raise_for_bad_registration(_response(400, INVALID_REGISTRATION_BODY))

    message = str(excinfo.value)
    assert "Invalid API caller credentials." in message
    # The message has to point at the config, not at RunSignUp being down.
    assert rsu.API_REG_TOKEN_SETTING in message
    assert rsu.API_REG_SECRET_SETTING in message


def test_registration_error_is_a_runsignup_error():
    """Existing `except RunSignUpError` call sites must still catch it."""
    assert issubclass(rsu.RunSignUpRegistrationError, rsu.RunSignUpError)


def test_registration_error_does_not_leak_the_secret(monkeypatch):
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "supersecretvalue")

    with pytest.raises(rsu.RunSignUpRegistrationError) as excinfo:
        rsu.raise_for_bad_registration(_response(400, INVALID_REGISTRATION_BODY))

    assert "supersecretvalue" not in str(excinfo.value)
    assert "4242.abcdef" not in str(excinfo.value)


@pytest.mark.parametrize(
    "status_code, content",
    [
        # Some other 400 -- a bad event_id, say. Not ours to reinterpret.
        (400, b'{"error":{"error_code":4,"error_msg":"Invalid event id."}}'),
        # Right code, wrong status.
        (500, INVALID_REGISTRATION_BODY),
        # Non-JSON error page.
        (400, b"<html>gateway error</html>"),
        (400, b""),
    ],
)
def test_passes_through_unrelated_errors(status_code, content):
    rsu.raise_for_bad_registration(_response(status_code, content))


def test_get_reports_bad_registration_distinctly(monkeypatch):
    """End to end through _get, which is where call sites actually see it."""
    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "999999.bogus")
    monkeypatch.setenv(rsu.API_REG_SECRET_SETTING, "bogus")

    class RejectingSession(requests.Session):
        def get(self, url, params=None, timeout=None, **kwargs):
            return _response(400, INVALID_REGISTRATION_BODY)

    monkeypatch.setattr(rsu, "_SESSION", RejectingSession())

    with pytest.raises(rsu.RunSignUpRegistrationError):
        rsu.fetch_race(85066)


# -------------------------------------------------
# probe_data_window
# -------------------------------------------------
# The probe exists to answer, empirically, whether RunSignUp's documented
# "one year back" data limit applies to the public results endpoints -- and
# whether registering changes that. These tests do not check the answer (it
# comes from the live API); they check that the probe would report a narrowed
# window honestly instead of hiding it behind a skipped year.
def _fake_race(years, race_name="Probe Race"):
    """A race whose events are one non-virtual 5K per year in ``years``."""
    return {
        "race_id": 1,
        "name": race_name,
        "address": {"city": "Kansas City", "state": "MO"},
        "events": [
            {
                "event_id": 1000 + year,
                "race_event_days_id": 2000 + year,
                "name": "5K",
                "start_time": f"06/01/{year} 07:00",
            }
            for year in years
        ],
    }


@pytest.fixture
def probe_api(monkeypatch):
    """Stub the three endpoints probe_data_window walks. Returns a recorder."""

    state = {"years_with_results": set(), "race": _fake_race([]), "result_calls": []}

    def fake_fetch_race(race_id):
        return state["race"]

    def fake_list_result_sets(race_id, event_id):
        year = event_id - 1000
        if year not in state["years_with_results"]:
            return []
        return [{"result_set_id": 7, "name": "Overall", "public": True, "preliminary": False}]

    def fake_fetch_results(race_id, event_id, result_set_id, **kwargs):
        state["result_calls"].append(event_id)
        return [{"first_name": "A", "last_name": "B", "chip_time": "25:00"}]

    monkeypatch.setattr(rsu, "fetch_race", fake_fetch_race)
    monkeypatch.setattr(rsu, "list_result_sets", fake_list_result_sets)
    monkeypatch.setattr(rsu, "fetch_results", fake_fetch_results)
    return state


def test_probe_reports_full_history_when_available(probe_api):
    probe_api["race"] = _fake_race([2022, 2023, 2024, 2025, 2026])
    probe_api["years_with_results"] = {2022, 2023, 2024, 2025, 2026}

    report = rsu.probe_data_window(race_id=1)

    assert [probe["year"] for probe in report["probes"]] == [2026, 2025, 2024, 2023, 2022]
    assert all(probe["status"] == "ok" for probe in report["probes"])
    assert report["oldest_retrievable_date"] == "2022-06-01"
    # Well past a year, i.e. the documented limit is not being applied here.
    assert report["days_back"] > 365


def test_probe_reports_a_one_year_window(probe_api):
    """The outcome we are actually watching for: history clipped to 12 months."""
    probe_api["race"] = _fake_race([2022, 2023, 2024, 2025, 2026])
    probe_api["years_with_results"] = {2026}

    report = rsu.probe_data_window(race_id=1)

    assert report["oldest_retrievable_date"] == "2026-06-01"
    assert report["days_back"] < 400
    # The walled-off years are still listed, so the shape of the limit is
    # visible rather than silently absent from the report.
    assert [probe["status"] for probe in report["probes"]] == [
        "ok",
        "no-public-result-set",
        "no-public-result-set",
        "no-public-result-set",
        "no-public-result-set",
    ]


def test_probe_records_oldest_listed_event_separately(probe_api):
    """Listed-but-unretrievable history is the main false-positive risk.

    Hospital Hill lists events back to 2011 but publishes no result sets
    before 2024, which looks identical to an API window. Keeping both numbers
    means the report cannot be misread as "the limit is 2.4 years".
    """
    probe_api["race"] = _fake_race([2011, 2025, 2026])
    probe_api["years_with_results"] = {2025, 2026}

    report = rsu.probe_data_window(race_id=1)

    assert report["oldest_event_date"] == "2011-06-01"
    assert report["oldest_retrievable_date"] == "2025-06-01"


def test_probe_is_bounded_by_max_years(probe_api):
    probe_api["race"] = _fake_race(range(2011, 2027))
    probe_api["years_with_results"] = set(range(2011, 2027))

    report = rsu.probe_data_window(race_id=1, max_years=3)

    assert [probe["year"] for probe in report["probes"]] == [2026, 2025, 2024]


def test_probe_skips_virtual_events(probe_api):
    race = _fake_race([2026])
    race["events"].insert(0, {
        "event_id": 9999,
        "race_event_days_id": 9999,
        "name": "Virtual 5K",
        "start_time": "06/01/2026 07:00",
    })
    probe_api["race"] = race
    probe_api["years_with_results"] = {2026, 8999}

    report = rsu.probe_data_window(race_id=1)

    # 8999 == 9999 - 1000, i.e. the virtual event, which must not be probed.
    assert probe_api["result_calls"] == [3026]


def test_probe_tolerates_per_event_errors(probe_api, monkeypatch):
    """One dud event must not abort the whole probe."""
    probe_api["race"] = _fake_race([2025, 2026])
    probe_api["years_with_results"] = {2025, 2026}

    def flaky_list_result_sets(race_id, event_id):
        if event_id == 3026:
            raise rsu.RunSignUpError("RunSignUp HTTP 404 for /race/1/results/get-result-sets")
        return [{"result_set_id": 7, "name": "Overall", "public": True, "preliminary": False}]

    monkeypatch.setattr(rsu, "list_result_sets", flaky_list_result_sets)

    report = rsu.probe_data_window(race_id=1)

    statuses = {probe["year"]: probe["status"] for probe in report["probes"]}
    assert statuses[2026] == "error"
    assert statuses[2025] == "ok"
    assert report["oldest_retrievable_date"] == "2025-06-01"


def test_probe_lets_registration_errors_out(probe_api, monkeypatch):
    """A rejected registration is config, not a data-window finding.

    Swallowing it the way per-event errors are swallowed would render the
    report as "no history available anywhere", which points at exactly the
    wrong cause.
    """
    probe_api["race"] = _fake_race([2025, 2026])

    def rejecting_list_result_sets(race_id, event_id):
        raise rsu.RunSignUpRegistrationError("RunSignUp rejected our API caller registration")

    monkeypatch.setattr(rsu, "list_result_sets", rejecting_list_result_sets)

    with pytest.raises(rsu.RunSignUpRegistrationError):
        rsu.probe_data_window(race_id=1)


def test_probe_records_registration_state(probe_api, monkeypatch):
    """So a before/after pair of reports can be told apart later."""
    probe_api["race"] = _fake_race([2026])
    probe_api["years_with_results"] = {2026}

    assert rsu.probe_data_window(race_id=1)["registered"] is False

    monkeypatch.setenv(rsu.API_REG_TOKEN_SETTING, "4242.abcdef")
    assert rsu.probe_data_window(race_id=1)["registered"] is True
