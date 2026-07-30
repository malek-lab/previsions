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


# comparer les deux vues.
def charger_groupe_article(refs_tuple):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None or not refs_tuple:
        return pd.DataFrame(columns=['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE'])

    TAILLE_LOT = 100
    lots = [refs_tuple[i:i+TAILLE_LOT] for i in range(0, len(refs_tuple), TAILLE_LOT)]
    resultats = []
    erreurs = 0
    premiere_erreur = None
    progress = st.progress(0, text="Vérification du groupe article...")
    for i, lot in enumerate(lots):

        refs_echappees = ["''" + r.replace("'", "''''") + "''" for r in lot]
        refs_sql = ", ".join(refs_echappees)
        try:
            with engine.connect() as conn:
                df_lot = pd.read_sql(text(f"""
                    SELECT *
                    FROM OPENQUERY([SRV-MSSQLDB], '
                        SELECT REF_ARTICLE, CODE_GROUPE_ARTICLE
                        FROM DW.PRODUCTION.V_ART_STANDARD
                        WHERE REF_ARTICLE IN ({refs_sql})
                    ')
                """), conn)
            df_lot.columns = ['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE']
            resultats.append(df_lot)
        except Exception as e:
            erreurs += 1
            if premiere_erreur is None:
                premiere_erreur = f"[{type(e).__name__}] {e}"
        progress.progress((i+1)/len(lots), text=f"Vérification groupe article... lot {i+1}/{len(lots)}")
    progress.empty()

    if erreurs > 0:
        st.warning(f"⚠️ {erreurs}/{len(lots)} lot(s) de vérification groupe article ont échoué.")
        with st.expander("🔍 Détail de la première erreur"):
            st.code(premiere_erreur)
    if not resultats:
        return pd.DataFrame(columns=['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE'])

    df = pd.concat(resultats, ignore_index=True)
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    return df.drop_duplicates('REF_ARTICLE_SERTA')

GROUPES_PDR = ['PDR', 'PFPDR', 'MAUNIT', 'MAJOIN']

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
    st.markdown("---")

    inclure_projets = st.checkbox(
        "Inclure les Nouveaux Projets (page 05) dans la consolidation",
        value=False,
        help="Désactivé par défaut. Les refs 'PROJET' n'ont pas d'équivalent "
             "dans le suivi habituel (ex: fichier de référence externe) -- "
             "à activer seulement si tu veux volontairement les inclure.")
    st.markdown("---")
.
    filtrer_pdr = st.checkbox(
        "Filtrer les PDR/composants du programme (LPC)",
        value=False,
        help="Désactivé par défaut. Exclut les références classées "
             "PDR/PFPDR/MAUNIT/MAJOIN (vrai CODE_GROUPE_ARTICLE) côté "
             "programme. À activer pour comparer avec/sans ce périmètre -- "
             "certaines refs PDR sont malgré tout suivies par la référence "
             "externe, aucune règle systématique connue pour les distinguer.")

if 'df_03' not in st.session_state:
    st.warning("⚠️ Aucune donnée — lancez d'abord la page **📦 Agrégation** et cliquez sur **LANCER**.")
    st.stop()

df_src = st.session_state['df_03'].copy()

# AJOUT : application du filtre PDR optionnel, uniquement sur les lignes LPC
# (le carnet exclut deja PDR a la source, pas la peine d'y retoucher ici).
if filtrer_pdr and 'ORIGINE' in df_src.columns:
    refs_lpc = tuple(sorted(
        df_src.loc[df_src['ORIGINE'].isin(['LPC', 'MANUEL']), 'REF_ARTICLE_SERTA']
        .dropna().astype(str).str.strip().unique()
    ))
    df_groupe = charger_groupe_article(refs_lpc)
    if not df_groupe.empty:
        nb_avant = len(df_src)
        df_src = df_src.merge(df_groupe, on='REF_ARTICLE_SERTA', how='left')
        est_lpc = df_src['ORIGINE'].isin(['LPC', 'MANUEL'])
        est_pdr = df_src['VRAI_GROUPE_ARTICLE'].isin(GROUPES_PDR)
        a_retirer = est_lpc & est_pdr
        nb_retirees = a_retirer.sum()
        df_src = df_src[~a_retirer].drop(columns=['VRAI_GROUPE_ARTICLE'])
        if nb_retirees > 0:
            st.info(f"🧹 {nb_retirees} ligne(s) programme retirée(s) (PDR/composants).")

