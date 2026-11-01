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

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, db, formatter as fmt, results, trial
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
    request.session['setup_flash'] = 'Trial details saved.'
    return back(f'/shows/{slug}/setup')


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


# ── Run order ───────────────────────────────────────────────────────────────

@app.exception_handler(trial.TrialError)
@app.exception_handler(results.ResultsError)
async def _trial_error(request: Request, exc):
    if request.headers.get('HX-Request'):
        return HTMLResponse(f'<div class="flash error" id="ro-msg">{exc}</div>', status_code=200,
                            headers={'HX-Retarget': '#ro-msg', 'HX-Reswap': 'outerHTML'})
    return render(request, 'error.html', status_code=400, message=str(exc))


def _days(show: Show) -> list:
    return [str(show.saturday_date), str(show.sunday_date)]


def _runorder_ctx(request, slug, day=None, cls=None):
    d, ws, ctx = _show_ctx(request, slug, 'runorder')
    built = trial.is_built(slug)
    classes = trial.overview(slug) if built else []
    days = _days(d.show)
    day = day if day in days else days[0]
    day_classes = [c for c in classes if c['day'] == day]
    if day_classes and cls not in [c['class_name'] for c in day_classes]:
        cls = day_classes[0]['class_name']
    ev = trial.event_view(slug, day, cls) if built and day_classes else None
    pending = trial.pending_changes(d) if built else None
    return {**ctx, 'built': built, 'classes': classes, 'days': days, 'day': day, 'cls': cls, 'ev': ev,
            'day_classes': day_classes, 'pending': pending, 'gap_target': trial.GAP_TARGET,
            'day_sets': {dd: trial.day_setting(slug, dd) for dd in days},
            'level_groups': trial.LEVEL_GROUPS, 'height_orders': trial.HEIGHT_ORDERS,
            'masters_role': trial.masters_order(slug, day).get(cls) if built else None}


@app.get('/shows/{slug}/runorder', response_class=HTMLResponse)
def runorder_page(request: Request, slug: str, day: str = '', cls: str = ''):
    require_user(request)
    return render(request, 'runorder.html', **_runorder_ctx(request, slug, day, cls))


def _runorder_table(request, slug, day, cls):
    ctx = _runorder_ctx(request, slug, day, cls)
    return render(request, '_runorder_table.html', oob=True, **ctx)


