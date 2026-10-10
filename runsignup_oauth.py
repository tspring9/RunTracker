"""RunSignUp OAuth 2.0 client for the connected track (SPR-20).

The connected track replaces the anonymous name+DOB sweep's *discovery* step with
a lookup against the runner's own registration list. Only an OAuth access token
authenticates that lookup -- see ``docs/connected-track-oauth.md`` for why none of
RunSignUp's other three credential types can stand in.

Four things here were found by probing and contradict the published spec, so do not
"simplify" them back toward the documentation:

1. **The authorize endpoint is on ``runsignup.com``, not ``api.runsignup.com``.**
   The OAuth2 spec declares a single server of ``https://api.runsignup.com``, but
   ``https://api.runsignup.com/Profile/OAuth2/RequestGrant`` is a hard nginx 404.
   Only the ``runsignup.com`` host serves it, which makes sense -- it is a
   browser-facing consent page, not an API call. Following the spec literally sends
   the runner to a 404 instead of a consent screen.
2. **Two different error conventions in one flow.** The token endpoint behaves like
   a normal OAuth2 server: real HTTP status codes with ``{"error": "invalid_client",
   "error_description": ...}``. The REST endpoints do the RunSignUp thing instead:
   **HTTP 200** with ``{"error": {"error_code": 6, "error_msg": ...}}`` in the body.
   A single error handler for both would miss one of them, so there are two.
3. **``registered-races`` does accept Bearer tokens** even though its own OpenAPI
   document lists only ``apiKey``/``apiSecret`` under ``security``. Unauthenticated
   gives ``error_code`` 7 (Permission Denied); a bogus Bearer gives 6 (Key
   authentication failed), i.e. the header was parsed and the token validated.
4. **The response schema is published as bare ``{"type": "object"}``** -- no
   properties at all. So ``parse_registrations`` is deliberately tolerant about
   where the list lives, and ``describe_payload_shape`` exists to report what
   actually came back. See "The load-bearing unknown" in the design doc.

Privacy rules this module enforces rather than documents:

* ``redeem_code`` **drops the refresh token on receipt** and has no way to return
  it. RunSignUp issues refresh tokens with a *20 year* lifetime, which is
  effectively a permanent credential for the runner's account; the access token
  already lasts a month, far longer than any Streamlit session, so there is
  nothing to refresh. Never holding it beats storing it carefully.
* Nothing here writes to disk, logs a token, or touches ``@st.cache_data``.
* ``OAuthFlowError`` messages are built from error codes and descriptions only,
  never from the request URL -- the authorize callback URL carries the single-use
  authorization code.

Runnable without Streamlit, which is how the diagnostic probe uses it:

    python probe_registered_races.py
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode

import requests

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
# Browser-facing consent page. NOT api.runsignup.com -- see note 1 above.
AUTHORIZE_URL = "https://runsignup.com/Profile/OAuth2/RequestGrant"

# Token exchange answers on both hosts; api.* matches the spec's declared server.
TOKEN_URL = "https://api.runsignup.com/rest/v2/auth/auth-code-redemption.json"

# The lookup this whole track exists for. Answers on both hosts; runsignup.com
# matches runsignup_results.RESULTS_API_HOST so both clients use one origin.
REGISTERED_RACES_URL = "https://runsignup.com/rest/user/registered-races"

# Narrowest scope that covers registered-races. There is no results- or
# profile-specific scope, so this necessarily grants more than we need; the
# consent UI says so plainly rather than burying it.
READ_SCOPE = "rsu_api_read"

DEFAULT_TIMEOUT = 30

# PKCE: RFC 7636 allows 43-128 chars of [A-Za-z0-9-._~]. 32 random bytes of
# unpadded urlsafe base64 is exactly 43 and uses only that alphabet.
_PKCE_ENTROPY_BYTES = 32

# RunSignUp in-band error codes seen while probing this endpoint.
ERROR_NOT_LOGGED_IN = 7       # "Permission Denied" -- no credential at all
ERROR_KEY_AUTH_FAILED = 6     # "Key authentication failed" -- token rejected


class OAuthConfigError(RuntimeError):
    """Raised when client id / secret / redirect URI are not configured."""


class OAuthFlowError(RuntimeError):
    """Raised when RunSignUp rejects a step of the flow."""


# -------------------------------------------------
# Configuration
# -------------------------------------------------
@dataclass(frozen=True)
class OAuthConfig:
    """Everything needed to start and finish the flow."""

    client_id: str
    client_secret: str
    redirect_uri: str


def _secret(name: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then the environment.

    Mirrors ``app.get_secret`` but imports Streamlit lazily so this module stays
    importable from a plain CLI, which is how the diagnostic probe runs it.
    """
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        # No Streamlit, no secrets.toml, or no such key -- fall through to env.
        pass
    return os.getenv(name, default).strip()


