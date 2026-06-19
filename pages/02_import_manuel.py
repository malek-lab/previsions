import streamlit as st
import pandas as pd
import datetime as dt
import pyodbc
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.title("📂 Dépôt & Agrégation des Programmes")

if get_engine() is None:
    st.stop()

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "**Workflow :**\n\n"
        "1. Générez votre pivot depuis la page **Pivot Prévision**\n"
        "2. Téléchargez l'Excel, modifiez si besoin\n"
        "3. Déposez ici\n"
        "4. Quand tout le monde a déposé → **Agréger**\n\n"
        "Le fichier consolidé sera utilisé pour la suite du processus."
    )

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=W25-DWDI;DATABASE=master;Trusted_Connection=yes;"
    "Connect Timeout=300;"
)

def get_conn():
    return pyodbc.connect(CONN_STR, timeout=300)

def lister_fichiers():
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ID, Nom_fichier, Utilisateur, Source, DATE_MODIF,
                   DATALENGTH(Fichier)/1024 AS Taille_Ko
            FROM [dbo].[T_PREVISION_FICHIERS]
            ORDER BY DATE_MODIF DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        if rows:
            return pd.DataFrame(
                [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows],
                columns=['ID','Fichier','Utilisateur','Source','Date','Taille_Ko']
            )
        return pd.DataFrame(columns=['ID','Fichier','Utilisateur','Source','Date','Taille_Ko'])
    except Exception as e:
        st.error(f"Erreur lecture : {e}")
        return pd.DataFrame(columns=['ID','Fichier','Utilisateur','Source','Date','Taille_Ko'])

def sauvegarder_fichier(fichier, utilisateur, source):
    try:
        contenu = fichier.read()
        fichier.seek(0)
        conn = get_conn()
        # MERGE — remplace si même nom + même utilisateur
        conn.execute("""
            MERGE [dbo].[T_PREVISION_FICHIERS] AS target
            USING (SELECT ? AS Nom_fichier, ? AS Utilisateur) AS src
            ON (target.Nom_fichier = src.Nom_fichier AND target.Utilisateur = src.Utilisateur)
            WHEN MATCHED THEN UPDATE SET
                Source = ?, DATE_MODIF = GETDATE(), Fichier = ?
            WHEN NOT MATCHED THEN INSERT
                (Nom_fichier, Utilisateur, Source, DATE_MODIF, Fichier)
            VALUES (?, ?, ?, GETDATE(), ?);
        """, (fichier.name, utilisateur, source, contenu,
              fichier.name, utilisateur, source, contenu))
        conn.commit()
        conn.close()
        return True, f"✅ '{fichier.name}' sauvegardé"
    except Exception as e:
        return False, f"❌ Erreur : {e}"

def telecharger_fichier(file_id):
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT Fichier, Nom_fichier FROM [dbo].[T_PREVISION_FICHIERS] WHERE ID=?", file_id)
        row = cursor.fetchone()
        conn.close()
        if row:
            return bytes(row[0]), row[1]
        return None, None
    except Exception as e:
        st.error(f"Erreur téléchargement : {e}")
        return None, None

def supprimer_fichier(file_id):
    try:
        conn = get_conn()
        conn.execute("DELETE FROM [dbo].[T_PREVISION_FICHIERS] WHERE ID=?", file_id)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Erreur suppression : {e}")
        return False

