"""
people.py — the handler & dog directory, and paper entries.

The directory keeps everyone who has ever entered, so a paper form (or a day-of entry) can be filled in by looking
the handler up instead of typing everything. It is filled from:
  - every show's JotForm entries (automatically on each refresh, or all at once from the Import page),
  - UKI Manager's handler export (grid_handlers CSV: UKI #, name, address, email, phone),
  - past UKI results spreadsheets (Raw Results: dog UKI #, name, breed, height, levels),
  - paper and day-of entries as they're made.

Each row remembers the date its details came from (info_date). Newer details overwrite older ones; older details
only fill blanks, so importing an old export never undoes a newer entry.

Paper entries are stored here and handed to formatter.load() as extra entries shaped exactly like parsed JotForm
submissions (submission_id 'paper-<id>'), so the workbook, typo fixes, warnings and run order treat them the same.
"""

import csv
import io
import re
from datetime import date, datetime

from sqlalchemy import and_, delete, func, insert, or_, select, update

from app import db
from app.core import CLASS_NAME_MAP, US_STATES, apply_corrections, build_runs, parse_entries

LEVELS = ['Nursery', 'Beginner', 'Novice', 'Senior', 'Champ']
HEIGHTS = [4, 8, 12, 16, 20, 22, 24]
HANDLER_INFO = ['handler_number', 'first_name', 'last_name', 'email', 'phone', 'addr_line1', 'addr_line2', 'city',
                'state', 'postal']
DOG_INFO = ['dog_number', 'dog_name', 'breed', 'height', 'class_type', 'level', 'speedstakes_level']

# Class wording from the JotForm forms, used when a show has no JotForm entries yet to copy it from
DEFAULT_CLASSES = {
    'saturday': ['Agility 1', 'Agility 2', 'Jumping', 'Jumping 2', 'Open Gamblers (all levels combined)', 'Snooker',
                 'Masters Series'],
    'sunday': ['Open Speedstakes 1 (all levels combined)', 'Speedstakes 2', 'Agility', 'Jumping', 'Jumping 2',
               'Masters Series'],
}
CLASS_SORT = ['Agility', 'Agility 2', 'Jumping', 'Jumping 2', 'Gamblers', 'Snooker', 'Speedstakes', 'Speedstakes 2',
              'Masters Series']


class PeopleError(Exception):
    """Shown to the user as a message."""


# ── Cleaning ─────────────────────────────────────────────────────────────────

def number(v) -> str:
    """UKI numbers come as 22247, '22,247', 22247.0 or ' 22247 '. Blank if there's no number."""
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    digits = re.sub(r'\D', '', str(v).split('.')[0])
    return digits.lstrip('0') or ('' if not digits else '0')


def _s(v) -> str:
    return re.sub(r'\s+', ' ', str(v)).strip() if v is not None else ''


def level(v) -> str:
    """'Champion' → 'Champ'; anything not a UKI level → ''."""
    v = _s(v).title()
    v = 'Champ' if v.startswith('Champ') else v
    return v if v in LEVELS else ''


def jotform_level(v) -> str:
    """The directory's 'Champ' back to the form's 'Champion'."""
    v = level(v)
    return 'Champion' if v == 'Champ' else v


def height(text) -> tuple:
    """'20 select', '20 inch (s)', '16 inch ', 16 → (20, 'Select') / (16, 'Regular'). (0, 'Regular') if unknown."""
    s = _s(text).lower()
    m = re.search(r'\d+', s)
    return (int(m.group()) if m else 0), ('Select' if '(s)' in s or 'select' in s else 'Regular')


def jotform_height(h, class_type) -> str:
    return f"{int(h)} {'select' if class_type == 'Select' else 'regular'}" if h else ''


