"""
db.py

Ledger for Level Up invoicing. Works with Railway Postgres (DATABASE_URL) in production and falls back
to a local SQLite file for development and tests. No Streamlit code lives here.

Money is stored in whole cents. Work adds credit (positive amounts); payments and barter subtract it
(negative amounts). Balance owed = the sum of every amount, the same as the old spreadsheet's Total Owed.
"""

import os
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import (
    JSON, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String, Table,
    and_, create_engine, delete, func, insert, inspect, select, text, update,
)

KINDS = {'class': 'Class', 'sub': 'Sub', 'trial': 'Trial secretary', 'credit_other': 'Other work',
         'barter': 'Barter', 'payment': 'Payment'}
CLASS_STATUSES = {'scheduled': 'Scheduled', 'taught': 'Taught', 'subbed_out': 'Subbed out',
                  'cancelled': 'Cancelled', 'no_class': 'No class'}
DEBIT_KINDS = ('barter', 'payment')
INVOICE_STATUSES = ('draft', 'sent', 'paid')
LOCKED_STATUSES = ('sent', 'paid')       # entries on these invoices can't change until the invoice is voided

DEFAULT_SETTINGS = {
    'instructor_name': 'Lachelle Henderson',
    'instructor_contact': '',
    'client_name': 'Level Up Dog Sports',
    'client_address': '',
    'hourly_rate_cents': '2500',
    'trial_rate_cents': '100',
    'payment_note': '',
}

metadata = MetaData()

settings = Table(
    'inv_settings', metadata,
    Column('key', String, primary_key=True),
    Column('value', String, nullable=False, default=''),
)

sessions = Table(
    'inv_sessions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', String, nullable=False),               # "Session 8"
    Column('first_date', Date, nullable=False),           # weekday comes from this date
    Column('weeks', Integer, nullable=False, default=6),
    Column('time_label', String, default=''),             # "6:00 - 9:00"
    Column('hours', Float, nullable=False, default=0),
    Column('rate_cents', Integer, nullable=False),
    Column('skip_dates', JSON, default=list),             # ISO dates with no class (workshop weeks)
    Column('created_at', DateTime(timezone=True)),
)

invoices = Table(
    'inv_invoices', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('number', Integer, nullable=False, unique=True),
    Column('title', String, default=''),                  # e.g. "April Invoice (imported)"
    Column('invoice_date', Date, nullable=False),
    Column('period_start', Date),                         # the period the invoice covers, start to end
    Column('period_end', Date, nullable=False),           # work up to and including this date
    Column('status', String, nullable=False, default='draft'),
    Column('created_at', DateTime(timezone=True)),
)

entries = Table(
    'inv_entries', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('entry_date', Date, index=True),               # may be blank on imported rows (flagged for review)
    Column('kind', String, nullable=False),
    Column('description', String, nullable=False, default=''),
    Column('session_id', Integer, ForeignKey('inv_sessions.id', ondelete='SET NULL')),
    Column('status', String),                             # classes only, see CLASS_STATUSES
    Column('time_label', String, default=''),
    Column('hours', Float),                               # classes and subs
    Column('quantity', Float),                            # trial runs
    Column('rate_cents', Integer),                        # copied onto the row so later rate changes don't rewrite history
    Column('amount_cents', Integer, nullable=False, default=0),
    Column('comments', String, default=''),
    Column('invoice_id', Integer, ForeignKey('inv_invoices.id', ondelete='SET NULL'), index=True),
    Column('created_at', DateTime(timezone=True)),
    Column('updated_at', DateTime(timezone=True)),
)


class LockedError(Exception):
    """The entry is on a sent or paid invoice."""


def _now():
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = os.environ.get('DATABASE_URL') or 'sqlite:///invoice_local.db'
    # Railway gives postgres:// or postgresql://; SQLAlchemy needs the psycopg driver named
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def get_engine(url: str = None):
    engine = create_engine(url or database_url(), pool_pre_ping=True)
    metadata.create_all(engine)
    _upgrade(engine)
    return engine


