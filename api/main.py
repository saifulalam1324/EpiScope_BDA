from pathlib import Path
from typing import Optional
import json
import math

import pulp

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from serving.data_access import (
    ServingDataError,
    read_csv_or_empty,
    read_json_or_empty,
    read_geojson,
    district_name_map as load_district_name_map,
    read_gold as load_gold,
)

# ============================================================
# PATHS AND CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DASHBOARD_DIR = ROOT / "dashboard"
REFERENCE_DIR = ROOT / "data" / "reference"
REPORT_DIR = ROOT / "data" / "reports"

GOLD_DIR = ROOT / "data" / "lake" / "gold" / "weekly_district_disease"

GEOJSON_FILE = REFERENCE_DIR / "districts.geojson"
DISTRICT_MAPPING_FILE = REFERENCE_DIR / "district_mapping.csv"

ALERTS_FILE = ROOT / "analytics" / "detection" / "ears_alerts.csv"
C2_ALERTS_FILE = ROOT / "analytics" / "detection" / "ears_c2_alerts.csv"

EVALUATION_FILE = ROOT / "analytics" / "detection" / "evaluation_results.csv"
EVALUATION_METRICS_FILE = ROOT / "analytics" / "detection" / "evaluation_metrics.csv"
THRESHOLD_FILE = ROOT / "analytics" / "detection" / "threshold_tradeoff.csv"

FORECAST_FILE = ROOT / "analytics" / "forecast" / "forecasts.csv"

DEMAND_FILE = ROOT / "analytics" / "forecast" / "resource_demand.csv"
DISTRICT_DEMAND_FILE = (
    ROOT / "analytics" / "forecast" / "resource_demand_by_district.csv"
)

ALLOCATION_FILE = ROOT / "analytics" / "optimise" / "allocation_results.csv"
SCENARIO_FILE = ROOT / "analytics" / "optimise" / "scenario_summary.csv"
SCENARIO_DETAILS_FILE = (
    ROOT / "analytics" / "optimise" / "scenario_allocation_results.csv"
)

CALENDAR_FILE = ROOT / "analytics" / "calendar" / "preparedness_calendar.csv"

SILVER_QUALITY_FILE = REPORT_DIR / "silver_quality.json"
BRONZE_INGESTION_FILE = REPORT_DIR / "bronze_ingestion.json"

SUPPRESSION_THRESHOLD = 5


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="EpiScope API",
    description=(
        "API for synthetic epidemiology surveillance, "
        "outbreak detection, forecasting and resource planning."
    ),
    version="4.0.0",
)

if not DASHBOARD_DIR.exists():
    raise RuntimeError(f"Dashboard directory not found: {DASHBOARD_DIR}")

app.mount(
    "/static",
    StaticFiles(directory=str(DASHBOARD_DIR)),
    name="static",
)


# ============================================================
# COMMON HELPERS
# ============================================================

