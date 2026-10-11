# Connected track: RunSignUp OAuth 2.0 (SPR-20)

Design and findings for the "Connect my RunSignUp account" fast path that replaces the
anonymous name+DOB sweep with a lookup against the runner's actual registration list.

**Status: client credentials are registered; the flow is implemented; one consent round-trip
is outstanding.** `runsignup_oauth.py` implements the full authorization-code flow and the
lookup, with 49 unit tests that run without credentials. What is still unanswered is the
question that decides whether this track can ship at all — does `registered-races` return
*past* races? — and it cannot be answered by anyone but the account owner, because
authorization-code consent requires a human in a browser. `probe_registered_races.py` reduces
that to one command and about a minute. See "The load-bearing unknown".

All probes run 2026-10-09. Note that the host matters and the published spec is wrong about
it — see "Where the endpoints actually live".

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
  (no auth)              -> HTTP 200 {"error":{"error_code":7,"error_msg":"Permission Denied"}}
  Authorization: Bearer bogus
                         -> HTTP 200 {"error":{"error_code":6,"error_msg":"Key authentication failed"}}
  Authorization: Bearer <jwt-shaped bogus>
                         -> HTTP 200 {"error":{"error_code":6,"error_msg":"Key authentication failed"}}
```

Error 6 rather than 7 means the request got past "nobody is logged in" into key
authentication — the endpoint parsed the header and tried to validate the token. That is as far
as verification can go without a real token, but it establishes the mechanism is live rather
than hoped for.

Two details here are load-bearing for the client:

1. These are **HTTP 200** with the error in the body. RunSignUp signals in-band, so the client
   must branch on `error_code` and never on status. Read as success, an `error_code` 6 looks
   exactly like "this runner has no registrations" — the most misleading possible outcome for
   a feature whose entire job is finding races.
2. The error object is **nested under `error`**, not flat at the top level. An earlier revision
   of this document recorded it as flat; re-probing shows `{"error": {"error_code": ...}}`,
   which is the same shape `runsignup_results._get` already handles.

Both hosts (`api.runsignup.com` and `runsignup.com`) serve this endpoint identically.

## Where the endpoints actually live

The OAuth2 spec declares exactly one server, `https://api.runsignup.com`, and applies it to all
three of its endpoints. **That is wrong for the authorize endpoint**, and wrong in a way that
would have shipped a dead "Connect" button:

| Endpoint | `api.runsignup.com` | `runsignup.com` |
|---|---|---|
| `GET /Profile/OAuth2/RequestGrant` | **404 (nginx)** | **302 → `/Login/?redirect=…`** ✅ |
| `POST /rest/v2/auth/auth-code-redemption.json` | 401 `invalid_client` ✅ | 401 `invalid_client` ✅ |
| `GET /rest/user/registered-races` | error 7 ✅ | error 7 ✅ |

The authorize endpoint only answers on `runsignup.com`, which makes sense in retrospect — it is
a browser-facing consent page, not an API call, so it lives on the web host rather than the API
host. The 404 is a bare nginx one, not a RunSignUp error page, so following the spec literally
sends the runner to a blank 404 instead of a consent screen.

The 302 to `/Login/?redirect=…` round-trips every query parameter including `code_challenge`,
which is the correct behaviour: log in first, then continue to consent.

### Two different error conventions in one flow

Worth stating separately because a single error handler would miss one of them:

| | Token endpoint | REST endpoints |
|---|---|---|
| HTTP status | real (`401`, `400`) | always `200` |
| Error body | `{"error": "invalid_client", "error_description": …}` | `{"error": {"error_code": 6, "error_msg": …}}` |
| Standard | OAuth 2.0 | RunSignUp in-band |

`runsignup_oauth.py` therefore has two separate guards, `_raise_for_oauth_error` and
`_raise_for_rest_error`, and the tests pin both.

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

## The load-bearing unknown — ANSWERED 2026-10-11: past races ARE returned

The board user ran `probe.cmd` against their own consented account. Verdict, exit code 0:

