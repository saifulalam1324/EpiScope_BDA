
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pandas as pd


# =========================================================
# CONFIGURATION
# =========================================================

ROOT = Path(__file__).resolve().parents[3]

GOLD_INPUT = ROOT / "data" / "lake" / "gold" / "weekly_district_disease"
MAPPING_PATH = ROOT / "data" / "reference" / "district_division_mapping.csv"

OUTPUT = ROOT / "analytics" / "calendar" / "preparedness_calendar.csv"

PREPARATION_LEAD_WEEKS = 4

EXPECTED_DIVISIONS = {
    "Barishal",
    "Chattogram",
    "Dhaka",
    "Khulna",
    "Mymensingh",
    "Rajshahi",
    "Rangpur",
    "Sylhet",
}

# A 53-week seasonal cycle accommodates ISO week 53.
SEASONAL_CYCLE_WEEKS = 53

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.preparedness")


# =========================================================
# ATOMIC CSV OUTPUT
# =========================================================

def save_csv(data: pd.DataFrame, destination: Path) -> None:

    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        suffix=".csv",
        dir=destination.parent,
    )
    os.close(fd)

    temp_path = Path(temp_name)

    try:
        data.to_csv(temp_path, index=False)
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# =========================================================
# LOAD GOLD DATA
# =========================================================

def load_gold() -> pd.DataFrame:

    if not GOLD_INPUT.exists():
        raise FileNotFoundError(
            f"Gold input not found: {GOLD_INPUT}"
        )

    frames = []

    for path in sorted(GOLD_INPUT.rglob("*.parquet")):

        frame = pd.read_parquet(path)

        # Support the actual EpiScope Gold schema.
        if "disease" not in frame.columns and "disease_group" in frame.columns:
            frame = frame.rename(
                columns={"disease_group": "disease"}
            )

        if "cases" not in frame.columns and "case_count" in frame.columns:
            frame = frame.rename(
                columns={"case_count": "cases"}
            )

        required = {
            "district_id",
            "disease",
            "week_start",
            "cases",
        }

        missing = required - set(frame.columns)

        if missing:
            raise ValueError(
                f"{path.name} missing columns: {sorted(missing)}"
            )

        frames.append(
            frame[
                ["district_id", "disease", "week_start", "cases"]
            ]
        )

    if not frames:
        raise ValueError("No Gold Parquet files found.")

    data = pd.concat(frames, ignore_index=True)

    data["district_id"] = (
        data["district_id"].astype("string").str.strip()
    )

    data["disease"] = (
        data["disease"].astype("string").str.strip()
    )

    data["week_start"] = pd.to_datetime(
        data["week_start"],
        errors="coerce",
    )

    data["cases"] = pd.to_numeric(
        data["cases"],
        errors="coerce",
    )

    if data[["district_id", "disease"]].isna().any().any():
        raise ValueError("Missing district or disease identifiers.")

    if data["week_start"].isna().any():
        raise ValueError("Invalid week_start values found.")

    if data["cases"].isna().any() or (data["cases"] < 0).any():
        raise ValueError("Invalid case counts found.")

    # Combine duplicate district-disease-week records if present.
    data = (
        data.groupby(
            ["district_id", "disease", "week_start"],
            as_index=False,
        )
        .agg(cases=("cases", "sum"))
    )

    return data


# =========================================================
# LOAD DISTRICT MAPPING
# =========================================================

def load_mapping() -> pd.DataFrame:

    if not MAPPING_PATH.exists():
        raise FileNotFoundError(
            f"District mapping not found: {MAPPING_PATH}"
        )

    mapping = pd.read_csv(MAPPING_PATH)

    required = {"district_id", "division_name"}

    missing = required - set(mapping.columns)

    if missing:
        raise ValueError(
            f"Mapping missing columns: {sorted(missing)}"
        )

    mapping = mapping[
        ["district_id", "division_name"]
    ].copy()

    mapping["district_id"] = (
        mapping["district_id"].astype("string").str.strip()
    )

    mapping["division_name"] = (
        mapping["division_name"].astype("string").str.strip()
    )

    if mapping.isna().any().any():
        raise ValueError("District mapping contains missing values.")

    if mapping["district_id"].duplicated().any():
        raise ValueError("Duplicate district IDs in mapping.")

    actual_divisions = set(mapping["division_name"].unique())

    if actual_divisions != EXPECTED_DIVISIONS:
        raise ValueError(
            "Expected all eight Bangladesh divisions. "
            f"Found: {sorted(actual_divisions)}"
        )

    if mapping["district_id"].nunique() != 64:
        raise ValueError(
            f"Expected 64 districts, found {mapping['district_id'].nunique()}."
        )

    return mapping


