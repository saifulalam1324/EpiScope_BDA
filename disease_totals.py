from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("EpiScope_Gold_Disease_Totals")
    .getOrCreate()
)

try:
    path = "data/lake/gold/weekly_district_disease"

    print("\nReading Gold Parquet data...")

    df = spark.read.parquet(path)

    print("\nDisease-wise case totals:\n")

    totals = (
        df.groupBy("disease_group")
        .agg(F.sum("case_count").alias("Cases"))
        .orderBy("disease_group")
    )

    totals.show(100, truncate=False)

    grand_total = totals.agg(
        F.sum("Cases").alias("Total")
    ).first()["Total"]

    print("\n================================")
    print("TOTAL GOLD CASES:", grand_total)
    print("================================")

    totals.coalesce(1).write.mode("overwrite") \
        .option("header", True) \
        .csv("data/reports/gold_disease_totals")

    print("\nCSV report saved in data/reports/gold_disease_totals")

finally:
    spark.stop()
