"""
core.py  (copied unchanged from FormattingTools/trial_formatter/core.py)

Fetches JotForm submissions and turns them into the three-sheet trial workbook:
  Contact     — handler contact info
  Balances    — amounts owed per handler
  Raw Results — one row per run, ready for generate_results.py

No UI code lives here; app.py and cli.py both call into this module.
"""

import io
import re
import ast
import json
from dataclasses import dataclass, field

import pandas as pd
import requests


# Maps verbose JotForm class names to the short names used in the trial system
CLASS_NAME_MAP = {
    'Agility 1':                                  'Agility',
    'Jumping 1':                                  'Jumping',
    'Open Speedstakes 1 (all levels combined)':   'Speedstakes',
    'Open Gamblers (all levels combined)':        'Gamblers',
}

KNOWN_CLASSES = {
    'Agility', 'Agility 2', 'Jumping', 'Jumping 2', 'Gamblers', 'Snooker',
    'Speedstakes', 'Speedstakes 2', 'Masters Agility', 'Masters Jumping',
}

# Run order used to sort Raw Results (matches generate_results.py CLASS_ORDER)
CLASS_ORDER = [
    'Agility', 'Agility 2', 'Jumping', 'Jumping 2',
    'Gamblers', 'Snooker', 'Speedstakes', 'Speedstakes 2',
    'Masters Agility', 'Masters Jumping',
]

MASTERS_CLASS_COST = 17.50
CLASS_COST         = 15.00

# Our field name → JotForm question "name" (the unique name set in the form builder).
# A show config can override any of these with a `field_names:` mapping.
DEFAULT_FIELD_NAMES = {
    'handler_name':        'name',
    'email':               'email',
    'address':             'address',
    'handler_number':      'handlersUki',
    'dog_name':            'dogsName',
    'dog_breed':           'dogsBreed',
    'dog_number':          'dogsUki',
    'jump_height':         'jumpHeight',
    'international_level': 'internationalLevel',
    'speedstakes_level':   'speedstakesLevel',
    'saturday_classes':    'saturdayClasses',
    'sunday_classes':      'sundayClasses',
    'payment_data':        'myProducts',
}

# Fields that can be hand-corrected in the app
HANDLER_FIELDS = ['first_name', 'last_name', 'email', 'addr_line1', 'addr_line2', 'city', 'state', 'postal']
DOG_FIELDS = ['dog_name', 'dog_breed', 'jump_height', 'international_level', 'speedstakes_level']

US_STATES = {
    'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA',
    'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'DISTRICT OF COLUMBIA': 'DC',
    'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID', 'ILLINOIS': 'IL',
    'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA',
    'MAINE': 'ME', 'MARYLAND': 'MD', 'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN',
    'MISSISSIPPI': 'MS', 'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV',
    'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY',
    'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK', 'OREGON': 'OR',
    'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC', 'SOUTH DAKOTA': 'SD',
    'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT', 'VERMONT': 'VT', 'VIRGINIA': 'VA',
    'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY',
}

CONTACT_COLUMNS = [
    'Member ID', 'Last Name', 'First Name', 'Address 1', 'Address 2', 'Address 3',
    'Town', 'County', 'Postcode', 'EMail', 'Home', 'Work', 'Cell',
]
BALANCE_COLUMNS = ['Member ID', 'Last Name', 'First Name', 'OWES']
RAW_COLUMNS = [
    'Show ID', 'Level', 'Class', 'Height', 'Date', 'Member ID', 'Member Name',
    'Dog ID', 'Dog Name', 'Breed', 'SCT', 'Faults', 'Games Points', 'Time',
    'Place', 'Level Points', 'Member Email',
]


# ── Field parsers ────────────────────────────────────────────────────────────

def _to_dict(value) -> dict:
    """Return a dict from a JotForm answer (already a dict, or a string repr)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, dict) else {}
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


def _payment_total(value):
    """Extract the total amount paid from a JotForm payment answer, or None if unreadable.

    JotForm nests the total inside value['paymentArray'], which is a JSON string
    containing {"total": "65.00", ...}.
    """
    if isinstance(value, dict):
        payment_array = value.get('paymentArray')
        if payment_array:
            try:
                parsed = json.loads(payment_array)
                return float(parsed.get('total') or 0)
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                pass
        # Fallback: stringify the whole dict and regex-scan it
        value = json.dumps(value)
    if isinstance(value, str):
        m = re.search(r'"total"\s*:\s*"?([0-9.]+)"?', value)
        if m:
            return float(m.group(1))
    return None


def _clean_height(jh) -> str:
    """
    'X Regular' → 'X inch'
    'X Select'  → 'X inch (s)'
    anything else → unchanged
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
    """Uppercase the first letter only."""
    s = str(s or '').strip()
    return s[:1].upper() + s[1:] if s else ''


