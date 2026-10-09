"""
formatter.py

Everything the formatter screens need, built on core.py:
entries with fees and balances, scratches, typo fixes, actionable warnings and downloads.

Every change re-reads the show from the database right before saving, so edits made at the same
time in the Streamlit formatter (which shares the table) aren't lost.
"""

import hashlib
import os
from dataclasses import dataclass, field

from app import db
from app.core import (
    DOG_FIELDS, HANDLER_FIELDS, US_STATES, Show, apply_corrections, build_runs, dog_table, fetch_submissions,
    handler_table, parse_entries, to_xlsx_bytes, transform,
)

FIELD_LABELS = {
    'first_name': 'First', 'last_name': 'Last', 'email': 'Email', 'addr_line1': 'Address 1', 'addr_line2': 'Address 2',
    'city': 'Town', 'state': 'State', 'postal': 'Zip', 'dog_name': 'Dog', 'dog_breed': 'Breed',
    'jump_height': 'Jump height', 'international_level': 'Level', 'speedstakes_level': 'Speedstakes level',
}

# First three zip digits → state, for spotting a mistyped state ("ALABAMA" with a Virginia zip)
_ZIP3 = [
    (10, 27, 'MA'), (28, 29, 'RI'), (30, 38, 'NH'), (39, 49, 'ME'), (50, 59, 'VT'), (60, 69, 'CT'), (70, 89, 'NJ'),
    (100, 149, 'NY'), (150, 196, 'PA'), (197, 199, 'DE'), (200, 200, 'DC'), (201, 201, 'VA'), (202, 205, 'DC'),
    (206, 219, 'MD'), (220, 246, 'VA'), (247, 268, 'WV'), (270, 289, 'NC'), (290, 299, 'SC'), (300, 319, 'GA'),
    (320, 349, 'FL'), (350, 369, 'AL'), (370, 385, 'TN'), (386, 397, 'MS'), (398, 399, 'GA'), (400, 427, 'KY'),
    (430, 459, 'OH'), (460, 479, 'IN'), (480, 499, 'MI'), (500, 528, 'IA'), (530, 549, 'WI'), (550, 567, 'MN'),
    (569, 569, 'DC'), (570, 577, 'SD'), (580, 588, 'ND'), (590, 599, 'MT'), (600, 629, 'IL'), (630, 658, 'MO'),
    (660, 679, 'KS'), (680, 693, 'NE'), (700, 714, 'LA'), (716, 729, 'AR'), (730, 749, 'OK'), (750, 799, 'TX'),
    (800, 816, 'CO'), (820, 831, 'WY'), (832, 838, 'ID'), (840, 847, 'UT'), (850, 865, 'AZ'), (870, 884, 'NM'),
    (885, 885, 'TX'), (889, 898, 'NV'), (900, 961, 'CA'), (967, 968, 'HI'), (970, 979, 'OR'), (980, 994, 'WA'),
    (995, 999, 'AK'),
]
_ABBREVS = set(US_STATES.values())


def zip_state(postal: str):
    digits = ''.join(ch for ch in str(postal) if ch.isdigit())
    if len(digits) < 5:
        return None
    z = int(digits[:3])
    return next((st for lo, hi, st in _ZIP3 if lo <= z <= hi), None)


def state_abbrev(state: str):
    s = str(state or '').strip().upper()
    return s if s in _ABBREVS else US_STATES.get(s)


class FormatterError(Exception):
    pass


# ── Loading a show with its entries ─────────────────────────────────────────

def refresh(show: Show):
    key = os.environ.get('JOTFORM_API_KEY')
    if not key:
        raise FormatterError('JOTFORM_API_KEY is not set, so entries can’t be fetched from JotForm.')
    try:
        subs = fetch_submissions(show.form_id, key)
    except Exception as e:  # noqa: BLE001 — network/API errors are shown on the page
        raise FormatterError(f'JotForm didn’t respond as expected: {e}')
    db.save_snapshot(show.form_id, subs)


@dataclass
class ShowData:
    slug: str
    show: Show
    fetched_at: object
    entries: list                       # as typed in JotForm
    corrected: list                     # with typo fixes applied
    rows: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    new_ids: set = field(default_factory=set)
    last_download: dict = None


