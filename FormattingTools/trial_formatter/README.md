# Trial Data Formatter

Pulls entries from JotForm and builds the trial workbook (Contact, Balances, Raw Results tabs).

The app needs two secret values:

| Name | What it is |
|---|---|
| `JOTFORM_API_KEY` | Your JotForm API key (JotForm → My Account → API) |
| `APP_PASSWORD` | Any password you choose. You'll type it to get into the app. |

Secrets never go in the code. You put them in one of two places, depending on where the app runs (see below).

---

## Option A: Run it on your Mac

### Where the secrets go
In a file named **`.env`**, inside this `trial_formatter` folder. It has already been created for you with your API key filled in. Open it and change the password:

```
JOTFORM_API_KEY=xxxxxxxxxxxx       ← already filled in
APP_PASSWORD=test-local            ← change this to your own password
```

(Files starting with a dot are hidden in Finder. Press **Cmd + Shift + .** to show them, or open the file from the terminal with `open -e .env`.)

`.env` is listed in `.gitignore`, so it is never uploaded to GitHub.

### Start the app
In Terminal:
```
cd ~/Documents/GitHub/personal_projects/FormattingTools/trial_formatter
.venv/bin/streamlit run app.py
```
Your browser opens the app. Log in with your `APP_PASSWORD`.

---

## Option B: Run it on Railway (so you can use it from anywhere)

### 1. Push the code to GitHub
Commit and push the `trial_formatter` folder. Do **not** commit the old `fetch_and_format.py`, because it still has the API key in it.

### 2. Create the service
1. In Railway: **New Project → Deploy from GitHub repo** → pick `personal_projects`.
2. Click the new service → **Settings** tab → **Root Directory** → enter `FormattingTools/trial_formatter`.

### 3. Add the variables (this replaces the `.env` file)
Open the service → **Variables** tab → **New Variable**. Add these three:

| Variable name | Value |
|---|---|
| `JOTFORM_API_KEY` | your JotForm API key |
| `APP_PASSWORD` | the password you want |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (type `${{` and pick your Postgres service from the suggestions) |

Shows, scratches and corrections are saved in the same Postgres database as the check-in app,
in their own table (`formatter_shows`), so they're included in that database's backups.

If the app shows a red warning about `DATABASE_URL`, this step isn't right yet.

### 4. Moving from the old volume (one time)
Earlier versions saved shows on a volume. The first time the app starts with `DATABASE_URL` set,
it copies every show from the volume into the database and shows a "Moved N saved show(s)" notice.
Shows already in the database are never overwritten.

Once you've opened the app and see your shows, the volume and the `DATA_DIR` variable are no
longer used. You can delete them, or keep the volume for a while as a spare copy.

### 5. Get a web address
**Settings** tab → **Networking** → **Generate Domain**. Open that link and log in with your `APP_PASSWORD`.

---

## Using it for each trial
1. In the sidebar, choose **➕ New show**. Fill in the name and dates (form ID and show ID carry over from your last show). Click **Save show**.
2. Tick anyone who scratched, then click **Save scratches**.
3. If someone typed something odd (wrong state, misspelled name, etc.), fix it in **Fix typos** and click **Save corrections**. Fixes are kept with the show and re-applied after every JotForm refresh; tick **Undo** in the corrections list to remove one.
4. Read any warnings, then click **Download workbook**.

---

## Extras
- **Without the app:** `.venv/bin/python cli.py shows/<show-file>.yaml` writes the workbook directly. It reads the same `.env` file.
  On your Mac, without `DATABASE_URL`, shows are saved as YAML files in `shows/`.
- **If you rename questions on the JotForm**, the app will tell you which field it can't find. Add a line like this to that show's file in `shows/`:
  `field_names: {handler_number: newQuestionName}`
- **Tests:** `.venv/bin/python -m pytest tests`
