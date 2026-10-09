# Name-first "Sign Up" search: discovery approach and API-call budget

Reference doc for SPR-15. Every number here comes from live probes against
RunSignUp, dated below — not from estimates.

## The problem RunSignUp creates

RunSignUp has **no cross-race runner search**. Results are addressed as
`(race_id, event_id, result_set_id)` and the only name filter lives on
`race/{race_id}/results/get-results`, one result set at a time. Confirmed by
enumerating the documented method index on 2026-10-09: of 185 documented REST
methods, every single results method is race-scoped
(`/API/race/:race_id/results/...`). There is no `my-results`, no
`results?name=`, no cross-race query of any kind.

So "what races has this person run?" cannot be asked directly. It has to be
reconstructed: *narrow the universe of result sets to a plausible set, then ask
each one whether this name finished it.* Everything below is about making that
first step cheap enough to be usable.

## Measurements (2026-10-09, unauthenticated)

| Probe | Result |
|---|---|
| `get-results` with `first_name`/`last_name` | HTTP 200, **1.37 s** per call. Returns `age`, `gender`, `city`, `state`, `chip_time`. Filters are **partial** matches, so every hit is re-confirmed locally. |
| `races?state=NE&start_date&end_date` (2026) | 192 races in **1** call at `results_per_page=1000` |
| same for `CO` | 1,001 races in **2** calls |
| `races` for 2019 | Works — historical listings are reachable |
| `results/has-result-sets` | HTTP 200 `{"has_results":"T"}` — cheap pruning filter |
| `/rest/v2/results/updated-result-sets.json` | **HTTP 200 with no credentials.** Public catalog of result sets: `race_id`, `event_id`, `individual_result_set_id`, `race_name`, `last_modified_ts`. 5,000 rows/page; 12,534 rows modified in the trailing 30 days; non-empty at page 80, empty at page 100 → **~400–500 k rows** total |
| `/rest/user/registered-races` | HTTP 200 `{"error_code":7,"Permission Denied"}` — exists, needs user credentials |
| `/rest/login` | Exists; POST-only (GET → error code 2) |

### The find that made this feasible

`updated-result-sets` hands over `(race_id, event_id, result_set_id)`
**directly**. Without it, narrowing a scope costs one `/race/{id}` call plus one
`get-result-sets` call *per event* — which is exactly the per-race cost this
feature exists to eliminate. Colorado 2026 the naive way is ~4,000–5,000 calls
and 45+ minutes **before the first name lookup**. With the catalog, the same
narrowing costs ~2 calls. That is the roughly 10× reduction the design rests on.

## Options evaluated

| Option | Verdict |
|---|---|
| **(a) Local SQLite index of chosen states/years** | **Adopted, in scoped metadata-only form.** It is what makes (b) affordable. |
| **(b) Live capped search at submit time** | **Adopted, on top of (a).** Not viable alone — see the Colorado number above. |
| **(c) Series pages / site-restricted discovery (SPR-14)** | **Rejected from the product path.** Depends on external search indexing, needs a human judgement call per hit, and SPR-14's own Runner A was an exact-name series hit on the *wrong person*. Recall is not reproducible. The read-only Discovery Review tab stays as the record of that experiment. |
| **(d) User login / OAuth for the runner's own data** | **Deferred, with cause — see below.** |

### Why (d) is not in this PR

`/rest/user/registered-races` would collapse discovery from a search into a
lookup: ~20–60 calls, a few seconds, and the runner's *actual* registration list
rather than a name guess. Two findings from probing it on 2026-10-09 are why it
is not shipped here:

1. **It takes no date filter and no pagination.** Documented parameters are
   exactly `rsu_api_reg`, `X-RSU-API-REG-SECRET`, and `format` — nothing else.
   Its OpenAPI 200 response schema is published as bare `{"type": "object"}`.
   So there is no documented way to ask it for *past* races, and no way to learn
   from the docs whether it returns them. Since this feature is about race
   *history*, an endpoint that might only return upcoming registrations cannot
   be built on untested.
2. **The Login API is no longer in the documented method index.** It still
   answers at `/rest/login`, but it is absent from all 185 listed methods. The
   SPR-15 plan was approved on the premise that it was "deprecated but working";
   "delisted entirely" is a materially worse premise for a form that asks the
   user to type their RunSignUp password into this app.

The live, documented alternative is **OAuth 2.0** (`/API/OAuth2`), which needs
an OAuth app registered on the account owner's RunSignUp API Keys page — an
external dependency this codebase cannot satisfy on its own. Tracked as a
follow-up rather than guessed at here.

## Shipped design

One matching engine, fed by a two-stage capped pipeline.

```
SearchScope(states, start_year, end_year, max_calls=400)
  │
  ├─ stage 1  candidate_result_sets()          results_search.py
  │     races?state&dates        → race_catalog        (SQLite, metadata only)
  │     updated-result-sets      → result_set_catalog  (SQLite, scoped rows only)
  │
  └─ stage 2  sweep_for_runner()               results_search.py
        get-results per candidate, name filters only, ≤2 concurrent
        → re-confirmed locally by runner_matching.names_match
             │
             └─ classify_signup_matches()      app.py, AFTER the cache boundary
                  age_on_date(dob, race_date) vs result age → High/Possible/Rejected
```

