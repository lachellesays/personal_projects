import streamlit as st
import sqlite3

def init_db():
    conn = sqlite3.connect('names.db')
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS names \
              (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    conn.commit()
    conn.close()
    
def insert_name(name):
    conn = sqlite3.connect('names.db')
    c = conn.cursor()
    c.execute("INSERT INTO names (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()



st.title("Add Name to Database")

init_db()

name = st.text_input("Enter your name:")
if st.button("Save"):
    if name.strip():
        insert_name(name.strip())
        st.success("Name saved to database!")

def get_names():
    conn = sqlite3.connect('names.db')
    c = conn.cursor()
    c.execute("SELECT id,name FROM names")
    names = c.fetchall()
    conn.close()
    return names

st.title("Names in Database")
names = get_names()

st.subheader("Current Names:")
for row in names:
    st.write(f"{row[0]}: {row[1]}")
