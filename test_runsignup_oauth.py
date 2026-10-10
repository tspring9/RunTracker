"""Unit tests for the pure helpers in runsignup_oauth.py.

No network and no credentials -- every test here runs on a laptop with no OAuth
app registered, which is the point: the flow's logic is checkable before anyone
has consented to anything.

Three areas carry real risk and get the most attention:

* **PKCE and state.** Getting these subtly wrong produces a flow that still
  works, so a bug here is invisible in manual testing and only matters when
  someone attacks it.
* **The two error conventions.** The token endpoint uses HTTP status codes plus
  ``{"error": "..."}``; the REST endpoint uses HTTP 200 plus
  ``{"error": {"error_code": ...}}``. Confusing them means a rejected token is
  read as a successful empty response.
* **Parsing an undocumented payload.** The response schema is published as bare
  ``{"type": "object"}``, so the parser is tolerant by design -- these tests pin
  down how tolerant, and that it never invents a registration it did not see.

    python -m pytest test_runsignup_oauth.py -v
"""

import base64
import hashlib
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

import runsignup_oauth as oauth


CONFIG = oauth.OAuthConfig(
    client_id="test-client",
    client_secret="test-secret",
    redirect_uri="http://localhost:8501/",
)


# -------------------------------------------------
# PKCE
# -------------------------------------------------
def test_code_verifier_matches_rfc7636_alphabet_and_length():
    # RFC 7636: 43-128 characters from [A-Za-z0-9-._~]. RunSignUp's own spec
    # repeats the 43-128 bound, and a verifier outside it is rejected at redeem
    # time -- after the user has already consented, which is the worst moment.
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    for _ in range(50):
        verifier = oauth.generate_code_verifier()
        assert 43 <= len(verifier) <= 128
        assert set(verifier) <= allowed


def test_code_verifiers_and_states_are_unique_per_call():
    # A reused verifier or state across sessions would break the CSRF binding.
    assert len({oauth.generate_code_verifier() for _ in range(200)}) == 200
    assert len({oauth.generate_state() for _ in range(200)}) == 200


def test_code_challenge_is_unpadded_urlsafe_sha256():
    verifier = "a" * 43
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    challenge = oauth.code_challenge_for(verifier)
    assert challenge == expected
    # Padding or the non-urlsafe alphabet would survive a naive round-trip test
    # but fail server-side verification.
    assert "=" not in challenge
    assert "+" not in challenge and "/" not in challenge


# -------------------------------------------------
# Authorize URL
# -------------------------------------------------
def test_authorize_url_targets_the_host_that_actually_serves_it():
    # api.runsignup.com/Profile/OAuth2/RequestGrant is a hard nginx 404 even
    # though the OAuth2 spec declares api.runsignup.com as its only server.
    # Following the spec here sends the runner to a 404 instead of consent.
    url = oauth.build_authorize_url(CONFIG, "state-123", "challenge-abc")
    assert url.startswith("https://runsignup.com/Profile/OAuth2/RequestGrant?")
    assert "api.runsignup.com" not in url


def test_authorize_url_always_sends_s256():
    # RunSignUp documents code_challenge_method as defaulting to "plain" when
    # omitted, which would put the verifier on the wire in clear.
    query = parse_qs(urlparse(oauth.build_authorize_url(CONFIG, "s", "c")).query)
    assert query["code_challenge_method"] == ["S256"]