| Module | Role |
|---|---|
| `runner_matching.py` | Pure. Name normalisation, exact age on race date, confidence classification, email format check. No network, no DB, no Streamlit. |
| `results_search.py` | The capped sweep orchestrator. Call accounting, progress callback, resume cursor. |
| `storage.py` | `race_catalog` + `result_set_catalog`. Shared public metadata, no `user_id`. |
| `app.py` | The Sign Up tab. Form, progress, High/Possible/Rejected grouping, per-row confirm/reject, old single-race lookup in an Advanced expander. |

## API-call budget

| Operation | Calls | Wall clock |
|---|---|---|
| Races in scope, per state-year | 1–2 | ~3 s |
| Result-set catalog walk, from stored watermark | 1–3 typical; 80–100 on a cold first run | 2 s – 2 min |
| Name sweep | **1 per candidate result set** | ~0.7 s per call at 2 concurrent |
| Typical state-year (NE-sized) end to end | ~300–500 | 3–6 min |
| **Hard cap per search** | **400 (default)** | capped, resumable |

The cap is real, not advisory: both stages count every HTTP call against
`scope.max_calls` and stop as soon as it is spent, setting `stage="capped"`.
The UI shows `calls_used / calls_budget` live and offers an explicit
"Continue searching" button that resumes from the returned cursor. A large
state or a 5-year span therefore takes *several user-initiated searches*, never
one unbounded crawl.

Repeat searches get cheaper: the catalog walk advances a single shared
watermark (`storage.catalog_watermark()`) and is never re-read from the start.

### Rate-limit and terms compliance

- **2 concurrent requests, hard-coded** (`MAX_CONCURRENT_REQUESTS`) — the
  contract's limit.
- Existing `Retry` adapter honours `Retry-After`.
- **No background or scheduled crawling.** Every call is inside a synchronous
  function reachable only from a button press.
- The API Developer Contract prohibits "bulk extraction to build a copy of
  RunSignup data." The catalog walk therefore **discards every row outside the
  requested state/year scope** instead of caching the whole feed to save a
  future call. Only `(race, event, result_set)` identifiers plus display
  metadata are persisted — **never participant rows**. There is a comment on the
  schema in `storage.py` saying so; keep it honest if you touch that table.

### Known deadline, outside this PR

**RunSignUp requires every API caller to register by 2027-01-01.** Calls without
an `rsu_api_reg` token and `X-RSU-API-REG-SECRET` header will be rejected, and
this app currently makes *only* unauthenticated calls — so every RunSignUp
feature breaks on that date. The docs also now give the canonical host as
`api.runsignup.com/rest`. Tracked as SPR-16; it does not block this PR.

## Privacy

The issue's requirement is that DOB and email are never persisted. How that is
enforced:

- **DOB and email live only in `st.session_state`.** Not in SQLite, not in
  logs, not in error strings, not in any file.
- **Email is a format-checked placeholder.** `runner_matching.valid_email` is
  the only thing that ever touches it. It is never sent to RunSignUp.
- **Neither crosses a cache key.** `@st.cache_data` keys are inspectable, so
  `cached_candidate_result_sets` and `cached_sweep_for_runner` key on name and
  scope only. Age classification happens in `classify_signup_matches`, in
  `app.py`, on rows that have *already left* the cache. `sweep_for_runner`
  accepts no DOB and no age at all — it returns RunSignUp's published `age`
  field for the caller to compare.
- **Enforced by a test, not just by review.** `test_results_search.py` asserts
  that no `dob`/`birth`/`email` identifier appears in the search module's
  signatures or source.

Confirmed by grep over the full diff on 2026-10-09: the only occurrences of
`dob`/`birth`/`email` outside tests are the session-state form widgets, the
pure `age_on_date`/`valid_email` functions, and the privacy comments above.

## Confidence model

`age` from RunSignUp is captured at **registration** time, not on race day. A
runner whose birthday falls between signing up and racing will legitimately
show an age one year off from their true age on the race date. That is the
entire reason for the ±1 tolerance:

| Condition | Confidence |
|---|---|
| `abs(age_on_race_date - result_age) <= 1` | **High** |
| result publishes no age, or no race date to compute against | **Possible** |
| off by more than 1 year | **Rejected** — shown collapsed, with the reason |

Nothing is ever auto-imported. Every row needs an explicit "This is me" before
it enters the tracker, carrying forward SPR-14's recommendation. This is a
deliberate product choice: a name collision that silently writes a stranger's
race into your history is much worse than one extra click.

A Feb-29 birthday resolves to Mar 1 in non-leap years — the leap birthday is
held open through Feb 28 rather than credited a day early. A race held exactly
on the birthday counts as the new age. Both are tested.

## Sources

- [API Developer Contract](https://runsignup.com/About-Us/APIDeveloper-Contract)
- [New API Registration Requirements](https://info.runsignup.com/2026/07/17/new-api-registration-requirements/)
- [API Methods](https://runsignup.com/API/Methods)
- [Authenticating with the API](https://help.runsignup.com/support/solutions/articles/17000065639-authenticating-with-the-api)
