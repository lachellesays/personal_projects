"""
app.py — Level Up invoicing: log classes, subs, trial secretary work, barter and payments; make PDF invoices.

Run locally:   streamlit run app.py
Environment:   DATABASE_URL (Railway Postgres; SQLite file if unset), APP_PIN
"""

import hmac
import io
import os
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import db
import import_csv
from invoice_pdf import build_pdf


def load_dotenv():
    env = Path(__file__).parent / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_dotenv()

st.set_page_config(page_title="Level Up Invoicing", page_icon="🧾", layout="wide")
st.markdown("""
<style>
    .block-container { padding-top: 2.5rem; padding-bottom: 3rem; max-width: 900px; }
    .main-header { font-size: clamp(1.4rem, 5vw, 2rem); font-weight: 800; color: #1E3A8A; margin: 0 0 .25rem 0; }
    .stMain { overflow-x: hidden !important; }
    .stButton button { touch-action: manipulation; }

    /* Class cards on Home: buttons stay side by side on phones */
    [class*="st-key-cls-"] { background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 8px solid #1E3A8A;
                             border-radius: 10px; padding: 10px 12px; gap: .5rem; box-sizing: border-box; }
    [class*="st-key-cls-late-"] { border-left-color: #F59E0B; background: #FFFBEB; }
    [class*="st-key-cls-"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: .5rem !important; }
    [class*="st-key-cls-"] [data-testid="stColumn"] { min-width: 0 !important; }
    [class*="st-key-cls-"] .stButton button, [class*="st-key-cls-"] [data-testid="stPopover"] button {
        min-height: 48px; font-weight: 700; padding-left: 4px; padding-right: 4px; }
    /* Streamlit sizes elements to the full column, which the card's border + padding push past its edge */
    [class*="st-key-cls-"] > *, [class*="st-key-cls-"] .stMarkdown,
    [class*="st-key-cls-"] [data-testid="stNumberInput"] { width: auto !important; max-width: 100% !important; }
    /* The popover arrow made "Cancelled" wrap on phones */
    [class*="st-key-cls-"] [data-testid="stPopover"] button [data-testid="stIconMaterial"] { display: none; }
    .cls-title { font-size: 17px; font-weight: 800; color: #0F172A; overflow-wrap: anywhere; }
    .cls-sub { font-size: 14px; color: #475569; }
</style>
""", unsafe_allow_html=True)

ENGINE = st.cache_resource(db.get_engine)()
if os.environ.get('RAILWAY_ENVIRONMENT_NAME') and ENGINE.dialect.name == 'sqlite':
    st.error('DATABASE_URL is not set (or is empty), so this app is using a temporary file that is erased on '
             'every deploy. In Railway, add the variable DATABASE_URL = ${{Postgres.DATABASE_URL}} and redeploy.')

st.markdown('<p class="main-header">Level Up Invoicing</p>', unsafe_allow_html=True)


def pin_unlocked() -> bool:
    """Show a PIN form until the right PIN is entered; remember it for this browser session."""
    if st.session_state.get('unlocked'):
        return True
    expected = os.environ.get('APP_PIN')
    if not expected:
        st.error('APP_PIN is not set. Add it as a Railway variable (or to a .env file when running locally).')
        return False
    with st.form('pin_form'):
        pin = st.text_input('PIN:', type='password')
        if st.form_submit_button('Enter', use_container_width=True, type='primary'):
            if hmac.compare_digest(pin.strip(), expected):
                st.session_state.unlocked = True
                st.rerun()
            st.error('Incorrect PIN.')
    return False


if not pin_unlocked():
    st.stop()

S = db.get_settings(ENGINE)
RATE = int(S['hourly_rate_cents'])
today = date.today()


def m(cents) -> str:
    """Money for on-screen text: Streamlit reads two $ signs on a line as a math formula, so escape them."""
    return db.money(cents).replace('$', '\\$')


def flash(msg: str):
    st.session_state.flash = msg
    st.rerun()


def show_flash():
    if 'flash' in st.session_state:
        st.success(st.session_state.pop('flash'))


def time_label(start: dtime, end: dtime) -> str:
    return f"{start.strftime('%-I:%M')} - {end.strftime('%-I:%M')}"


def hours_between(start: dtime, end: dtime) -> float:
    h = (datetime.combine(today, end) - datetime.combine(today, start)).total_seconds() / 3600
    return round(h + 24 if h <= 0 else h, 2)


