import streamlit as st
from st_supabase_connection import SupabaseConnection
import pandas as pd
import time
import re

# --- 1. PAGE CONFIG & UI STYLING ---
st.set_page_config(page_title="Agility Trial Center", page_icon="🐾", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 5rem; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1E3A8A; }
    
    /* Global Button Styling */
    .stButton > button {
        width: 100% !important;
        height: 70px !important;
        font-size: 20px !important;
        font-weight: bold !important;
        border-radius: 12px !important;
    }

    /* Scoring Display */
    .time-display {
        font-size: 60px !important;
        text-align: center;
        background: #1e1e1e;
        color: #00ff41;
        border-radius: 15px;
        padding: 15px;
        font-family: monospace;
        border: 3px solid #444;
        margin-bottom: 15px;
    }
    
    /* Force 3 columns on mobile */
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

# --- 3. DATA HELPERS ---
def fetch_fresh_data():
    res = conn_supabase.table("trialdata").select("*").execute()
    new_df = pd.DataFrame(res.data)
    if not new_df.empty:
        # Standardize Height Column
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
        # We don't fetch_fresh_data here to keep the UI from lagging on every click
        st.toast(f"Status: {new_status}")
    except Exception as e:
        st.error(f"Sync Error: {e}")

if 'main_df' not in st.session_state:
    fetch_fresh_data()

df = st.session_state.main_df
sorted_classes = df.groupby('Combined Class Name')['Run_Order'].min().sort_values().index.tolist() if not df.empty else []

# --- 4. TABS SETUP ---
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📲 Check-in", "📊 Dash", "🏃 Order", "📐 Math", "🚧 Gate", "🔒 Admin", "⏱️ SCORING"
])

# --- TAB 1: RESTORED CHECK-IN ---
with tab1:
    h_input = st.text_input("Enter UKI Handler Number:", key="search_box").strip()
    if h_input:
        u_data = df[df['UKI_Number'] == h_input]
        if not u_data.empty:
            st.subheader(f"Handler: {u_data.iloc[0]['Handler_Name']}")
            opts = ["Not Checked In", "Checked In", "Scratch", "Conflict", "NFC"]
            
            for dog in u_data['Name'].unique():
                dog_runs = u_data[u_data['Name'] == dog]
                with st.container(border=True):
                    st.write(f"### 🐶 {dog}")
                    
                    # RESTORED: Check-in All Logic
                    if st.button(f"Check in all runs for {dog}", key=f"all_{dog}"):
                        for _, r in dog_runs.iterrows():
                            conn_supabase.table("trialdata").update({"status": "Checked In"}).eq("Run_Order", r['Run_Order']).execute()
                        fetch_fresh_data()
                        st.rerun()
                    
                    for idx, row in dog_runs.iterrows():
                        pk = row['Run_Order']
                        st.selectbox(
                            row['Combined Class Name'], 
                            opts, 
                            index=opts.index(row['status']) if row['status'] in opts else 0, 
                            key=f"s_{pk}", 
                            on_change=lambda p=pk: update_status_instant(p, st.session_state[f"s_{p}"])
                        )

# --- TAB 2: DASHBOARD ---
with tab2:
    if st.button("🔄 Refresh Data"): 
        fetch_fresh_data()
        st.rerun()
    if not df.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Entries", len(df))
        c2.metric("Checked In", len(df[df['status'] == 'Checked In']))
        c3.metric("In Ring", len(df[df['status'] == 'In Ring']))
        c4.metric("Completed", len(df[df['status'] == 'Run Completed']))

# --- TAB 3: RUNNING ORDER ---
with tab3:
    if not df.empty:
        sel_c = st.selectbox("Select Class:", sorted_classes, key="ro_sel")
        r_df = df[df['Combined Class Name'] == sel_c].sort_values(['Height', 'Run_Order'])
        for h in sorted(r_df['Height'].unique()):
            st.markdown(f'<div class="height-header">{h}" Height</div>', unsafe_allow_html=True)
            st.dataframe(r_df[r_df['Height'] == h][['Handler_Name', 'Name', 'Breed', 'status']], use_container_width=True, hide_index=True)

# --- TAB 4: COURSE MATH ---
with tab4:
    if st.text_input("Math PIN:", type="password", key="m_p_v") == "7890":
        m_cls = st.selectbox("Class:", sorted_classes, key="m_cls")
        yrds = st.number_input("Measured Yards:", step=1.0)
        is_ss = st.checkbox("Is Speedstakes?", value="speedstakes" in m_cls.lower())
        
        if yrds > 0:
            lvl_idx = 1 if any(w in m_cls.lower() for w in ["senior", "champ"]) else 0
            lvl = st.selectbox("Level Group:", ["Beginner/Novice", "Senior/Champion"], index=lvl_idx)
            
            rate = (2.9, 3.15) if lvl == "Senior/Champion" else (2.5, 2.9)
            if is_ss: rate = (3.25, 3.5) if lvl == "Senior/Champion" else (2.75, 3.25)
            
            sct_b = round(yrds/rate[1])
            sct_s = round(sct_b * (1.1 if lvl == "Senior/Champion" else 1.2))
            
            st.info(f"Calculated SCTs -> Big: {sct_b}s | Small: {sct_s}s")
            if st.button("Save SCT to Database"):
                conn_supabase.table("course_specs").upsert({"class_name": m_cls, "chosen_sct_big": sct_b, "chosen_sct_small": sct_s}).execute()
                st.success("Saved!")

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
    if st.text_input("Admin PIN:", type="password", key="a_p_v") == "7890":
        if st.button("Reset All Run Statuses"):
            conn_supabase.table("trialdata").update({"status": "Not Checked In"}).neq("status", "Scratch").execute()
            fetch_fresh_data()
            st.rerun()