def test_authorize_url_carries_every_required_parameter():
    query = parse_qs(urlparse(oauth.build_authorize_url(CONFIG, "state-123", "chal")).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://localhost:8501/"]
    assert query["scope"] == ["rsu_api_read"]
    assert query["state"] == ["state-123"]
    assert query["code_challenge"] == ["chal"]


def test_authorize_url_does_not_leak_the_client_secret():
    # The consent URL goes in the user's address bar and browser history.
    assert "test-secret" not in oauth.build_authorize_url(CONFIG, "s", "c")


def test_begin_authorization_binds_state_and_verifier_to_the_url():
    pending = oauth.begin_authorization(CONFIG)
    query = parse_qs(urlparse(pending.url).query)
    assert query["state"] == [pending.state]
    assert query["code_challenge"] == [oauth.code_challenge_for(pending.code_verifier)]
    # The verifier itself must never appear in the authorize request -- that is
    # the entire difference between PKCE and no PKCE.
    assert pending.code_verifier not in pending.url


# -------------------------------------------------
# Callback handling
# -------------------------------------------------
def test_callback_returns_code_when_state_matches():
    params = {"code": "auth-code-1", "state": "expected"}
    assert oauth.authorization_code_from_callback(params, "expected") == "auth-code-1"


@pytest.mark.parametrize(
    "params, expected_state",
    [
        # Attacker-supplied code with a state the session never issued.
        ({"code": "c", "state": "attacker"}, "expected"),
        # State missing entirely.
        ({"code": "c"}, "expected"),
        # No state was ever stored -- a blank expectation must not match blank.
        ({"code": "c", "state": ""}, ""),
    ],
)
def test_callback_rejects_state_mismatch(params, expected_state):
    with pytest.raises(oauth.OAuthFlowError, match="state"):
        oauth.authorization_code_from_callback(params, expected_state)


def test_callback_surfaces_a_denied_consent():
    params = {"error": "access_denied", "error_description": "User denied", "state": "expected"}
    with pytest.raises(oauth.OAuthFlowError, match="access_denied"):
        oauth.authorization_code_from_callback(params, "expected")


def test_callback_rejects_a_missing_code():
    with pytest.raises(oauth.OAuthFlowError, match="no authorization code"):
        oauth.authorization_code_from_callback({"state": "expected"}, "expected")


# -------------------------------------------------
# Token endpoint: OAuth2-style errors (HTTP status + flat "error")
# -------------------------------------------------
class FakeResponse:
    def __init__(self, status_code, payload=None, text="body"):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_token_error_is_surfaced_with_its_description():
    # The real response for bad client credentials, observed while probing.
    response = FakeResponse(
        401, {"error": "invalid_client", "error_description": "Client authentication failed"}
    )
    with pytest.raises(oauth.OAuthFlowError, match="invalid_client"):
        oauth._raise_for_oauth_error(response)


def test_token_error_is_caught_even_on_http_200():
    # Belt and braces: an error body wins over a 200 status.
    response = FakeResponse(200, {"error": "invalid_grant"})
    with pytest.raises(oauth.OAuthFlowError, match="invalid_grant"):
        oauth._raise_for_oauth_error(response)


def test_non_json_token_response_raises_rather_than_crashing():
    with pytest.raises(oauth.OAuthFlowError, match="non-JSON"):
        oauth._raise_for_oauth_error(FakeResponse(502, None))


def test_json_but_not_an_object_raises_here_not_deeper_in():
    # Otherwise grant_from_token_response hits .get on a list and the user sees
    # an AttributeError traceback instead of an explanation.
    with pytest.raises(oauth.OAuthFlowError, match="not a token object"):
        oauth._raise_for_oauth_error(FakeResponse(200, ["unexpected"]))


def test_grant_drops_the_refresh_token():
    # RunSignUp issues 20-year refresh tokens. The access token already lasts a
    # month, longer than any Streamlit session, so we never hold one.
    payload = {
        "access_token": "jwt-access",
        "token_type": "Bearer",
        "expires_in": 2592000,
        "refresh_token": "twenty-year-credential",
        "scope": "rsu_api_read",
    }
    grant = oauth.grant_from_token_response(payload)

    assert grant.access_token == "jwt-access"
    assert grant.expires_in == 2592000
    assert grant.scope == "rsu_api_read"
    # Not merely unset -- there is no field that could hold it.
    assert not hasattr(grant, "refresh_token")
    assert "twenty-year-credential" not in repr(grant)


def test_grant_requires_an_access_token():
    with pytest.raises(oauth.OAuthFlowError, match="no access_token"):
        oauth.grant_from_token_response({"token_type": "Bearer", "expires_in": 60})


def test_grant_tolerates_a_junk_expires_in():
    # Missing/garbage lifetime must not break a usable token.
    assert oauth.grant_from_token_response({"access_token": "t", "expires_in": "soon"}).expires_in == 0


# -------------------------------------------------
# REST endpoint: in-band errors (HTTP 200 + nested "error")
# -------------------------------------------------
def test_rest_rejected_token_is_an_error_not_an_empty_result():
    # Observed shape. Read as success, this would look like "no races found",
    # which is the most misleading possible outcome for this feature.
    payload = {"error": {"error_code": 6, "error_msg": "Key authentication failed"}}
    with pytest.raises(oauth.OAuthFlowError, match="rejected the access token"):
        oauth._raise_for_rest_error(payload, "registered-races")


def test_rest_missing_credential_is_distinguished_from_a_rejected_one():
    # 7 vs 6 is the difference between "we sent nothing" (our bug) and "they
    # said no" (expired token), and they need different messages.
    payload = {"error": {"error_code": 7, "error_msg": "Permission Denied"}}
    with pytest.raises(oauth.OAuthFlowError, match="no credential"):
        oauth._raise_for_rest_error(payload, "registered-races")


def test_rest_unknown_error_code_still_raises():
    payload = {"error": {"error_code": 99, "error_msg": "Something new"}}
    with pytest.raises(oauth.OAuthFlowError, match="99"):
        oauth._raise_for_rest_error(payload, "registered-races")


def test_rest_success_passes_through():
    payload = {"races": []}
    assert oauth._raise_for_rest_error(payload, "registered-races") == payload


# -------------------------------------------------
# Parsing the undocumented payload
# -------------------------------------------------
def test_parses_the_documented_nesting_of_a_runsignup_race_listing():
    # Shape modelled on RunSignUp's other race endpoints: a wrapper key, each
    # record nesting the race under "race" with address fields inside "address".
    payload = {
        "races": [
            {
                "race": {
                    "race_id": 85066,
                    "name": "Hospital Hill Run",
                    "next_date": "6/1/2024",
                    "address": {"city": "Kansas City", "state": "MO"},
                },
                "event": {"name": "Half Marathon", "start_time": "6/1/2024 07:00"},
            }
        ]
    }
    (reg,) = oauth.parse_registrations(payload)

    assert reg.race_id == 85066
    assert reg.race_name == "Hospital Hill Run"
    assert reg.race_date == "2024-06-01"
    assert reg.event_name == "Half Marathon"
    assert reg.city == "Kansas City"
    assert reg.state == "MO"


def test_parses_flat_records_too():
    # The schema is unpublished, so an un-nested shape is equally plausible.
    payload = {"registrations": [{"race_id": "123", "race_name": "Flat Race", "race_date": "2023-04-05"}]}
    (reg,) = oauth.parse_registrations(payload)
    assert (reg.race_id, reg.race_name, reg.race_date) == (123, "Flat Race", "2023-04-05")


def test_parses_a_bare_top_level_list():
    (reg,) = oauth.parse_registrations([{"race_id": 7, "name": "Listed"}])
    assert reg.race_id == 7


def test_falls_back_to_the_longest_list_under_an_unexpected_key():
    # If RunSignUp names the key something we did not predict, degrade to
    # "parsed something" rather than silently reporting zero registrations.
    payload = {
        "surprise_key": [{"race_id": 1}, {"race_id": 2}],
        "metadata": [{"unrelated": True}],
    }
    assert sorted(r.race_id for r in oauth.parse_registrations(payload)) == [1, 2]


def test_falls_back_to_the_event_date_when_the_race_has_none():
    payload = {"races": [{"race": {"race_id": 5}, "event": {"start_time": "12/25/2022 08:30"}}]}
    assert oauth.parse_registrations(payload)[0].race_date == "2022-12-25"


def test_records_without_a_race_id_are_skipped():
    # race_id is the one field downstream per-race discovery cannot work without,
    # so a record lacking it is dropped rather than carried as race_id=0.
    payload = {"races": [{"name": "No id here"}, {"race_id": "not-a-number"}, {"race_id": 9}]}
    assert [r.race_id for r in oauth.parse_registrations(payload)] == [9]


def test_a_registration_with_an_unparseable_date_is_kept_but_undated():
    # Dropping it would hide a real registration; a blank date is honest and the
    # summary counts it separately.
    payload = {"races": [{"race_id": 4, "race_date": "sometime"}]}
    (reg,) = oauth.parse_registrations(payload)
    assert reg.race_id == 4
    assert reg.race_date == ""


@pytest.mark.parametrize("payload", [{}, None, "", 0, [], {"races": []}, {"races": "nope"}])
def test_unparseable_payloads_yield_no_registrations_without_raising(payload):
    assert oauth.parse_registrations(payload) == []


# -------------------------------------------------
# registration_list_key -- must agree with the parser
# -------------------------------------------------
@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"races": [{"race_id": 1}]}, "races"),
        ({"registrations": [{"race_id": 1}]}, "registrations"),
        ([{"race_id": 1}], oauth.TOP_LEVEL_LIST),
        ({"weird_name": [{"race_id": 1}]}, "weird_name"),
        ({"nothing": 1}, ""),
        ({}, ""),
        (None, ""),
    ],
)
def test_registration_list_key_reports_the_source(payload, expected):
    assert oauth.registration_list_key(payload) == expected


