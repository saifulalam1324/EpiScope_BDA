from pathlib import Path
from datetime import datetime, timezone
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

LOG_DIR = ROOT / "logs" / "pipeline_runs"

STAGES = [
    ("Bronze ingestion", "pipeline/bronze/ingest.py"),
    ("Silver cleaning", "pipeline/silver/clean.py"),
    ("District enrichment", "pipeline/silver/add_district_names.py"),
    ("Gold aggregation", "pipeline/gold/aggregate.py"),
    ("EARS detection", "analytics/detection/ears_alerts.py"),
    ("C2 detection", "analytics/detection/ears_c2.py"),
]


def run_stage(name: str, script: str, log_file) -> int:

    script_path = ROOT / script

    if not script_path.is_file():
        raise FileNotFoundError(
            f"Pipeline script not found: {script_path}"
        )

    command = [
        sys.executable,
        "-u",
        str(script_path),
    ]

    message = f"\n{'=' * 70}\nSTARTING: {name}\nSCRIPT: {script}\n{'=' * 70}\n"

    print(message, flush=True)
    log_file.write(message)
    log_file.flush()

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    for line in process.stdout:
        print(line, end="", flush=True)
        log_file.write(line)

    return_code = process.wait()

    status = (
        "SUCCESS"
        if return_code == 0
        else f"FAILED (exit code {return_code})"
    )

    message = f"\nFINISHED: {name} — {status}\n"

    print(message, flush=True)
    log_file.write(message)
    log_file.flush()

    return return_code


def main() -> int:

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_id = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S_%fZ")

    log_path = LOG_DIR / f"pipeline_{run_id}.log"

    started_at = datetime.now(timezone.utc)

    print("EpiScope pipeline started.")
    print("Python:", sys.executable)
    print("Project:", ROOT)
    print("Log:", log_path)

    results = []

    with log_path.open(
        "w",
        encoding="utf-8",
    ) as log_file:

        log_file.write(
            f"EpiScope pipeline run: {run_id}\n"
            f"Started UTC: {started_at.isoformat()}\n"
            f"Python: {sys.executable}\n\n"
        )

        for name, script in STAGES:

            try:
                return_code = run_stage(
                    name,
                    script,
                    log_file,
                )

            except Exception as error:
                return_code = 1

                message = (
                    f"\nERROR in {name}: {error}\n"
                )

                print(message, flush=True)
                log_file.write(message)
                log_file.flush()

            results.append(
                (name, return_code)
            )

            if return_code != 0:
                print(
                    "\nPipeline stopped because a stage failed.",
                    flush=True,
                )
                break

        finished_at = datetime.now(timezone.utc)

        log_file.write(
            f"\nStarted UTC: {started_at.isoformat()}\n"
            f"Finished UTC: {finished_at.isoformat()}\n"
        )

        log_file.write("\nPIPELINE SUMMARY\n")

        for name, code in results:
            log_file.write(
                f"{name}: {'SUCCESS' if code == 0 else 'FAILED'}\n"
            )

    failed = any(
        code != 0
        for _, code in results
    )

    print("\nPIPELINE SUMMARY")

    for name, code in results:
        print(
            f"{name}: {'SUCCESS' if code == 0 else 'FAILED'}"
        )

    print("Log saved:", log_path)

    if failed:
        return 1

    print("\nEpiScope pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
