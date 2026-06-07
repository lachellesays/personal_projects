import streamlit as st
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re
import json
from datetime import datetime, timezone

RESULTS_PIN = "7890"

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

# --- 2. DATABASE CONNECTION ---
try:
    s_url = st.secrets["connections"]["supabase"]["url"]
    s_key = st.secrets["connections"]["supabase"]["key"]
except KeyError:
    s_url = st.secrets["supabase_url"]
    s_key = st.secrets["supabase_key"]

conn_supabase = st.connection("supabase", type=SupabaseConnection, url=s_url, key=s_key)

# --- 3. GLOBAL DATA HELPERS ---
# We still use a global fetch for static tabs like Dashboard and Check-in
def fetch_global_data():
    res = conn_supabase.table("trialdata").select("*").execute()
    new_df = pd.DataFrame(res.data)
    if not new_df.empty:
        rename_map = {'Intl_Jump_Ht': 'Height', 'dog_height': 'Height', 'Jump_Height': 'Height'}
        for old_col, new_col in rename_map.items():
            if old_col in new_df.columns:
                new_df = new_df.rename(columns={old_col: new_col})
        
        new_df['UKI_Number'] = new_df['UKI_Number'].astype(str).str.strip()
        new_df['Run_Order'] = pd.to_numeric(new_df['Run_Order'], errors='coerce').fillna(0).astype(int)
        st.session_state.main_df = new_df
    return new_df

def update_status_instant(run_order, new_status):
    try:
        conn_supabase.table("trialdata").update({"status": new_status}).eq("Run_Order", run_order).execute()
        if 'main_df' in st.session_state:
            st.session_state.main_df.loc[st.session_state.main_df['Run_Order'] == run_order, 'status'] = new_status
    except Exception as e:
        st.error(f"Sync Error: {e}")

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
                faults_raw = r.get('faults', '')
                qualify_val = str(r.get('qualify', '')).strip().upper()
                place_raw = str(r.get('place', '')).strip()

                display_rows.append({
                    "Place": place_raw if place_raw else "—",
                    "Handler": r.get('uki_number', ''),
                    "Dog": r.get('uki_dog_number', ''),
                    "Time": _clean_val(r.get('time')),
                    "SCT": _clean_val(r.get('sct')),
                    "Faults": _clean_val(faults_raw),
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

            styled = disp_df.style.apply(_style_results_row, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)

# Initial Load for the session
if 'main_df' not in st.session_state:
    fetch_global_data()

# Ensure active_handler exists in session state
if 'active_handler' not in st.session_state:
    st.session_state.active_handler = ""

df = st.session_state.main_df
sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist() if not df.empty else []

# --- 4. TABS SETUP ---
tab1, tab2, tab3, tab5, tab6, tab7 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "🚧 Gate", "🔒 Admin", "🏆 Results"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    with st.form("checkin_form"):
        handler_input_raw = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box_input")
        submitted = st.form_submit_button("Submit", use_container_width=True, type="primary")

    if submitted:
        st.session_state.active_handler = handler_input_raw.strip()

    handler_input = st.session_state.get('active_handler', '')
    if handler_input:
        user_data = df[df['UKI_Number'] == handler_input]
        
        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['Handler_Name']}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict", "NFC"]
            
            # --- NEW SORTING LOGIC ---
            # 1. Determine the "global" order of classes based on the very first dog to run in each
            class_order_map = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().to_dict()
            
            # 2. Assign a sort priority to the handler's data based on that global order
            user_data = user_data.copy() # Avoid slice warnings
            user_data['class_priority'] = user_data['Combined Class Name'].map(class_order_map)
            # --------------------------

            for dog in user_data['Name'].unique():
                # Filter for this dog and sort its classes by the trial's running order
                dog_rows = user_data[user_data['Name'] == dog].sort_values('class_priority')
                
                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")
                    
                    if st.button(f"Check in all runs for {dog}", key=f"btn_all_{dog}"):
                        for _, r in dog_rows.iterrows():
                            update_status_instant(r['Run_Order'], "Checked In")
                            st.session_state[f"select_{r['Run_Order']}"] = "Checked In"
                        st.rerun()

                    for idx, row in dog_rows.iterrows():
                        pk = row['Run_Order']
                        key_name = f"select_{pk}"
                        
                        if key_name not in st.session_state:
                            st.session_state[key_name] = row['status']
                        
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class: 
                            st.markdown(f"**{row['Combined Class Name']}**")
                        with c_status:
                            st.selectbox(
                                "Status",
                                options=status_options,
                                key=key_name,
                                on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]),
                                label_visibility="collapsed"
                            )
        else:
            trial_days = df['Day'].dropna().unique() if 'Day' in df.columns else []
            trial_dates = df['Date'].dropna().unique() if 'Date' in df.columns else []
            day_str = trial_days[0] if len(trial_days) > 0 else ""
            date_str = trial_dates[0] if len(trial_dates) > 0 else ""
            day_date = f"{day_str} {date_str}".strip()
            loaded_for = f" Data loaded for **{day_date}** only." if day_date else ""
            st.warning(f"Handler not found.{loaded_for} If you're entered for this date and feel like this is a mistake, please reach out to the trial secretary!")