def d_short(d) -> str:
    return d.strftime('%a %-m/%-d') if d else 'no date'


show_flash()


# --- TAB: HOME ---
def render_home():
    t = db.totals(ENGINE)
    c1, c2 = st.columns(2)
    c1.metric("Balance owed", db.money(t['owed']))
    c2.metric("Work not yet invoiced", db.money(t['new_work']))
    if t['new_debits']:
        st.caption(f"Payments & barter since the last invoice: {m(t['new_debits'])}")

    to_mark = db.classes_to_mark(ENGINE, today + timedelta(days=6))
    st.subheader("Classes to mark")
    if not to_mark:
        nxt = db.upcoming_classes(ENGINE, today + timedelta(days=6), limit=1)
        st.caption("Nothing to mark this week." + (f" Next class: {nxt[0]['description']}, {d_short(nxt[0]['entry_date'])}."
                                                  if nxt else " Add your next session on the Sessions tab."))
    for r in to_mark:
        late = r['entry_date'] < today
        with st.container(key=f"cls-{'late-' if late else ''}{r['id']}"):
            when = 'Needs marking' if late else ('Today' if r['entry_date'] == today else 'Coming up')
            st.markdown(f'<div class="cls-title">{r["description"]}</div>'
                        f'<div class="cls-sub">{d_short(r["entry_date"])} · {r["time_label"] or ""} · {when}</div>',
                        unsafe_allow_html=True)
            hours = st.number_input("Hours", min_value=0.0, max_value=12.0, step=0.5, value=float(r['hours'] or 0),
                                    key=f"h_{r['id']}", help="Change this if the class ran shorter or longer")
            b1, b2, b3 = st.columns(3)
            if b1.button("Taught", key=f"taught_{r['id']}", type="primary", use_container_width=True):
                db.set_class_status(ENGINE, r['id'], 'taught', hours=hours)
                flash(f"{r['description']}: taught, {hours:g} h = {m(hours * r['rate_cents'])}.")
            with b2.popover("Subbed", use_container_width=True):
                who = st.text_input("Who subbed?", key=f"who_{r['id']}")
                if st.button("Save", key=f"sub_{r['id']}", use_container_width=True):
                    db.set_class_status(ENGINE, r['id'], 'subbed_out',
                                        comments=f"{who.strip()} Subbed" if who.strip() else 'Subbed')
                    flash(f"{r['description']}: subbed out.")
            with b3.popover("Cancelled", use_container_width=True):
                why = st.text_input("Reason (optional)", key=f"why_{r['id']}", placeholder="e.g. Snow")
                if st.button("Save", key=f"cxl_{r['id']}", use_container_width=True):
                    db.set_class_status(ENGINE, r['id'], 'cancelled', comments=why.strip())
                    flash(f"{r['description']}: cancelled.")

    if not db.all_entries(ENGINE):
        st.info("No data yet. Import your 2026 spreadsheet on the Settings tab, or add a session on the Sessions tab.")