def records(df: pd.DataFrame) -> list:
    if df.empty:
        return []

    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def safe_number(value, default=0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def safe_int(value):
    return int(round(safe_number(value)))


def truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(
        ["true", "1", "yes", "y"]
    )


def suppress_counts(
    df: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """
    Primary suppression for counts 1-4.

    This is not complementary suppression and does not guarantee
    protection against differencing or reconstruction attacks.
    """
    result = df.copy()

    for column in columns:
        if column not in result.columns:
            continue

        values = pd.to_numeric(result[column], errors="coerce")

        mask = (
            values.notna()
            & (values >= 1)
            & (values < SUPPRESSION_THRESHOLD)
        )

        result[column] = values.astype(object)
        result.loc[mask, column] = "Suppressed"

    return result


def apply_filters(
    df: pd.DataFrame,
    district: str = "ALL",
    disease: str = "ALL",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_column: Optional[str] = None,
) -> pd.DataFrame:

    result = df.copy()

    if district != "ALL" and "district_id" in result.columns:
        result = result[
            result["district_id"].astype(str) == str(district)
        ]

    if disease != "ALL" and "disease" in result.columns:
        result = result[
            result["disease"].astype(str).str.casefold()
            == str(disease).casefold()
        ]

    if date_column and date_column in result.columns:

        dates = pd.to_datetime(
            result[date_column],
            errors="coerce",
        )

        if date_from:
            start = pd.to_datetime(date_from, errors="coerce")

            if pd.notna(start):
                result = result.loc[dates >= start].copy()

        if date_to:
            end = pd.to_datetime(date_to, errors="coerce")

            if pd.notna(end):
                dates = pd.to_datetime(
                    result[date_column],
                    errors="coerce",
                )
                result = result.loc[dates <= end].copy()

    return result.reset_index(drop=True)


def district_name_map() -> dict:
    try:
        return load_district_name_map()

    except ServingDataError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc

def normalize_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert actual forecast CSV columns to API-friendly names.
    """
    result = df.copy()

    rename = {}

    if "week_start" in result.columns and "forecast_week" not in result.columns:
        rename["week_start"] = "forecast_week"

    if "predicted_cases" in result.columns and "forecast_cases" not in result.columns:
        rename["predicted_cases"] = "forecast_cases"

    if rename:
        result = result.rename(columns=rename)

    return result


def normalize_demand(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize actual resource demand columns.

    Disease-level:
      beds_needed, saline_needed, ors_needed

    District-level:
      beds_needed, saline_needed, ors_needed
    """
    result = df.copy()

    if "week_start" in result.columns and "forecast_week" not in result.columns:
        result = result.rename(
            columns={"week_start": "forecast_week"}
        )

    return result


def validate_date_column(df: pd.DataFrame, column: str):
    if column in df.columns:
        df[column] = pd.to_datetime(
            df[column],
            errors="coerce",
        )


# ============================================================
# GOLD DATA
# ============================================================

def read_gold() -> pd.DataFrame:
    try:
        return load_gold()

    except ServingDataError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc

    # Actual Gold schema compatibility.
    if "disease" not in df.columns and "disease_group" in df.columns:
        df = df.rename(
            columns={"disease_group": "disease"}
        )

    if "cases" not in df.columns and "case_count" in df.columns:
        df = df.rename(
            columns={"case_count": "cases"}
        )

    required = {
        "district_id",
        "disease",
        "week_start",
        "cases",
    }

    missing = required - set(df.columns)

    if missing:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Gold dataset missing columns: {sorted(missing)}. "
                f"Available columns: {sorted(df.columns)}"
            ),
        )

    df["district_id"] = df["district_id"].astype(str)
    df["disease"] = df["disease"].astype(str)

    df["week_start"] = pd.to_datetime(
        df["week_start"],
        errors="coerce",
    )

    df["cases"] = pd.to_numeric(
        df["cases"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["week_start", "cases"]
    ).copy()

    return df


def gold_with_filters(
    district="ALL",
    disease="ALL",
    date_from=None,
    date_to=None,
):
    return apply_filters(
        read_gold(),
        district,
        disease,
        date_from,
        date_to,
        "week_start",
    )


def weekly_aggregate(df: pd.DataFrame) -> pd.DataFrame:

    if df.empty:
        return pd.DataFrame(
            columns=["week_start", "cases"]
        )

    result = (
        df.groupby("week_start", as_index=False)["cases"]
        .sum()
        .sort_values("week_start")
    )

    result["cases"] = result["cases"].round().astype(int)

    result["week_start"] = (
        result["week_start"].dt.strftime("%Y-%m-%d")
    )

    return suppress_counts(result, ["cases"])


# ============================================================
# DASHBOARD AND HEALTH
# ============================================================

@app.get("/")
def dashboard():

    index_file = DASHBOARD_DIR / "index.html"

    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Dashboard index.html not found.",
        )

    return FileResponse(index_file)


@app.get("/api/health")
def health():

    return {
        "status": "ok",
        "gold_exists": GOLD_DIR.exists(),
        "geojson_exists": GEOJSON_FILE.exists(),
        "district_mapping_exists": DISTRICT_MAPPING_FILE.exists(),
        "forecast_exists": FORECAST_FILE.exists(),
        "demand_exists": DEMAND_FILE.exists(),
        "allocation_exists": ALLOCATION_FILE.exists(),
        "calendar_exists": CALENDAR_FILE.exists(),
    }


# ============================================================
# FILTER OPTIONS
# ============================================================

@app.get("/api/districts")
def districts():

    df = read_gold()
    mapping = district_name_map()

    result = (
        df.groupby("district_id", as_index=False)["cases"]
        .sum()
    )

    result["district_name"] = (
        result["district_id"].map(mapping).fillna("Unmapped")
    )

    result = result.sort_values("district_name")

    result["cases"] = result["cases"].round().astype(int)

    result = suppress_counts(result, ["cases"])

    return records(result)


@app.get("/api/district-list")
def district_list():

    mapping = district_name_map()

    return [
        {
            "district_id": district_id,
            "district_name": name,
        }
        for district_id, name in sorted(mapping.items())
    ]


# ============================================================
# SUMMARY
# ============================================================

