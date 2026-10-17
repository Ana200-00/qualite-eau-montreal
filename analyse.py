"""
Qualité de l'eau des cours d'eau de Montréal (programme COURDO, 2004-2017)

Trois questions, seulement sur les cours d'eau où l'eau devrait être propre :
  1. Chaque élément (température, pH, coliformes...) change-t-il dans le temps ?     -> evolution_*.png (courbes), tendances.csv
  2. Quel % des mesures ne respecte pas chaque critère québécois, année par année ?   -> depassements_par_annee.png
  3. Une région est-elle pire qu'une autre ?                                         -> depassements_par_region.png

On EXCLUT les points de rejet (STEP, OER) et le panache de la station d'épuration (EMIS) : ce n'est pas
de l'eau qu'on s'attend à voir propre. On garde seulement août-octobre (les mois présents chaque année).

Utilisation :  pip install pandas numpy matplotlib scipy
               python analyse.py        (CSV dans data/, résultats dans outputs/)
"""
import glob
import re
import unicodedata
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

# =====================================================================
# CONFIG
# =====================================================================
FICHIERS = "data/*.csv"
SORTIE = Path("outputs")

PARAMETRES = {
    "coli": "Coliformes fécaux (UFC/100 mL)",
    "ptot": "Phosphore total (µg/L)",
    "ph": "pH",
    "o2": "Oxygène dissous (mg/L)",
    "cond": "Conductivité (µS/cm)",
    "temp": "Température (°C)",
    "mes": "Matières en suspension (mg/L)",
    "nh3": "Azote ammoniacal (µg-N/L)",
}

EXCLURE_PREFIXES = ["STEP", "OER", "EMIS"]
MOIS_ETUDE = [8, 9, 10]          # août, septembre, octobre
MIN_STATIONS_FIABLE = 5          # groupe avec moins de stations : affiché en creux / pointillé (peu fiable)
MIN_STATIONS_COMMUNES = 8        # minimum de stations pour comparer des années
MIN_MESURES_GROUPE = 15          # minimum de mesures pour les tableaux et tests par région
MIN_MESURES_AFFICHAGE = 8        # minimum de mesures pour apparaître dans un graphique
COULEURS_PLAN = {"FSL": "tab:blue", "LSL": "tab:orange", "RDP": "tab:green", "BLAP": "tab:red"}
SEUIL_H1_PCT = 5                 # NOTRE choix : "respecté" = au plus 5 % des mesures dépassent
CHANGEMENT_MINIME_PCT = 10       # NOTRE choix : un changement de moins de 10 % est "minime"

# Critères de qualité du Québec (MELCCFP) : sources dans le README
SEUIL_COLI_CONTACT = 200         # UFC/100 mL, contact direct (baignade...)
SEUIL_COLI_INDIRECT = 1000       # UFC/100 mL, contact indirect (canotage, pêche...)
SEUIL_PTOT = 30                  # µg/L (0,03 mg/L), prévention de l'eutrophisation des rivières
PH_MIN, PH_MAX = 6.5, 9.0

PLANS_EAU = {"FSL": "Fleuve Saint-Laurent", "LSL": "Lac Saint-Louis", "RDP": "Rivière des Prairies",
             "BLAP": "Bassin de La Prairie", "RMI": "Rivière des Mille-Îles"}
# =====================================================================

SORTIE.mkdir(exist_ok=True)
plt.rcParams.update({"figure.dpi": 120, "axes.grid": True, "grid.alpha": 0.3})


# ---------------------------------------------------------------------
# 1. Chargement (dates et colonnes différentes d'un fichier à l'autre)
# ---------------------------------------------------------------------
def normaliser(nom):
    nom = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", nom.lower()).strip("_")


RENOMMER = {"point_d_echantillonnage": "station", "od": "od_pct", "temp_oc": "temp", "conductivite": "cond", "p": "ptot"}


def nom_court(nom):
    n = re.sub(r"_(ug|mg|g)_l$", "", normaliser(nom))
    return RENOMMER.get(n, n)


def lire(fichier):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(fichier, sep=None, engine="python", encoding=enc)
        except Exception:
            continue
    raise ValueError(f"Impossible de lire {fichier}")


