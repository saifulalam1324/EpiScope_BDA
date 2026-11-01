
from pathlib import Path
import logging
import os
import tempfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "analytics/forecast/forecasts.csv"
OUTPUT = ROOT / "analytics/forecast/resource_demand.csv"
DISTRICT_OUTPUT = (
    ROOT / "analytics/forecast/resource_demand_by_district.csv"
)

# Illustrative planning assumptions only.
# These values are not clinical recommendations.
ASSUMPTIONS = {
    "bed_fraction": 0.15,
    "saline_units_per_case": 2.0,
    "ors_units_per_case": 5.0,
}

SCENARIO = "illustrative_planning_assumptions"

REQUIRED_COLUMNS = {
    "district_id",
    "disease",
    "week_start",
    "predicted_cases",
}

logger = logging.getLogger("episcope.resource_demand")


def save_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write a CSV using a temporary file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="resource_demand_",
            dir=path.parent,
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            frame.to_csv(temporary_file, index=False)

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    logger.info("Saved: %s", path)


def validate_assumptions() -> None:
    """Validate the illustrative planning assumptions."""
    for name, value in ASSUMPTIONS.items():
        if not np.isfinite(value) or value < 0:
            raise ValueError(
                f"Invalid planning assumption {name}: {value}"
            )

    if ASSUMPTIONS["bed_fraction"] > 1:
        raise ValueError("bed_fraction cannot exceed 1.")



def load_forecasts() -> pd.DataFrame:
    """Load and validate records using the actual forecast schema."""
    if not INPUT.exists():
        raise FileNotFoundError(
            f"Forecast input not found: {INPUT}"
        )

    forecast = pd.read_csv(INPUT)

    if forecast.empty:
        raise ValueError("Forecast dataset is empty.")

    missing = REQUIRED_COLUMNS - set(forecast.columns)
    if missing:
        raise ValueError(
            f"Forecast data is missing columns: {sorted(missing)}"
        )

    forecast = forecast.copy()

    # Map the actual forecast schema to the resource-demand schema.
    forecast = forecast.rename(
        columns={
            "week_start": "forecast_week",
            "predicted_cases": "forecast_cases",
        }
    )

    for column in ["district_id", "disease"]:
        forecast[column] = (
            forecast[column].astype("string").str.strip()
        )

        if forecast[column].isna().any() or (
            forecast[column] == ""
        ).any():
            raise ValueError(
                f"Forecast contains missing {column} values."
            )

    if "district_name" in forecast.columns:
        forecast["district_name"] = (
            forecast["district_name"].astype("string").str.strip()
        )

    forecast["forecast_cases"] = pd.to_numeric(
        forecast["forecast_cases"],
        errors="coerce",
    )

    if forecast["forecast_cases"].isna().any():
        raise ValueError("Forecast contains invalid case counts.")

    if not np.isfinite(forecast["forecast_cases"]).all():
        raise ValueError("Forecast contains infinite case counts.")

    if (forecast["forecast_cases"] < 0).any():
        raise ValueError("Forecast cases cannot be negative.")

    forecast["forecast_week"] = pd.to_datetime(
        forecast["forecast_week"],
        errors="coerce",
    )

    if forecast["forecast_week"].isna().any():
        raise ValueError("Forecast contains invalid forecast dates.")

    grouping_columns = [
        "district_id",
        "disease",
        "forecast_week",
    ]

    if "district_name" in forecast.columns:
        name_counts = (
            forecast.groupby("district_id")["district_name"]
            .nunique(dropna=True)
        )

        if (name_counts > 1).any():
            raise ValueError(
                "A district_id has multiple district_name values."
            )

        grouping_columns.insert(1, "district_name")

    forecast = (
        forecast.groupby(
            grouping_columns,
            as_index=False,
            dropna=False,
        )["forecast_cases"]
        .sum()
    )

    if forecast.empty:
        raise ValueError(
            "No forecast records remain after validation."
        )

    return forecast


def calculate_demand(
    forecast: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate disease-level and district-level resource estimates."""

    # Disease-level estimates.
    demand = forecast.copy()

    demand["beds_needed"] = np.ceil(
        demand["forecast_cases"] * ASSUMPTIONS["bed_fraction"]
    ).astype("int64")

    demand["saline_needed"] = (
        demand["forecast_cases"]
        * ASSUMPTIONS["saline_units_per_case"]
    )

    demand["ors_needed"] = (
        demand["forecast_cases"]
        * ASSUMPTIONS["ors_units_per_case"]
    )

    demand["scenario"] = SCENARIO

    # District-level case totals across diseases.
    district_columns = ["district_id", "forecast_week"]

    if "district_name" in demand.columns:
        district_columns.insert(1, "district_name")

    district_demand = (
        demand.groupby(
            district_columns,
            as_index=False,
        )
        .agg(
            forecast_cases=("forecast_cases", "sum"),
            saline_needed=("saline_needed", "sum"),
            ors_needed=("ors_needed", "sum"),
        )
    )

    # Round beds after summing cases across diseases.
    district_demand["beds_needed"] = np.ceil(
        district_demand["forecast_cases"]
        * ASSUMPTIONS["bed_fraction"]
    ).astype("int64")

    district_demand["scenario"] = SCENARIO

    return demand, district_demand


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("Starting resource-demand estimation...")

    validate_assumptions()

    forecast = load_forecasts()

    logger.info("Validated forecast records: %s", len(forecast))
    logger.info(
        "Districts: %s",
        forecast["district_id"].nunique(),
    )
    logger.info(
        "Diseases: %s",
        forecast["disease"].nunique(),
    )

    demand, district_demand = calculate_demand(forecast)

    # Save both reports.
    save_csv_atomic(demand, OUTPUT)
    save_csv_atomic(district_demand, DISTRICT_OUTPUT)

    print("\nRESOURCE-DEMAND SUMMARY")
    print("Scenario:", SCENARIO)
    print("Planning assumptions:", ASSUMPTIONS)

    print("Disease-level records:", len(demand))
    print("District-week records:", len(district_demand))

    print(
        "Forecast weeks:",
        demand["forecast_week"].nunique(),
    )

    print(
        "Total forecast cases:",
        round(float(demand["forecast_cases"].sum()), 2),
    )

    print(
        "Estimated disease-level beds:",
        int(demand["beds_needed"].sum()),
    )

    print(
        "Estimated district-level beds:",
        int(district_demand["beds_needed"].sum()),
    )

    print(
        "Estimated saline units:",
        round(float(district_demand["saline_needed"].sum()), 2),
    )

    print(
        "Estimated ORS units:",
        round(float(district_demand["ors_needed"].sum()), 2),
    )

    print("\nDISTRICT-LEVEL DEMAND")
    print(
        district_demand.head(10).to_string(index=False)
    )

    print("\nSaved:")
    print(OUTPUT)
    print(DISTRICT_OUTPUT)

    logger.info("Resource-demand estimation completed successfully.")


if __name__ == "__main__":
    main()
