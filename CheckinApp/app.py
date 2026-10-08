"""
app.py — UKI trial check-in, running order, gate steward, admin and results.

Run locally:   streamlit run app.py
Environment:   DATABASE_URL (Railway Postgres; SQLite file if unset), GATE_PIN, ADMIN_PIN, TRIAL_TZ (optional)
"""

import hmac
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

# --- 1. PAGE CONFIG & UI STYLING ---
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 5rem; padding-bottom: 2rem; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1E3A8A; }

    /* Global Button Styling */
    .stButton > button {
        width: 100% !important;
        height: 60px !important;
        font-size: 18px !important;
        font-weight: bold !important;
        border-radius: 12px !important;
    }

    /* Column layout for mobile */
    [data-testid="column"] {
        min-width: 30% !important;
        flex: 1 1 30% !important;
    }

    .height-header {
        background-color: rgba(30, 58, 138, 0.1);
        padding: 10px;
        border-radius: 8px;
        border-left: 5px solid #1E3A8A;
        margin-top: 20px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">🏆 UKI Trial Secretary Portal</p>', unsafe_allow_html=True)


# --- 2. DATABASE ---
@st.cache_resource
def engine():
    return db.get_engine()


ENGINE = engine()
if os.environ.get('RAILWAY_ENVIRONMENT_NAME') and ENGINE.dialect.name == 'sqlite':
    st.error('⚠ DATABASE_URL is not set, so this app is using a temporary file that is erased on every deploy. '
             'In Railway, add the variable DATABASE_URL = ${{Postgres.DATABASE_URL}}.')


# --- 3. PINS ---
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


# --- 4. DAY SELECTION ---
TZ = ZoneInfo(os.environ.get('TRIAL_TZ', 'America/New_York'))
dates = db.trial_dates(ENGINE)
today = datetime.now(TZ).date()


def _default_day(ds):
    if today in ds:
        return today
    upcoming = [d for d in ds if d > today]
    return upcoming[0] if upcoming else ds[-1]


def _day_label(d: date) -> str:
    return d.strftime('%A %-m/%-d/%Y')


if dates:
    if 'pending_day' in st.session_state:  # set by the upload page; applied before the widget exists
        st.session_state.day = st.session_state.pop('pending_day')
    if st.session_state.get('day') not in dates:
        st.session_state.day = _default_day(dates)
    if len(dates) > 1:
        st.radio('Trial day', dates, format_func=_day_label, key='day', horizontal=True)
    day = st.session_state.day
else:
    day = None

df = db.runs_for_day(ENGINE, day) if day else pd.DataFrame(columns=[c.name for c in db.runs.columns])
sorted_classes = db.class_names(ENGINE, day) if day else []

if 'active_handler' not in st.session_state:
    st.session_state.active_handler = ""


def class_picker(state_key: str, prefix: str):
    """Two-column grid of class buttons (no keyboard needed on mobile). Returns the selected class."""
    if st.session_state.get(state_key) not in sorted_classes:
        st.session_state[state_key] = sorted_classes[0] if sorted_classes else None
    st.markdown("**Select Class:**")
    cols = st.columns(2)
    half = (len(sorted_classes) + 1) // 2
    for i, cls in enumerate(sorted_classes):
        with cols[0 if i < half else 1]:
            if st.button(cls, key=f"{prefix}_btn_{i}", use_container_width=True,
                         type="primary" if st.session_state[state_key] == cls else "secondary"):
                st.session_state[state_key] = cls
                st.rerun()
    return st.session_state[state_key]


def run_label(r) -> str:
    return f'{r["height"]}" {r["class_type"]} · {r["dog_name"]} — {r["handler_name"]}'


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
            st.markdown(f'<div class="height-header">📏 {height_label}" Height</div>', unsafe_allow_html=True)

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
                    "Q": "✅ Q" if qualify_val == 'Y' else "",
                })

            disp_df = pd.DataFrame(display_rows)

            def _style_results_row(row):
                f_val = str(row['Faults']).strip().upper()
                if f_val in ('E', 'NFC', 'ABS'):
                    return ['color: #A0A0A0; font-style: italic;'] * len(row)
                return [''] * len(row)

            st.dataframe(disp_df.style.apply(_style_results_row, axis=1), use_container_width=True, hide_index=True)


