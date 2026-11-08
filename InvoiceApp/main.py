"""
main.py — Level Up invoicing web app (FastAPI + HTMX).

Run locally:  .venv/bin/uvicorn main:app --reload
Environment:  DATABASE_URL (Railway Postgres; SQLite file if unset), APP_PIN, SECRET_KEY
"""

import csv
import hmac
import io
import os
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import db
import import_csv
from invoice_pdf import build_pdf

HERE = Path(__file__).parent


def load_dotenv():
    env = HERE / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_dotenv()
ON_RAILWAY = bool(os.environ.get('RAILWAY_ENVIRONMENT_NAME') or os.environ.get('RAILWAY_ENVIRONMENT'))

SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if ON_RAILWAY:
        raise RuntimeError('SECRET_KEY is not set. Add it as a Railway variable (see README).')
    SECRET_KEY = secrets.token_urlsafe(48)  # local only: you'll need the PIN again after a restart

app = FastAPI(title='Level Up Invoicing', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')
templates = Jinja2Templates(directory=HERE / 'templates')

ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        ENGINE = db.get_engine()
    return ENGINE


# ── Template helpers ─────────────────────────────────────────────────────────

def d_short(d):
    return d.strftime('%a %-m/%-d') if d else 'no date'


def num(v):
    """Number for an input box: 3.0 → '3', None → ''."""
    return '' if v is None else f'{v:g}'


templates.env.filters.update(money=db.money, d_short=d_short, num=num, dollars=lambda c: num((c or 0) / 100))
templates.env.globals.update(KINDS=db.KINDS, CLASS_STATUSES=db.CLASS_STATUSES, LOCKED=db.LOCKED_STATUSES)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault('csrf', request.session.get('csrf', ''))
    ctx.setdefault('flash', request.session.pop('flash', None))
    ctx.setdefault('tab', '')
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, msg: str, kind: str = 'ok'):
    request.session['flash'] = {'msg': msg, 'kind': kind}


def back(url: str):
    return RedirectResponse(url, status_code=303)


# ── Sign-in (one PIN) ────────────────────────────────────────────────────────

OPEN_PATHS = ('/login', '/health', '/static')
_fails: dict = {}          # ip → (count, first failure time); wrong PINs lock that address out for a while
MAX_FAILS, LOCK_SECONDS = 8, 15 * 60


@app.middleware('http')
async def require_pin(request: Request, call_next):
    if request.url.path.startswith(OPEN_PATHS) or request.session.get('unlocked'):
        return await call_next(request)
    if request.headers.get('HX-Request'):
        return Response(status_code=204, headers={'HX-Redirect': '/login'})
    return back('/login')


# Added after require_pin so it runs first (the last middleware added is the outermost): the PIN check needs the session
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie='inv_session', max_age=30 * 24 * 3600,
                   same_site='lax', https_only=ON_RAILWAY)


async def check_csrf(request: Request):
    form = await request.form()
    sent = request.headers.get('X-CSRF-Token') or form.get('csrf', '')
    if not sent or not hmac.compare_digest(str(sent), request.session.get('csrf', '') or '-'):
        raise PermissionError
    return form


@app.exception_handler(PermissionError)
async def csrf_failed(request: Request, exc):
    return PlainTextResponse('This page was open too long or came from somewhere else. Reload and try again.',
                             status_code=403)


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    request.session.setdefault('csrf', secrets.token_urlsafe(24))
    return render(request, 'login.html', error=None, pin_set=bool(os.environ.get('APP_PIN')))


@app.post('/login')
async def login(request: Request):
    form = await check_csrf(request)
    ip = request.client.host if request.client else '?'
    count, first = _fails.get(ip, (0, time.time()))
    if time.time() - first > LOCK_SECONDS:
        count, first = 0, time.time()
    expected = os.environ.get('APP_PIN', '')
    if count >= MAX_FAILS:
        error = 'Too many wrong PINs. Wait 15 minutes and try again.'
    elif expected and hmac.compare_digest(str(form.get('pin', '')).strip(), expected):
        _fails.pop(ip, None)
        request.session.clear()
        request.session.update(unlocked=True, csrf=secrets.token_urlsafe(24))
        return back('/')
    else:
        _fails[ip] = (count + 1, first)
        error = 'Incorrect PIN.'
    return render(request, 'login.html', status_code=401, error=error, pin_set=bool(expected))


