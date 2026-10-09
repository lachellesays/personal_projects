"""
db.py

All database tables and the small helpers that read and write them.

`formatter_shows` is the same table the Streamlit formatter uses, so both apps see the same
shows, scratches and corrections while you run them side by side. Everything new lives in
tables starting with `tm_`.

DATABASE_URL picks the database (Railway Postgres); without it a local SQLite file is used.
"""

import os
import re
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete, desc,
    func, insert, select, update,
)

from app.core import Show

metadata = MetaData()

# Shared with FormattingTools/trial_formatter/shows.py
shows = Table(
    'formatter_shows', metadata,
    Column('slug', String, primary_key=True),
    Column('data', JSON, nullable=False),
    Column('updated_at', DateTime(timezone=True)),
)

users = Table(
    'tm_users', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('email', String, nullable=False, unique=True),
    Column('name', String, nullable=False),
    Column('password_hash', String, nullable=False),
    Column('is_active', Boolean, nullable=False, default=True),
    Column('session_version', Integer, nullable=False, default=1),   # bumping it signs the user out everywhere
    Column('failed_logins', Integer, nullable=False, default=0),
    Column('locked_until', DateTime(timezone=True)),
    Column('created_at', DateTime(timezone=True)),
    Column('created_by', Integer),
)

invites = Table(
    'tm_invites', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('token_hash', String, nullable=False, unique=True),       # the link's token is never stored in plain text
    Column('email', String, nullable=False),
    Column('name', String, nullable=False),
    Column('created_by', Integer, nullable=False),
    Column('created_at', DateTime(timezone=True)),
    Column('expires_at', DateTime(timezone=True), nullable=False),
    Column('used_at', DateTime(timezone=True)),
    Column('cancelled_at', DateTime(timezone=True)),
)

login_events = Table(
    'tm_login_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('at', DateTime(timezone=True), nullable=False),
    Column('email', String, nullable=False),
    Column('user_id', Integer),
    Column('success', Boolean, nullable=False),
    Column('ip', String, default=''),
    Column('device', String, default=''),
)

audit = Table(
    'tm_audit', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('at', DateTime(timezone=True), nullable=False),
    Column('user_id', Integer),
    Column('user_name', String, default=''),
    Column('show_slug', String, default=''),
    Column('action', String, nullable=False),      # scratch, unscratch, fix, unfix, warning, download, show, people
    Column('target', String, default=''),          # e.g. "d:41102" or "h:20981:state"
    Column('detail', JSON),
)

downloads = Table(
    'tm_downloads', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('show_slug', String, nullable=False, index=True),
    Column('at', DateTime(timezone=True), nullable=False),
    Column('user_name', String, default=''),
    Column('handlers', Integer), Column('runs', Integer),
    Column('submission_ids', JSON),                # which JotForm submissions this workbook included
)

submissions_cache = Table(
    'tm_submissions', metadata,
    Column('form_id', String, primary_key=True),
    Column('fetched_at', DateTime(timezone=True), nullable=False),
    Column('data', JSON, nullable=False),
)

warning_states = Table(
    'tm_warning_states', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('show_slug', String, nullable=False, index=True),
    Column('key', String, nullable=False),
    Column('status', String, nullable=False),      # dismissed
    Column('note', Text, default=''),
    Column('user_name', String, default=''),
    Column('at', DateTime(timezone=True)),
)

_engine = None


def now():
    return datetime.now(timezone.utc)


