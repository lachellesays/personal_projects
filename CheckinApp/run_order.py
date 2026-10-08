"""
run_order.py

Reads the run order CSV exported by the run-order app (grid_run_order_export.csv) and
turns it into rows for the `runs` table.
"""

import io
from datetime import datetime

import pandas as pd

# CSV column → runs column
COLUMN_MAP = {
    'Date':                'trial_date',
    'Day':                 'day',
    'Combined Class Name': 'class_name',
    'Run_Order':           'source_run_order',
    'Ring':                'ring',
    'Event':               'event',
    'Event_Num':           'event_num',
    'Level':               'level',
    'Class_Type':          'class_type',
    'Intl_Jump_Ht':        'height',
    'Name':                'dog_name',
    'Breed':               'breed',
    'First_Name':          'first_name',
    'Last_Name':           'last_name',
    'Handler_Name':        'handler_name',
    'UKI_Number':          'handler_number',
    'UKI_Dog_Number':      'dog_number',
    'Run Group':           'run_group',
    'Team Name':           'team_name',
}
REQUIRED = ['Date', 'Combined Class Name', 'Run_Order', 'Name', 'UKI_Number']


class RunOrderError(ValueError):
    pass


def _parse_date(s: str):
    s = str(s).strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise RunOrderError(f'Could not read the date "{s}". Expected something like 11/07/2026.')


def _num_str(v) -> str:
    """'18364' / 18364 / 18364.0 → '18364'; blanks → ''."""
    s = '' if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    return s


def parse_run_order_csv(data: bytes) -> pd.DataFrame:
    """Return a DataFrame with `runs` column names, ready for db.import_runs."""
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    except Exception as e:  # noqa: BLE001
        raise RunOrderError(f'Could not read the CSV file: {e}')
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise RunOrderError('The CSV is missing these columns: ' + ', '.join(missing))
    df = df[[c for c in COLUMN_MAP if c in df.columns]].rename(columns=COLUMN_MAP)
    for col in COLUMN_MAP.values():
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].map(_num_str)
    df = df[df['class_name'] != ''].copy()
    if df.empty:
        raise RunOrderError('The CSV has no runs in it.')

    df['trial_date'] = df['trial_date'].map(_parse_date)
    df['_ro'] = pd.to_numeric(df['source_run_order'], errors='coerce')
    if df['_ro'].isna().any():
        bad = df.loc[df['_ro'].isna(), 'source_run_order'].iloc[0]
        raise RunOrderError(f'Run_Order "{bad}" is not a number.')
    blank = df['handler_name'] == ''
    df.loc[blank, 'handler_name'] = (df['first_name'] + ' ' + df['last_name']).str.strip()[blank]

    # Class order within each day = order of each class's first run; position = order within class
    first = df.groupby(['trial_date', 'class_name'])['_ro'].transform('min')
    df['class_order'] = first.groupby(df['trial_date']).rank(method='dense').astype(int)
    df['position'] = df.groupby(['trial_date', 'class_name'])['_ro'].rank(method='first').astype(float)
    return df.drop(columns='_ro').sort_values(['trial_date', 'class_order', 'position']).reset_index(drop=True)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day + class, for the upload preview."""
    return (df.groupby(['trial_date', 'class_order', 'class_name'])
            .size().reset_index(name='runs')
            .sort_values(['trial_date', 'class_order'])
            .drop(columns='class_order'))
