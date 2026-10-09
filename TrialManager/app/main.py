"""
main.py — Trial Manager web app (FastAPI + HTMX).

Run locally:  .venv/bin/uvicorn app.main:app --reload
Environment:  DATABASE_URL, JOTFORM_API_KEY, SECRET_KEY, BOOTSTRAP_ADMIN_EMAIL, TRIAL_TZ (optional)
"""

import hmac
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, db, formatter as fmt
from app.core import Show

HERE = Path(__file__).parent
ON_RAILWAY = bool(os.environ.get('RAILWAY_ENVIRONMENT_NAME') or os.environ.get('RAILWAY_ENVIRONMENT'))
TZ = ZoneInfo(os.environ.get('TRIAL_TZ', 'America/New_York'))

SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if ON_RAILWAY:
        raise RuntimeError('SECRET_KEY is not set. Add it as a Railway variable (see README).')
    SECRET_KEY = secrets.token_urlsafe(48)  # local only: sign-ins reset when the server restarts

app = FastAPI(title='Trial Manager', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie='tm_session', max_age=30 * 24 * 3600,
                   same_site='lax', https_only=ON_RAILWAY)
app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')
templates = Jinja2Templates(directory=HERE / 'templates')


# ── Template helpers ─────────────────────────────────────────────────────────

def money(n):
    n = n or 0
    return ('-' if n < 0 else '') + f'${abs(n):,.2f}'


def local(dt, fmt_='%b %-d, %-I:%M %p'):
    return dt.astimezone(TZ).strftime(fmt_) if dt else ''


def ago(dt):
    if not dt:
        return 'never'
    s = (db.now() - dt).total_seconds()
    if s < 60:
        return 'just now'
    if s < 3600:
        return f'{int(s // 60)} min ago'
    if s < 86400:
        return f'{int(s // 3600)} hr ago'
    return local(dt)


def day(d):
    try:
        return date.fromisoformat(str(d)).strftime('%a %-m/%-d')
    except ValueError:
        return str(d)


templates.env.filters.update(money=money, local=local, ago=ago, day=day)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault('user', getattr(request.state, 'user', None))
    ctx.setdefault('csrf', request.session.get('csrf', ''))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# ── Auth plumbing ───────────────────────────────────────────────────────────

class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc):
    nxt = quote(request.url.path)
    if request.headers.get('HX-Request'):
        return Response(status_code=204, headers={'HX-Redirect': f'/login?next={nxt}'})
    return RedirectResponse(f'/login?next={nxt}', status_code=303)


@app.exception_handler(fmt.FormatterError)
async def _formatter_error(request: Request, exc):
    return render(request, 'error.html', status_code=400, message=str(exc))


def require_user(request: Request) -> dict:
    user = auth.session_user(request.session)
    if not user:
        raise LoginRequired()
    request.state.user = user
    return user


async def check_csrf(request: Request):
    form = await request.form()
    sent = request.headers.get('X-CSRF-Token') or form.get('csrf', '')
    if not sent or not hmac.compare_digest(str(sent), request.session.get('csrf', '') or '-'):
        raise LoginRequired()  # stale page or forged request: make them sign in again
    return form


def client_info(request: Request):
    ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '')
    return ip, request.headers.get('user-agent', '')


def back(url: str):
    return RedirectResponse(url, status_code=303)


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = '/shows'):
    if auth.session_user(request.session):
        return back('/shows')
    if auth.setup_allowed():
        return back('/setup')
    request.session.setdefault('csrf', secrets.token_urlsafe(24))
    return render(request, 'login.html', next=next, error=None, email='')


@app.post('/login', response_class=HTMLResponse)
async def login(request: Request):
    form = await check_csrf(request)
    nxt = form.get('next') or '/shows'
    if not nxt.startswith('/') or nxt.startswith('//'):
        nxt = '/shows'
    try:
        user = auth.authenticate(form.get('email', ''), form.get('password', ''), *client_info(request))
    except auth.AuthError as e:
        return render(request, 'login.html', status_code=400, next=nxt, error=str(e), email=form.get('email', ''))
    auth.start_session(request.session, user)
    return back(nxt)


@app.post('/logout')
async def logout(request: Request):
    await check_csrf(request)
    request.session.clear()
    return back('/login')


