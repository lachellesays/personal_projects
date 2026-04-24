import streamlit as st
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re

# 1. PAGE CONFIG & UI STYLING
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 5rem; padding-bottom: 5rem; }
.main-header { font-size: 2.2rem; font-weight: 800; color: #1E3A8A; }
.height-header {
    background-color: rgba(30, 58, 138, 0.1);
    padding: 10px;
    border-radius: 8px;
    border-left: 5px solid #1E3A8A;
    margin-top: 20px;
    font-weight: bold;
}
div.stButton > button {
    height: 3em;
    width: 100%;
    font-weight: bold;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">🏆 Trial Secretary App</p>', unsafe_allow_html=True)
st.caption("UKI Agility | Live Portal Powered by Railway & Supabase")
st.divider()

# 2. ESTABLISH SUPABASE CONNECTION
try:
    s_url = st.secrets["connections"]["supabase"]["url"]
    s_key = st.secrets["connections"]["supabase"]["key"]
except KeyError:
    s_url = st.secrets["supabase_url"]
    s_key = st.secrets["supabase_key"]

conn_supabase = st.connection("supabase", type=SupabaseConnection, url=s_url, key=s_key)

# 3. DATA LOADING HELPERS (Fixed for 'Height' Error)
def fetch_fresh_data():
    res = conn_supabase.table("trialdata").select("*").execute()
    new_df = pd.DataFrame(res.data)
    if not new_df.empty:
        # Standardizing common column name variations to 'Height'
        rename_map = {'Intl_Jump_Ht': 'Height', 'dog_height': 'Height', 'Jump_Height': 'Height'}
        for old_col, new_col in rename_map.items():
            if old_col in new_df.columns:
                new_df = new_df.rename(columns={old_col: new_col})
        
        # Ensure UKI number is a string and Run_Order is numeric
        new_df['UKI_Number'] = new_df['UKI_Number'].astype(str).str.strip()
        new_df['Run_Order'] = pd.to_numeric(new_df['Run_Order'], errors='coerce').fillna(0).astype(int)
        
        # Final safety check: if 'Height' still doesn't exist, create it from 0 to prevent crash
        if 'Height' not in new_df.columns:
            new_df['Height'] = 0
            
        st.session_state.main_df = new_df
    return new_df

if 'main_df' not in st.session_state:
    fetch_fresh_data()

df = st.session_state.main_df
sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist() if not df.empty else []

# 4. TABS SETUP
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "📐 Math", "🚧 Gate", "🔒 Admin", "⏱️ SCORING"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box").strip()
    if handler_input:
        user_data = df[df['UKI_Number'] == handler_input]
        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['Handler_Name']}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict", "NFC"]
            for dog in user_data['Name'].unique():
                dog_rows = user_data[user_data['Name'] == dog]
                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")
                    if st.button(f"Check in all runs for {dog}", key=f"btn_{dog}"):
                        for _, r in dog_rows.iterrows():
                            update_status_instant(r['Run_Order'], "Checked In")
                        st.rerun()
                    for idx, row in dog_rows.iterrows():
                        pk = row['Run_Order']
                        current_status = row['status'] if row['status'] in status_options else "Not Checked In"
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class: st.markdown(f"**{row['Combined Class Name']}**")
                        with c_status: st.selectbox("Status", options=status_options, index=status_options.index(current_status), key=f"select_{pk}", on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]), label_visibility="collapsed")

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("📊 Trial Dashboard")
    if st.button("🔄 Refresh All Data", key="refresh_dash"):
        fetch_fresh_data()
        st.rerun()
    if not df.empty:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Entries", len(df))
        m2.metric("Checked In", len(df[df['status'] == 'Checked In']))
        m3.metric("Currently in Ring", len(df[df['status'] == 'In Ring']))
        m4.metric("Scratches", len(df[df['status'] == 'Scratch']))

