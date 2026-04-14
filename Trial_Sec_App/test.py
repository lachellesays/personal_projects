import streamlit as st
import sqlite3

st.title("Add Name to Database")

name = st.text_input("Enter your name:")

#python -m streamlit run .\test.py