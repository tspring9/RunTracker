"""End-to-end tests for probe_registered_races.py with the network stubbed out.

The probe gets one real run, against a live consent screen, by someone who is not
going to want to debug it. So its whole path -- config, consent URL, pasted
callback, redemption, lookup, report, exit code -- is exercised here with the two
network calls replaced. No credentials and no browser.

    python -m pytest test_probe_registered_races.py -v
"""

import os

import pytest

import probe_registered_races as probe
import runsignup_oauth as oauth


SECRET_ENV = (
    "RUNSIGNUP_OAUTH_CLIENT_ID",
    "RUNSIGNUP_OAUTH_CLIENT_SECRET",
    "RUNSIGNUP_OAUTH_REDIRECT_URI",
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("RUNSIGNUP_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("RUNSIGNUP_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("RUNSIGNUP_OAUTH_REDIRECT_URI", "http://localhost:8501/")


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    # The probe writes its report into the working directory.
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stub_flow(monkeypatch, payload, captured=None):
    """Replace the two network calls, echoing back the state the probe generated."""

    def fake_redeem(config, code, verifier):
        if captured is not None:
            captured.update(code=code, verifier=verifier, config=config)
        return oauth.AccessGrant(
            access_token="jwt-token", expires_in=2592000, scope="rsu_api_read"
        )

    monkeypatch.setattr(oauth, "redeem_code", fake_redeem)
    monkeypatch.setattr(oauth, "registered_races", lambda token, **kw: payload)


def _paste_callback_from_stdout(monkeypatch, capsys, redirect="http://localhost:8501/"):
    """Feed back a callback URL carrying the state from the printed consent URL."""

    def fake_input(_prompt=""):
        printed = capsys.readouterr().out
        # Recover the state the probe just generated from the consent URL it printed.
        from urllib.parse import parse_qs, urlparse

        line = next(ln for ln in printed.splitlines() if ln.startswith(oauth.AUTHORIZE_URL))
        state = parse_qs(urlparse(line).query)["state"][0]
        print(printed, end="")  # keep earlier output visible to later assertions
        return "%s?code=the-code&state=%s" % (redirect, state)

    monkeypatch.setattr("builtins.input", fake_input)


# -------------------------------------------------
# Configuration
# -------------------------------------------------
def test_missing_config_exits_2_and_names_the_keys(monkeypatch, capsys):
    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 2
    err = capsys.readouterr().err
    for name in SECRET_ENV:
        assert name in err
    # Tells the user where to put them, not just that they are missing -- and offers
    # both local sources, since Streamlit Cloud secrets are unreadable from a CLI.
    assert "secrets.toml" in err
    assert "Environment variables" in err


# -------------------------------------------------
# The happy path, and the verdict that decides the ticket
# -------------------------------------------------
def test_past_races_present_reports_history_and_exits_0(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    payload = {
        "races": [
            {"race": {"race_id": 1, "name": "Old Race", "next_date": "5/1/2022"}},
            {"race": {"race_id": 2, "name": "Future Race", "next_date": "12/1/2099"}},
        ]
    }
    captured = {}
    _stub_flow(monkeypatch, payload, captured)
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 0

    out = capsys.readouterr().out
    assert "Past races ARE returned" in out
    assert "1 past, 1 upcoming" in out

    # The code and PKCE verifier actually reached redemption.
    assert captured["code"] == "the-code"
    assert 43 <= len(captured["verifier"]) <= 128

    report = (in_tmp_cwd / probe.REPORT_FILENAME).read_text(encoding="utf-8")
    assert "Past races ARE returned" in report
    assert "races" in report  # the key the list was found under
    assert "| in the past | 1 |" in report


def test_upcoming_only_exits_3_so_the_close_outcome_is_unmissable(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    payload = {"races": [{"race": {"race_id": 1, "name": "Soon", "next_date": "12/1/2099"}}]}
    _stub_flow(monkeypatch, payload)
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 3
    assert "UPCOMING ONLY" in capsys.readouterr().out


def test_no_registrations_is_inconclusive_not_a_close_signal(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    # An account with no registrations proves nothing about the endpoint, so it
    # must exit 0 rather than 3 -- otherwise an empty account reads as "the
    # endpoint hides past races" and the ticket gets closed on no evidence.
    _stub_flow(monkeypatch, {"races": []})
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 0
    assert "inconclusive" in capsys.readouterr().out


# -------------------------------------------------
# The report must stay safe to paste into a ticket
# -------------------------------------------------
def test_report_contains_no_race_names_or_individual_dates(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    payload = {
        "races": [
            {
                "race": {
                    "race_id": 85066,
                    "name": "Hospital Hill Run",
                    "next_date": "6/1/2024",
                    "address": {"city": "Kansas City", "state": "MO"},
                },
                "event": {"name": "Half Marathon"},
            }
        ]
    }
    _stub_flow(monkeypatch, payload)
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])
    probe.main()

    report = (in_tmp_cwd / probe.REPORT_FILENAME).read_text(encoding="utf-8")
    assert "Hospital Hill" not in report
    assert "Kansas City" not in report
    assert "85066" not in report
    # The access token must never be written anywhere.
    assert "jwt-token" not in report


def test_show_races_prints_the_list_but_keeps_it_out_of_the_report(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    payload = {"races": [{"race": {"race_id": 85066, "name": "Hospital Hill Run",
                                   "next_date": "6/1/2024"}}]}
    _stub_flow(monkeypatch, payload)
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py", "--show-races"])
    probe.main()

    assert "Hospital Hill Run" in capsys.readouterr().out
    assert "Hospital Hill" not in (in_tmp_cwd / probe.REPORT_FILENAME).read_text(encoding="utf-8")


# -------------------------------------------------
# Failure paths the user could plausibly hit
# -------------------------------------------------
def test_pasting_something_without_a_query_string_exits_1_with_guidance(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    _stub_flow(monkeypatch, {})
    monkeypatch.setattr("builtins.input", lambda _="": "http://localhost:8501/")
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 1
    assert "full URL" in capsys.readouterr().err


def test_a_denied_consent_is_reported_clearly(configured, in_tmp_cwd, monkeypatch, capsys):
    _stub_flow(monkeypatch, {})
    monkeypatch.setattr(
        "builtins.input",
        lambda _="": "http://localhost:8501/?error=access_denied&error_description=No",
    )
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 1
    assert "access_denied" in capsys.readouterr().err


def test_a_mismatched_state_is_refused(configured, in_tmp_cwd, monkeypatch, capsys):
    # The CSRF check has to survive the probe's own plumbing.
    _stub_flow(monkeypatch, {})
    monkeypatch.setattr(
        "builtins.input", lambda _="": "http://localhost:8501/?code=c&state=not-the-one"
    )
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 1
    assert "state" in capsys.readouterr().err


def test_a_rejected_token_is_reported_rather_than_read_as_no_races(
    configured, in_tmp_cwd, monkeypatch, capsys
):
    # error_code 6 arrives on an HTTP 200. Reported as "no races found" it would
    # look like a real answer to the question this probe exists to settle.
    def fake_registered_races(token, **kw):
        raise oauth.OAuthFlowError("RunSignUp rejected the access token (error 6).")

    monkeypatch.setattr(
        oauth, "redeem_code",
        lambda c, code, v: oauth.AccessGrant("t", 60, "rsu_api_read"))
    monkeypatch.setattr(oauth, "registered_races", fake_registered_races)
    _paste_callback_from_stdout(monkeypatch, capsys)
    monkeypatch.setattr("sys.argv", ["probe_registered_races.py"])

    assert probe.main() == 1
    assert "Lookup failed" in capsys.readouterr().err
    assert not os.path.exists(probe.REPORT_FILENAME)
