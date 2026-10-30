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
