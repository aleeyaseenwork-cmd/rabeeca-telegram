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
import hashlib
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template, redirect,
    url_for, session, Response
)
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import dal
from database import slot

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

DASHBOARD_PASSWORD_ENV = os.environ.get("DASHBOARD_PASSWORD", "changeme")
GROUP_CHAT_ID_ENV = os.environ.get("GROUP_CHAT_ID", "")
VEROTEL_SIGNATURE_KEY = os.environ.get("VEROTEL_SIGNATURE_KEY", "")

# One database per bot. Bot 1 is the existing one; bots 2+ appear once BOT_TOKEN_<n> is
# set in .env. Create/upgrade every configured bot's database at startup.
for _n in slot.configured_slots():
    with slot.use(_n):
        dal.init_db()


def _bot_token():
    """Token of the bot currently selected in the dashboard. Read from the server's
    .env only (BOT_TOKEN_1, BOT_TOKEN_2, ...), never stored in the dashboard/database."""
    return slot.bot_token()


@app.before_request
def _select_bot():
    """Every dashboard request works on the bot chosen in the session (default bot 1).
    Webhooks pick their own bot from the payment data instead."""
    n = session.get("bot_slot", 1)
    slot.set_for_request(n if n in slot.configured_slots() else 1)


@app.teardown_request
def _reset_bot(exc=None):
    """Don't let one request's bot choice leak into the next request handled by the same thread."""
    slot.set_for_request(1)


def _group_chat_id():
    """The channel/group the bot manages. Set from Settings in the dashboard
    (so the client controls it herself); the GROUP_CHAT_ID env var is only
    a fallback for before anything has been saved there."""
    env_fallback = GROUP_CHAT_ID_ENV if slot.get() == 1 else ""   # the env fallback belongs to bot 1 only
    return (dal.get_setting("group_chat_id", "") or "").strip() or env_fallback


CSV_FIELDS = [
    "id", "telegram_user_id", "telegram_username", "button_text", "duration_type",
    "custom_days", "price", "currency", "status", "auto_renew", "purchase_count", "link_sent",
    "start_date", "expiry_date", "provider", "last_payment_status", "cancelled",
]


# ---------------------------------------------------------------- auth ----

def _current_password_hash():
    """A password set from the dashboard (Settings > Change password) always
    wins over the .env value, so the change actually sticks between deploys."""
    with slot.use(1):   # one password for the whole dashboard, whichever bot is selected
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
    with slot.use(1):
        dal.set_setting("dashboard_password_hash", generate_password_hash(new))
    return jsonify({"ok": True})


# ------------------------------------------------------------- pages ------

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", title=dal.get_setting("dashboard_title", "Dashboard"))


@app.route("/revenue")
@login_required
def revenue_page():
    return render_template("revenue.html", title=dal.get_setting("dashboard_title", "Dashboard"),
                           bot_label=_bot_label() if len(slot.configured_slots()) > 1 else "")


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
    banned_ids = {str(b["telegram_user_id"]) for b in dal.get_bans()}
    for row in result["rows"]:
        row["banned"] = str(row.get("telegram_user_id")) in banned_ids
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

    # Plan button labels are free text ("1 Month - $25"), so when the currency
    # symbol changes, carry the new symbol into them — otherwise prices on the
    # buttons would keep showing the old currency.
    old_symbol = dal.get_setting("currency_symbol", "$")
    new_symbol = data.get("currency_symbol")
    if new_symbol and new_symbol != old_symbol and old_symbol:
        for panel in dal.get_panels():
            buttons = panel.get("buttons", [])
            for b in buttons:
                b["text"] = b["text"].replace(old_symbol, new_symbol)
            dal.update_panel(panel["id"], panel["name"], panel["message_text"].replace(old_symbol, new_symbol),
                             buttons, panel.get("active", 1))

    for k, v in data.items():
        if k in ("admin_ids", "dashboard_password_hash"):
            continue
        dal.set_setting(k, v)
    if "admin_ids" in data:
        dal.set_admin_ids(data["admin_ids"])
    return jsonify({"ok": True})


# ------------------------------------------------------------ api: bans --

@app.route("/api/bans", methods=["GET"])
@login_required
def api_get_bans():
    return jsonify(dal.get_bans())


@app.route("/api/bans", methods=["POST"])
@login_required
def api_ban_user():
    """Ban by Telegram ID (usernames can be changed, IDs can't). Also cancels
    any existing subscription for that ID and, if kick_now is set, removes
    them from the group/channel right away."""
    data = request.get_json(force=True)
    tg_id = str(data.get("telegram_user_id", "")).strip()
    if not tg_id.lstrip("-").isdigit():
        return jsonify({"error": "Telegram ID must be a number"}), 400

    already_banned = dal.is_banned(tg_id)
    dal.ban_user(tg_id, data.get("reason", ""))

    # Tell them BEFORE any kick. If the DM fails (bot blocked, never started, ...) we
    # just carry on, the ban always completes. Re-banning someone already banned
    # doesn't message them again.
    if not already_banned:
        _send_dm(tg_id, "🚫 You have been banned and your access has been removed.")

    existing = dal.get_subscriber_by_telegram_id(tg_id)
    if existing:
        dal.update_subscriber(existing["id"], {"status": "cancelled", "auto_renew": 0})
        if data.get("kick_now"):
            _kick_member(existing["id"])
    return jsonify({"ok": True})


