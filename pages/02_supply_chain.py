import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
from sqlalchemy import text
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, to_excel_bytes

SC_COLS = [
    'SERTA_SO_CLIENT_CODE',
    'SERTA_SO_CLIENT_NAME',
    'SERTA_SO_CLIENT_GROUP_CODE',
    'SERTA_SO_CLIENT_GROUP_NAME',
    'SERTA_MAIN_CUSTOMER',
    'SALES_ADMINISTRATION_PERSON',
    'ITEM_REF',
    'ITEM_CLIENT_REF',
    'ITEM_GROUP_CODE',
    'ITEM_MAIN_PRODUCTION_UNIT',
    'ITEM_ORDER_MIN_QTY',
    'ITEM_PACKAGED_UNIT_QTY',
    'SERTA_SO_NUM',
    'CLIENT_ACK_DATE',
    'SERTA_SO_STILL_TO_BE_DELIVERED_QTY',
    'SERTA_SO_STILL_TO_BE_DELIVERED_TURNOVER',
]

@st.cache_data(ttl=300, show_spinner=False)
def load_supply_chain():
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        cols_str = ', '.join(SC_COLS)
        with engine.connect() as conn:
            df = pd.read_sql(
                text(f"SELECT {cols_str} FROM [master].[dbo].[V_SUPPLY_CHAIN]"),
                conn
            )
        if df.empty:
            return df

        # CLIENT_ACK_DATE → datetime puis semaine ISO S26-09
        df['CLIENT_ACK_DATE'] = pd.to_datetime(df['CLIENT_ACK_DATE'], errors='coerce')
        df['SEMAINE'] = df['CLIENT_ACK_DATE'].apply(
            lambda d: f"S{str(d.isocalendar()[0])[2:]}-{d.isocalendar()[1]:02d}"
            if pd.notna(d) else None
        )

        for col in ['SERTA_SO_STILL_TO_BE_DELIVERED_QTY',
                    'SERTA_SO_STILL_TO_BE_DELIVERED_TURNOVER',
                    'ITEM_ORDER_MIN_QTY', 'ITEM_PACKAGED_UNIT_QTY']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df
    except Exception as e:
        st.error(f"Erreur chargement supply chain : {e}")
        with st.expander("Détails"):
            st.exception(e)
        return pd.DataFrame()

DATES_FICTIVES = ['2030-12-31', '2099-12-31']

def agreger_par_semaine(df):
    if df.empty or 'SEMAINE' not in df.columns:
        return pd.DataFrame()
    # Exclure les lignes avec dates fictives BaaN (pas d'ARC confirmé)
    if 'CLIENT_ACK_DATE' in df.columns:
        df = df[~df['CLIENT_ACK_DATE'].dt.strftime('%Y-%m-%d').isin(DATES_FICTIVES)]
    if df.empty:
        return pd.DataFrame()
    grp_cols = [c for c in [
        'ITEM_CLIENT_REF', 'ITEM_REF', 'ITEM_GROUP_CODE',
        'ITEM_MAIN_PRODUCTION_UNIT', 'ITEM_ORDER_MIN_QTY', 'ITEM_PACKAGED_UNIT_QTY',
        'SERTA_MAIN_CUSTOMER', 'SERTA_SO_CLIENT_GROUP_CODE', 'SERTA_SO_CLIENT_GROUP_NAME',
        'SERTA_SO_CLIENT_CODE', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON',
        'SEMAINE'
    ] if c in df.columns]
    return (df.groupby(grp_cols, dropna=False)
              .agg(
                  QTE_FERME=('SERTA_SO_STILL_TO_BE_DELIVERED_QTY',     'sum'),
                  CA_FERME =('SERTA_SO_STILL_TO_BE_DELIVERED_TURNOVER', 'sum'),
                  NB_OV    =('SERTA_SO_NUM',                            'nunique'),
              )
              .reset_index()
              .sort_values(['ITEM_CLIENT_REF', 'SEMAINE']))

# ── Page ──────────────────────────────────────────────────────────────────────
st.title("🔗 Supply Chain — Carnet de commande ferme")

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Filtres")
    st.markdown("---")
    st.info("📌 Données issues de **V_SUPPLY_CHAIN** (DW25)\nOPENQUERY → SRV-MSSQLDB")
    btn_charger = st.button("🔄 Charger / Actualiser", type="primary", width="stretch")
    if btn_charger:
        load_supply_chain.clear()
        st.session_state.pop('df_sc', None)

