from pathlib import Path
import pandas as pd
import json

ROOT = Path.cwd()
OUT = ROOT / "data" / "reports" / "report_metrics"
OUT.mkdir(parents=True, exist_ok=True)

metrics = []
inventory = []
errors = []

folders = [
    ROOT / "analytics",
    ROOT / "data" / "reports",
    ROOT / "data" / "weather",
    ROOT / "data" / "raw",
    ROOT / "data" / "reference",
]

for folder in folders:
    if not folder.exists():
        continue

    for path in folder.rglob("*.csv"):
        if OUT in path.parents:
            continue

        try:
            df = pd.read_csv(path)

            inventory.append({
                "file": str(path.relative_to(ROOT)),
                "rows": len(df),
                "columns": len(df.columns),
                "column_names": " | ".join(map(str, df.columns))
            })

            for col in df.columns:
                numeric = pd.to_numeric(df[col], errors="coerce")

                if numeric.notna().any():
                    metrics.append({
                        "category": "CSV numerical summary",
                        "measure": f"{path.name}: {col}",
                        "observed_value": numeric.sum(),
                        "source": str(path.relative_to(ROOT))
                    })

            # Disease-wise totals from the weekly summary.
            if path.name == "outbreak_weekly_summary.csv":
                disease_col = next(
                    (c for c in df.columns
                     if c.lower() in ["disease_group", "disease"]),
                    None
                )
                case_col = next(
                    (c for c in df.columns
                     if c.lower() in ["case_count", "cases"]),
                    None
                )

                if disease_col and case_col:
                    disease_totals = (
                        df.groupby(disease_col)[case_col]
                        .sum()
                        .reset_index()
                    )
                    disease_totals.to_csv(
                        OUT / "disease_wise_totals.csv",
                        index=False
                    )

        except Exception as e:
            errors.append({
                "file": str(path),
                "error": str(e)
            })

# Inspect pipeline JSON reports.
for path in (ROOT / "data" / "reports").rglob("*.json"):
    if OUT in path.parents:
        continue

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        metrics.append({
            "category": "Pipeline JSON report",
            "measure": path.name,
            "observed_value": json.dumps(data),
            "source": str(path.relative_to(ROOT))
        })
    except Exception as e:
        errors.append({"file": str(path), "error": str(e)})

pd.DataFrame(metrics).to_csv(
    OUT / "all_report_metrics.csv", index=False
)

pd.DataFrame(inventory).to_csv(
    OUT / "all_csv_file_inventory.csv", index=False
)

pd.DataFrame(errors).to_csv(
    OUT / "extraction_errors.csv", index=False
)

print("\n========== EXTRACTION COMPLETE ==========")
print("CSV files inspected:", len(inventory))
print("Metrics collected:", len(metrics))
print("Errors:", len(errors))
print("Output folder:", OUT)
