# Trial Manager

The all-in-one trial app. **Right now it contains the entry formatter** (JotForm entries → the 3-tab trial
workbook). Check-in and online entries will be added here later.

It uses the **same database table as the current Streamlit formatter** (`formatter_shows`), so both apps
show the same shows, scratches and typo fixes. You can run them side by side and switch over when you're ready.

## What's in it
Each show's menu follows the trial: **1 Setup**; **Before the trial**: 2 Entries, 3 Review, 4 Send to UKI; **Trial day**: 5 Run order, 6 Day-of entries, 7 Scoring & results; **Competitors**: 8 Messages. Below that are **Directory → Handlers & dogs** and **Admin → Users & access**. On a phone the same menu opens from the top bar. The JotForm banner and stats show only on the before-the-trial pages. Old page addresses (`/paper`, `/typos`, `/warnings`, `/scoring/results`, `/scoring/masters`) redirect to their new homes.

- **Shows:** list of trials, plus New show / Edit details.
- **Entries:** one row per dog, with fees, paid and balance. The scratch switch saves instantly. Search, and filters for New / Scratched / Owes money / Paper. **+ Paper entry** at the top opens the paper-form lookup.
- **Discounts** (certificates etc.): on Entries, click **Discount** under a handler's balance, type the amount and an optional note (e.g. "Certificate #123"), and Save. It comes off what they owe on Entries, in the stats, in the workbook's Balances sheet and in their confirmation email (shown as its own line). A discount covers at most their entry fees; if they'd already paid, the rest shows as a credit (Review → Warnings). Edit or Remove it the same way.
- **Change classes:** click a dog's Sat or Sun number on Entries to change which classes it's in (for example, move Sunday's entries to Saturday, with one-click Move buttons). Saved like typo fixes: JotForm stays the original, the entry shows CLASSES CHANGED, fees and the workbook follow, Undo returns to the JotForm entry, and Run order picks it up on Update run order.
- **Review → Fix typos:** click a cell, type, press Enter. Fixed cells turn yellow and show the JotForm value. Every fix records who made it, with Undo.
- **Review → Warnings:** credits, addresses typed two ways (pick one), state/zip mismatches (one-click fix), plus anything else the formatter notices. Dismiss or Reopen.
- **Send to UKI:** downloads the entries workbook, with a download history. "N new entries since your last download" appears on the before-the-trial pages.
- **Users & access:** invite-only admin accounts. See below.
- **Show setup:** one page per trial for the trial details (name, dates, UKI show ID, JotForm form ID), each day's run order (which levels first, tall-to-small or small-to-tall), and each day's class order (drag to match the schedule) with ring, Select-before-Regular and all-levels settings per class. Works before the run order is built. Defaults follow the LUDS premium: Saturday Gamblers, Agility 1/2, Jumping 1/2, Speedstakes; Sunday Speedstakes 1/2, Jumping, Masters Jumping, Agility, Masters Agility.
- **Run order** (Trial day): once the check-in app is connected, every change goes straight to it (see step 5 of the Railway setup). Builds every class grouped by level then height (Gamblers and Masters: all levels together), Select before Regular. Each day has its own order, chosen when you build: by default Saturday is Senior/Champ first, tall to small, and Sunday is Beginner/Novice first, small to tall. Dogs are shuffled inside each height with a handler's dogs spread apart. Shows the **Gap** for each handler (fewer than 10 dogs = conflict), drag-and-drop or "Move to", Undo, per-class settings (order in the day, ring, Select/Regular first), and new JotForm entries join the end of their height group. Exports **grid_run_order_export.csv** in the exact columns the check-in app reads, and **scribe sheets** (PDF, four per page, in UKI Manager's Judging Sheet layout; Gamblers has its own sheet).
- **Paper entries** (Entries → + Paper entry): for mailed or handed-in forms. Type the handler's UKI #, name or a dog's name, pick them, and their details and dogs fill in from Handlers & dogs; tick the dog(s) and classes (worded like the show's JotForm), and note the check/cash paid. Fees add up as you tick. A paper entry is a real entry: it shows on Entries (marked PAPER), in typo fixes, warnings, balances and the workbook, and joins the run order on Update run order. Click its PAPER tag to edit or delete it; the Paper filter lists them all.
- **Messages** (Competitors): one email per handler. **Entry confirmations** list each dog, its classes, fees, paid and anything owed; **Results** list every calculated run (Q, place, time, level points) and Masters Series. Preview any email, pick people (or "Select not sent + changed"), add an optional note, and Send. Each person's row shows Not sent / Sent / **Changed since sent** (their entry or results changed after the email) / Failed (with the reason). Sends through Gmail; see step 6 of the Railway setup.
- **Handlers & dogs** (Directory): everyone who has ever entered, for lookups. Fill it from **Import**: every show's entries, UKI Manager's handlers CSV (grid_handlers), and past results workbooks (Raw Results: dog UKI #, breed, height, level, Speedstakes level). After that, JotForm refreshes and paper entries keep it current. People are matched by UKI #; newer details win and older ones only fill blanks, so re-importing is safe. Search and fix anyone by hand.
- **Day-of entries:** add classes for a dog already entered, or a brand-new handler/dog. Form B can look the handler up in Handlers & dogs to fill itself in. Each class is the normal fee plus $5, goes straight into the run order (marked DAY OF; no separate processing step), and payments are tracked per handler.
- **Scoring & results:** one page with tabs for Scores, Results and (on days with Masters) Masters Series overall. Per class, enter the judge's yardage (or Gamblers opening/gamble time, Snooker course time); SCTs fill in from the UKI rule book formulas. Type times, pick faults/refusals or E/ABS/NFC (saves as you type), then **Calculate** for time faults, Q, places and level points. The Results tab gives copy-ready JSON for each check-in class (Trial Secretary → Publish results) and **Results file for UKI** (top of the page) downloads the Raw Results sheet. The rules are in `app/scoring.py`; a test recomputes UKI Manager's June 2026 results and matches every SCT, Q, place and level point.
- **Masters Series:** the **Masters Series overall** tab on Scoring & results combines the Agility and Jumping rounds per height using the premium's rules (E = 100 + 60, absent/NFC = 1000 + 60 and forfeits the other round's awards, ties to Agility then Jumping), with the $4-per-dog prize fund (100%, or 70/30 above 10 entered) and Final qualifiers (1st/2nd overall plus each round's winner). The results spreadsheet gets "Masters Scoring" and "Masters Byes" sheets. On Run order, the second Masters class has **Order from first round** (reverse order of placing), and day-of Masters entries run first in the first class. Checked against June 2026: places, prizes and qualifiers all match.

## Accounts (no public sign-up)
- **The first account** can only be created by the email in the `BOOTSTRAP_ADMIN_EMAIL` variable, and only while no accounts exist. Open the app, and it takes you to a one-time setup page.
- **Everyone else joins by invite:** Users & access → Invite someone. You get a link that **works once and expires in 48 hours**. Send it however you like.
- **Users & access** lists every account with a **Disable** button, which signs that person out everywhere immediately. It also shows recent sign-ins, including failed ones.
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
your name and a password. Then go to **Users & access** and invite your co-admin.

### 5. Connect the check-in app (optional, recommended)
This lets Trial Manager update the check-in app directly, so you don't upload CSVs or paste results JSON.
Both services must be in the same Railway project.

1. On the **Trial Manager** box → **Variables** → add `CHECKIN_DATABASE_URL` = `${{<check-in's Postgres>.DATABASE_URL}}`.
   Type `${{` and pick the Postgres box the **check-in app** uses (not Trial Manager's own, if they're different).
2. Redeploy both services (the check-in app gets its "managed by Trial Manager" lock in the same update).

From then on every show syncs by itself as soon as its run order is built, so the preliminary run order is on the
check-in app weeks ahead and you don't need to email it out. **Exhibitors can't check in** until you turn on
**Check-in open** for that day in the check-in app's Admin.

What syncs:
- **Run order, scratches, day-of entries, A-frame settings:** reach the gate within seconds, every time something changes. Gate statuses (checked in, scratched at the gate, finished) are never overwritten, and a dog scratched here shows as Scratch at the gate.
- **Results:** **Calculate** publishes that class to the check-in Results tab.
- **Gate → scoring:** the Scores tab shows who scratched or went NFC at the gate, and **Fill from gate** marks unscored ones ABS / NFC.
- **In the check-in app,** live days are locked: Admin's Upload, Reorder, Late entry, A-frame and Publish results say "managed by Trial Manager". If Trial Manager is ever down on trial day, Admin can **unlock** a day and work by hand. Trial Manager then stops updating that day until it's handed back there.
- **The Run order page** shows what was sent, which database it's connected to, and how many runs the check-in app has for each day. **If an update fails,** it shows the reason in red, and the CSV download still works as a backup. Click **Send now** to retry.

### 6. Connect Gmail (for emailing competitors)
**Messages** (in each show's menu) sends entry confirmations and results through your Gmail account. Until this is
set up you can still preview every email. One-time setup, about 20 minutes:

1. Go to **console.cloud.google.com** and sign in with the Gmail account the emails should come from.
   Top bar → project picker → **New project** → name it `Trial Manager` → **Create**, and make sure it's selected.
2. **APIs & Services → Library** → search **Gmail API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (may be called **Google Auth Platform → Branding**):
   - App name `Trial Manager`, your email for the support and developer contact fields. **User type: External.**
   - **Audience:** click **Publish app** so it's **In production**. *This matters:* while it says "Testing", Google
     cancels the permission after 7 days and sending stops. (You don't need Google to verify it; it's just for you.)
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Application type **Desktop app** →
   name `Trial Manager` → **Create** → **Download JSON**.
5. On your Mac, in Terminal, from the `TrialManager` folder:
   `python3 tools/gmail_auth.py ~/Downloads/client_secret_….json`
   A browser opens: pick your Gmail account. Google warns "Google hasn't verified this app": click **Advanced →
   Go to Trial Manager (unsafe)** (it's your own app), then **Continue**. It only gets permission to *send* email;
   it can't read your mail.
6. Terminal prints three lines. Add each as a variable on the **Trial Manager** service in Railway:
   `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`. Then delete the downloaded JSON file.
7. Optional: to show a name other than your own on emails, also add `MAIL_FROM_NAME` (e.g. `Level Up Dog Sports
   Trials`) and `MAIL_FROM_ADDRESS` (your Gmail address).

8. The emails link to the check-in app at `https://trialsecretary.up.railway.app`. If it moves (e.g. to a custom
   domain), add `CHECKIN_APP_URL` with the new address.

Emails appear in that Gmail account's **Sent** folder. **Reply** goes to whoever clicked Send. Personal Gmail
allows about 500 emails a day. If Messages ever says Google no longer accepts the permission, repeat step 5–6.

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