def _iso(v) -> str:
    """A date from a spreadsheet cell ('3/7/2026', datetime, '2026-03-07') as ISO, or ''."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = _s(v)
    for fmt_ in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt_).date().isoformat()
        except ValueError:
            pass
    return ''


# ── Saving into the directory ────────────────────────────────────────────────

def _merge(c, table, row, data: dict, info_date: str, source: str):
    """Update `row` with `data`: newer details win, older ones only fill blanks. Returns the row id."""
    newer = (info_date or '') >= (row['info_date'] or '')
    changes = {k: v for k, v in data.items() if v not in ('', None, 0) and (newer or row[k] in ('', None, 0))}
    if newer and (info_date or not row['info_date']):
        changes.update(info_date=info_date, source=source)
    if changes:
        c.execute(update(table).where(table.c.id == row['id']).values(**changes, updated_at=db.now()))
    return row['id']


def _find_handler(c, data: dict):
    if data.get('handler_number'):
        return c.execute(select(db.handlers).where(db.handlers.c.handler_number == data['handler_number'])
                         .order_by(db.handlers.c.id)).mappings().first()
    if data.get('first_name') and data.get('last_name'):
        return c.execute(select(db.handlers).where(and_(
            db.handlers.c.handler_number == '',
            func.lower(db.handlers.c.first_name) == data['first_name'].lower(),
            func.lower(db.handlers.c.last_name) == data['last_name'].lower()))).mappings().first()
    return None


def save_handler(c, data: dict, info_date: str, source: str):
    """Add or update a handler. Returns the id, or None if there's nothing to identify them by."""
    data = {k: _s(data.get(k)) for k in HANDLER_INFO if k in data}
    data['handler_number'] = number(data.get('handler_number'))
    if data.get('state'):   # 'Virginia' / 'va' → 'VA'
        st = data['state'].upper().rstrip('.')
        data['state'] = US_STATES.get(st, st if len(st) <= 3 else data['state'])
    if not (data['handler_number'] or (data.get('first_name') and data.get('last_name'))):
        return None
    row = _find_handler(c, data)
    if row:
        return _merge(c, db.handlers, row, data, info_date, source)
    return c.execute(insert(db.handlers).values(**{k: data.get(k, '') for k in HANDLER_INFO}, info_date=info_date,
                                                source=source, updated_at=db.now())).inserted_primary_key[0]


def save_dog(c, handler_id, data: dict, info_date: str, source: str):
    data = {k: data.get(k) for k in DOG_INFO if k in data}
    data['dog_number'] = number(data.get('dog_number'))
    data['dog_name'] = _s(data.get('dog_name'))
    data['breed'] = _s(data.get('breed'))
    for k in ('level', 'speedstakes_level'):
        if k in data:
            data[k] = level(data[k])
    if not (data['dog_number'] or data['dog_name']):
        return None
    if data['dog_number']:
        row = c.execute(select(db.dogs).where(db.dogs.c.dog_number == data['dog_number'])
                        .order_by(db.dogs.c.id)).mappings().first()
    else:
        row = c.execute(select(db.dogs).where(and_(db.dogs.c.handler_id == handler_id, db.dogs.c.dog_number == '',
                                                   func.lower(db.dogs.c.dog_name) == data['dog_name'].lower()))
                        ).mappings().first()
    if handler_id:
        data['handler_id'] = handler_id
    if row:
        return _merge(c, db.dogs, row, data, info_date, source)
    return c.execute(insert(db.dogs).values(handler_id=handler_id, **{k: data.get(k) or ('Regular' if k == 'class_type'
                                                                                          else 0 if k == 'height' else '')
                                                                      for k in DOG_INFO},
                                            info_date=info_date, source=source, updated_at=db.now())).inserted_primary_key[0]


def add_from_entries(entries: list, info_date: str, source: str = 'jotform') -> int:
    """Put parsed (JotForm-shaped) entries into the directory. Returns how many entries were used."""
    n = 0
    with db.engine().begin() as c:
        for e in entries:
            hid = save_handler(c, e, info_date, source)
            h, ctype = height(e.get('jump_height'))
            save_dog(c, hid, {'dog_number': e.get('dog_number'), 'dog_name': e.get('dog_name'),
                              'breed': e.get('dog_breed'), 'height': h, 'class_type': ctype,
                              'level': e.get('international_level'),
                              'speedstakes_level': e.get('speedstakes_level') or e.get('international_level')},
                     info_date, source)
            n += 1
    return n


# ── Imports ──────────────────────────────────────────────────────────────────

