"""
scoring.py

UKI scoring rules (11th Edition rule book), checked against UKI Manager's June 2026 results.

Standard classes (Agility, Jumping, Speedstakes, Masters):
- SCT = yards ÷ rate of travel, rounded to the nearest second (Appendix A-2). "Lower SCT" uses the fast end
  of the rule book's range, "Higher SCT" the slow end. Dogs jumping 16" and under add 20% (Beginner, Nursery),
  15% (Novice) or 10% (Senior, Champ). Select runs get the SCT of the next height up plus 3 seconds.
- Masters classes, and any class given only a Senior/Champ yardage (an all-levels class), use the Champ
  yardage and rates for every dog.
- Faults = course faults + refusals (both in 5s) + time faults (each second over SCT, to 1/100th).
- Q ("clear round") = no course faults and a time at or under SCT.

Games:
- Gamblers: Q when the opening points reach the minimum (opening time × 6, drop the zero) and a gamble is
  completed, or when the opening is 5 points or less short and the 15/20-point gamble is completed.
- Snooker: Q with 37 points or more.

Places and level points (section 2.0):
- Only Q runs are placed: standard classes by time, games by points then time.
- Placed within level + height + Regular/Select. An all-levels standard class places by height only.
- Points depend on how many were entered in that group: 1–10 entered 6/5/4/4, 11–30 12/8/7/5,
  31+ 12/11/10/9/8/7/6/5/4/4. Every other Q gets 4 (clear round).
"""

import math

STATUSES = ('E', 'ABS', 'NFC')

RATES = {   # (fast end, slow end) yards per second
    'agility': {'bn': (2.9, 2.5), 'sc': (3.15, 2.9)},
    'jumping': {'bn': (3.25, 2.75), 'sc': (3.5, 3.25)},
}
SMALL_DOG_EXTRA = {'Nursery': 0.20, 'Beginner': 0.20, 'Novice': 0.15, 'Senior': 0.10, 'Champ': 0.10}
SELECT_SECONDS = 3
CLEAR_ROUND_POINTS = 4
SNOOKER_Q = 37


def class_kind(class_name: str) -> str:
    n = class_name.lower()
    if n.startswith('gamblers'):
        return 'gamblers'
    if n.startswith('snooker'):
        return 'snooker'
    return 'agility' if 'agility' in n else 'jumping'


def is_game(class_name: str) -> bool:
    return class_kind(class_name) in ('gamblers', 'snooker')


def is_open_class(class_name: str, course: dict) -> bool:
    """All levels judged as Champ: Masters, or a standard class with no Beginner/Novice yardage."""
    if is_game(class_name):
        return False
    return class_name.lower().startswith('masters') or not course.get('yards_bn')


def _round(x: float) -> int:
    return int(math.floor(x + 0.5))


def _level(level: str) -> str:
    return 'Champ' if (level or '').startswith('Champ') else (level or 'Champ')


def sct(class_name: str, level: str, height: int, class_type: str, course: dict):
    """Standard course time in whole seconds, or None when the course details aren't filled in yet."""
    kind = class_kind(class_name)
    select = class_type == 'Select'
    extra = int(course.get('sct_extra') or 0)
    if kind == 'gamblers':
        if not (course.get('opening_time') and course.get('closing_time')):
            return None
        return int(course['opening_time']) + int(course['closing_time']) + extra
    if kind == 'snooker':
        return int(course['course_time']) + extra if course.get('course_time') else None

    level = _level(level)
    if is_open_class(class_name, course):
        level = 'Champ'
    bn = level in ('Beginner', 'Novice', 'Nursery')
    if bn:
        yards = course.get('yards_bn')
    elif level == 'Senior':
        yards = course.get('yards_senior') or course.get('yards_champ')
    else:
        yards = course.get('yards_champ')
    if not yards:
        return None
    fast, slow = RATES[kind]['bn' if bn else 'sc']
    rate = slow if course.get('rate') == 'slow' else fast
    regular_height = (height or 0) + 4 if select else (height or 0)
    seconds = float(yards) / rate
    if regular_height <= 16:
        seconds *= 1 + SMALL_DOG_EXTRA.get(level, 0.10)
    return _round(seconds) + (SELECT_SECONDS if select else 0) + extra


