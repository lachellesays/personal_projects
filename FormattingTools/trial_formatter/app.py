"""
app.py — Streamlit front end for the trial data formatter.

Run locally:   streamlit run app.py
Environment:   JOTFORM_API_KEY, APP_PASSWORD, DATA_DIR (optional; Railway volume path)
"""

import os
import hmac
from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yaml

from core import (
    Show, fetch_submissions, parse_entries, entrant_summary, transform, to_xlsx_bytes,
    apply_corrections, handler_table, dog_table, diff_corrections, HANDLER_FIELDS, DOG_FIELDS,
)
from shows import list_shows, save_show, load_show, show_path, dump_show, storage_problem
from cli import load_dotenv

load_dotenv()
st.set_page_config(page_title='Trial Data Formatter', page_icon='🐕', layout='wide')


# ── Password gate ────────────────────────────────────────────────────────────

def check_password() -> bool:
    expected = os.environ.get('APP_PASSWORD')
    if not expected:
        st.error('APP_PASSWORD is not set. Add it as a Railway variable (or in trial_formatter/.env locally).')
        return False
    if st.session_state.get('authed'):
        return True
    with st.form('login'):
        pw = st.text_input('Password', type='password')
        if st.form_submit_button('Log in'):
            if hmac.compare_digest(pw, expected):
                st.session_state.authed = True
                st.rerun()
            st.error('Incorrect password')
    return False


if not check_password():
    st.stop()

if problem := storage_problem():
    st.error('⚠ ' + problem)

API_KEY = os.environ.get('JOTFORM_API_KEY')
if not API_KEY:
    st.error('JOTFORM_API_KEY is not set.')
    st.stop()


@st.cache_data(ttl=300, show_spinner='Fetching submissions from JotForm...')
def get_submissions(form_id: str) -> list:
    return fetch_submissions(form_id, API_KEY)


# ── Sidebar: pick or create a show ───────────────────────────────────────────

NEW = '➕ New show'
shows = list_shows()
labels = {slug: f'{s.name} ({s.saturday_date})' for slug, s in shows}

with st.sidebar:
    st.header('Show')
    options = list(labels) + [NEW]
    default_slug = st.session_state.get('slug')
    slug = st.selectbox(
        'Select a show', options,
        index=options.index(default_slug) if default_slug in options else 0,
        format_func=lambda s: labels.get(s, s),
    )
    is_new = slug == NEW
    show = None if is_new else load_show(show_path(slug))

    # Prefill a new show from the most recent one (same form/show ID, next weekend)
    template = shows[0][1] if shows else None

    def _date(s, fallback):
        try:
            return date.fromisoformat(s)
        except (TypeError, ValueError):
            return fallback

    with st.form('show_settings'):
        base = show or template
        name = st.text_input('Show name', value=show.name if show else '')
        show_id = st.number_input('Show ID', min_value=0, step=1, value=int(base.show_id) if base else 0)
        form_id = st.text_input('JotForm form ID', value=base.form_id if base else '')
        sat_default = date.today() + timedelta(days=(5 - date.today().weekday()) % 7)
        sat = st.date_input('Saturday date', value=_date(show.saturday_date, sat_default) if show else sat_default)
        sun = st.date_input('Sunday date', value=_date(show.sunday_date, sat + timedelta(days=1)) if show else sat_default + timedelta(days=1))
        excl = st.text_area(
            'Exclude addresses (one per line)',
            value='\n'.join((show or template).exclude_addresses) if (show or template) else '',
            help='Address variants to skip when a handler typed their address differently on different submissions.',
        )
        if st.form_submit_button('Save show', type='primary'):
            if not name or not form_id or not show_id:
                st.error('Name, show ID and form ID are required.')
            else:
                updated = Show(
                    name=name.strip(), show_id=int(show_id), form_id=form_id.strip(),
                    saturday_date=sat.isoformat(), sunday_date=sun.isoformat(),
                    scratched_handlers=show.scratched_handlers if show else [],
                    scratched_dogs=show.scratched_dogs if show else [],
                    exclude_addresses=[a.strip() for a in excl.splitlines() if a.strip()],
                    field_names=show.field_names if show else (template.field_names if template else {}),
                )
                st.session_state.slug = save_show(updated, None if is_new else slug)
                st.rerun()

    if show:
        st.divider()
        st.download_button('Download show config', dump_show(show), file_name=f'{slug}.yaml', mime='text/yaml')
    uploaded = st.file_uploader('Restore a show config', type=['yaml', 'yml'])
    if uploaded is not None and st.button('Import'):
        imported = Show.from_dict(yaml.safe_load(uploaded.getvalue()) or {})
        st.session_state.slug = save_show(imported)
        st.rerun()


if show is None:
    st.title('Trial Data Formatter')
    st.info('Create a show in the sidebar to get started.')
    st.stop()


# ── Main: entries, scratches, generate ───────────────────────────────────────

st.title(show.name)
st.caption(f'Show ID {show.show_id} · Sat {show.saturday_date} · Sun {show.sunday_date} · Form {show.form_id}')

