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
