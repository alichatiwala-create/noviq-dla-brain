"""
import_in.py
------------
Imports a daily IN*.TXT file (fixed-width, 140 chars/line - confirmed
against a real DIBBS file) into staging_in, then normalizes it into
solicitations + solicitation_lines.

Usage:
    python import_in.py path/to/IN260911.TXT
"""

import sys
import os

import db
from normalize import split_nsn, classify_date_status, parse_mmddyy, parse_post_date_from_filename

# Confirmed layout (matches the email spec exactly, verified against a
# real 430-row file: every line is exactly 140 characters).
LAYOUT = [
    ("solicitation", 13),
    ("nsn_part_raw", 46),
    ("purchase_request", 13),
    ("return_by_date", 8),
    ("file_name", 19),
    ("qty", 7),
    ("unit_issue", 2),
    ("nomenclature", 21),
    ("buyer_code", 5),
    ("amsc", 1),
    ("item_type_indicator", 1),
    ("sb_setaside_indicator", 1),
    ("sb_setaside_pct", 3),
]
EXPECTED_LINE_LENGTH = sum(length for _, length in LAYOUT)  # 140


def parse_line(line: str) -> dict:
    """Slice one fixed-width line into a field dict, per LAYOUT."""
    pos = 0
    row = {}
    for name, length in LAYOUT:
        row[name] = line[pos:pos + length].strip()
        pos += length
    return row


def parse_file(path: str):
    """Read the whole file and return a list of parsed row dicts.
    Skips any line that doesn't match the expected width, printing a
    warning rather than silently dropping data."""
    rows = []
    with open(path, "r", encoding="latin-1") as f:
        for line_num, raw_line in enumerate(f.read().splitlines(), start=1):
            if not raw_line.strip():
                continue
            if len(raw_line) != EXPECTED_LINE_LENGTH:
                print(f"  WARNING: line {line_num} is {len(raw_line)} chars "
                      f"(expected {EXPECTED_LINE_LENGTH}) - skipping: {raw_line!r}")
                continue
            rows.append(parse_line(raw_line))
    return rows


def load_staging(conn, rows, source_file, post_date):
    """Insert every parsed row into staging_in, untouched."""
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT INTO staging_in (
                solicitation, nsn_part_raw, purchase_request, return_by_date,
                file_name, qty, unit_issue, nomenclature, buyer_code, amsc,
                item_type_indicator, sb_setaside_indicator, sb_setaside_pct,
                source_file, post_date
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["solicitation"], row["nsn_part_raw"], row["purchase_request"],
                row["return_by_date"], row["file_name"], row["qty"], row["unit_issue"],
                row["nomenclature"], row["buyer_code"], row["amsc"],
                row["item_type_indicator"], row["sb_setaside_indicator"], row["sb_setaside_pct"],
                source_file, post_date,
            ),
        )
    conn.commit()
    cur.close()


def upsert_solicitation(cur, solicitation_number, issue_date, post_date):
    """
    Section 18 of the spec: if we've never seen this solicitation
    number, create it as NEW. If we have, update last_seen_post_date /
    times_posted and mark it PREVIOUSLY_SEEN (refined to UPDATED /
    RETURN_DATE_CHANGED at the line level - see upsert_solicitation_line).
    """
    cur.execute(
        "SELECT solicitation_number, times_posted, first_seen_post_date FROM solicitations WHERE solicitation_number = %s",
        (solicitation_number,),
    )
    existing = cur.fetchone()

    date_status = classify_date_status(issue_date, post_date)

    if existing is None:
        cur.execute(
            """
            INSERT INTO solicitations (
                solicitation_number, issue_date, first_seen_post_date,
                last_seen_post_date, times_posted, date_status, status
            ) VALUES (%s, %s, %s, %s, 1, %s, 'NEW')
            """,
            (solicitation_number, issue_date, post_date, post_date, date_status),
        )
        return "NEW"
    else:
        cur.execute(
            """
            UPDATE solicitations
            SET last_seen_post_date = %s,
                times_posted = times_posted + 1,
                date_status = %s,
                status = 'PREVIOUSLY_SEEN',
                updated_at = now()
            WHERE solicitation_number = %s
            """,
            (post_date, date_status, solicitation_number),
        )
        return "PREVIOUSLY_SEEN"


