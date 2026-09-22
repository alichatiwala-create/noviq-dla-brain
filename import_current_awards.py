"""
Real importer: pulls DLA "Current Awards" (CA) history for every NSN already
in your dashboard, and saves it into dla_award_history.

How it works:
1. Reads every distinct NSN currently in solicitation_lines (your dashboard's
   own database - uses the same connection as the dashboard, via database.py).
2. For each NSN, looks up https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=nsn&Value=<NSN>
   (same page you already tested with ca_by_nsn_test.py).
3. Parses every award row found (award/contract number, CAGE, total contract
   value, award date, posted date, nomenclature, PR number, solicitation).
4. Saves each award into dla_award_history, tagged source_type = 'Current
   Awards (CA)'. An award's identity is its contract/award number (not the
   date), so re-running this later:
     - adds a NEW row only for an award we've genuinely never seen before
     - UPDATES the existing row in place if DLA posted a change to an award
       we already have (a "modification" - DIBBS marks this with a "Last Mod
       Posting Date")
     - does nothing if it's the exact same award, unchanged
   Nothing is ever duplicated, and modifications are never missed.

5. For any award that's missing quantity, this also opens that award's PDF
   (the government's own DD Form 1155 order form), reads out the quantity
   and contractor/company name, and computes unit price as
   total contract value / quantity - DIBBS's webpage never shows a per-unit
   price directly, but the PDF's quantity plus the total we already have
   gives us one. The company name also gets saved into the vendors table
   (keyed by CAGE code), so it shows up anywhere else in the dashboard that
   looks up a CAGE code. This step is skipped for an award once it already
   has a quantity saved (so re-runs don't re-read PDFs unnecessarily), and
   it's skipped entirely if the award has no linked PDF or the PDF can't be
   read - it never stops the rest of the import.

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python import_current_awards.py

Optional: to test on just the first N NSNs before running the full list:
    python import_current_awards.py --limit 25

Optional: to skip reading PDFs (faster, but leaves quantity/unit price/
company name blank):
    python import_current_awards.py --skip-pdf

This can take a while if you have many NSNs (roughly 2-5 seconds per NSN,
a bit more for the first award per NSN since it also reads a PDF).
It prints progress as it goes, and a summary at the end.
"""

import argparse
import io
import re
import sys
import time
from datetime import datetime

import pdfplumber
from playwright.sync_api import sync_playwright

from database import get_connection

FIELD_PATTERN = re.compile(
    r'id="[^"]*_lbl(AwardBasicNumber|DeliveryOrder|LastModPostingDate|Cage|TotalContactPrice|'
    r'AwardDate|PostedDate|Nsn|Nomenclature|PurchaseRequest|Solicitation)"'
    r'[^>]*>(.*?)</span>',
    re.DOTALL,
)
NESTED_NOTE_SPLIT = re.compile(r'<span style="font-size: 9px;">.*', re.DOTALL)
TAG_STRIP = re.compile(r"<[^>]+>")
MONEY_STRIP = re.compile(r"[^\d.\-]")
PDF_HREF_PATTERN = re.compile(r'href="([^"]+\.PDF)"', re.IGNORECASE)

# --- PDF (DD Form 1155 "Order for Supplies or Services") field extraction ---
PDF_CAGE_PATTERN = re.compile(r"CONTRACTOR\s+CODE\s+(\w{5})")
PDF_QTY_PATTERN = re.compile(r"([\d,]+\.\d{3})\s*\n\*\s*If quantity accepted", re.IGNORECASE)
PDF_TOTAL_PATTERN = re.compile(r"TOTAL\s*\$?([\d,]+\.\d{2})")
PDF_CONTRACTOR_BLOCK_PATTERN = re.compile(
    r"CONTRACTOR\s+CODE\s+\w{5}.*?\n(.*?)12\.\s*DISCOUNT TERMS", re.DOTALL
)
PDF_NOISE_LINE = re.compile(
    r"^\(?YYYY.*\)?$|^\d+\s*DAYS\s*ADO.*$|^X?\s*SMALL$|^VANTAGED$|^WOMEN-OWNED$",
    re.IGNORECASE,
)
PDF_TRAILING_NOISE = re.compile(r"\s*(SMALL\s*DISAD-?|VANTAGED|WOMEN-OWNED|X\s*SMALL|SMALL)\s*$")


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass


def clean_field(field, raw_html):
    # Strip the "» Award/Basic Package View" / "» Delivery Order Package View"
    # nested note before removing tags, so we only keep the actual value.
    raw_html = NESTED_NOTE_SPLIT.sub("", raw_html)
    return TAG_STRIP.sub("", raw_html).strip()


def parse_rows(html):
    rows = []
    current = {}
    for match in FIELD_PATTERN.finditer(html):
        field, raw_value = match.group(1), match.group(2)
        value = clean_field(field, raw_value)
        if field == "AwardBasicNumber" and current:
            rows.append(current)
            current = {}
        current[field] = value
        if field == "AwardBasicNumber":
            # The PDF link (if any) is inside this same span's raw HTML,
            # before we stripped the tags out of it above.
            href_match = PDF_HREF_PATTERN.search(raw_value)
            current["AwardPdfUrl"] = href_match.group(1) if href_match else None
    if current:
        rows.append(current)
    return rows


def read_award_pdf(page, pdf_url):
    """
    Downloads and reads the award's PDF (DD Form 1155), pulling out the
    quantity and contractor/company name. Unit price is then computed as
    total_award_value / quantity - DIBBS's award pages never show a per-unit
    price directly, but the PDF's own total plus quantity gives us one.
    Returns a dict with whatever it could find (missing pieces are None) or
    None entirely if the PDF couldn't be read at all.
    """
    try:
        response = page.request.get(pdf_url, timeout=30000)
        if not response.ok:
            return None
        pdf_bytes = response.body()
    except Exception:
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception:
        return None

    result = {"quantity": None, "company_name": None, "pdf_total": None}

    qty_match = PDF_QTY_PATTERN.search(text)
    if qty_match:
        try:
            result["quantity"] = float(qty_match.group(1).replace(",", ""))
        except ValueError:
            pass

    total_match = PDF_TOTAL_PATTERN.search(text)
    if total_match:
        try:
            result["pdf_total"] = float(total_match.group(1).replace(",", ""))
        except ValueError:
            pass

    block_match = PDF_CONTRACTOR_BLOCK_PATTERN.search(text)
    if block_match:
        lines = [l.strip() for l in block_match.group(1).splitlines() if l.strip()]
        clean_lines = []
        for l in lines:
            if PDF_NOISE_LINE.match(l):
                continue
            l2 = PDF_TRAILING_NOISE.sub("", l).strip()
            if l2:
                clean_lines.append(l2)
        if clean_lines:
            name = clean_lines[0]
            if len(clean_lines) > 1 and clean_lines[1].upper().startswith("DBA"):
                name = f"{name} ({clean_lines[1]})"
            result["company_name"] = name

    return result


def upsert_vendor(conn, cage, company_name):
    if not cage or not company_name:
        return
    cur = conn.cursor()
    cur.execute("SELECT company_name FROM vendors WHERE cage_code = %s", (cage,))
    existing = cur.fetchone()
    if existing is None:
        cur.execute(
            "INSERT INTO vendors (cage_code, company_name) VALUES (%s, %s)",
            (cage, company_name),
        )
        conn.commit()
    elif not existing[0]:
        cur.execute(
            "UPDATE vendors SET company_name = %s WHERE cage_code = %s",
            (company_name, cage),
        )
        conn.commit()


def parse_date(mmddyyyy):
    mmddyyyy = (mmddyyyy or "").strip()
    if not mmddyyyy or mmddyyyy == "&nbsp;":
        return None
    try:
        return datetime.strptime(mmddyyyy, "%m-%d-%Y").date()
    except ValueError:
        return None


def parse_money(text):
    text = (text or "").strip()
    if not text or text == "&nbsp;":
        return None
    cleaned = MONEY_STRIP.sub("", text)
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def get_active_nsns(conn, limit=None):
    cur = conn.cursor()
    if limit:
        cur.execute("SELECT DISTINCT nsn FROM solicitation_lines WHERE nsn IS NOT NULL ORDER BY nsn LIMIT %s", (limit,))
    else:
        cur.execute("SELECT DISTINCT nsn FROM solicitation_lines WHERE nsn IS NOT NULL ORDER BY nsn")
    return [r[0] for r in cur.fetchall()]