@app.post('/logout')
async def logout(request: Request):
    await check_csrf(request)
    request.session.clear()
    return back('/login')


# ── Home ─────────────────────────────────────────────────────────────────────

def home_ctx():
    today = date.today()
    nxt = db.upcoming_classes(engine(), today + timedelta(days=6), limit=1)
    return {'totals': db.totals(engine()), 'to_mark': db.classes_to_mark(engine(), today + timedelta(days=6)),
            'today': today, 'next_class': nxt[0] if nxt else None, 'empty': db.ledger_is_empty(engine())}


@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    return render(request, 'home.html', tab='home', **home_ctx())


@app.post('/classes/{entry_id}/{action}', response_class=HTMLResponse)
async def mark_class(request: Request, entry_id: int, action: str):
    form = await check_csrf(request)
    row = db.get_entry(engine(), entry_id)
    if row is None or action not in ('taught', 'subbed_out', 'cancelled'):
        return Response(status_code=404)
    try:
        hours = float(form.get('hours') or row['hours'] or 0)
    except ValueError:
        hours = row['hours'] or 0
    note = str(form.get('who' if action == 'subbed_out' else 'why', '')).strip()
    if action == 'taught':
        db.set_class_status(engine(), entry_id, 'taught', hours=hours)
        msg = f"{row['description']}: taught, {hours:g} h = {db.money(hours * row['rate_cents'])}."
    elif action == 'subbed_out':
        db.set_class_status(engine(), entry_id, 'subbed_out', comments=f'{note} Subbed' if note else 'Subbed')
        msg = f"{row['description']}: subbed out."
    else:
        db.set_class_status(engine(), entry_id, 'cancelled', comments=note)
        msg = f"{row['description']}: cancelled."
    return render(request, '_home_body.html', msg=msg, **home_ctx())


# ── Log ──────────────────────────────────────────────────────────────────────

def _f(v, default=0.0):
    if v is None or str(v).strip() == '':
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _d(v):
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def _hours(start: str, end: str):
    try:
        s, e = datetime.strptime(start, '%H:%M'), datetime.strptime(end, '%H:%M')
    except ValueError:
        return None, ''
    h = (e - s).total_seconds() / 3600
    h = round(h + 24 if h <= 0 else h, 2)
    return h, f"{s.strftime('%-I:%M')} - {e.strftime('%-I:%M')}"


LOG_KINDS = {'sub': 'Sub', 'trial': 'Trial secretary', 'barter': 'Barter', 'payment': 'Payment',
             'credit_other': 'Other work'}


@app.get('/log', response_class=HTMLResponse)
def log_page(request: Request, kind: str = 'sub'):
    s = db.get_settings(engine())
    return render(request, 'log.html', tab='log', kind=kind if kind in LOG_KINDS else 'sub', LOG_KINDS=LOG_KINDS,
                  today=date.today(), rate=int(s['hourly_rate_cents']), trial_rate=int(s['trial_rate_cents']))


