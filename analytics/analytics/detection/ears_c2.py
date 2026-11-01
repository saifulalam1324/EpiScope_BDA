
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "data/lake/gold/weekly_district_disease"
OUTPUT = ROOT / "analytics/detection/ears_c2_alerts.csv"


# ============================================================
# GOLD SCHEMA
# ============================================================

REQUIRED_COLUMNS = {
    "district_id",
    "district_name",
    "disease_group",
    "week_start",
    "case_count",
}


# ============================================================
# EARS C2 CONFIGURATION
# ============================================================

BASELINE_WEEKS = 7
GUARD_BAND_WEEKS = 2
MIN_BASELINE_WEEKS = 7

# Require at least three reported cases.
MIN_CASES_FOR_ALERT = 3

# C2 threshold multiplier.
C2_MULTIPLIER = 2.0

# Prevent zero or extremely small standard deviations
# from producing unrealistically low thresholds.
MIN_STD_FLOOR = 1.0

# Missing Gold weeks are treated as zero only if the
# reporting dataset is known to be complete.
MISSING_WEEK_MEANS_ZERO = True


# ============================================================
# LOAD AND VALIDATE GOLD DATA
# ============================================================

def load_gold():

    if not INPUT.exists():
        raise FileNotFoundError(
            f"Gold input not found: {INPUT}"
        )

    gold = pd.read_parquet(INPUT)

    missing = REQUIRED_COLUMNS - set(gold.columns)

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

        if gold[column].astype(str).str.strip().eq("").any():
            raise ValueError(
                f"{column} contains empty values."
            )

    gold["week_start"] = pd.to_datetime(
        gold["week_start"],
        errors="coerce",
    )

    if gold["week_start"].isna().any():
        raise ValueError(
            "Gold data contains invalid week_start values."
        )

    if (gold["week_start"].dt.dayofweek != 0).any():
        raise ValueError(
            "week_start must contain Monday dates."
        )

    gold["cases"] = pd.to_numeric(
        gold["cases"],
        errors="coerce",
    )

    if gold["cases"].isna().any():
        raise ValueError(
            "Missing or invalid case counts."
        )

    if (gold["cases"] < 0).any():
        raise ValueError(
            "Case counts cannot be negative."
        )

    if not np.allclose(
        gold["cases"],
        gold["cases"].round(),
    ):
        raise ValueError(
            "Case counts must be whole numbers."
        )

    gold["cases"] = gold["cases"].astype("int64")

    duplicate_mask = gold.duplicated(
        ["district_id", "disease", "week_start"],
        keep=False,
    )

    if duplicate_mask.any():

        duplicates = gold.loc[
            duplicate_mask,
            ["district_id", "disease", "week_start"],
        ]

        raise ValueError(
            "Duplicate district-disease-week records found:\n"
            f"{duplicates.head(10)}"
        )

    return gold


# ============================================================
# EARS C2 DETECTION
# ============================================================

def detect_c2(
    group,
    district,
    district_name,
    disease,
    all_weeks,
):

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
            f"Missing reporting weeks for "
            f"{district}/{disease}."
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

    # --------------------------------------------------------
    # GUARD BAND
    # Excludes the current week and previous week.
    # Baseline uses t-2 through t-8.
    # --------------------------------------------------------

    eligible_baseline = group["cases"].shift(
        GUARD_BAND_WEEKS
    )

    group["baseline_mean"] = (
        eligible_baseline
        .rolling(
            window=BASELINE_WEEKS,
            min_periods=MIN_BASELINE_WEEKS,
        )
        .mean()
    )

    group["baseline_std"] = (
        eligible_baseline
        .rolling(
            window=BASELINE_WEEKS,
            min_periods=MIN_BASELINE_WEEKS,
        )
        .std(ddof=1)
    )

    # --------------------------------------------------------
    # STANDARD DEVIATION FLOOR
    # --------------------------------------------------------

    group["effective_std"] = (
        group["baseline_std"]
        .clip(lower=MIN_STD_FLOOR)
    )

    # C2 threshold using effective standard deviation.
    group["alert_threshold"] = (
        group["baseline_mean"]
        + C2_MULTIPLIER * group["effective_std"]
    )

    # Standardized anomaly score.
    group["z_score"] = (
        group["cases"] - group["baseline_mean"]
    ) / group["effective_std"]

    # --------------------------------------------------------
    # ALERT DECISION
    # --------------------------------------------------------

    valid_baseline = (
        group["baseline_mean"].notna()
        & group["baseline_std"].notna()
    )

    group["alert"] = (
        valid_baseline
        & (group["z_score"] >= C2_MULTIPLIER)
        & (group["cases"] >= MIN_CASES_FOR_ALERT)
    )

    group["detector"] = "EARS_C2"

    return group.reset_index()


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("EPISCOPE - EARS C2 OUTBREAK DETECTION")
    print("=" * 60)

    gold = load_gold()

    print("Gold records loaded:", len(gold))
    print("Districts:", gold["district_id"].nunique())
    print("Diseases:", gold["disease"].nunique())

    all_weeks = pd.date_range(
        start=gold["week_start"].min(),
        end=gold["week_start"].max(),
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
                f"Inconsistent district names for {district}."
            )

        result = detect_c2(
            group=group,
            district=district,
            district_name=district_name,
            disease=disease,
            all_weeks=all_weeks,
        )

        results.append(result)

    if not results:
        raise ValueError(
            "No district-disease groups found."
        )

    alerts = pd.concat(
        results,
        ignore_index=True,
    )

    alerts["year"] = alerts["week_start"].dt.year

    # --------------------------------------------------------
    # SAVE OUTPUT
    # --------------------------------------------------------

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    alerts.to_csv(
        OUTPUT,
        index=False,
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    total_alerts = int(alerts["alert"].sum())

    print("\nDETECTION SUMMARY")
    print("-" * 60)

    print("Detector: EARS C2 weekly adaptation")
    print("Baseline weeks:", BASELINE_WEEKS)
    print("Guard-band weeks:", GUARD_BAND_WEEKS)
    print("C2 multiplier:", C2_MULTIPLIER)
    print("Minimum standard deviation:", MIN_STD_FLOOR)
    print("Minimum cases:", MIN_CASES_FOR_ALERT)
    print("Weekly records:", len(alerts))
    print("Total alerts:", total_alerts)

    print(
        "Alert percentage:",
        round(total_alerts / len(alerts) * 100, 2),
        "%",
    )

    print("\nOutput saved:", OUTPUT)

    # --------------------------------------------------------
    # SHOW ALERTS
    # --------------------------------------------------------

    print("\nFIRST 10 ALERTS")
    print("-" * 60)

    alert_columns = [
        "district_id",
        "district_name",
        "disease",
        "week_start",
        "cases",
        "baseline_mean",
        "baseline_std",
        "effective_std",
        "alert_threshold",
        "z_score",
    ]

    print(
        alerts.loc[
            alerts["alert"],
            alert_columns,
        ]
        .head(10)
        .to_string(index=False)
    )

    print("\nEARS C2 completed successfully.")


if __name__ == "__main__":
    main()
