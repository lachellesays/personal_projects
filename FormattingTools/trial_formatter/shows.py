"""
shows.py

Load and save per-show settings (show ID, dates, scratches, corrections).

Where they're stored:
  - DATABASE_URL set (Railway): the `formatter_shows` table in the shared Postgres database.
    Shows found in the old YAML folder are copied in automatically the first time.
  - Otherwise (running on your Mac): YAML files in ./shows next to this file.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, create_engine, delete, insert, select

from core import Show

metadata = MetaData()
shows_table = Table(
    'formatter_shows', metadata,
    Column('slug', String, primary_key=True),
    Column('data', JSON, nullable=False),
    Column('updated_at', DateTime(timezone=True)),
)

_engine = None


# ── Where things live ────────────────────────────────────────────────────────

def _on_railway() -> bool:
    return bool(os.environ.get('RAILWAY_ENVIRONMENT_NAME') or os.environ.get('RAILWAY_ENVIRONMENT'))


def database_url():
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if not url:
        return None
    # Railway gives postgres:// or postgresql://; SQLAlchemy needs the psycopg driver named
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def yaml_dir() -> Path:
    """The YAML folder: the old Railway volume if one is attached, else ./shows."""
    base = os.environ.get('DATA_DIR') or os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or Path(__file__).parent
    return Path(base) / 'shows'


def engine():
    global _engine
    if _engine is None and database_url():
        _engine = create_engine(database_url(), pool_pre_ping=True)
        metadata.create_all(_engine)
    return _engine


def storage_problem():
    """On Railway, explain why saved shows would be lost (None if storage is fine)."""
    if not _on_railway() or database_url():
        return None
    if 'DATABASE_URL' in os.environ:
        return ('DATABASE_URL exists but is empty, so shows, scratches and corrections are not being saved '
                'to the database. Edit the variable, type ${{ and pick your Postgres service from the '
                'suggestions, then click Deploy.')
    return ('DATABASE_URL is not set, so shows, scratches and corrections are not being saved to the '
            'database. In Railway, add the variable DATABASE_URL = ${{Postgres.DATABASE_URL}} to this '
            'service and click Deploy.')


# ── One-time move from YAML files into the database ─────────────────────────

def migrate_yaml_to_db() -> int:
    """Copy YAML shows into the database if they aren't there yet. Returns how many were copied.

    Safe to run on every start: shows already in the database are never overwritten.
    """
    eng = engine()
    folder = yaml_dir()
    if eng is None or not folder.is_dir():
        return 0
    with eng.begin() as c:
        existing = {r[0] for r in c.execute(select(shows_table.c.slug))}
        copied = 0
        for path in sorted(folder.glob('*.yaml')):
            if path.stem in existing:
                continue
            show = load_show_file(path)
            c.execute(insert(shows_table).values(slug=path.stem, data=show.to_dict(),
                                                 updated_at=datetime.now(timezone.utc)))
            copied += 1
    return copied


# ── Public API ───────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'show'


def list_shows() -> list:
    """Return (slug, Show) pairs, newest Saturday date first."""
    eng = engine()
    if eng is not None:
        with eng.connect() as c:
            shows = [(r.slug, Show.from_dict(r.data)) for r in c.execute(select(shows_table))]
    else:
        folder = yaml_dir()
        folder.mkdir(parents=True, exist_ok=True)
        shows = [(p.stem, load_show_file(p)) for p in folder.glob('*.yaml')]
    return sorted(shows, key=lambda s: s[1].saturday_date, reverse=True)


def get_show(slug: str) -> Show:
    eng = engine()
    if eng is not None:
        with eng.connect() as c:
            data = c.execute(select(shows_table.c.data).where(shows_table.c.slug == slug)).scalar()
        if data is None:
            raise KeyError(f'No show named {slug!r}')
        return Show.from_dict(data)
    return load_show_file(yaml_dir() / f'{slug}.yaml')


def save_show(show: Show, slug: str = None) -> str:
    """Save the show and return its slug."""
    slug = slug or slugify(f'{show.saturday_date} {show.name}')
    eng = engine()
    if eng is not None:
        with eng.begin() as c:
            c.execute(delete(shows_table).where(shows_table.c.slug == slug))
            c.execute(insert(shows_table).values(slug=slug, data=show.to_dict(),
                                                 updated_at=datetime.now(timezone.utc)))
        return slug
    folder = yaml_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{slug}.yaml'
    tmp = path.with_suffix('.yaml.tmp')
    tmp.write_text(dump_show(show))
    tmp.replace(path)
    return slug


def load_show_file(path) -> Show:
    with open(path) as f:
        return Show.from_dict(yaml.safe_load(f) or {})


def dump_show(show: Show) -> str:
    return yaml.safe_dump(show.to_dict(), sort_keys=False, allow_unicode=True)
