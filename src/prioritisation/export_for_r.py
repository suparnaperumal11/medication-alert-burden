"""Flatten the policy assignments to a CSV the R evaluation reads.

R on this machine has no arrow package, so the handoff is a plain CSV. It is
regenerable in one command and is gitignored; the committed artefacts of Stage 8
are the R script and the figures and tables it produces.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ASSIGN = Path("outputs/policy_assignments.parquet")
OUT = Path("outputs/alerts_for_r.csv")

COLS = [
    "patient_id", "start_date", "pair_lo", "pair_hi", "severity",
    "age_years", "age_band", "n_active_ingredients", "n_comorbidities",
    "has_renal_condition", "n_simultaneous_alerts", "prior_notifications",
    "pair_is_high_volume", "involves_nonsystemic", "score_D",
]


def main() -> int:
    df = pd.read_parquet(ASSIGN)

    out = df[COLS].copy()
    out["is_major"] = (df.severity == "Major").astype(int)

    # Rescale Policy D's additive score to [0, 1] so decision curve analysis has
    # something to sweep a threshold over. This is a monotone rescaling and
    # NOTHING is refitted -- the weights in policies.py are untouched, and the
    # ranking is identical. It buys comparability with the threshold axis, not
    # calibration: a rescaled score is not a probability, and the calibration
    # plot in evaluation.R is precisely the check of how far from one it is.
    lo, hi = df.score_D.min(), df.score_D.max()
    out["score_D_scaled"] = (df.score_D - lo) / (hi - lo)

    for p in ["A", "B", "C", "D"]:
        for b in ["unlim", "10", "5", "3", "1"]:
            col = f"assign_{p}_{b}"
            out[f"interrupt_{p}_{b}"] = (df[col] == "interrupt").astype(int)

    # Pre-registered subgroups (eval/criteria.md section 11).
    out["sg_older"] = (df.age_years >= 65).astype(int)
    out["sg_polypharmacy"] = (df.n_active_ingredients >= 5).astype(int)
    out["sg_renal"] = df.has_renal_condition.astype(int)

    OUT.parent.mkdir(exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"  {len(out):,} rows x {out.shape[1]} cols -> {OUT}")
    print(f"  Major prevalence: {out.is_major.mean():.4f} "
          f"({int(out.is_major.sum()):,} of {len(out):,})")
    print(f"  patients (clustering unit for the bootstrap): {out.patient_id.nunique():,}")
    print(f"  score_D_scaled range: {out.score_D_scaled.min():.3f} "
          f"to {out.score_D_scaled.max():.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
