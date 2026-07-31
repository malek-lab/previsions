
# ============================================================================
import sys

if sys.platform.startswith("win"):
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _base_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

    def _silent_call_connection_lost(self, exc):
        try:
            _base_call_connection_lost(self, exc)
        except ConnectionResetError:
            # Connexion déjà fermée par le client -- rien à faire, on ignore.
            pass

    _ProactorBasePipeTransport._call_connection_lost = _silent_call_connection_lost

# ============================================================================
# Le reste de app.py, inchangé
# ============================================================================
import streamlit as st

st.set_page_config(page_title="SERTA — Outils", page_icon="📊", layout="wide")

pg = st.navigation([
    st.Page("pages/01_pivot.py",            title="Pivot Prévision",     icon="📊"),
    st.Page("pages/02_import_manuel.py",    title="Import Manuel",       icon="📂"),
    st.Page("pages/02_supply_chain.py",     title="Supply Chain",        icon="🔗"),
    st.Page("pages/03_agregation.py",       title="Agrégation LPC",      icon="📦"),
    st.Page("pages/05_nouveaux_projets.py", title="Nouveaux Projets",    icon="🚀"),
    st.Page("pages/04_consolide.py",        title="Consolidé",           icon="🔀"),
    st.Page("pages/06_pic.py",          title="PIC Mensuel",         icon="📅"),
    st.Page("pages/07_pic_dashboard.py",    title="PIC Dashboard",       icon="📈"),

])

pg.run()