def test_reported_key_is_the_one_the_parser_actually_read():
    # The diagnostic report prints this key. If it disagreed with the parser,
    # the report would send the next person looking at the wrong field. A known
    # key must win over a longer unknown list.
    payload = {
        "decoys": [{"race_id": 10}, {"race_id": 11}, {"race_id": 12}],
        "races": [{"race_id": 1}],
    }
    assert oauth.registration_list_key(payload) == "races"
    assert [r.race_id for r in oauth.parse_registrations(payload)] == [1]


# -------------------------------------------------
# describe_payload_shape -- the diagnostic's eyes
# -------------------------------------------------
def test_shape_description_reports_structure_without_values():
    # This string gets pasted into a ticket, so it must not carry the runner's
    # race names or dates.
    payload = {"races": [{"race_id": 85066, "name": "Hospital Hill Run"}], "num_results": 1}
    shape = oauth.describe_payload_shape(payload)

    assert "races: list[1]" in shape
    assert "num_results: int" in shape
    assert "Hospital Hill" not in shape
    assert "85066" not in shape


def test_shape_description_handles_a_top_level_list():
    assert oauth.describe_payload_shape([{"a": 1}]).startswith("list[1]")


def test_shape_description_handles_an_empty_payload():
    assert oauth.describe_payload_shape({}) == "{}"
    assert "empty" in oauth.describe_payload_shape([])


