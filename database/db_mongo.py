"""
MongoDB backend. Implements the same function interface as database/db.py
so database/dal.py can switch between them with one .env setting.

One subscriber document per Telegram user (not one per purchase). Buying
again while already active extends the existing expiry date (stacking);
buying again after expiry starts a fresh period from now. Lifetime
purchases always win outright.

Requires MONGODB_URI (and optionally MONGODB_DB_NAME) in .env.
"""
import os
from datetime import datetime, timedelta
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient

_client = None
_db = None

DURATION_DAYS = {"weekly": 7, "monthly": 30, "onetime": 36500}
LIFETIME_EXPIRY = (datetime(2126, 1, 1)).isoformat()


def _get_db():
    global _client, _db
    if _db is None:
        uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.environ.get("MONGODB_DB_NAME", "telegram_sub_bot")
        _client = MongoClient(uri)
        _db = _client[db_name]
    return _db


def _oid(id_str):
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        return id_str


def init_db():
    db = _get_db()

    db.panels.create_index("active")
    db.subscribers.create_index("status")
    index = db.subscribers.index_information().get("telegram_user_id_1")
    if index and not index.get("unique"):
    db.subscribers.drop_index("telegram_user_id_1")
    db.subscribers.create_index("telegram_user_id")
    db.payments.create_index("subscriber_id")
    db.payments.create_index("status")
    db.settings.create_index("key", unique=True)
    db.admins.create_index("telegram_admin_id", unique=True)

    defaults = {
        "dashboard_title": "Subscription Dashboard",
        "currency_symbol": "$",
        "active_providers": "stripe",
        "reminder_days_before": "3",
        "auto_kick_on_expiry": "0",
    }
    for k, v in defaults.items():
        db.settings.update_one({"key": k}, {"$setOnInsert": {"value": v}}, upsert=True)

    if db.panels.count_documents({}) == 0:
        default_buttons = [
            {"text": "1 Week - $9", "price": 9, "duration_type": "weekly"},
            {"text": "1 Month - $25", "price": 25, "duration_type": "monthly"},
            {"text": "Lifetime - $79", "price": 79, "duration_type": "onetime"},
        ]
        db.panels.insert_one({
            "name": "Default Panel",
            "message_text": "Welcome! Choose a plan below to get access.",
            "buttons": default_buttons,
            "active": 1,
            "created_at": datetime.utcnow().isoformat(),
        })


# ------------------------------------------------------------- settings --

def get_setting(key, default=None):
    db = _get_db()
    doc = db.settings.find_one({"key": key})
    return doc["value"] if doc else default


def set_setting(key, value):
    db = _get_db()
    db.settings.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)


def get_all_settings():
    db = _get_db()
    return {d["key"]: d["value"] for d in db.settings.find({})}


def get_admin_ids():
    db = _get_db()
    return [d["telegram_admin_id"] for d in db.admins.find({})]


def set_admin_ids(ids_list):
    db = _get_db()
    db.admins.delete_many({})
    for aid in ids_list:
        aid = str(aid).strip()
        if aid:
            db.admins.update_one({"telegram_admin_id": aid}, {"$set": {"telegram_admin_id": aid}}, upsert=True)


# --------------------------------------------------------------- stats ---

def get_stats():
    db = _get_db()
    total_revenue = sum(
        p.get("amount") or 0 for p in db.payments.find({"status": "successful"}, {"amount": 1})
    )
    month_start = datetime.utcnow().replace(day=1).isoformat()
    month_revenue = sum(
        p.get("amount") or 0 for p in db.payments.find(
            {"status": "successful", "created_at": {"$gte": month_start}}, {"amount": 1}
        )
    )
    active_subs = db.subscribers.count_documents({"status": "active"})
    soon = (datetime.utcnow() + timedelta(days=3)).isoformat()
    expiring_soon = db.subscribers.count_documents({
        "status": "active",
        "expiry_date": {"$lte": soon, "$ne": None},
    })
    return {
        "total_revenue": total_revenue,
        "month_revenue": month_revenue,
        "active_subs": active_subs,
        "expiring_soon": expiring_soon,
    }


