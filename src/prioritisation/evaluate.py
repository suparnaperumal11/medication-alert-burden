"""Stage 7 -- the alert budget table and the safety-burden frontier.

Reporting rule from eval/criteria.md section 9: both sides of every threshold,
always. No burden figure appears without its high-severity capture, and no
capture figure without its burden. Every rate carries its denominator.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ASSIGN = Path("outputs/policy_assignments.parquet")
OUT = Path("outputs")

BUDGETS = [None, 10, 5, 3, 1]
POLICIES = {"A": "Alert everything", "B": "Severity-only",
            "C": "Frequency suppression", "D": "Context-aware"}

# Pre-registered success condition, criteria.md section 10.
DECISION_BUDGET = 5
DECISION_MARGIN_PP = 5.0


def main() -> int:
    df = pd.read_parquet(ASSIGN)
    total = len(df)
    major = df[df.severity == "Major"]
    n_major = len(major)
    pt_days = df.groupby(["patient_id", "start_date"]).ngroups
    major_combos = major.groupby(["patient_id", "pair_lo", "pair_hi"]).ngroups

    rows = []
    for p, label in POLICIES.items():
        for b in BUDGETS:
            col = f"assign_{p}_{'unlim' if b is None else b}"
            interrupt = df[col] == "interrupt"
            maj_int = interrupt & (df.severity == "Major")
            # Guard metric: distinct (patient, pair) Major combinations that
            # get interrupted at least once.
            combos_seen = (df[maj_int].groupby(["patient_id", "pair_lo", "pair_hi"])
                             .ngroups)
            rows.append({
                "policy": p, "policy_name": label,
                "budget": "Unlimited" if b is None else f"{b}/patient-day",
                "alerts_shown": int(interrupt.sum()),
                "pct_of_total": round(100 * interrupt.sum() / total, 1),
                "interrupts_per_patient_day": round(interrupt.sum() / pt_days, 2),
                "major_captured": int(maj_int.sum()),
                "pct_major_captured": round(100 * maj_int.sum() / n_major, 1),
                "major_combos_ever_notified": combos_seen,
                "pct_major_combos_notified": round(100 * combos_seen / major_combos, 1),
                "passive": int((df[col] == "passive").sum()),
                "batch": int((df[col] == "batch").sum()),
            })
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "alert_budget_table.csv", index=False)

    print(f"  Denominators: {total:,} alerts, {n_major:,} Major, "
          f"{pt_days:,} patient-prescribing-days, {major_combos:,} distinct Major "
          f"(patient, pair) combinations\n")
    print("=" * 100)
    print("ALERT BUDGET TABLE")
    print("=" * 100)
    for p, label in POLICIES.items():
        print(f"\nPolicy {p} - {label}")
        sub = t[t.policy == p]
        print(f"  {'Budget':<16}{'Shown':>9}{'% total':>9}{'/pt-day':>9}"
              f"{'Major kept':>12}{'% Major':>9}{'% Maj combos':>14}")
        for r in sub.itertuples(index=False):
            print(f"  {r.budget:<16}{r.alerts_shown:>9,}{r.pct_of_total:>9.1f}"
                  f"{r.interrupts_per_patient_day:>9.2f}{r.major_captured:>12,}"
                  f"{r.pct_major_captured:>9.1f}{r.pct_major_combos_notified:>14.1f}")

    # ------------------------------------------------------ the honest question
    print("\n" + "=" * 100)
    print(f"DOES D BEAT B?  Pre-registered test: at the {DECISION_BUDGET}/patient-day "
          f"budget, D must retain >= {DECISION_MARGIN_PP} pp")
    print("more Major alerts than B at equal or lower interrupt burden "
          "(eval/criteria.md section 10).")
    print("=" * 100)
    bud = f"{DECISION_BUDGET}/patient-day"
    B = t[(t.policy == "B") & (t.budget == bud)].iloc[0]
    D = t[(t.policy == "D") & (t.budget == bud)].iloc[0]
    print(f"  B: {B.alerts_shown:>7,} interrupts ({B.interrupts_per_patient_day:.2f}/pt-day)"
          f"   Major kept {B.pct_major_captured:>5.1f}%")
    print(f"  D: {D.alerts_shown:>7,} interrupts ({D.interrupts_per_patient_day:.2f}/pt-day)"
          f"   Major kept {D.pct_major_captured:>5.1f}%")
    margin = D.pct_major_captured - B.pct_major_captured
    lower_burden = D.alerts_shown <= B.alerts_shown
    verdict = (margin >= DECISION_MARGIN_PP) and lower_burden
    print(f"\n  Margin: {margin:+.1f} pp   D burden <= B burden: {lower_burden}")
    print(f"  VERDICT: {'D beats B' if verdict else 'D does NOT beat B'}")
    if not verdict:
        print("  -> Reported as: context-aware prioritisation did not outperform a")
        print("     simple severity rule. Policy D is NOT retuned (criteria.md s10).")

    # ----------------------------------------------------------------- frontier
    fig, ax = plt.subplots(figsize=(9, 6.5))
    colours = {"A": "#666666", "B": "#1b6ca8", "C": "#c2410c", "D": "#15803d"}
    markers = {"A": "o", "B": "s", "C": "^", "D": "D"}
    # B and C produce identical interrupt sets (see NOTES.md): once severity
    # routing has already made every non-Major non-interrupting, frequency
    # suppression has nothing left above the interruption line to suppress.
    # C is drawn dashed and slightly wider so the coincidence is visible as a
    # result rather than looking like a missing series.
    styles = {"A": dict(linewidth=1.6), "B": dict(linewidth=3.2, alpha=0.9),
              "C": dict(linewidth=1.4, linestyle="--"), "D": dict(linewidth=1.6)}
    for p, label in POLICIES.items():
        sub = t[t.policy == p].sort_values("alerts_shown")
        suffix = " (identical to B)" if p == "C" else ""
        ax.plot(sub.interrupts_per_patient_day, sub.pct_major_captured,
                marker=markers[p], color=colours[p], markersize=7,
                label=f"{p} - {label}{suffix}", **styles[p])
        for r in sub.itertuples(index=False):
            if r.budget != "Unlimited":
                ax.annotate(r.budget.split("/")[0],
                            (r.interrupts_per_patient_day, r.pct_major_captured),
                            textcoords="offset points", xytext=(5, -9),
                            fontsize=7, color=colours[p])
    ax.set_xlabel("Burden: interrupting alerts per patient-prescribing-day")
    ax.set_ylabel("Safety: % of Major-severity alerts retained")
    ax.set_title("Safety-burden frontier\n"
                 "Reference standard: DDInter expert-assigned severity "
                 "(a knowledge-base property, not an observed outcome)",
                 fontsize=10.5)
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.annotate("B and C coincide exactly:\nfrequency suppression acts only\n"
                "below the interruption line",
                xy=(0.30, 87.3), xytext=(1.15, 72),
                fontsize=8, color="#c2410c",
                arrowprops=dict(arrowstyle="->", color="#c2410c", linewidth=0.9))
    ax.set_ylim(-3, 104)
    fig.text(0.01, 0.01,
             f"n = {total:,} alerts across {df.patient_id.nunique():,} patients and "
             f"{pt_days:,} patient-prescribing-days, 2021-09-07 to 2026-09-07. "
             f"Labels are budgets per patient-day.",
             fontsize=7, color="#444444")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT / "safety_burden_frontier.png", dpi=200)
    print(f"\n  wrote alert_budget_table.csv and safety_burden_frontier.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
