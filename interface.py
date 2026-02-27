import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta, date
import plotly.express as px
from io import BytesIO
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*keyword arguments.*deprecated.*')

st.set_page_config(page_title="Pivot Prévision", page_icon="📊", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"] {
    overflow-y: auto;
}
[data-testid="stSidebar"] > div:first-child {
    overflow-y: auto;
    height: 100vh;
    padding-bottom: 2rem;
}
</style>
""", unsafe_allow_html=True)

@st.cache_resource
def get_engine():
    try:
        return create_engine(
            "mssql+pyodbc://W25-DWDI/master"
            "?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
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

def execute_procedure(fpc_ids, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    engine = get_engine()
    if engine is None:
        return None
    try:
        # Si ventilation désactivée → date très lointaine = aucune semaine ventilée
        date_vent_eff = date_ventilation if activer_ventilation else date(9999, 12, 31)
        sql = f"""
        EXEC P_R_PIVOT_PREVISION_DEV_LOCAL
            @SERVEUR_LIE            = 'SRV-MSSQLDB',
            @FPC_ID                 = '{','.join(map(str, fpc_ids))}',
            @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
            @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
            @SOURCE_QTE_LOT         = {1 if source_lot else 0}
        """
        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:
        st.error(f"Erreur procédure : {e}")
        with st.expander("Détails"):
            st.exception(e)
        return None

# ====================================================================
# SIDEBAR
# ====================================================================
st.title("📊 Pivot Prévision — Ventilation des besoins")

if get_engine() is None:
    st.stop()

with st.sidebar:
    try:
        st.image("Serta_logo.jpg", width="stretch")
    except:
        st.title("SERTA")

    st.header("⚙️ Paramètres")
    st.markdown("---")

    # Programmes
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

        prog_choisi = st.selectbox(
            "Programme", options=options,
            index=default,
            placeholder="— Sélectionnez un programme —"
        )
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
        progs_choisis = st.multiselect(
            "Programmes", options=df_prog['Programme'].tolist(),
            placeholder="— Sélectionnez —"
        )
        selected_ids = df_prog[df_prog['Programme'].isin(progs_choisis)]['FPC_ID'].tolist()
        if selected_ids:
            st.info(f"✅ {len(selected_ids)} programme(s)")

    st.markdown("---")

    # Dates
    st.subheader("2️⃣ Dates")
    date_prevision = st.date_input(
        "📅 Date de prévision",
        value=datetime.now().date(), format="DD/MM/YYYY",
        help="Date de référence pour le calcul du cutoff. Correspond à aujourd'hui en utilisation normale."
    )
    date_ventilation = st.date_input(
        "📅 Date début ventilation",
        value=datetime.now().date() + timedelta(days=7), format="DD/MM/YYYY",
        help="Lundi à partir duquel les quantités futures sont ventilées. Avant cette date, les semaines affichent NULL."
    )

    st.markdown("---")
    st.subheader("🗓️ Filtre besoins client")
    date_filtre_du = st.date_input(
        "Du",
        value=datetime.now().date(), format="DD/MM/YYYY",
        help="Ne pas afficher les semaines avant cette date."
    )
    date_filtre_au = st.date_input(
        "Au",
        value=datetime(datetime.now().year + 1, 12, 31).date(), format="DD/MM/YYYY",
        help="Ne pas afficher les semaines après cette date."
    )

    st.markdown("---")
    st.subheader("⚙️ Options")
    activer_ventilation = st.toggle(
        "Activer ventilation",
        value=True,
        help="Si désactivé, affiche les quantités brutes du LPC sans ventilation MOQ/UC."
    )

    st.markdown("---")

    # Unité de lot
    st.subheader("3️⃣ Unité de lot")
    source_lot = st.radio("Type", ["📦 MOQ", "📏 UC"], horizontal=True, label_visibility="collapsed")
    source_lot_bool = source_lot.startswith("📦")

    st.markdown("---")

    btn_lancer = st.button(
        "🚀 LANCER", type="primary",
        width="stretch",
        disabled=len(selected_ids) == 0
    )
    if not selected_ids:
        st.warning("⚠️ Sélectionnez un programme")

# ====================================================================
# ZONE PRINCIPALE
# ====================================================================
if btn_lancer:
    prog   = st.progress(0)
    status = st.empty()
    status.text("⏳ Exécution en cours...")
    prog.progress(30)

    st.session_state['date_filtre_du'] = date_filtre_du
    st.session_state['date_filtre_au'] = date_filtre_au
    df_res = execute_procedure(selected_ids, date_prevision, date_ventilation, source_lot_bool, activer_ventilation)
    prog.progress(90)

    if df_res is not None and not df_res.empty:
        prog.progress(100)
        status.empty(); prog.empty()
        st.session_state['df_pivot'] = df_res

    elif df_res is not None:
        prog.empty(); status.empty()
        st.warning("⚠️ Aucune donnée retournée")
    else:
        prog.empty(); status.empty()

# Affichage depuis session_state
if 'df_pivot' in st.session_state:
    df = st.session_state['df_pivot']

    # Colonnes semaines = format S26-09
    wk_cols = sorted([c for c in df.columns
                      if isinstance(c, str) and len(c) == 6
                      and c[0] == 'S' and c[3] == '-' and c[1:3].isdigit() and c[4:6].isdigit()])

    # Métriques
    c1, c2, c3 = st.columns(3)
    c1.metric("Articles", df['REF_ARTICLE_SERTA'].nunique() if 'REF_ARTICLE_SERTA' in df.columns else 0)
    c2.metric("QTY Totale", f"{df['QTE_TOTALE'].max() * df['REF_ARTICLE_SERTA'].nunique():,.0f}" if 'QTE_TOTALE' in df.columns else 0)
    c3.metric("Semaines", len(wk_cols))
    if wk_cols:
        st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["📋 Tableau Pivot", "📈 Graphiques", "💾 Export"])

    with tab1:
        col1, col2 = st.columns(2)
        with col1:
            f_art = st.multiselect("🔍 Article SERTA",
                options=sorted(df['REF_ARTICLE_SERTA'].unique()) if 'REF_ARTICLE_SERTA' in df.columns else [],
                key="f_art")
        with col2:
            f_up = st.multiselect("🔍 UP",
                options=sorted(df['UP_PRINCIPALE'].dropna().unique()) if 'UP_PRINCIPALE' in df.columns else [],
                key="f_up")

        df_disp = df.copy()
        if f_art: df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].isin(f_art)]
        if f_up:  df_disp = df_disp[df_disp['UP_PRINCIPALE'].isin(f_up)]
        # Filtrer les colonnes semaines selon la plage date_filtre_du / date_filtre_au
        if 'date_filtre_du' in st.session_state and 'date_filtre_au' in st.session_state:
            fdu = st.session_state['date_filtre_du']
            fau = st.session_state['date_filtre_au']
            def wk_in_range(col):
                # S26-09 → année=2026, semaine=09
                try:
                    yy, ww = int('20'+col[1:3]), int(col[4:6])
                    import datetime as dt
                    d = dt.date.fromisocalendar(yy, ww, 1)
                    return fdu <= d <= fau
                except:
                    return True
            visible_wk = [c for c in wk_cols if wk_in_range(c)]
        else:
            visible_wk = wk_cols

        col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
        meta_display = [c for c in df_disp.columns if c not in wk_cols]
        st.dataframe(df_disp[meta_display + visible_wk], width='stretch', height=600, column_config=col_cfg)

    with tab2:
        if wk_cols and 'REF_ARTICLE_SERTA' in df.columns:
            # QTY totale ventilée par semaine
            totals = df[wk_cols].sum()
            fig = px.bar(x=totals.index, y=totals.values,
                         title="QTY ventilée par semaine",
                         labels={'x': 'Semaine', 'y': 'QTY'},
                         color_discrete_sequence=['#375623'])
            st.plotly_chart(fig, width="stretch")

        if 'QTE_TOTALE' in df.columns:
            top10 = df.nlargest(10, 'QTE_TOTALE')[['REF_ARTICLE_SERTA','QTE_TOTALE']]
            fig2 = px.bar(top10, x='QTE_TOTALE', y='REF_ARTICLE_SERTA',
                          orientation='h', title="Top 10 Articles par QTY totale",
                          color_discrete_sequence=['#1F4E79'])
            st.plotly_chart(fig2, width="stretch")

    with tab3:
        c1, c2 = st.columns(2)
        with c1:
            csv = df_disp.to_csv(index=False, encoding='utf-8-sig', sep=';')
            st.download_button("📥 CSV", data=csv,
                file_name=f"pivot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", width="stretch")
        with c2:
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as w:
                df_disp.to_excel(w, index=False, sheet_name='Pivot')
            st.download_button("📥 Excel", data=buf.getvalue(),
                file_name=f"pivot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch")

else:
    st.info("👈 Sélectionnez un programme et cliquez sur **LANCER**")