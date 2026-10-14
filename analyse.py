"""
Analyse de la qualité de l'eau des cours d'eau de Montréal (COURDO, plusieurs années)

Utilisation :
    1. Mets tous tes CSV COURDO dans le dossier data/
    2. python analyse.py
    3. Regarde le dossier outputs/ (un sous-dossier par zone)

Question : comment la qualité de l'eau varie-t-elle selon les stations,
les saisons et les années, et à quelle fréquence dépasse-t-elle les critères ?

Corrections de biais appliquées (voir README) :
  - points de contrôle des rejets (STEP, OER) exclus : ce ne sont pas des cours d'eau
  - doublons supprimés, coliformes plafonnés à une valeur commune
  - zone EMIS (panache de la station d'épuration) analysée à part des autres cours d'eau
  - années comparées seulement sur les mêmes stations ET les mêmes mois
  - effet de la pluie contrôlé (courbes « temps sec seulement »)
"""
import glob
import re
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # pas de fenêtre, on sauvegarde seulement les images
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# =====================================================================
# CONFIG (tu peux modifier cette section)
# =====================================================================
FICHIERS = "data/*.csv"
SORTIE = Path("outputs")

# Paramètres analysés : nom court -> libellé pour les graphiques
PARAMETRES = {
    "coli": "Coliformes fécaux (UFC/100 mL)",
    "ptot": "Phosphore total (µg/L)",
    "ph": "pH",
    "o2": "Oxygène dissous (mg/L)",
    "cond": "Conductivité (µS/cm)",
    "temp": "Température (°C)",
    "mes": "Matières en suspension (mg/L)",
    "nh3": "Azote ammoniacal (µg-N/L)",
    # Optionnel : décommente pour ajouter des métaux (leurs critères dépendent
    # de la dureté de l'eau, donc pas de seuil fixe ici)
    # "cu": "Cuivre (µg/L)",
    # "zn": "Zinc (µg/L)",
    # "pb": "Plomb (µg/L)",
}
ECHELLE_LOG = {"coli", "nh3"}  # valeurs très étalées : axe logarithmique

# Critères de qualité : à citer dans ton README
SEUIL_COLI_CONTACT = 200      # UFC/100 mL, contact direct (MELCCFP)
SEUIL_COLI_INDIRECT = 1000    # UFC/100 mL, contact indirect (MELCCFP)
SEUIL_PTOT = 30               # µg/L, prévention de l'eutrophisation (MELCCFP)
PH_MIN, PH_MAX = 6.5, 9.0     # vie aquatique (critères de qualité du Québec)
SEUIL_O2_MIN = 5.5            # mg/L, indicatif (CCME) : à confirmer

# --- Anti-biais --------------------------------------------------------
# Stations exclues : points de contrôle des rejets (pas des cours d'eau).
#   STEP = effluent de la station d'épuration lui-même (eau usée, pas une rivière)
#   OER  = points liés aux « objectifs environnementaux de rejet » (près des rejets)
EXCLURE_PREFIXES = ["STEP", "OER"]
# Les coliformes sont plafonnés à la même valeur dans toutes les années, car la
# limite du laboratoire varie (2017 : 600 000 ; d'autres années : millions).
CAP_COLI = 600000
MIN_MESURES_STATION = 5      # stations avec moins de mesures : ignorées dans les classements
MIN_STATIONS_COMMUNES = 8    # minimum de stations communes pour comparer les années
MIN_MESURES_ANNEE = 20       # en dessous : avertissement (année peu fiable)
NOTES_ANNEES = {2017: "crue historique\n(dilution)"}  # affiché sur les graphiques par année

ZONES = {
    "emis": "Zone EMIS : panache de la station d'épuration Jean-R.-Marcotte",
    "cours_eau": "Autres cours d'eau (FSL, LSL, BLAP, RDP, RMI...)",
}
# =====================================================================

SORTIE.mkdir(exist_ok=True)
plt.rcParams.update({"figure.dpi": 120, "axes.grid": True, "grid.alpha": 0.3})

NOMS_MOIS = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]
LIBELLES_METEO = {1: "Temps sec", 0: "Pluie", -1: "Pluie la veille", -2: "Pluie avant-veille"}


# ---------------------------------------------------------------------
# 1. Chargement et uniformisation des colonnes
# ---------------------------------------------------------------------
def normaliser(nom):
    """'Conductivité (µS/cm)' -> 'conductivite_s_cm' : minuscules, sans accents."""
    nom = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", nom.lower()).strip("_")


