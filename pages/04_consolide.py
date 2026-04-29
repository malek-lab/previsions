import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.title("📊 Consolidée — Somme par Référence")

if get_engine() is None:
    st.stop()

# ── Sidebar minimal ───────────────────────────────────────────────────────────
with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "Cette page consolide les données de la page **Agrégation**.\n\n"
        "Elle fait la somme des quantités par `REF_ARTICLE_SERTA` "
        "(tous clients et toutes origines confondus).\n\n"
        "👈 Lancez d'abord la page **Agrégation** pour alimenter cette vue."
    )

# ── Vérifier que df_03 existe ─────────────────────────────────────────────────
if 'df_03' not in st.session_state:
    st.warning("⚠️ Aucune donnée — lancez d'abord la page **📦 Agrégation** et cliquez sur **LANCER**.")
    st.stop()

df_src = st.session_state['df_03'].copy()

# ── Colonnes méta et semaines ─────────────────────────────────────────────────
META_SRC = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'HORIZON_PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
            'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON',
            'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
            'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
            'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
            'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
            'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
            'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC']
META_SRC = [c for c in META_SRC if c in df_src.columns]
wk_cols_src = [c for c in df_src.columns if c not in META_SRC
               and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-']
wk_cols_src = sorted(wk_cols_src)

# ── Colonnes méta à garder dans la consolidée (une ligne par ref) ─────────────
# Priorité LPC pour les colonnes enrichissement
META_CONSOLIDE = ['REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'UP_PRINCIPALE',
                  'CODE_SELECTION', 'QTE_MOQ', 'QTE_UC', 'PROGRAMME', 'HORIZON_PROGRAMME']
META_CONSOLIDE = [c for c in META_CONSOLIDE if c in df_src.columns]

# ── Construction consolidée ───────────────────────────────────────────────────
@st.cache_data
def consolider(df_src_json, wk_cols):
    import io
    df = pd.read_json(io.StringIO(df_src_json), orient='split')

    # Pour les colonnes méta : prendre la valeur LPC si dispo, sinon CARNET
    # On trie pour avoir LPC en premier
    df_sorted = df.sort_values('ORIGINE', ascending=False)  # LPC avant CARNET

    meta_agg = {}
    for col in ['REF_ARTICLE_CLIENT', 'UP_PRINCIPALE', 'CODE_SELECTION',
                'QTE_MOQ', 'QTE_UC', 'PROGRAMME', 'HORIZON_PROGRAMME',
                'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON',
                'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
                'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
                'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
                'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
                'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
                'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC']:
        if col not in df.columns:
            continue
        meta_agg[col] = df_sorted[df_sorted[col].astype(str).str.strip() != ''].groupby(
            'REF_ARTICLE_SERTA')[col].first()

    # ORIGINE : LPC / MANUEL / CARNET / PROJET ou combinaisons
    if 'ORIGINE' in df.columns:
        ORDRE = ['LPC', 'MANUEL', 'CARNET', 'PROJET']
        def calc_origine(series):
            origines = set(series.dropna().astype(str).str.strip().unique()) - {''}
            if not origines:
                return ''
            triees = [o for o in ORDRE if o in origines]
            return '/'.join(triees) if triees else '/'.join(sorted(origines))
        origine_map = df.groupby('REF_ARTICLE_SERTA')['ORIGINE'].apply(calc_origine)
    else:
        origine_map = None

    # Somme des semaines par ref
    wk_present = [c for c in wk_cols if c in df.columns]
    for c in wk_present:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    pivot = df.groupby('REF_ARTICLE_SERTA')[wk_present].sum().reset_index()

    # Colonnes numériques suivi
    NUM_COLS = {'QTE_MOQ','QTE_UC','QTE_TOTALE',
                'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
                'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
                'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
                'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
                'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
                'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC'}

    # Ajouter les colonnes méta
    for col, serie in meta_agg.items():
        mapped = pivot['REF_ARTICLE_SERTA'].map(serie)
        if col in NUM_COLS:
            pivot[col] = pd.to_numeric(mapped, errors='coerce').fillna(0)
        else:
            pivot[col] = mapped.fillna('').infer_objects(copy=False).astype(str)
    if origine_map is not None:
        pivot['ORIGINE'] = pivot['REF_ARTICLE_SERTA'].map(origine_map).fillna('').infer_objects(copy=False).astype(str)

    # Réordonner colonnes
    meta_cols = ['REF_ARTICLE_SERTA','REF_ARTICLE_CLIENT','ORIGINE','UP_PRINCIPALE',
                 'CODE_SELECTION','QTE_MOQ','QTE_UC','PROGRAMME','HORIZON_PROGRAMME',
                 'SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON',
                 'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
                 'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
                 'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
                 'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
                 'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
                 'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC']
    meta_cols = [c for c in meta_cols if c in pivot.columns]
    wk_sorted = sorted([c for c in pivot.columns if c not in meta_cols
                        and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])
    return pivot[meta_cols + wk_sorted]

df = consolider(df_src.to_json(orient='split'), wk_cols_src)

# ── Intégration nouveaux projets depuis page 05 ───────────────────────────────
if 'df_projets_nouveaux' in st.session_state or 'df_projets_doublons' in st.session_state:
    frames_proj = []
    df_proj_new = st.session_state.get('df_projets_nouveaux', pd.DataFrame())
    if not df_proj_new.empty:
        wk_new = sorted([c for c in df_proj_new.columns
                         if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])
        for c in wk_new:
            df_proj_new[c] = pd.to_numeric(df_proj_new[c], errors='coerce').fillna(0)
        meta_new = [c for c in df_proj_new.columns if c not in wk_new]
        df_consol_new = df_proj_new.groupby('REF_ARTICLE_SERTA')[wk_new].sum().reset_index()
        for col in meta_new:
            if col == 'REF_ARTICLE_SERTA': continue
            valid = df_proj_new[df_proj_new[col].astype(str).str.strip() != '']
            if not valid.empty:
                df_consol_new[col] = df_consol_new['REF_ARTICLE_SERTA'].map(
                    valid.groupby('REF_ARTICLE_SERTA')[col].first())
        df_consol_new['DOUBLON'] = False
        frames_proj.append(df_consol_new)
    df_proj_dbl = st.session_state.get('df_projets_doublons', pd.DataFrame())
    if not df_proj_dbl.empty:
        frames_proj.append(df_proj_dbl)
    if frames_proj:
        df = pd.concat([df] + [f.dropna(axis=1, how='all') for f in frames_proj],
                       ignore_index=True, sort=False)
        wk_all = sorted([c for c in df.columns
                         if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])
        for col_wk in wk_all:
            df[col_wk] = pd.to_numeric(df[col_wk], errors='coerce').fillna(0)
        for col_txt in ['PROGRAMME', 'CODE_SELECTION', 'ORIGINE']:
            if col_txt in df.columns:
                df[col_txt] = df[col_txt].fillna('').infer_objects(copy=False).astype(str)
        if 'DOUBLON' not in df.columns:
            df['DOUBLON'] = False
        df['DOUBLON'] = df['DOUBLON'].fillna(False).astype(bool)
        if not df_proj_dbl.empty:
            refs_doublons = set(df_proj_dbl['REF_ARTICLE_SERTA'].astype(str).unique())
            df.loc[df['REF_ARTICLE_SERTA'].astype(str).isin(refs_doublons), 'DOUBLON'] = True
        nb_new = len(df_proj_new) if not df_proj_new.empty else 0
        nb_dbl = len(df_proj_dbl) if not df_proj_dbl.empty else 0
        st.success(f"✅ Projets intégrés — {nb_new} nouvelles refs + {nb_dbl} doublons en lignes séparées")

# ── Colonnes semaines telles quelles — pas de cutoff appliqué ───────────────

# ── Sauvegarder pour la page PIC ─────────────────────────────────────────────
st.session_state['df_consolide'] = df.copy()

wk_cols = [c for c in df.columns
           if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
           and c[1:3].isdigit() and c[4:6].isdigit()]

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
nb_lpc    = df_src[df_src['ORIGINE'] == 'LPC']['REF_ARTICLE_SERTA'].nunique()
nb_manuel = df_src[df_src['ORIGINE'] == 'MANUEL']['REF_ARTICLE_SERTA'].nunique()
nb_carnet = df_src[df_src['ORIGINE'] == 'CARNET']['REF_ARTICLE_SERTA'].nunique()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Refs totales",       len(df))
c2.metric("dont LPC",           nb_lpc)
c3.metric("dont Hors Lasernet", nb_manuel)
c4.metric("dont CARNET",        nb_carnet)
c5.metric("Semaines",           len(wk_cols))
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

# ── Filtres ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filtres", expanded=False):
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        f_ref  = st.multiselect("Ref SERTA",
            options=sorted(df['REF_ARTICLE_SERTA'].dropna().astype(str).unique()))
    with col2:
        f_up   = st.multiselect("UP",
            options=sorted(df['UP_PRINCIPALE'].dropna().replace('', None).dropna().astype(str).unique())
            if 'UP_PRINCIPALE' in df.columns else [])
    with col3:
        f_prog = st.multiselect("Programme client",
            options=sorted(df['PROGRAMME'].dropna().replace('', None).dropna().astype(str).unique())
            if 'PROGRAMME' in df.columns else [])
    with col4:
        f_origine = st.multiselect("Origine",
            options=sorted(df['ORIGINE'].dropna().replace('', None).dropna().astype(str).unique())
            if 'ORIGINE' in df.columns else [])
    with col5:
        f_doublon = st.selectbox("Doublons",
            options=['Tous', 'Doublons uniquement', 'Sans doublons'], index=0)

df_disp = df.copy()
if f_ref:     df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].astype(str).isin(f_ref)]
if f_up:      df_disp = df_disp[df_disp['UP_PRINCIPALE'].astype(str).isin(f_up)]
if f_prog:    df_disp = df_disp[df_disp['PROGRAMME'].astype(str).isin(f_prog)]
if f_origine: df_disp = df_disp[df_disp['ORIGINE'].astype(str).isin(f_origine)]
if 'DOUBLON' in df_disp.columns:
    if f_doublon == 'Doublons uniquement':
        df_disp = df_disp[df_disp['DOUBLON'] == True]
    elif f_doublon == 'Sans doublons':
        df_disp = df_disp[df_disp['DOUBLON'] == False]

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Tableau pivot", "📈 Graphique", "💾 Export"])

with tab1:
    col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
    st.caption(f"{len(df_disp):,} lignes")
    st.dataframe(df_disp, width='stretch', height=600, column_config=col_cfg)

with tab2:
    if wk_cols:
        totals = df_disp[wk_cols].sum()
        fig = px.bar(x=totals.index, y=totals.values,
                     title="QTY totale consolidée par semaine (LPC + Carnet)",
                     labels={'x': 'Semaine', 'y': 'QTY'},
                     color_discrete_sequence=['#1F4E79'])
        st.plotly_chart(fig, width="stretch")

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV",
            data=df_disp.to_csv(index=False, encoding='utf-8-sig', sep=';'),
            file_name=f"consolide_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch")
    with c2:
        st.download_button("📥 Excel",
            data=to_excel_bytes(df_disp),
            file_name=f"consolide_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch")