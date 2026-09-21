# Noviq DLA Brain — Data Layer Foundation

This is Phase 1 of the full spec: PostgreSQL schema + working importers
for the two source files we have real examples of (IN and AS), built
and tested against your actual uploaded files (430 real IN rows, 378
real AS rows — all parsed correctly).

## What's in here

```
noviq-dla-brain/
├── schema.sql       # Run once to create every table (staging + normalized)
├── db.py            # PostgreSQL connection helper
├── normalize.py      # NSN/FSC/NIIN split + date-status classification
├── import_in.py       # Imports IN*.TXT -> solicitations + solicitation_lines
├── import_as.py        # Imports AS*.xlsx -> sources_canonical (dedup logic)
├── import_bq.py          # Imports BQ*.xlsx -> joins onto solicitation_lines
├── nightly_run.py         # Automation harness - runs all imports on a schedule
└── requirements.txt
```

## Step 1 — Create a free Neon PostgreSQL database (no local install needed)

We're using **Neon** (neon.tech) instead of installing PostgreSQL on your
PC — this sidesteps the Windows installer entirely, and since your team
needs shared access eventually anyway, this is a better fit than a
database that only lives on one computer.

1. Go to **https://neon.tech** and sign up (free, no credit card needed)
2. Create a new project — Neon will auto-create a database for you
3. On your project's dashboard, find the **Connection Details** panel.
   You'll see a connection string that looks like:
   ```
   postgresql://neondb_owner:AbCdEf123456@ep-cool-name-12345678.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   Break that down into its pieces (you'll need each one separately):
   - **user** = the part before the colon after `postgresql://` (e.g. `neondb_owner`)
   - **password** = the part after that colon, before the `@` (e.g. `AbCdEf123456`)
   - **host** = the part after `@`, before the next `/` (e.g. `ep-cool-name-12345678.us-east-2.aws.neon.tech`)
   - **database** = the part after that `/`, before the `?` (e.g. `neondb`)

## Step 2 — Create the tables using Neon's built-in SQL Editor

No `psql` needed. In the Neon dashboard, click **SQL Editor** in the
left sidebar. Open `schema.sql` from this project in a text editor,
copy its entire contents, paste them into Neon's SQL Editor, and click
**Run**. You should see a success message for each `CREATE TABLE` /
`CREATE INDEX` statement.

## Step 3 — Install Python dependencies

In this project folder:
```
pip install -r requirements.txt
```

We use `pg8000` here instead of the more common `psycopg2` — deliberately,
because `psycopg2` needs a prebuilt installer file matching your exact
Python version, and very new Python versions sometimes don't have one
yet (we hit this twice already with other packages). `pg8000` is pure
Python and always installs cleanly — no compiler needed.

## Step 4 — Set your connection details for this terminal session

Using the pieces you broke out in Step 1:

```
$env:NOVIQ_DB_HOST = "ep-cool-name-12345678.us-east-2.aws.neon.tech"
$env:NOVIQ_DB_NAME = "neondb"
$env:NOVIQ_DB_USER = "neondb_owner"
$env:NOVIQ_DB_PASSWORD = "AbCdEf123456"
$env:NOVIQ_DB_SSL = "true"
```

(Use your own real values, not the examples above. You'll need to run
these 5 lines again each time you open a new terminal window.)

## Step 5 — Run the importers

The automated fetch (below) is now working and confirmed — but you can
also run these manually against any file:

```
python import_in.py in260916.txt
python import_as.py as260916.txt
python import_bq.py bq260916.txt
```

Both the real raw formats (what the automated fetch produces) and the
`.xlsx` files your colleague sent manually are supported — the scripts
detect which one they're looking at automatically.

Each prints a summary of what it did — how many new solicitations,
how many lines were updated, how many sources matched vs. were newly
created, and (for BQ) how many rows matched an existing line versus
were orphaned.

## Automated daily fetching — CONFIRMED WORKING

`fetch_daily_files.py` downloads IN and BQ (which bundles AS inside
its zip) directly from DIBBS, fully automated:

```
python fetch_daily_files.py                # fetches today's files
python fetch_daily_files.py 2026-09-16      # a specific date
```

Real URL pattern (confirmed live): `https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/{prefix}{YYMMDD}.{ext}`
(`in...txt`, `bq...zip` — AS comes bundled inside the BQ zip).

This subdomain enforces its own DoD warning page (separate from
`www.dibbs.bsm.dla.mil`) - handled automatically via a one-time
Playwright session per run, then handed off to a plain `requests`
session for the actual (sometimes large) file transfer.

**CA ("Current Awards") is intentionally excluded by default** - it's
a separate item in the spec, unconfirmed as actually needed, and has
been a large (~960MB) and slow/unreliable download. Add `--with-ca` to
include it once that's confirmed as necessary.

`nightly_run.py` ties this together end to end: fetch → import IN →
import AS → import BQ, fully automated. Run it manually to test:
```
python nightly_run.py
```

**To schedule it to run automatically every night:**

1. Open **Task Scheduler** (search for it in the Start Menu)
2. Click **Create Basic Task**
3. Name it "Noviq Nightly Import", pick a nightly time (e.g. 2:00 AM)
4. Action: **Start a program**
   - Program: the full path to `python.exe`
   - Arguments: `nightly_run.py`
   - Start in: the full path to this project folder
