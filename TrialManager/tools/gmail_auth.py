"""
gmail_auth.py — one-time: get the refresh token that lets Trial Manager send email as your Gmail account.

Run on your Mac (not on Railway), from the TrialManager folder:

    python3 tools/gmail_auth.py ~/Downloads/client_secret_XXXX.json

It opens Google in your browser. Sign in with the Gmail account the emails should come from and click Allow
(Google may first say the app isn't verified: click Advanced → Go to Trial Manager — it's your own app).
It then prints the three values to paste into Railway: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
The permission is "send email on your behalf" only; Trial Manager can't read your mail.
"""

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPE = 'https://www.googleapis.com/auth/gmail.send'


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1]) as fh:
        info = json.load(fh)
    client = info.get('installed') or info.get('web') or {}
    if 'client_id' not in client:
        sys.exit('That file doesn’t look like a Google OAuth client file (it should be the JSON you downloaded '
                 'for a "Desktop app" client).')

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            msg = 'All set. You can close this tab and go back to Terminal.' if 'code' in got else \
                  f"Google said: {got.get('error', 'no code')}. Go back to Terminal."
            self.wfile.write(f'<p style="font:18px sans-serif;margin:40px">{msg}</p>'.encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    redirect = f'http://127.0.0.1:{server.server_port}'
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(16)
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
        'client_id': client['client_id'], 'redirect_uri': redirect, 'response_type': 'code', 'scope': SCOPE,
        'access_type': 'offline', 'prompt': 'consent', 'state': state,
        'code_challenge': challenge, 'code_challenge_method': 'S256'})
    threading.Thread(target=server.handle_request, daemon=True).start()
    print('Opening Google in your browser. If it doesn’t open, paste this address into it:\n\n' + url + '\n')
    webbrowser.open(url)
    while 'code' not in got and 'error' not in got:
        threading.Event().wait(0.3)
    if got.get('state') != state or 'code' not in got:
        sys.exit(f"Didn’t get permission: {got.get('error', 'the reply didn’t match')}. Try again.")

    body = urllib.parse.urlencode({'code': got['code'], 'client_id': client['client_id'],
                                   'client_secret': client.get('client_secret', ''), 'redirect_uri': redirect,
                                   'grant_type': 'authorization_code', 'code_verifier': verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', data=body)) as r:
        tokens = json.load(r)
    if 'refresh_token' not in tokens:
        sys.exit('Google didn’t return a refresh token. Remove "Trial Manager" at myaccount.google.com → Security → '
                 'Third-party access, then run this again.')
    print('Paste these into Railway → Trial Manager → Variables:\n')
    print(f"GMAIL_CLIENT_ID={client['client_id']}")
    print(f"GMAIL_CLIENT_SECRET={client.get('client_secret', '')}")
    print(f"GMAIL_REFRESH_TOKEN={tokens['refresh_token']}")
    print('\nKeep them private: together they let an app send email as you. Don’t commit them to GitHub.')


if __name__ == '__main__':
    main()
