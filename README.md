# Telegram Subscription Bot + Dashboard

What you're getting:
- A password-protected web dashboard (subscribers table, plan/panel builder, CSV export, settings, developer JSON view)
- A Telegram bot that posts your plan panel and starts checkout
- A shared database both use — SQLite by default, or MongoDB if you switch one setting

Everything below assumes basic command line use. If you're not comfortable with the VPS part, that's the part Ali will help you set up.

---

## 1. What you need before starting

- A VPS (Ubuntu 22.04+ is easiest) or any machine that can stay online
- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram group/channel's chat ID (the bot needs to be an admin there)
- Stripe account (test mode keys are enough to try everything first)

---

## 2. Install

```bash
# unzip the project, then:
cd telegram-sub-bot

# create a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# install dependencies
pip install -r requirements.txt

# copy the env template and fill in your values
cp .env.example .env
nano .env
```

Fill in `.env`:
- `DASHBOARD_PASSWORD` — the password to log into the dashboard
- `SECRET_KEY` — any random string (used to secure the login session)
- `BOT_TOKEN` — from BotFather
- `GROUP_CHAT_ID` — your group's ID (add [@userinfobot](https://t.me/userinfobot) to the group briefly to find it, or forward a group message to it)
- `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` — from your Stripe dashboard, test mode keys first

---

## 3. Initialize the database

By default nothing extra is needed, SQLite is a single file. If you want MongoDB instead, do the setup in section 3.1 first, then run this either way:

```bash
python database/db.py
```

This creates the tables/collections and one example plan panel so the dashboard isn't empty. `app.py` and `bot.py` also call this automatically on startup, so this step is really just for a quick manual check.

---

## 3.1 MongoDB setup (optional — skip if SQLite is fine for you)

SQLite is a single file, zero setup, and is completely fine for one bot/dashboard on one VPS. Use MongoDB instead if you want the database off the server (managed hosting), or you're planning to scale to multiple servers later.

**Easiest path — MongoDB Atlas (free tier, managed, no server to maintain):**

1. Go to [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas) and create a free account.
2. Create a free cluster (M0 tier is free forever, plenty for this project).
3. Under **Database Access**, create a database user with a password.
4. Under **Network Access**, add your VPS's IP address (or `0.0.0.0/0` while testing, then lock it down to your VPS IP once it's live).
5. Click **Connect → Drivers**, copy the connection string. It looks like:
   ```
   mongodb+srv://your_user:your_password@cluster0.xxxxx.mongodb.net
   ```

**Then in your `.env`:**
```
DB_BACKEND=mongodb
MONGODB_URI=mongodb+srv://your_user:your_password@cluster0.xxxxx.mongodb.net
MONGODB_DB_NAME=telegram_sub_bot
```

That's it, nothing else in the code changes. `app.py` and `bot.py` both read `DB_BACKEND` through `database/dal.py` and talk to whichever database you picked. You can switch back to `DB_BACKEND=sqlite` any time; the two databases don't share data automatically, so switching mid-project means starting the new one fresh (or writing a one-off migration script if you need the old data moved over — ask if you need this).

**Self-hosted MongoDB instead of Atlas:** install it on your VPS (`sudo apt install mongodb-org` on Ubuntu, or Docker with the official `mongo` image), then use `MONGODB_URI=mongodb://localhost:27017` in `.env`. Atlas is recommended unless you specifically want to self-host.

---

## 3.2 Google Sheets live backup (optional, on top of MongoDB)

This gives you a second, independent, human-readable copy of your data that updates in real time, every write to MongoDB also writes to a spreadsheet at the same moment. If MongoDB ever has an issue, or a code change ever causes unexpected data loss, this copy sits completely outside the database and is unaffected.

**Setup (about 5 minutes):**

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project (or use an existing one).
2. Enable the **Google Sheets API** for that project (search for it in the API library, click Enable).
3. Go to **IAM & Admin → Service Accounts → Create Service Account**. Give it any name, no special roles needed.
4. Open the service account, go to **Keys → Add Key → Create new key → JSON**. This downloads a `credentials.json` file, place it in the root of `telegram-sub-bot/` (next to `.env`).
5. Create a new Google Sheet (or use an existing one). Open it, click **Share**, and share it with the service account's email address (it looks like `something@your-project.iam.gserviceaccount.com`, found inside `credentials.json`), give it **Editor** access.
6. Copy the spreadsheet ID from its URL: `https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`

**Then in `.env`:**
```
GOOGLE_SHEETS_ENABLED=1
GOOGLE_SHEETS_CREDENTIALS_FILE=credentials.json
GOOGLE_SHEETS_SPREADSHEET_ID=the-id-you-copied
```

