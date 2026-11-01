
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp


# =========================================================
# CONFIGURATION
# =========================================================

ROOT = Path(__file__).resolve().parents[2]

GOLD_DIR = ROOT / "data" / "lake" / "gold" / "weekly_district_disease"

OUTPUT_DIR = ROOT / "analytics" / "forecast"

OUTPUT_FILE = OUTPUT_DIR / "seir_simulation.csv"
SUMMARY_FILE = OUTPUT_DIR / "seir_summary.csv"

SIMULATION_DAYS = 180

# Illustrative model parameters, not calibrated clinical estimates.
INCUBATION_PERIOD_DAYS = 5.0
INFECTIOUS_PERIOD_DAYS = 7.0

BETA = 0.30
INITIAL_EXPOSED_RATE = 0.001
INITIAL_INFECTIOUS_RATE = 0.0005

# Reproduction number under the simplified model:
# R0 = beta / gamma
# gamma = 1 / infectious period


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("episcope.seir")


# =========================================================
# CSV UTILITIES
# =========================================================

def save_csv(df: pd.DataFrame, destination: Path) -> None:

    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        suffix=".csv",
        dir=destination.parent,
    )

    os.close(fd)

    temp_path = Path(temp_name)

    try:
        df.to_csv(temp_path, index=False)
        temp_path.replace(destination)

    finally:
        if temp_path.exists():
            temp_path.unlink()


# =========================================================
# LOAD HISTORICAL DATA
# =========================================================

def load_population_data() -> pd.DataFrame:

    if not GOLD_DIR.exists():
        raise FileNotFoundError(
            f"Gold data directory not found: {GOLD_DIR}"
        )

    frames = []

    for path in GOLD_DIR.rglob("*.parquet"):

        frame = pd.read_parquet(path)

        required = {
            "district_id",
            "district_name",
            "disease_group",
            "case_count",
            "week_start",
        }

        missing = required - set(frame.columns)

        if missing:
            raise ValueError(
                f"{path.name} missing columns: {sorted(missing)}"
            )

        frames.append(
            frame[
                [
                    "district_id",
                    "district_name",
                    "disease_group",
                    "case_count",
                    "week_start",
                ]
            ]
        )

    if not frames:
        raise ValueError("No Gold Parquet files found.")

    data = pd.concat(frames, ignore_index=True)

    data["week_start"] = pd.to_datetime(
        data["week_start"],
        errors="coerce",
    )

    data["case_count"] = pd.to_numeric(
        data["case_count"],
        errors="coerce",
    )

    if data["week_start"].isna().any():
        raise ValueError("Gold data contains invalid dates.")

    if data["case_count"].isna().any():
        raise ValueError("Gold data contains invalid case counts.")

    if (data["case_count"] < 0).any():
        raise ValueError("Gold data contains negative cases.")

    data["disease"] = data["disease_group"].astype(str)

    data = (
        data.groupby(
            [
                "district_id",
                "district_name",
                "disease",
                "week_start",
            ],
            as_index=False,
        )
        ["case_count"]
        .sum()
    )

    return data


# =========================================================
# SEIR DIFFERENTIAL EQUATIONS
# =========================================================

def seir_equations(
    time: float,
    state: np.ndarray,
    beta: float,
    sigma: float,
    gamma: float,
) -> list[float]:

    susceptible, exposed, infectious, recovered = state

    population = susceptible + exposed + infectious + recovered

    if population <= 0:
        return [0.0, 0.0, 0.0, 0.0]

    new_exposures = (
        beta * susceptible * infectious / population
    )

    new_infectious = sigma * exposed

    new_recoveries = gamma * infectious

    return [
        -new_exposures,
        new_exposures - new_infectious,
        new_infectious - new_recoveries,
        new_recoveries,
    ]


# =========================================================
# RUN ONE SEIR SIMULATION
# =========================================================

