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

# AJOUT : aller-retour telecharger / reimporter -- deplace ici (APRES les
# metriques principales, dans un expander replie par defaut) plutot qu'en
# toute premiere chose visible sur la page -- c'est une fonctionnalite
# avancee/optionnelle, elle ne doit pas passer avant l'apercu general.
with st.expander("📤 Éditer hors-ligne (optionnel) — télécharger, modifier dans Excel, réimporter"):
    st.download_button(
        "📥 Télécharger le tableau chargé",
        data=to_excel_bytes(df_proj),
        file_name=f"nouveaux_projets_{dt.date.today().strftime('%Y%m%d')}.xlsx",
        help="Édite/complète une colonne dans Excel (ex: une quantité manquante), "
             "puis réimporte le fichier ci-dessous pour qu'il remplace ce tableau."
    )
    st.markdown("")  # petit espace vertical entre les deux widgets
    fichier_reimporte = st.file_uploader(
        "📤 Réimporter le fichier modifié",
        type=['xlsx'],
        help="Remplace le tableau ci-dessus par la version que tu réimportes ici. "
             "La page se rechargera automatiquement avec les nouvelles données."
    )
    if fichier_reimporte is not None:
        try:
            df_reimporte = pd.read_excel(fichier_reimporte)
            for col in ['NUM_PROJET', 'REF_ARTICLE_SERTA', 'CODE_SELECTION', 'UP_PRINCIPALE',
                        'BUSINESS_NAME', 'CODE_CLIENT', 'SERTA_SO_CLIENT_NAME',
                        'SERTA_SO_CLIENT_GROUP_NAME', 'PROJECT_MANAGER', 'SALES_PERSON',
                        'STATUT', 'ORIGINE']:
                if col in df_reimporte.columns:
                    df_reimporte[col] = df_reimporte[col].fillna('').astype(str)
            for c in wk_cols_from_df(df_reimporte):
                df_reimporte[c] = pd.to_numeric(df_reimporte[c], errors='coerce').fillna(0)
            st.session_state['df_projets_raw'] = df_reimporte
            st.success(f"✅ Fichier réimporté — {len(df_reimporte)} lignes. "
                       f"Rechargement en cours...")
            st.rerun()
        except Exception as e:
            st.error(f"❌ Erreur de lecture du fichier réimporté : {e}")

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
    st.download_button(
        "📥 Télécharger cette table (nouvelles refs)",
        data=to_excel_bytes(df_nouveaux[meta_pres + wk_pres]),
        file_name=f"nouveaux_projets_nouvelles_refs_{dt.date.today().strftime('%Y%m%d')}.xlsx",
        key="dl_nouveaux"
    )

st.markdown("---")

# ── Section 2 : Refs existantes → case à cocher ───────────────────────────────
st.subheader("⚠️ Références déjà présentes dans la consolidée")
st.caption("Cochez **GARDER** pour intégrer ces projets en plus des données existantes.")

if df_existants.empty:
    st.info("Aucune référence en doublon.")
    df_existants_sel = pd.DataFrame()
else:
    # AJOUT : bouton pour repartir de zero -- utile maintenant que l'appli
    # tourne en service permanent (session_state peut rester vivant plusieurs
    # jours si l'onglet navigateur n'est jamais ferme, gardant les cases
    # GARDER cochees d'une fois sur l'autre sans que ce soit voulu).
    if st.button("🔄 Réinitialiser la sélection (décoche tout)"):
        if "editor_existants" in st.session_state:
            del st.session_state["editor_existants"]
        st.rerun()

    # AJOUT : indicateur "deja integre precedemment" -- verifie si la ref
    # existe deja dans st.session_state['df_projets_doublons'], qui n'est
    # rempli que par un clic precedent sur "Integrer" (potentiellement une
    # session anterieure, vu que l'appli tourne maintenant en service
    # permanent). Sans cet indicateur, impossible de savoir si une case
    # cochee vient d'une action d'aujourd'hui ou d'il y a plusieurs jours.
    refs_deja_integrees = set()
    _prev_doublons = st.session_state.get('df_projets_doublons', pd.DataFrame())
    if not _prev_doublons.empty and 'REF_ARTICLE_SERTA' in _prev_doublons.columns:
        refs_deja_integrees = set(_prev_doublons['REF_ARTICLE_SERTA'].astype(str))

    df_edit  = df_existants.copy()
    wk_pres_ex   = [c for c in wk_cols if c in df_edit.columns]
    meta_pres_ex = [c for c in META_PROJ if c in df_edit.columns]
    df_edit.insert(0, 'GARDER', False)
    df_edit.insert(1, 'DEJA_INTEGRE',
        df_edit['REF_ARTICLE_SERTA'].astype(str).isin(refs_deja_integrees)
        .map({True: '⚠️ Session précédente', False: ''}))
    col_cfg_ex = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_pres_ex}
    col_cfg_ex['GARDER'] = st.column_config.CheckboxColumn("Garder ?", default=False)
    col_cfg_ex['DEJA_INTEGRE'] = st.column_config.TextColumn(
        "Déjà intégré ?", help="Indique si cette ref a déjà été intégrée lors d'un clic précédent sur 'Intégrer' (potentiellement une session antérieure)")

    edited = st.data_editor(
        df_edit[['GARDER', 'DEJA_INTEGRE'] + meta_pres_ex + wk_pres_ex],
        column_config=col_cfg_ex,
        width='stretch',
        height=300,
        key="editor_existants"
    )
    df_existants_sel = edited[edited['GARDER'] == True].drop(columns=['GARDER', 'DEJA_INTEGRE'])
    st.download_button(
        "📥 Télécharger cette table (refs existantes)",
        data=to_excel_bytes(edited),
        file_name=f"nouveaux_projets_refs_existantes_{dt.date.today().strftime('%Y%m%d')}.xlsx",
        key="dl_existants"
    )