@app.route("/api/bans/<telegram_user_id>", methods=["DELETE"])
@login_required
def api_unban_user(telegram_user_id):
    """Unbans. If the member still has time left on their plan (or lifetime), their
    status goes back to 'active' and they get a Telegram message with their expiry
    date and a fresh invite link. An already-expired plan is just unbanned.
    auto_renew is deliberately left alone."""
    was_banned = dal.is_banned(telegram_user_id)
    dal.unban_user(telegram_user_id)

    restored = notified = False
    sub = dal.get_subscriber_by_telegram_id(telegram_user_id) if was_banned else None
    if sub and _has_time_left(sub):
        dal.update_subscriber(sub["id"], {"status": "active"})
        restored = True
        notified, link_sent = _notify_unbanned(sub)
        dal.update_subscriber(sub["id"], {"link_sent": 1 if link_sent else 0})
    return jsonify({"ok": True, "restored": restored, "notified": notified})


# --------------------------------------------------- api: revenue range --

@app.route("/api/stats/revenue-range")
@login_required
def api_revenue_range():
    """Revenue between two dates, both inclusive (YYYY-MM-DD)."""
    from datetime import datetime, timedelta
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)  # make end inclusive
    except ValueError:
        return jsonify({"error": "Use YYYY-MM-DD for both dates"}), 400
    if end_dt <= start_dt:
        return jsonify({"error": "End date must be on or after the start date"}), 400

    result = dal.get_revenue_for_range(start_dt.isoformat(), end_dt.isoformat())
    result["payments"] = dal.get_payments_for_range(start_dt.isoformat(), end_dt.isoformat())
    result["currency_symbol"] = dal.get_setting("currency_symbol", "$")
    return jsonify(result)


# ---------------------------------------------------- api: send reminders --

def _send_reminder_message(sub):
    """Sends one expiry reminder via the bot. Returns True if Telegram accepted it."""
    if not _bot_token():
        return False
    try:
        import asyncio
        from telegram import Bot

        expiry = (sub.get("expiry_date") or "")[:10]
        text = (
            f"⏰ *Reminder:* your *{sub.get('button_text', 'subscription')}* plan "
            f"ends on *{expiry}*.\n\nSend /start to renew and keep your access."
        )

        async def _send():
            await Bot(token=_bot_token()).send_message(
                chat_id=int(sub["telegram_user_id"]), text=text, parse_mode="Markdown",
            )

        asyncio.run(_send())
        return True
    except Exception as e:
        app.logger.warning("Could not send reminder to %s: %s", sub.get("telegram_user_id"), e)
        return False


@app.route("/api/subscribers/<sub_id>/remind", methods=["POST"])
@login_required
def api_remind_one(sub_id):
    sub = dal.get_subscriber(sub_id)
    if not sub:
        return jsonify({"error": "not found"}), 404
    ok = _send_reminder_message(sub)
    return jsonify({"ok": ok}), (200 if ok else 502)


@app.route("/api/subscribers/remind-expiring", methods=["POST"])
@login_required
def api_remind_expiring():
    """One-time reminder to everyone whose plan ends within the configured
    'reminder days' window (same group the Expiring stat card counts)."""
    days = int(dal.get_setting("reminder_days_before", "3") or 3)
    sent = failed = 0
    for sub in dal.get_expiring_subscribers(days):
        if _send_reminder_message(sub):
            sent += 1
        else:
            failed += 1
    return jsonify({"ok": True, "sent": sent, "failed": failed})


# ------------------------------------------------------ api: bot web link --

_bot_username_cache = {}   # "username_<slot>" -> @name of each bot
_bot_lookup_failed = {}    # slot -> when a lookup last failed (don't hammer Telegram with a bad token)


def _lookup_bot_username(n=None):
    """@username of bot `n` (default: the selected bot), from its token. Cached."""
    import time
    n = n or slot.get()
    key = f"username_{n}"
    if key in _bot_username_cache:
        return _bot_username_cache[key]
    token = slot.bot_token(n)
    if not token or time.time() - _bot_lookup_failed.get(n, 0) < 60:
        return None
    try:
        import asyncio
        from telegram import Bot

        async def _me():
            return (await Bot(token=token).get_me()).username

        _bot_username_cache[key] = asyncio.run(_me())
        return _bot_username_cache[key]
    except Exception as e:
        _bot_lookup_failed[n] = time.time()
        app.logger.warning("Could not look up the username of bot %s: %s", n, e)
        return None


