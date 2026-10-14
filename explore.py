import glob
import pandas as pd

for fichier in glob.glob("data/*.csv"):
    df = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(fichier, sep=None, engine="python", encoding=enc)
            break
        except Exception:
            continue
    if df is None:
        print("Impossible de lire", fichier)
        continue

    print("=" * 60)
    print(fichier)
    print("Dimensions :", df.shape)
    print("\nColonnes :")
    for c in df.columns:
        print("  -", c)
    print("\nAperçu :")
    print(df.head(5).to_string())
    print("\nTypes :")
    print(df.dtypes)
    print("\nValeurs manquantes (top 10) :")
    print(df.isna().sum().sort_values(ascending=False).head(10))