def _upgrade(engine):
    """Bring a database made by an older version up to date (create_all only makes missing tables)."""
    cols = {c['name'] for c in inspect(engine).get_columns('inv_invoices')}
    if 'period_start' not in cols:
        with engine.begin() as c:
            c.execute(text('ALTER TABLE inv_invoices ADD COLUMN period_start DATE'))
    # Invoices made before there was a start date: the day after the previous invoice, else its earliest row
    with engine.begin() as c:
        prev_end = None
        for inv in c.execute(select(invoices).order_by(invoices.c.number)).mappings().all():
            if inv['period_start'] is None:
                first_row = c.execute(select(func.min(entries.c.entry_date))
                                      .where(entries.c.invoice_id == inv['id'])).scalar()
                start = prev_end + timedelta(days=1) if prev_end else (first_row or inv['period_end'])
                c.execute(update(invoices).where(invoices.c.id == inv['id']).values(period_start=start))
            prev_end = inv['period_end']


# ── Money and hours ──────────────────────────────────────────────────────────

def money(cents) -> str:
    cents = int(cents or 0)
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}".replace('.00', '')


def to_cents(dollars) -> int:
    return int(round(float(dollars or 0) * 100))


def hours_from_label(label: str):
    """'6:00 - 9:00' → 3.0. Evening times without AM/PM are read on a 12-hour clock. None if unreadable."""
    m = re.match(r'\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$', label or '')
    if not m:
        return None
    start = int(m[1]) % 12 + int(m[2] or 0) / 60
    end = int(m[3]) % 12 + int(m[4] or 0) / 60
    if end <= start:
        end += 12
    return round(end - start, 2)


def compute_amount(kind: str, status=None, hours=None, quantity=None, rate_cents=None, value_cents=None) -> int:
    """The row's ledger amount in cents. value_cents is the dollar value for other work, barter and payments."""
    if kind == 'class':
        return int(round((hours or 0) * (rate_cents or 0))) if status == 'taught' else 0
    if kind == 'sub':
        return int(round((hours or 0) * (rate_cents or 0)))
    if kind == 'trial':
        return int(round((quantity or 0) * (rate_cents or 0)))
    if kind == 'credit_other':
        return abs(int(value_cents or 0))
    if kind in DEBIT_KINDS:
        return -abs(int(value_cents or 0))
    raise ValueError(f'unknown kind {kind!r}')


# ── Settings ─────────────────────────────────────────────────────────────────

def get_settings(engine) -> dict:
    with engine.connect() as c:
        saved = {r.key: r.value for r in c.execute(select(settings))}
    return {**DEFAULT_SETTINGS, **saved}


def save_settings(engine, values: dict):
    with engine.begin() as c:
        for k, v in values.items():
            c.execute(delete(settings).where(settings.c.key == k))
            c.execute(insert(settings).values(key=k, value=str(v)))


# ── Entries ──────────────────────────────────────────────────────────────────

def _row_values(kind, status=None, hours=None, quantity=None, rate_cents=None, value_cents=None, **rest):
    if kind not in KINDS:
        raise ValueError(f'unknown kind {kind!r}')
    if kind == 'class' and status not in CLASS_STATUSES:
        raise ValueError(f'unknown class status {status!r}')
    return dict(rest, kind=kind, status=status if kind == 'class' else None, hours=hours, quantity=quantity,
                rate_cents=rate_cents,
                amount_cents=compute_amount(kind, status, hours, quantity, rate_cents, value_cents))


def add_entry(engine, kind: str, **fields) -> int:
    """fields: entry_date, description, status, time_label, hours, quantity, rate_cents, value_cents, comments,
    session_id. Returns the new id."""
    vals = _row_values(kind, **fields)
    with engine.begin() as c:
        return c.execute(insert(entries).values(**vals, created_at=_now(), updated_at=_now())).inserted_primary_key[0]


def _check_unlocked(c, entry_id: int):
    row = c.execute(select(invoices.c.status, invoices.c.number)
                    .select_from(entries.join(invoices, entries.c.invoice_id == invoices.c.id))
                    .where(entries.c.id == entry_id)).first()
    if row and row.status in LOCKED_STATUSES:
        raise LockedError(f'This row is on invoice #{row.number}, which is {row.status}. Void the invoice to change it.')


def update_entry(engine, entry_id: int, **fields):
    """Replace the row's editable fields (pass them all, like add_entry) and recompute its amount."""
    with engine.begin() as c:
        _check_unlocked(c, entry_id)
        current = c.execute(select(entries).where(entries.c.id == entry_id)).mappings().first()
        if current is None:
            return
        merged = {k: current[k] for k in ('kind', 'status', 'hours', 'quantity', 'rate_cents')}
        merged['value_cents'] = current['amount_cents']
        merged.update(fields)
        c.execute(update(entries).where(entries.c.id == entry_id)
                  .values(**_row_values(**merged), updated_at=_now()))