> Past races ARE returned (all 7 are in the past). The connected track can deliver race history.

| | |
|---|---|
| registrations parsed | 7 |
| in the past | 7 |
| today or upcoming | 0 |
| no usable date | 0 |
| earliest race date | 2021-07-19 |
| latest race date | 2026-05-09 |
| real payload shape | `{user_registered_races: list[7]}` |
| list key | `user_registered_races` |
| access token lifetime | 2682000s (31 days, as documented) |

So this ticket is **not** closed — it is built. Three things the probe settled beyond the
verdict:

1. **A five-year span of history, from one lookup.** 2021 to 2026 against an account with
   nothing indexed. That is the capability the ticket was for.
2. **The real list key is `user_registered_races`**, which was not among the guesses in
   `_LIST_KEYS`. The list was found only by the longest-list fallback — correct by luck. It is
   now the first key checked, and pinned by a test, because the fallback picks the *longest*
   list of objects and a bigger sibling list would have beaten the real one.
3. **`scope` is not reported in the token response** (`(not reported)` in the report). So the
   granted scope cannot be displayed back to the runner from the grant; the consent screen is
   the only place it is stated. Do not build UI that claims to show the granted scope.

Two of the original questions remain open and are **not** worth a second probe: whether
cancelled/DNS registrations appear, and whether SPR-16's one-year data window applies. Neither
changes the design — a registration with no matching result row simply yields no candidate, and
the sweep's name+age matching is what decides a row either way. They will answer themselves in
use.

The original statement of the unknown follows, since it is why the module is shaped as it is.

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

### Answering it: `probe_registered_races.py`

Why this needs a human at all: the authorization-code flow requires the *resource owner* to
authenticate and consent in a browser. That is the whole design of OAuth, and there is no
client-side workaround — no credential in `st.secrets` can stand in for the account owner's
consent. So this step is irreducibly manual. The probe exists to make it as small as possible.

```
python probe_registered_races.py      # or: double-click probe.cmd
```

On your own machine — it is a local CLI, not something the deployed app runs. Everything else
it needs, it asks for, because **the setup step was what actually blocked this, not the OAuth
flow**. Two rounds of back-and-forth went on "where do I run this" and "which secrets does it
read", so both are now answered by the program rather than by instructions:

- **`probe.cmd` removes the `cd`.** Double-clicking it in Explorer sets the working directory to
  its own location, so the import resolves and the report lands beside the script. It also
  `pause`s at the end, or a double-click would flash the verdict up and vanish.
- **Missing credentials are prompted for, not fatal.** At a terminal the probe asks for whichever
  of the three values is absent, reading the client secret through `getpass` — so it is neither
  echoed nor left in `ConsoleHost_history.txt`, which the environment-variable route could not
  avoid. Piped or `--no-prompt` runs keep failing with the full "where to put these" message,
  since a prompt nobody can answer would just hang.
- **Streamlit Cloud secrets are server-side** and invisible to a local process; this is the trap
  the prompt exists to defuse. Values live in `.streamlit/secrets.toml` or the environment
  (secrets consulted first), and otherwise are typed in at the prompt.
- **The branch is not merged**, so `main` does not contain the script. Use a checkout or worktree
  of `feature/spr-20-oauth-connected-track`.

At the redirect-URI prompt, `localhost` is accepted as shorthand for `http://localhost:8501/`.
Anything else is taken **verbatim**: RunSignUp compares the registered URI literally, so
"helpfully" adding or trimming a trailing slash would convert a typo into a consent-screen
failure with RunSignUp's error message rather than ours.

It prints a consent URL, you approve it, you paste back the URL you land on, and it prints the
verdict. The landing page may 404 or fail to load — that is fine and expected, since what
matters is the authorization code in the address bar.

It writes `registered-races-probe-report.md` containing **structure only**: counts, a date
range, the real payload shape, and the granted scope. No race names, dates of individual races,
or finish times, so the report is safe to paste into the ticket. `--show-races` prints your own
race list to the terminal as well, and is never written to the report.

