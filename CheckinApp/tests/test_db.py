import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
from run_order import RunOrderError, parse_run_order_csv  # noqa: E402

HEADER = ('"Run_Order","Date","Day","Event","Event_Num","Level","Class_Type","Intl_Jump_Ht","Name","Breed",'
          '"First_Name","Last_Name","Handler_Name","UKI_Number","UKI_Dog_Number","Run Group","Team Name",'
          '"Combined Class Name","Ring"\n')


def row(ro, cls, dog, dog_no, handler_no, height='16', ctype='Regular', d='11/07/2026', day='Saturday'):
    return (f'"{ro}","{d}","{day}","Agility","1","Novice","{ctype}","{height}","{dog}","Mix","Ann","Lee",'
            f'"Ann Lee","{handler_no}","{dog_no}"," ","","{cls}","1"\n')


SAT = (HEADER
       + row(41000010, 'Gamblers', 'Theo', 1, 100, '8')
       + row(41000020, 'Gamblers', 'Mishka', 2, 200, '12')
       + row(41000030, 'Gamblers', 'Clue', 3, 300, '16')
       + row(41100010, 'Senior/Champ Agility', 'Theo', 1, 100, '8')
       + row(41100020, 'Senior/Champ Agility', 'Clue', 3, 300, '16'))
SAT_DATE = date(2026, 11, 7)
SUN_DATE = date(2026, 11, 8)


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')  # set to a Postgres URL to run against Postgres
    eng = db.get_engine(url or f'sqlite:///{tmp_path}/test.db')
    if url:
        with eng.begin() as c:
            for t in reversed(db.metadata.sorted_tables):
                c.execute(t.delete())
    return eng


def load(engine, csv=SAT):
    db.import_runs(engine, parse_run_order_csv(csv.encode()))


def order(engine, cls, d=SAT_DATE):
    return list(db.runs_for_day(engine, d, cls)['dog_name'])


def test_parse_csv_orders_classes_and_runs():
    df = parse_run_order_csv(SAT.encode())
    assert list(df['class_name'].unique()) == ['Gamblers', 'Senior/Champ Agility']
    assert list(df.loc[df['class_name'] == 'Gamblers', 'position']) == [1.0, 2.0, 3.0]
    assert set(df['trial_date']) == {SAT_DATE}
    assert df['run_group'].iloc[0] == ''


def test_parse_real_export():
    sample = Path('/Users/victoriahenderson/Downloads/grid_run_order_export.csv')
    if not sample.exists():
        pytest.skip('sample export not available')
    df = parse_run_order_csv(sample.read_bytes())   # whatever export is in Downloads: it just has to parse
    assert len(df) > 0 and df['class_name'].nunique() > 0 and df['position'].notna().all()


def test_parse_csv_errors():
    with pytest.raises(RunOrderError, match='missing'):
        parse_run_order_csv(b'"Name","Date"\n"x","11/07/2026"\n')
    with pytest.raises(RunOrderError, match='date'):
        parse_run_order_csv((HEADER + row(1, 'A', 'x', 1, 1, d='Nov 7')).encode())


def test_import_and_class_order(engine):
    load(engine)
    assert db.trial_dates(engine) == [SAT_DATE]
    assert db.class_names(engine, SAT_DATE) == ['Gamblers', 'Senior/Champ Agility']
    assert order(engine, 'Gamblers') == ['Theo', 'Mishka', 'Clue']
    assert set(db.runs_for_day(engine, SAT_DATE)['status']) == {'Not Checked In'}


