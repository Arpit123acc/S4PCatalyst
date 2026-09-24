#!/usr/bin/env python3
"""PROBE: can a headless browser complete the SAP login and mint a session?

This answers one question and builds nothing. If it fails we stop; if it passes,
unattended refresh becomes worth building properly.

WHY A BROWSER AT ALL
    me.sap.com returns 200 with no form and no server-side redirect -- the SAML
    flow runs entirely in JavaScript. urllib and requests cannot complete it
    whatever credentials they hold, so a real browser engine is the only route.
    Storing the password in Secrets Manager does not change that; the thing that
    expires is a SESSION, and only a login can mint a new one.

THE RISK THIS IS TESTING
    Bot detection. blogs.sap.com already returns 403 to a scripted request that
    sends full browser headers, so accounts.sap.com may well refuse a headless
    browser too. That is unknowable without trying, and it is the single thing
    that decides whether unattended refresh is possible at all.

WHAT IT DOES NOT DO
    No credential storage, no Secrets Manager, no IAM change, nothing written to
    disk but a failure screenshot. The password is prompted for and held in
    memory. The minted cookie is never printed -- only its length and whether it
    works.

Install first (on the host that runs the fetch):
    pip3.11 install playwright && python3.11 -m playwright install chromium

Usage:
    python3.11 scripts/probe_sap_login.py                 # prompts for both
    python3.11 scripts/probe_sap_login.py --user S0001234 # prompts for password
"""

import sys
import json
import getpass
import argparse
import urllib.request
from pathlib import Path

ENTRY  = "https://me.sap.com/processnavigator/SolS/EARL_SolS-013/2608?region=DE"
VERIFY = ("https://pr.alm.me.sap.com/ui/earl-pn-ui/v1/odata/v4/EAXService/"
          "LatestSolutionScenarioIds?%24top=1")
SHOT   = Path("/tmp/sap-login-probe.png")

# SAP has used several login UIs (Customer Data Cloud, IAS, the classic form).
# Rather than guess one, try the fields each of them uses and report which
# matched -- a probe that says "none of these" is more useful than a timeout.
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


def verify(cookie):
    """Does the minted session actually open the OData service?"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="S-user / P-user; prompted if omitted")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (needs a display; not on EC2)")
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ("playwright not installed. On the fetch host:\n"
                "  pip3.11 install playwright && python3.11 -m playwright install chromium")

    user = a.user or input("SAP user: ").strip()
    pwd  = getpass.getpass("SAP password (not stored, not echoed): ")
    if not user or not pwd:
        return "need both a user and a password"

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

        print("1. opening Process Navigator …")
        page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)                 # let the SPA redirect
        print("   landed on: %s" % page.url[:95])

        print("2. looking for the login form …")
        usel, uel = first_visible(page, USER_SEL, timeout=12000)
        if not uel:
            SHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOT))
            title = page.title()
            browser.close()
            return ("no login field found.\n"
                    "   url    : %s\n   title  : %s\n   shot   : %s\n"
                    "   If that page is a bot challenge, this route is closed.\n"
                    "   If it is a login UI we do not have a selector for, add it "
                    "to USER_SEL." % (page.url[:95], title, SHOT))
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
            browser.close()
            return "found a user field but no password field — login UI unrecognised"
        print("   pass field: %s" % psel)
        pel.fill(pwd)

        print("3. submitting …")
        ssel, sel_el = first_visible(page, NEXT_SEL, timeout=6000)
        if sel_el:
            sel_el.click()
        else:
            pel.press("Enter")
        page.wait_for_timeout(9000)                 # SAML round trip
        print("   now on: %s" % page.url[:95])

        print("4. collecting cookies …")
        jar = ctx.cookies()
        names = sorted({c["name"] for c in jar})
        sid = [c for c in jar if c["name"] == "connect.sid"]
        if not sid:
            SHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOT))
            browser.close()
            return ("logged in but no connect.sid.\n   cookies: %s\n   shot: %s\n"
                    "   connect.sid is set by pr.alm.me.sap.com, so the SPA may need "
                    "to make its first OData call before it exists." % (names[:14], SHOT))
        cookie = "; ".join("%s=%s" % (c["name"], c["value"]) for c in jar
                           if c["domain"].endswith("sap.com"))
        browser.close()

    print("   cookies: %s" % names[:14])
    print("   header : %d chars (not printed)" % len(cookie))

    print("5. verifying against the OData service …")
    ok, detail = verify(cookie)
    print("   %s — %s" % ("OK" if ok else "FAILED", detail))
    print("\n%s" % ("PASS — a headless login can mint a working session. Unattended "
                    "refresh is feasible; next step is Secrets Manager plus a narrow "
                    "IAM grant."
                    if ok else
                    "FAIL — a session was minted but it does not open the service. "
                    "Check whether the account is entitled, or whether another cookie "
                    "is needed."))
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\nPROBE INCONCLUSIVE: %s" % err)
