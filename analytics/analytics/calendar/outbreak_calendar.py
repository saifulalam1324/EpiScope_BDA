
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]

INPUT_FILES = [
    ROOT / "analytics" / "detection" / "ears_alerts.csv",
    ROOT / "analytics" / "detection" / "ears_c2_alerts.csv",
]

OUTPUT_DIR = ROOT / "analytics" / "calendar"

CALENDAR_OUTPUT = OUTPUT_DIR / "outbreak_calendar.csv"
WEEKLY_OUTPUT = OUTPUT_DIR / "outbreak_weekly_summary.csv"
MONTHLY_OUTPUT = OUTPUT_DIR / "outbreak_monthly_summary.csv"
DISTRICT_OUTPUT = OUTPUT_DIR / "outbreak_by_district.csv"
DISEASE_OUTPUT = OUTPUT_DIR / "outbreak_by_disease.csv"

REQUIRED_COLUMNS = {
    "week_start",
    "district_id",
    "district_name",
    "disease",
    "cases",
    "z_score",
    "alert",
    "detector",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.calendar")


# ---------------------------------------------------------
# CSV UTILITIES
# ---------------------------------------------------------

def save_csv(df: pd.DataFrame, destination: Path) -> None:

    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        suffix=".csv",
        dir=destination.parent,
    )
    os.close(fd)

    temp_path = Path(temp_name)

    try:
        df.to_csv(temp_path, index=False)
        temp_path.replace(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# ---------------------------------------------------------
# LOAD AND VALIDATE
# ---------------------------------------------------------

def load_alert_file(path: Path) -> pd.DataFrame:

    if not path.exists():
        raise FileNotFoundError(f"Alert file not found: {path}")

    df = pd.read_csv(path)

    missing = REQUIRED_COLUMNS - set(df.columns)

    if missing:
        raise ValueError(
            f"{path.name} is missing columns: {sorted(missing)}"
        )

    df = df.copy()

    df["week_start"] = pd.to_datetime(
        df["week_start"],
        errors="coerce",
    )

    for column in [
        "district_id",
        "district_name",
        "disease",
        "detector",
    ]:
        df[column] = df[column].astype("string").str.strip()

    df["cases"] = pd.to_numeric(
        df["cases"],
        errors="coerce",
    )

    df["z_score"] = pd.to_numeric(
        df["z_score"],
        errors="coerce",
    )

    if df["week_start"].isna().any():
        raise ValueError(f"Invalid dates in {path.name}")

    if df[["district_id", "disease", "detector"]].isna().any().any():
        raise ValueError(f"Missing identifiers in {path.name}")

    if df["cases"].isna().any() or (df["cases"] < 0).any():
        raise ValueError(f"Invalid case counts in {path.name}")

    if "alert" in df.columns:
        df["alert"] = (
            df["alert"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map({
                "true": True,
                "1": True,
                "yes": True,
                "false": False,
                "0": False,
                "no": False,
            })
        )

    if df["alert"].isna().any():
        raise ValueError(f"Invalid alert flags in {path.name}")

    return df


def load_all_alerts() -> pd.DataFrame:

    frames = []

    for path in INPUT_FILES:
        logger.info("Loading %s", path.name)

        frame = load_alert_file(path)

        logger.info(
            "%s: %s rows, %s alerts",
            path.name,
            len(frame),
            int(frame["alert"].sum()),
        )

        frames.append(frame)

    return pd.concat(
        frames,
        ignore_index=True,
        sort=False,
    )


# ---------------------------------------------------------
# BUILD OUTBREAK CALENDAR
# ---------------------------------------------------------

def build_calendar(alerts: pd.DataFrame) -> pd.DataFrame:

    active = alerts.loc[alerts["alert"]].copy()

    if active.empty:
        return pd.DataFrame(columns=[
            "week_start",
            "week_end",
            "district_id",
            "district_name",
            "disease",
            "cases",
            "detector_count",
            "detectors",
            "maximum_z_score",
            "severity",
            "calendar_month",
            "calendar_year",
        ])

    active["z_score"] = pd.to_numeric(
        active["z_score"],
        errors="coerce",
    )

    # Combine duplicate district-disease-week alerts.
    keys = [
        "week_start",
        "district_id",
        "disease",
    ]

    calendar = (
        active.groupby(keys, dropna=False)
        .agg(
            district_name=("district_name", "first"),
            cases=("cases", "max"),
            detector_count=("detector", "nunique"),
            detectors=(
                "detector",
                lambda values: "|".join(
                    sorted(set(values.dropna().astype(str)))
                ),
            ),
            maximum_z_score=("z_score", "max"),
        )
        .reset_index()
    )

    calendar["week_end"] = (
        calendar["week_start"] + pd.Timedelta(days=6)
    )

    # Descriptive severity categories based on maximum Z-score.
    # These are analytical labels, not official clinical severity levels.
    calendar["severity"] = np.select(
        [
            calendar["maximum_z_score"] >= 3,
            calendar["maximum_z_score"] >= 2,
        ],
        [
            "High",
            "Moderate",
        ],
        default="Elevated",
    )

    calendar["calendar_month"] = (
        calendar["week_start"].dt.to_period("M").astype(str)
    )

    calendar["calendar_year"] = calendar["week_start"].dt.year

    calendar = calendar.sort_values(
        ["week_start", "district_id", "disease"]
    ).reset_index(drop=True)

    return calendar


# ---------------------------------------------------------
# WEEKLY SUMMARY
# ---------------------------------------------------------

def build_weekly_summary(calendar: pd.DataFrame) -> pd.DataFrame:

    if calendar.empty:
        return pd.DataFrame()

    return (
        calendar.groupby("week_start")
        .agg(
            alert_events=("disease", "size"),
            affected_districts=("district_id", "nunique"),
            affected_diseases=("disease", "nunique"),
            total_reported_cases=("cases", "sum"),
            high_alerts=("severity", lambda x: (x == "High").sum()),
            moderate_alerts=(
                "severity",
                lambda x: (x == "Moderate").sum(),
            ),
            elevated_alerts=(
                "severity",
                lambda x: (x == "Elevated").sum(),
            ),
        )
        .reset_index()
        .sort_values("week_start")
    )


# ---------------------------------------------------------
# MONTHLY SUMMARY
# ---------------------------------------------------------

def build_monthly_summary(calendar: pd.DataFrame) -> pd.DataFrame:

    if calendar.empty:
        return pd.DataFrame()

    return (
        calendar.groupby("calendar_month")
        .agg(
            alert_events=("disease", "size"),
            affected_districts=("district_id", "nunique"),
            affected_diseases=("disease", "nunique"),
            total_reported_cases=("cases", "sum"),
            high_alerts=("severity", lambda x: (x == "High").sum()),
            moderate_alerts=(
                "severity",
                lambda x: (x == "Moderate").sum(),
            ),
        )
        .reset_index()
        .sort_values("calendar_month")
    )


# ---------------------------------------------------------
# DISTRICT SUMMARY
# ---------------------------------------------------------

def build_district_summary(calendar: pd.DataFrame) -> pd.DataFrame:

    if calendar.empty:
        return pd.DataFrame()

    return (
        calendar.groupby(
            ["district_id", "district_name"]
        )
        .agg(
            alert_events=("disease", "size"),
            affected_diseases=("disease", "nunique"),
            total_reported_cases=("cases", "sum"),
            high_alerts=("severity", lambda x: (x == "High").sum()),
            first_alert=("week_start", "min"),
            last_alert=("week_start", "max"),
        )
        .reset_index()
        .sort_values(
            ["alert_events", "total_reported_cases"],
            ascending=False,
        )
    )


# ---------------------------------------------------------
# DISEASE SUMMARY
# ---------------------------------------------------------

def build_disease_summary(calendar: pd.DataFrame) -> pd.DataFrame:

    if calendar.empty:
        return pd.DataFrame()

    return (
        calendar.groupby("disease")
        .agg(
            alert_events=("district_id", "size"),
            affected_districts=("district_id", "nunique"),
            total_reported_cases=("cases", "sum"),
            high_alerts=("severity", lambda x: (x == "High").sum()),
            first_alert=("week_start", "min"),
            last_alert=("week_start", "max"),
        )
        .reset_index()
        .sort_values(
            ["alert_events", "total_reported_cases"],
            ascending=False,
        )
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    logger.info("Starting EpiScope outbreak calendar...")

    alerts = load_all_alerts()

    logger.info("Combined detector records: %s", len(alerts))
    logger.info("Combined positive alerts: %s", int(alerts["alert"].sum()))

    calendar = build_calendar(alerts)

    weekly = build_weekly_summary(calendar)
    monthly = build_monthly_summary(calendar)
    district = build_district_summary(calendar)
    disease = build_disease_summary(calendar)

    save_csv(calendar, CALENDAR_OUTPUT)
    save_csv(weekly, WEEKLY_OUTPUT)
    save_csv(monthly, MONTHLY_OUTPUT)
    save_csv(district, DISTRICT_OUTPUT)
    save_csv(disease, DISEASE_OUTPUT)

    print("\nOUTBREAK CALENDAR SUMMARY")
    print("=========================")

    print("Combined detector records:", len(alerts))
    print("Positive detector alerts:", int(alerts["alert"].sum()))
    print("Unique calendar events:", len(calendar))

    if not calendar.empty:
        print(
            "Affected districts:",
            calendar["district_id"].nunique(),
        )
        print(
            "Affected diseases:",
            calendar["disease"].nunique(),
        )

        print("\nSEVERITY SUMMARY")
        print(
            calendar["severity"].value_counts().to_string()
        )

        print("\nTOP DISTRICTS")
        print(district.head(10).to_string(index=False))

        print("\nDISEASE SUMMARY")
        print(disease.to_string(index=False))

    print("\nOUTPUT FILES")

    for path in [
        CALENDAR_OUTPUT,
        WEEKLY_OUTPUT,
        MONTHLY_OUTPUT,
        DISTRICT_OUTPUT,
        DISEASE_OUTPUT,
    ]:
        print(path)

    logger.info("Outbreak calendar completed successfully.")


if __name__ == "__main__":
    main()
