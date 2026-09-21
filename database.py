"""
Database access layer for the Noviq DLA Brain dashboard.

Connects to the SAME Neon Postgres database that noviq-dla-brain's
import scripts (import_in.py / import_as.py / import_bq.py) write into.
This file only READS that data (plus writes for quoted/notes/basket/quotes),
it never runs the import pipeline itself.

Uses the exact same environment variables as noviq-dla-brain/db.py:
    NOVIQ_DB_HOST
    NOVIQ_DB_PORT
    NOVIQ_DB_NAME
    NOVIQ_DB_USER
    NOVIQ_DB_PASSWORD
    NOVIQ_DB_SSL   ("true" for Neon)
"""

import os
import ssl
import pg8000.dbapi as pg8000


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection():
    host = os.environ.get("NOVIQ_DB_HOST")
    port = int(os.environ.get("NOVIQ_DB_PORT", "5432"))
    database = os.environ.get("NOVIQ_DB_NAME")
    user = os.environ.get("NOVIQ_DB_USER")
    password = os.environ.get("NOVIQ_DB_PASSWORD")
    use_ssl = os.environ.get("NOVIQ_DB_SSL", "false").strip().lower() == "true"

    if not password:
        raise RuntimeError(
            "NOVIQ_DB_PASSWORD is not set. Set the NOVIQ_DB_* environment "
            "variables (same ones noviq-dla-brain uses) before running the app."
        )

    ssl_context = ssl.create_default_context() if use_ssl else None

    return pg8000.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        ssl_context=ssl_context,
    )


def get_dict_cursor(conn):
    """Return a cursor whose fetchall()/fetchone() rows come back as dicts."""
    cur = conn.cursor()
    real_fetchall = cur.fetchall
    real_fetchone = cur.fetchone

    def _cols():
        return [d[0] for d in cur.description] if cur.description else []

    def fetchall():
        cols = _cols()
        return [dict(zip(cols, row)) for row in real_fetchall()]

    def fetchone():
        cols = _cols()
        row = real_fetchone()
        return dict(zip(cols, row)) if row is not None else None

    cur.fetchall = fetchall
    cur.fetchone = fetchone
    return cur


def init_db():
    # Schema lives in noviq-dla-brain/schema.sql + migration_baskets.sql + migration_v2.sql.
    # Nothing to do here; kept for compatibility with app.py's old startup call.
    pass


# ---------------------------------------------------------------------------
# Lookups / labels
# ---------------------------------------------------------------------------

SET_ASIDE_LABELS = {
    "N": "Unrestricted",
    "Y": "Small Business",
    "H": "HubZone",
    "R": "SDVOSB",
    "L": "8(a)",
    "A": "WOSB",
    "E": "EDWOSB",
}

# tab -> which date_status value(s) it matches
TAB_STATUS_MAP = {
    "CURRENT": ["CURRENT"],
    "FUTURE": ["FUTURE"],
    "OLD": ["OLD_POSTED", "OLD_EXTENDED"],
}

SAFE_SORT_COLUMNS = {
    "return_by_date": "sl.return_by_date",
    "nsn": "sl.nsn",
    "qty": "sl.qty",
    "amsc": "sl.amsc",
    "last_award_date": "last_award_date",
    "last_unit_price": "last_award_price",
    "estimated_value": "estimated_value",
    "solicitation_number": "sl.solicitation_number",
    "mcrl_count": "mcrl_count",
    "hist_vendor_count": "hist_vendor_count",
    "delivery_days": "sl.delivery_days",
    "days_remaining": "days_remaining",
}


def _meta_info(cur):
    cur.execute(
        "SELECT MAX(last_seen_post_date) AS latest_post_date, COUNT(*) AS total_solicitations FROM solicitations"
    )
    return cur.fetchone()


