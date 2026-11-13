"""Emails to competitors: entry confirmations and results, previewed, sent through Gmail, tracked per handler."""
import base64
import email

import pytest

from tests.conftest import SLUG
from tests.test_trial import SAT, built, trial_subs  # noqa: F401 - fixtures


@pytest.fixture
def outbox(monkeypatch):
    """Gmail stand-in: records what would be sent."""
    from app import mailer
    sent = []
    monkeypatch.setattr(mailer, 'configured', lambda: True)

    def fake_send(to, subject, text, html='', reply_to=''):
        if to.startswith('bounce'):
            raise mailer.MailError('Gmail refused it: Invalid To header')
        sent.append({'to': to, 'subject': subject, 'text': text, 'html': html, 'reply_to': reply_to})
        return f'id{len(sent)}'
    monkeypatch.setattr(mailer, 'send', fake_send)
    return sent


def test_confirmation_lists_dogs_classes_and_balance(admin):
    from app import formatter, messages
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    d = formatter.load(SLUG)
    by = {h['name']: h for h in messages.confirmation_content(d)}
    ann = by['Ann Smith']
    assert ann['email'] == 'ann@example.com' and [g['dog_name'] for g in ann['dogs']] == ['Bea', 'Rex']
    assert (ann['fees'], ann['paid'], ann['balance']) == (45.0, 30.0, 15.0)
    m = messages.render('confirmation', d.show, ann, note='Mail checks by Nov 25.', sender='Lachelle')
    assert m['subject'] == 'Entry confirmation: Test Trial'
    for text in ('Hi Ann', 'Mail checks by Nov 25.', 'Rex', 'Bea', 'Balance due: $15.00', 'Lachelle'):
        assert text in m['text']
    assert 'You still owe $15.00' in m['html'] and 'Snooker' in m['html']
    assert 'https://trialsecretary.up.railway.app' in m['text'] and 'href="https://trialsecretary.up.railway.app"' in m['html']
    assert 'check in' in m['text'] and 'course maps' in m['text']
    bob = messages.render('confirmation', d.show, by['Bob Jones'])
    assert 'Credit: $45.00' in bob['text']


def test_page_previews_before_gmail_is_connected(admin):
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    page = c.get(f'/shows/{SLUG}/messages').text
    assert 'Gmail isn&#39;t connected yet' in page or "Gmail isn't connected yet" in page
    assert 'Ann Smith' in page and 'Not sent' in page and '$15.00 due' in page
    assert 'Messages' in page and 'Competitors' in page              # in the show menu
    prev = c.get(f'/shows/{SLUG}/messages/preview', params={'kind': 'confirmation', 'key': '100'}).text
    assert 'Preview' in prev and 'ann@example.com' in prev and 'Rex' in prev
    r = c.post(f'/shows/{SLUG}/messages/send', data={'csrf': token, 'kind': 'confirmation', 'h': ['100']})
    assert r.status_code == 400 and 'Gmail isn' in r.text


def test_send_tracks_each_person_and_spots_changes(admin, outbox):
    from app import db
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    r = c.post(f'/shows/{SLUG}/messages/send', data={'csrf': token, 'kind': 'confirmation', 'h': ['100', '200'],
                                                       'note': 'See you there!'}, follow_redirects=True)
    assert 'Sending 2 emails' in r.text
    assert sorted(m['to'] for m in outbox) == ['ann@example.com', 'bob@example.com']
    ann = next(m for m in outbox if m['to'] == 'ann@example.com')
    assert ann['reply_to'] == 'lachelle@example.com' and 'See you there!' in ann['text'] and 'Rex' in ann['html']
    page = c.get(f'/shows/{SLUG}/messages').text
    assert page.count('st-sent') >= 2 and 'Sent log' in page
    # Ann scratches Rex: her confirmation is now out of date
    c.post(f'/shows/{SLUG}/scratch/500', data={'csrf': token})
    page = c.get(f'/shows/{SLUG}/messages?f=changed').text
    assert 'Ann Smith' in page and 'Bob Jones' not in page
    with db.engine().connect() as conn:
        rows = conn.execute(db.messages.select()).mappings().all()
    assert {r['status'] for r in rows} == {'sent'} and all(r['sent_by'] == 'Lachelle' for r in rows)


