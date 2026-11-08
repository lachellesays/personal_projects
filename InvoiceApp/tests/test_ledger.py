from datetime import date

import pytest

import db
import import_csv



@pytest.fixture
def eng(tmp_path):
    return db.get_engine(f'sqlite:///{tmp_path}/t.db')


def test_amount_rules():
    assert db.compute_amount('class', 'taught', hours=3, rate_cents=2500) == 7500
    for status in ('scheduled', 'subbed_out', 'cancelled', 'no_class'):
        assert db.compute_amount('class', status, hours=3, rate_cents=2500) == 0
    assert db.compute_amount('sub', hours=1.5, rate_cents=2500) == 3750
    assert db.compute_amount('trial', quantity=420, rate_cents=100) == 42000
    assert db.compute_amount('barter', value_cents=75000) == -75000
    assert db.compute_amount('payment', value_cents=-122000) == -122000      # sign doesn't matter on the way in
    assert db.compute_amount('credit_other', value_cents=5000) == 5000


def test_hours_from_label():
    assert db.hours_from_label('6:00 - 9:00') == 3
    assert db.hours_from_label('6:00 - 8:30') == 2.5
    assert db.hours_from_label('11:00 - 1:00') == 2
    assert db.hours_from_label('N/A') is None


def test_rate_is_kept_on_the_row(eng):
    rid = db.add_entry(eng, 'sub', entry_date=date(2026, 4, 22), description='Sub', hours=1, rate_cents=2500)
    db.save_settings(eng, {'hourly_rate_cents': 3000})
    db.update_entry(eng, rid, comments='rate went up later')
    assert db.get_entry(eng, rid)['amount_cents'] == 2500


def test_session_skips_push_later_weeks():
    classes, skipped = db.session_dates(date(2026, 11, 3), 6, [date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 22)])
    assert classes == [date(2026, 11, 3), date(2026, 11, 10), date(2026, 12, 1), date(2026, 12, 8),
                       date(2026, 12, 15), date(2026, 12, 29)]               # matches Session 8 in the sheet
    assert skipped == [date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 22)]


def test_create_session_and_mark_classes(eng):
    db.create_session(eng, 'Session 9', date(2027, 1, 5), 6, '6:00 - 9:00', 3, 2500, [date(2027, 1, 19)])
    rows = db.all_entries(eng)
    assert sum(r['status'] == 'scheduled' for r in rows) == 6
    assert [r['description'] for r in rows if r['status'] == 'no_class'] == ['Workshop Week - No Class']
    assert rows[0]['description'] == 'Tuesday Session 9 Wk 1'
    first, second = db.classes_to_mark(eng, date(2027, 1, 12))
    db.set_class_status(eng, first['id'], 'taught', hours=2)              # condensed to 2 hours
    db.set_class_status(eng, second['id'], 'subbed_out', comments='James Subbed')
    assert db.totals(eng)['owed'] == 5000
    assert db.classes_to_mark(eng, date(2027, 1, 12)) == []


def test_invoices_carry_the_balance_forward_and_lock(eng):
    a = db.add_entry(eng, 'sub', entry_date=date(2026, 1, 6), description='Sub', hours=3, rate_cents=2500)
    db.add_entry(eng, 'class', entry_date=date(2026, 1, 13), description='Still scheduled', status='scheduled',
                 hours=3, rate_cents=2500)
    db.add_entry(eng, 'payment', entry_date=None, description='Undated')
    inv1 = db.create_invoice(eng, date(2026, 1, 31), date(2026, 2, 1))
    d1 = db.invoice_detail(eng, inv1)
    assert [r['id'] for r in d1['rows']] == [a]                # scheduled and undated rows stay out
    assert (d1['previous'], d1['balance']) == (0, 7500)

    db.set_invoice_status(eng, inv1, 'sent')
    with pytest.raises(db.LockedError):
        db.update_entry(eng, a, hours=1)
    with pytest.raises(db.LockedError):
        db.delete_entry(eng, a)

    db.add_entry(eng, 'payment', entry_date=date(2026, 2, 10), description='Paid', value_cents=5000)
    db.add_entry(eng, 'barter', entry_date=date(2026, 2, 12), description='Wall jump', value_cents=1000)
    inv2 = db.create_invoice(eng, date(2026, 2, 28), date(2026, 3, 1))
    d2 = db.invoice_detail(eng, inv2)
    assert (d2['previous'], d2['credits'], d2['debits'], d2['balance']) == (7500, 0, -6000, 1500)
    assert d2['invoice']['number'] == 2

    db.void_invoice(eng, inv1)
    assert db.get_entry(eng, a)['invoice_id'] is None
    db.update_entry(eng, a, hours=1)                           # unlocked again
    assert db.invoice_detail(eng, inv2)['previous'] == 0


