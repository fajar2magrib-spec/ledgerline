# Giving Ledgerline to testers (a link anyone can open)

Running it on your own computer or an internal server means only people on that network can open it.
To let testers use it from anywhere you host it once and share the web address (URL).

## Important before you start
* **Use a real database (PostgreSQL) for hosted testing.** Most cheap hosts wipe local files on every restart, so the default
  SQLite file would lose all data. Set `DATABASE_URL` (see below). `render.yaml` and `docker-compose.yml` already do it.
* **Set `AUTH_SECRET`** (32+ random characters). In production the app refuses to start without it.
  Make one with: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
* Serve it over **HTTPS** (every host below does). Then login cookies are secure.
* Do not put real customer data in a test copy. Use *Administration -> Data & system -> Load sample data*.

## Option A - Render.com (easiest, no server to manage)
1. Put this folder in a GitHub repository (private is fine).
2. On render.com: **New + -> Blueprint** -> choose the repository. `render.yaml` creates the web app and a PostgreSQL database.
3. Wait for the build (a few minutes). Render shows the URL, like `https://ledgerline.onrender.com`.
4. Open it, create the administrator on the first screen, then add test users (manager / viewer) under Administration -> Users.
5. Send testers the URL plus their username and password.

## Option B - your own small server (VPS) with Docker
1. Install Docker. Copy this folder to the server.
2. Create a file named `.env` next to `docker-compose.yml`:
   ```
   DB_PASSWORD=choose-a-long-password
   AUTH_SECRET=paste-48-random-characters
   COOKIE_SECURE=true
   ```
3. `docker compose up -d --build`   -> the app listens on port 8501.
4. Put HTTPS in front (Caddy, Nginx, or a free Cloudflare Tunnel) so testers open `https://your-domain`.

## Option C - Railway / Fly.io / any Docker host
Use the included `Dockerfile`. Add a PostgreSQL service and set these variables on the app:
`LEDGERLINE_ENV=production`, `AUTH_SECRET=...`, `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname`.

## Streamlit Community Cloud + Neon (both free, no server, no credit card)
1. Put this folder in a **private** GitHub repository (`app.py` must be at the top level of the repository).
2. Create a free Postgres database on neon.com and copy its connection string. Paste it as it is: the app rewrites
   `postgresql://` to the bundled `psycopg` driver by itself.
3. On share.streamlit.io choose *Create app*, pick the repository, branch `main` and main file `app.py`.
   Under *Advanced settings -> Secrets* paste:
   ```
   LEDGERLINE_ENV = "production"
   AUTH_SECRET = "48+ random characters"
   DATABASE_URL = "the Neon connection string"
   ```
4. Open the `https://....streamlit.app` address, create the administrator, then add testers under Administration -> Users.

Free-tier behaviour: the app goes to sleep after about 12 hours without visitors (a visitor clicks the wake-up button and waits
about a minute), and the free Neon database pauses after 5 idle minutes (the next request is a little slower). Neon Free gives 0.5 GB.


## Forgot the administrator password
Nothing can read a password back (they are stored one-way), but the server owner can set a new one:
1. On Streamlit Community Cloud open the app's **Settings -> Secrets** and add a line (at least 10 characters, letters AND digits):
   `ADMIN_RESET_PASSWORD = "A-new-password-2026"`
   Optionally also `ADMIN_RESET_USERNAME = "the-admin-username"`. Without it the oldest active administrator is reset.
2. Save. The app restarts and the password is replaced (everyone is signed out). If you forgot the username too, open
   **Manage app -> Logs**: the line "Administrator '...': password reset" shows it.
3. Sign in, then open **Change my password** in the sidebar and choose your own. Then **delete the ADMIN_RESET_PASSWORD line**
   from Secrets. It only acts once per value, so leaving it does not overwrite your new password, but it should not stay.
Any signed-in user (viewer, manager, admin) can change their own password from the sidebar.

## Signed out after a browser refresh (Streamlit Community Cloud)
The app keeps you signed in through a browser cookie. Streamlit Community Cloud filters browser cookies at its proxy, so a full
page refresh signs you out there; on your own computer, Render, Docker and similar hosts it does not. Moving between pages
inside the app never signs you out. The sign-in boxes are marked as a sign-in form, so Chrome offers the SAVED password: after a
refresh, tap the saved login instead of typing.

## Tester checklist (send this to your testers)
1. Sign in, change your password.
2. Add a client with a phone number (pick the country).
3. Create an invoice, save it, download the PDF in your language.
4. Receive a payment (Banking -> Receive payment), then check the balance of the account.
5. Add a loan and a repayment (Banking -> Loans).
6. Try the language box in the left menu.
7. Report problems with a screenshot and what you clicked. If a grey "Reference" number appears, include it.
