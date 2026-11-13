import io
import re

import pytest

from tests.conftest import SLUG, csrf_of


def test_cleaning():
    from app import people
    assert people.number('22,247') == '22247' and people.number(22247.0) == '22247' and people.number('H100') == '100'
    assert people.number('') == '' and people.number(None) == ''
    assert people.level('Champion') == 'Champ' and people.level('novice') == 'Novice' and people.level('x') == ''
    assert people.height('20 inch (s)') == (20, 'Select') and people.height('16 inch ') == (16, 'Regular')
    assert people.height('12 regular') == (12, 'Regular') and people.jotform_height(20, 'Select') == '20 select'
    assert people.jotform_level('Champ') == 'Champion'


def test_newer_details_win_older_only_fill_blanks(app_env):
    from app import db, people
    e = {'handler_number': '100', 'first_name': 'Ann', 'last_name': 'Smith', 'email': 'new@example.com', 'phone': '',
         'dog_number': '500', 'dog_name': 'Rex', 'dog_breed': 'Mix', 'jump_height': '20 regular',
         'international_level': 'Senior', 'speedstakes_level': ''}
    people.add_from_entries([e], '2026-11-01')
    # An older export: different email and a phone. The phone fills a blank, the email doesn't win.
    with db.engine().begin() as c:
        people.save_handler(c, {'handler_number': '100', 'email': 'old@example.com', 'phone': '5551234'}, '2026-03-01', 'uki_manager')
    (h,) = people.search('100')
    assert (h['email'], h['phone'], h['info_date']) == ('new@example.com', '5551234', '2026-11-01')
    assert h['dogs'][0]['level'] == 'Senior' and h['dogs'][0]['speedstakes_level'] == 'Senior'
    # A newer entry: moved up to Champ
    people.add_from_entries([{**e, 'international_level': 'Champion', 'email': ''}], '2026-12-05')
    (h,) = people.search('Rex')
    assert h['dogs'][0]['level'] == 'Champ' and h['email'] == 'new@example.com'   # a blank never wipes a value
    assert people.counts() == {'handlers': 1, 'dogs': 1}


def test_import_uki_manager_handlers(app_env):
    from app import people
    text = ('"4692";"Abby";"Peach";"2013 Gore Drive";"Haymarket";"Va";"20169";"abby@example.com";"";"(703) 889-0141"\n'
            '"14849";"Abigail";"Bowman";"1205 Andover Rd";"Henrico";"VA";"23229";"bowman@example.com";"";"8043056699"\n')
    assert people.import_uki_handlers(text, '2026-03-01') == 2
    (h,) = people.search('Peach')
    assert (h['handler_number'], h['state'], h['phone'], h['city']) == ('4692', 'VA', '7038890141', 'Haymarket')
    assert people.import_uki_handlers(text, '2026-03-01') == 2 and people.counts()['handlers'] == 2   # no duplicates


