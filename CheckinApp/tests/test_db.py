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
    df = parse_run_order_csv(sample.read_bytes())
    assert len(df) == 27 and df['class_name'].nunique() == 10


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


def test_reset_keeps_scratches(engine):
    load(engine)
    ids = list(db.runs_for_day(engine, SAT_DATE)['id'])
    db.set_status(engine, ids[0], 'Scratch')
    db.set_status(engine, ids[1:3], 'Run Completed')
    assert db.reset_statuses(engine, SAT_DATE) == 2
    st = db.runs_for_day(engine, SAT_DATE).set_index('id')['status']
    assert st[ids[0]] == 'Scratch' and st[ids[1]] == 'Not Checked In'


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
