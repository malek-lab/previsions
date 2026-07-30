import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shared import get_engine, logo_sidebar, wk_cols_from_df, to_excel_bytes

st.title("📦 Agrégation — LPC + Carnet de commande")

if get_engine() is None:
    st.stop()

DATES_FICTIVES = ['2030-12-31', '2075-12-31', '2099-12-31']

def semaine_label(d):
    try:
        iso = d.isocalendar()
        return f"S{str(iso[0])[2:]}-{iso[1]:02d}"
    except:
        return None

def wk_label_to_date(label):
    try:
        yy, ww = int('20'+label[1:3]), int(label[4:6])
        return dt.date.fromisocalendar(yy, ww, 1)
    except:
        return None

def extract_code_client(programme_nom):
    try:
        return str(programme_nom).split('_')[0]
    except:
        return None

with st.sidebar:
    logo_sidebar()
    st.header("⚙️ Info")
    st.markdown("---")
    st.info(
        "Cette page repart des données **Programmes agrégés (page 02)** "
        "(Lasernet + Hors Lasernet) et y ajoute "
        "les lignes du carnet de commande dont le couple "
        "`CODE_CLIENT + REF_ARTICLE_SERTA` n'est pas couvert.\n\n"
        "👈 Allez d'abord sur la page **📂 Dépôt & Agrégation** "
        "et agrégez les fichiers pour alimenter cette vue."
    )
    st.markdown("---")
    st.subheader("⚙️ Paramètres carnet")
    # AJOUT : une seule source de verite pour le cutoff dans toute la page --
    # date_prevision (session, definie en page 01) est LA reference. Avant, cette
    # page avait son propre champ date_carnet_du independant, qui pouvait diverger
    # de date_prevision -- charger_carnet() utilisait alors un cutoff different de
    # charger_suivi_carnet()/charger_facture_recent_hors_couverture(), qui eux
    # restaient sur l'ancien date_prevision. Resultat : deux fonctions de la meme
    # page pouvaient traiter "retard vs encours" avec 2 dates de coupure differentes,
    # sans avertissement -- source d'incoherences silencieuses.
    date_prevision_ref = st.session_state.get('date_prevision', datetime.now().date())
    date_carnet_du = st.date_input("📅 Semaines à partir du",
        value=date_prevision_ref,
        format="DD/MM/YYYY",
        help="Initialisée sur la Date de prévision de la page 01, mais librement "
             "modifiable ici. Toutes les fonctions de cette page (carnet, "
             "retard/encours, facturé récent) utilisent cette même date -- "
             "pas besoin de repasser par la page 01.")
    date_carnet_au = st.date_input("📅 Au",
        value=st.session_state.get('date_carnet_au', datetime.now().date() + timedelta(weeks=52)),
        format="DD/MM/YYYY")
    # AJOUT : plafonner la date ARC (CLIENT_ACK_DATE) a fin 2027, quelle que
    # soit la valeur choisie ci-dessus -- regle metier : on ne prend jamais de
    # commande carnet avec un ARC au-dela du 31/12/2027.
    PLAFOND_ARC = dt.date(2027, 12, 31)
    if date_carnet_au > PLAFOND_ARC:
        date_carnet_au = PLAFOND_ARC
        st.caption("⚠️ Date plafonnée au 31/12/2027 (règle métier ARC carnet)")
    # AJOUT : persister sous des cles PROPRES a cette page (date_carnet_du/au),
    # differentes de date_filtre_du/au utilisees par la page 01 pour un tout
    # autre filtre ("besoins client") -- c'est cette collision de nom qui
    # causait des resultats incoherents/impredictibles en aval (page 04 lisait
    # la mauvaise date sans le savoir). Les autres pages doivent lire CES cles
    # (date_carnet_du/date_carnet_au), plus jamais date_filtre_du/date_filtre_au.
    st.session_state['date_carnet_du'] = date_carnet_du
    st.session_state['date_carnet_au'] = date_carnet_au
    st.markdown("---")
    btn_ajouter_carnet = st.button("🔄 Charger / Actualiser carnet", type="primary", width="stretch")

def charger_consolide_depuis_base():
    try:
        import pyodbc
        from io import BytesIO
        conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=W25-DWDI;DATABASE=master;Trusted_Connection=yes;"
            "Connect Timeout=300;"
        )
        conn = pyodbc.connect(conn_str, timeout=60)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1 Fichier FROM [dbo].[T_PREVISION_FICHIERS]
            WHERE Source = 'CONSOLIDE_VALIDE'
            ORDER BY DATE_MODIF DESC
        """)
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        df = pd.read_excel(BytesIO(bytes(row[0])))
        df.columns = [str(c) for c in df.columns]
        return df
    except Exception as e:
        print(f"Erreur chargement consolidé depuis base : {e}")
        return None

if 'df_consolide' in st.session_state:
    df_lpc = st.session_state['df_consolide'].copy()
    st.sidebar.success("✅ Données depuis la session (page 02)")
else:
    df_lpc = charger_consolide_depuis_base()
    if df_lpc is not None:
        st.sidebar.info("📦 Données chargées depuis la base SQL")
    else:
        st.warning(
            "⚠️ Aucune donnée — allez sur la page **📂 Dépôt & Agrégation**, "
            "agrégez les fichiers et cliquez sur **✅ Valider & Sauvegarder**."
        )
        st.stop()

wk_lpc = wk_cols_from_df(df_lpc)

if 'PROGRAMME' in df_lpc.columns:
    df_lpc['CODE_CLIENT'] = df_lpc['PROGRAMME'].apply(extract_code_client).astype(str)
elif 'CODE_CLIENT' in df_lpc.columns:
    df_lpc['CODE_CLIENT'] = df_lpc['CODE_CLIENT'].astype(str).str.strip()
else:
    df_lpc['CODE_CLIENT'] = ''

df_lpc['CODE_CLIENT'] = df_lpc['CODE_CLIENT'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

# AJOUT : filtre GRP jamais applique a df_lpc jusqu'ici -- seulement present
# dans charger_carnet() et charger_retard_reconstruit_historique(). Le
# programme (fichier Excel Hors Lasernet) peut tout aussi bien contenir des
# codes clients groupe (GRP...) a exclure.
nb_avant_grp = len(df_lpc)
df_lpc = df_lpc[~df_lpc['CODE_CLIENT'].str.startswith('GRP')]
nb_grp_retires = nb_avant_grp - len(df_lpc)
if nb_grp_retires > 0:
    st.info(f"🧹 {nb_grp_retires} ligne(s) programme retirée(s) (code client groupe GRP...).")

if 'ORIGINE' not in df_lpc.columns:
    if 'Source' in df_lpc.columns:
        df_lpc['ORIGINE'] = df_lpc['Source'].map(
            {'LASERNET': 'LPC', 'HORS_LASERNET': 'MANUEL'}
        ).fillna('LPC')
    else:
        df_lpc['ORIGINE'] = 'LPC'

df_lpc['ORIGINE'] = df_lpc['ORIGINE'].replace({'HORS_LASERNET': 'MANUEL'})

# AJOUT : filtrer df_lpc sur le meme perimetre que le carnet (hors PDR/
# composants) -- Sophie ne suit pas ces categories, confirme par le vrai
# CODE_GROUPE_ARTICLE (pas une deduction sur un nom de client). df_lpc vient
# d'un fichier Excel qui n'a jamais eu cette colonne, donc on va la chercher
# via V_ART_STANDARD, meme pour des refs qui n'ont plus de commande ouverte.
@st.cache_data(ttl=3600, show_spinner=False)
def charger_groupe_article(refs_tuple):
    """Recupere le vrai CODE_GROUPE_ARTICLE pour une liste de refs, depuis
    V_ART_STANDARD (independant du statut de commande). Mis en cache 1h car
    ces refs ne changent pas d'un rafraichissement a l'autre.

    CORRIGE : decoupe en lots de 200 refs -- avec ~1400+ refs d'un coup, la
    clause IN(...) devient enorme (dizaines de milliers de caracteres) et
    peut faire echouer OPENQUERY silencieusement (taille/timeout), skippant
    tout le filtre sans que ce soit evident a l'ecran.
    """
    from sqlalchemy import text
    engine = get_engine()
    if engine is None or not refs_tuple:
        return pd.DataFrame(columns=['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE'])

    TAILLE_LOT = 200
    lots = [refs_tuple[i:i+TAILLE_LOT] for i in range(0, len(refs_tuple), TAILLE_LOT)]
    resultats = []
    erreurs = 0
    progress = st.progress(0, text="Vérification du groupe article (programme)...")
    for i, lot in enumerate(lots):
        refs_sql = "', '".join(r.replace("'", "''") for r in lot)
        try:
            with engine.connect() as conn:
                df_lot = pd.read_sql(text(f"""
                    SELECT *
                    FROM OPENQUERY([SRV-MSSQLDB], '
                        SELECT REF_ARTICLE, CODE_GROUPE_ARTICLE
                        FROM DW.PRODUCTION.V_ART_STANDARD
                        WHERE REF_ARTICLE IN (''{refs_sql}'')
                    ')
                """), conn)
            df_lot.columns = ['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE']
            resultats.append(df_lot)
        except Exception:
            erreurs += 1
        progress.progress((i+1)/len(lots), text=f"Vérification groupe article... lot {i+1}/{len(lots)}")
    progress.empty()

    if erreurs > 0:
        st.warning(f"⚠️ {erreurs}/{len(lots)} lot(s) de vérification groupe article ont échoué "
                   f"(sur {len(lots)} au total) -- filtre PDR/composants partiel.")
    if not resultats:
        return pd.DataFrame(columns=['REF_ARTICLE_SERTA', 'VRAI_GROUPE_ARTICLE'])

    df = pd.concat(resultats, ignore_index=True)
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    return df.drop_duplicates('REF_ARTICLE_SERTA')

GROUPES_HORS_PERIMETRE = ['PDR', 'PFPDR', 'MAUNIT', 'MAJOIN']  # PDR/PFPDR selon la source

refs_lpc_tuple = tuple(sorted(df_lpc['REF_ARTICLE_SERTA'].dropna().astype(str).str.strip().unique()))
df_groupe_lpc = charger_groupe_article(refs_lpc_tuple)
if not df_groupe_lpc.empty:
    nb_avant = len(df_lpc)
    df_lpc = df_lpc.merge(df_groupe_lpc, on='REF_ARTICLE_SERTA', how='left')
    hors_perimetre = df_lpc['VRAI_GROUPE_ARTICLE'].isin(GROUPES_HORS_PERIMETRE)
    nb_retirees = hors_perimetre.sum()
    df_lpc = df_lpc[~hors_perimetre].drop(columns=['VRAI_GROUPE_ARTICLE'])
    if nb_retirees > 0:
        st.info(f"🧹 {nb_retirees} ligne(s) programme retirée(s) "
                f"(pièces détachées/composants, hors périmètre suivi par Sophie).")

couples_lpc = set(
    df_lpc['CODE_CLIENT'].astype(str).str.strip() + '|' +
    df_lpc['REF_ARTICLE_SERTA'].astype(str).str.strip()
)

def charger_carnet(couples_lpc, date_du, date_au):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("""
                SELECT
                    CAST(SERTA_SO_CLIENT_CODE AS VARCHAR(50))      AS CODE_CLIENT,
                    CAST(ITEM_REF AS VARCHAR(50))                  AS REF_ARTICLE_SERTA,
                    CAST(ITEM_CLIENT_REF AS VARCHAR(100))          AS REF_ARTICLE_CLIENT,
                    ITEM_MAIN_PRODUCTION_UNIT       AS UP_PRINCIPALE,
                    ITEM_ORDER_MIN_QTY              AS QTE_MOQ,
                    ITEM_PACKAGED_UNIT_QTY          AS QTE_UC,
                    SERTA_SO_CLIENT_GROUP_NAME,
                    SERTA_SO_CLIENT_NAME,
                    SALES_ADMINISTRATION_PERSON,
                    CLIENT_ACK_DATE,
                    SERTA_SO_STILL_TO_BE_DELIVERED_QTY AS QTE,
                    ITEM_GROUP_CODE,
                    SERTA_SO_STATUS_MIN,
                    CLIENT_ORDER_NUM,
                    DATE_LIGNE
                FROM [master].[dbo].[V_SUPPLY_CHAIN]
            """), conn)
    except Exception as e:
        st.error(f"Erreur carnet : {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    df['CLIENT_ACK_DATE'] = pd.to_datetime(df['CLIENT_ACK_DATE'], errors='coerce')
    df = df[~df['CLIENT_ACK_DATE'].dt.strftime('%Y-%m-%d').isin(DATES_FICTIVES)]
    df['QTE'] = pd.to_numeric(df['QTE'], errors='coerce').fillna(0)
    df['ITEM_GROUP_CODE'] = df['ITEM_GROUP_CODE'].fillna('').astype(str).str.strip()
    df['SERTA_SO_STATUS_MIN'] = pd.to_numeric(df['SERTA_SO_STATUS_MIN'], errors='coerce')
    df['CLIENT_ORDER_NUM'] = df['CLIENT_ORDER_NUM'].fillna('').astype(str).str.strip()

    # AJOUT : reconstruction historique du cutoff -- le STATUT (< 7) est calcule
    # en direct par la vue (jamais "rembobinable"), mais DATE_LIGNE (creation de
    # la ligne de commande) est un fait historique fige. On exclut donc les
    # lignes creees APRES date_du, pour approximer "le carnet tel qu'il etait
    # a la date choisie" plutot que "le carnet live filtre juste sur les dates
    # ARC". Les lignes sans DATE_LIGNE renseignee sont conservees par prudence
    # (on ne peut pas prouver qu'elles sont "trop recentes").
    df['DATE_LIGNE'] = pd.to_datetime(df['DATE_LIGNE'], errors='coerce')
    df = df[df['DATE_LIGNE'].isna() | (df['DATE_LIGNE'].dt.date <= date_du)]
    if df.empty:
        return pd.DataFrame()

    df['SEMAINE'] = df['CLIENT_ACK_DATE'].apply(lambda d: semaine_label(d) if pd.notna(d) else None)
    df = df[df['SEMAINE'].notna()]

    # CORRIGE (bug trouve par diagnostic) : filtrer sur la VRAIE date ARC
    # (CLIENT_ACK_DATE), pas sur le lundi de la semaine qui la contient --
    # comparer wk_label_to_date(semaine) introduisait un decalage jusqu'a 6
    # jours pres des frontieres de semaine.
    #
    # CORRIGE (bug plus grave, trouve juste apres) : on ne filtre ICI que la
    # borne HAUTE (date_au), PAS la borne basse (date_du) -- sinon
    # df_retard = df[CLIENT_ACK_DATE < date_du], calcule plus bas, est
    # TOUJOURS VIDE puisque df aurait deja exclu toute ARC anterieure au
    # cutoff avant meme d'essayer de l'isoler. Une commande en retard d'un
    # seul jour (ex: ARC 29/06 pour un cutoff au 30/06) disparaissait donc
    # entierement au lieu d'etre comptee comme retard. La borne basse
    # (date_du) n'est appliquee que plus bas, sur une copie dediee au pivot
    # hebdomadaire (df_pour_pivot), pas sur df lui-meme.
    df = df[df['CLIENT_ACK_DATE'].dt.date <= date_au]
    if df.empty:
        return pd.DataFrame()

    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df = df[~df['CODE_CLIENT'].str.startswith('GRP')]
    if df.empty:
        return pd.DataFrame()

    df_retard  = df[df['CLIENT_ACK_DATE'].dt.date <  date_du]
    df_encours = df[(df['CLIENT_ACK_DATE'].dt.date >= date_du) & (df['CLIENT_ACK_DATE'].dt.date <= date_au)]

    # NOTE : on NE filtre PAS df sur date_du ici -- une commande en retard
    # (ARC < date_du) doit quand meme produire une ligne CARNET (avec sa
    # quantite dans la semaine passee correspondante + son
    # QTE_BESOIN_CLIENT_RETARD_SC renseigne), sinon la ref disparait
    # completement au lieu d'apparaitre comme "en retard". La colonne semaine
    # passee sera simplement hors de la plage affichee par wk_in_range plus
    # bas dans le fichier, ce qui est correct (on n'affiche pas de semaines
    # passees), mais la LIGNE (couple+meta+retard) reste bien presente.

    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']
    df = df[~df['_COUPLE'].isin(couples_lpc)].drop(columns=['_COUPLE'])

    if df.empty:
        return pd.DataFrame()

    df['ORIGINE']        = 'CARNET'
    df['PROGRAMME']      = ''
    df['CODE_SELECTION'] = ''
    df['QTE_TOTALE']     = None

    besoin_retard  = df_retard.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['QTE'].sum().reset_index()
    besoin_retard.columns  = ['CODE_CLIENT','REF_ARTICLE_SERTA','QTE_BESOIN_CLIENT_RETARD_SC']
    besoin_encours = df_encours.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['QTE'].sum().reset_index()
    besoin_encours.columns = ['CODE_CLIENT','REF_ARTICLE_SERTA','QTE_BESOIN_CLIENT_ENCOURS_SC']

    diag_groupe = (df.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['ITEM_GROUP_CODE']
                      .agg(lambda s: s.mode().iat[0] if not s.mode().empty else '')
                      .reset_index())
    diag_statut = (df.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['SERTA_SO_STATUS_MIN']
                      .min().reset_index())
    # Un couple peut avoir plusieurs commandes -> on concatene les numeros de
    # commande client distincts (utile pour reperer les pseudo-commandes filtrees)
    diag_num_commande = (df.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['CLIENT_ORDER_NUM']
                            .agg(lambda s: ' / '.join(sorted(set(x for x in s if x))))
                            .reset_index())

    meta = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION', 'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']
    meta = [c for c in meta if c in df.columns]
    for c in meta:
        if df[c].dtype == object:
            df[c] = df[c].fillna('').astype(str)
        else:
            df[c] = df[c].fillna(0)

    agg   = df.groupby(meta + ['SEMAINE'])['QTE'].sum().reset_index()
    pivot = agg.pivot_table(index=meta, columns='SEMAINE', values='QTE',
                            aggfunc='sum', fill_value=0).reset_index()
    pivot.columns.name = None

    pivot = pivot.merge(besoin_retard,  on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    pivot = pivot.merge(besoin_encours, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    pivot = pivot.merge(diag_groupe, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    pivot = pivot.merge(diag_statut, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    pivot = pivot.merge(diag_num_commande, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
    for col in ['QTE_BESOIN_CLIENT_RETARD_SC','QTE_BESOIN_CLIENT_ENCOURS_SC']:
        pivot[col] = pd.to_numeric(pivot[col], errors='coerce').fillna(0).astype(int)
    return pivot


def charger_suivi_carnet(date_prevision, date_ventilation):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        dp = date_prevision.strftime('%Y-%m-%d')
        dv = date_ventilation.strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT
                    CODE_CLIENT,
                    REF_ARTICLE_SERTA,
                    SUM(CASE WHEN DATE_FACTURE IS NULL
                              AND DATE_EXPEDITION <= '{dp}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_EN_TRANSITE_RETARD,
                    SUM(CASE WHEN DATE_FACTURE IS NULL
                              AND DATE_EXPEDITION >  '{dp}'
                              AND DATE_EXPEDITION <= '{dv}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_EN_TRANSITE_ENCOURS,
                    SUM(CASE WHEN DATE_FACTURE IS NOT NULL
                              AND DATE_FACTURE >  '{dp}'
                              AND DATE_FACTURE <= '{dv}'
                        THEN QTE_EXPEDIEE ELSE 0 END)  AS QTE_FACTUREE_ENCOURS
                FROM [master].[dbo].[V_EXPEDITION_SUIVI]
                GROUP BY CODE_CLIENT, REF_ARTICLE_SERTA
            """), conn)
        df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.strip()
        df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
        return df
    except Exception as e:
        st.warning(f"Suivi expédition non disponible : {e}")
        return pd.DataFrame()


def charger_facture_recent_hors_couverture(couples_couverts, date_prevision):
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        # CORRIGE : "depuis le 1er du mois" seul cree un trou en debut de mois
        # (ex: le 3 juillet, ca ne couvre que 3 jours) -- une ref facturee fin
        # juin sortirait du carnet (statut>=7) SANS etre captee ici. On prend
        # donc le MAX de couverture entre "1er du mois" et "14 jours glissants",
        # pour garantir au moins 2 semaines de visibilite en toute circonstance.
        premier_du_mois = date_prevision.replace(day=1)
        quinze_jours_avant = date_prevision - timedelta(days=14)
        date_debut = min(premier_du_mois, quinze_jours_avant).strftime('%Y-%m-%d')
        date_fin   = date_prevision.strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT
                    CODE_CLIENT,
                    REF_ARTICLE_SERTA,
                    SUM(QTE_EXPEDIEE) AS QTE_FACTUREE_RECENTE,
                    MAX(DATE_FACTURE) AS DERNIERE_FACTURE
                FROM [master].[dbo].[V_EXPEDITION_SUIVI]
                WHERE DATE_FACTURE IS NOT NULL
                  AND DATE_FACTURE >= '{date_debut}'
                  AND DATE_FACTURE <= '{date_fin}'
                GROUP BY CODE_CLIENT, REF_ARTICLE_SERTA
            """), conn)
    except Exception as e:
        st.warning(f"Recherche facturé récent non disponible : {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']

    df = df[~df['_COUPLE'].isin(couples_couverts)].drop(columns=['_COUPLE'])
    if df.empty:
        return pd.DataFrame()

    df['ORIGINE']        = 'FACTURE_RECENTE'
    df['PROGRAMME']      = ''
    df['CODE_SELECTION'] = ''
    df['REF_ARTICLE_CLIENT'] = ''
    df['UP_PRINCIPALE']  = ''
    df['QTE_UC']         = 0
    df['QTE_MOQ']        = 0
    df['QTE_TOTALE']     = df['QTE_FACTUREE_RECENTE']

    return df


def charger_facture_entre_cutoff_et_aujourdhui(couples_deja_dans_carnet, date_cutoff):
    """
    Comble le "trou n1" de la reconstruction historique : une commande ouverte
    au cutoff (date_cutoff) mais facturee depuis (entre date_cutoff et
    aujourd'hui) est sortie du carnet LIVE (statut >= 7 maintenant), et le
    filtre DATE_LIGNE ne peut pas la retenir puisqu'elle existait bien avant le
    cutoff. Sans ce complement, elle disparaitrait purement et simplement de la
    vue reconstruite -- alors qu'elle etait bien "ferme, non facturee" au 21/06.

    On ne l'ajoute QUE si son couple n'est pas deja present dans le carnet
    reconstruit (anti-doublon : si elle y est deja par un autre biais, on ne la
    compte pas deux fois).
    """
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        date_debut = date_cutoff.strftime('%Y-%m-%d')
        date_fin   = datetime.now().date().strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT
                    CODE_CLIENT,
                    REF_ARTICLE_SERTA,
                    SUM(QTE_EXPEDIEE) AS QTE_FACTUREE_DEPUIS_CUTOFF
                FROM [master].[dbo].[V_EXPEDITION_SUIVI]
                WHERE DATE_FACTURE IS NOT NULL
                  AND DATE_FACTURE >= '{date_debut}'
                  AND DATE_FACTURE <= '{date_fin}'
                GROUP BY CODE_CLIENT, REF_ARTICLE_SERTA
            """), conn)
    except Exception as e:
        st.warning(f"Recherche facturé depuis cutoff non disponible : {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.strip()
    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']

    # Anti-doublon : exclure les couples deja presents dans le carnet reconstruit
    df = df[~df['_COUPLE'].isin(couples_deja_dans_carnet)].drop(columns=['_COUPLE'])
    if df.empty:
        return pd.DataFrame()

    df['ORIGINE']        = 'CARNET'
    df['PROGRAMME']      = ''
    df['CODE_SELECTION'] = ''
    df['REF_ARTICLE_CLIENT'] = ''
    df['UP_PRINCIPALE']  = ''
    df['QTE_UC']         = 0
    df['QTE_MOQ']        = 0
    df['QTE_TOTALE']     = df['QTE_FACTUREE_DEPUIS_CUTOFF']

    return df


def charger_retard_reconstruit_historique(couples_deja_dans_carnet, date_cutoff):
    """
    Complement au carnet reconstruit : capte les commandes qui etaient
    probablement encore ouvertes au cutoff mais qui n'apparaissent plus dans
    V_SUPPLY_CHAIN aujourd'hui (celle-ci exclut tout ce qui est passe
    STATUT=7, sans notion de "etait-ce deja le cas au cutoff ?").

    CRITERE (valide sur un echantillon reel -- capture 6/6 cas testes) :
    la ligne existait deja au cutoff (DATE_LIGNE <= cutoff) ET sa VRAIE date de
    facturation (DATE_FACTURE, depuis V_EXPEDITION) est soit posterieure au
    cutoff, soit inexistante (jamais encore facturee).

    Amelioration par rapport a une version precedente qui utilisait DATE_ARC
    (date de livraison SOUHAITEE par le client) comme proxy -- DATE_ARC est un
    objectif, pas un fait constate, et rate les commandes livrees en retard
    par rapport a leur promesse. DATE_FACTURE (evenement reel, trouve dans
    V_EXPEDITION) ne souffre pas de ce probleme.
    """
    from sqlalchemy import text
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        cutoff_str = date_cutoff.strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df = pd.read_sql(text(f"""
                SELECT *
                FROM OPENQUERY([SRV-MSSQLDB], '
                    SELECT
                        VSOLFR.REF_ARTICLE       AS REF_ARTICLE_SERTA,
                        VSOLFR.CODE_CLIENT,
                        VSOLFR.NOM_CLIENT        AS SERTA_SO_CLIENT_NAME,
                        VSOLFR.CODE_GROUPE_ARTICLE AS ITEM_GROUP_CODE,
                        VSOLFR.DATE_LIGNE,
                        CASE
                            WHEN VSOLFR.QTE_RELIQUAT = 0
                                THEN VSOLFR.QTE_COMMANDEE - VSOLFR.QTE_LIVREE
                            ELSE VSOLFR.QTE_RELIQUAT
                        END AS QTE,
                        MAX(VEXP.DATE_FACTURE)   AS DERNIERE_FACTURE
                    FROM DW.VENTE.V_LIGNE_ORDRE VSOLFR
                    LEFT JOIN DW.VENTE.V_EXPEDITION VEXP ON VSOLFR.LOR_ID = VEXP.LOR_ID
                    WHERE VSOLFR.DATE_LIGNE <= ''{cutoff_str}''
                      -- AJOUT : exclure les dates ARC fictives (commandes-cadres
                      -- sans echeance reelle, cf. DATES_FICTIVES cote Python) --
                      -- meme filtre que charger_carnet(), initialement oublie
                      -- dans cette fonction. Sans ca, on rattrapait a tort des
                      -- commandes ouvertes depuis 2015-2024 sans aucune date de
                      -- livraison engagee (ARC placeholder 2030/2075/2099).
                      AND (VSOLFR.DATE_ARC IS NULL OR VSOLFR.DATE_ARC NOT IN (
                          ''2030-12-31'', ''2075-12-31'', ''2099-12-31''
                      ))
                      AND VSOLFR.CODE_GROUPE_ARTICLE IN (
                          ''PF1'', ''PF2'', ''PF3'', ''PF4'', ''PF5'',
                          ''PFPACK'', ''PFPWP''
                      )
                      AND (VSOLFR.NUM_ORDRE_CLIENT IS NULL OR UPPER(LTRIM(RTRIM(VSOLFR.NUM_ORDRE_CLIENT))) NOT IN (
                          ''COMMANDE STOCK'', ''STOCK A FERRAILLER'', ''FORECAST'',
                          ''PRIX USD'', ''PRIX EUR''
                      ))
                      AND (
                          UPPER(VSOLFR.NOM_CLIENT) NOT LIKE ''%SERTA%''
                          OR UPPER(VSOLFR.NOM_CLIENT) LIKE ''%SERTA AMERICA%''
                      )
                    GROUP BY
                        VSOLFR.REF_ARTICLE, VSOLFR.CODE_CLIENT, VSOLFR.NOM_CLIENT,
                        VSOLFR.CODE_GROUPE_ARTICLE, VSOLFR.DATE_LIGNE,
                        VSOLFR.QTE_RELIQUAT, VSOLFR.QTE_COMMANDEE, VSOLFR.QTE_LIVREE
                    HAVING (MAX(VEXP.DATE_FACTURE) > ''{cutoff_str}''
                            OR MAX(VEXP.DATE_FACTURE) IS NULL)
                        AND (CASE
                                WHEN VSOLFR.QTE_RELIQUAT = 0
                                    THEN VSOLFR.QTE_COMMANDEE - VSOLFR.QTE_LIVREE
                                ELSE VSOLFR.QTE_RELIQUAT
                             END) > 0
                ')
            """), conn)
    except Exception as e:
        st.warning(f"Reconstruction historique retard non disponible : {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df['CODE_CLIENT']       = df['CODE_CLIENT'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['REF_ARTICLE_SERTA'] = df['REF_ARTICLE_SERTA'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['QTE'] = pd.to_numeric(df['QTE'], errors='coerce').fillna(0)
    df = df[~df['CODE_CLIENT'].str.startswith('GRP')]
    if df.empty:
        return pd.DataFrame()

    df['_COUPLE'] = df['CODE_CLIENT'] + '|' + df['REF_ARTICLE_SERTA']
    df = df[~df['_COUPLE'].isin(couples_deja_dans_carnet)].drop(columns=['_COUPLE'])
    if df.empty:
        return pd.DataFrame()

    agg = df.groupby(['CODE_CLIENT','REF_ARTICLE_SERTA'])['QTE'].sum().reset_index()
    agg['ORIGINE']        = 'CARNET'
    agg['PROGRAMME']      = ''
    agg['CODE_SELECTION'] = ''
    agg['REF_ARTICLE_CLIENT'] = ''
    agg['UP_PRINCIPALE']  = ''
    agg['QTE_UC']         = 0
    agg['QTE_MOQ']        = 0
    agg['QTE_TOTALE']     = agg['QTE']
    agg = agg.drop(columns=['QTE'])

    return agg


if btn_ajouter_carnet:
    with st.spinner("⏳ Chargement carnet de commande..."):
        df_carnet = charger_carnet(couples_lpc, date_carnet_du, date_carnet_au)
        # CORRIGE : on utilisait date_prevision (page 01, potentiellement perimee
        # si date_carnet_du a ete modifiee ici) pour le calcul du "facture recent",
        # alors que le carnet lui-meme utilise date_carnet_du. Deux dates de
        # reference differentes = incoherence de cutoff entre les deux mecanismes.
        # On aligne tout sur date_carnet_du, la seule que l'utilisateur controle
        # explicitement sur CETTE page.
        date_prev = date_carnet_du
        date_vent = date_carnet_au
        df_suivi  = charger_suivi_carnet(date_prev, date_vent)

    frames_all = [df_lpc]
    if df_carnet.empty:
        st.info("ℹ️ Aucune ligne carnet à ajouter — tous les couples sont couverts.")
    else:
        st.success(f"✅ {len(df_carnet)} lignes carnet ajoutées")
        frames_all.append(df_carnet)

    couples_lpc_carnet = set(couples_lpc)
    if not df_carnet.empty:
        couples_lpc_carnet |= set(
            df_carnet['CODE_CLIENT'].astype(str).str.strip() + '|' +
            df_carnet['REF_ARTICLE_SERTA'].astype(str).str.strip()
        )

    # AJOUT : reintegrer ce qui etait ouvert au cutoff mais facture depuis --
    # sorti du carnet live (statut>=7 maintenant), pas retenu par le filtre
    # DATE_LIGNE puisque la ligne existait bien avant le cutoff. Anti-doublon
    # via couples_lpc_carnet (deja couverts = pas re-ajoutes).
    with st.spinner("⏳ Reconstruction du cutoff historique (facturé depuis)..."):
        df_facture_depuis_cutoff = charger_facture_entre_cutoff_et_aujourdhui(
            couples_lpc_carnet, date_carnet_du)

    if not df_facture_depuis_cutoff.empty:
        st.info(f"🔄 {len(df_facture_depuis_cutoff)} ligne(s) réintégrée(s) "
                f"(ouvertes au {date_carnet_du.strftime('%d/%m/%Y')}, facturées depuis).")
        frames_all.append(df_facture_depuis_cutoff)
        couples_lpc_carnet |= set(
            df_facture_depuis_cutoff['CODE_CLIENT'].astype(str).str.strip() + '|' +
            df_facture_depuis_cutoff['REF_ARTICLE_SERTA'].astype(str).str.strip()
        )

    # AJOUT : complement best-effort -- commandes probablement encore ouvertes
    # au cutoff (DATE_LIGNE<=cutoff, ARC>=cutoff) mais invisibles de
    # V_SUPPLY_CHAIN aujourd'hui car deja passees STATUT=7. Amelioration
    # partielle (ARC = objectif client, pas un fait constate -- ne recupere
    # pas les commandes facturees en retard par rapport a leur ARC), voir
    # docstring de la fonction pour le detail de la limite.
    with st.spinner("⏳ Reconstruction complémentaire (commandes closes depuis)..."):
        df_retard_reconstruit = charger_retard_reconstruit_historique(
            couples_lpc_carnet, date_carnet_du)

    if not df_retard_reconstruit.empty:
        st.info(f"🔄 {len(df_retard_reconstruit)} ligne(s) supplémentaire(s) "
                f"reconstruite(s) (probablement ouvertes au cutoff, closes depuis "
                f"-- reconstruction approximative, voir doc).")
        frames_all.append(df_retard_reconstruit)
        couples_lpc_carnet |= set(
            df_retard_reconstruit['CODE_CLIENT'].astype(str).str.strip() + '|' +
            df_retard_reconstruit['REF_ARTICLE_SERTA'].astype(str).str.strip()
        )

    with st.spinner("⏳ Recherche du facturé récent hors couverture..."):
        df_facture_recent = charger_facture_recent_hors_couverture(couples_lpc_carnet, date_prev)

    if df_facture_recent.empty:
        st.info("ℹ️ Aucune référence facturée récemment hors couverture.")
    else:
        st.warning(
            f"⚠️ {len(df_facture_recent)} référence(s) facturée(s) récemment "
            "(depuis le 1er du mois, ou les 14 derniers jours si plus large) sans programme actif ni carnet ouvert — ajoutées."
        )
        frames_all.append(df_facture_recent)

    df_all = pd.concat(
        [f.dropna(axis=1, how='all') for f in frames_all],
        ignore_index=True, sort=False)

    if 'CODE_CLIENT' in df_all.columns and 'REF_ARTICLE_SERTA' in df_all.columns:
        priorite_origine = {'LPC': 0, 'MANUEL': 1, 'HORS_LASERNET': 1, 'CARNET': 2, 'FACTURE_RECENTE': 3}
        df_all['_PRIORITE'] = df_all['ORIGINE'].map(priorite_origine).fillna(9)
        df_all['_COUPLE_DEDUP'] = (
            df_all['CODE_CLIENT'].astype(str).str.strip() + '|' +
            df_all['REF_ARTICLE_SERTA'].astype(str).str.strip()
        )
        nb_avant_dedup = len(df_all)
        df_all = df_all.sort_values('_PRIORITE').drop_duplicates(subset='_COUPLE_DEDUP', keep='first')
        nb_doublons = nb_avant_dedup - len(df_all)
        if nb_doublons > 0:
            st.warning(f"⚠️ {nb_doublons} doublon(s) de couple CODE_CLIENT|REF_ARTICLE_SERTA détecté(s) et retiré(s) (priorité LPC > MANUEL > CARNET > FACTURE_RECENTE).")
        df_all = df_all.drop(columns=['_PRIORITE', '_COUPLE_DEDUP'])

    if not df_suivi.empty and 'ORIGINE' in df_all.columns:
        mask = df_all['ORIGINE'] == 'CARNET'
        df_c = df_all[mask].copy()
        df_c = df_c.merge(df_suivi, on=['CODE_CLIENT','REF_ARTICLE_SERTA'], how='left')
        for col in ['QTE_EN_TRANSITE_RETARD_SC','QTE_EN_TRANSITE_ENCOURS_SC','QTE_FACTUREE_ENCOURS_SC']:
            if col not in df_c.columns:
                df_c[col] = 0
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0).astype(int)
        for col in ['QTE_BESOIN_CLIENT_RETARD_SC','QTE_BESOIN_CLIENT_ENCOURS_SC']:
            if col not in df_c.columns:
                df_c[col] = 0
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0).astype(int)
        df_c['QTE_CUTOFF_RETARD_SC']   = df_c['QTE_BESOIN_CLIENT_RETARD_SC'] + df_c['QTE_EN_TRANSITE_RETARD_SC']
        df_c['QTE_CUTOFF_PREVISION_SC'] = df_c['QTE_EN_TRANSITE_ENCOURS_SC'] + df_c['QTE_FACTUREE_ENCOURS_SC'] + df_c['QTE_BESOIN_CLIENT_ENCOURS_SC']
        df_all = pd.concat([
            df_all[~mask].dropna(axis=1, how='all'),
            df_c.dropna(axis=1, how='all')
        ], ignore_index=True, sort=False)

    for col in ['PROGRAMME', 'CODE_SELECTION', 'ORIGINE', 'CODE_CLIENT',
                'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']:
        if col in df_all.columns:
            df_all[col] = df_all[col].fillna('').astype(str)
    if 'QTE_TOTALE' in df_all.columns:
        df_all['QTE_TOTALE'] = pd.to_numeric(df_all['QTE_TOTALE'], errors='coerce').fillna(0)
    # AJOUT : meme probleme que QTE_TOTALE -- df_lpc a QTE_UC/QTE_MOQ en texte
    # (lire_fichier_excel force dtype=str), tandis que charger_carnet() (SQL) et
    # charger_facture_recent_hors_couverture() (litteral 0) les ont en numerique.
    # Apres pd.concat, la colonne devient 'object' avec un vrai melange str/int,
    # ce qui fait planter pyarrow dans st.dataframe() (ArrowTypeError). On force
    # le type numerique une bonne fois pour toutes, comme deja fait pour QTE_TOTALE.
    for col in ['QTE_UC', 'QTE_MOQ']:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors='coerce').fillna(0)
    # AJOUT : meme probleme de type mixte que QTE_UC, mais cette fois sur une
    # colonne texte (CLIENT_NAME) -- probablement apportee par l'upload manuel
    # en session, avec un melange str/int (ex: codes clients numeriques stockes
    # par erreur dans une colonne censee etre du texte). On force le texte pour
    # etre coherent avec ce que la colonne est censee contenir.
    if 'CLIENT_NAME' in df_all.columns:
        df_all['CLIENT_NAME'] = df_all['CLIENT_NAME'].fillna('').astype(str).str.strip()

    wk_all = wk_cols_from_df(df_all)
    for c in wk_all:
        df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)

    wk_in_range = [c for c in wk_all
                   if wk_label_to_date(c) is not None
                   and date_carnet_du <= wk_label_to_date(c) <= date_carnet_au]
    meta_cols = [c for c in df_all.columns if c not in wk_all]
    df_all = df_all[meta_cols + sorted(wk_in_range)]

    try:
        from sqlalchemy import text as _text2
        with get_engine().connect() as _conn:
            df_grp = pd.read_sql(_text2(
                "SELECT DISTINCT SERTA_SO_CLIENT_CODE AS CODE_CLIENT, "
                "SERTA_SO_CLIENT_GROUP_NAME, SERTA_SO_CLIENT_NAME, "
                "SALES_ADMINISTRATION_PERSON "
                "FROM [master].[dbo].[V_SUPPLY_CHAIN] "
                "WHERE SERTA_SO_CLIENT_GROUP_NAME IS NOT NULL"
            ), _conn)
        df_grp['CODE_CLIENT'] = df_grp['CODE_CLIENT'].astype(str).str.strip()
        df_grp = df_grp.drop_duplicates('CODE_CLIENT')
        for _col in ['SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON']:
            if _col not in df_all.columns:
                df_all[_col] = ''
            df_all[_col] = df_all[_col].fillna('').astype(str).str.strip()
        grp_map = df_grp.set_index('CODE_CLIENT').to_dict('index')
        mask = df_all['SERTA_SO_CLIENT_GROUP_NAME'].isin(['', 'nan', 'None'])
        codes = df_all.loc[mask, 'CODE_CLIENT'].astype(str).str.strip()
        for _col in ['SERTA_SO_CLIENT_GROUP_NAME','SERTA_SO_CLIENT_NAME','SALES_ADMINISTRATION_PERSON']:
            df_all.loc[mask, _col] = codes.map(
                {k: v.get(_col, '') for k, v in grp_map.items()}
            ).fillna('')
    except Exception as _e:
        print(f"Mapping groupe client échoué : {_e}")

    st.session_state['df_03'] = df_all

