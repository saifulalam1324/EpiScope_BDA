
from pathlib import Path
from datetime import datetime, timezone
import json
import os

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.window import Window


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]

BRONZE = ROOT / "data" / "lake" / "bronze" / "encounters"
FACILITIES = ROOT / "data" / "raw" / "generated" / "facilities.csv"

SILVER = ROOT / "data" / "lake" / "silver" / "encounters"
QUARANTINE = ROOT / "data" / "quarantine" / "encounters"

REPORT = ROOT / "data" / "reports" / "silver_quality.json"


# --------------------------------------------------
# REQUIRED BRONZE SCHEMA
# --------------------------------------------------

REQUIRED_COLUMNS = [
    "encounter_id",
    "patient_id",
    "facility_id",
    "district_id",
    "onset_date",
    "report_date",
    "age_years",
    "sex",
    "icd10_code",
    "lab_confirmed",
    "admitted",
    "outcome",
    "batch_id",
]


# --------------------------------------------------
# QUALITY RULES
# --------------------------------------------------

QUALITY_RULES = [
    "missing_encounter_id",
    "missing_patient_id",
    "invalid_district_id",
    "orphan_facility",
    "invalid_date",
    "report_before_onset",
    "invalid_age",
    "invalid_lab_flag",
    "invalid_admission_flag",
    "invalid_sex",
    "invalid_icd10",
    "missing_outcome",
    "missing_batch_id",
    "duplicate_encounter_id",
    "cross_facility_duplicate_patient",
]


# --------------------------------------------------
# PRIVACY TRANSFORMATION
# --------------------------------------------------

def add_privacy_fields(data, salt):
    """
    Hash patient identifiers and replace exact ages
    with age bands.

    Raw patient_id and age_years are removed.
    """

    if not salt:
        raise ValueError("A non-empty privacy salt is required.")

    age = F.col("age_years").cast("int")

    return (
        data
        .withColumn(
            "patient_id_hash",
            F.sha2(
                F.concat(
                    F.lit(salt),
                    F.col("patient_id").cast("string"),
                ),
                256,
            ),
        )
        .withColumn(
            "age_band",
            F.when(age.isNull(), "unknown")
            .when(age < 5, "0-4")
            .when(age < 18, "5-17")
            .when(age < 35, "18-34")
            .when(age < 50, "35-49")
            .when(age < 65, "50-64")
            .otherwise("65+"),
        )
        .drop("patient_id", "age_years")
    )


# --------------------------------------------------
# VALIDATION HELPERS
# --------------------------------------------------

