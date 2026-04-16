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
    </style>
    """, unsafe_allow_html=True)

st.markdown('<p class="main-header">🏆 Trial Secretary App</p>', unsafe_allow_html=True)
st.caption("UKI Agility | Real-Time Sync Active ⚡")

# --- 2. CONNECTIONS ---
conn_gsheets = st.connection("gsheets", type=GSheetsConnection)
conn_supabase = st.connection(
    "supabase",
    type=SupabaseConnection,
    url=st.secrets["connections"]["supabase"]["url"],
    key=st.secrets["connections"]["supabase"]["key"]
)

# --- 3. CALLBACK FUNCTIONS (The "Instant Save" Logic) ---
def update_status_instant(run_order, new_status):
    """Pushes a single row update to Supabase immediately."""
    try:
        conn_supabase.table("trialdata").update({"status": new_status}).eq("Run_Order", run_order).execute()
        # Clear cache so other tabs see the update
        if 'main_df' in st.session_state:
            del st.session_state.main_df
        st.toast(f"Updated to {new_status}!", icon="💾")
    except Exception as e:
        st.error(f"Sync Failed: {e}")

# --- 4. DATA FETCHING ---
if 'main_df' not in st.session_state:
    res = conn_supabase.table("trialdata").select("*").execute()
    st.session_state.main_df = pd.DataFrame(res.data)

df = st.session_state.main_df

try:
    info_df = conn_gsheets.read(worksheet="trialinfo", ttl=3600)
    maps_df = conn_gsheets.read(worksheet="coursemaps", ttl=3600)
except:
    info_df, maps_df = pd.DataFrame(), pd.DataFrame()

# --- 5. TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📲 My Check-in", "📊 Dashboard", "🏃 Running Order", "ℹ️ Trial Info", "🚧 Gate Steward"])

with tab1:
    handler_input = st.text_input("Enter UKI Handler Number:", placeholder="e.g. 12345")

    if handler_input and not df.empty:
        user_data = df[df['UKI_Number'] == str(handler_input).strip()]
        
        if not user_data.empty:
            st.subheader(f"Welcome, {user_data.iloc[0]['Handler_Name']}")
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict"]
            
            for dog in user_data['Name'].unique():
                dog_rows = user_data[user_data['Name'] == dog]
                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")
                    
                    # Mass Check-in for Dog
                    if st.button(f"Check in all runs for {dog}", key=f"btn_{dog}"):
                        for _, r in dog_rows.iterrows():
                            update_status_instant(r['Run_Order'], "Checked In")
                        st.rerun()

                    for idx, row in dog_rows.iterrows():
                        pk = row['Run_Order']
                        
                        # Visual styling
                        current_status = row['status']
                        icon, label_class = "⚪", "status-default"
                        if current_status == "Checked In": icon, label_class = "✅", "status-checked"
                        elif current_status == "Conflict": icon, label_class = "⚠️", "status-conflict"
                        elif current_status == "Scratch": icon, label_class = "❌", "status-scratch"
                        
                        c_class, c_status = st.columns([1.5, 1])
                        with c_class:
                            st.markdown(f'{icon} <span class="{label_class}">{row["Combined Class Name"]}</span>', unsafe_allow_html=True)
                        with c_status:
                            # The "Magic" happens here with on_change
                            st.selectbox(
                                "Status", 
                                options=status_options, 
                                index=status_options.index(current_status) if current_status in status_options else 0,
                                key=f"select_{pk}",
                                on_change=lambda p=pk: update_status_instant(p, st.session_state[f"select_{p}"]),
                                label_visibility="collapsed"
                            )
        else:
            st.info("Handler not found.")

# Note: Tabs 2-5 remain the same as the previous version, 
# but they will feel faster because the data is always fresh!


# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("Trial Statistics")
    if st.button("🔄 Refresh Data", key="refresh_dash"):
        if 'main_df' in st.session_state: del st.session_state.main_df
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
            # --- IMPROVED COURSE MAP LOGIC ---
            if not maps_df.empty:
                # Normalize strings for comparison (case-insensitive, no extra spaces)
                target = str(selected_class).strip().lower()
                match = maps_df[maps_df['Class'].str.strip().str.lower() == target] if 'Class' in maps_df.columns else pd.DataFrame()
                
                if not match.empty:
                    st.markdown(f"#### 🗺️ Course Map for {selected_class}")
                    st.image(str(match.iloc[0]['Map_Link']), use_container_width=True)
                else:
                    with st.expander("🛠️ Map Debugger"):
                        st.write(f"Looking for match for: `{selected_class}`")
                        st.write("Found in GSheet:", maps_df['Class'].tolist() if 'Class' in maps_df.columns else "Column 'Class' missing!")

            run_df = df[df['Combined Class Name'] == selected_class].sort_values('Run_Order')
            display_df = run_df[['Run_Order', 'Handler_Name', 'Name', 'Breed', 'status']].copy()
            display_df.columns = ['#', 'Handler', 'Dog', 'Breed', 'Status']

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
        for _, row in info_df.iterrows():
            st.write(f"**{row['Parameter']}:** {row['Value']}")

# --- TAB 5: GATE STEWARD ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("PIN:", type="password", key="gate_pin") == "7890":
        if not df.empty:
            gate_class = st.selectbox("Class:", sorted(df['Combined Class Name'].unique()), key="gate_sel")
            gate_df = df[df['Combined Class Name'] == gate_class].sort_values('Run_Order')
            
            for _, row in gate_df.iterrows():
                if row['status'] == 'Scratch': continue
                pk_val = row['Run_Order']
                
                c_info, c_ring, c_undo = st.columns([2, 1, 1])
                with c_info: st.write(f"#{pk_val} **{row['Name']}** ({row['status']})")
                
                with c_ring:
                    if row['status'] not in ['In Ring', 'Run Completed']:
                        if st.button("IN RING", key=f"ring_{pk_val}"):
                            conn_supabase.table("trialdata").update({"status": "Run Completed"}).eq("Combined Class Name", gate_class).eq("status", "In Ring").execute()
                            conn_supabase.table("trialdata").update({"status": "In Ring"}).eq("Run_Order", pk_val).execute()
                            if 'main_df' in st.session_state: del st.session_state.main_df
                            st.rerun()
                
                with c_undo:
                    if row['status'] in ['In Ring', 'Run Completed'] and st.button("Undo", key=f"undo_{pk_val}"):
                        conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", pk_val).execute()
                        if 'main_df' in st.session_state: del st.session_state.main_df
                        st.rerun()