@app.post('/log/{kind}')
async def log_add(request: Request, kind: str):
    form = await check_csrf(request)
    if kind not in LOG_KINDS:
        return Response(status_code=404)
    d = _d(form.get('date')) or date.today()
    desc = str(form.get('description', '')).strip()
    notes = str(form.get('notes', '')).strip()
    if kind == 'sub':
        h, label = _hours(form.get('start', ''), form.get('end', ''))
        if h is None:
            flash(request, 'Enter a start and end time.', 'error')
            return back('/log?kind=sub')
        rate = db.to_cents(_f(form.get('rate')))
        db.add_entry(engine(), 'sub', entry_date=d, description=f'Sub for {desc}' if desc else 'Sub',
                     time_label=label, hours=h, rate_cents=rate, comments=notes)
        flash(request, f'Added sub on {d_short(d)}: {h:g} h = {db.money(h * rate)}.')
    elif kind == 'trial':
        runs, rate = _f(form.get('runs')), db.to_cents(_f(form.get('rate')))
        db.add_entry(engine(), 'trial', entry_date=d, description=desc or 'Trial Secretary', quantity=runs,
                     rate_cents=rate, comments=notes or f'{runs:g} runs at {db.money(rate)} per run')
        flash(request, f'Added {desc or "trial"}: {runs:g} runs = {db.money(runs * rate)}.')
    else:
        value = db.to_cents(_f(form.get('value')))
        db.add_entry(engine(), kind, entry_date=d, description=desc or LOG_KINDS[kind], value_cents=value,
                     comments=notes)
        flash(request, f'Added {LOG_KINDS[kind].lower()}: {desc or LOG_KINDS[kind]}, {db.money(value)}.')
    return back(f'/log?kind={kind}')


# ── Ledger (edit in place) ───────────────────────────────────────────────────

def _ledger_filters(request: Request):
    q = request.query_params
    return {'month': q.get('month', ''), 'kind': q.get('kind', ''), 'inv': q.get('inv', '')}


def _filtered(rows, f):
    return [r for r in rows
            if (not f['month'] or (r['entry_date'] and r['entry_date'].strftime('%Y-%m') == f['month']))
            and (not f['kind'] or r['kind'] == f['kind'])
            and (not f['inv'] or (f['inv'] == 'yes') == (r['invoice_id'] is not None))]


@app.get('/ledger', response_class=HTMLResponse)
def ledger(request: Request):
    rows = db.all_entries(engine())
    f = _ledger_filters(request)
    months = sorted({r['entry_date'].strftime('%Y-%m') for r in rows if r['entry_date']}, reverse=True)
    s = db.get_settings(engine())
    return render(request, 'ledger.html', tab='ledger', rows=_filtered(rows, f), f=f, months=months,
                  totals=db.totals(engine()), today=date.today(), rate=int(s['hourly_rate_cents']),
                  month_label=lambda m: datetime.strptime(m, '%Y-%m').strftime('%B %Y'))


def _row(entry_id: int):
    return next((r for r in db.all_entries(engine()) if r['id'] == entry_id), None)


def _cell_update(row, field: str, value: str) -> dict:
    """Turn one edited cell into update_entry() fields. Raises ValueError on bad input."""
    value = (value or '').strip()
    if field == 'entry_date':
        if value and _d(value) is None:
            raise ValueError('That date is not valid.')
        return {'entry_date': _d(value) if value else None}
    if field in ('description', 'comments', 'time_label'):
        return {field: value}
    if field == 'status':
        if value not in db.CLASS_STATUSES:
            raise ValueError('Pick a status.')
        return {'status': value}
    if field == 'kind':
        if value not in db.KINDS:
            raise ValueError('Pick a type.')
        out = {'kind': value}
        if value == 'class' and not row['status']:
            out['status'] = 'taught'
        if value in ('class', 'sub', 'trial') and not row['rate_cents']:
            out['rate_cents'] = int(db.get_settings(engine())['hourly_rate_cents' if value != 'trial'
                                                             else 'trial_rate_cents'])
        return out
    try:
        n = float(value) if value else 0.0
    except ValueError:
        raise ValueError('Enter a number.')
    if n < 0:
        raise ValueError('Use a positive number. Payments and barter are subtracted automatically.')
    if field == 'qty':
        return {'quantity': n} if row['kind'] == 'trial' else {'hours': n}
    if field == 'rate':
        return {'rate_cents': db.to_cents(n)}
    if field == 'value':
        return {'value_cents': db.to_cents(n)}
    raise ValueError('That column cannot be edited.')


