# Trial Check-in App

Check-in, running order, gate steward, admin and results for UKI trials.
All data lives in a Railway **Postgres** database, so it doesn't use Supabase.

| Who | Link | What they can do | PIN |
|---|---|---|---|
| Exhibitors | your app's address | Check in, see the running order, course maps and results | none |
| Gate stewards | address + `?staff` | Also the Gate tab: check in, start/finish runs, scratch | `GATE_PIN` |
| You (secretary) | address + `?staff` | Also Dash and Admin: upload run order, reorder, late entries, course maps, publish results | `ADMIN_PIN` |

Example: if the app is at `https://luds-checkin.up.railway.app`, staff use `https://luds-checkin.up.railway.app/?staff`.
Exhibitors who enter their handler number get a link ending in `?h=12345`. Bookmarking it takes them straight to their dogs.

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
**Open check-in when you're ready:** exhibitor check-in starts **closed** for every day. At the top of Admin,
turn on **Check-in open for <day>** (pick the day at the top first; Saturday and Sunday each have their own switch).
While closed, the Check-in tab says check-in isn't open yet, and exhibitors can't change their status, even from a
page they already had open. Running order, course maps, results, the Gate and Admin work either way.

**If Trial Manager is connected** (its `CHECKIN_DATABASE_URL` points at this app's database and the show is
switched "Live on check-in"), it fills in the run order, late entries, A-frame and results by itself. Those
Admin sections then say "managed by Trial Manager"; only Course maps and Reset statuses are used here. If
Trial Manager is down, **Unlock for manual edits** on that Admin page lets you work by hand. Click **Hand back
to Trial Manager** afterwards so it takes over again.

Without Trial Manager:
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
