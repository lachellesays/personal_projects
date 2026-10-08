import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shows  # noqa: E402
from core import Show  # noqa: E402


def make_show(name='June 2026', sat='2026-06-06', **kw):
    return Show(name=name, show_id=6892, form_id='261266312429152', saturday_date=sat,
                sunday_date='2026-06-07', **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated storage: YAML folder in tmp, no database unless a test sets DATABASE_URL."""
    for k in ('DATABASE_URL', 'RAILWAY_ENVIRONMENT_NAME', 'RAILWAY_ENVIRONMENT', 'RAILWAY_VOLUME_MOUNT_PATH'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'volume'))
    monkeypatch.setattr(shows, '_engine', None)
    return tmp_path


def use_db(env, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{env}/test.db')
    monkeypatch.setattr(shows, '_engine', None)


FULL = dict(scratched_handlers=['22247'], scratched_dogs=['26608'], exclude_addresses=['2260 MILL ROAD'],
            handler_corrections={'12349': {'state': 'VA', 'last_name': 'Smith'}},
            dog_corrections={'37742': {'dog_breed': 'Border Collie'}})


@pytest.mark.parametrize('backend', ['yaml', 'db'])
def test_save_list_get_round_trip(env, monkeypatch, backend):
    if backend == 'db':
        use_db(env, monkeypatch)
    slug = shows.save_show(make_show(**FULL))
    shows.save_show(make_show('Nov 2026', '2026-11-07'))
    assert [s for s, _ in shows.list_shows()] == ['2026-11-07-nov-2026', slug]
    back = shows.get_show(slug)
    assert back.scratched_handlers == ['22247'] and back.scratched_dogs == ['26608']
    assert back.handler_corrections == FULL['handler_corrections']
    assert back.dog_corrections == FULL['dog_corrections']
    # Saving again under the same slug replaces it
    back.scratched_handlers = []
    shows.save_show(back, slug)
    assert shows.get_show(slug).scratched_handlers == []
    assert len(shows.list_shows()) == 2
    assert (env / 'volume' / 'shows' / f'{slug}.yaml').exists() == (backend == 'yaml')


def test_migration_copies_yaml_once_and_never_overwrites(env, monkeypatch):
    slug = shows.save_show(make_show(**FULL))          # written as YAML (no database yet)
    use_db(env, monkeypatch)
    assert shows.migrate_yaml_to_db() == 1
    assert shows.get_show(slug).handler_corrections == FULL['handler_corrections']

    # Change it in the database, then run migration again: the database copy must win
    s = shows.get_show(slug)
    s.scratched_handlers = ['999']
    shows.save_show(s, slug)
    assert shows.migrate_yaml_to_db() == 0
    assert shows.get_show(slug).scratched_handlers == ['999']


def test_storage_warning(env, monkeypatch):
    assert shows.storage_problem() is None                       # local: fine
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_NAME', 'production')
    assert 'not set' in shows.storage_problem()
    monkeypatch.setenv('DATABASE_URL', '')
    assert 'empty' in shows.storage_problem()
    use_db(env, monkeypatch)
    assert shows.storage_problem() is None


def test_database_url_normalization(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@host:5432/railway')
    assert shows.database_url() == 'postgresql+psycopg://u:p@host:5432/railway'
