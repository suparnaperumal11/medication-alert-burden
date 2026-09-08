"""Layer 1 -- knowledge-based detection. Build the naive alert set (Policy A).

This is a lookup, not a model. A prescription is written; whatever else the
patient is already on is checked against DDInter; every hit is an alert. That
is what a conventional interruptive DDI system does, and it is the baseline the
other three policies are measured against.

THE ALERT MODEL

An alert fires at the moment of prescribing, not once per day of overlap. That
is deliberate: the thing being budgeted is clinician attention, and the
clinician is interrupted when they write the order. Counting one alert per
overlapping day would measure exposure, not interruption, and would inflate
burden by a factor set by prescription duration.

So: for each prescription written inside the analysis window, for each
ingredient it delivers, against each ingredient already active for that patient
at that instant -- if DDInter knows the pair, that is one alert.

THE OVERLAP ASSUMPTION -- the weakest joint in Stage 3

Synthea gives START and STOP, and STOP is null for 4.87% of prescriptions,
meaning "still active". Whether two prescriptions are concurrent therefore
depends on an assumption, and burden depends on the assumption. Rather than
picking one and hoping, the rule is a parameter and every variant is reported:

  assume_active  (primary)  null STOP runs to window end, or death if earlier.
                            Synthea's own semantics: these are chronic meds.
  assume_90d                null STOP treated as a 90-day supply.
  assume_30d                null STOP treated as a 30-day supply.
  assume_point              null STOP treated as a single-day exposure. The
                            most conservative reading; a floor on burden.
  same_day_only             ignores durations entirely and pairs only
                            prescriptions written on the same calendar day.
                            Tests the trap that same-day is not concurrency --
                            here it is used the other way round, as the
                            narrowest possible definition of co-prescribing.

The primary rule is assume_active. The others exist so the sensitivity of every
headline number can be stated rather than assumed away.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

DB = Path("data/alerts.duckdb")
OUT_DIR = Path("outputs")

WINDOW_START = "2021-09-07"
WINDOW_END = "2026-09-07"

RULES = ["assume_active", "assume_90d", "assume_30d", "assume_point", "same_day_only"]


def effective_stop_sql(rule: str) -> str:
    """SQL for a prescription's assumed exposure end."""
    death = f"LEAST(COALESCE(p.deathdate::TIMESTAMP, DATE '{WINDOW_END}'::TIMESTAMP), " \
            f"DATE '{WINDOW_END}'::TIMESTAMP)"
    if rule == "assume_active":
        return f"COALESCE(r.stop_ts, {death})"
    if rule == "assume_90d":
        return "COALESCE(r.stop_ts, r.start_ts + INTERVAL 90 DAY)"
    if rule == "assume_30d":
        return "COALESCE(r.stop_ts, r.start_ts + INTERVAL 30 DAY)"
    if rule in ("assume_point", "same_day_only"):
        return "COALESCE(r.stop_ts, r.start_ts + INTERVAL 1 DAY)"
    raise ValueError(rule)


def build_alerts(con: duckdb.DuckDBPyConnection, rule: str) -> pd.DataFrame:
    # Concurrency predicate. Under same_day_only, durations are ignored
    # entirely and only the calendar date of writing is compared.
    if rule == "same_day_only":
        concurrent = "c.start_date = t.start_date AND c.rx_id <> t.rx_id"
    else:
        # The other prescription must have started at or before the trigger and
        # still be running at that instant. Strict inequality on the end means
        # a prescription that stops exactly as another starts is not concurrent.
        concurrent = ("c.start_ts <= t.start_ts AND c.effective_stop > t.start_ts "
                      "AND c.rx_id <> t.rx_id")

    con.execute(f"""
    CREATE OR REPLACE TEMP VIEW exposure AS
    SELECT r.*, {effective_stop_sql(rule)} AS effective_stop
    FROM rx_ingredient r
    LEFT JOIN patients p USING (patient_id);

    CREATE OR REPLACE TEMP VIEW triggers AS
    SELECT * FROM exposure
    WHERE start_date >= DATE '{WINDOW_START}' AND start_date < DATE '{WINDOW_END}'
      AND ddinter_drug IS NOT NULL;

    CREATE OR REPLACE TEMP VIEW candidate AS
    SELECT
      t.rx_id            AS trigger_rx_id,
      t.patient_id, t.start_date, t.start_ts,
      t.provider_id, t.organization_id, t.encounter_class,
      t.rxnorm_ingredient AS trigger_ingredient,
      t.ddinter_drug      AS trigger_drug,
      t.route_flag        AS trigger_route,
      t.description       AS trigger_description,
      c.rx_id            AS concurrent_rx_id,
      c.rxnorm_ingredient AS concurrent_ingredient,
      c.ddinter_drug      AS concurrent_drug,
      c.route_flag        AS concurrent_route,
      LEAST(t.ddinter_drug, c.ddinter_drug)    AS pair_lo,
      GREATEST(t.ddinter_drug, c.ddinter_drug) AS pair_hi
    FROM triggers t
    JOIN exposure c
      ON c.patient_id = t.patient_id
     AND {concurrent}
    WHERE c.ddinter_drug IS NOT NULL
      -- An ingredient does not interact with itself. This matters because
      -- refills and dose changes of the same drug overlap constantly.
      AND c.ddinter_drug <> t.ddinter_drug;
    """)

    # Intra-product pairs: the two ingredients of one combination tablet. These
    # are excluded from the alert set -- the product exists as a licensed
    # combination, so warning a prescriber that co-formulated amoxicillin and
    # clavulanate interact is noise by construction -- but they are counted, so
    # the exclusion is visible rather than silent.
    intra = con.sql("SELECT COUNT(*) FROM candidate WHERE trigger_rx_id = concurrent_rx_id"
                    ).fetchone()[0]

    # One alert per (triggering prescription, interacting drug pair) -- NOT one
    # per overlapping prescription. A patient on five active refills of
    # lisinopril has one lisinopril interaction, not five. Collapsing here
    # rather than counting rows is the difference between measuring
    # interruptions and measuring repeat dispensing. n_concurrent_rx retains
    # how many prescriptions backed the alert, so the collapse stays visible.
    alerts = con.sql("""
    SELECT
      c.trigger_rx_id, c.patient_id, c.start_date, c.start_ts,
      c.provider_id, c.organization_id, c.encounter_class,
      c.trigger_ingredient, c.trigger_drug, c.trigger_route, c.trigger_description,
      c.concurrent_ingredient, c.concurrent_drug, c.concurrent_route,
      c.pair_lo, c.pair_hi, d.severity,
      COUNT(DISTINCT c.concurrent_rx_id) AS n_concurrent_rx
    FROM candidate c
    JOIN ddinter_pairs d ON d.drug_lo = c.pair_lo AND d.drug_hi = c.pair_hi
    WHERE c.trigger_rx_id <> c.concurrent_rx_id
    GROUP BY ALL
    """).df()
    alerts["overlap_rule"] = rule
    alerts.attrs["intra_product_pairs_excluded"] = intra
    return alerts


