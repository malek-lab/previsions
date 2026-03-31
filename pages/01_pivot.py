import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date
from io import BytesIO
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, get_programmes, execute_procedure, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.markdown("""
<style>
[data-testid="stSidebar"] { overflow-y: auto; }
[data-testid="stSidebar"] > div:first-child { overflow-y: auto; height: 100vh; padding-bottom: 2rem; }
</style>
""", unsafe_allow_html=True)

st.title("📊 Pivot Prévision — Ventilation des besoins")

if get_engine() is None:
    st.stop()

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Paramètres")
    st.markdown("---")

    st.subheader("1️⃣ Programme(s)")
    with st.spinner("Chargement..."):
        df_prog = get_programmes()

    if df_prog.empty:
        st.warning("Aucun programme disponible")
        st.stop()

    mode = st.radio("Mode", ["Un programme", "Plusieurs"], horizontal=True)

    if mode == "Un programme":
        options = df_prog['Programme'].tolist()
        last    = st.session_state.get('last_programme', None)
        default = options.index(last) if last in options else None
        prog_choisi = st.selectbox("Programme", options=options, index=default,
                                   placeholder="— Sélectionnez un programme —")
        if prog_choisi:
            st.session_state['last_programme'] = prog_choisi
            row = df_prog[df_prog['Programme'] == prog_choisi].iloc[0]
            selected_ids = [int(row['FPC_ID'])]
            with st.expander("ℹ️ Détails"):
                st.write(f"**Client :** {row.get('CLI_NOM','—')}")
                h = row.get('Horizon_programme')
                st.write(f"**Horizon :** {pd.to_datetime(h).strftime('%d/%m/%Y') if pd.notna(h) else '—'}")
                try:
                    st.write(f"**QTY Totale :** {row['QTE_PREVISION_TOTALE']:,.0f}")
                except:
                    pass
        else:
            selected_ids = []
    else:
        suffixes = sorted(set(
            p.split('_')[-1] if '_' in p else p[-4:]
            for p in df_prog['Programme'].tolist()
        ))
        with st.form("form_programmes"):
            filtre_suffix = st.selectbox("Filtrer par période",
                options=['— tous —'] + suffixes, index=0)
            options_filtrees = [p for p in df_prog['Programme'].tolist()
                if filtre_suffix == '— tous —' or p.endswith(filtre_suffix)]
            st.caption(f"{len(options_filtrees)} programme(s) dans cette période")
            col_a, col_b = st.columns(2)
            with col_a:
                btn_ajouter   = st.form_submit_button("➕ Ajouter", use_container_width=True)
            with col_b:
                btn_remplacer = st.form_submit_button("🔄 Remplacer", use_container_width=True)

        if 'multiselect_programmes' not in st.session_state:
            st.session_state['multiselect_programmes'] = []
        if btn_remplacer:
            st.session_state['multiselect_programmes'] = [
                p for p in options_filtrees if p in df_prog['Programme'].tolist()]
        elif btn_ajouter:
            existants = st.session_state.get('multiselect_programmes', [])
            st.session_state['multiselect_programmes'] = list(dict.fromkeys(
                existants + [p for p in options_filtrees if p in df_prog['Programme'].tolist()]))

        progs_choisis = st.multiselect(
            "Programmes sélectionnés",
            options=df_prog['Programme'].tolist(),
            placeholder="— Sélectionnez ou utilisez le filtre —",
            key="multiselect_programmes")

        selected_ids = df_prog[df_prog['Programme'].isin(progs_choisis)]['FPC_ID'].tolist()
        if selected_ids:
            st.info(f"✅ {len(selected_ids)} programme(s) sélectionné(s)")
        if st.button("🗑️ Vider", use_container_width=True):
            st.session_state['multiselect_programmes'] = []
            st.rerun()

    st.markdown("---")
    st.subheader("2️⃣ Dates")
    date_prevision   = st.date_input("📅 Date de prévision", value=datetime.now().date(),
                                     format="DD/MM/YYYY",
                                     help="Date de référence pour le calcul du cutoff.")
    date_ventilation = st.date_input("📅 Date début ventilation",
                                     value=datetime.now().date() + timedelta(days=7),
                                     format="DD/MM/YYYY",
                                     help="Lundi à partir duquel les quantités futures sont ventilées.")

    st.markdown("---")
    st.subheader("🗓️ Filtre besoins client")
    date_filtre_du = st.date_input("Du", value=datetime.now().date(), format="DD/MM/YYYY")
    date_filtre_au = st.date_input("Au", value=datetime(datetime.now().year+1, 12, 31).date(),
                                   format="DD/MM/YYYY")

    st.markdown("---")
    st.subheader("⚙️ Options")
    activer_ventilation = st.toggle("Activer ventilation", value=True)

    st.markdown("---")
    st.subheader("3️⃣ Unité de lot")
    source_lot      = st.radio("Type", ["📦 MOQ", "📏 UC"], horizontal=True,
                                label_visibility="collapsed")
    source_lot_bool = source_lot.startswith("📦")

    st.markdown("---")
    btn_lancer = st.button("🚀 LANCER", type="primary", width="stretch",
                           disabled=len(selected_ids) == 0)
    if not selected_ids:
        st.warning("⚠️ Sélectionnez un programme")

