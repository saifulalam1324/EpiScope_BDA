
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

GEOJSON_FILE = ROOT / "data" / "reference" / "districts.geojson"
OUTPUT_FILE = ROOT / "data" / "reference" / "district_mapping.csv"

EXPECTED_DISTRICTS = 64


def main():
    if not GEOJSON_FILE.exists():
        raise FileNotFoundError(
            f"District GeoJSON not found: {GEOJSON_FILE}\n"
            "Run generator/download_boundaries.py first."
        )

    with GEOJSON_FILE.open("r", encoding="utf-8") as file:
        geojson = json.load(file)

    features = geojson.get("features", [])

    if len(features) != EXPECTED_DISTRICTS:
        raise ValueError(
            f"Expected {EXPECTED_DISTRICTS} districts, "
            f"but found {len(features)}."
        )

    names = []

    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        name = properties.get("shapeName")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Missing district name in feature {index}."
            )

        names.append(name.strip())

    names = sorted(names, key=str.casefold)

    if len(set(name.casefold() for name in names)) != EXPECTED_DISTRICTS:
        raise ValueError("Duplicate district names detected.")

    mapping = pd.DataFrame({
        "district_id": [
            f"D{i:03d}" for i in range(1, EXPECTED_DISTRICTS + 1)
        ],
        "district_name": names
    })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    temp_file = OUTPUT_FILE.with_suffix(".csv.tmp")
    mapping.to_csv(temp_file, index=False)
    temp_file.replace(OUTPUT_FILE)

    print("\nDistrict mapping preview:")
    print(mapping.head(10).to_string(index=False))

    print("\nTotal districts:", len(mapping))
    print("Unique district IDs:", mapping["district_id"].nunique())
    print("Unique district names:", mapping["district_name"].nunique())
    print("Saved:", OUTPUT_FILE)


if __name__ == "__main__":
    main()
