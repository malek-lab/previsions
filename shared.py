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

@st.cache_data(ttl=60)
def get_programmes():
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            # Utiliser T_CACHE_PROGRAMMES (cache local) si disponible
            df = pd.read_sql(text("""
                SELECT FPC_ID, Programme, CLI_CODE, CLI_NOM,
                       Horizon_programme,
                       QTE_PREVISION_TOTALE
                FROM [master].[dbo].[T_CACHE_PROGRAMMES]
                ORDER BY Programme
            """), conn)
            if not df.empty:
                return df
    except:
        pass
    # Fallback sur Programme_VW si cache vide
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

def cache_disponible():
    try:
        engine = get_engine()
        if engine is None: return False
        with engine.connect() as conn:
            r = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='T_CACHE_META'"
            )).scalar()
            if not r: return False
            r2 = conn.execute(text(
                "SELECT DATE_CACHE FROM [dbo].[T_CACHE_META] WHERE DATE_CACHE IS NOT NULL"
            )).fetchone()
        return r2 is not None
    except:
        return False

def get_date_cache():
    try:
        engine = get_engine()
        if engine is None: return None
        with engine.connect() as conn:
            r = conn.execute(text("SELECT DATE_CACHE FROM [dbo].[T_CACHE_META]")).fetchone()
        return r[0] if r else None
    except:
        return None

def recharger_cache():
    try:
        engine = get_engine()
        if engine is None: return False
        with engine.connect() as conn:
            conn.execute(text("EXEC [dbo].[P_CACHE_NUIT]"))
            conn.commit()
        # Vider le cache Streamlit de la liste des programmes
        get_programmes.clear()
        return True
    except Exception as e:
        print(f"Erreur rechargement cache : {e}")
        return False

def execute_procedure_single(fpc_id, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    """Appel procédure pour UN SEUL programme (FPC_ID unique)."""
    engine = get_engine()
    if engine is None:
        return None
    try:
        date_vent_eff = date_ventilation if activer_ventilation else date_prevision
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
    Appel procédure vectorisée si cache dispo, sinon séquentiel.
    """
    if not fpc_ids:
        return None
    engine = get_engine()
    if engine is None:
        return None
    try:
        date_vent_eff = date_ventilation if activer_ventilation else date_prevision
        fpc_list = ','.join(str(f) for f in fpc_ids)

        if cache_disponible():
            sql = f"""
            EXEC [dbo].[P_R_PIVOT_PREVISION_CACHE_VECTORIZED]
                @SERVEUR_LIE            = 'SRV-MSSQLDB',
                @FPC_LIST               = '{fpc_list}',
                @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
                @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
                @SOURCE_QTE_LOT         = {1 if source_lot else 0}
            """
            with engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            print(f"Vectorisée OK — {len(fpc_ids)} programmes → {len(df)} lignes")
            return df if not df.empty else None
        else:
            frames = []
            for fpc_id in fpc_ids:
                try:
                    sql = f"""
                    EXEC [dbo].[P_R_PIVOT_PREVISION_DEV_LOCAL]
                        @SERVEUR_LIE            = 'SRV-MSSQLDB',
                        @FPC_ID                 = '{fpc_id}',
                        @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
                        @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
                        @SOURCE_QTE_LOT         = {1 if source_lot else 0}
                    """
                    with engine.connect() as conn:
                        df = pd.read_sql(text(sql), conn)
                    if df is not None and not df.empty:
                        frames.append(df)
                    print(f"FPC_ID={fpc_id} → {len(df) if df is not None else 0} lignes")
                except Exception as e:
                    print(f"ERREUR FPC_ID={fpc_id} : {e}")
            if not frames:
                return None
            df_all = pd.concat(frames, ignore_index=True, sort=False)
            wk = [c for c in df_all.columns
                  if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
                  and c[1:3].isdigit() and c[4:6].isdigit()]
            for c in wk:
                df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)
            return df_all
    except Exception as e:
        print(f"ERREUR execute_procedure : {e}")
        try:
            st.error(f"Erreur procédure : {e}")
        except:
            pass
        return None

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