def _bot_label(n=None):
    n = n or slot.get()
    name = _lookup_bot_username(n)
    return f"Bot {n}" + (f" (@{name})" if name else "")


@app.route("/api/bot-link")
@login_required
def api_bot_link():
    """https://t.me/<bot> — drop this on the website: tapping it opens Telegram
    and starts the bot. Username is looked up from the bot token once."""
    if not _bot_token():
        return jsonify({"error": f"{slot.token_env_name()} is not set"}), 400
    name = _lookup_bot_username()
    if not name:
        return jsonify({"error": "Could not reach Telegram"}), 502
    return jsonify({"username": name, "link": f"https://t.me/{name}"})


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
        _use_bot_from_payment(metadata.get("bot_slot"))   # which bot's database this purchase belongs to

        button_text = metadata.get("button_text", "Unknown plan")
        duration_type = metadata.get("duration_type", "monthly")
        price = float(metadata.get("price", amount) or amount)
        telegram_username = metadata.get("telegram_username", "")
        custom_days = int(metadata["custom_days"]) if metadata.get("custom_days") else None

        sub_id = dal.apply_purchase(
            telegram_user_id, telegram_username, button_text, duration_type, price, "stripe", custom_days,
            session_obj.get("subscription") or None,
        )
        dal.record_payment_success(telegram_user_id, amount, "stripe", session_obj.get("id", ""), subscriber_id=sub_id)

        sent = _notify_and_invite(telegram_user_id)
        dal.update_subscriber(sub_id, {"link_sent": 1 if sent else 0})

    elif event_type == "invoice.paid":
        # A recurring Stripe subscription charging again. (The very first invoice
        # is already handled by checkout.session.completed above, so skip it.)
        inv = payload.get("data", {}).get("object", {})
        if inv.get("billing_reason") == "subscription_cycle":
            member = _find_member_by_stripe_subscription(inv.get("subscription"))
            if member:
                _apply_renewal(member, (inv.get("amount_paid", 0) or 0) / 100, "stripe", inv.get("id", ""))

    elif event_type == "invoice.payment_failed":
        inv = payload.get("data", {}).get("object", {})
        member = _find_member_by_stripe_subscription(inv.get("subscription"))
        if member:
            dal.record_payment_failed(member["telegram_user_id"], "stripe", "renewal payment failed", subscriber_id=member["id"])

    elif event_type == "checkout.session.async_payment_failed":
        session_obj = payload.get("data", {}).get("object", {})
        telegram_user_id = session_obj.get("client_reference_id", "unknown")
        _use_bot_from_payment((session_obj.get("metadata") or {}).get("bot_slot"))
        existing = dal.get_subscriber_by_telegram_id(telegram_user_id)
        sub_id = existing["id"] if existing else None
        dal.record_payment_failed(telegram_user_id, "stripe", "payment declined", subscriber_id=sub_id)

    return jsonify({"received": True})


def _use_bot_from_payment(raw_slot):
    """Payment webhooks aren't logged-in dashboard requests: the checkout carries which
    bot it was for (Stripe metadata "bot_slot"; Verotel custom1 "<slot>:<telegram id>").
    Older checkouts have no slot and belong to bot 1."""
    try:
        n = int(raw_slot) if raw_slot not in (None, "") else 1
    except (TypeError, ValueError):
        n = 1
    slot.set_for_request(n if n in slot.configured_slots() else 1)


def _find_member_by_stripe_subscription(subscription_id):
    """Stripe's recurring-invoice events don't say which bot they belong to, so look the
    subscription up in every bot's database and continue in the one that owns it."""
    if not subscription_id:
        return None
    for n in slot.configured_slots():
        with slot.use(n):
            member = dal.get_subscriber_by_provider_subscription_id(subscription_id)
        if member:
            slot.set_for_request(n)
            return member
    return None


def _apply_renewal(member, amount, provider, ref):
    """A recurring charge succeeded: extend their period using the plan they're
    already on. They're already in the group, so no new invite goes out and the
    'link sent' flag stays exactly as it was."""
    keep_link_flag = member.get("link_sent", 0)
    sub_id = dal.apply_purchase(
        member["telegram_user_id"], member.get("telegram_username", ""), member.get("button_text", "Renewal"),
        member.get("duration_type", "monthly"), member.get("price", amount), provider, member.get("custom_days"),
    )
    dal.update_subscriber(sub_id, {"link_sent": keep_link_flag})
    dal.record_payment_success(member["telegram_user_id"], amount, provider, ref, subscriber_id=sub_id)
    return sub_id


def _verotel_signature(params):
    """Same algorithm as bot/bot.py's verotel_signature — duplicated here
    (rather than imported) so the dashboard process and the bot process
    stay independent of each other."""
    parts = [f"{k}={v}" for k, v in sorted(params.items()) if k != "signature"]
    base = VEROTEL_SIGNATURE_KEY + ":" + ":".join(parts)
    return hashlib.sha1(base.encode()).hexdigest()


