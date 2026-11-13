"""
db.py

Database layer for the check-in app. Works with Railway Postgres (DATABASE_URL) in
production and falls back to a local SQLite file for development and tests.
No Streamlit code lives here.
"""

import itertools
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, Integer, LargeBinary, MetaData, String, Table,
    UniqueConstraint, and_, create_engine, delete, func, insert, select, update,
)

STATUSES = ['Not Checked In', 'Checked In', 'Scratch', 'Conflict', 'NFC', 'In Ring', 'Run Completed']
# Statuses an exhibitor can pick for themselves on the Check-in tab
SELF_SERVE_STATUSES = ['Not Checked In', 'Checked In', 'Scratch', 'Conflict', 'NFC']
NOT_CHECKED_IN = 'Not Checked In'

metadata = MetaData()

runs = Table(
    'runs', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False, index=True),
    Column('day', String, default=''),
    Column('class_name', String, nullable=False),     # "Combined Class Name"
    Column('class_order', Integer, nullable=False),    # order of the class within the day
    Column('position', Float, nullable=False),         # order of the run within the class
    Column('source_run_order', String, default=''),    # Run_Order from the uploaded CSV
    Column('ring', String, default=''),
    Column('event', String, default=''),
    Column('event_num', String, default=''),
    Column('level', String, default=''),
    Column('class_type', String, default=''),          # Regular / Select
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
    'results', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False),
    Column('class_name', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('submitted_at', DateTime(timezone=True)),
    UniqueConstraint('trial_date', 'class_name'),
)

course_maps = Table(
    'course_maps', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False),
    Column('class_name', String, nullable=False),
    Column('filename', String, default=''),
    Column('mime', String, default='image/png'),
    Column('image', LargeBinary, nullable=False),
    Column('uploaded_at', DateTime(timezone=True)),
)

# When each day's course maps become visible to exhibitors (see public_course_map). Stored in UTC.
map_release = Table(
    'map_release', metadata,
    Column('trial_date', Date, primary_key=True),
    Column('release_at', DateTime(timezone=True)),          # None while on hold
    Column('on_hold', Boolean, nullable=False, default=False),
    Column('set_at', DateTime(timezone=True)),
)

# A-frame banners on the Gate: on for Agility classes, off for everything else, unless the
# secretary switches a class (e.g. a Gamblers course with an A-frame). Only switched classes are stored.
aframe_settings = Table(
    'aframe_settings', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('trial_date', Date, nullable=False),
    Column('class_name', String, nullable=False),
    Column('has_aframe', Boolean, nullable=False),
    UniqueConstraint('trial_date', 'class_name'),
)

# Whether exhibitors can check themselves in on the Check-in tab. Closed until the secretary opens it in Admin.
checkin_open = Table(
    'checkin_open', metadata,
    Column('trial_date', Date, primary_key=True),
    Column('is_open', Boolean, nullable=False, default=False),
    Column('changed_at', DateTime(timezone=True)),
)

# Days whose run order, A-frame and results come straight from Trial Manager (it writes this row on every push).
# Admin's upload/reorder/late-entry/A-frame/publish tools are locked for these days unless someone unlocks them;
# while unlocked, Trial Manager stops pushing that day.
managed_days = Table(
    'managed_days', metadata,
    Column('trial_date', Date, primary_key=True),
    Column('source', String, nullable=False, default='Trial Manager'),
    Column('updated_at', DateTime(timezone=True)),
    Column('unlocked', Boolean, nullable=False, default=False),
    Column('unlocked_at', DateTime(timezone=True)),
)

# Fields copied from the CSV / typed in for a late entry
RUN_FIELDS = ['day', 'ring', 'event', 'event_num', 'level', 'class_type', 'height', 'dog_name', 'breed',
              'first_name', 'last_name', 'handler_name', 'handler_number', 'dog_number',
              'run_group', 'team_name']


