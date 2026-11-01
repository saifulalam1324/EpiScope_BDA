
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from sklearn.ensemble import RandomForestRegressor


# ---------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]

GOLD_PATH = ROOT / "data" / "lake" / "gold" / "weekly_district_disease"
WEATHER_PATH = ROOT / "data" / "weather" / "weather_weekly.csv"
OUTPUT_PATH = ROOT / "analytics" / "forecast" / "forecasts.csv"

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
logger = logging.getLogger("episcope.forecast")


# ---------------------------------------------------------
# Spark setup
# ---------------------------------------------------------

def create_spark() -> SparkSession:
    python_executable = sys.executable

    return (
        SparkSession.builder
        .appName("EpiScopeWeatherAwareForecast")
        .master("local[*]")
        .config("spark.pyspark.python", python_executable)
        .config("spark.pyspark.driver.python", python_executable)
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.execution.arrow.pyspark.enabled", "false")
        .getOrCreate()
    )


# ---------------------------------------------------------
# Input validation and Spark transformations
# ---------------------------------------------------------

def load_and_prepare_data(spark: SparkSession) -> pd.DataFrame:
    if not GOLD_PATH.exists():
        raise FileNotFoundError(
            f"Gold data not found: {GOLD_PATH}"
        )

    if not WEATHER_PATH.exists():
        raise FileNotFoundError(
            f"Weekly weather file not found: {WEATHER_PATH}"
        )

    logger.info("Reading Gold Parquet data...")
    gold = spark.read.parquet(str(GOLD_PATH))

    required_gold = {
        "district_id",
        "district_name",
        "disease_group",
        "week_start",
        "case_count",
    }

    missing_gold = required_gold - set(gold.columns)

    if missing_gold:
        raise ValueError(
            f"Gold data is missing required columns: "
            f"{sorted(missing_gold)}"
        )

    gold = (
        gold.select(
            F.col("district_id").cast("string").alias("district_id"),
            F.col("district_name").cast("string").alias("district_name"),
            F.col("disease_group").cast("string").alias("disease"),
            F.to_date("week_start").alias("week_start"),
            F.col("case_count").cast("double").alias("cases"),
        )
        .filter(
            F.col("district_id").isNotNull()
            & F.col("district_name").isNotNull()
            & F.col("disease").isNotNull()
            & F.col("week_start").isNotNull()
            & F.col("cases").isNotNull()
            & (F.col("cases") >= 0)
        )
        .groupBy(
            "district_id",
            "district_name",
            "disease",
            "week_start",
        )
        .agg(
            F.sum("cases").alias("cases")
        )
    )

    logger.info("Reading weekly weather data...")

    weather = (
        spark.read
        .option("header", "true")
        .option("inferSchema", "true")
        .csv(str(WEATHER_PATH))
    )

    required_weather = {
        "district_name",
        "week_start",
        "temperature_mean_c",
        "precipitation_mm",
    }

    missing_weather = required_weather - set(weather.columns)

    if missing_weather:
        raise ValueError(
            f"Weekly weather data is missing columns: "
            f"{sorted(missing_weather)}"
        )

    weather = (
        weather.select(
            F.trim(F.col("district_name")).alias("district_name"),
            F.to_date("week_start").alias("week_start"),
            F.col("temperature_mean_c").cast("double").alias(
                "temperature_mean_c"
            ),
            F.col("precipitation_mm").cast("double").alias(
                "precipitation_mm"
            ),
        )
        .filter(
            F.col("district_name").isNotNull()
            & F.col("week_start").isNotNull()
            & F.col("temperature_mean_c").isNotNull()
            & F.col("precipitation_mm").isNotNull()
        )
        .groupBy(
            "district_name",
            "week_start",
        )
        .agg(
            F.avg("temperature_mean_c").alias(
                "temperature_mean_c"
            ),
            F.avg("precipitation_mm").alias(
                "precipitation_mm"
            ),
        )
    )

    gold_count = gold.count()
    weather_count = weather.count()

    logger.info("Gold training rows: %s", f"{gold_count:,}")
    logger.info("Weekly weather rows: %s", f"{weather_count:,}")

    if gold_count == 0:
        raise ValueError(
            "Gold dataset contains no valid case records."
        )

    if weather_count == 0:
        raise ValueError(
            "Weekly weather dataset contains no valid records."
        )

    logger.info("Joining Gold cases with weekly weather...")

    joined = gold.join(
        weather,
        on=["district_name", "week_start"],
        how="left",
    )

    missing_weather_count = joined.filter(
        F.col("temperature_mean_c").isNull()
        | F.col("precipitation_mm").isNull()
    ).count()

    if missing_weather_count:
        logger.warning(
            "%s Gold records have no matching weekly weather.",
            f"{missing_weather_count:,}",
        )

    joined = joined.dropna(
        subset=[
            "temperature_mean_c",
            "precipitation_mm",
        ]
    )

    if joined.count() == 0:
        raise ValueError(
            "No Gold records matched the weekly weather dataset. "
            "Check district names and week_start values."
        )

    # Calculate historical case lags using Spark SQL window functions.
    logger.info("Calculating lag features with Spark SQL...")

    history_window = (
        Window
        .partitionBy("district_id", "disease")
        .orderBy("week_start")
    )

    featured = joined

    for lag in range(1, 5):
        featured = featured.withColumn(
            f"cases_lag_{lag}",
            F.lag("cases", lag).over(history_window),
        )

    featured = featured.orderBy(
        "district_id",
        "disease",
        "week_start",
    )

    logger.info(
        "Collecting prepared training rows on the driver..."
    )

    # This uses Spark collect(), not applyInPandas or Python workers.
    records = [
        row.asDict(recursive=True)
        for row in featured.collect()
    ]

    frame = pd.DataFrame(records)

    if frame.empty:
        raise ValueError(
            "No prepared training rows were collected."
        )

    frame["week_start"] = pd.to_datetime(
        frame["week_start"]
    )

    for column in FEATURE_COLUMNS + ["cases"]:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    logger.info("Prepared rows: %s", f"{len(frame):,}")
    logger.info(
        "Districts: %s",
        frame["district_id"].nunique(),
    )
    logger.info(
        "Diseases: %s",
        frame["disease"].nunique(),
    )

    return frame


