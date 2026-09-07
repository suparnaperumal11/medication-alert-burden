"""Load Synthea CSVs and the reference tables into a DuckDB analytic database.

Everything downstream reads from here, so this module fixes the analysis window
and the ingredient explosion in one place rather than letting each script make
its own quiet choices.

The window (2021-09-07 to 2026-09-07) was fixed at Decision Point 1, before any
alert logic existed. See NOTES.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

CSV = "data/raw/synthea/csv"
DB = Path("data/alerts.duckdb")

WINDOW_START = "2021-09-07"
WINDOW_END = "2026-09-07"


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
    CREATE OR REPLACE TABLE patients AS
    SELECT Id AS patient_id, BIRTHDATE::DATE AS birthdate, DEATHDATE::DATE AS deathdate,
           GENDER AS gender, RACE AS race, ETHNICITY AS ethnicity
    FROM read_csv_auto('{CSV}/patients.csv');

    CREATE OR REPLACE TABLE encounters AS
    SELECT Id AS encounter_id, PATIENT AS patient_id, START::TIMESTAMP AS start_ts,
           STOP::TIMESTAMP AS stop_ts, PROVIDER AS provider_id,
           ORGANIZATION AS organization_id, ENCOUNTERCLASS AS encounter_class
    FROM read_csv_auto('{CSV}/encounters.csv');

    CREATE OR REPLACE TABLE conditions AS
    SELECT PATIENT AS patient_id, START::DATE AS start_date,
           TRY_CAST(STOP AS DATE) AS stop_date,
           CODE::VARCHAR AS snomed_code, DESCRIPTION AS description
    FROM read_csv_auto('{CSV}/conditions.csv');

    -- Prescriptions. STOP is null for 4.87% of rows, which Synthea uses to mean
    -- "still active". Those are carried as NULL here and given an explicit
    -- assumed end downstream, where the assumption can be varied and tested,
    -- rather than being silently filled in at load time.
    -- medications.csv carries no prescription identifier, and (patient, code,
    -- start) is not unique in this cohort, so a surrogate rx_id is assigned
    -- here. Everything downstream needs to say "this prescription" without
    -- accidentally collapsing two genuinely separate orders into one.
    CREATE OR REPLACE TABLE medications AS
    SELECT ROW_NUMBER() OVER (ORDER BY m.PATIENT, m.START, m.CODE) AS rx_id,
           m.START::TIMESTAMP AS start_ts,
           TRY_CAST(m.STOP AS TIMESTAMP) AS stop_ts,
           m.PATIENT AS patient_id, m.ENCOUNTER AS encounter_id,
           m.CODE::VARCHAR AS rxnorm_code, m.DESCRIPTION AS description,
           e.provider_id, e.organization_id, e.encounter_class
    FROM read_csv_auto('{CSV}/medications.csv') m
    LEFT JOIN encounters e ON m.ENCOUNTER = e.encounter_id;

    CREATE OR REPLACE TABLE ingredient_map AS
    SELECT rxnorm_ingredient, rxcui::VARCHAR AS ingredient_rxcui,
           NULLIF(ddinter_drug, '') AS ddinter_drug,
           match_method, routes
    FROM read_csv_auto('outputs/ingredient_mapping.csv');

    CREATE OR REPLACE TABLE ddinter_pairs AS
    SELECT drug_lo, drug_hi, severity
    FROM read_csv_auto('data/raw/ddinter/ddinter_pairs_deduped.csv');
    """)

    # Product -> ingredient, one row per ingredient a product delivers. A
    # combination product produces two rows, which is what lets a single
    # prescription trigger several candidate pairs.
    rx = json.loads(Path("data/raw/rxnorm_ingredients.json").read_text())
    product_ingredient = pd.DataFrame([
        {"rxnorm_code": code,
         "rxnorm_ingredient": ing["name"],
         "route_flag": prod["route_flag"]}
        for code, prod in rx["products"].items()
        for ing in prod["ingredients"]
    ])
    con.register("_pi", product_ingredient)
    con.execute("CREATE OR REPLACE TABLE product_ingredient AS SELECT * FROM _pi")
    con.unregister("_pi")

    # The analytic spine: one row per (prescription, ingredient it delivers),
    # carrying the DDInter name where the ingredient has one. Prescriptions
    # whose ingredient is absent from DDInter are kept, not dropped -- they are
    # part of the prescribing denominator even though they can never alert.
    con.execute(f"""
    CREATE OR REPLACE TABLE rx_ingredient AS
    SELECT m.rx_id, m.patient_id, m.encounter_id, m.rxnorm_code, m.description,
           m.start_ts, m.stop_ts,
           m.start_ts::DATE AS start_date,
           m.provider_id, m.organization_id, m.encounter_class,
           pi.rxnorm_ingredient, pi.route_flag,
           im.ddinter_drug,
           im.ddinter_drug IS NOT NULL AS in_reference_standard
    FROM medications m
    JOIN product_ingredient pi ON m.rxnorm_code = pi.rxnorm_code
    LEFT JOIN ingredient_map im ON pi.rxnorm_ingredient = im.rxnorm_ingredient;

    -- The analysis window, fixed before any alert logic existed.
    CREATE OR REPLACE VIEW rx_window AS
    SELECT * FROM rx_ingredient
    WHERE start_date >= DATE '{WINDOW_START}' AND start_date < DATE '{WINDOW_END}';
    """)


def main() -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))
    build(con)
    for t in ["patients", "encounters", "conditions", "medications",
              "ingredient_map", "ddinter_pairs", "product_ingredient",
              "rx_ingredient", "rx_window"]:
        n = con.sql(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<22} {n:>9,}")
    con.close()
    print(f"\n  wrote {DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
