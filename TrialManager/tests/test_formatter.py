import importlib.util
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import SLUG, submission

ROOT = Path(__file__).resolve().parents[2]


def show():
    from app import db
    return db.get_show(SLUG)


def test_entries_page_and_balances(admin):
    c, _ = admin
    page = c.get(f'/shows/{SLUG}/entries')
    assert page.status_code == 200
    for name in ('Ann Smith', 'Rex', 'Bea', 'Bob Jones', 'Cara Lee', 'Dee Fox'):
        assert name in page.text
    assert '$15.00' in page.text and 'class="owes"' in page.text and 'class="credit"' in page.text
    assert 'Not downloaded yet' in page.text
    # Filters
    assert 'Bob Jones' not in c.get(f'/shows/{SLUG}/entries?f=owes').text
    assert 'Bob Jones' in c.get(f'/shows/{SLUG}/entries?q=200').text


def test_scratch_toggle_saves_instantly(admin):
    c, token = admin
    r = c.post(f'/shows/{SLUG}/scratch/501', data={'csrf': token, 'q': '', 'f': 'all'}, headers={'HX-Request': 'true'})
    assert r.status_code == 200 and 'hx-swap-oob' in r.text and 'Scratched' in r.text
    assert show().scratched_dogs == ['501']
    c.post(f'/shows/{SLUG}/scratch/501', data={'csrf': token})
    assert show().scratched_dogs == []
    from app import db
    assert db.last_actions(SLUG, 'scratch')['d:501'][0] == 'Lachelle'


def test_unscratching_one_dog_of_a_whole_handler_scratch(admin):
    """Shows scratched in the old formatter by handler still work."""
    c, token = admin
    from app import db
    s = show(); s.scratched_handlers = ['100']; db.save_show(s, SLUG)
    c.post(f'/shows/{SLUG}/scratch/500', data={'csrf': token})
    s = show()
    assert s.scratched_handlers == [] and s.scratched_dogs == ['501']


def test_typo_fix_and_undo(admin):
    c, token = admin
    r = c.get(f'/shows/{SLUG}/typos/cell', params={'kind': 'h', 'num': '400', 'field': 'state', 'edit': 1})
    assert 'name="value"' in r.text
    r = c.post(f'/shows/{SLUG}/typos/cell', data={'csrf': token, 'kind': 'h', 'num': '400', 'field': 'state', 'value': 'VA'})
    assert 'fixed' in r.text and 'id="fixlog"' in r.text and 'Lachelle' in r.text
    assert show().handler_corrections == {'400': {'state': 'VA'}}
    # Typing the original value back removes the fix
    c.post(f'/shows/{SLUG}/typos/cell', data={'csrf': token, 'kind': 'h', 'num': '400', 'field': 'state', 'value': 'Alabama'})
    assert show().handler_corrections == {}
    # Undo from the log
    c.post(f'/shows/{SLUG}/typos/cell', data={'csrf': token, 'kind': 'd', 'num': '600', 'field': 'dog_name', 'value': 'Maxine'})
    assert show().dog_corrections == {'600': {'dog_name': 'Maxine'}}
    assert 'Maxine' in c.get(f'/shows/{SLUG}/entries').text
    c.post(f'/shows/{SLUG}/typos/undo', data={'csrf': token, 'key': 'd:600:dog_name'})
    assert show().dog_corrections == {}


def test_warnings_are_actionable(admin):
    c, token = admin
    page = c.get(f'/shows/{SLUG}/warnings').text
    assert 'Bob Jones (200) has a credit of $45.00' in page
    assert 'Cara Lee (300) typed their address 2 ways' in page
    assert 'zip code 23220 is in VA' in page

    c.post(f'/shows/{SLUG}/warnings', data={'csrf': token, 'key': 'addr:300', 'action': 'address', 'choice': '9 OAK ROAD'})
    assert show().exclude_addresses == ['9 OAK RD']
    c.post(f'/shows/{SLUG}/warnings', data={'csrf': token, 'key': 'zip:400', 'action': 'fixstate:VA'})
    assert show().handler_corrections == {'400': {'state': 'VA'}}
    c.post(f'/shows/{SLUG}/warnings', data={'csrf': token, 'key': 'credit:200', 'action': 'dismiss'})

    from app import formatter as fmt
    ws = fmt.warnings(fmt.load(SLUG))
    assert fmt.open_warning_count(ws) == 0
    c.post(f'/shows/{SLUG}/warnings', data={'csrf': token, 'key': 'credit:200', 'action': 'reopen'})
    assert fmt.open_warning_count(fmt.warnings(fmt.load(SLUG))) == 1