# -------------------------------------------------
# summarize_registrations -- the verdict that decides SPR-20
# -------------------------------------------------
TODAY = date(2026, 10, 9)


def _regs(*dates):
    return [oauth.Registration(race_id=i, race_name="r", race_date=d) for i, d in enumerate(dates)]


def test_summary_counts_past_and_upcoming_around_today():
    summary = oauth.summarize_registrations(
        _regs("2022-05-01", "2024-06-01", "2026-12-25", ""), today=TODAY
    )
    assert summary.total == 4
    assert summary.past == 2
    assert summary.upcoming == 1
    assert summary.undated == 1
    assert summary.earliest == "2022-05-01"
    assert summary.latest == "2026-12-25"
    assert summary.returns_past_races is True


def test_a_race_today_counts_as_upcoming_not_past():
    # Results for a race happening today do not exist yet, which is what "past"
    # has to mean for a race-history feature.
    summary = oauth.summarize_registrations(_regs("2026-10-09"), today=TODAY)
    assert (summary.past, summary.upcoming) == (0, 1)


def test_verdict_calls_out_upcoming_only_as_the_close_condition():
    summary = oauth.summarize_registrations(_regs("2026-12-01", "2027-01-01"), today=TODAY)
    assert summary.returns_past_races is False
    assert "UPCOMING ONLY" in summary.verdict
    assert "close SPR-20" in summary.verdict


def test_verdict_confirms_history_when_past_races_appear():
    summary = oauth.summarize_registrations(_regs("2022-05-01", "2026-12-01"), today=TODAY)
    assert "Past races ARE returned" in summary.verdict


def test_empty_result_is_reported_as_inconclusive_not_as_upcoming_only():
    # An account with no registrations proves nothing about the endpoint, and
    # must not be mistaken for evidence that it hides past races.
    summary = oauth.summarize_registrations([], today=TODAY)
    assert summary.total == 0
    assert "inconclusive" in summary.verdict
    assert "UPCOMING ONLY" not in summary.verdict


# -------------------------------------------------
# Configuration
# -------------------------------------------------
def test_oauth_config_names_every_missing_key(monkeypatch):
    for name in (
        "RUNSIGNUP_OAUTH_CLIENT_ID",
        "RUNSIGNUP_OAUTH_CLIENT_SECRET",
        "RUNSIGNUP_OAUTH_REDIRECT_URI",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(oauth.OAuthConfigError) as excinfo:
        oauth.oauth_config()

    message = str(excinfo.value)
    assert "RUNSIGNUP_OAUTH_CLIENT_ID" in message
    assert "RUNSIGNUP_OAUTH_CLIENT_SECRET" in message
    assert "RUNSIGNUP_OAUTH_REDIRECT_URI" in message


def test_oauth_config_reads_the_environment_and_strips_whitespace(monkeypatch):
    # Pasting into a secrets file or shell commonly leaves a trailing newline,
    # and redirect_uri has to match RunSignUp's registered value exactly.
    monkeypatch.setenv("RUNSIGNUP_OAUTH_CLIENT_ID", " id-1 ")
    monkeypatch.setenv("RUNSIGNUP_OAUTH_CLIENT_SECRET", "secret-1\n")
    monkeypatch.setenv("RUNSIGNUP_OAUTH_REDIRECT_URI", " http://localhost:8501/ ")

    config = oauth.oauth_config()
    assert config == oauth.OAuthConfig("id-1", "secret-1", "http://localhost:8501/")


def test_is_configured_is_false_without_credentials(monkeypatch):
    for name in (
        "RUNSIGNUP_OAUTH_CLIENT_ID",
        "RUNSIGNUP_OAUTH_CLIENT_SECRET",
        "RUNSIGNUP_OAUTH_REDIRECT_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    assert oauth.is_configured() is False