def test_reupload_keeps_statuses_and_other_days(engine):
    load(engine)
    sun = SAT.replace('11/07/2026', '11/08/2026').replace('Saturday', 'Sunday')
    load(engine, sun)
    runs = db.runs_for_day(engine, SAT_DATE)
    theo_gam = int(runs[(runs.dog_name == 'Theo') & (runs.class_name == 'Gamblers')]['id'].iloc[0])
    mishka = int(runs[runs.dog_name == 'Mishka']['id'].iloc[0])
    db.set_status(engine, theo_gam, 'Checked In')
    db.set_status(engine, mishka, 'Scratch')

    # New Saturday file: Mishka dropped, a new dog added, Clue moved first
    new_sat = (HEADER + row(41000010, 'Gamblers', 'Clue', 3, 300) + row(41000020, 'Gamblers', 'Theo', 1, 100, '8')
               + row(41000030, 'Gamblers', 'Rex', 9, 900))
    pv = db.preview_import(engine, parse_run_order_csv(new_sat.encode()))
    assert pv['statuses_kept'] == 1 and len(pv['statuses_lost']) == 1 and 'Mishka' in pv['statuses_lost'][0]

    load(engine, new_sat)
    sat = db.runs_for_day(engine, SAT_DATE)
    assert order(engine, 'Gamblers') == ['Clue', 'Theo', 'Rex']
    assert sat.set_index('dog_name').loc['Theo', 'status'] == 'Checked In'
    assert sat.set_index('dog_name').loc['Rex', 'status'] == 'Not Checked In'
    assert len(db.runs_for_day(engine, SUN_DATE)) == 5  # Sunday untouched


def test_start_run_finishes_previous_dog(engine):
    load(engine)
    runs = db.runs_for_day(engine, SAT_DATE, 'Gamblers')
    a, b = int(runs['id'].iloc[0]), int(runs['id'].iloc[1])
    other_class = int(db.runs_for_day(engine, SAT_DATE, 'Senior/Champ Agility')['id'].iloc[0])
    db.start_run(engine, other_class)
    db.start_run(engine, a)
    db.start_run(engine, b)
    st = db.runs_for_day(engine, SAT_DATE).set_index('id')['status']
    assert st[a] == 'Run Completed' and st[b] == 'In Ring'
    assert st[other_class] == 'In Ring'  # a different class isn't touched


def test_save_class_order(engine):
    load(engine)
    ids = list(db.runs_for_day(engine, SAT_DATE, 'Gamblers')['id'])
    db.save_class_order(engine, SAT_DATE, 'Gamblers', [ids[2], ids[0], ids[1]])
    assert order(engine, 'Gamblers') == ['Clue', 'Theo', 'Mishka']
    with pytest.raises(db.ClassChangedError):
        db.save_class_order(engine, SAT_DATE, 'Gamblers', ids[:2])


def test_late_entry_inserted_at_position(engine):
    load(engine)
    db.add_late_entry(engine, SAT_DATE, 'Gamblers',
                      {'dog_name': 'Zip', 'dog_number': '77', 'handler_number': '700',
                       'first_name': 'Zoe', 'last_name': 'Ray', 'height': '12'}, insert_at=2)
    assert order(engine, 'Gamblers') == ['Theo', 'Zip', 'Mishka', 'Clue']
    zip_row = db.runs_for_day(engine, SAT_DATE).set_index('dog_name').loc['Zip']
    assert zip_row['handler_name'] == 'Zoe Ray' and zip_row['event'] == 'Agility' and zip_row['day'] == 'Saturday'
    db.add_late_entry(engine, SAT_DATE, 'Gamblers', {'dog_name': 'Last', 'handler_number': '1'}, insert_at=99)
    assert order(engine, 'Gamblers')[-1] == 'Last'
    # Class order across the day is unchanged
    assert db.class_names(engine, SAT_DATE) == ['Gamblers', 'Senior/Champ Agility']


def test_reset_clears_everything_including_scratches(engine):
    load(engine)
    ids = list(db.runs_for_day(engine, SAT_DATE)['id'])
    db.set_status(engine, ids[0], 'Scratch')
    db.set_status(engine, ids[1:3], 'Run Completed')
    assert db.reset_statuses(engine, SAT_DATE) == 3
    assert set(db.runs_for_day(engine, SAT_DATE)['status']) == {'Not Checked In'}


