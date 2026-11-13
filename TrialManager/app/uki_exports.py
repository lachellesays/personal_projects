"""
uki_exports.py

The two files that go to UKI after a trial, filled into UKI's own workbooks (app/uki_templates/, made from the
November 2026 files with every competitor value removed) so headers, instructions, styles and formulas match:

  Trial data   TrialData_<slug>.xlsx    Raw Results, Measurements, Masters Scoring, Masters Byes
  Paper accts  PaperAccts_<show id>.xlsx  runs and Masters entries that came straight to the club rather than
               through UKI (for us that's every entry: JotForm, paper and day-of), for UKI's handling fees
"""

import io
from datetime import date
from pathlib import Path

import openpyxl

from app import measurements, trial

TEMPLATES = Path(__file__).parent / 'uki_templates'
MASTERS_FORMULA_ROWS = 198          # UKI's sheet has the total formulas filled down this far


def _int(v):
    return int(v) if str(v or '').isdigit() else (v or '')


def uki_class(class_name: str) -> str:
    """UKI's spelling: 'Agility ' (trailing space) for a first class, 'Agility 2' for a numbered one."""
    return class_name if class_name[-1:].isdigit() else class_name + ' '


def uki_height(height, class_type: str) -> str:
    """Raw Results heights: '16 inch ' and '16 inch (s)'."""
    return f"{height} inch " + ('(s)' if class_type == 'Select' else '')


def masters_height(height, class_type: str) -> str:
    """Masters sheets: '16 inch' and '16 inch(s)'."""
    return f"{height} inch" + ('(s)' if class_type == 'Select' else '')


def _num(v):
    """Whole numbers as ints (0 not 0.0), others rounded to 2 places, as UKI's sheet stores them."""
    if v is None:
        return 0
    v = round(float(v), 2)
    return int(v) if v == int(v) else v


# ── Trial data ──────────────────────────────────────────────────────────────

def readiness(slug: str, show) -> dict:
    """What's missing before the trial data file is complete: classes not calculated, dogs with no score."""
    from app import results
    waiting, unscored = [], 0
    for day in (str(show.saturday_date), str(show.sunday_date)):
        for s in trial.class_list(slug, day):
            ev = results.entry_view(slug, day, s['class_name'])
            if not ev['rows']:
                continue
            if not ev['calculated']:
                waiting.append(f"{s['class_name']} ({date.fromisoformat(day):%a})")
            unscored += sum(1 for r in ev['rows'] if not results.has_score(r['score']))
    m = measurements.all_for(slug)
    return {'waiting': waiting, 'unscored': unscored, 'measurements': len(m),
            'measurements_no_number': sum(1 for x in m if not x['dog_number'])}


def raw_results_rows(slug: str, show) -> list:
    from app import results
    rows = []
    for day in (str(show.saturday_date), str(show.sunday_date)):
        when = date.fromisoformat(day)
        for s in trial.class_list(slug, day):
            if not s['calculated_at']:
                continue
            for r in results.class_runs(slug, day, s['class_name']):
                sc = r['score'] or {}
                res = sc.get('result') or {}
                status = res.get('status') or ''
                if status:
                    faults = status
                elif res.get('scored'):
                    faults = int(sc.get('course_faults') or 0) + int(sc.get('refusals') or 0)
                else:
                    faults = 'ABS'          # never scored: UKI has no blank, and the dog didn't run
                row = [_int(show.show_id), r['level'], uki_class(s['class_name']), uki_height(r['height'], r['class_type']),
                       f"{when.month}/{when.day}/{when.year}", _int(r['handler_number']),
                       f"{r['first_name']} {r['last_name']}".strip(), _int(r['dog_number']), r['dog_name'], r['breed'],
                       _num(res.get('sct')), faults, _num(res.get('points')) if res.get('points') else 0,
                       0 if status or not res.get('time') else _num(res['time']), res.get('place') or 0,
                       res.get('level_points') or 0]
                rows.append(((day, row[2], row[1], row[3], r['seq'] or 0), row))
    return [row for _, row in sorted(rows, key=lambda x: x[0])]