That's it. The dashboard and bot will now auto-create three tabs the first time each is written to — **Subscribers**, **Payments**, **Panels** — and keep every row updated live. If Sheets is briefly unreachable, the real database write still goes through normally; only the mirror step is skipped and logged, nothing user-facing breaks.

**Why this matters for your redeploy worry:** stats and subscriber counts in this dashboard are always read live from the database itself, nothing is cached in the bot's memory, so redeploying or restarting the bot can never make counts show as zero. The Sheets mirror is an extra safety net on top of that, not something the app depends on to function.

---

## 4. Run the dashboard

```bash
cd app
python app.py
```

Open `http://localhost:5000` (or `http://your-server-ip:5000` on a VPS) and log in with `DASHBOARD_PASSWORD`.

From here you can:
- **Overview tab** — revenue, active subscribers, who's expiring soon
- **Subscribers tab** — search, filter by status/payment, change page size, download CSV
- **Plans tab** — build your panel: message text + buttons (each button = price + weekly/monthly/one-time), edit anytime
- **Developer view** (Plans tab) — raw JSON of any table, handy for pasting into ChatGPT/Claude when you want help generating panel content
- **Settings (gear icon)** — dashboard title, currency symbol, active payment provider, reminder timing, auto-kick toggle, admin Telegram IDs

## 5. Run the bot

In a second terminal:

```bash
cd bot
python bot.py
```

Message your bot with `/start`. It will show the active panel from the dashboard with its buttons. Tapping a button creates a pending subscriber record and gives a checkout link.

Run both the dashboard and the bot at the same time, they share the same database file.

---

## 6. Payments setup (Stripe first)

The bot currently builds a placeholder checkout link (`build_checkout_link` in `bot/bot.py`) so you can test the full flow end to end before wiring real payments. To go live with Stripe:

1. In `bot/bot.py`, replace `build_checkout_link` with a real Stripe Checkout Session:
   ```python
   import stripe
   stripe.api_key = STRIPE_SECRET_KEY

   session = stripe.checkout.Session.create(
       mode="subscription" if plan["duration_type"] != "onetime" else "payment",
       line_items=[{"price_data": {
           "currency": "usd",
           "product_data": {"name": plan["text"]},
           "unit_amount": int(plan["price"] * 100),
           **({"recurring": {"interval": "week" if plan["duration_type"] == "weekly" else "month"}}
              if plan["duration_type"] != "onetime" else {}),
       }, "quantity": 1}],
       client_reference_id=str(telegram_user_id),
       success_url="https://yourdomain.com/success",
       cancel_url="https://yourdomain.com/cancel",
   )
   return session.url
   ```
2. In Stripe Dashboard → Developers → Webhooks, add an endpoint pointing to:
   `https://yourdomain.com/webhook/stripe`
3. Send the `checkout.session.completed` and `checkout.session.async_payment_failed` events.
4. In `app/app.py`, the `stripe_webhook()` function already handles those two events and writes to the `payments` table — swap the placeholder JSON parsing for real signature verification with `stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)`.
5. Once a payment succeeds, add the logic to invite the user to your group (`context.bot.create_chat_invite_link` / send them the invite) and flip their `subscribers.status` to `active` with an `expiry_date`.

**Switching providers later:** the dashboard's Settings tab already has an "Active payment provider" dropdown (Stripe / PayPal / CCBill). `build_checkout_link()` in `bot/bot.py` reads this setting and branches accordingly, so you add the PayPal/CCBill equivalent of the Stripe block above whenever you're ready and it activates from the dashboard, no redeploy of the bot needed beyond that code addition.

---

## 7. Testing checklist

- [ ] Dashboard loads at `/`, wrong password is rejected, correct password logs in
- [ ] Overview stat cards show numbers (0s are fine on a fresh install)
- [ ] Plans tab shows the seeded "Default Panel", edit it, add a button, save, confirm it updates
- [ ] Subscribers tab: search box filters by username, status/payment filters work, CSV downloads
- [ ] Change page size, confirm pagination updates
- [ ] Developer view shows raw JSON for each table
- [ ] Settings: change dashboard title, save, confirm header updates immediately
- [ ] Telegram: `/start` shows your panel with correct buttons and prices
- [ ] Tapping a button creates a `pending` row in the Subscribers tab
- [ ] Send a test Stripe webhook (Stripe CLI: `stripe trigger checkout.session.completed`) and confirm a `payments` row appears with status `successful`
- [ ] If using MongoDB: set `DB_BACKEND=mongodb` in `.env`, restart both processes, repeat the checks above against Mongo (Atlas has a built-in data browser to confirm records are landing)

