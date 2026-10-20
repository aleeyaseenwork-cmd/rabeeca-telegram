"""
Backend switch + live Sheets mirror.

app.py and bot.py only ever import from here, never from db.py or
db_mongo.py directly. Two things happen in this one file:

1. DB_BACKEND in .env decides whether reads/writes go to SQLite or MongoDB.
2. Every write also gets mirrored into Google Sheets (if GOOGLE_SHEET_ID is
   set), so there's a live, human-readable backup outside the main database.
   Sheets mirroring never blocks or breaks a write — see sheets_sync.py.
"""
import os
import logging

log = logging.getLogger(__name__)

# Folder where the dashboard drops broadcast media and the bot picks it up. Both run on the
# same server, so a plain folder works. Override with BROADCAST_MEDIA_DIR in .env if needed.
BROADCAST_MEDIA_DIR = os.environ.get("BROADCAST_MEDIA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "broadcast_media")

_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower()

if _BACKEND == "mongodb":
    from database import db_mongo as _impl
else:
    from database import db as _impl

from database import sheets_sync

BACKEND_NAME = _BACKEND

# ---- pure passthroughs (reads, and things Sheets doesn't need to mirror) --
init_db = _impl.init_db
get_setting = _impl.get_setting
set_setting = _impl.set_setting
get_all_settings = _impl.get_all_settings
get_admin_ids = _impl.get_admin_ids
set_admin_ids = _impl.set_admin_ids
get_stats = _impl.get_stats
get_subscribers = _impl.get_subscribers
export_subscribers = _impl.export_subscribers
get_panels = _impl.get_panels
get_active_panel = _impl.get_active_panel
get_expiring_subscribers = _impl.get_expiring_subscribers
get_expired_subscribers = _impl.get_expired_subscribers
get_dev_table = _impl.get_dev_table
get_subscriber_by_provider_subscription_id = _impl.get_subscriber_by_provider_subscription_id
get_bans = _impl.get_bans
is_banned = _impl.is_banned
ban_user = _impl.ban_user
unban_user = _impl.unban_user
get_revenue_for_range = _impl.get_revenue_for_range
get_payments_for_range = _impl.get_payments_for_range
get_broadcast_recipients = _impl.get_broadcast_recipients
create_broadcast = _impl.create_broadcast
get_broadcast = _impl.get_broadcast
get_broadcasts = _impl.get_broadcasts
cancel_broadcast = _impl.cancel_broadcast
claim_due_broadcast = _impl.claim_due_broadcast
update_broadcast_progress = _impl.update_broadcast_progress
finish_broadcast = _impl.finish_broadcast
interrupt_stuck_broadcasts = _impl.interrupt_stuck_broadcasts
get_subscriber = _impl.get_subscriber
get_subscriber_by_telegram_id = _impl.get_subscriber_by_telegram_id
get_latest_pending_subscriber = _impl.get_latest_pending_subscriber  # back-compat alias


# --------------------------------------------------- wrapped write ops ----
# Each of these does the real write first (source of truth), then makes a
# best-effort attempt to mirror the result into Google Sheets.

def update_subscriber(sub_id, fields):
    ok = _impl.update_subscriber(sub_id, fields)
    if ok:
        try:
            row = _impl.get_subscriber(sub_id)
            if row:
                sheets_sync.mirror_subscriber(row)
        except Exception as e:
            log.warning("Could not mirror subscriber %s to Sheets: %s", sub_id, e)
    return ok


def set_all_auto_renew(value):
    _impl.set_all_auto_renew(value)
    try:
        for row in _impl.get_subscribers(page=1, page_size=10**9)["rows"]:
            sheets_sync.mirror_subscriber(row)
    except Exception as e:
        log.warning("Could not mirror bulk auto-renew change to Sheets: %s", e)


def delete_subscriber(sub_id):
    _impl.delete_subscriber(sub_id)
    try:
        sheets_sync.mirror_subscriber_deleted(sub_id)
    except Exception as e:
        log.warning("Could not remove deleted subscriber %s from Sheets: %s", sub_id, e)


def create_panel(name, message_text, buttons):
    _impl.create_panel(name, message_text, buttons)
    try:
        panels = _impl.get_panels()
        if panels:
            sheets_sync.mirror_panel(panels[0])  # most recently created, sorted desc
    except Exception as e:
        log.warning("Could not mirror new panel to Sheets: %s", e)


def update_panel(panel_id, name, message_text, buttons, active=1):
    _impl.update_panel(panel_id, name, message_text, buttons, active)
    try:
        match = next((p for p in _impl.get_panels() if p["id"] == str(panel_id)), None)
        if match:
            sheets_sync.mirror_panel(match)
    except Exception as e:
        log.warning("Could not mirror updated panel %s to Sheets: %s", panel_id, e)


def delete_panel(panel_id):
    _impl.delete_panel(panel_id)
    try:
        sheets_sync.mirror_panel_deleted(panel_id)
    except Exception as e:
        log.warning("Could not remove deleted panel %s from Sheets: %s", panel_id, e)


def record_payment_success(telegram_user_id, amount, provider, provider_ref, subscriber_id=None):
    _impl.record_payment_success(telegram_user_id, amount, provider, provider_ref, subscriber_id)
    try:
        rows = _impl.get_dev_table("payments")
        if rows:
            sheets_sync.mirror_payment(rows[-1])  # just-inserted row
    except Exception as e:
        log.warning("Could not mirror payment to Sheets: %s", e)


def record_payment_failed(telegram_user_id, provider, failure_reason, subscriber_id=None):
    _impl.record_payment_failed(telegram_user_id, provider, failure_reason, subscriber_id)
    try:
        rows = _impl.get_dev_table("payments")
        if rows:
            sheets_sync.mirror_payment(rows[-1])
    except Exception as e:
        log.warning("Could not mirror failed payment to Sheets: %s", e)


def apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider, custom_days=None, provider_subscription_id=None):
    """
    One row per Telegram user. First purchase creates it; buying again
    while active stacks the expiry forward; buying again after expiry
    starts fresh; lifetime always wins outright. See db.py/db_mongo.py.
    """
    sub_id = _impl.apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider, custom_days, provider_subscription_id)
    try:
        row = _impl.get_subscriber(sub_id)
        if row:
            sheets_sync.mirror_subscriber(row)
    except Exception as e:
        log.warning("Could not mirror purchase for subscriber %s to Sheets: %s", sub_id, e)
    return sub_id


# kept for compatibility with any old callers; routes straight to apply_purchase
def create_pending_subscriber(telegram_user_id, telegram_username, panel_id, button_text, duration_type, price, provider):
    return apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider)
