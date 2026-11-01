
from pathlib import Path
import logging
import os
import tempfile

import pandas as pd
import pulp


ROOT = Path(__file__).resolve().parents[3]

# Use district-level demand, already aggregated across diseases.
INPUT = (
    ROOT
    / "analytics/forecast/resource_demand_by_district.csv"
)

OUTPUT = ROOT / "analytics/optimise/allocation_results.csv"

# Illustrative national capacity available independently
# during each forecast week.
CAPACITY_PER_WEEK = 500

SOLVER_TIME_LIMIT_SECONDS = 120

ALLOCATION_METHOD = "weekly_max_min_equity"

REQUIRED_COLUMNS = {
    "district_id",
    "forecast_week",
    "beds_needed",
}

logger = logging.getLogger("episcope.allocation")


def save_csv_atomic(
    frame: pd.DataFrame,
    path: Path,
) -> None:
    """Save CSV through a temporary file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="allocation_",
            dir=path.parent,
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            frame.to_csv(temporary_file, index=False)

        os.replace(temporary_path, path)

    finally:
        if (
            temporary_path is not None
            and temporary_path.exists()
        ):
            temporary_path.unlink()

    logger.info("Saved: %s", path)


def load_demand() -> pd.DataFrame:
    """Load and validate district-week resource demand."""
    if not INPUT.exists():
        raise FileNotFoundError(
            f"Demand input not found: {INPUT}"
        )

    demand = pd.read_csv(INPUT)

    if demand.empty:
        raise ValueError("Resource-demand file is empty.")

    missing = REQUIRED_COLUMNS - set(demand.columns)

    if missing:
        raise ValueError(
            f"Demand file is missing columns: {sorted(missing)}"
        )

    demand = demand.copy()

    demand["district_id"] = (
        demand["district_id"].astype("string").str.strip()
    )

    if (
        demand["district_id"].isna().any()
        or (demand["district_id"] == "").any()
    ):
        raise ValueError("Missing district IDs.")

    demand["forecast_week"] = pd.to_datetime(
        demand["forecast_week"],
        errors="coerce",
    )

    if demand["forecast_week"].isna().any():
        raise ValueError("Invalid forecast dates.")

    demand["beds_needed"] = pd.to_numeric(
        demand["beds_needed"],
        errors="coerce",
    )

    if demand["beds_needed"].isna().any():
        raise ValueError("Invalid bed-demand values.")

    if not demand["beds_needed"].map(
        lambda value: pd.notna(value) and abs(value) != float("inf")
    ).all():
        raise ValueError("Bed demand contains non-finite values.")

    if (demand["beds_needed"] < 0).any():
        raise ValueError("Bed demand cannot be negative.")

    if (demand["beds_needed"] % 1 != 0).any():
        raise ValueError("Bed demand must contain whole numbers.")

    demand["beds_needed"] = demand["beds_needed"].astype("int64")

    # One district must have one consistent name.
    if "district_name" in demand.columns:
        demand["district_name"] = (
            demand["district_name"].astype("string").str.strip()
        )

        name_counts = (
            demand.groupby("district_id")["district_name"]
            .nunique(dropna=True)
        )

        if (name_counts > 1).any():
            raise ValueError(
                "A district has multiple district names."
            )

    # The district-level file should have one row per district-week.
    duplicate_mask = demand.duplicated(
        ["district_id", "forecast_week"],
        keep=False,
    )

    if duplicate_mask.any():
        raise ValueError(
            "Duplicate district-week demand records found. "
            "Check resource_demand_by_district.csv."
        )

    return demand


def solve_week(
    weekly_demand: pd.DataFrame,
    forecast_week: pd.Timestamp,
) -> list[dict]:
    """Optimize integer bed allocation for one forecast week."""

    weekly_demand = weekly_demand.sort_values(
        "district_id"
    ).reset_index(drop=True)

    districts = weekly_demand["district_id"].tolist()

    needs = {
        row["district_id"]: int(row["beds_needed"])
        for _, row in weekly_demand.iterrows()
    }

    total_demand = sum(needs.values())

    if not districts:
        return []

    model = pulp.LpProblem(
        "Weekly_Equitable_Bed_Allocation",
        pulp.LpMaximize,
    )

    allocated = {
        district: pulp.LpVariable(
            f"allocated_{i}",
            lowBound=0,
            upBound=needs[district],
            cat=pulp.LpInteger,
        )
        for i, district in enumerate(districts)
    }

    minimum_coverage = pulp.LpVariable(
        "minimum_coverage",
        lowBound=0,
        upBound=1,
        cat=pulp.LpContinuous,
    )

    # Stage 1: maximize the minimum coverage across districts.
    for i, district in enumerate(districts):
        if needs[district] > 0:
            model += (
                allocated[district]
                >= minimum_coverage * needs[district],
                f"minimum_coverage_{i}",
            )

    model += (
        pulp.lpSum(allocated[d] for d in districts)
        <= CAPACITY_PER_WEEK,
        "weekly_national_capacity",
    )

    model += minimum_coverage

    solver = pulp.PULP_CBC_CMD(
        msg=False,
        timeLimit=SOLVER_TIME_LIMIT_SECONDS,
    )

    status = model.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"Stage 1 failed for {forecast_week.date()}: "
            f"{pulp.LpStatus[status]}"
        )

    best_coverage = pulp.value(minimum_coverage)

    if best_coverage is None:
        raise RuntimeError(
            f"Stage 1 returned no coverage value for "
            f"{forecast_week.date()}."
        )

    # Stage 2: preserve the optimal minimum coverage,
    # then maximize the total number of allocated beds.
    model += (
        minimum_coverage >= best_coverage - 1e-7,
        "preserve_optimal_minimum_coverage",
    )

    model.objective = pulp.lpSum(
        allocated[d] for d in districts
    )

    status = model.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"Stage 2 failed for {forecast_week.date()}: "
            f"{pulp.LpStatus[status]}"
        )

    rows = []

    for _, row in weekly_demand.iterrows():
        district = row["district_id"]
        need = needs[district]

        allocation_value = pulp.value(allocated[district])

        if allocation_value is None:
            raise RuntimeError(
                f"No allocation returned for {district}."
            )

        allocation = int(round(allocation_value))
        unmet = need - allocation

        coverage = (
            allocation / need
            if need > 0
            else 1.0
        )

        result = {
            "district_id": district,
            "forecast_week": forecast_week.date().isoformat(),
            "beds_needed": need,
            "allocated_beds": allocation,
            "unmet_beds": unmet,
            "coverage_rate": coverage,
            "minimum_weekly_coverage": best_coverage,
            "weekly_capacity": CAPACITY_PER_WEEK,
            "allocation_method": ALLOCATION_METHOD,
        }

        if "district_name" in weekly_demand.columns:
            result["district_name"] = row["district_name"]

        rows.append(result)

    # Check the allocation constraints after solving.
    allocated_total = sum(
        row["allocated_beds"] for row in rows
    )

    if allocated_total > CAPACITY_PER_WEEK:
        raise RuntimeError(
            f"Capacity exceeded in {forecast_week.date()}."
        )

    if allocated_total > total_demand:
        raise RuntimeError(
            f"Allocation exceeds demand in {forecast_week.date()}."
        )

    for row in rows:
        if row["allocated_beds"] < 0:
            raise RuntimeError("Negative allocation detected.")

        if row["unmet_beds"] < 0:
            raise RuntimeError("Negative unmet demand detected.")

    return rows


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("Starting weekly resource-allocation optimization...")

    if (
        not isinstance(CAPACITY_PER_WEEK, int)
        or CAPACITY_PER_WEEK < 0
    ):
        raise ValueError(
            "CAPACITY_PER_WEEK must be a non-negative integer."
        )

    demand = load_demand()

    logger.info("Demand records: %s", len(demand))
    logger.info(
        "Districts: %s",
        demand["district_id"].nunique(),
    )
    logger.info(
        "Forecast weeks: %s",
        demand["forecast_week"].nunique(),
    )

    all_results = []

    for week_number, (forecast_week, weekly_demand) in enumerate(
        demand.groupby("forecast_week", sort=True),
        start=1,
    ):
        logger.info(
            "Optimizing week %s: %s",
            week_number,
            forecast_week.date(),
        )

        week_results = solve_week(
            weekly_demand,
            forecast_week,
        )

        all_results.extend(week_results)

        allocated_total = sum(
            row["allocated_beds"]
            for row in week_results
        )

        demand_total = sum(
            row["beds_needed"]
            for row in week_results
        )

        logger.info(
            "Week %s: demand=%s, allocated=%s, unmet=%s",
            forecast_week.date(),
            demand_total,
            allocated_total,
            demand_total - allocated_total,
        )

    results = pd.DataFrame(all_results)

    if results.empty:
        raise RuntimeError("No allocation results were generated.")

    # Put the main identifying columns first.
    preferred_columns = [
        "district_id",
        "district_name",
        "forecast_week",
        "beds_needed",
        "allocated_beds",
        "unmet_beds",
        "coverage_rate",
        "minimum_weekly_coverage",
        "weekly_capacity",
        "allocation_method",
    ]

    results = results[
        [column for column in preferred_columns if column in results.columns]
    ]

    save_csv_atomic(results, OUTPUT)

    total_demand = int(results["beds_needed"].sum())
    total_allocated = int(results["allocated_beds"].sum())
    total_unmet = int(results["unmet_beds"].sum())

    weekly_summary = (
        results.groupby("forecast_week")
        .agg(
            districts=("district_id", "nunique"),
            total_demand=("beds_needed", "sum"),
            allocated_beds=("allocated_beds", "sum"),
            unmet_beds=("unmet_beds", "sum"),
            minimum_coverage=("coverage_rate", "min"),
        )
    )

    print("\nWEEKLY BED ALLOCATION RESULTS")
    print("Solver: PuLP CBC")
    print("Allocation method:", ALLOCATION_METHOD)
    print("Capacity per week:", CAPACITY_PER_WEEK)
    print("Forecast weeks:", len(weekly_summary))
    print("Districts:", results["district_id"].nunique())

    print("\nOVERALL TOTALS")
    print("Total demand:", total_demand)
    print("Total allocated:", total_allocated)
    print("Total unmet:", total_unmet)

    print(
        "Overall demand coverage:",
        f"{total_allocated / total_demand:.1%}"
        if total_demand > 0 else "N/A",
    )

    print("\nWEEKLY SUMMARY")
    print(weekly_summary.to_string())

    print("\nDISTRICTS WITH THE MOST UNMET BEDS")
    print(
        results.sort_values(
            ["unmet_beds", "forecast_week", "district_id"],
            ascending=[False, True, True],
        )
        .head(15)
        .to_string(index=False)
    )

    print("\nSaved:", OUTPUT)

    logger.info(
        "Weekly resource-allocation optimization completed."
    )


if __name__ == "__main__":
    main()