@app.route("/webhook/verotel", methods=["GET", "POST"])
def verotel_webhook():
    """
    Receives Verotel/CardBilling (FlexPay) postbacks. Verotel sends these
    as a GET (or sometimes POST) request to the Postback URL configured in
    their Control Center for your shop — set it to:
        https://your-domain.com/webhook/verotel

    Every postback carries the same custom1/2/3 fields the checkout link
    was built with (telegram user id, duration type, custom days), plus a
    signature we verify before trusting anything in the request.
    """
    data = request.values.to_dict()
    received_sig = data.pop("signature", "")

    if not VEROTEL_SIGNATURE_KEY or _verotel_signature(data) != received_sig:
        app.logger.warning("Verotel postback with invalid/missing signature")
        return "invalid signature", 400

    sale_type = data.get("type", "")
    telegram_user_id = data.get("custom1", "unknown")
    if ":" in telegram_user_id:   # "<bot slot>:<telegram id>" (bot 1 sends the plain id, as before)
        slot_part, telegram_user_id = telegram_user_id.split(":", 1)
        _use_bot_from_payment(slot_part)
    else:
        _use_bot_from_payment(1)
    telegram_username = ""  # Verotel doesn't carry this back; already on file from the first purchase
    duration_type = data.get("custom2", "monthly")
    custom_days = int(data["custom3"]) if data.get("custom3") else None
    amount = float(data.get("priceAmount", 0) or 0)
    sale_id = data.get("saleID", "")

    # "purchase"/"subscription" = first charge, "rebill" = a recurring
    # subscription auto-charging again — both activate/extend the user
    if sale_type in ("purchase", "subscription", "rebill", "sale"):
        existing = dal.get_subscriber_by_telegram_id(telegram_user_id)
        if sale_type == "rebill" and existing:
            _apply_renewal(existing, amount, "verotel", sale_id)   # already in the group: just extend
        else:
            sub_id = dal.apply_purchase(
                telegram_user_id, telegram_username, data.get("description", "CardBilling plan"),
                duration_type, amount, "verotel", custom_days,
            )
            dal.record_payment_success(telegram_user_id, amount, "verotel", sale_id, subscriber_id=sub_id)
            sent = _notify_and_invite(telegram_user_id)
            dal.update_subscriber(sub_id, {"link_sent": 1 if sent else 0})

    # "cancel"/"stop" = subscription cancelled, "chargeback"/"decline"/"expiry" = failed/ended
    elif sale_type in ("cancel", "stop", "chargeback", "decline", "expiry"):
        existing = dal.get_subscriber_by_telegram_id(telegram_user_id)
        sub_id = existing["id"] if existing else None
        dal.record_payment_failed(telegram_user_id, "verotel", sale_type, subscriber_id=sub_id)

    return "OK"


def _kick_member(sub_id):
    """Removes a member from the group/channel right away (used when an
    admin manually marks someone expired and ticks 'remove now')."""
    if not _bot_token() or not _group_chat_id():
        return
    sub = dal.get_subscriber(sub_id)
    if not sub:
        return
    try:
        import asyncio
        from telegram import Bot

        async def _kick():
            bot = Bot(token=_bot_token())
            await bot.ban_chat_member(chat_id=_group_chat_id(), user_id=int(sub["telegram_user_id"]))
            await bot.unban_chat_member(chat_id=_group_chat_id(), user_id=int(sub["telegram_user_id"]))

        asyncio.run(_kick())
    except Exception as e:
        app.logger.warning("Could not remove %s from group: %s", sub["telegram_user_id"], e)


def _notify_and_invite(telegram_user_id):
    """Sends the user a one-time group invite link once payment is confirmed.
    Returns True if it was actually delivered, False otherwise (this is
    exactly what the dashboard's Link Sent column reflects)."""
    if not _bot_token() or not _group_chat_id():
        return False
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            bot = Bot(token=_bot_token())
            invite = await bot.create_chat_invite_link(chat_id=_group_chat_id(), member_limit=1)
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


# ------------------------------------------------------- api: which bot ----

@app.route("/api/bots")
@login_required
def api_bots():
    """The bots configured in .env (BOT_TOKEN_1, BOT_TOKEN_2, ...) and the selected one."""
    bots = [{"slot": n, "label": _bot_label(n), "has_token": bool(slot.bot_token(n))}
            for n in slot.configured_slots()]
    return jsonify({"bots": bots, "current": slot.get()})


@app.route("/api/bots/select", methods=["POST"])
@login_required
def api_select_bot():
    data = request.get_json(force=True, silent=True) or {}
    try:
        n = int(data.get("slot"))
    except (TypeError, ValueError):
        n = 0
    if n not in slot.configured_slots():
        return jsonify({"error": "Unknown bot"}), 400
    session["bot_slot"] = n
    return jsonify({"ok": True, "current": n})


