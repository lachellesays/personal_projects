"""
trial.py

The trial itself: runs built from the entries, the run order for each class, conflicts,
day-of entries and payments, the run order CSV for the check-in app, and scribe sheets.

Run order (grouping checked against UKI Manager's June 2026 export):
- Each class is one list split into level groups (Gamblers and Masters: all levels together).
- Each day has its own order: by default Saturday runs Senior/Champ first, tall to small, and Sunday runs
  Beginner/Novice first, small to tall. Both can be changed per day.
- Select runs before Regular at each height (a class setting can flip that).
- Dogs are shuffled inside each height + type group, then swapped around inside their group so a handler's
  dogs are as far apart as possible. A handler with fewer than 10 dogs between their runs is a conflict.
"""

import csv
import io
import random
import re
import uuid
from datetime import date

from sqlalchemy import and_, delete, insert, select, update

from app import db
from app.core import CLASS_COST, CLASS_ORDER, MASTERS_CLASS_COST, build_runs

GAP_TARGET = 10           # fewer dogs than this between a handler's runs = conflict (same as UKI Manager)
DAY_OF_FEE = 5.00         # added to each class entered on the day
SENIOR_CHAMP = ('Senior', 'Champ', 'Champion')
COMBINED_BY_DEFAULT = ('Gamblers', 'Masters Agility', 'Masters Jumping')


class TrialError(Exception):
    pass


# ── Small helpers ───────────────────────────────────────────────────────────

def parse_height(text) -> tuple:
    """'20 inch (s)' / '20 Select' → (20, 'Select'); '16 inch' → (16, 'Regular')."""
    s = str(text or '').lower()
    m = re.search(r'\d+', s)
    return (int(m.group()) if m else 0), ('Select' if '(s)' in s or 'select' in s else 'Regular')


def level_group(level: str, combined: bool) -> str:
    if combined:
        return ''
    return 'Senior/Champ' if (level or '') in SENIOR_CHAMP else 'Beginner/Novice'


def combined_name(class_name: str, group: str) -> str:
    """The class name the check-in app and UKI use, e.g. 'Senior/Champ Agility 2' or 'Gamblers'."""
    return f'{group} {class_name}' if group else class_name


def event_parts(class_name: str) -> tuple:
    """'Agility 2' → ('Agility', 2); 'Masters Jumping' → ('Masters Jumping', 1)."""
    m = re.match(r'^(.*?)\s+(\d+)$', class_name)
    return (m.group(1), int(m.group(2))) if m else (class_name, 1)


def class_fee(class_name: str) -> float:
    return MASTERS_CLASS_COST if class_name.lower().startswith('master') else CLASS_COST


# Default class order for each day (from the LUDS premium); Show setup can change it per trial.
SATURDAY_ORDER = ['Gamblers', 'Snooker', 'Agility', 'Agility 2', 'Jumping', 'Jumping 2', 'Speedstakes', 'Speedstakes 2',
                  'Masters Jumping', 'Masters Agility']
SUNDAY_ORDER = ['Speedstakes', 'Speedstakes 2', 'Jumping', 'Jumping 2', 'Masters Jumping', 'Agility', 'Agility 2',
                'Masters Agility', 'Gamblers', 'Snooker']


def _sort_index(class_name: str, day: str = None) -> int:
    order = CLASS_ORDER
    if day:
        order = SATURDAY_ORDER if date.fromisoformat(day).weekday() == 5 else SUNDAY_ORDER
    return order.index(class_name) if class_name in order else len(order)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Building runs from the entries ──────────────────────────────────────────

def _jotform_runs(d) -> list:
    """Runs from the (typo-fixed) JotForm entries, as tm_runs rows without positions."""
    frame = build_runs(d.corrected, d.show)
    names = {e['submission_id']: (e['first_name'], e['last_name']) for e in d.corrected}
    out = []
    for r in frame.to_dict('records'):
        height, ctype = parse_height(r['jump_height'])
        dog_id = r['dog_number'] or f"sub{r['submission_id']}:{r['dog_name']}"
        first, last = names.get(r['submission_id'], ('', ''))
        out.append({
            'day': str(r['date']), 'class_name': r['class_name'], 'run_key': f"{dog_id}|{r['date']}|{r['class_name']}",
            'level': r['class_level'] or '', 'class_type': ctype, 'height': height,
            'handler_number': r['handler_number'], 'first_name': first, 'last_name': last,
            'dog_number': r['dog_number'], 'dog_name': r['dog_name'], 'breed': r['dog_breed'],
            'submission_id': r['submission_id'], 'source': 'jotform', 'fee': r['class_cost'],
        })
    return out


def is_built(slug: str) -> bool:
    with db.engine().connect() as c:
        return c.execute(select(db.runs.c.id).where(db.runs.c.show_slug == slug).limit(1)).first() is not None


def all_runs(slug: str) -> list:
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(select(db.runs).where(db.runs.c.show_slug == slug)
                                           .order_by(db.runs.c.day, db.runs.c.class_name, db.runs.c.position)).mappings()]


