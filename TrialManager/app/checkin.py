"""
checkin.py

The live link to the check-in app (Trial Secretary). Both apps sit in one Railway project, so instead of
downloading a CSV and pasting results JSON, Trial Manager reads and writes the check-in app's own tables through
CHECKIN_DATABASE_URL:

  run order  → check-in `runs`            after every change (a diff: gate statuses and run ids are never replaced)
  A-frame    → check-in `aframe_settings` with each run order push
  results    → check-in `results`         when a class is calculated
  gate       ← check-in `runs.status`     scratches / NFC shown on the scoring sheet

Nothing is sent until the show is switched "Live on check-in" on the Run order page. Each pushed day is marked in
check-in's `managed_days`, which locks its Upload/Reorder/A-frame/Publish tools there; if someone unlocks a day in
check-in (Trial Manager down), pushes for that day stop until it's handed back.

The table definitions below are copies of CheckinApp/db.py (a test checks they still match).
"""

import hashlib
import json
import os
from datetime import date

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, Integer, LargeBinary, MetaData, String, Table, UniqueConstraint, and_,
    create_engine, delete, insert, select, update,
)

from app import db

NOT_CHECKED_IN = 'Not Checked In'
SCRATCH = 'Scratch'
SOURCE = 'Trial Manager'

cmeta = MetaData()

runs = Table(
    'runs', cmeta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False, index=True),
    Column('day', String, default=''),
    Column('class_name', String, nullable=False),
    Column('class_order', Integer, nullable=False),
    Column('position', Float, nullable=False),
    Column('source_run_order', String, default=''),
    Column('ring', String, default=''),
    Column('event', String, default=''),
    Column('event_num', String, default=''),
    Column('level', String, default=''),
    Column('class_type', String, default=''),
    Column('height', String, default=''),
    Column('dog_name', String, default=''),
    Column('breed', String, default=''),
    Column('first_name', String, default=''),
    Column('last_name', String, default=''),
    Column('handler_name', String, default=''),
    Column('handler_number', String, default='', index=True),
    Column('dog_number', String, default=''),
    Column('run_group', String, default=''),
    Column('team_name', String, default=''),
    Column('status', String, nullable=False, default=NOT_CHECKED_IN),
    Column('updated_at', DateTime(timezone=True)),
)

results = Table(
    'results', cmeta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False),
    Column('class_name', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('submitted_at', DateTime(timezone=True)),
    UniqueConstraint('trial_date', 'class_name'),
)

aframe_settings = Table(
    'aframe_settings', cmeta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False),
    Column('class_name', String, nullable=False),
    Column('has_aframe', Boolean, nullable=False),
    UniqueConstraint('trial_date', 'class_name'),
)

managed_days = Table(
    'managed_days', cmeta,
    Column('trial_date', Date, primary_key=True),
    Column('source', String, nullable=False, default=SOURCE),
    Column('updated_at', DateTime(timezone=True)),
    Column('unlocked', Boolean, nullable=False, default=False),
    Column('unlocked_at', DateTime(timezone=True)),
)

# Run fields Trial Manager owns (everything except status, which belongs to the gate)
OWNED = ['day', 'class_order', 'position', 'source_run_order', 'ring', 'event', 'event_num', 'level', 'class_type',
         'height', 'dog_name', 'breed', 'first_name', 'last_name', 'handler_name', 'handler_number', 'dog_number',
         'run_group', 'team_name']


class CheckinError(Exception):
    pass


# ── Connection ──────────────────────────────────────────────────────────────

_engine = None


def database_url() -> str:
    url = (os.environ.get('CHECKIN_DATABASE_URL') or '').strip()
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def describe() -> str:
    """Which database the link points at, without the password, so it can be checked against the check-in app's."""
    from sqlalchemy.engine import make_url
    try:
        u = make_url(database_url())
    except Exception:  # noqa: BLE001
        return 'an address Trial Manager can’t read'
    where = u.host or (u.query.get('host') if u.query else '') or 'local file'
    return f"{where}{':' + str(u.port) if u.port else ''} / {u.database or ''}".strip(' /')


def runs_in_checkin() -> dict:
    """{trial date: run count} currently in the check-in app, to confirm the push landed where the gate reads."""
    from sqlalchemy import func
    with engine().connect() as c:
        return {str(r[0]): r[1] for r in c.execute(select(runs.c.trial_date, func.count()).group_by(runs.c.trial_date)
                                                    .order_by(runs.c.trial_date))}