# ---------------------------------------------------------- subscribers --

def _serialize_subscriber(db, doc):
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    last_payment = db.payments.find_one(
        {"subscriber_id": d["id"]}, sort=[("created_at", -1)]
    )
    d["last_payment_status"] = last_payment["status"] if last_payment else None
    d["last_failure_reason"] = last_payment.get("failure_reason") if last_payment else None
    return d


def get_subscribers(status="", pay_status="", search="", page=1, page_size=30):
    db = _get_db()
    query = {}
    if status:
        query["status"] = status
    if search:
        query["$or"] = [
            {"telegram_username": {"$regex": search, "$options": "i"}},
            {"telegram_user_id": {"$regex": search, "$options": "i"}},
        ]

    rows = [_serialize_subscriber(db, d) for d in db.subscribers.find(query)]

    if pay_status:
        rows = [r for r in rows if r.get("last_payment_status") == pay_status]

    total = len(rows)
    start = (page - 1) * page_size
    return {"rows": rows[start:start + page_size], "total": total}


def get_subscriber(sub_id):
    db = _get_db()
    doc = db.subscribers.find_one({"_id": _oid(sub_id)})
    return _serialize_subscriber(db, doc) if doc else None


def get_subscriber_by_telegram_id(telegram_user_id):
    db = _get_db()
    doc = db.subscribers.find_one({"telegram_user_id": str(telegram_user_id)})
    return _serialize_subscriber(db, doc) if doc else None


def get_latest_pending_subscriber(telegram_user_id):
    return get_subscriber_by_telegram_id(telegram_user_id)


def update_subscriber(sub_id, fields):
    allowed = ["status", "auto_renew", "expiry_date", "telegram_username", "link_sent"]
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    db = _get_db()
    db.subscribers.update_one({"_id": _oid(sub_id)}, {"$set": fields})
    return True


def set_all_auto_renew(value):
    db = _get_db()
    db.subscribers.update_many({}, {"$set": {"auto_renew": 1 if value else 0}})


def delete_subscriber(sub_id):
    db = _get_db()
    db.payments.delete_many({"subscriber_id": str(sub_id)})
    db.subscribers.delete_one({"_id": _oid(sub_id)})


def export_subscribers(status="", pay_status="", search=""):
    result = get_subscribers(status, pay_status, search, page=1, page_size=10**9)
    return result["rows"]


def apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider):
    """
    The one place purchase logic happens. Creates the subscriber document on
    a first-ever purchase, or extends/stacks the existing one on a renewal:
    - lifetime (onetime) always wins outright, regardless of prior state
    - buying again while still active extends the current expiry forward
    - buying again after expiry (or first time) starts fresh from now
    Returns the subscriber's id (str).
    """
    db = _get_db()
    days = DURATION_DAYS.get(duration_type, 30)
    now = datetime.utcnow()
    existing = get_subscriber_by_telegram_id(telegram_user_id)

    if duration_type == "onetime":
        new_expiry = LIFETIME_EXPIRY
    elif existing and existing.get("expiry_date") and existing["expiry_date"] > now.isoformat() and existing["status"] == "active":
        base = datetime.fromisoformat(existing["expiry_date"])
        new_expiry = (base + timedelta(days=days)).isoformat()
    else:
        new_expiry = (now + timedelta(days=days)).isoformat()

    if existing:
        db.subscribers.update_one(
            {"telegram_user_id": str(telegram_user_id)},
            {
                "$set": {
                    "telegram_username": telegram_username or "",
                    "button_text": button_text,
                    "duration_type": duration_type,
                    "price": price,
                    "status": "active",
                    "expiry_date": new_expiry,
                    "provider": provider,
                    "link_sent": 0,
                },
                "$inc": {"purchase_count": 1},
            },
        )
        return existing["id"]
    else:
        res = db.subscribers.insert_one({
            "telegram_user_id": str(telegram_user_id), "telegram_username": telegram_username or "",
            "panel_id": None, "button_text": button_text, "duration_type": duration_type,
            "price": price, "currency": "USD", "status": "active", "auto_renew": 0,
            "start_date": now.isoformat(), "expiry_date": new_expiry, "provider": provider,
            "purchase_count": 1, "link_sent": 0, "created_at": now.isoformat(),
        })
        return str(res.inserted_id)


