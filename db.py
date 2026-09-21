"""
db.py
-----
PostgreSQL connection helper. Uses pg8000 - a pure-Python PostgreSQL
driver with no compiled/C extension - deliberately chosen over
psycopg2 because psycopg2 needs a matching prebuilt wheel for your
exact Python version, and (as we've already hit twice with lxml and
greenlet) very new Python versions sometimes don't have one yet,
forcing a from-source build that needs a C++ compiler you likely
don't have. pg8000 sidesteps that risk entirely - it always installs
with a plain `pip install pg8000`.

Reads connection details from environment variables so credentials
never live in code:

    NOVIQ_DB_HOST     (e.g. ep-something.us-east-2.aws.neon.tech for Neon)
    NOVIQ_DB_PORT     (default: 5432)
    NOVIQ_DB_NAME     (default: noviq)
    NOVIQ_DB_USER     (default: postgres)
    NOVIQ_DB_PASSWORD (required - no default, on purpose)
    NOVIQ_DB_SSL      ("true" for Neon/most cloud Postgres, "false" for
                        a local install with no SSL configured - default: false)

Set these once in your terminal session before running any importer:

    Windows (PowerShell):
        $env:NOVIQ_DB_PASSWORD = "your_password_here"
        $env:NOVIQ_DB_HOST = "ep-something.us-east-2.aws.neon.tech"
        $env:NOVIQ_DB_SSL = "true"
"""

import os
import ssl
import pg8000.dbapi as pg8000


def get_connection():
    """Open a new PostgreSQL connection using environment variables."""
    password = os.environ.get("NOVIQ_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "NOVIQ_DB_PASSWORD environment variable is not set. "
            "Set it before running any import script - see the README."
        )

    use_ssl = os.environ.get("NOVIQ_DB_SSL", "false").strip().lower() == "true"
    ssl_context = ssl.create_default_context() if use_ssl else None

    conn = pg8000.connect(
        host=os.environ.get("NOVIQ_DB_HOST", "localhost"),
        port=int(os.environ.get("NOVIQ_DB_PORT", "5432")),
        database=os.environ.get("NOVIQ_DB_NAME", "noviq"),
        user=os.environ.get("NOVIQ_DB_USER", "postgres"),
        password=password,
        ssl_context=ssl_context,
    )
    return conn


def get_dict_cursor(conn):
    """
    pg8000 doesn't have a built-in dict-cursor like psycopg2's
    RealDictCursor. This helper wraps a normal cursor's fetch methods
    to return dicts instead, using cursor.description for column names.
    """
    cur = conn.cursor()

    class DictCursorWrapper:
        def __getattr__(self, name):
            return getattr(cur, name)

        def _row_to_dict(self, row):
            if row is None:
                return None
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, row))

        def fetchone(self):
            return self._row_to_dict(cur.fetchone())

        def fetchall(self):
            return [self._row_to_dict(r) for r in cur.fetchall()]

    return DictCursorWrapper()


def run_with_retries(func, *args, max_retries=3, retry_delay_seconds=5, **kwargs):
    """
    Runs func(*args, **kwargs) - a whole import's run() function - and
    automatically retries it from scratch if the connection drops
    mid-way (a real thing we've hit: a long-lived connection making
    thousands of sequential queries can get reset by the network or
    Neon's own connection handling, especially on a slower/less stable
    connection).

    Safe to just retry the WHOLE thing because our upserts are
    idempotent - re-processing a row that already made it into the
    database just harmlessly updates it again, it doesn't duplicate
    anything or corrupt data.
    """
    import time

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            error_name = type(e).__name__
            looks_like_connection_issue = any(
                keyword in (error_name + str(e)).lower()
                for keyword in ["connection", "network", "reset", "closed", "timeout", "broken pipe"]
            )
            if not looks_like_connection_issue or attempt == max_retries:
                raise
            print(f"\n  Connection issue ({error_name}: {e}) - "
                  f"retrying the whole import (attempt {attempt + 1}/{max_retries}) "
                  f"in {retry_delay_seconds} seconds...\n")
            time.sleep(retry_delay_seconds)

    raise last_error

