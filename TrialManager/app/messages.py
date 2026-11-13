"""
messages.py

Emails to competitors: entry confirmations before the trial and results after it. One email per handler, covering
all their dogs. Nothing goes out automatically: the Messages page lists who hasn't been sent one (or whose entry or
results changed since), the secretary previews and picks, and sending runs in the background through mailer.py.

Every send is kept in tm_messages with a fingerprint of what the email said, so a later change (a class added, a
payment, a recalculated result) shows as "Changed since sent".
"""

import hashlib
import json
import os
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import insert, select, update

from app import db, mailer
from app import people as dirdb

KINDS = {'confirmation': 'Confirmations', 'results': 'Results'}
DEFAULT_APP_URL = 'https://trialsecretary.up.railway.app'


def app_url() -> str:
    """The check-in app's address for the emails (CHECKIN_APP_URL, e.g. after moving it to a custom domain)."""
    return ((os.environ.get('CHECKIN_APP_URL') or '').strip() or DEFAULT_APP_URL).rstrip('/')

env = Environment(loader=FileSystemLoader(Path(__file__).parent / 'templates' / 'email'),
                  autoescape=select_autoescape(['html']), trim_blocks=True, lstrip_blocks=True)
env.filters['money'] = lambda n: f"${n:,.2f}" if n >= 0 else f"-${-n:,.2f}"
env.filters['day'] = lambda d: date.fromisoformat(str(d)).strftime('%A %-m/%-d')
env.filters['class_label'] = dirdb.class_label


class MessageError(Exception):
    pass


