"""
app.py — UKI trial check-in, running order, gate steward, admin and results.

Exhibitors use the plain link (Check-in, Order, Results).
Staff add ?staff to the link to also get Gate, Dash and Admin (each still PIN-protected).

Run locally:   streamlit run app.py
Environment:   DATABASE_URL (Railway Postgres; SQLite file if unset), GATE_PIN, ADMIN_PIN, TRIAL_TZ (optional)
"""

import hmac
import html
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_sortables import sort_items

import db
from cached import engine, read
from run_order import RunOrderError, parse_run_order_csv, summarize


def load_dotenv():
    env = Path(__file__).parent / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_dotenv()

# --- 1. PAGE CONFIG & STYLING ---
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2.5rem; padding-bottom: 3rem; max-width: 900px; }
    .main-header { font-size: clamp(1.4rem, 5vw, 2rem); font-weight: 800; color: #1E3A8A; margin: 0 0 .25rem 0; }

    /* Keep button rows side by side on phones instead of stacking */
    .st-key-checkin [data-testid="stHorizontalBlock"], .st-key-gate [data-testid="stHorizontalBlock"],
    .st-key-dash [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important; gap: .5rem !important;
    }
    .st-key-checkin [data-testid="stColumn"], .st-key-gate [data-testid="stColumn"],
    .st-key-dash [data-testid="stColumn"] { min-width: 0 !important; }

    /* Big touch targets only where they matter */
    .st-key-checkin .stButton button { min-height: 48px; font-weight: 700; }
    .st-key-gate .stButton button { min-height: 56px; font-size: 17px; font-weight: 800; }

    /* Class chips: one row that scrolls sideways instead of wrapping onto many lines */
    .st-key-ro_sel [data-testid="stButtonGroup"] > div, .st-key-g_cls [data-testid="stButtonGroup"] > div,
    .st-key-res_view_class_sel [data-testid="stButtonGroup"] > div {
        flex-wrap: nowrap !important; overflow-x: auto; scrollbar-width: thin; padding-bottom: 6px;
    }
    [data-testid="stButtonGroup"] button { flex-shrink: 0; }
    .st-key-checkin [data-testid="stPopover"] button { min-height: 48px; }

    /* Running order list */
    .ro-strip { background: #EEF2FF; border: 1px solid #C7D2FE; border-radius: 10px; padding: 10px 12px; margin: 6px 0 10px; }
    .ro-strip b { color: #1E3A8A; }
    .ro-mine { background: #E0F2FE; border-left: 5px solid #0284C7; border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; font-weight: 600; }
    .ro-ht { font-size: 13px; font-weight: 800; color: #475569; letter-spacing: .03em; margin: 12px 0 4px; text-transform: uppercase; }
    .ro-row { display: grid; grid-template-columns: 2rem 1fr auto; align-items: center; gap: 8px;
              padding: 9px 10px; border-bottom: 1px solid #E2E8F0; }
    .ro-num { color: #64748B; font-weight: 700; text-align: right; }
    .ro-dog { font-size: 17px; font-weight: 700; color: #0F172A; line-height: 1.2; }
    .ro-sub { font-size: 13px; color: #64748B; }
    .ro-row.mine { background: #E0F2FE; }
    .you { font-size: 11px; font-weight: 800; color: #FFFFFF; background: #0284C7; border-radius: 4px; padding: 1px 5px; vertical-align: 2px; }
    .ro-row.inring { background: #FEF3C7; border-left: 5px solid #F59E0B; }
    .ro-row.done .ro-dog, .ro-row.done .ro-sub, .ro-row.done .ro-num { color: #94A3B8; font-style: italic; }
    .ro-row.scratch .ro-dog { text-decoration: line-through; color: #94A3B8; }

    .pill { font-size: 12px; font-weight: 700; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }
    .pill-notin { background: #F1F5F9; color: #475569; }
    .pill-in    { background: #DCFCE7; color: #166534; }
    .pill-ring  { background: #F59E0B; color: #FFFFFF; }
    .pill-done  { background: #E2E8F0; color: #64748B; }
    .pill-scr   { background: #FEE2E2; color: #B91C1C; }
    .pill-other { background: #FEF3C7; color: #92400E; }

    /* Gate cards: colored left border by status */
    [class*="st-key-card-"] { background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 10px solid #94A3B8;
                              border-radius: 10px; padding: 10px 12px; gap: .6rem; box-sizing: border-box; }
    [class*="st-key-card-"] > *, [class*="st-key-card-"] .stButton { max-width: 100% !important; }
    [class*="st-key-card-"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
    [class*="st-key-card-ring-"]  { border-left-color: #F59E0B; background: #FFFBEB; }
    [class*="st-key-card-in-"]    { border-left-color: #16A34A; }
    [class*="st-key-card-scr-"]   { border-left-color: #DC2626; }
    [class*="st-key-card-done-"]  { opacity: .6; }
    .card-title { font-size: 19px; font-weight: 800; color: #0F172A; }
    .card-sub { font-size: 14px; color: #475569; }
    /* "Now running" strip stays pinned while the steward scrolls the class */
    [data-testid="stVerticalBlockBorderWrapper"]:has(> div > .st-key-gate-strip) {
        position: sticky; top: 3rem; z-index: 50; background: #FFFFFF; padding-top: 4px;
    }
    .gate-banner { text-align: center; margin: 10px 0; padding: 8px; border-radius: 8px; font-weight: 800; font-size: 15px; }

    .height-header { background-color: rgba(30, 58, 138, 0.08); padding: 8px 10px; border-radius: 8px;
                     border-left: 5px solid #1E3A8A; margin-top: 16px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


# --- 2. DATABASE ---
ENGINE = engine()  # shared with cached.py, so the whole app uses one connection pool
if os.environ.get('RAILWAY_ENVIRONMENT_NAME') and ENGINE.dialect.name == 'sqlite':
    if 'DATABASE_URL' in os.environ:
        st.error('DATABASE_URL exists but is empty, so this app is using a temporary file that is erased on '
                 'every deploy. The reference probably doesn\'t match your database\'s name: edit the variable, '
                 'type ${{ and pick your Postgres service from the suggestions, then click Deploy.')
    else:
        st.error('DATABASE_URL is not set, so this app is using a temporary file that is erased on every deploy. '
                 'In Railway, add the variable DATABASE_URL = ${{Postgres.DATABASE_URL}} and click Deploy '
                 'on the "Apply changes" banner.')

STAFF = 'staff' in st.query_params
st.markdown(f'<p class="main-header">UKI Trial {"Staff Portal" if STAFF else "Center"}</p>', unsafe_allow_html=True)


# --- 3. HELPERS ---
def pin_unlocked(kind: str, env_var: str, label: str) -> bool:
    """Show a PIN form until the right PIN is entered; remember it for this browser session."""
    if st.session_state.get(f'unlocked_{kind}'):
        return True
    expected = os.environ.get(env_var)
    if not expected:
        st.error(f'{env_var} is not set. Add it as a Railway variable.')
        return False
    with st.form(f'{kind}_pin_form'):
        pin = st.text_input(f'{label}:', type='password')
        if st.form_submit_button('Enter', use_container_width=True, type='primary'):
            if hmac.compare_digest(pin.strip(), expected):
                st.session_state[f'unlocked_{kind}'] = True
                st.rerun()
            st.error('Incorrect PIN.')
    return False


def sticky_choice(key: str, options: list, default=None):
    """Keep a pills/segmented selection valid; clicking the selected chip again won't clear it."""
    if not options:
        st.session_state[key] = None
        return
    if st.session_state.get(key) not in options:
        last = st.session_state.get(f'{key}__last')
        st.session_state[key] = last if last in options else (default if default in options else options[0])
    st.session_state[f'{key}__last'] = st.session_state[key]


def current_class():
    """The class running now (someone In Ring), else the first one not finished."""
    if df.empty:
        return None
    for status_test in (lambda s: (s == 'In Ring').any(), lambda s: (~s.isin(FINISHED)).any()):
        for cls, g in df.groupby('class_name', sort=False):
            if status_test(g['status']):
                return cls
    return sorted_classes[0] if sorted_classes else None


def class_picker(state_key: str, label: str = "Class"):
    """Compact row of class chips, starting on the class that's running now. Returns the selection."""
    sticky_choice(state_key, sorted_classes, current_class())
    st.pills(label, sorted_classes, key=state_key, label_visibility="collapsed")
    return st.session_state[state_key]


def run_label(r) -> str:
    return f'{r["height"]}" {r["class_type"]} · {r["dog_name"]} — {r["handler_name"]}'


def esc(v) -> str:
    return html.escape(str(v if v is not None else ''))


PILL = {'Not Checked In': ('pill-notin', 'Not checked in'), 'Checked In': ('pill-in', 'Checked in'),
        'In Ring': ('pill-ring', 'In ring'), 'Run Completed': ('pill-done', 'Done'),
        'Scratch': ('pill-scr', 'Scratched'), 'Conflict': ('pill-other', 'Conflict'), 'NFC': ('pill-other', 'NFC')}


def pill(status: str) -> str:
    cls, text = PILL.get(status, ('pill-other', status))
    return f'<span class="pill {cls}">{esc(text)}</span>'


FINISHED = ('Run Completed', 'Scratch')


def class_progress(class_df: pd.DataFrame):
    """(in_ring_row, on_deck_rows, started, complete) for one class, in running order."""
    rows = class_df.to_dict('records')
    ring_idx = next((i for i, r in enumerate(rows) if r['status'] == 'In Ring'), None)
    started = ring_idx is not None or any(r['status'] == 'Run Completed' for r in rows)
    start = ring_idx + 1 if ring_idx is not None else 0
    on_deck = [r for r in rows[start:] if r['status'] not in FINISHED + ('In Ring',)][:2]
    complete = bool(rows) and all(r['status'] in FINISHED for r in rows)
    return (rows[ring_idx] if ring_idx is not None else None), on_deck, started, complete


def dogs_ahead(class_df: pd.DataFrame, run_id: int) -> str:
    """Short 'where am I' note for one run."""
    rows = class_df.to_dict('records')
    idx = next(i for i, r in enumerate(rows) if int(r['id']) == run_id)
    me = rows[idx]
    if me['status'] == 'In Ring':
        return 'In the ring now'
    if me['status'] == 'Run Completed':
        return 'Done'
    if me['status'] == 'Scratch':
        return ''
    _, _, started, _ = class_progress(class_df)
    if not started:
        return f'#{idx + 1} of {len(rows)} · class not started'
    ahead = sum(1 for r in rows[:idx] if r['status'] not in FINISHED)
    return "You're next" if ahead == 0 else f'{ahead} dog{"s" if ahead != 1 else ""} ahead of you'


def now_strip_html(class_df: pd.DataFrame) -> str:
    in_ring, on_deck, started, complete = class_progress(class_df)
    if complete:
        return '<div class="ro-strip"><b>Class complete</b></div>'
    deck = ', '.join(esc(r['dog_name']) for r in on_deck) or '—'
    if in_ring:
        return (f'<div class="ro-strip"><b>Now running:</b> {esc(in_ring["dog_name"])} '
                f'<span class="ro-sub">({esc(in_ring["handler_name"])})</span><br>'
                f'<b>On deck:</b> {deck}</div>')
    label = 'Up next' if started else 'Not started yet · First up'
    return f'<div class="ro-strip"><b>{label}:</b> {deck}</div>'


# --- 4. DAY SELECTION ---
TZ = ZoneInfo(os.environ.get('TRIAL_TZ', 'America/New_York'))
dates = read('trial_dates')
today = datetime.now(TZ).date()


def _default_day(ds):
    if today in ds:
        return today
    upcoming = [d for d in ds if d > today]
    return upcoming[0] if upcoming else ds[-1]


def _day_label(d: date) -> str:
    return d.strftime('%A %-m/%-d/%Y')


def clock() -> str:
    """Current time in the trial's time zone (Railway's servers run on UTC)."""
    return datetime.now(TZ).strftime('%-I:%M:%S %p')


def _day_short(d: date) -> str:
    return d.strftime('%a %-m/%-d')


if dates:
    if 'pending_day' in st.session_state:  # set by the upload page; applied before the widget exists
        st.session_state.day = st.session_state.pop('pending_day')
    sticky_choice('day', dates, _default_day(dates))
    if len(dates) > 1:
        st.segmented_control('Trial day', dates, format_func=_day_short, key='day', label_visibility='collapsed')
    day = st.session_state.day
else:
    day = None

df = read('runs_for_day', day) if day else pd.DataFrame(columns=[c.name for c in db.runs.columns])
sorted_classes = read('class_names', day) if day else []

# Handler number survives refreshes and bookmarks via ?h=12345
if 'active_handler' not in st.session_state:
    st.session_state.active_handler = st.query_params.get('h', '')


# --- RESULTS HELPERS ---
def _clean_val(val):
    """Turn blank/whitespace placeholders and raw floats into readable display text."""
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.2f}"
    s = str(val).strip()
    return s if s else "—"


def _results_sort_key(row):
    """Placed runs first (by place), then completed-but-unplaced (by time), then E/NFC/ABS last."""
    faults_val = str(row.get('faults', '')).strip().upper()
    is_elim = 1 if faults_val in ('E', 'NFC', 'ABS') else 0
    place_str = str(row.get('place', '')).strip()
    try:
        place_num = int(place_str)
        no_place = 0
    except ValueError:
        place_num = 9999
        no_place = 1
    try:
        time_val = float(row.get('time') or 0)
    except (ValueError, TypeError):
        time_val = 9999.0
    return (is_elim, no_place, place_num, time_val)


def render_formatted_results(data):
    """Render a list of result-row dicts grouped by class type, then by height (placed runs first)."""
    if not data:
        st.info("No results to display yet.")
        return

    r_df = pd.DataFrame(data)
    r_df['height_num'] = pd.to_numeric(r_df['height'], errors='coerce').fillna(999)

    for class_type in sorted(r_df['class_type'].dropna().astype(str).unique()):
        st.markdown(f"### {class_type}")
        ct_df = r_df[r_df['class_type'].astype(str) == class_type]

        for height_num in sorted(ct_df['height_num'].unique()):
            height_rows = ct_df[ct_df['height_num'] == height_num]
            height_label = height_rows.iloc[0]['height']
            st.markdown(f'<div class="height-header">{height_label}" Height</div>', unsafe_allow_html=True)

            rows = sorted(height_rows.to_dict('records'), key=_results_sort_key)
            display_rows = []
            for r in rows:
                qualify_val = str(r.get('qualify', '')).strip().upper()
                place_raw = str(r.get('place', '')).strip()
                display_rows.append({
                    "Place": place_raw if place_raw else "—",
                    "Handler": r.get('uki_number', ''),
                    "Dog": r.get('uki_dog_number', ''),
                    "Time": _clean_val(r.get('time')),
                    "SCT": _clean_val(r.get('sct')),
                    "Faults": _clean_val(r.get('faults', '')),
                    "YPS": _clean_val(r.get('yps')),
                    "Time Faults": _clean_val(r.get('timefaults')),
                    "Pts": _clean_val(r.get('level_points')),
                    "Q": "Q" if qualify_val == 'Y' else "",
                })

            disp_df = pd.DataFrame(display_rows)

            def _style_results_row(row):
                f_val = str(row['Faults']).strip().upper()
                if f_val in ('E', 'NFC', 'ABS'):
                    return ['color: #A0A0A0; font-style: italic;'] * len(row)
                return [''] * len(row)

            st.dataframe(disp_df.style.apply(_style_results_row, axis=1), use_container_width=True, hide_index=True)


# --- TAB: CHECK-IN ---
def set_handler(num: str):
    num = re.sub(r'^[Hh]', '', (num or '').strip())
    st.session_state.active_handler = num
    if num:
        st.query_params['h'] = num
    else:
        st.query_params.pop('h', None)


def render_checkin():
    if df.empty:
        st.info("The running order hasn't been posted yet. Check back soon!")
        return

    handler = st.session_state.active_handler
    if not handler:
        with st.form("checkin_form"):
            num = st.text_input("Enter your UKI handler number:", placeholder="e.g. 12345", key="search_box_input")
            if st.form_submit_button("Find my dogs", use_container_width=True, type="primary"):
                set_handler(num)
                st.rerun()
        return

    user_data = df[df['handler_number'] == handler]
    if user_data.empty:
        st.warning(f"No runs found for handler **{esc(handler)}** on {_day_label(day)}. If you're entered for this "
                   "date and think this is a mistake, please reach out to the trial secretary!")
        st.button("Try a different number", on_click=set_handler, args=('',))
        return

    top_l, top_r = st.columns([2.6, 1], vertical_alignment="center")
    top_l.subheader(f"Hi, {user_data.iloc[0]['first_name'] or user_data.iloc[0]['handler_name']}")
    top_r.button("Not me", on_click=set_handler, args=('',), use_container_width=True)
    st.caption("Tap **Checked in** for each run (tap again to undo). Bookmark this page to come straight back.")

    by_class = {c: g for c, g in df.groupby('class_name', sort=False)}
    for dog in user_data['dog_name'].unique():
        dog_rows = user_data[user_data['dog_name'] == dog]  # already in running order
        with st.container(border=True):
            st.markdown(f"### {esc(dog)}")
            open_ids = [int(i) for i in dog_rows.loc[dog_rows['status'] == 'Not Checked In', 'id']]
            if len(open_ids) > 1:
                st.button(f"Check in all {len(open_ids)} remaining runs", key=f"btn_all_{day}_{dog}",
                          on_click=db.set_status, args=(ENGINE, open_ids, "Checked In"), use_container_width=True)

            for _, row in dog_rows.iterrows():
                run_id, status = int(row['id']), row['status']
                note = dogs_ahead(by_class[row['class_name']], run_id)
                st.markdown(f"**{esc(row['class_name'])}** &nbsp;{pill(status)}"
                            + (f"<br><span class='ro-sub'>{esc(note)}</span>" if note else ''),
                            unsafe_allow_html=True)
                if status in ('In Ring', 'Run Completed'):
                    continue
                b1, b2, b3 = st.columns([1, 1, 0.45])
                checked = status == 'Checked In'
                b1.button("Checked in", key=f"ci_{run_id}", type="primary" if checked else "secondary",
                          on_click=db.set_status, args=(ENGINE, run_id, 'Not Checked In' if checked else 'Checked In'),
                          use_container_width=True)
                scratched = status == 'Scratch'
                b2.button("Scratch", key=f"sc_{run_id}", type="primary" if scratched else "secondary",
                          on_click=db.set_status, args=(ENGINE, run_id, 'Not Checked In' if scratched else 'Scratch'),
                          use_container_width=True)
                with b3.popover("⋯", use_container_width=True):
                    for s in ('Conflict', 'NFC', 'Not Checked In'):
                        st.button(s, key=f"more_{run_id}_{s}", on_click=db.set_status, args=(ENGINE, run_id, s),
                                  type="primary" if status == s else "secondary", use_container_width=True)


# --- TAB: RUNNING ORDER ---
def running_order_html(class_df: pd.DataFrame, handler: str) -> str:
    parts, prev_h = [], None
    for i, r in enumerate(class_df.to_dict('records'), start=1):
        if r['height'] != prev_h:
            parts.append(f'<div class="ro-ht">{esc(r["height"])}"</div>')
            prev_h = r['height']
        mine = handler and r['handler_number'] == handler
        cls = ' '.join(c for c, on in (('mine', mine), ('inring', r['status'] == 'In Ring'),
                                        ('done', r['status'] == 'Run Completed'),
                                        ('scratch', r['status'] == 'Scratch')) if on)
        star = '<span class="you">You</span> ' if mine else ''
        parts.append(
            f'<div class="ro-row {cls}"><span class="ro-num">{i}</span>'
            f'<div><div class="ro-dog">{star}{esc(r["dog_name"])}</div>'
            f'<div class="ro-sub">{esc(r["handler_name"])} · {esc(r["breed"])} · {esc(r["class_type"])}</div></div>'
            f'{pill(r["status"])}</div>')
    return ''.join(parts)


def render_order():
    if df.empty:
        st.info("The running order hasn't been posted yet.")
        return
    sel_c = class_picker('ro_sel')

    course_map = read('latest_course_map', day, sel_c)
    if course_map:
        with st.expander("Course map"):
            st.image(course_map, use_container_width=True)

    @st.fragment(run_every=10)
    def live_running_order_view(target_class, handler_num):
        r_df = read('runs_for_day', day, target_class)
        if r_df.empty:
            st.info("No data found for this class.")
            return
        st.markdown(now_strip_html(r_df), unsafe_allow_html=True)
        for _, mine in r_df[r_df['handler_number'] == handler_num].iterrows() if handler_num else []:
            note = dogs_ahead(r_df, int(mine['id']))
            if note:
                st.markdown(f'<div class="ro-mine">{esc(mine["dog_name"])}: {esc(note)}</div>',
                            unsafe_allow_html=True)
        st.markdown(running_order_html(r_df, handler_num), unsafe_allow_html=True)
        st.caption(f"Updates automatically • {clock()}")

    live_running_order_view(sel_c, st.session_state.active_handler)


# --- TAB: RESULTS ---
def render_results():
    res_class_map = read('results_for_day', day) if day else {}
    if not res_class_map:
        st.info("No results have been posted yet. Check back after the class runs!")
        return
    ordered = [c for c in sorted_classes if c in res_class_map]
    ordered += sorted(c for c in res_class_map if c not in sorted_classes)
    sticky_choice('res_view_class_sel', ordered)
    st.pills("Class", ordered, key="res_view_class_sel", label_visibility="collapsed")
    render_formatted_results(res_class_map[st.session_state.res_view_class_sel])


# --- TAB: DASHBOARD ---
def render_dash():
    if st.button("Refresh", key="dash_refresh"):
        st.rerun()
    if df.empty:
        st.info("No running order loaded for this day.")
        return
    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)
    c1.metric("Total Entries", len(df))
    c2.metric("Checked In", len(df[df['status'].isin(['Checked In', 'Conflict'])]))
    c3.metric("Scratched", len(df[df['status'] == 'Scratch']))
    c4.metric("Completed", len(df[df['status'] == 'Run Completed']))

    per_class = (df.assign(
        checked_in=df['status'].isin(['Checked In', 'Conflict', 'NFC', 'In Ring', 'Run Completed']),
        scratched=df['status'] == 'Scratch',
        done=df['status'] == 'Run Completed',
    ).groupby('class_name', sort=False)
        .agg(Runs=('id', 'size'), **{'Checked in': ('checked_in', 'sum'),
                                     'Scratched': ('scratched', 'sum'), 'Completed': ('done', 'sum')})
        .reset_index().rename(columns={'class_name': 'Class'}))
    per_class['Progress'] = (per_class['Completed'] + per_class['Scratched']) / per_class['Runs']
    st.dataframe(per_class, use_container_width=True, hide_index=True,
                 column_config={'Progress': st.column_config.ProgressColumn('Progress', min_value=0, max_value=1,
                                                                            format=' ')})


# --- TAB: GATE ---
CARD_KIND = {'In Ring': 'ring', 'Checked In': 'in', 'NFC': 'in', 'Conflict': 'in',
             'Scratch': 'scr', 'Run Completed': 'done'}


def render_gate():
    st.header("Gate Steward")
    if not (st.session_state.get('unlocked_admin') or pin_unlocked('gate', 'GATE_PIN', 'Gate PIN')):
        return
    if df.empty:
        st.info("No running order loaded for this day.")
        return

    with st.expander("Conflict timer (optional)", expanded=False):
        if 'gate_timer_minutes' not in st.session_state:
            st.session_state.gate_timer_minutes = 6
        if 'gate_timer_end' not in st.session_state:
            st.session_state.gate_timer_end = None

        t_cols = st.columns([2, 1, 1])
        with t_cols[0]:
            st.number_input("Minutes:", min_value=1, max_value=60, step=1, key='gate_timer_minutes')
        with t_cols[1]:
            if st.button("Start", use_container_width=True, key="gate_timer_start"):
                st.session_state.gate_timer_end = time.time() + st.session_state.gate_timer_minutes * 60
                st.rerun()
        with t_cols[2]:
            if st.button("Reset", use_container_width=True, key="gate_timer_reset"):
                st.session_state.gate_timer_end = None
                st.rerun()

        @st.fragment(run_every=1)
        def conflict_timer_display():
            end_time = st.session_state.get('gate_timer_end')
            if end_time is None:
                st.caption("Timer not running — set the minutes above and hit Start.")
            else:
                remaining = end_time - time.time()
                if remaining <= 0:
                    st.markdown(
                        '<div style="font-size: 32px; font-weight: bold; color: #dc3545; text-align: center;">TIME\'S UP</div>',
                        unsafe_allow_html=True
                    )
                else:
                    mins, secs = divmod(int(remaining) + 1, 60)
                    color = "#dc3545" if remaining <= 30 else "#1E3A8A"
                    st.markdown(
                        f'<div style="font-size: 32px; font-weight: bold; color: {color}; text-align: center;">{mins:02d}:{secs:02d}</div>',
                        unsafe_allow_html=True
                    )

        conflict_timer_display()

    g_cls = class_picker('g_cls')

    @st.fragment(run_every=5)
    def gate_steward_view(target_class):
        # Speedstakes / Jumping classes don't run the A-Frame — skip the equipment-change banners
        has_aframe = not any(kw in target_class.lower() for kw in ('speedstakes', 'jumping'))

        g_df = read('runs_for_day', day, target_class)
        if g_df.empty:
            st.info("No data found for this class.")
            return

        with st.container(key="gate-strip"):
            st.markdown(now_strip_html(g_df), unsafe_allow_html=True)

        def act(fn, *args):
            fn(ENGINE, *args)
            try:
                st.rerun(scope="fragment")  # just redraw the gate list
            except st.errors.StreamlitAPIException:
                st.rerun()  # the click arrived during a full-page run

        prev_aframe = None
        prev_height = None
        for _, r in g_df.iterrows():
            status = r['status']
            is_in_ring = status == "In Ring"
            is_done = status == "Run Completed"
            is_scratch = status == "Scratch"
            # NFC runs still need to go through the ring — treat them like Checked In
            is_checked_in = status in ("Checked In", "NFC", "Conflict")
            pk_val = int(r['id'])

            height_label = r["height"]
            try:
                height_val = float(re.sub(r'[^0-9.]', '', str(height_label)))
            except (ValueError, TypeError):
                height_val = 999
            is_select = str(r.get('class_type', '')).strip().lower() == 'select'
            aframe = "A-Frame: Down" if (height_val <= 12 or is_select) else "A-Frame: Up"

            # --- HEIGHT CHANGE DIVIDER ---
            if prev_height is not None and height_label != prev_height:
                st.markdown(f'<div class="gate-banner" style="background:#FEE2E2; border:2px dashed #DC2626; '
                            f'color:#991B1B;">JUMP HEIGHT CHANGE → {esc(height_label)}"</div>', unsafe_allow_html=True)
            prev_height = height_label

            # --- A-FRAME CHANGE DIVIDER (flagged BEFORE the run it applies to) ---
            # Scratched dogs aren't running, so they're ignored entirely for A-Frame tracking
            if not is_scratch:
                if has_aframe and aframe != prev_aframe:
                    a_color = "#DC2626" if aframe == "A-Frame: Down" else "#198754"
                    a_bg = "#FEE2E2" if aframe == "A-Frame: Down" else "#D1FAE5"
                    st.markdown(f'<div class="gate-banner" style="background:{a_bg}; border:2px dashed {a_color}; '
                                f'color:{a_color};">SET {aframe.upper()}</div>', unsafe_allow_html=True)
                prev_aframe = aframe

            kind = CARD_KIND.get(status, 'notin')
            with st.container(key=f"card-{kind}-{pk_val}"):
                st.markdown(
                    f'<div class="card-title">{esc(r["height"])}" &nbsp;{esc(r["dog_name"])} '
                    f'<span style="font-weight:400; font-size:15px;">({esc(r["breed"])})</span></div>'
                    f'<div class="card-sub">{esc(r["handler_name"])} • {esc(r["class_type"])} &nbsp;{pill(status)}</div>',
                    unsafe_allow_html=True)

                if is_in_ring:
                    if st.button("FINISH", key=f"finish_{pk_val}", type="primary", use_container_width=True):
                        act(db.set_status, pk_val, "Run Completed")
                elif is_done:
                    if st.button("UNDO FINISH", key=f"undo_{pk_val}", use_container_width=True):
                        act(db.set_status, pk_val, "Checked In")
                elif is_scratch:
                    if st.button("UN-SCRATCH", key=f"unscratch_{pk_val}", use_container_width=True):
                        act(db.set_status, pk_val, "Not Checked In")
                elif is_checked_in:
                    cb1, cb2 = st.columns(2)
                    if cb1.button("START RUN", key=f"start_{pk_val}", type="primary", use_container_width=True):
                        act(db.start_run, pk_val)
                    if cb2.button("SCRATCH", key=f"scratch_checkedin_{pk_val}", use_container_width=True):
                        act(db.set_status, pk_val, "Scratch")
                else:
                    b1, b2 = st.columns(2)
                    if b1.button("CHECK IN", key=f"checkin_{pk_val}", use_container_width=True):
                        act(db.set_status, pk_val, "Checked In")
                    if b2.button("SCRATCH", key=f"scratch_{pk_val}", use_container_width=True):
                        act(db.set_status, pk_val, "Scratch")

        st.caption(f"Gate live sync • {clock()}")

    gate_steward_view(g_cls)


# --- TAB: ADMIN ---
def render_admin():
    st.header("Secretary Admin")
    if pin_unlocked('admin', 'ADMIN_PIN', 'Admin PIN'):
        section = st.radio(
            "Section", ["Upload run order", "Reorder", "Late entry", "Course maps",
                        "Publish results", "Reset statuses"],
            horizontal=True, label_visibility="collapsed", key="admin_section",
        )
        st.divider()

        # ── Upload run order ──
        if section == "Upload run order":
            st.caption("Upload the CSV exported from the run order app. It replaces the runs for the day(s) in the "
                       "file only. Anyone already checked in, scratched, or finished keeps that status "
                       "(matched by class + dog number).")
            up_ver = st.session_state.get('upload_ver', 0)
            up = st.file_uploader("Run order CSV", type=['csv'], key=f"ro_upload_{up_ver}")
            if up is not None:
                try:
                    new_runs = parse_run_order_csv(up.getvalue())
                except RunOrderError as e:
                    st.error(str(e))
                else:
                    pv = db.preview_import(ENGINE, new_runs)
                    days_txt = ', '.join(_day_label(d) for d in pv['dates'])
                    st.markdown(f"**{pv['new']} runs** for **{days_txt}**")
                    summary = summarize(new_runs)
                    summary['trial_date'] = summary['trial_date'].map(_day_label)
                    st.dataframe(summary.rename(columns={'trial_date': 'Day', 'class_name': 'Class', 'runs': 'Runs'}),
                                 hide_index=True, use_container_width=True)
                    if pv['replacing']:
                        st.info(f"This replaces the {pv['replacing']} runs currently loaded for {days_txt}. "
                                f"{pv['statuses_kept']} status(es) will carry over.")
                    if pv['statuses_lost']:
                        st.warning("These runs have a status but are **not in the new file**, so they'll be removed:\n\n"
                                   + "\n".join(f"- {x}" for x in pv['statuses_lost']))
                    if st.button(f"Load this run order for {days_txt}", type="primary"):
                        db.import_runs(ENGINE, new_runs)
                        st.session_state.upload_ver = up_ver + 1
                        st.session_state.pending_day = pv['dates'][0]
                        st.session_state.admin_flash = f"Run order loaded: {pv['new']} runs for {days_txt}."
                        st.rerun()

            if dates:
                with st.expander("Remove a day"):
                    rm_day = st.selectbox("Day to remove", dates, format_func=_day_label, key="rm_day")
                    confirm = st.checkbox(f"Yes, delete every run for {_day_label(rm_day)}", key="rm_confirm")
                    if st.button("Delete day", disabled=not confirm):
                        db.delete_day(ENGINE, rm_day)
                        st.session_state.admin_flash = f"Removed {_day_label(rm_day)}."
                        st.rerun()

        # ── Reorder ──
        elif section == "Reorder":
            if df.empty:
                st.info("Upload a run order first.")
            else:
                re_cls = st.selectbox("Class", sorted_classes, key="reorder_cls")
                cls_runs = df[df['class_name'] == re_cls]
                labels, label_to_id = [], {}
                for _, r in cls_runs.iterrows():
                    lab = run_label(r)
                    if r['status'] == 'Scratch':
                        lab += ' (scratched)'
                    n = 2
                    while lab in label_to_id:
                        lab = f"{run_label(r)} ({n})"
                        n += 1
                    labels.append(lab)
                    label_to_id[lab] = int(r['id'])

                st.caption("Drag dogs into the new order, then click **Save order**. "
                           "Exhibitors see the new order on the Order tab within a few seconds.")
                ver = st.session_state.get('reorder_ver', 0)
                new_labels = sort_items(labels, direction='vertical', key=f"sort_{day}_{re_cls}_{ver}")
                changed = list(new_labels) != labels
                save_now = False

                with st.expander("Or move one dog to a position (easier on a phone)"):
                    mv_c1, mv_c2 = st.columns([3, 1])
                    mv_dog = mv_c1.selectbox("Dog", labels, key=f"mv_dog_{re_cls}_{ver}")
                    mv_pos = mv_c2.number_input("Position", min_value=1, max_value=len(labels),
                                                value=labels.index(mv_dog) + 1, key=f"mv_pos_{re_cls}_{ver}_{mv_dog}")
                    if st.button("Move", key="mv_btn"):
                        new_labels = [x for x in labels if x != mv_dog]
                        new_labels.insert(int(mv_pos) - 1, mv_dog)
                        changed = save_now = True

                if st.button("Save order", type="primary" if changed else "secondary", disabled=not changed) \
                        or save_now:
                    try:
                        db.save_class_order(ENGINE, day, re_cls, [label_to_id[x] for x in new_labels])
                        st.session_state.reorder_ver = ver + 1
                        st.session_state.admin_flash = f"Saved the new order for {re_cls}."
                        st.rerun()
                    except db.ClassChangedError as e:
                        st.error(str(e))

        # ── Late entry ──
        elif section == "Late entry":
            if df.empty:
                st.info("Upload a run order first.")
            else:
                le_cls = st.selectbox("Class", sorted_classes, key="le_cls")
                dogs = (df.drop_duplicates('dog_number')
                        .sort_values(['handler_name', 'dog_name'], key=lambda s: s.str.lower()))
                dog_opts = ['New dog (type the details)'] + [f"{r.dog_name} — {r.handler_name} (#{r.dog_number})"
                                                               for r in dogs.itertuples()]
                pick = st.selectbox("Dog", dog_opts, key="le_dog",
                                    help="Pick a dog that's already running today to fill in their details.")
                src = {} if pick == dog_opts[0] else dogs.iloc[dog_opts.index(pick) - 1].to_dict()

                cls_runs = df[df['class_name'] == le_cls]
                pos_opts = [f"{i + 1} — before {r.dog_name} ({r.height}\")" for i, r in enumerate(cls_runs.itertuples())]
                pos_opts.append(f"{len(cls_runs) + 1} — at the end")

                with st.form(f"late_entry_{le_cls}_{pick}"):
                    c1, c2 = st.columns(2)
                    dog_name = c1.text_input("Dog name", src.get('dog_name', ''))
                    dog_number = c2.text_input("Dog UKI #", src.get('dog_number', ''))
                    breed = c1.text_input("Breed", src.get('breed', ''))
                    height = c2.text_input("Jump height (inches)", src.get('height', ''))
                    first = c1.text_input("Handler first name", src.get('first_name', ''))
                    last = c2.text_input("Handler last name", src.get('last_name', ''))
                    handler_number = c1.text_input("Handler UKI #", src.get('handler_number', ''))
                    level = c2.selectbox("Level", ['Beginner', 'Novice', 'Senior', 'Champ'],
                                         index=['Beginner', 'Novice', 'Senior', 'Champ'].index(src['level'])
                                         if src.get('level') in ('Beginner', 'Novice', 'Senior', 'Champ') else 0)
                    class_type = c1.selectbox("Class type", ['Regular', 'Select'],
                                              index=1 if src.get('class_type') == 'Select' else 0)
                    position = st.selectbox("Where in the running order?", pos_opts, index=len(pos_opts) - 1)
                    if st.form_submit_button("Add to class", type="primary"):
                        if not dog_name.strip() or not handler_number.strip():
                            st.error("Dog name and handler UKI # are required.")
                        elif dog_number.strip() and dog_number.strip() in set(cls_runs['dog_number']):
                            st.error(f"Dog #{dog_number.strip()} is already in {le_cls}. "
                                     "Use **Reorder** to move them instead.")
                        else:
                            db.add_late_entry(ENGINE, day, le_cls, {
                                'dog_name': dog_name.strip(), 'dog_number': dog_number.strip(),
                                'breed': breed.strip(), 'height': height.strip(),
                                'first_name': first.strip(), 'last_name': last.strip(),
                                'handler_number': re.sub(r'^[Hh]', '', handler_number.strip()),
                                'level': level, 'class_type': class_type,
                            }, insert_at=pos_opts.index(position) + 1)
                            st.session_state.admin_flash = f"Added {dog_name.strip()} to {le_cls}."
                            st.rerun()

        # ── Course maps ──
        elif section == "Course maps":
            if df.empty:
                st.info("Upload a run order first.")
            else:
                upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_up_sel")
                current = read('latest_course_map', day, upload_class)
                if current:
                    st.caption("Current map:")
                    st.image(current, width=300)
                map_ver = st.session_state.get('map_ver', 0)
                uploaded_file = st.file_uploader("Choose Image", type=['jpg', 'png', 'jpeg'], key=f"map_file_{map_ver}")
                if uploaded_file and st.button("Upload map", type="primary"):
                    db.save_course_map(ENGINE, day, upload_class, uploaded_file.name,
                                       uploaded_file.type or 'image/png', uploaded_file.getvalue())
                    st.session_state.map_ver = map_ver + 1
                    st.session_state.admin_flash = f"Map uploaded for {upload_class}."
                    st.rerun()
                have = read('classes_with_maps', day)
                st.caption("Maps uploaded: " + (', '.join(c for c in sorted_classes if c in have) or 'none yet'))

        # ── Publish results ──
        elif section == "Publish results":
            if not sorted_classes:
                st.info("Upload a run order first.")
            else:
                sel_input_class = st.selectbox("Which class are these results for?", sorted_classes,
                                               key="res_input_class_sel")
                json_text = st.text_area(
                    "Paste results JSON here:", height=280, key="res_json_text",
                    placeholder='[ { "class_type": "Regular", "height": "8", "uki_number": "...", "uki_dog_number": "...", ... } ]'
                )

                if st.button("Preview results", use_container_width=True):
                    try:
                        parsed = json.loads(json_text)
                        if not isinstance(parsed, list):
                            st.error("That JSON parsed, but it needs to be a list of result entries (e.g. `[ {...}, {...} ]`).")
                        else:
                            st.session_state.res_parsed_preview = parsed
                    except json.JSONDecodeError as e:
                        st.session_state.pop('res_parsed_preview', None)
                        st.error(f"Couldn't parse that JSON: {e}")

                if 'res_parsed_preview' in st.session_state:
                    st.divider()
                    st.subheader(f"Preview — {sel_input_class}")
                    render_formatted_results(st.session_state.res_parsed_preview)

                    if st.button("Publish results", type="primary", use_container_width=True):
                        db.publish_results(ENGINE, day, sel_input_class, st.session_state.res_parsed_preview)
                        del st.session_state['res_parsed_preview']
                        st.session_state.admin_flash = f"Results for '{sel_input_class}' are now live on the Results tab!"
                        st.rerun()

        # ── Reset ──
        elif section == "Reset statuses":
            if day:
                st.caption(f"Sets every run on {_day_label(day)} back to Not Checked In, including scratches.")
                confirm = st.checkbox("Yes, reset all statuses for this day", key="reset_confirm")
                if st.button("Reset All Statuses", disabled=not confirm):
                    n = db.reset_statuses(ENGINE, day)
                    st.session_state.admin_flash = f"Reset {n} run(s)."
                    st.rerun()

        if 'admin_flash' in st.session_state:
            st.success(st.session_state.pop('admin_flash'))


# --- 5. TABS ---
TABS = [("Check-in", render_checkin, 'checkin'), ("Running order", render_order, 'order'),
        ("Results", render_results, 'results')]
if STAFF:
    TABS += [("Gate", render_gate, 'gate'), ("Dashboard", render_dash, 'dash'), ("Admin", render_admin, 'admin')]

for tab, (_, render, key) in zip(st.tabs([t[0] for t in TABS]), TABS):
    with tab:
        with st.container(key=key):
            render()
