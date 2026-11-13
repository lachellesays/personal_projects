"""
measurements.py

Dog measurements taken at a trial (UKI rule book p. 26), for the Measurements sheet of the trial data workbook
sent to UKI afterwards. Pick an entered dog; its handler, numbers and entered height fill in. UKI only records a
measurement that has the dog's UKI number, and wants the height as a plain number (15.75, not 15 3/4").
"""

import re
from fractions import Fraction

from sqlalchemy import delete, insert, select, update

from app import db

KINDS = {'P': 'Permanent', 'T': 'Temporary'}


class MeasurementError(Exception):
    pass


def height_entered(height, class_type: str) -> str:
    """'16 inch' / '16 inch(s)', as UKI's Measurements sheet writes the height a dog was entered at."""
    return f"{height} inch" + ('(s)' if class_type == 'Select' else '') if height else ''


def parse_inches(text) -> float:
    """'15.75', '15 3/4', '15 3/4"', '15¾' → 15.75. Refuses anything that isn't a sensible dog height."""
    t = str(text or '').strip().lower().replace('"', '').replace('inches', '').replace('inch', '').replace('in', '')
    t = t.replace('¼', ' 1/4').replace('½', ' 1/2').replace('¾', ' 3/4').strip()
    m = re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s+(\d+)\s*/\s*(\d+))?', t)
    if not m:
        raise MeasurementError('Type the height as a number of inches, like 15.75 (or 15 3/4).')
    value = float(m.group(1))
    if m.group(2):
        if '.' in m.group(1) or int(m.group(3)) == 0:
            raise MeasurementError('Type the height as a number of inches, like 15.75 (or 15 3/4).')
        value += float(Fraction(int(m.group(2)), int(m.group(3))))
    if not 4 <= value <= 32:
        raise MeasurementError(f'{value:g} inches doesn’t look like a dog’s height. Check the number.')
    return round(value, 3)


def all_for(slug: str) -> list:
    with db.engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(db.measurements).where(db.measurements.c.show_slug == slug)
                                           .order_by(db.measurements.c.handler_name, db.measurements.c.dog_name)).mappings()]
    for r in rows:
        r['kind_label'] = KINDS.get(r['kind'], r['kind'])
    return rows


def get(slug: str, measurement_id: int):
    with db.engine().connect() as c:
        r = c.execute(select(db.measurements).where(db.measurements.c.show_slug == slug,
                                                    db.measurements.c.id == measurement_id)).mappings().first()
    return dict(r) if r else None


def last_official(slug: str) -> str:
    """The measuring official typed most recently at this show, to fill in the next one."""
    with db.engine().connect() as c:
        return c.execute(select(db.measurements.c.official).where(db.measurements.c.show_slug == slug,
                                                                  db.measurements.c.official != '')
                         .order_by(db.measurements.c.updated_at.desc())).scalar() or ''


def save(slug: str, dog: dict, form: dict, user: dict, measurement_id: int = None) -> int:
    """Add (or update) a measurement for one of the trial's dogs (a trial.known_dogs entry)."""
    kind = (form.get('kind') or '').strip().upper()[:1]
    if kind not in KINDS:
        raise MeasurementError('Pick Permanent or Temporary. UKI needs one or the other.')
    inches = parse_inches(form.get('inches'))
    age = str(form.get('age') or '').strip()
    if age and not re.fullmatch(r'\d{1,2}(\.\d+)?', age):
        raise MeasurementError('Type the dog’s age in years as a number, like 3.')
    values = {'dog_number': dog['dog_number'] or '', 'dog_name': dog['dog_name'],
              'handler_number': dog['handler_number'] or '',
              'handler_name': f"{dog['first_name']} {dog['last_name']}".strip(),
              'height_entered': height_entered(dog['height'], dog['class_type']), 'kind': kind, 'age': age,
              'inches': inches, 'organization': str(form.get('organization') or '').strip()[:120],
              'official': str(form.get('official') or '').strip()[:120], 'notes': str(form.get('notes') or '').strip()[:300],
              'updated_at': db.now(), 'updated_by': user.get('name', '')}
    with db.engine().begin() as c:
        if measurement_id:
            c.execute(update(db.measurements).where(db.measurements.c.show_slug == slug,
                                                    db.measurements.c.id == measurement_id).values(**values))
            new_id = measurement_id
        else:
            new_id = c.execute(insert(db.measurements).values(show_slug=slug, **values)).inserted_primary_key[0]
    db.log(user, 'measurement', slug, values['dog_number'] or values['dog_name'], inches=inches, kind=kind)
    return new_id


def remove(slug: str, measurement_id: int, user: dict):
    with db.engine().begin() as c:
        c.execute(delete(db.measurements).where(db.measurements.c.show_slug == slug,
                                                db.measurements.c.id == measurement_id))
    db.log(user, 'measurement removed', slug, str(measurement_id))