# ---------------------------------------------------------
# Model training and recursive forecasting
# ---------------------------------------------------------

def make_training_features(
    group: pd.DataFrame,
) -> pd.DataFrame:

    group = group.sort_values(
        "week_start"
    ).copy()

    for lag in range(1, 5):
        group[f"cases_lag_{lag}"] = (
            group["cases"].shift(lag)
        )

    return group.dropna(
        subset=FEATURE_COLUMNS + ["cases"]
    )


def forecast_group(
    group: pd.DataFrame,
) -> list[dict]:

    group = group.sort_values(
        "week_start"
    ).copy()

    group = group.drop_duplicates(
        subset=["week_start"],
        keep="last",
    )

    group = group.reset_index(drop=True)

    if group.empty:
        return []

    district_id = str(
        group.iloc[-1]["district_id"]
    )

    district_name = str(
        group.iloc[-1]["district_name"]
    )

    disease = str(
        group.iloc[-1]["disease"]
    )

    training = make_training_features(group)

    latest = group.iloc[-1]

    last_week = pd.Timestamp(
        latest["week_start"]
    )

    latest_temperature = float(
        latest["temperature_mean_c"]
    )

    latest_precipitation = float(
        latest["precipitation_mm"]
    )

    observed_cases = (
        group["cases"]
        .astype(float)
        .tolist()
    )

    if not observed_cases:
        return []

    persistence_prediction = max(
        0.0,
        observed_cases[-1],
    )

    model = None
    model_name = "Persistence"

    if (
        len(training) >= MIN_TRAINING_ROWS
        and training[FEATURE_COLUMNS].notna().all().all()
        and training["cases"].nunique() > 1
    ):
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

    predictions = []

    # The most recent four observed/predicted case values
    # form the recursive lag inputs.
    case_history = observed_cases[-4:]

    if len(case_history) < 4:
        case_history = (
            [0.0] * (4 - len(case_history))
            + case_history
        )

    for horizon in range(
        1,
        FORECAST_HORIZON + 1,
    ):

        # Use an explicit day unit to avoid NumPy timedelta warnings.
        forecast_week = last_week + pd.DateOffset(
            days=7 * int(horizon)
        )

        features = {
            "cases_lag_1": case_history[-1],
            "cases_lag_2": case_history[-2],
            "cases_lag_3": case_history[-3],
            "cases_lag_4": case_history[-4],
            "temperature_mean_c": latest_temperature,
            "precipitation_mm": latest_precipitation,
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

        prediction = max(
            0.0,
            prediction,
        )

        predictions.append(
            {
                "district_id": district_id,
                "district_name": district_name,
                "disease": disease,
                "week_start": forecast_week.date().isoformat(),
                "forecast_horizon_weeks": horizon,
                "predicted_cases": round(
                    prediction,
                    2,
                ),
                "persistence_baseline": round(
                    persistence_prediction,
                    2,
                ),
                "model_name": model_name,
                "training_rows": int(len(training)),
                "weather_method": (
                    "last_observed_week_carried_forward"
                ),
                "temperature_mean_c": latest_temperature,
                "precipitation_mm": latest_precipitation,
            }
        )

        # Feed this prediction into the next forecast's lag history.
        case_history.append(prediction)

    return predictions


# ---------------------------------------------------------
# Output
# ---------------------------------------------------------

def save_forecasts(
    forecasts: list[dict],
) -> None:

    if not forecasts:
        raise ValueError(
            "No forecasts were generated."
        )

    output = pd.DataFrame(forecasts)

    output = output.sort_values(
        [
            "district_id",
            "disease",
            "week_start",
        ]
    ).reset_index(drop=True)

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="forecasts_",
            dir=OUTPUT_PATH.parent,
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temporary_file:

            temporary_path = Path(
                temporary_file.name
            )

            output.to_csv(
                temporary_file,
                index=False,
            )

        os.replace(
            temporary_path,
            OUTPUT_PATH,
        )

    finally:
        if (
            temporary_path is not None
            and temporary_path.exists()
        ):
            temporary_path.unlink()

    logger.info(
        "Forecast rows: %s",
        f"{len(output):,}",
    )

    logger.info(
        "Districts forecast: %s",
        output["district_id"].nunique(),
    )

    logger.info(
        "Diseases forecast: %s",
        output["disease"].nunique(),
    )

    logger.info(
        "Models used: %s",
        output["model_name"].value_counts().to_dict(),
    )

    logger.info(
        "Saved forecasts: %s",
        OUTPUT_PATH,
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    spark = create_spark()

    try:
        spark.sparkContext.setLogLevel("WARN")

        logger.info(
            "Starting EpiScope forecasting pipeline..."
        )

        training_frame = load_and_prepare_data(
            spark
        )

        forecasts = []

        grouped = training_frame.groupby(
            ["district_id", "disease"],
            sort=True,
        )

        total_groups = len(grouped)

        logger.info(
            "Training models for %s district-disease groups...",
            total_groups,
        )

        for index, (_, group) in enumerate(
            grouped,
            start=1,
        ):

            forecasts.extend(
                forecast_group(group)
            )

            if (
                index % 25 == 0
                or index == total_groups
            ):
                logger.info(
                    "Processed %s/%s groups",
                    index,
                    total_groups,
                )

        save_forecasts(forecasts)

        logger.info(
            "Forecasting pipeline completed successfully."
        )

    finally:
        spark.stop()


if __name__ == "__main__":
    main()