def results_workbook(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Raw Results'
    ws.append(['Show ID', 'Level', 'Class', 'Height', 'Date', 'Member ID', 'Member Name', 'Dog ID', 'Dog Name', 'Breed'])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_results(app_env):
    from app import people
    data = results_workbook([
        [6898, 'Novice', 'Agility ', '16 inch ', '3/7/2026', 22247, 'Adriana Johnson-Sol', 38009, 'Mabel', 'English Cocker Spaniel'],
        [6898, 'Beginner', 'Speedstakes ', '16 inch ', '3/8/2026', 22247, 'Adriana Johnson-Sol', 38009, 'Mabel', 'English Cocker Spaniel'],
        [6898, 'Champ', 'Masters Agility ', '16 inch ', '3/8/2026', 22247, 'Adriana Johnson-Sol', 38009, 'Mabel', 'English Cocker Spaniel'],
        [6908, 'Senior', 'Jumping ', '12 inch (s)', '5/2/2026', 22247, 'Adriana Johnson-Sol', 38009, 'Mabel', 'English Cocker Spaniel'],
        [6908, 'Novice', 'Agility ', '20 inch ', '5/2/2026', '14,849', 'Abigail Bowman', '40,001', 'Pip', 'Border Collie'],
    ])
    assert people.import_results(data) == {'dogs': 2, 'handlers': 2}
    (h,) = people.search('38009')
    (mabel,) = h['dogs']
    # Latest row wins for height; Masters rows don't set a level; Speedstakes level comes from Speedstakes rows
    assert (mabel['height'], mabel['class_type'], mabel['level'], mabel['speedstakes_level']) == (12, 'Select', 'Senior', 'Beginner')
    assert (h['first_name'], h['last_name'], h['handler_number']) == ('Adriana', 'Johnson-Sol', '22247')
    with pytest.raises(people.PeopleError):
        people.import_results(b'not a workbook')


def test_refresh_fills_the_directory(admin):
    from app import people
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    assert people.counts() == {'handlers': 4, 'dogs': 6}
    (ann,) = people.search('Ann Smith')
    assert ann['handler_number'] == '100' and {d['dog_name'] for d in ann['dogs']} == {'Rex', 'Bea'}
    assert ann['info_date'] == '2026-12-05' and ann['state'] == 'VA'
    page = c.get('/directory?q=Rex')
    assert 'Smith, Ann' in page.text and '#500' in page.text


def paper_form(token, handler, dogs, amount='', method='Check', note=''):
    data = {'csrf': token, 'dog_count': str(len(dogs)), 'amount': amount, 'method': method, 'note': note, **handler}
    for i, d in enumerate(dogs):
        for k in ('dog_id', 'dog_number', 'dog_name', 'breed', 'height', 'class_type', 'level', 'speedstakes_level'):
            data[f'{k}_{i}'] = str(d.get(k, ''))
        if d.get('enter'):
            data[f'enter_{i}'] = '1'
    pairs = list(data.items())
    for i, d in enumerate(dogs):
        pairs += [(f'sat_{i}', c) for c in d.get('sat', [])] + [(f'sun_{i}', c) for c in d.get('sun', [])]
    return pairs


def post(c, url, pairs, **kw):
    """Form post with repeated fields (checkbox lists), which a plain dict can't hold."""
    from urllib.parse import urlencode
    return c.post(url, content=urlencode(pairs), headers={'Content-Type': 'application/x-www-form-urlencoded'}, **kw)


def test_paper_entry_end_to_end(admin):
    from app import formatter, people, trial
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    # Look Bob up and open the prefilled form
    r = c.get(f'/shows/{SLUG}/paper?q=200', headers={'HX-Request': 'true', 'HX-Target': 'paper-results'})
    hid = re.search(r'/paper/new\?h=(\d+)', r.text)[1]
    form = c.get(f'/shows/{SLUG}/paper/new?h={hid}').text
    assert 'value="Bob"' in form and 'value="Max"' in form and 'ALREADY ENTERED IN THIS SHOW' in form
    assert 'value="Open Gamblers (all levels combined)"' not in form or 'Gamblers' in form   # class wording from JotForm
    # Bob mails in a form for a new dog, Nova, plus Max in one more class
    handler = {'handler_number': '200', 'first_name': 'Bob', 'last_name': 'Jones', 'email': 'bob@example.com',
               'phone': '8045550000', 'addr_line1': '5 Elm St', 'addr_line2': '', 'city': 'Richmond', 'state': 'VA',
               'postal': '23220'}
    dogs = [{'dog_number': '', 'dog_name': 'Nova', 'breed': 'Sheltie', 'height': 12, 'class_type': 'Select', 'level': 'Beginner',
             'enter': True, 'sat': ['Agility 1'], 'sun': ['Snooker']},
            {'dog_number': '', 'dog_name': '', 'enter': False}]
    r = post(c, f'/shows/{SLUG}/paper', paper_form(token, handler, dogs, amount='30', note='Check 1042'),
               follow_redirects=False)
    assert r.status_code == 303
    page = r.headers['location']
    assert page.endswith('/entries?f=paper')
    page = c.get(page).text
    assert 'Entered Bob Jones: Nova. Fees $30.00, paid $30.00.' in page and 'Nova' in page and 'PAPER' in page

    d = formatter.load(SLUG)
    (paper,) = [e for e in d.entries if e['submission_id'].startswith('paper-')]
    assert (paper['jump_height'], paper['international_level'], paper['amount_paid']) == ('12 select', 'Beginner', 30.0)
    assert paper['saturday_classes'] == ['Agility 1'] and paper['sunday_classes'] == ['Snooker']
    assert 'PAPER' in c.get(f'/shows/{SLUG}/entries').text
    # The directory learned Bob's phone and his new dog
    (bob,) = people.search('Jones')
    assert bob['phone'] == '8045550000' and {g['dog_name'] for g in bob['dogs']} == {'Max', 'Nova'}
    # The workbook includes it
    wb = c.post(f'/shows/{SLUG}/download', data={'csrf': token})
    import openpyxl
    raw = openpyxl.load_workbook(io.BytesIO(wb.content))['Raw Results']
    assert sum(1 for row in raw.iter_rows(values_only=True) if 'Nova' in [str(v) for v in row]) == 2
    # And the run order builds with its runs
    c.post(f'/shows/{SLUG}/runorder/build', data={'csrf': token})
    assert {r['class_name'] for r in trial.all_runs(SLUG) if r['dog_name'] == 'Nova'} == {'Agility', 'Snooker'}


def test_paper_entry_problems_keep_the_form(admin):
    c, token = admin
    handler = {'first_name': 'Zoe', 'last_name': ''}
    dogs = [{'dog_name': 'Pip', 'height': 16, 'level': 'Novice', 'enter': True, 'sat': ['Agility 1']}]
    r = post(c, f'/shows/{SLUG}/paper', paper_form(token, handler, dogs))
    assert r.status_code == 400 and "first and last name" in r.text and 'value="Pip"' in r.text
    handler['last_name'] = 'Ray'
    dogs[0]['sat'] = []
    r = post(c, f'/shows/{SLUG}/paper', paper_form(token, handler, dogs))
    assert 'Pick at least one class for Pip' in r.text
    dogs[0]['sat'] = ['Agility 1']
    r = post(c, f'/shows/{SLUG}/paper', paper_form(token, handler, dogs, amount='abc'))
    assert 'amount paid as a number' in r.text


def test_edit_and_delete_a_paper_entry(admin):
    from app import formatter, people
    c, token = admin
    handler = {'first_name': 'Zoe', 'last_name': 'Ray', 'handler_number': ''}
    dogs = [{'dog_name': 'Pip', 'height': 16, 'level': 'Novice', 'enter': True, 'sat': ['Agility 1']}]
    post(c, f'/shows/{SLUG}/paper', paper_form(token, handler, dogs, amount='15'))
    (row,) = people.paper_rows(SLUG)
    dogs[0].update(sun=['Snooker'], level='Senior')
    post(c, f"/shows/{SLUG}/paper/{row['id']}", paper_form(token, handler, dogs, amount='30', method='Cash'))
    (e,) = [x for x in formatter.load(SLUG).entries if x['submission_id'].startswith('paper-')]
    assert e['sunday_classes'] == ['Snooker'] and e['international_level'] == 'Senior' and e['amount_paid'] == 30.0
    c.post(f"/shows/{SLUG}/paper/{row['id']}", data={'csrf': token, 'delete': '1'})
    assert people.paper_rows(SLUG) == []
    # Zoe stays in the directory for next time, even without a UKI #
    (zoe,) = people.search('Zoe')
    assert zoe['handler_number'] == '' and zoe['dogs'][0]['dog_name'] == 'Pip'


def test_directory_edits_and_day_of_lookup(admin):
    from app import people
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    (ann,) = people.search('Ann')
    r = c.post(f"/directory/h/{ann['id']}", data={'csrf': token, 'handler_number': '200', 'first_name': 'Ann',
                                                  'last_name': 'Smith'})
    assert r.status_code == 400 and 'already belongs to someone else' in r.text
    c.post(f"/directory/h/{ann['id']}", data={'csrf': token, 'handler_number': '100', 'first_name': 'Ann',
                                              'last_name': 'Smith-Hart', 'phone': '555'})
    assert people.get_handler(ann['id'])['last_name'] == 'Smith-Hart'
    rex = next(g for g in ann['dogs'] if g['dog_name'] == 'Rex')
    c.post(f"/directory/h/{ann['id']}/dog", data={'csrf': token, 'dog_id': str(rex['id']), 'dog_name': 'Rex',
                                                  'dog_number': '500', 'height': '16', 'class_type': 'Select', 'level': 'Senior'})
    rex = next(g for g in people.get_handler(ann['id'])['dogs'] if g['dog_name'] == 'Rex')
    assert (rex['height'], rex['class_type'], rex['level'], rex['speedstakes_level']) == (16, 'Select', 'Senior', 'Senior')
    look = c.get(f'/shows/{SLUG}/dayof/lookup?q=Rex').text
    assert 'data-fill=' in look and '"dog_name": "Rex"' in look.replace('&#34;', '"') and '"height": "16"' in look


def test_import_page(admin):
    from app import people
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    r = c.post('/directory/import/shows', data={'csrf': token})
    assert 'Read 6 entries from 1 show(s)' in r.text
    r = c.post('/directory/import/uki', data={'csrf': token, 'info_date': '2026-03-01'},
               files={'file': ('grid_handlers_1.csv', b'"999";"New";"Person";"1 Rd";"Town";"VA";"22222";"n@example.com";"";"1"\n', 'text/csv')})
    page = r.text
    assert 'Read 1 handlers from UKI Manager' in page and '5 handlers (+1)' in page


def test_paper_classes_follow_the_running_order():
    from app import people
    from app.core import Show
    show = Show(name='T', show_id=1, form_id='f', saturday_date='2026-12-12', sunday_date='2026-12-13')
    entries = [{'saturday_classes': ['Snooker', 'Agility 1', 'Open Gamblers (all levels combined)', 'Jumping 2'],
                'sunday_classes': ['Masters Series', 'Open Speedstakes 1 (all levels combined)', 'Agility']}]
    setup = {'saturday': ['Gamblers', 'Agility', 'Jumping 2', 'Snooker'],
             'sunday': ['Speedstakes', 'Masters Jumping', 'Agility', 'Masters Agility']}
    opts = people.class_options(show, entries, setup)
    assert [people.class_label(c) for c in opts['saturday']] == ['Gamblers', 'Agility', 'Jumping 2', 'Snooker']
    assert [people.class_label(c) for c in opts['sunday']] == ['Speedstakes', 'Masters Series', 'Agility']
