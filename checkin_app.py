import streamlit as st
from streamlit_gsheets import GSheetsConnection
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time

# 1. PAGE CONFIG & UI STYLING
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 5rem; padding-bottom: 2rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1E3A8A; }
    
    /* Visual Status Styles */
    .status-checked { color: #155724; background-color: #d4edda; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .status-conflict { color: #721c24; background-color: #f8d7da; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .status-scratch { color: #383d41; background-color: #e2e3e5; padding: 3px 8px; border-radius: 4px; text-decoration: line-through; }
    .status-default { color: #0c5460; background-color: #d1ecf1; padding: 3px 8px; border-radius: 4px; }
    
    /* Height Header Styling */
    .height-header { background-color: #f1f5f9; padding: 10px; border-radius: 8px; border-left: 5px solid #1E3A8A; margin-top: 20px; font-weight: bold; }
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
        # Rename column for UI preference
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

# 5. TABS SETUP
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📲 My Check-in", "📊 Dashboard", "🏃 Running Order", "ℹ️ Trial Info", "🚧 Gate Steward"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345", key="search_box").strip()

    if handler_input:
        user_data = df[df['UKI_Number'] == handler_input]
        if user_data.empty:
            df = fetch_fresh_data()
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
                        
                        icon, label_class = "⚪", "status-default"
                        if current_status == "Checked In": icon, label_class = "✅", "status-checked"
                        elif current_status == "Conflict": icon, label_class = "⚠️", "status-conflict"
                        elif current_status == "Scratch": icon, label_class = "❌", "status-scratch"
                        
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class:
                            st.markdown(f'{icon} <span class="{label_class}">{row["Combined Class Name"]}</span>', unsafe_allow_html=True)
                        with c_status:
                            st.selectbox("Status", options=status_options, 
                                         index=status_options.index(current_status),
                                         key=f"select_{pk}",
                                         on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]),
                                         label_visibility="collapsed")
        else:
            st.warning(f"Handler #{handler_input} not found.")

# --- TAB 2: DASHBOARD (BROUGHT BACK & ENHANCED) ---
with tab2:
    st.header("📊 Trial Dashboard")
    
    if st.button("🔄 Refresh All Data", key="refresh_dash"):
        fetch_fresh_data()
        st.rerun()

    if not df.empty:
        # High-level Metrics
        m1, m2, m3, m4 = st.columns(4)
        total = len(df)
        checked_in = len(df[df['status'] == 'Checked In'])
        scratched = len(df[df['status'] == 'Scratch'])
        in_ring = len(df[df['status'] == 'In Ring'])
        
        m1.metric("Total Entries", total)
        m2.metric("Checked In", f"{checked_in} ({(checked_in/total)*100:.1f}%)")
        m3.metric("Already Run", in_ring)
        m4.metric("Scratches", scratched, delta_color="inverse")
        
        st.divider()
        
        # Breakdown by Class
        st.subheader("Progress by Class")
        for agility_class in sorted(df['Combined Class Name'].unique()):
            class_data = df[df['Combined Class Name'] == agility_class]
            class_total = len(class_data)
            class_done = len(class_data[class_data['status'] == 'Run Completed'])
            
            # Progress bar for the class
            progress = class_done / class_total if class_total > 0 else 0
            
            with st.expander(f"📌 {agility_class} ({int(progress*100)}% Complete)"):
                col_left, col_right = st.columns([2, 1])
                with col_left:
                    st.progress(progress)
                with col_right:
                    st.write(f"Done: {class_done} / Total: {class_total}")
                
                # Show specific status counts
                st.write(class_data['status'].value_counts())