if btn_charger or 'df_sc' not in st.session_state:
    with st.spinner("Chargement du carnet de commande..."):
        df_sc = load_supply_chain()
    if df_sc is not None and not df_sc.empty:
        st.session_state['df_sc'] = df_sc
    else:
        st.warning("⚠️ Aucune donnée — vérifiez que V_SUPPLY_CHAIN existe sur DW25")
        st.stop()

if 'df_sc' not in st.session_state:
    st.info("👈 Cliquez sur **Charger / Actualiser**")
    st.stop()

df_sc = st.session_state['df_sc']

c1, c2, c3, c4 = st.columns(4)
c1.metric("Refs client",  df_sc['ITEM_CLIENT_REF'].nunique()  if 'ITEM_CLIENT_REF' in df_sc.columns else 0)
c2.metric("Refs SERTA",   df_sc['ITEM_REF'].nunique()         if 'ITEM_REF'        in df_sc.columns else 0)
c3.metric("OV ouvertes",  df_sc['SERTA_SO_NUM'].nunique()     if 'SERTA_SO_NUM'    in df_sc.columns else 0)
total_qty = df_sc['SERTA_SO_STILL_TO_BE_DELIVERED_QTY'].sum() if 'SERTA_SO_STILL_TO_BE_DELIVERED_QTY' in df_sc.columns else 0
c4.metric("QTY restante", f"{total_qty:,.0f}")


st.markdown("---")

with st.expander("🔍 Filtres", expanded=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        f_groupe = st.multiselect("Groupe client",
            options=sorted(df_sc['SERTA_SO_CLIENT_GROUP_NAME'].dropna().unique())
            if 'SERTA_SO_CLIENT_GROUP_NAME' in df_sc.columns else [])
    with col2:
        f_up = st.multiselect("UP production",
            options=sorted(df_sc['ITEM_MAIN_PRODUCTION_UNIT'].dropna().unique())
            if 'ITEM_MAIN_PRODUCTION_UNIT' in df_sc.columns else [])
    with col3:
        f_ref = st.multiselect("Ref SERTA",
            options=sorted(df_sc['ITEM_REF'].dropna().unique())
            if 'ITEM_REF' in df_sc.columns else [])

df_filt = df_sc.copy()
if f_groupe: df_filt = df_filt[df_filt['SERTA_SO_CLIENT_GROUP_NAME'].isin(f_groupe)]
if f_up:     df_filt = df_filt[df_filt['ITEM_MAIN_PRODUCTION_UNIT'].isin(f_up)]
if f_ref:    df_filt = df_filt[df_filt['ITEM_REF'].isin(f_ref)]

tab1, tab2, tab3 = st.tabs(["📋 Détail lignes", "📦 Agrégation par semaine", "💾 Export"])

with tab1:
    st.caption(f"{len(df_filt):,} lignes")
    st.dataframe(df_filt, width='stretch', height=600)

with tab2:
    df_agg = agreger_par_semaine(df_filt)
    if not df_agg.empty:
        st.caption(f"{len(df_agg):,} lignes agrégées")
        if 'SEMAINE' in df_agg.columns:
            by_sem = (df_agg[df_agg['SEMAINE'].notna()]
                      .groupby('SEMAINE')['QTE_FERME'].sum()
                      .reset_index().sort_values('SEMAINE'))
            fig = px.bar(by_sem, x='SEMAINE', y='QTE_FERME',
                         title="QTY ferme par semaine (CLIENT_ACK_DATE)",
                         labels={'SEMAINE': 'Semaine', 'QTE_FERME': 'QTY'},
                         color_discrete_sequence=['#C00000'])
            st.plotly_chart(fig, width="stretch")
        st.dataframe(df_agg, width='stretch', height=500)
        st.session_state['df_sc_agg'] = df_agg
    else:
        st.info("Aucune donnée à agréger")

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        csv = df_filt.to_csv(index=False, encoding='utf-8-sig', sep=';')
        st.download_button("📥 Détail CSV", data=csv,
            file_name=f"supply_chain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch")
    with c2:
        df_agg_exp = agreger_par_semaine(df_filt)
        if not df_agg_exp.empty:
            st.download_button("📥 Agrégation Excel", data=to_excel_bytes(df_agg_exp),
                file_name=f"supply_chain_agg_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch")