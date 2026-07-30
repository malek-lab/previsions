import streamlit as st
import pandas as pd
import plotly.express as px
import datetime as dt
import pyodbc
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes, nettoyer_code, nettoyer_codes_dataframe, get_historique_ventes

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

def lister_fichiers_sources():
    """Comme lister_fichiers(), mais exclut les consolidés déjà validés
    (Source='CONSOLIDE_VALIDE') -- ce ne sont pas des fichiers sources Lasernet/
    Hors Lasernet à combiner, mais des résultats déjà finalisés. Les ré-agréger
    par erreur créerait des doublons / incohérences."""
    df = lister_fichiers()
    return df[df['Source'] != 'CONSOLIDE_VALIDE'].reset_index(drop=True)

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
    # dtype=str est CRITIQUE ici : sans lui, pandas réinterprète toute colonne
    # purement numérique (ex: CODE_CLIENT = "0388") comme un nombre et perd
    # systématiquement le zéro initial dès la lecture du fichier Excel.
    df = pd.read_excel(BytesIO(contenu_bytes), dtype=str)

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
    df_liste = lister_fichiers_sources()
    if df_liste.empty:
        return None, "Aucun fichier source (Lasernet/Hors Lasernet) en base"

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
    """Sauvegarde le fichier consolidé comme blob Excel dans T_PREVISION_FICHIERS.

    IMPORTANT : chaque validation crée une NOUVELLE ligne (nom de fichier daté),
    au lieu d'écraser la précédente. Ça permet de conserver un historique des
    consolidés validés, nécessaire pour calculer une comparaison M vs M-1
    (le programme tel qu'il était il y a environ un mois) comme dans le
    fichier de référence de Sophie."""
    try:
        from io import BytesIO
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df.to_excel(w, index=False, sheet_name='Consolide')
        contenu = buf.getvalue()

        nom_fichier_date = f"CONSOLIDE_VALIDE_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        conn = get_conn()
        conn.execute("""
            INSERT INTO [dbo].[T_PREVISION_FICHIERS]
                (Nom_fichier, Utilisateur, Source, DATE_MODIF, Fichier)
            VALUES (?, ?, 'CONSOLIDE_VALIDE', GETDATE(), ?);
        """, (nom_fichier_date, utilisateur, contenu))
        conn.commit()
        conn.close()
        return True, f"✅ Fichier consolidé sauvegardé ({len(df)} lignes) sous '{nom_fichier_date}'"
    except Exception as e:
        return False, f"❌ Erreur : {e}"


def lister_consolides_valides():
    """Liste tous les consolidés validés historiques (Source='CONSOLIDE_VALIDE'),
    triés du plus récent au plus ancien."""
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ID, Nom_fichier, Utilisateur, DATE_MODIF
            FROM [dbo].[T_PREVISION_FICHIERS]
            WHERE Source = 'CONSOLIDE_VALIDE'
            ORDER BY DATE_MODIF DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return pd.DataFrame(
            [[r[0], r[1], r[2], r[3]] for r in rows],
            columns=['ID', 'Nom_fichier', 'Utilisateur', 'Date']
        )
    except Exception as e:
        print(f"Erreur lister_consolides_valides : {e}")
        return pd.DataFrame(columns=['ID', 'Nom_fichier', 'Utilisateur', 'Date'])


