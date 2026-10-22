"""
Which bot ("slot") the running code is working for.

One project can run several Telegram bots, each with its OWN completely separate
data (plans, members, payments, bans, broadcasts, settings, channel). Bot 1 uses
exactly the database it always has, so nothing existing moves. Bot 2, 3... each get
their own database next to it (data_2.db for SQLite, "<MONGODB_DB_NAME>_2" for Mongo).

- The bot process runs for ONE slot: BOT_SLOT=2 python bot/bot.py (default 1).
- The dashboard serves all slots; the selected slot lives in the login session and
  is switched per request with set_for_request().
- Bot tokens live ONLY in the server's .env (BOT_TOKEN_1, BOT_TOKEN_2, ...), never
  in the dashboard or the database. BOT_TOKEN is still accepted for bot 1.
"""
import os
import contextvars
from contextlib import contextmanager

MAX_SLOTS = 9

_current = contextvars.ContextVar("bot_slot", default=None)
_default = 1


def set_default(n):
    """Process-wide default (used by the bot process: BOT_SLOT)."""
    global _default
    _default = int(n)


def get():
    v = _current.get()
    return v if v is not None else _default


def set_for_request(n):
    """Switch the current request/context to bot `n`."""
    _current.set(int(n))


@contextmanager
def use(n):
    """Temporarily work on bot `n`: `with slot.use(2): dal.get_stats()`."""
    token = _current.set(int(n))
    try:
        yield
    finally:
        _current.reset(token)


def token_env_name(n=None):
    return f"BOT_TOKEN_{n or get()}"


def bot_token(n=None):
    """The bot token for slot `n` from the environment ('' if missing)."""
    n = n or get()
    token = (os.environ.get(f"BOT_TOKEN_{n}") or "").strip()
    if not token and n == 1:
        token = (os.environ.get("BOT_TOKEN") or "").strip()   # the original variable name
    return token


def configured_slots():
    """Slot 1 always exists; slots 2..9 exist once BOT_TOKEN_<n> is set in .env."""
    return [1] + [n for n in range(2, MAX_SLOTS + 1) if bot_token(n)]
