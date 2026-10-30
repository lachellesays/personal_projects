import json
from pathlib import Path

import pytest

from app import scoring as sc

FIXTURES = Path(__file__).parent / 'fixtures'
JUNE_RESULTS = Path.home() / 'Documents' / 'Trial Archive' / 'data' / 'june2026_ukimgr' / 'june2026_results.xlsx'
ODD_DOG = 32799   # UKI Manager gave this dog +3 seconds in most (not all) classes — a hand edit, left out of the check


def test_sct_formula_from_the_rule_book():
    course = {'yards_bn': 160, 'yards_champ': 177}
    assert sc.sct('Agility', 'Beginner', 20, 'Regular', course) == 55          # 160 / 2.9
    assert sc.sct('Agility', 'Beginner', 12, 'Regular', course) == 66          # 160 / 2.9 + 20%
    assert sc.sct('Agility', 'Novice', 12, 'Regular', course) == 63            # + 15%
    assert sc.sct('Agility', 'Champ', 20, 'Regular', course) == 56             # 177 / 3.15
    assert sc.sct('Agility', 'Champ', 16, 'Select', course) == 59              # 20" Regular + 3
    assert sc.sct('Agility', 'Champ', 8, 'Select', course) == 65               # 12" Regular (61.8 -> 62) + 3
    assert sc.sct('Jumping', 'Champ', 20, 'Regular', {'yards_bn': 148, 'yards_champ': 155}) == 44   # 155 / 3.5
    assert sc.sct('Agility', 'Champ', 20, 'Regular', {**course, 'rate': 'slow'}) == 61               # 177 / 2.9
    assert sc.sct('Agility', 'Champ', 20, 'Regular', {**course, 'sct_extra': 2}) == 58
    # Masters and all-levels classes: Champ yardage and rates for everyone
    assert sc.sct('Masters Agility', 'Novice', 20, 'Regular', {'yards_champ': 189}) == 60
    assert sc.sct('Speedstakes', 'Beginner', 12, 'Regular', {'yards_champ': 176}) == 55
    assert sc.sct('Agility', 'Champ', 20, 'Regular', {}) is None
    assert sc.sct('Gamblers', 'Novice', 20, 'Regular', {'opening_time': 25, 'closing_time': 14}) == 39


def test_faults_and_q():
    r = sc.score_run('Agility', {'time': 41.5}, 40, {}, yards=150)
    assert r['time_faults'] == 1.5 and r['total_faults'] == 1.5 and not r['q'] and r['yps'] == 3.61
    r = sc.score_run('Agility', {'time': 40}, 40, {})
    assert r['q'] and r['total_faults'] == 0
    assert not sc.score_run('Agility', {'time': 30, 'course_faults': 5}, 40, {})['q']
    assert not sc.score_run('Agility', {'time': 30, 'refusals': 5}, 40, {})['q']
    assert not sc.score_run('Agility', {'status': 'E', 'time': 30}, 40, {})['q']


def test_games_from_the_rule_book_examples():
    g30 = {'opening_time': 30, 'closing_time': 15}
    assert sc.gamblers_minimum(30) == 18 and sc.gamblers_minimum(35) == 21
    assert sc.score_run('Gamblers', {'time': 44, 'opening_points': 18, 'gamble_points': 10}, 45, g30)['q']
    assert not sc.score_run('Gamblers', {'time': 44, 'opening_points': 28, 'gamble_points': 0}, 45, g30)['q']
    # 16 points in a 35-second opening (needs 21) plus the 15-point gamble qualifies
    assert sc.score_run('Gamblers', {'time': 44, 'opening_points': 16, 'gamble_points': 15}, 50, {'opening_time': 35})['q']
    assert not sc.score_run('Gamblers', {'time': 44, 'opening_points': 15, 'gamble_points': 15}, 50, {'opening_time': 35})['q']
    assert sc.score_run('Snooker', {'time': 40, 'points': 37}, 48, {})['q']
    assert not sc.score_run('Snooker', {'time': 40, 'points': 36}, 48, {})['q']
    assert not sc.score_run('Snooker', {'time': 0, 'points': 45}, 48, {})['q']   # didn't stop the clock


