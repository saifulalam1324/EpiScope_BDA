from pathlib import Path
import csv
import json
import traceback

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

ROOT = Path.cwd()
DATA = ROOT / "data"
OUTPUT = DATA / "reports" / "full_extraction"

PARQUET_DATASETS = {
    "bronze_encounters": DATA / "lake" / "bronze" / "encounters",
    "silver_encounters": DATA / "lake" / "silver" / "encounters",
    "silver_encounters_named": DATA / "lake" / "silver" / "encounters_named",
    "gold_data_quality": DATA / "lake" / "gold" / "data_quality",
    "gold_weekly_district_disease": DATA / "lake" / "gold" / "weekly_district_disease",
    "quarantine_encounters": DATA / "quarantine" / "encounters",
}

def write_inventory(rows, path, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    spark = (
        SparkSession.builder
        .appName("EpiScope_Full_Layer_Extraction")
        .getOrCreate()
    )

    inventory = []
    errors = []

    try:
        print("\n========== EPISCOPE FULL EXTRACTION ==========\n")

        for name, source in PARQUET_DATASETS.items():
            print(f"\nProcessing: {name}")
            print(f"Source: {source}")

            if not source.exists():
                print("NOT FOUND - skipping")
                inventory.append({
                    "dataset": name,
                    "source": str(source),
                    "format": "Parquet",
                    "rows": "",
                    "columns": "",
                    "status": "NOT FOUND",
                    "csv_output": "",
                })
                continue

            try:
                df = spark.read.parquet(str(source))

                row_count = df.count()
                column_names = df.columns

                print(f"Rows: {row_count:,}")
                print(f"Columns: {len(column_names)}")
                print(f"Column names: {column_names}")

                # Export complete dataset to CSV.
                csv_output = OUTPUT / name

                if csv_output.exists():
                    import shutil
                    shutil.rmtree(csv_output)

                (
                    df.write
                    .mode("overwrite")
                    .option("header", True)
                    .option("encoding", "UTF-8")
                    .csv(str(csv_output))
                )

                print(f"CSV exported: {csv_output}")

                # Calculate null counts for each column.
                null_expressions = [
                    F.sum(
                        F.when(
                            F.col(c).isNull(), 1
                        ).otherwise(0)
                    ).alias(c)
                    for c in column_names
                ]

                null_values = df.agg(*null_expressions).first().asDict()

                null_rows = [
                    {
                        "dataset": name,
                        "column": col,
                        "null_count": int(null_values[col] or 0),
                    }
                    for col in column_names
                ]

                write_inventory(
                    null_rows,
                    OUTPUT / f"{name}_null_counts.csv",
                    ["dataset", "column", "null_count"],
                )

                # Save schema information.
                schema_path = OUTPUT / f"{name}_schema.json"
                with schema_path.open("w", encoding="utf-8") as f:
                    json.dump(
                        json.loads(df.schema.json()),
                        f,
                        indent=2,
                    )

                inventory.append({
                    "dataset": name,
                    "source": str(source),
                    "format": "Parquet",
                    "rows": row_count,
                    "columns": len(column_names),
                    "status": "EXPORTED",
                    "csv_output": str(csv_output),
                })

                print("Schema and null-count reports saved.")

            except Exception as e:
                print(f"ERROR: {e}")
                errors.append({
                    "dataset": name,
                    "error": str(e),
                })

                inventory.append({
                    "dataset": name,
                    "source": str(source),
                    "format": "Parquet",
                    "rows": "",
                    "columns": "",
                    "status": "ERROR",
                    "csv_output": "",
                })

        # Inventory existing CSV files in data and analytics.
        print("\n\n========== EXISTING CSV FILES ==========\n")

        csv_inventory = []

        search_directories = [
            ROOT / "analytics",
            DATA / "raw",
            DATA / "reference",
            DATA / "weather",
            DATA / "reports",
        ]

        for folder in search_directories:
            if not folder.exists():
                continue

            for file in folder.rglob("*.csv"):
                if OUTPUT in file.parents:
                    continue

                try:
                    with file.open(
                        "r",
                        encoding="utf-8-sig",
                        newline="",
                    ) as f:
                        reader = csv.reader(f)
                        header = next(reader, [])
                        row_count = sum(1 for _ in reader)

                    csv_inventory.append({
                        "file": str(file.relative_to(ROOT)),
                        "rows": row_count,
                        "columns": len(header),
                        "column_names": " | ".join(header),
                        "status": "READ",
                    })

                    print(
                        f"{file.relative_to(ROOT)}: "
                        f"{row_count:,} rows"
                    )

                except Exception as e:
                    csv_inventory.append({
                        "file": str(file.relative_to(ROOT)),
                        "rows": "",
                        "columns": "",
                        "column_names": "",
                        "status": f"ERROR: {e}",
                    })

        write_inventory(
            inventory,
            OUTPUT / "parquet_layer_inventory.csv",
            [
                "dataset", "source", "format",
                "rows", "columns", "status", "csv_output",
            ],
        )

        write_inventory(
            csv_inventory,
            OUTPUT / "existing_csv_inventory.csv",
            [
                "file", "rows", "columns",
                "column_names", "status",
            ],
        )

        write_inventory(
            errors,
            OUTPUT / "extraction_errors.csv",
            ["dataset", "error"],
        )

        summary = {
            "project": "EpiScope",
            "parquet_datasets_found": sum(
                1 for x in inventory if x["status"] == "EXPORTED"
            ),
            "parquet_datasets_missing": sum(
                1 for x in inventory if x["status"] == "NOT FOUND"
            ),
            "parquet_datasets_with_errors": len(errors),
            "existing_csv_files_scanned": len(csv_inventory),
            "output_directory": str(OUTPUT),
        }

        with (OUTPUT / "extraction_summary.json").open(
            "w", encoding="utf-8"
        ) as f:
            json.dump(summary, f, indent=2)

        print("\n========== EXTRACTION SUMMARY ==========")
        print(json.dumps(summary, indent=2))
        print("\nExtraction completed.")
        print(f"All reports: {OUTPUT}")

    finally:
        spark.stop()

if __name__ == "__main__":
    main()
