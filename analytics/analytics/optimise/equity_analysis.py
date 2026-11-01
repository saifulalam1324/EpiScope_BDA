
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------
# PATHS AND CONFIGURATION
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]

INPUT_FILE = ROOT / "analytics" / "optimise" / "allocation_results.csv"
OUTPUT_DIR = ROOT / "analytics" / "optimise"

DISTRICT_OUTPUT = OUTPUT_DIR / "equity_by_district.csv"
WEEKLY_OUTPUT = OUTPUT_DIR / "equity_by_week.csv"
LORENZ_OUTPUT = OUTPUT_DIR / "lorenz_curve.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "equity_summary.csv"

EXPECTED_DISTRICTS = 64

REQUIRED_COLUMNS = {
    "district_id",
    "forecast_week",
    "beds_needed",
    "allocated_beds",
    "unmet_beds",
    "coverage_rate",
}


# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.equity")


# ---------------------------------------------------------
# CSV UTILITIES
# ---------------------------------------------------------

def save_csv(frame: pd.DataFrame, destination: Path) -> None:
    """Save a CSV atomically."""

    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        suffix=".csv",
        prefix=destination.stem + "_",
        dir=destination.parent,
    )
    os.close(fd)

    temporary_path = Path(temporary_name)

    try:
        frame.to_csv(temporary_path, index=False)
        temporary_path.replace(destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


# ---------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------

def load_allocations() -> pd.DataFrame:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Allocation file not found: {INPUT_FILE}"
        )

    df = pd.read_csv(INPUT_FILE)

    missing = REQUIRED_COLUMNS - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing allocation columns: {sorted(missing)}"
        )

    df["district_id"] = df["district_id"].astype("string").str.strip()

    df["forecast_week"] = pd.to_datetime(
        df["forecast_week"],
        errors="coerce",
    )

    numeric_columns = [
        "beds_needed",
        "allocated_beds",
        "unmet_beds",
        "coverage_rate",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    if df[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError(
            "Allocation data contains missing or invalid required values."
        )

    if not np.isfinite(
        df[numeric_columns].to_numpy(dtype=float)
    ).all():
        raise ValueError("Non-finite allocation values found.")

    if (df[["beds_needed", "allocated_beds", "unmet_beds"]] < 0).any().any():
        raise ValueError("Negative bed values found.")

    if (df["allocated_beds"] > df["beds_needed"] + 1e-8).any():
        raise ValueError("Allocated beds exceed requested beds.")

    if not np.allclose(
        df["beds_needed"],
        df["allocated_beds"] + df["unmet_beds"],
        atol=1e-6,
    ):
        raise ValueError(
            "Inconsistent values: demand must equal allocated plus unmet."
        )

    if (df["coverage_rate"] < 0).any() or (
        df["coverage_rate"] > 1 + 1e-8
    ).any():
        raise ValueError("Coverage rate must be between 0 and 1.")

    duplicate_columns = ["district_id", "forecast_week"]

    if df.duplicated(duplicate_columns).any():
        raise ValueError(
            "Duplicate district and forecast-week records found."
        )

    return df


# ---------------------------------------------------------
# GINI COEFFICIENT
# ---------------------------------------------------------

def calculate_gini(values) -> float:
    """
    Calculate the Gini coefficient.

    0 = perfect equality.
    1 = maximum inequality for non-negative values.

    Returns NaN when the total is zero.
    """

    values = np.asarray(values, dtype=float)

    if len(values) == 0:
        return float("nan")

    if not np.isfinite(values).all():
        raise ValueError("Gini input contains non-finite values.")

    if (values < 0).any():
        raise ValueError("Gini input cannot contain negative values.")

    total = values.sum()

    if total == 0:
        return float("nan")

    sorted_values = np.sort(values)
    n = len(sorted_values)

    ranks = np.arange(1, n + 1)

    return float(
        (2 * np.sum(ranks * sorted_values) / (n * total))
        - (n + 1) / n
    )


# ---------------------------------------------------------
# DISTRICT-LEVEL EQUITY
# ---------------------------------------------------------

def build_district_equity(df: pd.DataFrame) -> pd.DataFrame:

    aggregation = {
        "beds_needed": "sum",
        "allocated_beds": "sum",
        "unmet_beds": "sum",
        "forecast_week": "nunique",
    }

    if "district_name" in df.columns:
        aggregation["district_name"] = "first"

    result = (
        df.groupby("district_id")
        .agg(aggregation)
        .reset_index()
    )

    result = result.rename(
        columns={
            "forecast_week": "weeks_present",
        }
    )

    result["coverage_rate"] = np.divide(
        result["allocated_beds"],
        result["beds_needed"],
        out=np.zeros(len(result), dtype=float),
        where=result["beds_needed"].to_numpy() > 0,
    )

    result["demand_share"] = (
        result["beds_needed"]
        / max(result["beds_needed"].sum(), 1)
    )

    result["allocation_share"] = (
        result["allocated_beds"]
        / max(result["allocated_beds"].sum(), 1)
    )

    result["district_count_complete"] = (
        result["weeks_present"] == df["forecast_week"].nunique()
    )

    return result.sort_values("district_id").reset_index(drop=True)


# ---------------------------------------------------------
# WEEKLY EQUITY
# ---------------------------------------------------------

def build_weekly_equity(df: pd.DataFrame) -> pd.DataFrame:

    records = []

    for week, group in df.groupby("forecast_week"):

        demand = group["beds_needed"].sum()
        allocated = group["allocated_beds"].sum()
        unmet = group["unmet_beds"].sum()

        records.append({
            "forecast_week": week,
            "districts_present": group["district_id"].nunique(),
            "districts_expected": EXPECTED_DISTRICTS,
            "district_coverage_complete": (
                group["district_id"].nunique() == EXPECTED_DISTRICTS
            ),
            "total_demand": demand,
            "total_allocated": allocated,
            "total_unmet": unmet,
            "overall_coverage_rate": (
                allocated / demand if demand > 0 else np.nan
            ),
            "demand_gini": calculate_gini(
                group["beds_needed"].to_numpy()
            ),
            "allocation_gini": calculate_gini(
                group["allocated_beds"].to_numpy()
            ),
            "mean_district_coverage": group["coverage_rate"].mean(),
            "minimum_district_coverage": group["coverage_rate"].min(),
            "maximum_district_coverage": group["coverage_rate"].max(),
        })

    return pd.DataFrame(records).sort_values(
        "forecast_week"
    ).reset_index(drop=True)


# ---------------------------------------------------------
# LORENZ CURVE
# ---------------------------------------------------------

def build_lorenz_curve(
    district_df: pd.DataFrame,
) -> pd.DataFrame:

    records = []

    metrics = {
        "demand": "beds_needed",
        "allocation": "allocated_beds",
    }

    n = len(district_df)

    for metric_name, column in metrics.items():

        values = np.sort(
            district_df[column].to_numpy(dtype=float)
        )

        total = values.sum()

        if total > 0:
            cumulative_share = np.cumsum(values) / total
        else:
            cumulative_share = np.zeros(n)

        # Include the origin (0, 0).
        records.append({
            "metric": metric_name,
            "district_rank": 0,
            "cumulative_district_share": 0.0,
            "cumulative_value_share": 0.0,
        })

        for index, share in enumerate(cumulative_share, start=1):

            records.append({
                "metric": metric_name,
                "district_rank": index,
                "cumulative_district_share": index / n,
                "cumulative_value_share": float(share),
            })

    return pd.DataFrame(records)


# ---------------------------------------------------------
# OVERALL SUMMARY
# ---------------------------------------------------------

def build_summary(
    df: pd.DataFrame,
    district_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
) -> pd.DataFrame:

    total_demand = df["beds_needed"].sum()
    total_allocated = df["allocated_beds"].sum()
    total_unmet = df["unmet_beds"].sum()

    district_gini_demand = calculate_gini(
        district_df["beds_needed"]
    )

    district_gini_allocation = calculate_gini(
        district_df["allocated_beds"]
    )

    summary = [
        ("total_demand", total_demand),
        ("total_allocated", total_allocated),
        ("total_unmet", total_unmet),
        (
            "overall_coverage_rate",
            total_allocated / total_demand if total_demand > 0 else np.nan,
        ),
        ("district_demand_gini", district_gini_demand),
        ("district_allocation_gini", district_gini_allocation),
        (
            "gini_difference",
            district_gini_allocation - district_gini_demand,
        ),
        ("districts", df["district_id"].nunique()),
        ("forecast_weeks", df["forecast_week"].nunique()),
        (
            "weeks_with_all_64_districts",
            int(weekly_df["district_coverage_complete"].sum()),
        ),
        (
            "weeks_with_incomplete_district_coverage",
            int((~weekly_df["district_coverage_complete"]).sum()),
        ),
        (
            "districts_with_incomplete_week_coverage",
            int((~district_df["district_count_complete"]).sum()),
        ),
    ]

    return pd.DataFrame(
        summary,
        columns=["metric", "value"],
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    logger.info("Starting EpiScope equity analysis...")

    df = load_allocations()

    logger.info("Allocation records: %s", len(df))
    logger.info("Districts: %s", df["district_id"].nunique())
    logger.info("Forecast weeks: %s", df["forecast_week"].nunique())

    district_df = build_district_equity(df)

    weekly_df = build_weekly_equity(df)

    lorenz_df = build_lorenz_curve(district_df)

    summary_df = build_summary(
        df,
        district_df,
        weekly_df,
    )

    save_csv(district_df, DISTRICT_OUTPUT)
    save_csv(weekly_df, WEEKLY_OUTPUT)
    save_csv(lorenz_df, LORENZ_OUTPUT)
    save_csv(summary_df, SUMMARY_OUTPUT)

    print("\nEQUITY ANALYSIS SUMMARY")
    print("=======================")

    print(summary_df.to_string(index=False))

    print("\nWEEKLY EQUITY")
    print("=============")

    print(
        weekly_df[
            [
                "forecast_week",
                "districts_present",
                "total_demand",
                "total_allocated",
                "total_unmet",
                "demand_gini",
                "allocation_gini",
            ]
        ].to_string(index=False)
    )

    print("\nOUTPUT FILES")
    print("============")

    for path in [
        DISTRICT_OUTPUT,
        WEEKLY_OUTPUT,
        LORENZ_OUTPUT,
        SUMMARY_OUTPUT,
    ]:
        print(path)

    logger.info("Equity analysis completed successfully.")


if __name__ == "__main__":
    main()
