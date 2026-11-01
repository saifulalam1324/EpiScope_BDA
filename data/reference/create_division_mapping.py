
from pathlib import Path
import re
import unicodedata

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

SOURCE = ROOT / "data" / "reference" / "district_mapping.csv"
OUTPUT = ROOT / "data" / "reference" / "district_division_mapping.csv"

EXPECTED_DISTRICTS = 64

# Bangladesh's eight administrative divisions and their districts.
DIVISIONS = {
    "Dhaka": [
        "Dhaka", "Faridpur", "Gazipur", "Gopalganj",
        "Kishoreganj", "Madaripur", "Manikganj",
        "Munshiganj", "Narayanganj", "Narsingdi",
        "Rajbari", "Shariatpur", "Tangail"
    ],
    "Khulna": [
        "Bagerhat", "Chuadanga", "Jashore", "Jhenaidah",
        "Khulna", "Kushtia", "Magura", "Meherpur",
        "Narail", "Satkhira"
    ],
    "Chattogram": [
        "Bandarban", "Brahmanbaria", "Chandpur",
        "Chattogram", "Cumilla", "Cox's Bazar", "Feni",
        "Khagrachhari", "Lakshmipur", "Noakhali", "Rangamati"
    ],
    "Rajshahi": [
        "Bogura", "Joypurhat", "Naogaon", "Natore",
        "Chapainawabganj", "Pabna", "Rajshahi", "Sirajganj"
    ],
    "Sylhet": [
        "Habiganj", "Moulvibazar", "Sunamganj", "Sylhet"
    ],
    "Rangpur": [
        "Dinajpur", "Gaibandha", "Kurigram", "Lalmonirhat",
        "Nilphamari", "Panchagarh", "Rangpur", "Thakurgaon"
    ],
    "Mymensingh": [
        "Jamalpur", "Mymensingh", "Netrokona", "Sherpur"
    ],
    "Barishal": [
        "Barguna", "Barishal", "Bhola", "Jhalokati",
        "Patuakhali", "Pirojpur"
    ]
}

# Alternative spellings found in geographic datasets.
ALIASES = {
    "barisal": "barishal",
    "bogra": "bogura",
    "brahamanbaria": "brahmanbaria",
    "chittagong": "chattogram",
    "comilla": "cumilla",
    "coxsbazar": "coxsbazar",
    "jessore": "jashore",
    "maulvibazar": "moulvibazar",
    "chapainawabgonj": "chapainawabganj",
    "nawabganj": "chapainawabganj",
    "netrakona": "netrokona",
}


def normalize(value):
    """Normalize district names for spelling-independent matching."""
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", value.lower())


def build_division_lookup():
    lookup = {}

    for division, district_names in DIVISIONS.items():
        for district_name in district_names:
            key = normalize(district_name)

            if key in lookup:
                raise ValueError(
                    f"District appears in multiple divisions: {district_name}"
                )

            lookup[key] = division

    for alias, canonical in ALIASES.items():
        canonical_key = normalize(canonical)

        if canonical_key not in lookup:
            raise ValueError(
                f"Invalid alias configuration: {alias} -> {canonical}"
            )

        alias_key = normalize(alias)

        if alias_key in lookup and lookup[alias_key] != lookup[canonical_key]:
            raise ValueError(f"Conflicting district alias: {alias}")

        lookup[alias_key] = lookup[canonical_key]

    return lookup


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(
            f"District mapping not found: {SOURCE}\n"
            "Run generator/create_district_mapping.py first."
        )

    districts = pd.read_csv(SOURCE, dtype=str)
    districts.columns = districts.columns.str.strip()

    required = {"district_id", "district_name"}

    if not required.issubset(districts.columns):
        raise ValueError(
            f"Source file must contain: {sorted(required)}"
        )

    districts["district_id"] = districts["district_id"].str.strip()
    districts["district_name"] = districts["district_name"].str.strip()

    if districts[list(required)].isna().any().any():
        raise ValueError("Missing district IDs or district names.")

    if districts["district_id"].duplicated().any():
        raise ValueError("Duplicate district IDs found.")

    if districts["district_name"].map(normalize).duplicated().any():
        raise ValueError("Duplicate district names found.")

    if len(districts) != EXPECTED_DISTRICTS:
        raise ValueError(
            f"Expected {EXPECTED_DISTRICTS} districts, "
            f"found {len(districts)}."
        )

    division_lookup = build_division_lookup()

    districts["division_name"] = districts["district_name"].map(
        lambda name: division_lookup.get(normalize(name))
    )

    unmatched = districts[districts["division_name"].isna()]

    if not unmatched.empty:
        print("\nUnmatched districts:")
        print(
            unmatched[["district_id", "district_name"]]
            .to_string(index=False)
        )
        raise ValueError("Division mapping is incomplete.")

    # Confirm that all eight divisions are represented.
    counts = districts.groupby("division_name").size()

    if len(counts) != len(DIVISIONS):
        raise ValueError("One or more divisions are missing.")

    # Save atomically.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    temp_file = OUTPUT.with_suffix(".csv.tmp")
    districts.to_csv(temp_file, index=False)
    temp_file.replace(OUTPUT)

    print("\nDivision mapping created successfully.")
    print("District count:", len(districts))
    print("Unique district IDs:", districts["district_id"].nunique())

    print("\nDistricts per division:")
    print(counts.to_string())

    print("\nPreview:")
    print(districts.head(10).to_string(index=False))

    print("\nSaved:", OUTPUT)


if __name__ == "__main__":
    main()
