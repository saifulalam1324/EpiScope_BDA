
import argparse
import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


# ---------------------------------------------------------
# ING-1: Reproducible synthetic data generator
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "raw" / "generated"

START_DATE = date(2021, 1, 1)
END_DATE = date(2025, 12, 31)

BASE_RECORD_COUNT = 25_000_000
FACILITY_COUNT = 600
OUTBREAK_COUNT = 30
CHUNK_SIZE = 100_000

DISEASES = {
    "dengue": ["A90", "A91"],
    "diarrhoea": ["A00", "A09"],
    "influenza": ["J10", "J11"],
    "malaria": ["B50", "B51", "B52", "B53", "B54"],
    "other": ["Z00"],
}

BASE_WEIGHTS = {
    "dengue": 0.30,
    "diarrhoea": 0.20,
    "influenza": 0.20,
    "malaria": 0.10,
    "other": 0.20,
}

DISTRICTS = [f"D{i:03d}" for i in range(1, 65)]

parser = argparse.ArgumentParser(
    description="Generate reproducible synthetic EpiScope data."
)

parser.add_argument(
    "--scale",
    type=float,
    default=0.001,
    help="Scale factor between 0 and 1. Default: 0.001",
)

parser.add_argument(
    "--seed",
    type=int,
    default=42,
    help="Random seed. Default: 42",
)

parser.add_argument(
    "--out",
    type=Path,
    default=DEFAULT_OUTPUT,
    help="Output directory for generated CSV files.",
)

args = parser.parse_args()

if not 0 < args.scale <= 1:
    parser.error("--scale must be greater than 0 and at most 1.")

rng = random.Random(args.seed)

OUTPUT_DIR = args.out.resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOTAL_DAYS = (END_DATE - START_DATE).days + 1
RECORD_COUNT = int(BASE_RECORD_COUNT * args.scale)


# ---------------------------------------------------------
# Utility functions
# ---------------------------------------------------------

def random_date():
    return START_DATE + timedelta(
        days=rng.randrange(TOTAL_DAYS)
    )


def weighted_disease(active_outbreaks):
    """
    Select a disease using baseline probabilities.

    During an active outbreak, increase the probability
    of the outbreak disease.

    The outbreak boost is synthetic and configurable.
    """

    weights = BASE_WEIGHTS.copy()

    if active_outbreaks:
        # Select one active outbreak when multiple events overlap.
        outbreak = rng.choice(active_outbreaks)

        # Increase the corresponding disease probability.
        weights[outbreak["disease"]] *= 8.0

    diseases = list(weights.keys())
    probabilities = list(weights.values())

    return rng.choices(
        diseases,
        weights=probabilities,
        k=1,
    )[0]


def build_outbreaks():
    """
    Create synthetic outbreak ground truth.

    Returns:
        outbreak_rows: CSV rows.
        outbreak_index: District-indexed outbreak events.
    """

    outbreak_rows = []
    outbreak_index = defaultdict(list)

    outbreak_diseases = [
        "dengue",
        "diarrhoea",
        "influenza",
        "malaria",
    ]

    for i in range(OUTBREAK_COUNT):

        first = START_DATE + timedelta(
            days=rng.randint(0, TOTAL_DAYS - 60)
        )

        duration = rng.randint(14, 45)
        last = min(first + timedelta(days=duration), END_DATE)

        district = rng.choice(DISTRICTS)
        disease = rng.choice(outbreak_diseases)

        row = {
            "outbreak_id": f"OB{i + 1:03d}",
            "district_id": district,
            "disease": disease,
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
        }

        outbreak_rows.append(row)

        outbreak_index[district].append({
            "disease": disease,
            "start": first,
            "end": last,
        })

    return outbreak_rows, outbreak_index


# ---------------------------------------------------------
# ING-1: Generate synthetic facilities
# ---------------------------------------------------------

def generate_facilities():

    output_file = OUTPUT_DIR / "facilities.csv"

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "facility_id",
            "district_id",
            "facility_type",
            "beds",
            "icu_beds",
        ])

        for i in range(1, FACILITY_COUNT + 1):

            writer.writerow([
                f"F{i:04d}",
                rng.choice(DISTRICTS),
                rng.choice([
                    "hospital",
                    "upazila_health_complex",
                    "clinic",
                ]),
                rng.randint(10, 300),
                rng.randint(0, 30),
            ])

    print(f"Facilities generated: {FACILITY_COUNT:,}")


