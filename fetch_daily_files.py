"""
fetch_daily_files.py
----------------------
Downloads today's (or any given date's) IN and BQ files from DIBBS
(BQ's zip also contains AS bundled inside it), using the real,
confirmed URL pattern found by investigate_sources.py:

    https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/{prefix}{YYMMDD}.{ext}

CA ("Current Awards") is NOT fetched by default - see fetch_all()'s
docstring for why. Pass --with-ca to include it once that's confirmed
as actually needed.

CONFIRMED (from real runs):
  - dibbs2.bsm.dla.mil enforces its OWN DoD warning page, separate from
    www.dibbs.bsm.dla.mil (cookies don't cross subdomains). We accept
    that warning ONCE per run using Playwright, then hand off its
    session cookies to a plain `requests.Session` for the actual file
    downloads - Playwright is just for solving the warning page/bot
    checks, not for the (potentially huge) file transfer itself.
  - The BQ zip contains BOTH bq<date>.txt AND as<date>.txt - AS isn't
    a separate download, it's bundled inside BQ's zip. Both are raw
    .txt, not the .xlsx shape your colleague sent manually.

Usage:
    python fetch_daily_files.py                  # fetches today's IN+BQ
    python fetch_daily_files.py 2026-09-16        # a specific date
    python fetch_daily_files.py --with-ca         # also fetch CA (slow, ~960MB, unconfirmed)
"""

import sys
import os
import zipfile
from datetime import date, datetime

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloaded")

CHUNK_SIZE = 1024 * 1024        # 1 MB per chunk
CONNECT_TIMEOUT = 15            # seconds to establish the connection
READ_TIMEOUT = 60               # seconds allowed between chunks arriving (not for the whole file)
PROGRESS_EVERY_BYTES = 20 * 1024 * 1024  # print a progress line every 20 MB


def _looks_like_real_file(first_chunk: bytes) -> bool:
    """Guard against silently saving an HTML warning/error page as the
    real file. A real .txt/.zip won't start with '<' (HTML)."""
    return not first_chunk[:100].lstrip().startswith(b"<")


def get_authenticated_session(playwright) -> requests.Session:
    """
    Launches a browser just long enough to accept dibbs2's own DoD
    warning page (separate from www.dibbs.bsm.dla.mil - cookies don't
    cross subdomains), then copies its cookies into a plain
    requests.Session for the actual file downloads. Playwright solves
    the "prove you're a browser" problem; requests handles the
    (possibly huge) file transfer with proper streaming.
    """
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    root = "https://dibbs2.bsm.dla.mil/"
    print(f"Establishing a session on {root} (accepting its warning page once)...")
    try:
        page.goto(root, wait_until="networkidle", timeout=20000)
        if "dodwarning" in page.url.lower() or "Notice and Consent" in page.content():
            print("  Found the warning page - accepting it...")
            page.click("input[type=submit], button[type=submit]", timeout=10000)
            page.wait_for_load_state("networkidle")
            print(f"  Session ready (landed on {page.url}).")
        else:
            print("  No warning page encountered - session ready.")
    except Exception as e:
        print(f"  Couldn't pre-load the subdomain root (may not matter): {e}")

    cookies = context.cookies()
    browser.close()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    return session


def stream_download(session: requests.Session, url: str, dest_path: str) -> bool:
    """
    Streams url to dest_path in chunks, with a per-chunk read timeout
    rather than one timeout for the whole file - so a slow-but-steady
    ~1GB download succeeds instead of failing on an arbitrary deadline.
    Returns True on success, False if it looks like a blocked/HTML
    response instead of the real file (in which case nothing is saved).
    """
    resp = session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    print(f"    status={resp.status_code}, "
          f"content-length={resp.headers.get('content-length', 'unknown')}")

    if resp.status_code != 200:
        return False

    downloaded = 0
    next_progress_at = PROGRESS_EVERY_BYTES
    first_chunk_checked = False

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            if not first_chunk_checked:
                if not _looks_like_real_file(chunk):
                    print("    Got HTML back, not the real file - aborting this download.")
                    resp.close()
                    return False
                first_chunk_checked = True
            f.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_progress_at:
                print(f"    ...{downloaded / (1024*1024):.0f} MB downloaded so far")
                next_progress_at += PROGRESS_EVERY_BYTES

    print(f"    Done - {downloaded / (1024*1024):.1f} MB total.")
    return True


