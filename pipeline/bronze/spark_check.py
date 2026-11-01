
from pathlib import Path
import sys

from pyspark.sql import SparkSession
from pyspark.sql.functions import count, countDistinct

ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    ROOT / "data" / "raw" / "generated" / "encounters.csv"
)

REQUIRED_COLUMNS = {
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
}


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Encounter dataset not found: {INPUT_FILE}\n"
            "Run generator/generate_data.py first."
        )

    spark = (
        SparkSession.builder
        .appName("EpiScope-Bronze-Test")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("WARN")

    try:
        print("=" * 60)
        print("EPISCOPE SPARK VALIDATION")
        print("=" * 60)

        print("\nInput file:", INPUT_FILE)

        # Explicit schema inference is disabled here.
        # Bronze ingestion will preserve source values.
        df = (
            spark.read
            .option("header", True)
            .option("inferSchema", False)
            .option("mode", "FAILFAST")
            .csv(str(INPUT_FILE))
        )

        print("\nEncounter schema:")
        df.printSchema()

        actual_columns = set(df.columns)

        missing_columns = REQUIRED_COLUMNS - actual_columns

        if missing_columns:
            raise ValueError(
                f"Missing required columns: {sorted(missing_columns)}"
            )

        total_records = df.count()

        print("\nTotal encounter records:", total_records)

        if total_records == 0:
            raise ValueError("Encounter dataset is empty.")

        print(
            "Unique encounter IDs:",
            df.select(countDistinct("encounter_id")).first()[0]
        )

        print(
            "Unique patients:",
            df.select(countDistinct("patient_id")).first()[0]
        )

        print(
            "Unique districts:",
            df.select(countDistinct("district_id")).first()[0]
        )

        print("\nCases by district:")

        (
            df.groupBy("district_id")
            .agg(count("*").alias("encounter_count"))
            .orderBy("district_id")
            .show(20, truncate=False)
        )

        print("\nSample records:")

        df.show(5, truncate=False)

        print("\n" + "=" * 60)
        print("SPARK VALIDATION SUCCESSFUL")
        print("=" * 60)

    finally:
        spark.stop()


if __name__ == "__main__":
    main()