# ---------------------------------------------------------
# ING-1: Generate outbreak ground truth
# ---------------------------------------------------------

def generate_ground_truth():

    rows, outbreak_index = build_outbreaks()

    output_file = OUTPUT_DIR / "ground_truth_outbreaks.csv"

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "outbreak_id",
                "district_id",
                "disease",
                "start_date",
                "end_date",
            ],
        )

        writer.writeheader()
        writer.writerows(rows)

    print(f"Ground-truth outbreaks generated: {len(rows)}")

    return outbreak_index


# ---------------------------------------------------------
# ING-1: Generate synthetic encounters
# ---------------------------------------------------------



def generate_encounters(outbreak_index):

    output_file = OUTPUT_DIR / "encounters.csv"

    headers = [
        "encounter_id",
        "patient_id",
        "facility_id",
        "district_id",
        "onset_date",
        "report_date",
        "age_years",
        "sex",
        "icd10_code",
        "lab_confirmed",
        "admitted",
        "outcome",
    ]

    generated = 0

    # ---------------------------------------------------------
    # DQ-4 controlled positive and negative test pairs
    # ---------------------------------------------------------

    positive_pair_count = 20
    negative_pair_count = 5

    positive_record_count = positive_pair_count * 2
    negative_record_count = negative_pair_count * 2

    controlled_record_count = (
        positive_record_count + negative_record_count
    )

    controlled_pairs = []

    # Positive cases: should be detected.
    for pair_id in range(positive_pair_count):

        district = rng.choice(DISTRICTS)
        onset = random_date()

        active_outbreaks = [
            event
            for event in outbreak_index[district]
            if event["start"] <= onset <= event["end"]
        ]

        disease = weighted_disease(active_outbreaks)
        icd10_code = rng.choice(DISEASES[disease])

        profile = {
            "patient_id": f"PDQ4{pair_id + 1:05d}",
            "district_id": district,
            "onset_date": onset,
            "icd10_code": icd10_code,
            "age_years": rng.randint(18, 75),
            "sex": rng.choice(["M", "F"]),
            "lab_confirmed": rng.choice([0, 1]),
            "admitted": rng.choice([0, 1]),
            "outcome": rng.choice([
                "recovered",
                "admitted",
                "unknown",
            ]),
            "report_delays": [
                rng.randint(0, 3),
                rng.randint(4, 7),
            ],
            "facility_numbers": [
                pair_id * 2 + 1,
                pair_id * 2 + 2,
            ],
        }

        controlled_pairs.append({
            "type": "positive",
            "profile": profile,
        })

    # Negative cases: each pair violates exactly one matching rule.
    negative_cases = [
        "different_district",
        "different_onset",
        "different_icd10",
        "same_facility",
        "report_delay_over_7_days",
    ]

    for case_number, case_type in enumerate(negative_cases):

        onset = date(2023, 1, 10) + timedelta(
            days=case_number * 30
        )

        profile = {
            "patient_id": f"NDQ4{case_number + 1:05d}",
            "district_id": "D001",
            "onset_date": onset,
            "icd10_code": "A90",
            "age_years": 35,
            "sex": "M",
            "lab_confirmed": 1,
            "admitted": 0,
            "outcome": "recovered",
            "report_delays": [1, 2],
            "facility_numbers": [
                101 + case_number * 2,
                102 + case_number * 2,
            ],
            "case_type": case_type,
        }

        if case_type == "different_district":
            profile["second_district"] = "D002"

        elif case_type == "different_onset":
            profile["second_onset"] = onset + timedelta(days=1)

        elif case_type == "different_icd10":
            profile["second_icd10"] = "J10"

        elif case_type == "same_facility":
            profile["facility_numbers"] = [111, 111]

        elif case_type == "report_delay_over_7_days":
            profile["report_delays"] = [0, 10]

        controlled_pairs.append({
            "type": "negative",
            "profile": profile,
        })

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
        buffering=1024 * 1024,
    ) as f:

        writer = csv.writer(f)
        writer.writerow(headers)

        for batch_start in range(
            0,
            RECORD_COUNT,
            CHUNK_SIZE,
        ):

            batch_end = min(
                batch_start + CHUNK_SIZE,
                RECORD_COUNT,
            )

            for i in range(batch_start, batch_end):

                # -------------------------------------------------
                # Controlled DQ-4 positive and negative cases
                # -------------------------------------------------

                if i < controlled_record_count:

                    pair_id = i // 2
                    pair_position = i % 2

                    pair = controlled_pairs[pair_id]
                    profile = pair["profile"]

                    district = profile["district_id"]
                    onset = profile["onset_date"]
                    icd10_code = profile["icd10_code"]

                    if pair_position == 1:

                        district = profile.get(
                            "second_district",
                            district,
                        )

                        onset = profile.get(
                            "second_onset",
                            onset,
                        )

                        icd10_code = profile.get(
                            "second_icd10",
                            icd10_code,
                        )

                    report_date = (
                        profile["onset_date"]
                        + timedelta(
                            days=profile["report_delays"][
                                pair_position
                            ]
                        )
                    )

                    facility_num = profile[
                        "facility_numbers"
                    ][pair_position]

                    writer.writerow([
                        f"E{i:09d}",
                        profile["patient_id"],
                        f"F{facility_num:04d}",
                        district,
                        onset.isoformat(),
                        report_date.isoformat(),
                        profile["age_years"],
                        profile["sex"],
                        icd10_code,
                        profile["lab_confirmed"],
                        profile["admitted"],
                        profile["outcome"],
                    ])

                    continue

                # -------------------------------------------------
                # Ordinary synthetic encounter generation
                # -------------------------------------------------

                district = rng.choice(DISTRICTS)
                onset = random_date()

                active_outbreaks = [
                    event
                    for event in outbreak_index[district]
                    if event["start"] <= onset <= event["end"]
                ]

                disease = weighted_disease(active_outbreaks)
                icd10_code = rng.choice(DISEASES[disease])

                # Reporting delay: maximum 21 days.
                delay = min(
                    int(rng.expovariate(0.25)),
                    21,
                )

                report_date = onset + timedelta(days=delay)

                # Preserve approximately 2% duplicate encounter IDs.
                if i > 0 and rng.random() < 0.02:
                    encounter_id = f"E{rng.randrange(i):09d}"
                else:
                    encounter_id = f"E{i:09d}"

                # Preserve approximately 0.5% orphan facility keys.
                if rng.random() < 0.005:
                    facility_num = FACILITY_COUNT + 1
                else:
                    facility_num = rng.randint(1, FACILITY_COUNT)

                # Preserve invalid ages for quality-rule testing.
                if rng.random() < 0.01:
                    age = 212
                else:
                    age = rng.randint(0, 95)

                writer.writerow([
                    encounter_id,
                    f"P{rng.randint(1, max(1, RECORD_COUNT // 2)):09d}",
                    f"F{facility_num:04d}",
                    district,
                    onset.isoformat(),
                    report_date.isoformat(),
                    age,
                    rng.choice(["M", "F"]),
                    icd10_code,
                    rng.choice([0, 1]),
                    rng.choice([0, 1]),
                    rng.choice([
                        "recovered",
                        "admitted",
                        "unknown",
                    ]),
                ])

            generated = batch_end

            print(
                f"Progress: {generated:,}/{RECORD_COUNT:,} "
                f"({generated / max(RECORD_COUNT, 1) * 100:.1f}%)"
            )

            f.flush()

    print(f"Encounters generated: {generated:,}")
    print(
        "Controlled positive DQ-4 pairs generated: "
        f"{positive_pair_count}"
    )
    print(
        "Controlled negative DQ-4 pairs generated: "
        f"{negative_pair_count}"
    )



# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    print("=" * 60)
    print("EPISCOPE SYNTHETIC DATA GENERATOR")
    print("=" * 60)

    print(f"Scale factor: {args.scale}")
    print(f"Random seed: {args.seed}")
    print(f"Target encounters: {RECORD_COUNT:,}")
    print(f"Output: {OUTPUT_DIR}")

    generate_facilities()

    outbreak_index = generate_ground_truth()

    generate_encounters(outbreak_index)

    print("=" * 60)
    print("DATA GENERATION COMPLETED")
    print("=" * 60)

    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Encounter records: {RECORD_COUNT:,}")
    print(f"Facilities: {FACILITY_COUNT:,}")
    print(f"Outbreak events: {OUTBREAK_COUNT}")


if __name__ == "__main__":
    main()
