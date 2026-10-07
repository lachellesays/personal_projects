"""
fetch_and_format.py

Fetches submissions from JotForm, applies all data transformations (replacing
the Supabase SQL step), and writes a formatted Excel workbook with three sheets:
  Contact     — handler contact info
  Balances    — amounts owed per handler
  Raw Results — one row per run, ready for generate_results.py

Edit the CONFIG section below before each trial, then run:
    python3 fetch_and_format.py
"""

# ── CONFIG ──────────────────────────────────────────────────────────────────
JOTFORM_API_KEY         = '76680f3e636f49a630da81de5668a282'
JOTFORM_FORM_ID         = '261266312429152'
SATURDAY_DATE           = '2026-06-06'
SUNDAY_DATE             = '2026-06-07'
SHOW_ID                 = 6892
OUTPUT_PATH             = 'TrialDataJune.xlsx'

EXCLUDE_HANDLER_NUMBERS = []
EXCLUDE_DOG_NUMBERS     = []
EXCLUDE_ADDR_LINE1      = []
EXCLUDE_ADDR_LINE2      = []
# ────────────────────────────────────────────────────────────────────────────

import re
import sys
import ast
import json

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")
try:
    import pandas as pd
except ImportError:
    sys.exit("Missing: pip install pandas")
try:
    import openpyxl  # noqa: F401 — required by pandas ExcelWriter
except ImportError:
    sys.exit("Missing: pip install openpyxl")


# Maps verbose JotForm class names to the short names used in the trial system
CLASS_NAME_MAP = {
    'Agility 1':                                  'Agility',
    'Jumping 1':                                  'Jumping',
    'Open Speedstakes 1 (all levels combined)':   'Speedstakes',
    'Open Gamblers (all levels combined)':         'Gamblers',
}


# ── Field parsers ────────────────────────────────────────────────────────────

def _to_dict(value) -> dict:
    """Return a dict from a JotForm answer (already a dict, or a string repr)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return ast.literal_eval(value)
        except Exception:
            pass
    return {}


def _to_list(value) -> list:
    """Return a list from a JotForm answer (already a list, or a '[...]' string)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith('['):
        try:
            return ast.literal_eval(value)
        except Exception:
            pass
    return []


def _payment_total(value) -> float:
    """Extract the total amount paid from a JotForm payment answer.

    JotForm nests the total inside value['paymentArray'], which is a JSON string
    containing {"total": "65.00", ...}.
    """
    if isinstance(value, dict):
        payment_array = value.get('paymentArray')
        if payment_array:
            try:
                parsed = json.loads(payment_array)
                return float(parsed.get('total') or 0)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        # Fallback: stringify the whole dict and regex-scan it
        value = str(value)
    if isinstance(value, str):
        m = re.search(r'"total"\s*:\s*"?([0-9.]+)"?', value)
        if m:
            return float(m.group(1))
    return 0.0


def _clean_height(jh) -> str:
    """
    'X Regular' → 'X inch'
    'X Select'  → 'X inch (s)'
    anything else → unchanged
    Mirrors the SQL formatted_height view logic.
    """
    s = str(jh).strip() if jh else ''
    if not s:
        return s
    lower = s.lower()
    first = s.split()[0]
    if 'regular' in lower:
        return first + ' inch'
    if 'select' in lower:
        return first + ' inch (s)'
    return s


def _cap_first(s) -> str:
    """Uppercase the first letter only — mirrors SQL UPPER(LEFT(s,1)) || SUBSTRING(s,2)."""
    s = str(s or '').strip()
    return s[:1].upper() + s[1:] if s else ''