# --- TAB 3: RUNNING ORDER ---
with tab3:
    st.header("Class Running Order")
    if not df.empty:
        selected_class = st.segmented_control("Select Class:", options=sorted_classes, key="run_segment_select", selection_mode="single", default=sorted_classes[0] if sorted_classes else None)
        if selected_class:
            clean_class_search = selected_class.strip().lower()
            base_search = re.sub(r'[^a-z0-9]', '_', clean_class_search)
            try:
                files_res = conn_supabase.client.storage.from_("coursemaps").list()
                valid_files = [f for f in files_res if f['name'].startswith(base_search)]
                if valid_files:
                    valid_files.sort(key=lambda x: x['created_at'], reverse=True)
                    map_url = conn_supabase.client.storage.from_("coursemaps").get_public_url(valid_files[0]['name'])
                    st.image(map_url, use_container_width=True)
            except: pass
            
            run_df = df[df['Combined Class Name'] == selected_class].sort_values(['Height', 'Run_Order'])
            for height in sorted(run_df['Height'].unique()):
                st.markdown(f'<div class="height-header">📏 Height: {height}"</div>', unsafe_allow_html=True)
                st.dataframe(run_df[run_df['Height'] == height][['Handler_Name', 'Name', 'Breed', 'status']], hide_index=True, use_container_width=True)

# --- TAB 4: COURSE MATH & SCT (Now with Auto-Detection) ---
with tab4:
    st.header("📐 Course Math & SCT")
    if st.text_input("PIN to access calculations:", type="password", key="math_pin") == "7890":
        col1, col2, col3 = st.columns(3)
        with col1:
            target_class = st.selectbox("Select Class to Calculate:", sorted_classes)
            yardage = st.number_input("Measured Course (Yards):", min_value=0, step=1)
        
        # AUTO-DETECTION LOGIC
        detected_index = 0  # Default to Beginner/Novice
        if target_class:
            tc_lower = target_class.lower()
            if any(word in tc_lower for word in ["senior", "champ"]):
                detected_index = 1
            elif any(word in tc_lower for word in ["beginner", "novice"]):
                detected_index = 0

        with col2:
            lvl_group = st.selectbox(
                "Level Group:", 
                ["Beginner/Novice", "Senior/Champion"], 
                index=detected_index
            )
            is_ss = st.checkbox("Is Speedstakes?", value=("speedstakes" in target_class.lower() if target_class else False))
        
        rates = {
            "Agility/Jumping": {"Beginner/Novice": (2.5, 2.9), "Senior/Champion": (2.9, 3.15)},
            "Speedstakes": {"Beginner/Novice": (2.75, 3.25), "Senior/Champion": (3.25, 3.5)}
        }
        small_inc = {"Beginner/Novice": 1.20, "Senior/Champion": 1.10}
        
        type_key = "Speedstakes" if is_ss else "Agility/Jumping"
        r_low, r_high = rates[type_key][lvl_group]
        
        if yardage > 0:
            sct_big_f, sct_big_s = round(yardage/r_high), round(yardage/r_low)
            inc = small_inc[lvl_group]
            sct_small_f, sct_small_s = round(sct_big_f * inc), round(sct_big_s * inc)
            
            st.divider()
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Big Dogs (20-24\")")
                choice_big = st.radio("Select Big Dog SCT:", [f"Fast: {sct_big_f}s", f"Slow: {sct_big_s}s"], key="big_rad")
                st.info(f"Select Big Dogs (20-16): {int(choice_big.split(': ')[1][:-1]) + 3}s")
            with c2:
                st.subheader("Small Dogs (8-16\")")
                choice_small = st.radio("Select Small Dog SCT:", [f"Fast: {sct_small_f}s", f"Slow: {sct_small_s}s"], key="small_rad")
                st.info(f"Select Small Dogs (12-4): {int(choice_small.split(': ')[1][:-1]) + 3}s")
            
            if st.button("💾 Save SCT to Supabase", use_container_width=True):
                final_big = int(choice_big.split(": ")[1][:-1])
                final_small = int(choice_small.split(": ")[1][:-1])
                conn_supabase.table("course_specs").upsert({
                    "class_name": target_class, "yardage": yardage, "level_group": lvl_group, "is_speedstakes": is_ss,
                    "chosen_sct_big": final_big, "chosen_sct_small": final_small
                }).execute()
                st.success(f"SCTs saved for {target_class}!")

