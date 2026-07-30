import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar

st.title("📈 PIC — Dashboard")

if get_engine() is None:
    st.stop()

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "Dashboard PIC — mêmes graphes que le Power BI.\n\n"
        "👈 Lancez d'abord la page **📅 PIC Mensuel** et cliquez sur **Générer**."
    )

COL_GROUPE = 'SERTA_SO_CLIENT_GROUP_NAME'
COL_REF    = 'REF_ARTICLE_SERTA'

# AJOUT : mapping explicite anglais->numero de mois, independant de la locale
# systeme -- meme correctif que celui applique dans 06_pic.py. strptime/strftime
# avec '%b-%y' echouent silencieusement (caches par des except: pass) si le
# serveur est configure dans une langue non-anglaise.
import datetime as _dtt
MOIS_ABBREV_EN = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                   'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}

def _parse_month_label(ml):
    try:
        abbrev, yy = str(ml).split('-')
        mm = MOIS_ABBREV_EN[abbrev]
        return _dtt.date(2000 + int(yy), mm, 1)
    except (KeyError, ValueError):
        return None

def _mlbl(ym):
    try:
        d = _dtt.datetime.strptime(ym, '%Y-%m')
        abbrev = [k for k,v in MOIS_ABBREV_EN.items() if v == d.month][0]
        return f"{abbrev}-{str(d.year)[2:]}"
    except:
        return ym

def _msort(lbl):
    d = _parse_month_label(lbl)
    return _dtt.date(2099, 1, 1) if d is None else d

# ── Source de données ─────────────────────────────────────────────────────────
# CORRIGE : priorite reorganisee -- df_consolide_final (resultat de la page 04)
# devient la source principale, PAS df_03 (page 03, avant consolidation).
# Avant ce correctif, ce dashboard recalculait tout depuis df_03 directement,
# ignorant totalement les choix faits en page 04 (case "Filtrer les
# PDR/composants", case "Inclure les Nouveaux Projets") et sa logique de
# consolidation/anti-doublon (calc_origine, combinaison LPC/CARNET...) --
# les chiffres du dashboard pouvaient donc diverger silencieusement de ceux
# du Consolide et du PIC Mensuel, sans lien avec les reglages choisis.
if 'df_pic' in st.session_state:
    df_pic     = st.session_state['df_pic'].copy()
    mois_tries = st.session_state.get('df_pic_mois', [])
elif 'df_consolide_final' in st.session_state or 'df_03' in st.session_state:
    if 'df_consolide_final' in st.session_state:
        df_raw = st.session_state['df_consolide_final'].copy()
        _source_msg = "la page **Consolidée** (reflète vos filtres PDR/Projets)"
    else:
        df_raw = st.session_state['df_03'].copy()
        _source_msg = "**df_03** (Agrégation) -- lancez la page **Consolidée** pour refléter vos filtres PDR/Projets"

    wk_raw = [c for c in df_raw.columns if isinstance(c,str) and len(c)==6
              and c[0]=='S' and c[3]=='-' and c[1:3].isdigit() and c[4:6].isdigit()]
    def _wk2m(lbl):
        try:
            yy,ww = int('20'+lbl[1:3]), int(lbl[4:6])
            return _dtt.date.fromisocalendar(yy,ww,1).strftime('%Y-%m')
        except: return None
    mois_map = {}
    for wk in wk_raw:
        m = _wk2m(wk)
        if m: mois_map.setdefault(m,[]).append(wk)
    mois_tries = sorted(mois_map.keys())
    for c in wk_raw:
        df_raw[c] = pd.to_numeric(df_raw[c], errors='coerce').fillna(0)
    if 'PRIX_MOQ' in df_raw.columns:
        df_raw['PRIX_MOQ'] = pd.to_numeric(df_raw['PRIX_MOQ'], errors='coerce').fillna(0)
    rows = []
    grp_cols_def = [c for c in [COL_GROUPE,'REF_ARTICLE_SERTA'] if c in df_raw.columns]
    for c in grp_cols_def:
        df_raw[c] = df_raw[c].fillna('').astype(str)
    for keys, grp in df_raw.groupby(grp_cols_def, sort=False):
        if not isinstance(keys, tuple): keys = (keys,)
        base = dict(zip(grp_cols_def, keys))
        prix = float(grp['PRIX_MOQ'].iloc[0]) if 'PRIX_MOQ' in grp.columns else 0
        for mois in mois_tries:
            wks = [w for w in mois_map[mois] if w in grp.columns]
            qty = float(grp[wks].sum().sum()) if wks else 0
            rows.append({**base,'MOIS':mois,'MOIS_LABEL':_mlbl(mois),'QTY':qty,'CA':round(qty*prix,2)})
    df_pic = pd.DataFrame(rows)
    st.info(f"ℹ️ Données calculées depuis {_source_msg}.")