# Bumped after every successful write. The app's short-lived read cache includes it in its key,
# so a change shows up immediately instead of waiting for the cache to expire.
_version = itertools.count(1)
_current_version = 0
_version_lock = threading.Lock()


def data_version() -> int:
    return _current_version


@contextmanager
def _write(engine):
    """engine.begin(), then mark cached reads as stale once the change is committed."""
    global _current_version
    with engine.begin() as c:
        yield c
    with _version_lock:
        _current_version = next(_version)


def _now():
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = os.environ.get('DATABASE_URL') or 'sqlite:///checkin_local.db'
    # Railway gives postgres:// or postgresql://; SQLAlchemy needs the psycopg driver named
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def get_engine(url: str = None):
    engine = create_engine(url or database_url(), pool_pre_ping=True)
    metadata.create_all(engine)
    return engine


# ── Reading ──────────────────────────────────────────────────────────────────

def trial_dates(engine) -> list:
    with engine.connect() as c:
        return [r[0] for r in c.execute(select(runs.c.trial_date).distinct().order_by(runs.c.trial_date))]


def runs_for_day(engine, trial_date: date, class_name: str = None) -> pd.DataFrame:
    q = select(runs).where(runs.c.trial_date == trial_date)
    if class_name is not None:
        q = q.where(runs.c.class_name == class_name)
    q = q.order_by(runs.c.class_order, runs.c.position, runs.c.id)
    with engine.connect() as c:
        df = pd.DataFrame(c.execute(q).mappings().all(), columns=[col.name for col in runs.columns])
    return df


def class_names(engine, trial_date: date) -> list:
    q = (select(runs.c.class_name, func.min(runs.c.class_order).label('o'))
         .where(runs.c.trial_date == trial_date)
         .group_by(runs.c.class_name).order_by('o'))
    with engine.connect() as c:
        return [r[0] for r in c.execute(q)]


# ── Status changes ───────────────────────────────────────────────────────────

def set_status(engine, run_ids, status: str):
    if status not in STATUSES:
        raise ValueError(f'Unknown status {status!r}')
    ids = [run_ids] if isinstance(run_ids, int) else list(run_ids)
    with _write(engine) as c:
        c.execute(update(runs).where(runs.c.id.in_(ids)).values(status=status, updated_at=_now()))


def start_run(engine, run_id: int):
    """Put a dog In Ring, finishing whoever was In Ring in that class — in one transaction."""
    with _write(engine) as c:
        row = c.execute(select(runs.c.trial_date, runs.c.class_name).where(runs.c.id == run_id)).first()
        if row is None:
            return
        c.execute(update(runs)
                  .where(and_(runs.c.trial_date == row.trial_date, runs.c.class_name == row.class_name,
                              runs.c.status == 'In Ring', runs.c.id != run_id))
                  .values(status='Run Completed', updated_at=_now()))
        c.execute(update(runs).where(runs.c.id == run_id).values(status='In Ring', updated_at=_now()))


def reset_statuses(engine, trial_date: date) -> int:
    """Every run on the day, scratches included, goes back to Not Checked In. Returns rows changed."""
    with _write(engine) as c:
        res = c.execute(update(runs)
                        .where(and_(runs.c.trial_date == trial_date,
                                    runs.c.status != NOT_CHECKED_IN))
                        .values(status=NOT_CHECKED_IN, updated_at=_now()))
        return res.rowcount


# ── Run order upload ─────────────────────────────────────────────────────────

def _status_key(class_name, dog_number):
    return (str(class_name).strip().lower(), str(dog_number).strip())


def preview_import(engine, new_runs: pd.DataFrame) -> dict:
    """What would happen if new_runs replaced the runs for the same dates."""
    dates = sorted(set(new_runs['trial_date']))
    existing = pd.concat([runs_for_day(engine, d) for d in dates]) if dates else pd.DataFrame()
    new_keys = {_status_key(r.class_name, r.dog_number) for r in new_runs.itertuples()}
    kept, lost = 0, []
    if not existing.empty:
        for r in existing.itertuples():
            if r.status == NOT_CHECKED_IN:
                continue
            if _status_key(r.class_name, r.dog_number) in new_keys:
                kept += 1
            else:
                lost.append(f'{r.dog_name} ({r.handler_name}) — {r.class_name}: {r.status}')
    return {
        'dates': dates,
        'replacing': 0 if existing.empty else len(existing),
        'new': len(new_runs),
        'statuses_kept': kept,
        'statuses_lost': lost,
    }


