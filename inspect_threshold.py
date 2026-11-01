from pathlib import Path
import pandas as pd

path = Path("analytics/detection/threshold_tradeoff.csv")

print("File exists:", path.exists())

if path.exists():
    df = pd.read_csv(path)
    print("\nColumns:")
    print(df.columns.tolist())

    print("\nShape:")
    print(df.shape)

    print("\nFirst 10 rows:")
    print(df.head(10).to_string(index=False))

    print("\nData types:")
    print(df.dtypes.to_string())