def import_uki_handlers(text: str, info_date: str) -> int:
    """UKI Manager's handler grid export: "number";"first";"last";"address";"city";"state";"zip";"email";"";"phone"."""
    sample = text[:2000]
    delim = ';' if sample.count(';') > sample.count(',') else ','
    n = 0
    with db.engine().begin() as c:
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            row = (row + [''] * 10)[:10]
            if not number(row[0]):          # header or blank line
                continue
            if save_handler(c, {'handler_number': row[0], 'first_name': row[1], 'last_name': row[2],
                                'addr_line1': row[3], 'city': row[4], 'state': row[5], 'postal': row[6],
                                'email': row[7], 'phone': re.sub(r'[^\d+]', '', row[9])},
                            info_date, 'uki_manager'):
                n += 1
    return n


def import_results(xlsx_bytes: bytes) -> dict:
    """A UKI results workbook (Raw Results sheet). Each dog's latest row gives its height; agility/jumping/snooker
    rows give its level and Speedstakes rows its Speedstakes level. Gamblers and Snooker use the International level
    too (the form says "Agility, Jumping, Games"); Masters rows don't say a dog's level."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception:
        raise PeopleError("That file isn't an Excel workbook (.xlsx).")
    ws = wb['Raw Results'] if 'Raw Results' in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    head = [_s(h) for h in next(rows, [])]
    need = ['Level', 'Class', 'Height', 'Date', 'Member ID', 'Member Name', 'Dog ID', 'Dog Name']
    missing = [h for h in need if h not in head]
    if missing:
        raise PeopleError(f"This doesn't look like a UKI results sheet (no {', '.join(missing)} column).")
    col = {h: head.index(h) for h in head}
    dogs_seen = {}
    for r in rows:
        r = list(r) + [None] * len(head)
        dog_no, dog_name = number(r[col['Dog ID']]), _s(r[col['Dog Name']])
        if not (dog_no or dog_name):
            continue
        key = dog_no or f"{number(r[col['Member ID']])}:{dog_name.lower()}"
        when = _iso(r[col['Date']])
        cls = _s(r[col['Class']]).lower()
        d = dogs_seen.setdefault(key, {'when': '', 'lvl_when': '', 'ss_when': ''})
        if when >= d['when']:
            name = _s(r[col['Member Name']]).split(' ', 1)
            h, ctype = height(r[col['Height']])
            d.update(when=when, handler={'handler_number': r[col['Member ID']], 'first_name': name[0],
                                         'last_name': name[1] if len(name) > 1 else ''},
                     dog={'dog_number': dog_no, 'dog_name': dog_name, 'height': h, 'class_type': ctype,
                          'breed': _s(r[col['Breed']]) if 'Breed' in col else ''})
        lvl = level(r[col['Level']])
        if lvl and 'speedstakes' in cls and when >= d['ss_when']:
            d.update(ss_when=when, speedstakes_level=lvl)
        elif lvl and not any(w in cls for w in ('speedstakes', 'masters')) and when >= d['lvl_when']:
            d.update(lvl_when=when, level=lvl)
    with db.engine().begin() as c:
        handlers_n = set()
        for d in dogs_seen.values():
            hid = save_handler(c, d['handler'], d['when'], 'results')
            handlers_n.add(hid)
            save_dog(c, hid, {**d['dog'], 'level': d.get('level', ''), 'speedstakes_level': d.get('speedstakes_level', '')},
                     d['when'], 'results')
    return {'dogs': len(dogs_seen), 'handlers': len(handlers_n - {None})}


def import_all_shows() -> dict:
    """Every show's JotForm entries (with typo fixes) and paper entries, dated by the show's Saturday."""
    shows_n = entries_n = 0
    for slug, show in db.list_shows():
        subs, _ = db.get_snapshot(show.form_id)
        entries = apply_corrections(parse_entries(subs or [], show.field_names), show) if subs else []
        entries_n += add_from_entries(entries, show.saturday_date, 'jotform')
        entries_n += add_from_entries(paper_entries(slug), show.saturday_date, 'paper')
        shows_n += 1
    return {'shows': shows_n, 'entries': entries_n}


# ── Looking people up ────────────────────────────────────────────────────────

def _dogs_for(c, handler_ids) -> dict:
    out = {}
    if handler_ids:
        for d in c.execute(select(db.dogs).where(db.dogs.c.handler_id.in_(list(handler_ids)))
                           .order_by(db.dogs.c.dog_name)).mappings():
            out.setdefault(d['handler_id'], []).append(dict(d))
    return out


def search(q: str, limit: int = 15) -> list:
    """Handlers (each with their dogs) matching a UKI # (handler or dog), a name, a dog's name or an email."""
    q = _s(q)
    if not q:
        return []
    with db.engine().connect() as c:
        if number(q) and re.fullmatch(r'[\d,\s]+', q):
            n = number(q)
            dog_owner = select(db.dogs.c.handler_id).where(db.dogs.c.dog_number.like(f'{n}%'))
            where = or_(db.handlers.c.handler_number.like(f'{n}%'), db.handlers.c.id.in_(dog_owner))
        else:
            conds = []
            for word in q.lower().split()[:4]:
                like = f'%{word}%'
                dog_owner = select(db.dogs.c.handler_id).where(func.lower(db.dogs.c.dog_name).like(like))
                conds.append(or_(func.lower(db.handlers.c.first_name).like(like),
                                 func.lower(db.handlers.c.last_name).like(like),
                                 func.lower(db.handlers.c.email).like(like), db.handlers.c.id.in_(dog_owner)))
            where = and_(*conds)
        found = [dict(r) for r in c.execute(select(db.handlers).where(where)
                                            .order_by(db.handlers.c.last_name, db.handlers.c.first_name)
                                            .limit(limit)).mappings()]
        dogs_by = _dogs_for(c, [h['id'] for h in found])
    for h in found:
        h['dogs'] = dogs_by.get(h['id'], [])
    return found


