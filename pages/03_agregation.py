import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.title("📦 Agrégation — LPC + Carnet de commande")

if get_engine() is None:
    st.stop()

DATES_FICTIVES = ['2030-12-31', '2099-12-31']

def semaine_label(d):
    try:
        iso = d.isocalendar()
        return f"S{str(iso[0])[2:]}-{iso[1]:02d}"
    except:
        return None

def wk_label_to_date(label):
    try:
        yy, ww = int('20'+label[1:3]), int(label[4:6])
        return dt.date.fromisocalendar(yy, ww, 1)
    except:
        return None

def extract_code_client(programme_nom):
    try:
        return str(programme_nom).split('_')[0]
    except:
        return None

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "Cette page repart des données **Prévisions (page 01)** et y ajoute "
        "les lignes du carnet de commande dont le couple "
        "`CODE_CLIENT + REF_ARTICLE_SERTA` n'est pas couvert par un programme LPC.\n\n"
        "👈 Lancez d'abord la page **📊 Pivot Prévision** pour alimenter cette vue."
    )

    st.markdown("---")
    st.subheader("⚙️ Paramètres carnet")
    date_filtre_du = st.date_input("📅 Semaines à partir du",
        value=st.session_state.get('date_prevision', datetime.now().date()),
        format="DD/MM/YYYY",
        help="Doit correspondre à la date de prévision de la page 01")
    date_filtre_au = st.date_input("📅 Au",
        value=st.session_state.get('date_filtre_au', datetime.now().date() + timedelta(weeks=52)),
        format="DD/MM/YYYY")

    st.markdown("---")
    btn_ajouter_carnet = st.button("🔄 Charger / Actualiser carnet", type="primary", width="stretch")

# ── Vérifier que df_pivot existe ─────────────────────────────────────────────
if 'df_pivot' not in st.session_state:
    st.warning("⚠️ Aucune donnée LPC — lancez d'abord la page **📊 Pivot Prévision** et cliquez sur **LANCER**.")
    st.stop()

df_lpc = st.session_state['df_pivot'].copy()

# ── Préparer LPC : ajouter colonnes ORIGINE + CODE_CLIENT ────────────────────
wk_lpc = wk_cols_from_df(df_lpc)

# Extraire CODE_CLIENT depuis colonne PROGRAMME (déjà taguée en page 01)
if 'PROGRAMME' in df_lpc.columns:
    df_lpc['CODE_CLIENT'] = df_lpc['PROGRAMME'].apply(extract_code_client)
else:
    df_lpc['CODE_CLIENT'] = None

df_lpc['ORIGINE'] = 'LPC'

# Colonnes méta LPC disponibles
META_LPC_COLS = ['CODE_CLIENT', 'ORIGINE', 'PROGRAMME', 'REF_ARTICLE_SERTA',
                 'REF_ARTICLE_CLIENT', 'UP_PRINCIPALE', 'CODE_SELECTION',
                 'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE']

# Construire les couples LPC pour filtrer le carnet
couples_lpc = set(
    df_lpc['CODE_CLIENT'].astype(str) + '|' + df_lpc['REF_ARTICLE_SERTA'].astype(str)
)

