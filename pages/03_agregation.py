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

DATES_FICTIVES = ['2030-12-31', '2075-12-31', '2099-12-31']

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
        "Cette page repart des données **Programmes agrégés (page 02)** "
        "(Lasernet + Hors Lasernet) et y ajoute "
        "les lignes du carnet de commande dont le couple "
        "`CODE_CLIENT + REF_ARTICLE_SERTA` n'est pas couvert.\n\n"
        "👈 Allez d'abord sur la page **📂 Dépôt & Agrégation** "
        "et agrégez les fichiers pour alimenter cette vue."
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

# ── Charger le consolidé — session d'abord, sinon base ───────────────────────
def charger_consolide_depuis_base():
    """Fallback : lire le consolidé validé depuis T_PREVISION_FICHIERS."""
    try:
        import pyodbc
        from io import BytesIO
        conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=W25-DWDI;DATABASE=master;Trusted_Connection=yes;"
            "Connect Timeout=300;"
        )
        conn = pyodbc.connect(conn_str, timeout=60)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1 Fichier FROM [dbo].[T_PREVISION_FICHIERS]
            WHERE Source = 'CONSOLIDE_VALIDE'
            ORDER BY DATE_MODIF DESC
        """)
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        df = pd.read_excel(BytesIO(bytes(row[0])))
        df.columns = [str(c) for c in df.columns]
        return df
    except Exception as e:
        print(f"Erreur chargement consolidé depuis base : {e}")
        return None

if 'df_consolide' in st.session_state:
    df_lpc = st.session_state['df_consolide'].copy()
    st.sidebar.success("✅ Données depuis la session (page 02)")
else:
    df_lpc = charger_consolide_depuis_base()
    if df_lpc is not None:
        st.sidebar.info("📦 Données chargées depuis la base SQL")
    else:
        st.warning(
            "⚠️ Aucune donnée — allez sur la page **📂 Dépôt & Agrégation**, "
            "agrégez les fichiers et cliquez sur **✅ Valider & Sauvegarder**."
        )
        st.stop()

# ── Préparer : ajouter colonnes ORIGINE + CODE_CLIENT ────────────────────────
wk_lpc = wk_cols_from_df(df_lpc)

# Toujours extraire depuis PROGRAMME en priorité (format CODE_PERIODE, ex: 0288_2622)
if 'PROGRAMME' in df_lpc.columns:
    df_lpc['CODE_CLIENT'] = df_lpc['PROGRAMME'].apply(extract_code_client).astype(str)
elif 'CODE_CLIENT' in df_lpc.columns:
    df_lpc['CODE_CLIENT'] = df_lpc['CODE_CLIENT'].astype(str).str.strip()
else:
    df_lpc['CODE_CLIENT'] = ''

# Nettoyer les codes clients mal formés (ex: 676.0 → 676)
df_lpc['CODE_CLIENT'] = df_lpc['CODE_CLIENT'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

if 'ORIGINE' not in df_lpc.columns:
    if 'Source' in df_lpc.columns:
        df_lpc['ORIGINE'] = df_lpc['Source'].map(
            {'LASERNET': 'LPC', 'HORS_LASERNET': 'MANUEL'}
        ).fillna('LPC')
    else:
        df_lpc['ORIGINE'] = 'LPC'

# Couples couverts (LPC + Hors Lasernet) → le carnet ne complète que ce qui manque
couples_lpc = set(
    df_lpc['CODE_CLIENT'].astype(str).str.strip() + '|' +
    df_lpc['REF_ARTICLE_SERTA'].astype(str).str.strip()
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
                    CAST(SERTA_SO_CLIENT_CODE AS VARCHAR(50))      AS CODE_CLIENT,
                    CAST(ITEM_REF AS VARCHAR(50))                  AS REF_ARTICLE_SERTA,
                    CAST(ITEM_CLIENT_REF AS VARCHAR(100))          AS REF_ARTICLE_CLIENT,
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

    # Garder uniquement couples ABSENTS du consolidé
    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    # Exclure codes groupe (GRP...)
    df = df[~df['CODE_CLIENT'].str.startswith('GRP')]
    if df.empty:
        return pd.DataFrame()

    # Calculer besoin retard/encours AVANT le filtre couples
    df_retard  = df[df['CLIENT_ACK_DATE'].dt.date <  date_du]
    df_encours = df[(df['CLIENT_ACK_DATE'].dt.date >= date_du) & (df['CLIENT_ACK_DATE'].dt.date <= date_au)]

    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']
    df = df[~df['_COUPLE'].isin(couples_lpc)].drop(columns=['_COUPLE'])

    if df.empty:
        return pd.DataFrame()

    # Ajouter colonnes méta fixes
    df['ORIGINE']        = 'CARNET'
    df['PROGRAMME']      = ''
    df['CODE_SELECTION'] = ''
    df['QTE_TOTALE']     = None

    # Besoin retard et encours par couple
    besoin_retard  = df_retard.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['QTE'].sum().reset_index()
    besoin_retard.columns  = ['CODE_CLIENT','REF_ARTICLE_SERTA','QTE_BESOIN_CLIENT_RETARD_SC']
    besoin_encours = df_encours.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['QTE'].sum().reset_index()
    besoin_encours.columns = ['CODE_CLIENT','REF_ARTICLE_SERTA','QTE_BESOIN_CLIENT_ENCOURS_SC']

    # Pivoter par semaine
    meta = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION', 'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']
    meta = [c for c in meta if c in df.columns]
    for c in meta:
        if df[c].dtype == object:
            df[c] = df[c].fillna('').astype(str)
        else:
            df[c] = df[c].fillna(0)

    agg   = df.groupby(meta + ['SEMAINE'])['QTE'].sum().reset_index()
    pivot = agg.pivot_table(index=meta, columns='SEMAINE', values='QTE',
                            aggfunc='sum', fill_value=0).reset_index()
    pivot.columns.name = None

    pivot = pivot.merge(besoin_retard,  on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    pivot = pivot.merge(besoin_encours, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    for col in ['QTE_BESOIN_CLIENT_RETARD_SC','QTE_BESOIN_CLIENT_ENCOURS_SC']:
        pivot[col] = pd.to_numeric(pivot[col], errors='coerce').fillna(0).astype(int)
    return pivot


def charger_suivi_carnet(date_prevision, date_ventilation):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        dp = date_prevision.strftime('%Y-%m-%d')
        dv = date_ventilation.strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT
                    CODE_CLIENT,
                    REF_ARTICLE_SERTA,
                    SUM(CASE WHEN DATE_FACTURE IS NULL
                              AND DATE_EXPEDITION <= '{dp}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_EN_TRANSITE_RETARD,
                    SUM(CASE WHEN DATE_FACTURE IS NULL
                              AND DATE_EXPEDITION >  '{dp}'
                              AND DATE_EXPEDITION <= '{dv}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_EN_TRANSITE_ENCOURS,
                    SUM(CASE WHEN DATE_FACTURE IS NOT NULL
                              AND DATE_FACTURE >  '{dp}'
                              AND DATE_FACTURE <= '{dv}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_FACTUREE_ENCOURS
                FROM [master].[dbo].[V_EXPEDITION_SUIVI]
                GROUP BY CODE_CLIENT, REF_ARTICLE_SERTA
            """), conn)
        df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.strip()
        df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
        return df
    except Exception as e:
        st.warning(f"Suivi expédition non disponible : {e}")
        return pd.DataFrame()


