"""
import_bq.py
------------
Imports a daily BQ file into staging_bq, then joins it back onto the
existing solicitation_lines created by import_in.py.

Handles BOTH real formats we've seen:
  - The RAW format (bq<yymmdd>.txt, inside the daily zip - this is what
    the automated nightly fetch actually receives): quoted CSV, no
    header row, 121 columns in the EXACT SAME ORDER as the .xlsx
    headers below. Confirmed against a real 3,918-row file - every
    field lines up with the known column meanings.
  - The .xlsx format your colleague sent manually (in case that ever
    happens again): named headers, one dated sheet.

CONFIRMED (from the real file): BQ carries BOTH a "Solicitation Line
Number" (here a zero-padded 4-digit string like "0001", not an int)
AND a "Purchase Request Number" - and the PR number is the exact same
value IN already gave us. Per the spec (section 4): "Use Solicitation
Number + Line Number to connect BQ back to the solicitation" - in
practice, matching on (solicitation_number, purchase_request_number)
is the reliable way to do this here, since that's the key IN already
populated. We still store the Solicitation Line Number for reference.

Per the spec's explicit rule: BQ NEVER creates a new parent solicitation.
If a BQ row's solicitation isn't already in `solicitations` (from an
IN import), or the specific line isn't in `solicitation_lines`, we
skip it and count it as "orphaned" rather than inventing a new one.

Usage:
    python import_bq.py path/to/bq260916.txt      # the real raw format
    python import_bq.py path/to/BQ.xlsx            # the manual format
"""

import sys
import os
import csv
import json
import datetime
from openpyxl import load_workbook

import db
from normalize import parse_post_date_from_filename

# The real column headers, exactly as they appear in the .xlsx file -
# and, positionally (0-indexed), exactly matching the raw .txt column
# order too (confirmed against a real file).
COL_SOLICITATION = "Solicitation Number"
COL_LINE_NUMBER = "Solicitation Line Number"
COL_PR_NUMBER = "Purchase Request Number"
COL_NSN_PART = "National Stock Number / Part Number"
COL_UNIT_ISSUE = "Unit of Issue"
COL_QUANTITY = "Quantity"
COL_UNIT_PRICE = "Unit Price"
COL_DELIVERY_DAYS = "Delivery Days"
COL_RETURN_BY_DATE = "Return By Date"
COL_QUOTE_FOR_CAGE = "Quote for CAGE Code"

# Positional indices in the raw .txt format (0-indexed), confirmed
# against a real 121-column file.
IDX_SOLICITATION = 0
IDX_RETURN_BY_DATE = 4
IDX_QUOTE_FOR_CAGE = 6
IDX_LINE_NUMBER = 43
IDX_PR_NUMBER = 45
IDX_NSN_PART = 46
IDX_UNIT_ISSUE = 47
IDX_QUANTITY = 48
IDX_UNIT_PRICE = 49
IDX_DELIVERY_DAYS = 50


def _clean_number_string(value):
    """PR numbers etc. sometimes come from Excel as ints (e.g.
    7017430276) rather than text - convert to a plain string without
    a trailing '.0'."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or None


def _clean_date(value):
    """Return By Date comes from Excel as a real datetime - just take the date part."""
    if value is None:
        return None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.date() if isinstance(value, datetime.datetime) else value
    return None


def _parse_mmddyyyy(value: str):
    """Parse the raw .txt format's dates, e.g. '09/29/2026' (4-digit year -
    different from IN's 2-digit-year '09/17/26')."""
    if not value or not value.strip():
        return None
    try:
        month, day, year = value.strip().split("/")
        return datetime.date(int(year), int(month), int(day))
    except (ValueError, AttributeError):
        return None


