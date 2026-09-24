#!/usr/bin/env python3
"""PROBE: can a headless browser mint the sessions the refresh needs?

Builds nothing and stores nothing. It answers one question so we know whether
an unattended refresh is worth building properly.

TWO SESSIONS, NOT ONE
    The refresh needs two, on different hosts, and they are not interchangeable:
      pr.alm.me.sap.com  connect.sid       -- release detection (delta --check)
      support.sap.com    SUPPORT_IDS_PROD  -- the document download
    A me.sap.com portal cookie authenticates neither. Proving one says nothing
    about the other, so this probe tests both on a single login: the login is
    the expensive part, and both hosts federate to the same IdP, so the second
    should come free via SSO. If it does not, that is the finding.

WHY A BROWSER AT ALL
    me.sap.com returns 200 with no form and no server-side redirect -- the SAML
    flow runs entirely in JavaScript. urllib and requests cannot complete it
    whatever credentials they hold, so a real browser engine is the only route.
    Storing the password in Secrets Manager does not change that; the thing that
    expires is a SESSION, and only a login can mint a new one.

WHAT EARLIER RUNS SETTLED (2026-09-24)
    Bot detection does NOT block this -- the one risk that could have closed the
    route outright. blogs.sap.com returns 403 to a scripted request even with
    full browser headers, so accounts.sap.com refusing a headless browser was
    the likely outcome. It did not: two-step form found and filled, SAML
    completed, landed back on Process Navigator.

    Then the cookie replay 401'd, for a reason the probe's own comment had
    already named: connect.sid is issued by pr.alm.me.sap.com, and cookies were
    harvested before the SPA had called that host at all. Driving one real
    service call from inside the browser first fixed it -- HTTP 200 with a row,
    replayed OUTSIDE the browser. That is the pr.alm half proven.

WHAT THE SECOND RUN EXPOSED (2026-09-24)
    The support half reported PASS while SUPPORT_IDS_PROD -- the one cookie the
    fetcher actually needs -- was absent from the jar. Two separate faults, and
    the PASS was worthless:

    1. sapme_fetch.session_is_live was broken. It unpacked fetch()'s third
       return value (the final URL) into a variable named `kind` and compared
       it to "login", so it had never returned False in its life. Fixed there,
       routed through classify(), and pinned by brain-tests/test_session_check.
    2. Navigating straight at a .xlsx DOWNLOADS it. The navigation aborts, no
       HTML page loads, no SAML redirect runs, so support.sap.com never issues
       a session at all. It now loads an HTML page on the host first.

    A negative control was added as well, because neither fault would have been
    caught by a check that only ever asks "did it work".

THE RULE THIS FOLLOWS
    A session is proven by REPLAYING it outside the browser, never by the
    browser succeeding. The browser carries state a cookie export does not, so
    an in-browser 200 is necessary and nowhere near sufficient. Both halves
    below report the two numbers separately, because a failure that only the
    replay sees needs the opposite response from one the browser sees too.

    And a PASS is only believed once the same test has been shown to FAIL on a
    junk cookie. Three of the four defects found in this probe so far produced
    a false pass rather than a false failure, which is the strictly more
    expensive direction.

WHAT IT DOES NOT DO
    No credential storage, no Secrets Manager, no IAM change, nothing written to
    disk but a failure screenshot. The password is prompted for and held in
    memory. Minted cookies are never printed -- only their length and whether
    they work.

Install first (on the host that runs the fetch). On Amazon Linux 2023 the OS
libraries are NOT pulled in by pip, and Chromium fails at launch on
libatk-1.0.so.0 with no hint that the cause is packaging:
    sudo dnf install -y atk at-spi2-atk cups-libs libXcomposite libXdamage
    sudo dnf install -y libXrandr libgbm pango alsa-lib nss
    pip3.11 install playwright && python3.11 -m playwright install chromium

Usage:
    python3.11 scripts/probe_sap_login.py                 # both halves
    python3.11 scripts/probe_sap_login.py --only pralm    # release detection only
    python3.11 scripts/probe_sap_login.py --only support  # download only
"""

import sys
import json
import getpass
import argparse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ENTRY  = "https://me.sap.com/processnavigator/SolS/EARL_SolS-013/2608?region=DE"
VERIFY = ("https://pr.alm.me.sap.com/ui/earl-pn-ui/v1/odata/v4/EAXService/"
          "LatestSolutionScenarioIds?%24top=1")
