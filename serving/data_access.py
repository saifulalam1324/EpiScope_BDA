from pathlib import Path
import json

import pandas as pd


# ============================================================
# PROJECT PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

REFERENCE_DIR = ROOT / "data" / "reference"
REPORT_DIR = ROOT / "data" / "reports"

GOLD_DIR = (
    ROOT
    / "data"
    / "lake"
    / "gold"
    / "weekly_district_disease"
)

DISTRICT_MAPPING_FILE = (
    REFERENCE_DIR / "district_mapping.csv"
)


# ============================================================
# SERVING EXCEPTIONS
# ============================================================

class ServingDataError(Exception):
    """Raised when a required serving dataset cannot be loaded."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


# ============================================================
# CSV AND JSON ACCESS
# ============================================================

def read_csv_or_empty(path: Path) -> pd.DataFrame:
    """Read a CSV file, returning an empty DataFrame if unavailable."""

    if not path.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(path)

    except (
        OSError,
        ValueError,
        pd.errors.ParserError,
    ):
        return pd.DataFrame()


def read_json_or_empty(path: Path) -> dict:
    """Read a JSON object, returning an empty dictionary if unavailable."""

    if not path.exists():
        return {}

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )

        return value if isinstance(value, dict) else {}

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}


def read_geojson(path: Path) -> dict:
    """Read a GeoJSON file and validate its FeatureCollection structure."""

    if not path.exists():
        raise ServingDataError(
            f"GeoJSON file not found: {path}",
            status_code=503,
        )

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )

    except (OSError, json.JSONDecodeError) as exc:
        raise ServingDataError(
            f"Could not read GeoJSON: {exc}"
        ) from exc

    if (
        not isinstance(value, dict)
        or value.get("type") != "FeatureCollection"
        or not isinstance(value.get("features"), list)
    ):
        raise ServingDataError(
            "Invalid district GeoJSON FeatureCollection."
        )

    return value


# ============================================================
# DISTRICT MAPPING
# ============================================================

def district_name_map() -> dict:
    """Return a validated district ID-to-name mapping."""

    if not DISTRICT_MAPPING_FILE.exists():
        return {}

    try:
        mapping = pd.read_csv(
            DISTRICT_MAPPING_FILE,
            dtype=str,
        )

        required = {
            "district_id",
            "district_name",
        }

        missing = required - set(mapping.columns)

        if missing:
            raise ValueError(
                f"District mapping missing columns: {sorted(missing)}"
            )

        mapping["district_id"] = (
            mapping["district_id"].str.strip()
        )

        mapping["district_name"] = (
            mapping["district_name"].str.strip()
        )

        if mapping["district_id"].duplicated().any():
            raise ValueError(
                "Duplicate district IDs in district mapping."
            )

        if mapping["district_name"].duplicated().any():
            raise ValueError(
                "Duplicate district names in district mapping."
            )

        return dict(
            zip(
                mapping["district_id"],
                mapping["district_name"],
            )
        )

    except (
        OSError,
        ValueError,
        pd.errors.ParserError,
    ) as exc:
        raise ServingDataError(
            f"Could not load district mapping: {exc}"
        ) from exc


# ============================================================
# GOLD PARQUET ACCESS
# ============================================================

def read_gold() -> pd.DataFrame:
    """Load and validate the district-disease weekly Gold dataset."""

    if not GOLD_DIR.exists():
        raise ServingDataError(
            "Gold dataset unavailable. Run the data pipeline first.",
            status_code=503,
        )

    try:
        df = pd.read_parquet(GOLD_DIR)

    except Exception as exc:
        raise ServingDataError(
            f"Could not read Gold Parquet: {exc}"
        ) from exc

    # Support the actual Gold schema and legacy column names.
    if (
        "disease" not in df.columns
        and "disease_group" in df.columns
    ):
        df = df.rename(
            columns={"disease_group": "disease"}
        )

    if (
        "cases" not in df.columns
        and "case_count" in df.columns
    ):
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
        raise ServingDataError(
            (
                f"Gold dataset missing columns: {sorted(missing)}. "
                f"Available columns: {sorted(df.columns)}"
            )
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
