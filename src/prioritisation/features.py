"""Stage 6 -- patient-context features for Policy D.

LEAKAGE RULE (eval/criteria.md section 3): DDInter severity is the evaluation
label, so it must not appear here or in anything derived from here. The feature
matrix is asserted severity-free at the end of this module, and the assertion
failing is a build failure.

Every feature is computed **as at the moment the alert fires**, not from the
patient's whole record. A feature that peeks at later prescribing would let
Policy D use information a live system could not have.

A note on "comorbidity burden". Synthea's conditions table is dominated by
social determinants -- "Full-time employment (finding)", "Stress (finding)",
"Social isolation (finding)", "Medication review due (situation)" -- which are
recorded for nearly every patient. Counting rows would produce a comorbidity
score that mostly measures how thoroughly Synthea filled in a social history.
Only descriptions marked "(disorder)" are counted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

DB = Path("data/alerts.duckdb")
ALERTS = Path("outputs/alerts_primary.parquet")
OUT = Path("outputs/alert_features.parquet")

WINDOW_END = "2026-09-07"

# Renal-relevant conditions present in this cohort, enumerated explicitly
# rather than matched by substring so the list is auditable.
RENAL_SNOMED = [
    "127013003",  # Disorder of kidney due to diabetes mellitus
    "431855005",  # CKD stage 1
    "431856006",  # CKD stage 2
    "433144002",  # CKD stage 3
    "431857002",  # CKD stage 4
    "46177005",   # End-stage renal disease
    "698306007",  # Awaiting transplantation of kidney
    "161665007",  # History of renal transplant
    "213150003",  # Kidney transplant failure and rejection
]

FORBIDDEN = ("severity", "major", "moderate", "minor", "unknown", "ddinter")


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    con.execute(f"CREATE OR REPLACE TEMP VIEW alerts AS "
                f"SELECT * FROM read_parquet('{ALERTS.as_posix()}')")

    renal_list = ", ".join(f"'{c}'" for c in RENAL_SNOMED)

    con.execute(f"""
    -- Exposure intervals under the primary overlap rule, matching Stage 3.
    CREATE OR REPLACE TEMP VIEW exposure AS
    SELECT r.rx_id, r.patient_id, r.rxnorm_ingredient, r.start_ts,
           COALESCE(r.stop_ts,
                    LEAST(COALESCE(p.deathdate::TIMESTAMP, DATE '{WINDOW_END}'::TIMESTAMP),
                          DATE '{WINDOW_END}'::TIMESTAMP)) AS effective_stop
    FROM rx_ingredient r LEFT JOIN patients p USING (patient_id);

    -- Distinct ingredients live for the patient at the instant of the alert.
    CREATE OR REPLACE TEMP VIEW polypharmacy AS
    SELECT a.trigger_rx_id, a.pair_lo, a.pair_hi,
           COUNT(DISTINCT e.rxnorm_ingredient) AS n_active_ingredients
    FROM alerts a
    JOIN exposure e
      ON e.patient_id = a.patient_id
     AND e.start_ts <= a.start_ts AND e.effective_stop > a.start_ts
    GROUP BY 1, 2, 3;

    -- Conditions active at the alert date. "(disorder)" only; see module docstring.
    CREATE OR REPLACE TEMP VIEW comorbid AS
    SELECT a.trigger_rx_id, a.pair_lo, a.pair_hi,
           COUNT(DISTINCT c.snomed_code) AS n_comorbidities,
           MAX(CASE WHEN c.snomed_code IN ({renal_list}) THEN 1 ELSE 0 END) AS has_renal_condition
    FROM alerts a
    LEFT JOIN conditions c
      ON c.patient_id = a.patient_id
     AND c.start_date <= a.start_date
     AND (c.stop_date IS NULL OR c.stop_date > a.start_date)
     AND c.description LIKE '%(disorder)%'
    GROUP BY 1, 2, 3;
    """)

    feats = con.sql(f"""
    WITH day_load AS (
      SELECT patient_id, start_date, COUNT(*) AS n_simultaneous_alerts
      FROM alerts GROUP BY 1, 2
    ),
    pair_volume AS (
      SELECT pair_lo, pair_hi, COUNT(*) AS pair_alerts,
             ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS pair_rank
      FROM alerts GROUP BY 1, 2
    ),
    repeats AS (
      -- How many times this patient has already had this exact warning.
      -- Ordered by time, so it is a running count and never sees the future.
      SELECT trigger_rx_id, pair_lo, pair_hi,
             ROW_NUMBER() OVER (PARTITION BY patient_id, pair_lo, pair_hi
                                ORDER BY start_ts, trigger_rx_id) - 1 AS prior_notifications
      FROM alerts
    )
    SELECT
      a.trigger_rx_id, a.patient_id, a.start_date, a.start_ts,
      a.pair_lo, a.pair_hi,
      a.trigger_route, a.concurrent_route,
      DATE_DIFF('year', p.birthdate, a.start_date) AS age_years,
      CASE WHEN DATE_DIFF('year', p.birthdate, a.start_date) < 40 THEN '<40'
           WHEN DATE_DIFF('year', p.birthdate, a.start_date) < 65 THEN '40-64'
           WHEN DATE_DIFF('year', p.birthdate, a.start_date) < 75 THEN '65-74'
           ELSE '75+' END AS age_band,
      COALESCE(pp.n_active_ingredients, 1) AS n_active_ingredients,
      COALESCE(cm.n_comorbidities, 0)      AS n_comorbidities,
      COALESCE(cm.has_renal_condition, 0)  AS has_renal_condition,
      dl.n_simultaneous_alerts,
      rp.prior_notifications,
      CASE WHEN pv.pair_rank <= 10 THEN 1 ELSE 0 END AS pair_is_high_volume,
      pv.pair_rank,
      CASE WHEN a.trigger_route <> 'systemic' OR a.concurrent_route <> 'systemic'
           THEN 1 ELSE 0 END AS involves_nonsystemic
    FROM alerts a
    LEFT JOIN patients p USING (patient_id)
    LEFT JOIN polypharmacy pp USING (trigger_rx_id, pair_lo, pair_hi)
    LEFT JOIN comorbid    cm USING (trigger_rx_id, pair_lo, pair_hi)
    LEFT JOIN day_load    dl ON dl.patient_id = a.patient_id AND dl.start_date = a.start_date
    LEFT JOIN repeats     rp USING (trigger_rx_id, pair_lo, pair_hi)
    LEFT JOIN pair_volume pv USING (pair_lo, pair_hi)
    """).df()

    # Enforcement, not decoration. eval/criteria.md section 3.
    leaked = [c for c in feats.columns if any(f in c.lower() for f in FORBIDDEN)]
    if leaked:
        raise SystemExit(f"LEAKAGE: severity-derived columns in feature matrix: {leaked}")

    feats.to_parquet(OUT, index=False)

    print(f"  {len(feats):,} alerts featurised -> {OUT}")
    print(f"  leakage assertion passed: no severity-derived column present\n")
    print("  feature distributions:")
    print(f"    age_band            {feats.age_band.value_counts().to_dict()}")
    print(f"    n_active_ingredients  median {feats.n_active_ingredients.median():.0f}  "
          f"p90 {feats.n_active_ingredients.quantile(.9):.0f}  max {feats.n_active_ingredients.max()}")
    print(f"    n_comorbidities       median {feats.n_comorbidities.median():.0f}  "
          f"p90 {feats.n_comorbidities.quantile(.9):.0f}  max {feats.n_comorbidities.max()}")
    print(f"    has_renal_condition   {int(feats.has_renal_condition.sum()):,} alerts "
          f"({100*feats.has_renal_condition.mean():.1f}%)")
    print(f"    n_simultaneous_alerts median {feats.n_simultaneous_alerts.median():.0f}  "
          f"max {feats.n_simultaneous_alerts.max()}")
    print(f"    prior_notifications   median {feats.prior_notifications.median():.0f}  "
          f"p90 {feats.prior_notifications.quantile(.9):.0f}  max {feats.prior_notifications.max()}")
    print(f"    pair_is_high_volume   {int(feats.pair_is_high_volume.sum()):,} alerts "
          f"({100*feats.pair_is_high_volume.mean():.1f}%)")
    print(f"    involves_nonsystemic  {int(feats.involves_nonsystemic.sum()):,} alerts "
          f"({100*feats.involves_nonsystemic.mean():.1f}%)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