# --- TAB 7: SCORING ---
with tab7:
    st.header("⏱️ Scoring Booth")
    s_cls = st.selectbox("Class:", sorted_classes, key="s_tab_cls")
    is_nur = any(x in s_cls.lower() for x in ["nursery", "gamblers"])
    
    s_df = df[df['Combined Class Name'] == s_cls].sort_values(['Height', 'Run_Order'])
    in_ring = s_df[s_df['status'] == 'In Ring']
    dog_list = s_df['Name'].tolist()
    d_idx = dog_list.index(in_ring.iloc[0]['Name']) if not in_ring.empty else 0
    
    a_dog_name = st.selectbox("Active Dog:", dog_list, index=d_idx)
    a_dog = s_df[s_df['Name'] == a_dog_name].iloc[0]
    
    # State Setup
    for k in ['t_ref', 't_fault', 'is_e', 'time_str']:
        if k not in st.session_state: st.session_state[k] = False if k == 'is_e' else (0 if k != 'time_str' else "")

    # Restored Image Buttons
    c1, c2, c3 = st.columns(3)
    with c1:
        st.image("https://trialsecretary.notion.site/image/attachment%3A53feb389-ccd9-4f6b-a663-0fd46dc5d9a6%3Aimage.png?table=block&id=34ce6efe-88b7-806b-a467-d2033081650c&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button(f"Refusal ({st.session_state.t_ref})", key="r_btn"): st.session_state.t_ref += 1; st.rerun()
    with c2:
        st.image("https://trialsecretary.notion.site/image/attachment%3Aeaf1e083-97fb-4aad-8fca-3eba410da7be%3Aimage.png?table=block&id=34ce6efe-88b7-802b-9ef8-c06561fa78e4&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button(f"Fault ({st.session_state.t_fault})", key="f_btn"): st.session_state.t_fault += 1; st.rerun()
    with c3:
        st.image("https://trialsecretary.notion.site/image/attachment%3Ad1c1f212-0248-4411-ad96-74f00719b948%3Aimage.png?table=block&id=34ce6efe-88b7-8038-a837-e945ab877561&spaceId=a58286e5-194b-4546-8ee9-b7ebb91914d1&width=1410", width=80)
        if st.button("ELIM", type="primary" if st.session_state.is_e else "secondary"): st.session_state.is_e = not st.session_state.is_e; st.rerun()

    st.divider()
    raw_t = st.session_state.time_str.zfill(3)
    disp_t = f"{raw_t[:-2]}.{raw_t[-2:]}"
    st.markdown(f"<div class='time-display'>{disp_t}s</div>", unsafe_allow_html=True)

    # Snappy Numpad Grid
    rows = [[1, 2, 3], [4, 5, 6], [7, 8, 9], ["CLR", 0, "⌫"]]
    for r in rows:
        cols = st.columns(3)
        for i, v in enumerate(r):
            with cols[i]:
                if v == "CLR":
                    if st.button("CLR", key="clr_btn"): st.session_state.time_str = ""; st.rerun()
                elif v == "⌫":
                    if st.button("⌫", key="bk_btn"): st.session_state.time_str = st.session_state.time_str[:-1]; st.rerun()
                else:
                    if st.button(str(v), key=f"n_{v}"): st.session_state.time_str += str(v); st.rerun()

    if st.button("🚀 SUBMIT FINAL SCORE", type="primary", use_container_width=True):
        specs = conn_supabase.table("course_specs").select("*").eq("class_name", s_cls).execute()
        sct_val = specs.data[0]['chosen_sct_big' if int(a_dog['Height']) >= 20 else 'chosen_sct_small'] if specs.data else 0
        cur_t = float(disp_t)
        total = (0 if is_nur else st.session_state.t_ref * 5) + (st.session_state.t_fault * 5) + max(0, cur_t - sct_val)
        
        conn_supabase.table("trialdata").update({
            "status": "Eliminated" if st.session_state.is_e else "Run Completed",
            "refusals": st.session_state.t_ref, "faults": st.session_state.t_fault, 
            "time": cur_t, "total_score": total if not st.session_state.is_e else 999
        }).eq("Run_Order", a_dog['Run_Order']).execute()
        
        st.session_state.t_ref = 0; st.session_state.t_fault = 0; st.session_state.is_e = False; st.session_state.time_str = ""
        st.success("Score Saved!"); time.sleep(0.5); fetch_fresh_data(); st.rerun()