@app.get('/setup', response_class=HTMLResponse)
def setup_page(request: Request):
    if not auth.setup_allowed():
        return back('/login')
    request.session.setdefault('csrf', secrets.token_urlsafe(24))
    return render(request, 'setup.html', error=None, form={})


@app.post('/setup', response_class=HTMLResponse)
async def setup(request: Request):
    form = await check_csrf(request)
    try:
        user = auth.create_first_admin(form.get('email', ''), form.get('name', ''), form.get('password', ''), form.get('confirm', ''))
    except auth.AuthError as e:
        return render(request, 'setup.html', status_code=400, error=str(e), form=dict(form))
    auth.start_session(request.session, user)
    db.log(user, 'people', target=f'user:{user["id"]}', event='first admin created')
    return back('/shows')


@app.get('/invite/{token}', response_class=HTMLResponse)
def invite_page(request: Request, token: str):
    request.session.setdefault('csrf', secrets.token_urlsafe(24))
    try:
        inv = auth.find_invite(token)
    except auth.AuthError as e:
        return render(request, 'invite.html', status_code=400, invite=None, error=str(e), token=token)
    return render(request, 'invite.html', invite=inv, error=None, token=token)


@app.post('/invite/{token}', response_class=HTMLResponse)
async def invite_accept(request: Request, token: str):
    form = await check_csrf(request)
    try:
        user = auth.accept_invite(token, form.get('name', ''), form.get('password', ''), form.get('confirm', ''))
    except auth.AuthError as e:
        try:
            inv = auth.find_invite(token)
        except auth.AuthError:
            inv = None
        return render(request, 'invite.html', status_code=400, invite=inv, error=str(e), token=token)
    auth.start_session(request.session, user)
    db.log(user, 'people', target=f'user:{user["id"]}', event='joined from invite')
    return back('/shows')


# ── People & account ────────────────────────────────────────────────────────

@app.get('/people', response_class=HTMLResponse)
def people(request: Request):
    require_user(request)
    flash = request.session.pop('invite_flash', None) or {}   # shown once, right after creating an invite
    return render(request, 'people.html', people=auth.all_users(), invites=auth.pending_invites(),
                  logins=auth.recent_logins(), link=flash.get('link', ''), invited=flash.get('email', ''), error=None)


@app.post('/people/invite', response_class=HTMLResponse)
async def people_invite(request: Request):
    user = require_user(request)
    form = await check_csrf(request)
    try:
        token = auth.create_invite(user, form.get('email', ''), form.get('name', ''))
    except auth.AuthError as e:
        return render(request, 'people.html', status_code=400, people=auth.all_users(), invites=auth.pending_invites(),
                      logins=auth.recent_logins(), link='', error=str(e))
    db.log(user, 'people', target=f'invite:{form.get("email", "")}', event='invite created')
    link = str(request.base_url).rstrip('/') + f'/invite/{token}'
    request.session['invite_flash'] = {'link': link, 'email': form.get('email', '')}
    return back('/people')


@app.post('/people/{user_id}/{action}')
async def people_toggle(request: Request, user_id: int, action: str):
    user = require_user(request)
    await check_csrf(request)
    if action not in ('disable', 'enable'):
        return back('/people')
    if user_id == user['id']:
        raise fmt.FormatterError('You can’t disable your own account. Ask another admin.')
    auth.set_active(user_id, action == 'enable')
    db.log(user, 'people', target=f'user:{user_id}', event=action)
    return back('/people')


@app.post('/people/invites/{invite_id}/cancel')
async def invite_cancel(request: Request, invite_id: int):
    user = require_user(request)
    await check_csrf(request)
    auth.cancel_invite(invite_id)
    db.log(user, 'people', target=f'invite:{invite_id}', event='invite cancelled')
    return back('/people')


@app.get('/account', response_class=HTMLResponse)
def account(request: Request):
    require_user(request)
    return render(request, 'account.html', error=None, done=False)


@app.post('/account', response_class=HTMLResponse)
async def account_save(request: Request):
    user = require_user(request)
    form = await check_csrf(request)
    try:
        user = auth.change_password(user, form.get('current', ''), form.get('password', ''), form.get('confirm', ''))
    except auth.AuthError as e:
        return render(request, 'account.html', status_code=400, error=str(e), done=False)
    auth.start_session(request.session, user)  # stay signed in here; other devices are signed out
    request.state.user = user
    return render(request, 'account.html', error=None, done=True)


