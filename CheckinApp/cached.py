"""
cached.py

Shared, short-lived cache for database reads.

On trial day hundreds of phones refresh the running order every few seconds. Instead of each
one querying the database, everyone viewing the same class shares one result for up to
CACHE_SECONDS. Any write (check-in, gate button, admin edit) bumps db.data_version(), which
is part of the cache key, so changes appear immediately rather than after the cache expires.
"""

import streamlit as st

import db

CACHE_SECONDS = 3


@st.cache_resource
def engine():
    return db.get_engine()


@st.cache_data(ttl=CACHE_SECONDS, max_entries=1000, show_spinner=False)
def _read(fn_name: str, version: int, *args):
    return getattr(db, fn_name)(engine(), *args)


def read(fn_name: str, *args):
    """Call a read function from db.py, e.g. read('runs_for_day', day, 'Gamblers')."""
    return _read(fn_name, db.data_version(), *args)