# --- TAB 2: DASHBOARD ---
with tab2:
    if st.button("🔄 Manual Force Refresh", key="dash_refresh"): 
        fetch_global_data()
        st.rerun()
        
    if not df.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Entries", len(df))
        
        # Updated to include both 'Checked In' and 'Conflict'
        c2.metric("Checked In", len(df[df['status'].isin(['Checked In', 'Conflict'])]))
        
        c3.metric("Scratched", len(df[df['status'] == 'Scratch']))
        c4.metric("Completed", len(df[df['status'] == 'Run Completed']))

# --- TAB 3: RUNNING ORDER (LIVE DISPLAY via Fragment) ---
with tab3:
    if not df.empty:
        # Class selector grid — no keyboard on mobile
        if 'ro_sel' not in st.session_state or st.session_state.ro_sel not in sorted_classes:
            st.session_state.ro_sel = sorted_classes[0] if sorted_classes else None
        st.markdown("**Select Class:**")
        ro_cols = st.columns(2)
        ro_half = (len(sorted_classes) + 1) // 2
        for _i, _cls in enumerate(sorted_classes):
            with ro_cols[0 if _i < ro_half else 1]:
                if st.button(_cls, key=f"ro_btn_{_i}", use_container_width=True,
                             type="primary" if st.session_state.ro_sel == _cls else "secondary"):
                    st.session_state.ro_sel = _cls
                    st.rerun()
        sel_c = st.session_state.ro_sel
        
        # Course Map Display (Outside Fragment)
        clean_class_search = sel_c.strip().lower()
        base_search = re.sub(r'[^a-z0-9]', '_', clean_class_search)
        try:
            files_res = conn_supabase.client.storage.from_("coursemaps").list()
            valid_files = [f for f in files_res if f['name'].startswith(base_search)]
            if valid_files:
                valid_files.sort(key=lambda x: x['created_at'], reverse=True)
                map_url = conn_supabase.client.storage.from_("coursemaps").get_public_url(valid_files[0]['name'])
                st.image(map_url, use_container_width=True)
        except: 
            pass

        # --- THE FRAGMENT ---
        @st.fragment(run_every=10)
        def live_running_order_view(target_class, handler_num):
            st.caption(f"Live Sync Active • Last Update: {time.strftime('%H:%M:%S')}")
            
            # Fetch fresh data directly from DB
            res = conn_supabase.table("trialdata").select("*").eq("Combined Class Name", target_class).execute()
            r_df = pd.DataFrame(res.data)
            
            if not r_df.empty:
                # Standardize height columns
                rename_map = {'Intl_Jump_Ht': 'Height', 'dog_height': 'Height', 'Jump_Height': 'Height'}
                for old_col, new_col in rename_map.items():
                    if old_col in r_df.columns:
                        r_df = r_df.rename(columns={old_col: new_col})
                
                # Handle Run_Order as Float and Sort
                r_df['Run_Order'] = pd.to_numeric(r_df['Run_Order'], errors='coerce').fillna(0.0)
                r_df = r_df.sort_values('Run_Order')

                # Create display copy
                subset = r_df.copy()

                # Add star to active handler's dogs (No strikethrough logic here)
                subset['Name'] = subset.apply(
                    lambda r: f"⭐ {r['Name']}" if str(r['UKI_Number']).strip() == str(handler_num).strip() and handler_num != "" else r['Name'], 
                    axis=1
                )

                # --- ROW STYLING (The "Grey Out" Logic) ---
                def highlight_row(s):
                    styles = [''] * len(s)
                    # Check status and handler ownership
                    is_mine = str(s['UKI_Number']).strip() == str(handler_num).strip() and handler_num != ""
                    is_in_ring = s['status'] == 'In Ring'
                    is_done = s['status'] == 'Run Completed'
                    is_scratch = s['status'] == 'Scratch'

                    for i in range(len(s)):
                        if is_in_ring:
                            styles[i] = 'background-color: #FFF59D; color: #000000; border: 2px solid #FFD600;' # Bright Yellow
                        elif is_done or is_scratch:
                            styles[i] = 'color: #A0A0A0; font-style: italic;' # Greyed Out + Italic
                        elif is_mine:
                            styles[i] = 'background-color: #E3F2FD; color: #000000;' # Light Blue for "My Dogs"
                    return styles

                # Apply styling and formatting
                styled_table = subset[[ 'Height', 'Handler_Name', 'Name', 'Breed', 'status', 'UKI_Number']].style \
                    .apply(highlight_row, axis=1) \
                    .format({"Run_Order": "{:.1f}"}) \
                    .set_properties(**{
                        'font-size': '22px', 
                        'font-weight': 'bold'
                    })

                # Render the dataframe
                st.dataframe(
                    styled_table,
                    column_order=("Height", "Handler_Name", "Name", "Breed", "status"),
                    use_container_width=True,
                    hide_index=True,
                    key=f"ro_table_{target_class}"
                )
            else:
                st.info("No data found for this class.")

        # Execute
        h_num = st.session_state.get('active_handler', "")
        live_running_order_view(sel_c, h_num)