def import_runs(engine, new_runs: pd.DataFrame) -> dict:
    """Replace all runs on the dates in new_runs, carrying statuses over by class + dog."""
    dates = sorted(set(new_runs['trial_date']))
    with _write(engine) as c:
        old = c.execute(select(runs.c.class_name, runs.c.dog_number, runs.c.status)
                        .where(runs.c.trial_date.in_(dates))).all()
        old_status = {_status_key(r.class_name, r.dog_number): r.status for r in old}
        c.execute(delete(runs).where(runs.c.trial_date.in_(dates)))
        rows = []
        for r in new_runs.to_dict('records'):
            r['status'] = old_status.get(_status_key(r['class_name'], r['dog_number']), NOT_CHECKED_IN)
            r['updated_at'] = _now()
            rows.append(r)
        if rows:
            c.execute(insert(runs), rows)
    return {'dates': dates, 'runs': len(new_runs)}


# ── Admin edits ──────────────────────────────────────────────────────────────

class ClassChangedError(Exception):
    """The class was changed by someone else since the page was loaded."""


def save_class_order(engine, trial_date: date, class_name: str, ordered_ids: list):
    """Renumber a class so runs appear in ordered_ids order (1, 2, 3, ...)."""
    with _write(engine) as c:
        current = {r[0] for r in c.execute(select(runs.c.id).where(
            and_(runs.c.trial_date == trial_date, runs.c.class_name == class_name)))}
        if current != set(ordered_ids):
            raise ClassChangedError('This class changed since you opened it (a run was added or removed). '
                                    'Reload and try again.')
        for pos, run_id in enumerate(ordered_ids, start=1):
            c.execute(update(runs).where(runs.c.id == run_id).values(position=float(pos)))


def add_late_entry(engine, trial_date: date, class_name: str, fields: dict, insert_at: int) -> int:
    """Insert a run into a class at 1-based position insert_at (past the end = last). Returns new id."""
    with _write(engine) as c:
        existing = c.execute(select(runs).where(and_(runs.c.trial_date == trial_date,
                                                     runs.c.class_name == class_name))
                             .order_by(runs.c.position, runs.c.id)).mappings().all()
        if existing:
            template = existing[0]
            class_order = template['class_order']
        else:
            template = {}
            class_order = (c.execute(select(func.max(runs.c.class_order))
                                     .where(runs.c.trial_date == trial_date)).scalar() or 0) + 1
        row = {f: str(fields.get(f) or '') for f in RUN_FIELDS}
        # Class-level fields come from the class itself, never from the typed-in dog
        for f in ('day', 'ring', 'event', 'event_num', 'run_group'):
            row[f] = str(template.get(f) or row[f])
        if not row['handler_name']:
            row['handler_name'] = f"{row['first_name']} {row['last_name']}".strip()
        row.update(trial_date=trial_date, class_name=class_name, class_order=class_order,
                   position=0.0, source_run_order='late entry', status=NOT_CHECKED_IN, updated_at=_now())
        new_id = c.execute(insert(runs).values(**row)).inserted_primary_key[0]

        ids = [r['id'] for r in existing]
        ids.insert(max(0, min(insert_at - 1, len(ids))), new_id)
        for pos, run_id in enumerate(ids, start=1):
            c.execute(update(runs).where(runs.c.id == run_id).values(position=float(pos)))
    return new_id


def delete_day(engine, trial_date: date):
    with _write(engine) as c:
        c.execute(delete(runs).where(runs.c.trial_date == trial_date))


# ── Results & course maps ────────────────────────────────────────────────────