def configured() -> bool:
    return bool(database_url())


def engine():
    """The check-in app's database. Its tables are created by the check-in app, but creating any that are missing
    is harmless (same definitions) and lets a fresh setup or a test work in either order."""
    global _engine
    if _engine is None:
        if not configured():
            raise CheckinError('CHECKIN_DATABASE_URL isn’t set, so Trial Manager can’t reach the check-in app.')
        eng = create_engine(database_url(), pool_pre_ping=True)
        cmeta.create_all(eng)
        _engine = eng
    return _engine


def friendly(e: Exception) -> str:
    """A one-line reason for the Run order page, without SQL."""
    if isinstance(e, CheckinError):
        return str(e)
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    if isinstance(e, OperationalError):
        return 'Couldn’t reach the check-in app’s database. Check CHECKIN_DATABASE_URL on Railway, then click Send now.'
    if isinstance(e, SQLAlchemyError):
        detail = str(getattr(e, 'orig', '') or e).strip().splitlines()[0][:160]
        return f'The check-in app’s database refused the update ({detail}). Click Send now to try again.'
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def reset_engine():
    global _engine
    _engine = None


# ── The show's switch and sync status ───────────────────────────────────────

def is_live(slug: str) -> bool:
    with db.engine().connect() as c:
        return bool(c.execute(select(db.checkin_shows.c.live).where(db.checkin_shows.c.show_slug == slug)).scalar())


def set_live(slug: str, on: bool, user: dict):
    with db.engine().begin() as c:
        c.execute(delete(db.checkin_shows).where(db.checkin_shows.c.show_slug == slug))
        c.execute(insert(db.checkin_shows).values(show_slug=slug, live=on, changed_at=db.now(),
                                                  changed_by=user.get('name', '')))
        if on:   # send everything fresh
            c.execute(update(db.checkin_days).where(db.checkin_days.c.show_slug == slug).values(fingerprint=''))
    db.log(user, 'checkin live' if on else 'checkin off', slug)


def _day_row(c, slug: str, day: str) -> dict:
    row = c.execute(select(db.checkin_days).where(db.checkin_days.c.show_slug == slug, db.checkin_days.c.day == day)
                    ).mappings().first()
    if row is None:
        c.execute(insert(db.checkin_days).values(show_slug=slug, day=day, fingerprint='', error=''))
        return {'fingerprint': '', 'pushed_at': None, 'error': '', 'error_at': None}
    return dict(row)


def status(slug: str, days: list) -> dict:
    """For the Run order page: is the link set up, is the show live, and how each day's last push went."""
    out = {'configured': configured(), 'live': is_live(slug), 'days': {}, 'target': describe() if configured() else '',
           'there': None, 'there_error': ''}
    if configured():
        try:
            out['there'] = runs_in_checkin()
        except Exception as e:  # noqa: BLE001
            out['there_error'] = friendly(e)
    with db.engine().connect() as c:
        rows = {r['day']: dict(r) for r in c.execute(select(db.checkin_days).where(db.checkin_days.c.show_slug == slug)
                                                       ).mappings()}
    for d in days:
        r = rows.get(d, {})
        out['days'][d] = {'pushed_at': db.aware(r.get('pushed_at')), 'error': r.get('error') or '',
                          'error_at': db.aware(r.get('error_at'))}
    return out


def _record(slug: str, day: str, fingerprint: str = None, error: str = ''):
    with db.engine().begin() as c:
        _day_row(c, slug, day)
        values = {'error': error, 'error_at': db.now() if error else None}
        if fingerprint is not None:
            values.update(fingerprint=fingerprint, pushed_at=db.now())
        c.execute(update(db.checkin_days).where(db.checkin_days.c.show_slug == slug, db.checkin_days.c.day == day)
                  .values(**values))


# ── Run order + A-frame ─────────────────────────────────────────────────────

def _key(class_name, dog_number, dog_name='', handler_name='') -> tuple:
    """How a run is matched between the apps (the same rule check-in uses when a CSV is re-uploaded)."""
    dog_number = str(dog_number or '').strip()
    if dog_number:
        return (str(class_name).strip().lower(), dog_number)
    return (str(class_name).strip().lower(), '', str(dog_name).strip().lower(), str(handler_name).strip().lower())


