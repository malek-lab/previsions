import streamlit as st
import pandas as pd
import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df

st.title("📂 Import Manuel — Programmes Hors Lasernet")

if get_engine() is None:
    st.stop()

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "Cette page permet d'importer des programmes clients "
        "qui ne sont pas dans Lasernet.\n\n"
        "Le fichier doit avoir le même format que l'export pivot "
        "(colonnes `Programme`, `Article SERTA`, dates en colonnes semaines).\n\n"
        "Les données importées seront ajoutées au pivot et traitées "
        "comme des programmes LPC dans la page Agrégation."
    )
    if st.button("🗑️ Vider l'import", use_container_width=True):
        st.session_state.pop('df_manuels', None)
        st.rerun()

# ── Schéma de transformation ──────────────────────────────────────────────────
META_RENAME = {
    'Programme':                'CODE_CLIENT',
    'Article SERTA':            'REF_ARTICLE_SERTA',
    'Article client':           'REF_ARTICLE_CLIENT',
    'Code sélection':           'CODE_SELECTION',
    'Code signal':              'CODE_SIGNAL',
    'Système de commande':      'LIBELLE_SYSTEME_COMMANDE',
    'Qté ordre mini':           'QTE_ORDRE_MINI',
    'Qté UC':                   'QTE_UC',
    'UP':                       'UP_PRINCIPALE',
    'Qté MOQ':                  'QTE_MOQ',
    'Prix MOQ':                 'PRIX_MOQ',
    'Prix Colonné':             'PRIX_COLONNE',
    'Horizon programme':        'HORIZON_PROGRAMME',
    'Cadencement':              'CADENCEMENT',
    'QTE EN TRANSITE RETARD':   'QTE_EN_TRANSITE_RETARD',
    'QTE BESOIN CLIENT RETARD': 'QTE_BESOIN_CLIENT_RETARD',
    'QTE CUTOFF RETARD':        'QTE_CUTOFF_RETARD',
    'QTE FACTUREE ENCOURS':     'QTE_FACTUREE_ENCOURS',
    'QTE EN TRANSITE ENCOURS':  'QTE_EN_TRANSITE_ENCOURS',
    'QTE BESOIN CLIENT ENCOURS':'QTE_BESOIN_CLIENT_ENCOURS',
    'QTE CUTOFF PREVISION':     'QTE_CUTOFF_PREVISION',
    'DATE BORN GAUCHE':         'DATE_BORN_GAUCHE',
    'DATE BORN DROIT':          'DATE_BORN_DROIT',
    'Customer - P/N pair':      'NOM_FICHIER_PROGRAMME_CLIENT',
}

def date_to_iso(d):
    try:
        iso = d.isocalendar()
        return f"S{str(iso[0])[2:]}-{iso[1]:02d}"
    except:
        return None

def transformer_fichier(df_raw):
    """Transforme le fichier hors Lasernet au format interne du pivot."""
    df = df_raw.copy()

    # Renommer colonnes dates en semaines ISO
    rename_map = {}
    for col in df.columns:
        if isinstance(col, dt.datetime):
            iso = date_to_iso(col)
            if iso:
                rename_map[col] = iso
        elif isinstance(col, str) and ('2026' in col or '2027' in col):
            try:
                d = pd.to_datetime(col)
                iso = date_to_iso(d)
                if iso:
                    rename_map[col] = iso
            except:
                pass
    df = df.rename(columns=rename_map)

    # Renommer colonnes méta
    df = df.rename(columns={k: v for k, v in META_RENAME.items() if k in df.columns})

    # Forcer tout en string pour éviter la perte des zéros (0081 → 81)
    for col_str in ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT']:
        if col_str in df.columns:
            df[col_str] = df[col_str].astype(str).str.strip().replace('nan', '')

    # Vérifier colonnes obligatoires
    if 'CODE_CLIENT' not in df.columns:
        raise ValueError("Colonne 'Programme' introuvable — vérifiez le format du fichier")
    if 'REF_ARTICLE_SERTA' not in df.columns:
        raise ValueError("Colonne 'Article SERTA' introuvable — vérifiez le format du fichier")

    # Nettoyer
    df['CODE_CLIENT'] = df['CODE_CLIENT'].astype(str).str.strip().replace('nan', '')
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip().replace('nan', '')

    # Supprimer lignes sans ref article
    df = df[df['REF_ARTICLE_SERTA'].ne('') & df['REF_ARTICLE_SERTA'].ne('nan')]
    df = df[df['CODE_CLIENT'].ne('') & df['CODE_CLIENT'].ne('nan')]

    # Construire PROGRAMME et NOM_FICHIER comme procédure LPC
    df['PROGRAMME'] = df['CODE_CLIENT']
    df['NOM_FICHIER_PROGRAMME_CLIENT'] = (
        df['CODE_CLIENT'] + '_' + df['REF_ARTICLE_SERTA']
    )
    df['ORIGINE'] = 'MANUEL'

    # Colonnes texte
    for col in ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT',
                'CODE_SELECTION', 'CODE_SIGNAL', 'UP_PRINCIPALE',
                'PROGRAMME', 'NOM_FICHIER_PROGRAMME_CLIENT', 'ORIGINE']:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)

    # Colonnes numériques semaines
    wk_cols = [c for c in df.columns
               if isinstance(c, str) and len(c) == 6
               and c[0] == 'S' and c[3] == '-'
               and c[1:3].isdigit() and c[4:6].isdigit()]
    for c in wk_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    # Colonnes numériques méta
    for col in ['QTE_UC', 'QTE_MOQ', 'QTE_ORDRE_MINI']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df, wk_cols

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "📁 Uploader le(s) fichier(s) Hors Lasernet (.xlsx)",
    type=['xlsx', 'xls'],
    accept_multiple_files=True,
    help="Même format que l'export pivot — colonnes Programme, Article SERTA, dates en colonnes. Vous pouvez uploader plusieurs fichiers en même temps."
)

