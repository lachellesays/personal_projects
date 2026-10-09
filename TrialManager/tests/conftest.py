import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ADMIN_EMAIL = 'lachelle@example.com'
PASSWORD = 'correct-horse-battery'


def submission(sub_id, first, last, handler, dog_no, dog, sat=None, sun=None, paid='0.00',
               addr='1 Main St', state='Virginia', postal='23220', level='Novice', height='20 regular'):
    answers = {
        'name': {'first': first, 'last': last}, 'email': f'{first.lower()}@example.com',
        'address': {'addr_line1': addr, 'addr_line2': '', 'city': 'Richmond', 'state': state, 'postal': postal},
        'handlersUki': handler, 'dogsName': dog, 'dogsBreed': 'Mix', 'dogsUki': dog_no, 'jumpHeight': height,
        'internationalLevel': level, 'speedstakesLevel': level, 'saturdayClasses': sat, 'sundayClasses': sun,
        'myProducts': {'paymentArray': json.dumps({'total': paid})},
    }
    return {'id': sub_id, 'status': 'ACTIVE', 'created_at': '2026-10-01 10:00:00',
            'answers': {str(i): {'name': k, 'answer': v} for i, (k, v) in enumerate(answers.items())}}


def sample_submissions():
    return [
        # Ann: two dogs, owes money (3 classes = $45, paid $30)
        submission('1', 'Ann', 'Smith', 'H100', 'D500', 'Rex', sat=['Agility 1', 'Jumping 1'], paid='30.00'),
        submission('2', 'Ann', 'Smith', 'H100', 'D501', 'Bea', sun=['Snooker']),
        # Bob: paid more than his classes cost -> credit warning
        submission('3', 'Bob', 'Jones', '200', '600', 'Max', sat=['Agility 1'], paid='60.00'),
        # Cara: typed her address two ways across two dogs
        submission('4', 'Cara', 'Lee', '300', '700', 'Zip', sat=['Agility 1'], paid='15.00', addr='9 Oak Rd'),
        submission('5', 'Cara', 'Lee', '300', '701', 'Zap', sun=['Agility 1'], paid='15.00', addr='9 Oak Road'),
        # Dee: state says Alabama but the zip is in Virginia
        submission('6', 'Dee', 'Fox', '400', '800', 'Kit', sat=['Jumping 1'], paid='15.00', state='Alabama', postal='23220'),
    ]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/tm.db')
    monkeypatch.setenv('BOOTSTRAP_ADMIN_EMAIL', ADMIN_EMAIL)
    monkeypatch.setenv('JOTFORM_API_KEY', 'test-key')
    monkeypatch.setenv('SECRET_KEY', 'test-secret')
    for k in ('RAILWAY_ENVIRONMENT_NAME', 'RAILWAY_ENVIRONMENT'):
        monkeypatch.delenv(k, raising=False)
    from app import db, formatter
    db.reset_engine()
    subs = {'data': sample_submissions()}
    monkeypatch.setattr(formatter, 'fetch_submissions', lambda form_id, key: subs['data'])
    yield subs
    db.reset_engine()


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
    assert m, 'no CSRF token on page'
    return m.group(1)


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def new_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def sign_in(c, email=ADMIN_EMAIL, password=PASSWORD):
    page = c.get('/login')
    return c.post('/login', data={'csrf': csrf_of(page.text), 'email': email, 'password': password, 'next': '/shows'},
                  follow_redirects=False)


@pytest.fixture
def admin(client):
    """A signed-in first admin, plus a saved show. Returns (client, csrf)."""
    page = client.get('/setup')
    r = client.post('/setup', data={'csrf': csrf_of(page.text), 'name': 'Lachelle', 'email': ADMIN_EMAIL,
                                    'password': PASSWORD, 'confirm': PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get('/shows/new')
    token = csrf_of(page.text)
    r = client.post('/shows/new', data={'csrf': token, 'name': 'Test Trial', 'show_id': '7104', 'form_id': '261266312429152',
                                        'saturday_date': '2026-12-05', 'sunday_date': '2026-12-06'}, follow_redirects=False)
    assert r.headers['location'] == '/shows/2026-12-05-test-trial/entries'
    return client, token


SLUG = '2026-12-05-test-trial'
