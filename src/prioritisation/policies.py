"""Stage 7 -- the four policies and the alert budget.

Definitions are those fixed in eval/criteria.md before any of this was written.
Nothing here may be changed to improve a result; see criteria.md section 10.

HOW A BUDGET IS APPLIED

A policy does two separable things:

  1. Assigns each alert a base tier -- interrupt / passive / batch -- from its
     own rule.
  2. Provides a priority ordering, used to decide which alerts keep their
     interrupt when a budget binds.

A budget of N caps interrupts at N per patient-prescribing-day. Within each
patient-day, interrupt-eligible alerts are taken in the policy's priority order
until N is spent; the remainder are demoted to passive. Demotion is never
silent -- the tables report what moved.

Policy A has no priority ordering, because having none is what defines it. Under
a budget it therefore keeps an arbitrary N, implemented as a seeded shuffle so
the result is reproducible. That is not a strawman: a system that alerts on
everything and is then throttled really does drop alerts for no principled
reason.

Consequence worth stating plainly: at unlimited budget Policy D interrupts
everything, exactly as Policy A does, because D is a *ranking* and a ranking
without a budget excludes nothing. D is only meaningful under a constraint.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ALERTS = Path("outputs/alerts_primary.parquet")
FEATURES = Path("outputs/alert_features.parquet")
OUT = Path("outputs/policy_assignments.parquet")

SEED = 42
BUDGETS = [None, 10, 5, 3, 1]          # None = unlimited
POLICIES = ["A", "B", "C", "D"]

# Policy C parameters, fixed in criteria.md.
C_REPEAT_WINDOW_DAYS = 90
C_HIGH_VOLUME_TOP_N = 10

# ---------------------------------------------------------------------------
# Policy D weights.
#
# Assigned by reasoning and stated here, not fitted. Fitting them against the
# safety metric would make D's advantage circular: it would be optimised on the
# very label criteria.md forbids it from seeing.
#
# Sign conventions, each with its clinical argument:
#
#   age            Older patients have reduced clearance and less physiological
#                  reserve. Positive.
#   polypharmacy   More concurrent ingredients means more interaction surface
#                  and less chance any one prescriber holds the whole picture.
#                  Positive.
#   renal          Impaired elimination raises exposure for renally cleared
#                  drugs. Positive, and weighted highest of the patient terms.
#   comorbidity    A proxy for frailty and for competing clinical demands.
#                  Positive but small -- it overlaps heavily with polypharmacy.
#   crowded day    When many alerts fire for one patient at once, each
#                  individual one is less likely to be read. Negative: it argues
#                  for batching rather than adding to a pile.
#   prior repeats  A warning a prescriber has already dismissed dozens of times
#                  for this patient has been implicitly assessed. Strongly
#                  negative -- this is the lever aimed at the 91.8% repeat rate.
#   high volume    A pair that fires constantly cohort-wide is the classic
#                  low-yield alert. Negative.
#   non-systemic   Ingredient-level matching cannot rule out that the exposure
#                  is topical or inhaled. Negative, because the interaction may
#                  be physically impossible.
# ---------------------------------------------------------------------------
W = {
    "age_75plus": 2.0,
    "age_65_74": 1.0,
    "polypharm_10plus": 2.0,
    "polypharm_5_9": 1.0,
    "renal": 2.5,
    "comorbid_10plus": 0.5,
    "crowded_day_per_alert": -0.05,      # applied to n_simultaneous_alerts
    "prior_repeat_log2": -0.8,           # applied to log2(1 + prior_notifications)
    "high_volume_pair": -1.5,
    "non_systemic": -1.0,
}


def policy_d_score(f: pd.DataFrame) -> pd.Series:
    s = pd.Series(0.0, index=f.index)
    s += np.where(f.age_band == "75+", W["age_75plus"], 0.0)
    s += np.where(f.age_band == "65-74", W["age_65_74"], 0.0)
    s += np.where(f.n_active_ingredients >= 10, W["polypharm_10plus"],
                  np.where(f.n_active_ingredients >= 5, W["polypharm_5_9"], 0.0))
    s += f.has_renal_condition * W["renal"]
    s += (f.n_comorbidities >= 10) * W["comorbid_10plus"]
    s += f.n_simultaneous_alerts * W["crowded_day_per_alert"]
    s += np.log2(1 + f.prior_notifications) * W["prior_repeat_log2"]
    s += f.pair_is_high_volume * W["high_volume_pair"]
    s += f.involves_nonsystemic * W["non_systemic"]
    return s


def base_tiers(df: pd.DataFrame) -> pd.DataFrame:
    """Base tier and priority order for each policy, before any budget."""
    # Sort to a canonical order BEFORE drawing the seeded permutation.
    #
    # A fixed seed alone does not make this reproducible. rng.permutation()
    # assigns values positionally, and the alert table arrives from a DuckDB
    # GROUP BY, whose row order is not guaranteed stable between runs. Without
    # this sort, the same seed lands different arbitrary ranks on different
    # alerts each run, and Policy A -- whose whole definition is an arbitrary
    # ordering -- silently returns different Major-capture figures. It moved
    # 641 -> 578 Major alerts at budget 5 between two runs before this was
    # fixed. rank(method="first") in Policy D has the same exposure.
    #
    # The key is unique per alert, so the order is total and deterministic.
    df = (df.sort_values(["patient_id", "start_ts", "trigger_rx_id",
                          "pair_lo", "pair_hi"], kind="mergesort")
            .reset_index(drop=True))
    rng = np.random.default_rng(SEED)
    df["_arbitrary"] = rng.permutation(len(df))

    # --- Policy A: everything interrupts.
    df["tier_A"] = "interrupt"
    df["rank_A"] = df["_arbitrary"]                       # no principled order

    # --- Policy B: severity only. Unknown -> passive (criteria.md section 4).
    sev_tier = {"Major": "interrupt", "Moderate": "passive",
                "Unknown": "passive", "Minor": "batch"}
    df["tier_B"] = df.severity.map(sev_tier)
    df["rank_B"] = df["_arbitrary"]                       # no order within Major

    # --- Policy C: B, plus frequency suppression.
    # A repeat of the same (patient, pair) within 90 days is demoted to batch,
    # unless Major. Major always survives -- without that carve-out, frequency
    # suppression wins the burden comparison by silencing the alerts the safety
    # metric exists to protect (criteria.md section 6).
    # kind="mergesort" for a *stable* sort. The default quicksort reorders tied
    # rows arbitrarily, and ties are common here -- the same patient can have
    # several alerts for one pair at the identical timestamp. shift(1) below
    # then looks at a different "previous" alert between runs, so the recent
    # repeat flag, and with it tier_C, would vary despite the fixed seed.
    df = df.sort_values(["patient_id", "pair_lo", "pair_hi", "start_ts",
                         "trigger_rx_id"], kind="mergesort")
    prev = df.groupby(["patient_id", "pair_lo", "pair_hi"])["start_ts"].shift(1)
    days_since = (df.start_ts - prev).dt.days
    recent_repeat = days_since.notna() & (days_since <= C_REPEAT_WINDOW_DAYS)

    high_vol = df.pair_rank <= C_HIGH_VOLUME_TOP_N
    demote_C = (recent_repeat | high_vol) & (df.severity != "Major")

    df["tier_C"] = df["tier_B"]
    df.loc[demote_C, "tier_C"] = "batch"
    df["rank_C"] = df["_arbitrary"]
    df["_c_recent_repeat"] = recent_repeat
    df["_c_high_volume"] = high_vol

    # --- Policy D: context score. All alerts eligible; the budget decides.
    df["score_D"] = policy_d_score(df)
    df["tier_D"] = "interrupt"
    df["rank_D"] = (-df.score_D).rank(method="first")     # best score ranks first

    return df.drop(columns=["_arbitrary"])


def apply_budget(df: pd.DataFrame, policy: str, budget: int | None) -> pd.Series:
    """Cap interrupts at `budget` per patient-prescribing-day."""
    tier = df[f"tier_{policy}"].copy()
    if budget is None:
        return tier
    eligible = tier == "interrupt"
    order = (df.loc[eligible]
               .sort_values(["patient_id", "start_date", f"rank_{policy}"])
               .groupby(["patient_id", "start_date"]).cumcount())
    over = order[order >= budget].index
    tier.loc[over] = "passive"
    return tier


def main() -> int:
    a = pd.read_parquet(ALERTS)
    f = pd.read_parquet(FEATURES)

    key = ["trigger_rx_id", "pair_lo", "pair_hi"]
    df = a.merge(f.drop(columns=["patient_id", "start_date", "start_ts",
                                 "trigger_route", "concurrent_route"]),
                 on=key, how="left", validate="one_to_one")
    assert len(df) == len(a), "feature join changed row count"

    df = base_tiers(df)

    for p in POLICIES:
        for b in BUDGETS:
            col = f"assign_{p}_{'unlim' if b is None else b}"
            df[col] = apply_budget(df, p, b)

    # Canonical output order, so the committed artefact is byte-comparable
    # between runs rather than merely equivalent.
    df = (df.sort_values(["patient_id", "start_ts", "trigger_rx_id",
                          "pair_lo", "pair_hi"], kind="mergesort")
            .reset_index(drop=True))
    df.to_parquet(OUT, index=False)

    n_major = int((df.severity == "Major").sum())
    print(f"  {len(df):,} alerts, {n_major:,} Major\n")
    print(f"  Policy C demotions: recent repeat {int(df._c_recent_repeat.sum()):,}, "
          f"high-volume pair {int(df._c_high_volume.sum()):,}")
    print(f"  Policy D score: min {df.score_D.min():.2f}  median "
          f"{df.score_D.median():.2f}  max {df.score_D.max():.2f}\n")
    print(f"  base tiers (before any budget):")
    for p in POLICIES:
        vc = df[f"tier_{p}"].value_counts()
        print(f"    {p}: " + "  ".join(f"{k}={v:,}" for k, v in vc.items()))
    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
