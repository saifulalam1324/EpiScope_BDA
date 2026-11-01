
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[3]

TRUTH_FILE = ROOT / "data/raw/generated/ground_truth_outbreaks.csv"

GOLD_FILE = ROOT / "data/lake/gold/weekly_district_disease"

OUTPUT_DIR = ROOT / "analytics/detection"

DETECTOR_FILES = {
    "Rolling_Z_Score": OUTPUT_DIR / "ears_alerts.csv",
    "EARS_C2": OUTPUT_DIR / "ears_c2_alerts.csv",
}

EVENT_OUTPUT = OUTPUT_DIR / "evaluation_results.csv"
ALERT_OUTPUT = OUTPUT_DIR / "alert_evaluation.csv"
METRICS_OUTPUT = OUTPUT_DIR / "evaluation_metrics.csv"
FALSE_ALERT_OUTPUT = OUTPUT_DIR / "false_alerts_by_district_year.csv"
TRADEOFF_OUTPUT = OUTPUT_DIR / "threshold_tradeoff.csv"

THRESHOLDS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]


# ============================================================
# VALIDATION
# ============================================================

def require_file(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Required input not found: {path}"
        )


def require_columns(data, required, label):
    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"{label} is missing columns: {sorted(missing)}"
        )


def normalize_ids(data):
    data = data.copy()

    for column in ["district_id", "disease"]:
        data[column] = (
            data[column]
            .astype("string")
            .str.strip()
            .str.lower()
        )

    return data


def parse_boolean(series):
    values = (
        series.astype(str)
        .str.strip()
        .str.lower()
    )

    valid = values.isin(
        ["true", "false", "1", "0", "yes", "no"]
    )

    if not valid.all():
        raise ValueError("Invalid boolean values in alert column.")

    return values.isin(["true", "1", "yes"])


# ============================================================
# LOAD INPUTS
# ============================================================

def load_ground_truth():
    require_file(TRUTH_FILE)

    truth = pd.read_csv(TRUTH_FILE)

    require_columns(
        truth,
        {
            "outbreak_id",
            "district_id",
            "disease",
            "start_date",
            "end_date",
        },
        "Ground truth",
    )

    if truth.empty:
        raise ValueError("Ground-truth dataset is empty.")

    truth = normalize_ids(truth)

    truth["start_date"] = pd.to_datetime(
        truth["start_date"], errors="coerce"
    )

    truth["end_date"] = pd.to_datetime(
        truth["end_date"], errors="coerce"
    )

    if truth[["start_date", "end_date"]].isna().any().any():
        raise ValueError("Invalid outbreak dates.")

    if (truth["end_date"] < truth["start_date"]).any():
        raise ValueError("An outbreak ends before it starts.")

    if truth["outbreak_id"].duplicated().any():
        raise ValueError("Duplicate outbreak IDs found.")

    return truth


def load_gold():
    require_file(GOLD_FILE)

    gold = pd.read_parquet(GOLD_FILE)

    # Match the actual EpiScope Gold schema.
    require_columns(
        gold,
        {
            "district_id",
            "disease_group",
            "week_start",
            "case_count",
        },
        "Gold data",
    )

    gold = gold.rename(
        columns={
            "disease_group": "disease",
            "case_count": "cases",
        }
    )

    gold = normalize_ids(gold)

    gold["week_start"] = pd.to_datetime(
        gold["week_start"], errors="coerce"
    )

    gold["cases"] = pd.to_numeric(
        gold["cases"], errors="coerce"
    )

    if gold["week_start"].isna().any():
        raise ValueError("Invalid Gold week_start values.")

    if gold["cases"].isna().any() or (gold["cases"] < 0).any():
        raise ValueError("Invalid Gold case counts.")

    if not np.allclose(gold["cases"], gold["cases"].round()):
        raise ValueError("Gold case counts must be whole numbers.")

    gold["cases"] = gold["cases"].astype("int64")

    return gold


def load_alerts(path):
    require_file(path)

    alerts = pd.read_csv(path)

    require_columns(
        alerts,
        {
            "district_id",
            "disease",
            "week_start",
            "alert",
        },
        f"Alert file {path.name}",
    )

    alerts = normalize_ids(alerts)

    alerts["week_start"] = pd.to_datetime(
        alerts["week_start"], errors="coerce"
    )

    if alerts["week_start"].isna().any():
        raise ValueError(
            f"Invalid dates in {path.name}."
        )

    alerts["alert"] = parse_boolean(alerts["alert"])

    duplicate_mask = alerts.duplicated(
        ["district_id", "disease", "week_start"],
        keep=False,
    )

    if duplicate_mask.any():
        raise ValueError(
            f"Duplicate district-disease-week rows in {path.name}."
        )

    return alerts


# ============================================================
# EVENT AND ALERT MATCHING
# ============================================================

def week_overlaps_event(week_start, start_date, end_date):
    week_end = week_start + pd.Timedelta(days=6)

    return (
        week_start <= end_date
        and week_end >= start_date
    )


