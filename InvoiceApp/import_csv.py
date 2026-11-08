"""
import_csv.py

One-time import of the old "Level Up Debits & Credits" spreadsheet (exported as CSV).
Amounts come in exactly as the sheet has them, so the totals reconcile; anything that looks off is
listed for review instead of being guessed at.

    python import_csv.py "~/Downloads/Level Up Debits & Credits - 2026.csv"     (dry run: prints the review list)
"""

import csv
import io
import re
import sys
from collections import defaultdict
from datetime import date

import db

YEAR = 2026


def _f(v):
    try:
        return float(str(v).replace('$', '').replace(',', '').strip())
    except ValueError:
        return None


def _date(v):
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        return None


def _date_from_name(text: str, year: int):
    """'Trial 8/29-8/30' → the last m/d in the text (the end of the trial)."""
    found = re.findall(r'(\d{1,2})/(\d{1,2})', text or '')
    if not found:
        return None
    m, d = found[-1]
    try:
        return date(year, int(m), int(d))
    except ValueError:
        return None


def parse(text: str) -> dict:
    """Read the CSV text. Returns {'settings', 'rows', 'review', 'sheet_totals'} without touching the database."""
    lines = list(csv.reader(io.StringIO(text)))
    head = lines[0] + [''] * 12
    rate = _f(head[4]) or 25
    settings = {'instructor_name': head[1].strip() or db.DEFAULT_SETTINGS['instructor_name'],
                'hourly_rate_cents': str(db.to_cents(rate))}
    sheet_totals = {}
    for line in lines[:3]:
        for i, cell in enumerate(line[:-1]):
            if cell.strip() in ('Total Debits', 'Total Credits', 'Total Owed'):
                sheet_totals[cell.strip()] = db.to_cents(_f(line[i + 1]) or 0)

    rate_cents = db.to_cents(rate)
    rows, review = [], []
    for line in lines[3:]:
        line = (line + [''] * 10)[:10]
        session, name, d, time_label, taught, comments, hours, dollars, invoiced = [c.strip() for c in line[:9]]
        if not (session or name):
            continue
        entry_date = _date(d)
        amount = db.to_cents(_f(dollars) or 0)
        row = {'session': session, 'entry_date': entry_date, 'description': name, 'time_label': time_label,
               'comments': comments, 'invoiced': invoiced.upper() == 'TRUE', 'hours': None, 'quantity': None,
               'rate_cents': None, 'value_cents': None, 'status': None, 'sheet_amount': amount}
        key = session.upper()

        if session.isdigit():
            row['hours'] = _f(hours)
            row['rate_cents'] = rate_cents
            if name.lower().startswith('sub'):
                row['kind'] = 'sub'
                if row['hours'] is None:
                    row['hours'] = db.hours_from_label(time_label) or 0
            else:
                row['kind'] = 'class'
                t = taught.upper()
                if t == 'Y':
                    row['status'] = 'taught'
                    if row['hours'] is None:
                        row['hours'] = 0
                        review.append(f"{name} ({d}) is marked Taught but has no hours, so it's $0. "
                                      f"If you taught it, edit the row and enter the hours.")
                elif t == 'N':
                    row['status'] = 'subbed_out' if 'sub' in comments.lower() else 'cancelled'
                else:
                    row['status'] = 'scheduled'
        elif key == 'BARTER':
            row['kind'] = 'barter'
            row['value_cents'] = amount
        elif key == 'PAID':
            row['kind'] = 'payment'
            row['value_cents'] = amount
        elif key == 'WW':
            row['kind'] = 'class'
            row['status'] = 'no_class'
        elif 'TRIAL' in key or 'secretary' in name.lower():
            row['kind'] = 'trial'
            m = re.search(r'(\d+)\s*runs?\s*at\s*\$?([\d.]+)', comments, re.I)
            if m:
                row['quantity'], row['rate_cents'] = float(m[1]), db.to_cents(m[2])
            else:
                row['quantity'], row['rate_cents'] = 1, amount
        else:
            row['kind'] = 'credit_other' if amount >= 0 else 'barter'
            row['value_cents'] = amount
            review.append(f"Didn't recognise the row '{session} / {name}'; imported as {db.KINDS[row['kind']]}.")

        if entry_date is None:
            guessed = _date_from_name(name, YEAR) if row['kind'] in ('barter', 'payment', 'credit_other') else None
            if guessed:
                row['entry_date'] = guessed
                review.append(f"{db.KINDS[row['kind']]} '{name}' had no date; used {guessed:%-m/%-d/%Y} from its name.")
            else:
                review.append(f"{db.KINDS[row['kind']]} '{name}' has no date. Give it one on the Ledger tab "
                              f"so it can go on an invoice.")

        row['amount_cents'] = db.compute_amount(row['kind'], row['status'], row['hours'], row['quantity'],
                                                row['rate_cents'], row['value_cents'])
        if row['amount_cents'] != amount:
            review.append(f"'{name}': the sheet says {db.money(amount)} but the rows work out to "
                          f"{db.money(row['amount_cents'])}. Check it on the Ledger tab.")
        rows.append(row)

    # Same session, same date twice is almost always a typo
    by_session_date = defaultdict(list)
    for r in rows:
        if r['session'].isdigit() and r['kind'] == 'class' and r['entry_date']:
            by_session_date[(r['session'], r['entry_date'])].append(r['description'])
    for (s, d), names in by_session_date.items():
        if len(names) > 1:
            review.append(f"{' and '.join(names)} are both dated {d:%-m/%-d/%Y}. One date is probably a typo.")

    scheduled = [r for r in rows if r['status'] == 'scheduled']
    if scheduled:
        review.append(f"{len(scheduled)} class(es) from {scheduled[0]['description']} on have no Taught status yet, "
                      f"so they come in as Scheduled. Mark them on the Home tab as you teach them.")
    return {'settings': settings, 'rows': rows, 'review': review, 'sheet_totals': sheet_totals}