def charger_consolide_m_moins_1():
    """Récupère le consolidé validé le plus proche d'un mois avant aujourd'hui
    (cible : 30 jours en arrière, on prend le plus proche de cette cible)."""
    df_versions = lister_consolides_valides()
    if df_versions.empty or len(df_versions) < 2:
        return None, None
    cible = pd.Timestamp.now() - pd.Timedelta(days=30)
    df_versions['Date'] = pd.to_datetime(df_versions['Date'])
    df_versions['ecart_jours'] = (df_versions['Date'] - cible).abs()
    # On exclut la version la plus récente (= la version "actuelle", pas M-1)
    df_candidats = df_versions.iloc[1:]
    if df_candidats.empty:
        return None, None
    meilleure = df_candidats.sort_values('ecart_jours').iloc[0]
    contenu, nom = telecharger_fichier(int(meilleure['ID']))
    if contenu is None:
        return None, None
    from io import BytesIO
    df_m1 = pd.read_excel(BytesIO(contenu), dtype=str)
    return df_m1, meilleure['Date']


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_depot, tab_fichiers, tab_aggreg, tab_historique = st.tabs(
    ["📤 Déposer", "📋 Fichiers déposés", "🔗 Agréger", "📉 Historique Ventes"])

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

        col1, col2, col3 = st.columns(3)
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
            voir_fichier = st.button("📄 Visualiser ce fichier", width='stretch')
        with col3:
            if st.button("🗑️ Supprimer ce fichier", width='stretch', type="primary"):
                if supprimer_fichier(choix_id):
                    st.success("✅ Supprimé")
                    st.rerun()

        # ── Visualisation directe en tableau, sans agrégation ────────────────
        # Fonctionne pour n'importe quel fichier de la base (consolidé déjà
        # validé OU pivot Lasernet/Hors Lasernet brut) -- pas besoin de relancer
        # l'agrégation si le fichier voulu existe déjà tel quel.
        if voir_fichier:
            contenu_vis, nom_vis = telecharger_fichier(choix_id)
            if contenu_vis:
                try:
                    from io import BytesIO
                    df_vis = pd.read_excel(BytesIO(contenu_vis), dtype=str)
                    wk_vis = wk_cols_from_df(df_vis)
                    for c in wk_vis:
                        df_vis[c] = pd.to_numeric(df_vis[c], errors='coerce').fillna(0)
                    st.session_state['df_visualise'] = df_vis
                    st.session_state['nom_visualise'] = nom_vis
                except Exception as e:
                    st.error(f"Impossible de lire ce fichier comme un tableau : {e}")

        if 'df_visualise' in st.session_state:
            st.markdown("---")
            st.markdown(f"#### 📄 Aperçu : {st.session_state['nom_visualise']}")
            df_vis = st.session_state['df_visualise']
            wk_vis = wk_cols_from_df(df_vis)
            meta_vis = [c for c in df_vis.columns if c not in wk_vis]
            st.caption(f"{len(df_vis):,} lignes, {len(wk_vis)} colonnes semaine")
            st.dataframe(df_vis[meta_vis + wk_vis], width='stretch', height=450)

            if st.button("📌 Utiliser ce fichier comme consolidé courant "
                         "(filtres, export, Historique Ventes...)", width='stretch'):
                st.session_state['df_consolide'] = df_vis
                st.success("✅ Ce fichier est maintenant le consolidé courant — "
                          "allez dans l'onglet **🔗 Agréger** ou **📉 Historique Ventes** pour le voir.")

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

    st.markdown("---")

    # ── Bloc 2 : agréger les fichiers SOURCES (Lasernet / Hors Lasernet uniquement) ──
    df_liste = lister_fichiers_sources()
    if df_liste.empty:
        st.info("Aucun fichier source déposé — allez dans l'onglet **Déposer** d'abord")
    else:
        st.info(f"**{len(df_liste)} fichier(s) source(s)** (Lasernet/Hors Lasernet) prêts à agréger :")
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