else:
    # Charger depuis SQL
    try:
        from sqlalchemy import text as _t
        engine = get_engine()
        with engine.connect() as conn:
            df_pic = pd.read_sql(_t("SELECT * FROM [master].[dbo].[T_PIC_MENSUEL]"), conn)
        mois_tries = sorted(df_pic['ANNEE_MOIS'].dropna().unique().tolist()) if 'ANNEE_MOIS' in df_pic.columns else []
        # Harmoniser noms colonnes
        if 'GROUPE_CLIENT' in df_pic.columns and COL_GROUPE not in df_pic.columns:
            df_pic = df_pic.rename(columns={'GROUPE_CLIENT': COL_GROUPE})
        if 'ANNEE_MOIS' in df_pic.columns and 'MOIS' not in df_pic.columns:
            df_pic['MOIS'] = df_pic['ANNEE_MOIS']
        if 'MOIS_LABEL' not in df_pic.columns and 'MOIS' in df_pic.columns:
            df_pic['MOIS_LABEL'] = df_pic['MOIS'].apply(lambda x: _mlbl(str(x)) if x else '')
        st.info(f"ℹ️ Données chargées depuis T_PIC_MENSUEL — {len(df_pic):,} lignes")
    except Exception as e:
        st.warning(f"⚠️ Aucune donnée disponible. Lancez d'abord la page **📅 PIC Mensuel** ou vérifiez la table T_PIC_MENSUEL. ({e})")
        st.stop()

if df_pic is None or df_pic.empty:
    st.warning("⚠️ Aucune donnée à afficher.")
    st.stop()

# ── Filtre groupe client — indépendant ────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.subheader("🔍 Filtres")
    if COL_GROUPE in df_pic.columns:
        groupes = sorted([g for g in df_pic[COL_GROUPE].dropna().astype(str).unique() if g.strip()])
        f_grp = st.multiselect("🏢 Groupe client", options=groupes, default=groupes,
                                key="dash_grp")
        if f_grp:
            df_pic = df_pic[df_pic[COL_GROUPE].astype(str).isin(f_grp)]

    if 'MOIS_LABEL' in df_pic.columns:
        mois_dispo = sorted(df_pic['MOIS_LABEL'].dropna().unique(), key=_msort)
        f_mois = st.multiselect("📅 Mois", options=mois_dispo, default=mois_dispo,
                                 key="dash_mois")
        if f_mois:
            df_pic = df_pic[df_pic['MOIS_LABEL'].isin(f_mois)]

# ── Métriques ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("QTY Totale",      f"{int(df_pic['QTY'].sum()):,}")
if 'CA' in df_pic.columns:
    c2.metric("CA Total (€)", f"{df_pic['CA'].sum():,.0f}")
if COL_GROUPE in df_pic.columns:
    c3.metric("Groupes clients", df_pic[COL_GROUPE].nunique())
c4.metric("Mois", len(mois_tries))

st.markdown("---")

# ── Graphes ───────────────────────────────────────────────────────────────────
col_g1, col_g2 = st.columns(2)

# Donut — Customer Weight (QTY)
with col_g1:
    if COL_GROUPE in df_pic.columns:
        df_donut = df_pic.groupby(COL_GROUPE)['QTY'].sum().reset_index()
        df_donut = df_donut[df_donut['QTY'] > 0].sort_values('QTY', ascending=False)
        fig_donut = px.pie(
            df_donut, values='QTY', names=COL_GROUPE,
            title="🍩 Customer Weight (QTY)",
            hole=0.45,
            color_discrete_sequence=px.colors.qualitative.Set2
        )
        fig_donut.update_traces(textposition='inside', textinfo='percent+label')
        fig_donut.update_layout(showlegend=True, height=400)
        st.plotly_chart(fig_donut, use_container_width=True)