def get_meta():
    conn = get_connection()
    try:
        cur = get_dict_cursor(conn)
        info = _meta_info(cur)

        counts = {}
        for tab, statuses in TAB_STATUS_MAP.items():
            placeholders = ",".join(["%s"] * len(statuses))
            cur.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM solicitation_lines sl
                JOIN solicitations s ON s.solicitation_number = sl.solicitation_number
                WHERE s.date_status IN ({placeholders})
                """,
                tuple(statuses),
            )
            counts[tab] = cur.fetchone()["n"]

        return {
            "latest_post_date": str(info["latest_post_date"]) if info["latest_post_date"] else None,
            "total_solicitations": info["total_solicitations"],
            "tab_counts": counts,
        }
    finally:
        conn.close()


def list_baskets():
    conn = get_connection()
    try:
        cur = get_dict_cursor(conn)
        cur.execute(
            """
            SELECT b.id, b.name, COUNT(bi.solicitation_line_id) AS item_count
            FROM baskets b
            LEFT JOIN basket_items bi ON bi.basket_id = b.id
            GROUP BY b.id, b.name
            ORDER BY b.name
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def create_basket(name):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO baskets (name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            # already existed - fetch its id
            cur.execute("SELECT id FROM baskets WHERE name = %s", (name,))
            row = cur.fetchone()
        return row[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main RFQ list query
# ---------------------------------------------------------------------------

_BASE_SELECT = """
    SELECT
        sl.id,
        sl.line_number,
        sl.purchase_request_number AS rfq_number,
        sl.solicitation_number,
        sl.nsn,
        sl.fsc,
        sl.niin,
        sl.nomenclature AS description,
        sl.qty AS quantity,
        sl.unit_issue AS unit_of_issue,
        sl.amsc,
        sl.set_aside_indicator,
        sl.set_aside_pct,
        sl.return_by_date,
        sl.delivery_days,
        sl.quoted,
        s.date_status,
        s.issue_date,
        s.status AS solicitation_status,
        s.times_posted,
        (sl.return_by_date - CURRENT_DATE) AS days_remaining,
        la.last_award_date,
        la.last_award_price,
        la.last_award_cage,
        lav.company_name AS last_award_vendor_name,
        mp.unit_price AS management_price,
        COALESCE(mcrl.mcrl_count, 0) AS mcrl_count,
        COALESCE(hv.hist_vendor_count, 0) AS hist_vendor_count,
        bi.basket_ids,
        lq.quoted_price AS our_last_quote_price,
        lq.quoted_date AS our_last_quote_date,
        CASE
            WHEN la.last_award_price IS NOT NULL THEN la.last_award_price * sl.qty
            WHEN mp.unit_price IS NOT NULL THEN mp.unit_price * sl.qty
            ELSE NULL
        END AS estimated_value,
        CASE
            WHEN la.last_award_price IS NOT NULL THEN 'Last award price x qty (source: ContractHist/Awards)'
            WHEN mp.unit_price IS NOT NULL THEN 'Management price (DRN 7075) x qty (source: Management)'
            ELSE 'N/A - no award or management price on file'
        END AS estimated_value_basis
    FROM solicitation_lines sl
    JOIN solicitations s ON s.solicitation_number = sl.solicitation_number
    LEFT JOIN LATERAL (
        SELECT unit_price AS last_award_price, award_date AS last_award_date, winning_cage AS last_award_cage
        FROM dla_award_history dah
        WHERE dah.nsn = sl.nsn
        ORDER BY dah.award_date DESC
        LIMIT 1
    ) la ON TRUE
    LEFT JOIN vendors lav ON lav.cage_code = la.last_award_cage
    LEFT JOIN management_price mp ON mp.niin = sl.niin
    LEFT JOIN LATERAL (
        SELECT COUNT(*) AS mcrl_count
        FROM sources_canonical sc
        WHERE sc.nsn = sl.nsn AND (sc.in_as OR sc.approved)
    ) mcrl ON TRUE
    LEFT JOIN LATERAL (
        SELECT COUNT(DISTINCT winning_cage) AS hist_vendor_count
        FROM dla_award_history dah2
        WHERE dah2.nsn = sl.nsn
    ) hv ON TRUE
    LEFT JOIN LATERAL (
        SELECT array_agg(basket_id) AS basket_ids
        FROM basket_items bix
        WHERE bix.solicitation_line_id = sl.id
    ) bi ON TRUE
    LEFT JOIN LATERAL (
        SELECT quoted_price, quoted_date
        FROM quotes q
        WHERE q.nsn = sl.nsn
        ORDER BY q.quoted_date DESC, q.created_at DESC
        LIMIT 1
    ) lq ON TRUE
"""


def list_rfqs(filters, sort_by="return_by_date", sort_dir="asc", page=1, page_size=50):
    where_clauses = []
    params = []

    tab = filters.get("tab")
    if tab and tab in TAB_STATUS_MAP:
        statuses = TAB_STATUS_MAP[tab]
        placeholders = ",".join(["%s"] * len(statuses))
        where_clauses.append(f"s.date_status IN ({placeholders})")
        params.extend(statuses)

    # AMSC: accepts a list now (multi-select)
    amsc_list = filters.get("amsc") or []
    if amsc_list:
        placeholders = ",".join(["%s"] * len(amsc_list))
        where_clauses.append(f"sl.amsc IN ({placeholders})")
        params.extend(amsc_list)

    # Set Aside: accepts a list now (multi-select)
    set_aside_list = filters.get("set_aside") or []
    if set_aside_list:
        placeholders = ",".join(["%s"] * len(set_aside_list))
        where_clauses.append(f"sl.set_aside_indicator IN ({placeholders})")
        params.extend(set_aside_list)

    if filters.get("basket_id"):
        where_clauses.append(
            "sl.id IN (SELECT solicitation_line_id FROM basket_items WHERE basket_id = %s)"
        )
        params.append(filters["basket_id"])

    if filters.get("quoted") is not None:
        where_clauses.append("sl.quoted = %s")
        params.append(bool(filters["quoted"]))

    if filters.get("nsn"):
        where_clauses.append("sl.nsn ILIKE %s")
        params.append(f"%{filters['nsn']}%")

    # CAGE / vendor: accepts a list, matches either historical award winners
    # or approved-source CAGE codes / company names for this NSN.
    cage_list = filters.get("cage") or []
    if cage_list:
        or_parts = []
        for cage in cage_list:
            or_parts.append(
                "(EXISTS (SELECT 1 FROM dla_award_history dah3 WHERE dah3.nsn = sl.nsn AND (dah3.winning_cage ILIKE %s)) "
                "OR EXISTS (SELECT 1 FROM sources_canonical sc2 WHERE sc2.nsn = sl.nsn AND (sc2.cage_code ILIKE %s OR sc2.company_name ILIKE %s)))"
            )
            like = f"%{cage}%"
            params.extend([like, like, like])
        where_clauses.append("(" + " OR ".join(or_parts) + ")")

    if filters.get("est_value_min") is not None:
        where_clauses.append(
            "COALESCE("
            "(SELECT unit_price FROM dla_award_history WHERE nsn = sl.nsn ORDER BY award_date DESC LIMIT 1) * sl.qty, "
            "(SELECT unit_price FROM management_price WHERE niin = sl.niin) * sl.qty"
            ") >= %s"
        )
        params.append(filters["est_value_min"])

    if filters.get("est_value_max") is not None:
        where_clauses.append(
            "COALESCE("
            "(SELECT unit_price FROM dla_award_history WHERE nsn = sl.nsn ORDER BY award_date DESC LIMIT 1) * sl.qty, "
            "(SELECT unit_price FROM management_price WHERE niin = sl.niin) * sl.qty"
            ") <= %s"
        )
        params.append(filters["est_value_max"])

    if filters.get("return_by_from"):
        where_clauses.append("sl.return_by_date >= %s")
        params.append(filters["return_by_from"])

    if filters.get("return_by_to"):
        where_clauses.append("sl.return_by_date <= %s")
        params.append(filters["return_by_to"])

    if filters.get("delivery_days_min") is not None:
        where_clauses.append("sl.delivery_days >= %s")
        params.append(filters["delivery_days_min"])

    if filters.get("delivery_days_max") is not None:
        where_clauses.append("sl.delivery_days <= %s")
        params.append(filters["delivery_days_max"])

    if filters.get("mcrl_count_min") is not None:
        where_clauses.append(
            "(SELECT COUNT(*) FROM sources_canonical sc3 WHERE sc3.nsn = sl.nsn AND (sc3.in_as OR sc3.approved)) >= %s"
        )
        params.append(filters["mcrl_count_min"])

    if filters.get("last_award_from"):
        where_clauses.append(
            "(SELECT MAX(award_date) FROM dla_award_history WHERE nsn = sl.nsn) >= %s"
        )
        params.append(filters["last_award_from"])

    if filters.get("last_award_to"):
        where_clauses.append(
            "(SELECT MAX(award_date) FROM dla_award_history WHERE nsn = sl.nsn) <= %s"
        )
        params.append(filters["last_award_to"])

    if filters.get("last_unit_price_min") is not None:
        where_clauses.append(
            "(SELECT unit_price FROM dla_award_history WHERE nsn = sl.nsn ORDER BY award_date DESC LIMIT 1) >= %s"
        )
        params.append(filters["last_unit_price_min"])

    if filters.get("last_unit_price_max") is not None:
        where_clauses.append(
            "(SELECT unit_price FROM dla_award_history WHERE nsn = sl.nsn ORDER BY award_date DESC LIMIT 1) <= %s"
        )
        params.append(filters["last_unit_price_max"])

    search = filters.get("search")
    if search:
        where_clauses.append(
            "("
            "sl.nsn ILIKE %s OR sl.nomenclature ILIKE %s OR sl.solicitation_number ILIKE %s "
            "OR sl.purchase_request_number ILIKE %s OR sl.part_number_raw ILIKE %s "
            "OR EXISTS (SELECT 1 FROM sources_canonical sc4 WHERE sc4.nsn = sl.nsn AND (sc4.cage_code ILIKE %s OR sc4.company_name ILIKE %s OR sc4.part_number ILIKE %s)) "
            "OR EXISTS (SELECT 1 FROM dla_award_history dah4 WHERE dah4.nsn = sl.nsn AND dah4.winning_cage ILIKE %s)"
            ")"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like, like, like, like, like])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sort_col = SAFE_SORT_COLUMNS.get(sort_by, SAFE_SORT_COLUMNS["return_by_date"])
    sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

    offset = (max(page, 1) - 1) * page_size

    conn = get_connection()
    try:
        cur = get_dict_cursor(conn)

        count_sql = f"""
            SELECT COUNT(*) AS n
            FROM solicitation_lines sl
            JOIN solicitations s ON s.solicitation_number = sl.solicitation_number
            {where_sql}
        """
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()["n"]

        query = f"""
            {_BASE_SELECT}
            {where_sql}
            ORDER BY {sort_col} {sort_dir_sql} NULLS LAST, sl.id ASC
            LIMIT %s OFFSET %s
        """
        cur.execute(query, tuple(params) + (page_size, offset))
        rows = cur.fetchall()

        for r in rows:
            r["set_aside_label"] = SET_ASIDE_LABELS.get(r["set_aside_indicator"], r["set_aside_indicator"])

        return {"rows": rows, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


def get_rfq_detail(line_id):
    conn = get_connection()
    try:
        cur = get_dict_cursor(conn)
        cur.execute(f"{_BASE_SELECT} WHERE sl.id = %s", (line_id,))
        row = cur.fetchone()
        if row is None:
            return None
        row["set_aside_label"] = SET_ASIDE_LABELS.get(row["set_aside_indicator"], row["set_aside_indicator"])

        cur.execute(
            """
            SELECT cage_code, part_number, company_name, in_reference, in_as, approved
            FROM sources_canonical
            WHERE nsn = %s
            ORDER BY approved DESC, company_name ASC
            LIMIT 50
            """,
            (row["nsn"],),
        )
        row["mcrl_sources"] = cur.fetchall()

        # Full award history (not capped tight - this is meant to scroll),
        # with vendor company name and a source tag per row.
        cur.execute(
            """
            SELECT
                dah.award_number, dah.contract_number, dah.award_date, dah.winning_cage,
                v.company_name AS winning_vendor_name,
                dah.quantity, dah.unit_price, dah.total_award_value,
                dah.solicitation_number, dah.source_year, dah.source_type, dah.source_file
            FROM dla_award_history dah
            LEFT JOIN vendors v ON v.cage_code = dah.winning_cage
            WHERE dah.nsn = %s
            ORDER BY dah.award_date DESC
            LIMIT 500
            """,
            (row["nsn"],),
        )
        row["procurement_history"] = cur.fetchall()

        cur.execute(
            """
            SELECT
                COUNT(*) AS award_count,
                COUNT(DISTINCT winning_cage) AS distinct_vendor_count,
                MIN(unit_price) AS lowest_unit_price,
                MAX(unit_price) AS highest_unit_price,
                AVG(unit_price) AS average_unit_price
            FROM dla_award_history
            WHERE nsn = %s
            """,
            (row["nsn"],),
        )
        row["history_stats"] = cur.fetchone()

        cur.execute(
            """
            SELECT DISTINCT ON (winning_cage) winning_cage, v.company_name
            FROM dla_award_history dah
            LEFT JOIN vendors v ON v.cage_code = dah.winning_cage
            WHERE dah.nsn = %s
            ORDER BY winning_cage, award_date DESC
            """,
            (row["nsn"],),
        )
        row["historical_vendors"] = cur.fetchall()

        cur.execute(
            """
            SELECT id, note, created_at
            FROM notes
            WHERE solicitation_line_id = %s
            ORDER BY created_at DESC
            """,
            (line_id,),
        )
        row["notes"] = cur.fetchall()

        cur.execute(
            """
            SELECT id, quoted_by, quoted_price, quoted_date, created_at
            FROM quotes
            WHERE nsn = %s
            ORDER BY quoted_date DESC, created_at DESC
            LIMIT 20
            """,
            (row["nsn"],),
        )
        row["quote_history"] = cur.fetchall()

        cur.execute(
            "SELECT b.id, b.name FROM baskets b JOIN basket_items bi ON bi.basket_id = b.id WHERE bi.solicitation_line_id = %s",
            (line_id,),
        )
        row["baskets"] = cur.fetchall()

        return row
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def set_quoted(line_id, quoted):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE solicitation_lines SET quoted = %s WHERE id = %s",
            (bool(quoted), line_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_note(line_id, note):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notes (solicitation_line_id, note) VALUES (%s, %s)",
            (line_id, note),
        )
        conn.commit()
    finally:
        conn.close()


def add_quote(line_id, nsn, quoted_by, quoted_price, quoted_date):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO quotes (solicitation_line_id, nsn, quoted_by, quoted_price, quoted_date)
            VALUES (%s, %s, %s, %s, COALESCE(%s, CURRENT_DATE))
            """,
            (line_id, nsn, quoted_by, quoted_price, quoted_date),
        )
        # also flip the quoted flag on so it shows up consistently
        cur.execute("UPDATE solicitation_lines SET quoted = TRUE WHERE id = %s", (line_id,))
        conn.commit()
    finally:
        conn.close()


def add_to_basket(line_id, basket_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO basket_items (basket_id, solicitation_line_id)
            VALUES (%s, %s)
            ON CONFLICT (basket_id, solicitation_line_id) DO NOTHING
            """,
            (basket_id, line_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_from_basket(line_id, basket_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM basket_items WHERE basket_id = %s AND solicitation_line_id = %s",
            (basket_id, line_id),
        )
        conn.commit()
    finally:
        conn.close()
