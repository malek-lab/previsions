import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta, date
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*keyword arguments.*deprecated.*')

@st.cache_resource
def get_engine():
    try:
        return create_engine(
            "mssql+pyodbc://W25-DWDI/master"
            "?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes",
            pool_pre_ping=True,
            pool_recycle=1800
        )
    except Exception as e:
        st.error(f"Erreur connexion : {e}")
        return None

@st.cache_data(ttl=60)
def get_programmes():
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            # Utiliser T_CACHE_PROGRAMMES (cache local) si disponible
            df = pd.read_sql(text("""
                SELECT FPC_ID, Programme, CLI_CODE, CLI_NOM,
                       Horizon_programme,
                       QTE_PREVISION_TOTALE
                FROM [master].[dbo].[T_CACHE_PROGRAMMES]
                ORDER BY Programme
            """), conn)
            if not df.empty:
                return df
    except:
        pass
    # Fallback sur Programme_VW si cache vide
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

def cache_disponible():
    try:
        engine = get_engine()
        if engine is None: return False
        with engine.connect() as conn:
            r = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='T_CACHE_META'"
            )).scalar()
            if not r: return False
            r2 = conn.execute(text(
                "SELECT DATE_CACHE FROM [dbo].[T_CACHE_META] WHERE DATE_CACHE IS NOT NULL"
            )).fetchone()
        return r2 is not None
    except:
        return False

def get_date_cache():
    try:
        engine = get_engine()
        if engine is None: return None
        with engine.connect() as conn:
            r = conn.execute(text("SELECT DATE_CACHE FROM [dbo].[T_CACHE_META]")).fetchone()
        return r[0] if r else None
    except:
        return None

def recharger_cache():
    try:
        engine = get_engine()
        if engine is None: return False
        with engine.connect() as conn:
            conn.execute(text("EXEC [dbo].[P_CACHE_NUIT]"))
            conn.commit()
        # Vider le cache Streamlit de la liste des programmes
        get_programmes.clear()
        return True
    except Exception as e:
        print(f"Erreur rechargement cache : {e}")
        return False


def _forcer_programme_texte(df):
    """Force NOM_FICHIER_PROGRAMME_CLIENT en texte pur, dès la sortie SQL,
    pour empêcher toute réinterprétation numérique du code client (perte du 0 initial,
    ex: "0388_2626" -> 388_2626)."""
    if df is not None and not df.empty and 'NOM_FICHIER_PROGRAMME_CLIENT' in df.columns:
        df['NOM_FICHIER_PROGRAMME_CLIENT'] = df['NOM_FICHIER_PROGRAMME_CLIENT'].astype(str).str.strip()
    return df


def nettoyer_code(valeur, longueur=4):
    """Répare un code/identifiant abîmé par Excel ou pandas (perte du zéro initial,
    suffixe '.0' parasite ajouté par une conversion numérique implicite).
    À utiliser SYSTÉMATIQUEMENT sur tout code client / programme / référence,
    à CHAQUE point du pipeline où la donnée a pu transiter par un fichier Excel
    ou une lecture SQL non typée explicitement en texte.

    Exemples :
        nettoyer_code("388")      -> "0388"
        nettoyer_code(388)        -> "0388"
        nettoyer_code("388.0")    -> "0388"
        nettoyer_code(388.0)      -> "0388"
        nettoyer_code("0555_2626", longueur=4)  -> "0555_2626" (inchangé, contient déjà un séparateur)
    """
    if valeur is None:
        return ''
    code = str(valeur).strip()
    # Retirer le suffixe ".0" parasite ajouté par une conversion numérique implicite
    if code.endswith('.0'):
        code = code[:-2]
    # Si le code contient un séparateur (ex: "388_2626"), ne traiter QUE la partie
    # avant le premier underscore, et laisser le reste tel quel
    if '_' in code:
        partie_code, reste = code.split('_', 1)
        if partie_code.isdigit() and len(partie_code) < longueur:
            partie_code = partie_code.zfill(longueur)
        return partie_code + '_' + reste
    # Sinon, recompléter le zéro initial si c'est un nombre pur trop court
    if code.isdigit() and len(code) < longueur:
        code = code.zfill(longueur)
    return code


def nettoyer_codes_dataframe(df, colonnes, longueur=4):
    """Applique nettoyer_code() sur une liste de colonnes d'un DataFrame, en place.
    Retourne le DataFrame modifié. Ignore silencieusement les colonnes absentes."""
    for col in colonnes:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: nettoyer_code(v, longueur))
    return df

