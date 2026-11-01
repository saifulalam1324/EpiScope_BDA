
from pathlib import Path
import json
from datetime import datetime, timezone

from pyspark.sql import SparkSession, functions as F


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]

SILVER = ROOT / "data" / "lake" / "silver" / "encounters"
MAPPING = ROOT / "data" / "reference" / "district_mapping.csv"
OUTPUT = ROOT / "data" / "lake" / "silver" / "encounters_named"

REPORT = ROOT / "data" / "reports" / "district_mapping.json"


# --------------------------------------------------
# VALIDATE INPUT FILES
# --------------------------------------------------

if not SILVER.exists():
    raise FileNotFoundError(f"Silver data not found: {SILVER}")

if not MAPPING.is_file():
    raise FileNotFoundError(f"District mapping not found: {MAPPING}")


# --------------------------------------------------
# SPARK
# --------------------------------------------------

spark = (
    SparkSession.builder
    .master("local[*]")
    .appName("EpiScope-District-Mapping")
    .config("spark.sql.shuffle.partitions", "8")
    .config("spark.sql.adaptive.enabled", "true")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")


try:

    # --------------------------------------------------
    # 1. READ SILVER DATA
    # --------------------------------------------------

    silver = spark.read.parquet(str(SILVER))

    if "district_id" not in silver.columns:
        raise ValueError(
            "Silver dataset does not contain district_id."
        )

    input_count = silver.count()

    # Avoid duplicate district_name columns on reruns.
    if "district_name" in silver.columns:
        silver = silver.drop("district_name")

    # --------------------------------------------------
    # 2. READ AND VALIDATE DISTRICT MAPPING
    # --------------------------------------------------

    raw_mapping = (
        spark.read
        .option("header", True)
        .option("inferSchema", False)
        .csv(str(MAPPING))
    )

    required_columns = {
        "district_id",
        "district_name"
    }

    missing_columns = (
        required_columns - set(raw_mapping.columns)
    )

    if missing_columns:
        raise ValueError(
            f"Mapping CSV missing columns: {sorted(missing_columns)}"
        )

    mapping = raw_mapping.select(
        F.trim(F.col("district_id")).alias("district_id"),
        F.trim(F.col("district_name")).alias("district_name")
    )

    # Reject empty mapping values.
    invalid_mapping = mapping.filter(
        F.col("district_id").isNull()
        | (F.col("district_id") == "")
        | F.col("district_name").isNull()
        | (F.col("district_name") == "")
    )

    if invalid_mapping.limit(1).count():
        invalid_mapping.show(20, truncate=False)
        raise ValueError(
            "District mapping contains empty IDs or names."
        )

    # District IDs must be unique.
    duplicate_ids = (
        mapping.groupBy("district_id")
        .count()
        .filter(F.col("count") > 1)
    )

    if duplicate_ids.limit(1).count():
        duplicate_ids.show(20, truncate=False)
        raise ValueError(
            "Duplicate district IDs found in mapping."
        )

    # District names should also be unique.
    duplicate_names = (
        mapping.groupBy("district_name")
        .count()
        .filter(F.col("count") > 1)
    )

    if duplicate_names.limit(1).count():
        duplicate_names.show(20, truncate=False)
        raise ValueError(
            "Duplicate district names found in mapping."
        )

    # --------------------------------------------------
    # 3. CHECK SILVER DISTRICT REFERENCES
    # --------------------------------------------------

    silver_ids = (
        silver.select("district_id")
        .distinct()
    )

    mapping_ids = (
        mapping.select("district_id")
        .distinct()
    )

    unmatched_ids = (
        silver_ids.join(
            mapping_ids,
            on="district_id",
            how="left_anti"
        )
    )

    unmatched_id_count = unmatched_ids.count()

    if unmatched_id_count:
        print("WARNING: Unmatched district IDs:")
        unmatched_ids.show(30, truncate=False)

    # --------------------------------------------------
    # 4. BROADCAST JOIN
    # --------------------------------------------------

    result = silver.join(
        F.broadcast(mapping),
        on="district_id",
        how="left"
    )

    # --------------------------------------------------
    # 5. VERIFY RECORD PRESERVATION
    # --------------------------------------------------

    total_rows = result.count()

    if total_rows != input_count:
        raise RuntimeError(
            f"Row count changed during mapping: "
            f"{input_count} -> {total_rows}"
        )

    unmatched_rows = result.filter(
        F.col("district_name").isNull()
    ).count()

    # --------------------------------------------------
    # 6. WRITE OUTPUT
    # --------------------------------------------------

    (
        result.write
        .mode("overwrite")
        .parquet(str(OUTPUT))
    )

    # --------------------------------------------------
    # 7. SAVE REPORT
    # --------------------------------------------------

    report = {
        "processing_time_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "input_rows": input_count,
        "output_rows": total_rows,
        "unmatched_rows": unmatched_rows,
        "unmatched_district_ids": unmatched_id_count,
        "mapping_rows": mapping.count(),
        "broadcast_join": True,
        "status": "success"
    }

    REPORT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    REPORT.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8"
    )

    # --------------------------------------------------
    # 8. DISPLAY RESULTS
    # --------------------------------------------------

    print("\nDistrict mapping completed.")

    print("Input Silver:", SILVER)
    print("Output:", OUTPUT)

    print("Input rows:", input_count)
    print("Output rows:", total_rows)

    print("Unmatched district IDs:", unmatched_id_count)
    print("Unmatched records:", unmatched_rows)

    print("Report:", REPORT)

    result.select(
        "district_id",
        "district_name"
    ).distinct().orderBy(
        "district_id"
    ).show(30, truncate=False)

finally:
    spark.stop()
