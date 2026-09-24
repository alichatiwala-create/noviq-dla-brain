"""
Bulk importer for DLA's official "Contracthist" file (from
https://www.dla.mil/FOIA/Electronic-Reading-Room/ -> "Current DLA Procurement
Data Files" -> Contracthist). This is a monthly government file that already
has quantity, unit price, award date, and winning vendor (CAGE) for every
recent award, government-wide - so unlike the DIBBS PDF-reading approach,
this doesn't need to open a single PDF or depend on DIBBS's own (sometimes
flaky) live award search at all.

What it does:
1. Reads every distinct NSN currently in your dashboard (solicitation_lines).
2. Streams through the Contracthist file (it's huge - 800,000+ rows for the
   whole government - so this only keeps rows for NSNs you actually have).
3. For each matching row, saves it into dla_award_history the same
   "no duplicates unless something changed" way as the Current Awards (CA)
   importer:
     - identity = (nsn, contract_number)
     - if we don't have this award yet -> insert it, complete with quantity
       and unit price already filled in (no PDF-reading needed!)
     - if we already have this award (e.g. from a CA import) but it's
       missing quantity/unit price -> fill those in from this file
     - if we already have it complete -> leave it alone

Field mapping (from the file's own header row):
    NIIN + FSC       -> combine to make the full NSN (this file splits NSN
                        into FSC (4 digits) + NIIN (9 digits) separately,
                        AND also gives a combined "NSN" column - we use the
                        NSN column directly, it's simpler and matches)
    CAGE             -> winning_cage
    CONTRACT_NUMBER  -> contract_number (and award_number, since this file
                        has no separate delivery-order column)
    ORDER_QTY        -> quantity
    AWARD_DATE       -> award_date (format YYYYMMDD)
    NETPRICE         -> unit_price (confirmed against a real award PDF you
                        sent - this is the actual awarded per-unit price,
                        NOT the same as STD_U_PRICE, which is a separate
                        catalog/standard price DLA tracks and isn't what
                        we want here)

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python import_contracthist.py "C:\path\to\contracthist.zip"

(Point it at whichever Contracthist zip file you downloaded - it reads the
.txt file straight out of the zip, no need to unzip it yourself first.)

This can take a few minutes since the file has 800,000+ rows total, even
though only a small fraction will match your NSNs.
"""

import argparse
import csv
import io
import sys
import zipfile
from datetime import datetime

import db