@app.post('/entries/{entry_id}/cell', response_class=HTMLResponse)
async def edit_cell(request: Request, entry_id: int):
    """One cell changed. Swaps in the row's amount and the totals without redrawing the inputs (so Tab keeps
    working); a change of type or status redraws the row because different cells become editable."""
    form = await check_csrf(request)
    field = str(form.get('field', ''))
    row = _row(entry_id)
    if row is None:
        return Response(status_code=404)
    error = None
    try:
        db.update_entry(engine(), entry_id, **_cell_update(row, field, str(form.get('value', ''))))
    except (ValueError, db.LockedError) as e:
        error = str(e)
    row = _row(entry_id)
    redraw = field in ('kind', 'status') or error is not None
    resp = render(request, '_ledger_cell_result.html', r=row, error=error, redraw=redraw, totals=db.totals(engine()))
    if redraw:   # HTMX can't swap a lone <tr> out-of-band, so point the main swap at the row
        resp.headers.update({'HX-Retarget': f'#row-{entry_id}', 'HX-Reswap': 'outerHTML'})
    return resp


@app.post('/entries', response_class=HTMLResponse)
async def add_row(request: Request):
    form = await check_csrf(request)
    kind = str(form.get('kind', 'class'))
    if kind not in db.KINDS:
        return Response(status_code=400)
    s = db.get_settings(engine())
    fields = {'entry_date': _d(form.get('entry_date')) or date.today(),
              'description': str(form.get('description', '')).strip() or db.KINDS[kind]}
    if kind == 'class':
        fields.update(status='taught', hours=_f(form.get('qty'), 0), rate_cents=int(s['hourly_rate_cents']))
    elif kind == 'sub':
        fields.update(hours=_f(form.get('qty'), 0), rate_cents=int(s['hourly_rate_cents']))
    elif kind == 'trial':
        fields.update(quantity=_f(form.get('qty'), 0), rate_cents=int(s['trial_rate_cents']))
    else:
        fields['value_cents'] = db.to_cents(_f(form.get('value'), 0))
    new_id = db.add_entry(engine(), kind, **fields)
    return render(request, '_ledger_added.html', r=_row(new_id), totals=db.totals(engine()), today=date.today())


@app.post('/entries/{entry_id}/delete', response_class=HTMLResponse)
async def delete_row(request: Request, entry_id: int):
    await check_csrf(request)
    try:
        db.delete_entry(engine(), entry_id)
    except db.LockedError as e:
        resp = render(request, '_ledger_cell_result.html', r=None, error=str(e), redraw=False,
                      totals=db.totals(engine()))
        resp.headers['HX-Reswap'] = 'none'      # keep the row where it is
        return resp
    return render(request, '_ledger_deleted.html', entry_id=entry_id, totals=db.totals(engine()))


# ── Sessions ─────────────────────────────────────────────────────────────────

def _session_form(form, defaults):
    first = _d(form.get('first_date')) or defaults['first']
    weeks = max(1, min(20, int(_f(form.get('weeks'), defaults['weeks']))))
    h, label = _hours(form.get('start', '18:00'), form.get('end', '21:00'))
    skips = [d for d in (_d(x) for x in form.getlist('skip')) if d] if hasattr(form, 'getlist') else []
    return {'name': str(form.get('name', '')).strip() or defaults['name'], 'first': first, 'weeks': weeks,
            'start': form.get('start', '18:00'), 'end': form.get('end', '21:00'), 'hours': h or 0, 'label': label,
            'rate': _f(form.get('rate'), defaults['rate']), 'skips': skips}


def _session_defaults():
    existing = db.all_sessions(engine())
    last = existing[0] if existing else None
    try:
        nxt = int(last['name'].split()[-1]) + 1 if last else 1
    except (ValueError, IndexError):
        nxt = len(existing) + 1
    first = (last['first_date'] + timedelta(weeks=last['weeks'])) if last else date.today()
    return existing, {'name': f'Session {nxt}', 'first': max(first, date.today()), 'weeks': last['weeks'] if last else 6,
                      'rate': int(db.get_settings(engine())['hourly_rate_cents']) / 100}