def import_into(engine, parsed: dict) -> dict:
    """Write a parse() result into an empty ledger. Returns the new totals."""
    if not db.ledger_is_empty(engine):
        raise ValueError('The ledger already has rows. Clear it first.')
    db.save_settings(engine, parsed['settings'])
    rows = parsed['rows']

    # One session per numbered session, so classes can be grouped; hours default from the session's times
    session_ids = {}
    for num in dict.fromkeys(r['session'] for r in rows if r['session'].isdigit()):
        mine = [r for r in rows if r['session'] == num and r['kind'] == 'class']
        if not mine:
            continue
        label = next((r['time_label'] for r in reversed(mine) if r['time_label']), '')
        hours = db.hours_from_label(label) or 0
        first = min((r['entry_date'] for r in mine if r['entry_date']), default=None)
        if first is None:
            continue
        with engine.begin() as c:
            session_ids[num] = c.execute(db.insert(db.sessions).values(
                name=f'Session {num}', first_date=first, weeks=sum(r['status'] != 'no_class' for r in mine),
                time_label=label, hours=hours, rate_cents=mine[0]['rate_cents'], skip_dates=[],
                created_at=db._now())).inserted_primary_key[0]
        for r in mine:
            if r['status'] == 'scheduled':
                r['hours'] = r['hours'] or db.hours_from_label(r['time_label']) or hours
                r['time_label'] = r['time_label'] or label

    invoiced_ids = []
    for r in rows:
        new_id = db.add_entry(engine, r['kind'], entry_date=r['entry_date'], description=r['description'],
                              status=r['status'], time_label=r['time_label'], hours=r['hours'],
                              quantity=r['quantity'], rate_cents=r['rate_cents'], value_cents=r['value_cents'],
                              comments=r['comments'], session_id=session_ids.get(r['session']))
        if r['invoiced']:
            invoiced_ids.append(new_id)

    if invoiced_ids:
        dated = [r['entry_date'] for r in rows if r['invoiced'] and r['entry_date']]
        db.create_invoice(engine, through=max(dated), invoice_date=max(dated), title='April Invoice (imported)',
                          status='paid', entry_ids=invoiced_ids, period_start=min(dated))
    return db.totals(engine)


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'Level Up Debits & Credits - 2026.csv'
    with open(path, newline='', encoding='utf-8-sig') as fh:
        p = parse(fh.read())
    credits = sum(r['amount_cents'] for r in p['rows'] if r['amount_cents'] > 0)
    debits = sum(r['amount_cents'] for r in p['rows'] if r['amount_cents'] < 0)
    print(f"{len(p['rows'])} rows · credits {db.money(credits)} · debits {db.money(debits)} · "
          f"owed {db.money(credits + debits)}   (sheet: {p['sheet_totals']})")
    for item in p['review']:
        print(' -', item)
