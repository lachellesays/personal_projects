import csv
import importlib.util
import io
import random
from pathlib import Path

import pytest

from tests.conftest import SLUG, submission

SAT, SUN = '2026-12-05', '2026-12-06'
ROOT = Path(__file__).resolve().parents[2]


def trial_subs():
    """A small trial: mixed levels, heights and Regular/Select, and handlers with several dogs."""
    subs, n = [], 0
    dogs = [  # handler, dog, height, level
        ('100', 'Ace', '20 Regular', 'Champion'), ('100', 'Bolt', '20 Regular', 'Champion'), ('100', 'Cody', '20 Regular', 'Senior'),
        ('200', 'Dot', '8 Select', 'Novice'), ('200', 'Elf', '12 Regular', 'Beginner'),
        ('300', 'Fig', '20 Select', 'Champion'), ('400', 'Gus', '16 Regular', 'Senior'), ('500', 'Hap', '24 Regular', 'Champion'),
        ('600', 'Ivy', '20 Regular', 'Senior'), ('700', 'Jet', '20 Regular', 'Champion'), ('800', 'Kit', '12 Select', 'Novice'),
        ('900', 'Lux', '20 Regular', 'Novice'),
    ]
    for handler, dog, height, level in dogs:
        n += 1
        subs.append(submission(str(n), 'H' + handler, 'Smith', handler, f'9{n:03d}', dog, sat=['Agility 1', 'Open Gamblers (all levels combined)'],
                               sun=['Jumping 1'], paid='45.00', level=level, height=height))
    return subs


@pytest.fixture
def built(admin, app_env):
    app_env['data'] = trial_subs()
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    r = c.post(f'/shows/{SLUG}/runorder/build', data={'csrf': token}, follow_redirects=False)
    assert r.status_code == 303
    return c, token


def event(day, cls):
    from app import trial
    return trial.event_view(SLUG, day, cls)


def test_helpers():
    from app import trial
    assert trial.parse_height('20 inch (s)') == (20, 'Select') and trial.parse_height('16 inch') == (16, 'Regular')
    assert trial.level_group('Champ', False) == 'Senior/Champ' and trial.level_group('Novice', False) == 'Beginner/Novice'
    assert trial.level_group('Novice', True) == ''
    assert trial.combined_name('Agility 2', 'Senior/Champ') == 'Senior/Champ Agility 2'
    assert trial.event_parts('Agility 2') == ('Agility', 2) and trial.event_parts('Masters Jumping') == ('Masters Jumping', 1)


def test_build_groups_and_day_defaults(built):
    # Saturday: Senior/Champ first, tall to small, Select before Regular at each height
    ev = event(SAT, 'Agility')
    rows = ev['rows']
    assert len(rows) == 12 and ev['setting']['select_first'] is True
    groups = [r['group'] for r in rows]
    assert groups == sorted(groups, key=lambda g: g != 'Senior/Champ')
    sc = [(r['height'], r['class_type']) for r in rows if r['group'] == 'Senior/Champ']
    assert sc == sorted(sc, key=lambda x: (-x[0], x[1] != 'Select'))
    assert sc[:2] == [(24, 'Regular'), (20, 'Select')]
    # Sunday: Beginner/Novice first, small to tall
    sun = event(SUN, 'Jumping')['rows']
    assert [r['group'] for r in sun] == sorted((r['group'] for r in sun), key=lambda g: g != 'Beginner/Novice')
    bn = [r['height'] for r in sun if r['group'] == 'Beginner/Novice']
    assert bn == sorted(bn) and bn[0] == 8
    # Gamblers is one group for every level
    gam = event(SAT, 'Gamblers')
    assert {r['group'] for r in gam['rows']} == {''}
    assert [r['height'] for r in gam['rows']] == sorted((r['height'] for r in gam['rows']), reverse=True)


def test_build_page_sets_each_day_and_can_change_later(admin, app_env):
    app_env['data'] = trial_subs()
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    page = c.get(f'/shows/{SLUG}/runorder').text
    assert 'name="levels_first_2026-12-05" value="Senior/Champ" checked' in page
    assert 'name="heights_2026-12-06" value="small" checked' in page
    c.post(f'/shows/{SLUG}/runorder/build', data={'csrf': token, f'levels_first_{SAT}': 'Beginner/Novice', f'heights_{SAT}': 'small',
                                                  f'levels_first_{SUN}': 'Beginner/Novice', f'heights_{SUN}': 'small'})
    rows = event(SAT, 'Agility')['rows']
    assert rows[0]['group'] == 'Beginner/Novice' and rows[0]['height'] == 8
    # Changing the day later regroups every class but keeps the order inside each height
    tens = [r['dog_name'] for r in rows if r['group'] == 'Senior/Champ' and r['height'] == 20 and r['class_type'] == 'Regular']
    c.post(f'/shows/{SLUG}/runorder/day', data={'csrf': token, 'day': SAT, 'levels_first': 'Senior/Champ', 'heights': 'tall'})
    rows = event(SAT, 'Agility')['rows']
    assert rows[0]['group'] == 'Senior/Champ' and rows[0]['height'] == 24
    assert [r['dog_name'] for r in rows if r['group'] == 'Senior/Champ' and r['height'] == 20 and r['class_type'] == 'Regular'] == tens