# Pie — Turnover / CA
with col_g2:
    if 'CA' in df_pic.columns and COL_GROUPE in df_pic.columns:
        df_pie = df_pic.groupby(COL_GROUPE)['CA'].sum().reset_index()
        df_pie = df_pie[df_pie['CA'] > 0].sort_values('CA', ascending=False)
        fig_pie = px.pie(
            df_pie, values='CA', names=COL_GROUPE,
            title="🥧 Turnover par groupe client (€)",
            color_discrete_sequence=px.colors.qualitative.Pastel
        )
        fig_pie.update_traces(textposition='inside', textinfo='percent+label')
        fig_pie.update_layout(showlegend=True, height=400)
        st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("---")

# Bar — QTY par mois (empilé par groupe client)
if COL_GROUPE in df_pic.columns and 'MOIS_LABEL' in df_pic.columns:
    df_bar_qty = df_pic.groupby([COL_GROUPE, 'MOIS_LABEL'])['QTY'].sum().reset_index()
    # Trier les mois chronologiquement (fonction _msort deja definie en haut du fichier)
    mois_sorted = sorted(df_bar_qty['MOIS_LABEL'].unique(), key=_msort)
    df_bar_qty['MOIS_LABEL'] = pd.Categorical(df_bar_qty['MOIS_LABEL'], categories=mois_sorted, ordered=True)
    df_bar_qty = df_bar_qty.sort_values('MOIS_LABEL')

    fig_qty = px.bar(
        df_bar_qty, x='MOIS_LABEL', y='QTY', color=COL_GROUPE,
        title="📊 QTY par mois par groupe client",
        barmode='stack',
        color_discrete_sequence=px.colors.qualitative.Set2,
        labels={'MOIS_LABEL': 'Mois', 'QTY': 'Quantité', COL_GROUPE: 'Groupe client'}
    )
    fig_qty.update_layout(height=450, xaxis_tickangle=-45)
    st.plotly_chart(fig_qty, use_container_width=True)

# Bar — CA par mois (empilé par groupe client)
if 'CA' in df_pic.columns and COL_GROUPE in df_pic.columns:
    df_bar_ca = df_pic.groupby([COL_GROUPE, 'MOIS_LABEL'])['CA'].sum().reset_index()
    df_bar_ca['MOIS_LABEL'] = pd.Categorical(df_bar_ca['MOIS_LABEL'], categories=mois_sorted, ordered=True)
    df_bar_ca = df_bar_ca.sort_values('MOIS_LABEL')

    fig_ca = px.bar(
        df_bar_ca, x='MOIS_LABEL', y='CA', color=COL_GROUPE,
        title="💶 CA par mois par groupe client (€)",
        barmode='stack',
        color_discrete_sequence=px.colors.qualitative.Pastel,
        labels={'MOIS_LABEL': 'Mois', 'CA': 'CA (€)', COL_GROUPE: 'Groupe client'}
    )
    fig_ca.update_layout(height=450, xaxis_tickangle=-45)
    st.plotly_chart(fig_ca, use_container_width=True)

st.markdown("---")

# Tableau récap par groupe client
st.subheader("📋 Récapitulatif par groupe client")
if COL_GROUPE in df_pic.columns:
    df_recap = df_pic.groupby(COL_GROUPE).agg(
        QTY_TOTALE=('QTY', 'sum'),
        **({'CA_TOTAL': ('CA', 'sum')} if 'CA' in df_pic.columns else {})
    ).reset_index().sort_values('QTY_TOTALE', ascending=False)
    df_recap['QTY_TOTALE'] = df_recap['QTY_TOTALE'].astype(int)
    if 'CA_TOTAL' in df_recap.columns:
        df_recap['CA_TOTAL'] = df_recap['CA_TOTAL'].round(0).astype(int)
    st.dataframe(df_recap, width='stretch', height=min(len(df_recap)*35+50, 400))