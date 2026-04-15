import streamlit as st
from streamlit_gsheets import GSheetsConnection
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
st.caption("Official Agility Check-in & Live Tracker")
st.divider()

# 2. ESTABLISH CONNECTION
conn = st.connection("gsheets", type=GSheetsConnection)

# 3. SMART DATA FETCHING (API SHIELD)
if 'main_df' not in st.session_state:
    st.session_state.main_df = conn.read(worksheet="trialdata", ttl=10)

df = st.session_state.main_df

# Fetch maps and info with long TTL
try:
    maps_df = conn.read(worksheet="coursemaps", ttl=3600)
    info_df = conn.read(worksheet="trialinfo", ttl=3600)
except:
    maps_df = pd.DataFrame()
    info_df = pd.DataFrame()

# Cleanup
df['Run_Order'] = pd.to_numeric(df['Run_Order'], errors='coerce').fillna(0).astype(int)
df['Handler_Number'] = df['Handler_Number'].astype(str)

# 4. TABS
# Create a list of tab names
tab_names = ["📲 My Check-in", "📊 Dashboard", "🏃 Running Order", "ℹ️ Trial Info", "🚧 Gate Steward"]

# Initialize the active tab in session state if it's not there
if "active_tab" not in st.session_state:
    st.session_state.active_tab = tab_names[0]

# Create the tabs and force the selection to the stateful value
# Note: Use the 'key' argument to let Streamlit track which tab is open
tabs = st.tabs(tab_names)

# We use a loop to assign the tabs to variables while checking which one is clicked
tab1, tab2, tab3, tab4, tab5 = tabs
# --- TAB 1: CHECK-IN (Same as before) ---
with tab1:
    handler_input = st.text_input("Enter Handler Number:", placeholder="e.g. 101")
    if handler_input:
        user_data = df[df['Handler_Number'] == str(handler_input)]
        if not user_data.empty:
            status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict"]
            updates = {}
            for dog in user_data['Dog'].unique():
                dog_rows = user_data[user_data['Dog'] == dog]
                with st.container(border=True):
                    st.markdown(f"### 🐶 {dog}")
                    if st.button(f"Check in for all: {dog}", key=f"btn_{dog}"):
                        for idx in dog_rows.index: st.session_state[f"select_{idx}"] = "Checked In"
                        st.rerun()
                    for idx, row in dog_rows.iterrows():
                        if f"select_{idx}" not in st.session_state:
                            st.session_state[f"select_{idx}"] = row['Status'] if row['Status'] in status_options else "Not Checked In"
                        updates[idx] = st.selectbox("Status", options=status_options, key=f"select_{idx}", label_visibility="collapsed")
            if st.button("💾 SAVE ALL CHANGES", type="primary", use_container_width=True):
                for i, s in updates.items(): df.at[i, 'Status'] = s
                conn.update(spreadsheet=st.secrets.connections.gsheets.spreadsheet, data=df)
                del st.session_state.main_df
                st.success("Saved!"); time.sleep(1); st.rerun()

# --- TAB 3: RUNNING ORDER (With Strikethrough for Completed) ---
with tab3:
    st.header("Class Running Order")
    selected_class = st.selectbox("Select Class:", sorted(df['Class'].unique()), key="run_select")
    if selected_class:
        run_df = df[df['Class'] == selected_class].sort_values('Run_Order')
        display_df = run_df[['Handler', 'Dog', 'Height', 'Status']].copy()

        def style_rows(row):
            styles = [''] * len(row)
            # LIVE DOG
            if row['Status'] == 'In Ring':
                styles = ['background-color: #fef08a; color: #854d0e; font-weight: bold; border: 2px solid #eab308'] * len(row)
            # COMPLETED OR SCRATCHED (Strikethrough)
            elif row['Status'] in ['Run Completed', 'Scratch']:
                styles = ['text-decoration: line-through; color: #adb5bd; background-color: #f8f9fa'] * len(row)
            elif row['Status'] == 'Checked In':
                styles = ['background-color: #d4edda; color: #155724'] * len(row)
            return styles

        st.dataframe(display_df.style.apply(style_rows, axis=1), hide_index=True, use_container_width=True)
        st.info("💡 Yellow = In Ring | Strikethrough = Already Run or Scratched")

# --- TAB 5: GATE STEWARD (Simplified) ---
with tab5:
    st.header("🚧 Gate Steward")
    if st.text_input("PIN:", type="password") == "7890":
        gate_class = st.selectbox("Manage Class:", sorted(df['Class'].unique()))
        gate_df = df[df['Class'] == gate_class].sort_values('Run_Order')
        
        for idx, row in gate_df.iterrows():
            if row['Status'] == 'Scratch': continue
            
            c_info, c_btn1, c_btn2 = st.columns([2, 1, 1])
            with c_info:
                st.write(f"#{row['Run_Order']} **{row['Dog']}** ({row['Status']})")
            
            with c_btn1:
                # "In Ring" Button
                if row['Status'] != 'In Ring' and row['Status'] != 'Run Completed':
                    if st.button("IN RING ➡️", key=f"ring_{idx}"):
                        # Update Data
                        df.loc[(df['Class'] == gate_class) & (df['Status'] == 'In Ring'), 'Status'] = 'Run Completed'
                        df.at[idx, 'Status'] = 'In Ring'
                        conn.update(spreadsheet=st.secrets.connections.gsheets.spreadsheet, data=df)

                        # SHIELD & TAB PERSISTENCE
                        del st.session_state.main_df
                        # This ensures we come back to Tab index 4 (the 5th tab)
                        # Note: Modern Streamlit handles tab persistence better if you don't force a reset, 
                        # but setting a query param or session key is the safest "Old School" way.
                        st.rerun()
                elif row['Status'] == 'In Ring':
                    if st.button("DONE ✅", key=f"done_{idx}"):
                        df.at[idx, 'Status'] = 'Run Completed'
                        conn.update(spreadsheet=st.secrets.connections.gsheets.spreadsheet, data=df)
                        del st.session_state.main_df
                        st.rerun()

            with c_btn2:
                # "Undo" Button (Resets to Checked In)
                if row['Status'] in ['In Ring', 'Run Completed']:
                    if st.button("Undo ↩️", key=f"undo_{idx}"):
                        df.at[idx, 'Status'] = 'Checked In'
                        conn.update(spreadsheet=st.secrets.connections.gsheets.spreadsheet, data=df)
                        del st.session_state.main_df
                        st.rerun()