# --- TAB: LOG ---
def render_log():
    kind = st.segmented_control("What are you logging?", ["Sub", "Trial secretary", "Barter", "Payment", "Other work"],
                                default="Sub", key="log_kind")
    if kind == "Sub":
        with st.form("f_sub", clear_on_submit=True):
            d = st.date_input("Date", value=today, format="MM/DD/YYYY")
            who = st.text_input("Whose class?", placeholder="e.g. James")
            c1, c2 = st.columns(2)
            start = c1.time_input("Start", value=dtime(18, 0), step=900)
            end = c2.time_input("End", value=dtime(20, 0), step=900)
            rate = st.number_input("Hourly rate ($)", min_value=0.0, value=RATE / 100, step=1.0)
            notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Add sub", type="primary", use_container_width=True):
                h = hours_between(start, end)
                db.add_entry(ENGINE, 'sub', entry_date=d, description=f"Sub for {who.strip()}" if who.strip() else "Sub",
                             time_label=time_label(start, end), hours=h, rate_cents=db.to_cents(rate), comments=notes)
                flash(f"Added sub on {d_short(d)}: {h:g} h = {m(h * db.to_cents(rate))}.")
    elif kind == "Trial secretary":
        with st.form("f_trial", clear_on_submit=True):
            name = st.text_input("Trial", placeholder="e.g. UKI Trial Nov 2026")
            d = st.date_input("Date (last day of the trial)", value=today, format="MM/DD/YYYY")
            c1, c2 = st.columns(2)
            runs = c1.number_input("Runs", min_value=0, step=1)
            rate = c2.number_input("$ per run", min_value=0.0, value=int(S['trial_rate_cents']) / 100, step=0.25)
            notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Add trial", type="primary", use_container_width=True):
                db.add_entry(ENGINE, 'trial', entry_date=d, description=name.strip() or "Trial Secretary",
                             quantity=runs, rate_cents=db.to_cents(rate),
                             comments=notes or f"{runs} runs at {m(db.to_cents(rate))} per run")
                flash(f"Added {name.strip() or 'trial'}: {runs} runs = {m(runs * db.to_cents(rate))}.")
    elif kind in ("Barter", "Payment", "Other work"):
        k = {'Barter': 'barter', 'Payment': 'payment', 'Other work': 'credit_other'}[kind]
        prompts = {'barter': ("What did you get?", "e.g. Wall jump, or Trial 4/17-4/19 entries"),
                   'payment': ("Description", "e.g. Payment for Invoice #2"),
                   'credit_other': ("What was the work?", "e.g. Course building")}
        with st.form(f"f_{k}", clear_on_submit=True):
            d = st.date_input("Date", value=today, format="MM/DD/YYYY")
            desc = st.text_input(prompts[k][0], placeholder=prompts[k][1])
            value = st.number_input("Value ($)" if k == 'barter' else "Amount ($)", min_value=0.0, step=5.0)
            notes = st.text_input("Notes (optional)")
            if k == 'barter':
                st.caption("Barter counts like a payment: its value comes off what you're owed.")
            if st.form_submit_button(f"Add {kind.lower()}", type="primary", use_container_width=True):
                db.add_entry(ENGINE, k, entry_date=d, description=desc.strip() or kind,
                             value_cents=db.to_cents(value), comments=notes)
                flash(f"Added {kind.lower()}: {desc.strip() or kind}, {m(db.to_cents(value))}.")


# --- TAB: LEDGER ---
def _qty_text(r) -> str:
    if r['kind'] == 'trial':
        return f"{r['quantity']:g} runs"
    if r['kind'] in ('class', 'sub') and r['hours']:
        return f"{r['hours']:g} h"
    return ''


def render_ledger():
    t = db.totals(ENGINE)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total credits", db.money(t['credits']))
    c2.metric("Total debits", db.money(t['debits']))
    c3.metric("Total owed", db.money(t['owed']))

    rows = db.all_entries(ENGINE)
    if not rows:
        st.info("No rows yet.")
        return
    months = sorted({r['entry_date'].strftime('%Y-%m') for r in rows if r['entry_date']}, reverse=True)
    f1, f2, f3 = st.columns(3)
    month = f1.selectbox("Month", ["All"] + months,
                         format_func=lambda m: m if m == "All" else datetime.strptime(m, '%Y-%m').strftime('%B %Y'))
    kinds = f2.multiselect("Type", list(db.KINDS), format_func=db.KINDS.get, placeholder="All types")
    inv = f3.selectbox("Invoiced", ["All", "Not invoiced", "Invoiced"])
    shown = [r for r in rows
             if (month == "All" or (r['entry_date'] and r['entry_date'].strftime('%Y-%m') == month))
             and (not kinds or r['kind'] in kinds)
             and (inv == "All" or (inv == "Invoiced") == (r['invoice_id'] is not None))]
    table = pd.DataFrame([{
        'Date': r['entry_date'], 'Type': db.KINDS[r['kind']], 'Description': r['description'],
        'Status': db.CLASS_STATUSES.get(r['status'], ''), 'Qty': _qty_text(r),
        'Amount': r['amount_cents'] / 100, 'Invoice': f"#{r['invoice_number']}" if r['invoice_number'] else '',
        'Comments': r['comments'] or '',
    } for r in shown])
    st.caption(f"{len(shown)} row(s). Tap a row to edit it.")
    picked = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun",
                          selection_mode="single-row", key="ledger_table",
                          column_config={'Amount': st.column_config.NumberColumn(format="$%.2f"),
                                         'Date': st.column_config.DateColumn(format="MM/DD/YYYY")})
    sel = picked.selection.rows if picked else []
    if sel:
        edit_entry(shown[sel[0]])