CONFIG_KEYS = (
    "RUNSIGNUP_OAUTH_CLIENT_ID",
    "RUNSIGNUP_OAUTH_CLIENT_SECRET",
    "RUNSIGNUP_OAUTH_REDIRECT_URI",
)


def configured_values() -> dict[str, str]:
    """The three config values as found in secrets/env, ``''`` where absent.

    Exposed separately from :func:`oauth_config` so a caller can see *which*
    values are missing without parsing an exception message. The probe uses this
    to prompt for only the gaps.
    """
    return {name: _secret(name) for name in CONFIG_KEYS}


def oauth_config() -> OAuthConfig:
    """Load the OAuth client configuration, or explain exactly what is missing.

    ``RUNSIGNUP_OAUTH_REDIRECT_URI`` is read rather than hard-coded because the
    registered redirect URI differs per environment -- ``http://localhost:8501/``
    for local development, the Streamlit Cloud URL when deployed -- and RunSignUp
    requires an *exact* match. A mismatch fails at the consent screen before any
    token exists, so it is worth being explicit about.
    """
    values = configured_values()
    client_id = values["RUNSIGNUP_OAUTH_CLIENT_ID"]
    client_secret = values["RUNSIGNUP_OAUTH_CLIENT_SECRET"]
    redirect_uri = values["RUNSIGNUP_OAUTH_REDIRECT_URI"]

    missing = [name for name in CONFIG_KEYS if not values[name]]
    if missing:
        raise OAuthConfigError(
            "Missing RunSignUp OAuth configuration: "
            + ", ".join(missing)
            + ". Set these in .streamlit/secrets.toml locally or in the Streamlit "
            "Cloud secrets manager when deployed."
        )

    return OAuthConfig(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


def is_configured() -> bool:
    """True when the OAuth app credentials are present, for gating the UI."""
    try:
        oauth_config()
    except OAuthConfigError:
        return False
    return True


# -------------------------------------------------
# Step 1: authorization request (PKCE)
# -------------------------------------------------
@dataclass(frozen=True)
class PendingAuthorization:
    """A started flow. ``state`` and ``code_verifier`` must survive the redirect."""

    url: str
    state: str
    code_verifier: str


def generate_code_verifier() -> str:
    """A PKCE code verifier: 43 chars from the RFC 7636 unreserved alphabet."""
    raw = secrets.token_bytes(_PKCE_ENTROPY_BYTES)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def code_challenge_for(verifier: str) -> str:
    """S256 challenge: unpadded urlsafe base64 of the verifier's SHA-256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Opaque CSRF token binding the callback to the session that started it."""
    return secrets.token_urlsafe(24)


def build_authorize_url(
    config: OAuthConfig,
    state: str,
    code_challenge: str,
    scope: str = READ_SCOPE,
) -> str:
    """Build the consent URL.

    ``code_challenge_method`` is always sent: RunSignUp's spec says it **defaults
    to ``plain``** when omitted, which would put the verifier on the wire in clear
    and defeat the point of PKCE.
    """
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def begin_authorization(config: OAuthConfig, scope: str = READ_SCOPE) -> PendingAuthorization:
    """Start the flow: fresh state + PKCE pair, and the URL to send the runner to."""
    state = generate_state()
    verifier = generate_code_verifier()
    url = build_authorize_url(config, state, code_challenge_for(verifier), scope=scope)
    return PendingAuthorization(url=url, state=state, code_verifier=verifier)


def authorization_code_from_callback(params: dict, expected_state: str) -> str:
    """Pull the ``code`` out of callback params, rejecting errors and bad state.

    The state check is the CSRF defence and is treated as mandatory even though
    RunSignUp only marks ``state`` "recommended": without it, an attacker-supplied
    code could be redeemed into the victim's session.
    """
    if params.get("error"):
        # error_description is RunSignUp's text, safe to surface. The full
        # callback URL is not -- it carries the authorization code.
        detail = params.get("error_description") or ""
        raise OAuthFlowError(
            f"RunSignUp denied the authorization request: {params['error']}"
            + (f" ({detail})" if detail else "")
        )

    returned_state = params.get("state") or ""
    if not expected_state or returned_state != expected_state:
        raise OAuthFlowError(
            "Authorization state did not match the value this session sent. "
            "Start the connection again."
        )

    code = params.get("code") or ""
    if not code:
        raise OAuthFlowError("RunSignUp's callback carried no authorization code.")
    return code


# -------------------------------------------------
# Step 2: redeem the code
# -------------------------------------------------
@dataclass(frozen=True)
class AccessGrant:
    """A redeemed access token.

    There is deliberately **no** ``refresh_token`` field. See the module docstring:
    RunSignUp's refresh tokens last 20 years, and a one-month access token already
    outlives any Streamlit session, so the refresh token is dropped on receipt and
    this type gives callers no way to hold one.
    """

    access_token: str
    expires_in: int
    scope: str
    token_type: str = "Bearer"


def _raise_for_oauth_error(response: requests.Response) -> dict:
    """Decode a token-endpoint response, raising on the OAuth2 error shape.

    Unlike the REST endpoints, this one uses real HTTP status codes and the
    standard ``{"error", "error_description"}`` body.
    """
    try:
        payload = response.json()
    except ValueError:
        raise OAuthFlowError(
            f"RunSignUp's token endpoint returned HTTP {response.status_code} "
            "with a non-JSON body."
        ) from None

    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        detail = payload.get("error_description") or payload.get("hint") or ""
        raise OAuthFlowError(
            f"RunSignUp rejected the token request: {error}"
            + (f" -- {detail}" if detail else "")
        )

    if response.status_code != 200:
        raise OAuthFlowError(f"RunSignUp's token endpoint returned HTTP {response.status_code}.")

    if not isinstance(payload, dict):
        # A 200 carrying a JSON list or scalar. Caught here so the caller fails
        # with this message rather than an AttributeError deeper in.
        raise OAuthFlowError(
            f"RunSignUp's token endpoint returned a JSON {type(payload).__name__}, "
            "not a token object."
        )

    return payload


def grant_from_token_response(payload: dict) -> AccessGrant:
    """Map a ``TokenResponse`` onto :class:`AccessGrant`, discarding the refresh token."""
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise OAuthFlowError("RunSignUp's token response contained no access_token.")

    try:
        expires_in = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0

    return AccessGrant(
        access_token=access_token,
        expires_in=expires_in,
        scope=str(payload.get("scope") or ""),
        token_type=str(payload.get("token_type") or "Bearer"),
    )


def redeem_code(config: OAuthConfig, code: str, code_verifier: str) -> AccessGrant:
    """Exchange an authorization code for an access token.

    The code is single-use and expires in 5 minutes, so this gets one attempt --
    no retries, since a replayed code fails and the error would be misleading.
    """
    form = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        # Passed through verbatim. RunSignUp describes this field as "base64
        # encoded", which refers to the shape of the secret it issues -- do not
        # base64 it again here.
        "client_secret": config.client_secret,
        "code": code,
        "redirect_uri": config.redirect_uri,
        "code_verifier": code_verifier,
    }
    response = requests.post(
        TOKEN_URL,
        data=form,
        headers={
            "Accept": "application/json",
            "User-Agent": "RunTracker/1.0 (+RunSignUp OAuth connected track)",
        },
        timeout=DEFAULT_TIMEOUT,
    )
    return grant_from_token_response(_raise_for_oauth_error(response))