# An HTML page on support.sap.com that requires a session, so loading it runs
# the SAML redirect and makes the host issue SUPPORT_IDS_PROD. Navigating
# straight at a document does not: the browser downloads the file, the
# navigation aborts, and no session is ever established.
SUPPORT_SSO = "https://launchpad.support.sap.com/"
SHOT   = Path("/tmp/sap-login-probe.png")

# Confirmed against the live SAP IdP on 2026-09-24: the form is TWO-STEP and
# matched j_username -> #logOnFormSubmit -> j_password, i.e. the first entry in
# each list. The rest are kept because SAP has used several login UIs (Customer
# Data Cloud, IAS, the classic form) and swapping between them is not something
# we would be told about. A probe that says "none of these matched" is more
# useful than a timeout.
USER_SEL = ["input[name='j_username']", "#j_username", "input[name='identifier']",
            "input[type='email']", "input[name='username']", "#logonId"]
PASS_SEL = ["input[name='j_password']", "#j_password", "input[type='password']",
            "input[name='password']"]
NEXT_SEL = ["#logOnFormSubmit", "button[type='submit']", "input[type='submit']",
            "text=Continue", "text=Sign In", "text=Log On"]


def first_visible(page, selectors, timeout=8000):
    """The first selector that actually appears, or (None, None)."""
    for sel in selectors:
        try:
            el = page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                return sel, el
        except Exception:
            continue
    return None, None


def do_login(page, user, pwd):
    """Complete the SAML login. Returns None on success, else why not."""
    print("1. opening Process Navigator ...")
    page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)                 # let the SPA redirect
    print("   landed on: %s" % page.url[:95])

    print("2. looking for the login form ...")
    usel, uel = first_visible(page, USER_SEL, timeout=12000)
    if not uel:
        SHOT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOT))
        return ("no login field found.\n   url    : %s\n   title  : %s\n   shot   : %s\n"
                "   If that page is a bot challenge, this route is closed.\n"
                "   If it is a login UI we have no selector for, add it to USER_SEL."
                % (page.url[:95], page.title(), SHOT))
    print("   user field: %s" % usel)

    uel.fill(user)
    # Some SAP logins ask for the user first and reveal the password after.
    psel, pel = first_visible(page, PASS_SEL, timeout=4000)
    if not pel:
        nsel, nel = first_visible(page, NEXT_SEL, timeout=4000)
        if nel:
            print("   two-step form; advancing via %s" % nsel)
            nel.click()
            page.wait_for_timeout(2500)
        psel, pel = first_visible(page, PASS_SEL, timeout=10000)
    if not pel:
        return "found a user field but no password field - login UI unrecognised"
    print("   pass field: %s" % psel)
    pel.fill(pwd)

    print("3. submitting ...")
    _ssel, sel_el = first_visible(page, NEXT_SEL, timeout=6000)
    if sel_el:
        sel_el.click()
    else:
        pel.press("Enter")
    page.wait_for_timeout(9000)                 # SAML round trip
    print("   now on: %s" % page.url[:95])
    return None


def mint(page, ctx, url, suffix):
    """Call a host from inside the browser, then export its cookies.

    The navigation is not incidental. A host the browser has never contacted
    has set no cookies for it, so harvesting straight after login yields IdP
    state that 401s. This is the step that was missing when the first run
    looked like a failure.
    """
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
    except Exception as exc:
        # Navigating to a downloadable file aborts the navigation without that
        # being a failure -- the cookies are set by then regardless. Carry on
        # and let the replay be the judge.
        status = "nav aborted (%s)" % type(exc).__name__
    got = [c for c in ctx.cookies() if c["domain"].endswith(suffix)]
    header = "; ".join("%s=%s" % (c["name"], c["value"]) for c in got)
    return status, header, got


def replay_odata(cookie):
    """Does the exported session open the OData service, outside the browser?"""
    r = urllib.request.Request(VERIFY)
    r.add_header("Cookie", cookie)
    r.add_header("Accept", "application/json")
    r.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            d = json.loads(x.read())
        return True, "HTTP %s, %d row(s)" % (x.status, len(d.get("value", [])))
    except Exception as exc:
        return False, str(exc)[:90]