# --- TAB 5: GATE STEWARD (LIVE DISPLAY via Fragment) ---
with tab5:
    st.header("🚧 Gate Steward")
    with st.form("gate_pin_form"):
        st.text_input("Gate PIN:", type="password", key="g_p_v")
        st.form_submit_button("Enter", use_container_width=True, type="primary")

    if st.session_state.get("g_p_v", "") == "7890":
        # Class selector grid — no keyboard on mobile
        if 'g_cls' not in st.session_state or st.session_state.g_cls not in sorted_classes:
            st.session_state.g_cls = sorted_classes[0] if sorted_classes else None
        st.markdown("**Current Class:**")
        g_cols = st.columns(2)
        g_half = (len(sorted_classes) + 1) // 2
        for _i, _cls in enumerate(sorted_classes):
            with g_cols[0 if _i < g_half else 1]:
                if st.button(_cls, key=f"g_btn_{_i}", use_container_width=True,
                             type="primary" if st.session_state.g_cls == _cls else "secondary"):
                    st.session_state.g_cls = _cls
                    st.rerun()
        g_cls = st.session_state.g_cls
        
        @st.fragment(run_every=5) # Gate updates slightly faster (every 5s)
        def gate_steward_view(target_class):
            st.caption(f"Gate Live Sync • Last Update: {time.strftime('%H:%M:%S')}")
            
            # Fetch fresh data for this class
            res = conn_supabase.table("trialdata").select("*").eq("Combined Class Name", target_class).execute()
            g_df = pd.DataFrame(res.data)
            
            if not g_df.empty:
                # Standardize heights
                rename_map = {'Intl_Jump_Ht': 'Height', 'dog_height': 'Height', 'Jump_Height': 'Height'}
                for old_col, new_col in rename_map.items():
                    if old_col in g_df.columns:
                        g_df = g_df.rename(columns={old_col: new_col})
                        
                # --- UPDATED: Handle Run_Order as Float for inserted dogs ---
                g_df['Run_Order'] = pd.to_numeric(g_df['Run_Order'], errors='coerce').fillna(0.0)
                
                # Sort strictly by Run_Order (removes height segmentation)
                g_df = g_df.sort_values('Run_Order')

                prev_aframe = None
                for _, r in g_df.iterrows():
                    if r['status'] == "Scratch": continue

                    is_in_ring = r['status'] == "In Ring"
                    is_done = r['status'] == "Run Completed"
                    is_checked_in = r['status'] == "Checked In"
                    pk_val = r['Run_Order']

                    border_color = "#ffc107" if is_in_ring else "#28a745" if is_checked_in else "#adb5bd"

                    # Card-style display
                    c_main, c_btn = st.columns([3, 2])

                    with c_main:
                        ro_display = f"{float(r['Run_Order']):.1f}"
                        try:
                            height_val = float(re.sub(r'[^0-9.]', '', str(r['Height'])))
                        except (ValueError, TypeError):
                            height_val = 999
                        is_select = str(r.get('Class_Type', '')).strip().lower() == 'select'
                        aframe = "A-Frame: Down" if (height_val <= 12 or is_select) else "A-Frame: Up"
                        aframe_changed = aframe != prev_aframe
                        if aframe_changed:
                            aframe_style = f"font-size: 14px; font-weight: bold; color: {'#dc3545' if aframe == 'A-Frame: Down' else '#198754'};"
                        else:
                            aframe_style = "font-size: 14px; color: #adb5bd;"
                        prev_aframe = aframe
                        st.markdown(f'''
                            <div style="padding: 10px; border-left: 10px solid {border_color}; border-radius: 8px; background-color: #f8f9fa; margin-bottom: 10px;">
                                <div style="font-size: 20px; font-weight: bold; color: #333;">{r["Height"]} | {r["Name"]} <span style="font-weight: normal;">({r["Breed"]})</span></div>
                                <div style="font-size: 14px; color: #666;">{r["Handler_Name"]} • {r["Class_Type"]} • {r["status"]}</div>
                                <div style="{aframe_style}">{aframe}</div>
                            </div>
                        ''', unsafe_allow_html=True)

                    with c_btn:
                        if is_in_ring:
                            if st.button("FINISH ✅", key=f"finish_{pk_val}", use_container_width=True, type="primary"):
                                conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Run_Order", pk_val).execute()
                                st.rerun()
                        elif is_done:
                            if st.button("UNDO FINISH", key=f"undo_{pk_val}", use_container_width=True):
                                conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk_val).execute()
                                st.rerun()
                        elif is_checked_in:
                            if st.button("START RUN", key=f"start_{pk_val}", use_container_width=True):
                                conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", target_class).eq("status", "In Ring").execute()
                                conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute()
                                st.rerun()
                        else:
                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button("CHECK IN", key=f"checkin_{pk_val}", use_container_width=True):
                                    conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk_val).execute()
                                    st.rerun()
                            with b2:
                                if st.button("SCRATCH", key=f"scratch_{pk_val}", use_container_width=True):
                                    conn_supabase.table("trialdata").update({"status": "Scratch"}).eq("Run_Order", pk_val).execute()
                                    st.rerun()
            else:
                st.info("No data found for this class.")

        # Execute the fragment
        gate_steward_view(g_cls)