def set_class_status(engine, entry_id: int, status: str, hours=None, comments=None):
    """One-tap update from the Home tab. hours=None keeps the row's hours."""
    fields = {'status': status}
    if hours is not None:
        fields['hours'] = hours
    if comments is not None:
        fields['comments'] = comments
    update_entry(engine, entry_id, **fields)


def delete_entry(engine, entry_id: int):
    with engine.begin() as c:
        _check_unlocked(c, entry_id)
        c.execute(delete(entries).where(entries.c.id == entry_id))


def get_entry(engine, entry_id: int):
    with engine.connect() as c:
        return c.execute(select(entries).where(entries.c.id == entry_id)).mappings().first()


def all_entries(engine) -> list:
    """Every row with its invoice number/status, oldest first (undated rows last)."""
    q = (select(entries, invoices.c.number.label('invoice_number'), invoices.c.status.label('invoice_status'))
         .select_from(entries.outerjoin(invoices, entries.c.invoice_id == invoices.c.id))
         .order_by(entries.c.entry_date.is_(None), entries.c.entry_date, entries.c.id))
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings()]


def totals(engine) -> dict:
    with engine.connect() as c:
        credits = c.execute(select(func.coalesce(func.sum(entries.c.amount_cents), 0))
                            .where(entries.c.amount_cents > 0)).scalar()
        debits = c.execute(select(func.coalesce(func.sum(entries.c.amount_cents), 0))
                           .where(entries.c.amount_cents < 0)).scalar()
        new_work = c.execute(select(func.coalesce(func.sum(entries.c.amount_cents), 0))
                             .where(and_(entries.c.invoice_id.is_(None), entries.c.amount_cents > 0))).scalar()
        new_debits = c.execute(select(func.coalesce(func.sum(entries.c.amount_cents), 0))
                               .where(and_(entries.c.invoice_id.is_(None), entries.c.amount_cents < 0))).scalar()
    return {'credits': int(credits), 'debits': int(debits), 'owed': int(credits + debits),
            'new_work': int(new_work), 'new_debits': int(new_debits)}


def classes_to_mark(engine, through: date) -> list:
    """Scheduled classes on or before `through` (past ones included, so nothing is forgotten)."""
    q = (select(entries).where(and_(entries.c.kind == 'class', entries.c.status == 'scheduled',
                                    entries.c.entry_date <= through))
         .order_by(entries.c.entry_date, entries.c.id))
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings()]


def upcoming_classes(engine, after: date, limit: int = 3) -> list:
    q = (select(entries).where(and_(entries.c.kind == 'class', entries.c.status == 'scheduled',
                                    entries.c.entry_date > after))
         .order_by(entries.c.entry_date, entries.c.id).limit(limit))
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings()]


# ── Sessions ─────────────────────────────────────────────────────────────────

def session_dates(first_date: date, weeks: int, skip_dates=()) -> tuple:
    """(class dates, skipped dates). Weekly from first_date; a skipped week pushes the rest back a week."""
    skip = {d if isinstance(d, date) else date.fromisoformat(d) for d in skip_dates}
    classes, skipped, d = [], [], first_date
    while len(classes) < weeks:
        (skipped if d in skip else classes).append(d)
        d += timedelta(days=7)
    return classes, skipped


def create_session(engine, name: str, first_date: date, weeks: int, time_label: str, hours: float,
                   rate_cents: int, skip_dates=()) -> int:
    """Save the session and add its class rows (Scheduled) plus a No class row for each skipped week."""
    class_dates, skipped = session_dates(first_date, weeks, skip_dates)
    weekday = first_date.strftime('%A')
    with engine.begin() as c:
        sid = c.execute(insert(sessions).values(
            name=name, first_date=first_date, weeks=weeks, time_label=time_label, hours=hours,
            rate_cents=rate_cents, skip_dates=[d.isoformat() for d in skipped], created_at=_now())).inserted_primary_key[0]
        for i, d in enumerate(class_dates, start=1):
            c.execute(insert(entries).values(**_row_values(
                'class', status='scheduled', hours=hours, rate_cents=rate_cents, entry_date=d,
                description=f'{weekday} {name} Wk {i}', session_id=sid, time_label=time_label),
                created_at=_now(), updated_at=_now()))
        for d in skipped:
            c.execute(insert(entries).values(**_row_values(
                'class', status='no_class', entry_date=d, description='Workshop Week - No Class', session_id=sid),
                created_at=_now(), updated_at=_now()))
    return sid