# ── Shows ───────────────────────────────────────────────────────────────────

@app.get('/', include_in_schema=False)
def home():
    return back('/shows')


@app.get('/shows', response_class=HTMLResponse)
def shows_page(request: Request):
    require_user(request)
    cards = []
    for slug, show in db.list_shows():
        subs, fetched = db.get_snapshot(show.form_id)
        last = db.download_history(slug, limit=1)
        cards.append({'slug': slug, 'show': show, 'submissions': len(subs) if subs is not None else None,
                      'fetched': fetched, 'last_download': last[0] if last else None,
                      'past': str(show.sunday_date) < date.today().isoformat()})
    return render(request, 'shows.html', cards=cards)


def _show_form_defaults():
    existing = db.list_shows()
    last = existing[0][1] if existing else None
    sat = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
    return {'name': '', 'show_id': last.show_id if last else '', 'form_id': last.form_id if last else '',
            'saturday_date': sat.isoformat(), 'sunday_date': (sat + timedelta(days=1)).isoformat()}


@app.get('/shows/new', response_class=HTMLResponse)
def show_new(request: Request):
    require_user(request)
    return render(request, 'show_form.html', form=_show_form_defaults(), slug=None, error=None)


@app.get('/shows/{slug}/edit', response_class=HTMLResponse)
def show_edit(request: Request, slug: str):
    require_user(request)
    show = db.get_show(slug)
    if not show:
        raise fmt.FormatterError('That show doesn’t exist.')
    return render(request, 'show_form.html', slug=slug, error=None,
                  form={'name': show.name, 'show_id': show.show_id, 'form_id': show.form_id,
                        'saturday_date': show.saturday_date, 'sunday_date': show.sunday_date})


@app.post('/shows/new', response_class=HTMLResponse)
@app.post('/shows/{slug}/edit', response_class=HTMLResponse)
async def show_save(request: Request, slug: str = None):
    user = require_user(request)
    form = dict(await check_csrf(request))
    name, show_id, form_id = form.get('name', '').strip(), form.get('show_id', '').strip(), form.get('form_id', '').strip()
    error = None
    try:
        sat, sun = date.fromisoformat(form.get('saturday_date', '')), date.fromisoformat(form.get('sunday_date', ''))
    except ValueError:
        error = 'Pick both the Saturday and Sunday dates.'
    if not name:
        error = 'Give the show a name.'
    elif not show_id.isdigit():
        error = 'The show ID should be a number, like 7104.'
    elif not form_id.isdigit():
        error = 'The JotForm form ID is the long number in the form’s web address.'
    if error:
        return render(request, 'show_form.html', status_code=400, form=form, slug=slug, error=error)
    show_id = int(show_id)
    show = db.get_show(slug) if slug else None
    if show is None:
        show = Show(name=name, show_id=show_id, form_id=form_id, saturday_date=sat.isoformat(), sunday_date=sun.isoformat())
    else:
        show.name, show.show_id, show.form_id = name, show_id, form_id
        show.saturday_date, show.sunday_date = sat.isoformat(), sun.isoformat()
    slug = db.save_show(show, slug)
    db.log(user, 'show', slug, event='saved')
    return back(f'/shows/{slug}/entries')


