import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta, date
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*keyword arguments.*deprecated.*')

@st.cache_resource
def get_engine():
    try:
        return create_engine(
            "mssql+pyodbc://W25-DWDI/master"
            "?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes",
            pool_pre_ping=True,
            pool_recycle=1800
        )
    except Exception as e:
        st.error(f"Erreur connexion : {e}")
        return None

@st.cache_data(ttl=600)
def get_programmes():
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT FPC_ID, Programme, CLI_CODE, CLI_NOM,
                       MAX(Horizon_programme) AS Horizon_programme,
                       SUM(QTE_PREVISION_TOTALE) AS QTE_PREVISION_TOTALE
                FROM [master].[dbo].[Programme_VW]
                GROUP BY FPC_ID, Programme, CLI_CODE, CLI_NOM
                ORDER BY Programme
            """), conn)
    except Exception as e:
        st.error(f"Erreur chargement programmes : {e}")
        return pd.DataFrame()

def execute_procedure_single(fpc_id, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    """Appel procédure pour UN SEUL programme (FPC_ID unique)."""
    engine = get_engine()
    if engine is None:
        return None
    try:
        date_vent_eff = date_ventilation if activer_ventilation else date(9999, 12, 31)
        sql = f"""
        EXEC P_R_PIVOT_PREVISION_DEV_LOCAL
            @SERVEUR_LIE            = 'SRV-MSSQLDB',
            @FPC_ID                 = '{fpc_id}',
            @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
            @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
            @SOURCE_QTE_LOT         = {1 if source_lot else 0}
        """
        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:
        st.error(f"Erreur procédure (FPC_ID={fpc_id}) : {e}")
        with st.expander("Détails"):
            st.exception(e)
        return None

def execute_procedure(fpc_ids, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    """
    Appel procédure pour un ou plusieurs programmes.
    UN appel par FPC_ID, résultats concaténés.
    Chaque appel retourne ses propres colonnes semaines — on aligne avec fillna(0).
    """
    if not fpc_ids:
        return None
    frames = []
    for fpc_id in fpc_ids:
        df = execute_procedure_single(fpc_id, date_prevision, date_ventilation, source_lot, activer_ventilation)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0]
    df_all = pd.concat(frames, ignore_index=True, sort=False)
    # fillna sélectif : seulement colonnes semaines numériques, pas les colonnes texte
    wk = [c for c in df_all.columns
          if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
          and c[1:3].isdigit() and c[4:6].isdigit()]
    for c in wk:
        df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)
    return df_all

def logo_sidebar():
    try:
        st.image("Serta_logo.jpg", width="stretch")
    except:
        st.title("SERTA")

def wk_cols_from_df(df):
    return sorted([c for c in df.columns
                   if isinstance(c, str) and len(c) == 6
                   and c[0] == 'S' and c[3] == '-'
                   and c[1:3].isdigit() and c[4:6].isdigit()])

def to_excel_bytes(df):
    from io import BytesIO
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='Export')
    return buf.getvalue()