def _safe_str(value) -> str:
    """Return empty string for None/NaN, otherwise a stripped str."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _handler_number(value) -> str:
    """'H12345' → '12345'."""
    return _safe_str(value).lstrip('Hh').strip()


def _dog_number(value) -> str:
    """'D12345' / 'd12345' → '12345'."""
    return re.sub(r'^[Dd]+', '', _safe_str(value)).strip()


def _to_number(s):
    """'12345' → 12345; anything non-numeric is returned unchanged."""
    s = _safe_str(s)
    return int(s) if s.isdigit() else s


# ── Show config ──────────────────────────────────────────────────────────────

@dataclass
class Show:
    name: str
    show_id: int
    form_id: str
    saturday_date: str           # 'YYYY-MM-DD'
    sunday_date: str             # 'YYYY-MM-DD'
    scratched_handlers: list = field(default_factory=list)
    scratched_dogs: list = field(default_factory=list)
    exclude_addresses: list = field(default_factory=list)
    field_names: dict = field(default_factory=dict)
    # Hand-made fixes for typos, keyed by handler/dog number: {'12349': {'state': 'VA'}}
    handler_corrections: dict = field(default_factory=dict)
    dog_corrections: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> 'Show':
        return cls(
            name=str(d.get('name') or ''),
            show_id=int(d['show_id']),
            form_id=str(d['form_id']),
            saturday_date=str(d['saturday_date']),
            sunday_date=str(d['sunday_date']),
            scratched_handlers=[str(x) for x in d.get('scratched_handlers') or []],
            scratched_dogs=[str(x) for x in d.get('scratched_dogs') or []],
            exclude_addresses=[str(x) for x in d.get('exclude_addresses') or []],
            field_names=dict(d.get('field_names') or {}),
            handler_corrections=_clean_corrections(d.get('handler_corrections'), HANDLER_FIELDS),
            dog_corrections=_clean_corrections(d.get('dog_corrections'), DOG_FIELDS),
        )

    def to_dict(self) -> dict:
        d = {
            'name': self.name,
            'show_id': self.show_id,
            'form_id': self.form_id,
            'saturday_date': self.saturday_date,
            'sunday_date': self.sunday_date,
            'scratched_handlers': [_to_number(x) for x in self.scratched_handlers],
            'scratched_dogs': [_to_number(x) for x in self.scratched_dogs],
            'exclude_addresses': list(self.exclude_addresses),
        }
        if self.handler_corrections:
            d['handler_corrections'] = {_to_number(k): dict(v) for k, v in self.handler_corrections.items()}
        if self.dog_corrections:
            d['dog_corrections'] = {_to_number(k): dict(v) for k, v in self.dog_corrections.items()}
        if self.field_names:
            d['field_names'] = dict(self.field_names)
        return d


def _clean_corrections(raw, allowed: list) -> dict:
    """Normalize a corrections mapping from YAML: string keys/values, known fields only."""
    out = {}
    for num, fields in (raw or {}).items():
        fields = {k: _safe_str(v) for k, v in (fields or {}).items() if k in allowed}
        if fields:
            out[str(num)] = fields
    return out


def apply_corrections(entries: list, show: 'Show') -> list:
    """Return copies of the entries with the show's hand-made corrections applied."""
    if not show.handler_corrections and not show.dog_corrections:
        return entries
    fixed = []
    for e in entries:
        e = {**e,
             **show.handler_corrections.get(e['handler_number'], {}),
             **show.dog_corrections.get(e['dog_number'], {})}
        fixed.append(e)
    return fixed


def handler_table(entries: list) -> pd.DataFrame:
    """One row per handler with the editable contact fields (first submission wins)."""
    rows, seen = [], set()
    for e in entries:
        if e['handler_number'] in seen:
            continue
        seen.add(e['handler_number'])
        rows.append({'handler_number': e['handler_number'], **{f: e[f] for f in HANDLER_FIELDS}})
    return pd.DataFrame(rows, columns=['handler_number'] + HANDLER_FIELDS)


