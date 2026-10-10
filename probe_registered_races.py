"""Answer the one question that decides SPR-20: does registered-races return PAST races?

``/rest/user/registered-races`` takes no date filter, no pagination, and publishes
its response schema as bare ``{"type": "object"}``. So the documentation states
neither whether past registrations come back nor what the payload looks like. The
connected track is about race *history*, so if the endpoint is upcoming-only the
track cannot be built and SPR-20 should be closed rather than implemented.

That cannot be settled by reading docs, and it cannot be settled without a human:
OAuth's authorization-code flow requires the account owner to log in and consent
in a browser. This script makes that the *only* manual step -- about a minute --
and does the rest.

    python probe_registered_races.py

Or double-click ``probe.cmd`` in this directory, which does the same thing without
needing a terminal open at the right path.

It prints a consent URL, you approve it in a browser, you paste back the URL you
land on, and it prints the verdict. There is no setup step: if the OAuth app's
client id, secret and redirect URI are not already in ``.streamlit/secrets.toml``
or the environment, it asks for them, reading the secret through ``getpass`` so it
is neither echoed nor left in shell history.

The report it writes is **structural only** -- counts, a date range, and key
names, with no race names or finish times -- so it is safe to paste into a ticket.
Pass ``--show-races`` if you want your own race list printed to your terminal too;
that output is yours and is not written to the report file.

Nothing is stored: the access token lives in a local variable for the length of
this process, and the refresh token is dropped by ``redeem_code`` on receipt.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date
from urllib.parse import parse_qs, urlparse

import runsignup_oauth as oauth

REPORT_FILENAME = "registered-races-probe-report.md"

# Shorthands accepted at the redirect-URI prompt, so the common local value does
# not have to be typed exactly. Anything else is taken verbatim -- RunSignUp
# compares the registered URI literally, so guessing at corrections would turn a
# typo into a confusing consent-screen failure.
LOCALHOST_REDIRECT = "http://localhost:8501/"
_REDIRECT_SHORTHANDS = {"localhost", "local", "1", "l"}

_PROMPTS = {
    "RUNSIGNUP_OAUTH_CLIENT_ID": (
        "Client ID",
        "From RunSignUp -> My Account -> API Keys, your registered OAuth application.",
    ),
    "RUNSIGNUP_OAUTH_CLIENT_SECRET": (
        "Client secret",
        "Not echoed as you type, and not stored anywhere by this script.",
    ),
    "RUNSIGNUP_OAUTH_REDIRECT_URI": (
        "Redirect URI",
        "Must match the one registered on the OAuth app exactly, trailing slash "
        'included. Type "localhost" for %s.' % LOCALHOST_REDIRECT,
    ),
}


def _parse_callback(pasted: str) -> dict:
    """Accept either the full redirect URL or a bare query string."""
    text = pasted.strip().strip('"').strip("'")
    if not text:
        return {}
    query = urlparse(text).query if "?" in text or "://" in text else text
    return {key: values[0] for key, values in parse_qs(query).items() if values}


def _clean(value: str) -> str:
    """Strip whitespace and the quotes that come along with a copy-paste."""
    return value.strip().strip('"').strip("'").strip()


def _prompt_for_config(
    values: dict,
    *,
    input_fn=input,
    secret_input_fn=getpass.getpass,
) -> oauth.OAuthConfig:
    """Ask for whichever of the three values is missing, and return the config.

    This exists because the setup step, not the OAuth flow, is what actually
    trips people up: Streamlit *Cloud* secrets are invisible to a local process,
    so "I already set the secrets" and "the probe can see them" are different
    facts. Asking is also safer than the environment-variable route -- the secret
    goes through ``getpass``, so it is neither echoed nor left in shell history.

    Values already present in secrets or the environment are kept, not re-asked.
    """
    filled = dict(values)
    for name in oauth.CONFIG_KEYS:
        if filled.get(name):
            continue
        label, help_text = _PROMPTS[name]
        print("\n%s\n  %s" % (label, help_text))
        reader = secret_input_fn if name == "RUNSIGNUP_OAUTH_CLIENT_SECRET" else input_fn
        entered = _clean(reader("  %s: " % label))
        if name == "RUNSIGNUP_OAUTH_REDIRECT_URI" and entered.lower() in _REDIRECT_SHORTHANDS:
            entered = LOCALHOST_REDIRECT
        if not entered:
            raise oauth.OAuthConfigError("No %s entered." % label.lower())
        filled[name] = entered

    return oauth.OAuthConfig(
        client_id=filled["RUNSIGNUP_OAUTH_CLIENT_ID"],
        client_secret=filled["RUNSIGNUP_OAUTH_CLIENT_SECRET"],
        redirect_uri=filled["RUNSIGNUP_OAUTH_REDIRECT_URI"],
    )


def _load_config(interactive: bool) -> oauth.OAuthConfig:
    """Config from secrets/env, falling back to prompting at a terminal.

    Non-interactive callers (CI, a piped stdin, ``--no-prompt``) keep the old
    behaviour of failing with the full "where to put these" message, since a
    prompt nobody can answer would just hang.
    """
    values = oauth.configured_values()
    if all(values[name] for name in oauth.CONFIG_KEYS):
        return oauth.OAuthConfig(
            client_id=values["RUNSIGNUP_OAUTH_CLIENT_ID"],
            client_secret=values["RUNSIGNUP_OAUTH_CLIENT_SECRET"],
            redirect_uri=values["RUNSIGNUP_OAUTH_REDIRECT_URI"],
        )

    if not interactive:
        raise oauth.OAuthConfigError(
            "Missing RunSignUp OAuth configuration: "
            + ", ".join(name for name in oauth.CONFIG_KEYS if not values[name])
        )

    print("=" * 78)
    print("Step 0 of 2 -- OAuth app credentials")
    print("=" * 78)
    print(
        "\nNot found in .streamlit/secrets.toml or the environment, so I will ask.\n"
        "Nothing you type here is written to disk or kept after this run.\n"
        "\n(Secrets entered in the Streamlit Cloud dashboard live on Streamlit's\n"
        "servers and are not readable by a local script like this one -- copy them\n"
        "from the app's Settings -> Secrets page.)"
    )
    return _prompt_for_config(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--show-races",
        action="store_true",
        help="also print your own race list to the terminal (not written to the report)",
    )
    parser.add_argument(
        "--save-payload",
        metavar="PATH",
        help="write the raw JSON payload here for debugging. Contains your race "
        "history -- do not commit it or paste it into a ticket.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="fail instead of asking for credentials interactively",
    )
    args = parser.parse_args()

    interactive = not args.no_prompt and sys.stdin.isatty()
    try:
        config = _load_config(interactive)
    except (oauth.OAuthConfigError, EOFError, KeyboardInterrupt) as exc:
        if isinstance(exc, (EOFError, KeyboardInterrupt)):
            print("\nCancelled.", file=sys.stderr)
            return 1
        print("Configuration problem:\n  %s" % exc, file=sys.stderr)
        print(
            "\nRun this in a terminal and it will simply ask for the three values.\n"
            "Otherwise, either source works -- Streamlit secrets are read first, then the "
            "environment.\n"
            "\n  Environment variables, for a one-off run. Nothing is written to disk:\n"
            "    RUNSIGNUP_OAUTH_CLIENT_ID, RUNSIGNUP_OAUTH_CLIENT_SECRET,\n"
            "    RUNSIGNUP_OAUTH_REDIRECT_URI\n"
            "\n  .streamlit/secrets.toml (already gitignored), which also serves "
            "`streamlit run`:\n"
            '    RUNSIGNUP_OAUTH_CLIENT_ID = "..."\n'
            '    RUNSIGNUP_OAUTH_CLIENT_SECRET = "..."\n'
            '    RUNSIGNUP_OAUTH_REDIRECT_URI = "..."   # must match the registered URI exactly\n'
            "\nSecrets set in the Streamlit Cloud dashboard live on Streamlit's "
            "servers and are\nnot visible to this local script. Copy the values "
            "across from the app's\nSettings -> Secrets page.\n",
            file=sys.stderr,
        )
        return 2

    pending = oauth.begin_authorization(config)

    print("=" * 78)
    print("Step 1 of 2 -- approve read-only access in your browser")
    print("=" * 78)
    print("\nOpen this URL (it asks for %s, read-only):\n" % oauth.READ_SCOPE)
    print(pending.url)
    print(
        "\nYou will be asked to log in to RunSignUp, then to approve. Afterwards your\n"
        "browser lands on:\n\n    %s\n\n"
        "That page may well show an error or fail to load -- that is expected and fine.\n"
        "The part that matters is the URL in the address bar, which now carries the\n"
        "authorization code. Copy the whole thing." % config.redirect_uri
    )
    print("\n" + "=" * 78)
    print("Step 2 of 2 -- paste the URL you landed on")
    print("=" * 78)

    try:
        pasted = input("\nURL: ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 1

    params = _parse_callback(pasted)
    if not params:
        print(
            "\nCould not read any query parameters out of that. Paste the full URL,\n"
            "including everything from '?' onwards.",
            file=sys.stderr,
        )
        return 1

    try:
        code = oauth.authorization_code_from_callback(params, pending.state)
    except oauth.OAuthFlowError as exc:
        print("\nAuthorization failed:\n  %s" % exc, file=sys.stderr)
        return 1

    print("\nRedeeming the authorization code (single-use, 5 minute expiry)...")
    try:
        grant = oauth.redeem_code(config, code, pending.code_verifier)
    except oauth.OAuthFlowError as exc:
        print("\nToken exchange failed:\n  %s" % exc, file=sys.stderr)
        return 1

    print(
        "  Got an access token. scope=%r expires_in=%ss (~%s days). Refresh token discarded."
        % (grant.scope, grant.expires_in, round(grant.expires_in / 86400) if grant.expires_in else "?")
    )

    print("\nCalling registered-races...")
    try:
        payload = oauth.registered_races(grant.access_token)
    except oauth.OAuthFlowError as exc:
        print("\nLookup failed:\n  %s" % exc, file=sys.stderr)
        return 1

    registrations = oauth.parse_registrations(payload)
    summary = oauth.summarize_registrations(registrations, today=date.today())
    shape = oauth.describe_payload_shape(payload)
    source_key = oauth.registration_list_key(payload) or "(none found)"

    report = "\n".join(
        [
            "# registered-races probe report (SPR-20)",
            "",
            "Run %s against %s" % (date.today().isoformat(), oauth.REGISTERED_RACES_URL),
            "Structural only -- no race names, dates or times of any individual race.",
            "",
            "## Verdict",
            "",
            summary.verdict,
            "",
            "## Counts",
            "",
            "| | |",
            "|---|---|",
            "| registrations parsed | %d |" % summary.total,
            "| in the past | %d |" % summary.past,
            "| today or upcoming | %d |" % summary.upcoming,
            "| no usable date | %d |" % summary.undated,
            "| earliest race date | %s |" % (summary.earliest or "n/a"),
            "| latest race date | %s |" % (summary.latest or "n/a"),
            "",
            "## Payload shape",
            "",
            "Published schema is bare `{\"type\": \"object\"}`, so this is the real one:",
            "",
            "```",
            shape,
            "```",
            "",
            "Registration list found under: `%s`" % source_key,
            "",
            "## Grant",
            "",
            "| | |",
            "|---|---|",
            "| scope granted | `%s` |" % (grant.scope or "(not reported)"),
            "| access token lifetime | %ss |" % grant.expires_in,
            "",
        ]
    )

    with open(REPORT_FILENAME, "w", encoding="utf-8") as handle:
        handle.write(report)

    print("\n" + "=" * 78)
    print(report)
    print("=" * 78)
    print("\nWritten to %s -- safe to paste into the ticket." % REPORT_FILENAME)

    if args.show_races:
        print("\nYour registrations (terminal only, not in the report):")
        if not registrations:
            print("  none parsed")
        for reg in sorted(registrations, key=lambda r: r.race_date or "9999"):
            print(
                "  %-10s  race_id=%-8d %s%s"
                % (
                    reg.race_date or "(no date)",
                    reg.race_id,
                    reg.race_name or "(unnamed)",
                    " / %s" % reg.event_name if reg.event_name else "",
                )
            )

    if args.save_payload:
        with open(args.save_payload, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print("\nRaw payload written to %s -- contains your race history, do not commit."
              % args.save_payload)

    if summary.total and not summary.returns_past_races:
        # Non-zero exit makes "upcoming only" unmissable in a terminal.
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
