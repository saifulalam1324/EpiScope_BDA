
import csv
import json
import time
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import requests
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]

GEOJSON = ROOT / "data" / "reference" / "districts.geojson"
MAPPING = ROOT / "data" / "reference" / "district_mapping.csv"

OUT = ROOT / "data" / "weather" / "weather_daily.csv"
FAILED = ROOT / "data" / "weather" / "weather_failures.csv"

START_DATE = "2021-01-01"
END_DATE = "2025-12-31"
EXPECTED_DAYS = 1826

API_URL = "https://archive-api.open-meteo.com/v1/archive"

MAX_ATTEMPTS = 6
INITIAL_RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 900

FIELDS = [
    "district_id",
    "district_name",
    "district_iso",
    "latitude",
    "longitude",
    "date",
    "temperature_mean_c",
    "precipitation_mm",
]


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", value.lower())


def load_district_mapping():

    if not MAPPING.exists():
        raise FileNotFoundError(
            f"Missing district mapping: {MAPPING}"
        )

    mapping = {}

    with MAPPING.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            name = row["district_name"].strip()
            district_id = row["district_id"].strip()

            key = normalize(name)

            if key in mapping:
                raise ValueError(
                    f"Duplicate district name: {name}"
                )

            mapping[key] = {
                "district_id": district_id,
                "district_name": name,
            }

    if len(mapping) != 64:
        raise ValueError(
            f"Expected 64 districts, found {len(mapping)}"
        )

    return mapping


def expected_dates():

    start = date.fromisoformat(START_DATE)
    end = date.fromisoformat(END_DATE)

    result = []
    current = start

    while current <= end:
        result.append(current.isoformat())
        current += timedelta(days=1)

    return result


EXPECTED_DATE_SET = set(expected_dates())

if len(EXPECTED_DATE_SET) != EXPECTED_DAYS:
    raise RuntimeError("Incorrect configured date count.")


def load_existing_records():

    records = {}

    if not OUT.exists():
        return records

    with OUT.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            district_id = row.get("district_id", "").strip()
            day = row.get("date", "").strip()

            if not district_id or day not in EXPECTED_DATE_SET:
                continue

            key = (district_id, day)

            records.setdefault(key, row)

    return records


def district_is_complete(records, district_id):

    dates = {
        day
        for record_id, day in records
        if record_id == district_id
    }

    return dates == EXPECTED_DATE_SET


def retry_after_seconds(response):

    value = response.headers.get("Retry-After")

    if not value:
        return None

    try:
        return max(0, int(value))
    except ValueError:
        pass

    try:
        retry_date = parsedate_to_datetime(value)

        if retry_date.tzinfo is None:
            retry_date = retry_date.replace(tzinfo=timezone.utc)

        seconds = (
            retry_date - datetime.now(timezone.utc)
        ).total_seconds()

        return max(0, int(seconds))

    except (TypeError, ValueError, OverflowError):
        return None


def fetch_weather(session, name, params):

    last_error = "Unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):

        response = None

        try:

            response = session.get(
                API_URL,
                params=params,
                timeout=120,
            )

            if response.status_code == 429:

                server_wait = retry_after_seconds(response)

                exponential_wait = min(
                    INITIAL_RETRY_SECONDS * (2 ** (attempt - 1)),
                    MAX_RETRY_SECONDS,
                )

                wait = (
                    server_wait
                    if server_wait is not None
                    else exponential_wait
                )

                wait = min(wait, MAX_RETRY_SECONDS)

                last_error = (
                    f"HTTP 429 rate limit; retry delay {wait}s"
                )

                if attempt < MAX_ATTEMPTS:

                    print(
                        f"Rate limited: {name}. "
                        f"Attempt {attempt}/{MAX_ATTEMPTS}. "
                        f"Waiting {wait}s."
                    )

                    time.sleep(wait)
                    continue

                break

            response.raise_for_status()

            payload = response.json()

            if payload.get("error"):
                raise ValueError(
                    payload.get("reason", "Open-Meteo API error")
                )

            daily = payload.get("daily") or {}

            dates = daily.get("time") or []
            temps = daily.get("temperature_2m_mean")
            rain = daily.get("precipitation_sum")

            if temps is None or rain is None:
                raise ValueError(
                    "Missing weather measurements."
                )

            if not (
                len(dates)
                == len(temps)
                == len(rain)
                == EXPECTED_DAYS
            ):
                raise ValueError(
                    f"Incorrect record count for {name}."
                )

            if len(set(dates)) != EXPECTED_DAYS:
                raise ValueError(
                    f"Duplicate dates returned for {name}."
                )

            if set(dates) != EXPECTED_DATE_SET:
                raise ValueError(
                    f"Unexpected date range for {name}."
                )

            return dates, temps, rain

        except requests.RequestException as exc:

            last_error = str(exc)

            if attempt < MAX_ATTEMPTS:

                wait = min(
                    INITIAL_RETRY_SECONDS * (2 ** (attempt - 1)),
                    MAX_RETRY_SECONDS,
                )

                print(
                    f"Network error for {name}. "
                    f"Retry {attempt}/{MAX_ATTEMPTS} "
                    f"in {wait}s: {last_error}"
                )

                time.sleep(wait)

        except ValueError as exc:

            last_error = str(exc)
            break

        finally:

            if response is not None:
                response.close()

    raise RuntimeError(last_error)