# ── Charger carnet ────────────────────────────────────────────────────────────
def charger_carnet(couples_lpc, date_du, date_au):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT
                    SERTA_SO_CLIENT_CODE            AS CODE_CLIENT,
                    ITEM_REF                        AS REF_ARTICLE_SERTA,
                    ITEM_CLIENT_REF                 AS REF_ARTICLE_CLIENT,
                    ITEM_MAIN_PRODUCTION_UNIT       AS UP_PRINCIPALE,
                    ITEM_ORDER_MIN_QTY              AS QTE_MOQ,
                    ITEM_PACKAGED_UNIT_QTY          AS QTE_UC,
                    SERTA_SO_CLIENT_GROUP_NAME,
                    SERTA_SO_CLIENT_NAME,
                    SALES_ADMINISTRATION_PERSON,
                    CLIENT_ACK_DATE,
                    SERTA_SO_STILL_TO_BE_DELIVERED_QTY AS QTE
                FROM [master].[dbo].[V_SUPPLY_CHAIN]
            """), conn)
    except Exception as e:
        st.error(f"Erreur carnet : {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    # Nettoyer dates
    df['CLIENT_ACK_DATE'] = pd.to_datetime(df['CLIENT_ACK_DATE'], errors='coerce')
    df = df[~df['CLIENT_ACK_DATE'].dt.strftime('%Y-%m-%d').isin(DATES_FICTIVES)]
    df['QTE'] = pd.to_numeric(df['QTE'], errors='coerce').fillna(0)

    # Calculer semaine ISO
    df['SEMAINE'] = df['CLIENT_ACK_DATE'].apply(lambda d: semaine_label(d) if pd.notna(d) else None)
    df = df[df['SEMAINE'].notna()]

    # Filtrer par plage de dates
    df = df[df['SEMAINE'].apply(lambda s: wk_label_to_date(s) is not None
                                 and date_du <= wk_label_to_date(s) <= date_au)]
    if df.empty:
        return pd.DataFrame()

    # Garder uniquement couples ABSENTS des LPC
    df['CODE_CLIENT'] = df['CODE_CLIENT'].astype(str).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']
    df = df[~df['_COUPLE'].isin(couples_lpc)].drop(columns=['_COUPLE'])

    if df.empty:
        return pd.DataFrame()

    # Ajouter colonnes méta fixes
    df['ORIGINE']       = 'CARNET'
    df['PROGRAMME']     = ''
    df['CODE_SELECTION'] = ''
    df['QTE_TOTALE']    = None

    # Pivoter par semaine
    meta = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION', 'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']
    meta = [c for c in meta if c in df.columns]

    for _m in meta:
        df[_m] = df[_m].fillna('').astype(str)
    df['QTE'] = pd.to_numeric(df['QTE'], errors='coerce').fillna(0)

    agg = df.groupby(meta + ['SEMAINE'], dropna=False)['QTE'].sum().reset_index()
    pivot = agg.pivot_table(index=meta, columns='SEMAINE', values='QTE',
                            aggfunc='sum', fill_value=0).reset_index()
    pivot.columns.name = None
    return pivot


# ── Fusionner LPC + CARNET ────────────────────────────────────────────────────
if btn_ajouter_carnet:
    with st.spinner("⏳ Chargement carnet de commande..."):
        df_carnet = charger_carnet(couples_lpc, date_filtre_du, date_filtre_au)

    if df_carnet.empty:
        st.info("ℹ️ Aucune ligne carnet à ajouter — tous les couples (client+ref) sont couverts par les LPC sélectionnés.")
        df_all = df_lpc.copy()
        df_all['SERTA_SO_CLIENT_GROUP_NAME'] = ''
        df_all['SERTA_SO_CLIENT_NAME']       = ''
        df_all['SALES_ADMINISTRATION_PERSON'] = ''
    else:
        st.success(f"✅ {len(df_carnet)} lignes carnet ajoutées")
        df_all = pd.concat([df_lpc, df_carnet], ignore_index=True, sort=False)

    # Forcer types texte pour éviter erreur Arrow
    for col in ['PROGRAMME', 'HORIZON_PROGRAMME', 'CODE_SELECTION', 'ORIGINE', 'CODE_CLIENT',
                'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']:
        if col in df_all.columns:
            df_all[col] = df_all[col].fillna('').astype(str)

    # Remplir NaN dans colonnes semaines
    wk_all = wk_cols_from_df(df_all)
    for c in wk_all:
        df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)

    # Filtrer les colonnes semaines hors plage [date_du, date_au]
    wk_in_range = [c for c in wk_all
                   if wk_label_to_date(c) is not None
                   and date_filtre_du <= wk_label_to_date(c) <= date_filtre_au]
    meta_cols = [c for c in df_all.columns if c not in wk_all]
    df_all = df_all[meta_cols + sorted(wk_in_range)]

    st.session_state['df_03'] = df_all

# ── Si pas encore chargé avec carnet, afficher juste le LPC ──────────────────
if 'df_03' not in st.session_state:
    # Premier affichage : montrer LPC seul avec message
    df_lpc_disp = df_lpc.copy()
    for col in ['PROGRAMME', 'CODE_CLIENT', 'ORIGINE']:
        if col in df_lpc_disp.columns:
            df_lpc_disp[col] = df_lpc_disp[col].fillna('').astype(str)
    st.info("ℹ️ Affichage LPC uniquement. Cliquez sur **🔄 Charger / Actualiser carnet** pour ajouter le carnet de commande.")
    df_aff = df_lpc_disp
else:
    df_aff = st.session_state['df_03'].copy()

# ── Définir colonnes méta et semaines ────────────────────────────────────────
META_ALL = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'HORIZON_PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
            'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']
META_ALL = [c for c in META_ALL if c in df_aff.columns]
wk_cols  = sorted([c for c in df_aff.columns if c not in META_ALL
                   and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])

import datetime as _dt
_fdu = st.session_state.get('date_prevision', None)
_fau = st.session_state.get('date_filtre_au', None)
def _wk_ok(col):
    try:
        yy, ww = int('20'+col[1:3]), int(col[4:6])
        d = _dt.date.fromisocalendar(yy, ww, 1)
        if _fdu and d < _fdu: return False
        if _fau and d > _fau: return False
        return True
    except:
        return True
wk_cols = [c for c in wk_cols if _wk_ok(c)]

# ── Métriques ─────────────────────────────────────────────────────────────────
nb_lpc    = len(df_aff[df_aff.get('ORIGINE', pd.Series()) == 'LPC']) if 'ORIGINE' in df_aff.columns else len(df_aff)
nb_carnet = len(df_aff[df_aff.get('ORIGINE', pd.Series()) == 'CARNET']) if 'ORIGINE' in df_aff.columns else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lignes LPC",    nb_lpc)
c2.metric("Lignes CARNET", nb_carnet)
c3.metric("Semaines",      len(wk_cols))
c4.metric("Refs SERTA",    df_aff['REF_ARTICLE_SERTA'].nunique() if 'REF_ARTICLE_SERTA' in df_aff.columns else 0)
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

# ── Filtres ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filtres", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        f_origine = st.multiselect("Origine", options=['LPC', 'CARNET'], default=['LPC', 'CARNET'])
    with col2:
        f_client  = st.multiselect("Code client",
            options=sorted(df_aff['CODE_CLIENT'].dropna().astype(str).unique()) if 'CODE_CLIENT' in df_aff.columns else [])
    with col3:
        f_ref = st.multiselect("Ref SERTA",
            options=sorted(df_aff['REF_ARTICLE_SERTA'].dropna().astype(str).unique()) if 'REF_ARTICLE_SERTA' in df_aff.columns else [])
    with col4:
        f_prog = st.multiselect("Programme client",
            options=sorted(df_aff['PROGRAMME'].replace('', None).dropna().astype(str).unique()) if 'PROGRAMME' in df_aff.columns else [])

df_disp = df_aff.copy()
if f_origine: df_disp = df_disp[df_disp['ORIGINE'].isin(f_origine)]
if f_client:  df_disp = df_disp[df_disp['CODE_CLIENT'].astype(str).isin(f_client)]
if f_ref:     df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].astype(str).isin(f_ref)]
if f_prog:    df_disp = df_disp[df_disp['PROGRAMME'].isin(f_prog)]

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Tableau", "📈 Graphique", "💾 Export"])

with tab1:
    col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
    st.caption(f"{len(df_disp):,} lignes")
    st.dataframe(df_disp[META_ALL + wk_cols], width='stretch', height=600, column_config=col_cfg)

with tab2:
    if wk_cols and 'ORIGINE' in df_disp.columns:
        rows_g = []
        for orig, grp in df_disp.groupby('ORIGINE'):
            for wk in wk_cols:
                rows_g.append({'SEMAINE': wk, 'QTE': grp[wk].sum(), 'ORIGINE': orig})
        df_g = pd.DataFrame(rows_g)
        if not df_g.empty:
            fig = px.bar(df_g, x='SEMAINE', y='QTE', color='ORIGINE', barmode='group',
                         title="QTY par semaine — LPC vs Carnet",
                         color_discrete_map={'LPC': '#1F4E79', 'CARNET': '#C00000'})
            st.plotly_chart(fig, width="stretch")

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV",
            data=df_disp.to_csv(index=False, encoding='utf-8-sig', sep=';'),
            file_name=f"agregation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch")
    with c2:
        st.download_button("📥 Excel",
            data=to_excel_bytes(df_disp),
            file_name=f"agregation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch")