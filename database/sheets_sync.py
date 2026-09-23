"""
Live Google Sheets mirror.
Every write to the database (subscriber, payment, panel) also gets
upserted into a Google Sheet, so there's always a human-readable,
independent copy of the data sitting outside MongoDB.

This is best-effort: if Sheets is unreachable or misconfigured, writes
to the real database still succeed, and a warning is logged. The
dashboard/bot never depend on Sheets being up.

Credentials live in database/google_service_account.json (a real file,
not an .env line) because service account private keys are multi-line
and some .env loaders mangle the embedded \n characters. Only the
sheet ID needs to be in .env:
  GOOGLE_SHEET_ID=your-sheet-id

Sheets mirroring turns on automatically once GOOGLE_SHEET_ID is set and
the credentials file exists. Set GOOGLE_SHEETS_ENABLED=0 to force it off.
"""
import os
import json
import logging

log = logging.getLogger(__name__)

_client = None
_spreadsheet = None
_worksheet_cache = {}

CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "google_service_account.json")

SUBSCRIBER_HEADERS = [
    "id", "telegram_user_id", "telegram_username", "button_text", "duration_type",
    "price", "currency", "status", "auto_renew", "start_date", "expiry_date",
    "provider", "created_at",
]
PAYMENT_HEADERS = [
    "id", "subscriber_id", "telegram_user_id", "amount", "currency",
    "status", "failure_reason", "provider", "provider_ref", "created_at",
]
PANEL_HEADERS = ["id", "name", "message_text", "buttons", "active", "created_at"]


def is_enabled():
    if os.environ.get("GOOGLE_SHEETS_ENABLED", "1") == "0":
        return False
    return bool(os.environ.get("GOOGLE_SHEET_ID")) and os.path.exists(CREDENTIALS_PATH)


def _get_spreadsheet():
    global _client, _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    import gspread
    from google.oauth2.service_account import Credentials

    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set in .env")
    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(f"Credentials file not found at {CREDENTIALS_PATH}")

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    _client = gspread.authorize(creds)
    _spreadsheet = _client.open_by_key(sheet_id)
    return _spreadsheet


def _get_worksheet(title, headers):
    if title in _worksheet_cache:
        return _worksheet_cache[title]

    sh = _get_spreadsheet()
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
        ws.append_row(headers)

    _worksheet_cache[title] = ws
    return ws


def _upsert_row(title, headers, row_dict):
    """Find a row by its 'id' value in column A; update it, or append a new one."""
    ws = _get_worksheet(title, headers)
    row_id = str(row_dict.get("id", ""))
    if not row_id:
        return

    values = [str(row_dict.get(h, "")) for h in headers]

    id_column = ws.col_values(1)  # column A = id
    if row_id in id_column:
        row_number = id_column.index(row_id) + 1  # 1-indexed
        ws.update(f"A{row_number}:{chr(64 + len(headers))}{row_number}", [values])
    else:
        ws.append_row(values)


def mirror_subscriber(sub_dict):
    if not is_enabled():
        return
    try:
        _upsert_row("Subscribers", SUBSCRIBER_HEADERS, sub_dict)
    except Exception as e:
        log.warning("Sheets mirror (subscriber) failed: %s", e)


def mirror_payment(payment_dict):
    if not is_enabled():
        return
    try:
        _upsert_row("Payments", PAYMENT_HEADERS, payment_dict)
    except Exception as e:
        log.warning("Sheets mirror (payment) failed: %s", e)


def mirror_panel(panel_dict):
    if not is_enabled():
        return
    try:
        d = dict(panel_dict)
        d["buttons"] = str(d.get("buttons", ""))
        _upsert_row("Panels", PANEL_HEADERS, d)
    except Exception as e:
        log.warning("Sheets mirror (panel) failed: %s", e)


def mirror_panel_deleted(panel_id):
    if not is_enabled():
        return
    try:
        ws = _get_worksheet("Panels", PANEL_HEADERS)
        id_column = ws.col_values(1)
        row_id = str(panel_id)
        if row_id in id_column:
            row_number = id_column.index(row_id) + 1
            ws.delete_rows(row_number)
    except Exception as e:
        log.warning("Sheets mirror (panel delete) failed: %s", e)
