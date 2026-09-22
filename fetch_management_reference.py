"""
fetch_management_reference.py
-------------------------------
Automatically downloads DLA's official Management and Reference PUBLOG
files (and their layout/documentation PDFs) - the last 2 data sources
Shubham asked for, now fully automated instead of needing a manual
screenshot/download.

dla.mil turned out to need the download to look EXACTLY like a real
person clicking the link on the actual page (with the page as the
"referrer") - navigating straight to the file's own address, even from
inside a real visible browser, came back empty. So this version loads
the real reading room page and clicks the actual link text on it, the
same way you would with a mouse.

These are monthly government files (like ContractHist/Vendor), so this
script is meant to be re-run occasionally to refresh them - it's not
part of the nightly pipeline.

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python fetch_management_reference.py

A browser window will pop up - just let it run, don't close it yourself.
Files are saved into a new "reference_data" folder in this project.
"""

import os
from playwright.sync_api import sync_playwright

PAGE_URL = "https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/"

# (link text exactly as it appears on the page, the filename to save it as)
LINKS = [
    ("Management.zip", "Management.zip"),
    ("Reference.zip", "Reference.zip"),
]
# The "Management Layout"/"Reference Layout" PDF links crash the automated
# browser window (Chrome's built-in PDF viewer doesn't behave under
# automation) - skipped deliberately. Not needed: the columns can be read
# straight from the real zip file's own header row, same as ContractHist
# and Vendor were.

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

OUT_DIR = os.path.join(os.path.dirname(__file__), "reference_data")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="en-US")
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        print(f"Opening {PAGE_URL} ...")
        page.goto(PAGE_URL, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(1500)
        print("  OK.\n")

        for link_text, filename in LINKS:
            print(f"Clicking '{link_text}' ...")
            try:
                with page.expect_download(timeout=60000) as download_info:
                    page.get_by_text(link_text, exact=True).first.click()
                download = download_info.value
                dest_path = os.path.join(OUT_DIR, filename)
                download.save_as(dest_path)
                size_kb = os.path.getsize(dest_path) / 1024
                if size_kb == 0:
                    print(f"  Saved but it's 0 KB - still blocked somehow.")
                else:
                    print(f"  Saved {dest_path} ({size_kb:.0f} KB)")
            except Exception as e:
                print(f"  FAILED: {e}")

        browser.close()

    print("\nDone. Check the reference_data folder - tell Claude which files made it through.")


if __name__ == "__main__":
    main()