def pending_changes(d) -> dict:
    """JotForm runs not in the run order yet, and run order runs no longer in JotForm."""
    have = {r['run_key'] for r in all_runs(d.slug) if r['source'] == 'jotform'}
    want = {r['run_key']: r for r in _jotform_runs(d)}
    return {'new': [want[k] for k in want if k not in have], 'gone': sorted(have - set(want))}


def build_or_update(d, user: dict) -> dict:
    """First time: build every class and generate its order. Later: add new JotForm runs to the end of their
    height group and remove runs that were deleted from JotForm (day-of runs are never touched)."""
    first_time = not is_built(d.slug)
    changes = pending_changes(d)
    with db.engine().begin() as c:
        if changes['gone']:
            c.execute(delete(db.runs).where(db.runs.c.show_slug == d.slug, db.runs.c.source == 'jotform',
                                            db.runs.c.run_key.in_(changes['gone'])))
        for r in changes['new']:
            c.execute(insert(db.runs).values(show_slug=d.slug, position=0, scratched=False, created_at=db.now(),
                                             created_by=user.get('name', ''), **r))
    ensure_classes(d.slug)
    if first_time:
        for s in class_list(d.slug):
            generate(d.slug, s['day'], s['class_name'])
    else:
        for r in changes['new']:
            _place_in_group(d.slug, r['run_key'])
    db.log(user, 'runorder', d.slug, event='built' if first_time else 'updated from JotForm',
           added=len(changes['new']), removed=len(changes['gone']))
    return {'first_time': first_time, 'added': len(changes['new']), 'removed': len(changes['gone'])}


# ── Class settings ──────────────────────────────────────────────────────────

def ensure_classes(slug: str, also: set = frozenset()):
    """Give every day + class that has runs (or is in `also`) a settings row: order in the day, ring,
    Select first, combine levels. New classes slot in where the day's default order puts them."""
    with db.engine().begin() as c:
        have = {(r.day, r.class_name) for r in c.execute(select(db.class_settings.c.day, db.class_settings.c.class_name)
                                                         .where(db.class_settings.c.show_slug == slug))}
        used = {(r.day, r.class_name) for r in c.execute(select(db.runs.c.day, db.runs.c.class_name)
                                                         .where(db.runs.c.show_slug == slug).distinct())}
        for day_, cls in sorted((used | set(also)) - have):
            c.execute(insert(db.class_settings).values(
                show_slug=slug, day=day_, class_name=cls, sort=_sort_index(cls, day_) * 10, ring='1', select_first=True,
                combine_levels=cls in COMBINED_BY_DEFAULT))


def ensure_classes_from_entries(d):
    """Settings rows for every class people entered, even before the run order is built (for Show setup)."""
    ensure_classes(d.slug, {(r['day'], r['class_name']) for r in _jotform_runs(d)})


def save_day_setup(slug: str, day: str, levels_first: str, heights: str, classes: list, user: dict):
    """Show setup for one day: run order direction plus each class's place in the day, ring and Select first.
    `classes` is in running order: dicts with class_name, ring, select_first, combine_levels."""
    save_day(slug, day, levels_first, heights, user)
    current = {s['class_name']: s for s in class_list(slug, day)}
    if sorted(current) != sorted(c['class_name'] for c in classes):
        raise TrialError('The classes changed while you were editing. Reload the page and try again.')
    for n, c in enumerate(classes, start=1):
        s = current[c['class_name']]
        with db.engine().begin() as conn:
            conn.execute(update(db.class_settings).where(db.class_settings.c.id == s['id']).values(
                sort=n * 10, ring=(c.get('ring') or '1').strip()[:10], select_first=bool(c.get('select_first')),
                combine_levels=bool(c.get('combine_levels'))))
        if (s['select_first'], s['combine_levels']) != (bool(c.get('select_first')), bool(c.get('combine_levels'))):
            regroup_keep_order(slug, day, c['class_name'])
    db.log(user, 'setup', slug, target=day, classes=[c['class_name'] for c in classes])


def class_list(slug: str, day: str = None) -> list:
    q = select(db.class_settings).where(db.class_settings.c.show_slug == slug)
    if day:
        q = q.where(db.class_settings.c.day == day)
    with db.engine().connect() as c:
        rows = [dict(r) for r in c.execute(q).mappings()]
    return sorted(rows, key=lambda s: (s['day'], s['sort'], _sort_index(s['class_name'], s['day']), s['class_name']))


def get_class(slug: str, day: str, class_name: str) -> dict:
    found = [s for s in class_list(slug, day) if s['class_name'] == class_name]
    if not found:
        raise TrialError('That class isn’t in this trial.')
    return found[0]


def save_class(slug: str, day: str, class_name: str, user: dict, sort: int, ring: str, select_first: bool,
               combine_levels: bool, regroup: bool = True):
    before = get_class(slug, day, class_name)
    with db.engine().begin() as c:
        c.execute(update(db.class_settings).where(db.class_settings.c.id == before['id']).values(
            sort=sort, ring=(ring or '1').strip()[:10], select_first=select_first, combine_levels=combine_levels))
    if regroup and (before['select_first'] != select_first or before['combine_levels'] != combine_levels):
        regroup_keep_order(slug, day, class_name)
    db.log(user, 'runorder', slug, target=f'{day}|{class_name}', event='class settings', sort=sort, ring=ring,
           select_first=select_first, combine_levels=combine_levels)