# ── AJOUT : liste unifiee -- combine nouveaux + doublons coches, pour voir
# d'un coup ce qui sera reellement integre, sans avoir a combiner mentalement
# deux tableaux separes ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 Liste finale à intégrer (nouveaux + doublons cochés)")

_liste_unifiee_parts = []
if not df_nouveaux.empty:
    _tmp = df_nouveaux.copy()
    _tmp['TYPE'] = 'Nouveau'
    _liste_unifiee_parts.append(_tmp)
if not df_existants_sel.empty:
    _tmp2 = df_existants_sel.copy()
    _tmp2['TYPE'] = 'Doublon gardé'
    _liste_unifiee_parts.append(_tmp2)

if _liste_unifiee_parts:
    df_liste_unifiee = pd.concat(_liste_unifiee_parts, ignore_index=True)
    _meta_uni = [c for c in ['TYPE'] + META_PROJ if c in df_liste_unifiee.columns]
    _wk_uni   = [c for c in wk_cols if c in df_liste_unifiee.columns]
    st.dataframe(df_liste_unifiee[_meta_uni + _wk_uni], width='stretch', height=300)
    st.download_button(
        "📥 Télécharger la liste unifiée (ce qui sera intégré)",
        data=to_excel_bytes(df_liste_unifiee[_meta_uni + _wk_uni]),
        file_name=f"nouveaux_projets_liste_unifiee_{dt.date.today().strftime('%Y%m%d')}.xlsx",
        key="dl_unifiee"
    )
else:
    df_liste_unifiee = pd.DataFrame()
    st.info("Rien à intégrer pour l'instant — aucune nouvelle ref, aucun doublon coché.")

# ── Bouton intégrer ───────────────────────────────────────────────────────────
st.markdown("---")
nb_a_integrer = len(df_nouveaux) + (len(df_existants_sel) if not df_existants_sel.empty else 0)
st.info(f"**{nb_a_integrer} projet(s) à intégrer** — {len(df_nouveaux)} nouveaux "
        f"+ {len(df_existants_sel) if not df_existants_sel.empty else 0} doublon(s) conservés")

if st.button("✅ Intégrer dans la consolidée (page 04)", type="primary",
             disabled=nb_a_integrer == 0, width="stretch"):

    def _prep(df_, doublon=False):
        df_ = df_.copy()
        for col in ['CODE_CLIENT', 'PROGRAMME', 'ORIGINE', 'CODE_SELECTION']:
            if col in df_.columns:
                df_[col] = df_[col].fillna('').astype(str)
        if 'NUM_PROJET' in df_.columns:
            df_['PROGRAMME'] = df_['NUM_PROJET'].astype(str)
        df_['ORIGINE'] = 'PROJET'
        df_['DOUBLON'] = doublon
        for _c in wk_cols_from_df(df_):
            df_[_c] = pd.to_numeric(df_[_c], errors='coerce').fillna(0)
        return df_

    # Nouvelles refs → consolidées par ref dans page 04
    st.session_state['df_projets_nouveaux'] = _prep(df_nouveaux, doublon=False) if not df_nouveaux.empty else pd.DataFrame()

    # Doublons gardés → lignes séparées, DOUBLON=True
    if not df_existants_sel.empty:
        st.session_state['df_projets_doublons'] = _prep(df_existants_sel, doublon=True)
    else:
        st.session_state['df_projets_doublons'] = pd.DataFrame()

    # AJOUT : journal detaille -- quelles refs precises ont ete ajoutees, pour
    # tracabilite (sait exactement ce qui a ete integre, pas juste un compte).
    _refs_nouveaux_liste  = df_nouveaux['REF_ARTICLE_SERTA'].tolist() if not df_nouveaux.empty and 'REF_ARTICLE_SERTA' in df_nouveaux.columns else []
    _refs_doublons_liste  = df_existants_sel['REF_ARTICLE_SERTA'].tolist() if not df_existants_sel.empty and 'REF_ARTICLE_SERTA' in df_existants_sel.columns else []

    st.success(f"✅ {nb_a_integrer} projets prêts — allez sur la page **📊 Consolidée** pour finaliser et exporter.")
    with st.expander("📝 Journal — références précises intégrées à cette étape"):
        _lignes_journal = (
            [{"Référence": r, "Type": "Nouveau"} for r in _refs_nouveaux_liste] +
            [{"Référence": r, "Type": "Doublon gardé"} for r in _refs_doublons_liste]
        )
        if _lignes_journal:
            st.caption(f"{len(_refs_nouveaux_liste)} nouvelle(s) + {len(_refs_doublons_liste)} doublon(s) gardé(s)")
            st.dataframe(pd.DataFrame(_lignes_journal), width='stretch', height=300, hide_index=True)
        else:
            st.caption("Aucune référence intégrée.")