def charger_facture_recent_hors_couverture(couples_couverts, date_prevision):
    """
    Capture les couples CODE_CLIENT|REF_ARTICLE_SERTA facturés/expédiés récemment
    (fenêtre ±1 semaine autour de la date de prévision) qui ne sont couverts
    ni par un programme LPC actif, ni par une ligne carnet ouverte.
    Comble le trou : commande facturée (sortie du carnet) mais pas encore
    de nouveau programme enregistré pour la suite.
    """
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        date_debut = (date_prevision - timedelta(weeks=1)).strftime('%Y-%m-%d')
        date_fin   = (date_prevision + timedelta(weeks=1)).strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT
                    CODE_CLIENT,
                    REF_ARTICLE_SERTA,
                    SUM(QTE_EXPEDIEE) AS QTE_FACTUREE_RECENTE,
                    MAX(DATE_FACTURE) AS DERNIERE_FACTURE
                FROM [master].[dbo].[V_EXPEDITION_SUIVI]
                WHERE DATE_FACTURE IS NOT NULL
                  AND DATE_FACTURE >= '{date_debut}'
                  AND DATE_FACTURE <= '{date_fin}'
                GROUP BY CODE_CLIENT, REF_ARTICLE_SERTA
            """), conn)
    except Exception as e:
        st.warning(f"Recherche facturé récent non disponible : {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']

    # Ne garder que les couples NON couverts par LPC ni carnet
    df = df[~df['_COUPLE'].isin(couples_couverts)].drop(columns=['_COUPLE'])
    if df.empty:
        return pd.DataFrame()

    df['ORIGINE']        = 'FACTURE_RECENTE'
    df['PROGRAMME']      = ''
    df['CODE_SELECTION'] = ''
    df['REF_ARTICLE_CLIENT'] = ''
    df['UP_PRINCIPALE']  = ''
    df['QTE_UC']         = 0
    df['QTE_MOQ']        = 0
    df['QTE_TOTALE']     = df['QTE_FACTUREE_RECENTE']

    return df



# ── Fusionner consolidé + CARNET ─────────────────────────────────────────────
if btn_ajouter_carnet:
    with st.spinner("⏳ Chargement carnet de commande..."):
        df_carnet = charger_carnet(couples_lpc, date_filtre_du, date_filtre_au)
        date_prev = st.session_state.get('date_prevision', date_filtre_du)
        date_vent = st.session_state.get('date_ventilation', date_filtre_au)
        df_suivi  = charger_suivi_carnet(date_prev, date_vent)

    frames_all = [df_lpc]
    if df_carnet.empty:
        st.info("ℹ️ Aucune ligne carnet à ajouter — tous les couples sont couverts.")
    else:
        st.success(f"✅ {len(df_carnet)} lignes carnet ajoutées")
        frames_all.append(df_carnet)

    # Couples déjà couverts par LPC + CARNET (avant ajout du facturé récent)
    couples_lpc_carnet = set(couples_lpc)
    if not df_carnet.empty:
        couples_lpc_carnet |= set(
            df_carnet['CODE_CLIENT'].astype(str).str.strip() + '|' +
            df_carnet['REF_ARTICLE_SERTA'].astype(str).str.strip()
        )

    with st.spinner("⏳ Recherche du facturé récent hors couverture..."):
        df_facture_recent = charger_facture_recent_hors_couverture(couples_lpc_carnet, date_prev)

    if df_facture_recent.empty:
        st.info("ℹ️ Aucune référence facturée récemment hors couverture.")
    else:
        st.warning(
            f"⚠️ {len(df_facture_recent)} référence(s) facturée(s) récemment "
            "(±1 semaine autour de la date de prévision) sans programme actif ni carnet ouvert — ajoutées."
        )
        frames_all.append(df_facture_recent)

    df_all = pd.concat(
        [f.dropna(axis=1, how='all') for f in frames_all],
        ignore_index=True, sort=False)

    # Joindre suivi expédition sur lignes CARNET + calculer cutoff
    if not df_suivi.empty and 'ORIGINE' in df_all.columns:
        mask = df_all['ORIGINE'] == 'CARNET'
        df_c = df_all[mask].copy()
        df_c = df_c.merge(df_suivi, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
        for col in ['QTE_EN_TRANSITE_RETARD_SC','QTE_EN_TRANSITE_ENCOURS_SC','QTE_FACTUREE_ENCOURS_SC']:
            if col not in df_c.columns:
                df_c[col] = 0
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0).astype(int)
        for col in ['QTE_BESOIN_CLIENT_RETARD_SC','QTE_BESOIN_CLIENT_ENCOURS_SC']:
            if col not in df_c.columns:
                df_c[col] = 0
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0).astype(int)
        df_c['QTE_CUTOFF_RETARD_SC']   = df_c['QTE_BESOIN_CLIENT_RETARD_SC'] + df_c['QTE_EN_TRANSITE_RETARD_SC']
        df_c['QTE_CUTOFF_PREVISION_SC'] = df_c['QTE_EN_TRANSITE_ENCOURS_SC'] + df_c['QTE_FACTUREE_ENCOURS_SC'] + df_c['QTE_BESOIN_CLIENT_ENCOURS_SC']
        df_all = pd.concat([
            df_all[~mask].dropna(axis=1, how='all'),
            df_c.dropna(axis=1, how='all')
        ], ignore_index=True, sort=False)

    # Forcer types texte
    for col in ['PROGRAMME', 'CODE_SELECTION', 'ORIGINE', 'CODE_CLIENT',
                'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']:
        if col in df_all.columns:
            df_all[col] = df_all[col].fillna('').astype(str)
    if 'QTE_TOTALE' in df_all.columns:
        df_all['QTE_TOTALE'] = pd.to_numeric(df_all['QTE_TOTALE'], errors='coerce').fillna(0)

    wk_all = wk_cols_from_df(df_all)
    for c in wk_all:
        df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)

    # Filtrer colonnes semaines dans plage
    wk_in_range = [c for c in wk_all
                   if wk_label_to_date(c) is not None
                   and date_filtre_du <= wk_label_to_date(c) <= date_filtre_au]
    meta_cols = [c for c in df_all.columns if c not in wk_all]
    df_all = df_all[meta_cols + sorted(wk_in_range)]

    # Mapping groupe client depuis V_SUPPLY_CHAIN → LPC et MANUEL
    try:
        from sqlalchemy import text as _text2
        with get_engine().connect() as _conn:
            df_grp = pd.read_sql(_text2(
                "SELECT DISTINCT SERTA_SO_CLIENT_CODE AS CODE_CLIENT, "
                "SERTA_SO_CLIENT_GROUP_NAME, SERTA_SO_CLIENT_NAME, "
                "SALES_ADMINISTRATION_PERSON "
                "FROM [master].[dbo].[V_SUPPLY_CHAIN] "
                "WHERE SERTA_SO_CLIENT_GROUP_NAME IS NOT NULL"
            ), _conn)
        df_grp['CODE_CLIENT'] = df_grp['CODE_CLIENT'].astype(str).str.strip()
        df_grp = df_grp.drop_duplicates('CODE_CLIENT')
        for _col in ['SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON']:
            if _col not in df_all.columns:
                df_all[_col] = ''
            df_all[_col] = df_all[_col].fillna('').astype(str).str.strip()
        grp_map = df_grp.set_index('CODE_CLIENT').to_dict('index')
        mask = df_all['SERTA_SO_CLIENT_GROUP_NAME'].isin(['', 'nan', 'None'])
        codes = df_all.loc[mask, 'CODE_CLIENT'].astype(str).str.strip()
        for _col in ['SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON']:
            df_all.loc[mask, _col] = codes.map(
                {k: v.get(_col, '') for k, v in grp_map.items()}
            ).fillna('')
    except Exception as _e:
        print(f"Mapping groupe client échoué : {_e}")

    st.session_state['df_03'] = df_all

# ── Si pas encore chargé avec carnet ─────────────────────────────────────────
if 'df_03' not in st.session_state:
    df_lpc_disp = df_lpc.copy()
    for col in ['PROGRAMME', 'CODE_CLIENT', 'ORIGINE']:
        if col in df_lpc_disp.columns:
            df_lpc_disp[col] = df_lpc_disp[col].fillna('').astype(str)
    st.info("ℹ️ Affichage consolidé uniquement. Cliquez sur **🔄 Charger / Actualiser carnet** pour ajouter le carnet.")
    df_aff = df_lpc_disp
else:
    df_aff = st.session_state['df_03'].copy()

# ── Colonnes méta et semaines ─────────────────────────────────────────────────
META_ALL = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'HORIZON_PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
            'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'QTE_EN_TRANSITE_RETARD_SC', 'QTE_BESOIN_CLIENT_RETARD_SC', 'QTE_CUTOFF_RETARD_SC',
            'QTE_FACTUREE_ENCOURS_SC', 'QTE_EN_TRANSITE_ENCOURS_SC',
            'QTE_BESOIN_CLIENT_ENCOURS_SC', 'QTE_CUTOFF_PREVISION_SC',
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
nb_lpc    = len(df_aff[df_aff['ORIGINE'] == 'LPC'])    if 'ORIGINE' in df_aff.columns else len(df_aff)
nb_manuel = len(df_aff[df_aff['ORIGINE'].isin(['MANUEL','HORS_LASERNET'])]) if 'ORIGINE' in df_aff.columns else 0
nb_carnet = len(df_aff[df_aff['ORIGINE'] == 'CARNET']) if 'ORIGINE' in df_aff.columns else 0
nb_facture_recente = len(df_aff[df_aff['ORIGINE'] == 'FACTURE_RECENTE']) if 'ORIGINE' in df_aff.columns else 0

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Lignes LPC",           nb_lpc)
c2.metric("Lignes Hors Lasernet", nb_manuel)
c3.metric("Lignes CARNET",        nb_carnet)
c4.metric("Facturé récent",       nb_facture_recente)
c5.metric("Semaines",             len(wk_cols))
c6.metric("Refs SERTA",           df_aff['REF_ARTICLE_SERTA'].nunique() if 'REF_ARTICLE_SERTA' in df_aff.columns else 0)
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

# ── Filtres ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filtres", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        f_origine = st.multiselect("Origine", options=['LPC', 'MANUEL', 'HORS_LASERNET', 'CARNET', 'FACTURE_RECENTE'],
                                   default=['LPC', 'MANUEL', 'HORS_LASERNET', 'CARNET', 'FACTURE_RECENTE'])
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
                if wk in grp.columns:
                    rows_g.append({'SEMAINE': wk, 'QTE': grp[wk].sum(), 'ORIGINE': orig})
        df_g = pd.DataFrame(rows_g)
        if not df_g.empty:
            fig = px.bar(df_g, x='SEMAINE', y='QTE', color='ORIGINE', barmode='group',
                         title="QTY par semaine — LPC vs Hors Lasernet vs Carnet",
                         color_discrete_map={'LPC': '#1F4E79', 'MANUEL': '#375623',
                                             'HORS_LASERNET': '#375623', 'CARNET': '#C00000'})
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