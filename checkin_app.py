import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd

# Set up the page style
st.set_page_config(page_title="Agility Check-in", page_icon="🐾")

# 1. Establish the Connection
# This uses the URL you provided
sheet_url = "https://docs.google.com/spreadsheets/d/1cakveWwfjL7-RNGWvw9RogAc4odCtSNvjrjX6fVSKRk/edit?gid=0#gid=0"
# The string "gsheets" must match the word in your secrets.toml
conn = st.connection("gsheets", type=GSheetsConnection)
# This checks if the specific 'connections.gsheets' section exists in your secrets
if "connections" in st.secrets and "gsheets" in st.secrets.connections:
    st.success("✅ Streamlit found your secrets.toml file!")
    # Show the email just to be 100% sure it's the right one
    st.write(f"Logged in as: {st.secrets.connections.gsheets.client_email}")
else:
    st.error("❌ Streamlit CANNOT see your secrets.toml or the [connections.gsheets] section.")

# 2. Fetch Data (ttl=0 ensures we don't use old cached data)
df = conn.read(spreadsheet=sheet_url, ttl=0)

st.title("🐾 Agility Trial Check-in")
st.markdown("Enter your handler number to see your classes and update your status.")

# 3. User Input
handler_input = st.text_input("Handler Number:", placeholder="Enter your number here...")

if handler_input:
    # Filter the sheet for this handler (converting to string to avoid match errors)
    user_rows = df[df['Handler_Number'].astype(str) == str(handler_input)]

    if user_rows.empty:
        st.warning(f"No entries found for Handler #{handler_input}")
    else:
        handler_name = user_rows.iloc[0]['Handler']
        st.subheader(f"Handler: {handler_name}")
        
        # We use a form so the user can update multiple classes at once
        with st.form("checkin_form"):
            st.write("Current Class Schedule:")
            
            # This dictionary will store the new values until the user hits 'Submit'
            new_statuses = {}

            for index, row in user_rows.iterrows():
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown(f"**{row['Class']}**")
                    st.caption(f"Dog: {row['Dog']}")
                
                with col2:
                    # Determine the current index for the radio button
                    status_options = ["Not Checked In", "Checked In", "Scratch", "Conflict"]
                    current_val = row['Status'] if row['Status'] in status_options else "Not Checked In"
                    
                    choice = st.selectbox(
                        "Status",
                        options=status_options,
                        index=status_options.index(current_val),
                        key=f"select_{index}"
                    )
                    new_statuses[index] = choice
                
                st.divider()

            submit_button = st.form_submit_button("Update All Classes")

        # 4. Update the Google Sheet
        if submit_button:
            with st.spinner("Writing to Google Sheets..."):
                # Update the main dataframe with the new selections
                for idx, status in new_statuses.items():
                    df.at[idx, 'Status'] = status
                
                # Push the updated dataframe back to Google
                conn.update(spreadsheet=sheet_url, data=df)
                
                st.success("Successfully updated! You're all set.")
                st.balloons()