The probe exits `3` when the answer is "upcoming only", so the close-this-ticket outcome is
hard to misread.

### Why the probe is a CLI rather than the app's own button

A Streamlit in-app flow has a problem the CLI does not. Streamlit resets `st.session_state` on
a full page load, and an OAuth redirect *is* a full page load — the browser leaves for
`runsignup.com` and comes back to a new session. So the `state` and PKCE `code_verifier` stored
before the redirect are gone by the time the callback arrives, and the flow cannot be completed
or CSRF-checked.

The remedy, for when the UI is built, is a short-lived **server-side** pending store keyed by
`state` — a `@st.cache_resource` dict of `state -> (code_verifier, created_at)`, pruned on the
5-minute authorization-code expiry. That survives the session boundary because it lives in the
server process rather than the session, and it preserves the CSRF property: a callback whose
`state` is not one this server issued is rejected. It must be a `cache_resource` singleton and
not `cache_data` — see privacy rule 3.

This is a design consequence worth knowing before writing the expander, which is why the UI is
not in this change. It also rules out Streamlit's native `st.login()`: that is OIDC-only and
needs a discovery document and an `id_token`, and RunSignUp's OAuth 2.0 is neither.

## Design, now that it is answered

The chain is necessarily:

```
registered-races -> race_id list -> existing per-race result-set discovery -> runner_matching
```

Built as `connected_track.py`. It takes an access token and returns candidate result sets in
exactly the shape `results_search.sweep_for_runner` already consumes, then stops. The sweep and
the matching are the anonymous track's existing code, unchanged.

### What the offline index changed about this track's value

The ticket argued this track on call count: ~20-60 calls instead of the anonymous track's
~300-500. That framing is out of date, and the real argument is now stronger.

SPR-30 moved discovery offline. The anonymous track reads candidates from a SQLite index that a
human builds per state/year (`results_search.build_index`, CLI only). If the index does not
cover a scope, the search **cannot answer at all** — that is SPR-49's `unbuilt_index` terminal
state.

The connected track needs none of it. Registrations name the race ids outright, so there is no
index to build, no state list to pick, and no year range to guess. A connected runner gets an
answer on an account with nothing indexed. That is a capability difference, not a speed one.

### The date is the whole correctness problem

`runsignup_results.discover_result_sets` scans only the **five most recent race days** of a race
by default (`DEFAULT_DISCOVERY_EVENT_DAYS`). That is right for its original caller, which wanted
recent results, and wrong here: a `race_id` identifies a recurring race *series*, so for an
annual race the five most recent days are the last five years. Calling it plainly would silently
miss a 2021 registration on a race that has run every year since.

The probe's own account spans 2021–2026, so this is measured, not hypothetical — the naive call
would have lost the oldest races on the very account the feature was built for.

Registrations carry the race date, so `select_event_days` targets it instead, widening only when
the previous tier finds nothing:

1. events dated exactly the registration date
2. events within `EVENT_DAY_TOLERANCE_DAYS` (3) — a race weekend, or a registration stamped with
   the race-level `next_date` rather than the event's own
3. events in the same calendar year — the coarsest identifier that still picks one running
4. no match: reported as a race that could not be covered, rather than scanning everything

This fixes the miss *and* costs fewer calls than widening the scan would: 1 `/race/{id}` call
plus 1 `get-result-sets` per event on the targeted day, so 2-4 calls for a typical race.

### Both date keys, one value

A candidate sets `event_date` **and** `date` from a single value. Both are load-bearing and they
are read by different code:

- `event_date` is the frozen SPR-30 candidate contract; `app.py` reads it off `_context` to
  backfill a row's `race_date`.
- `date` is what `runsignup_results.result_to_tracker_row` actually reads for `race_date`.

Setting one and not the other yields rows with a blank date, which presents as a data problem
rather than a wiring one. A test builds a real tracker row and asserts the date survives, rather
than asserting on our own dict shape.

