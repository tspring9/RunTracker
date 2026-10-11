"""Registrations -> result-set candidates for the connected track (SPR-20).

This is the half of the connected track that replaces *discovery*. The anonymous
track has to guess which races a runner might have run, from a state/year scope;
a connected runner tells us directly. So this module's whole job is:

    the runner's registration list  ->  candidate result sets

and then it stops. The candidates it returns are fed to the **existing**
``results_search.sweep_for_runner``, which does the name filtering, and the
existing ``runner_matching.classify_match``, which does the age/confidence call.
There is deliberately no matching logic here -- having two matching engines was
the thing the two-track design existed to avoid (SPR-15).

Why this is worth having at all
-------------------------------
Since SPR-30 the anonymous track reads candidates from an **offline SQLite
index** that a human has to build per state/year (``results_search.build_index``,
CLI only). If the index does not cover a scope, the search cannot answer -- that
is the ``unbuilt_index`` terminal state from SPR-49.

The connected track needs none of that. Registrations name the race ids outright,
so there is no index to build, no state list to pick, and no year range to
guess. A connected runner gets an answer in a few calls per race on an account
with nothing indexed at all. That is a bigger win than the original
"~20-60 calls instead of ~300-500" framing on the ticket, which was written
before discovery moved offline.

The date is the point
---------------------
``runsignup_results.discover_result_sets`` defaults to scanning only the **five
most recent race days** of a race (``DEFAULT_DISCOVERY_EVENT_DAYS``). That
default is right for its original caller, which was looking for *recent*
results, and wrong here: a ``race_id`` identifies a recurring race *series*, so
for an annual race the five most recent days are the last five years. A 2021
registration on a race that has run every year since would be silently missed --
and this feature is specifically about old races. The probe's real account spans
2021 to 2026, so this is not a hypothetical.

Registrations carry the race *date*, so this module targets that date instead of
scanning recent days (:func:`select_event_days`). That fixes the miss and costs
fewer calls than widening the scan would.

Call cost
---------
Per registration: 1 ``/race/{id}`` call, then 1 ``get-result-sets`` call per
event on the targeted day(s). Events come out of the ``/race/{id}`` payload, so
listing them is free. Typical total is 2-4 calls for a race.

Nothing here is cached. ``@st.cache_data`` keys are inspectable, and these
arguments and return values are the runner's own race history (see PRIVACY
below), so it must not be wrapped in one.

PRIVACY
-------
* Takes no date of birth and no email -- same bar as SPR-15. Age classification
  happens in the caller, after this returns, exactly as ``sweep_for_runner``
  documents for the anonymous track.
* The access token is passed straight to ``runsignup_oauth`` and is never
  logged, persisted, or put in an error message.
* Race names and dates here *are* the runner's personal history. They belong in
  ``st.session_state`` for the session and nowhere else -- not SQLite, not a
  cache key, not a log line. :class:`CoverageReport` is counts only, so it is
  safe to surface or report; the candidates are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import runsignup_oauth as oauth
import runsignup_results as rsu

# A race "weekend": when no event sits exactly on the registration date, events
# within this many days are treated as the same running of the race. Covers
# multi-day races, and registrations stamped with a race-level date (RunSignUp's
# `next_date`) that differs by a day or two from the event the runner entered.
EVENT_DAY_TOLERANCE_DAYS = 3

# Default ceiling on API calls for one connected-track discovery pass. Sized for
# the measured case -- 7 registrations at 2-4 calls each -- with headroom for a
# runner with a longer history. The anonymous track's equivalent is
# SearchScope.max_calls; see "On unifying the budget" in the design doc.
DEFAULT_MAX_CALLS = 60


class ConnectedTrackError(RuntimeError):
    """Raised when the connected track cannot produce candidates at all."""


# -------------------------------------------------
# Budget
# -------------------------------------------------
@dataclass
class CallBudget:
    """One counter for every HTTP call a discovery pass makes.

    Deliberately the same shape as ``results_search.CallBudget`` (a ``limit``, a
    ``used``, and a ``spend()`` that returns False when exhausted) so the two can
    be unified when the connected track is wired into the Sign Up tab and both
    stages need to share one cap. Not imported from there because
    ``results_search`` does not exist on ``main`` yet -- it arrives with PR #5.

    Single-threaded by design: unlike the anonymous sweep, this stage issues
    calls serially, so there is no CAS dance to get right here (cf. SPR-46).
    """

    limit: int
    used: int = 0

    def spend(self, n: int = 1) -> bool:
        """Reserve ``n`` calls, or return False and reserve nothing."""
        if self.used + n > self.limit:
            return False
        self.used += n
        return True

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


# -------------------------------------------------
# What happened, in counts only
# -------------------------------------------------
@dataclass(frozen=True)
class CoverageReport:
    """Counts only -- no race names, no dates. Safe to log or paste in a ticket.

    The UI needs to distinguish "you have no results" from "we ran out of
    budget before checking all your races", which is why ``races_covered`` and
    ``races_skipped_for_budget`` are separate rather than one "done" flag.
    """

    registrations_total: int
    registrations_past: int
    registrations_upcoming: int
    registrations_undated: int
    races_covered: int
    races_without_results: int
    races_skipped_for_budget: int
    candidates_found: int
    calls_used: int
    calls_budget: int

    @property
    def complete(self) -> bool:
        """True when every past registration was actually looked at."""
        return self.races_skipped_for_budget == 0

    @property
    def capped(self) -> bool:
        return self.races_skipped_for_budget > 0

    def describe(self) -> str:
        """One line for the UI caption. Counts only, per the class docstring."""
        if self.registrations_total == 0:
            return "RunSignUp returned no registrations for this account."
        if self.registrations_past == 0:
            return (
                f"{self.registrations_total} registration"
                f"{'s' if self.registrations_total != 1 else ''} found, but none are in the "
                "past yet, so there are no results to look for."
            )
        parts = [
            f"Checked {self.races_covered} of {self.registrations_past} past "
            f"registration{'s' if self.registrations_past != 1 else ''}",
            f"found {self.candidates_found} published result set"
            f"{'s' if self.candidates_found != 1 else ''}",
            f"{self.calls_used} API call{'s' if self.calls_used != 1 else ''}",
        ]
        line = " — ".join(parts) + "."
        if self.capped:
            line += (
                f" {self.races_skipped_for_budget} race"
                f"{'s' if self.races_skipped_for_budget != 1 else ''} not checked: "
                "hit the call cap."
            )
        return line


# -------------------------------------------------
# Which registrations can possibly have results
# -------------------------------------------------
def partition_registrations(
    registrations: list[oauth.Registration], today: date | None = None
) -> tuple[list[oauth.Registration], list[oauth.Registration], list[oauth.Registration]]:
    """Split registrations into ``(past, upcoming, undated)``.

    Only the past ones can have results, so only those are worth spending calls
    on. A race *on* today counts as upcoming: its results do not exist yet,
    which matches ``oauth.summarize_registrations``' rule so the probe's verdict
    and this split can never disagree about the same account.

    Undated registrations are returned separately rather than lumped in with
    past ones, because without a date this module cannot target an event day and
    has to fall back to a recent-days scan -- a different cost and a different
    confidence. The measured account had none, but the payload schema is
    undocumented, so they are handled rather than assumed away.
    """
    reference = (today or date.today()).isoformat()
    past, upcoming, undated = [], [], []
    for registration in registrations:
        if not registration.race_date:
            undated.append(registration)
        elif registration.race_date < reference:
            past.append(registration)
        else:
            upcoming.append(registration)

    # Newest first: if the budget runs out, the races a runner most likely cares
    # about have already been checked.
    past.sort(key=lambda registration: registration.race_date, reverse=True)
    return past, upcoming, undated


# -------------------------------------------------
# Targeting the right running of a recurring race
# -------------------------------------------------
def _parse_iso(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def select_event_days(events: list[dict], race_date: str) -> list[dict]:
    """Pick the events belonging to the running of the race on ``race_date``.

    This is the function that stops a recurring race's older runnings being
    missed -- see "The date is the point" in the module docstring.

    Three tiers, widening only when the previous one finds nothing:

    1. Events dated exactly ``race_date``.
    2. Events within :data:`EVENT_DAY_TOLERANCE_DAYS` -- a race weekend, or a
       registration stamped with the race-level date rather than the event's.
    3. Events in the same calendar year. The year is the coarsest identifier
       that still picks one running of an annual race, and it is the last tier
       that is still *targeted*; beyond it we would be guessing.

    Returns ``[]`` when no tier matches, which the caller reports as a race it
    could not cover rather than silently scanning everything.

    With no ``race_date`` at all, returns ``[]`` -- the caller falls back to
    ``discover_result_sets``' own recent-days behaviour, since there is no date
    here to target.
    """
    target = _parse_iso(race_date)
    if target is None:
        return []

    dated = [(event, _parse_iso(event.get("date", ""))) for event in events]
    dated = [(event, parsed) for event, parsed in dated if parsed is not None]
    if not dated:
        return []

    exact = [event for event, parsed in dated if parsed == target]
    if exact:
        return exact

    window = timedelta(days=EVENT_DAY_TOLERANCE_DAYS)
    near = [event for event, parsed in dated if abs(parsed - target) <= window]
    if near:
        return near

    same_year = [event for event, parsed in dated if parsed.year == target.year]
    return same_year


# -------------------------------------------------
# One registration -> its candidate result sets
# -------------------------------------------------
@dataclass(frozen=True)
class RaceDiscovery:
    """One race's candidates, and whether the budget cut the race short.

    ``truncated`` exists so the caller never reports a half-checked race as
    "this race published no results". Those are opposite messages -- one means
    "nothing to find", the other means "we did not look" -- and collapsing them
    into an empty list would make the UI claim the first when it means the
    second.
    """

    candidates: list[dict]
    truncated: bool = False


def candidates_for_registration(
    registration: oauth.Registration,
    budget: CallBudget,
    include_virtual: bool = False,
) -> RaceDiscovery:
    """Result-set candidates for one registered race, targeted at its date.

    Emits the dict shape ``results_search.sweep_for_runner`` consumes. Two date
    keys are set on purpose, and both are load-bearing:

    * ``event_date`` -- the SPR-30 candidate contract, which ``app.py`` reads
      from ``_context`` to backfill a row's ``race_date``.
    * ``date`` -- what ``runsignup_results.result_to_tracker_row`` actually
      reads for ``race_date``.

    Setting only one of them produces rows with a blank date, so they are set
    together from a single value rather than computed twice.
    """
    if not budget.spend():
        return RaceDiscovery(candidates=[], truncated=True)

    race = rsu.fetch_race(registration.race_id)
    race_name = race.get("name", "") or registration.race_name
    address = race.get("address") or {}
    city = address.get("city", "") or registration.city
    state = (address.get("state") or registration.state or "").upper()

    # Free: events come out of the /race/{id} payload already fetched above, and
    # fetch_race passes future_events_only="F" so that payload carries every
    # historical event -- which is what makes an old registration reachable.
    #
    # Guarded on `race` being truthy because list_events treats a falsy race as
    # "not supplied" and re-fetches it, which would be a second HTTP call that
    # the budget never counted.
    events = rsu.list_events(registration.race_id, race) if race else []
    targeted = select_event_days(events, registration.race_date)
    if not targeted:
        # No date to target, or no event matched any tier. Fall back to the
        # library's own recent-days scan rather than returning nothing, but keep
        # it bounded -- an undated registration is the main way to get here.
        targeted = sorted(
            events, key=lambda event: event.get("date") or "", reverse=True
        )[:rsu.DEFAULT_DISCOVERY_EVENT_DAYS]

    candidates = []
    truncated = False
    for event in targeted:
        if not include_virtual and event.get("virtual"):
            continue
        if not budget.spend():
            truncated = True
            break
        try:
            result_sets = rsu.list_result_sets(registration.race_id, event["event_id"])
        except rsu.RunSignUpError:
            # Events with no results raise rather than returning empty; that is
            # a normal outcome for a race whose organiser never published any.
            continue

        event_date = event.get("date", "") or registration.race_date
        for result_set in result_sets:
            if not result_set["public"]:
                continue
            candidates.append(
                {
                    "race_id": registration.race_id,
                    "event_id": event["event_id"],
                    "result_set_id": result_set["result_set_id"],
                    "race_name": race_name,
                    "event_name": event.get("name", ""),
                    # Both date keys, one value -- see the docstring.
                    "event_date": event_date,
                    "date": event_date,
                    "state": state,
                    "city": city,
                    "url": race.get("url", "") or "",
                    "race_type": event.get("race_type", "")
                    or rsu.race_type_from_event_name(event.get("name", "")),
                    "result_set_name": result_set["name"],
                    "preliminary": result_set["preliminary"],
                    # Marks rows that came from the runner's own registration
                    # list rather than a name guess. The UI says so, because
                    # "we found this in your account" deserves more trust than
                    # "someone with your name appears here".
                    "from_registration": True,
                }
            )
    return RaceDiscovery(candidates=candidates, truncated=truncated)


# -------------------------------------------------
# The whole pass
# -------------------------------------------------
@dataclass
class ConnectedCandidates:
    """Candidates plus what it took to get them."""

    candidates: list[dict] = field(default_factory=list)
    coverage: CoverageReport | None = None


def candidates_from_registrations(
    registrations: list[oauth.Registration],
    max_calls: int = DEFAULT_MAX_CALLS,
    include_virtual: bool = False,
    today: date | None = None,
    progress_cb=None,
) -> ConnectedCandidates:
    """Registration list -> candidates for every past registration, within budget.

    Does no HTTP for upcoming registrations: results cannot exist for a race
    that has not happened, so spending a call to confirm that would be waste.

    ``progress_cb`` is called with the registration index and total after each
    race, for a Streamlit progress bar. It receives counts, never race names --
    the bar is rendered in the page, but a callback is also the easiest place for
    personal data to end up somewhere it should not.
    """
    past, upcoming, undated = partition_registrations(registrations, today=today)
    budget = CallBudget(limit=max(int(max_calls), 0))

    # Undated registrations are checked after the dated ones: they cost more
    # (a recent-days scan rather than a targeted day) and are less certain, so
    # they should be the first thing a tight budget gives up on.
    to_check = past + undated

    candidates: list[dict] = []
    races_covered = 0
    races_without_results = 0

    for index, registration in enumerate(to_check):
        if budget.exhausted:
            break
        discovery = candidates_for_registration(
            registration, budget, include_virtual=include_virtual
        )
        candidates.extend(discovery.candidates)

        # A truncated race is deliberately NOT counted as covered, so it falls
        # into races_skipped_for_budget below -- see RaceDiscovery. Any
        # candidates it did find are still kept; they are real, just not the
        # whole race.
        if not discovery.truncated:
            races_covered += 1
            if not discovery.candidates:
                races_without_results += 1

        if progress_cb is not None:
            progress_cb(index + 1, len(to_check))

    # Newest race first, matching indexed_candidates' ordering so both tracks
    # present results in the same order.
    candidates.sort(key=lambda candidate: candidate["event_date"], reverse=True)

    coverage = CoverageReport(
        registrations_total=len(registrations),
        registrations_past=len(past),
        registrations_upcoming=len(upcoming),
        registrations_undated=len(undated),
        races_covered=races_covered,
        races_without_results=races_without_results,
        # Everything not fully covered: races the loop never reached because the
        # budget was already gone, plus races it started and had to cut short.
        races_skipped_for_budget=max(len(to_check) - races_covered, 0),
        candidates_found=len(candidates),
        calls_used=budget.used,
        calls_budget=budget.limit,
    )
    return ConnectedCandidates(candidates=candidates, coverage=coverage)


def discover_connected_candidates(
    access_token: str,
    max_calls: int = DEFAULT_MAX_CALLS,
    include_virtual: bool = False,
    api_registration: tuple[str, str] | None = None,
    today: date | None = None,
    progress_cb=None,
) -> ConnectedCandidates:
    """Access token -> candidate result sets. The connected track's entry point.

    One call to ``registered-races`` (which does not count against ``max_calls``,
    since it is the lookup that defines the work rather than part of it), then a
    targeted discovery pass per past registration.

    ``api_registration`` is SPR-16's caller registration, passed through to ride
    along on the lookup; it authenticates nothing (see ``runsignup_oauth``) but
    becomes mandatory 2027-01-01.

    Raises :class:`oauth.OAuthFlowError` if the token is rejected -- deliberately
    not caught here, because "your connection expired, reconnect" is a different
    message from "you have no results" and the UI must not merge them.
    """
    payload = oauth.registered_races(access_token, api_registration=api_registration)
    registrations = oauth.parse_registrations(payload)
    return candidates_from_registrations(
        registrations,
        max_calls=max_calls,
        include_virtual=include_virtual,
        today=today,
        progress_cb=progress_cb,
    )
