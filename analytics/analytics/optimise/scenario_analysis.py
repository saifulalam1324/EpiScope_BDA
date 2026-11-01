
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pulp


ROOT = Path(__file__).resolve().parents[3]

INPUT_FILE = ROOT / "analytics" / "forecast" / "resource_demand_by_district.csv"

OUTPUT_DIR = ROOT / "analytics" / "optimise"

DETAIL_OUTPUT = OUTPUT_DIR / "scenario_allocation_results.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "scenario_summary.csv"

CAPACITY_SCENARIOS = [50, 100, 200, 500]

SOLVER_TIMEOUT_SECONDS = 120

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.scenarios")


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        suffix=".csv",
        dir=path.parent,
    )
    os.close(fd)

    temp_path = Path(temp_name)

    try:
        df.to_csv(temp_path, index=False)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_demand() -> pd.DataFrame:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)

    df = pd.read_csv(INPUT_FILE)

    required = {
        "district_id",
        "forecast_week",
        "beds_needed",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    df["district_id"] = df["district_id"].astype("string").str.strip()

    df["forecast_week"] = pd.to_datetime(
        df["forecast_week"],
        errors="coerce",
    )

    df["beds_needed"] = pd.to_numeric(
        df["beds_needed"],
        errors="coerce",
    )

    if df[list(required)].isna().any().any():
        raise ValueError("Missing or invalid demand values.")

    if not np.isfinite(df["beds_needed"]).all():
        raise ValueError("Non-finite bed demand found.")

    if (df["beds_needed"] < 0).any():
        raise ValueError("Negative bed demand found.")

    if df.duplicated(["district_id", "forecast_week"]).any():
        raise ValueError("Duplicate district-week demand records.")

    # Beds are indivisible resources.
    if not np.allclose(
        df["beds_needed"],
        np.round(df["beds_needed"]),
    ):
        raise ValueError(
            "Bed demand must be integer-valued for this model."
        )

    df["beds_needed"] = df["beds_needed"].round().astype(int)

    return df


def calculate_gini(values) -> float:
    values = np.asarray(values, dtype=float)

    if len(values) == 0 or values.sum() == 0:
        return float("nan")

    values = np.sort(values)

    n = len(values)

    return float(
        (2 * np.sum(np.arange(1, n + 1) * values)
         / (n * values.sum()))
        - (n + 1) / n
    )


def optimize_week(
    group: pd.DataFrame,
    capacity: int,
) -> pd.DataFrame:

    group = group.sort_values("district_id").reset_index(drop=True)

    districts = group["district_id"].tolist()

    demand = dict(
        zip(districts, group["beds_needed"].astype(int))
    )

    total_demand = sum(demand.values())

    if total_demand == 0:
        allocated = {district: 0 for district in districts}
        minimum_coverage = 1.0
    else:
        model = pulp.LpProblem(
            "EpiScope_Equity_Allocation",
            pulp.LpMaximize,
        )

        allocation = {
            district: pulp.LpVariable(
                f"beds_{index}",
                lowBound=0,
                upBound=demand[district],
                cat=pulp.LpInteger,
            )
            for index, district in enumerate(districts)
        }

        minimum_coverage = pulp.LpVariable(
            "minimum_coverage",
            lowBound=0,
            upBound=1,
        )

        model += minimum_coverage

        model += (
            pulp.lpSum(allocation.values()) <= capacity
        )

        for district in districts:
            if demand[district] > 0:
                model += (
                    allocation[district]
                    >= minimum_coverage * demand[district]
                )
            else:
                model += allocation[district] == 0

        solver = pulp.PULP_CBC_CMD(
            msg=False,
            timeLimit=SOLVER_TIMEOUT_SECONDS,
        )

        status = model.solve(solver)

        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(
                f"Stage 1 failed: {pulp.LpStatus[status]}"
            )

        best_coverage = float(pulp.value(minimum_coverage))

        # Stage 2: preserve max-min equity, then maximize total beds.
        model += (
            minimum_coverage >= best_coverage - 1e-7
        )

        model.sense = pulp.LpMaximize
        model.setObjective(
            pulp.lpSum(allocation.values())
        )

        status = model.solve(solver)

        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(
                f"Stage 2 failed: {pulp.LpStatus[status]}"
            )

        allocated = {
            district: int(round(pulp.value(allocation[district])))
            for district in districts
        }

        minimum_coverage = best_coverage

    results = group.copy()

    results["allocated_beds"] = results["district_id"].map(
        allocated
    )

    results["unmet_beds"] = (
        results["beds_needed"] - results["allocated_beds"]
    )

    results["coverage_rate"] = np.divide(
        results["allocated_beds"],
        results["beds_needed"],
        out=np.ones(len(results), dtype=float),
        where=results["beds_needed"].to_numpy() > 0,
    )

    results["minimum_coverage"] = minimum_coverage
    results["weekly_capacity"] = capacity

    return results


def main():

    logger.info("Starting capacity scenario analysis...")

    demand = load_demand()

    logger.info("Demand records: %s", len(demand))
    logger.info("Districts: %s", demand["district_id"].nunique())
    logger.info("Weeks: %s", demand["forecast_week"].nunique())

    all_results = []

    total_jobs = (
        demand["forecast_week"].nunique()
        * len(CAPACITY_SCENARIOS)
    )

    job = 0

    for capacity in CAPACITY_SCENARIOS:

        logger.info("Testing weekly capacity: %s beds", capacity)

        for week, group in demand.groupby("forecast_week"):

            job += 1

            logger.info(
                "Scenario %s/%s: capacity=%s, week=%s",
                job,
                total_jobs,
                capacity,
                week.date(),
            )

            result = optimize_week(group, capacity)

            result["forecast_week"] = week
            result["scenario_capacity"] = capacity

            all_results.append(result)

    detail = pd.concat(
        all_results,
        ignore_index=True,
    )

    detail["forecast_week"] = (
        detail["forecast_week"].dt.strftime("%Y-%m-%d")
    )

    summary_records = []

    for (capacity, week), group in detail.groupby(
        ["scenario_capacity", "forecast_week"]
    ):

        total_demand = group["beds_needed"].sum()
        total_allocated = group["allocated_beds"].sum()

        summary_records.append({
            "capacity": capacity,
            "forecast_week": week,
            "districts_present": group["district_id"].nunique(),
            "total_demand": total_demand,
            "allocated_beds": total_allocated,
            "unmet_beds": group["unmet_beds"].sum(),
            "coverage_rate": (
                total_allocated / total_demand
                if total_demand > 0 else 1.0
            ),
            "minimum_district_coverage": (
                group.loc[
                    group["beds_needed"] > 0,
                    "coverage_rate",
                ].min()
                if total_demand > 0 else 1.0
            ),
            "demand_gini": calculate_gini(
                group["beds_needed"]
            ),
            "allocation_gini": calculate_gini(
                group["allocated_beds"]
            ),
        })

    summary = pd.DataFrame(summary_records)

    save_csv(detail, DETAIL_OUTPUT)
    save_csv(summary, SUMMARY_OUTPUT)

    overall = (
        summary.groupby("capacity")
        .agg(
            total_demand=("total_demand", "sum"),
            allocated_beds=("allocated_beds", "sum"),
            unmet_beds=("unmet_beds", "sum"),
            average_weekly_coverage=("coverage_rate", "mean"),
            average_minimum_coverage=(
                "minimum_district_coverage",
                "mean",
            ),
        )
        .reset_index()
    )

    print("\nCAPACITY SCENARIO RESULTS")
    print("=========================")

    print(overall.to_string(index=False))

    print("\nSAVED FILES")
    print("===========")
    print(DETAIL_OUTPUT)
    print(SUMMARY_OUTPUT)

    logger.info("Capacity scenario analysis completed.")


if __name__ == "__main__":
    main()