# -------------------------------------------------
# Step 3: the lookup
# -------------------------------------------------
def _raise_for_rest_error(payload: object, context: str) -> dict:
    """Raise on RunSignUp's in-band REST error shape.

    These arrive as **HTTP 200** with ``{"error": {"error_code", "error_msg"}}``,
    so a status check alone never catches them.
    """
    if not isinstance(payload, dict):
        raise OAuthFlowError(f"RunSignUp returned an unexpected {type(payload).__name__} for {context}.")

    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("error_code")
        message = error.get("error_msg") or error
        if code == ERROR_KEY_AUTH_FAILED:
            raise OAuthFlowError(
                "RunSignUp rejected the access token (error 6). The connection may "
                "have expired -- connect again."
            )
        if code == ERROR_NOT_LOGGED_IN:
            raise OAuthFlowError(
                "RunSignUp saw no credential on the request (error 7). The access "
                "token was not sent."
            )
        raise OAuthFlowError(f"RunSignUp API error {code} for {context}: {message}")

    return payload


def registered_races(access_token: str, api_registration: tuple[str, str] | None = None) -> dict:
    """Fetch the consenting runner's registration list.

    ``api_registration`` is SPR-16's ``(token, secret)`` caller registration. It
    does **not** authenticate anything -- RunSignUp is explicit that caller
    registration is not authentication -- but it is how they contact us about
    breaking changes, and it becomes mandatory 2027-01-01, so it rides along on
    every call when configured. Optional so this module does not depend on
    SPR-16 having landed.
    """
    if not access_token:
        raise OAuthFlowError("No access token: connect a RunSignUp account first.")

    params = {"format": "json"}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "RunTracker/1.0 (+RunSignUp OAuth connected track)",
    }
    if api_registration:
        reg_token, reg_secret = api_registration
        if reg_token:
            params["rsu_api_reg"] = reg_token
        if reg_secret:
            headers["X-RSU-API-REG-SECRET"] = reg_secret

    response = requests.get(
        REGISTERED_RACES_URL, params=params, headers=headers, timeout=DEFAULT_TIMEOUT
    )

    if response.status_code != 200:
        # Note: no URL in the message. Nothing token-derived either.
        raise OAuthFlowError(f"RunSignUp returned HTTP {response.status_code} for registered-races.")

    try:
        payload = response.json()
    except ValueError:
        raise OAuthFlowError("RunSignUp returned a non-JSON body for registered-races.") from None

    return _raise_for_rest_error(payload, "registered-races")