def get_handler(handler_id: int):
    with db.engine().connect() as c:
        h = c.execute(select(db.handlers).where(db.handlers.c.id == handler_id)).mappings().first()
        if h is None:
            return None
        h = dict(h)
        h['dogs'] = _dogs_for(c, [handler_id]).get(handler_id, [])
    return h


def counts() -> dict:
    with db.engine().connect() as c:
        return {'handlers': c.execute(select(func.count()).select_from(db.handlers)).scalar(),
                'dogs': c.execute(select(func.count()).select_from(db.dogs)).scalar()}


def edit_handler(handler_id: int, data: dict, user: dict):
    """Typed in on the directory page: always wins (dated today)."""
    data = {k: _s(data.get(k)) for k in HANDLER_INFO}
    data['handler_number'] = number(data['handler_number'])
    if data['state']:
        st = data['state'].upper().rstrip('.')
        data['state'] = US_STATES.get(st, st if len(st) <= 3 else data['state'])
    if not (data['handler_number'] or (data['first_name'] and data['last_name'])):
        raise PeopleError('A handler needs a UKI # or a first and last name.')
    with db.engine().begin() as c:
        clash = c.execute(select(db.handlers.c.id).where(and_(db.handlers.c.handler_number == data['handler_number'],
                                                              db.handlers.c.id != handler_id))).first()
        if data['handler_number'] and clash:
            raise PeopleError(f"UKI #{data['handler_number']} already belongs to someone else in the directory.")
        c.execute(update(db.handlers).where(db.handlers.c.id == handler_id)
                  .values(**data, info_date=date.today().isoformat(), source='edited', updated_at=db.now()))
    db.log(user, 'directory_edit', target=f'h:{handler_id}')


def edit_dog(handler_id: int, dog_id, data: dict, user: dict) -> int:
    """Add (dog_id None) or change a dog on the directory page."""
    h, ctype = height(f"{data.get('height', '')} {data.get('class_type', '')}")
    vals = {'dog_number': number(data.get('dog_number')), 'dog_name': _s(data.get('dog_name')),
            'breed': _s(data.get('breed')), 'height': h, 'class_type': ctype, 'level': level(data.get('level')),
            'speedstakes_level': level(data.get('speedstakes_level')) or level(data.get('level')),
            'handler_id': handler_id, 'info_date': date.today().isoformat(), 'source': 'edited', 'updated_at': db.now()}
    if not vals['dog_name']:
        raise PeopleError("Enter the dog's name.")
    with db.engine().begin() as c:
        clash = c.execute(select(db.dogs.c.id).where(and_(db.dogs.c.dog_number == vals['dog_number'],
                                                          db.dogs.c.id != (dog_id or 0)))).first()
        if vals['dog_number'] and clash:
            raise PeopleError(f"Dog UKI #{vals['dog_number']} is already in the directory.")
        if dog_id:
            c.execute(update(db.dogs).where(db.dogs.c.id == dog_id).values(**vals))
        else:
            dog_id = c.execute(insert(db.dogs).values(**vals)).inserted_primary_key[0]
    db.log(user, 'directory_edit', target=f'd:{dog_id}')
    return dog_id