def lire_fichier_excel(contenu_bytes):
    from io import BytesIO
    import datetime as dt2
    df = pd.read_excel(BytesIO(contenu_bytes))

    # 1. Renommer colonnes dates → semaines ISO (ex: 2026-01-19 → S26-03)
    rename_map = {}
    for col in df.columns:
        if isinstance(col, dt2.datetime):
            iso = col.isocalendar()
            rename_map[col] = f"S{str(iso[0])[2:]}-{iso[1]:02d}"
        elif isinstance(col, str):
            try:
                d = pd.to_datetime(col)
                iso = d.isocalendar()
                rename_map[col] = f"S{str(iso[0])[2:]}-{iso[1]:02d}"
            except:
                pass
    df = df.rename(columns=rename_map)

    # 2. Renommer colonnes méta hors Lasernet → format interne
    META_RENAME = {
        'Programme':           'PROGRAMME',
        'Article SERTA':       'REF_ARTICLE_SERTA',
        'Article client':      'REF_ARTICLE_CLIENT',
        'Code sélection':      'CODE_SELECTION',
        'Code signal':         'CODE_SIGNAL',
        'UP':                  'UP_PRINCIPALE',
        'Qté UC':              'QTE_UC',
        'Qté MOQ':             'QTE_MOQ',
        'Comments':            'Commentaire',
        'Horizon programme':   'HORIZON_PROGRAMME',
        'NOM_FICHIER_PROGRAMME_CLIENT': 'PROGRAMME',
    }
    df = df.rename(columns={k: v for k, v in META_RENAME.items() if k in df.columns})

    # 3. Convertir toutes les colonnes en string
    df.columns = [str(c) for c in df.columns]
    return df

def agreger_tous_fichiers():
    df_liste = lister_fichiers()
    if df_liste.empty:
        return None, "Aucun fichier en base"

    frames = []
    for _, row in df_liste.iterrows():
        contenu, nom = telecharger_fichier(row['ID'])
        if contenu:
            try:
                df = lire_fichier_excel(contenu)
                if 'PROGRAMME' not in df.columns and 'NOM_FICHIER_PROGRAMME_CLIENT' in df.columns:
                    df = df.rename(columns={'NOM_FICHIER_PROGRAMME_CLIENT': 'PROGRAMME'})
                df['_SOURCE_FICHIER'] = nom
                df['_UTILISATEUR']    = str(row['Utilisateur'])
                df['_DATE_DEPOT']     = str(row['Date'])
                frames.append(df)
            except Exception as e:
                st.warning(f"⚠️ Impossible de lire '{nom}' : {e}")

    if not frames:
        return None, "Aucun fichier lisible"

    df_all = pd.concat(frames, ignore_index=True, sort=False)
    # S'assurer que toutes les colonnes sont des strings
    df_all.columns = [str(c) for c in df_all.columns]

    wk_cols = wk_cols_from_df(df_all)
    for c in wk_cols:
        df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)

    return df_all, f"✅ {len(frames)} fichiers agrégés — {len(df_all)} lignes"


def sauvegarder_consolide_blob(df, utilisateur, to_excel_fn):
    """Sauvegarde le fichier consolidé comme blob Excel dans T_PREVISION_FICHIERS."""
    try:
        from io import BytesIO
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df.to_excel(w, index=False, sheet_name='Consolide')
        contenu = buf.getvalue()

        conn = get_conn()
        conn.execute("""
            MERGE [dbo].[T_PREVISION_FICHIERS] AS target
            USING (SELECT ? AS Nom_fichier, ? AS Utilisateur) AS src
            ON (target.Nom_fichier = src.Nom_fichier AND target.Utilisateur = src.Utilisateur)
            WHEN MATCHED THEN UPDATE SET
                Source = ?, DATE_MODIF = GETDATE(), Fichier = ?
            WHEN NOT MATCHED THEN INSERT
                (Nom_fichier, Utilisateur, Source, DATE_MODIF, Fichier)
            VALUES (?, ?, ?, GETDATE(), ?);
        """, ('CONSOLIDE_VALIDE.xlsx', utilisateur, 'CONSOLIDE_VALIDE', contenu,
              'CONSOLIDE_VALIDE.xlsx', utilisateur, 'CONSOLIDE_VALIDE', contenu))
        conn.commit()
        conn.close()
        return True, f"✅ Fichier consolidé sauvegardé ({len(df)} lignes)"
    except Exception as e:
        return False, f"❌ Erreur : {e}"


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_depot, tab_fichiers, tab_aggreg = st.tabs(["📤 Déposer", "📋 Fichiers déposés", "🔗 Agréger"])