frames_source = [df_src]

if inclure_projets:
    df_proj_new = st.session_state.get('df_projets_nouveaux', pd.DataFrame())
    if not df_proj_new.empty:
        frames_source.append(df_proj_new)

    df_proj_dbl = st.session_state.get('df_projets_doublons', pd.DataFrame())
    if not df_proj_dbl.empty:

        df_proj_dbl_clean = df_proj_dbl.drop(columns=['DOUBLON'], errors='ignore')
        frames_source.append(df_proj_dbl_clean)

if len(frames_source) > 1:
    df_src = pd.concat([f.dropna(axis=1, how='all') for f in frames_source],
                        ignore_index=True, sort=False)
    wk_apres_fusion = wk_cols_from_df(df_src)
    for c in wk_apres_fusion:
        df_src[c] = pd.to_numeric(df_src[c], errors='coerce').fillna(0)
    for col_txt in ['CODE_CLIENT', 'PROGRAMME', 'CODE_SELECTION', 'ORIGINE']:
        if col_txt in df_src.columns:
            df_src[col_txt] = df_src[col_txt].fillna('').astype(str)
    nb_new = len(df_proj_new) if not df_proj_new.empty else 0
    nb_dbl = len(df_proj_dbl) if not df_proj_dbl.empty else 0
    st.success(f"✅ Projets fusionnés avec la source avant consolidation — "
               f"{nb_new} nouvelles refs + {nb_dbl} refs en commun (sommées, "
               f"origine combinée type LPC/PROJET)")

# ── Colonnes méta et semaines ─────────────────────────────────────────────────
META_SRC = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'HORIZON_PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
            'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'ITEM_GROUP_CODE', 'SERTA_SO_STATUS_MIN', 'CLIENT_ORDER_NUM',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON',
            'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
            'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
            'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
            'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
            'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
            'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC',
            # AJOUT : colonnes propres aux projets (page 05), utiles a garder si presentes
            'NUM_PROJET', 'STATUT', 'DATE_LIVRAISON_SERIE', 'SUCCESS_RATE', 'QTE_ANNUELLE',
            'PROJECT_MANAGER', 'SALES_PERSON']