# ── Run order ───────────────────────────────────────────────────────────────

LEVEL_GROUPS = ('Senior/Champ', 'Beginner/Novice')
HEIGHT_ORDERS = {'tall': 'Tall to small', 'small': 'Small to tall'}


def default_day(day: str) -> dict:
    """Saturday: Senior/Champ first, tall to small. Sunday (and any other day): Beginner/Novice first, small to tall."""
    if date.fromisoformat(day).weekday() == 5:
        return {'levels_first': 'Senior/Champ', 'heights': 'tall'}
    return {'levels_first': 'Beginner/Novice', 'heights': 'small'}


def day_setting(slug: str, day: str) -> dict:
    with db.engine().connect() as c:
        row = c.execute(select(db.day_settings).where(db.day_settings.c.show_slug == slug,
                                                      db.day_settings.c.day == day)).mappings().first()
    return {'day': day, **({'levels_first': row['levels_first'], 'heights': row['heights']} if row else default_day(day))}


def save_day(slug: str, day: str, levels_first: str, heights: str, user: dict, regroup: bool = True):
    if levels_first not in LEVEL_GROUPS or heights not in HEIGHT_ORDERS:
        raise TrialError('Pick which levels run first and which way the heights go.')
    before = day_setting(slug, day)
    with db.engine().begin() as c:
        c.execute(delete(db.day_settings).where(db.day_settings.c.show_slug == slug, db.day_settings.c.day == day))
        c.execute(insert(db.day_settings).values(show_slug=slug, day=day, levels_first=levels_first, heights=heights))
    if regroup and (before['levels_first'], before['heights']) != (levels_first, heights):
        for s in class_list(slug, day):
            regroup_keep_order(slug, day, s['class_name'])
    db.log(user, 'runorder', slug, target=day, event='day order', levels_first=levels_first, heights=heights)


def _group_key(run: dict, setting: dict, day: dict) -> tuple:
    group = level_group(run['level'], setting['combine_levels'])
    level_rank = 0 if group in ('', day['levels_first']) else 1
    height = run['height'] or 0
    type_rank = 0 if (run['class_type'] == 'Select') == setting['select_first'] else 1
    return (level_rank, -height if day['heights'] == 'tall' else height, type_rank)


def scratched_dogs(slug: str) -> tuple:
    show = db.get_show(slug)
    return (set(show.scratched_handlers), set(show.scratched_dogs)) if show else (set(), set())


def _is_out(run: dict, scratches: tuple) -> bool:
    handlers, dogs = scratches
    return bool(run['scratched'] or run['handler_number'] in handlers or (run['dog_number'] and run['dog_number'] in dogs))


def _event(slug: str, day: str, class_name: str) -> list:
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(
            select(db.runs).where(db.runs.c.show_slug == slug, db.runs.c.day == day, db.runs.c.class_name == class_name)
            .order_by(db.runs.c.position, db.runs.c.id)).mappings()]


def _save_positions(ordered_ids: list):
    with db.engine().begin() as c:
        for pos, run_id in enumerate(ordered_ids, start=1):
            c.execute(update(db.runs).where(db.runs.c.id == run_id).values(position=pos))


def gaps(ordered: list, out: set = frozenset()) -> dict:
    """{run_id: dogs between this run and the same handler's previous run} — scratched runs don't count."""
    result, last_seen, count = {}, {}, 0
    for r in ordered:
        if r['id'] in out:
            continue
        h = r['handler_number'] or f"?{r['first_name']} {r['last_name']}"
        if h in last_seen:
            result[r['id']] = count - last_seen[h] - 1
        last_seen[h] = count
        count += 1
    return result


def _penalty(ordered: list, out: set) -> int:
    return sum((GAP_TARGET - g) ** 2 for g in gaps(ordered, out).values() if g < GAP_TARGET)


def spread(ordered: list, keys: dict, out: set, rng: random.Random, iterations: int = 4000) -> list:
    """Swap runs around inside their own height/type group to push a handler's dogs apart."""
    order = list(ordered)
    best = _penalty(order, out)
    groups = {}
    for i, r in enumerate(order):
        groups.setdefault(keys[r['id']], []).append(i)
    for _ in range(iterations):
        if best == 0:
            break
        g = gaps(order, out)
        bad = [i for i, r in enumerate(order) if g.get(r['id'], GAP_TARGET) < GAP_TARGET]
        if not bad:
            break
        i = rng.choice(bad)
        if rng.random() < 0.5:   # the earlier dog of the pair can move too
            h = order[i]['handler_number']
            earlier = [j for j in range(i) if order[j]['handler_number'] == h and order[j]['id'] not in out]
            i = earlier[-1] if earlier else i
        slots = groups[keys[order[i]['id']]]
        j = rng.choice(slots)
        if i == j:
            continue
        order[i], order[j] = order[j], order[i]
        score = _penalty(order, out)
        if score <= best:
            best = score
        else:
            order[i], order[j] = order[j], order[i]
    return order


