"""
Live Google Sheets mirror.
Every write to the database (subscriber, payment, panel) also gets
upserted into a Google Sheet, so there's always a human-readable,
independent copy of the data sitting outside MongoDB.

This is best-effort: if Sheets is unreachable or misconfigured, writes
to the real database still succeed, and a warning is logged. The
dashboard/bot never depend on Sheets being up.

Requires in .env:
  GOOGLE_SHEETS_ENABLED=1
  GOOGLE_SHEETS_CREDENTIALS_FILE=credentials.json   (service account key)
  GOOGLE_SHEETS_SPREADSHEET_ID=your-sheet-id
"""
import os
import logging

log = logging.getLogger(__name__)

_client = None
_spreadsheet = None
_worksheet_cache = {}

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
    return os.environ.get("GOOGLE_SHEETS_ENABLED", "0") == "1"


def _get_spreadsheet():
    global _client, _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    import gspread
    from google.oauth2.service_account import Credentials

    creds_file = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json")
    sheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID is not set")

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
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
