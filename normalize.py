"""
normalize.py
------------
Shared helpers used by every importer. Keeping these in one place means
the NSN/FSC/NIIN rule and the date-status rule are defined exactly once.
"""

from datetime import date


def split_nsn(nsn: str):
    """
    Given a 13-digit NSN as a string (e.g. '5330011234567'), return
    (nsn, fsc, niin) - all as strings, preserving leading zeros.

    FSC  = first 4 digits
    NIIN = last 9 digits

    Returns (None, None, None) if the input isn't a clean 13-digit string.
    """
    if not nsn:
        return None, None, None

    digits = nsn.strip()
    if len(digits) != 13 or not digits.isdigit():
        return None, None, None

    fsc = digits[0:4]
    niin = digits[4:13]
    return digits, fsc, niin


def normalize_nsn_from_dashed(dashed: str):
    """
    Given a human-formatted NSN like '5330-01-123-4567', strip the
    dashes and return the 13-digit form. Returns None if it doesn't
    look like a valid NSN once dashes are removed.
    """
    if not dashed:
        return None
    digits = dashed.replace("-", "").strip()
    if len(digits) != 13 or not digits.isdigit():
        return None
    return digits


def classify_date_status(issue_date: date, post_date: date) -> str:
    """
    Section 13-16 of the spec:
      CURRENT     issue_date == post_date
      FUTURE      issue_date >  post_date
      OLD_POSTED  issue_date <  post_date (default; OLD_EXTENDED is a
                  refinement applied later, only once we can confirm
                  the return date actually changed since we last saw
                  this solicitation - see mark_return_date_changes()).
    """
    if issue_date is None or post_date is None:
        return None
    if issue_date == post_date:
        return "CURRENT"
    if issue_date > post_date:
        return "FUTURE"
    return "OLD_POSTED"


def parse_mmddyy(value: str):
    """Parse dates like '09/17/26' (as seen in the IN file) into a date object."""
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        month, day, year_2digit = value.split("/")
        year = 2000 + int(year_2digit)
        return date(year, int(month), int(day))
    except (ValueError, AttributeError):
        return None


def parse_post_date_from_filename(filename: str, prefix: str):
    """
    IN260911.TXT -> date(2026, 9, 11). AS/BQ filenames follow the same
    YYMMDD pattern after their prefix (as260911, bq260911, etc).
    `prefix` is matched case-insensitively.
    """
    name = filename.upper()
    pfx = prefix.upper()
    if not name.startswith(pfx):
        raise ValueError(f"Filename {filename!r} doesn't start with expected prefix {prefix!r}")
    digits = "".join(ch for ch in name[len(pfx):] if ch.isdigit())[:6]
    if len(digits) != 6:
        raise ValueError(f"Couldn't find a 6-digit YYMMDD date in filename {filename!r}")
    yy, mm, dd = digits[0:2], digits[2:4], digits[4:6]
    return date(2000 + int(yy), int(mm), int(dd))
