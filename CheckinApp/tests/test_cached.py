import logging
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger('streamlit').setLevel(logging.ERROR)  # quiet "no ScriptRunContext" warnings

import db  # noqa: E402
from run_order import parse_run_order_csv  # noqa: E402
from tests.test_db import SAT, SAT_DATE  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/cache.db')
    import cached
    cached.engine.clear()
    cached._read.clear()
    eng = cached.engine()
    db.import_runs(eng, parse_run_order_csv(SAT.encode()))
    queries = []
    event.listen(eng, 'before_cursor_execute', lambda *a, **k: queries.append(1))
    return cached, eng, queries


def test_many_viewers_share_one_query(cache):
    cached, eng, queries = cache
    for _ in range(300):  # 300 phones refreshing the same class
        df = cached.read('runs_for_day', SAT_DATE, 'Gamblers')
    assert list(df['dog_name']) == ['Theo', 'Mishka', 'Clue']
    assert len(queries) == 1
    cached.read('runs_for_day', SAT_DATE, 'Senior/Champ Agility')  # a different class is its own entry
    assert len(queries) == 2


def test_writes_show_up_immediately(cache):
    cached, eng, queries = cache
    run_id = int(cached.read('runs_for_day', SAT_DATE, 'Gamblers')['id'].iloc[0])
    db.set_status(eng, run_id, 'In Ring')  # e.g. the gate taps START RUN
    df = cached.read('runs_for_day', SAT_DATE, 'Gamblers')
    assert df.set_index('id').loc[run_id, 'status'] == 'In Ring'


def test_cache_expires(cache):
    cached, eng, queries = cache
    cached.read('trial_dates')
    time.sleep(cached.CACHE_SECONDS + 0.5)
    cached.read('trial_dates')
    assert len(queries) == 2


def test_cached_copies_are_independent(cache):
    cached, eng, queries = cache
    a = cached.read('runs_for_day', SAT_DATE, 'Gamblers')
    a.loc[:, 'status'] = 'changed by one viewer'
    b = cached.read('runs_for_day', SAT_DATE, 'Gamblers')
    assert set(b['status']) == {'Not Checked In'}