def generate(slug: str, day: str, class_name: str, seed=None, user: dict = None) -> list:
    """Build a fresh order for one class (shuffled inside each group, then spread apart)."""
    setting = get_class(slug, day, class_name)
    runs_ = _event(slug, day, class_name)
    before = [r['id'] for r in runs_]
    rng = random.Random(seed)
    out = {r['id'] for r in runs_ if _is_out(r, scratched_dogs(slug))}
    dset = day_setting(slug, day)
    keys = {r['id']: _group_key(r, setting, dset) for r in runs_}
    rng.shuffle(runs_)
    runs_.sort(key=lambda r: keys[r['id']])
    runs_ = spread(runs_, keys, out, rng)
    _save_positions([r['id'] for r in runs_])
    with db.engine().begin() as c:
        c.execute(update(db.class_settings).where(db.class_settings.c.id == setting['id']).values(generated_at=db.now()))
    if user:
        db.log(user, 'reorder', slug, target=f'{day}|{class_name}', event='generated', before=before)
    return runs_


def regroup_keep_order(slug: str, day: str, class_name: str):
    """After a settings change, re-sort into groups but keep each group's current order."""
    setting = get_class(slug, day, class_name)
    runs_ = _event(slug, day, class_name)
    dset = day_setting(slug, day)
    runs_.sort(key=lambda r: _group_key(r, setting, dset))   # stable sort keeps the order inside each group
    _save_positions([r['id'] for r in runs_])


def _place_in_group(slug: str, run_key: str):
    """Put a newly added run at the end of its height/type group."""
    with db.engine().connect() as c:
        new = c.execute(select(db.runs).where(db.runs.c.show_slug == slug, db.runs.c.run_key == run_key)).mappings().first()
    if not new:
        return
    setting = get_class(slug, new['day'], new['class_name'])
    others = [r for r in _event(slug, new['day'], new['class_name']) if r['id'] != new['id']]
    dset = day_setting(slug, new['day'])
    key = _group_key(dict(new), setting, dset)
    at = 0
    if new['source'] == 'day_of' and masters_order(slug, new['day']).get(new['class_name']) == 'first':
        # Masters rule: day-of entries run first within the first Masters class
        at = next((i for i, r in enumerate(others) if _group_key(r, setting, dset) >= key), len(others))
    else:
        for i, r in enumerate(others):
            if _group_key(r, setting, dset) <= key:
                at = i + 1
    ids = [r['id'] for r in others]
    ids.insert(at, new['id'])
    _save_positions(ids)


MASTERS = ('Masters Agility', 'Masters Jumping')


def masters_order(slug: str, day: str) -> dict:
    """{'Masters Jumping': 'first', 'Masters Agility': 'second'} for a day with both, by running order."""
    found = [s['class_name'] for s in class_list(slug, day) if s['class_name'] in MASTERS]
    return {found[0]: 'first', found[1]: 'second'} if len(found) == 2 else {}


def order_masters_from_first_round(slug: str, day: str, class_name: str, user: dict):
    """Masters rule: the second class runs in reverse order of placing from the first class —
    within each height, the dog with the worst first-round score runs first and the best runs last."""
    from app import results, scoring
    order = masters_order(slug, day)
    if order.get(class_name) != 'second':
        raise TrialError('Only the second Masters class of the day is ordered from the first round.')
    first = next(c for c, o in order.items() if o == 'first')
    view = results.entry_view(slug, day, first)
    if not view['calculated']:
        raise TrialError(f'Calculate {first} first.')
    score = {results._dog_key(r): scoring.masters_round_score(r['result']) for r in view['rows']}
    setting, dset = get_class(slug, day, class_name), day_setting(slug, day)
    runs_ = _event(slug, day, class_name)
    before = [r['id'] for r in runs_]
    def worst_first(r):
        s_ = score.get(results._dog_key(r))
        return (_group_key(r, setting, dset), 0 if s_ is None else 1, -(s_ or 0))
    runs_.sort(key=worst_first)
    _save_positions([r['id'] for r in runs_])
    db.log(user, 'reorder', slug, target=f'{day}|{class_name}', event=f'reverse order of {first} results', before=before)


def reorder(slug: str, day: str, class_name: str, ordered_ids: list, user: dict):
    current = [r['id'] for r in _event(slug, day, class_name)]
    ordered_ids = [int(i) for i in ordered_ids]
    if sorted(current) != sorted(ordered_ids):
        raise TrialError('The run order changed while you were editing. Reload the page and try again.')
    if current == ordered_ids:
        return
    _save_positions(ordered_ids)
    db.log(user, 'reorder', slug, target=f'{day}|{class_name}', event='moved', before=current)