### On unifying the budget

`connected_track.CallBudget` is deliberately the same shape as `results_search.CallBudget` — a
`limit`, a `used`, and a `spend()` that returns False when exhausted — so the two can be merged
into one cap when this is wired into the Sign Up tab. It is not imported from there because
`results_search` does not exist on `main`; it arrives with PR #5.

It is single-threaded by design: this stage issues calls serially, so none of SPR-46's
compare-and-swap concurrency work applies. If it is ever parallelised, take that version instead
of making this one thread-safe.

### "No results" must never mean "we stopped looking"

`RaceDiscovery.truncated` separates a race that published nothing from a race the budget cut
short. Those are opposite messages — "nothing to find" versus "we did not look" — and an empty
list alone would make the UI claim the first when it means the second. This is the same failure
SPR-49 fixed for the anonymous track's empty index, so it is not a hypothetical class of bug in
this codebase. `CoverageReport.describe()` is counts only, with no race names, so it is safe to
log or paste into a ticket; the candidates are not.

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

`runsignup_oauth.py` keeps the flow out of `app.py` and testable without a browser. Shipped:

| Function | Purpose |
|---|---|
| `oauth_config()` / `is_configured()` | client id, secret and redirect URI from `st.secrets` then env, mirroring `api_registration()` |
| `begin_authorization(config)` | step 1: fresh `state` + PKCE pair, returns the consent URL |
| `authorization_code_from_callback(params, state)` | validates the callback, enforces the state match |
| `redeem_code(config, code, verifier)` | step 2, returns `AccessGrant`, drops the refresh token |
| `registered_races(token, api_registration=None)` | the lookup, with SPR-16 registration attached when configured |
| `parse_registrations(payload)` | tolerant flattening of an undocumented payload |
| `describe_payload_shape(payload)` | reports structure, no values — for the diagnostic report |
| `summarize_registrations(regs)` | counts past vs upcoming and renders `.verdict` |

`AccessGrant` has **no `refresh_token` field at all**, so callers have no way to hold one —
privacy rule 2 is enforced by the type rather than by convention. `api_registration` is an
optional parameter so this module does not depend on SPR-16 having landed.

`parse_registrations` is deliberately tolerant because the response schema is unpublished: it
accepts a wrapper key, a bare top-level list, race fields nested under `race` or inlined, and
dates from either the race or the event. If RunSignUp uses a key we did not predict it falls
back to the longest list of objects, so an unexpected name degrades to "parsed something"
rather than a silent zero. Records with no usable `race_id` are dropped, since that is the one
field per-race discovery cannot work without.

`test_runsignup_oauth.py` — 60 tests, no network, no credentials, matching
`test_runsignup_helpers.py`. The three areas that carry real risk get the most attention: PKCE
and state (a subtle bug here still *works*, and only matters when attacked), the two error
conventions (confusing them reads a rejected token as an empty result), and parsing the
undocumented payload.

### `connected_track.py`

| Function | Purpose |
|---|---|
| `discover_connected_candidates(token, ...)` | entry point: access token -> candidate result sets |
| `candidates_from_registrations(regs, ...)` | the whole pass, within a call budget |
| `candidates_for_registration(reg, budget)` | one registered race -> its candidates, date-targeted |
| `select_event_days(events, race_date)` | picks the right *running* of a recurring race |
| `partition_registrations(regs)` | `(past, upcoming, undated)`; only past ones can have results |
| `CoverageReport` / `.describe()` | counts only — safe to log; says when the cap was hit |
| `RaceDiscovery.truncated` | "budget cut this short" vs "this race published nothing" |

Upcoming registrations cost **zero** API calls: results cannot exist for a race that has not
happened, so confirming that would be waste. Undated registrations are checked *after* dated
ones — they cost more (a recent-days scan, with no date to target) and are less certain, so they
are the first thing a tight budget gives up on. The measured account had none, but the schema is
undocumented, so they are handled rather than assumed away.

