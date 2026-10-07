"""
shows.py

Load and save per-show config files (YAML). Files live in DATA_DIR, which is a
Railway volume (/data) in production and ./shows next to this file locally.
"""

import os
import re
from pathlib import Path

import yaml

from core import Show

DATA_DIR = Path(os.environ.get('DATA_DIR') or Path(__file__).parent)
SHOWS_DIR = DATA_DIR / 'shows'


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'show'


def list_shows() -> list:
    """Return (slug, Show) pairs, newest Saturday date first."""
    SHOWS_DIR.mkdir(parents=True, exist_ok=True)
    shows = [(p.stem, load_show(p)) for p in SHOWS_DIR.glob('*.yaml')]
    return sorted(shows, key=lambda s: s[1].saturday_date, reverse=True)


def show_path(slug: str) -> Path:
    return SHOWS_DIR / f'{slug}.yaml'


def load_show(path) -> Show:
    with open(path) as f:
        return Show.from_dict(yaml.safe_load(f) or {})


def dump_show(show: Show) -> str:
    return yaml.safe_dump(show.to_dict(), sort_keys=False, allow_unicode=True)


def save_show(show: Show, slug: str = None) -> str:
    """Write the show to disk and return its slug."""
    SHOWS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slug or slugify(f'{show.saturday_date} {show.name}')
    path = show_path(slug)
    tmp = path.with_suffix('.yaml.tmp')
    tmp.write_text(dump_show(show))
    tmp.replace(path)
    return slug