# kept for compatibility; no longer used by bot.py (see apply_purchase)
def create_pending_subscriber(telegram_user_id, telegram_username, panel_id, button_text, duration_type, price, provider):
    return apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider)


# ---------------------------------------------------------------- panels --

def _serialize_panel(doc):
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


def get_panels():
    db = _get_db()
    return [_serialize_panel(d) for d in db.panels.find({}).sort("created_at", -1)]


def get_active_panel():
    db = _get_db()
    doc = db.panels.find_one({"active": 1}, sort=[("created_at", -1)])
    return _serialize_panel(doc) if doc else None


def create_panel(name, message_text, buttons):
    db = _get_db()
    db.panels.insert_one({
        "name": name, "message_text": message_text, "buttons": buttons,
        "active": 1, "created_at": datetime.utcnow().isoformat(),
    })


def update_panel(panel_id, name, message_text, buttons, active=1):
    db = _get_db()
    db.panels.update_one(
        {"_id": _oid(panel_id)},
        {"$set": {"name": name, "message_text": message_text, "buttons": buttons, "active": active}},
    )


def delete_panel(panel_id):
    db = _get_db()
    db.panels.delete_one({"_id": _oid(panel_id)})


# -------------------------------------------------------------- payments --

def record_payment_success(telegram_user_id, amount, provider, provider_ref, subscriber_id=None):
    db = _get_db()
    db.payments.insert_one({
        "subscriber_id": subscriber_id, "telegram_user_id": telegram_user_id, "amount": amount,
        "status": "successful", "provider": provider, "provider_ref": provider_ref,
        "created_at": datetime.utcnow().isoformat(),
    })


def record_payment_failed(telegram_user_id, provider, failure_reason, subscriber_id=None):
    db = _get_db()
    db.payments.insert_one({
        "subscriber_id": subscriber_id, "telegram_user_id": telegram_user_id,
        "status": "failed", "failure_reason": failure_reason, "provider": provider,
        "created_at": datetime.utcnow().isoformat(),
    })


# --------------------------------------------------------- bot-side ops --

def get_expiring_subscribers(reminder_days):
    db = _get_db()
    soon = (datetime.utcnow() + timedelta(days=reminder_days)).isoformat()
    now = datetime.utcnow().isoformat()
    docs = db.subscribers.find({
        "status": "active", "auto_renew": 0,
        "expiry_date": {"$lte": soon, "$gt": now},
    })
    return [{**d, "id": str(d["_id"])} for d in docs]


def get_expired_subscribers():
    db = _get_db()
    now = datetime.utcnow().isoformat()
    docs = db.subscribers.find({"status": "active", "expiry_date": {"$lte": now}})
    return [{**d, "id": str(d["_id"])} for d in docs]


# ------------------------------------------------------------ dev view ---

def get_dev_table(table_name):
    if table_name not in ("panels", "subscribers", "payments", "settings", "admins"):
        raise ValueError("invalid table")
    db = _get_db()
    rows = []
    for d in db[table_name].find({}):
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        rows.append(d)
    return rows


if __name__ == "__main__":
    init_db()
    print("MongoDB database initialized:", os.environ.get("MONGODB_URI", "mongodb://localhost:27017"))