# ── Tab 1 : Dépôt ─────────────────────────────────────────────────────────────
with tab_depot:
    utilisateur = st.text_input("👤 Votre nom", placeholder="Prénom Nom")
    source = st.radio("Type de fichier", ["📊 Pivot Lasernet", "📝 Hors Lasernet / Modifié"],
                      horizontal=True)
    source_label = 'LASERNET' if '📊' in source else 'HORS_LASERNET'

    uploaded_files = st.file_uploader(
        "📁 Déposer le(s) fichier(s) Excel",
        type=['xlsx', 'xls'],
        accept_multiple_files=True
    )

    if uploaded_files:
        if not utilisateur.strip():
            st.warning("⚠️ Renseignez votre nom")
            st.stop()

        for f in uploaded_files:
            try:
                df_prev = pd.read_excel(f, dtype=str, nrows=5)
                f.seek(0)
                st.caption(f"**{f.name}** — {len(df_prev.columns)} colonnes")
            except:
                pass

        if st.button("💾 Sauvegarder en base", type="primary", width='stretch'):
            for f in uploaded_files:
                ok, msg = sauvegarder_fichier(f, utilisateur.strip(), source_label)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            st.rerun()

# ── Tab 2 : Fichiers déposés ──────────────────────────────────────────────────
with tab_fichiers:
    df_liste = lister_fichiers()

    if df_liste.empty:
        st.info("Aucun fichier déposé pour l'instant")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Fichiers déposés", len(df_liste))
        c2.metric("Utilisateurs", df_liste['Utilisateur'].nunique())

        st.dataframe(df_liste[['ID','Fichier','Utilisateur','Source','Date','Taille_Ko']],
                     height=300, width='stretch')

        st.markdown("---")
        choix_id = st.selectbox(
            "Sélectionner un fichier",
            options=df_liste['ID'].tolist(),
            format_func=lambda x: (
                f"{df_liste[df_liste['ID']==x]['Fichier'].values[0]} — "
                f"{df_liste[df_liste['ID']==x]['Utilisateur'].values[0]} — "
                f"{df_liste[df_liste['ID']==x]['Date'].values[0]}"
            )
        )

        col1, col2 = st.columns(2)
        with col1:
            contenu, nom = telecharger_fichier(choix_id)
            if contenu:
                st.download_button(
                    "📥 Télécharger ce fichier",
                    data=contenu,
                    file_name=nom,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )
        with col2:
            if st.button("🗑️ Supprimer ce fichier", width='stretch', type="primary"):
                if supprimer_fichier(choix_id):
                    st.success("✅ Supprimé")
                    st.rerun()

        st.markdown("---")
        if st.button("🗑️ Vider tous les fichiers", width='stretch'):
            conn = get_conn()
            conn.execute("TRUNCATE TABLE [dbo].[T_PREVISION_FICHIERS]")
            conn.commit()
            conn.close()
            st.success("✅ Table vidée")
            st.rerun()

