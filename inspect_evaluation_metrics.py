from pathlib import Path
import pandas as pd

path = Path("analytics/detection/evaluation_metrics.csv")

print("File exists:", path.exists())

if path.exists():
    df = pd.read_csv(path)
    print("\nColumns:", df.columns.tolist())
    print("\nShape:", df.shape)
    print("\nAll rows:")
    print(df.to_string(index=False))