# -------------------------------------------------
# Parsing an undocumented payload
# -------------------------------------------------
@dataclass(frozen=True)
class Registration:
    """One of the runner's registrations, flattened to what discovery needs."""

    race_id: int
    race_name: str
    race_date: str  # ISO 'YYYY-MM-DD', or '' when the payload carried no date
    event_name: str = ""
    city: str = ""
    state: str = ""


# Keys the list of registrations might plausibly live under. The published
# response schema is bare {"type": "object"}, so this is tolerant by necessity;
# describe_payload_shape() reports the real shape once a token exists.
_LIST_KEYS = ("races", "registered_races", "registrations", "race_registrations", "results")


TOP_LEVEL_LIST = "(top-level list)"


def registration_list_key(payload: object) -> str:
    """Report which top-level key holds the registration list.

    The diagnostic report prints this, so it has to agree with what
    ``parse_registrations`` actually read -- hence one function used by both,
    rather than two that can disagree about an unexpected payload.

    Returns ``''`` when no list of records could be found.
    """
    if isinstance(payload, list):
        return TOP_LEVEL_LIST
    if not isinstance(payload, dict):
        return ""

    for key in _LIST_KEYS:
        if isinstance(payload.get(key), list):
            return key

    # Fall back to the longest list of objects anywhere at the top level, so an
    # unexpected key name degrades to "parsed something" rather than "parsed
    # nothing silently".
    candidates = [
        (key, value)
        for key, value in payload.items()
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value)
    ]
    if candidates:
        return max(candidates, key=lambda item: len(item[1]))[0]
    return ""


def _as_registration_dicts(payload: object) -> list[dict]:
    """Find the list of registration records inside an undocumented payload."""
    key = registration_list_key(payload)
    if key == TOP_LEVEL_LIST:
        records = payload
    elif key:
        records = payload[key]
    else:
        return []
    return [item for item in records if isinstance(item, dict)]