def _to_number(value):
    """Empty string -> None, otherwise a float (Postgres NUMERIC accepts either)."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_file_txt(path: str):
    """
    Parse the real raw format: quoted CSV, no header, 121 columns in
    the confirmed positional order.
    """
    rows = []
    with open(path, "r", encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        for r in reader:
            if len(r) < 51:  # need at least through Delivery Days
                continue
            rows.append({
                "solicitation_number": r[IDX_SOLICITATION].strip(),
                "solicitation_line_number": r[IDX_LINE_NUMBER].strip() or None,
                "purchase_request_number": r[IDX_PR_NUMBER].strip() or None,
                "nsn_part": r[IDX_NSN_PART].strip(),
                "unit_of_issue": r[IDX_UNIT_ISSUE].strip() or None,
                "quantity": _to_number(r[IDX_QUANTITY]),
                "unit_price": _to_number(r[IDX_UNIT_PRICE]),
                "delivery_days": _to_number(r[IDX_DELIVERY_DAYS]),
                "return_by_date": _parse_mmddyyyy(r[IDX_RETURN_BY_DATE]),
                "quote_for_cage_code": r[IDX_QUOTE_FOR_CAGE].strip() or None,
                "raw_row": r,  # every column (as a plain list) for anything not modeled yet
            })
    return rows


def parse_file_xlsx(path: str):
    """
    Parse the manually-converted .xlsx format your colleague sent once.
    Like AS.xlsx, the real file has a dated data sheet (e.g. 'bq260911')
    plus an empty 'Sheet1' - find whichever sheet actually has the
    'Solicitation Number' header.
    """
    wb = load_workbook(path, read_only=True, data_only=True)

    data_sheet = None
    for name in wb.sheetnames:
        ws = wb[name]
        first_row = next(ws.iter_rows(max_row=1, values_only=True), None)
        if first_row and COL_SOLICITATION in [str(c).strip() if c else "" for c in first_row]:
            data_sheet = name
            break

    if data_sheet is None:
        raise ValueError(f"Couldn't find a sheet with a {COL_SOLICITATION!r} header in {path}")

    ws = wb[data_sheet]
    rows = []
    header = None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            header = [str(c).strip() if c else "" for c in row]
            continue
        if not any(row):
            continue
        record = dict(zip(header, row))

        rows.append({
            "solicitation_number": str(record.get(COL_SOLICITATION, "") or "").strip(),
            "solicitation_line_number": _clean_number_string(record.get(COL_LINE_NUMBER)),
            "purchase_request_number": _clean_number_string(record.get(COL_PR_NUMBER)),
            "nsn_part": str(record.get(COL_NSN_PART, "") or "").strip(),
            "unit_of_issue": str(record.get(COL_UNIT_ISSUE, "") or "").strip() or None,
            "quantity": record.get(COL_QUANTITY),
            "unit_price": record.get(COL_UNIT_PRICE),
            "delivery_days": record.get(COL_DELIVERY_DAYS),
            "return_by_date": _clean_date(record.get(COL_RETURN_BY_DATE)),
            "quote_for_cage_code": str(record.get(COL_QUOTE_FOR_CAGE, "") or "").strip() or None,
            "raw_row": record,  # every column, for anything we haven't modeled yet
        })
    return rows


def parse_file(path: str):
    """Dispatches to the right parser based on the file extension."""
    if path.lower().endswith(".xlsx"):
        return parse_file_xlsx(path)
    return parse_file_txt(path)


def load_staging(conn, rows, source_file, post_date):
    cur = conn.cursor()
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        cur.execute(
            """
            INSERT INTO staging_bq (
                solicitation_number, solicitation_line_number, purchase_request_number,
                nsn_part, unit_of_issue, quantity, unit_price, delivery_days,
                return_by_date, quote_for_cage_code, raw_row, source_file, post_date
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["solicitation_number"], row["solicitation_line_number"], row["purchase_request_number"],
                row["nsn_part"], row["unit_of_issue"], row["quantity"], row["unit_price"],
                row["delivery_days"], row["return_by_date"], row["quote_for_cage_code"],
                json.dumps(row["raw_row"], default=str), source_file, post_date,
            ),
        )
        if i % 500 == 0 or i == total:
            conn.commit()
            print(f"  ...staged {i}/{total} rows")
    cur.close()


def join_into_solicitation_lines(conn, rows):
    """
    For each BQ row: find the existing solicitation_line by
    (solicitation_number, purchase_request_number) - created earlier by
    import_in.py - and fill in solicitation_line_number, delivery_days,
    and unit_price_quoted (only overwriting with a real value, never
    blanking out something already there with NULL).

    Per the spec: NEVER create a new solicitation just because a BQ row
    doesn't match one. Rows that don't match an existing line are
    counted as orphaned, not silently dropped.
    """
    cur = conn.cursor()
    counts = {"matched": 0, "orphaned_no_solicitation": 0, "orphaned_no_line": 0}
    total = len(rows)

    for i, row in enumerate(rows, start=1):
        sol_num = row["solicitation_number"]
        pr_num = row["purchase_request_number"]
        if not sol_num or not pr_num:
            counts["orphaned_no_line"] += 1
            continue

        cur.execute("SELECT 1 FROM solicitations WHERE solicitation_number = %s", (sol_num,))
        if cur.fetchone() is None:
            counts["orphaned_no_solicitation"] += 1
            continue

        cur.execute(
            "SELECT id FROM solicitation_lines WHERE solicitation_number = %s AND purchase_request_number = %s",
            (sol_num, pr_num),
        )
        line = cur.fetchone()
        if line is None:
            counts["orphaned_no_line"] += 1
            continue

        line_id = line[0]
        cur.execute(
            """
            UPDATE solicitation_lines
            SET line_number = COALESCE(%s, line_number),
                delivery_days = COALESCE(%s, delivery_days),
                unit_price_quoted = COALESCE(%s, unit_price_quoted),
                updated_at = now()
            WHERE id = %s
            """,
            (row["solicitation_line_number"], row["delivery_days"], row["unit_price"], line_id),
        )
        counts["matched"] += 1

        if i % 200 == 0 or i == total:
            conn.commit()
            print(f"  ...processed {i}/{total} rows")

    cur.close()
    return counts


def run(path: str):
    filename = os.path.basename(path)
    try:
        post_date = parse_post_date_from_filename(filename, "BQ")
    except ValueError:
        if filename.lower().endswith(".xlsx"):
            wb = load_workbook(path, read_only=True)
            dated_sheet = next((s for s in wb.sheetnames if s.lower().startswith("bq") and any(c.isdigit() for c in s)), None)
            if dated_sheet:
                post_date = parse_post_date_from_filename(dated_sheet, "bq")
            else:
                raise ValueError("Couldn't determine the post date from the filename or sheet name - pass it explicitly.")
        else:
            raise

    print(f"Parsing {filename} (post date: {post_date})...")
    rows = parse_file(path)
    print(f"  Parsed {len(rows)} rows.")

    conn = db.get_connection()
    try:
        print("Loading into staging_bq...")
        load_staging(conn, rows, filename, post_date)

        print("Joining onto existing solicitation_lines...")
        counts = join_into_solicitation_lines(conn, rows)
        print(f"  Matched and updated: {counts['matched']}")
        print(f"  Orphaned (no matching solicitation - not created): {counts['orphaned_no_solicitation']}")
        print(f"  Orphaned (solicitation exists, but no matching line): {counts['orphaned_no_line']}")
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python import_bq.py path/to/bq260916.txt   (or BQ.xlsx)")
        sys.exit(1)
    db.run_with_retries(run, sys.argv[1])