# =========================================================
# BUILD PREPAREDNESS CALENDAR
# =========================================================

def build_calendar(
    gold: pd.DataFrame,
    mapping: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:

    gold_districts = set(gold["district_id"].astype(str))
    mapped_districts = set(mapping["district_id"].astype(str))

    unmatched_districts = sorted(
        gold_districts - mapped_districts
    )

    if unmatched_districts:
        raise ValueError(
            f"Unmapped Gold districts: {unmatched_districts}"
        )

    data = gold.merge(
        mapping,
        on="district_id",
        how="left",
        validate="many_to_one",
    )

    unmatched_rows = int(data["division_name"].isna().sum())

    if unmatched_rows:
        raise ValueError(
            f"{unmatched_rows} Gold records lack a division."
        )

    # ISO week numbering preserves week 53.
    iso = data["week_start"].dt.isocalendar()

    data["seasonal_week"] = iso.week.astype(int)

    # Historical average for each division, disease and seasonal week.
    calendar = (
        data.groupby(
            ["division_name", "disease", "seasonal_week"],
            as_index=False,
        )
        .agg(
            average_weekly_cases=("cases", "mean"),
            total_observed_cases=("cases", "sum"),
            observations=("cases", "count"),
        )
    )

    # Identify the highest average seasonal week.
    peak_indices = (
        calendar.groupby(
            ["division_name", "disease"]
        )["average_weekly_cases"].idxmax()
    )

    peaks = calendar.loc[
        peak_indices,
        [
            "division_name",
            "disease",
            "seasonal_week",
            "average_weekly_cases",
        ],
    ].rename(
        columns={
            "seasonal_week": "peak_week",
            "average_weekly_cases": "peak_average_cases",
        }
    )

    calendar = calendar.merge(
        peaks,
        on=["division_name", "disease"],
        how="left",
        validate="many_to_one",
    )

    # Four-week lead, wrapping across the 53-week seasonal cycle.
    calendar["preparation_start_week"] = (
        (
            calendar["peak_week"]
            - PREPARATION_LEAD_WEEKS
            - 1
        )
        % SEASONAL_CYCLE_WEEKS
    ) + 1

    calendar["preparation_lead_weeks"] = (
        PREPARATION_LEAD_WEEKS
    )

    calendar["recommended_action"] = (
        "Review supplies, staffing, surveillance readiness "
        "and community awareness before the seasonal peak."
    )

    calendar["calendar_method"] = (
        "Historical seasonal average; not a live outbreak forecast"
    )

    calendar = calendar.sort_values(
        ["division_name", "disease", "seasonal_week"]
    ).reset_index(drop=True)

    return calendar, unmatched_rows


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("Starting preparedness calendar...")

    gold = load_gold()
    mapping = load_mapping()

    logger.info("Gold records: %s", len(gold))
    logger.info("Gold districts: %s", gold["district_id"].nunique())
    logger.info("Mapped districts: %s", mapping["district_id"].nunique())
    logger.info("Divisions: %s", mapping["division_name"].nunique())

    calendar, unmatched = build_calendar(gold, mapping)

    calendar_divisions = set(calendar["division_name"].unique())

    missing_divisions = EXPECTED_DIVISIONS - calendar_divisions

    if missing_divisions:
        raise ValueError(
            f"Divisions missing from preparedness calendar: "
            f"{sorted(missing_divisions)}"
        )

    save_csv(calendar, OUTPUT)

    print("\nPREPAREDNESS CALENDAR SUMMARY")
    print("=============================")

    print("Gold records:", len(gold))
    print("Districts:", gold["district_id"].nunique())
    print("Divisions:", calendar["division_name"].nunique())
    print("Disease groups:", calendar["disease"].nunique())
    print("Calendar rows:", len(calendar))
    print("Unmatched Gold rows:", unmatched)

    print("\nDIVISION COVERAGE")
    print(
        calendar.groupby("division_name")["disease"]
        .nunique()
        .to_string()
    )

    print("\nPEAK SEASONAL PERIODS")

    peaks = (
        calendar[
            [
                "division_name",
                "disease",
                "peak_week",
                "peak_average_cases",
                "preparation_start_week",
            ]
        ]
        .drop_duplicates()
        .sort_values(["division_name", "disease"])
    )

    print(peaks.to_string(index=False))

    print("\nSaved:", OUTPUT)

    logger.info("Preparedness calendar completed successfully.")


if __name__ == "__main__":
    main()
