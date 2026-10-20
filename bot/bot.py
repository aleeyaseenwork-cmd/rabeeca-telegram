"""
Telegram subscription bot.

- /start shows the active panel with its buttons (from the dashboard).
- Tapping a button creates a real Stripe Checkout session.
- Nothing is written to the database until the webhook (app/app.py)
  confirms payment succeeded — that's also where the purchase (new or
  renewal/stacking) actually gets applied, see database/dal.py apply_purchase.
- A daily job sends renewal reminders and optionally auto-kicks expired members.

Run with: python bot.py
Requires BOT_TOKEN, GROUP_CHAT_ID, STRIPE_SECRET_KEY in .env (see README).
"""
import os
import sys
import hashlib
import logging
import asyncio
import math
from datetime import datetime
from urllib.parse import urlencode

import stripe
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import dal

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GROUP_CHAT_ID_ENV = os.environ.get("GROUP_CHAT_ID", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5000")

VEROTEL_SHOP_ID = os.environ.get("VEROTEL_SHOP_ID", "")
VEROTEL_SIGNATURE_KEY = os.environ.get("VEROTEL_SIGNATURE_KEY", "")

stripe.api_key = STRIPE_SECRET_KEY

dal.init_db()


def _group_chat_id():
    """Channel/group the bot manages — set from Settings in the dashboard,
    with the GROUP_CHAT_ID env var only as a fallback."""
    return (dal.get_setting("group_chat_id", "") or "").strip() or GROUP_CHAT_ID_ENV


BANNED_TEXT = "🚫 Sorry, you can't use this service."

# dashboard currency symbol -> ISO code the payment providers bill in
CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP", "CHF": "CHF", "CA$": "CAD", "A$": "AUD"}


def _currency_code():
    symbol = (dal.get_setting("currency_symbol", "$") or "$").strip()
    return CURRENCY_CODES.get(symbol, "USD")

PROVIDER_LABELS = {"stripe": "Stripe", "ccbill": "CCBill", "verotel": "CardBilling", "paypal": "PayPal"}
DURATION_LABELS = {"weekly": "week", "monthly": "month", "quarterly": "3 months", "semiannual": "6 months", "yearly": "year", "onetime": "lifetime", "custom": "custom"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if dal.is_banned(update.effective_user.id):
        await update.message.reply_text(BANNED_TEXT)
        return

    panel = dal.get_active_panel()
    if not panel:
        await update.message.reply_text(
            "🚧 No plans are live right now — check back soon!"
        )
        return

    buttons = panel["buttons"]
    keyboard = [
        [InlineKeyboardButton(f"💎 {b['text']}", callback_data=f"buy:{i}")]
        for i, b in enumerate(buttons)
    ]
    context.user_data["panel_buttons"] = buttons  # cache for callback lookup

    await update.message.reply_text(
        f"✨ *{panel['name']}* ✨\n\n{panel['message_text']}\n\n👇 Pick a plan to get started:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if dal.is_banned(update.effective_user.id):
        await update.message.reply_text(BANNED_TEXT)
        return

    await update.message.reply_text(
        "Here's what I can do:\n\n"
        "/start - See the available plans and subscribe\n"
        "/status - Check your plan, expiry date and renewal\n"
        "/renew - Renew or add more time to your plan\n"
        "/cancel - Cancel your subscription's auto-renewal (you keep access until it ends)\n"
        "/help - Show this list of commands"
    )


def _plan_timing(sub):
    """(expiry datetime or None, is_lifetime, is_active_now) for a subscriber row."""
    try:
        expiry = datetime.fromisoformat(sub.get("expiry_date") or "")
    except ValueError:
        return None, False, False
    lifetime = expiry.year >= 2100
    active = sub.get("status") == "active" and expiry > datetime.utcnow()
    return expiry, lifetime, active


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if dal.is_banned(user.id):
        await update.message.reply_text(BANNED_TEXT)
        return

    sub = dal.get_subscriber_by_telegram_id(user.id)
    expiry, lifetime, active = _plan_timing(sub) if sub else (None, False, False)
    if not sub or not expiry:
        await update.message.reply_text(
            "ℹ️ You don't have a subscription yet. Send /start to see the plans."
        )
        return

    lines = [f"📋 Plan: {sub.get('button_text') or 'Subscription'}"]
    if active:
        lines.append("Status: ✅ Active")
        if lifetime:
            lines.append("Expires: never (lifetime access)")
            lines.append("Days left: unlimited")
            lines.append("Renewal: ♾️ One-time, lifetime access")
        else:
            days_left = max(1, math.ceil((expiry - datetime.utcnow()).total_seconds() / 86400))
            lines.append(f"Expires: {expiry.date().isoformat()}")
            lines.append(f"Days left: {days_left}")
            if sub.get("cancelled"):
                lines.append(f"Renewal: 🛑 Cancelled, it won't renew and access ends on {expiry.date().isoformat()}")
            elif sub.get("auto_renew"):
                lines.append("Renewal: 🔁 Auto-renews")
            else:
                lines.append("Renewal: 1️⃣ One-time, it doesn't renew automatically")
    else:
        lines.append("Status: ❌ Expired")
        lines.append(f"Ended: {expiry.date().isoformat()}")
        lines.append("\nSend /renew to get access again.")

    await update.message.reply_text("\n".join(lines))


async def renew_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if dal.is_banned(user.id):
        await update.message.reply_text(BANNED_TEXT)
        return

    panel = dal.get_active_panel()
    if not panel:
        await update.message.reply_text(
            "🚧 No plans are live right now — check back soon!"
        )
        return

    sub = dal.get_subscriber_by_telegram_id(user.id)
    expiry, lifetime, active = _plan_timing(sub) if sub else (None, False, False)
    if active and lifetime:
        await update.message.reply_text("♾️ You already have lifetime access, so there's nothing to renew.")
        return
    if active:
        note = f"⏳ Buying now adds time on top of your current end date (*{expiry.date().isoformat()}*)."
        if sub.get("auto_renew") and not sub.get("cancelled"):
            note += "\n\nYou're already on auto-renew, so only buy if you want extra time on top."
    elif expiry:
        note = "⏳ Your plan has ended, so buying now starts a fresh period from today."
    else:
        note = "👇 Pick a plan to get started."

    buttons = panel["buttons"]
    keyboard = [
        [InlineKeyboardButton(f"💎 {b['text']}", callback_data=f"buy:{i}")]
        for i, b in enumerate(buttons)
    ]
    context.user_data["panel_buttons"] = buttons  # cache for callback lookup

    await update.message.reply_text(
        f"🔁 *{panel['name']}*\n\n{panel['message_text']}\n\n{note}\n\n👇 Pick a plan:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


def get_active_providers():
    raw = dal.get_setting("active_providers", "stripe")
    return [p.strip() for p in raw.split(",") if p.strip()]


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: plan chosen. If only one provider is active, skip straight
    to payment. If several are active, ask which one to pay with."""
    query = update.callback_query
    await query.answer()
    if dal.is_banned(query.from_user.id):
        await query.edit_message_text(BANNED_TEXT)
        return

    buttons = context.user_data.get("panel_buttons")
    if not buttons:
        panel = dal.get_active_panel()
        buttons = panel["buttons"] if panel else []

    idx = int(query.data.split(":")[1])
    if idx >= len(buttons):
        await query.edit_message_text("⚠️ This plan is no longer available — send /start to see current plans.")
        return

    chosen = buttons[idx]
    providers = get_active_providers()

    if len(providers) <= 1:
        provider = providers[0] if providers else "stripe"
        await _send_payment_link(query, chosen, provider)
        return

    keyboard = [
        [InlineKeyboardButton(f"💳 Pay with {PROVIDER_LABELS.get(p, p.title())}", callback_data=f"pay:{idx}:{p}")]
        for p in providers
    ]
    context.user_data["panel_buttons"] = buttons
    await query.edit_message_text(
        f"🎉 Great choice: *{chosen['text']}*\n\n💰 Price: *{chosen['price']}*\n\nHow would you like to pay?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2 (only reached with multiple active providers): provider chosen."""
    query = update.callback_query
    await query.answer()
    if dal.is_banned(query.from_user.id):
        await query.edit_message_text(BANNED_TEXT)
        return

    buttons = context.user_data.get("panel_buttons")
    if not buttons:
        panel = dal.get_active_panel()
        buttons = panel["buttons"] if panel else []

    _, idx_str, provider = query.data.split(":", 2)
    idx = int(idx_str)
    if idx >= len(buttons):
        await query.edit_message_text("⚠️ This plan is no longer available — send /start to see current plans.")
        return

    chosen = buttons[idx]
    await _send_payment_link(query, chosen, provider)


async def _send_payment_link(query, chosen, provider):
    user = query.from_user
    checkout_link = build_checkout_link(user.id, user.username, chosen, provider)

    if checkout_link is None:
        await query.edit_message_text(
            "😕 Something went wrong setting up payment. Please try again in a moment, or contact support."
        )
        return

    label = f"💳 Pay with {PROVIDER_LABELS.get(provider, provider.title())}"
    buy_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(label, url=checkout_link)]])

    await query.edit_message_text(
        f"🛒 *{chosen['text']}* — {chosen['price']}\n\n"
        "👉 Tap below to complete your payment.\n\n"
        "🔑 You'll get your group invite link automatically the moment payment is confirmed!",
        reply_markup=buy_keyboard,
        parse_mode="Markdown",
    )


STRIPE_RECURRING = {
    "weekly": {"interval": "week", "interval_count": 1},
    "monthly": {"interval": "month", "interval_count": 1},
    "quarterly": {"interval": "month", "interval_count": 3},
    "semiannual": {"interval": "month", "interval_count": 6},
    "yearly": {"interval": "year", "interval_count": 1},
}

# ISO-8601 periods FlexPay (Verotel/CardBilling) expects for subscriptionType=recurring
VEROTEL_PERIODS = {
    "weekly": "P1W",
    "monthly": "P1M",
    "quarterly": "P3M",
    "semiannual": "P6M",
    "yearly": "P1Y",
}


def verotel_signature(signature_key, params):
    """
    FlexPay's signature: SHA-1 of "signatureKey:key1=val1:key2=val2:..."
    with every param (excluding 'signature' itself) sorted alphabetically
    by key. Used both when building a checkout link and when verifying an
    incoming postback in app/app.py.
    """
    parts = [f"{k}={v}" for k, v in sorted(params.items()) if k != "signature"]
    base = signature_key + ":" + ":".join(parts)
    return hashlib.sha1(base.encode()).hexdigest()


def _plan_auto_renew(plan):
    """Whether this plan should auto-renew. Uses the plan's own "auto_renew" flag
    (set per button in the dashboard's panel editor). Plans saved before that flag
    existed have no such key, so they fall back to the old global
    auto_billing_enabled setting and keep behaving exactly as before."""
    value = plan.get("auto_renew")
    if value is None:
        return dal.get_setting("auto_billing_enabled", "1") == "1"
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def build_checkout_link(telegram_user_id, telegram_username, plan, provider):
    """
    Builds the real payment link for the chosen plan. Stripe and
    Verotel/CardBilling (FlexPay) are both fully wired up. CCBill/PayPal
    return a placeholder until you wire up their SDKs the same way.

    Each plan's own "auto_renew" flag (Auto-renew checkbox in the panel
    editor) controls whether it is a real recurring subscription. That only
    happens for weekly/monthly/quarterly/semiannual/yearly plans; otherwise
    (flag off, or a lifetime/custom plan) it is a single one-off charge that
    simply ends after the purchased duration and relies on the reminder job.
    Plans without the flag fall back to the global auto_billing_enabled
    setting (see _plan_auto_renew) — same rule, both providers.

    The plan details ride along as metadata/custom fields (nothing is
    written to the database yet) — the matching webhook in app/app.py
    reads that back once payment is confirmed and applies the purchase
    there (new sub, or stacking/renewal onto an existing one).
    """
    auto_billing = _plan_auto_renew(plan)
    custom_days = plan.get("custom_days", "") if plan["duration_type"] == "custom" else ""

    if provider == "stripe":
        try:
            is_subscription = auto_billing and plan["duration_type"] in STRIPE_RECURRING
            line_item = {
                "price_data": {
                    "currency": _currency_code().lower(),
                    "product_data": {"name": plan["text"]},
                    "unit_amount": int(round(plan["price"] * 100)),
                },
                "quantity": 1,
            }
            if is_subscription:
                line_item["price_data"]["recurring"] = STRIPE_RECURRING[plan["duration_type"]]

            session = stripe.checkout.Session.create(
                mode="subscription" if is_subscription else "payment",
                line_items=[line_item],
                client_reference_id=str(telegram_user_id),
                metadata={
                    "telegram_user_id": str(telegram_user_id),
                    "telegram_username": telegram_username or "",
                    "button_text": plan["text"],
                    "duration_type": plan["duration_type"],
                    "custom_days": str(custom_days),
                    "price": str(plan["price"]),
                },
                success_url=f"{DASHBOARD_URL}/checkout/success",
                cancel_url=f"{DASHBOARD_URL}/checkout/cancel",
            )
            return session.url
        except Exception as e:
            log.error("Stripe checkout session failed: %s", e)
            return None

    elif provider == "verotel":
        if not VEROTEL_SHOP_ID or not VEROTEL_SIGNATURE_KEY:
            log.error("Verotel/CardBilling not configured: set VEROTEL_SHOP_ID and VEROTEL_SIGNATURE_KEY in .env")
            return None
        try:
            is_subscription = auto_billing and plan["duration_type"] in VEROTEL_PERIODS
            params = {
                "shopID": VEROTEL_SHOP_ID,
                "priceAmount": f"{plan['price']:.2f}",
                "priceCurrency": _currency_code(),
                "version": "3.5",
                "description": plan["text"][:255],
                "custom1": str(telegram_user_id),
                "custom2": plan["duration_type"],
                "custom3": str(custom_days),
                "type": "subscription" if is_subscription else "purchase",
                "successURL": f"{DASHBOARD_URL}/checkout/success",
            }
            if is_subscription:
                params["subscriptionType"] = "recurring"
                params["period"] = VEROTEL_PERIODS[plan["duration_type"]]

            params["signature"] = verotel_signature(VEROTEL_SIGNATURE_KEY, params)
            return f"https://secure.verotel.com/startorder?{urlencode(params)}"
        except Exception as e:
            log.error("Verotel checkout link failed: %s", e)
            return None

    elif provider == "ccbill":
        # TODO: wire up CCBill the same way once CCBILL_* env vars are set
        return f"{DASHBOARD_URL}/checkout/ccbill?ref={telegram_user_id}&price={plan['price']}"
    else:
        # TODO: wire up PayPal the same way once ready
        return f"{DASHBOARD_URL}/checkout/paypal?ref={telegram_user_id}&price={plan['price']}"


def _cancel_provider_billing(sub):
    """Ends the real recurring billing at the provider (blocking calls —
    run via asyncio.to_thread). Returns True if the provider accepted it."""
    provider = sub.get("provider")

    if provider == "stripe" and sub.get("provider_subscription_id"):
        # cancel_at_period_end: no further charges, but they keep access
        # until the period they already paid for runs out
        stripe.Subscription.modify(sub["provider_subscription_id"], cancel_at_period_end=True)
        return True

    if provider == "verotel" and VEROTEL_SHOP_ID and VEROTEL_SIGNATURE_KEY:
        from urllib.request import urlopen
        # the sale id of their latest successful CardBilling payment
        payments = [
            p for p in dal.get_dev_table("payments")
            if str(p.get("telegram_user_id")) == str(sub["telegram_user_id"])
            and p.get("provider") == "verotel" and p.get("status") == "successful" and p.get("provider_ref")
        ]
        if not payments:
            return False
        sale_id = sorted(payments, key=lambda p: p.get("created_at", ""))[-1]["provider_ref"]
        params = {"saleID": sale_id, "shopID": VEROTEL_SHOP_ID, "version": "3.5"}
        params["signature"] = verotel_signature(VEROTEL_SIGNATURE_KEY, params)
        with urlopen(f"https://secure.verotel.com/cancel-subscription?{urlencode(params)}", timeout=15) as resp:
            return 200 <= resp.status < 300

    return False


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel — member turns off their own auto-renewal. They keep access
    until the end of the period they already paid for."""
    user = update.effective_user
    sub = dal.get_subscriber_by_telegram_id(user.id)

    if not sub or sub.get("status") != "active":
        await update.message.reply_text("You don't have an active subscription to cancel.")
        return
    if sub.get("cancelled"):
        await update.message.reply_text(
            "ℹ️ Your subscription is already cancelled — you keep access until "
            f"*{(sub.get('expiry_date') or '')[:10]}* and won't be charged again.",
            parse_mode="Markdown",
        )
        return
    if not sub.get("auto_renew"):
        await update.message.reply_text(
            "ℹ️ Your plan isn't set to auto-renew, so there's nothing to cancel — "
            f"it simply ends on *{(sub.get('expiry_date') or '')[:10]}*.",
            parse_mode="Markdown",
        )
        return

    try:
        import asyncio
        cancelled_ok = await asyncio.to_thread(_cancel_provider_billing, sub)
    except Exception as e:
        log.error("Provider cancel failed for %s: %s", user.id, e)
        cancelled_ok = False

    if not cancelled_ok:
        # False = nothing was actually cancelled at the provider (no provider
        # subscription id, Verotel sale not found, unsupported provider)
        log.warning("Provider billing was not cancelled for %s (provider=%s)", user.id, sub.get("provider"))
        await update.message.reply_text(
            "😕 I couldn't cancel that automatically. Please contact support and we'll sort it out."
        )
        return

    # auto_renew stays as-is; "cancelled" is what marks the member as cancelled
    dal.update_subscriber(sub["id"], {"cancelled": 1})
    await update.message.reply_text(
        "✅ *Auto-renewal cancelled.*\n\n"
        f"You keep full access until *{(sub.get('expiry_date') or '')[:10]}* and won't be charged again.",
        parse_mode="Markdown",
    )


async def send_reminders_and_kick(context: ContextTypes.DEFAULT_TYPE):
    """Daily job: reminds users expiring soon, optionally kicks expired ones."""
    reminder_days = int(dal.get_setting("reminder_days_before", "3"))
    auto_kick = dal.get_setting("auto_kick_on_expiry", "0") == "1"

    expiring = dal.get_expiring_subscribers(reminder_days)
    for sub in expiring:
        try:
            await context.bot.send_message(
                chat_id=int(sub["telegram_user_id"]),
                text=f"⏰ Heads up! Your *{sub['button_text']}* plan expires soon.\n\n"
                     f"Send /start to renew and keep your access.",
                parse_mode="Markdown",
            )
        except Exception as e:
            log.warning("Could not message user %s: %s", sub["telegram_user_id"], e)

    if auto_kick:
        expired = dal.get_expired_subscribers()
        for sub in expired:
            try:
                if _group_chat_id():
                    await context.bot.ban_chat_member(chat_id=_group_chat_id(), user_id=int(sub["telegram_user_id"]))
                    await context.bot.unban_chat_member(chat_id=_group_chat_id(), user_id=int(sub["telegram_user_id"]))
                dal.update_subscriber(sub["id"], {"status": "expired"})
            except Exception as e:
                log.warning("Could not remove user %s: %s", sub["telegram_user_id"], e)


# ------------------------------------------------------------- broadcasts --
# The dashboard only queues broadcasts in the database. This job, run every 60 s,
# is the ONLY thing that sends them.

BROADCAST_SEND_DELAY = 0.05      # seconds between messages (~20/s, Telegram allows ~30/s)
BROADCAST_PROGRESS_EVERY = 25    # write sent/failed counts to the DB every N recipients
_broadcast_lock = asyncio.Lock() # one broadcast tick at a time

# BadRequest messages that mean "this recipient is unreachable", not "the media is bad"
_RECIPIENT_BAD_REQUESTS = ("chat not found", "user not found", "peer_id_invalid", "user is deactivated")


def _broadcast_media_path(b):
    return os.path.join(dal.BROADCAST_MEDIA_DIR, os.path.basename(b.get("media_file") or ""))


def _delete_broadcast_media(b):
    if b.get("media_file"):
        try:
            os.remove(_broadcast_media_path(b))
        except OSError:
            pass


async def _broadcast_send_one(bot, chat_id, text, media_type, media_path, state):
    """Sends one broadcast message. Returns ("ok"|"failed"|"abort", error_text).
    "failed" includes blocked users. "abort" = the media itself was rejected, so
    there is no point trying everyone else. The file is uploaded once; after that
    its Telegram file_id is reused for every other recipient."""
    for _ in range(3):
        try:
            if not media_type:
                await bot.send_message(chat_id=chat_id, text=text)
            else:
                send = {"photo": bot.send_photo, "video": bot.send_video, "document": bot.send_document}[media_type]
                if state["file_id"]:
                    await send(chat_id=chat_id, caption=text or None, **{media_type: state["file_id"]})
                else:
                    with open(media_path, "rb") as fh:
                        msg = await send(chat_id=chat_id, caption=text or None, **{media_type: fh},
                                         read_timeout=300, write_timeout=300)
                    if media_type == "photo":
                        state["file_id"] = msg.photo[-1].file_id
                    else:
                        state["file_id"] = getattr(msg, media_type).file_id
            return "ok", None
        except RetryAfter as e:
            wait = e.retry_after.total_seconds() if hasattr(e.retry_after, "total_seconds") else e.retry_after
            await asyncio.sleep(float(wait) + 1)      # Telegram did NOT send it, safe to retry
        except Forbidden as e:
            return "failed", str(e)                    # user blocked the bot / deactivated
        except BadRequest as e:
            msg_l = str(e).lower()
            if media_type and not state["file_id"] and not any(k in msg_l for k in _RECIPIENT_BAD_REQUESTS):
                return "abort", f"Telegram rejected the media: {e}"
            return "failed", str(e)
        except Exception as e:                         # timeouts etc: NOT retried, it may have been delivered
            return "failed", str(e)
    return "failed", "Telegram kept asking to slow down"


async def _run_broadcast(bot, b):
    bid = b["id"]
    sent = failed = 0
    try:
        recipients = dal.get_broadcast_recipients(b["audience"])   # banned users already excluded
        dal.update_broadcast_progress(bid, 0, 0, total=len(recipients))
        text = b.get("message_text") or ""
        media_type = b.get("media_type") or None
        media_path = _broadcast_media_path(b) if media_type else None
        if media_type and not os.path.isfile(media_path):
            dal.finish_broadcast(bid, "failed", 0, 0,
                                 "The media file wasn't found on the bot's server (the dashboard and bot must share BROADCAST_MEDIA_DIR).")
            return

        state = {"file_id": None}
        status, error = "sent", None
        for i, uid in enumerate(recipients, 1):
            result, err = await _broadcast_send_one(bot, int(uid), text, media_type, media_path, state)
            if result == "ok":
                sent += 1
            elif result == "abort":
                status, error = "failed", err
                break
            else:
                failed += 1      # includes users who blocked the bot
            if i % BROADCAST_PROGRESS_EVERY == 0:
                dal.update_broadcast_progress(bid, sent, failed)
            await asyncio.sleep(BROADCAST_SEND_DELAY)

        dal.finish_broadcast(bid, status, sent, failed, error)
    except Exception as e:
        log.exception("Broadcast %s crashed", bid)
        dal.finish_broadcast(bid, "failed", sent, failed, f"Unexpected error: {e}"[:300])
    finally:
        _delete_broadcast_media(b)


async def process_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    """Every 60 s: send every broadcast that is due. dal.claim_due_broadcast() is atomic,
    so a broadcast can never be picked up (and therefore sent) twice."""
    if _broadcast_lock.locked():
        return   # a previous tick is still sending
    async with _broadcast_lock:
        while True:
            b = dal.claim_due_broadcast()
            if not b:
                break
            log.info("Sending broadcast %s (audience=%s)", b["id"], b["audience"])
            await _run_broadcast(context.bot, b)


def _recover_stuck_broadcasts():
    """Startup: a broadcast still marked 'sending' died with the previous run. Mark it
    'interrupted' (never resume it, that could double-send) and drop its media file."""
    try:
        for b in dal.get_broadcasts(100):
            if b.get("status") == "sending":
                _delete_broadcast_media(b)
        n = dal.interrupt_stuck_broadcasts()
        if n:
            log.warning("%s broadcast(s) were interrupted by the last shutdown", n)
    except Exception as e:
        log.warning("Could not check for interrupted broadcasts: %s", e)


BOT_COMMANDS = [
    ("start", "See the plans and subscribe"),
    ("status", "Check your plan, expiry date and renewal"),
    ("renew", "Renew or add more time to your plan"),
    ("cancel", "Cancel auto-renewal"),
    ("help", "Show all commands"),
]


async def _post_init(application):
    """Fills the '/' command menu in Telegram."""
    try:
        await application.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
    except Exception as e:
        log.warning("Could not set the command menu: %s", e)


def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing from .env — see README setup steps.")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("renew", renew_command))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(handle_pay, pattern=r"^pay:"))

    # daily reminder/auto-kick job, runs once every 24h
    app.job_queue.run_repeating(send_reminders_and_kick, interval=24 * 60 * 60, first=10)

    # broadcasts queued from the dashboard: checked every 60 seconds
    _recover_stuck_broadcasts()
    app.job_queue.run_repeating(process_broadcasts, interval=60, first=15)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