def move_to(slug: str, run_id: int, position: int, user: dict):
    with db.engine().connect() as c:
        run = c.execute(select(db.runs).where(db.runs.c.id == run_id, db.runs.c.show_slug == slug)).mappings().first()
    if not run:
        raise TrialError('That run doesn’t exist.')
    ids = [r['id'] for r in _event(slug, run['day'], run['class_name'])]
    ids.remove(run_id)
    ids.insert(max(0, min(len(ids), position - 1)), run_id)
    reorder(slug, run['day'], run['class_name'], ids, user)


def undo(slug: str, day: str, class_name: str, user: dict) -> bool:
    """Put back the order from before the last change to this class."""
    target = f'{day}|{class_name}'
    with db.engine().connect() as c:
        rows = c.execute(select(db.audit).where(db.audit.c.show_slug == slug, db.audit.c.action == 'reorder',
                                                db.audit.c.target == target).order_by(db.audit.c.id.desc())).mappings().all()
    last = next((r for r in rows if (r['detail'] or {}).get('event') != 'undo'), None)
    if not last or not (last['detail'] or {}).get('before'):
        return False
    current = {r['id'] for r in _event(slug, day, class_name)}
    before = [i for i in last['detail']['before'] if i in current]
    before += [i for i in current if i not in before]          # runs added since go to the end
    _save_positions(before)
    with db.engine().begin() as c:   # mark that change as undone so the next Undo goes one step further
        detail = dict(last['detail']); detail['event'] = 'undo'
        c.execute(update(db.audit).where(db.audit.c.id == last['id']).values(detail=detail))
    db.log(user, 'runorder', slug, target=target, event='undo')
    return True


def toggle_run_scratch(slug: str, run_id: int, user: dict) -> bool:
    with db.engine().begin() as c:
        run = c.execute(select(db.runs).where(db.runs.c.id == run_id, db.runs.c.show_slug == slug)).mappings().first()
        if not run:
            raise TrialError('That run doesn’t exist.')
        c.execute(update(db.runs).where(db.runs.c.id == run_id).values(scratched=not run['scratched']))
    db.log(user, 'unscratch' if run['scratched'] else 'scratch', slug, target=f'r:{run_id}',
           dog=run['dog_name'], cls=run['class_name'], day=run['day'])
    return not run['scratched']


def event_view(slug: str, day: str, class_name: str) -> dict:
    """Everything the run order page shows for one class."""
    setting = get_class(slug, day, class_name)
    runs_ = _event(slug, day, class_name)
    scr = scratched_dogs(slug)
    out = {r['id'] for r in runs_ if _is_out(r, scr)}
    g = gaps(runs_, out)
    rows, seq, prev_group, prev_block = [], 0, None, None
    for r in runs_:
        group = level_group(r['level'], setting['combine_levels'])
        block = (group, r['height'], r['class_type'])
        is_out = r['id'] in out
        if not is_out:
            seq += 1
        rows.append({**r, 'out': is_out, 'dog_scratched': is_out and not r['scratched'], 'seq': None if is_out else seq,
                     'gap': g.get(r['id']), 'conflict': g.get(r['id'], GAP_TARGET) < GAP_TARGET,
                     'group': group, 'new_group': group != prev_group, 'new_block': block != prev_block,
                     'block_label': f"{r['height']}\" {r['class_type']}"})
        prev_group, prev_block = group, block
    return {'setting': setting, 'rows': rows, 'active': seq, 'conflicts': sum(1 for r in rows if r['conflict'] and not r['out'])}


def overview(slug: str) -> list:
    """Per day + class: runs and conflicts, for the class list."""
    scr = scratched_dogs(slug)
    out = []
    for s in class_list(slug):
        runs_ = _event(slug, s['day'], s['class_name'])
        o = {r['id'] for r in runs_ if _is_out(r, scr)}
        g = gaps(runs_, o)
        out.append({**s, 'runs': len(runs_) - len(o), 'conflicts': sum(1 for v in g.values() if v < GAP_TARGET),
                    'day_of': sum(1 for r in runs_ if r['source'] == 'day_of' and r['id'] not in o)})
    return out


# ── Day-of entries ──────────────────────────────────────────────────────────

def known_dogs(d) -> list:
    """Every dog in the trial (JotForm or day-of), with what a day-of entry needs to copy."""
    dogs = {}
    for e in d.corrected:
        if not e['dog_name']:
            continue
        key = e['dog_number'] or f"sub{e['submission_id']}:{e['dog_name']}"
        height, ctype = parse_height(e['jump_height'])
        dogs[key] = {'key': key, 'dog_number': e['dog_number'], 'dog_name': e['dog_name'], 'breed': e['dog_breed'],
                     'handler_number': e['handler_number'], 'first_name': e['first_name'], 'last_name': e['last_name'],
                     'height': height, 'class_type': ctype, 'level': _lvl(e['international_level']),
                     'speedstakes_level': _lvl(e['speedstakes_level'] or e['international_level'])}
    for r in all_runs(d.slug):
        if r['source'] != 'day_of':
            continue
        key = r['dog_number'] or f"dayof:{r['handler_number']}:{r['dog_name']}"
        if key not in dogs:
            lvl = r['level']
            dogs[key] = {'key': key, 'dog_number': r['dog_number'], 'dog_name': r['dog_name'], 'breed': r['breed'],
                         'handler_number': r['handler_number'], 'first_name': r['first_name'], 'last_name': r['last_name'],
                         'height': r['height'], 'class_type': r['class_type'], 'level': lvl, 'speedstakes_level': lvl}
    return sorted(dogs.values(), key=lambda x: (x['last_name'].lower(), x['first_name'].lower(), x['dog_name'].lower()))