def _safe_str(value) -> str:
    """Return empty string for None/NaN, otherwise str."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value)


# ── Step 1: Fetch from JotForm ───────────────────────────────────────────────

def fetch_raw() -> pd.DataFrame:
    url = (
        f"https://api.jotform.com/form/{JOTFORM_FORM_ID}/submissions"
        f"?apiKey={JOTFORM_API_KEY}&limit=1000"
    )
    print("Fetching from JotForm...")
    resp = requests.get(url)
    resp.raise_for_status()
    submissions = resp.json().get('content', [])
    print(f"  {len(submissions)} submissions received.")

    rows = []
    for sub in submissions:
        row = {'submission_id': sub.get('id'), 'created_at': sub.get('created_at')}
        for _, f_data in sub.get('answers', {}).items():
            row[f_data.get('text', '')] = f_data.get('answer')
        rows.append(row)

    df = pd.DataFrame(rows)

    # Same positional column selection as the original jotform.py
    df_sel = df.iloc[:, [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 17, 19, 20]].copy()
    df_sel.columns = [
        'submission_id', 'created_at',
        'handler_name', 'handler_number',
        'dog_name', 'dog_number', 'jump_height',
        'international_level', 'speedstakes_level',
        'saturday_classes', 'sunday_classes',
        'payment_data', 'Email', 'address', 'dog_breed',
    ]
    df_sel['submission_id'] = pd.to_numeric(df_sel['submission_id'], errors='coerce')
    return df_sel


# ── Step 2: Build class data (mirrors class_data SQL view) ───────────────────

def build_class_data(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, sub in df.iterrows():
        name_d     = _to_dict(sub['handler_name'])
        first_name = _safe_str(name_d.get('first'))
        last_name  = _safe_str(name_d.get('last'))
        full_name  = (first_name + ' ' + last_name).strip()

        # Strip leading H / D|d (mirrors formatted_handler_number / formatted_dog_number)
        handler_num = _safe_str(sub['handler_number']).lstrip('H')
        dog_num     = re.sub(r'^[Dd]+', '', _safe_str(sub['dog_number']))
        height      = _clean_height(sub['jump_height'])

        sat_classes = _to_list(sub['saturday_classes'])
        sun_classes = _to_list(sub['sunday_classes'])

        for cls, date in (
            [(c, SATURDAY_DATE) for c in sat_classes] +
            [(c, SUNDAY_DATE)   for c in sun_classes]
        ):
            # formatted_class_level: Speedstakes classes use speedstakes_level
            if 'speedstakes' in cls.lower():
                level = _safe_str(sub['speedstakes_level'])
            else:
                level = _safe_str(sub['international_level'])

            # class_info: Champion → Champ, rename class names
            level     = 'Champ' if level == 'Champion' else level
            clean_cls = CLASS_NAME_MAP.get(cls, cls)

            rows.append({
                'show_id':        SHOW_ID,
                'class_level':    level,
                'class_name':     clean_cls,
                'jump_height':    height,
                'date':           date,
                'handler_number': handler_num,
                'full_name':      full_name,
                'dog_number':     dog_num,
                'dog_name':       _safe_str(sub['dog_name']),
                'dog_breed':      _safe_str(sub['dog_breed']),
                'Email':          _safe_str(sub['Email']),
            })

    if not rows:
        return pd.DataFrame()

    class_df = pd.DataFrame(rows)

    # master_series_handling: expand 'Masters Series' into two rows
    masters     = class_df[class_df['class_name'] == 'Masters Series']
    non_masters = class_df[class_df['class_name'] != 'Masters Series']
    ma = masters.copy(); ma['class_name'] = 'Masters Agility'
    mj = masters.copy(); mj['class_name'] = 'Masters Jumping'
    class_df = pd.concat([non_masters, ma, mj], ignore_index=True)

    # Apply exclusion filters (mirrors WHERE clause in class_data view)
    class_df = class_df[~class_df['handler_number'].isin(EXCLUDE_HANDLER_NUMBERS)]
    class_df = class_df[~class_df['dog_number'].isin(EXCLUDE_DOG_NUMBERS)]

    return class_df.reset_index(drop=True)


# ── Step 3: Build contact data (mirrors contact_info SQL view) ───────────────

def build_contact_data(df: pd.DataFrame, valid_handlers: set) -> pd.DataFrame:
    rows = []
    seen = set()
    exclude_addr1 = {a.upper() for a in EXCLUDE_ADDR_LINE1}
    exclude_addr2 = {a.upper() for a in EXCLUDE_ADDR_LINE2}

    for _, sub in df.iterrows():
        handler_num = _safe_str(sub['handler_number']).lstrip('H')
        if handler_num not in valid_handlers or handler_num in seen:
            continue

        name_d = _to_dict(sub['handler_name'])
        addr_d = _to_dict(sub['address'])

        addr1  = _safe_str(addr_d.get('addr_line1')).upper()
        addr2  = _safe_str(addr_d.get('addr_line2')).upper()
        city   = _safe_str(addr_d.get('city')).upper()
        state  = _safe_str(addr_d.get('state')).upper()
        postal = _safe_str(addr_d.get('postal'))[:5]

        if addr1 in exclude_addr1 or addr2 in exclude_addr2:
            continue

        if state == 'VIRGINIA':
            state = 'VA'

        rows.append({
            'Member ID':  handler_num,
            'Last Name':  _cap_first(name_d.get('last')),
            'First Name': _cap_first(name_d.get('first')),
            'Address 1':  addr1,
            'Address 2':  addr2,
            'Address 3':  '',
            'Town':       city,
            'County':     state,
            'Postcode':   postal,
            'EMail':      _safe_str(sub['Email']).upper(),
            'Home':       '',
            'Work':       '',
            'Cell':       '',
        })
        seen.add(handler_num)

    return pd.DataFrame(rows)


# ── Step 4: Build balance data (mirrors balances_owed SQL view) ──────────────

def build_balance_data(class_df: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    valid_handlers = set(class_df['handler_number'].unique())

    # create_balances / create_sums: class costs per handler
    costs = class_df[['handler_number', 'class_name']].copy()
    costs['class_cost'] = costs['class_name'].apply(
        lambda n: 17.50 if n.lower().startswith('master') else 15.00
    )
    owed = (
        costs.groupby('handler_number')['class_cost']
        .sum()
        .reset_index()
        .rename(columns={'class_cost': 'total_owed'})
    )

    # paid_stage1 / total_paid: total amount paid per handler
    paid_rows = []
    for _, sub in df.iterrows():
        hn    = _safe_str(sub['handler_number']).lstrip('H')
        total = _payment_total(sub['payment_data'])
        paid_rows.append({'handler_number': hn, 'amount_paid': total})
    paid_df = (
        pd.DataFrame(paid_rows)
        .groupby('handler_number')['amount_paid']
        .sum()
        .reset_index()
    )

    # Names for the output sheet
    name_rows = []
    for _, sub in df.iterrows():
        hn    = _safe_str(sub['handler_number']).lstrip('H')
        name_d = _to_dict(sub['handler_name'])
        name_rows.append({
            'handler_number': hn,
            'last_name':  _safe_str(name_d.get('last')),
            'first_name': _safe_str(name_d.get('first')),
        })
    names_df = pd.DataFrame(name_rows).drop_duplicates(subset='handler_number')

    result = owed.merge(paid_df, on='handler_number', how='left')
    result['amount_paid'] = result['amount_paid'].fillna(0)
    result['OWES'] = result['total_owed'] - result['amount_paid']
    result = result.merge(names_df, on='handler_number', how='left')
    result = result[result['handler_number'].isin(valid_handlers)].reset_index(drop=True)
    return result


# ── Step 5: Write Excel ──────────────────────────────────────────────────────

def write_excel(class_df: pd.DataFrame, contact_df: pd.DataFrame, balance_df: pd.DataFrame):
    # Raw Results — matches the input format expected by generate_results.py
    raw_results = pd.DataFrame({
        'Show ID':      class_df['show_id'],
        'Level':        class_df['class_level'],
        'Class':        class_df['class_name'],
        'Height':       class_df['jump_height'],
        'Date':         class_df['date'].apply(
                            lambda d: pd.to_datetime(d).strftime('%-m/%-d/%Y')
                        ),
        'Member ID':    pd.to_numeric(class_df['handler_number'], errors='coerce'),
        'Member Name':  class_df['full_name'],
        'Dog ID':       pd.to_numeric(class_df['dog_number'], errors='coerce'),
        'Dog Name':     class_df['dog_name'],
        'Breed':        class_df['dog_breed'],
        'SCT':          '',
        'Faults':       '',
        'Games Points': '',
        'Time':         '',
        'Place':        '',
        'Level Points': '',
        'Member Email': class_df['Email'],
    })

    # Balances
    balances_out = pd.DataFrame({
        'Member ID':  pd.to_numeric(balance_df['handler_number'], errors='coerce'),
        'Last Name':  balance_df['last_name'].apply(_cap_first),
        'First Name': balance_df['first_name'].apply(_cap_first),
        'OWES':       balance_df['OWES'].astype(float),
    })

    # Convert Contact Member ID to numeric to match the trial system's format
    contact_out = contact_df.copy()
    contact_out['Member ID'] = pd.to_numeric(contact_out['Member ID'], errors='coerce')

    with pd.ExcelWriter(OUTPUT_PATH, engine='openpyxl') as writer:
        contact_out.to_excel(writer,  sheet_name='Contact',     index=False)
        balances_out.to_excel(writer, sheet_name='Balances',    index=False)
        raw_results.to_excel(writer,  sheet_name='Raw Results', index=False)

    print(f"\nWritten: {OUTPUT_PATH}")
    print(f"  Contact:     {len(contact_df)} handlers")
    print(f"  Balances:    {len(balances_out)} handlers")
    print(f"  Raw Results: {len(raw_results)} runs")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    df = fetch_raw()

    print("Building class data...")
    class_df = build_class_data(df)
    if class_df.empty:
        print("No class data found — check your JotForm form ID and API key.")
        return
    print(f"  {len(class_df)} runs (after filters and Masters Series expansion)")

    valid_handlers = set(class_df['handler_number'].unique())

    print("Building contact data...")
    contact_df = build_contact_data(df, valid_handlers)
    print(f"  {len(contact_df)} unique handlers")

    print("Building balance data...")
    balance_df = build_balance_data(class_df, df)

    write_excel(class_df, contact_df, balance_df)


if __name__ == '__main__':
    main()