def _preview(p):
    classes, skipped = db.session_dates(p['first'], p['weeks'], p['skips'])
    candidates = [p['first'] + timedelta(weeks=i) for i in range(p['weeks'] + 6)]
    return {'p': p, 'classes': classes, 'skipped': skipped, 'candidates': candidates,
            'per_class': p['hours'] * db.to_cents(p['rate'])}


@app.get('/sessions', response_class=HTMLResponse)
def sessions_page(request: Request):
    existing, defaults = _session_defaults()
    p = _session_form({}, defaults)
    return render(request, 'sessions.html', tab='sessions', sessions=existing, **_preview(p))


@app.post('/sessions/preview', response_class=HTMLResponse)
async def sessions_preview(request: Request):
    form = await check_csrf(request)
    _, defaults = _session_defaults()
    return render(request, '_session_preview.html', **_preview(_session_form(form, defaults)))


@app.post('/sessions')
async def sessions_create(request: Request):
    form = await check_csrf(request)
    _, defaults = _session_defaults()
    p = _session_form(form, defaults)
    if not p['label']:
        flash(request, 'Enter a start and end time.', 'error')
        return back('/sessions')
    classes, _ = db.session_dates(p['first'], p['weeks'], p['skips'])
    db.create_session(engine(), p['name'], p['first'], p['weeks'], p['label'], p['hours'], db.to_cents(p['rate']),
                      p['skips'])
    flash(request, f"Created {p['name']}: {len(classes)} classes, {d_short(classes[0])} to {d_short(classes[-1])}.")
    return back('/sessions')


# ── Invoices ─────────────────────────────────────────────────────────────────

def _invoice_preview(through: date):
    rows = db.invoiceable(engine(), through)
    undated = [r for r in db.all_entries(engine()) if r['entry_date'] is None and r['invoice_id'] is None]
    return {'through': through, 'rows': rows, 'unmarked': db.classes_to_mark(engine(), through), 'undated': undated,
            'undated_total': sum(r['amount_cents'] for r in undated),
            'work': sum(r['amount_cents'] for r in rows if r['amount_cents'] > 0),
            'paid': sum(r['amount_cents'] for r in rows if r['amount_cents'] < 0),
            'next_number': db.next_invoice_number(engine())}


@app.get('/invoices', response_class=HTMLResponse)
def invoices_page(request: Request):
    invs = [db.invoice_detail(engine(), i['id']) for i in db.all_invoices(engine())]
    return render(request, 'invoices.html', tab='invoices', invoices=invs, today=date.today(),
                  **_invoice_preview(date.today()))


@app.get('/invoices/preview', response_class=HTMLResponse)
def invoices_preview(request: Request, through: str = ''):
    return render(request, '_invoice_preview.html', **_invoice_preview(_d(through) or date.today()))


@app.post('/invoices')
async def invoice_create(request: Request):
    form = await check_csrf(request)
    through = _d(form.get('through')) or date.today()
    if not db.invoiceable(engine(), through):
        flash(request, 'Nothing new to invoice up to that date.', 'error')
        return back('/invoices')
    inv_id = db.create_invoice(engine(), through, _d(form.get('invoice_date')) or date.today())
    flash(request, 'Invoice created. Download the PDF, email it, then mark it Sent.')
    return back(f'/invoices#inv-{inv_id}')