def _fingerprint(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _key(handler_number: str, first: str, last: str) -> str:
    return handler_number or f'name:{first} {last}'.strip().lower()


def _emails(d) -> dict:
    """{handler key: email} from the (typo-fixed) entries; the first one given wins."""
    out = {}
    for e in d.corrected:
        k = _key(e['handler_number'], e['first_name'], e['last_name'])
        if (e.get('email') or '').strip() and k not in out:
            out[k] = e['email'].strip()
    return out


# ── Confirmations ───────────────────────────────────────────────────────────

def confirmation_content(d) -> list:
    """One dict per handler: their dogs and classes, fees, paid and balance, as the confirmation states them."""
    emails = _emails(d)
    by_sub = {e['submission_id']: e for e in d.corrected}
    handlers = {}
    for r in d.rows:
        e = by_sub.get(r['submission_id'])
        if e is None:
            continue
        k = _key(r['handler_number'], e['first_name'], e['last_name'])
        h = handlers.setdefault(k, {'key': k, 'handler_number': r['handler_number'], 'first_name': e['first_name'],
                                    'name': r['handler_name'], 'email': emails.get(k, ''), 'dogs': [],
                                    'fees': 0.0, 'paid': r['paid'], 'balance': r['balance']})
        h['dogs'].append({'dog_name': r['dog_name'], 'dog_number': r['dog_number'], 'breed': r['breed'],
                          'height': r['height'], 'level': r['level'], 'scratched': r['scratched'],
                          'speedstakes_level': (e.get('speedstakes_level') or '').strip()
                          if (e.get('speedstakes_level') or '').strip().lower() != (r['level'] or '').strip().lower() else '',
                          'saturday': list(e['saturday_classes'] or []), 'sunday': list(e['sunday_classes'] or []),
                          'paper': str(r['submission_id']).startswith('paper-')})
        if not r['scratched']:
            h['fees'] += r['fee']
    out = sorted(handlers.values(), key=lambda h: h['name'].lower())
    for h in out:
        h['fees'] = round(h['fees'], 2)
        h['all_scratched'] = all(g['scratched'] for g in h['dogs'])
        h['fingerprint'] = _fingerprint({k: h[k] for k in ('email', 'dogs', 'fees', 'paid', 'balance')})
    return out


# ── Results ─────────────────────────────────────────────────────────────────

def results_content(d) -> dict:
    """{'handlers': [...], 'waiting': [classes not calculated yet]} — each handler with every dog's results by day,
    plus Masters Series overall where it's been worked out. Only handlers with at least one calculated run."""
    from app import results, trial
    emails = _emails(d)
    names = {_key(e['handler_number'], e['first_name'], e['last_name']): (e['first_name'], f"{e['first_name']} {e['last_name']}".strip())
             for e in d.corrected}
    days = [str(d.show.saturday_date), str(d.show.sunday_date)]
    handlers, waiting = {}, []
    if not trial.is_built(d.slug):
        return {'handlers': [], 'waiting': []}
    for day in days:
        for s in trial.class_list(d.slug, day):
            ev = results.entry_view(d.slug, day, s['class_name'])
            if not ev['rows']:
                continue
            if not ev['calculated']:
                waiting.append(f"{s['class_name']} ({date.fromisoformat(day):%a})")
                continue
            games = ev['kind'] in ('gamblers', 'snooker')
            for r in ev['rows']:
                res = r['result'] or {}
                k = _key(r['handler_number'], r['first_name'], r['last_name'])
                first, full = names.get(k, (r['first_name'], f"{r['first_name']} {r['last_name']}".strip()))
                h = handlers.setdefault(k, {'key': k, 'handler_number': r['handler_number'], 'first_name': first,
                                            'name': full, 'email': emails.get(k, ''), 'dogs': {}, 'qs': 0, 'masters': []})
                dog = h['dogs'].setdefault(r['dog_number'] or r['dog_name'], {'dog_name': r['dog_name'], 'days': {}})
                if res.get('status'):
                    score = res['status']
                elif not res.get('scored'):
                    score = 'No score'
                elif games:
                    score = f"{res.get('points') or 0} pts"
                else:
                    score = f"{res['total_faults']:g} faults" if res.get('total_faults') else 'Clean'
                dog['days'].setdefault(day, []).append({
                    'class_name': r['combined'], 'place': res.get('place') or '', 'q': bool(res.get('q')),
                    'time': f"{res['time']:.2f}" if res.get('time') else '', 'score': score,
                    'level_points': res.get('level_points') or ''})
                h['qs'] += bool(res.get('q'))
        mv = results.masters_view(d.slug, day)
        if mv.get('available'):
            for g in mv['groups']:
                for m in g['rows']:
                    k = m['handler_number'] or f"name:{m['handler']}".strip().lower()   # same as _key()
                    if k in handlers:
                        handlers[k]['masters'].append({'day': day, 'dog_name': m['dog_name'], 'group': g['label'],
                                                       'place': m['place'] or '', 'prize': m['prize'] or 0,
                                                       'final': bool(m['bye']), 'note': m['note'] or ''})
    out = []
    for h in sorted(handlers.values(), key=lambda h: h['name'].lower()):
        h['dogs'] = [{'dog_name': g['dog_name'], 'days': [{'day': dd, 'runs': g['days'][dd]} for dd in days if dd in g['days']]}
                     for g in h['dogs'].values()]
        h['fingerprint'] = _fingerprint({k: h[k] for k in ('email', 'dogs', 'masters')})
        out.append(h)
    return {'handlers': out, 'waiting': waiting}


# ── Rendering ───────────────────────────────────────────────────────────────

def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def render(kind: str, show, h: dict, note: str = '', sender: str = '') -> dict:
    """{'subject', 'text', 'html'} for one handler."""
    if kind not in KINDS:
        raise MessageError('Unknown kind of message.')
    ctx = {'show': show, 'h': h, 'note': (note or '').strip(), 'sender': sender, 'app_url': app_url(),
           'dates': f"{_d(show.saturday_date):%A %B %-d} and {_d(show.sunday_date):%A %B %-d, %Y}"}
    subject = (f"Entry confirmation: {show.name}" if kind == 'confirmation' else f"Your results: {show.name}")
    return {'subject': subject, 'text': env.get_template(f'{kind}.txt').render(**ctx).strip() + '\n',
            'html': env.get_template(f'{kind}.html').render(**ctx)}


# ── Who has been sent what ──────────────────────────────────────────────────

def latest(slug: str, kind: str) -> dict:
    """{handler key: their most recent message of this kind}."""
    with db.engine().connect() as c:
        rows = c.execute(select(db.messages).where(db.messages.c.show_slug == slug, db.messages.c.kind == kind)
                         .order_by(db.messages.c.id)).mappings().all()
    return {r['handler_key']: dict(r) for r in rows}


def overview(d, kind: str) -> dict:
    """Handlers with their state for the Messages page: no_email, not_sent, sending, sent, changed, failed."""
    content = confirmation_content(d) if kind == 'confirmation' else results_content(d)
    handlers = content if kind == 'confirmation' else content['handlers']
    last = latest(d.slug, kind)
    counts = {}
    for h in handlers:
        m = last.get(h['key'])
        h['last'] = m
        if not h['email']:
            h['state'] = 'no_email'
        elif m is None:
            h['state'] = 'not_sent'
        elif m['status'] == 'queued':
            h['state'] = 'sending'
        elif m['status'] == 'failed':
            h['state'] = 'failed'
        else:
            h['state'] = 'sent' if m['fingerprint'] == h['fingerprint'] else 'changed'
        counts[h['state']] = counts.get(h['state'], 0) + 1
    return {'handlers': handlers, 'counts': counts,
            'waiting': content.get('waiting', []) if isinstance(content, dict) else []}


def queue(d, kind: str, keys: list, user: dict, note: str = '') -> list:
    """Save one queued message per chosen handler (with an email address). Returns the new message ids;
    send_queued() delivers them."""
    keys = set(keys)
    if not keys:
        raise MessageError('Pick at least one person to send to.')
    content = confirmation_content(d) if kind == 'confirmation' else results_content(d)['handlers']
    chosen = [h for h in content if h['key'] in keys and h['email']]
    if not chosen:
        raise MessageError('None of the people picked have an email address.')
    ids, now = [], db.now()
    with db.engine().begin() as c:
        for h in chosen:
            m = render(kind, d.show, h, note, sender=user.get('name', ''))
            ids.append(c.execute(insert(db.messages).values(
                show_slug=d.slug, kind=kind, handler_key=h['key'], to_email=h['email'], subject=m['subject'],
                body_text=m['text'], fingerprint=h['fingerprint'], status='queued', created_at=now,
                sent_by=user.get('name', ''), reply_to=user.get('email', ''), note=note or '')).inserted_primary_key[0])
    db.log(user, f'email {kind}', d.slug, count=len(ids))
    return ids


def send_queued(slug: str, ids: list):
    """Deliver queued messages one by one (run in the background). Each is marked sent or failed with the reason.
    The HTML is rebuilt from the same content the text came from, so a preview matches what's sent."""
    from app import formatter
    d = formatter.load(slug, fetch_if_missing=False)
    by_kind = {}
    for i in ids:
        with db.engine().connect() as c:
            m = c.execute(select(db.messages).where(db.messages.c.id == i)).mappings().first()
        if not m or m['status'] != 'queued':
            continue
        if m['kind'] not in by_kind:
            content = confirmation_content(d) if m['kind'] == 'confirmation' else results_content(d)['handlers']
            by_kind[m['kind']] = {h['key']: h for h in content}
        h = by_kind[m['kind']].get(m['handler_key'])
        try:
            html = render(m['kind'], d.show, h, m['note'], m['sent_by'])['html'] if h else ''
            mailer.send(m['to_email'], m['subject'], m['body_text'], html, reply_to=m['reply_to'])
            values = {'status': 'sent', 'sent_at': db.now(), 'error': ''}
        except Exception as e:  # noqa: BLE001 - recorded per person and shown on the page
            values = {'status': 'failed', 'error': str(e)[:300]}
        with db.engine().begin() as c:
            c.execute(update(db.messages).where(db.messages.c.id == i).values(**values))


def history(slug: str, kind: str, limit: int = 50) -> list:
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(select(db.messages).where(db.messages.c.show_slug == slug,
                                                                      db.messages.c.kind == kind)
                                           .order_by(db.messages.c.id.desc()).limit(limit)).mappings()]