def simulate_seir(
    population: int,
    initial_cases: float,
    beta: float = BETA,
    incubation_period: float = INCUBATION_PERIOD_DAYS,
    infectious_period: float = INFECTIOUS_PERIOD_DAYS,
    days: int = SIMULATION_DAYS,
) -> pd.DataFrame:

    if population <= 0:
        raise ValueError("Population must be positive.")

    if initial_cases < 0:
        raise ValueError("Initial cases cannot be negative.")

    if beta < 0 or incubation_period <= 0 or infectious_period <= 0:
        raise ValueError("Invalid SEIR model parameters.")

    if days <= 0:
        raise ValueError("Simulation days must be positive.")

    # Initial conditions.
    exposed = min(
        population * INITIAL_EXPOSED_RATE,
        population,
    )

    infectious = min(
        population * INITIAL_INFECTIOUS_RATE,
        population - exposed,
    )

    # Use observed weekly cases only as a rough initial
    # infectious-state proxy, not as a direct population estimate.
    infectious = min(
        max(infectious, initial_cases),
        population - exposed,
    )

    susceptible = population - exposed - infectious

    recovered = 0.0

    initial_state = [
        susceptible,
        exposed,
        infectious,
        recovered,
    ]

    sigma = 1.0 / incubation_period
    gamma = 1.0 / infectious_period

    times = np.arange(0, days + 1, dtype=float)

    result = solve_ivp(
        fun=lambda t, y: seir_equations(
            t,
            y,
            beta,
            sigma,
            gamma,
        ),
        t_span=(0, days),
        y0=initial_state,
        t_eval=times,
        method="RK45",
        rtol=1e-7,
        atol=1e-8,
    )

    if not result.success:
        raise RuntimeError(
            f"SEIR simulation failed: {result.message}"
        )

    output = pd.DataFrame({
        "day": result.t.astype(int),
        "susceptible": result.y[0],
        "exposed": result.y[1],
        "infectious": result.y[2],
        "recovered": result.y[3],
    })

    output["new_exposures_per_day"] = (
        beta
        * output["susceptible"]
        * output["infectious"]
        / population
    )

    output["population"] = population

    # Numerical and conservation checks.
    compartments = [
        "susceptible",
        "exposed",
        "infectious",
        "recovered",
    ]

    if (output[compartments] < -1e-5).any().any():
        raise RuntimeError("Negative compartment values detected.")

    total = output[compartments].sum(axis=1)

    if not np.allclose(
        total,
        population,
        rtol=1e-5,
        atol=1e-3,
    ):
        raise RuntimeError(
            "SEIR population conservation check failed."
        )

    return output


# =========================================================
# BUILD DISTRICT-LEVEL SCENARIOS
# =========================================================

def run_all_scenarios(
    historical: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    results = []
    summaries = []

    groups = historical.groupby(
        ["district_id", "district_name", "disease"],
        sort=True,
    )

    group_count = historical[
        ["district_id", "disease"]
    ].drop_duplicates().shape[0]

    logger.info(
        "Running SEIR simulations for %s district-disease groups.",
        group_count,
    )

    for index, (
        (district_id, district_name, disease),
        group,
    ) in enumerate(groups, start=1):

        group = group.sort_values("week_start")

        recent_cases = (
            group.tail(4)["case_count"].mean()
        )

        # Population is not available in the current Gold schema.
        # This is an explicitly illustrative scenario population,
        # not an estimate of the district's real population.
        scenario_population = 100_000

        initial_cases = max(
            1.0,
            float(recent_cases),
        )

        simulation = simulate_seir(
            population=scenario_population,
            initial_cases=initial_cases,
        )

        simulation.insert(0, "disease", disease)
        simulation.insert(0, "district_name", district_name)
        simulation.insert(0, "district_id", district_id)

        results.append(simulation)

        peak_index = simulation["infectious"].idxmax()

        summaries.append({
            "district_id": district_id,
            "district_name": district_name,
            "disease": disease,
            "scenario_population": scenario_population,
            "initial_infectious": initial_cases,
            "beta": BETA,
            "incubation_period_days": INCUBATION_PERIOD_DAYS,
            "infectious_period_days": INFECTIOUS_PERIOD_DAYS,
            "basic_reproduction_number": BETA * INFECTIOUS_PERIOD_DAYS,
            "peak_infectious": simulation.loc[
                peak_index,
                "infectious",
            ],
            "peak_day": int(
                simulation.loc[peak_index, "day"]
            ),
            "final_recovered": simulation.iloc[-1]["recovered"],
            "model_type": "Illustrative_SEIR",
        })

        if index % 50 == 0:
            logger.info(
                "Completed %s/%s simulations.",
                index,
                group_count,
            )

    simulation_results = pd.concat(
        results,
        ignore_index=True,
    )

    summary_results = pd.DataFrame(summaries)

    return simulation_results, summary_results


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("Starting EpiScope SEIR model...")

    historical = load_population_data()

    logger.info(
        "Historical Gold records: %s",
        len(historical),
    )

    logger.info(
        "Districts: %s",
        historical["district_id"].nunique(),
    )

    logger.info(
        "Diseases: %s",
        historical["disease"].nunique(),
    )

    simulations, summaries = run_all_scenarios(
        historical
    )

    save_csv(simulations, OUTPUT_FILE)
    save_csv(summaries, SUMMARY_FILE)

    print("\nSEIR SIMULATION SUMMARY")
    print("=======================")

    print("Simulation records:", len(simulations))
    print("Districts:", summaries["district_id"].nunique())
    print("Diseases:", summaries["disease"].nunique())
    print("Simulated groups:", len(summaries))

    print(
        "\nMean peak infectious population:",
        round(summaries["peak_infectious"].mean(), 2),
    )

    print(
        "Maximum peak infectious population:",
        round(summaries["peak_infectious"].max(), 2),
    )

    print("\nOutput files:")
    print(OUTPUT_FILE)
    print(SUMMARY_FILE)

    logger.info("SEIR model completed successfully.")


if __name__ == "__main__":
    main()