def dog_table(entries: list) -> pd.DataFrame:
    """One row per dog with the editable dog fields."""
    rows, seen = [], set()
    for e in entries:
        if e['dog_number'] in seen:
            continue
        seen.add(e['dog_number'])
        rows.append({'dog_number': e['dog_number'],
                     'handler': (e['first_name'] + ' ' + e['last_name']).strip(),
                     **{f: e[f] for f in DOG_FIELDS}})
    return pd.DataFrame(rows, columns=['dog_number', 'handler'] + DOG_FIELDS)


def diff_corrections(original: pd.DataFrame, edited: pd.DataFrame, key: str, fields: list) -> dict:
    """Corrections = every cell in `edited` that differs from the JotForm `original`."""
    orig = original.set_index(key)
    out = {}
    for _, row in edited.iterrows():
        num = row[key]
        if num not in orig.index:
            continue
        changed = {f: _safe_str(row[f]) for f in fields
                   if _safe_str(row[f]) != _safe_str(orig.at[num, f])}
        if changed:
            out[str(num)] = changed
    return out


# ── Step 1: Fetch from JotForm ───────────────────────────────────────────────

def fetch_submissions(form_id: str, api_key: str, page_size: int = 1000) -> list:
    """Return every ACTIVE submission for the form, following pagination."""
    submissions = []
    offset = 0
    while True:
        resp = requests.get(
            f'https://api.jotform.com/form/{form_id}/submissions',
            params={'apiKey': api_key, 'limit': page_size, 'offset': offset},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json().get('content') or []
        submissions.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return [s for s in submissions if s.get('status', 'ACTIVE') == 'ACTIVE']


# ── Step 2: Normalize each submission into one entry ─────────────────────────

def parse_entries(submissions: list, field_names: dict = None) -> list:
    """Turn raw JotForm submissions into flat entry dicts (one per submission/dog).

    Answers are matched by the question's unique name, not its position, so
    reordering or adding questions on the form doesn't shift the data.
    """
    names = {**DEFAULT_FIELD_NAMES, **(field_names or {})}

    seen_names = {a.get('name') for s in submissions for a in (s.get('answers') or {}).values()}
    missing = [f'{ours} (JotForm name "{theirs}")'
               for ours, theirs in names.items() if theirs not in seen_names]
    if submissions and missing:
        raise ValueError(
            'These form fields were not found in the JotForm submissions: '
            + ', '.join(missing)
            + '. If the form was edited, add a `field_names:` override to the show config.'
        )

    entries = []
    for sub in submissions:
        by_name = {a.get('name'): a.get('answer') for a in (sub.get('answers') or {}).values()}
        get = lambda key: by_name.get(names[key])

        name_d = _to_dict(get('handler_name'))
        addr_d = _to_dict(get('address'))
        entries.append({
            'submission_id':       _safe_str(sub.get('id')),
            'created_at':          _safe_str(sub.get('created_at')),
            'first_name':          _safe_str(name_d.get('first')),
            'last_name':           _safe_str(name_d.get('last')),
            'handler_number':      _handler_number(get('handler_number')),
            'dog_number':          _dog_number(get('dog_number')),
            'dog_name':            _safe_str(get('dog_name')),
            'dog_breed':           _safe_str(get('dog_breed')),
            'email':               _safe_str(get('email')),
            'addr_line1':          _safe_str(addr_d.get('addr_line1')),
            'addr_line2':          _safe_str(addr_d.get('addr_line2')),
            'city':                _safe_str(addr_d.get('city')),
            'state':               _safe_str(addr_d.get('state')),
            'postal':              _safe_str(addr_d.get('postal')),
            'jump_height':         _safe_str(get('jump_height')),
            'international_level': _safe_str(get('international_level')),
            'speedstakes_level':   _safe_str(get('speedstakes_level')),
            'saturday_classes':    _to_list(get('saturday_classes')),
            'sunday_classes':      _to_list(get('sunday_classes')),
            'amount_paid':         _payment_total(get('payment_data')),
        })
    return entries


# ── Step 3: Build one row per run ────────────────────────────────────────────

def build_runs(entries: list, show: Show) -> pd.DataFrame:
    """One row per (dog, day, class), with Masters Series expanded into two classes.

    Scratches are NOT applied here, so the app can show everyone who entered.
    """
    rows = []
    for e in entries:
        for classes, date in ((e['saturday_classes'], show.saturday_date),
                              (e['sunday_classes'],   show.sunday_date)):
            for cls in classes:
                level = e['speedstakes_level'] if 'speedstakes' in cls.lower() else e['international_level']
                level = 'Champ' if level == 'Champion' else level
                clean_cls = CLASS_NAME_MAP.get(cls, cls)
                expanded = ['Masters Agility', 'Masters Jumping'] if clean_cls == 'Masters Series' else [clean_cls]
                for c in expanded:
                    rows.append({
                        'submission_id':  e['submission_id'],
                        'class_level':    level,
                        'class_name':     c,
                        'jump_height':    _clean_height(e['jump_height']),
                        'date':           date,
                        'handler_number': e['handler_number'],
                        'full_name':      (e['first_name'] + ' ' + e['last_name']).strip(),
                        'dog_number':     e['dog_number'],
                        'dog_name':       e['dog_name'],
                        'dog_breed':      e['dog_breed'],
                        'email':          e['email'],
                        'class_cost':     MASTERS_CLASS_COST if c.lower().startswith('master') else CLASS_COST,
                    })
    return pd.DataFrame(rows, columns=[
        'submission_id', 'class_level', 'class_name', 'jump_height', 'date',
        'handler_number', 'full_name', 'dog_number', 'dog_name', 'dog_breed',
        'email', 'class_cost',
    ])


def entrant_summary(entries: list, show: Show) -> pd.DataFrame:
    """One row per handler/dog for the scratch picker, with runs and money."""
    runs = build_runs(apply_corrections(entries, show), show)
    rows = []
    for (hn, dn), grp in runs.groupby(['handler_number', 'dog_number'], sort=False):
        rows.append({
            'handler_number': hn,
            'handler_name':   grp['full_name'].iloc[0],
            'dog_number':     dn,
            'dog_name':       grp['dog_name'].iloc[0],
            'runs':           len(grp),
            'owed':           grp['class_cost'].sum(),
        })
    df = pd.DataFrame(rows, columns=['handler_number', 'handler_name', 'dog_number', 'dog_name', 'runs', 'owed'])
    return df.sort_values(['handler_name', 'dog_name'], key=lambda s: s.str.lower()).reset_index(drop=True)


# ── Step 4: Assemble the three sheets ────────────────────────────────────────

@dataclass
class Result:
    contact: pd.DataFrame
    balances: pd.DataFrame
    raw_results: pd.DataFrame
    warnings: list


def transform(entries: list, show: Show) -> Result:
    entries = apply_corrections(entries, show)
    warnings = []
    scratched_h = {str(x) for x in show.scratched_handlers}
    scratched_d = {str(x) for x in show.scratched_dogs}

    runs = build_runs(entries, show)

    unknown = sorted(set(runs['class_name']) - KNOWN_CLASSES)
    if unknown:
        warnings.append('Unrecognized class names (passed through unchanged): ' + ', '.join(unknown)
                        + '. Add them to CLASS_NAME_MAP in core.py if they need renaming.')
    for e in entries:
        if not e['handler_number']:
            warnings.append(f"Submission {e['submission_id']} ({e['first_name']} {e['last_name']}) has no handler number.")
        if e['amount_paid'] is None:
            warnings.append(f"Couldn't read a payment total for submission {e['submission_id']} "
                            f"({e['first_name']} {e['last_name']}); treated as $0 paid.")

    # Scratches: drop the handler (all their dogs) or just a single dog
    runs = runs[~runs['handler_number'].isin(scratched_h) & ~runs['dog_number'].isin(scratched_d)]
    valid_handlers = set(runs['handler_number'])

    # A handler may submit several forms (one per dog); contact/name info comes from
    # their first submission. Flag any that disagree so they can be checked by hand.
    by_handler = {}
    for e in entries:
        by_handler.setdefault(e['handler_number'], []).append(e)
    for hn, es in by_handler.items():
        if hn not in valid_handlers:
            continue
        names = {(x['first_name'].lower(), x['last_name'].lower()) for x in es}
        addrs = {x['addr_line1'].upper() for x in es}
        if len(names) > 1:
            warnings.append(f'Handler {hn} used different names across submissions: '
                            + '; '.join(sorted(f'{f} {l}' for f, l in names)))
        if len(addrs) > 1:
            warnings.append(f'Handler {hn} used different addresses across submissions: '
                            + '; '.join(sorted(addrs))
                            + ' (using the first; add unwanted variants to "Exclude addresses").')

    # Contact: one row per handler. exclude_addresses lists address variants to skip
    # when a handler typed their address differently on different submissions.
    exclude_addr = {a.upper() for a in show.exclude_addresses}
    contact_rows = []
    for hn, es in by_handler.items():
        if hn not in valid_handlers:
            continue
        e = next((x for x in es if x['addr_line1'].upper() not in exclude_addr
                  and x['addr_line2'].upper() not in exclude_addr), es[0])
        addr1, addr2 = e['addr_line1'].upper(), e['addr_line2'].upper()
        state = e['state'].upper()
        contact_rows.append({
            'Member ID':  _to_number(hn),
            'Last Name':  _cap_first(e['last_name']),
            'First Name': _cap_first(e['first_name']),
            'Address 1':  addr1,
            'Address 2':  addr2,
            'Address 3':  '',
            'Town':       e['city'].upper(),
            'County':     US_STATES.get(state, state),
            'Postcode':   _to_number(e['postal'][:5]),
            'EMail':      e['email'].upper(),
            'Home':       '',
            'Work':       '',
            'Cell':       '',
        })
    contact = pd.DataFrame(contact_rows, columns=CONTACT_COLUMNS)

    # Balances: class costs minus everything the handler paid across all their submissions
    owed = runs.groupby('handler_number', sort=False)['class_cost'].sum()
    paid = {}
    for e in entries:
        paid[e['handler_number']] = paid.get(e['handler_number'], 0.0) + (e['amount_paid'] or 0.0)
    balance_rows = []
    for hn, total_owed in owed.items():
        e = by_handler[hn][0]
        owes = float(total_owed) - paid.get(hn, 0.0)
        if owes < 0:
            warnings.append(f"{e['first_name']} {e['last_name']} ({hn}) has a negative balance of {owes:.2f}.")
        balance_rows.append({
            'Member ID':  _to_number(hn),
            'Last Name':  _cap_first(e['last_name']),
            'First Name': _cap_first(e['first_name']),
            'OWES':       owes,
        })
    balances = pd.DataFrame(balance_rows, columns=BALANCE_COLUMNS)

    # Raw Results, sorted by day → handler → dog → class run order
    runs = runs.assign(
        _class_order=runs['class_name'].map(lambda c: CLASS_ORDER.index(c) if c in CLASS_ORDER else len(CLASS_ORDER)),
        _name_lower=runs['full_name'].str.lower(),
    ).sort_values(['date', '_name_lower', 'dog_name', '_class_order', 'class_name'], kind='stable')
    raw = pd.DataFrame({
        'Show ID':      show.show_id,
        'Level':        runs['class_level'],
        'Class':        runs['class_name'],
        'Height':       runs['jump_height'],
        'Date':         pd.to_datetime(runs['date']),
        'Member ID':    runs['handler_number'].map(_to_number),
        'Member Name':  runs['full_name'],
        'Dog ID':       runs['dog_number'].map(_to_number),
        'Dog Name':     runs['dog_name'],
        'Breed':        runs['dog_breed'],
        'SCT':          '',
        'Faults':       '',
        'Games Points': '',
        'Time':         '',
        'Place':        '',
        'Level Points': '',
        'Member Email': runs['email'],
    }, columns=RAW_COLUMNS).reset_index(drop=True)

    return Result(contact=contact, balances=balances, raw_results=raw, warnings=warnings)


# ── Step 5: Write Excel ──────────────────────────────────────────────────────

# Columns stored as numbers/dates; every other column gets Excel's text format ('@'),
# matching the trial system's template workbook.
_NON_TEXT_COLUMNS = {'Show ID', 'Member ID', 'Dog ID', 'Postcode', 'OWES', 'Date',
                     'Address 2', 'Address 3', 'Home', 'Work', 'Cell',
                     'SCT', 'Faults', 'Games Points', 'Time', 'Place', 'Level Points'}


def to_xlsx_bytes(result: Result) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        for sheet, df in (('Contact', result.contact),
                          ('Balances', result.balances),
                          ('Raw Results', result.raw_results)):
            # Blank strings → truly empty cells, like the template
            df = df.copy()
            for c in df.columns[df.dtypes == object]:
                df[c] = df[c].where(df[c] != '', None)
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for col_idx, col_name in enumerate(df.columns, start=1):
                if col_name == 'Date':
                    fmt = 'yyyy-mm-dd'
                elif col_name in _NON_TEXT_COLUMNS:
                    continue
                else:
                    fmt = '@'
                for (cell,) in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
                    cell.number_format = fmt
            for col_cells in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(width + 2, 8), 40)
    return buf.getvalue()
