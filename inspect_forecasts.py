import pandas as pd

path = "analytics/forecast/forecasts.csv"
df = pd.read_csv(path)

print("Columns:", df.columns.tolist())
print("Shape:", df.shape)
print("\nFirst 10 rows:")
print(df.head(10).to_string(index=False))
print("\nData types:")
print(df.dtypes.to_string())
