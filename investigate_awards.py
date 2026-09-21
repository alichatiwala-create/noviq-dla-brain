"""
One-off investigation script for the DIBBS "Current Awards" pages.

Run this from your noviq-dla-brain project folder (it needs the same
playwright package already installed there). It does NOT touch your
database - it just visits the two Awards pages Shubham sent, gets past
the DoD warning page the same way fetch_daily_files.py does, and prints
every file-like link it finds (plus saves the HTML/screenshot so we can
look closer if the links aren't obvious).

Usage:
    python investigate_awards.py
"""

import re
from pathlib import Path
from playwright.sync_api import sync_playwright

PAGES_TO_CHECK = [
    ("dibbs_awards_by_award_date", "https://www.dibbs.bsm.dla.mil/Awards/AwdDates.aspx?category=awddt"),
    ("dibbs_awards_by_post_date", "https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=post&TypeSrch=cq&Value=09-18-2026"),
]

FILE_LINK_PATTERN = re.compile(r"\.(zip|txt|csv|xlsx|xls|pdf)(\?|$)", re.IGNORECASE)

OUT_DIR = Path("debug_html")
OUT_DIR.mkdir(exist_ok=True)


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        print("  Hit the DoD warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  (couldn't auto-click warning page: {e})")


def investigate(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    for name, url in PAGES_TO_CHECK:
        print(f"\nVisiting {url} ...")
        try:
            page.goto(url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  Failed to load: {e}")
            continue

        accept_warning_if_present(page)

        html = page.content()
        html_path = OUT_DIR / f"{name}.html"
        png_path = OUT_DIR / f"{name}.png"
        html_path.write_text(html, encoding="utf-8")
        page.screenshot(path=str(png_path), full_page=True)
        print(f"  Saved {html_path}")
        print(f"  Saved {png_path}")

        # Find every href/src that looks like a downloadable file
        links = set()
        for match in re.finditer(r'(?:href|src)="([^"]+)"', html):
            candidate = match.group(1)
            if FILE_LINK_PATTERN.search(candidate):
                links.add(candidate)

        if links:
            print(f"  Found {len(links)} file-like link(s):")
            for link in sorted(links):
                print(f"    {link}")
        else:
            print("  No obvious file links found on this page directly - check the screenshot, it may be behind a click/expand or a table row you select first.")

    context.close()
    browser.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        investigate(p)
    print("\nDone. Send me the printed links above, or the screenshots/HTML in debug_html/, and I'll build the real Current Awards fetcher from that.")
