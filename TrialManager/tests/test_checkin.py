"""The live link to the check-in app: run order, A-frame and results pushed into its tables, gate statuses read back."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, update

from tests.conftest import SLUG
from tests.test_trial import SAT, SUN, built, trial_subs  # noqa: F401 - fixtures

CHECKIN_DB = Path(__file__).resolve().parents[2] / 'CheckinApp' / 'db.py'


def checkin_module():
    """The real CheckinApp/db.py, when it sits next to this repo (it does on the secretary's computer)."""
    if not CHECKIN_DB.exists():
        return None
    spec = importlib.util.spec_from_file_location('checkin_app_db', CHECKIN_DB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """An empty check-in database, made the way the check-in app makes it."""
    from app import checkin
    url = f'sqlite:///{tmp_path}/checkin.db'
    eng = create_engine(url)
    mod = checkin_module()
    (mod.metadata if mod else checkin.cmeta).create_all(eng)
    monkeypatch.setenv('CHECKIN_DATABASE_URL', url)
    checkin.reset_engine()
    yield eng
    checkin.reset_engine()


@pytest.fixture
def live(built, gate):
    """A built show with the link set up (the build happened before the URL was set, so send it once)."""
    c, token = built
    r = c.post(f'/shows/{SLUG}/checkin/send', data={'csrf': token, 'day': SAT}, follow_redirects=True)
    assert 'Sent to the check-in app' in r.text
    return c, token, gate


def gate_runs(eng, day=SAT, cls=None):
    from app import checkin
    q = select(checkin.runs).where(checkin.runs.c.trial_date == date.fromisoformat(day))
    if cls:
        q = q.where(checkin.runs.c.class_name == cls)
    with eng.connect() as c:
        return [dict(r) for r in c.execute(q.order_by(checkin.runs.c.class_order, checkin.runs.c.position)).mappings()]


def test_table_copies_match_the_check_in_app():
    from app import checkin
    mod = checkin_module()
    if mod is None:
        pytest.skip('CheckinApp is not next to this repo')
    for name in ('runs', 'results', 'aframe_settings', 'managed_days'):
        mine, theirs = checkin.cmeta.tables[name], mod.metadata.tables[name]
        assert [(c.name, type(c.type).__name__) for c in mine.columns] == \
               [(c.name, type(c.type).__name__) for c in theirs.columns], name


def test_syncs_by_itself_from_the_first_build(admin, app_env, gate):
    """No switch to flip: building the run order puts the preliminary order on the check-in app."""
    app_env['data'] = trial_subs()
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    assert gate_runs(gate) == []
    c.post(f'/shows/{SLUG}/runorder/build', data={'csrf': token})
    assert gate_runs(gate) and gate_runs(gate, day=SUN)
    page = c.get(f'/shows/{SLUG}/runorder').text
    assert 'Synced with the check-in app' in page and 'Live on check-in' not in page


def test_not_configured_keeps_the_csv_way(built, monkeypatch):
    c, token = built
    monkeypatch.delenv('CHECKIN_DATABASE_URL', raising=False)
    from app import checkin
    checkin.reset_engine()
    page = c.get(f'/shows/{SLUG}/runorder').text
    assert 'not connected' in page and 'Run order CSV' in page
    assert checkin.sync_show(SLUG) == {}


def test_going_live_sends_the_same_run_order_as_the_csv(live):
    import csv
    import io
    from app import checkin, trial
    c, token, eng = live
    csv_rows = list(csv.DictReader(io.StringIO(trial.run_order_csv(SLUG, SAT).decode())))
    runs = gate_runs(eng)
    assert [(r['class_name'], r['dog_number']) for r in runs] == \
           [(x['Combined Class Name'], x['UKI_Dog_Number']) for x in csv_rows]
    assert all(r['status'] == 'Not Checked In' for r in runs)
    with eng.connect() as conn:
        md = conn.execute(select(checkin.managed_days)).mappings().all()
    assert {m['trial_date'] for m in md} == {date(2026, 12, 5), date(2026, 12, 6)} and not any(m['unlocked'] for m in md)
    assert 'up to date' in c.get(f'/shows/{SLUG}/runorder').text


def test_changes_keep_gate_statuses_and_run_ids(live):
    from app import checkin, trial
    c, token, eng = live
    cls = 'Senior/Champ Agility'
    before = gate_runs(eng, cls=cls)
    ids = {r['dog_number']: r['id'] for r in before}
    # A volunteer checks the first dog in at the gate
    first = before[0]
    with eng.begin() as conn:
        conn.execute(update(checkin.runs).where(checkin.runs.c.id == first['id']).values(status='Checked In'))
    # The secretary reverses the Senior/Champ order in Trial Manager
    ev = trial.event_view(SLUG, SAT, 'Agility')
    sc = [r['id'] for r in ev['rows'] if r['group'] == 'Senior/Champ']
    rest = [r['id'] for r in ev['rows'] if r['group'] != 'Senior/Champ']
    c.post(f'/shows/{SLUG}/runorder/reorder', data={'csrf': token, 'day': SAT, 'cls': 'Agility',
                                                     'run': [str(x) for x in rest + sc[::-1]]})
    after = gate_runs(eng, cls=cls)
    assert [r['dog_number'] for r in after] == [r['dog_number'] for r in before][::-1]
    assert {r['dog_number']: r['id'] for r in after} == ids            # same rows, just moved
    assert next(r for r in after if r['id'] == first['id'])['status'] == 'Checked In'


def test_scratches_and_day_of_entries_reach_the_gate(live):
    from app import checkin
    c, token, eng = live
    # Scratch a whole entry in Trial Manager: stays on the gate list as Scratch
    c.post(f'/shows/{SLUG}/scratch/9001', data={'csrf': token})
    ace = [r for r in gate_runs(eng) if r['dog_number'] == '9001']
    assert ace and all(r['status'] == 'Scratch' for r in ace)
    # Un-scratch puts them back
    c.post(f'/shows/{SLUG}/scratch/9001', data={'csrf': token})
    assert all(r['status'] == 'Not Checked In' for r in gate_runs(eng) if r['dog_number'] == '9001')
    # A gate scratch is the gate's business: another push doesn't undo it
    with eng.begin() as conn:
        conn.execute(update(checkin.runs).where(checkin.runs.c.dog_number == '9002').values(status='Scratch'))
    checkin.sync_show(SLUG, force=True)
    assert all(r['status'] == 'Scratch' for r in gate_runs(eng) if r['dog_number'] == '9002')
    # A day-of entry goes straight to the gate
    n = len(gate_runs(eng, day=SUN))
    r = c.post(f'/shows/{SLUG}/dayof/add', data={
        'csrf': token, 'mode': 'new', 'first_name': 'Zed', 'last_name': 'Late', 'handler_number': '1234', 'dog_number': '5678',
        'dog_name': 'Nova', 'breed': 'Mix', 'height': '20', 'class_type': 'Regular', 'level': 'Champ', 'speedstakes_level': '',
        'classes': [f'{SAT}|Agility']}, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert any(x['dog_number'] == '5678' and x['class_name'] == 'Senior/Champ Agility' for x in gate_runs(eng))
    assert len(gate_runs(eng, day=SUN)) == n


def test_aframe_setting_goes_to_the_gate(live):
    from app import checkin
    c, token, eng = live
    # Gamblers is off by default in check-in; turn it on in Trial Manager's Show setup
    from app import trial
    names = [x['class_name'] for x in trial.class_list(SLUG, SAT)]
    form = {'csrf': token, 'day': SAT, 'levels_first': 'Senior/Champ', 'heights': 'tall', 'class': names}
    form.update({f'ring::{n}': '1' for n in names})
    form.update({f'combine::{n}': 'on' for n in names if n == 'Gamblers'})
    form.update({f'aframe::{n}': 'on' for n in names if n in ('Agility', 'Gamblers')})
    r = c.post(f'/shows/{SLUG}/setup/day', data=form, follow_redirects=False)
    assert r.status_code < 400, r.text
    assert trial.has_aframe(trial.get_class(SLUG, SAT, 'Gamblers'))
    with eng.connect() as conn:
        rows = {r['class_name']: r['has_aframe'] for r in conn.execute(select(checkin.aframe_settings)).mappings()}
    assert rows.get('Gamblers') is True


def test_results_publish_on_calculate_and_gate_fills_scores(live):
    from app import checkin, results
    c, token, eng = live
    c.post(f'/shows/{SLUG}/scoring/course', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'yards_bn': '160',
                                                   'yards_champ': '177', 'rate': 'fast', 'sct_extra': '0'})
    by = {r['dog_name']: r for r in results.entry_view(SLUG, SAT, 'Agility')['rows']}
    # At the gate: Bolt scratched, Cody NFC, Ace scratched but the scribe already has a time for him
    with eng.begin() as conn:
        for dog, status in (('Bolt', 'Scratch'), ('Cody', 'NFC'), ('Ace', 'Scratch')):
            conn.execute(update(checkin.runs).where(checkin.runs.c.dog_number == by[dog]['dog_number'],
                                                    checkin.runs.c.class_name == 'Senior/Champ Agility').values(status=status))
    c.post(f'/shows/{SLUG}/scoring/score/{by["Ace"]["id"]}', data={'csrf': token, 'day': SAT, 'cls': 'Agility',
                                                                    'time': '30.10', 'course_faults': '0', 'refusals': '0'})
    page = c.get(f'/shows/{SLUG}/scoring?day={SAT}&cls=Agility').text
    assert 'SCRATCHED AT GATE' in page and 'Fill from gate: 1 ABS, 1 NFC' in page
    r = c.post(f'/shows/{SLUG}/scoring/gate-fill', data={'csrf': token, 'day': SAT, 'cls': 'Agility'}, follow_redirects=True)
    assert 'Filled from the gate: 1 ABS (scratched), 1 NFC.' in r.text
    scores = {x['dog_name']: (x['score'] or {}) for x in results.class_runs(SLUG, SAT, 'Agility')}
    assert scores['Bolt']['status'] == 'ABS' and scores['Cody']['status'] == 'NFC'
    assert scores['Ace']['status'] == '' and scores['Ace']['time'] == 30.10        # a typed score is never replaced
    # Calculate publishes each check-in class's results, identical to the JSON people used to paste
    r = c.post(f'/shows/{SLUG}/scoring/calculate', data={'csrf': token, 'day': SAT, 'cls': 'Agility'}, follow_redirects=True)
    assert 'Published to the check-in app' in r.text
    with eng.connect() as conn:
        pub = {x['class_name']: x['data'] for x in conn.execute(select(checkin.results)).mappings()}
    assert set(pub) == {'Senior/Champ Agility', 'Beginner/Novice Agility'}
    assert pub['Senior/Champ Agility'] == results.results_json(SLUG, SAT, 'Agility', 'Senior/Champ Agility')


def test_an_unlocked_day_in_check_in_is_left_alone(live):
    from app import checkin
    c, token, eng = live
    with eng.begin() as conn:
        conn.execute(update(checkin.managed_days).where(checkin.managed_days.c.trial_date == date(2026, 12, 5))
                     .values(unlocked=True))
        conn.execute(insert(checkin.runs).values(trial_date=date(2026, 12, 5), class_name='Senior/Champ Agility',
                                                 class_order=1, position=99.0, dog_name='Hand typed', status='Checked In'))
    out = checkin.sync_show(SLUG, force=True)
    assert not out[SAT]['sent'] and 'unlocked' in out[SAT]['reason'] and out[SUN]['sent']
    assert any(r['dog_name'] == 'Hand typed' for r in gate_runs(eng))
    page = c.get(f'/shows/{SLUG}/runorder').text
    assert 'not updated' in page and 'unlocked' in page


def test_works_before_check_in_has_made_its_tables(built, tmp_path, monkeypatch):
    """Streamlit only creates its tables when someone first opens the check-in app; going live first still works."""
    from app import checkin
    monkeypatch.setenv('CHECKIN_DATABASE_URL', f'sqlite:///{tmp_path}/fresh.db')
    checkin.reset_engine()
    c, token = built
    r = c.post(f'/shows/{SLUG}/checkin/send', data={'csrf': token, 'day': SAT}, follow_redirects=True)
    assert 'Sent to the check-in app' in r.text
    assert gate_runs(create_engine(f'sqlite:///{tmp_path}/fresh.db'))
    checkin.reset_engine()


def test_errors_are_one_short_line():
    from sqlalchemy.exc import OperationalError, ProgrammingError
    from app import checkin
    assert 'CHECKIN_DATABASE_URL' in checkin.friendly(OperationalError('SELECT 1', {}, Exception('could not connect')))
    msg = checkin.friendly(ProgrammingError('SELECT runs.id FROM runs WHERE ...', {}, Exception('relation "x" does not exist\nLINE 2')))
    assert 'SELECT' not in msg and 'relation "x" does not exist' in msg and '\n' not in msg


def test_run_order_page_says_where_it_sends_and_what_is_there(live):
    c, token, eng = live
    page = c.get(f'/shows/{SLUG}/runorder').text
    assert 'Connected to:' in page and 'checkin.db' in page
    assert 'In the check-in app now:' in page and 'runs' in page and 'nothing yet' not in page