@app.post('/shows/{slug}/refresh')
async def show_refresh(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    show = db.get_show(slug)
    fmt.refresh(show)
    db.log(user, 'show', slug, event='refreshed from JotForm')
    return back(form.get('back') or f'/shows/{slug}/entries')


def _show_ctx(request, slug, tab, d=None):
    d = d or fmt.load(slug)
    ws = fmt.warnings(d)
    return d, ws, {'d': d, 'slug': slug, 'tab': tab, 'open_warnings': fmt.open_warning_count(ws),
                   'fix_count': sum(len(v) for v in d.show.handler_corrections.values())
                   + sum(len(v) for v in d.show.dog_corrections.values())}


@app.get('/shows/{slug}', include_in_schema=False)
def show_home(slug: str):
    return back(f'/shows/{slug}/entries')


@app.get('/shows/{slug}/entries', response_class=HTMLResponse)
def entries(request: Request, slug: str, q: str = '', f: str = 'all'):
    require_user(request)
    d, ws, ctx = _show_ctx(request, slug, 'entries')
    rows = fmt.filter_rows(d.rows, q, f)
    if request.headers.get('HX-Target') == 'entry-table':  # live search: just the table
        return render(request, '_entry_rows.html', rows=rows, slug=slug)
    return render(request, 'entries.html', rows=rows, q=q, f=f, **ctx)


@app.post('/shows/{slug}/scratch/{dog_number}', response_class=HTMLResponse)
async def scratch(request: Request, slug: str, dog_number: str):
    user = require_user(request)
    form = await check_csrf(request)
    fmt.toggle_scratch(slug, dog_number, user)
    d, ws, ctx = _show_ctx(request, slug, 'entries')
    rows = fmt.filter_rows(d.rows, form.get('q', ''), form.get('f', 'all'))
    return render(request, '_entry_rows.html', rows=rows, oob=True, **ctx)


@app.get('/shows/{slug}/typos', response_class=HTMLResponse)
def typos(request: Request, slug: str, kind: str = 'handlers', q: str = ''):
    require_user(request)
    kind = 'dogs' if kind == 'dogs' else 'handlers'
    d, ws, ctx = _show_ctx(request, slug, 'typos')
    rows, fields = fmt.typo_tables(d, kind)
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r['num'] + ' ' + r['label'] + ' ' + ' '.join(c['value'] for c in r['cells'])).lower()]
    return render(request, 'typos.html', rows=rows, fields=fields, labels=fmt.FIELD_LABELS, kind=kind, q=q,
                  log=fmt.corrections_log(d), **ctx)


def _cell(d, kind, num, field):
    rows, _ = fmt.typo_tables(d, 'handlers' if kind == 'h' else 'dogs')
    row = next((r for r in rows if r['num'] == num), None)
    cell = next((c for c in row['cells'] if c['field'] == field), None) if row else None
    if cell is None:
        raise fmt.FormatterError('That cell doesn’t exist.')
    return cell


@app.get('/shows/{slug}/typos/cell', response_class=HTMLResponse)
def typo_cell(request: Request, slug: str, kind: str, num: str, field: str, edit: int = 0):
    require_user(request)
    d = fmt.load(slug)
    return render(request, '_typo_cell.html', slug=slug, kind=kind, num=num, cell=_cell(d, kind, num, field), edit=bool(edit))


@app.post('/shows/{slug}/typos/cell', response_class=HTMLResponse)
async def typo_save(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    kind, num, field = form['kind'], form['num'], form['field']
    fmt.set_fix(slug, kind, num, field, form.get('value', ''), user)
    d, ws, ctx = _show_ctx(request, slug, 'typos')
    return render(request, '_typo_cell.html', kind=kind, num=num, cell=_cell(d, kind, num, field), edit=False,
                  oob_log=fmt.corrections_log(d), **ctx)


@app.post('/shows/{slug}/typos/undo')
async def typo_undo(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    fmt.undo_fix(slug, form['key'], user)
    return back(f'/shows/{slug}/typos?kind={form.get("kind", "handlers")}')


@app.get('/shows/{slug}/warnings', response_class=HTMLResponse)
def warnings_page(request: Request, slug: str):
    require_user(request)
    d, ws, ctx = _show_ctx(request, slug, 'warnings')
    return render(request, 'warnings.html', warnings=ws, **ctx)


@app.post('/shows/{slug}/warnings')
async def warning_action(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    key, action = form.get('key', ''), form.get('action', '')
    if action == 'dismiss':
        fmt.dismiss(slug, key, user)
    elif action == 'reopen':
        fmt.dismiss(slug, key, user, reopen=True)
    elif action == 'address':
        fmt.choose_address(slug, key.split(':', 1)[1], form.get('choice', ''), user)
    elif action.startswith('fixstate:'):
        fmt.fix_state(slug, key.split(':', 1)[1], action.split(':', 1)[1], user)
    elif action == 'show':
        return back(f'/shows/{slug}/entries?q={quote(key.split(":", 1)[1])}')
    return back(f'/shows/{slug}/warnings')


@app.get('/shows/{slug}/download', response_class=HTMLResponse)
def download_page(request: Request, slug: str):
    require_user(request)
    d, ws, ctx = _show_ctx(request, slug, 'download')
    return render(request, 'download.html', counts=fmt.workbook_counts(d), history=db.download_history(slug), **ctx)


@app.post('/shows/{slug}/download')
async def download(request: Request, slug: str):
    user = require_user(request)
    await check_csrf(request)
    data, filename = fmt.download(slug, user)
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})
