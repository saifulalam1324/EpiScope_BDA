
"""
EpiScope - Gold Layer Aggregation

Reads:
    data/lake/silver/encounters_named/
    data/reports/silver_quality.json

Writes:
    data/lake/gold/weekly_district_disease/
    data/lake/gold/data_quality/

Features:
    - Weekly district/disease aggregation
    - Disease grouping from ICD-10 codes
    - Parquet output partitioned by year
    - Bronze batch lineage
    - Data-quality summary
    - Idempotent quality-report writes
"""

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import sys

# Force Spark workers to use this project's active Python environment.
PYTHON_EXE = sys.executable
os.environ["PYSPARK_PYTHON"] = PYTHON_EXE
os.environ["PYSPARK_DRIVER_PYTHON"] = PYTHON_EXE

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DoubleType,
    ArrayType,
)


# =========================================================
# 1. PATHS AND CONFIGURATION
# =========================================================

ROOT = Path(__file__).resolve().parents[2]

SILVER_INPUT = (
    ROOT / "data" / "lake" / "silver" / "encounters_named"
)

QUALITY_REPORT = (
    ROOT / "data" / "reports" / "silver_quality.json"
)

WEEKLY_GOLD = (
    ROOT / "data" / "lake" / "gold" /
    "weekly_district_disease"
)

QUALITY_GOLD = (
    ROOT / "data" / "lake" / "gold" / "data_quality"
)


# =========================================================
# 2. SPARK SESSION
# =========================================================

def create_spark():

    return (
        SparkSession.builder
        .appName("EpiScope-Gold-Aggregation")
        .master("local[2]")
        .config("spark.pyspark.python", PYTHON_EXE)
        .config("spark.pyspark.driver.python", PYTHON_EXE)
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )

# =========================================================
# 3. HELPERS
# =========================================================

def read_quality_report():

    if not QUALITY_REPORT.exists():
        print(
            "WARNING: Silver quality report not found. "
            "Quality metrics will use available aggregation data."
        )
        return {}

    try:
        with open(QUALITY_REPORT, "r", encoding="utf-8") as file:
            report = json.load(file)

        if not isinstance(report, dict):
            raise ValueError("Quality report must contain a JSON object.")

        return report

    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"Could not read Silver quality report: {exc}"
        ) from exc


def get_report_value(report, possible_names, default=0):

    for name in possible_names:
        if name in report:
            return report[name]

    return default


def get_disease_group():

    return (
        F.when(F.col("icd10_code").rlike(r"^A90|^A91"), "dengue")
        .when(F.col("icd10_code").rlike(r"^A0[0-9]"), "diarrhoea")
        .when(F.col("icd10_code").rlike(r"^J"), "influenza")
        .when(F.col("icd10_code").rlike(r"^B5[0-4]"), "malaria")
        .otherwise("other")
    )


def has_parquet_files(path):

    return (
        path.exists()
        and any(path.rglob("*.parquet"))
    )


# =========================================================
# 4. MAIN PIPELINE
# =========================================================