def main() -> int:
    con = duckdb.connect(str(DB))
    OUT_DIR.mkdir(exist_ok=True)

    summaries = []
    for rule in RULES:
        a = build_alerts(con, rule)
        n_rx = con.sql(f"""SELECT COUNT(DISTINCT rx_id) FROM rx_ingredient
                           WHERE start_date >= DATE '{WINDOW_START}'
                             AND start_date <  DATE '{WINDOW_END}'""").fetchone()[0]
        n_pt = con.sql(f"""SELECT COUNT(DISTINCT patient_id) FROM rx_ingredient
                           WHERE start_date >= DATE '{WINDOW_START}'
                             AND start_date <  DATE '{WINDOW_END}'""").fetchone()[0]
        pt_days = con.sql(f"""SELECT COUNT(*) FROM (
                              SELECT DISTINCT patient_id, start_date FROM rx_ingredient
                              WHERE start_date >= DATE '{WINDOW_START}'
                                AND start_date <  DATE '{WINDOW_END}')""").fetchone()[0]
        sev = a.severity.value_counts()
        summaries.append({
            "overlap_rule": rule,
            "alerts": len(a),
            "alerts_per_patient_prescribing_day": round(len(a) / pt_days, 3),
            "distinct_pairs": a[["pair_lo", "pair_hi"]].drop_duplicates().shape[0],
            "patients_with_alert": a.patient_id.nunique(),
            "major": int(sev.get("Major", 0)),
            "moderate": int(sev.get("Moderate", 0)),
            "minor": int(sev.get("Minor", 0)),
            "unknown": int(sev.get("Unknown", 0)),
            "intra_product_excluded": a.attrs["intra_product_pairs_excluded"],
            "prescriptions_in_window": n_rx,
            "patients_in_window": n_pt,
            "patient_prescribing_days": pt_days,
        })
        print(f"  {rule:<15} {len(a):>8,} alerts   "
              f"{len(a)/pt_days:>7.3f} per patient-prescribing-day   "
              f"{a[['pair_lo','pair_hi']].drop_duplicates().shape[0]:>4} distinct pairs")

        if rule == "assume_active":
            # Canonical row order on write. DuckDB does not guarantee GROUP BY
            # output order, and anything downstream that assigns by position --
            # Policy A's seeded permutation, Policy D's rank tie-break -- would
            # otherwise vary run to run despite a fixed seed.
            (a.sort_values(["patient_id", "start_ts", "trigger_rx_id",
                            "pair_lo", "pair_hi"], kind="mergesort")
              .reset_index(drop=True)
              .to_parquet(OUT_DIR / "alerts_primary.parquet", index=False))

    s = pd.DataFrame(summaries)
    s.to_csv(OUT_DIR / "overlap_sensitivity.csv", index=False)
    print(f"\n  primary rule = assume_active -> {OUT_DIR/'alerts_primary.parquet'}")
    print(f"  sensitivity  -> {OUT_DIR/'overlap_sensitivity.csv'}")
    print("\n  sensitivity of total burden to the overlap assumption:")
    base = s.loc[s.overlap_rule == "assume_active", "alerts"].iat[0]
    for r in s.itertuples(index=False):
        print(f"    {r.overlap_rule:<15} {r.alerts:>8,}  "
              f"({100*r.alerts/base:>6.1f}% of primary)   major={r.major:,}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
