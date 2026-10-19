## One-click start

See START_HERE.txt. Double-click `start.bat` (Windows), `start.command` (Mac) or `start.sh` (Linux): the launcher
creates the private Python environment, installs the libraries, generates the security keys in `.env` and opens the app.

# Ledgerline 3.0

Invoicing and business-performance dashboard built on Streamlit + SQLAlchemy.

## Quick start (Windows / macOS / Linux)

```bash
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                 # macOS/Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste the result into AUTH_SECRET in .env
python -m streamlit run app.py
```

On first launch the app shows a **Create administrator** screen (there is no default password).
Then open **Administration -> Data & system** to load sample data or import v2.0 data.


## Pages (sidebar)

| Page | What it does |
|---|---|
| Business Performance | KPIs, charts, recent invoices, per-invoice actions (payments, PDF, WhatsApp, email) |
| New Invoice | Multi-line invoices with discount and tax; choose Unpaid / Partly paid / Paid in full |
| Products | Your price list (digital products, templates, services). Add items to an invoice in one click |
| Expenses | Ads, tools and fees. Shows your real profit after all costs |
| Clients | Directory with money owed, client profile, account statement, add/edit |
| Payments | Every payment received, filters, chart, CSV export |
| Reports | Who owes me (aging), best clients, tax summary, profit by category, profit & loss, CSV export |
| Help & Guide | Four-step walkthrough, FAQ, roles |
| Administration | Users, audit log, settings, imports (admin only) |

## Configuration

All secrets come from environment variables, `.env`, or `.streamlit/secrets.toml` (same flat
UPPERCASE names, see `.env.example` and `.streamlit/secrets.toml.example`).
Priority: environment > `.env` > `secrets.toml` > defaults.

| Need | Settings |
|---|---|
| Stay signed in after refresh | `AUTH_SECRET` (32+ chars), `extra-streamlit-components` installed |
| PostgreSQL / MySQL | `DATABASE_URL` + one driver from `requirements.txt` |
| Email | `SMTP_HOST`, `SMTP_FROM_ADDRESS`, credentials |
| Automatic WhatsApp | `WHATSAPP_PROVIDER=whatsapp_cloud` or `twilio` and its credentials |
| Encrypt client contact data | `ENCRYPTION_KEY` (Fernet key, back it up) |

## Roles

| Role | Can |
|---|---|
| viewer | view dashboard, download PDFs |
| manager | viewer + create invoices, edit/cancel invoices created in the last 7 days, record payments, send email/WhatsApp, edit clients, export |
| admin | everything: edit any invoice, delete invoices/payments, users, audit log, settings, imports |

Rules are enforced in the service layer (`ledgerline/security.py`, `services/`), not only in the UI.

## Project layout

```
app.py                      entry point
ledgerline/config.py        secrets and settings        ledgerline/models.py   relational schema
ledgerline/db.py            engine, WAL, sessions       ledgerline/events.py   totals + audit hooks
ledgerline/calc.py          pure money maths            ledgerline/security.py passwords, JWT, RBAC
ledgerline/pdf.py           ReportLab engine            ledgerline/services/   use-cases, analytics, outbox
ledgerline/integrations/    SMTP pool, WhatsApp         ledgerline/ui/         Streamlit pages
tests/                      pytest suite
```

## Backups and data safety

* SQLite installs back themselves up automatically (default every 6 hours, last 60 kept) into `data/backups/`.
  Each copy is made with SQLite's online-backup API, so it is consistent even while people are working, and it is
  integrity-checked before it counts. Administration -> Data & system shows status, "Back up now" and a download button.
* Set `BACKUP_EXTRA_DIR` to a second disk or synced cloud folder: a backup next to the database does not survive a dead disk.
* Restore: stop the app, copy a backup over `data/ledgerline.db`, delete `ledgerline.db-wal`/`-shm`, start the app.
* PostgreSQL/MySQL: use pg_dump/mysqldump or your provider's point-in-time recovery.

## Production notes

* Run behind HTTPS and set `LEDGERLINE_ENV=production` (secure cookies, mandatory `AUTH_SECRET`).
* Streamlit cannot set HttpOnly cookies. Sessions are signed, expiring and revocable server-side;
  for stricter needs put the app behind an identity-aware proxy or use Streamlit's `st.login`.
* `create_all` only creates missing tables. Adopt Alembic before changing the schema in production.
* SQLite is fine for one server. Use PostgreSQL for several app instances.
* If the project folder is synced by OneDrive/Dropbox, set `DATABASE_URL` to a path outside it.
* Tests: `pip install -r requirements-dev.txt && pytest -q`


## Customize without code (admin)  — new in this version

Sidebar -> **Customize** (administrators only):

| Tab | What you can change |
|---|---|
| Appearance | main/second colour, quick colour sets, font, roundness, page background, side menu, spacing, your own CSS. Live preview, Save applies to everyone |
| Client form / Invoice form | show or hide fields, rename, mark required, reorder, **add your own boxes** (text, long text, number, date, dropdown, tick box, phone) and delete them. Invoice fields can be printed on the PDF |
| Dropdown lists | invoice categories, payment methods, product categories, expense categories |
| Languages | company default language, default country for phone numbers, fix any translation, add a new language |

* **Phone numbers** everywhere use a country drop-down (240 countries, flag + name + code, type to search). Stored as `+92 3001234567`.
* **Language**: the 🌐 Language box in the sidebar (and on the login page) switches the whole app; Urdu and Arabic switch the layout to right-to-left.
  Shipped files: `ledgerline/locales/*.json`. Missing sentences fall back to English. PDF invoices and some technical messages stay in English.
* Extra-field values live in the new `custom_values` table (created automatically on first start; nothing existing is altered).
* After changing code that adds sentences, regenerate the master list used by the translation editor: see `ledgerline/locales/_keys.json`.


## Update v5

* **Languages**: English, اردو, العربية, हिन्दी, Español, Français. Month and weekday names follow the language (screens, charts, PDF).
* **PDF invoices** print in the chosen language. Urdu/Arabic are joined and right-to-left using the bundled DejaVu Sans font
  (`ledgerline/assets/fonts`, free licence). Hindi PDFs stay in English (the font has no Devanagari). Long right-to-left notes that wrap
  over several lines can come out in the wrong line order.
* **Messages**: every save shows what to fix in plain words plus a centred notice; success shows a centred message that fades by itself.
  Unexpected errors are logged with a reference number and never show a crash page.
* **Light / dark** in Customize -> Appearance (refresh once to see it everywhere).
* Es/Fr/Hi cover the interface and common messages; a few long help texts fall back to English (translate them under Customize -> Languages).


## Update v6
* **Banking** page (admins and managers): accounts (bank / cash / card / wallet) with balances, **Receive payment** (adds the payment to the
  invoice and deposits it into an account), money in/out, transfers between accounts, **reconciliation** against the bank statement,
  and **Loans** (borrowed amount, monthly instalment estimate, repayments split into principal and interest; interest is booked as an expense).
* New tables are created automatically on first start; nothing existing is changed.
* Hosting for testers: see **DEPLOY.md** (Dockerfile, docker-compose.yml and render.yaml are included).
* New Banking text is English until translated (Customize -> Languages).
