"""
import_reference.py
--------------------
Bulk importer for DLA's official "Reference" file (from
https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/
-> Reference.zip). This is DLA's government-wide cross-reference of every
NIIN to every part number and CAGE code (manufacturer) known to be able to
make that item - this is the "MCRL-Qualified Sources" list on the dashboard
(mcrl_count / mcrl_sources), filled in automatically instead of waiting for
AS files to slowly accumulate the same information one solicitation at a
time.

This file (V_FLIS_PART.CSV) is keyed by NIIN only, not the full 13-digit
NSN sources_canonical needs - so this looks up each NIIN's NSN from your
own solicitation_lines (you already know the NSN for every NIIN you care
about, since IN files always carry the full NSN) rather than needing DLA's
NIIN->FSC mapping at all.

What it does:
1. Reads every distinct (NIIN -> NSN) pair currently in your dashboard
   (solicitation_lines). A NIIN normally maps to exactly one NSN, but this
   handles the rare case of more than one just as safely.
2. Streams through V_FLIS_PART.CSV inside the zip (it's huge - many
   millions of rows, government-wide - so this only keeps rows for NIINs
   you actually have).
3. Merges each matching row into sources_canonical using the same
   NSN + CAGE + normalized Part Number dedup rule import_as.py uses:
     - if a canonical row already exists (e.g. from AS) -> just flip
       in_reference = TRUE, never creates a duplicate visible row
     - if not -> creates a new canonical row with in_reference = TRUE
       (in_as / approved stay FALSE until an AS file confirms it)

Field mapping (from the file's own header row):
    NIIN          -> looked up against solicitation_lines to get the NSN
    PART_NUMBER   -> part_number (normalized: trimmed + uppercased)
    CAGE_CODE     -> cage_code

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python import_reference.py "C:\path\to\Reference.zip"

This can take a while - the file has many millions of rows government-wide,
even though only a small fraction will match your NIINs. It prints progress
as it goes so you can see it's still working.
"""

import csv
import io
import sys
import zipfile

import db


def normalize_part_number(raw: str) -> str:
    """Trim and uppercase so 'abc-100' and 'ABC-100' match as the same part."""
    if raw is None:
        return ""
    return str(raw).strip().upper()


def get_niin_to_nsns(conn):
    """
    Returns {niin: {nsn, nsn, ...}} - almost always a single NSN per NIIN,
    but kept as a set in case the same NIIN was ever posted under more
    than one FSC.
    """
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT niin, nsn FROM solicitation_lines WHERE niin IS NOT NULL AND nsn IS NOT NULL")
    mapping = {}
    for niin, nsn in cur.fetchall():
        mapping.setdefault(niin, set()).add(nsn)
    return mapping


def open_data_file(zip_path):
    """
    Opens the V_FLIS_PART.CSV file inside the given zip and returns a
    line-by-line text reader for it.
    """
    zf = zipfile.ZipFile(zip_path)
    exact = [n for n in zf.namelist() if n.upper() == "V_FLIS_PART.CSV"]
    if not exact:
        exact = [n for n in zf.namelist() if n.upper().endswith(".CSV")]
    if not exact:
        print(f"Couldn't find V_FLIS_PART.CSV inside {zip_path} - is this the right zip?")
        print(f"Files found: {zf.namelist()}")
        sys.exit(1)
    inner = zf.open(exact[0], "r")
    return io.TextIOWrapper(inner, encoding="utf-8", errors="replace"), exact[0]


def merge_row(conn, nsn, cage_code, part_number):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sources_canonical (nsn, cage_code, part_number, in_reference)
        VALUES (%s, %s, %s, TRUE)
        ON CONFLICT (nsn, cage_code, part_number) DO UPDATE
        SET in_reference = TRUE,
            updated_at = now()
        RETURNING (xmax = 0) AS was_insert
        """,
        (nsn, cage_code, part_number),
    )
    was_insert = cur.fetchone()[0]
    return "new" if was_insert else "matched_existing"


def run(zip_path):
    conn = db.get_connection()
    niin_to_nsns = get_niin_to_nsns(conn)
    print(f"Loaded {len(niin_to_nsns)} distinct NIIN(s) from your dashboard.\n")

    reader_file, member_name = open_data_file(zip_path)
    print(f"Reading {member_name} ...")
    reader = csv.DictReader(reader_file)

    total_rows = 0
    total_matched = 0
    total_new = 0
    total_matched_existing = 0
    total_skipped = 0

    for row in reader:
        total_rows += 1
        if total_rows % 500000 == 0:
            conn.commit()
            print(f"  ...scanned {total_rows} rows so far ({total_matched} matched your NIINs)")

        niin = (row.get("NIIN") or "").strip()
        nsns = niin_to_nsns.get(niin)
        if not nsns:
            continue

        cage_code = (row.get("CAGE_CODE") or "").strip()
        part_number = normalize_part_number(row.get("PART_NUMBER"))
        if not cage_code or not part_number:
            total_skipped += 1
            continue

        total_matched += 1
        for nsn in nsns:
            try:
                result = merge_row(conn, nsn, cage_code, part_number)
                if result == "new":
                    total_new += 1
                else:
                    total_matched_existing += 1
            except Exception as e:
                print(f"  (error saving a row for NIIN {niin} / CAGE {cage_code}: {e})")

        if total_matched % 500 == 0:
            conn.commit()

    conn.commit()
    conn.close()

    print("\n---- Summary ----")
    print(f"Total rows in file:              {total_rows}")
    print(f"Rows matching your NIINs:        {total_matched}")
    print(f"New canonical source rows:       {total_new}")
    print(f"Matched existing rows (flagged in_reference): {total_matched_existing}")
    print(f"Skipped (missing CAGE/part number): {total_skipped}")
    print("\nDone. Refresh your dashboard - MCRL-Qualified Sources counts should now show")
    print("up for a lot more NSNs, without waiting for AS files to build the list slowly.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python import_reference.py path/to/Reference.zip")
        sys.exit(1)
    db.run_with_retries(run, sys.argv[1])