def desired_runs(slug: str, day: str) -> list:
    """The day's runs as check-in rows, in running order. Runs scratched here come along marked out=True so they
    stay in the gate list as Scratch instead of vanishing."""
    from app import trial
    rows, class_order, position = [], {}, {}
    for r in trial.ordered_for_export(slug, day, include_out=True):
        x = trial.export_row(r)
        cls = x['Combined Class Name']
        class_order.setdefault(cls, len(class_order) + 1)
        position[cls] = position.get(cls, 0) + 1
        rows.append({'class_name': cls, 'class_order': class_order[cls], 'position': float(position[cls]),
                     'source_run_order': str(x['Run_Order']) if not r['out'] else 'scratched', 'day': x['Day'],
                     'ring': str(x['Ring'] or ''), 'event': x['Event'], 'event_num': str(x['Event_Num']),
                     'level': x['Level'] or '', 'class_type': x['Class_Type'] or '', 'height': str(x['Intl_Jump_Ht'] or ''),
                     'dog_name': x['Name'] or '', 'breed': x['Breed'] or '', 'first_name': x['First_Name'] or '',
                     'last_name': x['Last_Name'] or '', 'handler_name': x['Handler_Name'],
                     'handler_number': str(x['UKI_Number'] or ''), 'dog_number': str(x['UKI_Dog_Number'] or ''),
                     'run_group': x['Run Group'], 'team_name': x['Team Name'], 'out': bool(r['out']),
                     'has_aframe': r['has_aframe']})
    return rows


def _default_aframe(class_name: str) -> bool:
    return 'agility' in (class_name or '').lower()   # check-in's own default (db.default_aframe)


def push_day(slug: str, day: str, force: bool = False) -> dict:
    """Bring check-in's copy of one day up to date. Returns {'sent': bool, 'reason': str, 'added', 'removed', ...}."""
    want = desired_runs(slug, day)
    fp = hashlib.sha256(json.dumps(want, sort_keys=True, default=str).encode()).hexdigest()
    with db.engine().connect() as c:
        last = c.execute(select(db.checkin_days.c.fingerprint).where(
            db.checkin_days.c.show_slug == slug, db.checkin_days.c.day == day)).scalar()
    if fp == last and not force:
        return {'sent': False, 'reason': 'unchanged'}

    trial_date = date.fromisoformat(day)
    now = db.now()
    counts = {'added': 0, 'updated': 0, 'removed': 0, 'scratched': 0}
    with engine().begin() as c:
        md = c.execute(select(managed_days).where(managed_days.c.trial_date == trial_date)).mappings().first()
        if md and md['unlocked']:
            raise CheckinError('This day is unlocked for hand edits in the check-in app, so it isn’t being updated. '
                               'Hand it back to Trial Manager there (Admin) to turn updates on again.')
        have = {}
        extra_ids = []
        for r in c.execute(select(runs).where(runs.c.trial_date == trial_date).order_by(runs.c.id)).mappings():
            k = _key(r['class_name'], r['dog_number'], r['dog_name'], r['handler_name'])
            if k in have:
                extra_ids.append(r['id'])     # a duplicate left over from a hand edit
            else:
                have[k] = dict(r)
        seen = set()
        for w in want:
            k = _key(w['class_name'], w['dog_number'], w['dog_name'], w['handler_name'])
            if k in seen:
                continue
            seen.add(k)
            cur = have.get(k)
            if cur is None:
                c.execute(insert(runs).values(trial_date=trial_date, status=SCRATCH if w['out'] else NOT_CHECKED_IN,
                                              updated_at=now, class_name=w['class_name'],
                                              **{f: w[f] for f in OWNED}))
                counts['added'] += 1
                continue
            values = {f: w[f] for f in OWNED + ['class_name'] if cur.get(f) != w[f]}
            if w['out'] and cur['status'] != SCRATCH:
                values.update(status=SCRATCH, updated_at=now)    # scratched in Trial Manager
                counts['scratched'] += 1
            elif not w['out'] and cur['source_run_order'] == 'scratched' and cur['status'] == SCRATCH:
                values.update(status=NOT_CHECKED_IN, updated_at=now)   # un-scratched in Trial Manager
            if values:
                c.execute(update(runs).where(runs.c.id == cur['id']).values(**values))
                counts['updated'] += 1
        gone = [r['id'] for k, r in have.items() if k not in seen] + extra_ids
        if gone:
            c.execute(delete(runs).where(runs.c.id.in_(gone)))
            counts['removed'] = len(gone)

        # A-frame banners: store only classes that differ from check-in's default, as check-in does
        c.execute(delete(aframe_settings).where(aframe_settings.c.trial_date == trial_date))
        aframe = {}
        for w in want:
            aframe.setdefault(w['class_name'], w['has_aframe'])
        for cls, on in aframe.items():
            if on != _default_aframe(cls):
                c.execute(insert(aframe_settings).values(trial_date=trial_date, class_name=cls, has_aframe=on))

        c.execute(delete(managed_days).where(managed_days.c.trial_date == trial_date))
        c.execute(insert(managed_days).values(trial_date=trial_date, source=SOURCE, updated_at=now, unlocked=False))
    _record(slug, day, fingerprint=fp)
    return {'sent': True, 'reason': '', **counts}