if 'df_03' not in st.session_state:
    df_lpc_disp = df_lpc.copy()
    for col in ['PROGRAMME', 'CODE_CLIENT', 'ORIGINE']:
        if col in df_lpc_disp.columns:
            df_lpc_disp[col] = df_lpc_disp[col].fillna('').astype(str)
    st.info("ℹ️ Affichage consolidé uniquement. Cliquez sur **🔄 Charger / Actualiser carnet** pour ajouter le carnet.")
    df_aff = df_lpc_disp
else:
    df_aff = st.session_state['df_03'].copy()

META_ALL = ['CODE_CLIENT', 'REF_ARTICLE_SERTA', 'REF_ARTICLE_CLIENT', 'ORIGINE',
            'PROGRAMME', 'HORIZON_PROGRAMME', 'UP_PRINCIPALE', 'CODE_SELECTION',
            'QTE_UC', 'QTE_MOQ', 'QTE_TOTALE',
            'ITEM_GROUP_CODE', 'SERTA_SO_STATUS_MIN', 'CLIENT_ORDER_NUM',
            'QTE_EN_TRANSITE_RETARD_SC', 'QTE_BESOIN_CLIENT_RETARD_SC', 'QTE_CUTOFF_RETARD_SC',
            'QTE_FACTUREE_ENCOURS_SC', 'QTE_EN_TRANSITE_ENCOURS_SC',
            'QTE_BESOIN_CLIENT_ENCOURS_SC', 'QTE_CUTOFF_PREVISION_SC',
            'SERTA_SO_CLIENT_GROUP_NAME', 'SERTA_SO_CLIENT_NAME', 'SALES_ADMINISTRATION_PERSON']