def execute_procedure_single(fpc_id, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    """Appel procédure pour UN SEUL programme (FPC_ID unique)."""
    engine = get_engine()
    if engine is None:
        return None
    try:
        date_vent_eff = date_ventilation if activer_ventilation else date_prevision
        sql = f"""
        EXEC P_R_PIVOT_PREVISION_DEV_LOCAL
            @SERVEUR_LIE            = 'SRV-MSSQLDB',
            @FPC_ID                 = '{fpc_id}',
            @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
            @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
            @SOURCE_QTE_LOT         = {1 if source_lot else 0}
        """
        with engine.connect() as conn:
            return _forcer_programme_texte(pd.read_sql(text(sql), conn))
    except Exception as e:
        st.error(f"Erreur procédure (FPC_ID={fpc_id}) : {e}")
        with st.expander("Détails"):
            st.exception(e)
        return None

def execute_procedure(fpc_ids, date_prevision, date_ventilation, source_lot, activer_ventilation=True):
    """
    Appel procédure vectorisée si cache dispo, sinon séquentiel.
    """
    if not fpc_ids:
        return None
    engine = get_engine()
    if engine is None:
        return None
    try:
        date_vent_eff = date_ventilation if activer_ventilation else date_prevision
        fpc_list = ','.join(str(f) for f in fpc_ids)

        if cache_disponible():
            sql = f"""
            EXEC [dbo].[P_R_PIVOT_PREVISION_CACHE_VECTORIZED]
                @SERVEUR_LIE            = 'SRV-MSSQLDB',
                @FPC_LIST               = '{fpc_list}',
                @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
                @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
                @SOURCE_QTE_LOT         = {1 if source_lot else 0}
            """
            with engine.connect() as conn:
                df = _forcer_programme_texte(pd.read_sql(text(sql), conn))
            print(f"Vectorisée OK — {len(fpc_ids)} programmes → {len(df)} lignes")
            return df if not df.empty else None
        else:
            frames = []
            for fpc_id in fpc_ids:
                try:
                    sql = f"""
                    EXEC [dbo].[P_R_PIVOT_PREVISION_DEV_LOCAL]
                        @SERVEUR_LIE            = 'SRV-MSSQLDB',
                        @FPC_ID                 = '{fpc_id}',
                        @DATE_DEBUT_PREVISION   = '{date_prevision.strftime('%Y-%m-%d')}',
                        @DATE_DEBUT_VENTILATION = '{date_vent_eff.strftime('%Y-%m-%d')}',
                        @SOURCE_QTE_LOT         = {1 if source_lot else 0}
                    """
                    with engine.connect() as conn:
                        df = _forcer_programme_texte(pd.read_sql(text(sql), conn))
                    if df is not None and not df.empty:
                        frames.append(df)
                    print(f"FPC_ID={fpc_id} → {len(df) if df is not None else 0} lignes")
                except Exception as e:
                    print(f"ERREUR FPC_ID={fpc_id} : {e}")
            if not frames:
                return None
            df_all = pd.concat(frames, ignore_index=True, sort=False)
            # Re-blinder NOM_FICHIER_PROGRAMME_CLIENT après la fusion : pandas peut
            # réharmoniser le type d'une colonne lors d'un concat de plusieurs
            # DataFrames hétérogènes (ex: un programme Lasernet ventilé, un autre
            # non-ventilé, un Hors Lasernet), ce qui peut faire perdre le zéro initial
            # même sur des lignes qui l'avaient correctement avant la fusion.
            df_all = _forcer_programme_texte(df_all)
            wk = [c for c in df_all.columns
                  if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
                  and c[1:3].isdigit() and c[4:6].isdigit()]
            for c in wk:
                df_all[c] = pd.to_numeric(df_all[c], errors='coerce').fillna(0)
            return df_all
    except Exception as e:
        print(f"ERREUR execute_procedure : {e}")
        try:
            st.error(f"Erreur procédure : {e}")
        except:
            pass
        return None

@st.cache_data(ttl=3600)
def get_historique_ventes(annee_min=None):
    """Charge l'historique des ventes (France + USA consolidées) -- quantités
    facturées par client/article/JOUR (DATE_FACTURE complète) pour permettre
    un cutoff précis choisi par l'utilisateur.

    IMPORTANT : interroge DIRECTEMENT V_EXPEDITION (et l'équivalent USA), PAS
    la vue V_VENTES_SERTA_GROUPE. Cette dernière filtre CODE_GROUPE_ARTICLE sur
    PF1-PF5/PFPACK/PFPWP uniquement (choix fait pour une autre analyse), ce qui
    exclut à tort des catégories à fort volume comme PFPDR (pièces détachées),
    MAUNIT, MAJOIN, 800, 900 -- vérifié empiriquement le 30/06/2026 : ces
    catégories représentent des centaines de milliers d'unités vendues,
    absentes à tort de l'historique si on passe par la vue filtrée.
    Mise en cache 1h."""
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        where_annee = f"AND YEAR(SQDELFR.DATE_FACTURE) >= {int(annee_min)}" if annee_min else ""
        where_annee_usa = f"AND YEAR(VDEL.DELIVERY_INVOICE_DATE) >= {int(annee_min)}" if annee_min else ""
        sql = f"""
            SELECT * FROM OPENQUERY([SRV-MSSQLDB], '
                SELECT
                    SQDELFR.CODE_CLIENT                     AS CLIENT_CODE
                    ,SQDELFR.NOM_CLIENT                     AS CLIENT_NAME
                    ,VCLI.NOM_CLIENT_GROUPE                 AS CLIENT_GROUP_NAME
                    ,VAS.REF_ARTICLE                        AS ITEM_CODE
                    ,SQDELFR.QTE_EXPEDIEE                   AS QTE_EXPEDIEE
                    ,CAST(SQDELFR.DATE_FACTURE AS SMALLDATETIME) AS DATE_FACTURE
                    ,YEAR(SQDELFR.DATE_FACTURE)             AS INVOICE_YEAR
                    ,MONTH(SQDELFR.DATE_FACTURE)            AS INVOICE_MONTH
                    ,YEAR(SQDELFR.DATE_FACTURE)*100 + MONTH(SQDELFR.DATE_FACTURE) AS INVOICE_YEAR_MONTH
                FROM DW.VENTE.V_EXPEDITION SQDELFR
                    LEFT JOIN DW.VENTE.V_CLIENT VCLI ON VCLI.CLI_ID = SQDELFR.CLI_ID
                    LEFT JOIN DW.PRODUCTION.V_ART_STANDARD VAS ON VAS.ART_ID = SQDELFR.ART_ID
                WHERE SQDELFR.NUM_FACTURE <> 0
                    AND VCLI.CODE_CLIENT_GROUPE NOT IN (''GRP040'', ''GRP041'', ''GRP039'')
                    AND VAS.CODE_GROUPE_ARTICLE IN (
                        ''PF1'',''PF2'',''PF3'',''PF4'',''PF5'',''PFPACK'',''PFPWP'',
                        ''PFPDR'',''MAUNIT'',''MAJOIN'',''800'',''900''
                    )
                    {where_annee}
            ')
            UNION ALL
            SELECT * FROM OPENQUERY([SRV-MSSQLDB], '
                SELECT
                    VDEL.CLIENT_CODE                                       AS CLIENT_CODE
                    ,VDEL.CLIENT_NAME                                      AS CLIENT_NAME
                    ,VCLI.NOM_CLIENT_GROUPE                                AS CLIENT_GROUP_NAME
                    ,VDEL.ITEM_CODE                                        AS ITEM_CODE
                    ,VDEL.DELIVERY_DELIVERED_QTY                           AS QTE_EXPEDIEE
                    ,CAST(VDEL.DELIVERY_INVOICE_DATE AS SMALLDATETIME)     AS DATE_FACTURE
                    ,YEAR(VDEL.DELIVERY_INVOICE_DATE)                      AS INVOICE_YEAR
                    ,MONTH(VDEL.DELIVERY_INVOICE_DATE)                     AS INVOICE_MONTH
                    ,YEAR(VDEL.DELIVERY_INVOICE_DATE)*100 + MONTH(VDEL.DELIVERY_INVOICE_DATE) AS INVOICE_YEAR_MONTH
                FROM DB_DW_SERTA_USA.S_SLS.V_DELIVERY VDEL
                    LEFT JOIN DW.VENTE.V_CLIENT VCLI ON VCLI.CODE = VDEL.CLIENT_CODE
                WHERE VDEL.CLIENT_CODE NOT IN (''6168'', ''6217'')
                    AND VDEL.DELIVERY_INVOICE_DATE IS NOT NULL
                    AND VDEL.DELIVERY_INVOICE_NUM IS NOT NULL
                    {where_annee_usa}
            ')
        """
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
        if df.empty:
            return df
        df = nettoyer_codes_dataframe(df, ['CLIENT_CODE', 'ITEM_CODE'], longueur=4)
        # Forcer les colonnes texte en str pur -- pyarrow (cache Streamlit) exige
        # des types homogènes et plante si CLIENT_NAME ou CLIENT_GROUP_NAME contient
        # un mélange str/int/None (cas fréquent sur les lignes USA sans nom client)
        for col in ['CLIENT_NAME', 'CLIENT_GROUP_NAME']:
            if col in df.columns:
                df[col] = df[col].fillna('').astype(str).str.strip()
        df['QTE'] = pd.to_numeric(df['QTE_EXPEDIEE'], errors='coerce').fillna(0)
        df = df.groupby(['CLIENT_CODE', 'CLIENT_NAME', 'CLIENT_GROUP_NAME', 'ITEM_CODE',
                          'DATE_FACTURE', 'INVOICE_YEAR', 'INVOICE_MONTH', 'INVOICE_YEAR_MONTH'],
                         as_index=False)['QTE'].sum()
        return df
    except Exception as e:
        print(f"Erreur get_historique_ventes : {e}")
        return pd.DataFrame()


def debut_premiere_semaine_mois(d):
    """Lundi de la semaine ISO qui CONTIENT le 1er du mois de `d`.
    Règle métier « facturé récent » : on part de la première semaine du mois,
    prise ENTIÈRE. Si le 1er n'est pas un lundi, on remonte au lundi précédent
    (la semaine déborde alors de quelques jours sur le mois précédent, c'est voulu).

    Exemples :
        1er = mercredi 01/07/2026 -> lundi 29/06/2026
        1er = lundi 01/06/2026    -> lundi 01/06/2026 (inchangé)
    """
    premier = d.replace(day=1)
    # isoweekday(): lundi=1 ... dimanche=7 -> on recule jusqu'au lundi
    return premier - timedelta(days=premier.isoweekday() - 1)


def plafond_arc(today=None):
    """Plafond métier ARC carnet (CLIENT_ACK_DATE) : jamais avant le 31/12/2027,
    mais toujours AU MOINS un an devant la date du jour. Aujourd'hui (2026) le
    max reste 31/12/2027 ; le +1 an prend le relais dès qu'on dépasse fin 2026.
    Source UNIQUE partagée par la page Agrégation et la page Supply chain pour
    éviter deux plafonds divergents."""
    if today is None:
        today = date.today()
    base = date(2027, 12, 31)
    try:
        plus_un_an = today.replace(year=today.year + 1)
    except ValueError:  # 29 février
        plus_un_an = today.replace(year=today.year + 1, day=28)
    return max(base, plus_un_an)


def wk_label_to_date(label):
    """Convertit une étiquette de semaine ISO ('S26-28') en date du LUNDI de
    cette semaine. Fonction dupliquée localement dans plusieurs pages jusqu'ici
    (03_Agregation.py, 06_pic.py...) -- centralisée ici pour éviter toute
    divergence future entre les copies."""
    try:
        yy = 2000 + int(label[1:3])
        ww = int(label[4:6])
        return date.fromisocalendar(yy, ww, 1)
    except Exception:
        return None


def repartir_semaine_jours_ouvres(label):
    """
    Pour une semaine donnée ('S26-28'), calcule la répartition de sa quantité
    entre le(s) mois qu'elle chevauche, au prorata des JOURS OUVRÉS (lundi à
    vendredi, 5 jours -- pas 7, car aucune activité le week-end).

    PROBLÈME RÉSOLU : la convention utilisée jusqu'ici partout dans le
    pipeline (wk_label_to_date -> mois du LUNDI) attribue 100% de la semaine
    au mois du lundi, même si la majorité des jours ouvrés de cette semaine
    tombent en réalité dans le mois suivant. Exemple : semaine du 29/06 au
    05/07 (lundi 29/06) -- avec l'ancienne convention, 100% de la quantité
    va en juin, alors que 4 des 5 jours ouvrés (mar/mer/jeu/ven) sont en
    juillet.

    Retourne un dict {mois ('YYYY-MM'): fraction (0 à 1)}, dont les valeurs
    somment à 1.0 -- une seule clé si la semaine ne chevauche pas deux mois,
    deux clés sinon.

    NE REMPLACE PAS wk_label_to_date() dans le reste du pipeline (qui reste
    la convention par défaut, déjà validée sur les totaux annuels/
    trimestriels) -- fonction additionnelle, à utiliser explicitement là où
    la précision jour-par-jour est voulue (ex: bascule mensuelle fine).
    """
    lundi = wk_label_to_date(label)
    if lundi is None:
        return {}

    jours_ouvres = [lundi + timedelta(days=i) for i in range(5)]  # lundi a vendredi
    repartition = {}
    for j in jours_ouvres:
        mois = j.strftime('%Y-%m')
        repartition[mois] = repartition.get(mois, 0) + 1

    total_jours = len(jours_ouvres)  # toujours 5
    return {mois: nb / total_jours for mois, nb in repartition.items()}


def agreger_semaines_vers_mois_proportionnel(df, wk_cols):
    """
    Agrège les colonnes semaine vers des colonnes mois, en répartissant CHAQUE
    semaine au prorata des jours ouvrés entre les mois qu'elle chevauche (voir
    repartir_semaine_jours_ouvres). Alternative plus précise à la convention
    "mois du lundi" utilisée par défaut ailleurs dans le pipeline.

    Paramètres :
    - df : DataFrame avec les colonnes wk_cols (quantités par semaine)
    - wk_cols : liste des colonnes semaine à agréger

    Retourne : copie de df avec les colonnes wk_cols remplacées par des
    colonnes mois ('YYYY-MM'), sommées sur toutes les semaines concernées.
    """
    resultat = df.drop(columns=wk_cols).copy()
    mois_vus = set()
    contributions = {}  # mois -> Series de quantites a sommer

    for wk in wk_cols:
        if wk not in df.columns:
            continue
        repartition = repartir_semaine_jours_ouvres(wk)
        qte_semaine = pd.to_numeric(df[wk], errors='coerce').fillna(0)
        for mois, fraction in repartition.items():
            mois_vus.add(mois)
            contribution = qte_semaine * fraction
            if mois in contributions:
                contributions[mois] = contributions[mois] + contribution
            else:
                contributions[mois] = contribution

    for mois in sorted(mois_vus):
        resultat[mois] = contributions[mois]
    return resultat


def reequilibrer_semaines_avance_retard(df, wk_cols_apres_cutoff, df_hist,
                                          col_client='CODE_CLIENT', col_ref='REF_ARTICLE_SERTA',
                                          col_client_hist='CLIENT_CODE', col_ref_hist='ITEM_CODE',
                                          aujourdhui=None):
    """
    Rééquilibrage avance/retard, semaine par semaine, APRÈS le cutoff.
    Symétrique dans les deux sens :
    - Retard (facturé < prévu, semaine déjà passée) -> le manque s'AJOUTE au
      prévu de la semaine suivante (la demande non honorée se reporte).
    - Avance (facturé > prévu) -> l'excédent se RETIRE du prévu de la semaine
      suivante (déjà vendu en trop -- pris sur stock ou avance en atelier).

    IMPORTANT : seules les semaines DÉJÀ PASSÉES (<= aujourd'hui) affichent le
    facturé réel -- une semaine future n'a par définition aucun facturé (0),
    donc l'afficher tel quel effacerait à tort toute la prévision à venir.
    Les semaines futures affichent le prévu déjà ajusté par les reports
    accumulés des semaines passées, jamais un facturé fictif à 0.

    NE MODIFIE JAMAIS les totaux annuels/trimestriels calculés ailleurs
    (Qty_YTD, Qty {année}, etc. -- déjà validés à 100%, voir règle dans
    02_import_manuel.py). Cette fonction ne touche QUE les colonnes semaine
    transmises, à utiliser en complément d'affichage.

    Paramètres :
    - df : DataFrame avec colonnes [col_client, col_ref] + wk_cols_apres_cutoff
    - wk_cols_apres_cutoff : semaines à traiter, APRÈS le cutoff, TRIÉES
           chronologiquement (utiliser wk_label_to_date pour trier si besoin)
    - df_hist : sortie de get_historique_ventes() -- doit avoir col_client_hist,
           col_ref_hist, DATE_FACTURE, QTE. Si vide/None, retourne df inchangé.
    - col_client, col_ref : noms des colonnes identifiantes dans `df`
    - col_client_hist, col_ref_hist : noms des colonnes identifiantes dans
           `df_hist` -- get_historique_ventes() renvoie CLIENT_CODE/ITEM_CODE
           par défaut, MAIS si l'appelant a déjà renommé ces colonnes avant de
           stocker df_hist (ex: en CODE_CLIENT/REF_ARTICLE_SERTA), il faut le
           préciser ici -- sinon KeyError garanti (bug trouvé et corrigé :
           02_import_manuel.py renommait CLIENT_CODE->CODE_CLIENT avant
           stockage en session, cassant l'appel avec les noms par défaut).
    - aujourdhui : date du jour, pour distinguer passé/futur. Par défaut,
           date.today().

    Retourne : copie de df avec les colonnes wk_cols_apres_cutoff ajustées.
    """
    if df_hist is None or df_hist.empty or not wk_cols_apres_cutoff:
        return df
    if aujourdhui is None:
        aujourdhui = date.today()

    if col_client_hist not in df_hist.columns or col_ref_hist not in df_hist.columns:
        # Garde-fou : si les noms attendus n'existent pas dans df_hist, on
        # evite un KeyError silencieux et on retourne df inchange plutot que
        # de planter -- mieux vaut aucun reequilibrage qu'un crash de page.
        return df

    dfh = df_hist.copy()
    dfh[col_client_hist] = dfh[col_client_hist].astype(str).str.strip()
    dfh[col_ref_hist]    = dfh[col_ref_hist].astype(str).str.strip()

    def _date_to_wk_label(d):
        try:
            iso = pd.Timestamp(d).isocalendar()
            return f"S{str(iso[0])[2:]}-{iso[1]:02d}"
        except Exception:
            return None
    dfh['SEMAINE_FACTURE'] = dfh['DATE_FACTURE'].apply(_date_to_wk_label)

    facture_par_semaine = (
        dfh.groupby([col_client_hist, col_ref_hist, 'SEMAINE_FACTURE'])['QTE']
        .sum().to_dict()
    )

    # Une semaine est "passee" (facture reelle exploitable) si son lundi est
    # <= aujourd'hui -- sinon elle est future, on garde le prevu ajuste.
    semaine_passee = {wk: (wk_label_to_date(wk) or date(2099, 1, 1)) <= aujourdhui
                       for wk in wk_cols_apres_cutoff}

    ajuste = df.copy()
    ajuste[col_client] = ajuste[col_client].astype(str).str.strip()
    ajuste[col_ref]    = ajuste[col_ref].astype(str).str.strip()

    for idx in ajuste.index:
        cc  = ajuste.at[idx, col_client]
        ref = ajuste.at[idx, col_ref]
        report = 0  # ecart signe cumule : positif = retard (a ajouter), negatif = avance (a retirer)
        for i, wk in enumerate(wk_cols_apres_cutoff):
            prevu_semaine = ajuste.at[idx, wk]
            prevu_restant = max(0, prevu_semaine + report)

            if semaine_passee[wk]:
                # Semaine passee : on affiche le fait reel, et on calcule
                # l'ecart signe a propager vers la semaine suivante.
                facture_reel = facture_par_semaine.get((cc, ref, wk), 0)
                ajuste.at[idx, wk] = facture_reel
                report = prevu_restant - facture_reel
            else:
                # Semaine future : pas de fait constate -- on affiche le
                # prevu deja ajuste par les reports accumules des semaines
                # passees, le report ne change plus au-dela.
                ajuste.at[idx, wk] = prevu_restant
                report = 0
    return ajuste


def logo_sidebar():
    try:
        st.image("Serta_logo.jpg", width="stretch")
    except:
        st.title("SERTA")

def wk_cols_from_df(df):
    return sorted([c for c in df.columns
                   if isinstance(c, str) and len(c) == 6
                   and c[0] == 'S' and c[3] == '-'
                   and c[1:3].isdigit() and c[4:6].isdigit()])

def to_excel_bytes(df):
    from io import BytesIO
    buf = BytesIO()
    # Sur les colonnes semaines (S26-14, S27-03, ...), remplacer 0 par une
    # cellule vide (NaN) pour un export plus lisible -- les autres colonnes
    # numériques (QTE_TOTALE, QTE_CUTOFF_PREVISION, etc.) gardent leurs 0.
    df_export = df.copy()
    wk_cols = [c for c in df_export.columns
               if isinstance(c, str) and len(c) == 6 and c[0] == 'S' and c[3] == '-'
               and c[1:3].isdigit() and c[4:6].isdigit()]
    for c in wk_cols:
        df_export[c] = df_export[c].replace(0, pd.NA)
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df_export.to_excel(w, index=False, sheet_name='Export')
    return buf.getvalue()