_ROWS = None


def fetch_rows():
    """The fetcher's own row list, loaded once. Returns (rows, why_not).

    Cached because load_rows prints a line about excluded duplicates, and
    calling it twice would print it twice and invite the reader to think two
    different row sets are in play.

    SystemExit is caught deliberately: load_rows calls sys.exit when the
    manifest is missing, and SystemExit does not inherit from Exception, so the
    obvious `except Exception` would let it straight through and abort the
    probe with no hint that --only pralm is still available.
    """
    global _ROWS
    if _ROWS is None:
        try:
            from sapme_fetch import load_rows                  # noqa: PLC0415
            _ROWS = (load_rows("sapbp", False), None)
        except SystemExit as exc:
            _ROWS = (None, str(exc)[:120])
        except Exception as exc:
            _ROWS = (None, "%s: %s" % (type(exc).__name__, str(exc)[:100]))
    return _ROWS


CONTROL_COOKIE = "s4pc-probe-control=not-a-session"


def replay_support(cookie):
    """Reuse the fetcher's OWN session check, and CONTROL it before believing it.

    session_is_live already probes several rows, because needs_auth is inferred
    from the host and some support.sap.com assets serve anonymously -- a single
    probe once reported "session ok" against a cookie that was already dead.
    Re-implementing that here would give this project two definitions of a live
    session, and the copy is always the one that drifts.

    THE CONTROL IS NOT OPTIONAL. PROBE_N=3 reduced the anonymous-asset false
    positive; it did not remove it. If all three probe rows happen to be DAM
    files that serve without a session -- and the first authenticated row in
    this manifest is /content/dam/... -- then "live" comes back True for a
    cookie that authenticates nothing at all. On the first run of this probe it
    did exactly that, while SUPPORT_IDS_PROD was absent from the jar.

    So ask the same question with a junk cookie first. If the answer is still
    "live", the probe rows cannot distinguish a session from no session, and
    the only honest verdict is INCONCLUSIVE. A test that passes when it should
    fail has negative value: it moves the blame somewhere else.
    """
    rows, why = fetch_rows()
    if rows is None:
        return None, why
    from sapme_fetch import session_is_live                     # noqa: PLC0415
    auth = [r for r in rows if r.get("needs_auth") and r.get("url")]
    if not auth:
        return None, "no rows need auth - nothing to test with"

    control = session_is_live(rows, CONTROL_COOKIE)
    if control is not False:
        return None, ("NOT DISCRIMINATING - a junk cookie also scores %r on these "
                      "probe rows, so they serve anonymously and a pass here would "
                      "prove nothing. Point the probe at a row that genuinely "
                      "demands a session." % control)

    live = session_is_live(rows, cookie)
    if live is None:
        return None, "inconclusive (every probe was a transient failure)"
    return live, ("%d authenticated row(s); junk cookie correctly rejected, so this "
                  "result is meaningful" % len(auth))