# ── Tab 4 : Historique des ventes (ref/client du consolidé) ───────────────────
with tab_historique:
    st.subheader("📉 Historique des ventes — couples Ref/Client du consolidé")

    if 'df_consolide' not in st.session_state:
        st.info("Agrégez d'abord vos fichiers dans l'onglet **🔗 Agréger** pour voir l'historique "
                "des ventes correspondant aux références/clients du consolidé.")
    else:
        df_c = st.session_state['df_consolide']
        if 'CODE_CLIENT' not in df_c.columns or 'REF_ARTICLE_SERTA' not in df_c.columns:
            st.warning("Le consolidé ne contient pas les colonnes CODE_CLIENT / REF_ARTICLE_SERTA.")
        else:
            # Sécurité anti-perte de zéro : re-forcer le texte ici, même si lire_fichier_excel()
            # utilise déjà dtype=str -- au cas où df_consolide aurait transité par un autre
            # chemin (ex: fichier "Visualisé" utilisé comme consolidé, session persistée...).
            df_c['CODE_CLIENT'] = df_c['CODE_CLIENT'].astype(str).str.strip()
            df_c['REF_ARTICLE_SERTA'] = df_c['REF_ARTICLE_SERTA'].astype(str).str.strip()
            couples = df_c[['CODE_CLIENT', 'REF_ARTICLE_SERTA']].dropna().drop_duplicates()
            st.caption(f"{len(couples)} couple(s) Ref/Client distinct(s) dans le consolidé.")

            annee_actuelle = pd.Timestamp.now().year       # toujours calculée dynamiquement
            annee_precedente = annee_actuelle - 1            # année N-1, jamais figée
            annee_suivante = annee_actuelle + 1               # année N+1, jamais figée
            MOIS_LABEL = {1:'Jan',2:'Fév',3:'Mar',4:'Avr',5:'Mai',6:'Jun',
                          7:'Jul',8:'Aoû',9:'Sep',10:'Oct',11:'Nov',12:'Déc'}

            def annee_semaine(col):
                try:
                    return 2000 + int(col[1:3]), int(col[4:6])
                except Exception:
                    return None, None

            def qty_trimestres(df_source, wk_cols_an, annee, suffixe):
                """Retourne un DataFrame indexé (CODE_CLIENT, REF_ARTICLE_SERTA) avec
                les 4 colonnes trimestrielles 'Q1 {annee} {suffixe}' ... 'Q4 {annee} {suffixe}'."""
                out = None
                for t in [1, 2, 3, 4]:
                    cols_t = [c for c in wk_cols_an if annee_semaine(c)[1] in range((t-1)*13+1, t*13+1)]
                    if not cols_t:
                        continue
                    s = df_source.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])[cols_t].sum().sum(axis=1)
                    s = s.rename(f'Q{t} {annee} {suffixe}')
                    out = s.to_frame() if out is None else out.join(s, how='outer')
                return out if out is not None else pd.DataFrame()

            # Date de coupure : champ libre, propre à cet onglet (indépendant de la page 1
            # pour pouvoir déposer/agréger directement ici sans repasser par le Pivot).
            # Valeur INITIALE = date_ventilation (ou date_prevision à défaut) de la page 1,
            # mais UNE SEULE FOIS -- si l'utilisateur la change ensuite, son choix est
            # définitivement conservé et n'est plus jamais écrasé par la page 1, même si
            # elle est antérieure à la date système ou à une ancienne valeur.
            # Aucun arrondi au lundi : la date choisie est utilisée exactement telle quelle
            # (le vrai cutoff métier n'est pas toujours un lundi, ex: 21/06/2026 = dimanche).
            if 'date_cutoff_hist' not in st.session_state:
                date_ref_page1 = (st.session_state.get('date_ventilation')
                                   or st.session_state.get('date_prevision'))
                st.session_state['date_cutoff_hist'] = (
                    pd.Timestamp(date_ref_page1).date() if date_ref_page1 else pd.Timestamp.now().date())

            col_date, col_annee = st.columns(2)
            with col_date:
                date_cutoff = st.date_input(
                    "Date de coupure (YTD jusqu'à cette date incluse)",
                    format="DD/MM/YYYY", key="date_cutoff_hist",
                    help="Initialisée sur la Date de ventilation/prévision de la page 1, "
                         "mais librement modifiable ici. Votre choix est conservé tel quel, "
                         "exactement à la date choisie (aucun arrondi au lundi).")
            with col_annee:
                annee_min = st.number_input(
                    "Charger l'historique de ventes depuis l'année", min_value=2018,
                    max_value=annee_actuelle, value=annee_precedente, step=1, key="annee_min_hist")

            mois_actuel = date_cutoff.month
            semaine_actuelle = pd.Timestamp(date_cutoff).isocalendar()[1]

            if st.button("🔄 Charger / Actualiser l'historique", type="primary", width='stretch'):
                with st.spinner("Chargement et calcul en cours..."):
                    # 1) Historique de ventes réelles, restreint aux couples du consolidé
                    df_hist_all = get_historique_ventes(annee_min)
                    # IMPORTANT : forcer le texte ICI, sur les colonnes elles-mêmes (pas
                    # seulement sur une clé temporaire) -- si get_historique_ventes() renvoie
                    # CLIENT_CODE/ITEM_CODE en type numérique (SQL INT), les zéros de tête
                    # (ex: "0081") sont déjà perdus à ce stade. Le forcer ici garantit que
                    # TOUT ce qui est dérivé de df_hist_all en aval (_KEY, rename, groupby,
                    # pivot_table, join avec base/synth) reste cohérent en texte avec df_c.
                    # ⚠️ Si le zéro est déjà perdu AVANT ce point (colonne SQL typée en INT
                    # côté base), ce cast Python ne peut plus le restaurer -- il faut alors
                    # vérifier/corriger le type de la colonne côté requête SQL dans shared.py
                    # (CAST(CLIENT_CODE AS VARCHAR) ou équivalent).
                    df_hist_all['CLIENT_CODE'] = df_hist_all['CLIENT_CODE'].astype(str).str.strip()
                    df_hist_all['ITEM_CODE'] = df_hist_all['ITEM_CODE'].astype(str).str.strip()
                    couples_set = set(zip(couples['CODE_CLIENT'].astype(str), couples['REF_ARTICLE_SERTA'].astype(str)))
                    df_hist_all['_KEY'] = list(zip(df_hist_all['CLIENT_CODE'], df_hist_all['ITEM_CODE']))
                    df_hist = df_hist_all[df_hist_all['_KEY'].isin(couples_set)].drop(columns=['_KEY']).copy()

                    df_hist['DATE_FACTURE'] = pd.to_datetime(df_hist['DATE_FACTURE'], errors='coerce')
                    nb_anomalies = (df_hist['DATE_FACTURE'].dt.date > date_cutoff).sum()
                    if nb_anomalies > 0:
                        st.info(f"ℹ️ {nb_anomalies} ligne(s) facturée(s) après le "
                                f"{date_cutoff.strftime('%d/%m/%Y')} (date de coupure choisie) "
                                "ont été exclues.")
                        df_hist = df_hist[df_hist['DATE_FACTURE'].dt.date <= date_cutoff]
                    df_hist['TRIMESTRE'] = ((df_hist['INVOICE_MONTH'] - 1) // 3) + 1
                    df_hist = df_hist.rename(columns={'CLIENT_CODE': 'CODE_CLIENT', 'ITEM_CODE': 'REF_ARTICLE_SERTA'})

                    # 2) Programme ACTUEL (consolidé en session) -- limité strictement aux
                    #    années en cours et suivante (jamais de colonnes inventées au-delà)
                    wk_tous = wk_cols_from_df(df_c)
                    for c in wk_tous:
                        df_c[c] = pd.to_numeric(df_c[c], errors='coerce').fillna(0)
                    df_c['PRIX_MOQ'] = pd.to_numeric(df_c.get('PRIX_MOQ', 0), errors='coerce').fillna(0)

                    wk_n = [c for c in wk_tous if annee_semaine(c)[0] == annee_actuelle]
                    wk_n1 = [c for c in wk_tous if annee_semaine(c)[0] == annee_suivante]

                    # RÈGLE (confirmée par contrôle croisé -- 100% de correspondance exacte,
                    # écart nul sur 1746/1746 lignes testées) :
                    # Qty {annee_actuelle} = Qty_YTD + SUM(toutes les semaines {annee_actuelle}
                    # du programme, passées ET futures).
                    #
                    # Le retard (QTE_CUTOFF_RETARD = besoin_client_retard + en_transit_retard,
                    # champ BaaN) est déjà pris en compte par BaaN à l'intérieur du programme
                    # lui-même (report automatique dans les semaines passées) -- NE JAMAIS
                    # l'additionner séparément à Qty {annee_actuelle} ou à un trimestre, sous
                    # peine de double comptage (vérifié : ça introduit un écart de plusieurs
                    # milliers d'unités qui n'existe pas dans le calcul simple ci-dessus).
                    # QTE_CUTOFF_RETARD est conservé uniquement comme colonne informative.
                    wk_futurs_n = wk_n  # toutes les semaines de l'année en cours, passées et futures

                    col_retard = 'QTE_CUTOFF_RETARD'
                    if col_retard in df_c.columns:
                        df_c[col_retard] = pd.to_numeric(df_c[col_retard], errors='coerce').fillna(0)

                    base = df_c.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA']).agg(PRIX_MOQ=('PRIX_MOQ', 'max'))
                    base['_Qty_prog_reste_an'] = (
                        df_c.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])[wk_futurs_n].sum().sum(axis=1)
                        if wk_futurs_n else 0)
                    # Retard : aggregé par couple (max, car la colonne est déjà agrégée par ligne pivot)
                    # -- gardé uniquement à titre informatif, jamais additionné.
                    if col_retard in df_c.columns:
                        base['_Qty_retard'] = df_c.groupby(
                            ['CODE_CLIENT', 'REF_ARTICLE_SERTA'])[col_retard].max()
                    else:
                        base['_Qty_retard'] = 0
                    base[f'Qty {annee_suivante}'] = (
                        df_c.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])[wk_n1].sum().sum(axis=1)
                        if wk_n1 else 0)
                    base = base.reset_index()

                    # Trimestres PROGRAMME ACTUEL : année en cours ET année suivante (comme Sophie,
                    # qui a Quarter 1-4/2026 ET T1-T4/2027)
                    quarters_prog = qty_trimestres(df_c, wk_n, annee_actuelle, "(prog)")
                    quarters_prog_n1 = qty_trimestres(df_c, wk_n1, annee_suivante, "(prog)")
                    if not quarters_prog_n1.empty:
                        quarters_prog = (quarters_prog.join(quarters_prog_n1, how='outer')
                                         if quarters_prog is not None and not quarters_prog.empty
                                         else quarters_prog_n1)

                    # 3) Historique réel : YTD (année en cours), trimestres ANNÉE PRÉCÉDENTE
                    #    et trimestres RÉELS ANNÉE EN COURS (pour enrichir Q1/Q2 2026 comme Sophie)
                    hist_ytd = df_hist[df_hist['REF_ARTICLE_SERTA'].notna() & (df_hist['INVOICE_YEAR'] == annee_actuelle)].groupby(
                        ['CODE_CLIENT', 'REF_ARTICLE_SERTA'])['QTE'].sum().rename('Qty_YTD').to_frame()

                    quarters_reel = None
                    # Trimestres réels année précédente (2025)
                    for t in [1, 2, 3, 4]:
                        sous = df_hist[(df_hist['INVOICE_YEAR'] == annee_precedente) & (df_hist['TRIMESTRE'] == t)]
                        if sous.empty:
                            continue
                        s = sous.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])['QTE'].sum().rename(f'Q{t} {annee_precedente} (réel)')
                        quarters_reel = s.to_frame() if quarters_reel is None else quarters_reel.join(s, how='outer')

                    # Trimestres réels année en cours (2026) -- pour enrichir Q1/Q2 2026
                    # comme Sophie qui inclut le réel facturé dans ses trimestres programme
                    quarters_reel_an = None
                    for t in [1, 2, 3, 4]:
                        sous = df_hist[(df_hist['INVOICE_YEAR'] == annee_actuelle) & (df_hist['TRIMESTRE'] == t)]
                        if sous.empty:
                            continue
                        s = sous.groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])['QTE'].sum().rename(f'_Q{t}_{annee_actuelle}_reel')
                        quarters_reel_an = s.to_frame() if quarters_reel_an is None else quarters_reel_an.join(s, how='outer')

                    # Retard : quantités prévues non facturées dans les 2 mois avant cutoff
                    # (semaines entre cutoff-8 et cutoff-1) -- si QTE_CUTOFF_RETARD absent
                    date_2mois = pd.Timestamp(date_cutoff) - pd.Timedelta(weeks=8)
                    retard_hist = df_hist[
                        (df_hist['DATE_FACTURE'] >= date_2mois) &
                        (df_hist['DATE_FACTURE'].dt.date <= date_cutoff) &
                        (df_hist['INVOICE_YEAR'] == annee_actuelle)
                    ].groupby(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])['QTE'].sum().rename('_retard_2mois')
                    retard_hist.index = retard_hist.index.set_names(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])

                    # 4) Programme M-1 (consolidé validé il y a ~1 mois), si disponible
                    quarters_m1 = None
                    df_versions = lister_consolides_valides()
                    date_m1 = None
                    if len(df_versions) >= 2:
                        df_m1, date_m1 = charger_consolide_m_moins_1()
                        if df_m1 is not None and 'CODE_CLIENT' in df_m1.columns:
                            df_m1['CODE_CLIENT'] = df_m1['CODE_CLIENT'].astype(str).str.strip()
                            df_m1['REF_ARTICLE_SERTA'] = df_m1['REF_ARTICLE_SERTA'].astype(str).str.strip()
                            wk_m1_tous = wk_cols_from_df(df_m1)
                            for c in wk_m1_tous:
                                df_m1[c] = pd.to_numeric(df_m1[c], errors='coerce').fillna(0)
                            wk_m1_n = [c for c in wk_m1_tous if annee_semaine(c)[0] == annee_actuelle]
                            quarters_m1 = qty_trimestres(df_m1, wk_m1_n, annee_actuelle, "M-1")

                    # 5) Assembler tout
                    synth = base.set_index(['CODE_CLIENT', 'REF_ARTICLE_SERTA'])
                    synth = synth.join(hist_ytd, how='outer')
                    synth = synth.join(quarters_prog, how='outer')
                    if quarters_m1 is not None:
                        synth = synth.join(quarters_m1, how='outer')
                    if quarters_reel is not None:
                        synth = synth.join(quarters_reel, how='outer')
                    # Joindre réel 2026 par trimestre
                    if quarters_reel_an is not None:
                        synth = synth.join(quarters_reel_an, how='outer')
                    synth = synth.fillna(0).reset_index()

                    # Fusionner réel 2026 + programme 2026 dans chaque trimestre
                    # Q{t} 2026 (prog) = réel facturé du trimestre + programme restant
                    # Cela correspond à ce que fait Sophie : elle inclut le réel dans ses trimestres
                    for t in [1, 2, 3, 4]:
                        col_prog = f'Q{t} {annee_actuelle} (prog)'
                        col_reel = f'_Q{t}_{annee_actuelle}_reel'
                        if col_prog in synth.columns and col_reel in synth.columns:
                            synth[col_prog] = synth[col_prog] + synth[col_reel]
                        elif col_reel in synth.columns:
                            synth[col_prog] = synth[col_reel]
                    # Nettoyer colonnes intermédiaires
                    cols_reel_an_tmp = [f'_Q{t}_{annee_actuelle}_reel' for t in [1,2,3,4]]
                    synth = synth.drop(columns=[c for c in cols_reel_an_tmp if c in synth.columns], errors='ignore')

                    # Qty {annee_actuelle} = YTD + toutes semaines programme (le retard, déjà
                    # pris en compte par BaaN dans le programme lui-même, n'est PAS ajouté une
                    # seconde fois -- voir commentaire "RÈGLE" plus haut).
                    synth['QTE_CUTOFF_RETARD'] = synth.get('_Qty_retard', 0)
                    synth[f'Qty {annee_actuelle}'] = (
                        synth.get('Qty_YTD', 0)
                        + synth.get('_Qty_prog_reste_an', 0))
                    synth = synth.drop(
                        columns=['_Qty_prog_reste_an', '_Qty_retard'],
                        errors='ignore')

                    nom_gap = f'Gap Qty {annee_suivante} vs {annee_actuelle}'
                    synth[nom_gap] = synth[f'Qty {annee_suivante}'] - synth[f'Qty {annee_actuelle}']
                    synth[nom_gap + ' (%)'] = synth.apply(
                        lambda r: round(r[nom_gap] / r[f'Qty {annee_actuelle}'] * 100, 1)
                        if r[f'Qty {annee_actuelle}'] != 0 else None, axis=1)

                    # Garantir que Q1 de l'année en cours existe toujours dans synth
                    # (même à 0) -- T1 est souvent déjà passé quand le programme est déposé,
                    # donc qty_trimestres ne trouve aucune semaine pour T1 et ne crée pas
                    # la colonne, ce qui fait sauter Gap Q1 2026 vs 2025 plus bas.
                    col_q1_prog = f'Q1 {annee_actuelle} (prog)'
                    if col_q1_prog not in synth.columns:
                        synth[col_q1_prog] = 0

                    # 7) Gap trimestre PROGRAMME ACTUEL vs PROGRAMME M-1 (même trimestre, même année)
                    if quarters_m1 is not None:
                        for t in [1, 2, 3, 4]:
                            c_prog = f'Q{t} {annee_actuelle} (prog)'
                            c_m1 = f'Q{t} {annee_actuelle} M-1'
                            if c_prog in synth.columns and c_m1 in synth.columns:
                                synth[f'Gap Q{t} {annee_actuelle} M vs M-1'] = synth[c_prog] - synth[c_m1]

                    # 8) Gap trimestre PROGRAMME année en cours vs RÉEL année précédente
                    for t in [1, 2, 3, 4]:
                        c_prog = f'Q{t} {annee_actuelle} (prog)'
                        c_reel = f'Q{t} {annee_precedente} (réel)'
                        if c_prog in synth.columns and c_reel in synth.columns:
                            synth[f'Gap Q{t} {annee_actuelle} vs {annee_precedente}'] = synth[c_prog] - synth[c_reel]

                    # 9) TOTAL année précédente (réel) -- somme des 4 trimestres réels,
                    #    et Gap global Qty année en cours vs année précédente (quantités seulement)
                    cols_q_reel_prec_calc = [f'Q{t} {annee_precedente} (réel)' for t in [1,2,3,4]
                                              if f'Q{t} {annee_precedente} (réel)' in synth.columns]
                    if cols_q_reel_prec_calc:
                        synth[f'TOTAL {annee_precedente}'] = synth[cols_q_reel_prec_calc].sum(axis=1)
                        synth[f'Global gap Qty {annee_actuelle} vs {annee_precedente}'] = (
                            synth[f'Qty {annee_actuelle}'] - synth[f'TOTAL {annee_precedente}'])
                        synth[f'Global gap Qty {annee_actuelle} vs {annee_precedente} (%)'] = synth.apply(
                            lambda r: round(r[f'Global gap Qty {annee_actuelle} vs {annee_precedente}']
                                             / r[f'TOTAL {annee_precedente}'] * 100, 1)
                            if r[f'TOTAL {annee_precedente}'] != 0 else None, axis=1)

                    st.session_state['df_synthese'] = synth
                    st.session_state['df_hist_detail'] = df_hist
                    st.session_state['date_m1_hist'] = date_m1

            if 'df_synthese' in st.session_state:
                synth = st.session_state['df_synthese']

                c1, c2, c3 = st.columns(3)
                c1.metric("Couples dans le tableau", len(synth))
                c2.metric("Couples avec historique", int((synth.get('Qty_YTD', 0) > 0).sum()))
                if st.session_state.get('date_m1_hist') is not None:
                    c3.metric("Programme M-1 du", pd.Timestamp(st.session_state['date_m1_hist']).strftime('%d/%m/%Y'))

                # ── Organisation STRICTE par blocs, comme demandé -- quantités uniquement ──
                cols_id = ['CODE_CLIENT', 'REF_ARTICLE_SERTA']
                cols_qty_an = [c for c in [
                    'Qty_YTD', 'QTE_CUTOFF_RETARD',
                    f'Qty {annee_actuelle}', f'Qty {annee_suivante}',
                    f'Gap Qty {annee_suivante} vs {annee_actuelle}',
                    f'Gap Qty {annee_suivante} vs {annee_actuelle} (%)',
                ] if c in synth.columns]
                cols_q_prog = [f'Q{t} {annee_actuelle} (prog)' for t in [1,2,3,4] if f'Q{t} {annee_actuelle} (prog)' in synth.columns]
                cols_q_prog_n1 = [f'Q{t} {annee_suivante} (prog)' for t in [1,2,3,4] if f'Q{t} {annee_suivante} (prog)' in synth.columns]
                cols_q_m1 = [f'Q{t} {annee_actuelle} M-1' for t in [1,2,3,4] if f'Q{t} {annee_actuelle} M-1' in synth.columns]
                cols_gap_m = [f'Gap Q{t} {annee_actuelle} M vs M-1' for t in [1,2,3,4] if f'Gap Q{t} {annee_actuelle} M vs M-1' in synth.columns]
                cols_q_reel_prec = [f'Q{t} {annee_precedente} (réel)' for t in [1,2,3,4] if f'Q{t} {annee_precedente} (réel)' in synth.columns]
                cols_gap_an = [f'Gap Q{t} {annee_actuelle} vs {annee_precedente}' for t in [1,2,3,4] if f'Gap Q{t} {annee_actuelle} vs {annee_precedente}' in synth.columns]
                cols_total_global = [c for c in [
                    f'TOTAL {annee_precedente}',
                    f'Global gap Qty {annee_actuelle} vs {annee_precedente}',
                    f'Global gap Qty {annee_actuelle} vs {annee_precedente} (%)',
                ] if c in synth.columns]

                ordre = cols_id + cols_qty_an + cols_q_prog + cols_q_prog_n1 + cols_q_m1 + cols_gap_m + cols_q_reel_prec + cols_gap_an + cols_total_global
                ordre = [c for c in ordre if c in synth.columns]
                # Pas de filet de sécurité ajoutant les colonnes restantes : la liste ci-dessus
                # est exhaustive et volontaire (fidèle à la structure de Sophie). Toute colonne
                # technique interne (ex: PRIX_MOQ, conservé en interne pour un usage futur Turnover)
                # ne doit jamais apparaître si elle n'est pas explicitement listée.

                # Blindage anti-pyarrow : forcer toutes les colonnes texte en str pur
                # avant l'affichage -- pyarrow plante si une colonne 'object' contient
                # un mélange str/int/None (cas fréquent sur CLIENT_NAME côté USA).
                cols_texte_synth = ['CODE_CLIENT', 'REF_ARTICLE_SERTA']
                for _c in cols_texte_synth:
                    if _c in synth.columns:
                        synth[_c] = synth[_c].fillna('').astype(str)
                st.dataframe(synth[ordre], width='stretch', height=500)
                st.download_button(
                    "📥 Télécharger ce tableau (Excel)",
                    data=to_excel_bytes(synth[ordre]),
                    file_name=f"synthese_ventes_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )

                if 'df_hist_detail' in st.session_state and not st.session_state['df_hist_detail'].empty:
                    df_hist = st.session_state['df_hist_detail']
                    # Blindage anti-pyarrow : les colonnes texte servant d'index aux pivots
                    # ci-dessous (notamment CLIENT_NAME) peuvent mélanger str/int/None
                    # côté USA -> pyarrow plante à l'affichage. On force le str pur ici,
                    # avant tout pivot_table, pour que les colonnes reset_index soient propres.
                    for _c in ['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA']:
                        if _c in df_hist.columns:
                            df_hist[_c] = df_hist[_c].fillna('').astype(str)
                    st.markdown("---")
                    st.markdown("#### Détail de l'historique réel — autres vues")
                    sous_tab_mois, sous_tab_t, sous_tab_an = st.tabs(
                        ["📅 Par mois", "📆 Par trimestre", "🗓️ Par année"])

                    with sous_tab_mois:
                        pivot_m = df_hist.pivot_table(
                            index=['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'],
                            columns='INVOICE_YEAR_MONTH', values='QTE', aggfunc='sum', fill_value=0
                        ).reset_index()
                        m_cols = sorted([c for c in pivot_m.columns if isinstance(c, (int, float))])
                        label_mois = {c: f"{MOIS_LABEL[int(str(int(c))[4:])]}-{str(int(c))[2:4]}" for c in m_cols}
                        pivot_m_disp = pivot_m[['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'] + m_cols].rename(columns=label_mois)
                        st.dataframe(pivot_m_disp, width='stretch', height=350)
                        st.download_button(
                            "📥 Télécharger (Par mois)",
                            data=to_excel_bytes(pivot_m_disp),
                            file_name=f"historique_par_mois_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width='stretch', key="dl_mois"
                        )

                    with sous_tab_t:
                        df_hist['ANNEE_T'] = df_hist['INVOICE_YEAR'].astype(str) + "-T" + df_hist['TRIMESTRE'].astype(str)
                        pivot_t = df_hist.pivot_table(
                            index=['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'],
                            columns='ANNEE_T', values='QTE', aggfunc='sum', fill_value=0
                        ).reset_index()
                        t_cols = sorted([c for c in pivot_t.columns
                                         if c not in ['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA']])
                        pivot_t_disp = pivot_t[['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'] + t_cols]
                        st.dataframe(pivot_t_disp, width='stretch', height=350)
                        st.download_button(
                            "📥 Télécharger (Par trimestre)",
                            data=to_excel_bytes(pivot_t_disp),
                            file_name=f"historique_par_trimestre_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width='stretch', key="dl_trimestre"
                        )

                    with sous_tab_an:
                        st.caption("Années réelles (passé, vente facturée) et années programme "
                                   f"({annee_actuelle}/{annee_suivante}, prévu) sur la même ligne, "
                                   "pour voir le futur à côté de l'historique.")
                        # Réel par année (toutes les années chargées, ex: 2024, 2025, et YTD en cours)
                        pivot_an_reel = df_hist.pivot_table(
                            index=['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'],
                            columns='INVOICE_YEAR', values='QTE', aggfunc='sum', fill_value=0
                        ).reset_index()
                        an_reel_cols = sorted([c for c in pivot_an_reel.columns if isinstance(c, (int, float))])
                        rename_an = {c: f"{int(c)} (réel)" for c in an_reel_cols}
                        pivot_an_reel = pivot_an_reel.rename(columns=rename_an)
                        an_reel_cols_renamed = [rename_an[c] for c in an_reel_cols]

                        # Programme actuel + suivant (depuis synth déjà calculé, on réutilise)
                        cols_prog_an = [c for c in [f'Qty {annee_actuelle}', f'Qty {annee_suivante}'] if c in synth.columns]
                        prog_an = synth[['CODE_CLIENT', 'REF_ARTICLE_SERTA'] + cols_prog_an].rename(
                            columns={f'Qty {annee_actuelle}': f'{annee_actuelle} (réel+reste prog)',
                                     f'Qty {annee_suivante}': f'{annee_suivante} (prog)'})

                        pivot_an_complet = pivot_an_reel.merge(
                            prog_an, on=['CODE_CLIENT', 'REF_ARTICLE_SERTA'], how='outer').fillna(0)
                        # Blindage anti-pyarrow : le merge outer + fillna(0) ci-dessus
                        # transforme les CLIENT_NAME absents (lignes programme sans
                        # historique réel, côté droit du merge) en 0 (int) -> colonne
                        # mixte str/int qui fait planter l'affichage. On restaure le
                        # texte pur sur les colonnes d'identité (le 0 parasite -> vide).
                        for _c in ['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA']:
                            if _c in pivot_an_complet.columns:
                                pivot_an_complet[_c] = (
                                    pivot_an_complet[_c].replace(0, '').fillna('').astype(str))

                        # Gap année en cours vs année précédente, sur ce tableau aussi
                        col_n = f'{annee_actuelle} (réel+reste prog)'
                        col_n1 = f'{annee_precedente} (réel)'
                        if col_n in pivot_an_complet.columns and col_n1 in pivot_an_complet.columns:
                            pivot_an_complet[f'Écart {annee_actuelle} vs {annee_precedente}'] = (
                                pivot_an_complet[col_n] - pivot_an_complet[col_n1])

                        cols_finales = ['CODE_CLIENT', 'CLIENT_NAME', 'REF_ARTICLE_SERTA'] + an_reel_cols_renamed
                        cols_finales += [c for c in [col_n, f'{annee_suivante} (prog)',
                                          f'Écart {annee_actuelle} vs {annee_precedente}'] if c in pivot_an_complet.columns]
                        cols_finales = list(dict.fromkeys([c for c in cols_finales if c in pivot_an_complet.columns]))
                        pivot_an_disp = pivot_an_complet[cols_finales]
                        st.dataframe(pivot_an_disp, width='stretch', height=350)
                        st.download_button(
                            "📥 Télécharger (Par année)",
                            data=to_excel_bytes(pivot_an_disp),
                            file_name=f"historique_par_annee_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width='stretch', key="dl_annee"
                        )


# ── Statut session ────────────────────────────────────────────────────────────
st.markdown("---")
if 'df_consolide' in st.session_state:
    df_c = st.session_state['df_consolide']
    wk_c = wk_cols_from_df(df_c)
    st.success(f"📦 Fichier consolidé en session — {len(df_c)} lignes, {len(wk_c)} semaines")
else:
    st.info("Aucun fichier consolidé en session — agrégez dans l'onglet **Agréger**")