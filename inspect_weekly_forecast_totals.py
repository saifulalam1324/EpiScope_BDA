import pandas as pd

df = pd.read_csv("analytics/forecast/forecasts.csv")
df["predicted_cases"] = pd.to_numeric(
    df["predicted_cases"], errors="coerce"
)

weekly = (
    df.groupby("week_start")["predicted_cases"]
    .sum()
    .sort_index()
)

print("Weekly aggregate forecast diagnostics")
print("Number of weeks:", len(weekly))
print("Numeric weeks:", weekly.notna().sum())
print("Minimum weekly total:", round(weekly.min(), 3))
print("Maximum weekly total:", round(weekly.max(), 3))
print("Weeks below 5:", int((weekly < 5).sum()))
print("Weeks at least 5:", int((weekly >= 5).sum()))
print("\nFirst 10 weekly totals:")
print(weekly.head(10).round(3).to_string())
