"""
auth.py

Admin accounts. There is no public sign-up:
  - The very first account can only be created by the email in BOOTSTRAP_ADMIN_EMAIL (a Railway
    variable), and only while no accounts exist.
  - Everyone after that is invited by an existing admin with a single-use link that expires.

Passwords are stored as salted scrypt hashes. Sessions live in a signed cookie that carries the
user's session_version; disabling a user or changing a password bumps it, which signs them out
everywhere.
"""

import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from sqlalchemy import insert, select, update

from app import db

MIN_PASSWORD = 10
MAX_FAILED = 5
LOCK_MINUTES = 15
INVITE_HOURS = 48


class AuthError(Exception):
    """Shown to the person as-is."""


# ── Passwords ────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split('$')
        test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(test, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def validate_password(password: str, confirm: str):
    if len(password) < MIN_PASSWORD:
        raise AuthError(f'Use at least {MIN_PASSWORD} characters.')
    if password != confirm:
        raise AuthError("The two passwords don't match.")


# Burned on unknown emails so sign-in takes the same time either way
_DUMMY_HASH = hash_password(secrets.token_hex(8))


# ── Sign in ──────────────────────────────────────────────────────────────────

def authenticate(email: str, password: str, ip: str = '', device: str = '') -> dict:
    email = (email or '').strip().lower()
    user = db.get_user(email=email)
    now = db.now()

    def record(success):
        with db.engine().begin() as c:
            c.execute(insert(db.login_events).values(at=now, email=email, user_id=user['id'] if user else None,
                                                     success=success, ip=ip[:64], device=device[:200]))

    if user is None:
        check_password(password, _DUMMY_HASH)
        record(False)
        raise AuthError('Email or password is incorrect.')
    if user['locked_until'] and user['locked_until'] > now:
        record(False)
        mins = max(1, int((user['locked_until'] - now).total_seconds() // 60) + 1)
        raise AuthError(f'Too many wrong passwords. Try again in {mins} minute{"s" if mins != 1 else ""}.')
    if not check_password(password, user['password_hash']):
        fails = user['failed_logins'] + 1
        values = {'failed_logins': fails}
        if fails >= MAX_FAILED:
            values = {'failed_logins': 0, 'locked_until': now + timedelta(minutes=LOCK_MINUTES)}
        db.update_user(user['id'], **values)
        record(False)
        raise AuthError('Email or password is incorrect.')
    if not user['is_active']:
        record(False)
        raise AuthError('This account has been disabled. Ask another admin to turn it back on.')
    db.update_user(user['id'], failed_logins=0, locked_until=None)
    record(True)
    return db.get_user(user_id=user['id'])


def session_user(session: dict):
    """The signed-in user for this cookie, or None (also None if they were disabled or signed out elsewhere)."""
    uid, ver = session.get('uid'), session.get('ver')
    if uid is None:
        return None
    user = db.get_user(user_id=uid)
    if not user or not user['is_active'] or user['session_version'] != ver:
        return None
    return user


def start_session(session: dict, user: dict):
    session.clear()
    session.update(uid=user['id'], ver=user['session_version'], csrf=secrets.token_urlsafe(24))


# ── First admin ──────────────────────────────────────────────────────────────

def setup_allowed() -> bool:
    return db.user_count() == 0 and bool(os.environ.get('BOOTSTRAP_ADMIN_EMAIL', '').strip())


def create_first_admin(email: str, name: str, password: str, confirm: str) -> dict:
    if not setup_allowed():
        raise AuthError('Setup is already complete.')
    allowed = os.environ['BOOTSTRAP_ADMIN_EMAIL'].strip().lower()
    if (email or '').strip().lower() != allowed:
        raise AuthError('That email is not the one set up for this app.')
    validate_password(password, confirm)
    return _create_user(email, name, password, created_by=None)


def _create_user(email, name, password, created_by):
    email, name = email.strip().lower(), (name or '').strip() or email.split('@')[0]
    if db.get_user(email=email):
        raise AuthError('An account with that email already exists.')
    with db.engine().begin() as c:
        c.execute(insert(db.users).values(email=email, name=name, password_hash=hash_password(password), is_active=True,
                                          session_version=1, failed_logins=0, created_at=db.now(), created_by=created_by))
    return db.get_user(email=email)


# ── Invites ──────────────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(by_user: dict, email: str, name: str) -> str:
    """Returns the secret token for the link. Only its hash is stored."""
    email = (email or '').strip().lower()
    if '@' not in email:
        raise AuthError('Enter a valid email address.')
    if db.get_user(email=email):
        raise AuthError('That person already has an account.')
    token = secrets.token_urlsafe(32)
    with db.engine().begin() as c:
        c.execute(update(db.invites).where(db.invites.c.email == email, db.invites.c.used_at.is_(None),
                                           db.invites.c.cancelled_at.is_(None)).values(cancelled_at=db.now()))
        c.execute(insert(db.invites).values(token_hash=_token_hash(token), email=email, name=(name or '').strip(),
                                            created_by=by_user['id'], created_at=db.now(),
                                            expires_at=db.now() + timedelta(hours=INVITE_HOURS)))
    return token


def find_invite(token: str):
    """The invite for this link if it can still be used, else raises AuthError."""
    with db.engine().connect() as c:
        inv = c.execute(select(db.invites).where(db.invites.c.token_hash == _token_hash(token or ''))).mappings().first()
    if not inv:
        raise AuthError('This invite link is not valid.')
    if inv['used_at']:
        raise AuthError('This invite link has already been used. Sign in instead.')
    if inv['cancelled_at']:
        raise AuthError('This invite was cancelled. Ask an admin for a new one.')
    if db.aware(inv['expires_at']) < db.now():
        raise AuthError('This invite link has expired. Ask an admin for a new one.')
    return inv


def accept_invite(token: str, name: str, password: str, confirm: str) -> dict:
    inv = find_invite(token)
    validate_password(password, confirm)
    with db.engine().begin() as c:
        # Mark used first, guarded on still-unused, so a link can't be used twice at the same moment
        claimed = c.execute(update(db.invites).where(db.invites.c.id == inv['id'], db.invites.c.used_at.is_(None))
                            .values(used_at=db.now())).rowcount
    if not claimed:
        raise AuthError('This invite link has already been used. Sign in instead.')
    return _create_user(inv['email'], name or inv['name'], password, created_by=inv['created_by'])


def cancel_invite(invite_id: int):
    with db.engine().begin() as c:
        c.execute(update(db.invites).where(db.invites.c.id == invite_id, db.invites.c.used_at.is_(None))
                  .values(cancelled_at=db.now()))


def pending_invites() -> list:
    with db.engine().connect() as c:
        rows = c.execute(select(db.invites).where(db.invites.c.used_at.is_(None), db.invites.c.cancelled_at.is_(None))
                         .order_by(db.invites.c.id.desc())).mappings().all()
    return [{**r, 'expires_at': db.aware(r['expires_at']), 'expired': db.aware(r['expires_at']) < db.now()} for r in rows]


# ── People ───────────────────────────────────────────────────────────────────

def all_users() -> list:
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(select(db.users).order_by(db.users.c.id)).mappings()]


def set_active(user_id: int, active: bool):
    user = db.get_user(user_id=user_id)
    # Bump the session version so a disabled person is signed out on every device right away
    db.update_user(user_id, is_active=active, session_version=user['session_version'] + 1)


def change_password(user: dict, current: str, new: str, confirm: str) -> dict:
    if not check_password(current, user['password_hash']):
        raise AuthError('Your current password is incorrect.')
    validate_password(new, confirm)
    db.update_user(user['id'], password_hash=hash_password(new), session_version=user['session_version'] + 1)
    return db.get_user(user_id=user['id'])


def recent_logins(limit: int = 15) -> list:
    with db.engine().connect() as c:
        rows = c.execute(select(db.login_events).order_by(db.login_events.c.id.desc()).limit(limit)).mappings().all()
    return [{**r, 'at': db.aware(r['at'])} for r in rows]