def find_existing_award(conn, nsn, contract_number, award_number):
    """
    Looks up whether we already have this exact award saved. The identity of
    an award is its contract/award number (+ delivery order number if any),
    not the date - that way, if DLA posts a MODIFICATION to an award we
    already have (same contract, but something about it changed), we detect
    it as the SAME award and update it in place, instead of creating a
    second, duplicate row for it.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, total_award_value, last_mod_posting_date, award_date, winning_cage, quantity, award_pdf_url
        FROM dla_award_history
        WHERE nsn = %s AND contract_number = %s AND award_number IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (nsn, contract_number, award_number),
    )
    return cur.fetchone()


def save_award(conn, row, nsn):
    """
    Returns (status, award_id, needs_pdf_read):
      status is one of "new" / "modified" / "unchanged" / "skipped".
      award_id is the dla_award_history row's id (or None if skipped).
      needs_pdf_read is True when this row still doesn't have a quantity
      saved and there's a PDF link we can try reading it from.
    """
    contract_number = row.get("AwardBasicNumber") or None
    delivery_order = row.get("DeliveryOrder") or None
    award_number = delivery_order or contract_number
    cage = row.get("Cage") or None
    total_value = parse_money(row.get("TotalContactPrice"))
    award_date = parse_date(row.get("AwardDate"))
    last_mod_date = parse_date(row.get("LastModPostingDate"))
    solicitation_number = row.get("Solicitation") or None
    if solicitation_number == "&nbsp;":
        solicitation_number = None
    pdf_url = row.get("AwardPdfUrl")

    if not contract_number:
        return "skipped", None, False

    existing = find_existing_award(conn, nsn, contract_number, award_number)
    cur = conn.cursor()

    if existing is None:
        cur.execute(
            """
            INSERT INTO dla_award_history
                (nsn, award_number, contract_number, award_date, winning_cage,
                 quantity, unit_price, total_award_value, solicitation_number,
                 source_year, source_type, source_file, last_mod_posting_date,
                 award_pdf_url)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                nsn, award_number, contract_number, award_date, cage,
                total_value, solicitation_number,
                award_date.year if award_date else None,
                "Current Awards (CA)", "dibbs_awards_by_nsn", last_mod_date,
                pdf_url,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return "new", new_id, bool(pdf_url)

    (existing_id, existing_total, existing_mod_date, existing_award_date,
     existing_cage, existing_qty, existing_pdf_url) = existing
    changed = (
        existing_total != total_value
        or existing_mod_date != last_mod_date
        or existing_award_date != award_date
        or existing_cage != cage
    )
    # Even if nothing else about the award changed, make sure we save the PDF
    # link if we have one now and didn't before (e.g. an older row saved
    # before this column existed) - that's what lets backfill_award_pdfs.py
    # find it later.
    if pdf_url and not existing_pdf_url:
        cur.execute(
            "UPDATE dla_award_history SET award_pdf_url = %s WHERE id = %s",
            (pdf_url, existing_id),
        )
        conn.commit()

    needs_pdf = existing_qty is None and bool(pdf_url or existing_pdf_url)

    if not changed:
        return "unchanged", existing_id, needs_pdf

    # Something about this award changed since we last saved it (a
    # modification) - update the SAME row rather than inserting a new one.
    cur.execute(
        """
        UPDATE dla_award_history
        SET award_date = %s, winning_cage = %s, total_award_value = %s,
            solicitation_number = %s, last_mod_posting_date = %s,
            award_pdf_url = COALESCE(%s, award_pdf_url)
        WHERE id = %s
        """,
        (award_date, cage, total_value, solicitation_number, last_mod_date, pdf_url, existing_id),
    )
    conn.commit()
    return "modified", existing_id, needs_pdf


