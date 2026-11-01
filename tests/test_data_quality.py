
import hashlib

import pytest

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    BooleanType,
)

from pipeline.silver.clean import (
    add_reasons,
    add_privacy_fields,
)


# --------------------------------------------------
# SPARK FIXTURE
# --------------------------------------------------

@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder
        .appName("EpiScope-Data-Quality-Tests")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )

    session.sparkContext.setLogLevel("ERROR")

    yield session

    session.stop()


# --------------------------------------------------
# TEST DATA
# --------------------------------------------------

SCHEMA = StructType([
    StructField("encounter_id", StringType(), True),
    StructField("patient_id", StringType(), True),
    StructField("facility_id", StringType(), True),
    StructField("district_id", StringType(), True),
    StructField("onset_date", StringType(), True),
    StructField("report_date", StringType(), True),
    StructField("age_years", StringType(), True),
    StructField("sex", StringType(), True),
    StructField("icd10_code", StringType(), True),
    StructField("lab_confirmed", StringType(), True),
    StructField("admitted", StringType(), True),
    StructField("outcome", StringType(), True),
    StructField("batch_id", StringType(), True),
    StructField("facility_exists", BooleanType(), True),
])


def valid_record():
    return {
        "encounter_id": "E0001",
        "patient_id": "P0001",
        "facility_id": "F001",
        "district_id": "D001",
        "onset_date": "2025-01-01",
        "report_date": "2025-01-03",
        "age_years": "25",
        "sex": "M",
        "icd10_code": "A09",
        "lab_confirmed": "1",
        "admitted": "0",
        "outcome": "Recovered",
        "batch_id": "B001",
        "facility_exists": True,
    }


def create_dataframe(spark, updates=None):
    record = valid_record()

    if updates:
        record.update(updates)

    return spark.createDataFrame([record], schema=SCHEMA)


def get_reasons(spark, updates):
    df = create_dataframe(spark, updates)

    result = (
        add_reasons(df)
        .select("reasons")
        .first()["reasons"]
    )

    return result


# --------------------------------------------------
# VALID RECORD TEST
# --------------------------------------------------

def test_valid_record_has_no_reasons(spark):
    reasons = get_reasons(spark, {})

    assert reasons == []


# --------------------------------------------------
# INDIVIDUAL QUALITY RULE TESTS
# --------------------------------------------------

@pytest.mark.parametrize(
    "updates, expected_reason",
    [
        (
            {"encounter_id": None},
            "missing_encounter_id",
        ),
        (
            {"patient_id": None},
            "missing_patient_id",
        ),
        (
            {"district_id": "INVALID"},
            "invalid_district_id",
        ),
        (
            {"facility_exists": None},
            "orphan_facility",
        ),
        (
            {"onset_date": "not-a-date"},
            "invalid_date",
        ),
        (
            {
                "onset_date": "2025-01-05",
                "report_date": "2025-01-02",
            },
            "report_before_onset",
        ),
        (
            {"age_years": "150"},
            "invalid_age",
        ),
        (
            {"lab_confirmed": "3"},
            "invalid_lab_flag",
        ),
        (
            {"admitted": "5"},
            "invalid_admission_flag",
        ),
        (
            {"sex": "UNKNOWN"},
            "invalid_sex",
        ),
        (
            {"icd10_code": "INVALID"},
            "invalid_icd10",
        ),
        (
            {"outcome": ""},
            "missing_outcome",
        ),
        (
            {"batch_id": None},
            "missing_batch_id",
        ),
    ],
)
def test_invalid_records_are_flagged(
    spark,
    updates,
    expected_reason,
):
    reasons = get_reasons(spark, updates)

    assert expected_reason in reasons


# --------------------------------------------------
# MULTIPLE FAILURE TEST
# --------------------------------------------------

def test_record_can_have_multiple_reasons(spark):
    reasons = get_reasons(
        spark,
        {
            "age_years": "150",
            "sex": "UNKNOWN",
            "district_id": "BAD",
        },
    )

    assert "invalid_age" in reasons
    assert "invalid_sex" in reasons
    assert "invalid_district_id" in reasons

    assert len(reasons) == 3


# --------------------------------------------------
# PRIVACY TESTS
# --------------------------------------------------

def test_patient_id_is_hashed(spark):
    salt = "test-private-salt"

    df = create_dataframe(spark)

    result = add_privacy_fields(df, salt)

    row = result.first()

    expected_hash = hashlib.sha256(
        (salt + "P0001").encode("utf-8")
    ).hexdigest()

    assert row["patient_id_hash"] == expected_hash


def test_original_identifiers_are_removed(spark):
    result = add_privacy_fields(
        create_dataframe(spark),
        "test-private-salt",
    )

    assert "patient_id" not in result.columns
    assert "age_years" not in result.columns

    assert "patient_id_hash" in result.columns
    assert "age_band" in result.columns


def test_different_salts_produce_different_hashes(spark):
    df = create_dataframe(spark)

    hash_one = add_privacy_fields(
        df,
        "salt-one",
    ).first()["patient_id_hash"]

    hash_two = add_privacy_fields(
        df,
        "salt-two",
    ).first()["patient_id_hash"]

    assert hash_one != hash_two


def test_empty_salt_is_rejected(spark):
    df = create_dataframe(spark)

    with pytest.raises(ValueError):
        add_privacy_fields(df, "")


# --------------------------------------------------
# AGE BAND TESTS
# --------------------------------------------------

@pytest.mark.parametrize(
    "age, expected_band",
    [
        ("0", "0-4"),
        ("4", "0-4"),
        ("5", "5-17"),
        ("17", "5-17"),
        ("18", "18-34"),
        ("34", "18-34"),
        ("35", "35-49"),
        ("49", "35-49"),
        ("50", "50-64"),
        ("64", "50-64"),
        ("65", "65+"),
        ("90", "65+"),
    ],
)
def test_age_band_conversion(spark, age, expected_band):
    result = add_privacy_fields(
        create_dataframe(spark, {"age_years": age}),
        "test-private-salt",
    )

    assert result.first()["age_band"] == expected_band


def test_null_age_becomes_unknown(spark):
    result = add_privacy_fields(
        create_dataframe(spark, {"age_years": None}),
        "test-private-salt",
    )

    assert result.first()["age_band"] == "unknown"