def _lvl(level: str) -> str:
    level = (level or '').strip()
    return 'Champ' if level == 'Champion' else level


def add_day_of(d, user: dict, dog: dict, day: str, classes: list) -> list:
    """Enter one dog in classes on the day. Each class costs its normal fee plus DAY_OF_FEE.
    `dog` needs handler_number, first_name, last_name, dog_number, dog_name, breed, height, class_type,
    level and speedstakes_level."""
    if not classes:
        raise TrialError('Pick at least one class.')
    if not (dog.get('dog_name') and (dog.get('handler_number') or dog.get('last_name'))):
        raise TrialError('A day-of entry needs at least the dog’s name and the handler’s name or number.')
    if day not in (str(d.show.saturday_date), str(d.show.sunday_date)):
        raise TrialError('Pick Saturday or Sunday.')
    existing = {(r['day'], r['class_name'], r['dog_number'] or r['dog_name']) for r in all_runs(d.slug) if not r['scratched']}
    added = []
    with db.engine().begin() as c:
        for cls in classes:
            if (day, cls, dog.get('dog_number') or dog['dog_name']) in existing:
                raise TrialError(f'{dog["dog_name"]} is already entered in {cls} that day.')
            level = dog.get('speedstakes_level') if 'speedstakes' in cls.lower() else dog.get('level')
            key = f'dayof:{uuid.uuid4().hex[:12]}'
            c.execute(insert(db.runs).values(
                show_slug=d.slug, day=day, class_name=cls, run_key=key, level=_lvl(level or ''),
                class_type=dog.get('class_type') or 'Regular', height=int(dog.get('height') or 0),
                handler_number=str(dog.get('handler_number') or '').strip(), first_name=(dog.get('first_name') or '').strip(),
                last_name=(dog.get('last_name') or '').strip(), dog_number=str(dog.get('dog_number') or '').strip(),
                dog_name=dog['dog_name'].strip(), breed=(dog.get('breed') or '').strip(), source='day_of',
                fee=class_fee(cls) + DAY_OF_FEE, scratched=False, position=0, created_at=db.now(),
                created_by=user.get('name', '')))
            added.append(key)
    ensure_classes(d.slug)
    for key in added:
        _place_in_group(d.slug, key)
    db.log(user, 'dayof', d.slug, target=f"d:{dog.get('dog_number') or dog['dog_name']}", day=day, classes=classes)
    return added


def add_payment(slug: str, user: dict, handler_number: str, amount, method: str = '', note: str = ''):
    amount = _num(amount)
    if not handler_number or amount == 0:
        raise TrialError('Enter the handler and an amount.')
    with db.engine().begin() as c:
        c.execute(insert(db.day_payments).values(show_slug=slug, handler_number=handler_number, amount=amount,
                                                 method=method[:30], note=note[:200], at=db.now(),
                                                 user_name=user.get('name', '')))
    db.log(user, 'payment', slug, target=f'h:{handler_number}', amount=amount, method=method)


def day_of_summary(slug: str) -> dict:
    runs_ = [r for r in all_runs(slug) if r['source'] == 'day_of']
    with db.engine().connect() as c:
        pays = [dict(r) for r in c.execute(select(db.day_payments).where(db.day_payments.c.show_slug == slug)
                                           .order_by(db.day_payments.c.id)).mappings()]
    handlers = {}
    for r in runs_:
        h = handlers.setdefault(r['handler_number'] or f"{r['first_name']} {r['last_name']}", {
            'handler_number': r['handler_number'], 'name': f"{r['first_name']} {r['last_name']}".strip(),
            'runs': [], 'fees': 0.0, 'paid': 0.0})
        h['runs'].append(r)
        if not r['scratched']:
            h['fees'] += _num(r['fee'])
    for p in pays:
        h = handlers.setdefault(p['handler_number'], {'handler_number': p['handler_number'], 'name': '', 'runs': [],
                                                      'fees': 0.0, 'paid': 0.0})
        h['paid'] += _num(p['amount'])
    for h in handlers.values():
        h['balance'] = round(h['fees'] - h['paid'], 2)
        h['runs'].sort(key=lambda r: (r['day'], _sort_index(r['class_name'])))
    return {'handlers': sorted(handlers.values(), key=lambda h: h['name'].lower()), 'payments': pays,
            'fees': sum(h['fees'] for h in handlers.values()), 'paid': sum(h['paid'] for h in handlers.values())}


# ── Exports ─────────────────────────────────────────────────────────────────