RENOMMER = {
    "point_d_echantillonnage": "station",
    "od": "od_pct",
    "temp_oc": "temp",
    "conductivite": "cond",
    "p": "ptot",
}


def nom_court(nom):
    """'ptot_g_l' -> 'ptot', 'mes_mg_l' -> 'mes', 'o2_mg_l' -> 'o2', 'COND.' -> 'cond'."""
    n = normaliser(nom)
    n = re.sub(r"_(ug|mg|g)_l$", "", n)
    return RENOMMER.get(n, n)


def lire(fichier):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(fichier, sep=None, engine="python", encoding=enc)
        except Exception:
            continue
    raise ValueError(f"Impossible de lire {fichier}")


FORMATS_DATE = [
    "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
    "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%d/%m/%y %H:%M", "%d/%m/%y", "%d-%m-%y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%b %d, %Y", "%d%b%Y", "%d%b%y",
]
MOIS_PREFIXES = {  # français (et anglais) -> abréviation anglaise
    "janv": "jan", "fev": "feb", "mars": "mar", "avr": "apr", "mai": "may", "juin": "jun",
    "juil": "jul", "aout": "aug", "sep": "sep", "oct": "oct", "nov": "nov", "dec": "dec",
}


def _mot_mois(m):
    mot = m.group(0)
    for prefixe, abr in MOIS_PREFIXES.items():
        if mot.startswith(prefixe):
            return abr
    return mot[:3]


def _preparer_date(texte):
    if pd.isna(texte):
        return np.nan
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode().lower().strip()
    t = re.sub(r"\s+", " ", t)  # "2011-08-02  09:03:46" (double espace) -> un seul espace
    t = re.sub(r"[a-z]{3,}", _mot_mois, t)  # juin / juillet / June -> jun / jul
    return t if t not in ("", "nan", "nat") else np.nan


def lire_dates(serie):
    """Essaie plusieurs formats, valeur par valeur, puis les numéros de série Excel."""
    s = serie.map(_preparer_date)
    resultat = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in FORMATS_DATE:
        reste = resultat.isna() & s.notna()
        if not reste.any():
            break
        essai = pd.to_datetime(s.where(reste), format=fmt, errors="coerce")
        resultat = resultat.fillna(essai)
    reste = resultat.isna() & s.notna()
    if reste.any():
        nombres = pd.to_numeric(s.where(reste), errors="coerce")
        ok = nombres.between(25000, 60000)
        if ok.any():
            excel = pd.to_datetime(nombres.where(ok), unit="D", origin="1899-12-30", errors="coerce")
            resultat = resultat.fillna(excel)
    return resultat


