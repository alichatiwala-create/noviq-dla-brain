"""
Bulk importer for DLA's official "Vendor" file (from
https://www.dla.mil/FOIA/Electronic-Reading-Room/ -> "Current DLA Procurement
Data Files" -> Vendor). This is a monthly government file linking every CAGE
code to a real company name and address - so instead of relying on reading
a company name out of one award PDF at a time, this fills in company names
for every vendor CAGE code you've ever seen an award from, in one go.

What it does:
1. Looks at every distinct CAGE code already saved in dla_award_history
   (i.e. every vendor who's actually won an award for one of your NSNs).
2. Streams through the Vendor file (it's huge - 1.2+ million vendors
   government-wide - so this only keeps rows for CAGE codes you actually
   care about).
3. For each matching CAGE code, saves the vendor's name and address into
   the vendors table:
     - if we don't have this CAGE code yet -> insert it
     - if we have it but the name/address is blank -> fill it in
     - if we already have a name for it -> leave it alone (never overwrites
       a name you might already have from somewhere else)

Field mapping (from the file's own header row):
    CAGE           -> cage_code
    VENDOR_NAME    -> company_name
    STREET_ADDRESS, CITY, STATE, ZIP -> combined into one address line

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python import_vendor.py "C:\path\to\vendor.zip"

This can take a few minutes - the file has 1.2+ million rows total, even
though only a small fraction will match CAGE codes you actually have.
"""

import argparse
import csv
import io
import sys
import zipfile

from database import get_connection


def get_relevant_cages(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT winning_cage FROM dla_award_history WHERE winning_cage IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


def open_data_file(zip_path):
    zf = zipfile.ZipFile(zip_path)
    names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
    if not names:
        print(f"Couldn't find a .txt file inside {zip_path} - is this the right zip?")
        sys.exit(1)
    inner = zf.open(names[0], "r")
    return io.TextIOWrapper(inner, encoding="utf-8", errors="replace")


def build_address(row):
    parts = [
        (row.get("STREET_ADDRESS") or "").strip(),
        (row.get("CITY") or "").strip(),
        (row.get("STATE") or "").strip(),
        (row.get("ZIP") or "").strip(),
    ]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def save_vendor(conn, cage, company_name, address):
    cur = conn.cursor()
    cur.execute("SELECT company_name, address FROM vendors WHERE cage_code = %s", (cage,))
    existing = cur.fetchone()

    if existing is None:
        cur.execute(
            "INSERT INTO vendors (cage_code, company_name, address) VALUES (%s, %s, %s)",
            (cage, company_name, address),
        )
        conn.commit()
        return "new"

    existing_name, existing_address = existing
    if not existing_name and not existing_address:
        cur.execute(
            "UPDATE vendors SET company_name = %s, address = %s WHERE cage_code = %s",
            (company_name, address, cage),
        )
        conn.commit()
        return "filled_in"

    return "already_had_name"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", help="Path to the Vendor zip file you downloaded")
    args = parser.parse_args()

    conn = get_connection()
    relevant_cages = get_relevant_cages(conn)
    print(f"Found {len(relevant_cages)} distinct CAGE code(s) already in your award history.\n")

    reader_file = open_data_file(args.zip_path)
    reader = csv.DictReader(reader_file, delimiter="|")

    total_rows = 0
    total_matched = 0
    total_new = 0
    total_filled_in = 0
    total_already_had_name = 0

    for row in reader:
        total_rows += 1
        if total_rows % 200000 == 0:
            print(f"  ...scanned {total_rows} rows so far ({total_matched} matched)")

        cage = (row.get("CAGE") or "").strip()
        if cage not in relevant_cages:
            continue

        total_matched += 1
        company_name = (row.get("VENDOR_NAME") or "").strip() or None
        address = build_address(row)

        try:
            result = save_vendor(conn, cage, company_name, address)
            if result == "new":
                total_new += 1
            elif result == "filled_in":
                total_filled_in += 1
            else:
                total_already_had_name += 1
        except Exception as e:
            print(f"  (error saving vendor {cage}: {e})")

    print("\n---- Summary ----")
    print(f"Total rows in file:            {total_rows}")
    print(f"Rows matching your CAGE codes: {total_matched}")
    print(f"New vendors saved:             {total_new}")
    print(f"Existing vendors filled in:    {total_filled_in}")
    print(f"Already had a name (left alone): {total_already_had_name}")
    print("\nDone. Refresh your dashboard - vendor/company names should now show up")
    print("wherever a CAGE code is displayed.")


if __name__ == "__main__":
    main()