# --- 5. TABS ---
tab1, tab2, tab3, tab5, tab6, tab7 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "🚧 Gate", "🔒 Admin", "🏆 Results"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    if df.empty:
        st.info("The running order hasn't been posted yet. Check back soon!")
    with st.form("checkin_form"):
        handler_input_raw = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box_input")
        submitted = st.form_submit_button("Submit", use_container_width=True, type="primary")

    if submitted:
        st.session_state.active_handler = re.sub(r'^[Hh]', '', handler_input_raw.strip())

    handler_input = st.session_state.get('active_handler', '')
    if handler_input and not df.empty:
        user_data = df[df['handler_number'] == handler_input]

        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['handler_name']}")

            for dog in user_data['dog_name'].unique():
                # This dog's runs, in the trial's running order (df is already sorted that way)
                dog_rows = user_data[user_data['dog_name'] == dog]

                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")

                    open_ids = [int(i) for i in dog_rows.loc[dog_rows['status'].isin(['Not Checked In']), 'id']]
                    if st.button(f"Check in all runs for {dog}", key=f"btn_all_{day}_{dog}", disabled=not open_ids):
                        db.set_status(ENGINE, open_ids, "Checked In")
                        st.rerun()

                    for _, row in dog_rows.iterrows():
                        run_id, status = int(row['id']), row['status']
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class:
                            st.markdown(f"**{row['class_name']}**")
                        with c_status:
                            if status in db.SELF_SERVE_STATUSES:
                                # Key includes the status so the box resets if the gate changes it
                                key_name = f"select_{run_id}_{status}"
                                st.selectbox(
                                    "Status", options=db.SELF_SERVE_STATUSES,
                                    index=db.SELF_SERVE_STATUSES.index(status), key=key_name,
                                    on_change=lambda rid=run_id, k=key_name: db.set_status(ENGINE, rid, st.session_state[k]),
                                    label_visibility="collapsed",
                                )
                            else:
                                st.markdown(f"**{'🟡 In Ring' if status == 'In Ring' else '✅ Run Completed'}**")
        else:
            loaded_for = f" Data loaded for **{_day_label(day)}**." if day else ""
            st.warning(f"Handler not found.{loaded_for} If you're entered for this date and feel like this is a mistake, please reach out to the trial secretary!")

# --- TAB 2: DASHBOARD ---
with tab2:
    if st.button("🔄 Refresh", key="dash_refresh"):
        st.rerun()

    if not df.empty:
        c1, c2, c3, c4 = st.columns(4)
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
        st.dataframe(per_class, use_container_width=True, hide_index=True)

# --- TAB 3: RUNNING ORDER (LIVE DISPLAY via Fragment) ---
with tab3:
    if not df.empty:
        sel_c = class_picker('ro_sel', 'ro')

        course_map = db.latest_course_map(ENGINE, day, sel_c)
        if course_map:
            st.image(course_map, use_container_width=True)

        @st.fragment(run_every=10)
        def live_running_order_view(target_class, handler_num):
            st.caption(f"Live Sync Active • Last Update: {time.strftime('%H:%M:%S')}")
            r_df = db.runs_for_day(ENGINE, day, target_class)

            if not r_df.empty:
                subset = r_df.copy()
                subset['#'] = range(1, len(subset) + 1)
                is_mine_col = (subset['handler_number'] == str(handler_num).strip()) & (handler_num != "")
                subset['dog_name'] = [f"⭐ {n}" if m else n for n, m in zip(subset['dog_name'], is_mine_col)]

                def highlight_row(s):
                    styles = [''] * len(s)
                    is_mine = str(s['handler_number']).strip() == str(handler_num).strip() and handler_num != ""
                    is_in_ring = s['status'] == 'In Ring'
                    is_done = s['status'] == 'Run Completed'
                    is_scratch = s['status'] == 'Scratch'

                    for i in range(len(s)):
                        if is_in_ring:
                            styles[i] = 'background-color: #FFF59D; color: #000000; border: 2px solid #FFD600;'
                        elif is_done:
                            styles[i] = 'color: #A0A0A0; font-style: italic;'
                        elif is_mine:
                            styles[i] = 'background-color: #E3F2FD; color: #000000;'

                    # Highlight ONLY the status cell's "Scratch" text in red
                    if is_scratch:
                        styles[list(s.index).index('status')] = 'color: #DC2626; font-weight: bold;'
                    return styles

                styled_table = subset[['#', 'height', 'handler_name', 'dog_name', 'breed', 'status', 'handler_number']].style \
                    .apply(highlight_row, axis=1) \
                    .set_properties(**{'font-size': '22px', 'font-weight': 'bold'})

                st.dataframe(
                    styled_table,
                    column_order=('#', 'height', 'handler_name', 'dog_name', 'breed', 'status'),
                    column_config={'height': 'Height', 'handler_name': 'Handler', 'dog_name': 'Dog',
                                   'breed': 'Breed', 'status': 'Status'},
                    use_container_width=True,
                    hide_index=True,
                    key=f"ro_table_{target_class}"
                )
            else:
                st.info("No data found for this class.")

        live_running_order_view(sel_c, st.session_state.get('active_handler', ""))
    else:
        st.info("The running order hasn't been posted yet.")

