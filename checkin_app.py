import streamlit as st
from streamlit_gsheets import GSheetsConnection
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re
import requests

# 1. PAGE CONFIG & UI STYLING
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
    <style>
    /* 1. LAYOUT & SPACING */
    .block-container { 
        padding-top: 5rem; 
        padding-bottom: 5rem; 
    }
    .stTabs [data-baseweb="tab-list"] { 
        gap: 8px; 
    }
    .main-header { 
        font-size: 2.2rem; 
        font-weight: 800; 
        color: #1E3A8A; 
    }
    
    /* 2. MOBILE SCROLLING FIX (iPhone Optimized) */
    /* Restore native 'bounce' and momentum to prevent the 'stuck' feeling */
    html, body {
        overscroll-behavior-y: auto !important;
        overflow: auto !important;
    }

    /* Increase dropdown height to reduce need for scrolling in lists */
    div[data-baseweb="popover"] ul {
        max-height: 450px !important; 
        -webkit-overflow-scrolling: touch !important; 
    }

    /* Add room at the bottom so the iPhone Home Bar doesn't block buttons */
    .main .block-container {
        padding-bottom: 150px !important;
    }

    /* 3. STATUS LABEL STYLING */
    .status-checked { color: #155724; background-color: #d4edda; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .status-conflict { color: #721c24; background-color: #f8d7da; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .status-scratch { color: #383d41; background-color: #e2e3e5; padding: 3px 8px; border-radius: 4px; text-decoration: line-through; }
    .status-default { color: #0c5460; background-color: #d1ecf1; padding: 3px 8px; border-radius: 4px; }
    
    /* 4. THEME-AWARE HEIGHT HEADER (Light & Dark Mode) */
    /* Uses RGBA transparency so the background adapts to the theme */
    .height-header { 
        background-color: rgba(30, 58, 138, 0.1); 
        color: inherit; /* Keeps text white in dark mode, black in light mode */
        padding: 10px; 
        border-radius: 8px; 
        border-left: 5px solid #1E3A8A; 
        margin-top: 20px; 
        margin-bottom: 10px;
        font-weight: bold;
        font-size: 1.1rem;
    }
    </style>
    """, unsafe_allow_html=True)

st.markdown('<p class="main-header">🏆 Trial Secretary App</p>', unsafe_allow_html=True)
st.caption("UKI Agility | Personalized Live Portal")
st.divider()

# 2. ESTABLISH CONNECTIONS
conn_gsheets = st.connection("gsheets", type=GSheetsConnection)

try:
    s_url = st.secrets["connections"]["supabase"]["url"]
    s_key = st.secrets["connections"]["supabase"]["key"]
except KeyError:
    s_url = st.secrets["supabase_url"]
    s_key = st.secrets["supabase_key"]

conn_supabase = st.connection("supabase", type=SupabaseConnection, url=s_url, key=s_key)

# 3. HELPER FUNCTIONS
def fetch_fresh_data():
    res = conn_supabase.table("trialdata").select("*").execute()
    new_df = pd.DataFrame(res.data)
    if not new_df.empty:
        new_df['UKI_Number'] = new_df['UKI_Number'].astype(str).str.strip()
        new_df['Run_Order'] = pd.to_numeric(new_df['Run_Order'], errors='coerce').fillna(0).astype(int)
        if 'Intl_Jump_Ht' in new_df.columns:
            new_df = new_df.rename(columns={'Intl_Jump_Ht': 'Height'})
    st.session_state.main_df = new_df
    return new_df

def update_status_instant(run_order, new_status):
    try:
        conn_supabase.table("trialdata").update({"status": new_status}).eq("Run_Order", run_order).execute()
        fetch_fresh_data()
        st.toast(f"Saved: {new_status}", icon="⚡")
    except Exception as e:
        st.error(f"Sync Failed: {e}")

# 4. INITIAL DATA LOAD
if 'main_df' not in st.session_state:
    fetch_fresh_data()

df = st.session_state.main_df

try:
    info_df = conn_gsheets.read(worksheet="trialinfo", ttl=3600)
    maps_df = conn_gsheets.read(worksheet="coursemaps", ttl=3600)
except:
    info_df, maps_df = pd.DataFrame(), pd.DataFrame()

# 4.1 CHRONOLOGICAL CLASS SORTING
if not df.empty:
    sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist()
else:
    sorted_classes = []

# 4.5 LIVE "ON DECK" BANNER (Fragment Version)
@st.fragment
def render_on_deck_banner():
    handler_id = st.session_state.get("search_box", "").strip()
    if handler_id:
        res = conn_supabase.table("trialdata").select("*").execute()
        f_df = pd.DataFrame(res.data)
        if not f_df.empty:
            f_df['UKI_Number'] = f_df['UKI_Number'].astype(str).str.strip()
            f_df['Run_Order'] = pd.to_numeric(f_df['Run_Order'], errors='coerce').fillna(0).astype(int)
            master_queue = f_df[~f_df['status'].isin(['Run Completed', 'Scratch'])].sort_values('Run_Order')
            my_remaining = master_queue[master_queue['UKI_Number'] == handler_id]
            
            if my_remaining.empty:
                if not f_df[f_df['UKI_Number'] == handler_id].empty:
                    st.success("🎉 All your runs for the day are finished!")
            else:
                next_run = my_remaining.iloc[0]
                try:
                    queue_list = list(master_queue['Run_Order'])
                    position = queue_list.index(next_run['Run_Order'])
                    c_msg, c_refresh = st.columns([5, 1])
                    with c_msg:
                        if position == 0:
                            st.warning(f"🚀 **{next_run['Name']} is IN THE RING!** ({next_run['Combined Class Name']})")
                        else:
                            st.info(f"🐾 **Next Run:** {next_run['Name']} in {next_run['Combined Class Name']}. There are **{position}** dogs before you.")
                    with c_refresh:
                        if st.button("🔄", key="frag_refresh_btn", use_container_width=True):
                            st.rerun()
                except ValueError:
                    pass

render_on_deck_banner()

# 5. TABS SETUP
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📲 My Check-in", "📊 Dashboard", "🏃 Running Order", "ℹ️ Trial Info", "🚧 Gate Steward", "🔒 Admin"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box").strip()
    if handler_input:
        user_data = df[df['UKI_Number'] == handler_input]
        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['Handler_Name']}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict"]
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
                        with c_class:
                            st.markdown(f"**{row['Combined Class Name']}**")
                        with c_status:
                            st.selectbox("Status", options=status_options, index=status_options.index(current_status), key=f"select_{pk}", on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]), label_visibility="collapsed")

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("📊 Trial Dashboard")
    if st.button("🔄 Refresh All Data", key="refresh_dash"):
        fetch_fresh_data()
        st.rerun()
    if not df.empty:
        m1, m2, m3, m4 = st.columns(4)
        total = len(df)
        checked_in = len(df[df['status'] == 'Checked In'])
        in_ring = len(df[df['status'] == 'In Ring'])
        scratched = len(df[df['status'] == 'Scratch'])
        m1.metric("Total Entries", total)
        m2.metric("Checked In", f"{checked_in}")
        m3.metric("Currently in Ring", in_ring)
        m4.metric("Scratches", scratched)
        st.divider()
        for agility_class in sorted_classes:
            class_data = df[df['Combined Class Name'] == agility_class]
            class_done = len(class_data[class_data['status'] == 'Run Completed'])
            progress = class_done / len(class_data) if len(class_data) > 0 else 0
            with st.expander(f"📌 {agility_class} ({int(progress*100)}% Complete)"):
                st.progress(progress)

# --- TAB 3: RUNNING ORDER ---
with tab3:
    st.header("Class Running Order")
    if not df.empty:
        selected_class = st.selectbox("Select Class:", sorted_classes, key="run_select")
        if selected_class:
            clean_class_search = selected_class.strip().lower()
            base_search = re.sub(r'[^a-z0-9]', '_', clean_class_search)
            map_displayed = False
            try:
                files_res = conn_supabase.client.storage.from_("coursemaps").list()
                valid_files = [f for f in files_res if f['name'].startswith(base_search)]
                if valid_files:
                    valid_files.sort(key=lambda x: x['created_at'], reverse=True)
                    latest_file = valid_files[0]['name']
                    map_url = conn_supabase.client.storage.from_("coursemaps").get_public_url(latest_file)
                    if latest_file.lower().endswith('.pdf'):
                        st.markdown(f'<div style="width: 100%; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.1);"><iframe src="{map_url}#view=FitH" width="100%" height="500px" style="border:none;"></iframe></div>', unsafe_allow_html=True)
                    else:
                        st.image(map_url, use_container_width=True, caption=f"Latest Map for {selected_class}")
                    map_displayed = True
            except Exception as e:
                st.error(f"Error fetching maps: {e}")
            if not map_displayed:
                st.info("📍 No course map uploaded yet for this class.")
            st.divider()
            run_df = df[df['Combined Class Name'] == selected_class].sort_values(['Height', 'Run_Order'])
            current_handler_num = st.session_state.get("search_box", "").strip()
            for height in sorted(run_df['Height'].unique()):
                st.markdown(f'<div class="height-header">📏 Height: {height}"</div>', unsafe_allow_html=True)
                height_df = run_df[run_df['Height'] == height].copy()
                if current_handler_num:
                    height_df['Dog'] = height_df.apply(lambda x: f"🌟 {x['Name']}" if str(x['UKI_Number']) == current_handler_num else x['Name'], axis=1)
                else:
                    height_df['Dog'] = height_df['Name']
                display_cols = ['Handler_Name', 'Dog', 'Breed', 'status']
                final_display = height_df[display_cols].copy().rename(columns={'Handler_Name': 'Handler', 'status': 'Status'})
                def style_ro(row):
                    styles = [''] * len(row)
                    if "🌟" in str(row['Dog']): styles = ['background-color: #dbeafe; color: #1e40af; font-weight: bold'] * len(row)
                    if row['Status'] == 'In Ring': styles = ['background-color: #fef08a; color: #854d0e; border: 2px solid #854d0e'] * len(row)
                    elif row['Status'] in ['Run Completed', 'Scratch']: styles = ['text-decoration: line-through; color: #adb5bd'] * len(row)
                    return styles
                st.dataframe(final_display.style.apply(style_ro, axis=1), hide_index=True, use_container_width=True)

# --- TAB 4: TRIAL INFO ---
with tab4:
    st.header("Trial Information")
    if not info_df.empty:
        for _, row in info_df.iterrows():
            st.write(f"**{row['Parameter']}:** {row['Value']}")

# --- TAB 5: GATE STEWARD ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("PIN:", type="password", key="gate_pin") == "7890":
        gate_class = st.selectbox("Class:", sorted_classes, key="gate_sel")
        gate_df = df[df['Combined Class Name'] == gate_class].sort_values(['Height', 'Run_Order'])
        for _, row in gate_df.iterrows():
            if row['status'] == 'Scratch': continue
            pk_val = row['Run_Order']
            is_done = row['status'] == "Run Completed"
            is_in_ring = row['status'] == "In Ring"
            border_color = "#28a745" if row['status'] == "Checked In" else "#ffc107" if is_in_ring else "#adb5bd"
            c_main, c_btn = st.columns([5, 1])
            with c_main:
                st.markdown(f'<div style="padding: 10px; border-left: 6px solid {border_color}; border-radius: 4px; background-color: white;">{row["Name"]} | {row["Height"]}"</div>', unsafe_allow_html=True)
            with c_btn:
                if not is_done and not is_in_ring:
                    if st.button("▶️", key=f"ring_{pk_val}", use_container_width=True):
                        conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", gate_class).eq("status", "In Ring").execute()
                        conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute()
                        fetch_fresh_data()
                        st.rerun()
                elif is_in_ring or is_done:
                    if st.button("↩️", key=f"undo_{pk_val}", use_container_width=True):
                        conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk_val).execute()
                        fetch_fresh_data()
                        st.rerun()

# --- TAB 6: ADMIN MAP UPLOAD ---
with tab6:
    st.header("🔒 Secretary Admin")
    if st.text_input("Admin PIN:", type="password", key="admin_pin") == "7890":
        st.subheader("Course Map Upload")
        upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_assign_select")
        uploaded_file = st.file_uploader("Choose a file (PDF, JPG, PNG)", type=['pdf', 'jpg', 'jpeg', 'png'])
        if uploaded_file and upload_class:
            if st.button("🚀 Sync Map to App", use_container_width=True):
                with st.spinner("Uploading..."):
                    file_ext = uploaded_file.name.split('.')[-1].lower()
                    clean_class = upload_class.strip().lower()
                    safe_class_name = re.sub(r'[^a-z0-9]', '_', clean_class)
                    clean_filename = f"{safe_class_name}_{int(time.time())}.{file_ext}"
                    try:
                        conn_supabase.client.storage.from_("coursemaps").upload(
                            path=clean_filename,
                            file=uploaded_file.getvalue(),
                            file_options={"upsert": "true", "content-type": f"application/pdf" if file_ext == 'pdf' else f"image/{file_ext}"}
                        )
                        st.success(f"Success! {upload_class} map is live.")
                    except Exception as e:
                        st.error(f"Upload failed: {e}")