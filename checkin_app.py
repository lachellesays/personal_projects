import streamlit as st
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re

# --- 1. PAGE CONFIG & UI STYLING ---
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 5rem; padding-bottom: 5rem; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1E3A8A; }
    
    /* Global Button Styling */
    .stButton > button {
        width: 100% !important;
        height: 70px !important;
        font-size: 20px !important;
        font-weight: bold !important;
        border-radius: 12px !important;
    }

    /* Highlight for Handler's own dogs in Dataframe */
    .highlight-row {
        background-color: #e3f2fd !important;
        font-weight: bold;
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

# --- 3. DATA HELPERS & AUTO-REFRESH ---
def fetch_fresh_data():
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
        st.session_state.last_sync = time.time()
    return new_df

def update_status_instant(run_order, new_status):
    try:
        conn_supabase.table("trialdata").update({"status": new_status}).eq("Run_Order", run_order).execute()
        if 'main_df' in st.session_state:
            st.session_state.main_df.loc[st.session_state.main_df['Run_Order'] == run_order, 'status'] = new_status
    except Exception as e:
        st.error(f"Sync Error: {e}")

# Initialize State
if 'main_df' not in st.session_state:
    fetch_fresh_data()

# --- AUTO-REFRESH LOGIC (Every 10 Seconds) ---
# This ensures the "Big Screen" stays updated without manual intervention
if "last_sync" not in st.session_state:
    st.session_state.last_sync = time.time()

refresh_interval = 10 # seconds
if time.time() - st.session_state.last_sync > refresh_interval:
    fetch_fresh_data()
    st.rerun()

df = st.session_state.main_df
sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist() if not df.empty else []

# --- 4. TABS SETUP ---
tab1, tab2, tab3, tab5, tab6 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "🚧 Gate", "🔒 Admin"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    # We store the active UKI number in session state to use for highlighting in Tab 3
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box").strip()
    if handler_input:
        st.session_state.active_handler = handler_input
        user_data = df[df['UKI_Number'] == handler_input]
        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['Handler_Name']}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict", "NFC"]
            
            for dog in user_data['Name'].unique():
                dog_rows = user_data[user_data['Name'] == dog]
                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")
                    if st.button(f"Check in all runs for {dog}", key=f"btn_all_{dog}"):
                        for _, r in dog_rows.iterrows():
                            pk = r['Run_Order']
                            conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk).execute()
                            st.session_state[f"select_{pk}"] = "Checked In"
                        fetch_fresh_data()
                        st.rerun()

                    for idx, row in dog_rows.iterrows():
                        pk = row['Run_Order']
                        key_name = f"select_{pk}"
                        if key_name not in st.session_state:
                            st.session_state[key_name] = row['status']
                        
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class: st.markdown(f"**{row['Combined Class Name']}**")
                        with c_status: 
                            st.selectbox("Status", options=status_options, key=key_name, 
                                         on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]), 
                                         label_visibility="collapsed")

# --- TAB 3: RUNNING ORDER (With Highlighting & Star) ---
with tab3:
    if not df.empty:
        sel_c = st.selectbox("Select Class:", sorted_classes, key="ro_sel")
        
        # Course Map Display
        clean_class_search = sel_c.strip().lower()
        base_search = re.sub(r'[^a-z0-9]', '_', clean_class_search)
        try:
            files_res = conn_supabase.client.storage.from_("coursemaps").list()
            valid_files = [f for f in files_res if f['name'].startswith(base_search)]
            if valid_files:
                valid_files.sort(key=lambda x: x['created_at'], reverse=True)
                map_url = conn_supabase.client.storage.from_("coursemaps").get_public_url(valid_files[0]['name'])
                st.image(map_url, use_container_width=True)
        except: pass

        r_df = df[df['Combined Class Name'] == sel_c].sort_values(['Height', 'Run_Order']).copy()
        
        # --- LOGIC: Highlight & Star Handler's Dogs ---
        current_handler = st.session_state.get('active_handler', None)
        
        def style_running_order(row):
            is_mine = str(row['UKI_Number']) == str(current_handler)
            # Add Star to name if it's the handler's dog
            display_name = f"⭐ {row['Name']}" if is_mine else row['Name']
            return display_name, is_mine

        for h in sorted(r_df['Height'].unique()):
            st.markdown(f'<div class="height-header">{h}" Height</div>', unsafe_allow_html=True)
            
            subset = r_df[r_df['Height'] == h].copy()
            
            # Apply the star and identify rows to highlight
            subset['Name'], subset['is_mine'] = zip(*subset.apply(style_running_order, axis=1))
            
            # Display using a styled dataframe
            def apply_row_style(s):
                return ['background-color: #D1E9FF' if s.is_mine else '' for _ in s]

            styled_subset = subset[['Handler_Name', 'Name', 'Breed', 'status', 'is_mine']].style.apply(apply_row_style, axis=1)
            
            st.dataframe(
                styled_subset, 
                column_order=("Handler_Name", "Name", "Breed", "status"),
                use_container_width=True, 
                hide_index=True
            )

# --- TAB 5: GATE ---
with tab5:
    if st.text_input("Gate PIN:", type="password", key="g_p_v") == "7890":
        g_cls = st.selectbox("Current Class:", sorted_classes, key="g_cls")
        g_df = df[df['Combined Class Name'] == g_cls].sort_values(['Height', 'Run_Order'])
        for _, r in g_df.iterrows():
            if r['status'] == "Scratch": continue
            cm, cb = st.columns([3, 1])
            cm.write(f"**{r['Name']}** ({r['Height']}\") - {r['status']}")
            if r['status'] != "Run Completed":
                if cb.button("START", key=f"g_{r['Run_Order']}"):
                    conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", g_cls).eq("status", "In Ring").execute()
                    update_status_instant(r['Run_Order'], "In Ring")
                    fetch_fresh_data()
                    st.rerun()

# --- TAB 6: ADMIN ---
with tab6:
    st.header("🔒 Secretary Admin")
    if st.text_input("Admin PIN:", type="password", key="a_p_v") == "7890":
        
        if st.button("Reset All Run Statuses"):
            conn_supabase.table("trialdata").update({"status": "Not Checked In"}).neq("status", "Scratch").execute()
            # Clear local state so it triggers a full refresh
            if 'main_df' in st.session_state:
                del st.session_state.main_df
            st.rerun()
            
        st.divider()
        
        # --- RESTORED: Course Map Uploader ---
        st.subheader("🗺️ Course Map Upload")
        upload_class = st.selectbox("Assign Map to Class:", sorted_classes, key="map_assign_select")
        uploaded_file = st.file_uploader("Choose a file", type=['jpg', 'png', 'jpeg'])
        
        if uploaded_file and st.button("🚀 Sync Map to App"):
            with st.spinner("Uploading to Supabase..."):
                clean_filename = f"{re.sub(r'[^a-z0-9]', '_', upload_class.lower())}_{int(time.time())}.{uploaded_file.name.split('.')[-1]}"
                try:
                    conn_supabase.client.storage.from_("coursemaps").upload(path=clean_filename, file=uploaded_file.getvalue())
                    st.success(f"Map for {upload_class} is live!")
                except Exception as e:
                    st.error(f"Upload failed: Make sure the 'coursemaps' bucket exists in Supabase Storage. Error: {e}")