# ---------------------------------------------------- api: connect channel --

def _channel_candidates(raw):
    """Turns whatever the admin pasted into the chat ids to try, best guess first.
    Returns (candidates, problem). Accepts -1001234567890, 1001234567890, 1234567890,
    @channelusername and t.me links. A private invite link can't be resolved by a bot."""
    import re
    text = (raw or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(.+)$", text, re.I)
    if m:
        path = m.group(1).split("?")[0].strip("/")
        if path.startswith("+") or path.lower().startswith("joinchat"):
            return None, "invite_link"
        parts = path.split("/")
        if parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
            return ["-100" + parts[1]], None          # t.me/c/<id>/<post> links
        text = "@" + parts[0]
    if text.startswith("@"):
        if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", text):
            return [text], None
        return None, "invalid"
    if re.fullmatch(r"-?\d{5,}", text):
        digits = text.lstrip("-")
        if text.startswith("-100"):
            return [text], None
        if digits.startswith("100") and len(digits) >= 13:
            return ["-" + digits, "-100" + digits], None   # the minus sign got lost
        return ["-100" + digits, "-" + digits], None       # channel/supergroup first, then an old-style group
    return None, "invalid"


async def _inspect_channel(token, candidates):
    """Asks Telegram, as the bot, what it can see. Returns a dict with a 'status':
    connected | not_admin | not_joined | bad_token (plus channel details)."""
    from telegram import Bot
    from telegram.error import BadRequest, Forbidden, InvalidToken

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
    except InvalidToken:
        return {"status": "bad_token"}

    chat = None
    for candidate in candidates:
        try:
            chat = await bot.get_chat(candidate)
            break
        except (BadRequest, Forbidden):
            continue                      # not found / bot not in it: try the next guess
    if chat is None:
        return {"status": "not_joined"}

    try:
        member = await bot.get_chat_member(chat.id, me.id)
    except (BadRequest, Forbidden):
        return {"status": "not_joined"}

    info = {"channel_id": str(chat.id), "channel_name": chat.title or chat.username or str(chat.id)}
    if member.status in ("administrator", "creator"):
        missing = []
        if member.status == "administrator":
            if not getattr(member, "can_invite_users", False):
                missing.append("Invite users")
            if not getattr(member, "can_restrict_members", False):
                missing.append("Ban users")
        return {"status": "connected", "missing": missing, **info}
    if member.status in ("member", "restricted"):
        return {"status": "not_admin", **info}
    return {"status": "not_joined"}           # left / kicked


@app.route("/api/channel")
@login_required
def api_channel():
    """What the selected bot is currently connected to (no Telegram call, instant)."""
    channel_id = _group_chat_id()
    title = ""
    if channel_id and dal.get_setting("group_chat_title_for", "") == channel_id:
        title = dal.get_setting("group_chat_title", "")
    return jsonify({
        "channel_id": channel_id, "channel_name": title,
        "has_token": bool(_bot_token()), "token_env": slot.token_env_name(),
        "bot": _bot_label() if _bot_token() else "",
    })


@app.route("/api/channel/connect", methods=["POST"])
@login_required
def api_channel_connect():
    """Checks the channel with the selected bot and, only if the bot is an admin there,
    saves it as that bot's channel. The bot token is read from the server's .env."""
    env_name = slot.token_env_name()
    token = _bot_token()
    if not token:
        return jsonify({
            "ok": False, "status": "no_token",
            "message": f"No bot token found. Ask the developer to add {env_name}=<the token from @BotFather> "
                       f"to the server's .env file, then restart the dashboard and the bot.",
            "hint": "The token must go in the .env file, never in the dashboard, for security.",
        })

    data = request.get_json(force=True, silent=True) or {}
    candidates, problem = _channel_candidates(data.get("channel_id", ""))
    if not candidates:
        message = ("That's a private invite link, and Telegram doesn't let a bot find a channel from it."
                   if problem == "invite_link" else "That doesn't look like a channel ID.")
        return jsonify({"ok": False, "status": "invalid_id", "message": message,
                        "hint": "Paste the numeric channel ID (like -1001234567890, with or without the -100) or the @channelusername."})

    import asyncio
    try:
        result = asyncio.run(_inspect_channel(token, candidates))
    except Exception as e:
        app.logger.warning("Channel check failed: %s", e)
        return jsonify({"ok": False, "status": "error", "message": "Could not reach Telegram. Please try again in a moment."})

    status = result["status"]
    if status == "bad_token":
        return jsonify({"ok": False, "status": "bad_token",
                        "message": f"Telegram rejected the bot token. Ask the developer to check {env_name} in the server's .env file.",
                        "hint": "The token must go in the .env file, never in the dashboard, for security."})
    if status == "not_joined":
        return jsonify({"ok": False, "status": "not_joined", "message": "Bot is not joined and not an admin",
                        "hint": "Add the bot to your channel as an administrator, then press Connect again. Also double-check the channel ID."})
    if status == "not_admin":
        return jsonify({"ok": False, "status": "not_admin", "message": "Bot is joined but not an admin",
                        "channel_name": result["channel_name"],
                        "hint": "Make the bot an administrator of the channel (with the 'Invite users' and 'Ban users' permissions), then press Connect again."})

    # admin: save it for this bot
    dal.set_setting("group_chat_id", result["channel_id"])
    dal.set_setting("group_chat_title", result["channel_name"])
    dal.set_setting("group_chat_title_for", result["channel_id"])
    out = {"ok": True, "status": "connected", "channel_id": result["channel_id"],
           "channel_name": result["channel_name"],
           "message": f"Successfully connected with the channel: {result['channel_name']}"}
    if result["missing"]:
        out["warning"] = ("The bot is an admin but is missing the permission(s): " + ", ".join(result["missing"]) +
                          ". Without them it can't create invite links or remove expired members.")
    return jsonify(out)


# ------------------------------------------------- api: revenue export ------

def _revenue_xlsx(payments, total, count, start, end, symbol, bot_label):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    def text(cell, value):
        cell.value = "" if value is None else str(value)
        if cell.data_type == "f":          # never let a value like "=1+1" become a formula
            cell.data_type = "s"

    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws["A1"] = "Revenue report" + (f" - {bot_label}" if bot_label else "")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"], ws["B2"] = "Period (UTC)", f"{start} to {end}"
    ws["A3"], ws["B3"] = "Payments", count
    ws["A4"], ws["B4"] = "Total", float(total)
    ws["B4"].number_format = '"' + symbol.replace('"', "") + '"#,##0.00'
    for ref in ("A2", "A3", "A4"):
        ws[ref].font = Font(bold=True)
    ws["B2"].alignment = ws["B3"].alignment = ws["B4"].alignment = Alignment(horizontal="left")

    headers = ["Date (UTC)", "Telegram ID", "Username", "Plan", "Amount", "Currency", "Provider"]
    header_row = 6
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=header_row, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="3B3F5C")
    for r, pay in enumerate(payments, header_row + 1):
        text(ws.cell(row=r, column=1), (pay.get("created_at") or "").replace("T", " ")[:16])
        text(ws.cell(row=r, column=2), pay.get("telegram_user_id"))
        text(ws.cell(row=r, column=3), ("@" + pay["telegram_username"]) if pay.get("telegram_username") else "")
        text(ws.cell(row=r, column=4), pay.get("plan"))
        amt = ws.cell(row=r, column=5, value=float(pay.get("amount") or 0))
        amt.number_format = "0.00"
        text(ws.cell(row=r, column=6), pay.get("currency"))
        text(ws.cell(row=r, column=7), pay.get("provider"))
    for col, width in zip("ABCDEFG", (18, 16, 22, 30, 12, 10, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _revenue_pdf(payments, total, count, start, end, symbol, bot_label):
    import io
    from datetime import datetime
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    def clean(value):
        # the built-in PDF font is Latin-only: anything else becomes "?" instead of a broken box
        return escape(str("" if value is None else value).encode("cp1252", "replace").decode("cp1252"))

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=10.5)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    right = ParagraphStyle("right", parent=cell, alignment=2)
    right_head = ParagraphStyle("righthead", parent=head, alignment=2)

    title = "Revenue report" + (f" - {bot_label}" if bot_label else "")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=title)
    story = [
        Paragraph(clean(title), styles["Title"]),
        Paragraph(f"Period (UTC): {clean(start)} to {clean(end)}", styles["Normal"]),
        Paragraph(f"<b>Total: {clean(symbol)}{total:,.2f}</b> from {count} payment{'s' if count != 1 else ''}", styles["Normal"]),
        Paragraph(f"Generated {datetime.utcnow():%Y-%m-%d %H:%M} UTC", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    rows = [[Paragraph(h, right_head if h == "Amount" else head)
             for h in ("Date (UTC)", "Telegram ID", "Username", "Plan", "Amount", "Currency", "Provider")]]
    for pay in payments:
        rows.append([
            Paragraph(clean((pay.get("created_at") or "").replace("T", " ")[:16]), cell),
            Paragraph(clean(pay.get("telegram_user_id")), cell),
            Paragraph(clean(("@" + pay["telegram_username"]) if pay.get("telegram_username") else "-"), cell),
            Paragraph(clean(pay.get("plan") or "-"), cell),
            Paragraph(f"{float(pay.get('amount') or 0):,.2f}", right),
            Paragraph(clean(pay.get("currency")), cell),
            Paragraph(clean(pay.get("provider")), cell),
        ])
    if not payments:
        rows.append([Paragraph("No successful payments in this range", cell)] + [""] * 6)
    widths = [34 * mm, 32 * mm, 40 * mm, 70 * mm, 24 * mm, 20 * mm, 22 * mm]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3B3F5C")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F3F8")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D3E0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    if not payments:
        table.setStyle(TableStyle([("SPAN", (0, 1), (-1, 1))]))   # one wide "nothing here" row
    story.append(table)

    def footer(canvas, d):
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f"Page {d.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


@app.route("/api/revenue/export")
@login_required
def api_revenue_export():
    """The revenue page's table as an Excel or PDF file. Same dates, same rows, same
    total as /api/stats/revenue-range, for the currently selected bot."""
    from datetime import datetime, timedelta
    from flask import Response
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    fmt = request.args.get("format", "xlsx").lower()
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)  # make end inclusive
    except ValueError:
        return jsonify({"error": "Use YYYY-MM-DD for both dates"}), 400
    if end_dt <= start_dt:
        return jsonify({"error": "End date must be on or after the start date"}), 400
    if fmt not in ("xlsx", "pdf"):
        return jsonify({"error": "format must be xlsx or pdf"}), 400

    summary = dal.get_revenue_for_range(start_dt.isoformat(), end_dt.isoformat())
    payments = dal.get_payments_for_range(start_dt.isoformat(), end_dt.isoformat())
    symbol = dal.get_setting("currency_symbol", "$")
    multi = len(slot.configured_slots()) > 1
    build = _revenue_xlsx if fmt == "xlsx" else _revenue_pdf
    try:
        body = build(payments, summary["revenue"], summary["count"], start, end, symbol, _bot_label() if multi else "")
    except ImportError:
        return jsonify({"error": "Exporting needs two extra packages (openpyxl and reportlab). "
                                 "Ask the developer to run: pip install -r requirements.txt, then restart the dashboard."}), 500
    name = f"revenue_{'bot' + str(slot.get()) + '_' if multi else ''}{start}_to_{end}.{fmt}"
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx"
            else "application/pdf")
    return Response(body, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _send_dm(telegram_user_id, text):
    """Best-effort plain-text DM through the bot. Never raises; True if Telegram accepted it."""
    if not _bot_token():
        return False
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            await Bot(token=_bot_token()).send_message(chat_id=int(telegram_user_id), text=text)

        asyncio.run(_send())
        return True
    except Exception as e:
        app.logger.warning("Could not DM %s: %s", telegram_user_id, e)
        return False


def _has_time_left(sub):
    """True if the plan hasn't expired yet. Lifetime plans expire in 2126, so they count."""
    from datetime import datetime
    return bool(sub.get("expiry_date")) and sub["expiry_date"] > datetime.utcnow().isoformat()


def _expiry_label(sub):
    expiry = sub.get("expiry_date") or ""
    return "lifetime" if expiry[:4] >= "2100" else expiry[:10]


def _notify_unbanned(sub):
    """DMs an unbanned member their expiry date plus a fresh one-time invite link.
    Returns (message_delivered, invite_link_included). If the invite link can't be
    created (no group configured / bot not admin) the message still goes out without it."""
    if not _bot_token():
        return False, False
    plan = sub.get("button_text") or "subscription"
    if _expiry_label(sub) == "lifetime":
        status_line = f"Your {plan} plan is active (lifetime access)."
    else:
        status_line = f"Your {plan} plan is active until {_expiry_label(sub)}."
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            bot = Bot(token=_bot_token())
            link = None
            if _group_chat_id():
                try:
                    invite = await bot.create_chat_invite_link(chat_id=_group_chat_id(), member_limit=1)
                    link = invite.invite_link
                except Exception as e:
                    app.logger.warning("Could not create invite for unbanned %s: %s", sub["telegram_user_id"], e)
            text = f"✅ You have been unbanned.\n\n{status_line}"
            if link:
                text += f"\n\n🎉 Tap below to rejoin:\n{link}"
            await bot.send_message(chat_id=int(sub["telegram_user_id"]), text=text)
            return link is not None

        return True, asyncio.run(_send())
    except Exception as e:
        app.logger.warning("Could not notify unbanned %s: %s", sub.get("telegram_user_id"), e)
        return False, False


# ---------------------------------------------------------- api: broadcast --
# The dashboard only QUEUES broadcasts here. The bot's job queue (bot/bot.py,
# process_broadcasts, every 60 s) does the actual sending, never this web request.

BROADCAST_AUDIENCES = ("active", "expired", "all")
BROADCAST_MEDIA_TYPES = ("photo", "video", "document")
BROADCAST_MAX_BYTES = 50 * 1024 * 1024          # Telegram bot upload limit
BROADCAST_PHOTO_MAX_BYTES = 10 * 1024 * 1024    # Telegram limit for sendPhoto
BROADCAST_TEXT_MAX = 4096
BROADCAST_CAPTION_MAX = 1024                    # caption limit when media is attached


def _broadcast_media_path(media_file):
    return os.path.join(dal.BROADCAST_MEDIA_DIR, os.path.basename(media_file or ""))


def _delete_broadcast_media(broadcast):
    if broadcast and broadcast.get("media_file"):
        try:
            os.remove(_broadcast_media_path(broadcast["media_file"]))
        except OSError:
            pass


@app.route("/api/broadcasts/audience-count")
@login_required
def api_broadcast_audience_count():
    audience = request.args.get("audience", "")
    if audience not in BROADCAST_AUDIENCES:
        return jsonify({"error": "Pick an audience"}), 400
    return jsonify({"count": len(dal.get_broadcast_recipients(audience))})


@app.route("/api/broadcasts", methods=["GET"])
@login_required
def api_list_broadcasts():
    rows = dal.get_broadcasts(30)
    for r in rows:
        r.pop("media_file", None)   # internal storage name, not for the browser
    return jsonify(rows)


@app.route("/api/broadcasts", methods=["POST"])
@login_required
def api_create_broadcast():
    from datetime import datetime, timezone
    import uuid
    from werkzeug.utils import secure_filename

    # refuse obviously oversized uploads before reading the body
    if (request.content_length or 0) > BROADCAST_MAX_BYTES + 2 * 1024 * 1024:
        return jsonify({"error": "File is too large (max 50 MB)"}), 413

    message = (request.form.get("message") or "").strip()
    audience = request.form.get("audience", "")
    mode = request.form.get("mode", "now")
    media_type = request.form.get("media_type", "")
    upload = request.files.get("file")
    has_media = bool(upload and upload.filename)

    if audience not in BROADCAST_AUDIENCES:
        return jsonify({"error": "Pick an audience"}), 400
    if mode not in ("now", "schedule"):
        return jsonify({"error": "Invalid send mode"}), 400
    if not message and not has_media:
        return jsonify({"error": "Write a message or attach a file"}), 400
    if has_media and media_type not in BROADCAST_MEDIA_TYPES:
        return jsonify({"error": "Pick how to send the file (photo, video or document)"}), 400
    if has_media and len(message) > BROADCAST_CAPTION_MAX:
        return jsonify({"error": f"With a file attached the message is its caption: max {BROADCAST_CAPTION_MAX} characters"}), 400
    if len(message) > BROADCAST_TEXT_MAX:
        return jsonify({"error": f"Message is too long (max {BROADCAST_TEXT_MAX} characters)"}), 400

    now = datetime.utcnow()
    if mode == "schedule":
        raw = (request.form.get("scheduled_at") or "").strip()
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "Pick a valid date and time"}), 400
        if when.tzinfo:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        if (now - when).total_seconds() > 60:
            return jsonify({"error": "Pick a time in the future"}), 400
        scheduled_at = when.isoformat()
    else:
        if not dal.get_broadcast_recipients(audience):
            return jsonify({"error": "There is nobody in that audience to send to"}), 400
        scheduled_at = now.isoformat()

    media_file = media_name = None
    if has_media:
        os.makedirs(dal.BROADCAST_MEDIA_DIR, exist_ok=True)
        media_name = secure_filename(upload.filename) or "file"
        media_file = uuid.uuid4().hex + os.path.splitext(media_name)[1].lower()[:10]
        path = _broadcast_media_path(media_file)
        size = 0
        with open(path, "wb") as out:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > BROADCAST_MAX_BYTES:
                    break
                out.write(chunk)
        problem = None
        if size > BROADCAST_MAX_BYTES:
            problem = ("File is too large (max 50 MB)", 413)
        elif size == 0:
            problem = ("The file is empty", 400)
        elif media_type == "photo" and size > BROADCAST_PHOTO_MAX_BYTES:
            problem = ("Photos can be 10 MB at most. Send it as a document instead", 400)
        if problem:
            os.remove(path)
            return jsonify({"error": problem[0]}), problem[1]

    try:
        new_id = dal.create_broadcast(message, media_type if has_media else None,
                                      media_file, media_name, audience, scheduled_at)
    except Exception:
        if media_file:
            try:
                os.remove(_broadcast_media_path(media_file))
            except OSError:
                pass
        raise
    return jsonify({"ok": True, "id": new_id, "scheduled_at": scheduled_at})


@app.route("/api/broadcasts/<broadcast_id>/cancel", methods=["POST"])
@login_required
def api_cancel_broadcast(broadcast_id):
    try:
        broadcast = dal.get_broadcast(broadcast_id)
    except ValueError:
        broadcast = None
    if not broadcast:
        return jsonify({"error": "not found"}), 404
    if not dal.cancel_broadcast(broadcast_id):
        return jsonify({"error": "It has already started or finished, so it can't be cancelled"}), 409
    _delete_broadcast_media(broadcast)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
