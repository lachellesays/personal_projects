# Trial Check-in App

Check-in, running order, gate steward, admin and results for UKI trials.
All data lives in a Railway **Postgres** database, so it doesn't use Supabase.

| Who | What they can do | PIN |
|---|---|---|
| Exhibitors | Check in, see the running order and results | none |
| Gate stewards | Gate tab: check in, start/finish runs, scratch | `GATE_PIN` |
| You (secretary) | Everything above, plus Admin: upload run order, reorder, late entries, course maps, publish results | `ADMIN_PIN` |

---

## Set it up on Railway (one time)

### 1. Add a database
On your project canvas: **+ Add → Database → PostgreSQL**. A "Postgres" box appears.

### 2. Add the app
**+ Add → GitHub Repo → personal_projects**. Click the new box:
- **Settings → Root Directory:** `CheckinApp`
- **Settings → Deploy → Custom Start Command:** leave it **empty** (the app's `railway.json` has the right one)

### 3. Add the variables
New box → **Variables** tab → add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (type it exactly like this; Railway links it to the database) |
| `GATE_PIN` | the PIN gate stewards will use |
| `ADMIN_PIN` | your PIN (keep it different from the gate PIN) |

No volume is needed. Everything, including course map images, is stored in the database.

### 4. Get a web address
**Settings → Networking → Generate Domain.**

If the app shows a red warning about `DATABASE_URL`, step 3 isn't right yet.

---

## Each trial
1. **Admin → Upload run order:** choose the CSV from the run order app, check the preview, then click **Load**.
   Upload Saturday and Sunday separately. Each upload only replaces its own day.
   If you re-upload later, statuses (checked in, scratched, etc.) are kept for dogs still in the same class.
2. **Admin → Reorder:** pick a class, drag dogs into place, then click **Save order**.
   On a phone, use **"Or move one dog to a position"** instead.
3. **Admin → Late entry:** pick the dog (or type a new one), the class, and where it goes in the order.
4. **Admin → Course maps / Publish results** during the day.

The app opens on today's date automatically. If more than one day is loaded, there's a day switcher at the top.

---

## Run it on your Mac
```
cd ~/Documents/GitHub/personal_projects/CheckinApp
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
GATE_PIN=1111 ADMIN_PIN=2222 .venv/bin/streamlit run app.py
```
Without `DATABASE_URL`, it uses a local test file (`checkin_local.db`) instead of Postgres.

Tests: `.venv/bin/python -m pytest tests`.
To run them against a real Postgres database, set `TEST_DATABASE_URL`. **This erases that database**, so never point it at the live one.