col_a, col_b = st.columns([1, 5])
if col_a.button('↻ Refresh from JotForm'):
    get_submissions.clear()

try:
    submissions = get_submissions(show.form_id)
    entries = parse_entries(submissions, show.field_names)
except Exception as e:  # noqa: BLE001 — surface any API/parse problem in the UI
    st.error(f'Could not load submissions: {e}')
    st.stop()
col_b.write(f'**{len(submissions)}** active submissions')

summary = entrant_summary(entries, show)
scratched_h = {str(x) for x in show.scratched_handlers}
scratched_d = {str(x) for x in show.scratched_dogs}

st.subheader('Scratches')
st.caption('Tick anyone who has scratched, then click **Save scratches**. '
           'A scratched handler is removed from all three tabs; a scratched dog removes just that dog\'s runs.')

handlers = (summary.groupby(['handler_number', 'handler_name'], sort=False)
            .agg(dogs=('dog_name', lambda s: ', '.join(s)), runs=('runs', 'sum'), owed=('owed', 'sum'))
            .reset_index())
handlers.insert(0, 'scratched', handlers['handler_number'].isin(scratched_h))
dogs = summary.copy()
dogs.insert(0, 'scratched', dogs['dog_number'].isin(scratched_d))

tab_h, tab_d = st.tabs([f'Handlers ({len(handlers)})', f'Dogs ({len(dogs)})'])
money = st.column_config.NumberColumn(format='$%.2f')
with tab_h:
    edited_h = st.data_editor(
        handlers, hide_index=True, use_container_width=True, key=f'h_{slug}',
        disabled=[c for c in handlers.columns if c != 'scratched'],
        column_config={
            'scratched': st.column_config.CheckboxColumn('Scratch'),
            'handler_number': 'Handler #', 'handler_name': 'Handler', 'dogs': 'Dogs',
            'runs': 'Runs', 'owed': st.column_config.NumberColumn('Class fees', format='$%.2f'),
        },
    )
with tab_d:
    edited_d = st.data_editor(
        dogs, hide_index=True, use_container_width=True, key=f'd_{slug}',
        disabled=[c for c in dogs.columns if c != 'scratched'],
        column_config={
            'scratched': st.column_config.CheckboxColumn('Scratch'),
            'handler_number': 'Handler #', 'handler_name': 'Handler',
            'dog_number': 'Dog #', 'dog_name': 'Dog',
            'runs': 'Runs', 'owed': st.column_config.NumberColumn('Class fees', format='$%.2f'),
        },
    )

new_h = sorted(edited_h.loc[edited_h['scratched'], 'handler_number'])
new_d = sorted(edited_d.loc[edited_d['scratched'], 'dog_number'])
# Keep scratches for numbers that no longer appear in JotForm (e.g. a deleted submission)
known_h, known_d = set(handlers['handler_number']), set(dogs['dog_number'])
new_h += sorted(scratched_h - known_h)
new_d += sorted(scratched_d - known_d)

dirty = set(new_h) != scratched_h or set(new_d) != scratched_d
if st.button('Save scratches', type='primary' if dirty else 'secondary', disabled=not dirty):
    show.scratched_handlers, show.scratched_dogs = new_h, new_d
    save_show(show, slug)
    st.session_state.flash = f'Saved: {len(new_h)} handler(s), {len(new_d)} dog(s) scratched.'
    st.rerun()
if 'flash' in st.session_state:
    st.success(st.session_state.pop('flash'))
if dirty:
    st.warning('You have unsaved scratch changes. The workbook below already reflects them.')

# ── Corrections ──────────────────────────────────────────────────────────────

st.subheader('Fix typos')
st.caption('Click any cell to fix what someone typed, then click **Save corrections**. '
           'Fixes are kept with this show and re-applied every time you refresh from JotForm. '
           'They show up on every tab (e.g. a corrected name changes Contact, Balances and Raw Results). '
           'Save before changing the search box, or unsaved edits are dropped.')

FIELD_LABELS = {
    'handler_number': 'Handler #', 'first_name': 'First name', 'last_name': 'Last name',
    'email': 'Email', 'addr_line1': 'Address 1', 'addr_line2': 'Address 2', 'city': 'Town',
    'state': 'State (County)', 'postal': 'Zip', 'dog_number': 'Dog #', 'handler': 'Handler',
    'dog_name': 'Dog name', 'dog_breed': 'Breed', 'jump_height': 'Jump height',
    'international_level': 'Level', 'speedstakes_level': 'Speedstakes level',
}
corrected = apply_corrections(entries, show)
orig_h, orig_d = handler_table(entries), dog_table(entries)
cur_h, cur_d = handler_table(corrected), dog_table(corrected)
search = st.text_input('Search', placeholder='Type a name or number to filter the tables',
                       key=f'fix_search_{slug}')


