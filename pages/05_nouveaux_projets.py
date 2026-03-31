import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date
import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.title("🚀 Nouveaux Projets — Intégration PIC")

if get_engine() is None:
    st.stop()

# ── Helpers ───────────────────────────────────────────────────────────────────
def semaine_s1_du_mois(annee, mois):
    """
    Retourne le label ISO de la première semaine COMPLÈTE du mois.
    Si la S1 du mois commence en mois-1 (semaine coupée), on prend la semaine suivante.
    Ex: si lundi de la S26-14 est en mars → on décale à S26-15 pour avril.
    """
    try:
        d = dt.date(annee, mois, 1)
        iso = d.isocalendar()
        # Lundi de cette semaine ISO
        lundi = dt.date.fromisocalendar(iso[0], iso[1], 1)
        # Si le lundi est dans le mois précédent → semaine coupée → décaler d'une semaine
        if lundi.month != mois:
            lundi = lundi + dt.timedelta(weeks=1)
            iso = lundi.isocalendar()
        return f"S{str(iso[0])[2:]}-{iso[1]:02d}"
    except:
        return None

def mois_offset_to_date(offset, date_ref):
    """1er du mois = date_ref + offset mois."""
    try:
        mois  = date_ref.month + offset
        annee = date_ref.year + (mois - 1) // 12
        mois  = ((mois - 1) % 12) + 1
        return dt.date(annee, mois, 1)
    except:
        return None

def calculer_qty_mois(month_0, month_6, month_12, month_18, num_month):
    """
    Recrée la logique P_R_PIC :
    - NUM_SEMESTER = (num_month // 6) + 1
    - NUM_MONTH_IN_SEMESTER = num_month % 6
    - QTY source selon le semestre
    """
    num_semester = (num_month // 6) + 1
    num_in_sem   = num_month % 6

    qty_source = {1: month_0, 2: month_6, 3: month_12, 4: month_18}.get(num_semester, 0) or 0

    if num_in_sem == 0:
        if 50 <= qty_source <= 199: return qty_source // 2
        if qty_source >= 200:       return qty_source // 6
        return qty_source
    elif num_in_sem == 3:
        if 50 <= qty_source <= 199: return qty_source // 2
        if qty_source >= 200:       return qty_source // 6
        return 0
    else:  # 1, 2, 4, 5
        if qty_source >= 200: return qty_source // 6
        return 0

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Paramètres")
    st.markdown("---")

    date_ref = st.date_input(
        "📅 Date de référence (mois 0)",
        value=st.session_state.get('date_prevision', datetime.now().date()),
        format="DD/MM/YYYY",
        help="Correspond à la date de prévision de la page 01. Mois 0 = ce mois."
    )
    annee_n1 = date_ref.year + 1
    date_au  = dt.date(annee_n1, 12, 31)
    st.info(f"Plage : {date_ref.strftime('%d/%m/%Y')} → 31/12/{annee_n1}")

    st.markdown("---")
    btn_charger = st.button("🔄 Charger les projets", type="primary", width="stretch")

# ── Chargement depuis V_NOUVEAUX_PROJETS ──────────────────────────────────────
@st.cache_data(ttl=300)
def charger_projets_bruts():
    from sqlalchemy import text
    engine = get_engine()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT
                    PRJ_ID, NUM_PROJET, REF_ARTICLE_SERTA, CODE_SELECTION,
                    DATE_DEBUT_SERIE, UP_PRINCIPALE, BUSINESS_NAME,
                    CODE_CLIENT, SERTA_SO_CLIENT_NAME, SERTA_SO_CLIENT_GROUP_NAME,
                    PROJECT_MANAGER, SALES_PERSON, STATUT,
                    DATE_LIVRAISON_SERIE, SUCCESS_RATE, QTE_ANNUELLE,
                    MONTH_0_QTY, MONTH_6_QTY, MONTH_12_QTY, MONTH_18_QTY,
                    DATE_DEBUT_SERIE_CALC
                FROM [master].[dbo].[V_NOUVEAUX_PROJETS]
            """), conn)
        return df
    except Exception as e:
        st.error(f"Erreur chargement projets : {e}")
        return pd.DataFrame()

def ventiler_vers_semaines(df_raw, date_ref, date_au):
    """
    Recrée la logique P_R_PIC en Python :
    - Calcule QTY pour chaque mois 0..31
    - Décale selon DATEDIFF entre GETDATE() et DATE_DEBUT_SERIE_CALC
    - Rabat la quantité sur la S1 du mois
    - Garde uniquement les semaines dans [date_ref, date_au]
    """
    META_COLS = ['PRJ_ID', 'NUM_PROJET', 'REF_ARTICLE_SERTA', 'CODE_SELECTION',
                 'DATE_DEBUT_SERIE', 'UP_PRINCIPALE', 'BUSINESS_NAME',
                 'CODE_CLIENT', 'SERTA_SO_CLIENT_NAME', 'SERTA_SO_CLIENT_GROUP_NAME',
                 'PROJECT_MANAGER', 'SALES_PERSON', 'STATUT',
                 'DATE_LIVRAISON_SERIE', 'SUCCESS_RATE', 'QTE_ANNUELLE']
    META_COLS = [c for c in META_COLS if c in df_raw.columns]

    today = dt.date.today()
    rows  = []

    for _, row in df_raw.iterrows():
        base = {c: row[c] for c in META_COLS}
        base['ORIGINE'] = 'PROJET'

        # Calculer le décalage en mois entre aujourd'hui et DATE_DEBUT_SERIE_CALC
        date_serie = pd.to_datetime(row.get('DATE_DEBUT_SERIE_CALC'), errors='coerce')
        if pd.isna(date_serie):
            continue
        date_serie = date_serie.date()
        decalage   = (date_serie.year - today.year) * 12 + (date_serie.month - today.month)

        def _si(v):
            try:
                f = float(v)
                return 0 if f != f else int(f)
            except:
                return 0
        m0  = _si(row.get('MONTH_0_QTY',  0))
        m6  = _si(row.get('MONTH_6_QTY',  0))
        m12 = _si(row.get('MONTH_12_QTY', 0))
        m18 = _si(row.get('MONTH_18_QTY', 0))

        for num_month in range(32):
            qty = calculer_qty_mois(m0, m6, m12, m18, num_month)
            if qty == 0:
                continue

            # Mois cible = num_month + décalage (depuis aujourd'hui)
            mois_cible = num_month + decalage
            if mois_cible < 0:
                continue

            d_mois = mois_offset_to_date(mois_cible, dt.date(today.year, today.month, 1))
            if d_mois is None:
                continue
            if d_mois < date_ref or d_mois > date_au:
                continue

            semaine = semaine_s1_du_mois(d_mois.year, d_mois.month)
            if semaine:
                base[semaine] = base.get(semaine, 0) + qty

        rows.append(base)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Forcer colonnes texte
    for col in ['NUM_PROJET', 'REF_ARTICLE_SERTA', 'CODE_SELECTION', 'UP_PRINCIPALE',
                'BUSINESS_NAME', 'CODE_CLIENT', 'SERTA_SO_CLIENT_NAME',
                'SERTA_SO_CLIENT_GROUP_NAME', 'PROJECT_MANAGER', 'SALES_PERSON',
                'STATUT', 'ORIGINE']:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)

    wk = wk_cols_from_df(df)
    for c in wk:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    return df

# ── Bouton charger ────────────────────────────────────────────────────────────
if btn_charger:
    with st.spinner("⏳ Chargement et ventilation des projets..."):
        df_raw   = charger_projets_bruts()
        if df_raw.empty:
            st.warning("⚠️ Aucun projet retourné.")
        else:
            df_pivot = ventiler_vers_semaines(df_raw, date_ref, date_au)
            if df_pivot.empty:
                st.warning("⚠️ Aucun projet dans la plage de dates.")
            else:
                st.session_state['df_projets_raw'] = df_pivot
                st.success(f"✅ {len(df_pivot)} projets chargés et ventilés")

if 'df_projets_raw' not in st.session_state:
    st.info("👈 Cliquez sur **🔄 Charger les projets**")
    st.stop()

df_proj = st.session_state['df_projets_raw'].copy()

# ── Séparer nouvelles refs et refs existantes dans la consolidée ──────────────
refs_existantes = set()
if 'df_03' in st.session_state:
    refs_existantes = set(
        st.session_state['df_03']['REF_ARTICLE_SERTA'].dropna().astype(str).unique()
    )

df_proj['REF_ARTICLE_SERTA'] = df_proj['REF_ARTICLE_SERTA'].astype(str)
df_proj['_DANS_CONSOLIDE']   = df_proj['REF_ARTICLE_SERTA'].isin(refs_existantes)

df_nouveaux  = df_proj[~df_proj['_DANS_CONSOLIDE']].drop(columns=['_DANS_CONSOLIDE'])
df_existants = df_proj[df_proj['_DANS_CONSOLIDE']].drop(columns=['_DANS_CONSOLIDE'])

META_PROJ = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'NUM_PROJET', 'ORIGINE', 'CODE_SELECTION',
             'UP_PRINCIPALE', 'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME',
             'SALES_PERSON', 'STATUT', 'DATE_LIVRAISON_SERIE', 'SUCCESS_RATE', 'QTE_ANNUELLE']
META_PROJ = [c for c in META_PROJ if c in df_proj.columns]
wk_cols   = sorted(wk_cols_from_df(df_proj))

# ── Métriques ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Projets totaux",            len(df_proj))
c2.metric("Refs nouvelles",            len(df_nouveaux))
c3.metric("Refs déjà en consolidée",   len(df_existants))
c4.metric("Semaines",                  len(wk_cols))
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

# ── Section 1 : Nouvelles refs ────────────────────────────────────────────────
st.subheader("✅ Nouvelles références — absentes de la consolidée")
st.caption("Ces refs seront ajoutées directement.")

if df_nouveaux.empty:
    st.info("Aucune nouvelle référence.")
else:
    col_cfg  = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
    wk_pres  = [c for c in wk_cols if c in df_nouveaux.columns]
    meta_pres = [c for c in META_PROJ if c in df_nouveaux.columns]
    st.dataframe(df_nouveaux[meta_pres + wk_pres], width='stretch', height=300,
                 column_config=col_cfg)

st.markdown("---")

# ── Section 2 : Refs existantes → case à cocher ───────────────────────────────
st.subheader("⚠️ Références déjà présentes dans la consolidée")
st.caption("Cochez **GARDER** pour intégrer ces projets en plus des données existantes.")

if df_existants.empty:
    st.info("Aucune référence en doublon.")
    df_existants_sel = pd.DataFrame()
else:
    df_edit  = df_existants.copy()
    wk_pres_ex   = [c for c in wk_cols if c in df_edit.columns]
    meta_pres_ex = [c for c in META_PROJ if c in df_edit.columns]
    df_edit.insert(0, 'GARDER', False)
    col_cfg_ex = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_pres_ex}
    col_cfg_ex['GARDER'] = st.column_config.CheckboxColumn("Garder ?", default=False)

    edited = st.data_editor(
        df_edit[['GARDER'] + meta_pres_ex + wk_pres_ex],
        column_config=col_cfg_ex,
        use_container_width=True,
        height=300,
        key="editor_existants"
    )
    df_existants_sel = edited[edited['GARDER'] == True].drop(columns=['GARDER'])

# ── Bouton intégrer ───────────────────────────────────────────────────────────
st.markdown("---")
nb_a_integrer = len(df_nouveaux) + (len(df_existants_sel) if not df_existants_sel.empty else 0)
st.info(f"**{nb_a_integrer} projet(s) à intégrer** — {len(df_nouveaux)} nouveaux "
        f"+ {len(df_existants_sel) if not df_existants_sel.empty else 0} doublon(s) conservés")

# Export Excel deux feuilles
def to_excel_deux_feuilles(df1, df2):
    from io import BytesIO
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        if not df1.empty:
            wk1 = sorted(wk_cols_from_df(df1))
            m1  = [c for c in META_PROJ if c in df1.columns]
            df1[[c for c in m1 + wk1 if c in df1.columns]].to_excel(w, index=False, sheet_name='Nouvelles refs')
        if not df2.empty:
            wk2 = sorted(wk_cols_from_df(df2))
            m2  = [c for c in META_PROJ if c in df2.columns]
            df2[[c for c in m2 + wk2 if c in df2.columns]].to_excel(w, index=False, sheet_name='Refs existantes')
    return buf.getvalue()

st.download_button("📥 Export Excel (2 feuilles)",
    data=to_excel_deux_feuilles(df_nouveaux, df_existants),
    file_name=f"nouveaux_projets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch")

if st.button("✅ Intégrer dans la consolidée (page 04)", type="primary",
             disabled=nb_a_integrer == 0, width="stretch"):

    frames = [df_nouveaux]
    if not df_existants_sel.empty:
        frames.append(df_existants_sel)
    df_a_integrer = pd.concat(frames, ignore_index=True, sort=False)

    for col in ['CODE_CLIENT', 'PROGRAMME', 'ORIGINE', 'CODE_SELECTION']:
        if col in df_a_integrer.columns:
            df_a_integrer[col] = df_a_integrer[col].fillna('').astype(str)
    # PROGRAMME vide pour les projets (pas de programme LPC)
    df_a_integrer['PROGRAMME'] = ''
    # Semaines numériques
    for _c in wk_cols_from_df(df_a_integrer):
        df_a_integrer[_c] = pd.to_numeric(df_a_integrer[_c], errors='coerce').fillna(0)

    st.session_state['df_projets_a_integrer'] = df_a_integrer
    st.success(f"✅ {len(df_a_integrer)} projets prêts — allez sur la page **📊 Consolidée** pour finaliser et exporter.")