def apply_pdf_details(conn, page, award_id, pdf_url, cage, fallback_total):
    """
    Reads quantity + company name from the award's PDF, computes unit price
    from total/quantity, and saves it onto the already-saved award row (plus
    upserts the company name into the vendors table so it shows up wherever
    else CAGE codes are looked up in the dashboard).
    """
    details = read_award_pdf(page, pdf_url)
    if not details:
        return False

    quantity = details.get("quantity")
    total = details.get("pdf_total") or fallback_total
    unit_price = round(total / quantity, 2) if quantity and total else None

    if quantity is not None or unit_price is not None:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dla_award_history SET quantity = %s, unit_price = %s WHERE id = %s",
            (quantity, unit_price, award_id),
        )
        conn.commit()

    if details.get("company_name"):
        upsert_vendor(conn, cage, details["company_name"])

    return quantity is not None or unit_price is not None or bool(details.get("company_name"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N NSNs (for testing)")
    parser.add_argument("--nsn", type=str, default=None, help="Only process this one specific NSN (for testing)")
    parser.add_argument(
        "--skip-pdf", action="store_true",
        help="Don't open award PDFs for quantity/unit price/company name (faster, but leaves those blank)",
    )
    args = parser.parse_args()

    conn = get_connection()
    if args.nsn:
        nsns = [args.nsn]
    else:
        nsns = get_active_nsns(conn, args.limit)
    print(f"Found {len(nsns)} distinct NSN(s) to check.\n")

    total_new = 0
    total_modified = 0
    total_unchanged = 0
    total_nsns_with_awards = 0
    total_nsns_no_awards = 0
    total_errors = 0
    total_pdfs_read = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for i, nsn in enumerate(nsns, 1):
            url = f"https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=nsn&Value={nsn}"
            print(f"[{i}/{len(nsns)}] {nsn} ...", end=" ")
            try:
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                accept_warning_if_present(page)
                html = page.content()
            except Exception as e:
                print(f"FAILED to load ({e})")
                total_errors += 1
                continue

            if "no awards are on file" in html.lower():
                print("no awards on file.")
                total_nsns_no_awards += 1
                continue

            rows = parse_rows(html)
            if not rows:
                print("no rows parsed (page may have changed - check manually).")
                total_errors += 1
                continue

            new_count = 0
            modified_count = 0
            unchanged_count = 0
            pdf_count = 0
            for row in rows:
                try:
                    result, award_id, needs_pdf = save_award(conn, row, nsn)
                    if result == "new":
                        new_count += 1
                    elif result == "modified":
                        modified_count += 1
                    elif result == "unchanged":
                        unchanged_count += 1

                    if not args.skip_pdf and needs_pdf and award_id:
                        pdf_url = row.get("AwardPdfUrl")
                        cage = row.get("Cage") or None
                        fallback_total = parse_money(row.get("TotalContactPrice"))
                        if apply_pdf_details(conn, page, award_id, pdf_url, cage, fallback_total):
                            pdf_count += 1
                        time.sleep(0.3)
                except Exception as e:
                    # A failed INSERT/UPDATE leaves the whole connection's
                    # transaction "aborted" in Postgres - every subsequent
                    # save on this same connection would silently fail with
                    # "current transaction is aborted" until we roll back,
                    # so without this a single bad row would quietly break
                    # saving for the rest of the entire run.
                    conn.rollback()
                    print(f"\n    (error saving a row for {nsn}: {e})")

            total_new += new_count
            total_modified += modified_count
            total_unchanged += unchanged_count
            total_pdfs_read += pdf_count
            total_nsns_with_awards += 1
            print(
                f"{len(rows)} award(s) found - {new_count} new, "
                f"{modified_count} modified, {unchanged_count} unchanged"
                + (f", {pdf_count} PDF(s) read." if pdf_count else ".")
            )

            time.sleep(0.5)  # be polite to DIBBS's server

        context.close()
        browser.close()

    print("\n---- Summary ----")
    print(f"NSNs checked:        {len(nsns)}")
    print(f"NSNs with awards:    {total_nsns_with_awards}")
    print(f"NSNs with no awards: {total_nsns_no_awards}")
    print(f"Errors:              {total_errors}")
    print(f"New awards saved:      {total_new}")
    print(f"Modified awards updated: {total_modified}")
    print(f"Unchanged (skipped):     {total_unchanged}")
    print(f"PDFs read (qty/price/company filled in): {total_pdfs_read}")
    print("\nDone. Refresh your dashboard and click into an NSN to see the award history.")


if __name__ == "__main__":
    main()