def delete_dog(dog_id: int, user: dict):
    with db.engine().begin() as c:
        c.execute(delete(db.dogs).where(db.dogs.c.id == dog_id))
    db.log(user, 'directory_delete', target=f'd:{dog_id}')


# ── Paper entries ────────────────────────────────────────────────────────────

def class_options(show, jotform_entries: list, setup_classes: dict = None) -> dict:
    """The classes offered each day, worded like this show's JotForm form (so paper and online entries match),
    plus any class added on Show setup that nobody has entered online yet. `setup_classes`: {'saturday': [names]}."""
    seen = {'saturday': [], 'sunday': []}
    for e in jotform_entries:
        for day in seen:
            for cls in e.get(f'{day}_classes') or []:
                if cls not in seen[day]:
                    seen[day].append(cls)
    for day, names in (setup_classes or {}).items():
        covered = {CLASS_NAME_MAP.get(c, c) for c in seen[day]}
        if 'Masters Series' in covered:
            covered |= {'Masters Agility', 'Masters Jumping'}
        for name in names:
            if name not in covered:
                add = 'Masters Series' if name.startswith('Masters') else name   # the form enters both rounds at once
                if add not in seen[day]:
                    seen[day].append(add)
                covered.add(name)
    key = lambda cls: (CLASS_SORT.index(CLASS_NAME_MAP.get(cls, cls)) if CLASS_NAME_MAP.get(cls, cls) in CLASS_SORT
                       else 99, cls)
    return {day: sorted(seen[day] or DEFAULT_CLASSES[day], key=key) for day in seen}


def class_label(cls: str) -> str:
    return CLASS_NAME_MAP.get(cls, cls)


def _as_entry(r) -> dict:
    return {
        'submission_id': f"paper-{r['id']}", 'created_at': (db.aware(r['created_at']) or db.now()).isoformat(),
        'first_name': r['first_name'], 'last_name': r['last_name'], 'handler_number': r['handler_number'],
        'dog_number': r['dog_number'], 'dog_name': r['dog_name'], 'dog_breed': r['breed'], 'email': r['email'],
        'addr_line1': r['addr_line1'], 'addr_line2': r['addr_line2'], 'city': r['city'], 'state': r['state'],
        'postal': r['postal'], 'jump_height': r['jump_height'], 'international_level': r['international_level'],
        'speedstakes_level': r['speedstakes_level'], 'saturday_classes': list(r['saturday_classes'] or []),
        'sunday_classes': list(r['sunday_classes'] or []),
        'amount_paid': float(r['amount_paid']) if r['amount_paid'] is not None else 0.0,
    }


def paper_entries(slug: str) -> list:
    """This show's paper entries, shaped like parse_entries() output."""
    with db.engine().connect() as c:
        rows = c.execute(select(db.paper_entries).where(db.paper_entries.c.show_slug == slug)
                         .order_by(db.paper_entries.c.id)).mappings().all()
    return [_as_entry(r) for r in rows]


def paper_rows(slug: str) -> list:
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(select(db.paper_entries).where(db.paper_entries.c.show_slug == slug)
                                           .order_by(db.paper_entries.c.id.desc())).mappings()]


def get_paper(slug: str, paper_id: int):
    with db.engine().connect() as c:
        r = c.execute(select(db.paper_entries).where(and_(db.paper_entries.c.show_slug == slug,
                                                          db.paper_entries.c.id == paper_id))).mappings().first()
    return dict(r) if r else None


def fees_for(show, dogs: list) -> float:
    """What these dogs' classes cost at the normal prices (same rule as the workbook)."""
    entries = [{'submission_id': '', 'first_name': '', 'last_name': '', 'handler_number': '', 'dog_number': '',
                'dog_name': '', 'dog_breed': '', 'email': '', 'jump_height': '', 'international_level': '',
                'speedstakes_level': '', 'saturday_classes': d['saturday_classes'], 'sunday_classes': d['sunday_classes']}
               for d in dogs]
    runs = build_runs(entries, show)
    return float(runs['class_cost'].sum()) if len(runs) else 0.0


