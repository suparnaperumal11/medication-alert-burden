# Power BI data contract

Power BI Desktop is not installed on the machine this analysis was built on, so
the `.pbix` is not in this repo. The Streamlit app in `dashboard/app.py` is the
runnable dashboard; this file is the contract a `.pbix` binds to, so the Power
BI version can be built later without re-deriving anything.

**Do not point Power BI at `data/`.** Everything it needs is in `outputs/`,
which is committed.

---

## Tables

### 1. `outputs/policy_assignments.parquet` — the fact table

One row per alert: 36,929 rows. Power BI reads Parquet natively
(Get Data → Parquet), or run `python src/prioritisation/export_for_r.py` for a
flat CSV of the same thing.

| Column | Type | Notes |
|---|---|---|
| `trigger_rx_id` | int | Surrogate key of the prescription that fired the alert |
| `patient_id` | text | Synthea patient UUID |
| `start_date` | date | Date the triggering prescription was written |
| `start_ts` | timestamp | Instant the alert fires |
| `provider_id`, `organization_id` | text | **Concentration axis only — never a workload rate.** See NOTES.md, Decision Point 1 |
| `encounter_class` | text | ambulatory / outpatient / wellness / urgentcare / inpatient / emergency |
| `trigger_drug`, `concurrent_drug` | text | DDInter drug names |
| `pair_lo`, `pair_hi` | text | Canonical (alphabetical) pair — **use these to group by pair**, not trigger/concurrent, which are order-dependent |
| `severity` | text | `Major` / `Moderate` / `Minor` / `Unknown` — **the reference standard** |
| `trigger_route`, `concurrent_route` | text | systemic / inhalation / dental / ophthalmic / topical / implant / local |
| `n_concurrent_rx` | int | How many active prescriptions backed this alert |
| `age_years`, `age_band` | int / text | At the alert date |
| `n_active_ingredients` | int | Polypharmacy at the alert instant |
| `n_comorbidities` | int | Active `(disorder)` conditions only |
| `has_renal_condition` | 0/1 | |
| `n_simultaneous_alerts` | int | Alerts for that patient that day |
| `prior_notifications` | int | Times this (patient, pair) already warned |
| `pair_is_high_volume` | 0/1 | Pair in the cohort-wide top 10 |
| `involves_nonsystemic` | 0/1 | |
| `score_D` | float | Policy D context score |
| `tier_A` … `tier_D` | text | Base tier before budget |
| `assign_{A,B,C,D}_{unlim,10,5,3,1}` | text | **20 columns.** Final tier: `interrupt` / `passive` / `batch` |

### 2. `outputs/alert_budget_table.csv` — pre-aggregated

20 rows (4 policies × 5 budgets). Use this for the KPI cards and the frontier if
you would rather not write DAX.

### 3. Supporting

| File | Contents |
|---|---|
| `outputs/burden_by_pair.csv` | Alerts per drug pair with cumulative share |
| `outputs/burden_by_severity.csv` | Alert volume by severity |
| `outputs/burden_summary.csv` | One row: every headline figure and denominator |
| `outputs/overlap_sensitivity.csv` | Burden under all five overlap assumptions |
| `outputs/r_subgroups.csv` | Subgroup performance from Stage 8 |
| `outputs/ingredient_mapping.csv` | RxNorm → DDInter bridge, incl. unmatched drugs |

---

## Required measures

Burden is the count of `interrupt` rows for the selected `assign_*` column.
Passive and batch are **not** burden — they do not consume attention at
prescribing.

```
Alerts shown        = CALCULATE(COUNTROWS(alerts), alerts[assign_X] = "interrupt")
Major captured      = CALCULATE(COUNTROWS(alerts),
                        alerts[assign_X] = "interrupt", alerts[severity] = "Major")
Patient-days        = DISTINCTCOUNT(alerts[patient_id] & "|" & alerts[start_date])
Interrupts / pt-day = [Alerts shown] / [Patient-days]
% Major retained    = DIVIDE([Major captured],
                        CALCULATE(COUNTROWS(alerts), alerts[severity] = "Major"))
```

`Patient-days` must be a distinct count of the **patient-date pair**, not of
dates. Counting dates alone would divide by ~1,600 instead of 5,677 and inflate
every rate by roughly 3.5×.

---

## Non-negotiable on any page that ships

1. The callout **"Not an adverse-event prediction model. No outcome labels
   exist in this data."**
2. Every severity visual labelled **"DDInter expert-assigned severity — a
   knowledge-base property, not an observed outcome."**
3. Every rate shown with its denominator.
4. Burden and Major-retention shown **together**. Either alone is misleading,
   and a Power BI page is exactly where a burden-reduction number gets screenshotted
   without its safety cost.
5. No per-prescriber rate. `provider_id` answers "is burden concentrated across
   practices?" and nothing else.