def test_results_and_maps_are_per_day(engine):
    db.publish_results(engine, SAT_DATE, 'Gamblers', [{'place': '1'}])
    db.publish_results(engine, SAT_DATE, 'Gamblers', [{'place': '2'}])  # republish replaces
    db.publish_results(engine, SUN_DATE, 'Gamblers', [{'place': '3'}])
    assert db.results_for_day(engine, SAT_DATE) == {'Gamblers': [{'place': '2'}]}
    db.save_course_map(engine, SAT_DATE, 'Gamblers', 'a.png', 'image/png', b'old')
    db.save_course_map(engine, SAT_DATE, 'Gamblers', 'b.png', 'image/png', b'new')
    assert db.latest_course_map(engine, SAT_DATE, 'Gamblers') == b'new'
    assert db.latest_course_map(engine, SUN_DATE, 'Gamblers') is None


def test_database_url_normalization(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@host:5432/railway')
    assert db.database_url() == 'postgresql+psycopg://u:p@host:5432/railway'
    monkeypatch.setenv('DATABASE_URL', 'postgres://u:p@host/db')
    assert db.database_url().startswith('postgresql+psycopg://')


def test_aframe_defaults_and_switches(tmp_path):
    from datetime import date
    import pandas as pd
    import db
    eng = db.get_engine(f'sqlite:///{tmp_path}/a.db')
    day = date(2026, 11, 7)
    rows = []
    for i, cls in enumerate(['Senior/Champ Agility', 'Beginner/Novice Agility 2', 'Masters Agility',
                             'Senior/Champ Jumping', 'Speedstakes', 'Gamblers']):
        rows.append({'trial_date': day, 'class_name': cls, 'class_order': i + 1, 'position': 1.0,
                     'dog_name': f'D{i}', 'handler_number': str(i), 'dog_number': str(100 + i)})
    db.import_runs(eng, pd.DataFrame(rows))
    got = db.aframe_by_class(eng, day)
    assert got == {'Senior/Champ Agility': True, 'Beginner/Novice Agility 2': True, 'Masters Agility': True,
                   'Senior/Champ Jumping': False, 'Speedstakes': False, 'Gamblers': False}
    db.set_aframe(eng, day, 'Gamblers', True)          # this Gamblers course has an A-frame
    db.set_aframe(eng, day, 'Masters Agility', False)
    got = db.aframe_by_class(eng, day)
    assert got['Gamblers'] is True and got['Masters Agility'] is False and got['Senior/Champ Agility'] is True
    db.set_aframe(eng, day, 'Gamblers', False)         # back to the default: nothing stored
    with eng.connect() as c:
        assert [r.class_name for r in c.execute(db.aframe_settings.select())] == ['Masters Agility']


def test_course_maps_stay_hidden_until_release(tmp_path):
    from datetime import date, datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    import db
    eng = db.get_engine(f'sqlite:///{tmp_path}/m.db')
    et = ZoneInfo('America/New_York')
    sat, sun = date(2026, 11, 7), date(2026, 11, 8)
    db.save_course_map(eng, sat, 'Gamblers', 'g.png', 'image/png', b'SAT-MAP')
    db.save_course_map(eng, sun, 'Agility', 'a.png', 'image/png', b'SUN-MAP')
    wed = datetime(2026, 11, 4, 12, 0, tzinfo=et)

    # Pre-loaded on Wednesday, no release time set: hidden (the trial day hasn't started)
    assert db.public_course_map(eng, sat, 'Gamblers', wed, wed.date()) is None
    # Release time 7:00 AM Saturday
    db.set_map_release_time(eng, sat, datetime(2026, 11, 7, 7, 0, tzinfo=et))
    six59 = datetime(2026, 11, 7, 6, 59, 59, tzinfo=et)
    assert db.public_course_map(eng, sat, 'Gamblers', six59, sat) is None
    assert db.public_course_map(eng, sat, 'Gamblers', datetime(2026, 11, 7, 7, 0, tzinfo=et), sat) == b'SAT-MAP'
    # Saturday's release doesn't release Sunday; Sunday with no time set opens at midnight Sunday
    sat_noon = datetime(2026, 11, 7, 12, 0, tzinfo=et)
    assert db.public_course_map(eng, sun, 'Agility', sat_noon, sat) is None
    assert db.public_course_map(eng, sun, 'Agility', datetime(2026, 11, 8, 0, 1, tzinfo=et), sun) == b'SUN-MAP'
    # Release time stored and compared in UTC, whatever the server's clock zone
    assert db.map_release_time(eng, sat) == datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    # Moving the release later hides it again
    db.set_map_release_time(eng, sat, datetime(2026, 11, 7, 23, 59, tzinfo=et))
    assert db.public_course_map(eng, sat, 'Gamblers', sat_noon, sat) is None
    # Release now
    db.set_map_release_time(eng, sat, sat_noon - timedelta(seconds=1))
    assert db.public_course_map(eng, sat, 'Gamblers', sat_noon, sat) == b'SAT-MAP'
    # Hide again: on hold, hidden even on the day and long after any time, until a new time or Release now
    db.hold_maps(eng, sat)
    assert db.map_release_status(eng, sat) == {'release_at': None, 'on_hold': True}
    assert db.public_course_map(eng, sat, 'Gamblers', datetime(2026, 11, 7, 23, 0, tzinfo=et), sat) is None
    db.set_map_release_time(eng, sat, sat_noon)
    assert db.public_course_map(eng, sat, 'Gamblers', sat_noon, sat) == b'SAT-MAP'
    # The Course maps tab's class list follows the same rule, so class names don't show before the release
    assert db.public_map_classes(eng, sat, sat_noon, sat) == {'Gamblers'}
    assert db.public_map_classes(eng, sat, sat_noon - timedelta(minutes=1), sat) == set()
    assert db.public_map_classes(eng, sun, sat_noon, sat) == set()
    import pytest
    with pytest.raises(ValueError):
        db.set_map_release_time(eng, sat, datetime(2026, 11, 7, 7, 0))   # a time without a time zone is refused


def test_public_pages_only_get_maps_through_the_release_check():
    """Guard: outside the PIN-protected Admin section, app.py must never read a map directly."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'app.py').read_text()
    admin_start = src.index('def render_admin')
    public = src[:admin_start]
    assert "'latest_course_map'" not in public and 'latest_course_map(' not in public
    assert "read('public_course_map'" in public
    assert "'classes_with_maps'" not in public and 'classes_with_maps(' not in public
    admin = src[admin_start:]
    assert admin.count("'latest_course_map'") == 1 and 'Show me this class' in admin


def test_conflict_gaps_skip_scratched_runs():
    from run_order import conflict_gaps
    rows = [{'handler': h, 'out': o} for h, o in [('A', False), ('B', False), ('B', True), ('A', False), ('C', False)]]
    got = conflict_gaps(rows)
    assert [r['gap'] for r in got] == [None, None, None, 1, None]       # one dog (B) ran between A's runs
    assert got[3]['conflict'] and got[0]['conflict_partner'] and not got[2]['conflict']


def test_managed_days_lock_and_unlock(engine):
    assert db.managed_day(engine, SAT_DATE) is None and not db.is_locked(engine, SAT_DATE)
    with engine.begin() as c:   # what Trial Manager writes on each push
        c.execute(db.insert(db.managed_days).values(trial_date=SAT_DATE, source='Trial Manager', updated_at=db._now()))
    m = db.managed_day(engine, SAT_DATE)
    assert m['source'] == 'Trial Manager' and m['updated_at'].tzinfo is not None and db.is_locked(engine, SAT_DATE)
    db.set_day_unlocked(engine, SAT_DATE, True)
    assert not db.is_locked(engine, SAT_DATE) and db.managed_day(engine, SAT_DATE)['unlocked']
    db.set_day_unlocked(engine, SAT_DATE, False)
    assert db.is_locked(engine, SAT_DATE)


def test_admin_locks_trial_manager_days():
    src = (Path(__file__).resolve().parent.parent / 'app.py').read_text()
    assert 'MANAGED_SECTIONS = {"Upload run order", "Reorder", "Late entry", "A-frame", "Publish results"}' in src
    assert 'db.is_locked(ENGINE, d)' in src   # an uploaded CSV can't replace a managed day
