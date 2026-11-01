
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


# ---------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]

GOLD_PATH = ROOT / "data" / "lake" / "gold" / "weekly_district_disease"
WEATHER_PATH = ROOT / "data" / "weather" / "weather_weekly.csv"

OUTPUT_DIR = ROOT / "analytics" / "forecast"

DETAIL_PATH = OUTPUT_DIR / "backtest_details.csv"
SUMMARY_PATH = OUTPUT_DIR / "backtest_summary.csv"
DISEASE_PATH = OUTPUT_DIR / "backtest_by_disease.csv"
DISTRICT_PATH = OUTPUT_DIR / "backtest_by_district.csv"

FORECAST_HORIZON = 4
MIN_TRAINING_ROWS = 20
RANDOM_STATE = 42

FEATURE_COLUMNS = [
    "cases_lag_1",
    "cases_lag_2",
    "cases_lag_3",
    "cases_lag_4",
    "temperature_mean_c",
    "precipitation_mm",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.backtesting")


# ---------------------------------------------------------
# Atomic CSV output
# ---------------------------------------------------------

def save_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="backtest_",
            dir=path.parent,
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temporary_file:

            temporary_path = Path(temporary_file.name)

            frame.to_csv(
                temporary_file,
                index=False,
            )

        os.replace(
            temporary_path,
            path,
        )

    finally:
        if (
            temporary_path is not None
            and temporary_path.exists()
        ):
            temporary_path.unlink()

    logger.info("Saved: %s", path)


# ---------------------------------------------------------
# Load and prepare historical data
# ---------------------------------------------------------

def load_data() -> pd.DataFrame:

    if not GOLD_PATH.exists():
        raise FileNotFoundError(
            f"Gold dataset not found: {GOLD_PATH}"
        )

    if not WEATHER_PATH.exists():
        raise FileNotFoundError(
            f"Weekly weather file not found: {WEATHER_PATH}"
        )

    logger.info("Reading Gold Parquet data...")

    gold = pd.read_parquet(GOLD_PATH)

    required_gold = {
        "district_id",
        "district_name",
        "disease_group",
        "week_start",
        "case_count",
    }

    missing = required_gold - set(gold.columns)

    if missing:
        raise ValueError(
            f"Gold data missing columns: {sorted(missing)}"
        )

    gold = gold[
        [
            "district_id",
            "district_name",
            "disease_group",
            "week_start",
            "case_count",
        ]
    ].copy()

    gold = gold.rename(
        columns={
            "disease_group": "disease",
            "case_count": "cases",
        }
    )

    gold["week_start"] = pd.to_datetime(
        gold["week_start"],
        errors="coerce",
    )

    gold["cases"] = pd.to_numeric(
        gold["cases"],
        errors="coerce",
    )

    gold = gold.dropna(
        subset=[
            "district_id",
            "district_name",
            "disease",
            "week_start",
            "cases",
        ]
    )

    gold = gold[gold["cases"] >= 0].copy()

    gold["week_start"] = (
        gold["week_start"].dt.to_period("W-SUN").dt.start_time
    )

    gold = (
        gold.groupby(
            [
                "district_id",
                "district_name",
                "disease",
                "week_start",
            ],
            as_index=False,
        )["cases"]
        .sum()
    )

    logger.info("Gold records: %s", f"{len(gold):,}")

    logger.info("Reading weekly weather data...")

    weather = pd.read_csv(WEATHER_PATH)

    required_weather = {
        "district_name",
        "week_start",
        "temperature_mean_c",
        "precipitation_mm",
    }

    missing = required_weather - set(weather.columns)

    if missing:
        raise ValueError(
            f"Weather data missing columns: {sorted(missing)}"
        )

    weather["district_name"] = (
        weather["district_name"].astype("string").str.strip()
    )

    weather["week_start"] = pd.to_datetime(
        weather["week_start"],
        errors="coerce",
    )

    for column in [
        "temperature_mean_c",
        "precipitation_mm",
    ]:
        weather[column] = pd.to_numeric(
            weather[column],
            errors="coerce",
        )

    weather = weather.dropna(
        subset=[
            "district_name",
            "week_start",
            "temperature_mean_c",
            "precipitation_mm",
        ]
    )

    weather["week_start"] = (
        weather["week_start"].dt.to_period("W-SUN").dt.start_time
    )

    weather = (
        weather.groupby(
            ["district_name", "week_start"],
            as_index=False,
        )
        .agg(
            temperature_mean_c=(
                "temperature_mean_c",
                "mean",
            ),
            precipitation_mm=(
                "precipitation_mm",
                "mean",
            ),
        )
    )

    logger.info("Weather records: %s", f"{len(weather):,}")

    logger.info("Joining cases with observed weather...")

    data = gold.merge(
        weather,
        on=["district_name", "week_start"],
        how="inner",
        validate="many_to_one",
    )

    if data.empty:
        raise ValueError(
            "No Gold records matched the weather dataset. "
            "Check district names and week_start values."
        )

    data = data.sort_values(
        [
            "district_id",
            "disease",
            "week_start",
        ]
    ).reset_index(drop=True)

    logger.info("Matched historical records: %s", f"{len(data):,}")
    logger.info("Districts: %s", data["district_id"].nunique())
    logger.info("Diseases: %s", data["disease"].nunique())

    return data


# ---------------------------------------------------------
# Build historical training features
# ---------------------------------------------------------

def build_training_data(
    history: pd.DataFrame,
) -> pd.DataFrame:

    history = history.sort_values(
        "week_start"
    ).copy()

    for lag in range(1, 5):
        history[f"cases_lag_{lag}"] = (
            history["cases"].shift(lag)
        )

    training = history.dropna(
        subset=FEATURE_COLUMNS + ["cases"]
    ).copy()

    return training


# ---------------------------------------------------------
# Historical four-week backtest
# ---------------------------------------------------------

def backtest_group(
    group: pd.DataFrame,
) -> list[dict]:

    group = (
        group.sort_values("week_start")
        .drop_duplicates(
            subset=["week_start"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    if len(group) <= FORECAST_HORIZON + 4:
        return []

    # Reserve the final four observed weeks for testing.
    # All earlier observations are used for training.
    training_history = group.iloc[:-FORECAST_HORIZON].copy()

    test_data = group.iloc[-FORECAST_HORIZON:].copy()

    training = build_training_data(training_history)

    if len(training) < MIN_TRAINING_ROWS:
        return []

    if training["cases"].nunique() <= 1:
        model = None
        model_name = "Persistence"
    else:
        model = RandomForestRegressor(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=2,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )

        model.fit(
            training[FEATURE_COLUMNS],
            training["cases"],
        )

        model_name = "RandomForestRegressor"

    district_id = str(group.iloc[0]["district_id"])
    district_name = str(group.iloc[0]["district_name"])
    disease = str(group.iloc[0]["disease"])

    history_cases = (
        training_history["cases"]
        .astype(float)
        .tolist()
    )

    persistence_prediction = max(
        0.0,
        history_cases[-1],
    )

    results = []

    for horizon in range(1, FORECAST_HORIZON + 1):

        actual_row = test_data.iloc[horizon - 1]

        actual_cases = float(actual_row["cases"])

        lag_values = history_cases[-4:]

        if len(lag_values) < 4:
            lag_values = (
                [0.0] * (4 - len(lag_values))
                + lag_values
            )

        features = {
            "cases_lag_1": lag_values[-1],
            "cases_lag_2": lag_values[-2],
            "cases_lag_3": lag_values[-3],
            "cases_lag_4": lag_values[-4],
            "temperature_mean_c": float(
                actual_row["temperature_mean_c"]
            ),
            "precipitation_mm": float(
                actual_row["precipitation_mm"]
            ),
        }

        if model is not None:
            feature_frame = pd.DataFrame(
                [features],
                columns=FEATURE_COLUMNS,
            )

            prediction = float(
                model.predict(feature_frame)[0]
            )
        else:
            prediction = persistence_prediction

        prediction = max(0.0, prediction)

        results.append(
            {
                "district_id": district_id,
                "district_name": district_name,
                "disease": disease,
                "week_start": actual_row[
                    "week_start"
                ].date().isoformat(),
                "forecast_horizon_weeks": horizon,
                "actual_cases": actual_cases,
                "predicted_cases": prediction,
                "persistence_prediction": (
                    persistence_prediction
                ),
                "model_name": model_name,
                "training_rows": len(training),
                "temperature_mean_c": features[
                    "temperature_mean_c"
                ],
                "precipitation_mm": features[
                    "precipitation_mm"
                ],
                "weather_method": "observed_test_week_weather",
            }
        )

        # Recursive prediction: later horizons use earlier predictions.
        history_cases.append(prediction)

    return results


# ---------------------------------------------------------
# Evaluation metrics
# ---------------------------------------------------------

def calculate_metrics(
    frame: pd.DataFrame,
    actual_column: str,
    predicted_column: str,
) -> dict:

    if frame.empty:
        return {
            "observations": 0,
            "mae": np.nan,
            "rmse": np.nan,
            "mape_percent": np.nan,
            "wape_percent": np.nan,
        }

    actual = frame[actual_column].astype(float).to_numpy()
    predicted = frame[predicted_column].astype(float).to_numpy()

    errors = predicted - actual

    mae = mean_absolute_error(
        actual,
        predicted,
    )

    rmse = float(
        np.sqrt(mean_squared_error(actual, predicted))
    )

    nonzero_mask = actual != 0

    if nonzero_mask.any():
        mape = float(
            np.mean(
                np.abs(
                    errors[nonzero_mask]
                    / actual[nonzero_mask]
                )
            ) * 100
        )
    else:
        mape = np.nan

    total_actual = float(np.sum(np.abs(actual)))

    if total_actual > 0:
        wape = float(
            np.sum(np.abs(errors))
            / total_actual
            * 100
        )
    else:
        wape = np.nan

    return {
        "observations": len(frame),
        "mae": float(mae),
        "rmse": rmse,
        "mape_percent": mape,
        "wape_percent": wape,
    }


# ---------------------------------------------------------
# Build evaluation reports
# ---------------------------------------------------------

def build_reports(
    details: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    if details.empty:
        raise ValueError(
            "No backtest results were generated. "
            "Check historical coverage and minimum training rows."
        )

    summary_rows = []

    for horizon, subset in details.groupby(
        "forecast_horizon_weeks"
    ):

        for model_name, model_data in subset.groupby(
            "model_name"
        ):

            metrics = calculate_metrics(
                model_data,
                "actual_cases",
                "predicted_cases",
            )

            summary_rows.append(
                {
                    "forecast_horizon_weeks": int(horizon),
                    "model_name": model_name,
                    **metrics,
                }
            )

        # Evaluate persistence separately as the baseline.
        baseline_metrics = calculate_metrics(
            subset,
            "actual_cases",
            "persistence_prediction",
        )

        summary_rows.append(
            {
                "forecast_horizon_weeks": int(horizon),
                "model_name": "PersistenceBaseline",
                **baseline_metrics,
            }
        )

    summary = pd.DataFrame(summary_rows)

    # Disease-level evaluation.
    disease_rows = []

    for disease, subset in details.groupby("disease"):

        for model_name, prediction_column in [
            ("RandomForestOrFallback", "predicted_cases"),
            ("PersistenceBaseline", "persistence_prediction"),
        ]:

            metrics = calculate_metrics(
                subset,
                "actual_cases",
                prediction_column,
            )

            disease_rows.append(
                {
                    "disease": disease,
                    "model_name": model_name,
                    **metrics,
                }
            )

    by_disease = pd.DataFrame(disease_rows)

    # District-level evaluation.
    district_rows = []

    for (district_id, district_name), subset in details.groupby(
        ["district_id", "district_name"]
    ):

        for model_name, prediction_column in [
            ("RandomForestOrFallback", "predicted_cases"),
            ("PersistenceBaseline", "persistence_prediction"),
        ]:

            metrics = calculate_metrics(
                subset,
                "actual_cases",
                prediction_column,
            )

            district_rows.append(
                {
                    "district_id": district_id,
                    "district_name": district_name,
                    "model_name": model_name,
                    **metrics,
                }
            )

    by_district = pd.DataFrame(district_rows)

    return summary, by_disease, by_district


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    logger.info("Starting EpiScope forecast backtesting...")

    data = load_data()

    grouped = data.groupby(
        ["district_id", "disease"],
        sort=True,
    )

    total_groups = len(grouped)

    logger.info(
        "Backtesting %s district-disease groups...",
        total_groups,
    )

    all_results = []

    for index, (_, group) in enumerate(
        grouped,
        start=1,
    ):

        all_results.extend(
            backtest_group(group)
        )

        if index % 25 == 0 or index == total_groups:
            logger.info(
                "Processed %s/%s groups",
                index,
                total_groups,
            )

    if not all_results:
        raise ValueError(
            "Backtesting produced no results."
        )

    details = pd.DataFrame(all_results)

    summary, by_disease, by_district = build_reports(
        details
    )

    save_csv(details, DETAIL_PATH)
    save_csv(summary, SUMMARY_PATH)
    save_csv(by_disease, DISEASE_PATH)
    save_csv(by_district, DISTRICT_PATH)

    logger.info(
        "Backtest observations: %s",
        f"{len(details):,}",
    )

    logger.info(
        "Backtested districts: %s",
        details["district_id"].nunique(),
    )

    logger.info(
        "Backtested diseases: %s",
        details["disease"].nunique(),
    )

    logger.info("Backtesting completed successfully.")

    print("\nBACKTEST SUMMARY")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
