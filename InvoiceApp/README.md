# Level Up Invoicing

Log classes, subs, trial secretary work, barter and payments, and make PDF invoices for Level Up Dog Sports.
Replaces the "Level Up Debits & Credits" spreadsheet: work adds credit, payments and barter take it away,
and **Balance owed** is what's left (the sheet's Total Owed).

| Tab | What it's for |
|---|---|
| Home | Balance owed, and this week's classes: tap **Taught**, **Subbed** or **Cancelled** |
| Log | Add a sub, trial secretary work (runs × $/run), barter, a payment, or other work |
| Ledger | Every row, like the spreadsheet. Tap a row to edit or delete it |
| Sessions | Create the next 6-week session (skip workshop weeks; later weeks slide back) |
| Invoices | Make an invoice, download the PDF, mark it Sent / Paid, or void it |
| Settings | Your name and contact info, who you bill, rates, CSV backup, spreadsheet import |

Rows on a **sent** or **paid** invoice are locked. Void the invoice to change them.

---

## Set it up on Railway (one time)

1. **Database:** on your project canvas, **+ Add → Database → PostgreSQL**. Use a new one so your invoices
   stay separate from the trial apps' data.
2. **App:** **+ Add → GitHub Repo → personal_projects**. Click the new box:
   - **Settings → Root Directory:** `InvoiceApp`
   - **Settings → Deploy → Custom Start Command:** leave it empty (`railway.json` has it)
3. **Variables:**

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (pick the new database from the suggestions) |
   | `APP_PIN` | the PIN you'll type to open the app |

4. **Settings → Networking → Generate Domain.**
5. Open the app, go to **Settings**, fill in your contact info and Level Up's address, then
   **Import the old spreadsheet** (File → Download → CSV from Google Sheets) and read the "Worth a look" list.

Railway's Hobby plan doesn't back up the database, so use **Settings → Download everything (CSV)** now and then.

---

## Run it on your Mac
```
cd ~/Documents/GitHub/personal_projects/InvoiceApp
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
echo "APP_PIN=1234" > .env
.venv/bin/streamlit run app.py
```
Without `DATABASE_URL` it uses a local file, `invoice_local.db`.

Tests: `uv pip install -p .venv/bin/python pytest && .venv/bin/python -m pytest`
