# Trial Manager

The all-in-one trial app. **Right now it contains the entry formatter** (JotForm entries → the 3-tab trial
workbook). Check-in and online entries will be added here later.

It uses the **same database table as the current Streamlit formatter** (`formatter_shows`), so both apps
show the same shows, scratches and typo fixes. You can run them side by side and switch over when you're ready.

## What's in it
- **Shows:** list of trials, plus New show / Edit details.
- **Entries:** one row per dog, with fees, paid and balance. The scratch switch saves instantly. Search, and filters for New / Scratched / Owes money.
- **Fix typos:** click a cell, type, press Enter. Fixed cells turn yellow and show the JotForm value. Every fix records who made it, with Undo.
- **Warnings:** credits, addresses typed two ways (pick one), state/zip mismatches (one-click fix), plus anything else the formatter notices. Dismiss or Reopen.
- **Download:** the same workbook as today, with a download history. "N new entries since your last download" appears on every page.
- **People:** invite-only admin accounts. See below.

## Accounts (no public sign-up)
- **The first account** can only be created by the email in the `BOOTSTRAP_ADMIN_EMAIL` variable, and only while no accounts exist. Open the app, and it takes you to a one-time setup page.
- **Everyone else joins by invite:** People → Invite someone. You get a link that **works once and expires in 48 hours**. Send it however you like.
- **People** lists every account with a **Disable** button, which signs that person out everywhere immediately. It also shows recent sign-ins, including failed ones.
- **Passwords** must be 10+ characters and are stored scrambled (hashed). Five wrong passwords lock the account for 15 minutes.
- **Account → Change password** signs you out on your other devices.

---

## Set it up on Railway (one time)

### 1. Add the service
On your project canvas: **+ Add → GitHub Repo → personal_projects**. Click the new box:
- **Settings → Root Directory:** `TrialManager`
- **Settings → Deploy → Custom Start Command:** leave **empty** (the app's `railway.json` has it).

### 2. Add the variables
New box → **Variables** tab:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (type `${{` and pick your Postgres service) |
| `JOTFORM_API_KEY` | your JotForm API key |
| `SECRET_KEY` | a long random string. Generate one in Terminal: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BOOTSTRAP_ADMIN_EMAIL` | your email address |

`SECRET_KEY` signs the login cookies. If it changes, everyone has to sign in again. Keep it secret.

### 3. Get a web address
**Settings → Networking → Generate Domain** (port `8080`).

### 4. Create your account
Open the address. You'll see **Create the first admin**: enter the same email as `BOOTSTRAP_ADMIN_EMAIL`,
your name and a password. Then go to **People** and invite your co-admin.

### Switching over from the Streamlit formatter
Both apps share the same data, so there's nothing to move. Use this one for a trial; when you're happy,
delete the old formatter service in Railway.

---

## Run it on your Mac
```
cd ~/Documents/GitHub/personal_projects/TrialManager
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
BOOTSTRAP_ADMIN_EMAIL=you@example.com JOTFORM_API_KEY=... .venv/bin/uvicorn app.main:app --reload
```
Without `DATABASE_URL`, it uses a local test file (`trial_manager_local.db`).

**Tests:** `.venv/bin/python -m pip install pytest httpx && .venv/bin/python -m pytest tests`
