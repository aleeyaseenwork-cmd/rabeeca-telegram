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
import logging

import stripe
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import dal

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5000")

stripe.api_key = STRIPE_SECRET_KEY

dal.init_db()

PROVIDER_LABELS = {"stripe": "Stripe", "ccbill": "CCBill", "paypal": "PayPal"}
DURATION_LABELS = {"weekly": "week", "monthly": "month", "onetime": "lifetime"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


def get_active_providers():
    raw = dal.get_setting("active_providers", "stripe")
    return [p.strip() for p in raw.split(",") if p.strip()]


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: plan chosen. If only one provider is active, skip straight
    to payment. If several are active, ask which one to pay with."""
    query = update.callback_query
    await query.answer()

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


def build_checkout_link(telegram_user_id, telegram_username, plan, provider):
    """
    Builds the real payment link for the chosen plan. Stripe is fully wired
    up (test or live key, whichever is in .env). CCBill/PayPal return a
    placeholder until you wire up their SDKs the same way.

    The plan details ride along as Stripe metadata (nothing is written to
    the database yet) — the webhook in app/app.py reads that metadata once
    payment is confirmed and applies the purchase there (new sub, or
    stacking/renewal onto an existing one). client_reference_id carries
    the Telegram user id so the webhook knows who to activate.
    """
    if provider == "stripe":
        try:
            is_subscription = plan["duration_type"] in ("weekly", "monthly")
            line_item = {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": plan["text"]},
                    "unit_amount": int(round(plan["price"] * 100)),
                },
                "quantity": 1,
            }
            if is_subscription:
                interval = "week" if plan["duration_type"] == "weekly" else "month"
                line_item["price_data"]["recurring"] = {"interval": interval}

            session = stripe.checkout.Session.create(
                mode="subscription" if is_subscription else "payment",
                line_items=[line_item],
                client_reference_id=str(telegram_user_id),
                metadata={
                    "telegram_user_id": str(telegram_user_id),
                    "telegram_username": telegram_username or "",
                    "button_text": plan["text"],
                    "duration_type": plan["duration_type"],
                    "price": str(plan["price"]),
                },
                success_url=f"{DASHBOARD_URL}/checkout/success",
                cancel_url=f"{DASHBOARD_URL}/checkout/cancel",
            )
            return session.url
        except Exception as e:
            log.error("Stripe checkout session failed: %s", e)
            return None
    elif provider == "ccbill":
        # TODO: wire up CCBill the same way once CCBILL_* env vars are set
        return f"{DASHBOARD_URL}/checkout/ccbill?ref={telegram_user_id}&price={plan['price']}"
    else:
        # TODO: wire up PayPal the same way once ready
        return f"{DASHBOARD_URL}/checkout/paypal?ref={telegram_user_id}&price={plan['price']}"


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
                if GROUP_CHAT_ID:
                    await context.bot.ban_chat_member(chat_id=GROUP_CHAT_ID, user_id=int(sub["telegram_user_id"]))
                    await context.bot.unban_chat_member(chat_id=GROUP_CHAT_ID, user_id=int(sub["telegram_user_id"]))
                dal.update_subscriber(sub["id"], {"status": "expired"})
            except Exception as e:
                log.warning("Could not remove user %s: %s", sub["telegram_user_id"], e)


def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing from .env — see README setup steps.")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(handle_pay, pattern=r"^pay:"))

    # daily reminder/auto-kick job, runs once every 24h
    app.job_queue.run_repeating(send_reminders_and_kick, interval=24 * 60 * 60, first=10)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