FORMATS_DATE = ["%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%d-%m-%Y %H:%M", "%d-%m-%Y",
                "%d/%m/%y %H:%M", "%d/%m/%y", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y",
                "%b %d, %Y", "%d%b%Y", "%d%b%y"]
MOIS_PREFIXES = {"janv": "jan", "fev": "feb", "mars": "mar", "avr": "apr", "mai": "may", "juin": "jun",
                 "juil": "jul", "aout": "aug", "sep": "sep", "oct": "oct", "nov": "nov", "dec": "dec"}


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
    t = re.sub(r"[a-z]{3,}", _mot_mois, re.sub(r"\s+", " ", t))
    return t if t not in ("", "nan", "nat") else np.nan


def lire_dates(serie):
    """Essaie plusieurs formats valeur par valeur, puis les numéros de série Excel."""
    s = serie.map(_preparer_date)
    resultat = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in FORMATS_DATE:
        reste = resultat.isna() & s.notna()
        if not reste.any():
            break
        resultat = resultat.fillna(pd.to_datetime(s.where(reste), format=fmt, errors="coerce"))
    reste = resultat.isna() & s.notna()
    if reste.any():
        nombres = pd.to_numeric(s.where(reste), errors="coerce")
        ok = nombres.between(25000, 60000)
        if ok.any():
            resultat = resultat.fillna(pd.to_datetime(nombres.where(ok), unit="D", origin="1899-12-30", errors="coerce"))
    return resultat


def en_nombre(serie):
    """Virgule décimale -> point ; retire < et > ; texte -> NaN."""
    s = serie.astype(str).str.replace(",", ".", regex=False).str.replace("<", "", regex=False)
    return pd.to_numeric(s.str.replace(">", "", regex=False).str.strip(), errors="coerce")


fichiers = sorted(glob.glob(FICHIERS))
if not fichiers:
    raise SystemExit(f"Aucun fichier trouvé avec {FICHIERS}. Mets tes CSV dans data/.")

morceaux = []
print("=== Chargement ===")
for f in fichiers:
    d = lire(f)
    d.columns = [nom_court(c) for c in d.columns]
    d = d.loc[:, ~d.columns.duplicated()]
    brutes = d["date"].copy()
    d["date"] = lire_dates(brutes)
    echecs = d["date"].isna().sum() - brutes.isna().sum()
    print(f"{Path(f).name}: {len(d)} lignes, dates non comprises : {echecs}")
    morceaux.append(d)
df = pd.concat(morceaux, ignore_index=True)

# ---------------------------------------------------------------------
# 2. Nettoyage et choix des données à garder (chaque filtre est affiché)
# ---------------------------------------------------------------------
print("\n=== Ce qu'on garde ===")
n = len(df)
df = df.dropna(subset=["date", "station"]).copy()
print(f"Lignes sans date ou station retirées : {n - len(df)}")

params = [c for c in PARAMETRES if c in df.columns]
for c in params + ["meteo"]:
    if c in df.columns:
        df[c] = en_nombre(df[c])
df["station"] = df["station"].astype(str).str.strip().str.upper()
df["annee"], df["mois"] = df["date"].dt.year, df["date"].dt.month

n = len(df)
df["_n"] = df[params].notna().sum(axis=1)
df = df.sort_values("_n", ascending=False).drop_duplicates(["station", "date"]).drop(columns="_n")
print(f"Doublons retirés : {n - len(df)}")

masque = df["station"].str.startswith(tuple(p.upper() for p in EXCLURE_PREFIXES))
for p in EXCLURE_PREFIXES:
    print(f"Hors portée, préfixe {p} : {int(df.loc[masque, 'station'].str.startswith(p).sum())} mesures retirées")
df = df[~masque].copy()

df["plan"] = df["station"].str.extract(r"^([A-Z]+)")[0]
df["sec"] = df["meteo"] == 1

st_an = df.groupby("annee")["station"].nunique()
for a in st_an[st_an < MIN_STATIONS_FIABLE].index:
    print(f"Année {int(a)} : seulement {int(st_an[a])} station(s) -> gardée dans les graphiques (en creux), pas dans les tests")