@app.get('/invoices/{invoice_id}/pdf')
def invoice_pdf(invoice_id: int):
    det = db.invoice_detail(engine(), invoice_id)
    if det is None:
        return Response(status_code=404)
    s = db.get_settings(engine())
    inv = det['invoice']
    name = f"Invoice {inv['number']} - {s['instructor_name']} - {inv['invoice_date']:%Y-%m-%d}.pdf"
    return Response(build_pdf(det, s), media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{name}"'})


@app.post('/invoices/{invoice_id}/{action}')
async def invoice_action(request: Request, invoice_id: int, action: str):
    form = await check_csrf(request)
    det = db.invoice_detail(engine(), invoice_id)
    if det is None:
        return Response(status_code=404)
    n = det['invoice']['number']
    if action == 'sent':
        db.set_invoice_status(engine(), invoice_id, 'sent')
        flash(request, f'Invoice #{n} marked sent. Its rows are locked now.')
    elif action == 'paid':
        db.set_invoice_status(engine(), invoice_id, 'paid')
        amount = db.to_cents(_f(form.get('amount')))
        if form.get('log_payment') and amount:
            db.add_entry(engine(), 'payment', entry_date=_d(form.get('paid_date')) or date.today(),
                         value_cents=amount, description=f'Payment for Invoice #{n}')
            flash(request, f'Invoice #{n} marked paid and {db.money(amount)} logged as a payment.')
        else:
            flash(request, f'Invoice #{n} marked paid.')
    elif action == 'void':
        db.void_invoice(engine(), invoice_id)
        flash(request, f'Invoice #{n} voided. Its rows are back to not invoiced.')
    else:
        return Response(status_code=404)
    return back('/invoices')


# ── Settings, backup, import ─────────────────────────────────────────────────

@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    return render(request, 'settings.html', tab='settings', s=db.get_settings(engine()),
                  has_rows=not db.ledger_is_empty(engine()))


@app.post('/settings')
async def settings_save(request: Request):
    form = await check_csrf(request)
    db.save_settings(engine(), {
        'instructor_name': form.get('instructor_name', ''), 'instructor_contact': form.get('instructor_contact', ''),
        'client_name': form.get('client_name', ''), 'client_address': form.get('client_address', ''),
        'hourly_rate_cents': db.to_cents(_f(form.get('hourly_rate'), 25)),
        'trial_rate_cents': db.to_cents(_f(form.get('trial_rate'), 1)),
        'payment_note': form.get('payment_note', '')})
    flash(request, 'Settings saved. New rows use the new rates; existing rows keep theirs.')
    return back('/settings')


@app.get('/backup.csv')
def backup():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Date', 'Type', 'Description', 'Status', 'Time', 'Hours', 'Runs', 'Rate', 'Amount', 'Invoice',
                'Comments'])
    for r in db.all_entries(engine()):
        w.writerow([r['entry_date'] or '', db.KINDS[r['kind']], r['description'],
                    db.CLASS_STATUSES.get(r['status'], ''), r['time_label'] or '', num(r['hours']),
                    num(r['quantity']), f"{r['rate_cents'] / 100:.2f}" if r['rate_cents'] else '',
                    f"{r['amount_cents'] / 100:.2f}", r['invoice_number'] or '', r['comments'] or ''])
    return Response(out.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="Level Up ledger {date.today()}.csv"'})


@app.post('/import/preview', response_class=HTMLResponse)
async def import_preview(request: Request):
    form = await check_csrf(request)
    up = form.get('file')
    text = (await up.read()).decode('utf-8-sig') if up and hasattr(up, 'read') else str(form.get('text', ''))
    try:
        parsed = import_csv.parse(text)
    except Exception:
        flash(request, "That file doesn't look like the Debits & Credits spreadsheet (CSV).", 'error')
        return back('/settings')
    credits = sum(r['amount_cents'] for r in parsed['rows'] if r['amount_cents'] > 0)
    debits = sum(r['amount_cents'] for r in parsed['rows'] if r['amount_cents'] < 0)
    return render(request, 'import_preview.html', tab='settings', parsed=parsed, text=text, credits=credits,
                  debits=debits, empty=db.ledger_is_empty(engine()))


@app.post('/import')
async def import_run(request: Request):
    form = await check_csrf(request)
    empty = db.ledger_is_empty(engine())
    if not empty and not form.get('replace'):
        flash(request, 'Tick "Replace everything" to import over the current ledger.', 'error')
        return back('/settings')
    if not empty:
        db.wipe(engine())
    t = import_csv.import_into(engine(), import_csv.parse(str(form.get('text', ''))))
    flash(request, f"Imported. Balance owed {db.money(t['owed'])}. Check the Ledger for the rows flagged on the "
                   f"import screen.")
    return back('/ledger')
