"""
explore_flis.py
----------------
ONE-TIME exploration script - not part of the automated pipeline.

Opens DLA's "FLIS Data Electronic Reading Room" page (where Management
and Reference files live) and saves its HTML into debug_html/, so Claude
can find the real download links and build an automatic downloader for
them - same idea as explore_rfq.py did for DIBBS. Doesn't touch your
database. Safe to run.

This version opens a REAL VISIBLE browser window (not hidden/headless) -
dla.mil's security service (Akamai) blocks the hidden kind, so a window
will pop up on your screen for a few seconds. Just let it run - don't
close it yourself.

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python explore_flis.py

After it finishes, just tell Claude it's done.
"""

from playwright.sync_api import sync_playwright

URLS = [
    "https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/",
    "https://www.dla.mil/FOIA/Electronic-Reading-Room/",
]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        print("  Hit a warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  (couldn't auto-click warning page: {e})")


def dump_links(page, name):
    html = page.content()
    path = f"debug_html/{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved {path} ({len(html)} chars)")

    if "access denied" in html.lower():
        print("  STILL BLOCKED (Access Denied) - see the saved file.")
        return

    links = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.innerText.trim(), e.href])")
    print(f"  Found {len(links)} link(s) on this page:")
    for text, href in links:
        if any(k in (text + href).lower() for k in ["zip", "management", "reference", "layout", "pdf", "docx", "download"]):
            print(f"    - {text!r} -> {href}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        for i, url in enumerate(URLS, 1):
            print(f"\nOpening {url} ...")
            try:
                page.goto(url, timeout=60000)
                page.wait_for_load_state("networkidle", timeout=20000)
                accept_warning_if_present(page)
                page.wait_for_timeout(2500)
                dump_links(page, f"flis_page_{i}")
            except Exception as e:
                print(f"  Couldn't load this page: {e}")

        browser.close()
        print("\nDone. Check debug_html/ and the lines above starting with '- '.")


if __name__ == "__main__":
    main()