CSV_COLUMNS = ['Run_Order', 'Date', 'Day', 'Event', 'Event_Num', 'Level', 'Class_Type', 'Intl_Jump_Ht', 'Name', 'Breed',
               'First_Name', 'Last_Name', 'Handler_Name', 'UKI_Number', 'UKI_Dog_Number', 'Run Group', 'Team Name',
               'Combined Class Name', 'Ring']


def ordered_for_export(slug: str, day: str = None) -> list:
    """Active runs in running order, one dict per run, with the class's combined name and running number."""
    scr = scratched_dogs(slug)
    out = []
    for n, s in enumerate(class_list(slug, day), start=1):
        seq = 0
        for r in _event(slug, s['day'], s['class_name']):
            if _is_out(r, scr):
                continue
            seq += 1
            group = level_group(r['level'], s['combine_levels'])
            out.append({**r, 'seq': seq, 'event_index': n, 'ring': s['ring'], 'group': group,
                        'combined': combined_name(s['class_name'], group)})
    return out


def run_order_csv(slug: str, day: str = None) -> bytes:
    """The run order in the same columns as UKI Manager's grid_run_order_export.csv (what the check-in app reads)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in ordered_for_export(slug, day):
        d_ = date.fromisoformat(r['day'])
        event, num = event_parts(r['class_name'])
        w.writerow([r['event_index'] * 100000 + r['seq'] * 10, d_.strftime('%m/%d/%Y'), d_.strftime('%A'), event, num,
                    r['level'], r['class_type'], r['height'], r['dog_name'], r['breed'], r['first_name'], r['last_name'],
                    f"{r['first_name']} {r['last_name']}".strip(), r['handler_number'], r['dog_number'], ' ', '',
                    r['combined'], r['ring']])
    return buf.getvalue().encode('utf-8')


def class_label(r: dict) -> str:
    """'Agility-1 / Champ 8inch (s)', as printed on UKI Manager's judging sheets."""
    event, num = event_parts(r['class_name'])
    return f"{event}-{num} / {r['level']} {r['height']}inch" + (' (s)' if r['class_type'] == 'Select' else '')