# --- TAB 3: RUNNING ORDER (Personalized & Grouped) ---
with tab3:
    st.header("Class Running Order")
    if not df.empty:
        selected_class = st.selectbox("Select Class:", sorted(df['Combined Class Name'].unique()), key="run_select")
        
        if selected_class:
            # Course Map Logic
            if not maps_df.empty:
                target = str(selected_class).strip().lower()
                match = maps_df[maps_df['Class'].str.strip().str.lower() == target] if 'Class' in maps_df.columns else pd.DataFrame()
                if not match.empty:
                    st.image(str(match.iloc[0]['Map_Link']), use_container_width=True)

            # Filter and Sort
            run_df = df[df['Combined Class Name'] == selected_class].sort_values(['Height', 'Run_Order'])
            
            # Identify current handler's dogs for highlighting
            current_handler_num = st.session_state.get("search_box", "").strip()
            
            # Grouping by Height
            for height in sorted(run_df['Height'].unique()):
                st.markdown(f'<div class="height-header">📏 Height: {height}"</div>', unsafe_allow_html=True)
                
                height_df = run_df[run_df['Height'] == height].copy()
                
                # Add personalization indicator
                if current_handler_num:
                    height_df['Dog'] = height_df.apply(
                        lambda x: f"🌟 {x['Name']}" if str(x['UKI_Number']) == current_handler_num else x['Name'], 
                        axis=1
                    )
                else:
                    height_df['Dog'] = height_df['Name']

                # Format Display DF
                display_cols = ['Handler_Name', 'Dog', 'Breed', 'Class_Type', 'status']
                # Ensure Class_Type exists
                available_cols = [c for c in display_cols if c in height_df.columns]
                
                final_display = height_df[available_cols].copy()
                # Rename for UI
                column_mapping = {
                    'Handler_Name': 'Handler', 
                    'Class_Type': 'Type', 
                    'status': 'Status'
                }
                final_display = final_display.rename(columns=column_mapping)

                def style_running_order(row):
                    styles = [''] * len(row)
                    # 1. Highlight current handler's dogs in Blue
                    if "🌟" in str(row['Dog']):
                        styles = ['background-color: #dbeafe; color: #1e40af; font-weight: bold'] * len(row)
                    
                    # 2. Status Overrides
                    if row['Status'] == 'In Ring':
                        styles = ['background-color: #fef08a; color: #854d0e; border: 2px solid #854d0e'] * len(row)
                    elif row['Status'] in ['Run Completed', 'Scratch']:
                        styles = ['text-decoration: line-through; color: #adb5bd; background-color: transparent'] * len(row)
                    
                    return styles

                st.dataframe(
                    final_display.style.apply(style_running_order, axis=1), 
                    hide_index=True, 
                    use_container_width=True
                )

# --- TAB 4: TRIAL INFO ---
with tab4:
    st.header("Trial Information")
    if not info_df.empty:
        for _, row in info_df.iterrows():
            st.write(f"**{row['Parameter']}:** {row['Value']}")

# --- TAB 5: GATE STEWARD (Fixing the KeyError) ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("PIN:", type="password", key="gate_pin") == "7890":
        if not df.empty:
            gate_class = st.selectbox("Class:", sorted(df['Combined Class Name'].unique()), key="gate_sel")
            
            # 1. Prepare Data
            gate_df = df[df['Combined Class Name'] == gate_class].sort_values(['Height', 'Run_Order']).copy()
            gate_df = gate_df[gate_df['status'] != 'Scratch']
            
            # 2. Add 'In Ring' checkbox column for the UI
            gate_df['In Ring'] = gate_df['status'] == 'In Ring'
            
            # 3. Truncate Breed for Mobile Space
            gate_df['Breed'] = gate_df['Breed'].apply(lambda x: str(x)[:10] + '..' if len(str(x)) > 10 else x)

            # 4. Prepare the display set
            # We keep 'status' and 'Run_Order' in the background for logic
            display_df = gate_df[['Name', 'Breed', 'Height', 'status', 'In Ring', 'Run_Order']].copy()

            # 5. The Editor (Passing styled object)
            def style_gate_grid(row):
                if row['status'] == 'In Ring':
                    return ['background-color: #fef08a; color: #854d0e; font-weight: bold'] * len(row)
                if row['status'] == 'Run Completed':
                    return ['text-decoration: line-through; color: #adb5bd'] * len(row)
                return [''] * len(row)

            # We apply style to the display_df
            styled_df = display_df.style.apply(style_gate_grid, axis=1)

            edited_df = st.data_editor(
                styled_df,
                hide_index=True,
                use_container_width=True,
                disabled=['Name', 'Breed', 'Height', 'status'], 
                column_config={
                    "In Ring": st.column_config.CheckboxColumn("Ring", width="small"),
                    "Height": st.column_config.TextColumn("Ht", width="small"),
                    "status": None, # Hiding the status text column from view
                    "Run_Order": None # Hiding the primary key from view
                },
                key="gate_editor_final"
            )

            # 6. Action Logic (Safe extraction)
            # We iterate through the original gate_df and check corresponding rows in edited_df
            for i in range(len(gate_df)):
                # Use .iloc to safely access by position
                original_row = gate_df.iloc[i]
                current_pk = original_row['Run_Order']
                original_status = original_row['status']
                
                # Check the state of the checkbox in the edited dataframe
                new_check_val = edited_df.iloc[i]['In Ring']

                # LOGIC: Checkbox turned ON (Move to Ring)
                if new_check_val and original_status != 'In Ring':
                    # Move previous In Ring dog to Completed
                    conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", gate_class).eq("status", "In Ring").execute()
                    # Move this dog to In Ring
                    conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", current_pk).execute()
                    fetch_fresh_data()
                    st.rerun()

                # LOGIC: Checkbox turned OFF (Undo)
                elif not new_check_val and original_status == 'In Ring':
                    conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", current_pk).execute()
                    fetch_fresh_data()
                    st.rerun()

            st.caption("Tap 'Ring' to start. Yellow = Running | Strikethrough = Done")