# --- TAB 5: GATE STEWARD (LIVE DISPLAY via Fragment) ---
with tab5:
    st.header("🚧 Gate Steward")
    gate_ok = st.session_state.get('unlocked_admin') or pin_unlocked('gate', 'GATE_PIN', 'Gate PIN')

    if gate_ok and df.empty:
        st.info("No running order loaded for this day.")
    elif gate_ok:
        with st.expander("⏱️ Conflict Timer (optional)", expanded=False):
            if 'gate_timer_minutes' not in st.session_state:
                st.session_state.gate_timer_minutes = 6
            if 'gate_timer_end' not in st.session_state:
                st.session_state.gate_timer_end = None

            t_cols = st.columns([2, 1, 1])
            with t_cols[0]:
                st.number_input("Minutes:", min_value=1, max_value=60, step=1, key='gate_timer_minutes')
            with t_cols[1]:
                if st.button("▶️ Start", use_container_width=True, key="gate_timer_start"):
                    st.session_state.gate_timer_end = time.time() + st.session_state.gate_timer_minutes * 60
                    st.rerun()
            with t_cols[2]:
                if st.button("⏹️ Reset", use_container_width=True, key="gate_timer_reset"):
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
                            '<div style="font-size: 32px; font-weight: bold; color: #dc3545; text-align: center;">⏰ TIME\'S UP</div>',
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

        g_cls = class_picker('g_cls', 'g')

        @st.fragment(run_every=5)
        def gate_steward_view(target_class):
            st.caption(f"Gate Live Sync • Last Update: {time.strftime('%H:%M:%S')}")

            # Speedstakes / Jumping classes don't run the A-Frame — skip the equipment-change banners
            class_name_lower = target_class.lower()
            has_aframe = not any(kw in class_name_lower for kw in ('speedstakes', 'jumping'))

            g_df = db.runs_for_day(ENGINE, day, target_class)
            if g_df.empty:
                st.info("No data found for this class.")
                return

            def act(fn, *args):
                fn(ENGINE, *args)
                try:
                    st.rerun(scope="fragment")  # just redraw the gate list
                except st.errors.StreamlitAPIException:
                    st.rerun()  # the click arrived during a full-page run

            prev_aframe = None
            prev_height = None
            for _, r in g_df.iterrows():
                is_in_ring = r['status'] == "In Ring"
                is_done = r['status'] == "Run Completed"
                is_scratch = r['status'] == "Scratch"
                # NFC runs still need to go through the ring — treat them like Checked In
                is_checked_in = r['status'] in ("Checked In", "NFC", "Conflict")
                pk_val = int(r['id'])

                border_color = "#ffc107" if is_in_ring else "#28a745" if is_checked_in else "#dc3545" if is_scratch else "#adb5bd"

                height_label = r["height"]
                try:
                    height_val = float(re.sub(r'[^0-9.]', '', str(height_label)))
                except (ValueError, TypeError):
                    height_val = 999
                is_select = str(r.get('class_type', '')).strip().lower() == 'select'
                aframe = "A-Frame: Down" if (height_val <= 12 or is_select) else "A-Frame: Up"

                # --- HEIGHT CHANGE DIVIDER ---
                if prev_height is not None and height_label != prev_height:
                    st.markdown(f'''
                        <div style="text-align: center; margin: 16px 0; padding: 10px; background-color: #FEE2E2; border: 2px dashed #DC2626; border-radius: 8px;">
                            <span style="font-size: 16px; font-weight: bold; color: #991B1B;">JUMP HEIGHT CHANGE → {height_label}"</span>
                        </div>
                    ''', unsafe_allow_html=True)
                prev_height = height_label

                # --- A-FRAME CHANGE DIVIDER (flagged BEFORE the run it applies to) ---
                # Scratched dogs aren't running, so they're ignored entirely for A-Frame tracking
                if not is_scratch:
                    if has_aframe and aframe != prev_aframe:
                        a_color = "#DC2626" if aframe == "A-Frame: Down" else "#198754"
                        a_bg = "#FEE2E2" if aframe == "A-Frame: Down" else "#D1FAE5"
                        st.markdown(f'''
                            <div style="text-align: center; margin: 8px 0 16px 0; padding: 10px; background-color: {a_bg}; border: 2px dashed {a_color}; border-radius: 8px;">
                                <span style="font-size: 16px; font-weight: bold; color: {a_color};">🔺 SET {aframe.upper()}</span>
                            </div>
                        ''', unsafe_allow_html=True)
                    prev_aframe = aframe

                c_main, c_btn = st.columns([3, 2])
                with c_main:
                    st.markdown(f'''
                        <div style="padding: 10px; border-left: 10px solid {border_color}; border-radius: 8px; background-color: #f8f9fa; margin-bottom: 10px;">
                            <div style="font-size: 20px; font-weight: bold; color: #333;">{r["height"]} | {r["dog_name"]} <span style="font-weight: normal;">({r["breed"]})</span></div>
                            <div style="font-size: 14px; color: #666;">{r["handler_name"]} • {r["class_type"]} • {r["status"]}</div>
                        </div>
                    ''', unsafe_allow_html=True)

                with c_btn:
                    if is_in_ring:
                        if st.button("FINISH ✅", key=f"finish_{pk_val}", use_container_width=True, type="primary"):
                            act(db.set_status, pk_val, "Run Completed")
                    elif is_done:
                        if st.button("UNDO FINISH", key=f"undo_{pk_val}", use_container_width=True):
                            act(db.set_status, pk_val, "Checked In")
                    elif is_scratch:
                        if st.button("UN-SCRATCH", key=f"unscratch_{pk_val}", use_container_width=True):
                            act(db.set_status, pk_val, "Not Checked In")
                    elif is_checked_in:
                        cb1, cb2 = st.columns(2)
                        with cb1:
                            if st.button("START RUN", key=f"start_{pk_val}", use_container_width=True):
                                act(db.start_run, pk_val)
                        with cb2:
                            if st.button("SCRATCH", key=f"scratch_checkedin_{pk_val}", use_container_width=True):
                                act(db.set_status, pk_val, "Scratch")
                    else:
                        b1, b2 = st.columns(2)
                        with b1:
                            if st.button("CHECK IN", key=f"checkin_{pk_val}", use_container_width=True):
                                act(db.set_status, pk_val, "Checked In")
                        with b2:
                            if st.button("SCRATCH", key=f"scratch_{pk_val}", use_container_width=True):
                                act(db.set_status, pk_val, "Scratch")

        gate_steward_view(g_cls)