def upsert_solicitation_line(cur, row, post_date):
    """
    One line = one (solicitation_number, purchase_request_number) pair.
    NSN is normalized here (split into nsn/fsc/niin) per the spec's
    NSN normalization rule. Detects RETURN_DATE_CHANGED by comparing
    against what was previously stored for this exact line.
    """
    solicitation_number = row["solicitation"]
    purchase_request_number = row["purchase_request"]
    return_by_date = parse_mmddyy(row["return_by_date"])
    qty = int(row["qty"]) if row["qty"].isdigit() else None
    set_aside_pct = int(row["sb_setaside_pct"]) if row["sb_setaside_pct"].isdigit() else None

    # item_type_indicator: 1 = NSN, 2 = Part Number (per the spec's codes)
    nsn = fsc = niin = None
    part_number_raw = None
    if row["item_type_indicator"] == "1":
        nsn, fsc, niin = split_nsn(row["nsn_part_raw"])
    else:
        part_number_raw = row["nsn_part_raw"]

    cur.execute(
        "SELECT id, return_by_date FROM solicitation_lines WHERE solicitation_number = %s AND purchase_request_number = %s",
        (solicitation_number, purchase_request_number),
    )
    existing = cur.fetchone()

    if existing is None:
        cur.execute(
            """
            INSERT INTO solicitation_lines (
                solicitation_number, purchase_request_number, nsn, fsc, niin,
                item_type_indicator, part_number_raw, qty, unit_issue,
                nomenclature, buyer_code, amsc, set_aside_indicator,
                set_aside_pct, return_by_date, file_name
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                solicitation_number, purchase_request_number, nsn, fsc, niin,
                row["item_type_indicator"], part_number_raw, qty, row["unit_issue"],
                row["nomenclature"], row["buyer_code"], row["amsc"],
                row["sb_setaside_indicator"], set_aside_pct, return_by_date, row["file_name"],
            ),
        )
        return "NEW"
    else:
        line_id, prev_return_by = existing
        return_date_changed = prev_return_by is not None and return_by_date != prev_return_by
        cur.execute(
            """
            UPDATE solicitation_lines
            SET nsn = %s, fsc = %s, niin = %s, qty = %s, unit_issue = %s,
                nomenclature = %s, buyer_code = %s, amsc = %s,
                set_aside_indicator = %s, set_aside_pct = %s,
                return_by_date_prev = return_by_date,
                return_by_date = %s,
                file_name = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                nsn, fsc, niin, qty, row["unit_issue"], row["nomenclature"],
                row["buyer_code"], row["amsc"], row["sb_setaside_indicator"],
                set_aside_pct, return_by_date, row["file_name"], line_id,
            ),
        )
        return "RETURN_DATE_CHANGED" if return_date_changed else "UPDATED"


def normalize_into_core_tables(conn, rows, post_date):
    """
    For each staged row: upsert the parent solicitation, then the line.
    IMPORTANT: the IN file layout doesn't carry a separate Issue Date
    column (only Return By Date) - per the spec's own example, Issue
    Date is a distinct field DIBBS tracks. Until we have a source that
    supplies Issue Date directly, we use post_date as a stand-in so
    every solicitation classifies as CURRENT on first sight; this
    should be revisited once we import a source that carries the real
    Issue Date (the spec's examples suggest IN itself may include it in
    a fuller feed - flag this to confirm against the next real file).

    Commits every 50 rows (not just once at the end) and prints progress,
    so a long run over a network connection to a cloud database doesn't
    look "frozen", and an interruption doesn't lose everything already
    processed.
    """
    cur = conn.cursor()
    counts = {"solicitations_new": 0, "solicitations_seen": 0, "lines_new": 0, "lines_updated": 0, "lines_return_changed": 0}

    total = len(rows)
    for i, row in enumerate(rows, start=1):
        issue_date = post_date  # see docstring caveat above
        sol_result = upsert_solicitation(cur, row["solicitation"], issue_date, post_date)
        counts["solicitations_new" if sol_result == "NEW" else "solicitations_seen"] += 1

        line_result = upsert_solicitation_line(cur, row, post_date)
        if line_result == "NEW":
            counts["lines_new"] += 1
        elif line_result == "RETURN_DATE_CHANGED":
            counts["lines_return_changed"] += 1
        else:
            counts["lines_updated"] += 1

        if i % 50 == 0 or i == total:
            conn.commit()  # save progress so far - an interruption won't lose it
            print(f"  ...processed {i}/{total} rows")

    cur.close()
    return counts


def run(path: str):
    filename = os.path.basename(path)
    post_date = parse_post_date_from_filename(filename, "IN")

    print(f"Parsing {filename} (post date: {post_date})...")
    rows = parse_file(path)
    print(f"  Parsed {len(rows)} rows.")

    conn = db.get_connection()
    try:
        print("Loading into staging_in...")
        load_staging(conn, rows, filename, post_date)

        print("Normalizing into solicitations / solicitation_lines...")
        counts = normalize_into_core_tables(conn, rows, post_date)
        print(f"  New solicitations: {counts['solicitations_new']}")
        print(f"  Previously-seen solicitations (re-posted): {counts['solicitations_seen']}")
        print(f"  New lines: {counts['lines_new']}")
        print(f"  Updated lines: {counts['lines_updated']}")
        print(f"  Lines with return date changed: {counts['lines_return_changed']}")
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python import_in.py path/to/IN260911.TXT")
        sys.exit(1)
    db.run_with_retries(run, sys.argv[1])