def collated_pages(runs_: list, per_page: int = 4) -> list:
    """Lay each class out for cutting, like UKI Manager: with P pages, run k goes on page k % P in slot k // P.
    Print, cut the stack into quarters, and stack the piles top-left, top-right, bottom-left, bottom-right
    to get the class in running order. Each class starts on a fresh page."""
    pages, by_class = [], {}
    for r in runs_:
        by_class.setdefault((r['event_index'], r['class_name']), []).append(r)
    for _, items in sorted(by_class.items()):
        n_pages = -(-len(items) // per_page)
        block = [[None] * per_page for _ in range(n_pages)]
        for k, r in enumerate(items):
            block[k % n_pages][k // n_pages] = r
        pages.extend(block)
    return pages


def scribe_sheets_pdf(slug: str, show_name: str, day: str, class_names: list = None) -> bytes:
    """Judging (scribe) sheets, four to a page, collated for cutting (see collated_pages), laid out like
    UKI Manager's. Gamblers gets the opening-points grid; every other class gets the time/faults sheet."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    runs_ = [r for r in ordered_for_export(slug, day) if not class_names or r['class_name'] in class_names]
    if not runs_:
        raise TrialError('There are no runs to print for that day.')
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    pdf.setTitle(f'Scribe sheets {day}')
    pw, ph = letter
    margin_x, margin_y, gutter_x, gutter_y = 40, 40, 30, 40
    cw, ch = (pw - 2 * margin_x - gutter_x) / 2, (ph - 2 * margin_y - gutter_y) / 2
    heading = f"{date.fromisoformat(day).strftime('%A')}: {show_name}"
    date_label = date.fromisoformat(day).strftime('%m/%d/%Y')
    for page in collated_pages(runs_):
        for slot, r in enumerate(page):
            if r is None:
                continue
            col, row = slot % 2, slot // 2
            x, y = margin_x + col * (cw + gutter_x), ph - margin_y - (row + 1) * ch - row * gutter_y
            if r['class_name'].lower().startswith('gamblers'):
                _gamblers_card(pdf, x, y, cw, ch, r, heading, date_label)
            else:
                _standard_card(pdf, x, y, cw, ch, r, heading, date_label)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _card_people(pdf, x, top, w, r, name_size=8.5):
    """Handler, then the dog in bold with R/O (running order) on the right."""
    handler = f"{r['first_name']} {r['last_name']}".strip()
    pdf.setFont('Helvetica', name_size)
    pdf.drawString(x + 6, top - 12, f'Handler:{handler}')
    dog = f"Dog:{r['dog_name']}" + (f" ({r['breed']})" if r['breed'] else '')
    size = 10.5
    while size > 7 and pdf.stringWidth(dog, 'Helvetica-Bold', size) > w - 52:
        size -= 0.5
    pdf.setFont('Helvetica-Bold', size)
    pdf.drawString(x + 6, top - 26, dog)
    pdf.setFont('Helvetica', 8)
    pdf.drawRightString(x + w - 6, top - 26, f"R/O {r['seq']}" + ('  DAY OF' if r['source'] == 'day_of' else ''))


def _standard_card(pdf, x, y, w, h, r, heading, date_label):
    top = y + h
    pdf.setLineWidth(1.4)
    pdf.rect(x, y, w, h)
    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(x + 6, top - 11, heading)
    pdf.drawRightString(x + w - 6, top - 11, 'Judging Sheet')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(x + 6, top - 24, class_label(r))
    pdf.setFont('Helvetica', 7.5)
    pdf.drawRightString(x + w - 6, top - 24, date_label)
    pdf.line(x, top - 30, x + w, top - 30)
    _card_people(pdf, x, top - 30, w, r)
    box_x = x + w * 0.69
    yy = top - 62
    pdf.line(x, yy, x + w, yy)
    for label in ("Dog's Time", 'Standard Course Time', 'Time Faults / Penalty'):
        pdf.setFont('Helvetica', 8)
        pdf.drawString(x + 32, yy - 14, label)
        pdf.line(box_x, yy, box_x, yy - 22)
        yy -= 22
        pdf.line(x, yy, x + w, yy)
    pdf.drawString(x + 32, yy - 18, 'Course Faults / Points')
    pdf.line(box_x, yy, box_x, yy - 36)
    pdf.line(box_x, yy - 36, x + w, yy - 36)
    total_h = h * 0.2
    pdf.line(box_x, y + total_h, x + w, y + total_h)
    pdf.line(box_x, y, box_x, y + total_h)
    pdf.setFont('Helvetica', 9)
    pdf.drawString(x + 6, y + total_h * 0.55, 'Group:')
    pdf.drawString(x + 6, y + total_h * 0.2, (r.get('run_group') or '').strip())
    pdf.setFont('Helvetica', 8)
    pdf.drawString(x + w * 0.42, y + total_h * 0.4, 'Total')


def _gamblers_card(pdf, x, y, w, h, r, heading, date_label):
    top = y + h
    pdf.setLineWidth(1.4)
    pdf.rect(x, y, w, h)
    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(x + 6, top - 10, 'Group:')
    pdf.drawRightString(x + w - 6, top - 10, 'Gamblers Judging Sheet')
    pdf.drawString(x + 10, top - 19, heading)
    pdf.drawString(x + 10, top - 28, class_label(r))
    pdf.drawRightString(x + w - 6, top - 28, date_label)
    pdf.line(x, top - 32, x + w, top - 32)
    _card_people(pdf, x, top - 32, w, r, name_size=9)
    right = x + w * 0.76
    yy = top - 64
    pdf.line(x, yy, x + w, yy)
    # Opening + gamble time / dog's time
    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(x + 4, yy - 20, 'Opening + Gamble time=__________ MCT')
    pdf.drawRightString(x + w - 6, yy - 10, "Dog's Time")
    pdf.line(right + 8, yy - 26, x + w - 6, yy - 26)
    pdf.line(right, yy, right, y + h * 0.17)
    yy -= 30
    pdf.line(x, yy, x + w, yy)
    pdf.setFont('Helvetica-Bold', 7.5)
    pdf.drawString(x + 4, yy - 10, "Opening obstacles points in dog's path")
    pdf.setFont('Helvetica', 7)
    pdf.drawString(x + 4, yy - 20, 'ie 1-1-2-5-1-3-0-5-2   0=faulted obstacle')
    grid_top = yy - 24
    rows_, cols = 4, 8
    cell_h = (grid_top - (y + h * 0.17) - 28) / rows_
    cell_w = (right - x) / cols
    for i in range(rows_ + 1):
        pdf.line(x, grid_top - i * cell_h, right, grid_top - i * cell_h)
    for j in range(1, cols):
        pdf.line(x + j * cell_w, grid_top, x + j * cell_w, grid_top - rows_ * cell_h)
    pdf.setFont('Helvetica', 7.5)
    for k in range(5):   # point multipliers down the right
        ly = yy - 18 - k * ((yy - 18 - (grid_top - rows_ * cell_h)) / 5)
        pdf.drawString(right + 6, ly, f'{k + 1}x__________')
    tot_top = grid_top - rows_ * cell_h
    pdf.line(right, tot_top, x + w, tot_top)
    pdf.setFont('Helvetica-Bold', 7.5)
    pdf.drawRightString(right - 6, tot_top - 12, 'Total opening points')
    bottom = y + h * 0.17
    pdf.line(x, bottom, x + w, bottom)
    pdf.line(x + w * 0.54, bottom, x + w * 0.54, y)
    pdf.line(right, bottom, right, y)
    pdf.setFont('Helvetica', 9)
    pdf.drawString(x + 10, bottom - 22, 'Gamble Points')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(x + 14, bottom - 40, '10 / 15 / 20')
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawCentredString(x + w * 0.65, bottom - 14, 'Qualify?')
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawCentredString(x + w * 0.65, bottom - 32, 'Y     N')
    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(right + 6, bottom - 12, 'Total game')
    pdf.drawString(right + 6, bottom - 21, 'points')
    pdf.line(right + 8, y + 8, x + w - 6, y + 8)