def publish_results(engine, trial_date: date, class_name: str, data: list):
    with _write(engine) as c:
        c.execute(delete(results).where(and_(results.c.trial_date == trial_date,
                                             results.c.class_name == class_name)))
        c.execute(insert(results).values(trial_date=trial_date, class_name=class_name,
                                         data=data, submitted_at=_now()))


def results_for_day(engine, trial_date: date) -> dict:
    with engine.connect() as c:
        rows = c.execute(select(results.c.class_name, results.c.data)
                         .where(results.c.trial_date == trial_date)).all()
    return {r.class_name: r.data for r in rows}


def save_course_map(engine, trial_date: date, class_name: str, filename: str, mime: str, image: bytes):
    with _write(engine) as c:
        c.execute(insert(course_maps).values(trial_date=trial_date, class_name=class_name, filename=filename,
                                             mime=mime, image=image, uploaded_at=_now()))


def latest_course_map(engine, trial_date: date, class_name: str):
    """The newest map image (bytes) for a class, or None."""
    with engine.connect() as c:
        return c.execute(select(course_maps.c.image)
                         .where(and_(course_maps.c.trial_date == trial_date,
                                     course_maps.c.class_name == class_name))
                         .order_by(course_maps.c.uploaded_at.desc(), course_maps.c.id.desc())
                         .limit(1)).scalar()


def classes_with_maps(engine, trial_date: date) -> set:
    with engine.connect() as c:
        return {r[0] for r in c.execute(select(course_maps.c.class_name).distinct()
                                        .where(course_maps.c.trial_date == trial_date))}


# ── Exhibitor check-in open / closed ─────────────────────────────────────────

def checkin_is_open(engine, trial_date: date) -> bool:
    """False until the secretary opens check-in for the day (Admin), and again after it's closed."""
    with engine.connect() as c:
        return bool(c.execute(select(checkin_open.c.is_open).where(checkin_open.c.trial_date == trial_date)).scalar())


def set_checkin_open(engine, trial_date: date, is_open: bool):
    with _write(engine) as c:
        c.execute(delete(checkin_open).where(checkin_open.c.trial_date == trial_date))
        c.execute(insert(checkin_open).values(trial_date=trial_date, is_open=is_open, changed_at=_now()))


def exhibitor_set_status(engine, run_ids, status: str) -> bool:
    """An exhibitor's own tap on the Check-in tab. Refused (returns False) while check-in is closed for that day,
    so a page left open from before can't sneak a change in. Gate stewards and Admin use set_status."""
    if status not in SELF_SERVE_STATUSES:
        raise ValueError(f'Exhibitors can\'t set {status!r}')
    ids = [run_ids] if isinstance(run_ids, int) else list(run_ids)
    with engine.connect() as c:
        days = {r[0] for r in c.execute(select(runs.c.trial_date).where(runs.c.id.in_(ids)).distinct())}
    if not days or not all(checkin_is_open(engine, d) for d in days):
        return False
    set_status(engine, ids, status)
    return True


# ── Days managed by Trial Manager ────────────────────────────────────────────

def managed_day(engine, trial_date: date):
    """{'source', 'updated_at', 'unlocked'} if Trial Manager sends this day's run order, else None."""
    with engine.connect() as c:
        row = c.execute(select(managed_days).where(managed_days.c.trial_date == trial_date)).mappings().first()
    if row is None:
        return None
    return {'source': row['source'], 'updated_at': _aware(row['updated_at']), 'unlocked': bool(row['unlocked'])}


def is_locked(engine, trial_date: date) -> bool:
    """True while Trial Manager owns the day, so the admin tools that change the run order are hidden."""
    m = managed_day(engine, trial_date)
    return bool(m and not m['unlocked'])


def set_day_unlocked(engine, trial_date: date, unlocked: bool):
    """Unlock a managed day for hand edits (Trial Manager stops pushing it), or hand it back."""
    with _write(engine) as c:
        c.execute(update(managed_days).where(managed_days.c.trial_date == trial_date)
                  .values(unlocked=unlocked, unlocked_at=_now() if unlocked else None))


