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

### 3. Add the secrets (this replaces the `.env` file)
Open the service → **Variables** tab → **New Variable**. Add these three:

| Variable name | Value |
|---|---|
| `JOTFORM_API_KEY` | your JotForm API key |
| `APP_PASSWORD` | the password you want |
| `DATA_DIR` | `/data` |

### 4. Add storage for your saved shows
Right-click the service (or use **+ New**) → **Volume** → set the mount path to `/data`.
Without this, your shows and scratches are erased every time Railway redeploys.

### 5. Get a web address
**Settings** tab → **Networking** → **Generate Domain**. Open that link and log in with your `APP_PASSWORD`.

---

## Using it for each trial
1. In the sidebar, choose **➕ New show**. Fill in the name and dates (form ID and show ID carry over from your last show). Click **Save show**.
2. Tick anyone who scratched, then click **Save scratches**.
3. Read any warnings, then click **Download workbook**.

---

## Extras
- **Without the app:** `.venv/bin/python cli.py shows/<show-file>.yaml` writes the workbook directly. It reads the same `.env` file.
- **If you rename questions on the JotForm**, the app will tell you which field it can't find. Add a line like this to that show's file in `shows/`:
  `field_names: {handler_number: newQuestionName}`
- **Tests:** `.venv/bin/python -m pytest tests`