def test_places_and_points():
    assert [sc.points_for(p, 8) for p in (1, 2, 3, 4, 5)] == [6, 5, 4, 4, 4]
    assert [sc.points_for(p, 12) for p in (1, 2, 3, 4, 5)] == [12, 8, 7, 5, 4]
    assert [sc.points_for(p, 31) for p in (1, 5, 10, 11)] == [12, 8, 4, 4]
    course = {'yards_bn': 160, 'yards_champ': 177}
    def run(i, level, h, t, q, time):
        return {'id': i, 'level': level, 'height': h, 'class_type': t,
                'result': {'q': q, 'time': time, 'total_faults': 0 if q else 5, 'points': None}}
    runs = [run(1, 'Champ', 20, 'Regular', True, 30), run(2, 'Champ', 20, 'Regular', True, 28),
            run(3, 'Champ', 20, 'Regular', False, 25), run(4, 'Senior', 20, 'Regular', True, 35),
            run(5, 'Champ', 20, 'Select', True, 40)]
    out = {r['id']: (r['place'], r['level_points']) for r in sc.place_class('Agility', runs, course)}
    assert out == {1: (2, 5), 2: (1, 6), 3: (0, 0), 4: (1, 6), 5: (1, 6)}
    # An all-levels class places by height across levels
    out = {r['id']: r['place'] for r in sc.place_class('Speedstakes', runs, {'yards_champ': 176})}
    assert out[4] == 3 and out[2] == 1


@pytest.mark.skipif(not JUNE_RESULTS.exists(), reason='June 2026 results not on this computer')
def test_matches_uki_manager_on_june_2026():
    """Recompute every standard-class SCT, Q, place and level point from what the scribes recorded."""
    import pandas as pd
    course = {}
    for c in json.loads((FIXTURES / 'june2026_course_details.json').read_text())['classes']:
        name = c['event'].replace('-1', '').replace('-2', ' 2')
        course[(c['day'], name)] = c
    df = pd.read_excel(JUNE_RESULTS, sheet_name='Raw Results')
    df['Class'] = df['Class'].str.strip()
    df['Height'] = df['Height'].astype(str).str.strip()
    df['day'] = df['Date'].astype(str).map(lambda d: 'Saturday' if '6/6' in d else 'Sunday')
    std = df[~df['Class'].isin(['Gamblers', 'Snooker'])]
    checked = 0
    by_class = {}
    for r in std.itertuples():
        c = course[(r.day, r.Class)]
        height = int(r.Height.split()[0])
        ctype = 'Select' if '(s)' in r.Height else 'Regular'
        if r._8 != ODD_DOG:   # Dog ID
            assert sc.sct(r.Class, r.Level, height, ctype, c) == r.SCT, (r.day, r.Class, r.Level, r.Height)
            checked += 1
        f = str(r.Faults)
        res = sc.score_run(r.Class, {'status': f if f in sc.STATUSES else '', 'time': r.Time,
                                     'course_faults': 0 if f in sc.STATUSES else float(f)}, int(r.SCT), c)
        by_class.setdefault((r.day, r.Class), []).append(
            {'id': r.Index, 'level': r.Level, 'height': height, 'class_type': ctype, 'result': res,
             'want': (int(r.Place), int(r._16))})   # Level Points
    for (day, cls), runs in by_class.items():
        for r in sc.place_class(cls, runs, course[(day, cls)]):
            assert (r['place'], r['level_points']) == r['want'], (day, cls, r['level'], r['height'], r['class_type'])
    assert checked > 270


def test_new_columns_are_added_to_an_existing_database(tmp_path, monkeypatch):
    """A database made by an earlier release gets the scoring columns added on startup."""
    import sqlite3
    from app import db, trial
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.execute('create table tm_classes (id integer primary key, show_slug varchar not null, day varchar not null, '
                'class_name varchar not null, sort integer not null, ring varchar, select_first boolean not null, '
                'combine_levels boolean not null, generated_at datetime)')
    con.execute("insert into tm_classes values (1, 's', '2026-12-05', 'Agility', 0, '1', 1, 0, null)")
    con.commit(); con.close()
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    db.reset_engine()
    try:
        rows = trial.class_list('s')
        assert rows[0]['class_name'] == 'Agility' and rows[0]['yards_bn'] is None and rows[0]['calculated_at'] is None
    finally:
        db.reset_engine()


def test_gamblers_opening_digits_are_added_up():
    assert sc.opening_points_from('11531151') == ('11531151', 18)
    assert sc.opening_points_from('1-1-2-5-1-3-0-5-2') == ('112513052', 20)   # the scribe sheet's example
    assert sc.opening_points_from(' 1 5 3 ') == ('153', 9)
    assert sc.opening_points_from('') == ('', None)
    with pytest.raises(ValueError):
        sc.opening_points_from('1a5')


def _round(status='', time=0.0, faults=0.0, place=0):
    return {'scored': True, 'status': status, 'time': time, 'total_faults': faults, 'place': place}