def fetch_one(session: requests.Session, prefix: str, ext: str, target_date: date) -> str:
    """
    Downloads one file (e.g. prefix='in', ext='txt') for the given
    date. Returns the local file path (or the first extracted file, if
    it was a zip), or None if it couldn't be fetched.
    """
    yymmdd = target_date.strftime("%y%m%d")
    filename = f"{prefix}{yymmdd}.{ext}"
    url = f"{BASE_URL}/{filename}"

    print(f"\n  Fetching {filename} from {url} ...")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    dest_path = os.path.join(DOWNLOAD_DIR, filename)

    try:
        ok = stream_download(session, url, dest_path)
    except requests.RequestException as e:
        print(f"    FAILED: {e}")
        return None

    if not ok:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None

    print(f"    Saved to {dest_path}")

    if ext == "zip":
        try:
            with zipfile.ZipFile(dest_path) as zf:
                names = zf.namelist()
                zf.extractall(DOWNLOAD_DIR)
                print(f"    Unzipped: {names}")
                if names:
                    return os.path.join(DOWNLOAD_DIR, names[0])
        except zipfile.BadZipFile:
            print(f"    WARNING: {filename} didn't unzip cleanly - check it manually.")
            return dest_path

    return dest_path


def fetch_all(target_date: date, include_ca: bool = False):
    """
    By default, fetches only IN and BQ (which bundles AS inside its
    zip) - the three sources actively confirmed and needed right now.
    Returns a dict with keys 'in', 'bq', AND 'as' (the AS path is
    derived after BQ's zip is extracted, since AS is bundled inside
    it rather than being its own top-level download).

    CA ("Current Awards") is deliberately left out of the default run:
    it's listed as a separate item in the spec, not part of IN/AS/BQ,
    it's unconfirmed whether it's even the right file, and it's been
    a large (~960MB), slow, occasionally-timing-out download that
    isn't worth blocking the working part of the pipeline on. Pass
    include_ca=True (or run with --with-ca) once that's actually
    confirmed as needed.
    """
    print(f"Fetching files for {target_date} ...")
    prefixes = [("in", "txt"), ("bq", "zip")]
    if include_ca:
        prefixes.append(("ca", "zip"))

    results = {}
    with sync_playwright() as p:
        session = get_authenticated_session(p)
        for prefix, ext in prefixes:
            results[prefix] = fetch_one(session, prefix, ext, target_date)

    # AS is bundled inside BQ's zip (confirmed from a real run) rather
    # than being its own download - derive its expected path directly
    # instead of relying on zip listing order.
    if results.get("bq"):
        yymmdd = target_date.strftime("%y%m%d")
        expected_as_path = os.path.join(DOWNLOAD_DIR, f"as{yymmdd}.txt")
        results["as"] = expected_as_path if os.path.exists(expected_as_path) else None
    else:
        results["as"] = None

    print()
    print("Summary:")
    for prefix, path in results.items():
        print(f"  {prefix}: {'OK - ' + path if path else 'FAILED'}")
    if not include_ca:
        print("  ca: skipped (not confirmed as needed yet - pass --with-ca to include it)")
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    include_ca = "--with-ca" in args
    args = [a for a in args if a != "--with-ca"]

    if args:
        target = datetime.strptime(args[0], "%Y-%m-%d").date()
    else:
        target = date.today()
    fetch_all(target, include_ca=include_ca)
