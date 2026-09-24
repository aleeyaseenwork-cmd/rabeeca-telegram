"""
Dashboard backend.
Serves the password-protected dashboard UI and its JSON API.
Data access goes through database/dal.py, which picks SQLite or MongoDB
based on DB_BACKEND in .env - nothing in this file needs to change either way.
Run with: python app.py
"""
import os
import sys
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template, redirect,
    url_for, session, Response
)
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import dal

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "")

DURATION_DAYS = {"weekly": 7, "monthly": 30, "onetime": 36500}  # ~100yr for lifetime

dal.init_db()


# ---------------------------------------------------------------- auth ----

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------- pages ------

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", title=dal.get_setting("dashboard_title", "Dashboard"))


# --------------------------------------------------------- api: stats -----

@app.route("/api/stats")
@login_required
def api_stats():
    stats = dal.get_stats()
    stats["currency_symbol"] = dal.get_setting("currency_symbol", "$")
    return jsonify(stats)


# ----------------------------------------------------- api: subscribers ---

@app.route("/api/subscribers")
@login_required
def api_subscribers():
    status = request.args.get("status", "")
    pay_status = request.args.get("pay_status", "")
    search = request.args.get("search", "").strip()
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 30))

    result = dal.get_subscribers(status, pay_status, search, page, page_size)
    return jsonify({
        "rows": result["rows"],
        "total": result["total"],
        "page": page,
        "page_size": page_size,
    })


@app.route("/api/subscribers/<sub_id>", methods=["PATCH"])
@login_required
def api_update_subscriber(sub_id):
    data = request.get_json(force=True)
    ok = dal.update_subscriber(sub_id, data)
    if not ok:
        return jsonify({"error": "nothing to update"}), 400
    return jsonify({"ok": True})


@app.route("/api/subscribers/export.csv")
@login_required
def export_csv():
    status = request.args.get("status", "")
    pay_status = request.args.get("pay_status", "")
    search = request.args.get("search", "").strip()

    rows = dal.export_subscribers(status, pay_status, search)

    import csv
    import io
    output = io.StringIO()
    if rows:
        fieldnames = ["id", "telegram_user_id", "telegram_username", "button_text", "duration_type",
                      "price", "currency", "status", "auto_renew", "start_date", "expiry_date",
                      "provider", "last_payment_status"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("no data\n")

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers_export.csv"},
    )


# ----------------------------------------------------------- api: panels --

@app.route("/api/panels", methods=["GET"])
@login_required
def api_panels():
    return jsonify(dal.get_panels())


@app.route("/api/panels", methods=["POST"])
@login_required
def api_create_panel():
    data = request.get_json(force=True)
    dal.create_panel(
        data.get("name", "Untitled Panel"),
        data.get("message_text", "Choose a plan below."),
        data.get("buttons", []),
    )
    return jsonify({"ok": True})


@app.route("/api/panels/<panel_id>", methods=["PUT"])
@login_required
def api_update_panel(panel_id):
    data = request.get_json(force=True)
    dal.update_panel(
        panel_id,
        data.get("name"),
        data.get("message_text"),
        data.get("buttons"),
        data.get("active", 1),
    )
    return jsonify({"ok": True})


@app.route("/api/panels/<panel_id>", methods=["DELETE"])
@login_required
def api_delete_panel(panel_id):
    dal.delete_panel(panel_id)
    return jsonify({"ok": True})


# --------------------------------------------------------- api: settings --

@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    d = dal.get_all_settings()
    d["admin_ids"] = dal.get_admin_ids()
    return jsonify(d)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_set_settings():
    data = request.get_json(force=True)
    for k, v in data.items():
        if k == "admin_ids":
            continue
        dal.set_setting(k, v)
    if "admin_ids" in data:
        dal.set_admin_ids(data["admin_ids"])
    return jsonify({"ok": True})


# ------------------------------------------------------- api: dev / json --

@app.route("/api/dev/table/<table>")
@login_required
def api_dev_table(table):
    try:
        return jsonify(dal.get_dev_table(table))
    except ValueError:
        return jsonify({"error": "invalid table"}), 400


@app.route("/checkout/success")
def checkout_success():
    return "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>" \
           "<h2>Payment received</h2><p>You can close this tab and return to Telegram.</p></body></html>"


@app.route("/checkout/cancel")
def checkout_cancel():
    return "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>" \
           "<h2>Payment cancelled</h2><p>No charge was made. Go back to Telegram and /start again to retry.</p></body></html>"


# -------------------------------------------------- stripe webhook (stub) -

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """
    Receives Stripe events. In test/dev this just logs; wire up real
    signature verification with STRIPE_WEBHOOK_SECRET before going live.
    See README "Payments setup" section for the exact steps.
    """
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "")

    if event_type == "checkout.session.completed":
        session_obj = payload.get("data", {}).get("object", {})
        telegram_user_id = session_obj.get("client_reference_id", "unknown")
        amount = (session_obj.get("amount_total", 0) or 0) / 100

        # find the pending subscriber this payment belongs to, so we can
        # actually link the payment record and flip them to active
        pending = dal.get_latest_pending_subscriber(telegram_user_id)
        sub_id = pending["id"] if pending else None

        dal.record_payment_success(telegram_user_id, amount, "stripe", session_obj.get("id", ""), subscriber_id=sub_id)

        if pending:
            days = DURATION_DAYS.get(pending.get("duration_type"), 30)
            expiry = (datetime.utcnow() + timedelta(days=days)).isoformat()
            dal.update_subscriber(sub_id, {"status": "active", "expiry_date": expiry})
            _notify_and_invite(telegram_user_id)

    elif event_type == "checkout.session.async_payment_failed":
        session_obj = payload.get("data", {}).get("object", {})
        telegram_user_id = session_obj.get("client_reference_id", "unknown")
        pending = dal.get_latest_pending_subscriber(telegram_user_id)
        sub_id = pending["id"] if pending else None
        dal.record_payment_failed(telegram_user_id, "stripe", "payment declined", subscriber_id=sub_id)

    return jsonify({"received": True})


def _notify_and_invite(telegram_user_id):
    """Sends the user a group invite link once their payment is confirmed."""
    if not BOT_TOKEN or not GROUP_CHAT_ID:
        return
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            bot = Bot(token=BOT_TOKEN)
            invite = await bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1)
            await bot.send_message(
                chat_id=int(telegram_user_id),
                text=f"Payment confirmed! Join the group here:\n{invite.invite_link}",
            )

        asyncio.run(_send())
    except Exception as e:
        app.logger.warning("Could not send invite to %s: %s", telegram_user_id, e)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