def test_masters_series_rules_from_the_premium():
    assert sc.masters_round_score(_round('E')) == 160 and sc.masters_round_score(_round('ABS')) == 1060
    assert sc.masters_round_score(_round(time=49.97, faults=0.97)) == 50.94
    assert sc.masters_prizes(3) == (12.0, 0.0) and sc.masters_prizes(11) == (30.8, 13.2) and sc.masters_prizes(20) == (56.0, 24.0)
    dogs = [  # June 2026, 20" Regular
        {'id': 'a', 'height': 20, 'class_type': 'Regular', 'jumping': _round(time=32.33, place=1), 'agility': _round('E')},
        {'id': 'b', 'height': 20, 'class_type': 'Regular', 'jumping': _round('E'), 'agility': _round('E')},
        {'id': 'c', 'height': 20, 'class_type': 'Regular', 'jumping': _round('E'), 'agility': _round('E')},
        {'id': 'd', 'height': 20, 'class_type': 'Regular', 'jumping': _round('E'), 'agility': _round('ABS')},
        {'id': 'e', 'height': 20, 'class_type': 'Regular', 'jumping': _round(time=49.97, faults=0.97), 'agility': _round(time=60.48, faults=0.48)},
        {'id': 'f', 'height': 20, 'class_type': 'Regular', 'jumping': _round('E'), 'agility': _round('ABS')},
        {'id': 's', 'height': 20, 'class_type': 'Select', 'jumping': _round(time=33.13, place=1), 'agility': _round('E')},
    ]
    out = {d['id']: d for d in sc.masters_series(dogs)}
    assert (out['e']['place'], out['e']['prize'], out['e']['bye']) == (1, 24.0, True)      # 6 entered -> $24
    assert (out['a']['place'], out['a']['prize'], out['a']['bye']) == (2, 0.0, True)
    assert out['b']['place'] == 0 and out['b']['note'] == 'Eliminated in both rounds' and not out['b']['bye']
    assert out['d']['place'] == 0 and 'forfeits' in out['d']['note']
    assert (out['s']['place'], out['s']['prize'], out['s']['bye']) == (1, 4.0, True)      # Select is its own height
    assert out['e']['overall'] == 111.90


def test_masters_ties_go_to_agility_then_runoff():
    dogs = [{'id': 1, 'height': 12, 'class_type': 'Regular', 'agility': _round(time=40), 'jumping': _round(time=30)},
            {'id': 2, 'height': 12, 'class_type': 'Regular', 'agility': _round(time=35), 'jumping': _round(time=35)},
            {'id': 3, 'height': 12, 'class_type': 'Regular', 'agility': _round(time=35), 'jumping': _round(time=35)}]
    out = {d['id']: d for d in sc.masters_series(dogs)}
    assert out[1]['place'] == 3                       # same 70 total, but the slower Agility
    assert out[2]['runoff'] and out[3]['runoff'] and not out[1]['runoff']


@pytest.mark.skipif(not JUNE_RESULTS.exists(), reason='June 2026 results not on this computer')
def test_masters_matches_uki_manager_on_june_2026():
    """Rebuild June's Masters Series from the raw scores; places, prizes and Final qualifiers must match UKI's sheets."""
    import pandas as pd
    raw = pd.read_excel(JUNE_RESULTS, sheet_name='Raw Results')
    raw['Class'] = raw['Class'].str.strip()
    raw['Height'] = raw['Height'].astype(str).str.strip()
    dogs = {}
    for cls, key in (('Masters Agility', 'agility'), ('Masters Jumping', 'jumping')):
        rows = raw[raw['Class'] == cls]
        runs = []
        for r in rows.itertuples():
            f = str(r.Faults)
            res = sc.score_run(cls, {'status': f if f in sc.STATUSES else '', 'time': r.Time,
                                     'course_faults': 0 if f in sc.STATUSES else float(f)}, int(r.SCT), {})
            res['scored'] = True
            runs.append({'id': int(r._8), 'level': r.Level, 'height': int(r.Height.split()[0]),
                         'class_type': 'Select' if '(s)' in r.Height else 'Regular', 'result': res})
        absent_other = set(raw[(raw['Class'] != cls) & raw['Class'].isin(['Masters Agility', 'Masters Jumping'])
                               & raw['Faults'].astype(str).isin(['ABS', 'NFC'])]['Dog ID'].astype(int))
        for r in runs:
            if r['id'] in absent_other:
                r['result']['q'] = False
        for r in sc.place_class(cls, runs, {'yards_champ': 1}):
            d = dogs.setdefault(r['id'], {'id': r['id'], 'height': r['height'], 'class_type': r['class_type']})
            d[key] = {**r['result'], 'place': r['place']}
    out = {d['id']: d for d in sc.masters_series(list(dogs.values()))}
    sheet = pd.read_excel(JUNE_RESULTS, sheet_name='Masters Scoring', header=None, skiprows=8).dropna(subset=[3])
    for row in sheet.itertuples(index=False):
        dog = int(row[3])
        assert (out[dog]['place'], out[dog]['prize']) == (int(row[16] or 0), float(row[17] or 0)), dog
    byes = set(pd.read_excel(JUNE_RESULTS, sheet_name='Masters Byes')['Dog #'].dropna().astype(int))
    assert {d for d, v in out.items() if v['bye']} == byes