def _first(mapping: dict, *keys: str) -> str:
    """First non-empty value among ``keys``, as a stripped string."""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _iso_date(raw: str) -> str:
    """Normalize RunSignUp's date formats to ISO, or '' when unparseable."""
    text = str(raw or "").strip()
    if not text:
        return ""
    date_part = text.split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def parse_registrations(payload: object) -> list[Registration]:
    """Flatten the registration payload into :class:`Registration` rows.

    Records can nest the race under a ``race`` key or inline its fields, and the
    date can arrive as a race-level date or an event ``start_time``. Both shapes
    are accepted; a record with no usable ``race_id`` is skipped, since a race id
    is the only field downstream discovery cannot work without.
    """
    registrations = []
    for record in _as_registration_dicts(payload):
        race = record.get("race") if isinstance(record.get("race"), dict) else record

        race_id_text = _first(race, "race_id", "id") or _first(record, "race_id")
        try:
            race_id = int(race_id_text)
        except (TypeError, ValueError):
            continue

        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        race_date = (
            _iso_date(_first(race, "next_date", "race_date", "start_date"))
            or _iso_date(_first(event, "start_time", "event_date", "start_date"))
            or _iso_date(_first(record, "race_date", "start_time", "registration_date"))
        )

        address = race.get("address") if isinstance(race.get("address"), dict) else race

        registrations.append(
            Registration(
                race_id=race_id,
                race_name=_first(race, "name", "race_name"),
                race_date=race_date,
                event_name=_first(event, "name", "event_name"),
                city=_first(address, "city"),
                state=_first(address, "state", "state_code"),
            )
        )
    return registrations


def describe_payload_shape(payload: object, max_keys: int = 25) -> str:
    """One-line description of an unknown payload, for the diagnostic report.

    This exists because the endpoint's response schema is published as bare
    ``{"type": "object"}``. It reports structure -- key names, types, list
    lengths -- and deliberately **no values**, so the output is safe to paste
    into a ticket without leaking the runner's race history.
    """
    if isinstance(payload, list):
        inner = describe_payload_shape(payload[0], max_keys) if payload else "empty"
        return f"list[{len(payload)}] of ({inner})"
    if isinstance(payload, dict):
        parts = []
        for key in list(payload.keys())[:max_keys]:
            value = payload[key]
            if isinstance(value, list):
                parts.append(f"{key}: list[{len(value)}]")
            elif isinstance(value, dict):
                parts.append(f"{key}: object({len(value)} keys)")
            else:
                parts.append(f"{key}: {type(value).__name__}")
        suffix = ", ..." if len(payload) > max_keys else ""
        return "{" + ", ".join(parts) + suffix + "}"
    return type(payload).__name__


# -------------------------------------------------
# The diagnostic that decides this ticket
# -------------------------------------------------
@dataclass(frozen=True)
class RegistrationsSummary:
    """Answers "does registered-races return *past* races?" -- see the design doc.

    If ``past`` is 0 while ``upcoming`` is not, the endpoint is upcoming-only and
    the connected track cannot deliver race history at all.
    """

    total: int
    past: int
    upcoming: int
    undated: int
    earliest: str
    latest: str

    @property
    def returns_past_races(self) -> bool:
        return self.past > 0

    @property
    def verdict(self) -> str:
        """Plain-language answer, the line that gets pasted into the ticket."""
        if self.total == 0:
            return (
                "No registrations returned at all -- inconclusive. Either this account "
                "has none, or the endpoint hides them."
            )
        if self.past and self.upcoming:
            return (
                f"Past races ARE returned ({self.past} past, {self.upcoming} upcoming). "
                "The connected track can deliver race history."
            )
        if self.past:
            return (
                f"Past races ARE returned (all {self.past} are in the past). "
                "The connected track can deliver race history."
            )
        return (
            f"UPCOMING ONLY -- {self.upcoming} registrations, none in the past. "
            "The connected track cannot deliver race history; close SPR-20."
        )


def summarize_registrations(
    registrations: list[Registration], today: date | None = None
) -> RegistrationsSummary:
    """Count past vs upcoming and find the date range.

    A race *on* today counts as upcoming, not past: its results will not exist
    yet, which is what "past" means for this feature.
    """
    reference = today or date.today()
    dated = sorted(reg.race_date for reg in registrations if reg.race_date)
    iso_today = reference.isoformat()

    return RegistrationsSummary(
        total=len(registrations),
        past=sum(1 for value in dated if value < iso_today),
        upcoming=sum(1 for value in dated if value >= iso_today),
        undated=len(registrations) - len(dated),
        earliest=dated[0] if dated else "",
        latest=dated[-1] if dated else "",
    )
