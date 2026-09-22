"""
import_management.py
---------------------
Bulk importer for DLA's official "Management" file (from
https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/
-> Management.zip). This is DLA's own government-wide catalog file that has
a standard unit price (DRN 7075) for every NIIN it manages - this is the
"FLIS Management Price" fallback used whenever an NSN has no recent award
price of its own yet (see estimated_value_basis in solicitation_lines).

What it does:
1. Reads every distinct NIIN currently in your dashboard (solicitation_lines).
2. Streams through V_FLIS_MANAGEMENT.CSV inside the zip (it's huge -
   millions of rows, government-wide - so this only keeps rows for NIINs
   you actually have).
3. A NIIN can appear more than once in this file (different effective
   dates, different management activities). For each NIIN this keeps the
   best single row: an active one (ROW_OBS_DT blank, i.e. not obsolete)
   with the latest EFFECTIVE_DATE; only falls back to an obsolete row if
   no active one exists.
4. Saves one row per NIIN into management_price:
     - if we don't have this NIIN yet -> insert it
     - if we do -> update the price (this file is the source of truth for
       this specific number, so it's fine to overwrite on every re-run)

Field mapping (from the file's own header row):
    NIIN         -> niin
    UNIT_PRICE   -> unit_price (DRN 7075 - the FLIS Management Price)
    EFFECTIVE_DATE, ROW_OBS_DT -> used only to pick the best row per NIIN,
                                   not stored

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python import_management.py "C:\path\to\Management.zip"

This can take a while - the file has millions of rows government-wide, even
though only a small fraction will match your NIINs. It prints progress as
it goes so you can see it's still working.
"""

import csv
import io
import sys
import zipfile
from datetime import datetime

import db


def get_relevant_niins(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT niin FROM solicitation_lines WHERE niin IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


def open_data_file(zip_path):
    """
    Opens the V_FLIS_MANAGEMENT.CSV file inside the given zip (there are
    other .CSV files bundled alongside it - phrase text, future changes,
    per-branch extracts - we only want the main one) and returns a
    line-by-line text reader for it.
    """
    zf = zipfile.ZipFile(zip_path)
    exact = [n for n in zf.namelist() if n.upper() == "V_FLIS_MANAGEMENT.CSV"]
    if not exact:
        exact = [
            n for n in zf.namelist()
            if n.upper().endswith(".CSV") and "MANAGEMENT" in n.upper() and "FUTURE" not in n.upper()
        ]
    if not exact:
        print(f"Couldn't find V_FLIS_MANAGEMENT.CSV inside {zip_path} - is this the right zip?")
        print(f"Files found: {zf.namelist()}")
        sys.exit(1)
    inner = zf.open(exact[0], "r")
    return io.TextIOWrapper(inner, encoding="utf-8", errors="replace"), exact[0]


def parse_price(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_effective_date(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d-%b-%Y").date()
    except ValueError:
        return None


def is_better(candidate, current):
    """
    candidate/current are dicts: {"unit_price", "effective_date", "active"}
    (active = True when ROW_OBS_DT was blank, i.e. not obsolete).
    Prefer an active row over an obsolete one; among rows of the same
    "active-ness", prefer the later effective date.
    """
    if current is None:
        return True
    if candidate["active"] and not current["active"]:
        return True
    if not candidate["active"] and current["active"]:
        return False
    cand_date = candidate["effective_date"]
    curr_date = current["effective_date"]
    if cand_date is None:
        return False
    if curr_date is None:
        return True
    return cand_date > curr_date


def save_management_prices(conn, best_by_niin, source_file):
    cur = conn.cursor()
    counts = {"new": 0, "updated": 0, "no_price": 0}
    total = len(best_by_niin)
    i = 0
    for niin, best in best_by_niin.items():
        i += 1
        if best["unit_price"] is None:
            counts["no_price"] += 1
            continue
        cur.execute(
            """
            INSERT INTO management_price (niin, unit_price, source_file, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (niin) DO UPDATE
            SET unit_price = EXCLUDED.unit_price,
                source_file = EXCLUDED.source_file,
                updated_at = now()
            RETURNING (xmax = 0) AS was_insert
            """,
            (niin, best["unit_price"], source_file),
        )
        was_insert = cur.fetchone()[0]
        counts["new" if was_insert else "updated"] += 1
        if i % 500 == 0 or i == total:
            conn.commit()
            print(f"  ...saved {i}/{total} NIIN price(s)")
    cur.close()
    return counts


def run(zip_path):
    conn = db.get_connection()
    relevant_niins = get_relevant_niins(conn)
    print(f"Loaded {len(relevant_niins)} distinct NIIN(s) from your dashboard.\n")

    reader_file, member_name = open_data_file(zip_path)
    print(f"Reading {member_name} ...")
    reader = csv.DictReader(reader_file)

    total_rows = 0
    total_matched = 0
    best_by_niin = {}

    for row in reader:
        total_rows += 1
        if total_rows % 500000 == 0:
            print(f"  ...scanned {total_rows} rows so far ({total_matched} matched your NIINs)")

        niin = (row.get("NIIN") or "").strip()
        if niin not in relevant_niins:
            continue

        total_matched += 1
        candidate = {
            "unit_price": parse_price(row.get("UNIT_PRICE")),
            "effective_date": parse_effective_date(row.get("EFFECTIVE_DATE")),
            "active": not (row.get("ROW_OBS_DT") or "").strip(),
        }
        current = best_by_niin.get(niin)
        if is_better(candidate, current):
            best_by_niin[niin] = candidate

    print(f"\nFinished scanning {total_rows} total rows. {total_matched} matched your NIINs "
          f"({len(best_by_niin)} distinct NIIN(s)).")

    filename = zip_path.replace("\\", "/").split("/")[-1]
    print("Saving prices into management_price...")
    counts = save_management_prices(conn, best_by_niin, filename)
    conn.close()

    print("\n---- Summary ----")
    print(f"Total rows in file:              {total_rows}")
    print(f"Rows matching your NIINs:        {total_matched}")
    print(f"New NIIN prices saved:           {counts['new']}")
    print(f"Existing NIIN prices updated:    {counts['updated']}")
    print(f"Matched but no usable price:     {counts['no_price']}")
    print("\nDone. Refresh your dashboard - Estimated Value should now show the FLIS")
    print("Management Price for a lot more NSNs that don't have a recent award yet.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python import_management.py path/to/Management.zip")
        sys.exit(1)
    db.run_with_retries(run, sys.argv[1])
