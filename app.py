import streamlit as st

st.set_page_config(page_title="SERTA — Outils", page_icon="📊", layout="wide")

pg = st.navigation([
    st.Page("pages/01_pivot.py",            title="Pivot Prévision",   icon="📊"),
    st.Page("pages/02_supply_chain.py",     title="Supply Chain",      icon="🔗"),
    st.Page("pages/03_agregation.py",       title="Agrégation LPC",    icon="📦"),
    st.Page("pages/05_nouveaux_projets.py", title="Nouveaux Projets",  icon="🚀"),
    st.Page("pages/04_consolide.py",        title="Consolidé",         icon="🔀"),
])
pg.run()