def edit_entry(r):
    st.subheader(f"Edit: {r['description']}")
    if r['invoice_status'] in db.LOCKED_STATUSES:
        st.info(f"This row is on invoice #{r['invoice_number']}, which is {r['invoice_status']}. "
                f"Void that invoice on the Invoices tab to change it.")
        return
    k = r['kind']
    with st.form(f"edit_{r['id']}"):
        d = st.date_input("Date", value=r['entry_date'], format="MM/DD/YYYY")
        desc = st.text_input("Description", value=r['description'])
        fields = {}
        if k == 'class':
            statuses = list(db.CLASS_STATUSES)
            fields['status'] = st.selectbox("Status", statuses, index=statuses.index(r['status']),
                                            format_func=db.CLASS_STATUSES.get)
        if k in ('class', 'sub'):
            c1, c2, c3 = st.columns(3)
            fields['time_label'] = c1.text_input("Time", value=r['time_label'] or '')
            fields['hours'] = c2.number_input("Hours", min_value=0.0, step=0.5, value=float(r['hours'] or 0))
            fields['rate_cents'] = db.to_cents(c3.number_input("Rate ($/h)", min_value=0.0, step=1.0,
                                                               value=(r['rate_cents'] or RATE) / 100))
        elif k == 'trial':
            c1, c2 = st.columns(2)
            fields['quantity'] = c1.number_input("Runs", min_value=0.0, step=1.0, value=float(r['quantity'] or 0))
            fields['rate_cents'] = db.to_cents(c2.number_input("$ per run", min_value=0.0, step=0.25,
                                                               value=(r['rate_cents'] or 0) / 100))
        else:
            fields['value_cents'] = db.to_cents(st.number_input("Value ($)", min_value=0.0, step=5.0,
                                                                value=abs(r['amount_cents']) / 100))
        comments = st.text_input("Comments", value=r['comments'] or '')
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save", type="primary", use_container_width=True)
        really = c2.checkbox("Delete this row")
        remove = c2.form_submit_button("Delete", use_container_width=True)
    if save:
        db.update_entry(ENGINE, r['id'], entry_date=d, description=desc.strip(), comments=comments, **fields)
        flash("Saved.")
    if remove:
        if really:
            db.delete_entry(ENGINE, r['id'])
            flash("Deleted.")
        st.warning("Tick 'Delete this row' first.")


# --- TAB: SESSIONS ---
def render_sessions():
    existing = db.all_sessions(ENGINE)
    last = existing[0] if existing else None
    st.subheader("New session")
    try:
        next_num = int(last['name'].split()[-1]) + 1 if last else 1
    except (ValueError, IndexError):
        next_num = len(existing) + 1
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Name", value=f"Session {next_num}")
    default_first = (last['first_date'] + timedelta(weeks=last['weeks'])) if last else today
    first = c2.date_input("First class", value=max(default_first, today), format="MM/DD/YYYY")
    weeks = c3.number_input("Weeks", min_value=1, max_value=20, value=last['weeks'] if last else 6)
    c1, c2, c3 = st.columns(3)
    start = c1.time_input("Start", value=dtime(18, 0), step=900)
    end = c2.time_input("End", value=dtime(21, 0), step=900)
    rate = c3.number_input("Hourly rate ($)", min_value=0.0, value=RATE / 100, step=1.0)
    candidates = [first + timedelta(weeks=i) for i in range(int(weeks) + 6)]
    skip = st.multiselect("Weeks with no class (workshop weeks, holidays)", candidates, format_func=d_short)
    class_dates, skipped = db.session_dates(first, int(weeks), skip)
    hours = hours_between(start, end)
    st.caption(f"{first:%A}s, {time_label(start, end)} ({hours:g} h × {m(db.to_cents(rate))} = "
               f"{m(hours * db.to_cents(rate))} per class)")
    st.markdown("**Classes:** " + ", ".join(f"Wk {i} {d_short(d)}" for i, d in enumerate(class_dates, 1))
                + (("  \n**No class:** " + ", ".join(d_short(d) for d in skipped)) if skipped else ""))
    if st.button("Create session", type="primary"):
        db.create_session(ENGINE, name.strip(), first, int(weeks), time_label(start, end), hours,
                          db.to_cents(rate), skip)
        flash(f"Created {name.strip()}: {len(class_dates)} classes from {d_short(class_dates[0])} "
              f"to {d_short(class_dates[-1])}.")

    if existing:
        st.subheader("Sessions")
        st.dataframe(pd.DataFrame([{'Session': s['name'], 'Starts': s['first_date'], 'Weeks': s['weeks'],
                                    'Time': s['time_label'], 'Hours': s['hours'], 'Rate': s['rate_cents'] / 100}
                                   for s in existing]),
                     hide_index=True, use_container_width=True,
                     column_config={'Rate': st.column_config.NumberColumn(format="$%.2f"),
                                    'Starts': st.column_config.DateColumn(format="MM/DD/YYYY")})


