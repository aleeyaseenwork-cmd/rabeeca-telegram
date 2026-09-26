"""
Dashboard backend.
Serves the password-protected dashboard UI and its JSON API.
Data access goes through database/dal.py, which picks SQLite or MongoDB
based on DB_BACKEND in .env - nothing in this file needs to change either way.
Run with: python app.py
"""
import os
import sys
import csv
import io
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template, redirect,
    url_for, session, Response
)
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import dal

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

DASHBOARD_PASSWORD_ENV = os.environ.get("DASHBOARD_PASSWORD", "changeme")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "")

dal.init_db()

CSV_FIELDS = [
    "id", "telegram_user_id", "telegram_username", "button_text", "duration_type",
    "custom_days", "price", "currency", "status", "auto_renew", "purchase_count", "link_sent",
    "start_date", "expiry_date", "provider", "last_payment_status",
]


# ---------------------------------------------------------------- auth ----

def _current_password_hash():
    """A password set from the dashboard (Settings > Change password) always
    wins over the .env value, so the change actually sticks between deploys."""
    return dal.get_setting("dashboard_password_hash", "")


def _check_password(pw):
    stored_hash = _current_password_hash()
    if stored_hash:
        return check_password_hash(stored_hash, pw)
    return pw == DASHBOARD_PASSWORD_ENV


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
        if _check_password(pw):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/change-password", methods=["POST"])
@login_required
def api_change_password():
    data = request.get_json(force=True)
    current = data.get("current_password", "")
    new = data.get("new_password", "")
    if not _check_password(current):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(new) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    dal.set_setting("dashboard_password_hash", generate_password_hash(new))
    return jsonify({"ok": True})


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
    kick_now = bool(data.pop("kick_now", False))

    ok = dal.update_subscriber(sub_id, data)
    if not ok:
        return jsonify({"error": "nothing to update"}), 400

    if kick_now and data.get("status") == "expired":
        _kick_member(sub_id)

    return jsonify({"ok": True})


@app.route("/api/subscribers/<sub_id>", methods=["DELETE"])
@login_required
def api_delete_subscriber(sub_id):
    dal.delete_subscriber(sub_id)
    return jsonify({"ok": True})


@app.route("/api/subscribers/auto-renew-all", methods=["POST"])
@login_required
def api_set_all_auto_renew():
    data = request.get_json(force=True)
    dal.set_all_auto_renew(bool(data.get("value")))
    return jsonify({"ok": True})


@app.route("/api/subscribers/export.csv")
@login_required
def export_csv():
    status = request.args.get("status", "")
    pay_status = request.args.get("pay_status", "")
    search = request.args.get("search", "").strip()

    rows = dal.export_subscribers(status, pay_status, search)

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("no data\n")

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers_export.csv"},
    )


@app.route("/api/subscribers/import.csv", methods=["POST"])
@login_required
def import_csv():
    """
    Bulk-restore subscribers from a CSV in the same shape as the export
    (telegram_user_id is required, everything else optional). Existing rows
    for the same telegram_user_id are updated in place, new ones are created.
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "no file uploaded"}), 400

    try:
        text = file.stream.read().decode("utf-8-sig")
    except Exception:
        return jsonify({"error": "could not read file as UTF-8 CSV"}), 400

    reader = csv.DictReader(io.StringIO(text))
    imported = 0
    skipped = 0
    for row in reader:
        telegram_user_id = (row.get("telegram_user_id") or "").strip()
        if not telegram_user_id:
            skipped += 1
            continue
        dal.apply_purchase(
            telegram_user_id,
            row.get("telegram_username", ""),
            row.get("button_text", "Imported plan"),
            row.get("duration_type", "monthly"),
            float(row.get("price") or 0),
            row.get("provider", "stripe"),
        )
        imported += 1

    return jsonify({"ok": True, "imported": imported, "skipped": skipped})


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
    d.pop("dashboard_password_hash", None)  # never leak the hash to the frontend
    d["admin_ids"] = dal.get_admin_ids()
    return jsonify(d)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_set_settings():
    data = request.get_json(force=True)
    for k, v in data.items():
        if k in ("admin_ids", "dashboard_password_hash"):
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

    All the purchase logic (new subscriber vs. renewal stacking vs.
    lifetime) lives in dal.apply_purchase — this handler just reads the
    plan back out of Stripe metadata and applies it.
    """
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "")

    if event_type == "checkout.session.completed":
        session_obj = payload.get("data", {}).get("object", {})
        telegram_user_id = session_obj.get("client_reference_id", "unknown")
        amount = (session_obj.get("amount_total", 0) or 0) / 100
        metadata = session_obj.get("metadata", {}) or {}

        button_text = metadata.get("button_text", "Unknown plan")
        duration_type = metadata.get("duration_type", "monthly")
        price = float(metadata.get("price", amount) or amount)
        telegram_username = metadata.get("telegram_username", "")

        sub_id = dal.apply_purchase(
            telegram_user_id, telegram_username, button_text, duration_type, price, "stripe",
        )
        dal.record_payment_success(telegram_user_id, amount, "stripe", session_obj.get("id", ""), subscriber_id=sub_id)

        sent = _notify_and_invite(telegram_user_id)
        dal.update_subscriber(sub_id, {"link_sent": 1 if sent else 0})

    elif event_type == "checkout.session.async_payment_failed":
        session_obj = payload.get("data", {}).get("object", {})
        telegram_user_id = session_obj.get("client_reference_id", "unknown")
        existing = dal.get_subscriber_by_telegram_id(telegram_user_id)
        sub_id = existing["id"] if existing else None
        dal.record_payment_failed(telegram_user_id, "stripe", "payment declined", subscriber_id=sub_id)

    return jsonify({"received": True})


def _kick_member(sub_id):
    """Removes a member from the group/channel right away (used when an
    admin manually marks someone expired and ticks 'remove now')."""
    if not BOT_TOKEN or not GROUP_CHAT_ID:
        return
    sub = dal.get_subscriber(sub_id)
    if not sub:
        return
    try:
        import asyncio
        from telegram import Bot

        async def _kick():
            bot = Bot(token=BOT_TOKEN)
            await bot.ban_chat_member(chat_id=GROUP_CHAT_ID, user_id=int(sub["telegram_user_id"]))
            await bot.unban_chat_member(chat_id=GROUP_CHAT_ID, user_id=int(sub["telegram_user_id"]))

        asyncio.run(_kick())
    except Exception as e:
        app.logger.warning("Could not remove %s from group: %s", sub["telegram_user_id"], e)


def _notify_and_invite(telegram_user_id):
    """Sends the user a one-time group invite link once payment is confirmed.
    Returns True if it was actually delivered, False otherwise (this is
    exactly what the dashboard's Link Sent column reflects)."""
    if not BOT_TOKEN or not GROUP_CHAT_ID:
        return False
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            bot = Bot(token=BOT_TOKEN)
            invite = await bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1)
            await bot.send_message(
                chat_id=int(telegram_user_id),
                text=f"✅ *Payment confirmed!*\n\n🎉 Welcome aboard — tap below to join:\n{invite.invite_link}",
                parse_mode="Markdown",
            )

        asyncio.run(_send())
        return True
    except Exception as e:
        app.logger.warning("Could not send invite to %s: %s", telegram_user_id, e)
        return False


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