def add_reasons(data):
    """
    Apply independent validation rules.

    A record can have multiple reason codes.
    """

    age = F.col("age_years").cast("int")
    lab = F.col("lab_confirmed").cast("int")
    admitted = F.col("admitted").cast("int")

    onset = F.to_date(F.col("onset_date"))
    report = F.to_date(F.col("report_date"))

    checks = [
        F.when(
            F.col("encounter_id").isNull()
            | (F.trim(F.col("encounter_id")) == ""),
            "missing_encounter_id",
        ),

        F.when(
            F.col("patient_id").isNull()
            | (F.trim(F.col("patient_id")) == ""),
            "missing_patient_id",
        ),

        F.when(
            F.col("district_id").isNull()
            | (~F.col("district_id").rlike(r"^D[0-9]{3}$")),
            "invalid_district_id",
        ),

        F.when(
            F.col("facility_exists").isNull(),
            "orphan_facility",
        ),

        F.when(
            onset.isNull() | report.isNull(),
            "invalid_date",
        ),

        F.when(
            onset.isNotNull()
            & report.isNotNull()
            & (report < onset),
            "report_before_onset",
        ),

        F.when(
            age.isNull() | (age < 0) | (age > 120),
            "invalid_age",
        ),

        F.when(
            lab.isNull() | (~lab.isin(0, 1)),
            "invalid_lab_flag",
        ),

        F.when(
            admitted.isNull() | (~admitted.isin(0, 1)),
            "invalid_admission_flag",
        ),

        F.when(
            F.col("sex").isNull()
            | (~F.upper(F.trim(F.col("sex"))).isin("M", "F")),
            "invalid_sex",
        ),

        F.when(
            F.col("icd10_code").isNull()
            | (
                ~F.upper(F.trim(F.col("icd10_code")))
                .rlike(r"^[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
            ),
            "invalid_icd10",
        ),

        F.when(
            F.col("outcome").isNull()
            | (F.trim(F.col("outcome")) == ""),
            "missing_outcome",
        ),

        F.when(
            F.col("batch_id").isNull()
            | (F.trim(F.col("batch_id")) == ""),
            "missing_batch_id",
        ),
    ]

    return data.withColumn(
        "reasons",
        F.filter(
            F.array(*checks),
            lambda item: item.isNotNull(),
        ),
    )


# --------------------------------------------------
# DQ-4: CROSS-FACILITY PATIENT DEDUPLICATION
# --------------------------------------------------

def detect_cross_facility_duplicates(data, salt):
    """
    Identify likely duplicate patient episodes across facilities.

    Matching criteria:
      - Same hashed patient identifier.
      - Same district.
      - Same onset date.
      - Same ICD-10 code.
      - Different facility.
      - Report dates within seven days.

    The earliest encounter is retained as the reference.
    Later matching encounters are quarantined for review.

    This function must receive records that passed validation.
    """

    prepared = (
        data
        .withColumn(
            "_patient_match_hash",
            F.sha2(
                F.concat(
                    F.lit(salt),
                    F.col("patient_id").cast("string"),
                ),
                256,
            ),
        )
        .withColumn(
            "_onset_date",
            F.to_date("onset_date"),
        )
        .withColumn(
            "_report_date",
            F.to_date("report_date"),
        )
        .withColumn(
            "_icd10_normalized",
            F.upper(F.trim(F.col("icd10_code"))),
        )
    )

    match_window = (
        Window
        .partitionBy(
            "_patient_match_hash",
            "district_id",
            "_onset_date",
            "_icd10_normalized",
        )
        .orderBy(
            F.col("_report_date").asc(),
            F.col("facility_id").asc(),
            F.col("encounter_id").asc(),
        )
    )

    ranked = (
        prepared
        .withColumn(
            "_match_rank",
            F.row_number().over(match_window),
        )
        .withColumn(
            "_first_facility",
            F.first("facility_id").over(match_window),
        )
        .withColumn(
            "_first_report_date",
            F.first("_report_date").over(match_window),
        )
    )

    duplicate_candidates = (
        ranked
        .filter(
            (F.col("_match_rank") > 1)
            & (F.col("facility_id") != F.col("_first_facility"))
            & (
                F.datediff(
                    F.col("_report_date"),
                    F.col("_first_report_date"),
                ).between(0, 7)
            )
        )
        .drop(
            "_patient_match_hash",
            "_onset_date",
            "_report_date",
            "_icd10_normalized",
            "_match_rank",
            "_first_facility",
            "_first_report_date",
        )
        .withColumn(
            "reasons",
            F.array(
                F.lit("cross_facility_duplicate_patient")
            ),
        )
    )

    duplicate_ids = duplicate_candidates.select(
        "encounter_id"
    ).distinct()

    remaining = (
        data
        .join(
            duplicate_ids,
            on="encounter_id",
            how="left_anti",
        )
    )

    return remaining, duplicate_candidates


# --------------------------------------------------
# MAIN PIPELINE
# --------------------------------------------------

def main():

    # 1. Load privacy configuration.

    salt = os.environ.get("EPISCOPE_HASH_SALT")

    if not salt:
        raise RuntimeError(
            "Set EPISCOPE_HASH_SALT before running Silver."
        )

    # 2. Validate input paths.

    if not BRONZE.exists():
        raise FileNotFoundError(
            f"Bronze data not found: {BRONZE}"
        )

    if not FACILITIES.exists():
        raise FileNotFoundError(
            f"Facilities file not found: {FACILITIES}"
        )

    # 3. Start Spark.

    spark = (
        SparkSession.builder
        .appName("EpiScope-Silver-Cleaning")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("WARN")

    try:

        # 4. Read Bronze.

        df = spark.read.parquet(str(BRONZE))

        missing_columns = [
            column for column in REQUIRED_COLUMNS
            if column not in df.columns
        ]

        if missing_columns:
            raise ValueError(
                f"Bronze schema missing columns: {missing_columns}"
            )

        input_count = df.count()

        # 5. Read facility reference data.

        facilities = (
            spark.read
            .option("header", True)
            .option("inferSchema", False)
            .csv(str(FACILITIES))
            .select("facility_id")
            .filter(F.col("facility_id").isNotNull())
            .distinct()
            .withColumn("facility_exists", F.lit(True))
        )

        # 6. Identify duplicate encounter IDs.

        encounter_window = (
            Window
            .partitionBy("encounter_id")
            .orderBy(
                F.col("report_date").asc_nulls_last(),
                F.col("batch_id").asc_nulls_last(),
            )
        )

        ranked = df.withColumn(
            "_row_number",
            F.row_number().over(encounter_window),
        )

        duplicate_rows = (
            ranked
            .filter(F.col("_row_number") > 1)
            .drop("_row_number")
            .withColumn(
                "reasons",
                F.array(F.lit("duplicate_encounter_id")),
            )
        )

        unique = (
            ranked
            .filter(F.col("_row_number") == 1)
            .drop("_row_number")
        )

        # 7. Check facility references.

        checked = unique.join(
            F.broadcast(facilities),
            on="facility_id",
            how="left",
        )

        # 8. Apply existing validation rules.

        checked = add_reasons(checked)

        rejected = checked.filter(
            F.size("reasons") > 0
        )

        valid = (
            checked
            .filter(F.size("reasons") == 0)
            .drop("facility_exists", "reasons")
        )

        rejected = rejected.select(
            *df.columns,
            "reasons",
        )

        # 9. DQ-4 cross-facility duplicate detection.

        valid, cross_facility_duplicates = (
            detect_cross_facility_duplicates(valid, salt)
        )

        # 10. Combine all quarantine records.

        quarantine_all = (
            rejected
            .unionByName(duplicate_rows)
            .unionByName(cross_facility_duplicates)
        )

        # 11. Apply privacy transformations.

        valid = add_privacy_fields(valid, salt)

        quarantine_all = add_privacy_fields(
            quarantine_all,
            salt,
        )

        # 12. Save outputs.

        (
            valid.write
            .mode("overwrite")
            .parquet(str(SILVER))
        )

        (
            quarantine_all.write
            .mode("overwrite")
            .parquet(str(QUARANTINE))
        )

        # 13. Calculate counts.

        duplicate_count = duplicate_rows.count()
        rejected_count = rejected.count()
        cross_facility_count = cross_facility_duplicates.count()
        valid_count = valid.count()
        quarantined_count = quarantine_all.count()

        # 14. Calculate rule-level metrics.

        rule_counts = (
            quarantine_all
            .select(F.explode("reasons").alias("rule"))
            .groupBy("rule")
            .count()
            .collect()
        )

        failure_counts = {
            row["rule"]: row["count"]
            for row in rule_counts
        }

        rule_metrics = {}

        for rule in QUALITY_RULES:

            failures = failure_counts.get(rule, 0)

            rule_metrics[rule] = {
                "failed_rows": failures,
                "pass_rate": round(
                    max(0, input_count - failures)
                    / input_count,
                    4,
                ) if input_count else 1.0,
            }

        # 15. Collect Bronze batch lineage.

        batch_ids = [
            row["batch_id"]
            for row in (
                df.select("batch_id")
                .where(F.col("batch_id").isNotNull())
                .distinct()
                .collect()
            )
        ]

        # 16. Create quality report.

        quality_report = {
            "processing_time_utc": datetime.now(
                timezone.utc
            ).isoformat(),

            "input_rows": input_count,
            "valid_rows": valid_count,

            "duplicate_rows_quarantined": duplicate_count,
            "invalid_unique_rows_quarantined": rejected_count,
            "cross_facility_duplicate_rows_quarantined": (
                cross_facility_count
            ),

            "quarantined_rows": quarantined_count,

            "bronze_batch_ids": batch_ids,

            "rules": rule_metrics,

            "dq4": {
                "matching_method": (
                    "same_hashed_patient_district_onset_icd10"
                ),
                "different_facility_required": True,
                "maximum_report_date_difference_days": 7,
                "earliest_encounter_retained": True,
                "candidate_encounters_quarantined_for_review": True,
            },

            "privacy": {
                "patient_id_hashed": True,
                "exact_patient_id_removed": True,
                "age_years_replaced_with_age_band": True,
                "salt_loaded_from_environment": True,
            },
        }

        # 17. Save report.

        REPORT.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        REPORT.write_text(
            json.dumps(quality_report, indent=2),
            encoding="utf-8",
        )

        print("\nSilver cleaning completed.")
        print(json.dumps(quality_report, indent=2))

        print("\nSilver output:", SILVER)
        print("Quarantine output:", QUARANTINE)
        print("Quality report:", REPORT)

    finally:
        spark.stop()


# --------------------------------------------------
# SAFE EXECUTION ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    main()