`test_connected_track.py` — 40 tests, no network and no credentials, both HTTP entry points
monkeypatched. The four areas that carry real risk: date targeting (including an explicit
regression test that an old running of an annual race with 8 runnings is still found), the two
date keys, budget accounting, and the past/upcoming split.

### UI — still not in this change, and now for one reason rather than two

The load-bearing unknown is answered, so the question of whether this UI should exist at all is
settled: it should. What remains is a real blocker and a real constraint.

**Blocker: there is nowhere to put it.** The expander belongs in the Sign Up tab, which arrives
with PR #5 (SPR-15) and is then substantially rebuilt by SPR-24. `main` still has the old "Find
My Results" tab. Building the expander against a tab that is concurrently being rewritten on two
branches would conflict for no gain, so it waits for PR #5 to merge.

**Constraint to design around: Streamlit resets `st.session_state` on a full page load, and an
OAuth redirect is one.** The browser leaves for `runsignup.com` and returns to a *new* session,
so the PKCE verifier and CSRF `state` stored before the redirect are gone when the callback
arrives — the flow cannot complete or be CSRF-checked. The remedy is a server-side
`@st.cache_resource` store keyed by `state`, pruned on the authorization code's 5-minute
expiry. This also rules out Streamlit's native `st.login()`, which is OIDC-only and wants a
discovery document and an `id_token`; RunSignUp's OAuth 2.0 is neither.

When built: a "Connect my RunSignUp account" expander, collapsed by default, stating what is
requested (`rsu_api_read`, read-only — and that it necessarily grants more than this feature
needs, since there is no narrower scope), that nothing is stored, and that the anonymous search
works without connecting. Candidates carry `from_registration: True`, so a card sourced from the
runner's own registration list can say so — that is provenance on the card, not a parallel
scoring path. The callback handler must clear `code` and `state` individually and **preserve
`user`**; never `st.query_params.clear()`. A single-use authorization code left in the address
bar lands in browser history and in any screenshot.

## Configuration

Registered and in place as of 2026-10-09. Three secrets, in `.streamlit/secrets.toml` locally
(gitignored), the Streamlit Cloud secrets manager when deployed, or the environment for a
one-off CLI run:

```toml
RUNSIGNUP_OAUTH_CLIENT_ID = "..."
RUNSIGNUP_OAUTH_CLIENT_SECRET = "..."
RUNSIGNUP_OAUTH_REDIRECT_URI = "..."
```

The redirect URI is **read from configuration rather than hard-coded**, because it differs per
environment and RunSignUp requires an exact match:

| Environment | Redirect URI |
|---|---|
| Local | `http://localhost:8501/` (Streamlit's default port) |
| Deployed | `https://runtracker-mlgmzxgxovwxlb8a3any8b.streamlit.app/` |

A mismatch fails at the consent screen, before any token exists — so it fails safe, but with an
error from RunSignUp rather than from us. Whichever URI a given environment uses must be
registered on the OAuth app; register both up front.

Registration lives at RunSignUp → My Account → API Keys → "Register Your Application (OAuth)",
or the [OAuth2 Developer Guide](https://runsignup.com/Profile/OAuth2/DeveloperGuide). Both are
behind a RunSignUp login — the guide URL serves only a sign-in page when unauthenticated — so
the exact field labels are not reproduced here.

Credentials go straight into secrets, never into a ticket thread. One wrinkle: RunSignUp's spec
describes `client_secret` as "base64 encoded". That describes the shape of the secret they
issue, not an encoding step we have to perform — `redeem_code` sends it verbatim.

## References

- OAuth2 overview — https://runsignup.com/API/OAuth2
- OAuth2 OpenAPI spec — https://runsignup.com/API/OAuth2/openapi-spec.json
- API Keys / caller registration — https://runsignup.com/API/ApiKeys
- `registered-races` method docs — https://runsignup.com/API/user/registered-races/GET
- Anonymous track background — `docs/signup-search-discovery.md` (SPR-15), "Why (d) is not in this PR"
