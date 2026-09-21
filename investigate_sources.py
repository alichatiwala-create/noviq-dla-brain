"""
investigate_sources.py
------------------------
One-off investigation tool - NOT part of the automated pipeline.

Visits each real page your colleague confirmed as the source for our
files, using a real browser (same approach that got us past DIBBS's
DoD warning page and bot-protection for the RFQ search). Saves the
HTML and a screenshot of each to debug_html/, so we can see the real
download-link structure instead of guessing.

Usage:
    python investigate_sources.py
"""

import os
from playwright.sync_api import sync_playwright

DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PAGES_TO_CHECK = [
    ("dibbs_rfqdates_recent", "https://www.dibbs.bsm.dla.mil/RFQ/RFQDates.aspx?category=recent"),
    ("dla_flis_reading_room", "https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/"),
    ("dla_foia_reading_room", "https://www.dla.mil/FOIA/Electronic-Reading-Room/"),
    # Best guess for "DIBBS Current Awards" based on the site's menu
    # pattern (RFQDates.aspx?category=recent above) - UNCONFIRMED, ask
    # your colleague to verify this is the right page before relying on it.
    ("dibbs_awards_by_post_date_GUESS", "https://www.dibbs.bsm.dla.mil/Awards/AwdDates.aspx?category=post"),
]


def save(page, name):
    html_path = os.path.join(DEBUG_DIR, f"{name}.html")
    png_path = os.path.join(DEBUG_DIR, f"{name}.png")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page.content())
    page.screenshot(path=png_path, full_page=True)
    print(f"  Saved {html_path}")
    print(f"  Saved {png_path}")


def accept_dod_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "Notice and Consent" in page.content():
        print("  Hit the DoD warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=10000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"  Couldn't auto-click through: {e}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for name, url in PAGES_TO_CHECK:
            print(f"\nVisiting {url} ...")
            page = browser.new_page(user_agent=USER_AGENT)
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                accept_dod_warning_if_present(page)
                # Give any lazy-loaded content (tables, widgets) a moment
                page.wait_for_timeout(2000)
                save(page, name)

                # Also print every link on the page that looks like a
                # downloadable file - the fastest way to spot the real
                # download URLs without reading the whole HTML dump.
                links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href).filter(h => "
                    "/\\.(zip|txt|csv|xlsx|xls|pdf)(\\?|$)/i.test(h))"
                )
                if links:
                    print(f"  Found {len(links)} file-like link(s):")
                    for l in links[:30]:
                        print(f"    {l}")
                else:
                    print("  No obvious file links found on this page directly - "
                          "check the screenshot, it may be behind a click/expand.")
            except Exception as e:
                print(f"  FAILED to load: {e}")
            finally:
                page.close()

        browser.close()

    print("\nDone. Send me the printed file links above, or the screenshots"
          " in debug_html/, and I'll build the real fetchers from that.")


if __name__ == "__main__":
    main()
