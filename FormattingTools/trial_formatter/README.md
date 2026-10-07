# Trial Data Formatter

Pulls entries from JotForm and builds the 3-tab trial workbook (Contact, Balances, Raw Results).
Shows and scratches are saved as YAML files in `shows/`.

## Run locally
```sh
cp .env.example .env          # fill in JOTFORM_API_KEY and APP_PASSWORD
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/streamlit run app.py
```
Without the app: `.venv/bin/python cli.py shows/<show>.yaml`

Tests: `.venv/bin/python -m pip install pytest && .venv/bin/python -m pytest tests`

## Deploy to Railway
1. New service → GitHub repo → **Settings → Root Directory** = `FormattingTools/trial_formatter`.
   `railway.json` supplies the start command and health check.
2. **Variables:** `JOTFORM_API_KEY`, `APP_PASSWORD`, `DATA_DIR=/data`.
3. **Volume:** attach one mounted at `/data`. Without it, saved shows and scratches are lost on every deploy.
4. **Settings → Networking → Generate Domain.**

## Each trial
1. Sidebar → **New show**. Form ID and show ID are prefilled from the last show; set the name and dates, then click **Save show**.
2. Tick scratched handlers (or individual dogs) → **Save scratches**.
3. Review the warnings → **Download workbook**.

If the JotForm questions are renamed, add a `field_names:` override to the show's YAML, e.g.
`field_names: {handler_number: handlerNumber}`. Defaults are in `DEFAULT_FIELD_NAMES` in `core.py`.