@app.get("/api/summary")
def summary(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    df = gold_with_filters(
        district,
        disease,
        date_from,
        date_to,
    )

    if df.empty:
        return {
            "total_cases": 0,
            "districts": 0,
            "diseases": 0,
            "alert_weeks": 0,
            "estimated_beds": 0,
            "date_min": None,
            "date_max": None,
            "disease_totals": [],
        }

    total = safe_int(df["cases"].sum())

    displayed_total = (
        "Suppressed"
        if 0 < total < SUPPRESSION_THRESHOLD
        else total
    )

    disease_totals = (
        df.groupby("disease", as_index=False)["cases"]
        .sum()
        .sort_values("cases", ascending=False)
    )

    disease_totals["cases"] = (
        disease_totals["cases"].round().astype(int)
    )

    disease_totals = suppress_counts(
        disease_totals,
        ["cases"],
    )

    alert_df = read_csv_or_empty(ALERTS_FILE)

    if not alert_df.empty:
        alert_df = apply_filters(
            alert_df,
            district,
            disease,
            date_from,
            date_to,
            "week_start",
        )

    alert_count = 0

    if not alert_df.empty and "alert" in alert_df.columns:
        alert_count = int(
            truthy(alert_df["alert"]).sum()
        )

    demand = normalize_demand(
        read_csv_or_empty(DISTRICT_DEMAND_FILE)
    )

    if demand.empty:
        demand = normalize_demand(
            read_csv_or_empty(DEMAND_FILE)
        )

    demand = apply_filters(
        demand,
        district,
        disease,
    )

    beds = 0

    if not demand.empty and "beds_needed" in demand.columns:
        beds = safe_int(
            pd.to_numeric(
                demand["beds_needed"],
                errors="coerce",
            ).fillna(0).sum()
        )

    return {
        "total_cases": displayed_total,
        "districts": int(df["district_id"].nunique()),
        "diseases": int(df["disease"].nunique()),
        "alert_weeks": alert_count,
        "estimated_beds": beds,
        "date_min": df["week_start"].min().date().isoformat(),
        "date_max": df["week_start"].max().date().isoformat(),
        "disease_totals": records(disease_totals),
    }


# ============================================================
# WEEKLY
# ============================================================

@app.get("/api/weekly")
def weekly(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    df = gold_with_filters(
        district,
        disease,
        date_from,
        date_to,
    )

    return records(
        weekly_aggregate(df)
    )


# ============================================================
# DISEASE COUNTS
# ============================================================

@app.get("/api/disease-counts")
def disease_counts(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    df = gold_with_filters(
        district,
        disease,
        date_from,
        date_to,
    )

    if df.empty:
        return []

    result = (
        df.groupby("disease", as_index=False)["cases"]
        .sum()
        .sort_values("cases", ascending=False)
    )

    result["cases"] = result["cases"].round().astype(int)

    result = suppress_counts(
        result,
        ["cases"],
    )

    return records(result)


# ============================================================
# MAP
# ============================================================

@app.get("/api/map")
def district_map(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    # --------------------------------------------------------
    # LOAD DISTRICT GEOJSON THROUGH SERVING LAYER
    # --------------------------------------------------------

    try:
        geo = read_geojson(GEOJSON_FILE)

    except ServingDataError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc

    # --------------------------------------------------------
    # DISTRICT NAME MAPPING
    # --------------------------------------------------------

    mapping = district_name_map()

    reverse_mapping = {
        str(name).strip().casefold(): str(district_id)
        for district_id, name in mapping.items()
    }

    # --------------------------------------------------------
    # APPLY API FILTERS TO GOLD DATA
    # --------------------------------------------------------

    df = gold_with_filters(
        district,
        disease,
        date_from,
        date_to,
    )

    totals = (
        df.groupby("district_id", as_index=False)["cases"]
        .sum()
    )

    totals_by_id = {
        str(row["district_id"]): safe_int(row["cases"])
        for row in records(totals)
    }

    # --------------------------------------------------------
    # ENRICH GEOJSON FEATURES
    # --------------------------------------------------------

    for feature in geo["features"]:

        if not isinstance(feature, dict):
            continue

        props = feature.setdefault("properties", {})

        name = str(
            props.get("shapeName")
            or props.get("district_name")
            or props.get("NAME_2")
            or ""
        ).strip()

        district_id = reverse_mapping.get(
            name.casefold()
        )

        props["district_id"] = district_id

        props["district_name"] = mapping.get(
            district_id,
            name or "Unknown",
        )

        count = (
            totals_by_id.get(district_id, 0)
            if district_id
            else 0
        )

        # ----------------------------------------------------
        # SMALL-CELL SUPPRESSION
        # ----------------------------------------------------

        if 0 < count < SUPPRESSION_THRESHOLD:

            props["total_cases"] = None
            props["suppressed"] = True

        else:

            props["total_cases"] = count
            props["suppressed"] = False

        # Population denominators are not currently supplied.
        props["incidence_per_100k"] = None

    # --------------------------------------------------------
    # RETURN ENRICHED GEOJSON
    # --------------------------------------------------------

    return geo

# ============================================================
# ALERTS
# ============================================================

@app.get("/api/alerts")
def alerts(
    limit: int = Query(100, ge=1, le=1000),
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    df = read_csv_or_empty(ALERTS_FILE)

    if df.empty:
        return []

    if "alert" in df.columns:
        df = df[truthy(df["alert"])]

    df = apply_filters(
        df,
        district,
        disease,
        date_from,
        date_to,
        "week_start",
    )

    protected = {
        "patient_id",
        "person_id",
        "national_id",
        "nid",
        "name",
        "phone",
        "address",
        "date_of_birth",
    }

    df = df.drop(
        columns=[
            col for col in df.columns
            if col.lower() in protected
        ],
        errors="ignore",
    )

    if "cases" in df.columns:
        df = suppress_counts(df, ["cases"])

    if "week_start" in df.columns:
        df = df.sort_values(
            "week_start",
            ascending=False,
        )

    return records(df.head(limit))


# ============================================================
# FORECASTS
# ============================================================

@app.get("/api/forecasts")
@app.get("/api/forecast")
def forecasts(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    df = normalize_forecast(
        read_csv_or_empty(FORECAST_FILE)
    )

    if df.empty:
        return []

    df = apply_filters(
        df,
        district,
        disease,
        date_from,
        date_to,
        "forecast_week",
    )

    if df.empty:
        return []

    if "forecast_cases" not in df.columns:
        return []

    # When all districts are selected, aggregate predictions
    # before applying small-count suppression.
    if district == "ALL":

        df["forecast_cases"] = pd.to_numeric(
            df["forecast_cases"],
            errors="coerce",
        )

        df = df.dropna(
            subset=["forecast_cases", "forecast_week"]
        )

        result = (
            df.groupby("forecast_week", as_index=False)
            ["forecast_cases"]
            .sum()
            .sort_values("forecast_week")
        )

        result["district_id"] = "ALL"
        result["district_name"] = "All districts"
        result["disease"] = (
            "All diseases" if disease == "ALL" else disease
        )

        result = suppress_counts(
            result,
            ["forecast_cases"],
        )

        return records(result)

    # A specific district remains subject to primary suppression.
    df = suppress_counts(
        df,
        ["forecast_cases"],
    )

    # The source forecast file does not provide prediction intervals.
    return records(df)
# ============================================================
# RESOURCES
# ============================================================

@app.get("/api/resources")
def resources(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    demand = normalize_demand(
        read_csv_or_empty(DISTRICT_DEMAND_FILE)
    )

    if demand.empty:
        demand = normalize_demand(
            read_csv_or_empty(DEMAND_FILE)
        )

    allocation = read_csv_or_empty(ALLOCATION_FILE)

    if demand.empty:
        return []

    demand = apply_filters(
        demand,
        district,
        disease,
        date_from,
        date_to,
        "forecast_week",
    )

    if (
        not allocation.empty
        and "district_id" in allocation.columns
        and "district_id" in demand.columns
    ):

        allocation_data = allocation.copy()

        if "forecast_week" in allocation_data.columns:
            allocation_data["forecast_week"] = (
                allocation_data["forecast_week"].astype(str)
            )

        keys = ["district_id"]

        if (
            "forecast_week" in demand.columns
            and "forecast_week" in allocation_data.columns
        ):
            demand["forecast_week"] = (
                demand["forecast_week"].astype(str)
            )
            keys.append("forecast_week")

        available_columns = keys + [
            col for col in [
                "allocated_beds",
                "unmet_beds",
                "coverage_rate",
            ]
            if col in allocation_data.columns
        ]

        allocation_data = allocation_data[
            available_columns
        ].drop_duplicates(keys)

        demand = demand.merge(
            allocation_data,
            on=keys,
            how="left",
        )

    demand = suppress_counts(
        demand,
        [
            "beds_needed",
            "saline_needed",
            "ors_needed",
            "allocated_beds",
            "unmet_beds",
        ],
    )

    return records(demand)


# ============================================================
# DATA QUALITY
# ============================================================

@app.get("/api/data-quality")
@app.get("/api/quality-report")
def quality_report():

    report = read_json_or_empty(
        SILVER_QUALITY_FILE
    )

    if not report:
        return {
            "available": False,
            "status": "Unavailable",
            "message": "Silver quality report not found.",
            "input_rows": None,
            "output_rows": None,
            "quarantined_rows": None,
            "pass_rate": None,
            "rules": [],
            "report": {},
        }

    input_rows = report.get("input_rows")
    output_rows = report.get("valid_rows")
    quarantined = report.get("quarantined_rows")

    rule_data = report.get("rules", {})
    rules = []

    if isinstance(rule_data, dict):

        for name, value in rule_data.items():

            if isinstance(value, dict):
                passed = value.get("passed")
                failed = value.get("failed")
                rate = value.get("pass_rate")
            else:
                passed = None
                failed = None
                rate = value

            rules.append({
                "rule": name,
                "passed": passed,
                "failed": failed,
                "pass_rate": rate,
            })

    elif isinstance(rule_data, list):

        for item in rule_data:

            if isinstance(item, dict):
                rules.append({
                    "rule": item.get("rule", item.get("name")),
                    "passed": item.get("passed"),
                    "failed": item.get("failed"),
                    "pass_rate": item.get("pass_rate"),
                })

    rates = [
        safe_number(r.get("pass_rate"), float("nan"))
        for r in rules
    ]

    rates = [
        value for value in rates
        if math.isfinite(value)
    ]

    overall_rate = (
        sum(rates) / len(rates)
        if rates else None
    )

    return {
        "available": True,
        "status": "Loaded",
        "input_rows": input_rows,
        "output_rows": output_rows,
        "quarantined_rows": quarantined,
        "pass_rate": overall_rate,
        "rules": rules,
        "processing_time_utc": report.get("processing_time_utc"),
        "batch_ids": report.get("batch_ids", []),
        "privacy": report.get("privacy", {}),
        "report": report,
    }


# ============================================================
# PRIVACY STATUS
# ============================================================

@app.get("/api/privacy-status")
def privacy_status():

    report = read_json_or_empty(
        SILVER_QUALITY_FILE
    )

    privacy = report.get("privacy", {})

    if not isinstance(privacy, dict):
        privacy = {}

    return {
        "available": bool(report),
        "aggregated_only": True,
        "suppression_applied_server_side": True,
        "suppression_threshold": SUPPRESSION_THRESHOLD,
        "patient_id_hashed_in_silver_report": (
            privacy.get("patient_id_hashed") is True
        ),
        "exact_patient_id_removed_in_silver_report": (
            privacy.get("exact_patient_id_removed") is True
        ),
        "age_banding_reported": (
            privacy.get("age_years_replaced_with_age_band") is True
        ),
        "suppressed_cells": None,
        "verification_scope": (
            "Reported pipeline metadata and API suppression behavior. "
            "Not an independent security audit. Role-based authorization, "
            "complementary suppression and differential privacy are "
            "not verified here."
        ),
    }


# ============================================================
# LINEAGE
# ============================================================

@app.get("/api/lineage")
def lineage():

    bronze = read_json_or_empty(
        BRONZE_INGESTION_FILE
    )

    gold = pd.DataFrame()
    gold_error = None

    try:
        gold = read_gold()

    except HTTPException as exc:
        gold_error = exc.detail

    datasets = []

    if bronze:

        datasets.append({
            "dataset": "Bronze encounters",
            "bronze_batch_ids": bronze.get(
                "batch_id",
                bronze.get("batch_ids", "Not supplied"),
            ),
            "loaded_at": bronze.get(
                "ingestion_time_utc",
                "Not supplied",
            ),
        })

    if not gold.empty:

        batch_ids = []

        if "bronze_batch_ids" in gold.columns:

            for value in gold["bronze_batch_ids"].dropna().astype(str):

                for batch_id in value.split(","):

                    batch_id = batch_id.strip()

                    if batch_id and batch_id not in batch_ids:
                        batch_ids.append(batch_id)

        loaded_at = None

        if "processing_time_utc" in gold.columns:

            values = gold["processing_time_utc"].dropna()

            if not values.empty:
                loaded_at = str(values.max())

        datasets.append({
            "dataset": "Gold weekly district disease",
            "bronze_batch_ids": ", ".join(batch_ids) or "Not supplied",
            "loaded_at": loaded_at or "Not supplied",
        })

    batch_count = len({
        batch
        for row in datasets
        for batch in str(row["bronze_batch_ids"]).split(",")
        if batch.strip() and batch.strip() != "Not supplied"
    })

    return {
        "available": bool(datasets),
        "batch_count": batch_count,
        "datasets": datasets,
        "gold_error": gold_error,
    }


# ============================================================
# SEASONAL HEATMAP
# ============================================================


@app.get("/api/seasonal-heatmap")
def seasonal_heatmap(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    df = gold_with_filters(
        district,
        disease,
        date_from,
        date_to,
    )

    if df.empty:
        return []

    # Extract ISO epidemiological year and week.
    iso = df["week_start"].dt.isocalendar()

    df["week_of_year"] = iso.week.astype(int)
    df["iso_year"] = iso.year.astype(int)

    # Combine district cases into national totals
    # for each disease, year, and epidemiological week.
    yearly_totals = (
        df.groupby(
            ["disease", "iso_year", "week_of_year"],
            as_index=False,
        )["cases"]
        .sum()
    )

    # Calculate the average yearly national total
    # for each disease and epidemiological week.
    result = (
        yearly_totals.groupby(
            ["disease", "week_of_year"],
            as_index=False,
        )["cases"]
        .mean()
    )

    result["cases"] = result["cases"].round(2)

    # Preserve privacy suppression.
    result = suppress_counts(
        result,
        ["cases"],
    )

    return records(result)


# ============================================================
# EARLY WARNING
# ============================================================

@app.get("/api/early-warning")
def early_warning(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(30, ge=1, le=500),
):

    df = read_csv_or_empty(ALERTS_FILE)

    if df.empty:
        return []

    if "alert" in df.columns:
        df = df[truthy(df["alert"])]

    df = apply_filters(
        df,
        district,
        disease,
        date_from,
        date_to,
        "week_start",
    )

    if df.empty:
        return []

    if "z_score" in df.columns:

        df["z_score"] = pd.to_numeric(
            df["z_score"],
            errors="coerce",
        )

        df = df.sort_values(
            "z_score",
            ascending=False,
        )

    output = pd.DataFrame({
        "district_id": df.get("district_id"),
        "disease": df.get("disease"),
        "week_start": df.get("week_start"),
        "observed": df.get("cases"),
        "expected": df.get("baseline_mean"),
        "signal": df.get("z_score"),
    })

    output = output.dropna(
        subset=["district_id", "disease"]
    )

    output.insert(
        0,
        "rank",
        range(1, len(output) + 1),
    )

    output = suppress_counts(
        output,
        ["observed", "expected"],
    )

    return records(
        output.head(limit)
    )


# ============================================================
# EVALUATION
# ============================================================

@app.get("/api/evaluation")
def evaluation(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
):

    df = read_csv_or_empty(
        EVALUATION_FILE
    )

    if df.empty:
        return {
            "available": False,
            "events": [],
            "event_count": 0,
            "detected_count": 0,
        }

    if district != "ALL" and "district_id" in df.columns:
        df = df[
            df["district_id"].astype(str) == district
        ]

    if disease != "ALL" and "disease" in df.columns:
        df = df[
            df["disease"].astype(str).str.casefold()
            == disease.casefold()
        ]

    detected_count = (
        int(truthy(df["detected"]).sum())
        if "detected" in df.columns else 0
    )

    return {
        "available": True,
        "events": records(df),
        "event_count": len(df),
        "detected_count": detected_count,
    }


# ============================================================
# DETECTOR COMPARISON
# ============================================================

@app.get("/api/detector-comparison")
def detector_comparison(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
):

    df = read_csv_or_empty(
        EVALUATION_METRICS_FILE
    )

    if df.empty:
        return []

    if district != "ALL" and "district_id" in df.columns:
        df = df[
            df["district_id"].astype(str) == district
        ]

    if disease != "ALL" and "disease" in df.columns:
        df = df[
            df["disease"].astype(str).str.casefold()
            == disease.casefold()
        ]

    return records(df)


# ============================================================
# THRESHOLD CURVE
# ============================================================

@app.get("/api/threshold-curve")
def threshold_curve(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
):

    df = read_csv_or_empty(
        THRESHOLD_FILE
    )

    if df.empty:
        return []

    if district != "ALL" and "district_id" in df.columns:
        df = df[
            df["district_id"].astype(str) == district
        ]

    if disease != "ALL" and "disease" in df.columns:
        df = df[
            df["disease"].astype(str).str.casefold()
            == disease.casefold()
        ]

    return records(df)


# ============================================================
# RESOURCE DEMAND
# ============================================================

@app.get("/api/resource-demand")
def resource_demand(
    district: str = Query("ALL"),
    disease: str = Query("ALL"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):

    # District-level demand is appropriate for district planning.
    df = normalize_demand(
        read_csv_or_empty(DISTRICT_DEMAND_FILE)
    )

    if df.empty:
        df = normalize_demand(
            read_csv_or_empty(DEMAND_FILE)
        )

    if df.empty:
        return {
            "available": False,
            "beds": None,
            "saline_units": None,
            "test_kits": None,
            "uncertainty": "Not supplied by source data",
            "assumptions": "No resource-demand file is available.",
            "districts": [],
        }

    df = apply_filters(
        df,
        district,
        disease,
        date_from,
        date_to,
        "forecast_week",
    )

    def total(column):

        if column not in df.columns:
            return None

        return safe_number(
            pd.to_numeric(
                df[column],
                errors="coerce",
            ).sum()
        )

    aggregate_columns = [
        col for col in [
            "beds_needed",
            "saline_needed",
            "ors_needed",
        ]
        if col in df.columns
    ]

    if "district_id" in df.columns and aggregate_columns:

        district_totals = (
            df.groupby(
                "district_id",
                as_index=False,
            )[aggregate_columns]
            .sum()
        )

    else:
        district_totals = pd.DataFrame()

    if not district_totals.empty:

        district_totals = district_totals.rename(
            columns={
                "saline_needed": "saline_units",
                "ors_needed": "test_kits",
            }
        )

        mapping = district_name_map()

        district_totals["district_name"] = (
            district_totals["district_id"]
            .map(mapping)
            .fillna("Unmapped")
        )

        district_totals = suppress_counts(
            district_totals,
            [
                "beds_needed",
                "saline_units",
                "test_kits",
            ],
        )

    assumptions = sorted(
        set(df["scenario"].dropna().astype(str))
    ) if "scenario" in df.columns else []

    return {
        "available": True,
        "beds": total("beds_needed"),
        "saline_units": total("saline_needed"),
        "test_kits": total("ors_needed"),
        "uncertainty": (
            "Prediction intervals are not supplied by the source file."
        ),
        "assumptions": (
            "; ".join(assumptions)
            if assumptions
            else "Assumptions not documented in source."
        ),
        "districts": records(district_totals),
    }


# ============================================================
# ALLOCATION COMPARISON
# ============================================================

@app.get("/api/allocation-comparison")
def allocation_comparison(
    district: str = Query("ALL"),
):

    df = read_csv_or_empty(
        ALLOCATION_FILE
    )

    if df.empty:
        return {
            "available": False,
            "baseline_available": False,
            "districts": [],
        }

    if district != "ALL" and "district_id" in df.columns:
        df = df[
            df["district_id"].astype(str) == district
        ]

    required = {
        "district_id",
        "allocated_beds",
        "beds_needed",
        "unmet_beds",
    }

    if not required.issubset(df.columns):
        return {
            "available": False,
            "baseline_available": False,
            "districts": [],
        }

    group_columns = ["district_id"]

    if "district_name" in df.columns:
        group_columns.append("district_name")

    result = (
        df.groupby(
            group_columns,
            as_index=False,
        )
        .agg(
            optimized=("allocated_beds", "sum"),
            beds_needed=("beds_needed", "sum"),
            unmet=("unmet_beds", "sum"),
        )
    )

    # No population-proportional baseline is available.
    result["baseline"] = None

    result = suppress_counts(
        result,
        ["optimized", "beds_needed", "unmet"],
    )

    return {
        "available": True,
        "baseline_available": False,
        "districts": records(result),
    }


# ============================================================
# ALLOCATION EFFICIENCY CURVE
# ============================================================

@app.get("/api/allocation-curve")
def allocation_curve():

    df = read_csv_or_empty(
        SCENARIO_FILE
    )

    if df.empty:
        return []

    required_columns = {
        "capacity",
        "total_demand",
        "allocated_beds",
        "minimum_district_coverage",
    }

    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        raise HTTPException(
            status_code=500,
            detail=(
                "Scenario summary is missing required columns: "
                f"{sorted(missing_columns)}"
            ),
        )

    for column in (
        "capacity",
        "total_demand",
        "allocated_beds",
        "minimum_district_coverage",
    ):
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "capacity",
            "total_demand",
            "allocated_beds",
            "minimum_district_coverage",
        ]
    )

    if df.empty:
        return []

    curve = (
        df.groupby("capacity", as_index=False)
        .agg(
            total_demand=("total_demand", "sum"),
            allocated_beds=("allocated_beds", "sum"),
            equity=("minimum_district_coverage", "mean"),
        )
    )

    curve["efficiency"] = (
        curve["allocated_beds"]
        / curve["total_demand"].replace(0, float("nan"))
    )

    curve = curve.dropna(
        subset=["efficiency", "equity"]
    )

    curve = curve.rename(
        columns={
            "capacity": "budget",
        }
    )

    return records(
        curve[
            [
                "budget",
                "efficiency",
                "equity",
            ]
        ].sort_values("budget")
    )


# ============================================================
# INTERACTIVE SCENARIO OPTIMIZATION
# ============================================================

@app.post("/api/scenario")
def run_scenario(payload: dict = Body(...)):
    """
    Run an in-memory weekly bed-allocation scenario.

    Equity is calculated only across districts represented in each
    forecast week. Missing district-week records are not imputed.
    """

    # --------------------------------------------------------
    # Validate request parameters
    # --------------------------------------------------------

    try:
        capacity_value = float(payload.get("weekly_capacity"))
        equity_floor = float(payload.get("equity_floor"))
        threshold = float(payload.get("z_score_threshold"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="weekly_capacity, equity_floor and z_score_threshold must be numeric.",
        )

    if not math.isfinite(capacity_value) or not capacity_value.is_integer():
        raise HTTPException(
            status_code=422,
            detail="weekly_capacity must be a whole number.",
        )

    weekly_capacity = int(capacity_value)

    if weekly_capacity < 1 or weekly_capacity > 10000:
        raise HTTPException(
            status_code=422,
            detail="weekly_capacity must be between 1 and 10000 beds.",
        )

    if not math.isfinite(equity_floor) or not 0 <= equity_floor <= 1:
        raise HTTPException(
            status_code=422,
            detail="equity_floor must be between 0 and 1.",
        )

    detector = str(
        payload.get("detector", "Rolling_Z_Score")
    ).strip()

    if detector not in {"Rolling_Z_Score", "EARS_C2"}:
        raise HTTPException(
            status_code=422,
            detail="Unsupported detector.",
        )

    # --------------------------------------------------------
    # Load and validate available threshold metrics
    # --------------------------------------------------------

    threshold_df = read_csv_or_empty(THRESHOLD_FILE)

    if threshold_df.empty:
        raise HTTPException(
            status_code=503,
            detail="Threshold performance data is unavailable.",
        )

    required_threshold_columns = {
        "detector",
        "z_score_threshold",
        "event_sensitivity",
        "alert_precision",
        "false_alert_weeks",
        "median_detection_delay_days",
        "mean_false_alerts_per_district_year",
    }

    if not required_threshold_columns.issubset(threshold_df.columns):
        raise HTTPException(
            status_code=500,
            detail="Threshold performance file has an invalid schema.",
        )

    threshold_df["z_score_threshold"] = pd.to_numeric(
        threshold_df["z_score_threshold"],
        errors="coerce",
    )

    matching_threshold = threshold_df[
        (threshold_df["detector"].astype(str) == detector)
        & (
            (threshold_df["z_score_threshold"] - threshold).abs()
            < 1e-9
        )
    ]

    if matching_threshold.empty:
        raise HTTPException(
            status_code=422,
            detail="The selected threshold is not available for this detector.",
        )

    threshold_metrics = records(
        matching_threshold.iloc[[0]]
    )[0]

    # --------------------------------------------------------
    # Load district demand
    # --------------------------------------------------------

    demand = normalize_demand(
        read_csv_or_empty(DISTRICT_DEMAND_FILE)
    )

    required_demand_columns = {
        "district_id",
        "forecast_week",
        "beds_needed",
    }

    if demand.empty or not required_demand_columns.issubset(demand.columns):
        raise HTTPException(
            status_code=503,
            detail="District-level bed demand is unavailable or incomplete.",
        )

    demand = demand.copy()

    demand["district_id"] = (
        demand["district_id"].astype(str).str.strip()
    )

    demand["forecast_week"] = pd.to_datetime(
        demand["forecast_week"],
        errors="coerce",
    )

    demand["beds_needed"] = pd.to_numeric(
        demand["beds_needed"],
        errors="coerce",
    )

    if (
        demand["district_id"].eq("").any()
        or demand["forecast_week"].isna().any()
        or demand["beds_needed"].isna().any()
        or not demand["beds_needed"].map(math.isfinite).all()
        or (demand["beds_needed"] < 0).any()
        or (
            demand["beds_needed"] % 1 != 0
        ).any()
    ):
        raise HTTPException(
            status_code=500,
            detail="District demand contains invalid identifiers, dates or bed counts.",
        )

    if demand.duplicated(
        subset=["district_id", "forecast_week"]
    ).any():
        raise HTTPException(
            status_code=500,
            detail="Duplicate district-week demand records were found.",
        )

    demand["beds_needed"] = demand["beds_needed"].astype(int)

    # --------------------------------------------------------
    # Optimize each forecast week
    # --------------------------------------------------------

    weekly_results = []
    total_demand = 0
    total_allocated = 0
    minimum_coverages = []

    for forecast_week, group in demand.groupby(
        "forecast_week",
        sort=True,
    ):
        group = group.sort_values("district_id").reset_index(drop=True)

        districts = group["district_id"].tolist()
        needs = group["beds_needed"].tolist()

        problem = pulp.LpProblem(
            "EpiScope_Interactive_Scenario",
            pulp.LpMaximize,
        )

        allocations = {
            district: pulp.LpVariable(
                f"allocation_{index}",
                lowBound=0,
                upBound=need,
                cat="Integer",
            )
            for index, (district, need) in enumerate(
                zip(districts, needs)
            )
        }

        problem += (
            pulp.lpSum(
                allocations[district]
                for district in districts
            )
            <= weekly_capacity
        )

        for district, need in zip(districts, needs):
            problem += (
                allocations[district]
                >= equity_floor * need
            )

        problem += pulp.lpSum(
            allocations[district]
            for district in districts
        )

        try:
            status = problem.solve(
                pulp.PULP_CBC_CMD(
                    msg=False,
                    timeLimit=30,
                )
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Scenario solver failed: {str(exc)}",
            )

        if status != pulp.LpStatusOptimal:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "The requested equity floor cannot be satisfied "
                        "with the selected weekly capacity."
                    ),
                    "forecast_week": forecast_week.strftime("%Y-%m-%d"),
                    "weekly_capacity": weekly_capacity,
                    "equity_floor": equity_floor,
                    "represented_districts": len(districts),
                },
            )

        allocated_values = [
            int(round(pulp.value(allocations[district]) or 0))
            for district in districts
        ]

        week_demand = sum(needs)
        week_allocated = sum(allocated_values)

        positive_coverages = [
            allocated / need
            for allocated, need in zip(
                allocated_values,
                needs,
            )
            if need > 0
        ]

        week_minimum_coverage = (
            min(positive_coverages)
            if positive_coverages
            else 1.0
        )

        total_demand += week_demand
        total_allocated += week_allocated
        minimum_coverages.append(week_minimum_coverage)

        weekly_results.append({
            "forecast_week": forecast_week.strftime("%Y-%m-%d"),
            "represented_districts": len(districts),
            "total_demand": week_demand,
            "allocated_beds": week_allocated,
            "unmet_beds": week_demand - week_allocated,
            "efficiency": (
                week_allocated / week_demand
                if week_demand else None
            ),
            "minimum_coverage": week_minimum_coverage,
        })

    # --------------------------------------------------------
    # Return scenario summary
    # --------------------------------------------------------

    return {
        "available": True,
        "scenario_type": "illustrative_planning_simulation",
        "weekly_capacity": weekly_capacity,
        "equity_floor": equity_floor,
        "detector": detector,
        "z_score_threshold": threshold,
        "forecast_weeks": len(weekly_results),
        "total_demand": total_demand,
        "total_allocated": total_allocated,
        "total_unmet": total_demand - total_allocated,
        "overall_efficiency": (
            total_allocated / total_demand
            if total_demand else None
        ),
        "average_weekly_minimum_coverage": (
            sum(minimum_coverages) / len(minimum_coverages)
            if minimum_coverages else None
        ),
        "minimum_represented_districts": min(
            row["represented_districts"]
            for row in weekly_results
        ),
        "maximum_represented_districts": max(
            row["represented_districts"]
            for row in weekly_results
        ),
        "demand_coverage_note": (
            "Equity is calculated only among districts with recorded "
            "demand in each week. Missing district-week records are "
            "not imputed. Results do not establish complete national "
            "coverage or actual hospital capacity."
        ),
        "threshold_performance": threshold_metrics,
        "weekly_results": weekly_results,
    }

# ============================================================
# PREPAREDNESS CALENDAR
# ============================================================

@app.get("/api/preparedness-calendar")
def preparedness_calendar(
    division: str = Query("ALL"),
    disease: str = Query("ALL"),
):

    df = read_csv_or_empty(
        CALENDAR_FILE
    )

    if df.empty:
        return []

    if division != "ALL" and "division_name" in df.columns:
        df = df[
            df["division_name"].astype(str).str.casefold()
            == division.casefold()
        ]

    if disease != "ALL" and "disease" in df.columns:
        df = df[
            df["disease"].astype(str).str.casefold()
            == disease.casefold()
        ]

    return records(df)