def test_gaps_and_spread():
    from app import trial
    runs = [{'id': i, 'handler_number': h, 'first_name': '', 'last_name': ''} for i, h in enumerate('AABCA')]
    assert trial.gaps(runs) == {1: 0, 4: 2}
    assert trial.gaps(runs, out={1}) == {4: 2}
    # Spreading never moves a dog out of its group, and pushes the handler's dogs apart
    runs = [{'id': i, 'handler_number': 'A' if i < 3 else str(i), 'first_name': '', 'last_name': ''} for i in range(20)]
    keys = {r['id']: 0 for r in runs}
    out = trial.spread(runs, keys, set(), random.Random(1))
    assert sorted(r['id'] for r in out) == list(range(20))
    assert min(trial.gaps(out).values()) >= 5


def test_reorder_move_undo_and_scratch(built):
    c, token = built
    rows = event(SAT, 'Agility')['rows']
    ids = [r['id'] for r in rows]
    new = ids[1:] + ids[:1]
    r = c.post(f'/shows/{SLUG}/runorder/reorder', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'run': new},
               headers={'HX-Request': 'true'})
    assert r.status_code == 200 and 'id="ro-table"' in r.text
    assert [x['id'] for x in event(SAT, 'Agility')['rows']] == new
    c.post(f'/shows/{SLUG}/runorder/move', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'run_id': ids[0], 'position': '1'})
    assert event(SAT, 'Agility')['rows'][0]['id'] == ids[0]
    c.post(f'/shows/{SLUG}/runorder/undo', data={'csrf': token, 'day': SAT, 'cls': 'Agility'})
    assert [x['id'] for x in event(SAT, 'Agility')['rows']] == new
    c.post(f'/shows/{SLUG}/runorder/undo', data={'csrf': token, 'day': SAT, 'cls': 'Agility'})
    assert [x['id'] for x in event(SAT, 'Agility')['rows']] == ids
    # A stale page (different set of runs) is refused
    r = c.post(f'/shows/{SLUG}/runorder/reorder', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'run': ids[1:]},
               headers={'HX-Request': 'true'})
    assert 'changed while you were editing' in r.text
    # Scratching one run keeps its slot but drops it from numbering and gaps
    c.post(f'/shows/{SLUG}/runorder/scratch/{ids[0]}', data={'csrf': token, 'day': SAT, 'cls': 'Agility'})
    first = event(SAT, 'Agility')['rows'][0]
    assert first['out'] and first['seq'] is None


def test_csv_matches_the_check_in_app(built):
    c, token = built
    from app import db, trial
    show = db.get_show(SLUG); show.scratched_dogs = ['9012']; db.save_show(show, SLUG)   # Lux scratched for the show
    r = c.get(f'/shows/{SLUG}/runorder.csv')
    assert r.headers['content-type'].startswith('text/csv')
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0].keys()) == trial.CSV_COLUMNS
    assert 'Lux' not in [x['Name'] for x in rows]
    names = {x['Combined Class Name'] for x in rows}
    assert {'Senior/Champ Agility', 'Beginner/Novice Agility', 'Gamblers', 'Senior/Champ Jumping'} <= names
    assert rows[0]['Date'] == '12/05/2026' and rows[0]['Day'] == 'Saturday'

    spec = importlib.util.spec_from_file_location('ro', ROOT / 'CheckinApp' / 'run_order.py')
    if not spec or not (ROOT / 'CheckinApp' / 'run_order.py').exists():
        pytest.skip('check-in app not present')
    ro = importlib.util.module_from_spec(spec); spec.loader.exec_module(ro)
    df = ro.parse_run_order_csv(r.content)
    assert len(df) == len(rows)
    sc = df[(df['class_name'] == 'Senior/Champ Agility')].sort_values('position')
    expected = [x['dog_name'] for x in trial.ordered_for_export(SLUG, SAT) if x['combined'] == 'Senior/Champ Agility']
    assert sc['dog_name'].tolist() == expected


