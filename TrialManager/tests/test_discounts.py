"""Discounts (certificates): taken off what a handler owes on Entries, in the UKI workbook and in confirmations."""
import io

import openpyxl

from tests.conftest import SLUG


def balances(c):
    wb = openpyxl.load_workbook(io.BytesIO(_download(c)))
    ws = wb['Balances']
    head = [x.value for x in ws[1]]
    return {str(r[head.index('Member ID')]): r[head.index('OWES')] for r in ws.iter_rows(min_row=2, values_only=True)}


def _download(c):
    from tests.conftest import csrf_of
    token = csrf_of(c.get(f'/shows/{SLUG}/download').text)
    return c.post(f'/shows/{SLUG}/download', data={'csrf': token}).content


def test_discount_lowers_what_they_owe_everywhere(admin):
    from app import formatter, messages
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    ann = lambda: next(r for r in formatter.load(SLUG).rows if r['handler_number'] == '100' and r['first_of_handler'])
    assert ann()['balance'] == 15.0                                     # $45 of classes, paid $30
    r = c.post(f'/shows/{SLUG}/discount', data={'csrf': token, 'handler_number': '100', 'amount': '$10',
                                                 'note': 'Certificate #123'}, follow_redirects=True)
    assert 'Ann Smith: $10.00 discount saved. They still owe $5.00.' in r.text
    assert 'after $10.00 discount' in r.text and 'Certificate #123' in r.text
    assert (ann()['discount'], ann()['balance']) == (10.0, 5.0)
    assert balances(c)['100'] == 5.0                                    # UKI workbook's Balances sheet agrees
    d = formatter.load(SLUG)
    h = next(x for x in messages.confirmation_content(d) if x['key'] == '100')
    text = messages.render('confirmation', d.show, h)['text']
    assert 'Discount (Certificate #123): -$10.00' in text and 'Balance due: $5.00' in text
    # Remove it
    c.post(f'/shows/{SLUG}/discount', data={'csrf': token, 'handler_number': '100', 'amount': '10', 'remove': '1'})
    assert (ann()['discount'], ann()['balance']) == (0.0, 15.0)


def test_discount_covers_at_most_the_entry_fees(admin):
    from app import formatter
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    row = lambda hid: next(x for x in formatter.load(SLUG).rows if x['handler_number'] == hid and x['first_of_handler'])
    # Ann: $45 of classes, paid $30. A $50 certificate only covers the $45, leaving her $30 cash as a credit
    r = c.post(f'/shows/{SLUG}/discount', data={'csrf': token, 'handler_number': '100', 'amount': '50'},
               follow_redirects=True)
    assert 'Only $45.00 counts' in r.text and '$30.00 credit' in r.text
    assert (row('100')['discount'], row('100')['balance']) == (45.0, -30.0)
    assert 'Ann Smith (100) has a credit of $30.00' in c.get(f'/shows/{SLUG}/review?t=warnings').text


def test_bad_discount_is_refused(admin):
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    r = c.post(f'/shows/{SLUG}/discount', data={'csrf': token, 'handler_number': '100', 'amount': 'ten'})
    assert r.status_code == 400 and 'Type the discount as a number' in r.text