# --- TAB: INVOICES ---
def render_invoices():
    st.subheader("New invoice")
    c1, c2 = st.columns(2)
    through = c1.date_input("Include work through", value=today, format="MM/DD/YYYY")
    inv_date = c2.date_input("Invoice date", value=today, format="MM/DD/YYYY")
    rows = db.invoiceable(ENGINE, through)
    unmarked = db.classes_to_mark(ENGINE, through)
    undated = [r for r in db.all_entries(ENGINE) if r['entry_date'] is None and r['invoice_id'] is None]
    if unmarked:
        st.warning(f"{len(unmarked)} class(es) up to {d_short(through)} are still Scheduled and won't be included: "
                   + ", ".join(r['description'] for r in unmarked) + ". Mark them on the Home tab first.")
    if undated:
        st.warning(f"{len(undated)} row(s) have no date and won't be included: "
                   + ", ".join(f"{r['description']} ({m(r['amount_cents'])})" for r in undated)
                   + f". Until they're dated, the balance on this invoice is off by "
                   f"{m(-sum(r['amount_cents'] for r in undated))}. Give them a date on the Ledger tab.")
    if rows:
        work = sum(r['amount_cents'] for r in rows if r['amount_cents'] > 0)
        paid = sum(r['amount_cents'] for r in rows if r['amount_cents'] < 0)
        st.write(f"{len(rows)} row(s): work {m(work)}, payments & barter {m(paid)}.")
        if st.button(f"Create invoice #{db.next_invoice_number(ENGINE)}", type="primary"):
            db.create_invoice(ENGINE, through, inv_date)
            flash("Invoice created. Download the PDF below, then mark it Sent.")
    else:
        st.caption("Nothing new to invoice up to that date.")

    invs = db.all_invoices(ENGINE)
    if not invs:
        return
    st.subheader("Invoices")
    for inv in invs:
        det = db.invoice_detail(ENGINE, inv['id'])
        label = (f"#{inv['number']} · {inv['invoice_date']:%-m/%-d/%Y} · {inv['status'].title()} · "
                 f"balance {m(det['balance'])}" + (f" · {inv['title']}" if inv['title'] else ''))
        with st.expander(label, expanded=inv['status'] == 'draft'):
            st.write(f"Previous balance {m(det['previous'])} + work {m(det['credits'])} "
                     f"− payments & barter {m(-det['debits'])} = **{m(det['balance'])}**")
            st.download_button("Download PDF", build_pdf(det, S), file_name=f"Invoice {inv['number']} - "
                               f"{S['instructor_name']} - {inv['invoice_date']:%Y-%m-%d}.pdf",
                               mime="application/pdf", key=f"pdf_{inv['id']}", use_container_width=True)
            c1, c2 = st.columns(2)
            if inv['status'] == 'draft' and c1.button("Mark sent", key=f"sent_{inv['id']}", use_container_width=True):
                db.set_invoice_status(ENGINE, inv['id'], 'sent')
                flash(f"Invoice #{inv['number']} marked sent. Its rows are locked now.")
            if inv['status'] != 'paid':
                with c2.popover("Mark paid", use_container_width=True):
                    log = st.checkbox("Also log the payment", value=True, key=f"logpay_{inv['id']}")
                    amt = st.number_input("Amount received ($)", min_value=0.0, step=5.0,
                                          value=max(det['balance'], 0) / 100, key=f"amt_{inv['id']}")
                    pdate = st.date_input("Date received", value=today, format="MM/DD/YYYY", key=f"pd_{inv['id']}")
                    st.caption("Paid in barter instead? Untick this and log it on the Log tab.")
                    if st.button("Save", key=f"paid_{inv['id']}", use_container_width=True):
                        db.set_invoice_status(ENGINE, inv['id'], 'paid')
                        if log and amt:
                            db.add_entry(ENGINE, 'payment', entry_date=pdate, value_cents=db.to_cents(amt),
                                         description=f"Payment for Invoice #{inv['number']}")
                        flash(f"Invoice #{inv['number']} marked paid.")
            with st.popover("Void invoice"):
                st.write("Deletes this invoice and puts its rows back to not invoiced.")
                if st.button("Void it", key=f"void_{inv['id']}"):
                    db.void_invoice(ENGINE, inv['id'])
                    flash(f"Invoice #{inv['number']} voided.")