def test_a_failed_send_is_shown_with_its_reason(admin, outbox, monkeypatch):
    from app import messages
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    real = messages.confirmation_content
    monkeypatch.setattr(messages, 'confirmation_content',
                        lambda d: [{**h, 'email': 'bounce@x' if h['key'] == '200' else h['email']} for h in real(d)])
    c.post(f'/shows/{SLUG}/messages/send', data={'csrf': token, 'kind': 'confirmation', 'h': ['200']})
    page = c.get(f'/shows/{SLUG}/messages?f=failed').text
    assert 'Bob Jones' in page and 'Invalid To header' in page


def test_results_email_after_scoring(built, outbox):
    from app import formatter, messages, results
    c, token = built
    page = c.get(f'/shows/{SLUG}/messages?kind=results').text
    assert 'No results to send yet' in page
    c.post(f'/shows/{SLUG}/scoring/course', data={'csrf': token, 'day': SAT, 'cls': 'Agility', 'yards_bn': '160',
                                                   'yards_champ': '177', 'rate': 'fast', 'sct_extra': '0'})
    by = {r['dog_name']: r for r in results.entry_view(SLUG, SAT, 'Agility')['rows']}
    for dog, kw in (('Ace', {'time': '30.10', 'course_faults': '0', 'refusals': '0'}),
                    ('Bolt', {'time': '31.00', 'course_faults': '5', 'refusals': '0'}), ('Cody', {'status': 'E'})):
        c.post(f'/shows/{SLUG}/scoring/score/{by[dog]["id"]}', data={'csrf': token, 'day': SAT, 'cls': 'Agility', **kw})
    c.post(f'/shows/{SLUG}/scoring/calculate', data={'csrf': token, 'day': SAT, 'cls': 'Agility'})
    content = messages.results_content(formatter.load(SLUG))
    assert any(w.startswith('Gamblers') for w in content['waiting'])          # not calculated: left out
    h100 = next(h for h in content['handlers'] if h['handler_number'] == '100')
    runs = {g['dog_name']: g['days'][0]['runs'][0] for g in h100['dogs']}
    assert runs['Ace']['q'] and runs['Ace']['place'] == 1 and runs['Ace']['time'] == '30.10'
    assert runs['Bolt']['score'] == '5 faults' and not runs['Bolt']['q'] and runs['Cody']['score'] == 'E'
    assert h100['qs'] == 1
    page = c.get(f'/shows/{SLUG}/messages?kind=results').text
    assert 'Not calculated yet' in page and 'H100 Smith' in page
    c.post(f'/shows/{SLUG}/messages/send', data={'csrf': token, 'kind': 'results', 'h': ['100']})
    (m,) = outbox
    assert m['subject'] == 'Your results: Test Trial' and '1 qualifying run' in m['text']
    assert 'Senior/Champ Agility: Q, Clean, 30.10s, place 1' in m['text']


def test_gmail_message_shape(monkeypatch):
    from app import mailer
    monkeypatch.setenv('MAIL_FROM_NAME', 'Level Up Trials')
    monkeypatch.setenv('MAIL_FROM_ADDRESS', 'secretary@example.com')
    msg = mailer.build('ann@example.com', 'Hi', 'plain', '<p>html</p>', reply_to='co@example.com')
    parsed = email.message_from_bytes(msg.as_bytes())
    assert parsed['From'] == 'Level Up Trials <secretary@example.com>' and parsed['Reply-To'] == 'co@example.com'
    assert parsed.is_multipart() and {p.get_content_type() for p in parsed.walk()} >= {'text/plain', 'text/html'}
    assert base64.urlsafe_b64encode(msg.as_bytes())   # what goes to Gmail as "raw"


def test_expired_gmail_permission_says_what_to_do(monkeypatch):
    from app import mailer

    class Resp:
        status_code = 400
        headers = {'content-type': 'application/json'}

        def json(self):
            return {'error': 'invalid_grant'}
    for k in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REFRESH_TOKEN'):
        monkeypatch.setenv(k, 'x')
    monkeypatch.setattr(mailer.requests, 'post', lambda *a, **kw: Resp())
    mailer._token.update(value='', expires=0)
    with pytest.raises(mailer.MailError, match='gmail_auth.py'):
        mailer.send('a@example.com', 's', 't')


def test_app_link_follows_checkin_app_url(admin, monkeypatch):
    from app import formatter, messages
    c, token = admin
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    monkeypatch.setenv('CHECKIN_APP_URL', 'https://checkin.example.com/')
    d = formatter.load(SLUG)
    m = messages.render('confirmation', d.show, messages.confirmation_content(d)[0])
    assert 'https://checkin.example.com' in m['text'] and 'trialsecretary' not in m['html']