def test_day_of_entries_and_payments(built):
    c, token = built
    from app import trial
    page = c.get(f'/shows/{SLUG}/dayof')
    assert 'Add classes for a dog already entered' in page.text
    # Existing dog: Elf (12 Regular Beginner) into Sunday Gamblers... not offered Sunday, so Saturday Agility is a duplicate
    r = c.post(f'/shows/{SLUG}/dayof/add', data={'csrf': token, 'mode': 'existing', 'dog_key': '9005', 'classes': [f'{SAT}|Agility']})
    assert 'already entered' in r.text
    # New handler and dog
    r = c.post(f'/shows/{SLUG}/dayof/add', data={
        'csrf': token, 'mode': 'new', 'first_name': 'Zed', 'last_name': 'Late', 'handler_number': '1234', 'dog_number': '5678',
        'dog_name': 'Nova', 'breed': 'Mix', 'height': '20', 'class_type': 'Regular', 'level': 'Champ', 'speedstakes_level': '',
        'classes': [f'{SAT}|Agility', f'{SUN}|Jumping']}, follow_redirects=True)
    assert 'Added Nova to 2 classes' in r.text
    rows = event(SAT, 'Agility')['rows']
    nova = next(x for x in rows if x['dog_name'] == 'Nova')
    assert nova['source'] == 'day_of' and nova['fee'] == 20.0
    sc_20r = [x['dog_name'] for x in rows if x['group'] == 'Senior/Champ' and x['height'] == 20 and x['class_type'] == 'Regular']
    assert sc_20r[-1] == 'Nova'                                           # end of its height group, not the end of the class
    assert rows[-1]['dog_name'] != 'Nova'
    s = trial.day_of_summary(SLUG)
    assert s['fees'] == 40.0
    c.post(f'/shows/{SLUG}/dayof/payment', data={'csrf': token, 'handler_number': '1234', 'amount': '40', 'method': 'Cash'})
    assert trial.day_of_summary(SLUG)['handlers'][0]['balance'] == 0
    assert 'Nova' in c.get(f'/shows/{SLUG}/runorder.csv?day={SUN}').text


def test_new_jotform_entries_join_their_group(built, app_env):
    c, token = built
    app_env['data'] = app_env['data'] + [submission('99', 'New', 'Person', '950', '9950', 'Pip', sat=['Agility 1'],
                                                    paid='15.00', level='Novice', height='8 Select')]
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    page = c.get(f'/shows/{SLUG}/runorder')
    assert '1 new run' in page.text
    c.post(f'/shows/{SLUG}/runorder/build', data={'csrf': token})
    bn = [x for x in event(SAT, 'Agility')['rows'] if x['group'] == 'Beginner/Novice']
    eights = [x['dog_name'] for x in bn if x['height'] == 8]
    assert eights[-1] == 'Pip' and bn[-1]['dog_name'] == 'Pip' and bn[0]['height'] == 20
    assert '1 new run' not in c.get(f'/shows/{SLUG}/runorder').text


def test_pages_and_scribe_sheets(built):
    c, token = built
    page = c.get(f'/shows/{SLUG}/runorder?day={SAT}&cls=Agility')
    assert page.status_code == 200 and 'Senior/Champ' in page.text and 'Run order CSV' in page.text
    pdf = c.get(f'/shows/{SLUG}/scribe.pdf?day={SAT}&cls=Agility')
    assert pdf.content[:4] == b'%PDF' and pdf.headers['content-type'] == 'application/pdf'
    # Class settings: Regular first flips the order inside each height
    c.post(f'/shows/{SLUG}/runorder/class', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'sort': '0', 'ring': '2'})
    ev = event(SAT, 'Agility')
    assert ev['setting']['ring'] == '2' and ev['setting']['select_first'] is False
    sc20 = [x['class_type'] for x in ev['rows'] if x['group'] == 'Senior/Champ' and x['height'] == 20]
    assert sc20 == sorted(sc20, key=lambda t: t != 'Regular')


def test_scribe_sheets_are_collated_for_cutting():
    from app import trial
    runs = [{'event_index': 1, 'class_name': 'Agility', 'seq': i} for i in range(1, 20)]   # 19 dogs -> 5 pages
    runs += [{'event_index': 2, 'class_name': 'Jumping', 'seq': i} for i in range(1, 4)]   # 3 dogs -> 1 page
    pages = trial.collated_pages(runs)
    assert len(pages) == 6
    assert [p[0]['seq'] for p in pages[:5]] == [1, 2, 3, 4, 5]           # top-left pile = runs 1-5
    assert [p[1]['seq'] for p in pages[:5]] == [6, 7, 8, 9, 10]          # top-right pile = 6-10
    assert [p[3]['seq'] if p[3] else None for p in pages[:5]] == [16, 17, 18, 19, None]
    assert [r['seq'] if r else None for r in pages[5]] == [1, 2, 3, None]  # a new class starts a new page
