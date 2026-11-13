"""
mailer.py

Sends email through the secretary's Gmail account with the Gmail API (a web request, so Railway's block on
regular email doesn't apply). Messages appear in that account's Sent folder; Reply-To is whoever clicked Send.

Railway variables (see README → "Connect Gmail"; tools/gmail_auth.py gets the refresh token):
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
    MAIL_FROM_NAME + MAIL_FROM_ADDRESS   optional display name (e.g. "Level Up Dog Sports Trials") and the Gmail
                                         address it belongs to; without them Gmail uses the account's own name
"""

import base64
import os
import time
from email.message import EmailMessage
from email.utils import formataddr

import requests

TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://gmail.googleapis.com/gmail/v1/users/me'
SCOPE = 'https://www.googleapis.com/auth/gmail.send'


class MailError(Exception):
    pass


def configured() -> bool:
    return all((os.environ.get(k) or '').strip() for k in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REFRESH_TOKEN'))


_token = {'value': '', 'expires': 0.0}


def _access_token() -> str:
    if _token['value'] and time.time() < _token['expires'] - 60:
        return _token['value']
    try:
        r = requests.post(TOKEN_URL, data={'client_id': os.environ['GMAIL_CLIENT_ID'].strip(),
                                           'client_secret': os.environ['GMAIL_CLIENT_SECRET'].strip(),
                                           'refresh_token': os.environ['GMAIL_REFRESH_TOKEN'].strip(),
                                           'grant_type': 'refresh_token'}, timeout=20)
    except requests.RequestException as e:
        raise MailError(f'Couldn’t reach Google: {e}')
    if r.status_code != 200:
        err = (r.json() if r.headers.get('content-type', '').startswith('application/json') else {}).get('error', '')
        if err == 'invalid_grant':
            raise MailError('Google no longer accepts the Gmail permission (it was revoked, the password changed, or '
                            'the Google Cloud app is still in Testing mode, which expires after 7 days). '
                            'Run tools/gmail_auth.py again and update GMAIL_REFRESH_TOKEN.')
        raise MailError(f'Google refused the Gmail sign-in ({err or r.status_code}). Check the GMAIL_ variables.')
    data = r.json()
    _token.update(value=data['access_token'], expires=time.time() + int(data.get('expires_in', 3600)))
    return _token['value']


def from_header() -> str:
    """'Level Up Dog Sports <you@gmail.com>' when both MAIL_FROM_NAME and MAIL_FROM_ADDRESS are set; otherwise
    blank, and Gmail uses the account's own name and address."""
    name = (os.environ.get('MAIL_FROM_NAME') or '').strip()
    addr = (os.environ.get('MAIL_FROM_ADDRESS') or '').strip()
    return formataddr((name, addr)) if name and addr else ''


def build(to: str, subject: str, text: str, html: str = '', reply_to: str = '') -> EmailMessage:
    msg = EmailMessage()
    msg['To'] = to
    msg['Subject'] = subject
    if from_header():
        msg['From'] = from_header()
    if reply_to:
        msg['Reply-To'] = reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    return msg


def send(to: str, subject: str, text: str, html: str = '', reply_to: str = '') -> str:
    """Send one email. Returns Gmail's message id; raises MailError with a one-line reason."""
    if not configured():
        raise MailError('Gmail isn’t connected yet (see README → Connect Gmail).')
    raw = base64.urlsafe_b64encode(build(to, subject, text, html, reply_to).as_bytes()).decode()
    try:
        r = requests.post(f'{API}/messages/send', json={'raw': raw}, timeout=30,
                          headers={'Authorization': f'Bearer {_access_token()}'})
    except requests.RequestException as e:
        raise MailError(f'Couldn’t reach Gmail: {e}')
    if r.status_code == 429:
        raise MailError('Gmail’s sending limit was reached for now. Try the rest later.')
    if r.status_code >= 400:
        try:
            reason = r.json()['error']['message']
        except Exception:  # noqa: BLE001
            reason = str(r.status_code)
        raise MailError(f'Gmail refused it: {reason}')
    return r.json().get('id', '')