def _paper_values(handler: dict, dog: dict) -> dict:
    h = {k: _s(handler.get(k)) for k in HANDLER_INFO}
    h['handler_number'] = number(h['handler_number'])
    hgt, ctype = height(f"{dog.get('height', '')} {dog.get('class_type', '')}")
    lvl = level(dog.get('level'))
    return {**h, 'dog_number': number(dog.get('dog_number')), 'dog_name': _s(dog.get('dog_name')),
            'breed': _s(dog.get('breed')), 'jump_height': jotform_height(hgt, ctype),
            'international_level': jotform_level(lvl),
            'speedstakes_level': jotform_level(dog.get('speedstakes_level') or lvl),
            'saturday_classes': list(dog.get('saturday_classes') or []),
            'sunday_classes': list(dog.get('sunday_classes') or [])}


def _check(handler: dict, dogs: list):
    if not (_s(handler.get('first_name')) and _s(handler.get('last_name'))):
        raise PeopleError("Enter the handler's first and last name.")
    if not dogs:
        raise PeopleError('Tick at least one dog and pick its classes.')
    for d in dogs:
        if not _s(d.get('dog_name')):
            raise PeopleError("Every dog being entered needs a name.")
        if not (d.get('saturday_classes') or d.get('sunday_classes')):
            raise PeopleError(f"Pick at least one class for {_s(d.get('dog_name'))}.")
        if not height(d.get('height'))[0]:
            raise PeopleError(f"Pick a jump height for {_s(d.get('dog_name'))}.")
        if not level(d.get('level')):
            raise PeopleError(f"Pick a level for {_s(d.get('dog_name'))}.")


def add_paper(slug: str, user: dict, handler: dict, dogs: list, amount_paid, method: str = '', note: str = '') -> list:
    """Save a paper form: one entry per dog (the payment goes on the first). Also updates the directory.
    `dogs`: dicts with dog_number, dog_name, breed, height, class_type, level, speedstakes_level, saturday_classes,
    sunday_classes and optionally dog_id."""
    _check(handler, dogs)
    today = date.today().isoformat()
    ids = []
    with db.engine().begin() as c:
        hid = save_handler(c, handler, today, 'paper')
        for i, dog in enumerate(dogs):
            vals = _paper_values(handler, dog)
            hgt, ctype = height(vals['jump_height'])
            dog_id = save_dog(c, hid, {'dog_number': vals['dog_number'], 'dog_name': vals['dog_name'],
                                       'breed': vals['breed'], 'height': hgt, 'class_type': ctype,
                                       'level': vals['international_level'],
                                       'speedstakes_level': vals['speedstakes_level']}, today, 'paper')
            ids.append(c.execute(insert(db.paper_entries).values(
                show_slug=slug, handler_id=hid, dog_id=dog_id, **vals,
                amount_paid=float(amount_paid or 0) if i == 0 else 0.0,
                payment_method=_s(method)[:30] if i == 0 else '', payment_note=_s(note)[:200] if i == 0 else '',
                created_at=db.now(), created_by=user.get('name', ''), updated_at=db.now())).inserted_primary_key[0])
    db.log(user, 'paper_entry', slug, target=f"h:{number(handler.get('handler_number')) or handler.get('last_name')}",
           dogs=[d.get('dog_name') for d in dogs], paid=float(amount_paid or 0))
    return ids


def update_paper(slug: str, paper_id: int, user: dict, handler: dict, dog: dict, amount_paid, method: str = '',
                 note: str = ''):
    _check(handler, [dog])
    vals = _paper_values(handler, dog)
    with db.engine().begin() as c:
        c.execute(update(db.paper_entries).where(and_(db.paper_entries.c.show_slug == slug,
                                                      db.paper_entries.c.id == paper_id))
                  .values(**vals, amount_paid=float(amount_paid or 0), payment_method=_s(method)[:30],
                          payment_note=_s(note)[:200], updated_at=db.now()))
    db.log(user, 'paper_edit', slug, target=f'paper-{paper_id}')


def delete_paper(slug: str, paper_id: int, user: dict):
    with db.engine().begin() as c:
        c.execute(delete(db.paper_entries).where(and_(db.paper_entries.c.show_slug == slug,
                                                      db.paper_entries.c.id == paper_id)))
    db.log(user, 'paper_delete', slug, target=f'paper-{paper_id}')