def en_nombre(serie):
    """Virgule décimale -> point ; retire < et > ; texte -> NaN."""
    s = (
        serie.astype(str)
        .str.replace(",", ".", regex=False)
        .str.replace("<", "", regex=False)
        .str.replace(">", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(s, errors="coerce")


# Fichier 2014 : les valeurs sont décalées d'une colonne à partir de COT
# (ex. la colonne "Mo" contient du sodium, la colonne "Ptot" du plomb).
ORDRE_DECALE = ["cot", "cr", "cu", "fe", "k", "mg", "mn", "mo", "na", "nh3", "ni", "ptot", "pb", "mes"]
APRES_MES = ["sb", "se", "sn", "u", "v", "zn"]


def corriger_decalage(d, nom_fichier):
    """Détecte et répare le décalage : le molybdène (Mo) vaut ~1 µg/L, jamais > 1000."""
    if not {"mo", "ptot"} <= set(d.columns):
        return d
    if en_nombre(d["mo"]).median() <= 1000:
        return d
    d = d.copy()
    brut = {c: d[c].copy() for c in ORDRE_DECALE if c in d.columns}
    d["cot"] = np.nan  # COT absent de ce fichier
    for avant, apres in zip(ORDRE_DECALE[:-1], ORDRE_DECALE[1:]):
        if avant in brut and apres in d.columns:
            d[apres] = brut[avant]
    for c in APRES_MES:  # alignement incertain après MES : on les ignore
        if c in d.columns:
            d[c] = np.nan
    print(f"   DÉCALAGE de colonnes détecté et corrigé dans {nom_fichier} (de COT à MES ; Sb à Zn ignorés)")
    return d


fichiers = sorted(glob.glob(FICHIERS))
if not fichiers:
    raise SystemExit(f"Aucun fichier trouvé avec le motif {FICHIERS}. Mets tes CSV dans data/.")

morceaux = []
colonnes_ref = None
print("=== Chargement ===")
for f in fichiers:
    d = lire(f)
    d.columns = [nom_court(c) for c in d.columns]
    d = d.loc[:, ~d.columns.duplicated()]
    d = corriger_decalage(d, Path(f).name)
    dates_brutes = d["date"].copy()
    d["date"] = lire_dates(dates_brutes)
    vides = dates_brutes.isna().sum()
    echecs = d["date"].isna().sum() - vides
    annees_f = [int(a) for a in sorted(d["date"].dt.year.dropna().unique())]
    print(f"{Path(f).name}: {len(d)} lignes, année(s) {annees_f}, lignes vides (ignorées) : {vides}, dates NON comprises : {echecs}")
    if echecs:
        exemples = dates_brutes[d["date"].isna() & dates_brutes.notna()].astype(str).unique()[:6]
        print(f"   Exemples de dates NON comprises : {list(exemples)}")
    if colonnes_ref is None:
        colonnes_ref = set(d.columns)
    else:
        manquantes = colonnes_ref - set(d.columns)
        nouvelles = set(d.columns) - colonnes_ref
        if manquantes:
            print(f"   Colonnes du 1er fichier absentes ici : {sorted(manquantes)}")
        if nouvelles:
            print(f"   Colonnes en plus par rapport au 1er fichier : {sorted(nouvelles)}")
    d["fichier_source"] = Path(f).name
    morceaux.append(d)

df = pd.concat(morceaux, ignore_index=True)

# ---------------------------------------------------------------------
# 2. Nettoyage et corrections de biais
# ---------------------------------------------------------------------
avant = len(df)
df = df.dropna(subset=["date", "station"]).copy()
print(f"\nLignes retirées (sans date ou station) : {avant - len(df)}")

for c in list(PARAMETRES) + ["meteo"]:
    if c in df.columns:
        df[c] = en_nombre(df[c])
    elif c in PARAMETRES:
        print(f"ATTENTION : la colonne '{c}' n'existe pas, elle sera ignorée.")
params = [c for c in PARAMETRES if c in df.columns]

df["station"] = df["station"].astype(str).str.strip().str.upper()
df["annee"] = df["date"].dt.year
df["mois"] = df["date"].dt.month

# (a) Doublons : même station + même date -> on garde la ligne la plus complète
df["_completude"] = df[params].notna().sum(axis=1)
n0 = len(df)
df = (
    df.sort_values("_completude", ascending=False)
    .drop_duplicates(["station", "date"], keep="first")
    .drop(columns="_completude")
    .sort_values(["date", "station"])
    .reset_index(drop=True)
)
print(f"Doublons supprimés (même station et même date) : {n0 - len(df)}")

# (b) Points de contrôle des rejets : exclus (ce ne sont pas des cours d'eau)
prefixes = tuple(p.upper() for p in EXCLURE_PREFIXES)
masque_exclu = df["station"].str.startswith(prefixes)
if masque_exclu.any():
    exclues = df[masque_exclu]
    print("\nStations EXCLUES (points de contrôle des rejets) :")
    print(
        exclues.groupby("station")
        .agg(mesures=("coli", "size"), coli_mediane=("coli", "median"), ptot_mediane=("ptot", "median"))
        .round(0)
        .to_string()
    )
    exclues.to_csv(SORTIE / "donnees_exclues.csv", index=False)
df = df[~masque_exclu].copy()

# (c) Coliformes : plafond commun (la limite du laboratoire change d'une année à l'autre)
if "coli" in df.columns:
    nb_cap = (df["coli"] > CAP_COLI).groupby(df["annee"]).sum()
    if nb_cap.sum():
        print(f"\nColiformes plafonnés à {CAP_COLI} (valeurs touchées par année) : {nb_cap[nb_cap > 0].to_dict()}")
    df["coli"] = df["coli"].clip(upper=CAP_COLI)

# (d) Zone, distance en aval du rejet (km) et météo sèche
df["zone"] = np.where(df["station"].str.startswith("EMIS"), "emis", "cours_eau")
df["km"] = df["station"].str.extract(r"^EMIS-\(?(-?\d+(?:\.\d+)?)\)?")[0].astype(float)  # EMIS-40P -> 40
df["sec"] = (df["meteo"] == 1) if "meteo" in df.columns else False

df.to_csv(SORTIE / "donnees_nettoyees.csv", index=False)
annees = [int(a) for a in sorted(df["annee"].unique())]
print(f"\n{len(df)} mesures conservées, {df['station'].nunique()} stations, années : {annees}")

# (e) Contrôle des biais : ce qui change d'une année à l'autre
controle = df.groupby(["zone", "annee"]).agg(
    mesures=("station", "size"),
    stations=("station", "nunique"),
    mois=("mois", lambda m: ",".join(str(int(x)) for x in sorted(set(m)))),
    pct_temps_sec=("sec", lambda s: round(s.mean() * 100)),
)
controle.to_csv(SORTIE / "controles_biais.csv")
print("\n=== CONTRÔLE DES BIAIS (par zone et par année) ===")
print(controle.to_string())
print("-> si les mois ou le % de temps sec varient beaucoup, les années ne sont pas directement comparables.")


# ---------------------------------------------------------------------
# 3. Fonctions d'analyse
# ---------------------------------------------------------------------
def depasse_coli(d, seuil):
    """Tient compte du signe (<, =, >) : '>600000' dépasse sûrement ; '<10' ne dépasse sûrement pas."""
    v = d["coli"]
    signe = d["signe_coli"].fillna("=").astype(str).str.strip() if "signe_coli" in d else pd.Series("=", index=d.index)
    r = np.where(
        signe == ">",
        np.where(v >= seuil, 1.0, np.nan),
        np.where(signe == "<", np.where(v <= seuil, 0.0, np.nan), (v > seuil).astype(float)),
    )
    return pd.Series(r, index=d.index).where(v.notna())


def depasse(serie, test):
    return test(serie).astype(float).where(serie.notna())


def calculer_criteres(d):
    c = {}
    if "coli" in d.columns:
        c[f"Coliformes > {SEUIL_COLI_CONTACT} UFC/100 mL (contact direct)"] = depasse_coli(d, SEUIL_COLI_CONTACT)
        c[f"Coliformes > {SEUIL_COLI_INDIRECT} UFC/100 mL (contact indirect)"] = depasse_coli(d, SEUIL_COLI_INDIRECT)
    if "ptot" in d.columns:
        c[f"Phosphore total > {SEUIL_PTOT} µg/L"] = depasse(d["ptot"], lambda s: s > SEUIL_PTOT)
    if "ph" in d.columns:
        c[f"pH hors de [{PH_MIN} ; {PH_MAX}]"] = depasse(d["ph"], lambda s: (s < PH_MIN) | (s > PH_MAX))
    if "o2" in d.columns:
        c[f"Oxygène dissous < {SEUIL_O2_MIN} mg/L"] = depasse(d["o2"], lambda s: s < SEUIL_O2_MIN)
    return pd.DataFrame(c)


def barres_depassements(ax, table, titre):
    table.plot(kind="bar", ax=ax, width=0.8, legend=False)
    ax.set_title(titre, fontsize=10)
    ax.set_xlabel("Année")
    ax.set_ylabel("% des mesures")
    ax.tick_params(axis="x", rotation=0)


def graphique_panache(dfa, dossier):
    """Zone EMIS : la qualité dépend surtout de la DISTANCE au rejet de la station d'épuration."""
    e = dfa.dropna(subset=["km"])
    if e.empty:
        return
    for c in ("coli", "ptot"):
        if c not in e.columns:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
        for ax, (titre_ax, sous) in zip(axes, (("Tous les échantillons", e), ("Temps sec seulement", e[e["sec"]]))):
            for an, g in sous.groupby("annee"):
                m = g.groupby("km")[c].median().sort_index()
                ax.plot(m.index, m.values, marker="o", ms=3, label=str(int(an)))
            if c in ECHELLE_LOG:
                ax.set_yscale("log")
            if c == "coli":
                ax.axhline(SEUIL_COLI_CONTACT, ls="--", c="gray", lw=1)
                ax.axhline(SEUIL_COLI_INDIRECT, ls=":", c="gray", lw=1)
            if c == "ptot":
                ax.axhline(SEUIL_PTOT, ls="--", c="gray", lw=1)
            ax.set_xlabel("Distance en aval du rejet de la STEP (km ; -1 = en amont)")
            ax.set_title(titre_ax)
            ax.legend(title="Année", fontsize=8)
        axes[0].set_ylabel(PARAMETRES[c])
        fig.suptitle(f"Panache de la station d'épuration : {PARAMETRES[c]}")
        fig.tight_layout()
        fig.savefig(dossier / f"panache_{c}_par_km.png")
        plt.close(fig)
    if "coli" in e.columns:
        tab = e.groupby(["km", "annee"])["coli"].median().unstack().round(0)
        tab.to_csv(dossier / "panache_coli_par_km.csv")
        bandes = pd.cut(e["km"], [-5, 0.5, 5, 10, 20, 50], labels=["-1 à 0", "0,5 à 5", "5,5 à 10", "10,5 à 20", "20,5 à 44"])
        print("\nMédiane de coliformes selon la distance au rejet (km) :")
        print(e.groupby(bandes, observed=True)["coli"].median().round(0).to_string())


def analyser(d, cle, titre):
    dossier = SORTIE / cle
    dossier.mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 72)
    print(f"ANALYSE : {titre}")
    print("=" * 72)
    d = d.copy()
    p = [c for c in params if d[c].notna().any()]
    print(f"{len(d)} mesures, {d['station'].nunique()} stations, années {sorted(int(a) for a in d['annee'].unique())}")

    # ---- Stations et mois comparables pour comparer les ANNÉES ----
    nb_ann = d.groupby("station")["annee"].nunique()
    n_tot = d["annee"].nunique()
    d["commune"] = True
    for k in range(n_tot, 1, -1):
        st_k = set(nb_ann[nb_ann >= k].index)
        if len(st_k) >= MIN_STATIONS_COMMUNES:
            d["commune"] = d["station"].isin(st_k)
            print(f"\nComparaison dans le temps : {len(st_k)} stations présentes dans au moins {k} années")
            break
    else:
        print("\nTrop peu de stations communes : comparaison entre années peu fiable.")
    dc = d[d["commune"]]
    annees_c = sorted(int(a) for a in dc["annee"].unique())
    exclues_a = [int(a) for a in sorted(d["annee"].unique()) if a not in annees_c]
    print(f"   Années comparables : {annees_c}")
    if exclues_a:
        print(f"   Années SANS ces stations (exclues des courbes par année) : {exclues_a}")

    mois_par_annee = dc.groupby("annee")["mois"].agg(lambda m: set(m))
    mois_communs = set.intersection(*mois_par_annee.tolist()) if len(mois_par_annee) else set()
    if mois_communs:
        dfa = dc[dc["mois"].isin(mois_communs)].copy()
        txt_mois = ", ".join(NOMS_MOIS[m - 1] for m in sorted(mois_communs))
        print(f"   Mois échantillonnés CHAQUE année (seuls utilisés pour comparer les années) : {txt_mois}")
    else:
        dfa = dc.copy()
        txt_mois = "tous les mois"
        print("   Aucun mois commun à toutes les années : comparaison saisonnière biaisée.")
    n_an = dfa.groupby("annee").size()
    print("   Mesures utilisées par année :", n_an.to_dict())
    for a, n in n_an.items():
        if n < MIN_MESURES_ANNEE:
            print(f"   ATTENTION : seulement {n} mesures en {a}, résultat peu fiable.")
    print(f"   Part de temps sec par année (%) : {(dfa.groupby('annee')['sec'].mean() * 100).round(0).to_dict()}")

    # ---- Dépassements de critères ----
    crit = calculer_criteres(d)
    print("\n--- Dépassements de critères (toutes années, toutes stations de la zone) ---")
    lignes = []
    for nom in crit.columns:
        valides = crit[nom].dropna()
        pct = valides.mean() * 100 if len(valides) else np.nan
        print(f"{nom} : {pct:.1f} % de {len(valides)} mesures évaluables")
        lignes.append({"critere": nom, "mesures_evaluables": len(valides), "pct_depassement": round(pct, 1)})
    pd.DataFrame(lignes).to_csv(dossier / "depassements_global.csv", index=False)

    crit_a = crit.loc[dfa.index]
    par_annee = crit_a.groupby(dfa["annee"]).mean().mul(100).round(1)
    sec = dfa["sec"]
    par_annee_sec = crit_a[sec].groupby(dfa.loc[sec, "annee"]).mean().mul(100).round(1)
    par_annee.to_csv(dossier / "depassements_par_annee.csv")
    par_annee_sec.to_csv(dossier / "depassements_par_annee_temps_sec.csv")
    print("\n% de dépassement par année (stations et mois comparables, tous échantillons) :\n", par_annee.to_string())
    print("\n% de dépassement par année (mêmes conditions, TEMPS SEC seulement) :\n", par_annee_sec.to_string())

    if len(par_annee.columns):
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
        barres_depassements(axes[0], par_annee, "Tous les échantillons")
        if len(par_annee_sec):
            barres_depassements(axes[1], par_annee_sec, "Temps sec seulement")
        axes[0].legend(fontsize=6)
        fig.suptitle(f"% de mesures qui dépassent chaque critère ({txt_mois}, stations comparables)")
        fig.tight_layout()
        fig.savefig(dossier / "depassements_par_annee.png")
        plt.close(fig)

    # ---- Années : évolution dans le temps ----
    print("\n--- Tendances (médiane annuelle, stations et mois comparables) ---")
    resume_annee = dfa.groupby("annee")[p].median().round(2)
    resume_annee.insert(0, "nb_mesures", dfa.groupby("annee").size())
    resume_annee.insert(1, "nb_stations", dfa.groupby("annee")["station"].nunique())
    resume_annee.to_csv(dossier / "resume_par_annee.csv")
    print(resume_annee.to_string())
    dsec = dfa[dfa["sec"]]
    dsec.groupby("annee")[p].median().round(2).to_csv(dossier / "resume_par_annee_temps_sec.csv")

    for c in p:
        s_all = dfa.groupby("annee")[c].median()[dfa.groupby("annee")[c].count() >= 3].dropna()
        s_sec = dsec.groupby("annee")[c].median()[dsec.groupby("annee")[c].count() >= 5].dropna()
        if s_all.empty:
            continue
        fig, ax = plt.subplots(figsize=(8, 4.2))
        ax.plot(s_all.index, s_all.values, marker="o", label="Médiane (tous échantillons)")
        if len(s_sec):
            ax.plot(s_sec.index, s_sec.values, marker="s", ls="--", color="gray", label="Médiane (temps sec seulement)")
        if len(s_all) >= 3:
            pente, ordonnee = np.polyfit(s_all.index, s_all.values, 1)
            ax.plot(s_all.index, pente * s_all.index + ordonnee, ":", color="tab:orange", label=f"Tendance indicative ({pente:+.3g}/an)")
            print(f"{c}: pente {pente:+.4g} par an, sur {len(s_all)} années (indicatif)")
        for a, note in NOTES_ANNEES.items():
            if a in s_all.index:
                ax.annotate(note, (a, s_all[a]), textcoords="offset points", xytext=(0, 14), ha="center", fontsize=7, color="dimgray")
        ax.set_xticks(list(s_all.index))
        if c in ECHELLE_LOG:
            ax.set_yscale("log")
        ax.set_title(f"{PARAMETRES[c]}\n(stations comparables, {txt_mois})", fontsize=10)
        ax.set_xlabel("Année")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(dossier / f"annee_{c}.png")
        plt.close(fig)

    # ---- Saisons : variation selon le mois (toutes années confondues) ----
    for c in p:
        mois_ok = [m for m in range(1, 13) if d.loc[d["mois"] == m, c].notna().sum() > 0]
        if not mois_ok:
            continue
        groupes = [d.loc[d["mois"] == m, c].dropna() for m in mois_ok]
        fig, ax = plt.subplots(figsize=(8, 4.2))
        ax.boxplot(groupes, showfliers=False)
        ax.set_xticks(range(1, len(mois_ok) + 1))
        ax.set_xticklabels([f"{NOMS_MOIS[m - 1]}\n(n={len(g)})" for m, g in zip(mois_ok, groupes)])
        if c in ECHELLE_LOG:
            ax.set_yscale("log")
        ax.set_title(f"Variation par mois (toutes années) : {PARAMETRES[c]}", fontsize=10)
        fig.tight_layout()
        fig.savefig(dossier / f"saison_{c}.png")
        plt.close(fig)

    # ---- Stations ----
    resume = d.groupby("station")[p].agg(["count", "median", "mean", "std"]).round(2)
    resume.insert(0, ("meta", "nb_annees"), d.groupby("station")["annee"].nunique())
    resume.to_csv(dossier / "resume_stations.csv")

    if cle == "emis":
        graphique_panache(dfa, dossier)  # pour EMIS, la distance au rejet remplace le classement des stations
    else:
        nb_par_station = d.groupby("station").size()
        stations_ok = nb_par_station[nb_par_station >= MIN_MESURES_STATION].index
        for c in p:
            med = d[d["station"].isin(stations_ok)].groupby("station")[c].median().dropna().sort_values(ascending=False).head(15)
            if med.empty:
                continue
            fig, ax = plt.subplots(figsize=(8, 5))
            med[::-1].plot(kind="barh", ax=ax)
            if c in ECHELLE_LOG:
                ax.set_xscale("log")
            ax.set_title(f"15 stations avec la médiane la plus élevée\n{PARAMETRES[c]}", fontsize=10)
            fig.tight_layout()
            fig.savefig(dossier / f"stations_{c}.png")
            plt.close(fig)
        nom_coli = f"Coliformes > {SEUIL_COLI_CONTACT} UFC/100 mL (contact direct)"
        if nom_coli in crit.columns:
            t = pd.DataFrame({"station": d["station"], "dep": crit[nom_coli]}).dropna()
            t = t[t["station"].isin(stations_ok)]
            par_st = t.groupby("station")["dep"].mean().mul(100).sort_values(ascending=False)
            par_st.round(1).to_csv(dossier / "depassement_coli_par_station.csv")
            top = par_st.head(15)
            fig, ax = plt.subplots(figsize=(8, 5))
            top[::-1].plot(kind="barh", ax=ax, color="firebrick")
            ax.set_title(f"% de dépassement de {SEUIL_COLI_CONTACT} UFC/100 mL, 15 pires stations", fontsize=10)
            ax.set_xlabel("% des mesures")
            fig.tight_layout()
            fig.savefig(dossier / "depassements_coli_stations.png")
            plt.close(fig)

    # ---- Météo et coliformes ----
    if "meteo" in d.columns and "coli" in d.columns:
        cats = [k for k in (1, 0, -1, -2) if (d["meteo"] == k).sum() > 0]
        if len(cats) >= 2:
            groupes = [d.loc[d["meteo"] == k, "coli"].dropna() for k in cats]
            fig, ax = plt.subplots(figsize=(8, 4.2))
            ax.boxplot(groupes, showfliers=False)
            ax.set_xticks(range(1, len(cats) + 1))
            ax.set_xticklabels([f"{LIBELLES_METEO[k]}\n(n={len(g)})" for k, g in zip(cats, groupes)])
            ax.set_yscale("log")
            ax.set_title("Coliformes fécaux selon la météo", fontsize=10)
            fig.tight_layout()
            fig.savefig(dossier / "meteo_coli.png")
            plt.close(fig)
            print("\nMédiane de coliformes selon la météo :")
            print(d.groupby("meteo")["coli"].median().rename(index=LIBELLES_METEO).to_string())

    # ---- Corrélations (Spearman : adapté aux valeurs très étalées) ----
    if len(p) >= 2:
        corr = d[p].corr(method="spearman")
        fig, ax = plt.subplots(figsize=(7, 6))
        im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
        ax.set_xticks(range(len(p)))
        ax.set_xticklabels(p, rotation=45, ha="right")
        ax.set_yticks(range(len(p)))
        ax.set_yticklabels(p)
        for i in range(len(p)):
            for j in range(len(p)):
                ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=8)
        fig.colorbar(im)
        ax.set_title("Corrélations entre paramètres (Spearman)", fontsize=10)
        ax.grid(False)
        fig.tight_layout()
        fig.savefig(dossier / "correlations.png")
        plt.close(fig)


# ---------------------------------------------------------------------
# 4. Lancer l'analyse pour chaque zone
# ---------------------------------------------------------------------
for cle, titre in ZONES.items():
    sous = df[df["zone"] == cle]
    if len(sous) >= 30:
        analyser(sous, cle, titre)
    else:
        print(f"\nZone '{cle}' ignorée : trop peu de mesures ({len(sous)}).")

print(f"\nTerminé. Résultats dans {SORTIE}/emis/ et {SORTIE}/cours_eau/ ; contrôles dans {SORTIE}/controles_biais.csv")