# ── Lancement ─────────────────────────────────────────────────────────────────
if btn_lancer:
    prog   = st.progress(0)
    status = st.empty()
    st.session_state['date_filtre_du'] = date_filtre_du
    st.session_state['date_filtre_au'] = date_filtre_au
    st.session_state['date_prevision'] = date_prevision
    frames = []
    n = len(selected_ids)
    for i, fpc_id in enumerate(selected_ids):
        nom_prog = df_prog[df_prog['FPC_ID'] == fpc_id]['Programme'].values[0]
        status.text(f"⏳ {i+1}/{n} : {nom_prog}")
        prog.progress(int((i / n) * 90) + 5)
        df_tmp = execute_procedure([fpc_id], date_prevision, date_ventilation,
                                   source_lot_bool, activer_ventilation)
        if df_tmp is not None and not df_tmp.empty:
            df_tmp['PROGRAMME'] = str(nom_prog)
            row_prog = df_prog[df_prog['FPC_ID'] == fpc_id].iloc[0]
            df_tmp['CODE_CLIENT'] = str(row_prog.get('CLI_CODE', '')).strip()
            df_tmp = df_tmp.drop(columns=[c for c in ['NOM_FICHIER_PROGRAMME_CLIENT',
                'LIBELLE_SYSTEME_COMMANDE','DATE_BORN_GAUCHE','DATE_BORN_DROIT']
                if c in df_tmp.columns])
            frames.append(df_tmp)

    if frames:
        df_res = pd.concat(frames, ignore_index=True, sort=False)

        # Colonnes texte → str avant fillna pour éviter ArrowTypeError (UP_PRINCIPALE mixte)
        TEXT_COLS = ['PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
                     'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'CODE_CLIENT',
                     'HORIZON_PROGRAMME']
        for _c in TEXT_COLS:
            if _c in df_res.columns:
                df_res[_c] = df_res[_c].fillna('').astype(str)

        # Colonnes numériques → pd.to_numeric + fillna(0) sans downcasting warning
        for _c in [c for c in df_res.columns if c not in TEXT_COLS]:
            if df_res[_c].dtype == object:
                df_res[_c] = pd.to_numeric(df_res[_c], errors='coerce')
            df_res[_c] = df_res[_c].fillna(0)

        if 'HORIZON_PROGRAMME' in df_res.columns:
            df_res['HORIZON_PROGRAMME'] = pd.to_datetime(
                df_res['HORIZON_PROGRAMME'], errors='coerce'
            ).dt.strftime('%d/%m/%Y').fillna('')

        prog.progress(100)
        status.empty(); prog.empty()
        st.session_state['df_pivot'] = df_res
    else:
        prog.empty(); status.empty()
        st.warning("⚠️ Aucune donnée retournée")

# ── Affichage ─────────────────────────────────────────────────────────────────
if 'df_pivot' not in st.session_state:
    st.info("👈 Sélectionnez un programme et cliquez sur **LANCER**")
    st.stop()

df      = st.session_state['df_pivot']
wk_cols = wk_cols_from_df(df)

# Filtrer les colonnes semaines selon plage du filtre
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

# Métriques
c1, c2, c3 = st.columns(3)
c1.metric("Articles",   df['REF_ARTICLE_SERTA'].nunique() if 'REF_ARTICLE_SERTA' in df.columns else 0)
c2.metric("QTY Totale", f"{int(df['QTE_TOTALE'].sum()):,}" if 'QTE_TOTALE' in df.columns else 0)
c3.metric("Semaines",   len(wk_cols))
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")
tab1, tab2, tab3 = st.tabs(["📋 Tableau Pivot", "📈 Graphiques", "💾 Export"])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        f_art = st.multiselect("🔍 Article SERTA",
            options=sorted(df['REF_ARTICLE_SERTA'].dropna().astype(str).unique())
            if 'REF_ARTICLE_SERTA' in df.columns else [], key="f_art")
    with col2:
        f_up = st.multiselect("🔍 UP",
            options=sorted(df['UP_PRINCIPALE'].dropna().astype(str).unique())
            if 'UP_PRINCIPALE' in df.columns else [], key="f_up")
    with col3:
        f_prog = st.multiselect("🔍 Programme client",
            options=sorted(df['PROGRAMME'].dropna().astype(str).unique())
            if 'PROGRAMME' in df.columns else [], key="f_prog")

    df_disp = df.copy()
    if f_art:  df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].astype(str).isin(f_art)]
    if f_up:   df_disp = df_disp[df_disp['UP_PRINCIPALE'].astype(str).isin(f_up)]
    if f_prog: df_disp = df_disp[df_disp['PROGRAMME'].astype(str).isin(f_prog)]

    # PROGRAMME en première colonne, puis le reste des meta, puis semaines
    all_wk    = wk_cols_from_df(df_disp)
    meta_disp = [c for c in df_disp.columns if c not in all_wk]
    # Remonter PROGRAMME en tête
    if 'PROGRAMME' in meta_disp:
        meta_disp = ['PROGRAMME'] + [c for c in meta_disp if c != 'PROGRAMME']
    col_cfg   = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
    wk_disp   = [c for c in wk_cols if c in df_disp.columns]
    st.caption(f"{len(df_disp):,} lignes")
    st.dataframe(df_disp[meta_disp + wk_disp], width='stretch', height=600,
                 column_config=col_cfg)

with tab2:
    if wk_cols and 'REF_ARTICLE_SERTA' in df.columns:
        totals = df[[c for c in wk_cols if c in df.columns]].sum()
        fig = px.bar(x=totals.index, y=totals.values,
                     title="QTY ventilée par semaine",
                     labels={'x': 'Semaine', 'y': 'QTY'},
                     color_discrete_sequence=['#375623'])
        st.plotly_chart(fig, width="stretch")
    if 'QTE_TOTALE' in df.columns:
        top10 = df.nlargest(10, 'QTE_TOTALE')[['REF_ARTICLE_SERTA', 'QTE_TOTALE']]
        fig2  = px.bar(top10, x='QTE_TOTALE', y='REF_ARTICLE_SERTA', orientation='h',
                       title="Top 10 Articles par QTY totale",
                       color_discrete_sequence=['#1F4E79'])
        st.plotly_chart(fig2, width="stretch")

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV",
            data=df_disp.to_csv(index=False, encoding='utf-8-sig', sep=';'),
            file_name=f"pivot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch")
    with c2:
        st.download_button("📥 Excel",
            data=to_excel_bytes(df_disp),
            file_name=f"pivot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch")