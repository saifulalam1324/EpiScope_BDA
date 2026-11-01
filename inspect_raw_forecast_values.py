import pandas as pd

df = pd.read_csv("analytics/forecast/forecasts.csv")

print("Columns:", df.columns.tolist())
print("\nForecast value types:")
print(df["predicted_cases"].dtype)

print("\nFirst 10 raw predictions:")
print(df[["district_id", "disease", "week_start", "predicted_cases"]].head(10).to_string(index=False))

print("\nNumeric prediction count:",
      pd.to_numeric(df["predicted_cases"], errors="coerce").notna().sum())