@app.post('/shows/{slug}/runorder/build')
async def runorder_build(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    d = fmt.load(slug)
    if not trial.is_built(slug):   # the build page sets each day's order first
        for day_ in _days(d.show):
            lf, hs = form.get(f'levels_first_{day_}'), form.get(f'heights_{day_}')
            if lf and hs:
                trial.save_day(slug, day_, lf, hs, user, regroup=False)
    trial.build_or_update(d, user)
    return back(f'/shows/{slug}/runorder')


@app.post('/shows/{slug}/runorder/day')
async def runorder_day(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day_ = form.get('day', '')
    trial.save_day(slug, day_, form.get('levels_first', ''), form.get('heights', ''), user)
    return back(f'/shows/{slug}/runorder?day={day_}&cls={quote(form.get("cls", ""))}')


@app.post('/shows/{slug}/runorder/generate')
async def runorder_generate(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day, cls = form.get('day', ''), form.get('cls', '')
    trial.generate(slug, day, cls, user=user)
    return back(f'/shows/{slug}/runorder?day={day}&cls={quote(cls)}')


@app.post('/shows/{slug}/runorder/reorder', response_class=HTMLResponse)
async def runorder_reorder(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day, cls = form.get('day', ''), form.get('cls', '')
    trial.reorder(slug, day, cls, form.getlist('run'), user)
    return _runorder_table(request, slug, day, cls)


@app.post('/shows/{slug}/runorder/move', response_class=HTMLResponse)
async def runorder_move(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    try:
        pos = int(form.get('position', ''))
    except ValueError:
        raise trial.TrialError('Type the position as a number, like 5.')
    trial.move_to(slug, int(form['run_id']), pos, user)
    return _runorder_table(request, slug, form.get('day', ''), form.get('cls', ''))


@app.post('/shows/{slug}/runorder/undo', response_class=HTMLResponse)
async def runorder_undo(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day, cls = form.get('day', ''), form.get('cls', '')
    if not trial.undo(slug, day, cls, user):
        raise trial.TrialError('Nothing to undo for this class.')
    return _runorder_table(request, slug, day, cls)


@app.post('/shows/{slug}/runorder/scratch/{run_id}', response_class=HTMLResponse)
async def runorder_scratch(request: Request, slug: str, run_id: int):
    user = require_user(request)
    form = await check_csrf(request)
    trial.toggle_run_scratch(slug, run_id, user)
    return _runorder_table(request, slug, form.get('day', ''), form.get('cls', ''))


@app.post('/shows/{slug}/runorder/class')
async def runorder_class(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day, cls = form.get('day', ''), form.get('cls', '')
    try:
        sort = int(form.get('sort') or 0)
    except ValueError:
        sort = 0
    trial.save_class(slug, day, cls, user, sort, form.get('ring', '1'), form.get('select_first') == 'on',
                     form.get('combine_levels') == 'on')
    return back(f'/shows/{slug}/runorder?day={day}&cls={quote(cls)}')


@app.get('/shows/{slug}/runorder.csv')
def runorder_csv(request: Request, slug: str, day: str = ''):
    user = require_user(request)
    data = trial.run_order_csv(slug, day or None)
    db.log(user, 'download', slug, target='runorder.csv', day=day or 'both')
    name = f'grid_run_order_export{"_" + day if day else ""}.csv'
    return Response(data, media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{name}"'})


@app.get('/shows/{slug}/scribe.pdf')
def scribe_pdf(request: Request, slug: str, day: str, cls: list[str] = Query(default=[])):
    user = require_user(request)
    show = db.get_show(slug)
    data = trial.scribe_sheets_pdf(slug, show.name, day, cls or None)
    db.log(user, 'download', slug, target='scribe.pdf', day=day, classes=cls)
    return Response(data, media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="scribe_sheets_{day}.pdf"'})


# ── Day-of entries ──────────────────────────────────────────────────────────

@app.get('/shows/{slug}/dayof', response_class=HTMLResponse)
def dayof_page(request: Request, slug: str):
    require_user(request)
    msg = request.session.pop('dayof_flash', '')
    d, ws, ctx = _show_ctx(request, slug, 'dayof')
    if not trial.is_built(slug):
        return render(request, 'dayof.html', built=False, **ctx)
    days = _days(d.show)
    classes = {day_: [c['class_name'] for c in trial.class_list(slug, day_)] for day_ in days}
    return render(request, 'dayof.html', built=True, dogs=trial.known_dogs(d), days=days, classes=classes,
                  summary=trial.day_of_summary(slug), day_of_fee=trial.DAY_OF_FEE, msg=msg, error=None, form={}, **ctx)


@app.post('/shows/{slug}/dayof/add', response_class=HTMLResponse)
async def dayof_add(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    d = fmt.load(slug)
    picked = {}
    for v in form.getlist('classes'):
        day_, _, cls = v.partition('|')
        picked.setdefault(day_, []).append(cls)
    if not picked:
        raise trial.TrialError('Pick at least one class.')
    if form.get('mode') == 'existing':
        dog = next((x for x in trial.known_dogs(d) if x['key'] == form.get('dog_key')), None)
        if not dog:
            raise trial.TrialError('Pick the dog from the list.')
    else:
        height, ctype = trial.parse_height(f"{form.get('height', '')} {form.get('class_type', 'Regular')}")
        dog = {k: form.get(k, '').strip() for k in ('handler_number', 'first_name', 'last_name', 'dog_number',
                                                    'dog_name', 'breed', 'level', 'speedstakes_level')}
        dog.update(height=height, class_type=ctype)
        dog['speedstakes_level'] = dog['speedstakes_level'] or dog['level']
    for day_, classes in picked.items():
        trial.add_day_of(d, user, dog, day_, classes)
    n = sum(len(v) for v in picked.values())
    msg = f'Added {dog["dog_name"]} to {n} class{"es" if n != 1 else ""}. Now print new scribe sheets and update the check-in app.'
    request.session['dayof_flash'] = msg
    return back(f'/shows/{slug}/dayof')


@app.post('/shows/{slug}/dayof/payment')
async def dayof_payment(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    trial.add_payment(slug, user, form.get('handler_number', '').strip(), form.get('amount', ''),
                      form.get('method', ''), form.get('note', ''))
    request.session['dayof_flash'] = 'Payment recorded.'
    return back(f'/shows/{slug}/dayof')


# ── Scoring ─────────────────────────────────────────────────────────────────

def _scoring_ctx(request, slug, day, cls, tab='scoring'):
    d, ws, ctx = _show_ctx(request, slug, tab)
    days = _days(d.show)
    day = day if day in days else days[0]
    built = trial.is_built(slug)
    classes = results.overview(slug, day) if built else []
    if classes and cls not in [c['class_name'] for c in classes]:
        cls = classes[0]['class_name']
    ev = results.entry_view(slug, day, cls) if classes else None
    return {**ctx, 'built': built, 'days': days, 'day': day, 'cls': cls, 'score_classes': classes, 'ev': ev,
            'fault_choices': results.FAULT_CHOICES, 'refusal_choices': results.REFUSAL_CHOICES,
            'gamble_choices': results.GAMBLE_CHOICES, 'flash': request.session.pop('scoring_flash', '')}


@app.get('/shows/{slug}/scoring', response_class=HTMLResponse)
def scoring_page(request: Request, slug: str, day: str = '', cls: str = ''):
    require_user(request)
    return render(request, 'scoring.html', **_scoring_ctx(request, slug, day, cls))


@app.post('/shows/{slug}/scoring/course')
async def scoring_course(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day_, cls = form.get('day', ''), form.get('cls', '')
    results.save_course(slug, day_, cls, dict(form), user)
    request.session['scoring_flash'] = 'Course details saved.'
    return back(f'/shows/{slug}/scoring?day={day_}&cls={quote(cls)}')


@app.post('/shows/{slug}/scoring/score/{run_id}', response_class=HTMLResponse)
async def scoring_score(request: Request, slug: str, run_id: int):
    user = require_user(request)
    form = await check_csrf(request)
    day_, cls = form.get('day', ''), form.get('cls', '')
    results.save_score(slug, run_id, dict(form), user)
    ctx = _scoring_ctx(request, slug, day_, cls)
    row = next(r for r in ctx['ev']['rows'] if r['id'] == run_id)
    return render(request, '_score_row.html', r=row, oob=True, **ctx)


@app.post('/shows/{slug}/scoring/calculate')
async def scoring_calculate(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day_, cls = form.get('day', ''), form.get('cls', '')
    out = results.calculate(slug, day_, cls, user)
    msg = f"Calculated: {out['qs']} qualifying run{'s' if out['qs'] != 1 else ''}."
    if out['unscored']:
        msg += f" {out['unscored']} dog{'s have' if out['unscored'] != 1 else ' has'} no score yet."
    request.session['scoring_flash'] = msg
    return back(f'/shows/{slug}/scoring/results?day={day_}&cls={quote(cls)}')


@app.get('/shows/{slug}/scoring/results', response_class=HTMLResponse)
def scoring_results(request: Request, slug: str, day: str = '', cls: str = ''):
    require_user(request)
    ctx = _scoring_ctx(request, slug, day, cls)
    groups, exports = [], []
    if ctx['ev'] and ctx['ev']['calculated']:
        import json as _json
        groups = results.results_view(slug, ctx['day'], ctx['cls'])
        for combined in sorted({r['combined'] for r in ctx['ev']['rows']}):
            exports.append({'name': combined, 'json': _json.dumps(results.results_json(slug, ctx['day'], ctx['cls'], combined))})
    return render(request, 'scoring_results.html', groups=groups, exports=exports, **ctx)


@app.get('/shows/{slug}/results.xlsx')
def results_xlsx(request: Request, slug: str):
    user = require_user(request)
    show = db.get_show(slug)
    data = results.results_workbook(slug, show)
    db.log(user, 'download', slug, target='results.xlsx')
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="Results_{slug}.xlsx"'})


@app.get('/shows/{slug}/scoring/masters', response_class=HTMLResponse)
def scoring_masters(request: Request, slug: str, day: str = ''):
    require_user(request)
    ctx = _scoring_ctx(request, slug, day, '')
    return render(request, 'masters.html', mv=results.masters_view(slug, ctx['day']), **ctx)


@app.post('/shows/{slug}/runorder/masters')
async def runorder_masters(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day_, cls = form.get('day', ''), form.get('cls', '')
    trial.order_masters_from_first_round(slug, day_, cls, user)
    return back(f'/shows/{slug}/runorder?day={day_}&cls={quote(cls)}')


@app.get('/shows/{slug}/scoring/masters/certificates.pdf')
def masters_certificates(request: Request, slug: str, day: str):
    user = require_user(request)
    show = db.get_show(slug)
    data = results.masters_certificates_pdf(slug, day, show.name)
    db.log(user, 'download', slug, target='masters certificates', day=day)
    return Response(data, media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="masters_final_certificates_{day}.pdf"'})


# ── Show setup ──────────────────────────────────────────────────────────────

@app.get('/shows/{slug}/setup', response_class=HTMLResponse)
def show_setup(request: Request, slug: str):
    require_user(request)
    d, ws, ctx = _show_ctx(request, slug, 'setup')
    trial.ensure_classes_from_entries(d)
    days = _days(d.show)
    return render(request, 'show_setup.html', days=days, day_sets={dd: trial.day_setting(slug, dd) for dd in days},
                  day_classes={dd: trial.class_list(slug, dd) for dd in days}, built=trial.is_built(slug),
                  level_groups=trial.LEVEL_GROUPS, height_orders=trial.HEIGHT_ORDERS,
                  flash=request.session.pop('setup_flash', ''),
                  form={'name': d.show.name, 'show_id': d.show.show_id, 'form_id': d.show.form_id,
                        'saturday_date': d.show.saturday_date, 'sunday_date': d.show.sunday_date}, **ctx)


@app.post('/shows/{slug}/setup/day')
async def show_setup_day(request: Request, slug: str):
    user = require_user(request)
    form = await check_csrf(request)
    day_ = form.get('day', '')
    names = form.getlist('class')
    classes = [{'class_name': n, 'ring': form.get(f'ring::{n}', '1'), 'select_first': form.get(f'select_first::{n}') == 'on',
                'combine_levels': form.get(f'combine::{n}') == 'on'} for n in names]
    trial.save_day_setup(slug, day_, form.get('levels_first', ''), form.get('heights', ''), classes, user)
    request.session['setup_flash'] = f'{day(day_)} saved.'
    return back(f'/shows/{slug}/setup#day-{day_}')