if uploaded_files:
    try:
        frames = []
        errors = []
        for f in uploaded_files:
            try:
                df_raw = pd.read_excel(f, header=0, dtype=str)
                df_t, _ = transformer_fichier(df_raw)
                df_t['_SOURCE_FICHIER'] = f.name
                frames.append(df_t)
                st.success(f"✅ {f.name} — {len(df_t)} programmes")
            except Exception as e:
                errors.append(f"❌ {f.name} : {e}")

        for err in errors:
            st.error(err)

        if not frames:
            st.stop()

        df_transformed = pd.concat(frames, ignore_index=True, sort=False)
        wk_cols = wk_cols_from_df(df_transformed)
        for c in wk_cols:
            df_transformed[c] = pd.to_numeric(df_transformed[c], errors='coerce').fillna(0)

        st.success(f"✅ Total — {len(df_transformed)} programmes, {len(wk_cols)} semaines ({wk_cols[0] if wk_cols else '?'} → {wk_cols[-1] if wk_cols else '?'})")

        # Aperçu
        st.subheader("👁️ Aperçu")
        meta_preview = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT',
                        'UP_PRINCIPALE', 'ORIGINE']
        meta_preview = [c for c in meta_preview if c in df_transformed.columns]

        st.caption(f"{len(df_transformed):,} lignes — {len(wk_cols)} semaines")
        col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
        st.dataframe(
            df_transformed[meta_preview + wk_cols],
            width='stretch',
            height=min(len(df_transformed) * 35 + 50, 1200),
            column_config=col_cfg
        )

        # Export Excel
        from io import BytesIO
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df_transformed[meta_preview + wk_cols].to_excel(w, index=False, sheet_name='Import Manuel')
        st.download_button(
            "📥 Télécharger en Excel",
            data=buf.getvalue(),
            file_name="apercu_import_manuel.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        # Vérifier doublons avec df_pivot si disponible
        if 'df_pivot' in st.session_state:
            df_lpc = st.session_state['df_pivot']
            refs_lpc = set(
                df_lpc['PROGRAMME'].astype(str).str.split('_').str[0].str.strip() + '|' +
                df_lpc['REF_ARTICLE_SERTA'].astype(str).str.strip()
                if 'PROGRAMME' in df_lpc.columns
                else []
            )
            refs_manuels = set(
                df_transformed['CODE_CLIENT'].astype(str) + '|' +
                df_transformed['REF_ARTICLE_SERTA'].astype(str)
            )
            doublons = refs_manuels & refs_lpc
            if doublons:
                st.warning(f"⚠️ {len(doublons)} couple(s) déjà présents dans le pivot LPC — ils seront quand même ajoutés avec ORIGINE=MANUEL")
        else:
            st.info("ℹ️ Lancez d'abord la page **Pivot Prévision** pour croiser avec les LPC existants")

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Valider l'import", type="primary", use_container_width=True):
                st.session_state['df_manuels'] = df_transformed
                st.success(f"✅ {len(df_transformed)} programmes prêts — allez sur la page **📦 Agrégation** et cliquez sur **Charger / Actualiser carnet**")
        with col2:
            if st.button("🗑️ Annuler", use_container_width=True):
                st.session_state.pop('df_manuels', None)
                st.rerun()

    except Exception as e:
        st.error(f"❌ Erreur lecture fichier : {e}")
        import traceback
        with st.expander("Détails"):
            st.code(traceback.format_exc())

# ── Statut actuel ─────────────────────────────────────────────────────────────
st.markdown("---")
if 'df_manuels' in st.session_state:
    df_m = st.session_state['df_manuels']
    wk_m = wk_cols_from_df(df_m)
    st.success(f"📦 Import actif — {len(df_m)} programmes, {len(wk_m)} semaines")
    clients = df_m['CODE_CLIENT'].value_counts().head(5).to_dict()
    st.caption(f"Clients : {clients}")

    if st.button("📋 Voir détail complet", use_container_width=True):
        meta_cols = [c for c in df_m.columns if c not in wk_m]
        col_cfg_m = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_m}
        st.dataframe(
            df_m[meta_cols + wk_m],
            width='stretch',
            height=min(len(df_m) * 35 + 50, 1200),
            column_config=col_cfg_m
        )
else:
    st.info("Aucun import actif — uploadez un fichier pour commencer")