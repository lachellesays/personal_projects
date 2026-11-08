import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import import_csv
import main

SAMPLE = (Path(__file__).parent / 'test_ledger.py').read_text().split('SAMPLE = """', 1)[1].split('"""', 1)[0]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PIN', '2468')
    main.ENGINE = db.get_engine(f'sqlite:///{tmp_path}/w.db')
    main._fails.clear()
    return TestClient(main.app)


def csrf_of(c, path='/login'):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text)[1]


def signed_in(c):
    c.post('/login', data={'csrf': csrf_of(c), 'pin': '2468'})
    return re.search(r'"X-CSRF-Token": "([^"]+)"', c.get('/').text)[1]


def test_pages_need_the_pin(client):
    r = client.get('/ledger', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/login'
    assert client.get('/health').json() == {'ok': True}
    bad = client.post('/login', data={'csrf': csrf_of(client), 'pin': '0000'})
    assert bad.status_code == 401 and 'Incorrect PIN' in bad.text
    signed_in(client)
    assert client.get('/ledger').status_code == 200


def test_wrong_pins_lock_out(client):
    token = csrf_of(client)
    for _ in range(main.MAX_FAILS):
        client.post('/login', data={'csrf': token, 'pin': '1'})
    r = client.post('/login', data={'csrf': token, 'pin': '2468'})       # right PIN, but locked out
    assert 'Too many wrong PINs' in r.text


def test_changes_need_the_csrf_token(client):
    signed_in(client)
    rid = db.add_entry(main.ENGINE, 'sub', entry_date=date(2026, 4, 22), description='Sub', hours=1, rate_cents=2500)
    r = client.post(f'/entries/{rid}/cell', data={'field': 'qty', 'value': '3'})
    assert r.status_code == 403 and db.get_entry(main.ENGINE, rid)['hours'] == 1


def test_every_page_renders(client):
    signed_in(client)
    import_csv.import_into(main.ENGINE, import_csv.parse(SAMPLE))
    for path in ('/', '/log', '/log?kind=trial', '/log?kind=barter', '/ledger', '/ledger?kind=class&inv=no',
                 '/sessions', '/invoices', '/settings', '/backup.csv'):
        r = client.get(path)
        assert r.status_code == 200, path
    inv = db.all_invoices(main.ENGINE)[0]
    pdf = client.get(f"/invoices/{inv['id']}/pdf")
    assert pdf.headers['content-type'] == 'application/pdf' and pdf.content.startswith(b'%PDF')


def test_edit_cells_in_the_ledger(client):
    token = signed_in(client)
    h = {'X-CSRF-Token': token, 'HX-Request': 'true'}
    rid = db.add_entry(main.ENGINE, 'class', entry_date=date(2026, 11, 3), description='Tuesday Session 8 Wk 1',
                       status='taught', hours=0, rate_cents=2500)
    r = client.post(f'/entries/{rid}/cell', data={'field': 'qty', 'value': '3'}, headers=h)
    assert f'<span id="amt-{rid}" hx-swap-oob="true">$75</span>' in r.text and 'id="totals"' in r.text  # amount + totals
    assert f'id="row-{rid}"' not in r.text                                                # inputs left alone (Tab keeps working)
    client.post(f'/entries/{rid}/cell', data={'field': 'comments', 'value': 'Back to 3 hours'}, headers=h)
    client.post(f'/entries/{rid}/cell', data={'field': 'entry_date', 'value': '2026-11-04'}, headers=h)
    row = db.get_entry(main.ENGINE, rid)
    assert (row['amount_cents'], row['comments'], row['entry_date']) == (7500, 'Back to 3 hours', date(2026, 11, 4))

    r = client.post(f'/entries/{rid}/cell', data={'field': 'status', 'value': 'subbed_out'}, headers=h)
    assert f'id="row-{rid}"' in r.text and r.headers['HX-Retarget'] == f'#row-{rid}'          # row redrawn in place
    assert db.get_entry(main.ENGINE, rid)['amount_cents'] == 0

    r = client.post(f'/entries/{rid}/cell', data={'field': 'qty', 'value': 'abc'}, headers=h)
    assert 'Enter a number' in r.text

    # Switching type: a class becomes barter worth what the class was worth
    r = client.post(f'/entries/{rid}/cell', data={'field': 'kind', 'value': 'payment'}, headers=h)
    assert db.get_entry(main.ENGINE, rid)['kind'] == 'payment'
    client.post(f'/entries/{rid}/cell', data={'field': 'value', 'value': '120'}, headers=h)
    assert db.get_entry(main.ENGINE, rid)['amount_cents'] == -12000


def test_locked_rows_cannot_be_edited_or_deleted(client):
    token = signed_in(client)
    h = {'X-CSRF-Token': token}
    rid = db.add_entry(main.ENGINE, 'sub', entry_date=date(2026, 4, 22), description='Sub', hours=1, rate_cents=2500)
    inv = db.create_invoice(main.ENGINE, date(2026, 4, 30), date(2026, 5, 1))
    db.set_invoice_status(main.ENGINE, inv, 'sent')
    r = client.post(f'/entries/{rid}/cell', data={'field': 'qty', 'value': '5'}, headers=h)
    assert 'Void the invoice' in r.text and db.get_entry(main.ENGINE, rid)['hours'] == 1
    r = client.post(f'/entries/{rid}/delete', headers=h)
    assert 'Void the invoice' in r.text and r.headers['HX-Reswap'] == 'none' and db.get_entry(main.ENGINE, rid)
    assert '🔒 #1' in client.get('/ledger').text


def test_add_and_delete_rows(client):
    token = signed_in(client)
    h = {'X-CSRF-Token': token}
    r = client.post('/entries', data={'kind': 'barter', 'entry_date': '2026-09-15', 'description': 'Wall Jump',
                                      'value': '750'}, headers=h)
    assert 'Wall Jump' in r.text and '-$750' in r.text
    (row,) = db.all_entries(main.ENGINE)
    assert row['amount_cents'] == -75000
    r = client.post('/entries', data={'kind': 'trial', 'entry_date': '2026-03-08', 'description': 'UKI', 'qty': '420'},
                    headers=h)
    assert db.totals(main.ENGINE)['owed'] == 42000 - 75000
    r = client.post(f"/entries/{row['id']}/delete", headers=h)
    assert db.totals(main.ENGINE)["owed"] == 42000 and db.get_entry(main.ENGINE, row["id"]) is None


def test_home_buttons(client):
    token = signed_in(client)
    db.create_session(main.ENGINE, 'Session 9', date.today(), 2, '6:00 - 9:00', 3, 2500)
    first, = [r for r in db.classes_to_mark(main.ENGINE, date.today())]
    r = client.post(f"/classes/{first['id']}/taught", data={'hours': '2', 'who': '', 'why': ''},
                    headers={'X-CSRF-Token': token})
    assert 'taught, 2 h = $50' in r.text and db.get_entry(main.ENGINE, first['id'])['amount_cents'] == 5000
    nxt = db.add_entry(main.ENGINE, 'class', entry_date=date.today(), description='Extra', status='scheduled',
                       hours=3, rate_cents=2500)
    client.post(f'/classes/{nxt}/subbed_out', data={'hours': '3', 'who': 'James', 'why': 'ignored'},
                headers={'X-CSRF-Token': token})
    assert db.get_entry(main.ENGINE, nxt)['comments'] == 'James Subbed'


def test_import_through_the_web(client):
    token = signed_in(client)
    r = client.post('/import/preview', data={'csrf': token},
                    files={'file': ('sheet.csv', SAMPLE.encode(), 'text/csv')})
    assert 'Worth a look' in r.text and 'both dated' in r.text
    client.post('/import', data={'csrf': token, 'text': SAMPLE})
    assert db.totals(main.ENGINE)['owed'] == -33400
    client.post('/import', data={'csrf': token, 'text': SAMPLE})               # not over the top without "replace"
    assert len(db.all_entries(main.ENGINE)) == 13