def opening_points_from(typed) -> tuple:
    """Gamblers opening as the scribe writes it, one digit per obstacle: '11531151' -> 18 (1+1+5+3+1+1+5+1).
    Dashes, spaces, plus signs and commas are ignored, so '1-1-2-5-1-3-0-5-2' works too.
    Returns (digits as typed, total), or ('', None) when nothing was entered."""
    text = str(typed or '').strip()
    if not text:
        return '', None
    if any(ch not in '0123456789-+, ' for ch in text):
        raise ValueError('Opening points: type one digit per obstacle, like 11531151.')
    digits = ''.join(ch for ch in text if ch.isdigit())
    return digits, sum(int(ch) for ch in digits)


def gamblers_minimum(opening_time) -> int:
    """Opening time × 6 with the zero dropped: 30 seconds → 18 points."""
    return int(opening_time or 0) * 6 // 10


def score_run(class_name: str, s: dict, sct_: int, course: dict, yards=None) -> dict:
    """One run's computed result. `s` holds what the scribe sheet gave: status (''/E/ABS/NFC), time,
    course_faults, refusals, and for games opening_points / gamble_points / points."""
    status = (s.get('status') or '').upper()
    time = float(s.get('time') or 0)
    out = {'status': status, 'sct': sct_, 'time': time, 'time_faults': 0.0, 'total_faults': None,
           'yps': None, 'points': None, 'q': False}
    if status in STATUSES:
        return out
    kind = class_kind(class_name)
    if kind == 'gamblers':
        opening, gamble = int(s.get('opening_points') or 0), int(s.get('gamble_points') or 0)
        out['points'] = opening + gamble
        need = gamblers_minimum(course.get('opening_time'))
        out['q'] = time > 0 and ((opening >= need and gamble >= 10) or (opening >= need - 5 and gamble >= 15))
        return out
    if kind == 'snooker':
        out['points'] = int(s.get('points') or 0)
        out['q'] = time > 0 and out['points'] >= SNOOKER_Q
        return out
    course_faults = float(s.get('course_faults') or 0) + float(s.get('refusals') or 0)
    if sct_ is not None and time > sct_:
        out['time_faults'] = round(time - sct_, 2)
    out['total_faults'] = round(course_faults + out['time_faults'], 2)
    if yards and time > 0:
        out['yps'] = round(float(yards) / time, 2)
    out['q'] = time > 0 and sct_ is not None and course_faults == 0 and time <= sct_
    return out


def points_for(place: int, entered: int) -> int:
    if entered <= 10:
        table = {1: 6, 2: 5, 3: 4, 4: 4}
    elif entered <= 30:
        table = {1: 12, 2: 8, 3: 7, 4: 5}
    else:
        table = {1: 12, 2: 11, 3: 10, 4: 9, 5: 8, 6: 7, 7: 6, 8: 5, 9: 4, 10: 4}
    return table.get(place, CLEAR_ROUND_POINTS)


def placement_group(class_name: str, level: str, height: int, class_type: str, course: dict) -> tuple:
    if is_open_class(class_name, course):
        return (height, class_type)
    return (_level(level), height, class_type)


def place_class(class_name: str, runs: list, course: dict) -> list:
    """runs: dicts with id, level, height, class_type and a 'result' from score_run.
    Returns them with place and level_points filled in."""
    groups = {}
    for r in runs:
        groups.setdefault(placement_group(class_name, r['level'], r['height'], r['class_type'], course), []).append(r)
    game = is_game(class_name)
    for members in groups.values():
        entered = len(members)
        qs = [r for r in members if r['result']['q']]
        if game:
            qs.sort(key=lambda r: (-(r['result']['points'] or 0), r['result']['time']))
        else:
            qs.sort(key=lambda r: (r['result']['total_faults'] or 0, r['result']['time']))
        for r in members:
            r['place'], r['level_points'] = 0, 0
        for i, r in enumerate(qs, start=1):
            r['place'], r['level_points'] = i, points_for(i, entered)
    return runs