# --- TAB 6: ADMIN ---
with tab6:
    st.header("🔒 Secretary Admin")
    if pin_unlocked('admin', 'ADMIN_PIN', 'Admin PIN'):
        section = st.radio(
            "Section", ["📤 Upload run order", "↕️ Reorder", "➕ Late entry", "🗺️ Course maps",
                        "🏆 Publish results", "♻️ Reset"],
            horizontal=True, label_visibility="collapsed", key="admin_section",
        )
        st.divider()

        # ── Upload run order ──
        if section == "📤 Upload run order":
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
                    if st.button(f"✅ Load this run order for {days_txt}", type="primary"):
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
        elif section == "↕️ Reorder":
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

                if st.button("💾 Save order", type="primary" if changed else "secondary", disabled=not changed) \
                        or save_now:
                    try:
                        db.save_class_order(ENGINE, day, re_cls, [label_to_id[x] for x in new_labels])
                        st.session_state.reorder_ver = ver + 1
                        st.session_state.admin_flash = f"Saved the new order for {re_cls}."
                        st.rerun()
                    except db.ClassChangedError as e:
                        st.error(str(e))

        # ── Late entry ──
        elif section == "➕ Late entry":
            if df.empty:
                st.info("Upload a run order first.")
            else:
                le_cls = st.selectbox("Class", sorted_classes, key="le_cls")
                dogs = (df.drop_duplicates('dog_number')
                        .sort_values(['handler_name', 'dog_name'], key=lambda s: s.str.lower()))
                dog_opts = ['➕ New dog (type the details)'] + [f"{r.dog_name} — {r.handler_name} (#{r.dog_number})"
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
        elif section == "🗺️ Course maps":
            if df.empty:
                st.info("Upload a run order first.")
            else:
                upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_up_sel")
                current = db.latest_course_map(ENGINE, day, upload_class)
                if current:
                    st.caption("Current map:")
                    st.image(current, width=300)
                map_ver = st.session_state.get('map_ver', 0)
                uploaded_file = st.file_uploader("Choose Image", type=['jpg', 'png', 'jpeg'], key=f"map_file_{map_ver}")
                if uploaded_file and st.button("🚀 Upload Map", type="primary"):
                    db.save_course_map(ENGINE, day, upload_class, uploaded_file.name,
                                       uploaded_file.type or 'image/png', uploaded_file.getvalue())
                    st.session_state.map_ver = map_ver + 1
                    st.session_state.admin_flash = f"Map uploaded for {upload_class}."
                    st.rerun()
                have = db.classes_with_maps(ENGINE, day)
                st.caption("Maps uploaded: " + (', '.join(c for c in sorted_classes if c in have) or 'none yet'))

        # ── Publish results ──
        elif section == "🏆 Publish results":
            if not sorted_classes:
                st.info("Upload a run order first.")
            else:
                sel_input_class = st.selectbox("Which class are these results for?", sorted_classes,
                                               key="res_input_class_sel")
                json_text = st.text_area(
                    "Paste results JSON here:", height=280, key="res_json_text",
                    placeholder='[ { "class_type": "Regular", "height": "8", "uki_number": "...", "uki_dog_number": "...", ... } ]'
                )

                if st.button("👀 Preview Formatted Results", use_container_width=True):
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

                    if st.button("✅ Publish These Results", type="primary", use_container_width=True):
                        db.publish_results(ENGINE, day, sel_input_class, st.session_state.res_parsed_preview)
                        del st.session_state['res_parsed_preview']
                        st.session_state.admin_flash = f"Results for '{sel_input_class}' are now live on the Results tab!"
                        st.rerun()

        # ── Reset ──
        elif section == "♻️ Reset":
            if day:
                st.caption(f"Sets every run on {_day_label(day)} back to Not Checked In, except scratches.")
                confirm = st.checkbox("Yes, reset all statuses for this day", key="reset_confirm")
                if st.button("Reset All Statuses", disabled=not confirm):
                    n = db.reset_statuses(ENGINE, day)
                    st.session_state.admin_flash = f"Reset {n} run(s)."
                    st.rerun()

        if 'admin_flash' in st.session_state:
            st.success(st.session_state.pop('admin_flash'))

# --- TAB 7: RESULTS (view only; publishing is on the Admin tab) ---
with tab7:
    st.header("🏆 Results")
    res_class_map = db.results_for_day(ENGINE, day) if day else {}
    if not res_class_map:
        st.info("No results have been posted yet. Check back after the class runs!")
    else:
        ordered_res_classes = [c for c in sorted_classes if c in res_class_map]
        ordered_res_classes += sorted(c for c in res_class_map if c not in sorted_classes)
        sel_res_class = st.selectbox("Select Class:", ordered_res_classes, key="res_view_class_sel")
        render_formatted_results(res_class_map[sel_res_class])
