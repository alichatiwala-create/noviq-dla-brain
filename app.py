import os
import threading
from datetime import date, datetime

from flask import Flask, jsonify, request, send_from_directory

import database

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------------------------------------------------------------------------
# Admin panel (run the pipeline / see logs from the website itself)
# ---------------------------------------------------------------------------
# This ONLY works on a machine that has Playwright installed and can reach
# DIBBS - i.e. your own PC, not the public Render-hosted copy (Render's
# requirements-deploy.txt deliberately leaves Playwright out, and Render's
# servers can't reach DIBBS anyway). So this whole panel is OFF by default,
# and only turns on when NOVIQ_ENABLE_ADMIN=true is set in your own
# PowerShell session before running app.py locally - it stays off on Render
# automatically, with nothing extra to configure there.
ADMIN_ENABLED = os.environ.get("NOVIQ_ENABLE_ADMIN", "false").strip().lower() == "true"

_admin_lock = threading.Lock()   # only one pipeline run at a time
_admin_running = {"active": False, "started_at": None}

if ADMIN_ENABLED:
    import db as legacy_db
    import nightly_run
    LOG_PATH = os.path.join(os.path.dirname(__file__), "nightly_run.log")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.route("/api/meta")
def api_meta():
    try:
        return jsonify(database.get_meta())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Baskets
# ---------------------------------------------------------------------------

@app.route("/api/baskets", methods=["GET"])
def api_list_baskets():
    try:
        return jsonify(database.list_baskets())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/baskets", methods=["POST"])
