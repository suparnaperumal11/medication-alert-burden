"""Stage 9 -- policy simulator.

This is meant to let a pharmacy informatics lead *simulate a decision*, not
browse a dataset. Pick a policy, set an attention budget, filter to a
population, and see immediately what it costs in high-severity coverage.

Run:  .venv/Scripts/streamlit run dashboard/app.py

The headline constraint is repeated on the page rather than buried in a
footnote: this is not an adverse-event prediction model, and DDInter severity
is an expert-assigned knowledge-base property rather than an observed outcome.
A dashboard is exactly where that caveat gets lost, so it is pinned to the top.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
ASSIGN = ROOT / "outputs" / "policy_assignments.parquet"
PPD = ROOT / "outputs" / "patient_prescribing_days.csv"

POLICIES = {
    "A - Alert everything": "A",
    "B - Severity only": "B",
    "C - Frequency suppression": "C",
    "D - Context-aware": "D",
}
BUDGETS = {"Unlimited": "unlim", "10 per patient-day": "10",
           "5 per patient-day": "5", "3 per patient-day": "3",
           "1 per patient-day": "1"}
SUBGROUPS = {
    "All alerts": None,
    "Older adults (65+)": lambda d: d.age_years >= 65,
    "High polypharmacy (5+ active ingredients)": lambda d: d.n_active_ingredients >= 5,
    "Renal-relevant condition": lambda d: d.has_renal_condition == 1,
}

st.set_page_config(page_title="Medication Alert Burden", layout="wide")


@st.cache_data
def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_parquet(ASSIGN), pd.read_csv(PPD)


try:
    df, ppd = load()
except FileNotFoundError:
    st.error(
        "outputs/policy_assignments.parquet not found. Build it first:\n\n"
        "```\npython src/ingest/build_db.py\n"
        "python src/alerts/generate_alerts.py\n"
        "python src/prioritisation/features.py\n"
        "python src/prioritisation/policies.py\n```")
    st.stop()

st.title("Medication alert burden: prioritising clinician attention")

st.warning(
    "**Not an adverse-event prediction model.** There are no outcome labels in "
    "this data. The reference standard is **DDInter severity** - an "
    "expert-assigned property of a knowledge base, not observed patient harm. "
    "Nothing here estimates a patient's risk of anything. Synthetic prescribing "
    "data (Synthea, seed 42); Synthea prescribes to clinical guidelines and is "
    "tidier than reality, so every figure is an optimistic upper bound."
)

# ------------------------------------------------------------------ controls
c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
policy_label = c1.selectbox("Policy", list(POLICIES), index=1)
budget_label = c2.selectbox("Alert budget", list(BUDGETS), index=2)
subgroup_label = c3.selectbox("Population", list(SUBGROUPS))

policy, budget = POLICIES[policy_label], BUDGETS[budget_label]
col = f"assign_{policy}_{budget}"

mask = SUBGROUPS[subgroup_label]
d = df if mask is None else df[mask(df)]
if d.empty:
    st.info("No alerts in this subgroup.")
    st.stop()

interrupt = d[col] == "interrupt"
n_all = len(d)
n_major = int((d.severity == "Major").sum())
major_kept = int((interrupt & (d.severity == "Major")).sum())

# Denominator: prescribing days, not alerting days. Grouping the alert table
# would count only days that generate an alert and report a rate about twice as
# large under the same name Stage 4 uses.
#
# For the whole cohort this is every prescribed-for patient (951 patients,
# 11,655 days), which is what outputs/alert_budget_table.csv uses -- restricting
# to the 524 patients who happen to alert would quietly shrink the denominator
# to 9,170 and inflate every rate. For a subgroup it is that subgroup's own
# patients, since a subgroup is only identifiable from alerts.
if mask is None:
    pt_days = int(ppd.prescribing_days.sum())
else:
    pt_days = int(ppd.loc[ppd.patient_id.isin(d.patient_id.unique()),
                          "prescribing_days"].sum())
alerting_days = d.groupby(["patient_id", "start_date"]).ngroups

# Policy A unlimited is the burden baseline for the same subgroup.
base = int((d["assign_A_unlim"] == "interrupt").sum())

# ----------------------------------------------------------------- KPI cards
k1, k2, k3, k4 = st.columns(4)
k1.metric("Interrupting alerts per patient-prescribing-day",
          f"{interrupt.sum() / pt_days:.2f}",
          help=f"{int(interrupt.sum()):,} interrupts over {pt_days:,} "
               f"patient-prescribing-days")
k2.metric("Burden reduction vs alert-everything",
          f"{100 * (1 - interrupt.sum() / base):.1f}%" if base else "n/a",
          help=f"Baseline {base:,} interrupts under Policy A, unlimited")
k3.metric("Major interactions retained",
          f"{100 * major_kept / n_major:.1f}%" if n_major else "n/a",
          help=f"{major_kept:,} of {n_major:,} Major-severity alerts still interrupt")
top_pair_share = (d.groupby(["pair_lo", "pair_hi"]).size().max() / n_all * 100)
k4.metric("Top pair's share of all alerts", f"{top_pair_share:.1f}%",
          help="Concentration check: how much of the raw alert volume "
               "one drug pair accounts for")

st.caption(
    f"Denominators: **{n_all:,} alerts**, {n_major:,} Major, "
    f"{d.patient_id.nunique():,} patients, {pt_days:,} patient-prescribing-days "
    f"({alerting_days:,} of which generate at least one alert), "
    f"window 2021-09-07 to 2026-09-07."
)

# ------------------------------------------------------------ main: frontier
st.subheader("Safety-burden frontier")
st.caption("Each point is one policy at one budget, for the selected "
           "population. Up is safer, left is less burdensome.")

rows = []
for plabel, p in POLICIES.items():
    for blabel, b in BUDGETS.items():
        c = f"assign_{p}_{b}"
        it = d[c] == "interrupt"
        mk = int((it & (d.severity == "Major")).sum())
        rows.append({
            "Policy": plabel, "Budget": blabel,
            "Interrupts per patient-day": round(it.sum() / pt_days, 3),
            "% Major retained": round(100 * mk / n_major, 1) if n_major else 0.0,
            "Alerts shown": int(it.sum()),
            "% of all alerts": round(100 * it.sum() / n_all, 1),
            "Major kept": mk,
        })
frontier = pd.DataFrame(rows)

st.scatter_chart(frontier, x="Interrupts per patient-day",
                 y="% Major retained", color="Policy", height=430)

# ------------------------------------------------------------ budget table
st.subheader("Alert budget table")
sel = frontier[frontier.Policy == policy_label][
    ["Budget", "Alerts shown", "% of all alerts",
     "Interrupts per patient-day", "Major kept", "% Major retained"]]
st.dataframe(sel, hide_index=True, width="stretch")

if policy in ("B", "C"):
    st.info(
        "Policies B and C produce **identical interrupt sets**. Once severity "
        "routing has sent every non-Major alert below the interruption line, "
        "frequency suppression has nothing left above it to suppress. C still "
        "moves ~26,000 alerts from passive to batch, so its effect lands on "
        "what a pharmacist reviews later, not on what interrupts a prescriber."
    )
if policy == "D":
    st.info(
        "Policy D was tested against a pre-registered condition (retain >=5 pp "
        "more Major alerts than Policy B at the 5-per-patient-day budget) and "
        "**failed by 59.3 pp**. Its AUC against Major severity is 0.54, with a "
        "patient-clustered 95% interval of 0.30-0.62 that contains chance. It "
        "has not been retuned. Patient context tells you which patients are "
        "complex, not which interactions are dangerous."
    )

# ------------------------------------------------------- secondary: top pairs
st.subheader("Top alert-generating pairs")
pairs = (d.groupby(["pair_lo", "pair_hi", "severity"], observed=True)
           .size().reset_index(name="Alerts")
           .sort_values("Alerts", ascending=False).head(15))
pairs["% of all alerts"] = (100 * pairs.Alerts / n_all).round(1)
pairs["Still interrupting"] = [
    int(((d.pair_lo == r.pair_lo) & (d.pair_hi == r.pair_hi) & interrupt).sum())
    for r in pairs.itertuples(index=False)
]
pairs = pairs.rename(columns={"pair_lo": "Drug A", "pair_hi": "Drug B",
                              "severity": "DDInter severity"})
st.dataframe(pairs, hide_index=True, width="stretch")

# ---------------------------------------------------------------- repetition
rep_combos = d.groupby(["patient_id", "pair_lo", "pair_hi"]).ngroups
st.subheader("How much of this is repetition?")
st.markdown(
    f"**{n_all:,} alerts** arise from **{rep_combos:,} distinct "
    f"(patient, drug pair) combinations** - "
    f"**{100 * (n_all - rep_combos) / n_all:.1f}% of alerts re-notify a warning "
    f"that patient has already received.** This, rather than concentration in a "
    f"few drug pairs, is the dominant structure in the burden: the top pair "
    f"accounts for only {top_pair_share:.1f}% of alerts."
)

with st.expander("What this dashboard does not show"):
    st.markdown(
        "- **No outcome data.** No adverse events are recorded in Synthea, so "
        "no policy here can be shown to prevent harm.\n"
        "- **Reference standard has gaps.** 18% of ingredient-prescriptions "
        "involve drugs absent from DDInter and can never alert. The largest, "
        "epoetin alfa, is the most-prescribed product in the cohort.\n"
        "- **Overlap assumption moves burden 2x.** 4.87% of prescriptions have "
        "no stop date; treating them as ongoing doubles total alerts from "
        "~18,000 to 36,929.\n"
        "- **19.5% of alerts** involve a drug that may not be systemically "
        "absorbed - ingredient-level matching cannot exclude inhalers, eye "
        "drops or topical products.\n"
        "- **Policy B's apparent excellence is partly definitional**: it is "
        "defined on severity and scored against severity, so it has no false "
        "positives by construction."
    )
