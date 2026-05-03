import streamlit as st
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re

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

# Initial Load for the session
if 'main_df' not in st.session_state:
    fetch_global_data()

# Ensure active_handler exists in session state
if 'active_handler' not in st.session_state:
    st.session_state.active_handler = ""

df = st.session_state.main_df
sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist() if not df.empty else []

# --- 4. TABS SETUP ---
tab1, tab2, tab3, tab5, tab6 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "🚧 Gate", "🔒 Admin"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box").strip()
    
    if handler_input:
        st.session_state.active_handler = handler_input
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
        # Dropdown is OUTSIDE the fragment so it doesn't blink or reset
        sel_c = st.selectbox("Select Class:", sorted_classes, key="ro_sel")
        
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
    if st.text_input("Gate PIN:", type="password", key="g_p_v") == "7890":
        # Dropdown outside fragment
        g_cls = st.selectbox("Current Class:", sorted_classes, key="g_cls")
        
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

                for _, r in g_df.iterrows():
                    if r['status'] == "Scratch": continue
                    
                    # Determine border color based on status
                    is_in_ring = r['status'] == "In Ring"
                    is_done = r['status'] == "Run Completed"
                    border_color = "#ffc107" if is_in_ring else "#28a745" if r['status'] == "Checked In" else "#adb5bd"
                    
                    # Card-style display
                    c_main, c_btn = st.columns([3, 2])
                    
                    with c_main:
                        # Display Run Order as float (1.0, 1.5, etc)
                        ro_display = f"{float(r['Run_Order']):.1f}"
                        st.markdown(f'''
                            <div style="padding: 10px; border-left: 10px solid {border_color}; border-radius: 8px; background-color: #f8f9fa; margin-bottom: 10px;">
                                <div style="font-size: 20px; font-weight: bold; color: #333;">{ro_display} | {r["Name"]}</div>
                                <div style="font-size: 14px; color: #666;">{r["Handler_Name"]} • {r["Height"]}" • {r["status"]}</div>
                            </div>
                        ''', unsafe_allow_html=True)
                        
                    with c_btn:
                        # Logic for buttons
                        pk_val = r['Run_Order']
                        if is_in_ring:
                            if st.button("FINISH ✅", key=f"finish_{pk_val}", use_container_width=True, type="primary"):
                                conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Run_Order", pk_val).execute()
                                st.rerun()
                        elif not is_done:
                            if st.button("START RUN", key=f"start_{pk_val}", use_container_width=True):
                                # Mark any existing "In Ring" as completed automatically
                                conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", target_class).eq("status", "In Ring").execute()
                                # Set this dog to In Ring
                                conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute()
                                st.rerun()
                        else:
                            # Completed runs get a disabled state or "Undo"
                            if st.button("UNDO FINISH", key=f"undo_{pk_val}", use_container_width=True):
                                conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk_val).execute()
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