META_ALL = [c for c in META_ALL if c in df_aff.columns]
wk_cols  = sorted([c for c in df_aff.columns if c not in META_ALL
                   and isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'])

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

nb_lpc    = len(df_aff[df_aff['ORIGINE'] == 'LPC'])    if 'ORIGINE' in df_aff.columns else len(df_aff)
nb_manuel = len(df_aff[df_aff['ORIGINE'] == 'MANUEL']) if 'ORIGINE' in df_aff.columns else 0
nb_carnet = len(df_aff[df_aff['ORIGINE'] == 'CARNET']) if 'ORIGINE' in df_aff.columns else 0
nb_facture_recente = len(df_aff[df_aff['ORIGINE'] == 'FACTURE_RECENTE']) if 'ORIGINE' in df_aff.columns else 0

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Lignes LPC",           nb_lpc)
c2.metric("Lignes Hors Lasernet", nb_manuel)
c3.metric("Lignes CARNET",        nb_carnet)
c4.metric("Facturé récent",       nb_facture_recente)
c5.metric("Semaines",             len(wk_cols))
c6.metric("Refs SERTA",           df_aff['REF_ARTICLE_SERTA'].nunique() if 'REF_ARTICLE_SERTA' in df_aff.columns else 0)
if wk_cols:
    st.caption(f"📅 {wk_cols[0]} → {wk_cols[-1]}")

st.markdown("---")

with st.expander("🔍 Filtres", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        f_origine = st.multiselect("Origine", options=['LPC', 'MANUEL', 'CARNET', 'FACTURE_RECENTE'],
                                   default=['LPC', 'MANUEL', 'CARNET', 'FACTURE_RECENTE'])
    with col2:
        f_client  = st.multiselect("Code client",
            options=sorted(df_aff['CODE_CLIENT'].dropna().astype(str).unique()) if 'CODE_CLIENT' in df_aff.columns else [])
    with col3:
        f_ref = st.multiselect("Ref SERTA",
            options=sorted(df_aff['REF_ARTICLE_SERTA'].dropna().astype(str).unique()) if 'REF_ARTICLE_SERTA' in df_aff.columns else [])
    with col4:
        f_prog = st.multiselect("Programme client",
            options=sorted(df_aff['PROGRAMME'].replace('', None).dropna().astype(str).unique()) if 'PROGRAMME' in df_aff.columns else [])

df_disp = df_aff.copy()
if f_origine: df_disp = df_disp[df_disp['ORIGINE'].isin(f_origine)]
if f_client:  df_disp = df_disp[df_disp['CODE_CLIENT'].astype(str).isin(f_client)]
if f_ref:     df_disp = df_disp[df_disp['REF_ARTICLE_SERTA'].astype(str).isin(f_ref)]
if f_prog:    df_disp = df_disp[df_disp['PROGRAMME'].isin(f_prog)]

tab1, tab2, tab3 = st.tabs(["📋 Tableau", "📈 Graphique", "💾 Export"])

with tab1:
    col_cfg = {wk: st.column_config.NumberColumn(wk, format="%d") for wk in wk_cols}
    st.caption(f"{len(df_disp):,} lignes")
    st.dataframe(df_disp[META_ALL + wk_cols], width='stretch', height=600, column_config=col_cfg)

with tab2:
    if wk_cols and 'ORIGINE' in df_disp.columns:
        rows_g = []
        for orig, grp in df_disp.groupby('ORIGINE'):
            for wk in wk_cols:
                if wk in grp.columns:
                    rows_g.append({'SEMAINE': wk, 'QTE': grp[wk].sum(), 'ORIGINE': orig})
        df_g = pd.DataFrame(rows_g)
        if not df_g.empty:
            fig = px.bar(df_g, x='SEMAINE', y='QTE', color='ORIGINE', barmode='group',
                         title="QTY par semaine — LPC vs Manuel vs Carnet vs Facturé récent",
                         color_discrete_map={'LPC': '#1F4E79', 'MANUEL': '#375623',
                                             'CARNET': '#C00000', 'FACTURE_RECENTE': '#E8A33D'})
            st.plotly_chart(fig, width="stretch")

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV",
            data=df_disp.to_csv(index=False, encoding='utf-8-sig', sep=';'),
            file_name=f"agregation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch")
    with c2:
        st.download_button("📥 Excel",
            data=to_excel_bytes(df_disp),
            file_name=f"agregation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch")