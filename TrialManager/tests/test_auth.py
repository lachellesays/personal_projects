from datetime import timedelta

from sqlalchemy import update

from tests.conftest import ADMIN_EMAIL, PASSWORD, csrf_of, new_client, sign_in


def test_everything_requires_sign_in(client):
    for path in ('/shows', '/people', '/account', '/shows/x/entries', '/shows/x/download'):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers['location'].startswith('/login') or r.headers['location'] == '/setup', path


def test_there_is_no_sign_up(client):
    for path in ('/signup', '/register', '/users/new'):
        assert client.get(path).status_code in (303, 404, 405) and 'Create account' not in client.get(path).text


def test_first_admin_only_for_bootstrap_email_and_only_once(client):
    assert client.get('/login', follow_redirects=False).headers['location'] == '/setup'
    page = client.get('/setup')
    bad = client.post('/setup', data={'csrf': csrf_of(page.text), 'name': 'X', 'email': 'intruder@example.com',
                                      'password': PASSWORD, 'confirm': PASSWORD})
    assert bad.status_code == 400 and 'not the one set up' in bad.text
    ok = client.post('/setup', data={'csrf': csrf_of(page.text), 'name': 'Lachelle', 'email': ADMIN_EMAIL,
                                     'password': PASSWORD, 'confirm': PASSWORD}, follow_redirects=False)
    assert ok.status_code == 303
    # Setup is closed for good once an account exists
    other = new_client()
    assert other.get('/setup', follow_redirects=False).headers['location'] == '/login'
    assert other.get('/login').status_code == 200


def test_invite_link_single_use_and_expiry(admin):
    c, token = admin
    page = c.post('/people/invite', follow_redirects=True, data={'csrf': token, 'name': 'Co Admin', 'email': 'co@example.com'})
    assert 'expires in 48 hours' in page.text
    link = page.text.split('id="invlink" value="')[1].split('"')[0]
    path = link.split('testserver')[1]

    guest = new_client()
    form = guest.get(path)
    assert 'co@example.com' in form.text
    r = guest.post(path, data={'csrf': csrf_of(form.text), 'name': 'Co', 'password': 'another-good-pass', 'confirm': 'another-good-pass'},
                   follow_redirects=False)
    assert r.status_code == 303 and guest.get('/shows').status_code == 200

    reuse = new_client().get(path)
    assert reuse.status_code == 400 and 'already been used' in reuse.text

    # Expired links are refused
    page = c.post('/people/invite', follow_redirects=True, data={'csrf': token, 'name': 'Late', 'email': 'late@example.com'})
    path2 = page.text.split('id="invlink" value="')[1].split('"')[0].split('testserver')[1]
    from app import db
    with db.engine().begin() as conn:
        conn.execute(update(db.invites).where(db.invites.c.email == 'late@example.com')
                     .values(expires_at=db.now() - timedelta(minutes=1)))
    assert 'expired' in new_client().get(path2).text


def test_cancelled_invite_stops_working(admin):
    c, token = admin
    page = c.post('/people/invite', follow_redirects=True, data={'csrf': token, 'name': 'X', 'email': 'x@example.com'})
    path = page.text.split('id="invlink" value="')[1].split('"')[0].split('testserver')[1]
    from app import auth
    inv_id = auth.pending_invites()[0]['id']
    c.post(f'/people/invites/{inv_id}/cancel', data={'csrf': token})
    assert 'cancelled' in new_client().get(path).text


def test_lockout_after_repeated_wrong_passwords(admin):
    guest = new_client()
    for _ in range(5):
        assert sign_in(guest, password='wrong-password!').status_code == 400
    r = sign_in(guest)  # right password, but locked
    assert r.status_code == 400 and 'Too many wrong passwords' in r.text


def test_disabling_signs_someone_out_everywhere(admin):
    c, token = admin
    page = c.post('/people/invite', follow_redirects=True, data={'csrf': token, 'name': 'Co', 'email': 'co@example.com'})
    path = page.text.split('id="invlink" value="')[1].split('"')[0].split('testserver')[1]
    co = new_client()
    form = co.get(path)
    co.post(path, data={'csrf': csrf_of(form.text), 'name': 'Co', 'password': 'another-good-pass', 'confirm': 'another-good-pass'})
    assert co.get('/shows').status_code == 200

    from app import db
    co_id = db.get_user(email='co@example.com')['id']
    c.post(f'/people/{co_id}/disable', data={'csrf': token})
    assert co.get('/shows', follow_redirects=False).status_code == 303
    assert 'disabled' in sign_in(new_client(), 'co@example.com', 'another-good-pass').text
    # You can't disable yourself
    me = db.get_user(email=ADMIN_EMAIL)['id']
    assert c.post(f'/people/{me}/disable', data={'csrf': token}).status_code == 400


def test_forms_need_the_csrf_token(admin):
    c, token = admin
    r = c.post('/people/invite', data={'name': 'X', 'email': 'x@example.com'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/login')


def test_password_change_signs_out_other_devices(admin):
    c, token = admin
    laptop = new_client()
    assert sign_in(laptop).status_code == 303
    r = c.post('/account', data={'csrf': token, 'current': PASSWORD, 'password': 'a-brand-new-pass', 'confirm': 'a-brand-new-pass'})
    assert 'Password changed' in r.text
    assert c.get('/shows').status_code == 200                                    # this device stays signed in
    assert laptop.get('/shows', follow_redirects=False).status_code == 303       # the other one is signed out
    assert sign_in(new_client(), password='a-brand-new-pass').status_code == 303


def test_passwords_are_not_stored_in_plain_text(admin):
    from app import db
    stored = db.get_user(email=ADMIN_EMAIL)['password_hash']
    assert PASSWORD not in stored and stored.startswith('scrypt$')
