"""
Telegram subscription bot.

- /start shows the active panel with its buttons (from the dashboard).
- Tapping a button creates a Stripe Checkout link (test mode by default).
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

DURATION_DAYS = {"weekly": 7, "monthly": 30, "onetime": 36500}  # ~100yr for lifetime


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    panel = dal.get_active_panel()
    if not panel:
        await update.message.reply_text("No plans are available right now. Please check back later.")
        return

    buttons = panel["buttons"]
    keyboard = [
        [InlineKeyboardButton(b["text"], callback_data=f"buy:{i}")]
        for i, b in enumerate(buttons)
    ]
    context.user_data["panel_buttons"] = buttons  # cache for callback lookup

    await update.message.reply_text(
        panel["message_text"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    buttons = context.user_data.get("panel_buttons")
    if not buttons:
        panel = dal.get_active_panel()
        buttons = panel["buttons"] if panel else []

    idx = int(query.data.split(":")[1])
    if idx >= len(buttons):
        await query.edit_message_text("This plan is no longer available, please /start again.")
        return

    chosen = buttons[idx]
    user = query.from_user
    provider = dal.get_setting("active_provider", "stripe")

    # Create the pending subscriber record now; it flips to active once
    # the payment webhook confirms success.
    dal.create_pending_subscriber(
        user.id, user.username, None, chosen["text"], chosen["duration_type"], chosen["price"], provider,
    )

    checkout_link = build_checkout_link(user.id, chosen, provider)

    if checkout_link is None:
        await query.edit_message_text(
            "Sorry, something went wrong setting up payment. Please try again in a moment or contact support."
        )
        return

    buy_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Buy Now", url=checkout_link)]])

    await query.edit_message_text(
        f"You selected: {chosen['text']} ({chosen['price']})\n\n"
        "Tap the button below to complete payment.\n\n"
        "You'll be added to the group automatically once payment is confirmed.",
        reply_markup=buy_keyboard,
    )


def build_checkout_link(telegram_user_id, plan, provider):
    """
    Builds the real payment link for the chosen plan. Stripe is fully wired
    up (test or live key, whichever is in .env). PayPal/CCBill return a
    placeholder until you wire up their SDKs the same way.
    client_reference_id carries the Telegram user id so the webhook in
    app/app.py knows who to activate once payment succeeds.
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
                success_url=f"{DASHBOARD_URL}/checkout/success",
                cancel_url=f"{DASHBOARD_URL}/checkout/cancel",
            )
            return session.url
        except Exception as e:
            log.error("Stripe checkout session failed: %s", e)
            return None
    elif provider == "paypal":
        # TODO: wire up PayPal SDK the same way once ready
        return f"{DASHBOARD_URL}/checkout/paypal?ref={telegram_user_id}&price={plan['price']}"
    else:
        # TODO: wire up CCBill the same way once ready
        return f"{DASHBOARD_URL}/checkout/ccbill?ref={telegram_user_id}&price={plan['price']}"


async def send_reminders_and_kick(context: ContextTypes.DEFAULT_TYPE):
    """Daily job: reminds users expiring soon, optionally kicks expired ones."""
    reminder_days = int(dal.get_setting("reminder_days_before", "3"))
    auto_kick = dal.get_setting("auto_kick_on_expiry", "0") == "1"

    expiring = dal.get_expiring_subscribers(reminder_days)
    for sub in expiring:
        try:
            await context.bot.send_message(
                chat_id=int(sub["telegram_user_id"]),
                text=f"Your subscription ({sub['button_text']}) expires soon. "
                     f"Use /start to renew and stay in the group.",
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

    # daily reminder/auto-kick job, runs once every 24h
    app.job_queue.run_repeating(send_reminders_and_kick, interval=24 * 60 * 60, first=10)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