# --- TAB 5: GATE STEWARD ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("PIN:", type="password", key="gate_pin") == "7890":
        gate_class = st.selectbox("Class:", sorted_classes, key="gate_sel")
        gate_df = df[df['Combined Class Name'] == gate_class].sort_values(['Height', 'Run_Order'])
        for _, row in gate_df.iterrows():
            if row['status'] == 'Scratch': continue
            pk_val = row['Run_Order']
            is_done, is_in_ring = row['status'] == "Run Completed", row['status'] == "In Ring"
            border_color = "#28a745" if row['status'] == "Checked In" else "#ffc107" if is_in_ring else "#adb5bd"
            c_main, c_btn = st.columns([3, 2])
            with c_main:
                st.markdown(f'''<div style="padding: 12px; border-left: 8px solid {border_color}; border-radius: 6px; background-color: #f8f9fa; height: 80px;">
                    <div style="font-size: 18px; font-weight: bold; color: #333;">{row["Name"]}</div>
                    <div style="font-size: 14px; color: #666;">{row["Breed"]} | {row["Height"]}"</div></div>''', unsafe_allow_html=True)
            with c_btn:
                if not is_done and not is_in_ring:
                    if st.button("START RUN", key=f"ring_{pk_val}"):
                        conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", gate_class).eq("status", "In Ring").execute()
                        conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute()
                        fetch_fresh_data(); st.rerun()
                elif is_in_ring:
                    if st.button("FINISH ✅", key=f"finish_{pk_val}"):
                        conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Run_Order", pk_val).execute(); fetch_fresh_data(); st.rerun()
                elif is_done:
                    if st.button("UNDO ↩️", key=f"undo_{pk_val}"):
                        conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute(); fetch_fresh_data(); st.rerun()

# --- TAB 6: ADMIN ---
with tab6:
    st.header("🔒 Secretary Admin")
    if st.text_input("Admin PIN:", type="password", key="admin_pin") == "7890":
        st.subheader("Course Map Upload")
        upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_assign_select")
        uploaded_file = st.file_uploader("Choose a file", type=['pdf', 'jpg', 'png', 'jpeg'])
        if uploaded_file and st.button("🚀 Sync Map to App"):
            clean_filename = f"{re.sub(r'[^a-z0-9]', '_', upload_class.lower())}_{int(time.time())}.{uploaded_file.name.split('.')[-1]}"
            conn_supabase.client.storage.from_("coursemaps").upload(path=clean_filename, file=uploaded_file.getvalue())
            st.success("Map live!")