def get_active_nsns(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT nsn FROM solicitation_lines WHERE nsn IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


def _find_text_readers(zf):
    """
    Recursively finds every .txt file inside a ZipFile, opening one level
    of nested .zip files along the way - DLA doesn't package these the
    same way every year: some years are a single flat .txt, some are one
    .txt wrapped in an outer zip, and some years (seen for 2019, 2021,
    2022, and 2020) bundle a WHOLE YEAR as twelve separate monthly .zip
    files, each with its own .txt inside, all wrapped in one outer zip.
    Returns a list of (name, text-reader) pairs - callers must process
    every one of them, not just the first, or most of the year's data
    gets silently skipped.
    """
    readers = []
    for info in zf.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        if name.lower().endswith(".txt"):
            readers.append((name, io.TextIOWrapper(zf.open(name, "r"), encoding="utf-8", errors="replace")))
        elif name.lower().endswith(".zip"):
            inner_zf = zipfile.ZipFile(io.BytesIO(zf.read(name)))
            readers.extend(_find_text_readers(inner_zf))
    return readers


def open_data_files(zip_path):
    """
    Opens the given zip and returns every .txt data file inside it (see
    _find_text_readers for why there can be more than one), each as a
    line-by-line text reader (doesn't load anything fully into memory -
    these files can be 100+ MB uncompressed each).
    """
    zf = zipfile.ZipFile(zip_path)
    readers = _find_text_readers(zf)
    if not readers:
        print(f"Couldn't find any .txt file inside {zip_path} (including nested zips) - is this the right zip?")
        sys.exit(1)
    return readers


def parse_qty_or_price(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_award_date(yyyymmdd):
    yyyymmdd = (yyyymmdd or "").strip()
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").date()
    except ValueError:
        return None


def find_existing(conn, nsn, contract_number):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, quantity, unit_price, total_award_value
        FROM dla_award_history
        WHERE nsn = %s AND contract_number = %s
        LIMIT 1
        """,
        (nsn, contract_number),
    )
    return cur.fetchone()


def save_row(conn, row, source_file):
    nsn = (row.get("NSN") or "").strip()
    contract_number = (row.get("CONTRACT_NUMBER") or "").strip()
    if not nsn or not contract_number:
        return "skipped"

    cage = (row.get("CAGE") or "").strip() or None
    quantity = parse_qty_or_price(row.get("ORDER_QTY"))
    unit_price = parse_qty_or_price(row.get("NETPRICE"))
    award_date = parse_award_date(row.get("AWARD_DATE"))
    total_value = round(quantity * unit_price, 2) if quantity and unit_price else None

    existing = find_existing(conn, nsn, contract_number)
    cur = conn.cursor()

    if existing is None:
        cur.execute(
            """
            INSERT INTO dla_award_history
                (nsn, award_number, contract_number, award_date, winning_cage,
                 quantity, unit_price, total_award_value, source_year,
                 source_type, source_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                nsn, contract_number, contract_number, award_date, cage,
                quantity, unit_price, total_value,
                award_date.year if award_date else None,
                "ContractHist", source_file,
            ),
        )
        conn.commit()
        return "new"

    existing_id, existing_qty, existing_unit_price, existing_total = existing
    if existing_qty is None or existing_unit_price is None:
        cur.execute(
            """
            UPDATE dla_award_history
            SET quantity = COALESCE(quantity, %s),
                unit_price = COALESCE(unit_price, %s),
                total_award_value = COALESCE(total_award_value, %s)
            WHERE id = %s
            """,
            (quantity, unit_price, total_value, existing_id),
        )
        conn.commit()
        return "filled_in"

    return "already_complete"


def run(zip_path):
    conn = db.get_connection()
    active_nsns = get_active_nsns(conn)
    print(f"Loaded {len(active_nsns)} distinct NSN(s) from your dashboard.\n")

    source_file_label = zip_path.split("/")[-1].split("\\")[-1]
    data_files = open_data_files(zip_path)
    if len(data_files) > 1:
        print(f"Found {len(data_files)} data file(s) inside this zip (some years bundle one file per month):")
        for name, _ in data_files:
            print(f"  - {name}")
        print()

    total_rows = 0
    total_matched = 0
    total_new = 0
    total_filled_in = 0
    total_already_complete = 0
    total_skipped = 0
    total_errors = 0

    for name, reader_file in data_files:
        reader = csv.DictReader(reader_file, delimiter="|")
        for row in reader:
            total_rows += 1
            if total_rows % 100000 == 0:
                print(f"  ...scanned {total_rows} rows so far ({total_matched} matched your NSNs)")

            nsn = (row.get("NSN") or "").strip()
            if nsn not in active_nsns:
                continue

            total_matched += 1
            try:
                result = save_row(conn, row, source_file_label)
                if result == "new":
                    total_new += 1
                elif result == "filled_in":
                    total_filled_in += 1
                elif result == "already_complete":
                    total_already_complete += 1
                else:
                    total_skipped += 1
            except Exception as e:
                # A failed INSERT/UPDATE leaves the whole connection's
                # transaction "aborted" in Postgres - every subsequent save
                # on this same connection would silently fail with "current
                # transaction is aborted" until we roll back, so without
                # this a single bad row would quietly break saving for the
                # rest of the entire file (this bit hard on 2023-2026).
                conn.rollback()
                total_errors += 1
                print(f"  (error saving a row for NSN {nsn}: {e})")

    print("\n---- Summary ----")
    print(f"Total rows in file:              {total_rows}")
    print(f"Rows matching your NSNs:         {total_matched}")
    print(f"New awards saved (qty+price included): {total_new}")
    print(f"Existing awards filled in (qty/price added): {total_filled_in}")
    print(f"Already had qty/price (left alone): {total_already_complete}")
    print(f"Skipped (missing NSN/contract #): {total_skipped}")
    print(f"Errors (row rolled back, didn't save): {total_errors}")
    print("\nDone. Refresh your dashboard - quantity and unit price should now show up")
    print("for a lot more NSNs, without needing to read a single PDF.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", help="Path to the Contracthist zip file you downloaded")
    args = parser.parse_args()
    db.run_with_retries(run, args.zip_path)


if __name__ == "__main__":
    main()
