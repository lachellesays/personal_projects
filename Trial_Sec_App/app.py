import streamlit as st
import sqlite3
import pandas as pd

# 1. DATABASE SETUP (The SQL part you know!)
conn = sqlite3.connect('my_app_data.db')
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS notes (content TEXT)')
conn.commit()

# 2. THE WEBSITE HEADER
st.title("My First SQL Web App")
st.subheader("Store your thoughts in a database below:")

# 3. THE INTERFACE (The Python part)
user_input = st.text_input("Enter a note:")
if st.button("Save to Database"):
    c.execute('INSERT INTO notes (content) VALUES (?)', (user_input,))
    conn.commit()
    st.success("Saved!")

# 4. SHOW THE DATA
st.divider()
st.write("Current Database Entries:")
df = pd.read_sql_query("SELECT * FROM notes", conn)
st.table(df)