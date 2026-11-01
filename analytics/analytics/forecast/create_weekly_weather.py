
from pathlib import Path
import pandas as pd

# ------------------------------------------------------------
# PATHS
# ------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]

DAILY_INPUT = ROOT / "data/weather/weather_daily.csv"
GOLD_INPUT = ROOT / "data/lake/gold/weekly_district_disease"
WEEKLY_OUTPUT = ROOT / "data/weather/weather_weekly.csv"

EXPECTED_DAILY_RECORDS = 1826

REQUIRED_COLUMNS = {
    "district_id",
    "district_name",
    "date",
    "temperature_mean_c",
    "precipitation_mm",
}


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():

    print("=" * 60)
    print("EPISCOPE - WEEKLY WEATHER AGGREGATION")
    print("=" * 60)

    if not DAILY_INPUT.exists():
        raise FileNotFoundError(
            f"Daily weather file not found: {DAILY_INPUT}"
        )

    if not GOLD_INPUT.exists():
        raise FileNotFoundError(
            f"Gold dataset not found: {GOLD_INPUT}"
        )

    weather = pd.read_csv(DAILY_INPUT)

    missing_columns = REQUIRED_COLUMNS - set(weather.columns)

    if missing_columns:
        raise ValueError(
            f"Daily weather missing columns: {sorted(missing_columns)}"
        )

    print("\nDaily weather rows:", len(weather))

    # --------------------------------------------------------
    # VALIDATE AND NORMALIZE
    # --------------------------------------------------------

    weather["date"] = pd.to_datetime(
        weather["date"],
        errors="coerce",
    )

    weather["temperature_mean_c"] = pd.to_numeric(
        weather["temperature_mean_c"],
        errors="coerce",
    )

    weather["precipitation_mm"] = pd.to_numeric(
        weather["precipitation_mm"],
        errors="coerce",
    )

    if weather["date"].isna().any():
        raise ValueError("Invalid dates found in daily weather.")

    if weather[
        ["temperature_mean_c", "precipitation_mm"]
    ].isna().any().any():
        raise ValueError("Missing or invalid weather measurements found.")

    if (weather["precipitation_mm"] < 0).any():
        raise ValueError("Negative precipitation values found.")

    weather["district_name"] = (
        weather["district_name"].astype(str).str.strip()
    )

    weather["district_id"] = (
        weather["district_id"].astype(str).str.strip()
    )

    if weather["district_name"].eq("").any():
        raise ValueError("Empty district names found.")

    # Each district must have only one observation per date.
    duplicate_mask = weather.duplicated(
        ["district_id", "date"],
        keep=False,
    )

    if duplicate_mask.any():
        raise ValueError(
            "Duplicate district/date observations found:\n"
            + weather.loc[
                duplicate_mask,
                ["district_id", "district_name", "date"],
            ].head(20).to_string(index=False)
        )

    # --------------------------------------------------------
    # DAILY COVERAGE REPORT
    # --------------------------------------------------------

    coverage = (
        weather.groupby(
            ["district_id", "district_name"]
        )
        .agg(
            daily_rows=("date", "size"),
            unique_dates=("date", "nunique"),
            first_date=("date", "min"),
            last_date=("date", "max"),
        )
        .reset_index()
    )

    incomplete = coverage[
        coverage["unique_dates"] != EXPECTED_DAILY_RECORDS
    ]

    print("\nDistricts with daily weather:", len(coverage))
    print("Incomplete districts:", len(incomplete))

    if not incomplete.empty:
        print("\nIncomplete daily weather coverage:")
        print(incomplete.to_string(index=False))

    # --------------------------------------------------------
    # CHECK GOLD DISTRICT COVERAGE
    # --------------------------------------------------------

    gold = pd.read_parquet(
        GOLD_INPUT,
        columns=["district_name"],
    )

    gold_districts = set(
        gold["district_name"]
        .dropna()
        .astype(str)
        .str.strip()
        .str.casefold()
    )

    weather_districts = set(
        weather["district_name"]
        .str.casefold()
    )

    missing_districts = sorted(
        gold_districts - weather_districts
    )

    print("\nGold districts:", len(gold_districts))
    print("Weather districts:", len(weather_districts))
    print("Gold districts without weather:", len(missing_districts))

    if missing_districts:
        print("Missing districts:", missing_districts)

    # --------------------------------------------------------
    # WEEKLY AGGREGATION
    # Monday is the start of each week.
    # Temperature = weekly mean.
    # Precipitation = weekly sum.
    # --------------------------------------------------------

    weather["week_start"] = (
        weather["date"]
        - pd.to_timedelta(
            weather["date"].dt.weekday,
            unit="D",
        )
    )

    weekly = (
        weather.groupby(
            ["district_id", "district_name", "week_start"],
            as_index=False,
        )
        .agg(
            temperature_mean_c=(
                "temperature_mean_c",
                "mean",
            ),
            precipitation_mm=(
                "precipitation_mm",
                "sum",
            ),
            daily_observations=(
                "date",
                "nunique",
            ),
        )
    )

    weekly = weekly.sort_values(
        ["district_name", "week_start"]
    )

    if weekly.duplicated(
        ["district_id", "week_start"]
    ).any():
        raise ValueError(
            "Duplicate district-week records after aggregation."
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    WEEKLY_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_output = WEEKLY_OUTPUT.with_suffix(".tmp.csv")

    weekly.to_csv(
        temp_output,
        index=False,
    )

    temp_output.replace(WEEKLY_OUTPUT)

    print("\n" + "=" * 60)
    print("WEEKLY WEATHER SUMMARY")
    print("=" * 60)

    print("Weekly records:", len(weekly))
    print("Districts:", weekly["district_id"].nunique())
    print("Weeks:", weekly["week_start"].nunique())

    print("\nWeekly sample:")
    print(weekly.head(10).to_string(index=False))

    print("\nSaved:", WEEKLY_OUTPUT)

    if missing_districts:
        print(
            "\nWARNING: Weekly weather is incomplete nationally. "
            "Missing districts have not been fabricated."
        )
    else:
        print("\nAll Gold districts have weather coverage.")


if __name__ == "__main__":
    main()
