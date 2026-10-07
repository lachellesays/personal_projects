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

# Railway sets RAILWAY_VOLUME_MOUNT_PATH when a volume is attached, so DATA_DIR is optional there.
VOLUME_PATH = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
DATA_DIR = Path(os.environ.get('DATA_DIR') or VOLUME_PATH or Path(__file__).parent)
SHOWS_DIR = DATA_DIR / 'shows'


def storage_problem():
    """On Railway, explain why saved shows would be lost on redeploy (None if storage is fine)."""
    if not os.environ.get('RAILWAY_ENVIRONMENT_NAME') and not os.environ.get('RAILWAY_ENVIRONMENT'):
        return None  # running locally
    if not VOLUME_PATH:
        return ('No Railway volume is attached to this service, so saved shows, scratches and '
                'corrections will be erased on the next deploy. Attach a volume to this service '
                '(mount path /data).')
    if Path(VOLUME_PATH).resolve() not in (DATA_DIR.resolve(), *DATA_DIR.resolve().parents):
        return (f'DATA_DIR is "{DATA_DIR}" but the volume is mounted at "{VOLUME_PATH}", so saves '
                f'are not going to the volume. Delete the DATA_DIR variable or set it to {VOLUME_PATH}.')
    return None


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