---

## 8. Deploying to your VPS, and connecting your domain

This is the part that answers "where do I write the domain" — short answer: **in two places**, your domain registrar's DNS settings, and the Nginx config file on your VPS. Full walkthrough below.

### 8.1 Run both processes with systemd (so they restart if the server reboots)

```ini
# /etc/systemd/system/sub-dashboard.service
[Unit]
Description=Subscription Dashboard
After=network.target

[Service]
WorkingDirectory=/path/to/telegram-sub-bot/app
ExecStart=/path/to/telegram-sub-bot/venv/bin/python app.py
Restart=always
EnvironmentFile=/path/to/telegram-sub-bot/.env

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/sub-bot.service
[Unit]
Description=Telegram Subscription Bot
After=network.target

[Service]
WorkingDirectory=/path/to/telegram-sub-bot/bot
ExecStart=/path/to/telegram-sub-bot/venv/bin/python bot.py
Restart=always
EnvironmentFile=/path/to/telegram-sub-bot/.env

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sub-dashboard sub-bot
```

The dashboard is now running on the server but only reachable at `http://your-server-ip:5000`. The steps below put a real domain on top of that.

### 8.2 Point your domain at the server (DNS — done at your registrar)

Wherever you bought the domain (Namecheap, GoDaddy, Cloudflare, etc.), open its **DNS settings** and add:

| Type | Host/Name                  | Value               |
|------|-----------------------------|----------------------|
| A    | `dashboard` (or `@` for root) | your VPS's IP address |

This is "where you write the domain" for step one — a subdomain like `dashboard.yourdomain.com` pointing at your server's IP. DNS changes can take a few minutes to a few hours to go live (usually fast).

### 8.3 Install Nginx and point it at the dashboard

```bash
sudo apt update && sudo apt install nginx -y
```

Create `/etc/nginx/sites-available/dashboard`:
```nginx
server {
    listen 80;
    server_name dashboard.yourdomain.com;   # <-- your domain goes here

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

At this point `http://dashboard.yourdomain.com` should load the login page.

### 8.4 Add HTTPS (required for Stripe webhooks and for Telegram to trust links you send)

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d dashboard.yourdomain.com
```

Certbot edits the Nginx config for you and sets up auto-renewal. Your dashboard is now at `https://dashboard.yourdomain.com`.

### 8.5 Update `.env` to use the real domain

```
DASHBOARD_URL=https://dashboard.yourdomain.com
```

This is the value `bot.py` uses when it builds checkout links to send users, so it must be the real HTTPS domain once you're live, not `localhost`.

### 8.6 Update the Stripe webhook URL

In Stripe Dashboard → Developers → Webhooks, point the endpoint at:
```
https://dashboard.yourdomain.com/webhook/stripe
```

Restart both services after any `.env` change:
```bash
sudo systemctl restart sub-dashboard sub-bot
```

---

## 9. Project structure

```
telegram-sub-bot/
├── app/                  # dashboard (Flask)
│   ├── app.py
│   ├── templates/
│   └── static/
├── bot/
│   └── bot.py            # Telegram bot
├── database/
│   ├── dal.py            # picks sqlite or mongodb, mirrors writes to Sheets
│   ├── db.py              # SQLite backend
│   ├── db_mongo.py        # MongoDB backend
│   └── sheets_sync.py     # live Google Sheets mirror (optional)
├── requirements.txt
├── .env.example
└── README.md
```

`app.py` and `bot.py` never call `db.py`, `db_mongo.py`, or `sheets_sync.py` directly, they only call `database/dal.py`. That one file decides which database is used and whether writes also get mirrored to Sheets.

## 10. Notes

- The dashboard password is a single shared password stored in `.env`, not per-user login — matches what was scoped (env-based password, not a user system).
- SQLite data lives in `database/data.db`. Back this file up regularly once live. With MongoDB, backups are handled by Atlas (or your own `mongodump` schedule if self-hosting).
- Switching Stripe → CCBill/PayPal later doesn't require touching the database schema, `provider` is already a column/field on both `subscribers` and `payments`.
- Switching SQLite → MongoDB doesn't require touching `app.py` or `bot.py`, just the `DB_BACKEND` and `MONGODB_URI` lines in `.env`.
- Stats and subscriber counts are always read live from the database on every request, nothing is cached in bot/dashboard memory, so a restart or redeploy can never show stale zeros.