def load(slug: str, fetch_if_missing: bool = True) -> ShowData:
    show = db.get_show(slug)
    if show is None:
        raise FormatterError('That show doesn’t exist.')
    subs, fetched_at = db.get_snapshot(show.form_id)
    if subs is None and fetch_if_missing:
        refresh(show)
        subs, fetched_at = db.get_snapshot(show.form_id)
    entries = parse_entries(subs or [], show.field_names)
    data = ShowData(slug, show, fetched_at, entries, apply_corrections(entries, show))
    history = db.download_history(slug, limit=1)
    data.last_download = history[0] if history else None
    if data.last_download:
        seen = set(data.last_download['submission_ids'] or [])
        data.new_ids = {e['submission_id'] for e in entries if e['submission_id'] not in seen}
    _build_rows(data)
    return data


def is_scratched(show: Show, e: dict) -> bool:
    return e['handler_number'] in set(show.scratched_handlers) or (
        bool(e['dog_number']) and e['dog_number'] in set(show.scratched_dogs))


def _build_rows(d: ShowData):
    runs = build_runs(d.corrected, d.show)
    fee_by_sub = runs.groupby('submission_id')['class_cost'].sum().to_dict() if len(runs) else {}
    runs_by_sub = runs.groupby('submission_id').size().to_dict() if len(runs) else {}

    by_handler = {}
    for e in d.corrected:
        by_handler.setdefault(e['handler_number'], []).append(e)

    rows, balance_out, scratched = [], 0.0, 0
    handlers_live, dogs_live, runs_live = 0, 0, 0
    order = sorted(by_handler.items(), key=lambda kv: (kv[1][0]['last_name'].lower(), kv[1][0]['first_name'].lower()))
    for hid, es in order:
        paid = sum(e['amount_paid'] or 0 for e in es)
        live = [e for e in es if not is_scratched(d.show, e)]
        fees = sum(fee_by_sub.get(e['submission_id'], 0) for e in live)
        balance = fees - paid
        if live:
            handlers_live += 1
            balance_out += max(balance, 0)
        es = sorted(es, key=lambda e: e['dog_name'].lower())
        for i, e in enumerate(es):
            scr = is_scratched(d.show, e)
            scratched += scr
            if not scr:
                dogs_live += 1
                runs_live += runs_by_sub.get(e['submission_id'], 0)
            rows.append({
                'first_of_handler': i == 0, 'handler_number': hid,
                'handler_name': f"{e['first_name']} {e['last_name']}".strip(),
                'submission_id': e['submission_id'], 'dog_number': e['dog_number'], 'dog_name': e['dog_name'],
                'breed': e['dog_breed'], 'height': e['jump_height'], 'level': e['international_level'],
                'sat': len(e['saturday_classes']), 'sun': len(e['sunday_classes']),
                'fee': fee_by_sub.get(e['submission_id'], 0.0),
                'paid': paid, 'balance': balance, 'scratched': scr,
                'new': e['submission_id'] in d.new_ids,
                'fixed': any(k == hid for k in d.show.handler_corrections) or e['dog_number'] in d.show.dog_corrections,
            })
    d.rows = rows
    d.stats = {'handlers': handlers_live, 'dogs': dogs_live, 'runs': runs_live, 'scratched': scratched,
               'balance': balance_out, 'submissions': len(d.entries)}


def filter_rows(rows: list, q: str = '', f: str = 'all') -> list:
    q = (q or '').strip().lower()
    out = []
    for r in rows:
        if q and q not in f"{r['handler_name']} {r['handler_number']} {r['dog_name']} {r['dog_number']}".lower():
            continue
        if f == 'new' and not r['new']:
            continue
        if f == 'scr' and not r['scratched']:
            continue
        if f == 'owes' and r['balance'] <= 0:
            continue
        out.append(r)
    # When filtering, every row shows its handler (not just the first row of each handler)
    if q or f != 'all':
        out = [{**r, 'first_of_handler': True} for r in out]
    return out


# ── Scratches ───────────────────────────────────────────────────────────────

def toggle_scratch(slug: str, dog_number: str, user: dict) -> bool:
    """Flip one dog's scratch. Returns True if the dog is now scratched."""
    show = db.get_show(slug)
    data_entries = load(slug).corrected
    entry = next((e for e in data_entries if e['dog_number'] == dog_number), None)
    if entry is None or not dog_number:
        raise FormatterError('That dog isn’t in this show’s entries.')
    hid = entry['handler_number']
    dogs = set(show.scratched_dogs)
    if hid in show.scratched_handlers:
        # Turning one dog back on for a whole-handler scratch: scratch their other dogs individually instead
        show.scratched_handlers = [h for h in show.scratched_handlers if h != hid]
        dogs |= {e['dog_number'] for e in data_entries if e['handler_number'] == hid and e['dog_number'] != dog_number}
        now_scratched = False
    elif dog_number in dogs:
        dogs.discard(dog_number)
        now_scratched = False
    else:
        dogs.add(dog_number)
        now_scratched = True
    show.scratched_dogs = sorted(dogs)
    db.save_show(show, slug)
    db.log(user, 'scratch' if now_scratched else 'unscratch', slug, f'd:{dog_number}', dog=entry['dog_name'])
    return now_scratched


# ── Typo fixes ──────────────────────────────────────────────────────────────

def typo_tables(d: ShowData, kind: str):
    """(rows, columns) for the Fix typos table: each cell has value, original and fixed flag."""
    if kind == 'handlers':
        orig, cur, key, fields = handler_table(d.entries), handler_table(d.corrected), 'handler_number', HANDLER_FIELDS
    else:
        orig, cur, key, fields = dog_table(d.entries), dog_table(d.corrected), 'dog_number', DOG_FIELDS
    orig = orig.set_index(key)
    rows = []
    for r in cur.to_dict('records'):
        num = r[key]
        cells = []
        for f in fields:
            o = str(orig.at[num, f]) if num in orig.index else ''
            cells.append({'field': f, 'value': str(r[f]), 'orig': o, 'fixed': str(r[f]) != o})
        rows.append({'num': num, 'label': r.get('handler', ''), 'cells': cells})
    rows.sort(key=lambda x: (x['label'] or ' '.join(c['value'] for c in x['cells'][:2])).lower())
    return rows, fields


def set_fix(slug: str, kind: str, num: str, fld: str, value: str, user: dict):
    """kind 'h' (handler) or 'd' (dog). Typing the JotForm value back removes the fix."""
    allowed = HANDLER_FIELDS if kind == 'h' else DOG_FIELDS
    if fld not in allowed:
        raise FormatterError('That field can’t be edited.')
    show = db.get_show(slug)
    d = load(slug)
    table = handler_table(d.entries) if kind == 'h' else dog_table(d.entries)
    keycol = 'handler_number' if kind == 'h' else 'dog_number'
    match = table[table[keycol] == num]
    if match.empty:
        raise FormatterError('That entry isn’t in this show.')
    original = str(match.iloc[0][fld])
    value = (value or '').strip()
    corrections = show.handler_corrections if kind == 'h' else show.dog_corrections
    fields = dict(corrections.get(num, {}))
    if value == original:
        fields.pop(fld, None)
        action = 'unfix'
    else:
        fields[fld] = value
        action = 'fix'
    if fields:
        corrections[num] = fields
    else:
        corrections.pop(num, None)
    db.save_show(show, slug)
    db.log(user, action, slug, f'{kind}:{num}:{fld}', original=original, value=value)


def corrections_log(d: ShowData) -> list:
    who = db.last_actions(d.slug, 'fix')
    items = []
    for kind, corr, fields in (('h', d.show.handler_corrections, HANDLER_FIELDS), ('d', d.show.dog_corrections, DOG_FIELDS)):
        table = handler_table(d.entries) if kind == 'h' else dog_table(d.entries)
        keycol = 'handler_number' if kind == 'h' else 'dog_number'
        orig = table.set_index(keycol)
        for num, fx in corr.items():
            for f, v in fx.items():
                key = f'{kind}:{num}:{f}'
                by, at = who.get(key, ('', None))
                items.append({'key': key, 'kind': 'Handler' if kind == 'h' else 'Dog', 'num': num, 'field': FIELD_LABELS.get(f, f),
                              'from': str(orig.at[num, f]) if num in orig.index else '(not in JotForm)', 'to': v,
                              'by': by, 'at': at})
    items.sort(key=lambda x: x['at'] or db.now().replace(year=2000), reverse=True)
    return items


def undo_fix(slug: str, key: str, user: dict):
    kind, num, fld = key.split(':', 2)
    show = db.get_show(slug)
    corrections = show.handler_corrections if kind == 'h' else show.dog_corrections
    if num in corrections:
        corrections[num].pop(fld, None)
        if not corrections[num]:
            corrections.pop(num)
    db.save_show(show, slug)
    db.log(user, 'unfix', slug, key)


# ── Warnings ────────────────────────────────────────────────────────────────

def warnings(d: ShowData) -> list:
    decisions = db.warning_decisions(d.slug)
    out = []

    def add(key, kind, title, detail, actions=(), choices=None):
        dec = decisions.get(key)
        out.append({'key': key, 'kind': kind, 'title': title, 'detail': detail, 'actions': list(actions),
                    'choices': choices, 'dismissed': bool(dec), 'decision': dec})

    by_handler = {}
    for e in d.corrected:
        by_handler.setdefault(e['handler_number'], []).append(e)
    first_row = {r['handler_number']: r for r in d.rows if r['first_of_handler']}

    for hid, es in by_handler.items():
        name = f"{es[0]['first_name']} {es[0]['last_name']}".strip()
        r = first_row.get(hid)
        live = [e for e in es if not is_scratched(d.show, e)]
        if r and live and r['balance'] < -0.005:
            add(f'credit:{hid}', 'money', f'{name} ({hid}) has a credit of ${-r["balance"]:.2f}',
                f'Paid ${r["paid"]:.2f} for ${r["paid"] + r["balance"]:.2f} of classes. Check whether they dropped a class after paying.',
                actions=['show'])
        if not live:
            continue
        exclude = {a.upper() for a in d.show.exclude_addresses}
        variants = sorted({e['addr_line1'].upper() for e in es if e['addr_line1']})
        if len(variants) > 1 and len([v for v in variants if v not in exclude]) > 1:
            add(f'addr:{hid}', 'addr', f'{name} ({hid}) typed their address {len(variants)} ways',
                'Which one goes on the Contact tab?', choices=variants)
        st, zs = state_abbrev(es[0]['state']), zip_state(es[0]['postal'])
        if zs and st and zs != st:
            add(f'zip:{hid}', 'data', f'{name} ({hid}): state is "{es[0]["state"]}" but the zip code {es[0]["postal"][:5]} is in {zs}',
                'Probably a typo in the form.', actions=[f'fixstate:{zs}'])

    # Anything else core.transform noticed (unknown class names, missing numbers, unreadable payments...)
    for text in transform(d.entries, d.show).warnings:
        if 'negative balance' in text or 'different addresses' in text:
            continue  # covered by the actionable warnings above
        add('core:' + hashlib.sha1(text.encode()).hexdigest()[:12], 'data', text, '')
    out.sort(key=lambda w: w['dismissed'])
    return out


def open_warning_count(ws: list) -> int:
    return sum(not w['dismissed'] for w in ws)


def choose_address(slug: str, hid: str, chosen: str, user: dict):
    show = db.get_show(slug)
    d = load(slug)
    variants = {e['addr_line1'].upper() for e in d.corrected if e['handler_number'] == hid and e['addr_line1']}
    if chosen.upper() not in variants:
        raise FormatterError('That address isn’t one of the options.')
    exclude = {a.upper() for a in show.exclude_addresses}
    exclude = (exclude | (variants - {chosen.upper()})) - {chosen.upper()}
    show.exclude_addresses = sorted(exclude)
    db.save_show(show, slug)
    db.set_warning(slug, f'addr:{hid}', 'resolved', f'Using "{chosen}" on the Contact tab.', user['name'])
    db.log(user, 'warning', slug, f'addr:{hid}', chosen=chosen)


def fix_state(slug: str, hid: str, state: str, user: dict):
    set_fix(slug, 'h', hid, 'state', state, user)
    db.set_warning(slug, f'zip:{hid}', 'resolved', f'State set to {state} (see Fix typos).', user['name'])


def dismiss(slug: str, key: str, user: dict, reopen: bool = False):
    db.set_warning(slug, key, None if reopen else 'dismissed', '' if reopen else f'Dismissed by {user["name"]}.', user['name'])
    db.log(user, 'warning', slug, key, reopened=reopen)


# ── Download ────────────────────────────────────────────────────────────────

def workbook_counts(d: ShowData) -> dict:
    r = transform(d.entries, d.show)
    return {'contact': len(r.contact), 'balances': len(r.balances), 'runs': len(r.raw_results)}


def download(slug: str, user: dict):
    d = load(slug)
    result = transform(d.entries, d.show)
    db.record_download(slug, user, len(result.contact), len(result.raw_results), [e['submission_id'] for e in d.entries])
    db.log(user, 'download', slug, runs=len(result.raw_results))
    return to_xlsx_bytes(result), f'TrialData_{slug}.xlsx'