META_SRC = [c for c in META_SRC if c in df_src.columns]
wk_cols_src = [c for c in df_src.columns if c not in META_SRC
               and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-']
wk_cols_src = sorted(wk_cols_src)

# ── Construction consolidée ───────────────────────────────────────────────────
@st.cache_data
def consolider(df_src_json, wk_cols):
    import io
    df = pd.read_json(io.StringIO(df_src_json), orient='split')

    QTE_A_SOMMER = [
        'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
        'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
        'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
        'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
        'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
        'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC',
    ]
    QTE_A_SOMMER = [c for c in QTE_A_SOMMER if c in df.columns]
    for c in QTE_A_SOMMER:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    df_sorted = df.sort_values('ORIGINE', ascending=False)  # LPC avant CARNET/PROJET

    meta_agg = {}
    for col in ['REF_ARTICLE_CLIENT', 'UP_PRINCIPALE', 'CODE_SELECTION',
                'QTE_MOQ', 'QTE_UC', 'PROGRAMME', 'HORIZON_PROGRAMME',
                'ITEM_GROUP_CODE', 'SERTA_SO_STATUS_MIN', 'CLIENT_ORDER_NUM',
                'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON',
                'NUM_PROJET', 'STATUT', 'DATE_LIVRAISON_SERIE', 'SUCCESS_RATE', 'QTE_ANNUELLE',
                'PROJECT_MANAGER', 'SALES_PERSON']:
        if col not in df.columns:
            continue
        meta_agg[col] = df_sorted[df_sorted[col].astype(str).str.strip() != ''].groupby(
            'REF_ARTICLE_SERTA')[col].first()

    # ORIGINE : LPC / MANUEL / CARNET / PROJET ou combinaisons (ex: LPC/PROJET)
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

    wk_present = [c for c in wk_cols if c in df.columns]
    for c in wk_present:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    pivot = df.groupby('REF_ARTICLE_SERTA')[wk_present + QTE_A_SOMMER].sum().reset_index()

    for col, serie in meta_agg.items():
        mapped = pivot['REF_ARTICLE_SERTA'].map(serie)
        pivot[col] = mapped.fillna('').infer_objects(copy=False).astype(str)
    if origine_map is not None:
        pivot['ORIGINE'] = pivot['REF_ARTICLE_SERTA'].map(origine_map).fillna('').infer_objects(copy=False).astype(str)

    meta_cols = ['REF_ARTICLE_SERTA','REF_ARTICLE_CLIENT','ORIGINE','UP_PRINCIPALE',
                 'CODE_SELECTION','QTE_MOQ','QTE_UC','PROGRAMME','HORIZON_PROGRAMME',
                 'ITEM_GROUP_CODE','SERTA_SO_STATUS_MIN','CLIENT_ORDER_NUM',
                 'SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON',
                 'QTE_EN_TRANSITE_RETARD','QTE_BESOIN_CLIENT_RETARD','QTE_CUTOFF_RETARD',
                 'QTE_FACTUREE_ENCOURS','QTE_EN_TRANSITE_ENCOURS',
                 'QTE_BESOIN_CLIENT_ENCOURS','QTE_CUTOFF_PREVISION',
                 'QTE_EN_TRANSITE_RETARD_SC','QTE_BESOIN_CLIENT_RETARD_SC','QTE_CUTOFF_RETARD_SC',
                 'QTE_FACTUREE_ENCOURS_SC','QTE_EN_TRANSITE_ENCOURS_SC',
                 'QTE_BESOIN_CLIENT_ENCOURS_SC','QTE_CUTOFF_PREVISION_SC',
                 'NUM_PROJET','STATUT','DATE_LIVRAISON_SERIE','SUCCESS_RATE','QTE_ANNUELLE',
                 'PROJECT_MANAGER','SALES_PERSON']
    meta_cols = [c for c in meta_cols if c in pivot.columns]
    wk_sorted = sorted([c for c in pivot.columns if c not in meta_cols
                        and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])
    return pivot[meta_cols + wk_sorted]

df = consolider(df_src.to_json(orient='split'), wk_cols_src)

# ── Sauvegarder pour la page PIC ─────────────────────────────────────────────
st.session_state['df_consolide_final'] = df.copy() 


wk_cols = [c for c in df.columns
           if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
           and c[1:3].isdigit() and c[4:6].isdigit()]

import datetime as _dt
_fdu = st.session_state.get('date_carnet_du', None)
_fau = st.session_state.get('date_carnet_au', None)
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
nb_lpc    = df_src[df_src['ORIGINE'] == 'LPC']['REF_ARTICLE_SERTA'].nunique() if 'ORIGINE' in df_src.columns else 0
nb_manuel = df_src[df_src['ORIGINE'] == 'MANUEL']['REF_ARTICLE_SERTA'].nunique() if 'ORIGINE' in df_src.columns else 0
nb_carnet = df_src[df_src['ORIGINE'] == 'CARNET']['REF_ARTICLE_SERTA'].nunique() if 'ORIGINE' in df_src.columns else 0
nb_projet = df_src[df_src['ORIGINE'] == 'PROJET']['REF_ARTICLE_SERTA'].nunique() if 'ORIGINE' in df_src.columns else 0

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Refs totales",       len(df))
c2.metric("dont LPC",           nb_lpc)
c3.metric("dont Hors Lasernet", nb_manuel)
c4.metric("dont CARNET",        nb_carnet)
c5.metric("dont PROJET",        nb_projet)
c6.metric("Semaines",           len(wk_cols))
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

# ── Filtres ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filtres", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
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

df_disp = df.copy()
if f_ref:     df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].astype(str).isin(f_ref)]
if f_up:      df_disp = df_disp[df_disp['UP_PRINCIPALE'].astype(str).isin(f_up)]
if f_prog:    df_disp = df_disp[df_disp['PROGRAMME'].astype(str).isin(f_prog)]
if f_origine: df_disp = df_disp[df_disp['ORIGINE'].astype(str).isin(f_origine)]

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
                     title="QTY totale consolidée par semaine (LPC + Carnet + Projet)",
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