def all_sessions(engine) -> list:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(sessions).order_by(sessions.c.first_date.desc())).mappings()]


# ── Invoices ─────────────────────────────────────────────────────────────────

def next_invoice_number(engine) -> int:
    with engine.connect() as c:
        return int(c.execute(select(func.coalesce(func.max(invoices.c.number), 0))).scalar()) + 1


def default_period_start(engine, through: date) -> date:
    """The day after the last invoice's period ends, so periods don't overlap or leave gaps.
    Before the first invoice: the earliest row that would go on it."""
    with engine.connect() as c:
        last_end = c.execute(select(invoices.c.period_end).order_by(invoices.c.number.desc()).limit(1)).scalar()
    if last_end:
        return last_end + timedelta(days=1)
    rows = invoiceable(engine, through)
    return rows[0]['entry_date'] if rows else through


def invoiceable(engine, through: date) -> list:
    """Rows a new invoice through `through` would pick up: dated, not on an invoice, and not a class still
    marked Scheduled (it hasn't been taught yet, or still needs marking)."""
    q = (select(entries).where(and_(entries.c.invoice_id.is_(None), entries.c.entry_date.is_not(None),
                                    entries.c.entry_date <= through,
                                    ~and_(entries.c.kind == 'class', entries.c.status == 'scheduled')))
         .order_by(entries.c.entry_date, entries.c.id))
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings()]


def create_invoice(engine, through: date, invoice_date: date, title: str = '', status: str = 'draft',
                   entry_ids=None, period_start: date = None) -> int:
    """Put the invoiceable rows (or exactly entry_ids) onto a new invoice. Returns its id.
    Uninvoiced rows dated before period_start are still included, so nothing is ever left off."""
    ids = entry_ids if entry_ids is not None else [r['id'] for r in invoiceable(engine, through)]
    if period_start is None:
        period_start = default_period_start(engine, through)
    if period_start > through:
        raise ValueError('The period start is after its end.')
    number = next_invoice_number(engine)
    with engine.begin() as c:
        inv_id = c.execute(insert(invoices).values(number=number, title=title, invoice_date=invoice_date,
                                                   period_start=period_start, period_end=through, status=status,
                                                   created_at=_now())).inserted_primary_key[0]
        if ids:
            c.execute(update(entries).where(and_(entries.c.id.in_(ids), entries.c.invoice_id.is_(None)))
                      .values(invoice_id=inv_id))
    return inv_id


def set_invoice_status(engine, invoice_id: int, status: str):
    if status not in INVOICE_STATUSES:
        raise ValueError(status)
    with engine.begin() as c:
        c.execute(update(invoices).where(invoices.c.id == invoice_id).values(status=status))


def void_invoice(engine, invoice_id: int):
    """Delete the invoice; its rows go back to not invoiced."""
    with engine.begin() as c:
        c.execute(update(entries).where(entries.c.invoice_id == invoice_id).values(invoice_id=None))
        c.execute(delete(invoices).where(invoices.c.id == invoice_id))


def all_invoices(engine) -> list:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(invoices).order_by(invoices.c.number.desc())).mappings()]


def invoice_detail(engine, invoice_id: int) -> dict:
    """The invoice, its rows, and its balance: previous balance (everything on earlier invoices) plus this one."""
    with engine.connect() as c:
        inv = c.execute(select(invoices).where(invoices.c.id == invoice_id)).mappings().first()
        if inv is None:
            return None
        rows = [dict(r) for r in c.execute(select(entries).where(entries.c.invoice_id == invoice_id)
                                           .order_by(entries.c.entry_date, entries.c.id)).mappings()]
        previous = c.execute(
            select(func.coalesce(func.sum(entries.c.amount_cents), 0))
            .select_from(entries.join(invoices, entries.c.invoice_id == invoices.c.id))
            .where(invoices.c.number < inv['number'])).scalar()
    credits = sum(r['amount_cents'] for r in rows if r['amount_cents'] > 0)
    debits = sum(r['amount_cents'] for r in rows if r['amount_cents'] < 0)
    return {'invoice': dict(inv), 'rows': rows, 'previous': int(previous), 'credits': credits, 'debits': debits,
            'balance': int(previous) + credits + debits}


def ledger_is_empty(engine) -> bool:
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(entries)).scalar() == 0


def wipe(engine):
    """Remove every row, invoice and session (settings are kept). Used by 'replace with spreadsheet'."""
    with engine.begin() as c:
        c.execute(delete(entries))
        c.execute(delete(invoices))
        c.execute(delete(sessions))