# --- TAB 7: SCORING (The Scrimer's Interface) ---
with tab7:
    # 1. AGGRESSIVE CUSTOM CSS FOR TRUE KEYPAD
    st.markdown("""
        <style>
        /* Force the grid container */
        .numpad-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            max-width: 400px;
            margin: 0 auto;
        }
        
        /* Target every button inside the scoring tab */
        .stButton > button {
            width: 100% !important;
            height: 80px !important;
            font-size: 26px !important;
            font-weight: bold !important;
            border-radius: 10px !important;
        }

        /* Digital Time Display Styling */
        .time-display {
            font-size: 65px !important;
            text-align: center;
            background: #1e1e1e;
            color: #00ff41;
            border-radius: 15px;
            padding: 20px;
            font-family: 'Courier New', monospace;
            border: 3px solid #444;
            margin: 20px auto;
            max-width: 400px;
        }

        /* Make the Submit button green and massive */
        .stButton > button[kind="primary"] {
            background-color: #28a745 !important;
            height: 100px !important;
            font-size: 30px !important;
            margin-top: 20px;
        }
        
        /* Fix for mobile/iPad column stacking */
        [data-testid="column"] {
            min-width: 30% !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.header("⏱️ Scrimer's Scoring Booth")
    
    # --- 2. Selection Logic ---
    scoring_class = st.selectbox("Select Class:", sorted_classes, key="score_class_sel")
    is_nursery = "nursery" in scoring_class.lower()
    is_gamblers = "gamblers" in scoring_class.lower()
    
    current_in_ring = df[(df['Combined Class Name'] == scoring_class) & (df['status'] == 'In Ring')]
    all_dogs_in_class = df[df['Combined Class Name'] == scoring_class].sort_values(['Height', 'Run_Order'])
    dog_names = all_dogs_in_class['Name'].tolist()
    
    default_idx = 0
    if not current_in_ring.empty:
        try: default_idx = dog_names.index(current_in_ring.iloc[0]['Name'])
        except: pass
    
    selected_dog_name = st.selectbox("Active Dog:", dog_names, index=default_idx)
    active_dog = all_dogs_in_class[all_dogs_in_class['Name'] == selected_dog_name].iloc[0]

    # SCT Pull
    is_big = int(active_dog['Height']) >= 20
    specs_res = conn_supabase.table("course_specs").select("*").eq("class_name", scoring_class).execute()
    sct = 0
    if specs_res.data:
        specs = specs_res.data[0]
        sct = specs['chosen_sct_big'] if is_big else specs['chosen_sct_small']

    # --- 3. State ---
    if 't_ref' not in st.session_state: st.session_state.t_ref = 0
    if 't_fault' not in st.session_state: st.session_state.t_fault = 0
    if 'is_e' not in st.session_state: st.session_state.is_e = False
    if 'time_str' not in st.session_state: st.session_state.time_str = ""

    # --- 4. Judge's Marks (Standard Columns) ---
    c1, c2, c3 = st.columns(3)
    with c1:
        st.image("https://trialsecretary.notion.site/image/attachment%3A53feb389-ccd9-4f6b-a663-0fd46dc5d9a6%3Aimage.png?table=block&id=34ce6efe-88b7-806b-a467-d2033081650c&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button(f"R ({st.session_state.t_ref})", key="r_btn"):
            st.session_state.t_ref += 1; st.rerun()
    with c2:
        st.image("https://trialsecretary.notion.site/image/attachment%3Aeaf1e083-97fb-4aad-8fca-3eba410da7be%3Aimage.png?table=block&id=34ce6efe-88b7-802b-9ef8-c06561fa78e4&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button(f"F ({st.session_state.t_fault})", key="f_btn"):
            st.session_state.t_fault += 1; st.rerun()
    with c3:
        st.image("https://trialsecretary.notion.site/image/attachment%3Ad1c1f212-0248-4411-ad96-74f00719b948%3Aimage.png?table=block&id=34ce6efe-88b7-8038-a837-e945ab877561&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button("E", type="primary" if st.session_state.is_e else "secondary", key="e_btn"):
            st.session_state.is_e = not st.session_state.is_e; st.rerun()

    # --- 5. Digital Display ---
    display_time = "0.00"
    if st.session_state.time_str:
        raw = st.session_state.time_str.zfill(3)
        display_time = f"{raw[:-2]}.{raw[-2:]}"
    
    st.markdown(f"<div class='time-display'>{display_time}s</div>", unsafe_allow_html=True)

    # --- 6. The 3-Column Keypad ---
    # We use a manual loop to keep the button keys unique and the layout tight
    def press(v): st.session_state.time_str += str(v)
    
    rows = [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9],
        ["CLR", 0, "⌫"]
    ]

    for row in rows:
        cols = st.columns(3)
        for i, val in enumerate(row):
            with cols[i]:
                if val == "CLR":
                    if st.button("CLR", key="clr"): st.session_state.time_str = ""; st.rerun()
                elif val == "⌫":
                    if st.button("⌫", key="back"): st.session_state.time_str = st.session_state.time_str[:-1]; st.rerun()
                else:
                    if st.button(str(val), key=f"num_{val}"): press(val); st.rerun()

    # --- 7. Submit Math ---
    st.divider()
    final_time = float(display_time)
    r_pts = 0 if (is_nursery or is_gamblers) else (st.session_state.t_ref * 5)
    f_pts = (st.session_state.t_fault * 5)
    t_faults = max(0, final_time - sct)
    total = r_pts + f_pts + t_faults

    st.info(f"SCT: {sct}s | Total: {total:.2f}")

    if st.button("🚀 SUBMIT SCORE", type="primary", use_container_width=True):
        status = "Eliminated" if st.session_state.is_e else "Run Completed"
        conn_supabase.table("trialdata").update({
            "status": status, "refusals": st.session_state.t_ref, "faults": st.session_state.t_fault,
            "time": final_time, "total_score": total if not st.session_state.is_e else 999
        }).eq("Run_Order", active_dog['Run_Order']).execute()
        
        st.session_state.t_ref = 0; st.session_state.t_fault = 0; st.session_state.is_e = False; st.session_state.time_str = ""
        st.success("Score Recorded!"); time.sleep(1); fetch_fresh_data(); st.rerun()