def sync_show(slug: str, force: bool = False) -> dict:
    """Push every day of a live show whose run order changed. Safe to call after any change: it does nothing when
    the link isn't set up, the show isn't live, the run order isn't built, or nothing changed. Never raises; each
    day's failure is recorded for the Run order page."""
    from app import trial
    if not configured() or not is_live(slug) or not trial.is_built(slug):
        return {}
    show = db.get_show(slug)
    out = {}
    for d in [str(show.saturday_date), str(show.sunday_date)]:
        try:
            out[d] = push_day(slug, d, force=force)
        except Exception as e:  # noqa: BLE001 - shown on the page; the CSV is still there as a fallback
            reason = friendly(e)
            _record(slug, d, error=reason)
            out[d] = {'sent': False, 'reason': reason}
    return out


# ── Results ─────────────────────────────────────────────────────────────────

def publish_results(slug: str, day: str, class_name: str) -> int:
    """Publish a calculated class to check-in's Results tab, one entry per check-in class (e.g. Senior/Champ
    Agility). Returns how many check-in classes were published (0 when the show isn't live)."""
    from app import results as res
    if not configured() or not is_live(slug):
        return 0
    trial_date = date.fromisoformat(day)
    ev = res.entry_view(slug, day, class_name)
    if not ev['calculated']:
        return 0
    groups = sorted({r['combined'] for r in ev['rows']})
    with engine().begin() as c:
        md = c.execute(select(managed_days).where(managed_days.c.trial_date == trial_date)).mappings().first()
        if md and md['unlocked']:
            raise CheckinError('This day is unlocked in the check-in app, so results weren’t sent.')
        for combined in groups:
            data = res.results_json(slug, day, class_name, combined)
            c.execute(delete(results).where(and_(results.c.trial_date == trial_date, results.c.class_name == combined)))
            c.execute(insert(results).values(trial_date=trial_date, class_name=combined, data=data, submitted_at=db.now()))
    return len(groups)


# ── Gate statuses → scoring ─────────────────────────────────────────────────

def gate_statuses(slug: str, day: str, class_name: str) -> dict:
    """{Trial Manager run id: gate status} for one class, or {} if the link isn't set up / the show isn't live."""
    from app import trial
    if not configured() or not is_live(slug):
        return {}
    mine = [r for r in trial.ordered_for_export(slug, day) if r['class_name'] == class_name]
    if not mine:
        return {}
    names = {r['combined'] for r in mine}
    with engine().connect() as c:
        gate = {_key(r['class_name'], r['dog_number'], r['dog_name'], r['handler_name']): r['status']
                for r in c.execute(select(runs).where(runs.c.trial_date == date.fromisoformat(day),
                                                      runs.c.class_name.in_(names))).mappings()}
    out = {}
    for r in mine:
        handler = f"{r['first_name']} {r['last_name']}".strip()
        s = gate.get(_key(r['combined'], r['dog_number'], r['dog_name'], handler))
        if s:
            out[r['id']] = s
    return out


GATE_TO_SCORE = {SCRATCH: 'ABS', 'NFC': 'NFC'}


def fill_from_gate(slug: str, day: str, class_name: str, user: dict, dry_run: bool = False) -> dict:
    """Score unscored dogs from the gate: Scratch → ABS, NFC → NFC. Never touches a dog that already has a score."""
    from app import results as res
    gate = gate_statuses(slug, day, class_name)
    todo = []
    for r in res.class_runs(slug, day, class_name):
        mark = GATE_TO_SCORE.get(gate.get(r['id']))
        if mark and not res.has_score(r['score']):
            todo.append((r['id'], mark))
    if not dry_run:
        for run_id, mark in todo:
            res.save_score(slug, run_id, {'status': mark}, user)
    return {'ABS': sum(m == 'ABS' for _, m in todo), 'NFC': sum(m == 'NFC' for _, m in todo)}