def main():

    spark = create_spark()

    try:

        print("\n======================================")
        print(" EpiScope Gold Aggregation")
        print("======================================\n")

        # -------------------------------------------------
        # Check input
        # -------------------------------------------------

        if not SILVER_INPUT.exists():
            raise FileNotFoundError(
                f"Silver input not found: {SILVER_INPUT}"
            )

        if not has_parquet_files(SILVER_INPUT):
            raise FileNotFoundError(
                "Silver input directory contains no Parquet files."
            )

        print("[1/9] Reading Silver data...")

        df = spark.read.parquet(str(SILVER_INPUT))

        required_columns = [
            "district_id",
            "district_name",
            "onset_date",
            "admitted",
            "lab_confirmed",
            "icd10_code",
            "batch_id",
        ]

        missing = [
            column for column in required_columns
            if column not in df.columns
        ]

        if missing:
            raise ValueError(
                f"Silver dataset is missing required columns: {missing}"
            )

        input_rows = df.count()

        print(f"Silver input rows: {input_rows:,}")

        if input_rows == 0:
            raise ValueError("Silver dataset contains zero rows.")

        # -------------------------------------------------
        # Standardize columns
        # -------------------------------------------------

        print("[2/9] Standardizing columns...")

        df = (
            df
            .withColumn(
                "onset_date",
                F.to_date(F.col("onset_date"))
            )
            .withColumn(
                "admitted",
                F.col("admitted").cast("int")
            )
            .withColumn(
                "lab_confirmed",
                F.col("lab_confirmed").cast("int")
            )
            .withColumn(
                "icd10_code",
                F.upper(F.trim(F.col("icd10_code").cast("string")))
            )
            .withColumn(
                "district_id",
                F.trim(F.col("district_id").cast("string"))
            )
            .withColumn(
                "district_name",
                F.trim(F.col("district_name").cast("string"))
            )
            .withColumn(
                "batch_id",
                F.col("batch_id").cast("string")
            )
        )

        # -------------------------------------------------
        # Filter invalid aggregation records
        # -------------------------------------------------

        print("[3/9] Checking aggregation records...")

        invalid_condition = (
            F.col("onset_date").isNull()
            | F.col("district_id").isNull()
            | (F.col("district_id") == "")
            | F.col("district_name").isNull()
            | (F.col("district_name") == "")
            | F.col("batch_id").isNull()
            | (F.col("batch_id") == "")
            | (~F.col("admitted").isin(0, 1))
            | (~F.col("lab_confirmed").isin(0, 1))
        )

        invalid_rows = df.filter(invalid_condition).count()

        valid_df = df.filter(~invalid_condition)

        valid_rows = valid_df.count()

        print(f"Valid rows: {valid_rows:,}")
        print(f"Invalid aggregation rows: {invalid_rows:,}")

        if valid_rows == 0:
            raise ValueError(
                "No valid rows remain for Gold aggregation."
            )

        # -------------------------------------------------
        # Disease classification
        # -------------------------------------------------

        print("[4/9] Classifying diseases...")

        valid_df = valid_df.withColumn(
            "disease_group",
            get_disease_group()
        )

        # -------------------------------------------------
        # Weekly aggregation
        # -------------------------------------------------

        print("[5/9] Aggregating weekly district data...")

        weekly_df = (
            valid_df
            .withColumn(
                "week_start",
                F.to_date(
                    F.date_trunc("week", F.col("onset_date"))
                )
            )
            .withColumn(
                "year",
                F.year(F.col("week_start"))
            )
            .groupBy(
                "district_id",
                "district_name",
                "disease_group",
                "week_start",
                "year",
            )
            .agg(
                F.count("*").alias("case_count"),
                F.sum("admitted").alias("admissions"),
                F.sum("lab_confirmed").alias("lab_confirmed_cases"),
                F.sort_array(
                    F.collect_set("batch_id")
                ).alias("bronze_batch_ids"),
            )
            .withColumn(
                "processing_time_utc",
                F.lit(
                    datetime.now(timezone.utc).isoformat()
                )
            )
        )

        weekly_count = weekly_df.count()

        print(f"Gold weekly records: {weekly_count:,}")

        # -------------------------------------------------
        # Write weekly Gold output
        # -------------------------------------------------

        print("[6/9] Writing weekly Gold Parquet...")

        WEEKLY_GOLD.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        (
            weekly_df
            .repartition("year")
            .write
            .mode("overwrite")
            .partitionBy("year")
            .parquet(str(WEEKLY_GOLD))
        )

        print(f"Weekly Gold saved: {WEEKLY_GOLD}")

        # -------------------------------------------------
        # Prepare data-quality report
        # -------------------------------------------------

        print("[7/9] Preparing quality report...")

        silver_report = read_quality_report()

        silver_input = int(
            get_report_value(
                silver_report,
                ["input_rows", "total_rows"],
                input_rows
            ) or input_rows
        )

        silver_valid = int(
            get_report_value(
                silver_report,
                ["valid_rows", "accepted_rows"],
                valid_rows
            ) or 0
        )

        silver_quarantined = int(
            get_report_value(
                silver_report,
                ["quarantined_rows", "rejected_rows"],
                0
            ) or 0
        )

        silver_pass_rate = (
            silver_valid / silver_input
            if silver_input > 0 else 0.0
        )

        batch_ids = [
            row["batch_id"]
            for row in (
                valid_df
                .select("batch_id")
                .distinct()
                .collect()
            )
            if row["batch_id"] is not None
        ]

        processing_time = datetime.now(
            timezone.utc
        ).isoformat()

        quality_rules = [
            (
                "gold_valid_aggregation_rows",
                invalid_rows,
                (
                    valid_rows / input_rows
                    if input_rows else 0.0
                )
            ),
            (
                "silver_quality_pass_rate",
                silver_quarantined,
                silver_pass_rate
            ),
        ]

        report_content = {
            "processing_time_utc": processing_time,
            "input_rows": input_rows,
            "valid_rows": valid_rows,
            "quarantined_rows": invalid_rows,
            "weekly_gold_rows": weekly_count,
            "bronze_batch_ids": sorted(batch_ids),
            "silver_report_input_rows": silver_input,
            "silver_report_valid_rows": silver_valid,
            "silver_report_quarantined_rows": silver_quarantined,
            "rules": quality_rules,
        }

        report_bytes = json.dumps(
            report_content,
            sort_keys=True,
            default=str
        ).encode("utf-8")

        report_id = hashlib.sha256(
            report_bytes
        ).hexdigest()

        quality_rows = []

        for rule_name, failed_rows, pass_rate in quality_rules:

            quality_rows.append(
                (
                    report_id,
                    processing_time,
                    rule_name,
                    int(failed_rows),
                    float(pass_rate),
                    int(input_rows),
                    int(valid_rows),
                    int(invalid_rows),
                    sorted(batch_ids),
                )
            )

        quality_schema = StructType([
            StructField("report_id", StringType(), False),
            StructField("processing_time_utc", StringType(), True),
            StructField("rule", StringType(), False),
            StructField("failed_rows", LongType(), False),
            StructField("pass_rate", DoubleType(), False),
            StructField("input_rows", LongType(), False),
            StructField("valid_rows", LongType(), False),
            StructField("quarantined_rows", LongType(), False),
            StructField(
                "bronze_batch_ids",
                ArrayType(StringType()),
                True
            ),
        ])

        quality_df = spark.createDataFrame(
            quality_rows,
            schema=quality_schema
        )

        # -------------------------------------------------
        # Idempotent quality report write
        # -------------------------------------------------

        print("[8/9] Writing quality report...")

        QUALITY_GOLD.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        report_already_loaded = False

        if has_parquet_files(QUALITY_GOLD):

            existing_quality = spark.read.parquet(
                str(QUALITY_GOLD)
            )

            if "report_id" in existing_quality.columns:

                report_already_loaded = (
                    existing_quality
                    .filter(
                        F.col("report_id") == report_id
                    )
                    .limit(1)
                    .count() > 0
                )

            else:
                raise RuntimeError(
                    "Existing Gold quality table has an older schema "
                    "without report_id. Back it up and migrate it before "
                    "appending the new schema."
                )

        if report_already_loaded:

            print(
                "This quality report already exists. "
                "Skipping duplicate write."
            )

        else:

            (
                quality_df
                .write
                .mode("append")
                .parquet(str(QUALITY_GOLD))
            )

            print("Quality report saved.")

        # -------------------------------------------------
        # Final summary
        # -------------------------------------------------

        print("\n[9/9] Gold aggregation completed.")

        print("\n========== SUMMARY ==========")
        print(f"Input rows:             {input_rows:,}")
        print(f"Valid rows:             {valid_rows:,}")
        print(f"Invalid rows:           {invalid_rows:,}")
        print(f"Weekly Gold rows:       {weekly_count:,}")
        print(f"Bronze batches traced:  {len(batch_ids):,}")
        print(f"Weekly output:          {WEEKLY_GOLD}")
        print(f"Quality output:         {QUALITY_GOLD}")
        print("=============================\n")

    except Exception as exc:

        print("\nGOLD AGGREGATION FAILED")
        print(f"Reason: {exc}")

        raise

    finally:

        spark.stop()


# =========================================================
# 5. ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
