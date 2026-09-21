# Setting up the rebuilt dashboard — do this in order

This is a full rebuild of the dashboard to match the mockup layout, using your
real Neon database (the same one noviq-dla-brain's import scripts write into).

## 1. Copy these files into your existing project folder

Your existing project is at:
`C:\Users\alich\OneDrive\Documents\dibbs-rfq-dashboard`

Replace/add these files there with the new versions from this package:
- `app.py`
- `database.py`
- `static\index.html`
- `static\app.js`
- `static\style.css`
- `requirements.txt`
- `requirements-deploy.txt`
- `Procfile`
- `migration_baskets.sql`  (new file)

Your `scraper.py` and old SQLite code are no longer used by the app — you can
leave `scraper.py` in the folder untouched, it's just dead weight now, or
delete it later.

## 2. Run the one-time database migration

This adds support for **named baskets** (like "SBTD26") instead of one
generic basket, and makes sure the `notes` / `quoted` pieces exist.

Open your Neon project's **SQL Editor** (same place you've run other SQL
before) and paste in the entire contents of `migration_baskets.sql`, then
run it. It's safe to run more than once.

## 3. Make sure your environment variables are still set

These are the same 5 you already set up for noviq-dla-brain — the dashboard
uses the exact same ones, so if `nightly_run.py` already works, you're done
with this step:
- `NOVIQ_DB_HOST`
- `NOVIQ_DB_PORT`
- `NOVIQ_DB_NAME`
- `NOVIQ_DB_USER`
- `NOVIQ_DB_PASSWORD`
- `NOVIQ_DB_SSL` = `true`

## 4. Install dependencies and run it

In VS Code's terminal, inside the dashboard folder:

```powershell
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## 5. Create your first basket

The Basket dropdown starts empty (aside from "All"). Baskets are created via
a quick one-time SQL insert until we build a proper "+ New Basket" button —
in the Neon SQL editor:

```sql
INSERT INTO baskets (name) VALUES ('SBTD26') ON CONFLICT DO NOTHING;
```

Run that once per basket name you want (e.g. `Default`, `SBTD26`, whatever
your team uses). Refresh the dashboard and it'll show up in the dropdown.

## What's real vs. still placeholder

**Real, live from your actual data:**
- Every RFQ row (from IN/BQ), NSN, description, qty, AMSC, set aside, return
  by date, status tabs (Current / Future / Old-Extended)
- MCRL / Qualified Sources count and list (from AS)
- Last Award Date/Price and Hist. Vendors count — these will show real
  numbers **once you've imported ContractHist / Current Awards**; until then
  they'll correctly show "—" rather than fake data
- Estimated Value — this is now actually calculated live (Rule 1: last award
  price × qty, Rule 2: management price × qty, Rule 3: N/A), with the
  **Basis always shown** under it in the detail panel, exactly like the spec
  asked for. It'll show "N/A" for everything until award/management data
  exists, which is expected and honest, not a bug.
- Quoted flag, notes, and basket membership are fully working right now.

**Still blank on purpose (not available from any source we have yet):**
- Price Reason Code, AAC, SOS, SOSM, UI, SLC, CIIC, Management Control,
  Technical Document — these are DIBBS-native fields that only exist on
  DIBBS's own RFQ detail page, not in IN/AS/BQ. They show "—" with a note
  explaining why, right in the Additional Information card.

Once Reference/Vendor/ContractHist/Management are imported, Last Award,
Hist. Vendors, and Estimated Value will start showing real numbers
automatically — no code changes needed, the queries already join against
those tables.