# --- TAB 6: ADMIN ---
with tab6:
    st.header("🔒 Secretary Admin")
    if st.text_input("Admin PIN:", type="password", key="a_p_v") == "7890":
        if st.button("Reset All Statuses"):
            conn_supabase.table("trialdata").update({"status": "Not Checked In"}).neq("status", "Scratch").execute()
            fetch_global_data()
            st.success("All statuses reset!")
            st.rerun()
            
        st.divider()
        st.subheader("🗺️ Course Map Upload")
        upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_up_sel")
        uploaded_file = st.file_uploader("Choose Image", type=['jpg', 'png', 'jpeg'])
        
        if uploaded_file and st.button("🚀 Sync Map"):
            with st.spinner("Uploading to Supabase..."):
                clean_filename = f"{re.sub(r'[^a-z0-9]', '_', upload_class.lower())}_{int(time.time())}.{uploaded_file.name.split('.')[-1]}"
                conn_supabase.client.storage.from_("coursemaps").upload(path=clean_filename, file=uploaded_file.getvalue())
                st.success("Map Uploaded!")

# --- TAB 7: RESULTS ---
with tab7:
    st.header("🏆 Results")
    res_mode = st.radio(
        "Mode", ["View Results", "Submit Results"],
        horizontal=True, label_visibility="collapsed", key="res_mode_sel"
    )
    st.divider()

    # --- VIEW RESULTS (open to everyone) ---
    if res_mode == "View Results":
        try:
            res_rows = conn_supabase.table("results").select("class_name, data, submitted_at").execute().data
        except Exception as e:
            res_rows = []
            st.error(f"Could not load results: {e}")

        if not res_rows:
            st.info("No results have been posted yet. Check back after the class runs!")
        else:
            res_class_map = {r['class_name']: r for r in res_rows}
            # Order by the trial's run order where possible, append anything else alphabetically
            ordered_res_classes = [c for c in sorted_classes if c in res_class_map]
            ordered_res_classes += sorted(c for c in res_class_map if c not in sorted_classes)
            sel_res_class = st.selectbox("Select Class:", ordered_res_classes, key="res_view_class_sel")
            render_formatted_results(res_class_map[sel_res_class]['data'])

    # --- SUBMIT RESULTS (PIN-gated) ---
    else:
        with st.form("results_pin_form"):
            st.text_input("Results PIN:", type="password", key="res_pin_v")
            st.form_submit_button("Unlock", use_container_width=True, type="primary")

        entered_pin = st.session_state.get("res_pin_v", "")
        if entered_pin == "":
            pass
        elif entered_pin != RESULTS_PIN:
            st.error("Incorrect PIN.")
        else:
            sel_input_class = st.selectbox(
                "Which class are these results for?", sorted_classes, key="res_input_class_sel"
            )
            json_text = st.text_area(
                "Paste results JSON here:",
                height=280,
                key="res_json_text",
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
                    try:
                        conn_supabase.table("results").upsert({
                            "class_name": sel_input_class,
                            "data": st.session_state.res_parsed_preview,
                            "submitted_at": datetime.now(timezone.utc).isoformat(),
                        }, on_conflict="class_name").execute()
                        st.success(f"Results for '{sel_input_class}' are now live on the View Results tab!")
                        del st.session_state['res_parsed_preview']
                    except Exception as e:
                        st.error(f"Sync Error: {e}")