n = len(df)
df = df[df["mois"].isin(MOIS_ETUDE)].sort_values(["date", "station"]).reset_index(drop=True)
print(f"Mesures hors août-octobre retirées : {n - len(df)}")
print(f"=> {len(df)} mesures, {df['station'].nunique()} stations, années {sorted(int(a) for a in df['annee'].unique())}")
df.to_csv(SORTIE / "donnees_nettoyees.csv", index=False)

structure = df.groupby(["annee", "plan"]).agg(stations=("station", "nunique"), mesures=("station", "size"),
                                              pct_temps_sec=("sec", lambda s: round(s.mean() * 100)))
structure.to_csv(SORTIE / "structure_donnees.csv")
print("\nAnnée x plan d'eau (chaque année ne couvre pas les mêmes plans d'eau) :")
print(structure.to_string())


# ---------------------------------------------------------------------
# 3. Critères de qualité du Québec : une mesure dépasse (1) ou respecte (0)
# ---------------------------------------------------------------------
def depasse_coli(d, seuil):
    """Tient compte du signe : '>x' dépasse sûrement, '<x' ne dépasse sûrement pas."""
    v = d["coli"]
    signe = d["signe_coli"].fillna("=").astype(str).str.strip() if "signe_coli" in d else pd.Series("=", index=d.index)
    r = np.where(signe == ">", np.where(v >= seuil, 1.0, np.nan),
                 np.where(signe == "<", np.where(v <= seuil, 0.0, np.nan), (v > seuil).astype(float)))
    return pd.Series(r, index=d.index).where(v.notna())


C_COLI200 = f"Coliformes > {SEUIL_COLI_CONTACT} UFC/100 mL (contact direct)"
C_COLI1000 = f"Coliformes > {SEUIL_COLI_INDIRECT} UFC/100 mL (contact indirect)"
C_PTOT = f"Phosphore total > {SEUIL_PTOT} µg/L"
C_PH = f"pH hors de {PH_MIN} à {PH_MAX}"


def calculer_criteres(d):
    c = pd.DataFrame(index=d.index)
    c[C_COLI200] = depasse_coli(d, SEUIL_COLI_CONTACT)
    c[C_COLI1000] = depasse_coli(d, SEUIL_COLI_INDIRECT)
    c[C_PTOT] = (d["ptot"] > SEUIL_PTOT).astype(float).where(d["ptot"].notna())
    c[C_PH] = ((d["ph"] < PH_MIN) | (d["ph"] > PH_MAX)).astype(float).where(d["ph"].notna())
    return c


crit = calculer_criteres(df)

print("\n=== DÉPASSEMENTS DES CRITÈRES (toutes régions et années, août-octobre) ===")
for nom in crit.columns:
    v = crit[nom].dropna()
    print(f"{nom} : {v.mean() * 100:.1f} % de {len(v)} mesures")

# --- Par région et année ---
lignes = []
for (plan, annee), g in df.groupby(["plan", "annee"]):
    if len(g) < MIN_MESURES_GROUPE:
        continue
    for nom in crit.columns:
        v = crit.loc[g.index, nom].dropna()
        k, nb = int(v.sum()), len(v)
        ic = stats.binomtest(k, nb).proportion_ci(confidence_level=0.95, method="wilson")
        bas, haut = ic.low * 100, ic.high * 100
        verdict = "H1 rejetée" if bas > SEUIL_H1_PCT else ("H1 soutenue" if haut <= SEUIL_H1_PCT else "indéterminé")
        lignes.append({"region": plan, "annee": int(annee), "stations": g["station"].nunique(), "mesures_region": len(g),
                       "critere": nom, "mesures_evaluables": nb, "pct_depassement": round(k / nb * 100, 1),
                       "ic95_bas": round(bas, 1), "ic95_haut": round(haut, 1), "verdict_H1": verdict})
reg = pd.DataFrame(lignes)
reg.to_csv(SORTIE / "criteres_par_region.csv", index=False)
print("\n=== PAR RÉGION ET ANNÉE (% de mesures qui dépassent) ===")
tab = reg.pivot_table(index="critere", columns=["region", "annee"], values="pct_depassement")
print(tab.round(1).to_string())
print(f"\nVerdict H1 (seuil {SEUIL_H1_PCT} %) :")
print(reg.groupby("critere")["verdict_H1"].value_counts().unstack(fill_value=0).to_string())

# --- Sensibilité : le lac Saint-Louis est un lac ; le critère de phosphore des lacs peut aller jusqu'à 20 µg/L ---
if "LSL" in df["plan"].values:
    v = df.loc[df["plan"] == "LSL", "ptot"].dropna()
    print(f"\nSensibilité LSL : phosphore > 30 µg/L (cours d'eau) = {(v > 30).mean() * 100:.1f} % ; "
          f"> 20 µg/L (critère des lacs) = {(v > 20).mean() * 100:.1f} % ({len(v)} mesures)")

# --- Comparaison de deux régions la même année (test exact de Fisher) ---
comp = []
for annee in sorted(reg["annee"].unique()):
    regions = sorted(reg.loc[reg["annee"] == annee, "region"].unique())
    for r1, r2 in combinations(regions, 2):
        for nom in (C_COLI200, C_PTOT):
            a = crit.loc[(df["plan"] == r1) & (df["annee"] == annee), nom].dropna()
            b = crit.loc[(df["plan"] == r2) & (df["annee"] == annee), nom].dropna()
            p = stats.fisher_exact([[int(a.sum()), len(a) - int(a.sum())], [int(b.sum()), len(b) - int(b.sum())]])[1]
            comp.append({"annee": int(annee), "region_1": r1, "region_2": r2, "critere": nom,
                         "pct_1": round(a.mean() * 100, 1), "pct_2": round(b.mean() * 100, 1), "p": p})
comp = pd.DataFrame(comp)
if len(comp):
    comp["seuil_corrige"] = 0.05 / len(comp)
    comp["difference_nette"] = comp["p"] < comp["seuil_corrige"]
    comp.to_csv(SORTIE / "comparaison_regions.csv", index=False)
    print("\n=== RÉGIONS COMPARÉES LA MÊME ANNÉE (Fisher) ===")
    print(comp.assign(p=comp["p"].round(4)).to_string(index=False))

# --- Graphiques de dépassement ---
C_UN = "Au moins un critère dépassé"
crit[C_UN] = crit[[C_COLI200, C_PTOT, C_PH]].max(axis=1)   # coli > 1000 est déjà compris dans coli > 200
annees_toutes = sorted(int(a) for a in df["annee"].unique())
NOMS_COURTS = {"FSL": "Fleuve", "LSL": "Lac\nSt-Louis", "RDP": "Rivière\ndes Prairies", "BLAP": "Bassin\nLa Prairie"}


def taux(masque, nom):
    v = crit.loc[masque, nom].dropna()
    return round(v.mean() * 100, 1) if len(v) else np.nan


# Graphique 1 : % des mesures qui ne respectent pas chaque critère, année par année (toutes régions ensemble)
cols_g1 = [C_COLI200, C_COLI1000, C_PTOT, C_PH, C_UN]
annuel = []
for a in annees_toutes:
    g = df[df["annee"] == a]
    plans = sorted(p for p, n in g["plan"].value_counts().items() if n >= MIN_MESURES_AFFICHAGE)
    ligne = {"annee": a, "plans_eau": ", ".join(plans), "stations": g["station"].nunique(), "mesures": len(g)}
    ligne.update({nom: taux(df["annee"] == a, nom) for nom in cols_g1})
    annuel.append(ligne)
annuel = pd.DataFrame(annuel)
annuel["fiable"] = annuel["stations"] >= MIN_STATIONS_FIABLE
annuel.to_csv(SORTIE / "depassements_par_annee.csv", index=False)
print("\n=== DÉPASSEMENTS PAR ANNÉE (toutes régions combinées) ===")
print(annuel.drop(columns="fiable").to_string(index=False))

noms_g1 = {C_COLI200: f"Coliformes > {SEUIL_COLI_CONTACT} UFC/100 mL", C_COLI1000: f"Coliformes > {SEUIL_COLI_INDIRECT} UFC/100 mL",
           C_PTOT: f"Phosphore > {SEUIL_PTOT} µg/L", C_PH: f"pH hors de {PH_MIN} à {PH_MAX}", C_UN: "Au moins un critère"}
couleurs_g1 = {C_COLI200: "tab:red", C_COLI1000: "tab:orange", C_PTOT: "tab:green", C_PH: "tab:purple", C_UN: "black"}
fig, ax = plt.subplots(figsize=(9, 5))
pos = np.arange(len(annuel))
for nom in cols_g1:
    ax.plot(pos, annuel[nom], color=couleurs_g1[nom], marker="o", lw=2.8 if nom == C_UN else 1.6, label=noms_g1[nom])
    creux = ~annuel["fiable"]
    ax.scatter(pos[creux], annuel.loc[creux, nom], s=60, facecolor="white", edgecolor=couleurs_g1[nom], zorder=4)
ax.axhline(SEUIL_H1_PCT, color="gray", ls="--", lw=1)
ax.text(0.99, SEUIL_H1_PCT + 0.8, f"{SEUIL_H1_PCT} %", transform=ax.get_yaxis_transform(), ha="right", fontsize=8, color="gray")
etiq = []
for r in annuel.itertuples():
    note = f"\n({r.stations} stations seulement)" if not r.fiable else (
        f"\n({PLANS_EAU.get(r.plans_eau, r.plans_eau)} seulement)" if "," not in r.plans_eau else "")
    etiq.append(f"{r.annee}{note}")
ax.set_xticks(pos)
ax.set_xticklabels(etiq, fontsize=9)
ax.set_ylabel("% des mesures qui ne respectent pas le critère")
ax.set_title("Mesures qui ne respectent pas les critères québécois, année par année (août-octobre)", fontsize=10)
ax.legend(fontsize=8, loc="upper left")
ax.set_ylim(bottom=0)
fig.tight_layout()
fig.savefig(SORTIE / "depassements_par_annee.png")
plt.close(fig)

# Graphique 2 : comparaison des régions, un petit graphique par critère (une barre par région et par année)
ordre = [r for r in NOMS_COURTS if r in set(reg["region"])]
cles = sorted({(r, a) for r, a in zip(reg["region"], reg["annee"])}, key=lambda k: (ordre.index(k[0]), k[1]))
fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
for ax, nom in zip(axes.ravel(), [C_COLI200, C_COLI1000, C_PTOT, C_PH]):
    for i_, (r, a) in enumerate(cles):
        ligne = reg[(reg["region"] == r) & (reg["annee"] == a) & (reg["critere"] == nom)].iloc[0]
        barre = ax.bar(i_, ligne["pct_depassement"], color=COULEURS_PLAN.get(r, "gray"), alpha=1 if ligne["stations"] >= MIN_STATIONS_FIABLE else 0.4)
        ax.text(i_, ligne["pct_depassement"] + 0.8, f"{ligne['pct_depassement']:.0f} %", ha="center", fontsize=8)
    ax.axhline(SEUIL_H1_PCT, color="gray", ls="--", lw=1)
    ax.set_xticks(range(len(cles)))
    ax.set_xticklabels([f"{NOMS_COURTS[r]}\n{a}" for r, a in cles], fontsize=7.5)
    ax.set_title(noms_g1[nom], fontsize=10)
    ax.set_ylim(0, max(10, reg.loc[reg["critere"] == nom, "pct_depassement"].max() * 1.2))
fig.supylabel("% des mesures qui ne respectent pas le critère", fontsize=9)
fig.suptitle("Comparaison des régions (août-octobre). Barre pâle : moins de 5 stations", fontsize=10)
fig.tight_layout()
fig.savefig(SORTIE / "depassements_par_region.png")
plt.close(fig)