def _filter(df):
    if not search:
        return df
    mask = df.astype(str).apply(lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
    return df[mask]


fix_h, fix_d = st.tabs([f'Handlers ({len(cur_h)})', f'Dogs ({len(cur_d)})'])
with fix_h:
    view_h = _filter(cur_h)
    edited_fix_h = st.data_editor(
        view_h, hide_index=True, use_container_width=True, key=f'fix_h_{slug}_{search}',
        disabled=['handler_number'], column_config=FIELD_LABELS,
    )
with fix_d:
    st.caption('Jump height and levels use the JotForm wording, e.g. "20 regular", "16 select", "Champion".')
    view_d = _filter(cur_d)
    edited_fix_d = st.data_editor(
        view_d, hide_index=True, use_container_width=True, key=f'fix_d_{slug}_{search}',
        disabled=['dog_number', 'handler'], column_config=FIELD_LABELS,
    )

# Start from the saved corrections, then overwrite the rows visible in the (possibly filtered) tables
new_hc = {k: v for k, v in show.handler_corrections.items() if k not in set(view_h['handler_number'])}
new_hc.update(diff_corrections(orig_h, edited_fix_h, 'handler_number', HANDLER_FIELDS))
new_dc = {k: v for k, v in show.dog_corrections.items() if k not in set(view_d['dog_number'])}
new_dc.update(diff_corrections(orig_d, edited_fix_d, 'dog_number', DOG_FIELDS))

# List every correction, with a way to undo it
orig_h_idx, orig_d_idx = orig_h.set_index('handler_number'), orig_d.set_index('dog_number')
fix_rows = []
for kind, corrections, idx in (('Handler', new_hc, orig_h_idx), ('Dog', new_dc, orig_d_idx)):
    for num, fields in corrections.items():
        for f, val in fields.items():
            before = idx.at[num, f] if num in idx.index else '(not in JotForm)'
            fix_rows.append({'undo': False, 'kind': kind, 'number': num, 'field': FIELD_LABELS[f],
                             'from_jotform': before, 'corrected_to': val, '_field': f})
if fix_rows:
    with st.expander(f'{len(fix_rows)} correction(s)', expanded=True):
        fixes = st.data_editor(
            pd.DataFrame(fix_rows), hide_index=True, use_container_width=True, key=f'fix_list_{slug}',
            disabled=['kind', 'number', 'field', 'from_jotform', 'corrected_to'],
            column_order=['undo', 'kind', 'number', 'field', 'from_jotform', 'corrected_to'],
            column_config={'undo': st.column_config.CheckboxColumn('Undo'), 'kind': '', 'number': '#',
                           'field': 'Field', 'from_jotform': 'From JotForm', 'corrected_to': 'Corrected to'},
        )
    for _, r in fixes[fixes['undo']].iterrows():
        target = new_hc if r['kind'] == 'Handler' else new_dc
        target.get(r['number'], {}).pop(r['_field'], None)
        if not target.get(r['number'], True):
            del target[r['number']]

fixes_dirty = new_hc != show.handler_corrections or new_dc != show.dog_corrections
if st.button('Save corrections', type='primary' if fixes_dirty else 'secondary', disabled=not fixes_dirty):
    show.handler_corrections, show.dog_corrections = new_hc, new_dc
    save_show(show, slug)
    for k in [k for k in st.session_state if str(k).startswith(('fix_h_', 'fix_d_', 'fix_list_'))]:
        del st.session_state[k]
    st.session_state.flash_fix = 'Corrections saved.'
    st.rerun()
if 'flash_fix' in st.session_state:
    st.success(st.session_state.pop('flash_fix'))
if fixes_dirty:
    st.warning('You have unsaved corrections. The workbook below already reflects them.')

# Generate using the on-screen selections, saved or not
preview = Show(**{**show.__dict__, 'scratched_handlers': new_h, 'scratched_dogs': new_d,
                  'handler_corrections': new_hc, 'dog_corrections': new_dc})
result = transform(entries, preview)

st.subheader('Workbook')
m1, m2, m3 = st.columns(3)
m1.metric('Contact', f'{len(result.contact)} handlers')
m2.metric('Balances', f'{len(result.balances)} handlers',
          f"${result.balances['OWES'].sum():,.2f} outstanding" if len(result.balances) else None,
          delta_color='off')
m3.metric('Raw Results', f'{len(result.raw_results)} runs')

for w in result.warnings:
    st.warning(w)

st.download_button(
    '⬇ Download workbook', to_xlsx_bytes(result), type='primary',
    file_name=f'TrialData_{slug}.xlsx',
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
)

with st.expander('Preview tabs'):
    t1, t2, t3 = st.tabs(['Contact', 'Balances', 'Raw Results'])
    t1.dataframe(result.contact, hide_index=True, use_container_width=True)
    t2.dataframe(result.balances, hide_index=True, use_container_width=True, column_config={'OWES': money})
    t3.dataframe(result.raw_results, hide_index=True, use_container_width=True,
                 column_config={'Date': st.column_config.DateColumn(format='YYYY-MM-DD')})
