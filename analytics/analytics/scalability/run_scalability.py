
"""
EpiScope - Spark Scalability Benchmark

Controlled synthetic public-health workload.
Each dataset size is benchmarked repeatedly to reduce timing noise.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import platform
import shutil
import statistics
import sys
import time

from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


ROOT = Path(__file__).resolve().parents[3]

OUTPUT_DIR = ROOT / "data" / "reports" / "scalability"
TEMP_DIR = OUTPUT_DIR / "benchmark_temp"

CSV_OUTPUT = OUTPUT_DIR / "scalability_results.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "scalability_summary.csv"
JSON_OUTPUT = OUTPUT_DIR / "scalability_report.json"

DEFAULT_SIZES = [10_000, 100_000, 500_000, 1_000_000]
DEFAULT_PARTITIONS = 8
DEFAULT_REPEATS = 3

LOGGER = logging.getLogger("episcope_scalability")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run repeated EpiScope Spark scalability experiments."
    )

    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=DEFAULT_SIZES,
    )

    parser.add_argument(
        "--partitions",
        type=int,
        default=DEFAULT_PARTITIONS,
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="Number of repetitions for each dataset size.",
    )

    parser.add_argument(
        "--master",
        default="local[*]",
    )

    parser.add_argument(
        "--app-name",
        default="EpiScopeScalabilityBenchmark",
    )

    return parser.parse_args()


def create_spark(master: str, app_name: str):
    spark = (
        SparkSession.builder
        .master(master)
        .appName(app_name)
        .config("spark.sql.shuffle.partitions", str(DEFAULT_PARTITIONS))
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("WARN")
    return spark


def generate_encounters(spark, row_count: int, partitions: int):
    df = spark.range(
        start=0,
        end=row_count,
        step=1,
        numPartitions=partitions,
    )

    df = (
        df
        .withColumn(
            "encounter_id",
            F.concat(F.lit("E"), F.col("id").cast("string")),
        )
        .withColumn(
            "district_id",
            F.concat(
                F.lit("D"),
                F.lpad(
                    (F.pmod(F.col("id"), F.lit(64)) + 1).cast("string"),
                    3,
                    "0",
                ),
            ),
        )
        .withColumn(
            "facility_id",
            F.concat(
                F.lit("F"),
                F.lpad(
                    (F.pmod(F.col("id"), F.lit(600)) + 1).cast("string"),
                    4,
                    "0",
                ),
            ),
        )
        .withColumn(
            "disease_group",
            F.when(F.pmod(F.col("id"), 5) == 0, "Disease_A")
            .when(F.pmod(F.col("id"), 5) == 1, "Disease_B")
            .when(F.pmod(F.col("id"), 5) == 2, "Disease_C")
            .when(F.pmod(F.col("id"), 5) == 3, "Disease_D")
            .otherwise("Disease_E"),
        )
        .withColumn(
            "age",
            (F.pmod(F.col("id"), 90) + 1).cast("integer"),
        )
        .withColumn(
            "admitted",
            F.when(F.pmod(F.col("id"), 7) == 0, 1).otherwise(0),
        )
        .withColumn(
            "encounter_date",
            F.date_add(
                F.to_date(F.lit("2021-01-01")),
                F.pmod(F.col("id"), 1826).cast("integer"),
            ),
        )
        .withColumn(
            "patient_hash",
            F.sha2(
                F.concat(
                    F.lit("synthetic-patient-"),
                    F.pmod(F.col("id"), 1_000_000).cast("string"),
                ),
                256,
            ),
        )
        .drop("id")
    )

    return df


def run_benchmark(
    spark,
    row_count: int,
    partitions: int,
    repetition: int,
):
    output_path = TEMP_DIR / f"run_{row_count}_{repetition}"

    if output_path.exists():
        shutil.rmtree(output_path)

    LOGGER.info(
        "Starting %s records, repetition %s",
        f"{row_count:,}",
        repetition,
    )

    start_time = time.perf_counter()

    encounters = generate_encounters(
        spark,
        row_count,
        partitions,
    )

    input_partitions = encounters.rdd.getNumPartitions()

    aggregated = (
        encounters
        .withColumn(
            "week_start",
            F.to_date(
                F.date_trunc(
                    "week",
                    F.col("encounter_date").cast("timestamp"),
                )
            ),
        )
        .groupBy(
            "district_id",
            "disease_group",
            "week_start",
        )
        .agg(
            F.count("*").alias("case_count"),
            F.sum("admitted").alias("admissions"),
            F.countDistinct("patient_hash").alias("unique_patients"),
            F.avg("age").alias("average_age"),
        )
    )

    output_partitions = aggregated.rdd.getNumPartitions()

    aggregated.write.mode("overwrite").parquet(str(output_path))

    elapsed_seconds = time.perf_counter() - start_time

    stored = spark.read.parquet(str(output_path))
    output_rows = stored.count()

    parquet_bytes = sum(
        file.stat().st_size
        for file in output_path.rglob("*")
        if file.is_file()
    )

    if output_rows <= 0 or output_rows > row_count:
        raise RuntimeError(
            f"Unexpected output row count: {output_rows}"
        )

    throughput = row_count / elapsed_seconds if elapsed_seconds > 0 else 0

    result = {
        "dataset_size": row_count,
        "repetition": repetition,
        "input_rows": row_count,
        "output_rows": output_rows,
        "input_partitions": input_partitions,
        "output_partitions": output_partitions,
        "execution_time_seconds": round(elapsed_seconds, 3),
        "throughput_records_per_second": round(throughput, 2),
        "parquet_size_bytes": parquet_bytes,
        "parquet_size_mb": round(parquet_bytes / (1024 ** 2), 3),
        "status": "SUCCESS",
    }

    LOGGER.info(
        "Completed %s records, repetition %s: %.3f seconds, %.2f records/sec",
        f"{row_count:,}",
        repetition,
        elapsed_seconds,
        throughput,
    )

    return result


def build_summary(results):
    summary = []

    sizes = sorted({row["dataset_size"] for row in results})

    for size in sizes:
        group = [
            row for row in results
            if row["dataset_size"] == size
            and row["status"] == "SUCCESS"
        ]

        if not group:
            continue

        times = [row["execution_time_seconds"] for row in group]
        throughputs = [
            row["throughput_records_per_second"] for row in group
        ]

        summary.append({
            "dataset_size": size,
            "successful_repetitions": len(group),
            "median_execution_time_seconds": round(
                statistics.median(times), 3
            ),
            "median_throughput_records_per_second": round(
                statistics.median(throughputs), 2
            ),
            "median_output_rows": round(
                statistics.median(row["output_rows"] for row in group)
            ),
            "median_parquet_size_mb": round(
                statistics.median(row["parquet_size_mb"] for row in group),
                3,
            ),
            "input_partitions": group[0]["input_partitions"],
            "status": "SUCCESS",
        })

    return summary


def save_results(results):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    detail_fields = [
        "dataset_size",
        "repetition",
        "input_rows",
        "output_rows",
        "input_partitions",
        "output_partitions",
        "execution_time_seconds",
        "throughput_records_per_second",
        "parquet_size_bytes",
        "parquet_size_mb",
        "status",
    ]

    with CSV_OUTPUT.open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(results)

    summary = build_summary(results)

    summary_fields = [
        "dataset_size",
        "successful_repetitions",
        "median_execution_time_seconds",
        "median_throughput_records_per_second",
        "median_output_rows",
        "median_parquet_size_mb",
        "input_partitions",
        "status",
    ]

    with SUMMARY_OUTPUT.open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary)

    report = {
        "project": "EpiScope",
        "experiment": "Repeated Spark scalability benchmark",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "results": results,
        "summary": summary,
        "methodology": {
            "dataset_type": "Deterministic synthetic encounters",
            "repetitions_per_size": DEFAULT_REPEATS,
            "summary_statistic": "Median",
            "workload": [
                "Synthetic record generation",
                "Column transformations",
                "Weekly aggregation",
                "Distinct patient counting",
                "Parquet output",
                "Output verification",
            ],
            "notes": [
                "Each dataset size is executed repeatedly.",
                "Median timing and throughput reduce sensitivity to one unusual run.",
                "Execution time includes Spark workload and Parquet writing.",
                "Output verification count is performed after the timed write.",
                "The experiment uses a local Spark environment.",
                "Results are not production-cluster performance measurements.",
            ],
        },
    }

    # Record the actual configured repetition count from completed results.
    report["methodology"]["repetitions_per_size"] = max(
        (row["repetition"] for row in results),
        default=0,
    )

    with JSON_OUTPUT.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=4)

    LOGGER.info("Saved detailed results: %s", CSV_OUTPUT)
    LOGGER.info("Saved summary results: %s", SUMMARY_OUTPUT)
    LOGGER.info("Saved JSON report: %s", JSON_OUTPUT)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    args = parse_args()

    if not args.sizes or any(size <= 0 for size in args.sizes):
        raise ValueError("All dataset sizes must be positive integers.")

    if args.partitions <= 0:
        raise ValueError("Partitions must be greater than zero.")

    if args.repeats <= 0:
        raise ValueError("Repetitions must be greater than zero.")

    sizes = sorted(set(args.sizes))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    spark = None
    results = []

    try:
        spark = create_spark(args.master, args.app_name)

        LOGGER.info("Spark version: %s", spark.version)
        LOGGER.info("Spark master: %s", args.master)
        LOGGER.info("Python executable: %s", sys.executable)
        LOGGER.info("Partitions: %s", args.partitions)
        LOGGER.info("Repetitions per size: %s", args.repeats)

        LOGGER.info("Running Spark warm-up.")
        generate_encounters(spark, 1000, args.partitions).count()

        for size in sizes:
            for repetition in range(1, args.repeats + 1):
                result = run_benchmark(
                    spark,
                    size,
                    args.partitions,
                    repetition,
                )

                results.append(result)
                save_results(results)

        summary = build_summary(results)

        print("\n" + "=" * 90)
        print("EPISCOPE REPEATED SCALABILITY RESULTS — MEDIAN")
        print("=" * 90)

        for row in summary:
            print(
                f"Records: {row['dataset_size']:>10,} | "
                f"Median time: {row['median_execution_time_seconds']:>8.3f}s | "
                f"Median throughput: "
                f"{row['median_throughput_records_per_second']:>12,.2f} records/s"
            )

        print("=" * 90)
        print(f"Detailed CSV: {CSV_OUTPUT}")
        print(f"Summary CSV:  {SUMMARY_OUTPUT}")
        print(f"JSON report:  {JSON_OUTPUT}")

    finally:
        if spark is not None:
            spark.stop()

        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)


if __name__ == "__main__":
    main()