5. On the **Settings** tab, you'll also want to set the 5 `NOVIQ_DB_*`
   environment variables so the scheduled task can see them — the
   simplest way is to set them as permanent **System Environment
   Variables** (search "Edit the system environment variables" in the
   Start Menu) rather than only in a terminal session, since a
   scheduled task doesn't have a terminal session to inherit from.
6. Check `nightly_run.log` in this folder each morning to confirm it
   ran, or see what broke.

## What's already verified

- **IN file**: the fixed-width layout (140 chars/line) confirmed
  against two real files on different dates (430 and 3,758 rows) —
  every field parses cleanly, including a rare edge case (a non-numeric
  "NSN" field despite being flagged as a real NSN) handled gracefully
  rather than crashing.
- **AS file**: confirmed against the REAL raw format (quoted CSV, no
  header, 4 columns: NSN/CAGE/Part Number/Company Name) - 4,256 of
  4,267 real rows parsed; 11 rows with a non-standard identifier
  (e.g. "1680LN0033448") correctly skipped and counted rather than
  crashing. Company name is genuinely blank in the real file for every
  row - not a bug (will fill in once a real Vendor file is imported).
- **BQ file**: confirmed against the REAL raw format (quoted CSV, no
  header, 121 columns, exact same order as the `.xlsx` your colleague
  sent) - 3,918 real rows parsed cleanly. Cross-verified against IN
  for the same date: BQ's Purchase Request Number for solicitation
  SPE1C126Q0504 (`7017447616`) matches IN's exactly, proving the join
  back onto `solicitation_lines` works correctly on real data.
- **NSN normalization**: tested against real values — `8455008917538`
  correctly splits into FSC `8455` / NIIN `008917538`, leading zero
  preserved (stored as TEXT, never converted to a number).
- **Date-status classification** (CURRENT/FUTURE/OLD_POSTED): logic
  matches the spec's rules exactly, tested with real dates.
- **Automated fetching**: IN and BQ (with AS bundled) download
  end-to-end with zero manual steps, confirmed against a live run.

## What's NOT done yet (needs real example files from your colleague)

- **PUB LOG FLIS Reference** — feeds `staging_reference` /
  `sources_canonical.in_reference`. The page he gave us
  (dla.mil FLIS reading room) is public with no bot-protection, but
  didn't show direct file links on a first check - needs another look.
- **Vendor** — feeds the `vendors` table (this will also fill in the
  blank company names in `sources_canonical`)
- **ContractHist** (all years + current) — feeds `dla_award_history`
- **DIBBS Current Awards** — feeds `dla_award_history`. Possibly the
  "ca" file we found (unconfirmed - deliberately not fetched by
  default, see above) or possibly something else entirely - worth
  confirming with your colleague.
- **PUB LOG Management** — feeds `management_price`
- **Estimated Value engine** (Rules 1–3) — needs award history and
  management price populated first
- **Date-status tabs + Flask/UI wiring** — connecting this database to
  the dashboard we built earlier

## Running automatically every night (Windows Task Scheduler)

1. Open **Task Scheduler** (search for it in the Start menu).
2. Click **Create Basic Task...** on the right.
3. Name it something like "Noviq DLA Brain - Nightly Pipeline", click Next.
4. Trigger: choose **Daily**, pick a time when your PC is normally on and
   idle (e.g. 3:00 AM), click Next.
5. Action: choose **Start a program**, click Next.
6. Program/script: click **Browse...** and select `run_nightly.bat` in this
   project folder. Leave "Add arguments" and "Start in" blank (the .bat
   file sets its own folder). Click Next, then **Finish**.
7. That's it - every night at the time you picked, `run_nightly.bat` will
   run the pipeline (fetch yesterday's files, fall back to the live RFQ
   listing if the real file isn't there yet, then import everything) with
   nobody needing to be at the computer.

To check it's working: after it's run at least once, open
`nightly_task_scheduler.log` (a plain text file in this same folder) or
`nightly_run.log` for the detailed, line-by-line version - or use the
Admin page described below, which shows the same log in your browser.

**Testing it right now, without waiting for 3 AM:** double-click
`run_nightly.bat` yourself, or right-click the task in Task Scheduler and
choose "Run".

## The Admin page (run it / see logs from the website itself)

This only works when you run the website on your own PC (it needs
Playwright and a route to DIBBS, neither of which the public Render copy
has) - it's automatically OFF there, nothing to configure.

To use it locally:
1. In PowerShell, in this project folder, set one more environment
   variable before starting the site:
   ```
   $env:NOVIQ_ENABLE_ADMIN = "true"
   ```
   (along with the NOVIQ_DB_* variables you already set for the database.)
2. Run `python app.py` like normal.
3. Open `http://localhost:5000/admin` in your browser.

From there you can:
- Click **"Run tonight's pipeline"** to run it right now instead of
  waiting for Task Scheduler.
- Pick any date and click **"Run for this date"** to backfill it - if
  DIBBS's file for that date is missing, it automatically falls back to
  the live RFQ listing scrape, safely (no duplicates once the real file
  arrives later).
- Watch the same plain-English log the automatic nightly run writes to,
  right there in the page, refreshing itself while a run is in progress.