def evaluate_detector(name, alerts, truth, gold):
    alert_rows = alerts.loc[alerts["alert"]].copy()

    alert_rows = alert_rows.reset_index(drop=True)

    alert_rows["alert_id"] = np.arange(
        len(alert_rows)
    )

    event_groups = {
        key: group
        for key, group in truth.groupby(
            ["district_id", "disease"]
        )
    }

    # --------------------------------------------------------
    # Match each alert to at most one outbreak.
    # --------------------------------------------------------

    alert_matches = []

    for _, alert in alert_rows.iterrows():

        key = (
            alert["district_id"],
            alert["disease"],
        )

        candidates = event_groups.get(key)

        matched_event = None

        if candidates is not None:

            overlapping = candidates.loc[
                (
                    candidates["start_date"]
                    <= alert["week_start"] + pd.Timedelta(days=6)
                )
                & (
                    candidates["end_date"]
                    >= alert["week_start"]
                )
            ]

            if not overlapping.empty:

                distances = (
                    overlapping["start_date"]
                    - alert["week_start"]
                ).abs()

                matched_event = overlapping.loc[
                    distances.idxmin()
                ]

        alert_matches.append({
            "detector": name,
            "alert_id": int(alert["alert_id"]),
            "district_id": alert["district_id"],
            "disease": alert["disease"],
            "week_start": alert["week_start"],
            "matched_outbreak_id": (
                matched_event["outbreak_id"]
                if matched_event is not None
                else ""
            ),
            "is_true_alert": matched_event is not None,
        })

    alert_evaluation = pd.DataFrame(alert_matches)

    if alert_evaluation.empty:
        alert_evaluation = pd.DataFrame(
            columns=[
                "detector",
                "alert_id",
                "district_id",
                "disease",
                "week_start",
                "matched_outbreak_id",
                "is_true_alert",
            ]
        )

    # --------------------------------------------------------
    # Evaluate every outbreak.
    # --------------------------------------------------------

    event_results = []

    for _, event in truth.iterrows():

        candidates = alert_rows.loc[
            (alert_rows["district_id"] == event["district_id"])
            & (alert_rows["disease"] == event["disease"])
        ]

        overlapping = candidates.loc[
            candidates["week_start"].apply(
                lambda week: week_overlaps_event(
                    week,
                    event["start_date"],
                    event["end_date"],
                )
            )
        ]

        detected = not overlapping.empty

        if detected:
            first_alert_week = overlapping["week_start"].min()

            delay_days = max(
                0,
                (first_alert_week - event["start_date"]).days,
            )
        else:
            first_alert_week = pd.NaT
            delay_days = np.nan

        event_results.append({
            "detector": name,
            "outbreak_id": event["outbreak_id"],
            "district_id": event["district_id"],
            "disease": event["disease"],
            "start_date": event["start_date"],
            "end_date": event["end_date"],
            "detected": detected,
            "first_alert_week": first_alert_week,
            "delay_days": delay_days,
        })

    evaluation = pd.DataFrame(event_results)

    # --------------------------------------------------------
    # Metrics.
    # --------------------------------------------------------

    total_events = len(evaluation)

    detected_events = int(
        evaluation["detected"].sum()
    )

    total_alerts = len(alert_evaluation)

    true_alerts = int(
        alert_evaluation["is_true_alert"].sum()
    )

    false_alerts = total_alerts - true_alerts

    sensitivity = (
        detected_events / total_events
        if total_events else np.nan
    )

    precision = (
        true_alerts / total_alerts
        if total_alerts else np.nan
    )

    median_delay = (
        float(
            evaluation.loc[
                evaluation["detected"],
                "delay_days",
            ].median()
        )
        if detected_events else np.nan
    )

    # Use all district-years represented in Gold,
    # including district-years with zero alerts.
    district_years = gold[
        ["district_id", "week_start"]
    ].copy()

    district_years["year"] = (
        district_years["week_start"].dt.year
    )

    district_years = district_years[
        ["district_id", "year"]
    ].drop_duplicates()

    alert_evaluation["year"] = (
        alert_evaluation["week_start"].dt.year
    )

    false_counts = (
        alert_evaluation.loc[
            ~alert_evaluation["is_true_alert"]
        ]
        .groupby(["district_id", "year"])
        .size()
        .rename("false_alerts")
        .reset_index()
    )

    false_by_district_year = district_years.merge(
        false_counts,
        on=["district_id", "year"],
        how="left",
    )

    false_by_district_year["false_alerts"] = (
        false_by_district_year["false_alerts"]
        .fillna(0)
        .astype(int)
    )

    false_by_district_year["detector"] = name

    mean_false_alerts = (
        float(false_by_district_year["false_alerts"].mean())
        if not false_by_district_year.empty
        else np.nan
    )

    metrics = {
        "detector": name,
        "ground_truth_events": total_events,
        "detected_events": detected_events,
        "event_sensitivity": sensitivity,
        "total_alert_weeks": total_alerts,
        "true_alert_weeks": true_alerts,
        "false_alert_weeks": false_alerts,
        "alert_precision": precision,
        "median_detection_delay_days": median_delay,
        "mean_false_alerts_per_district_year": mean_false_alerts,
    }

    return (
        evaluation,
        alert_evaluation,
        false_by_district_year,
        metrics,
    )