def test_download_and_new_entries_since_last_download(admin, app_env):
    c, token = admin
    r = c.post(f'/shows/{SLUG}/download', data={'csrf': token})
    assert r.headers['content-disposition'] == f'attachment; filename="TrialData_{SLUG}.xlsx"'
    sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    assert list(sheets) == ['Contact', 'Balances', 'Raw Results'] and len(sheets['Raw Results']) == 7
    assert 'No new entries since your last download' in c.get(f'/shows/{SLUG}/download').text

    # A new JotForm submission arrives; after a refresh it's flagged as new
    app_env['data'] = app_env['data'] + [submission('7', 'Eve', 'Ng', '900', '990', 'Nova', sat=['Agility 1'], paid='15.00')]
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    page = c.get(f'/shows/{SLUG}/entries?f=new').text
    assert '1 new entry' in page and 'Eve Ng' in page and 'Ann Smith' not in page
    hist = c.get(f'/shows/{SLUG}/download').text
    assert hist.count('Lachelle') >= 2  # sidebar + history row


def test_scratched_and_fixed_data_flow_into_the_workbook(admin):
    c, token = admin
    c.post(f'/shows/{SLUG}/scratch/600', data={'csrf': token})             # Bob's only dog
    c.post(f'/shows/{SLUG}/typos/cell', data={'csrf': token, 'kind': 'h', 'num': '100', 'field': 'last_name', 'value': 'Smyth'})
    r = c.post(f'/shows/{SLUG}/download', data={'csrf': token})
    sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    assert 200 not in sheets['Contact']['Member ID'].tolist()
    assert 'Smyth' in sheets['Contact']['Last Name'].tolist()


def test_shares_shows_with_the_streamlit_formatter(admin):
    """A show saved by FormattingTools/trial_formatter (DB mode) opens here, and changes made here are seen there."""
    fmt_dir = ROOT / 'FormattingTools' / 'trial_formatter'
    if not fmt_dir.exists():
        pytest.skip('formatter folder not present')
    sys.path.insert(0, str(fmt_dir))
    try:
        spec = importlib.util.spec_from_file_location('old_shows', fmt_dir / 'shows.py')
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        old._engine = None
        c, token = admin
        c.post(f'/shows/{SLUG}/scratch/700', data={'csrf': token})
        assert old.get_show(SLUG).scratched_dogs == ['700']
        s = old.get_show(SLUG); s.dog_corrections = {'800': {'dog_name': 'Kitty'}}; old.save_show(s, SLUG)
        assert 'Kitty' in c.get(f'/shows/{SLUG}/entries').text
    finally:
        sys.path.remove(str(fmt_dir))


REAL = Path('/private/tmp/claude-501/-Users-victoriahenderson/d1dde547-ce07-4d83-a4ef-c55f3869de40/scratchpad/jf/subs.json')


@pytest.mark.skipif(not REAL.exists(), reason='real June sample not available')
def test_workbook_matches_formatter_on_real_june_data(admin, app_env):
    """Same JotForm data + same show settings -> identical workbook to the current formatter."""
    from app import core, db
    app_env['data'] = json.loads(REAL.read_text())['content']
    c, token = admin
    s = show(); s.show_id = 6892; s.saturday_date, s.sunday_date = '2026-06-06', '2026-06-07'; db.save_show(s, SLUG)
    c.post(f'/shows/{SLUG}/refresh', data={'csrf': token})
    ours = pd.read_excel(io.BytesIO(c.post(f'/shows/{SLUG}/download', data={'csrf': token}).content), sheet_name=None)
    theirs = pd.read_excel(io.BytesIO(core.to_xlsx_bytes(core.transform(core.parse_entries(app_env['data']), show()))), sheet_name=None)
    for name in ('Contact', 'Balances', 'Raw Results'):
        pd.testing.assert_frame_equal(ours[name], theirs[name])
    assert len(ours['Raw Results']) == 218
