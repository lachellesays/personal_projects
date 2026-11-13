"""The two files for UKI after a trial, in UKI's own layouts, plus the measurements that feed one of them."""
import io

import openpyxl
import pytest

from tests.conftest import SLUG
from tests.test_trial import SAT, SUN, built, trial_subs  # noqa: F401 - fixtures


def sheet_rows(ws, start=2):
    head = [c.value for c in ws[1]]
    return head, [dict(zip(head, r)) for r in ws.iter_rows(min_row=start, values_only=True) if any(v is not None for v in r)]


def test_parse_inches():
    from app.measurements import MeasurementError, parse_inches
    assert parse_inches('15.75') == 15.75 and parse_inches('15 3/4') == 15.75 and parse_inches('15 3/4"') == 15.75
    assert parse_inches('16½ inches') == 16.5 and parse_inches(' 22 ') == 22
    for bad in ('', 'tall', '15 3/0', '150', '2'):
        with pytest.raises(MeasurementError):
            parse_inches(bad)


def score_agility(c, token):
    from app import results
    c.post(f'/shows/{SLUG}/scoring/course', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'yards_bn': '160',
                                                   'yards_champ': '177', 'rate': 'fast', 'sct_extra': '0'})
    by = {r['dog_name']: r for r in results.entry_view(SLUG, SAT, 'Agility')['rows']}
    for dog, kw in (('Ace', {'time': '30.10', 'course_faults': '0', 'refusals': '0'}),
                    ('Bolt', {'time': '31.00', 'course_faults': '5', 'refusals': '0'}),
                    ('Cody', {'status': 'E'}), ('Dot', {'time': '40', 'course_faults': '0', 'refusals': '0'})):
        c.post(f'/shows/{SLUG}/scoring/score/{by[dog]["id"]}', data={'csrf': token, 'day': SAT, 'cls': 'Agility', **kw})
    c.post(f'/shows/{SLUG}/scoring/calculate', data={'csrf': token, 'day': SAT, 'cls': 'Agility'})


def test_trial_data_matches_ukis_layout(built):
    c, token = built
    score_agility(c, token)
    # Measure two dogs, one typed the way people write it
    page = c.get(f'/shows/{SLUG}/measurements').text
    assert 'Add a measurement' in page and 'Measurements' in page
    r = c.post(f'/shows/{SLUG}/measurements', data={'csrf': token, 'dog_key': '9004', 'kind': 'P', 'inches': '9 3/4',
                                                     'age': '3', 'official': 'Pat Judge'}, follow_redirects=True)
    assert 'Saved Dot&#39;s measurement.' in r.text and '9.75' in r.text
    c.post(f'/shows/{SLUG}/measurements', data={'csrf': token, 'dog_key': '9006', 'kind': 'T', 'inches': '21',
                                                 'organization': 'AKC', 'official': 'Pat Judge'})
    r = c.post(f'/shows/{SLUG}/measurements', data={'csrf': token, 'dog_key': '9004', 'kind': '', 'inches': '9'})
    assert r.status_code == 400 and 'Permanent or Temporary' in r.text

    resp = c.get(f'/shows/{SLUG}/results.xlsx')
    assert resp.headers['content-disposition'] == f'attachment; filename="TrialData_{SLUG}.xlsx"'
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ['Raw Results', 'Measurements', 'Masters Scoring', 'Masters Byes']

    head, rows = sheet_rows(wb['Raw Results'])
    assert head == ['Show ID', 'Level', 'Class', 'Height', 'Date', 'Member ID', 'Member Name', 'Dog ID', 'Dog Name', 'Breed',
                    'SCT', 'Faults', 'Games Points', 'Time', 'Place', 'Level Points']
    by = {r['Dog Name']: r for r in rows}
    ace, bolt, cody, dot = by['Ace'], by['Bolt'], by['Cody'], by['Dot']
    assert (ace['Show ID'], ace['Class'], ace['Height'], ace['Date']) == (7104, 'Agility ', '20 inch ', '12/5/2026')
    assert (ace['Member ID'], ace['Dog ID'], ace['Faults'], ace['Time'], ace['Place']) == (100, 9001, 0, 30.1, 1)
    assert dot['Height'] == '8 inch (s)' and bolt['Faults'] == 5
    assert (cody['Faults'], cody['Time'], cody['Place'], cody['Level Points']) == ('E', 0, 0, 0)
    assert all(isinstance(r['Faults'], (int, str)) for r in rows)
    assert wb['Raw Results']['E2'].number_format == 'mm/dd/yyyy'
    assert len({r['Class'] for r in rows}) == 1                     # only calculated classes go in

    head, meas = sheet_rows(wb['Measurements'])
    assert head[:5] == ['Handler Number', 'Handler Name', 'Dog Name', 'Dog Number', 'Height entered']
    dotm = next(m for m in meas if m['Dog Name'] == 'Dot')
    assert (dotm['Dog Number'], dotm['Height entered'], dotm['Temporary Measure or Permanent']) == (9004, '8 inch(s)', 'P')
    assert dotm['Measurement  in inches'] == 9.75 and dotm['Dogs Age'] == 3 and dotm['UKI Measuring Official Name'] == 'Pat Judge'
    figm = next(m for m in meas if m['Dog Name'] == 'Fig')
    assert figm['Temporary Measure or Permanent'] == 'T' and figm['Organization name if used for temporary measure '] == 'AKC'
    assert wb['Measurements']['B13'].value == 'Temporary or Permanent column must be completed'   # UKI's notes kept

    ms = wb['Masters Scoring']
    assert ms['A8'].value == 'Height' and ms['R8'].value == 'Prize $' and ms['G7'].value == 'Jumping'
    assert ms['I9'].value == '=SUM(G9:H9)' and ms['P198'].value == '=SUM(N198:O198)'