SAMPLE = """Instructor:,Lachelle Henderson,,Hourly Billed Rate:,25,,,,,Total Debits,-1045
,,,,,,,,,Total Credits,670
Session,Class/Event,Date,Time,Taught,Comments,Hours Taught,$$,Invoiced,Total Owed,-375
1,Tuesday Session 1 Wk 1,2026-01-06,6:00 - 9:00,Y,,3,75,TRUE,,
1,Tuesday Session 1 Wk 2,2026-01-13,6:00 - 9:00,N,Katie Subbed (Sick),0,0,TRUE,,
1,Tuesday Session 1 Wk 3,2026-01-20,,N,SNOW - Classes Cancelled,0,0,TRUE,,
2,Subbing for James ,2026-02-18,6:00 - 8:00,Y,,2,50,TRUE,,
UKI Trial,UKI Trial Secretary,2026-03-08,N/A,N/A,420 runs at $1 per run,N/A,420,TRUE,,
6,Tuesday Session 6 Wk 4,2026-08-25,6:00 - 8:00,Y,,2,50,FALSE,,
6,Tuesday Session 6 Wk 5,2026-08-25,6:00 - 8:00,Y,,2,50,FALSE,,
WW,Workshop Week - No Class,2026-09-08,,,,,0,FALSE,,
BARTER,Wall Jump,2026-09-15,,,,,-750,FALSE,,
8,Tuesday Session 8 Wk 1,2026-11-03,6:00 - 9:00,Y,Back to 3 hours starting this session,,0,FALSE,,
8,Tuesday Session 8 Wk 2,2026-11-10,,,,,0,FALSE,,
PAID,April Invoice,,,,,,-125,FALSE,,
BARTER,Trial 8/29-8/30,,,,"Course built for Hutton's entries",0,-104,FALSE,,
"""


def test_import_reconciles_and_flags(eng):
    p = import_csv.parse(SAMPLE)
    review = '\n'.join(p['review'])
    assert 'Session 8 Wk 1' in review and 'no hours' in review
    assert 'both dated 8/25/2026' in review
    assert "'April Invoice' has no date" in review
    assert 'used 8/30/2026' in review
    assert 'come in as Scheduled' in review
    assert "the sheet says" not in review                       # every row's amount matches the sheet

    t = import_csv.import_into(eng, p)
    assert (t['credits'], t['debits'], t['owed']) == (64500, -97900, -33400)
    kinds = {r['description']: (r['kind'], r['status']) for r in db.all_entries(eng)}
    assert kinds['Tuesday Session 1 Wk 2'] == ('class', 'subbed_out')
    assert kinds['Tuesday Session 1 Wk 3'] == ('class', 'cancelled')
    assert kinds['Subbing for James'] == ('sub', None)
    assert kinds['UKI Trial Secretary'] == ('trial', None)
    assert kinds['Workshop Week - No Class'] == ('class', 'no_class')
    # Invoiced=TRUE rows sit on one paid, imported invoice
    (inv,) = db.all_invoices(eng)
    assert inv['status'] == 'paid' and db.invoice_detail(eng, inv['id'])['balance'] == 54500
    # The scheduled class picks up the session's 3 hours
    (wk2,) = db.classes_to_mark(eng, date(2026, 12, 31))
    assert wk2['hours'] == 3
    with pytest.raises(ValueError):
        import_csv.import_into(eng, p)                           # never imports twice on top of itself


def test_real_spreadsheet_reconciles_if_present(eng):
    from pathlib import Path
    path = Path.home() / 'Downloads' / 'Level Up Debits & Credits - 2026.csv'
    if not path.exists():
        pytest.skip('spreadsheet not on this machine')
    p = import_csv.parse(path.read_text(encoding='utf-8-sig'))
    t = import_csv.import_into(eng, p)
    assert (t['credits'], t['debits'], t['owed']) == (287000, -224200, 62800)
    (inv,) = db.all_invoices(eng)
    assert db.invoice_detail(eng, inv['id'])['balance'] == 122000          # = the PAID April Invoice row


def test_pdf_builds(eng):
    from invoice_pdf import build_pdf
    db.add_entry(eng, 'trial', entry_date=date(2026, 3, 8), description='UKI Trial Secretary', quantity=420, rate_cents=100)
    db.add_entry(eng, 'class', entry_date=date(2026, 3, 10), description='Tuesday <Wk 3> & more', status='subbed_out',
                 comments='James Subbed', hours=3, rate_cents=2500)
    db.add_entry(eng, 'barter', entry_date=date(2026, 3, 12), description='Wall jump', value_cents=75000)
    inv = db.create_invoice(eng, date(2026, 3, 31), date(2026, 4, 1))
    pdf = build_pdf(db.invoice_detail(eng, inv), db.get_settings(eng))
    assert pdf.startswith(b'%PDF')