# ── Tab 3 : Agrégation ────────────────────────────────────────────────────────
with tab_aggreg:
    st.subheader("🔗 Consolider tous les fichiers déposés")

    df_liste = lister_fichiers()
    if df_liste.empty:
        st.info("Aucun fichier déposé — allez dans l'onglet **Déposer** d'abord")
    else:
        st.info(f"**{len(df_liste)} fichier(s)** prêts à agréger :")
        st.dataframe(df_liste[['Fichier','Utilisateur','Source','Date']], width='stretch')

        if st.button("🔗 Agréger tous les fichiers", type="primary", width='stretch'):
            with st.spinner("Agrégation en cours..."):
                df_aggr, msg = agreger_tous_fichiers()

            if df_aggr is not None:
                st.success(msg)

                # Colonnes semaines et méta — toutes en string déjà
                wk_cols = wk_cols_from_df(df_aggr)
                meta_cols = [c for c in df_aggr.columns
                             if c not in wk_cols and not c.startswith('_')]

                # Métriques
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Lignes", len(df_aggr))
                c2.metric("Semaines", len(wk_cols))
                if 'PROGRAMME' in df_aggr.columns:
                    c3.metric("Programmes", df_aggr['PROGRAMME'].nunique())
                if 'REF_ARTICLE_SERTA' in df_aggr.columns:
                    c4.metric("Refs", df_aggr['REF_ARTICLE_SERTA'].nunique())

                st.markdown("---")

                # Filtres
                col_f1, col_f2, col_f3 = st.columns(3)
                with col_f1:
                    f_prog = st.multiselect("🔍 Programme",
                        options=sorted(df_aggr['PROGRAMME'].dropna().unique())
                        if 'PROGRAMME' in df_aggr.columns else [])
                with col_f2:
                    f_ref = st.multiselect("🔍 Ref SERTA",
                        options=sorted(df_aggr['REF_ARTICLE_SERTA'].dropna().unique())
                        if 'REF_ARTICLE_SERTA' in df_aggr.columns else [])
                with col_f3:
                    f_up = st.multiselect("🔍 UP",
                        options=sorted(df_aggr['UP_PRINCIPALE'].dropna().astype(str).unique())
                        if 'UP_PRINCIPALE' in df_aggr.columns else [])

                df_disp = df_aggr.copy()
                if f_prog: df_disp = df_disp[df_disp['PROGRAMME'].isin(f_prog)]
                if f_ref:  df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].isin(f_ref)]
                if f_up:   df_disp = df_disp[df_disp['UP_PRINCIPALE'].astype(str).isin(f_up)]

                cols_affich = [c for c in meta_cols if c in df_disp.columns]
                wk_disp = [c for c in wk_cols if c in df_disp.columns]
                col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_disp}

                st.caption(f"{len(df_disp):,} lignes")
                st.dataframe(df_disp[cols_affich + wk_disp],
                             height=500, width='stretch', column_config=col_cfg)

                # Sauvegarder en session
                st.session_state['df_consolide'] = df_aggr
                st.session_state['_utilisateur_consolide'] = utilisateur if 'utilisateur' in dir() else 'inconnu'

                # Export
                st.download_button(
                    "📥 Télécharger le fichier consolidé",
                    data=to_excel_bytes(df_disp[cols_affich + wk_disp]),
                    file_name=f"consolide_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )
            else:
                st.error(msg)

        # ── Bouton Valider & Sauvegarder en base ──────────────────────────────
        if 'df_consolide' in st.session_state:
            st.markdown("---")
            st.subheader("✅ Valider & Sauvegarder en base")
            st.info(
                f"Le fichier consolidé ({len(st.session_state['df_consolide'])} lignes) est prêt. "
                "Cliquez sur **Valider** pour le sauvegarder en base. "
                "Il sera disponible dans toutes les pages suivantes même après navigation."
            )
            nom_valid = st.text_input("👤 Votre nom", key="nom_validation")
            if st.button("✅ Valider & Sauvegarder en base", type="primary", width='stretch',
                         disabled=not nom_valid.strip()):
                with st.spinner("Sauvegarde en cours..."):
                    ok, msg_save = sauvegarder_consolide_blob(
                        st.session_state['df_consolide'], nom_valid.strip(), to_excel_bytes
                    )
                if ok:
                    st.success(msg_save)
                    st.balloons()
                else:
                    st.error(msg_save)

# ── Statut session ────────────────────────────────────────────────────────────
st.markdown("---")
if 'df_consolide' in st.session_state:
    df_c = st.session_state['df_consolide']
    wk_c = wk_cols_from_df(df_c)
    st.success(f"📦 Fichier consolidé en session — {len(df_c)} lignes, {len(wk_c)} semaines")
else:
    st.info("Aucun fichier consolidé en session — agrégez dans l'onglet **Agréger**")