def test_files_for_uki_tab_and_paper_accounts(built):
    from app import uki_exports
    c, token = built
    score_agility(c, token)
    page = c.get(f'/shows/{SLUG}/scoring?view=uki&day={SAT}').text
    assert 'Files for UKI' in page and 'Not calculated yet' in page and 'Gamblers' in page
    counts = uki_exports.paper_counts(SLUG)
    assert counts == {'runs': 36, 'masters': 0}                     # 12 dogs × Agility, Gamblers, Jumping
    assert f'<b>{counts["runs"]}</b>' in page and 'value="info@levelupdogsports.com"' in page
    resp = c.post(f'/shows/{SLUG}/paper-accounts.xlsx', data={
        'csrf': token, 'paypal_email': 'club@example.com', 'unpaid_masters': '12',
        'mnum_0': '4321', 'mname_0': 'New Member', 'mamt_0': '40'})
    assert resp.headers['content-disposition'] == 'attachment; filename="PaperAccts_7104.xlsx"'
    ws = openpyxl.load_workbook(io.BytesIO(resp.content)).worksheets[0]
    assert ws['B1'].value == 'Test Trial, 2026-12-05, 7104'
    assert (ws['C4'].value, ws['C5'].value, ws['C6'].value, ws['C7'].value) == (15, 36, 35, 0)
    assert ws['J22'].value == 'club@example.com' and ws['D28'].value == 12
    assert (ws['G31'].value, ws['H31'].value, ws['J31'].value) == (4321, 'New Member', 40)
    assert ws['D23'].value == '=(C5*C23)' and ws['D34'].value == '=D32+D31'     # UKI's formulas untouched
    assert ws['C23'].value == 1.85 and ws['C32'].value == 0.035
    totals = uki_exports.paper_totals(counts, 12, [{'amount': 40}])
    assert totals['handling'] == round(36 * 1.85 + 12 + 40, 2)


def test_masters_entries_count_dogs_not_rounds(monkeypatch):
    """A Masters Series entry is both rounds: UKI's form counts dogs, not runs."""
    from app import trial, uki_exports
    run = lambda day, cls, dog: {'day': day, 'class_name': cls, 'dog_number': dog, 'handler_number': '1', 'dog_name': dog}
    runs = [run(SUN, 'Masters Jumping', '1'), run(SUN, 'Masters Agility', '1'), run(SUN, 'Masters Jumping', '2'),
            run(SUN, 'Masters Agility', '2'), run(SAT, 'Masters Agility', '1'), run(SAT, 'Agility', '1'),
            run(SUN, 'Speedstakes 2', '2')]
    monkeypatch.setattr(trial, 'ordered_for_export', lambda slug, day=None, include_out=False: runs)
    assert uki_exports.paper_counts('x') == {'runs': 2, 'masters': 3}