# ---------------------------------------------------------------------
# 4. Évolution dans le temps : MÊMES stations, MÊMES mois, test apparié
# ---------------------------------------------------------------------
print("\n=== ÉVOLUTION DANS LE TEMPS ===")
annees = sorted(int(a) for a in df["annee"].unique())
stations_par_annee = {a: set(df.loc[df["annee"] == a, "station"]) for a in annees}
paires = [(len(stations_par_annee[a] & stations_par_annee[b]), a, b) for a, b in combinations(annees, 2)]
nb_communes, A, B = max(paires)
print("Stations communes entre années :", {f"{a}-{b}": n for n, a, b in paires})
if nb_communes < MIN_STATIONS_COMMUNES:
    raise SystemExit("Trop peu de stations communes : pas de comparaison dans le temps possible.")
communes = stations_par_annee[A] & stations_par_annee[B]
x = df[df["station"].isin(communes) & df["annee"].isin([A, B])]
part_sec = (x.groupby("annee")["sec"].mean() * 100).round(0)
print(f"Années comparées : {A} et {B}, {len(communes)} stations communes, {len(x)} mesures")
print(f"Part de temps sec : {A} = {part_sec[A]:.0f} %, {B} = {part_sec[B]:.0f} %  (si très différent, la météo brouille la comparaison)")
print("Les années restantes ne sont pas comparables (autres stations) : on ne peut pas tester une tendance sur plus de 2 années.")

groupes = [("toutes", communes)]
for plan in sorted(x["plan"].unique()):
    st = set(x.loc[x["plan"] == plan, "station"])
    if len(st) >= MIN_STATIONS_COMMUNES:
        groupes.append((plan, st))

res = []
for nom_g, st in groupes:
    xg = x[x["station"].isin(st)]
    for p in params:
        m = xg.groupby(["station", "annee"])[p].median().unstack().dropna()
        if len(m) < MIN_STATIONS_COMMUNES:
            print(f"   {nom_g} / {p} : seulement {len(m)} station(s) avec des données les deux années -> non testé")
            continue
        try:
            pv = stats.wilcoxon(m[A], m[B]).pvalue
        except ValueError:          # toutes les différences sont nulles
            pv = 1.0
        med_a, med_b = m[A].median(), m[B].median()
        ligne = {"groupe": nom_g, "parametre": p, "stations": len(m), f"mediane_{A}": round(med_a, 2),
                 f"mediane_{B}": round(med_b, 2), "changement_pct": round((med_b - med_a) / med_a * 100, 1), "p": pv,
                 "pct_stations_en_hausse": round((m[B] > m[A]).mean() * 100)}
        # Vérification en temps sec seulement : la météo explique-t-elle l'écart ?
        sa = xg[(xg["annee"] == A) & xg["sec"]][p].dropna()
        sb = xg[(xg["annee"] == B) & xg["sec"]][p].dropna()
        assez = min(len(sa), len(sb)) >= 8
        ligne["p_temps_sec"] = stats.mannwhitneyu(sa, sb).pvalue if assez else np.nan
        ligne["sens_temps_sec"] = np.sign(sb.median() - sa.median()) if assez else np.nan
        res.append(ligne)
res = pd.DataFrame(res)
seuil = 0.05 / len(res)


def verdict(r):
    if r["p"] >= seuil:
        return "aucune différence détectable"
    if abs(r["changement_pct"]) < CHANGEMENT_MINIME_PCT:
        return "différence réelle mais minime"
    if not (r["p_temps_sec"] < 0.05 and r["sens_temps_sec"] == np.sign(r["changement_pct"])):
        return "différence possiblement due à la météo"
    return "différence nette"


res["verdict"] = res.apply(verdict, axis=1)
res["passe_seuil_corrige"] = res["p"] < seuil
res.to_csv(SORTIE / "tendances.csv", index=False)
print(f"\n{len(res)} tests -> seuil corrigé (Bonferroni) = {seuil:.4f}")
print(res.drop(columns=["sens_temps_sec"]).assign(p=res["p"].round(4)).to_string(index=False))

# H2 : si un paramètre change, est-ce dans le même sens dans toutes les régions ?
print("\n=== H2 : changements dans le même sens dans toutes les régions ? ===")
regions = res[res["groupe"] != "toutes"].pivot(index="parametre", columns="groupe", values="changement_pct")
sig = res[res["groupe"] != "toutes"].pivot(index="parametre", columns="groupe", values="passe_seuil_corrige")
regions["meme_sens"] = regions.apply(lambda r: bool(np.all(np.sign(r) == np.sign(r.iloc[0]))), axis=1)
regions["significatif_dans"] = [", ".join(c for c in sig.columns if sig.loc[p, c]) or "aucune" for p in regions.index]
print(regions.to_string())
regions.to_csv(SORTIE / "tendances_par_region.csv")

# --- Graphiques d'évolution : une courbe par plan d'eau (médiane de chaque année) ---
# (les tests ci-dessus ne portent que sur les stations communes ; ces courbes montrent TOUTES les mesures)
for p in params:
    g = df.groupby(["plan", "annee"])[p].agg(mediane="median", n="count").reset_index()
    nst = df[df[p].notna()].groupby(["plan", "annee"])["station"].nunique().rename("stations").reset_index()
    g = g.merge(nst, on=["plan", "annee"])
    g = g[g["n"] >= MIN_MESURES_AFFICHAGE]
    if g.empty:
        continue
    fig, ax = plt.subplots(figsize=(8, 4.6))
    for plan in COULEURS_PLAN:
        s = g[g["plan"] == plan].sort_values("annee")
        if s.empty:
            continue
        ax.plot(s["annee"], s["mediane"], color=COULEURS_PLAN[plan], marker="o", lw=2, label=PLANS_EAU.get(plan, plan))
        peu = s[s["stations"] < MIN_STATIONS_FIABLE]
        ax.scatter(peu["annee"], peu["mediane"], s=60, facecolor="white", edgecolor=COULEURS_PLAN[plan], zorder=4)
    ax.set_xticks(annees_toutes)
    ax.set_xlim(min(annees_toutes) - 1, max(annees_toutes) + 1)
    ax.set_xlabel("Année")
    ax.set_ylabel(PARAMETRES[p])
    ax.set_title(f"{PARAMETRES[p]} : médiane de chaque plan d'eau, année par année", fontsize=10)
    ax.legend(fontsize=8)
    fig.text(0.01, 0.01, "Août-octobre. Cercle creux : moins de 5 stations. Point seul : plan d'eau mesuré une seule année.", fontsize=7, color="gray")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(SORTIE / f"evolution_{p}.png")
    plt.close(fig)

# --- Stations suivies sur 3 années ou plus (ex. 2004, 2011 et 2017) : la seule façon d'aller jusqu'à 2017 ---
nb_ann_st = df.groupby("station")["annee"].nunique()
suivies = sorted(nb_ann_st[nb_ann_st >= 3].index)
if suivies:
    lignes = []
    for st in suivies:
        for a, g in df[df["station"] == st].groupby("annee"):
            lig = {"station": st, "annee": int(a), "mesures": len(g), "pct_coli_sup_200": round(crit.loc[g.index, C_COLI200].mean() * 100)}
            lig.update({f"mediane_{p}": round(g[p].median(), 2) for p in params})
            lignes.append(lig)
    suivi = pd.DataFrame(lignes)
    suivi.to_csv(SORTIE / "stations_suivies.csv", index=False)
    print("\n=== STATIONS MESURÉES AU MOINS 3 ANNÉES (seules à aller jusqu'à 2017) ===")
    print(suivi.drop(columns=["mediane_nh3"]).to_string(index=False))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for st in suivies:
        s = suivi[suivi["station"] == st]
        axes[0].plot(s["annee"].astype(str), s["mediane_coli"], marker="o", label=st)
        axes[1].plot(s["annee"].astype(str), s["pct_coli_sup_200"], marker="o", label=st)
    axes[0].set_yscale("log")
    axes[0].set_title("Coliformes : médiane de la station (UFC/100 mL, échelle logarithmique)", fontsize=9)
    axes[1].set_title(f"% des mesures de la station > {SEUIL_COLI_CONTACT} UFC/100 mL", fontsize=9)
    axes[0].legend(fontsize=8)
    fig.suptitle("Stations mesurées en 2004, 2011 et 2017 (août-octobre)", fontsize=10)
    fig.tight_layout()
    fig.savefig(SORTIE / "evolution_stations_suivies.png")
    plt.close(fig)

print(f"\nTerminé. Résultats dans {SORTIE}/")
