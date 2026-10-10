# RunSignUp API caller registration

**Deadline: 2027-01-01.** From that date RunSignUp rejects API calls from
unregistered callers. RunTracker's RunSignUp features — future-race pulls,
result-set discovery, name lookups — all stop working if we are not registered
by then. Registration is free.

Announcement: <https://info.runsignup.com/2026/07/17/new-api-registration-requirements/>

The code side is done and merges safely today: with nothing configured, calls
go out exactly as they did before. This document is the *configuration* side.

---

## The two settings

| Setting | Sent to RunSignUp as |
|---|---|
| `RUNSIGNUP_API_REG_TOKEN` | `rsu_api_reg` GET parameter |
| `RUNSIGNUP_API_REG_SECRET` | `X-RSU-API-REG-SECRET` request header |

Both are read by `runsignup_results.api_registration()`, from Streamlit
secrets first and then environment variables — the same pattern as
`RUNTRACKER_DB_PATH` in `storage.py`. `app.py` reads them through that same
function, so the two call sites share one source of truth.

### Set both or neither

RunSignUp validates registration **today**, not from 2027. A token it does not
recognise fails *every* call, on both hosts:

```
HTTP 400 {"error":{"error_code":17,"error_msg":"Invalid API caller credentials."}}
```

So a mis-pasted or half-finished setup is strictly worse than no setup: it
takes the entire RunSignUp surface down immediately. Either both values are
correct, or both are unset. There is no useful in-between, and
`registration_notes()` will warn about one-without-the-other.

---

## This is not OAuth, and not an API key

Worth reading before you start, because all of these are issued from the same
RunSignUp **API Keys** page and two of them have "register" in the name.
RunSignUp states it plainly on that page:

> API caller registration is not the same as your authentication to the API.
> You still need to use OAuth or API keys to access your race, events, etc.

That cuts both ways: **registering as an API caller authenticates nothing, and
setting up OAuth does not register you as a caller.**

| Credential | Settings | What it does | Satisfies the 2027 deadline? |
|---|---|---|---|
| **API caller registration** | `RUNSIGNUP_API_REG_TOKEN`, `RUNSIGNUP_API_REG_SECRET` | Identifies this app so RunSignUp can contact us about breaking changes | **Yes — only this one** |
| OAuth 2.0 client | `RUNSIGNUP_OAUTH_CLIENT_ID`, `RUNSIGNUP_OAUTH_CLIENT_SECRET` | Lets a runner consent to us reading their own registrations (SPR-20, `docs/connected-track-oauth.md`) | No |
| API key / secret | `RUNSIGNUP_API_KEY`, `RUNSIGNUP_API_SECRET` | Authenticates the race-*search* endpoint in `app.py` | No |
| Affiliate token | `RUNSIGNUP_AFFILIATE_TOKEN` | Decorates outbound race links — not authentication at all | No |

So "I registered an OAuth application" and "I registered as an API caller" are
two separate free steps on the same page, and the deadline only cares about the
second. If you have done one and are not sure which, the command below tells
you — it reports every credential type, so a wrongly-configured one shows up
instead of looking like nothing was configured:

```bash
python runsignup_results.py --check-registration
```

With an OAuth client set up but no caller registration, it says so directly:

```
Other RunSignUp credentials (none of these satisfy the deadline)
  OAuth 2.0 client         set      lets a runner consent to us reading their own registrations (SPR-20)

  ! RUNSIGNUP_OAUTH_CLIENT_ID, RUNSIGNUP_OAUTH_CLIENT_SECRET are set -- that is
    the OAuth 2.0 client, which lets a runner consent to us reading their own
    registrations (SPR-20). It is NOT API caller registration and does not
    satisfy the 2027-01-01 deadline.
```

---

## Where to put them

### Local development — `.streamlit/secrets.toml`

**This is the recommended home.** It already works (no code change), it is
already gitignored, and it is the same mechanism Community Cloud uses, so
local and deployed configuration stay identical.

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Then uncomment and fill the two registration lines:

```toml
RUNSIGNUP_API_REG_TOKEN  = "<id>.<token>"
RUNSIGNUP_API_REG_SECRET = "<the secret shown once at registration>"
```

`.gitignore` already contains `.streamlit/secrets.toml`. Only the
`.example` template is tracked.

Environment variables work too and are the better fit for CI or a container
(`RUNSIGNUP_API_REG_TOKEN=... streamlit run app.py`). Prefer the file for
day-to-day local work — an exported variable is easy to lose track of, and
the symptom of a stale one is an app-wide HTTP 400.

### Deployed (Streamlit Community Cloud)

Do **not** commit or upload a secrets file. Go to **Manage app → Settings →
Secrets** and paste the same two `key = "value"` lines. The app restarts
automatically and picks them up.

### Do not paste either value into a ticket, PR, or chat thread

Put them straight into the secrets file or the Community Cloud secrets box.
The secret is shown only once at registration, so if it is lost the
registration has to be regenerated. Nothing in RunTracker ever prints either
value: `--check-registration` prints only the id half of the token and a
bare `(set)` / `(unset)` for the secret, and the error message names the
setting rather than its contents.

---

## Verify after setting them

```bash
python runsignup_results.py --check-registration
```

Expected when correctly configured:

```
RunSignUp API caller registration
  RUNSIGNUP_API_REG_TOKEN:  12345.***
  RUNSIGNUP_API_REG_SECRET: (set)
  sending rsu_api_reg param:        yes
  sending X-RSU-API-REG-SECRET header: yes

Other RunSignUp credentials (none of these satisfy the deadline)
  OAuth 2.0 client         unset    lets a runner consent to us reading their own registrations (SPR-20)
  API key / secret         unset    authenticates the race-search endpoint in app.py
  Affiliate token          unset    decorates outbound race links; not authentication at all
  live call OK: race 85066 -> Hospital Hill Run
```

No `!` warning lines is the signal that registration is complete. The "other
credentials" block is informational — `unset` there is fine and expected.

If instead you get `RunSignUpRegistrationError`, the token or secret is wrong.
Re-copy both from RunSignUp → API Keys, or comment both out to restore
unregistered access until the deadline.

---

## The one-year data window

RunSignUp's API Developer Contract says data requests are limited to **one
year back**. The published docs do not say whether that applies to the public
results endpoints, and it matters: a hard one-year cap would limit any
multi-year results search, including SPR-15's year-range hints.

Rather than guess, measure it:

```bash
python runsignup_results.py --check-data-window
```

This walks a real race's history newest-to-oldest and reports, per year,
whether finisher rows can still be pulled. Run it **before and after**
configuring registration — a single run proves nothing; the comparison is the
point. If the cap is enforced per-registration, the registered run's history
collapses to the last 12 months while the baseline below does not.

### Unregistered baseline, measured 2026-10-09

```
RunSignUp data window -- race 85066 Hospital Hill Run
  registered:  no (baseline)
  oldest event RunSignUp lists: 2011-06-06
  2026  ok     2026-05-16
  2025  ok     2025-05-31
  2024  ok     2024-06-01
  2023  none   2023-06-03     <- no public result set
  ...
  oldest retrievable results:   2024-06-01 (860 days / 2.4 years back)
```

Two things to read off this:

1. **Results reach 2.4 years back unregistered, so the one-year limit is not
   enforced on these endpoints today.** Good news for SPR-15.
2. **The 2023-and-older `none` rows are not evidence of a cap.** RunSignUp
   lists events back to 2011 for this race but publishes no result sets before
   2024, which is indistinguishable from a window at this level. So treat
   "2.4 years" as a floor, not a measurement of the actual limit — the
   available history ran out before any API limit did.

If the registered run still reaches 2024, the question is settled empirically
and no email to RunSignUp is needed. If it collapses to 12 months, that is a
real constraint on SPR-15 and worth raising with `info@runsignup.com` then.

---

## Other limits from the same announcement

- **2 concurrent calls.** Everything in `runsignup_results.py` is sequential,
  so this is headroom rather than a constraint — but do not fan these calls
  out across threads.
- Registration is per *caller*, not per endpoint: it covers both
  `runsignup.com/rest` (the results client) and `api.runsignup.com/rest`
  (the race search in `app.py`).