def aware(dt):
    """SQLite hands back naive datetimes; treat them as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def database_url() -> str:
    url = (os.environ.get('DATABASE_URL') or '').strip() or 'sqlite:///trial_manager_local.db'
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True)
        metadata.create_all(_engine)
    return _engine


def reset_engine():
    """Used by tests to point at a fresh database."""
    global _engine
    _engine = None


# ── Shows ────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'show'


def list_shows() -> list:
    with engine().connect() as c:
        rows = [(r.slug, Show.from_dict(r.data)) for r in c.execute(select(shows))]
    return sorted(rows, key=lambda s: s[1].saturday_date, reverse=True)


def get_show(slug: str):
    with engine().connect() as c:
        data = c.execute(select(shows.c.data).where(shows.c.slug == slug)).scalar()
    return Show.from_dict(data) if data is not None else None


def save_show(show: Show, slug: str = None) -> str:
    slug = slug or slugify(f'{show.saturday_date} {show.name}')
    with engine().begin() as c:
        c.execute(delete(shows).where(shows.c.slug == slug))
        c.execute(insert(shows).values(slug=slug, data=show.to_dict(), updated_at=now()))
    return slug


# ── Audit log ────────────────────────────────────────────────────────────────

def log(user: dict, action: str, show_slug: str = '', target: str = '', **detail):
    with engine().begin() as c:
        c.execute(insert(audit).values(at=now(), user_id=user.get('id'), user_name=user.get('name', ''),
                                       show_slug=show_slug, action=action, target=target, detail=detail))


def last_actions(show_slug: str, action: str) -> dict:
    """{target: (user_name, at)} for the most recent `action` on each target in a show."""
    with engine().connect() as c:
        rows = c.execute(select(audit.c.target, audit.c.user_name, audit.c.at)
                         .where(audit.c.show_slug == show_slug, audit.c.action == action)
                         .order_by(audit.c.id)).all()
    return {r.target: (r.user_name, aware(r.at)) for r in rows}


# ── JotForm snapshot ─────────────────────────────────────────────────────────

def get_snapshot(form_id: str):
    with engine().connect() as c:
        row = c.execute(select(submissions_cache).where(submissions_cache.c.form_id == form_id)).first()
    return (row.data, aware(row.fetched_at)) if row else (None, None)


def save_snapshot(form_id: str, data: list):
    with engine().begin() as c:
        c.execute(delete(submissions_cache).where(submissions_cache.c.form_id == form_id))
        c.execute(insert(submissions_cache).values(form_id=form_id, data=data, fetched_at=now()))


# ── Downloads ────────────────────────────────────────────────────────────────

def record_download(show_slug: str, user: dict, handlers: int, runs: int, submission_ids: list):
    with engine().begin() as c:
        c.execute(insert(downloads).values(show_slug=show_slug, at=now(), user_name=user.get('name', ''),
                                           handlers=handlers, runs=runs, submission_ids=submission_ids))


def download_history(show_slug: str, limit: int = 20) -> list:
    with engine().connect() as c:
        rows = c.execute(select(downloads).where(downloads.c.show_slug == show_slug)
                         .order_by(desc(downloads.c.id)).limit(limit)).mappings().all()
    return [{**r, 'at': aware(r['at'])} for r in rows]


# ── Warning decisions ────────────────────────────────────────────────────────

def warning_decisions(show_slug: str) -> dict:
    with engine().connect() as c:
        rows = c.execute(select(warning_states).where(warning_states.c.show_slug == show_slug)).mappings().all()
    return {r['key']: {**r, 'at': aware(r['at'])} for r in rows}


def set_warning(show_slug: str, key: str, status: str = None, note: str = '', user_name: str = ''):
    """status=None reopens the warning."""
    with engine().begin() as c:
        c.execute(delete(warning_states).where(warning_states.c.show_slug == show_slug, warning_states.c.key == key))
        if status:
            c.execute(insert(warning_states).values(show_slug=show_slug, key=key, status=status, note=note,
                                                    user_name=user_name, at=now()))


# ── Users (see auth.py for passwords, sessions and invites) ─────────────────

def user_count() -> int:
    with engine().connect() as c:
        return c.execute(select(func.count()).select_from(users)).scalar()


def get_user(user_id=None, email=None):
    q = select(users)
    q = q.where(users.c.id == user_id) if user_id is not None else q.where(func.lower(users.c.email) == (email or '').lower())
    with engine().connect() as c:
        row = c.execute(q).mappings().first()
    return {**row, 'locked_until': aware(row['locked_until'])} if row else None


def update_user(user_id: int, **values):
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(**values))