# ============================================================
# THRESHOLD TRADE-OFF CURVE
# ============================================================

def create_threshold_curve(name, alerts, truth, gold):
    results = []

    if "z_score" not in alerts.columns:
        print(
            f"WARNING: {name} has no z_score column. "
            "Threshold curve skipped."
        )
        return results

    scores = pd.to_numeric(
        alerts["z_score"],
        errors="coerce",
    )

    for threshold in THRESHOLDS:

        threshold_alerts = alerts.copy()

        threshold_alerts["alert"] = (
            scores.notna()
            & (scores >= threshold)
        )

        (
            event_results,
            alert_results,
            false_results,
            metrics,
        ) = evaluate_detector(
            name,
            threshold_alerts,
            truth,
            gold,
        )

        results.append({
            "detector": name,
            "z_score_threshold": threshold,
            "event_sensitivity": metrics["event_sensitivity"],
            "alert_precision": metrics["alert_precision"],
            "false_alert_weeks": metrics["false_alert_weeks"],
            "median_detection_delay_days": (
                metrics["median_detection_delay_days"]
            ),
            "mean_false_alerts_per_district_year": (
                metrics["mean_false_alerts_per_district_year"]
            ),
        })

    return results


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print("EPISCOPE - OUTBREAK DETECTION EVALUATION")
    print("=" * 65)

    truth = load_ground_truth()
    gold = load_gold()

    print("Ground-truth events:", len(truth))
    print("Gold weekly records:", len(gold))

    all_event_results = []
    all_alert_results = []
    all_false_results = []
    all_metrics = []
    all_tradeoff_results = []

    for detector_name, detector_path in DETECTOR_FILES.items():

        if not detector_path.exists():
            print(
                f"\nSkipping {detector_name}: "
                f"{detector_path.name} not found."
            )
            continue

        print(f"\nEvaluating {detector_name}...")

        alerts = load_alerts(detector_path)

        (
            event_results,
            alert_results,
            false_results,
            metrics,
        ) = evaluate_detector(
            detector_name,
            alerts,
            truth,
            gold,
        )

        all_event_results.append(event_results)
        all_alert_results.append(alert_results)
        all_false_results.append(false_results)
        all_metrics.append(metrics)

        all_tradeoff_results.extend(
            create_threshold_curve(
                detector_name,
                alerts,
                truth,
                gold,
            )
        )

        print("Events:", metrics["ground_truth_events"])
        print("Detected:", metrics["detected_events"])

        print(
            "Sensitivity:",
            f"{metrics['event_sensitivity']:.1%}"
            if not np.isnan(metrics["event_sensitivity"])
            else "N/A",
        )

        print("Alert weeks:", metrics["total_alert_weeks"])
        print("False alert weeks:", metrics["false_alert_weeks"])

        print(
            "Median detection delay:",
            metrics["median_detection_delay_days"],
        )

        print(
            "Mean false alerts per district-year:",
            metrics["mean_false_alerts_per_district_year"],
        )

    if not all_metrics:
        raise FileNotFoundError(
            "No detector files found. Run ears_alerts.py "
            "and/or ears_c2.py first."
        )

    # --------------------------------------------------------
    # SAVE OUTPUTS
    # --------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pd.concat(
        all_event_results,
        ignore_index=True,
    ).to_csv(EVENT_OUTPUT, index=False)

    pd.concat(
        all_alert_results,
        ignore_index=True,
    ).to_csv(ALERT_OUTPUT, index=False)

    pd.DataFrame(all_metrics).to_csv(
        METRICS_OUTPUT,
        index=False,
    )

    pd.concat(
        all_false_results,
        ignore_index=True,
    ).to_csv(FALSE_ALERT_OUTPUT, index=False)

    pd.DataFrame(all_tradeoff_results).to_csv(
        TRADEOFF_OUTPUT,
        index=False,
    )

    # --------------------------------------------------------
    # FINAL SUMMARY
    # --------------------------------------------------------

    print("\n" + "=" * 65)
    print("EVALUATION COMPLETED")
    print("=" * 65)

    print("\nDetector comparison:")

    print(
        pd.DataFrame(all_metrics)[
            [
                "detector",
                "event_sensitivity",
                "alert_precision",
                "median_detection_delay_days",
                "mean_false_alerts_per_district_year",
            ]
        ].to_string(index=False)
    )

    print("\nSaved files:")

    for path in [
        EVENT_OUTPUT,
        ALERT_OUTPUT,
        METRICS_OUTPUT,
        FALSE_ALERT_OUTPUT,
        TRADEOFF_OUTPUT,
    ]:
        print(path)


if __name__ == "__main__":
    main()