def save_records(records):

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUT.with_suffix(".csv.tmp")

    with temp_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=FIELDS,
        )

        writer.writeheader()

        for key in sorted(records):
            writer.writerow(records[key])

    temp_file.replace(OUT)


def save_failure(name, error):

    FAILED.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing = {}

    if FAILED.exists():

        with FAILED.open(
            "r",
            newline="",
            encoding="utf-8-sig",
        ) as f:

            for row in csv.DictReader(f):
                existing[row["district_name"]] = row["error"]

    existing[name] = str(error)

    temp_file = FAILED.with_suffix(".csv.tmp")

    with temp_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)
        writer.writerow(["district_name", "error"])

        for district in sorted(existing):
            writer.writerow([
                district,
                existing[district],
            ])

    temp_file.replace(FAILED)


def clear_failure(name):

    if not FAILED.exists():
        return

    remaining = {}

    with FAILED.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        for row in csv.DictReader(f):

            if row["district_name"] != name:
                remaining[row["district_name"]] = row["error"]

    temp_file = FAILED.with_suffix(".csv.tmp")

    with temp_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)
        writer.writerow(["district_name", "error"])

        for district in sorted(remaining):
            writer.writerow([
                district,
                remaining[district],
            ])

    temp_file.replace(FAILED)


def main():

    if not GEOJSON.exists():
        raise FileNotFoundError(
            f"Missing boundary file: {GEOJSON}"
        )

    geo = json.loads(
        GEOJSON.read_text(encoding="utf-8")
    )

    features = geo.get("features", [])

    if len(features) != 64:
        raise ValueError(
            f"Expected 64 boundaries, found {len(features)}"
        )

    mapping = load_district_mapping()
    records = load_existing_records()

    session = requests.Session()

    session.headers.update({
        "User-Agent": "EpiScope-coursework/1.0"
    })

    print("Existing daily records:", len(records))

    completed = sum(
        district_is_complete(records, item["district_id"])
        for item in mapping.values()
    )

    print(f"Already complete: {completed}/64")

    # Only missing or incomplete districts are downloaded.
    for index, feature in enumerate(features, start=1):

        props = feature.get("properties") or {}
        geometry = feature.get("geometry")

        if not geometry:
            raise ValueError(
                f"Missing geometry in feature {index}"
            )

        name = props.get("shapeName")

        if not name:
            raise ValueError(
                f"Missing shapeName in feature {index}"
            )

        mapped = mapping.get(normalize(name))

        if mapped is None:
            raise ValueError(
                f"District not found in mapping: {name}"
            )

        district_id = mapped["district_id"]
        name = mapped["district_name"]

        if district_is_complete(records, district_id):

            print(
                f"{index}/64 skipped: {name} (complete)"
            )
            continue

        point = shape(geometry).representative_point()

        params = {
            "latitude": point.y,
            "longitude": point.x,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "Asia/Dhaka",
        }

        print(
            f"\n{index}/64 downloading missing district: {name}"
        )

        try:

            dates, temps, rain = fetch_weather(
                session,
                name,
                params,
            )

            new_rows = {}

            for day, temp, precipitation in zip(
                dates,
                temps,
                rain,
            ):

                if temp is None or precipitation is None:
                    raise ValueError(
                        f"Missing measurements on {day}"
                    )

                key = (district_id, day)

                new_rows[key] = {
                    "district_id": district_id,
                    "district_name": name,
                    "district_iso": props.get("shapeISO", ""),
                    "latitude": point.y,
                    "longitude": point.x,
                    "date": day,
                    "temperature_mean_c": temp,
                    "precipitation_mm": precipitation,
                }

            if len(new_rows) != EXPECTED_DAYS:
                raise ValueError(
                    f"Expected {EXPECTED_DAYS} rows, "
                    f"received {len(new_rows)}"
                )

            # Replace old district records only after
            # the new district download has fully validated.
            updated_records = {
                key: value
                for key, value in records.items()
                if key[0] != district_id
            }

            updated_records.update(new_rows)

            save_records(updated_records)

            records = updated_records

            clear_failure(name)

            print(
                f"SUCCESS: {name}, {len(new_rows)} daily records"
            )

        except (
            RuntimeError,
            ValueError,
            requests.RequestException,
        ) as exc:

            print(f"FAILED: {name}: {exc}")
            save_failure(name, str(exc))

        # Avoid immediately sending another request.
        time.sleep(15)

    session.close()

    completed_count = sum(
        district_is_complete(records, item["district_id"])
        for item in mapping.values()
    )

    print("\n" + "=" * 60)
    print("WEATHER DOWNLOAD SUMMARY")
    print("=" * 60)

    print(f"Completed districts: {completed_count}/64")
    print(f"Daily weather records: {len(records)}")
    print("Daily output:", OUT)
    print("Failure report:", FAILED)


if __name__ == "__main__":
    main()