def api_create_basket():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Basket name is required"}), 400
    try:
        basket_id = database.create_basket(name)
        return jsonify({"id": basket_id, "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# RFQs
# ---------------------------------------------------------------------------

def _float_arg(name):
    v = request.args.get(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _list_arg(name):
    """Reads a repeated query param, e.g. ?amsc=G&amsc=C, or a comma-separated one."""
    values = request.args.getlist(name)
    if not values:
        return []
    if len(values) == 1 and "," in values[0]:
        return [v.strip() for v in values[0].split(",") if v.strip()]
    return [v for v in values if v]


@app.route("/api/rfqs")
def api_list_rfqs():
    try:
        filters = {
            "tab": request.args.get("tab", "CURRENT"),
            "amsc": _list_arg("amsc"),
            "set_aside": _list_arg("set_aside"),
            "cage": _list_arg("cage"),
            "basket_id": request.args.get("basket_id") or None,
            "search": request.args.get("search") or None,
            "nsn": request.args.get("nsn") or None,
            "est_value_min": _float_arg("est_value_min"),
            "est_value_max": _float_arg("est_value_max"),
            "return_by_from": request.args.get("return_by_from") or None,
            "return_by_to": request.args.get("return_by_to") or None,
            "delivery_days_min": _float_arg("delivery_days_min"),
            "delivery_days_max": _float_arg("delivery_days_max"),
            "mcrl_count_min": _float_arg("mcrl_count_min"),
            "last_award_from": request.args.get("last_award_from") or None,
            "last_award_to": request.args.get("last_award_to") or None,
            "last_unit_price_min": _float_arg("last_unit_price_min"),
            "last_unit_price_max": _float_arg("last_unit_price_max"),
        }
        quoted_param = request.args.get("quoted")
        if quoted_param is not None:
            filters["quoted"] = quoted_param.lower() == "true"

        sort_by = request.args.get("sort_by", "return_by_date")
        sort_dir = request.args.get("sort_dir", "asc")
        page = int(request.args.get("page", 1))
        page_size = min(int(request.args.get("page_size", 50)), 200)

        result = database.list_rfqs(filters, sort_by, sort_dir, page, page_size)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rfqs/<int:line_id>")
def api_get_rfq(line_id):
    try:
        row = database.get_rfq_detail(line_id)
        if row is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(row)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rfqs/<int:line_id>/status", methods=["POST"])
def api_set_status(line_id):
    data = request.get_json(force=True)
    try:
        database.set_quoted(line_id, data.get("quoted", True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rfqs/<int:line_id>/notes", methods=["POST"])
def api_add_note(line_id):
    data = request.get_json(force=True)
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify({"error": "Note text is required"}), 400
    try:
        database.add_note(line_id, note)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rfqs/<int:line_id>/quotes", methods=["POST"])
def api_add_quote(line_id):
    data = request.get_json(force=True)
    nsn = (data.get("nsn") or "").strip()
    if not nsn:
        return jsonify({"error": "nsn is required"}), 400
    try:
        database.add_quote(
            line_id,
            nsn,
            (data.get("quoted_by") or "").strip() or None,
            data.get("quoted_price"),
            data.get("quoted_date") or None,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/basket-items", methods=["POST"])
def api_add_basket_item():
    data = request.get_json(force=True)
    try:
        database.add_to_basket(data["line_id"], data["basket_id"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/basket-items", methods=["DELETE"])
def api_remove_basket_item():
    data = request.get_json(force=True)
    try:
        database.remove_from_basket(data["line_id"], data["basket_id"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    return jsonify({
        "error": (
            "Live scraping has been retired. Data now comes from the "
            "noviq-dla-brain import pipeline (IN/AS/BQ files), which runs "
            "nightly. Open the Admin page (only available when you run "
            "this website on your own PC) to trigger it, or wait for the "
            "scheduled task, then refresh this page."
        )
    }), 400


# ---------------------------------------------------------------------------
# Admin: run the pipeline / view logs from the website (local PC only)
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_page():
    if not ADMIN_ENABLED:
        return (
            "Admin panel is off. It only works on your own PC (not on the public "
            "Render website), and you turn it on by setting NOVIQ_ENABLE_ADMIN=true "
            "before running 'python app.py' locally. See README.md.",
            404,
        )
    return send_from_directory(app.static_folder, "admin.html")


@app.route("/api/admin/status")
def api_admin_status():
    return jsonify({
        "enabled": ADMIN_ENABLED,
        "running": _admin_running["active"],
        "started_at": _admin_running["started_at"],
    })


def _guard_admin():
    if not ADMIN_ENABLED:
        return jsonify({"error": "Admin panel is disabled on this deployment."}), 403
    return None


@app.route("/api/admin/log")
def api_admin_log():
    guard = _guard_admin()
    if guard:
        return guard
    n = min(int(request.args.get("lines", 200)), 2000)
    try:
        if not os.path.exists(LOG_PATH):
            return jsonify({"lines": []})
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return jsonify({"lines": [l.rstrip("\n") for l in all_lines[-n:]]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_pipeline_in_background(target_date):
    try:
        _admin_running["active"] = True
        _admin_running["started_at"] = datetime.now().isoformat()
        if target_date is None:
            nightly_run.run_nightly()
        else:
            nightly_run.run_for_date(target_date)
    finally:
        _admin_running["active"] = False
        _admin_lock.release()


@app.route("/api/admin/run", methods=["POST"])
def api_admin_run():
    guard = _guard_admin()
    if guard:
        return guard

    if not _admin_lock.acquire(blocking=False):
        return jsonify({"error": "A pipeline run is already in progress. Check the log."}), 409

    data = request.get_json(force=True, silent=True) or {}
    date_str = (data.get("date") or "").strip()
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            _admin_lock.release()
            return jsonify({"error": "Date must be in YYYY-MM-DD format."}), 400

    thread = threading.Thread(target=_run_pipeline_in_background, args=(target_date,), daemon=True)
    thread.start()

    return jsonify({
        "ok": True,
        "message": (
            f"Started running the pipeline for {target_date} - check the log below for progress."
            if target_date else
            "Started running tonight's pipeline (yesterday's date) - check the log below for progress."
        ),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug, host="0.0.0.0", port=port)
