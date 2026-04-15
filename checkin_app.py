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
    </style>
    """, unsafe_allow_html=True)

st.markdown('<p class="main-header">🏆 Trial Secretary App</p>', unsafe_allow_html=True)
st.caption("UKI Agility Trial | Powered by Supabase")
st.divider()

# 2. ESTABLISH CONNECTIONS
conn_gsheets = st.connection("gsheets", type=GSheetsConnection)
conn_supabase = st.connection("supabase", type=SupabaseConnection)

# 3. SMART DATA FETCHING
if 'main_df' not in st.session_state:
    try:
        res = conn_supabase.table("trialdata").select("*").execute()
        st.session_state.main_df = pd.DataFrame(res.data)
    except Exception as e:
        st.error(f"Error connecting to Supabase: {e}")
        st.session_state.main_df = pd.DataFrame()

df = st.session_state.main_df

# Static Info from Google Sheets (cached for 1 hour)
try:
    info_df = conn_gsheets.read(worksheet="trialinfo", ttl=3600)
    maps_df = conn_gsheets.read(worksheet="coursemaps", ttl=3600)
except Exception:
    info_df = pd.DataFrame(columns=["Parameter", "Value"])
    maps_df = pd.DataFrame(columns=["Class", "Map_Link"])

# --- DATA CLEANUP ---
if not df.empty:
    df['Run_Order'] = pd.to_numeric(df['Run_Order'], errors='coerce').fillna(0).astype(int)
    df['Handler_Name'] = df['Handler_Name'].astype(str)

# 4. TABS SETUP
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📲 My Check-in", 
    "📊 Dashboard", 
    "🏃 Running Order", 
    "ℹ️ Trial Info",
    "🚧 Gate Steward"
])

# --- TAB 1: INDIVIDUAL CHECK-IN ---
with tab1:
    handler_input = st.text_input("Enter Handler Name:", placeholder="First Last", key="handler_search")

    if handler_input and not df.empty:
        user_data = df[df['Handler_Name'].str.contains(handler_input, case=False, na=False)]
        
        if not user_data.empty:
            st.subheader(f"Welcome, {handler_input}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict"]
            all_handler_updates = {}
            unique_dogs = user_data['Name'].unique() 
            
            for dog in unique_dogs:
                dog_rows = user_data[user_data['Name'] == dog]
                
                with st.container(border=True):
                    col_name, col_btn = st.columns([1.2, 1])
                    with col_name:
                        st.markdown(f"### 🐶 {dog}")
                    
                    with col_btn:
                        if st.button(f"Check in all: {dog}", key=f"btn_{dog}"):
                            for _, r in dog_rows.iterrows():
                                st.session_state[f"sel_{r['id']}"] = "Checked In"
                            st.rerun()

                    for index, row in dog_rows.iterrows():
                        row_id = row['id']
                        if f"sel_{row_id}" not in st.session_state:
                            st.session_state[f"sel_{row_id}"] = row['status'] if row['status'] in status_options else "Not Checked In"
                        
                        current_val = st.session_state[f"sel_{row_id}"]
                        icon = "✅" if current_val == "Checked In" else "⚠️" if current_val == "Conflict" else "❌" if current_val == "Scratch" else "⚪"
                        
                        c_class, c_status = st.columns([1, 1.2])
                        with c_class:
                            st.markdown(f"{icon} **{row['Combined Class Name']}**")
                        
                        with c_status:
                            all_handler_updates[row_id] = st.selectbox(
                                "Status", 
                                options=status_options, 
                                key=f"sel_{row_id}",
                                label_visibility="collapsed"
                            )

            st.divider()
            if st.button("💾 SAVE ALL CHANGES", type="primary", use_container_width=True):
                with st.spinner("Syncing..."):
                    for r_id, s_val in all_handler_updates.items():
                        conn_supabase.table("trialdata").update({"status": s_val}).eq("id", r_id).execute()
                    
                    if 'main_df' in st.session_state:
                        del st.session_state.main_df 
                    st.success("Updates Saved!")
                    time.sleep(1)
                    st.rerun()
        else:
            st.info("Handler not found.")

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("Trial Statistics")
    if st.button("🔄 Refresh Data", key="refresh_dash"):
        if 'main_df' in st.session_state:
            del st.session_state.main_df
        st.rerun()

    if not df.empty:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Entries", len(df))
        c2.metric("Checked In", len(df[df['status'] == 'Checked In']))
        c3.metric("Scratches", len(df[df['status'] == 'Scratch']), delta_color="inverse")
        
        st.divider()
        
        for agility_class in sorted(df['Combined Class Name'].unique()):
            class_data = df[df['Combined Class Name'] == agility_class]
            with st.expander(f"📌 {agility_class}"):
                st.write(class_data['status'].value_counts())

# --- TAB 3: RUNNING ORDER ---
with tab3:
    st.header("Class Running Order")
    if not df.empty:
        selected_class = st.selectbox("Select Class:", sorted(df['Combined Class Name'].unique()), key="run_select")
        
        if selected_class:
            if not maps_df.empty:
                map_row = maps_df[maps_df['Class'] == selected_class]
                if not map_row.empty:
                    st.image(str(map_row.iloc[0]['Map_Link']), use_container_width=True)

            run_df = df[df['Combined Class Name'] == selected_class].sort_values('Run_Order')
            display_df = run_df[['Run_Order', 'Handler_Name', 'Name', 'Breed', 'status']].copy()
            display_df.columns = ['#', 'Handler', 'Dog', 'Breed', 'Status']

            # FIXED: Corrected indentation for the style function
            def style_rows(row):
                styles = [''] * len(row)
                if row['Status'] == 'In Ring':
                    styles = ['background-color: #fef08a; color: #854d0e; font-weight: bold'] * len(row)
                elif row['Status'] in ['Run Completed', 'Scratch']:
                    styles = ['text-decoration: line-through; color: #adb5bd'] * len(row)
                elif row['Status'] == 'Checked In':
                    styles = ['background-color: #d4edda; color: #155724'] * len(row)
                return styles

            st.dataframe(display_df.style.apply(style_rows, axis=1), hide_index=True, use_container_width=True)

# --- TAB 4: TRIAL INFO ---
with tab4:
    st.header("Trial Information")
    if not info_df.empty:
        for index, row in info_df.iterrows():
            st.write(f"**{row['Parameter']}:** {row['Value']}")

# --- TAB 5: GATE STEWARD ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("Enter Gate PIN:", type="password", key="gate_pin") == "7890":
        if not df.empty:
            gate_class = st.selectbox("Manage Class:", sorted(df['Combined Class Name'].unique()), key="gate_class_sel")
            gate_df = df[df['Combined Class Name'] == gate_class].sort_values('Run_Order')
            
            for idx, row in gate_df.iterrows():
                if row['status'] == 'Scratch': continue
                
                c_info, c_ring, c_undo = st.columns([2, 1, 1])
                with c_info:
                    st.write(f"#{row['Run_Order']} **{row['Name']}** ({row['status']})")
                
                with c_ring:
                    if row['status'] not in ['In Ring', 'Run Completed']:
                        if st.button("IN RING", key=f"ring_{row['id']}"):
                            conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", gate_class).eq("status", "In Ring").execute()
                            conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("id", row['id']).execute()
                            if 'main_df' in st.session_state: del st.session_state.main_df
                            st.rerun()
                
                with c_undo:
                    if row['status'] in ['In Ring', 'Run Completed']:
                        if st.button("Undo", key=f"undo_{row['id']}"):
                            conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("id", row['id']).execute()
                            if 'main_df' in st.session_state: del st.session_state.main_df
                            st.rerun()