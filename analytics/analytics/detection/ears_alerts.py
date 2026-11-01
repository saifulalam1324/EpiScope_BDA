
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[3]

INPUT = ROOT / "data/lake/gold/weekly_district_disease"
OUTPUT = ROOT / "analytics/detection/ears_alerts.csv"


# ============================================================
# CONFIGURATION
# ============================================================

BASELINE_WEEKS = 7
MIN_BASELINE_WEEKS = 4

MIN_CASES_FOR_ALERT = 2

# Alert if cases exceed baseline mean by at least
# the larger of 2 cases or 2 standard deviations.
MIN_ABSOLUTE_INCREASE = 2
Z_THRESHOLD = 2.0

MISSING_WEEK_MEANS_ZERO = True


# ============================================================
# LOAD GOLD DATA
# ============================================================

def load_gold():

    if not INPUT.exists():
        raise FileNotFoundError(
            f"Gold input not found: {INPUT}"
        )

    gold = pd.read_parquet(INPUT)

    required = {
        "district_id",
        "district_name",
        "disease_group",
        "week_start",
        "case_count",
        "admissions",
        "lab_confirmed_cases",
        "bronze_batch_ids",
    }

    missing = required - set(gold.columns)

    if missing:
        raise ValueError(
            f"Missing Gold columns: {sorted(missing)}"
        )

    if gold.empty:
        raise ValueError("Gold dataset is empty.")

    gold = gold.rename(
        columns={
            "disease_group": "disease",
            "case_count": "cases",
        }
    )

    for column in [
        "district_id",
        "district_name",
        "disease",
    ]:
        if gold[column].isna().any():
            raise ValueError(
                f"{column} contains null values."
            )

    gold["week_start"] = pd.to_datetime(
        gold["week_start"],
        errors="coerce",
    )

    if gold["week_start"].isna().any():
        raise ValueError("Invalid week_start values.")

    if (gold["week_start"].dt.dayofweek != 0).any():
        raise ValueError(
            "week_start must contain Monday dates."
        )

    gold["cases"] = pd.to_numeric(
        gold["cases"],
        errors="coerce",
    )

    if gold["cases"].isna().any():
        raise ValueError("Invalid case counts.")

    if (gold["cases"] < 0).any():
        raise ValueError("Negative case counts found.")

    if not np.allclose(
        gold["cases"],
        gold["cases"].round(),
    ):
        raise ValueError(
            "Case counts must be whole numbers."
        )

    gold["cases"] = gold["cases"].astype("int64")

    duplicates = gold.duplicated(
        ["district_id", "disease", "week_start"],
        keep=False,
    )

    if duplicates.any():
        raise ValueError(
            "Duplicate district-disease-week records found."
        )

    return gold


# ============================================================
# ROLLING Z-SCORE DETECTION
# ============================================================

def detect_alerts(group, district, district_name,
                  disease, all_weeks):

    group = (
        group
        .set_index("week_start")
        .sort_index()
    )

    missing_weeks = all_weeks.difference(group.index)

    if (
        len(missing_weeks) > 0
        and not MISSING_WEEK_MEANS_ZERO
    ):
        raise ValueError(
            f"Missing weeks for {district}/{disease}"
        )

    group = group.reindex(all_weeks)

    group.index.name = "week_start"

    group["district_id"] = district
    group["district_name"] = district_name
    group["disease"] = disease

    group["cases"] = (
        group["cases"]
        .fillna(0)
        .astype("int64")
    )

    # Use only previous weeks, excluding current week.
    historical = group["cases"].shift(1)

    group["baseline_mean"] = (
        historical
        .rolling(
            BASELINE_WEEKS,
            min_periods=MIN_BASELINE_WEEKS,
        )
        .mean()
    )

    group["baseline_std"] = (
        historical
        .rolling(
            BASELINE_WEEKS,
            min_periods=MIN_BASELINE_WEEKS,
        )
        .std(ddof=1)
    )

    # Avoid zero standard deviation.
    group["effective_std"] = (
        group["baseline_std"].fillna(0).clip(lower=1.0)
    )

    group["alert_threshold"] = (
        group["baseline_mean"]
        + np.maximum(
            MIN_ABSOLUTE_INCREASE,
            Z_THRESHOLD * group["effective_std"],
        )
    )

    group["z_score"] = (
        group["cases"] - group["baseline_mean"]
    ) / group["effective_std"]

    valid_baseline = group["baseline_mean"].notna()

    group["alert"] = (
        valid_baseline
        & (
            group["cases"]
            >= group["alert_threshold"]
        )
        & (
            group["cases"]
            >= MIN_CASES_FOR_ALERT
        )
    )

    group["detector"] = "Rolling_Z_Score"

    return group.reset_index()


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("EPISCOPE - ROLLING Z-SCORE OUTBREAK DETECTION")
    print("=" * 60)

    gold = load_gold()

    print("Gold records:", len(gold))
    print("Districts:", gold["district_id"].nunique())
    print("Diseases:", gold["disease"].nunique())

    all_weeks = pd.date_range(
        gold["week_start"].min(),
        gold["week_start"].max(),
        freq="W-MON",
    )

    results = []

    for (district, disease), group in gold.groupby(
        ["district_id", "disease"],
        sort=True,
    ):

        district_name = group["district_name"].iloc[0]

        if group["district_name"].nunique() != 1:
            raise ValueError(
                f"Inconsistent district names: {district}"
            )

        result = detect_alerts(
            group,
            district,
            district_name,
            disease,
            all_weeks,
        )

        results.append(result)

    alerts = pd.concat(
        results,
        ignore_index=True,
    )

    alerts["year"] = alerts["week_start"].dt.year

    # Arrange output columns.
    output_columns = [
        "week_start",
        "district_id",
        "district_name",
        "disease",
        "cases",
        "admissions",
        "lab_confirmed_cases",
        "bronze_batch_ids",
        "aggregation_time_utc",
        "year",
        "baseline_mean",
        "baseline_std",
        "alert_threshold",
        "z_score",
        "alert",
        "detector",
    ]

    # Retain columns that exist in the source or detector.
    output_columns = [
        column for column in output_columns
        if column in alerts.columns
    ]

    alerts = alerts[output_columns]

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    alerts.to_csv(
        OUTPUT,
        index=False,
    )

    total_alerts = int(alerts["alert"].sum())

    print("\nDETECTION SUMMARY")
    print("-" * 60)
    print("Detector: Rolling Z-score")
    print("Baseline weeks:", BASELINE_WEEKS)
    print("Minimum baseline weeks:", MIN_BASELINE_WEEKS)
    print("Z threshold:", Z_THRESHOLD)
    print("Total weekly records:", len(alerts))
    print("Total alerts:", total_alerts)

    print(
        "Alert percentage:",
        round(total_alerts / len(alerts) * 100, 2),
        "%",
    )

    print("\nOutput saved:", OUTPUT)

    print("\nFIRST 10 ALERTS")
    print("-" * 60)

    print(
        alerts.loc[
            alerts["alert"]
        ].head(10).to_string(index=False)
    )

    print("\nRolling Z-score detection completed successfully.")


if __name__ == "__main__":
    main()