def trial_data_workbook(slug: str, show) -> bytes:
    from app import results
    wb = openpyxl.load_workbook(TEMPLATES / 'TrialData_template.xlsx')

    raw = wb['Raw Results']
    for row in raw_results_rows(slug, show):
        raw.append(row)
        raw.cell(raw.max_row, 5).number_format = 'mm/dd/yyyy'

    meas = wb['Measurements']
    items = measurements.all_for(slug)
    extra = max(0, len(items) - 11)            # UKI's notes start at row 13; push them down if needed
    if extra:
        meas.insert_rows(13, extra)
    for i, m in enumerate(items, start=2):
        age = m['age']
        values = [_int(m['handler_number']), m['handler_name'], m['dog_name'], _int(m['dog_number']), m['height_entered'],
                  m['kind'], (int(age) if age.isdigit() else float(age)) if age else None, m['inches'],
                  m['organization'] or None, m['official'] or None, m['notes'] or None]
        for col, v in enumerate(values, start=1):
            meas.cell(i, col, v)

    ms, byes = wb['Masters Scoring'], wb['Masters Byes']
    row_n = 9
    for day in (str(show.saturday_date), str(show.sunday_date)):
        mv = results.masters_view(slug, day)
        if not mv.get('available'):
            continue
        for g in sorted(mv['groups'], key=lambda g: (g['height'], g['class_type'] == 'Select')):
            height = masters_height(g['height'], g['class_type'])
            for d in g['rows']:
                j = results._round_cells(d.get('jumping'), d.get('jumping_sct'))
                a = results._round_cells(d.get('agility'), d.get('agility_sct'))
                values = [height, _int(d['handler_number']), d['handler'], _int(d['dog_number']), d['dog_name'],
                          _num(j[0]), _num(j[1]), _num(j[2]), None, _num(a[0]), _num(a[1]), _num(a[2]), None,
                          None, None, None, d['place'] or 0, _num(d['prize'] or 0)]
                for col, v in enumerate(values, start=1):
                    if v is not None:
                        ms.cell(row_n, col, v)
                _masters_formulas(ms, row_n)
                row_n += 1
                if d['bye']:
                    byes.append([height, _int(d['handler_number']), d['handler'], _int(d['dog_number']), d['dog_name']])
    for r in range(row_n, MASTERS_FORMULA_ROWS + 1):
        _masters_formulas(ms, r)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _masters_formulas(ws, r: int):
    ws[f'I{r}'] = f'=SUM(G{r}:H{r})'
    ws[f'M{r}'] = f'=SUM(K{r}:L{r})'
    ws[f'N{r}'] = f'=SUM(G{r}+K{r})'
    ws[f'O{r}'] = f'=SUM(H{r}+L{r})'
    ws[f'P{r}'] = f'=SUM(N{r}:O{r})'


# ── Paper accounts ──────────────────────────────────────────────────────────

PAYPAL_EMAIL = 'info@levelupdogsports.com'   # where UKI sends its PayPal request for the handling fees
RUN_PRICE = 15.00          # UKI's form: one price per regular run
MASTERS_PRICE = 35.00      # a Masters Series entry (both rounds)
HANDLING = {'run': 1.85, 'masters': 5.00}
BANKING = 0.035


def paper_counts(slug: str) -> dict:
    """Regular runs and Masters Series entries that came straight to the club (every run still in the run order,
    so pre-trial scratches don't count; dogs absent on the day do, as in UKI Manager's figures)."""
    runs = trial.ordered_for_export(slug)
    regular = sum(1 for r in runs if not r['class_name'].lower().startswith('masters'))
    masters = set()
    for r in runs:
        if r['class_name'].lower().startswith('masters'):
            masters.add((r['day'], r['dog_number'] or f"{r['handler_number']}:{r['dog_name'].lower()}"))
    return {'runs': regular, 'masters': len(masters)}


def paper_totals(counts: dict, unpaid_masters: float = 0.0, memberships: list = ()) -> dict:
    """The form's own arithmetic, to show on the page before downloading."""
    income = counts['runs'] * RUN_PRICE + counts['masters'] * MASTERS_PRICE
    registration = sum(m['amount'] for m in memberships)
    handling = counts['runs'] * HANDLING['run'] + counts['masters'] * HANDLING['masters'] + unpaid_masters + registration
    return {'income': round(income, 2), 'handling': round(handling, 2), 'banking': round(handling * BANKING, 2),
            'due': round(handling * (1 + BANKING), 2)}


def paper_accounts_workbook(slug: str, show, paypal_email: str = '', unpaid_masters: float = 0.0,
                            memberships: list = ()) -> bytes:
    """memberships: up to 3 {'number', 'name', 'amount'} for UKI memberships paid at the show."""
    counts = paper_counts(slug)
    wb = openpyxl.load_workbook(TEMPLATES / 'PaperAccts_template.xlsx')
    ws = wb.worksheets[0]
    sat = show.saturday_date if isinstance(show.saturday_date, date) else date.fromisoformat(str(show.saturday_date))
    ws['B1'] = f"{show.name}, {sat.isoformat()}, {show.show_id}"
    ws['C4'] = RUN_PRICE
    ws['C5'] = counts['runs']
    ws['C6'] = MASTERS_PRICE
    ws['C7'] = counts['masters']
    ws['D28'] = _num(unpaid_masters)
    if paypal_email:
        ws['J22'] = paypal_email
    for i, m in enumerate(list(memberships)[:3]):
        r = 31 + i
        ws[f'G{r}'] = _int(m.get('number'))
        ws[f'H{r}'] = m.get('name') or None
        ws[f'J{r}'] = _num(m.get('amount') or 0)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
