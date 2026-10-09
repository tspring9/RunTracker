# Connected track: RunSignUp OAuth 2.0 (SPR-20)

Design and findings for the "Connect my RunSignUp account" fast path that replaces the
anonymous name+DOB sweep with a lookup against the runner's actual registration list.

**Status: blocked on OAuth client credentials.** Everything below is reachable from public
documentation and unauthenticated probing. The one question that decides whether this track
can ship at all — does `registered-races` return *past* races? — cannot be answered without
credentials. See "The load-bearing unknown".

All probes run 2026-10-09 against `api.runsignup.com`.

## Why this track exists

| | Anonymous track (PR #5) | Connected track (this doc) |
|---|---|---|
| Input | First/last name + DOB | OAuth consent |
| Discovery | Sweep every race in a state-year | Lookup of the runner's registrations |
| API calls | ~300-500 per state-year | ~20-60 total |
| Wall clock | 3-6 minutes per state-year | a few seconds |
| Correctness | Name guess, needs age confirmation | The runner's own registration list |

The anonymous track stays fully functional. This is strictly an opt-in accelerator.

## Auth: what actually authenticates this endpoint

This was the main thing to settle, because the ticket's premise needed checking and there are
four different RunSignUp credential types that are easy to conflate.

RunSignUp's own API Keys page is explicit:

> API caller registration is not the same as your authentication to the API. You still need to
> use OAuth or API keys to access your race, events, etc.

So the four credentials are:

| Credential | Wire format | Identifies | Authenticates `registered-races`? |
|---|---|---|---|
| API caller registration | `rsu_api_reg` + `X-RSU-API-REG-SECRET` | the developer account, for contact | **No** — identification only |
| Partner / affiliate key | `rsu_api_key` + `X-RSU-API-SECRET` | a company | No — wrong subject (company, not runner) |
| Legacy API key | `api_key` + `api_secret` | a company | No — same |
| **OAuth 2.0 access token** | `Authorization: Bearer <jwt>` | **the consenting runner** | **Yes** — the only path |

### This rules out reusing SPR-16's credential

SPR-16 ships `rsu_api_reg` / `X-RSU-API-REG-SECRET` plumbing and is waiting on the same board
user for a registration. It would have been convenient if that credential also unlocked
`registered-races` — "the user logged into the API" reads like it might. It does not: RunSignUp
documents API caller registration as *not* authentication. SPR-16 cannot substitute for the
OAuth app here.

The two tickets are still coupled in one direction: SPR-16's registration should be sent
*alongside* the Bearer token on every connected-track call, because registration is how
RunSignUp contacts us about breaking changes and it becomes mandatory 2027-01-01.

### Probe: the endpoint does inspect `Authorization`

`registered-races`' published OpenAPI document lists `apiKey`/`apiSecret` under `security` and
does **not** list an `oauth2` scheme — the OAuth2 spec is a separate document covering only its
own three auth endpoints. Taken literally that would mean Bearer tokens do not work here. They
do; the docs are just incomplete. Unauthenticated versus bogus-Bearer returns *different*
errors:

```
GET /rest/user/registered-races?format=json
  (no auth)              -> {"error_code":7,  "error_msg":"Permission Denied"}
  Authorization: Bearer bogus
                         -> {"error_code":6,  "error_msg":"Key authentication failed"}
  ?rsu_api_reg=1.bogus   -> HTTP 400 {"error_code":17,"error_msg":"Invalid API caller credentials."}
```

Error 6 rather than 7 means the request got past "nobody is logged in" into key
authentication — the endpoint parsed the header and tried to validate the token. That is as far
as verification can go without a real token, but it establishes the mechanism is live rather
than hoped for.

Note all three return **HTTP 200** for the first two cases with the error in the body.
RunSignUp signals these in-band, so the client must check `error_code` and not rely on status.

### Why not the Login API

`/rest/login` still answers (POST-only; GET returns error code 2) but is absent from all 185
documented methods. It is also the only option that would require the user to type their
RunSignUp password into this app. OAuth is live, documented, and keeps the password on
RunSignUp's domain. The Login API is not used.

## OAuth 2.0 flow, as published

From `https://runsignup.com/API/OAuth2/openapi-spec.json` (OpenAPI 3.0.3, "RunSignUp OAuth2
Authentication API" v2.0.0). Authorization code flow with PKCE. Server: `https://api.runsignup.com`.

### 1. Authorize — `GET /Profile/OAuth2/RequestGrant`

| Parameter | Required | Notes |
|---|---|---|
| `response_type` | yes | `code` |
| `client_id` | yes | from client registration |
| `redirect_uri` | yes | must match the registered URI exactly |
| `scope` | no | `rsu_api_read` is what we need |
| `state` | no | CSRF. We treat it as required. |
| `code_challenge` | no | PKCE, 43-128 chars of `[A-Za-z0-9-._~]` |
| `code_challenge_method` | no | `S256`. **Defaults to `plain` if omitted** — always send `S256`. |

Responds `302` to `redirect_uri` with `code` (or error) in the query string.

### 2. Redeem — `POST /rest/v2/auth/auth-code-redemption.json`

`application/x-www-form-urlencoded`. Required: `grant_type=authorization_code`, `client_id`,
`client_secret`, `code`, `redirect_uri`. Plus `code_verifier` when PKCE was used.

### 3. Refresh — `POST /rest/v2/auth/refresh-token.json`

`grant_type=refresh_token`, `refresh_token`, `client_id`, `client_secret`; optional `scope`.

Both return `TokenResponse`: `access_token` (JWT), `token_type` (`Bearer`), `expires_in`,
`refresh_token`, `scope`.

### Scopes

The OAuth2 doc page lists two; the spec's `securitySchemes` lists eight. We request
**`rsu_api_read` only** — read-only, and the narrowest scope that covers the endpoint.

```
rsu_api_read   rsu_api_write   mcp.access
rsu_admin.super_user   rsu_admin.developer   rsu_admin.finance
rsu_admin.onboarding_retention   rsu_admin.employee
```

There is no results- or profile-specific scope, so `rsu_api_read` necessarily grants more than
this feature needs. Worth saying plainly on the consent expander.

### Token lifetimes drive the privacy design

| Token | Lifetime |
|---|---|
| Authorization code | 5 minutes |
| Access token | **1 month** (2,592,000s) |
| Refresh token | **20 years** |

A 20-year refresh token is effectively a permanent credential for the runner's RunSignUp
account. This is the reason the session-only rule below is non-negotiable rather than
housekeeping: persisting one would leave a two-decade bearer credential in a SQLite file in the
app directory.

Because the access token lasts a month and a Streamlit session lasts minutes, **the refresh
endpoint is not needed for v1**. Dropping it means we never have to hold the refresh token at
all — see below.

## Privacy rules

Same bar as SPR-15 (DOB and email never persisted):

1. `access_token` lives in `st.session_state` only. Never SQLite, never logs, never files.
2. **Discard the refresh token on receipt.** Do not store it even in session state. A one-month
   access token already outlives any session; a 20-year credential has no use here.
3. Nothing token-derived goes into a `@st.cache_data` key — those are inspectable. Cache
   per-race lookups keyed by `race_id`, which is public, not by token or runner.
4. `client_secret` comes from `st.secrets` and is never rendered or logged.
5. The PKCE `code_verifier` and `state` are session-only and cleared once redeemed.
6. On error, log `error_code` / `error_msg` only — never the request URL, which carries the code.

### Clearing the callback query string

`app.py:57` already reads `st.query_params` (for `user`), so the callback mechanism is proven in
this codebase. But after redemption the handler must remove `code` and `state` from the URL
while **preserving `user`**. Authorization codes are single-use and expire in 5 minutes; leaving
one in the address bar puts it in browser history and in any screenshot. Clear the two keys
individually rather than calling `st.query_params.clear()`.

## The load-bearing unknown

**`registered-races` takes no date filter and no pagination.** Its documented parameters are
exactly `rsu_api_reg`, `X-RSU-API-REG-SECRET`, and `format`. Its published 200 response schema
is bare `{"type": "object"}` — no properties at all. So the docs state neither whether past
registrations are returned nor what the payload looks like.

This feature is about race *history*. If the endpoint returns only upcoming registrations, this
track cannot deliver it.

**So the first task once credentials land is one API call, not any of the code below.** Report
before building:

- total count returned, and the response's actual shape
- date range: earliest and latest race date
- past races present, or upcoming only
- whether registrations the runner cancelled or DNS'd appear
- whether the one-year data window raised in SPR-16 applies here

If it is upcoming-only, close this ticket. The anonymous track already ships the capability and
there is no second endpoint to fall back to — of 185 documented REST methods, every results
method is race-scoped (`/API/race/:race_id/results/...`); there is no cross-race "my results".

## Design once that is answered

The chain is necessarily:

```
registered-races -> race_id list -> existing per-race result-set discovery -> runner_matching
```

### No second matching engine

This is the whole point of the two-track split. The connected track replaces **discovery**
only. Once it has `race_id`s it reuses what PR #5 already ships:

- `runsignup_results.discover_result_sets()` / `fetch_results()` per race
- `runner_matching` for name + age-on-race-date confidence
- the same confirm/reject ("This is me" / "Not me") cards

Being connected does raise confidence: a race from the runner's own registration list is a
confirmed entry rather than a name guess. That should surface as provenance on the card, not as
a parallel scoring path — the runner still has to be matched to a *result row* by name, because
`registered-races` gives registrations, not finish times.

### Module layout

New `runsignup_oauth.py`, keeping the flow out of `app.py` and testable without a browser:

| Function | Purpose |
|---|---|
| `oauth_config()` | client id/secret from `st.secrets` then env, mirroring `api_registration()` |
| `authorize_url(redirect_uri)` | builds step 1, returns URL + `state` + `code_verifier` |
| `redeem_code(code, verifier, redirect_uri)` | step 2, returns access token, drops refresh token |
| `registered_races(token)` | the call, with SPR-16 registration attached |

Pure helpers (`state`/PKCE generation, URL building, response and in-band-error parsing) are
unit-testable with no network and no credentials — that is where the test suite goes, matching
`test_runsignup_helpers.py`.

### UI

A "Connect my RunSignUp account" expander in the Sign Up tab, collapsed by default, stating
what is requested (`rsu_api_read`, read-only), that nothing is stored, and that the anonymous
search works without connecting.

## Unblock

Register an OAuth application: RunSignUp -> My Account -> API Keys -> "Register Your
Application (OAuth)", or the [OAuth2 Developer Guide](https://runsignup.com/Profile/OAuth2/DeveloperGuide).

Both the guide and the registration form are behind a RunSignUp login — the guide URL serves
only a sign-in page when unauthenticated — so the exact field labels could not be read and are
not reproduced here.

Needed in `st.secrets`:

```toml
RUNSIGNUP_OAUTH_CLIENT_ID = "..."
RUNSIGNUP_OAUTH_CLIENT_SECRET = "..."
```

Register `http://localhost:8501/` as the redirect URI for local development, matching
Streamlit's default port. If the app is ever deployed, that origin must be registered too —
`redirect_uri` must match the registered value exactly, and a mismatch fails at step 1 before
any token exists. Register both up front if a deployment URL is known.

Credentials go straight into secrets, never into a ticket thread.

## References

- OAuth2 overview — https://runsignup.com/API/OAuth2
- OAuth2 OpenAPI spec — https://runsignup.com/API/OAuth2/openapi-spec.json
- API Keys / caller registration — https://runsignup.com/API/ApiKeys
- `registered-races` method docs — https://runsignup.com/API/user/registered-races/GET
- Anonymous track background — `docs/signup-search-discovery.md` (SPR-15), "Why (d) is not in this PR"
