"""
SQLite backend (default). Implements the same function interface as
database/db_mongo.py so database/dal.py can switch between them.

One subscriber row per Telegram user (not one row per purchase). Buying
again while already active extends the existing expiry date (stacking);
buying again after expiry starts a fresh period from now. Lifetime
purchases always win outright.
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

DURATION_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, "semiannual": 180, "yearly": 365, "onetime": 36500}  # ~100yr for lifetime
LIFETIME_EXPIRY = (datetime(2126, 1, 1)).isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column_if_missing(conn, table, col_def):
    col_name = col_def.split()[0]
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS panels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        message_text TEXT NOT NULL,
        buttons_json TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id TEXT NOT NULL UNIQUE,
        telegram_username TEXT,
        panel_id INTEGER,
        button_text TEXT,
        duration_type TEXT,
        price REAL,
        currency TEXT DEFAULT 'USD',
        status TEXT DEFAULT 'active',
        auto_renew INTEGER DEFAULT 0,
        start_date TEXT,
        expiry_date TEXT,
        provider TEXT DEFAULT 'stripe',
        purchase_count INTEGER DEFAULT 0,
        link_sent INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (panel_id) REFERENCES panels(id)
    )
    """)
    # migration for databases created before purchase_count/link_sent existed
    _add_column_if_missing(conn, "subscribers", "purchase_count INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "subscribers", "link_sent INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "subscribers", "custom_days INTEGER")

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER,
        telegram_user_id TEXT,
        amount REAL,
        currency TEXT DEFAULT 'USD',
        status TEXT NOT NULL,
        failure_reason TEXT,
        provider TEXT DEFAULT 'stripe',
        provider_ref TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_admin_id TEXT UNIQUE NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    defaults = {
        "dashboard_title": "Subscription Dashboard",
        "currency_symbol": "$",
        "active_providers": "stripe",
        "reminder_days_before": "3",
        "auto_kick_on_expiry": "0",
        "auto_billing_enabled": "1",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()

    c.execute("SELECT COUNT(*) as n FROM panels")
    if c.fetchone()["n"] == 0:
        default_buttons = [
            {"text": "1 Week - $9", "price": 9, "duration_type": "weekly"},
            {"text": "1 Month - $25", "price": 25, "duration_type": "monthly"},
            {"text": "Lifetime - $79", "price": 79, "duration_type": "onetime"},
        ]
        c.execute(
            "INSERT INTO panels (name, message_text, buttons_json, active, created_at) VALUES (?, ?, ?, 1, ?)",
            ("Default Panel", "Welcome! Choose a plan below to get access.",
             json.dumps(default_buttons), datetime.utcnow().isoformat()),
        )
        conn.commit()

    conn.close()


# ------------------------------------------------------------- settings --

def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_all_settings():
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def get_admin_ids():
    conn = get_conn()
    rows = conn.execute("SELECT telegram_admin_id FROM admins").fetchall()
    conn.close()
    return [r["telegram_admin_id"] for r in rows]


def set_admin_ids(ids_list):
    conn = get_conn()
    conn.execute("DELETE FROM admins")
    for aid in ids_list:
        aid = str(aid).strip()
        if aid:
            conn.execute("INSERT OR IGNORE INTO admins (telegram_admin_id) VALUES (?)", (aid,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------- stats ---

def get_stats():
    conn = get_conn()
    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE status='successful'"
    ).fetchone()["total"]
    month_start = datetime.utcnow().replace(day=1).isoformat()
    month_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE status='successful' AND created_at >= ?",
        (month_start,),
    ).fetchone()["total"]
    active_subs = conn.execute("SELECT COUNT(*) as n FROM subscribers WHERE status='active'").fetchone()["n"]
    soon = (datetime.utcnow() + timedelta(days=3)).isoformat()
    expiring_soon = conn.execute(
        "SELECT COUNT(*) as n FROM subscribers WHERE status='active' AND expiry_date <= ? AND expiry_date IS NOT NULL",
        (soon,),
    ).fetchone()["n"]
    conn.close()
    return {
        "total_revenue": total_revenue,
        "month_revenue": month_revenue,
        "active_subs": active_subs,
        "expiring_soon": expiring_soon,
    }


# ---------------------------------------------------------- subscribers --

def _row_with_last_payment(conn, row):
    d = dict(row)
    p = conn.execute(
        "SELECT status, failure_reason FROM payments WHERE subscriber_id = ? ORDER BY created_at DESC LIMIT 1",
        (d["id"],),
    ).fetchone()
    d["last_payment_status"] = p["status"] if p else None
    d["last_failure_reason"] = p["failure_reason"] if p else None
    d["id"] = str(d["id"])
    return d


def get_subscriber(sub_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM subscribers WHERE id = ?", (int(sub_id),)).fetchone()
    if not row:
        conn.close()
        return None
    d = _row_with_last_payment(conn, row)
    conn.close()
    return d


def get_subscriber_by_telegram_id(telegram_user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM subscribers WHERE telegram_user_id = ?", (str(telegram_user_id),)
    ).fetchone()
    if not row:
        conn.close()
        return None
    d = _row_with_last_payment(conn, row)
    conn.close()
    return d


# kept for backward compatibility with any code still calling it; now just
# an alias since there is at most one (pending-or-not) row per user
def get_latest_pending_subscriber(telegram_user_id):
    return get_subscriber_by_telegram_id(telegram_user_id)


def get_subscribers(status="", pay_status="", search="", page=1, page_size=30):
    query = "SELECT * FROM subscribers WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (telegram_username LIKE ? OR telegram_user_id LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    conn = get_conn()
    rows = [_row_with_last_payment(conn, r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    if pay_status:
        rows = [r for r in rows if r.get("last_payment_status") == pay_status]

    total = len(rows)
    start = (page - 1) * page_size
    return {"rows": rows[start:start + page_size], "total": total}


def update_subscriber(sub_id, fields):
    allowed = ["status", "auto_renew", "expiry_date", "telegram_username", "link_sent",
               "button_text", "duration_type", "price", "purchase_count", "custom_days"]
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE subscribers SET {set_clause} WHERE id = ?", list(fields.values()) + [int(sub_id)])
    conn.commit()
    conn.close()
    return True


def set_all_auto_renew(value):
    conn = get_conn()
    conn.execute("UPDATE subscribers SET auto_renew = ?", (1 if value else 0,))
    conn.commit()
    conn.close()


def delete_subscriber(sub_id):
    conn = get_conn()
    conn.execute("DELETE FROM payments WHERE subscriber_id = ?", (int(sub_id),))
    conn.execute("DELETE FROM subscribers WHERE id = ?", (int(sub_id),))
    conn.commit()
    conn.close()


def export_subscribers(status="", pay_status="", search=""):
    result = get_subscribers(status, pay_status, search, page=1, page_size=10**9)
    return result["rows"]


def apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider, custom_days=None):
    """
    The one place purchase logic happens. Creates the subscriber row on a
    first-ever purchase, or extends/stacks the existing one on a renewal:
    - lifetime (onetime) always wins outright, regardless of prior state
    - buying again while still active extends the current expiry forward
    - buying again after expiry (or first time) starts fresh from now
    - duration_type "custom" uses custom_days instead of the fixed table
    Returns the subscriber's id (str).
    """
    days = custom_days if (duration_type == "custom" and custom_days) else DURATION_DAYS.get(duration_type, 30)
    now = datetime.utcnow()
    existing = get_subscriber_by_telegram_id(telegram_user_id)

    if duration_type == "onetime":
        new_expiry = LIFETIME_EXPIRY
    elif existing and existing.get("expiry_date") and existing["expiry_date"] > now.isoformat() and existing["status"] == "active":
        base = datetime.fromisoformat(existing["expiry_date"])
        new_expiry = (base + timedelta(days=days)).isoformat()
    else:
        new_expiry = (now + timedelta(days=days)).isoformat()

    conn = get_conn()
    if existing:
        conn.execute(
            "UPDATE subscribers SET telegram_username=?, button_text=?, duration_type=?, custom_days=?, price=?, "
            "status='active', expiry_date=?, provider=?, purchase_count=purchase_count+1, link_sent=0 "
            "WHERE telegram_user_id=?",
            (telegram_username or "", button_text, duration_type, custom_days, price, new_expiry, provider, str(telegram_user_id)),
        )
        sub_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO subscribers (telegram_user_id, telegram_username, panel_id, button_text, "
            "duration_type, custom_days, price, status, auto_renew, start_date, expiry_date, provider, "
            "purchase_count, link_sent, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, 'active', 0, ?, ?, ?, 1, 0, ?)",
            (str(telegram_user_id), telegram_username or "", button_text, duration_type, custom_days, price,
             now.isoformat(), new_expiry, provider, now.isoformat()),
        )
        sub_id = str(cur.lastrowid)
    conn.commit()
    conn.close()
    return sub_id


# kept for compatibility; no longer used by bot.py (see apply_purchase)
def create_pending_subscriber(telegram_user_id, telegram_username, panel_id, button_text, duration_type, price, provider):
    return apply_purchase(telegram_user_id, telegram_username, button_text, duration_type, price, provider)


# ---------------------------------------------------------------- panels --

def get_panels():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM panels ORDER BY created_at DESC").fetchall()]
    conn.close()
    for r in rows:
        r["buttons"] = json.loads(r["buttons_json"])
        r["id"] = str(r["id"])
    return rows


def get_active_panel():
    conn = get_conn()
    row = conn.execute("SELECT * FROM panels WHERE active = 1 ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["buttons"] = json.loads(d["buttons_json"])
    d["id"] = str(d["id"])
    return d


def create_panel(name, message_text, buttons):
    conn = get_conn()
    conn.execute(
        "INSERT INTO panels (name, message_text, buttons_json, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (name, message_text, json.dumps(buttons), datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def update_panel(panel_id, name, message_text, buttons, active=1):
    conn = get_conn()
    conn.execute(
        "UPDATE panels SET name=?, message_text=?, buttons_json=?, active=? WHERE id=?",
        (name, message_text, json.dumps(buttons), active, int(panel_id)),
    )
    conn.commit()
    conn.close()


def delete_panel(panel_id):
    conn = get_conn()
    conn.execute("DELETE FROM panels WHERE id=?", (int(panel_id),))
    conn.commit()
    conn.close()


# -------------------------------------------------------------- payments --

def record_payment_success(telegram_user_id, amount, provider, provider_ref, subscriber_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO payments (subscriber_id, telegram_user_id, amount, status, provider, provider_ref, created_at) "
        "VALUES (?, ?, ?, 'successful', ?, ?, ?)",
        (subscriber_id, telegram_user_id, amount, provider, provider_ref, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def record_payment_failed(telegram_user_id, provider, failure_reason, subscriber_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO payments (subscriber_id, telegram_user_id, status, failure_reason, provider, created_at) "
        "VALUES (?, ?, 'failed', ?, ?, ?)",
        (subscriber_id, telegram_user_id, failure_reason, provider, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------- bot-side ops --

def get_expiring_subscribers(reminder_days):
    soon = (datetime.utcnow() + timedelta(days=reminder_days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM subscribers WHERE status='active' AND auto_renew=0 "
        "AND expiry_date <= ? AND expiry_date > ?",
        (soon, datetime.utcnow().isoformat()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_expired_subscribers():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM subscribers WHERE status='active' AND expiry_date <= ?",
        (datetime.utcnow().isoformat(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ------------------------------------------------------------ dev view ---

def get_dev_table(table_name):
    if table_name not in ("panels", "subscribers", "payments", "settings", "admins"):
        raise ValueError("invalid table")
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table_name}").fetchall()]
    conn.close()
    return rows


if __name__ == "__main__":
    init_db()
    print("SQLite database initialized at", DB_PATH)
