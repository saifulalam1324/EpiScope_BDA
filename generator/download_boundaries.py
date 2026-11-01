
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "reference"
OUTPUT_FILE = OUTPUT_DIR / "districts.geojson"

QUERY_URL = (
    "https://gis.dghs.gov.bd/server/rest/services/Hosted/"
    "bgd_admbnda_adm2_bbs_20201113/FeatureServer/0/query"
)

PARAMS = {
    "where": "1=1",
    "outFields": "*",
    "returnGeometry": "true",
    "outSR": "4326",
    "f": "geojson",
}

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading Bangladesh district boundaries...")

    response = requests.get(
        QUERY_URL,
        params=PARAMS,
        timeout=120
    )
    response.raise_for_status()

    geojson = response.json()

    if geojson.get("type") != "FeatureCollection":
        raise RuntimeError(
            f"Unexpected response: {geojson}"
        )

    features = geojson.get("features", [])

    if len(features) != 64:
        raise RuntimeError(
            f"Expected 64 districts, received {len(features)}. "
            "File was not replaced."
        )

    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")

        if not properties.get("adm2_en"):
            raise RuntimeError("A district is missing adm2_en.")

        if not geometry:
            raise RuntimeError(
                f"Missing geometry for {properties['adm2_en']}"
            )

        # Keep the name field expected by the existing mapping script.
        properties["shapeName"] = properties["adm2_en"]

    temp_file = OUTPUT_FILE.with_suffix(".geojson.tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    temp_file.replace(OUTPUT_FILE)

    print("Saved:", OUTPUT_FILE)
    print("District features:", len(features))
    print("Source: Bangladesh DGHS GIS")
    print("Coordinate system: EPSG:4326")


if __name__ == "__main__":
    main()