# --- TAB: SETTINGS ---
def render_settings():
    st.subheader("Invoice details")
    with st.form("f_settings"):
        name = st.text_input("Your name", value=S['instructor_name'])
        contact = st.text_area("Your contact info (shown under your name)", value=S['instructor_contact'],
                               placeholder="Email\nPhone")
        client = st.text_input("Bill to", value=S['client_name'])
        address = st.text_area("Their address / contact", value=S['client_address'])
        c1, c2 = st.columns(2)
        rate = c1.number_input("Default hourly rate ($)", min_value=0.0, value=RATE / 100, step=1.0)
        trate = c2.number_input("Default trial rate ($ per run)", min_value=0.0,
                                value=int(S['trial_rate_cents']) / 100, step=0.25)
        note = st.text_area("Note at the bottom of invoices", value=S['payment_note'],
                            placeholder="e.g. Thank you! Payment by Venmo @...")
        if st.form_submit_button("Save", type="primary"):
            db.save_settings(ENGINE, {'instructor_name': name, 'instructor_contact': contact, 'client_name': client,
                                      'client_address': address, 'hourly_rate_cents': db.to_cents(rate),
                                      'trial_rate_cents': db.to_cents(trate), 'payment_note': note})
            flash("Settings saved. New rows use the new rates; existing rows keep theirs.")

    st.subheader("Backup")
    rows = db.all_entries(ENGINE)
    if rows:
        out = pd.DataFrame([{'Date': r['entry_date'], 'Type': db.KINDS[r['kind']], 'Description': r['description'],
                             'Status': db.CLASS_STATUSES.get(r['status'], ''), 'Time': r['time_label'],
                             'Hours': r['hours'], 'Runs': r['quantity'],
                             'Rate': (r['rate_cents'] or 0) / 100 if r['rate_cents'] else None,
                             'Amount': r['amount_cents'] / 100, 'Invoice': r['invoice_number'],
                             'Comments': r['comments']} for r in rows])
        st.download_button("Download everything (CSV)", out.to_csv(index=False).encode(),
                           file_name=f"Level Up ledger {today:%Y-%m-%d}.csv", mime="text/csv")
        st.caption("Railway doesn't back up the database on your plan, so download this now and then.")

    st.subheader("Import the old spreadsheet")
    up = st.file_uploader("Level Up Debits & Credits (CSV)", type=['csv'])
    if up:
        parsed = import_csv.parse(up.getvalue().decode('utf-8-sig'))
        credits = sum(r['amount_cents'] for r in parsed['rows'] if r['amount_cents'] > 0)
        debits = sum(r['amount_cents'] for r in parsed['rows'] if r['amount_cents'] < 0)
        st.write(f"{len(parsed['rows'])} rows: credits {m(credits)}, debits {m(debits)}, "
                 f"owed **{m(credits + debits)}**.")
        sheet = parsed['sheet_totals']
        if sheet.get('Total Owed') is not None:
            ok = sheet['Total Owed'] == credits + debits
            (st.success if ok else st.warning)(f"The sheet says Total Owed {m(sheet['Total Owed'])}"
                                               + (" ✓ matches." if ok else ". These don't match; check the rows below."))
        if parsed['review']:
            st.markdown("**Worth a look after importing:**\n" + "\n".join(f"- {x}" for x in parsed['review']))
        empty = db.ledger_is_empty(ENGINE)
        replace = empty or st.checkbox("Replace everything currently in the app with this spreadsheet")
        if st.button("Import", type="primary", disabled=not replace):
            if not empty:
                db.wipe(ENGINE)
            t = import_csv.import_into(ENGINE, parsed)
            flash(f"Imported. Balance owed {m(t['owed'])}.")


TABS = [("Home", render_home), ("Log", render_log), ("Ledger", render_ledger), ("Sessions", render_sessions),
        ("Invoices", render_invoices), ("Settings", render_settings)]
for tab, (_, render) in zip(st.tabs([t[0] for t in TABS]), TABS):
    with tab:
        render()
