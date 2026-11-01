
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import uuid

from pyspark.sql import SparkSession
from pyspark.sql.functions import lit


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "data" / "raw" / "generated" / "encounters.csv"

OUTPUT = ROOT / "data" / "lake" / "bronze" / "encounters"

REPORT_DIR = ROOT / "data" / "reports" / "bronze_ingestion"


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def calculate_checksum(file_path: Path) -> str:
    """Calculate SHA-256 checksum without loading the whole file."""
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def find_existing_batch(checksum: str):
    """Return an existing successful batch with the same checksum."""
    if not REPORT_DIR.exists():
        return None

    for report_file in REPORT_DIR.glob("*.json"):
        try:
            report = json.loads(
                report_file.read_text(encoding="utf-8")
            )

            if (
                report.get("status") == "success"
                and report.get("file_checksum_sha256") == checksum
            ):
                return report

        except (json.JSONDecodeError, OSError):
            continue

    return None


def write_report(report: dict, batch_id: str):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / f"{batch_id}.json"

    report_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8"
    )

    return report_path


# --------------------------------------------------
# VALIDATE INPUT
# --------------------------------------------------

if not INPUT.is_file():
    raise FileNotFoundError(f"Input file not found: {INPUT}")

REPORT_DIR.mkdir(parents=True, exist_ok=True)

batch_id = str(uuid.uuid4())
ingestion_time = datetime.now(timezone.utc).isoformat()

file_checksum = calculate_checksum(INPUT)

# --------------------------------------------------
# IDEMPOTENCY CHECK
# --------------------------------------------------

existing_batch = find_existing_batch(file_checksum)

if existing_batch:
    print("Ingestion skipped: this exact file was already loaded.")
    print("Existing batch ID:", existing_batch["batch_id"])
    print("Checksum:", file_checksum)

else:

    spark = (
        SparkSession.builder
        .appName("EpiScope-Bronze-Ingestion")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("WARN")

    try:
        # --------------------------------------------------
        # READ SOURCE WITHOUT TYPE CONVERSION
        # --------------------------------------------------

        df = (
            spark.read
            .option("header", True)
            .option("inferSchema", False)
            .option("mode", "FAILFAST")
            .csv(str(INPUT))
        )

        rows_read = df.count()

        # --------------------------------------------------
        # ADD BRONZE METADATA
        # --------------------------------------------------

        df = (
            df
            .withColumn("batch_id", lit(batch_id))
            .withColumn("ingestion_time_utc", lit(ingestion_time))
            .withColumn("source_file", lit(INPUT.name))
            .withColumn("file_checksum_sha256", lit(file_checksum))
        )

        # --------------------------------------------------
        # WRITE BRONZE PARQUET
        # --------------------------------------------------

        (
            df.write
            .mode("append")
            .partitionBy("batch_id")
            .parquet(str(OUTPUT))
        )

        # --------------------------------------------------
        # INGESTION REPORT
        # --------------------------------------------------

        report = {
            "batch_id": batch_id,
            "source": str(INPUT.relative_to(ROOT)),
            "output": str(OUTPUT.relative_to(ROOT)),
            "source_file": INPUT.name,
            "file_checksum_sha256": file_checksum,
            "rows_read": rows_read,
            "rows_written": rows_read,
            "ingestion_time_utc": ingestion_time,
            "format": "parquet",
            "status": "success"
        }

        report_path = write_report(report, batch_id)

        print("\nBronze ingestion successful")
        print("Batch ID:", batch_id)
        print("Rows read:", rows_read)
        print("Rows written:", rows_read)
        print("SHA-256:", file_checksum)
        print("Parquet output:", OUTPUT)
        print("Report:", report_path)

    except Exception as error:

        failure_report = {
            "batch_id": batch_id,
            "source": str(INPUT.relative_to(ROOT)),
            "file_checksum_sha256": file_checksum,
            "ingestion_time_utc": ingestion_time,
            "status": "failed",
            "error": str(error)
        }

        report_path = write_report(failure_report, batch_id)

        print("Bronze ingestion failed.")
        print("Failure report:", report_path)

        raise

    finally:
        spark.stop()