# ── A-frame ──────────────────────────────────────────────────────────────────

def default_aframe(class_name: str) -> bool:
    """Agility classes (Agility 1, Agility 2, Masters Agility) have an A-frame; Jumping, Speedstakes and games don't."""
    return 'agility' in (class_name or '').lower()


def aframe_by_class(engine, trial_date: date) -> dict:
    """{class_name: has A-frame} for every class that day, defaults plus the secretary's switches."""
    with engine.connect() as c:
        switched = {r.class_name: r.has_aframe for r in c.execute(
            select(aframe_settings).where(aframe_settings.c.trial_date == trial_date))}
    return {name: switched.get(name, default_aframe(name)) for name in class_names(engine, trial_date)}


def set_aframe(engine, trial_date: date, class_name: str, on: bool):
    with _write(engine) as c:
        c.execute(delete(aframe_settings).where(and_(aframe_settings.c.trial_date == trial_date,
                                                     aframe_settings.c.class_name == class_name)))
        if on != default_aframe(class_name):
            c.execute(insert(aframe_settings).values(trial_date=trial_date, class_name=class_name, has_aframe=on))


# ── Course map release ───────────────────────────────────────────────────────
#
# Maps can be uploaded ahead of time. Exhibitors only ever get a map through public_course_map(), which checks
# the release rule in the database layer itself, so a page can't show a map early by mistake:
#   - the day has a release time and it has passed, or
#   - the day has NO release time and the trial day has started (local date), which covers day-of uploads.
# A map for a future day with no release time set stays hidden, and a day put on hold stays hidden until a
# new release time is saved or Release now is clicked.

def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def map_release_time(engine, trial_date: date):
    """The day's release time (UTC), or None if it hasn't been set or the day is on hold."""
    return map_release_status(engine, trial_date)['release_at']


def map_release_status(engine, trial_date: date) -> dict:
    with engine.connect() as c:
        row = c.execute(select(map_release).where(map_release.c.trial_date == trial_date)).mappings().first()
    return {'release_at': _aware(row['release_at']) if row else None, 'on_hold': bool(row and row['on_hold'])}


def hold_maps(engine, trial_date: date):
    """Hide the day's maps until a new release time is saved or they're released by hand."""
    with _write(engine) as c:
        c.execute(delete(map_release).where(map_release.c.trial_date == trial_date))
        c.execute(insert(map_release).values(trial_date=trial_date, release_at=None, on_hold=True, set_at=_now()))


def set_map_release_time(engine, trial_date: date, release_at):
    """release_at: an aware datetime, or None to clear it."""
    with _write(engine) as c:
        c.execute(delete(map_release).where(map_release.c.trial_date == trial_date))
        if release_at is not None:
            if release_at.tzinfo is None:
                raise ValueError('release_at must include a time zone')
            c.execute(insert(map_release).values(trial_date=trial_date, release_at=release_at.astimezone(timezone.utc),
                                                 on_hold=False, set_at=_now()))


def maps_released(engine, trial_date: date, now, local_today: date) -> bool:
    """now: aware datetime. local_today: today's date in the trial's time zone."""
    status = map_release_status(engine, trial_date)
    if status['on_hold']:
        return False
    if status['release_at'] is None:
        return local_today >= trial_date
    return now >= status['release_at']


def public_course_map(engine, trial_date: date, class_name: str, now, local_today: date):
    """The map exhibitors may see right now, or None. The only way the public pages get a map."""
    if not maps_released(engine, trial_date, now, local_today):
        return None
    return latest_course_map(engine, trial_date, class_name)


def public_map_classes(engine, trial_date: date, now, local_today: date) -> set:
    """Classes with a map exhibitors may see right now (empty before the release). Same rule as public_course_map."""
    if not maps_released(engine, trial_date, now, local_today):
        return set()
    return classes_with_maps(engine, trial_date)