def support_nav_url():
    """A real URL the fetcher will actually use, not an invented one."""
    rows, why = fetch_rows()
    if rows is None:
        return None, why
    for r in rows:
        if r.get("needs_auth") and r.get("url"):
            return r["url"], None
    return None, "no authenticated rows in the manifest"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="S-user / P-user; prompted if omitted")
    ap.add_argument("--only", choices=("pralm", "support"),
                    help="test one half only (default: both)")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (needs a display; not on EC2)")
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright         # noqa: PLC0415
    except ImportError:
        return ("playwright not installed. On the fetch host:\n"
                "  pip3.11 install playwright && python3.11 -m playwright install chromium")

    do_pralm   = a.only in (None, "pralm")
    do_support = a.only in (None, "support")

    nav_url = None
    if do_support:
        nav_url, why = support_nav_url()
        if not nav_url:
            # Fail before asking for a password rather than after.
            return ("cannot test the support half: %s\n"
                    "   Run sapbp_catalog.py first, or use --only pralm." % why)

    user = a.user or input("SAP user: ").strip()
    pwd  = getpass.getpass("SAP password (not stored, not echoed): ")
    if not user or not pwd:
        return "need both a user and a password"

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        # A real UA and viewport: the default headless fingerprint is the most
        # obvious thing for bot detection to key on, and we are testing whether
        # the login works, not whether the default fingerprint does.
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900}, locale="en-US")
        page = ctx.new_page()

        err = do_login(page, user, pwd)
        if err:
            browser.close()
            return err

        if do_pralm:
            print("4. pr.alm: calling the OData service from inside the browser ...")
            status, header, got = mint(page, ctx, VERIFY, "me.sap.com")
            names = sorted(c["name"] for c in got)
            print("   in-browser status : %s" % status)
            print("   cookies (%d)       : %s" % (len(names), ", ".join(names)))
            print("   connect.sid       : %s" % ("connect.sid" in names))
            print("   header            : %d chars, not printed" % len(header))
            results["pralm"] = (status, header)

        if do_support:
            # SSO FIRST, DOCUMENT SECOND. Navigating straight at a .xlsx makes
            # the browser DOWNLOAD it -- the navigation aborts, no HTML page
            # loads, so no SAML redirect runs and support.sap.com never issues
            # a session. The first run did exactly that and reported "nav
            # aborted" with SUPPORT_IDS_PROD absent, while the replay still
            # claimed to pass. Load a real page on the host first.
            print("5. support.sap.com: establishing the session via SSO ...")
            status, _h, _g = mint(page, ctx, SUPPORT_SSO, "support.sap.com")
            print("   %s -> %s" % (SUPPORT_SSO, status))
            page.wait_for_timeout(5000)

            print("   then opening a real document URL ...")
            print("   %s" % nav_url[:95])
            dstatus, header, got = mint(page, ctx, nav_url, "sap.com")
            names = sorted({c["name"] for c in got})
            has_ids = "SUPPORT_IDS_PROD" in names
            print("   in-browser status : %s   (a download aborts navigation; "
                  "not a failure)" % dstatus)
            print("   cookies (%d)       : %s" % (len(names), ", ".join(names)[:180]))
            print("   SUPPORT_IDS_PROD  : %s%s"
                  % (has_ids, "" if has_ids else "   <-- the cookie the fetcher needs"))
            print("   header            : %d chars, not printed" % len(header))
            # The SSO page status is what says whether a session exists; the
            # document navigation aborting tells us nothing either way.
            results["support"] = (status, header)

        if not any(h for _s, h in results.values()):
            SHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOT))
            browser.close()
            return "logged in but exported no cookies at all. shot: %s" % SHOT
        browser.close()

    verdict = {}
    if do_pralm:
        print("6. pr.alm: replaying the cookie OUTSIDE the browser ...")
        ok, detail = replay_odata(results["pralm"][1])
        print("   %s - %s" % ("OK" if ok else "FAILED", detail))
        verdict["pralm"] = (ok, results["pralm"][0], detail)

    if do_support:
        print("7. support: replaying the cookie OUTSIDE the browser ...")
        ok, detail = replay_support(results["support"][1])
        label = {True: "OK", False: "FAILED", None: "INCONCLUSIVE"}[ok]
        print("   %s - %s" % (label, detail))
        verdict["support"] = (ok, results["support"][0], detail)

    print("")
    print("== VERDICT")
    # The failures below need opposite responses, and the in-browser status is
    # the only thing that separates them. Without it a 401 is unattributable --
    # which is what made a working login read as a dead end on the first run.
    for half, (ok, status, _detail) in verdict.items():
        if ok:
            print("   %-8s PASS - the session replays outside the browser" % half)
        elif status == 200:
            print("   %-8s EXPORT PROBLEM - the browser opens it (200), the exported" % half)
            print("            cookie does not. Login is NOT the issue; look for a")
            print("            header (x-csrf-token, bearer) that the replay drops.")
        else:
            print("   %-8s CLOSED - the browser itself gets %s, so this is not a"
                  % (half, status))
            print("            cookie-export problem. Entitlement, or a token that")
            print("            the SAML login alone does not grant.")

    if len(verdict) == 2 and all(v[0] for v in verdict.values()):
        print("")
        print("   Both halves replay, so a full unattended refresh is technically")
        print("   possible. Weigh what it COSTS before building it: it needs a stored")
        print("   SAP password, and a personal S-user in a secret store means an")
        print("   automated job acting as a named human. Audit trails attribute it to")
        print("   them, and the job dies when they rotate the password or leave.")
        print("   Ask SAP for a technical user before wiring this to a cron.")
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\nPROBE INCONCLUSIVE: %s" % err)
