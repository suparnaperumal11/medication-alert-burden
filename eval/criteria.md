# Evaluation criteria

**Committed before any prioritisation code exists. The commit timestamp is the
point of this file.** Everything below — thresholds, tier rules, the definition
of success, and the conditions under which the project reports a negative
result — is fixed here so it cannot be adjusted after the numbers are seen.

Nothing in this document may be relaxed later. If something here turns out to
be wrong, the correction goes in `NOTES.md` with its reason and a new commit,
leaving the original visible in history.

---

## 1. What is being evaluated, and what is not

This project evaluates **alert prioritisation policies** against a measurable
trade-off between clinician attention spent and high-severity interactions
retained.

It is **not** an adverse-event prediction model. Synthea contains prescribing
events and no observed medication-related harm. There are no outcome labels,
and none are invented. No figure in this project estimates a patient's risk of
anything, because "risk of what?" has no answer in this data.

The two layers stay separate:

- **Layer 1, detection** — drug pair → DDInter → does an interaction exist, at
  what severity. A lookup.
- **Layer 2, prioritisation** — given that an interaction exists, should *this
  instance* interrupt. Output is a priority tier, which is a policy choice.

---

## 2. Reference standard

**DDInter severity** (`Major` / `Moderate` / `Minor` / `Unknown`), retrieved
2026-09-07, CC BY-NC-SA 4.0.

DDInter severity is an **expert-assigned property of a knowledge base**. It is
not an observed outcome, not a probability, and not evidence that harm did or
would occur. Every figure and every plot in this project that uses it must say
so.

### Known weaknesses of this reference standard, stated in advance

1. **It grades pairs, not situations.** A `Major` pair is major in general, not
   major for this patient on this day at this dose.
2. **Coverage gaps cap the analysis.** 18.0% of ingredient-prescriptions
   involve drugs absent from DDInter entirely and can never alert under any
   policy. The largest, **epoetin alfa (8,760 prescriptions), is the single
   most-prescribed product in the cohort**. Any statement about "total burden"
   is a statement about the 82% DDInter can see.
3. **`Unknown` is not a low-severity grade.** It means the knowledge base has
   no opinion. It is 18.0% of DDInter but **56.0% of the realised alert set**.
4. **No override data exists.** The published finding that >90% of alerts are
   overridden, and that >half of overrides are inappropriate at ~6× the adverse
   event rate, cannot be reproduced or tested here. There is no clinician
   response to observe.

---

## 3. The leakage rule, and how it is enforced

**DDInter severity is the evaluation label. It is therefore forbidden as a
prioritisation feature in Policy D.**

- Policy D's score is computed from **patient-context features only**. Severity,
  and anything derived from severity, must not appear in it.
- Policies A, B and C are *rules*, not fitted models. B and C use severity
  openly and by definition — that is what they are. This is not leakage,
  because they are not being credited with learning anything; they are the
  baselines D must beat.
- Enforcement is mechanical: `src/prioritisation/` asserts that the feature
  matrix handed to Policy D contains no column derived from `severity`, and the
  assertion failing is a build failure, not a warning.

This makes "does D beat B?" a real question. B is handed the label directly. D
never sees it. If D still wins, context carries information severity does not.
If D loses, that is the finding.

---

## 4. Severity tiers, including `Unknown`

`Unknown` is treated as **its own tier, meaning ungraded** — never folded into
Minor and never silently suppressed.

Rationale: "the reference standard has no opinion" and "the reference standard
says low risk" are different claims. Collapsing them would suppress 56% of the
alert set on no evidence and would hand Policies B and C a large, flattering
burden reduction that is an artefact of grading gaps rather than a clinical
judgement.

`Unknown` is reported as its own row in every severity table in this project.

---

## 5. Priority tiers

Every policy assigns each alert exactly one of:

| Tier | Meaning | Counts toward burden? |
|---|---|---|
| **interrupt** | Modal interruption at prescribing | **Yes** |
| **passive** | Visible in the record, no interruption | No |
| **batch** | Aggregated for later pharmacy review | No |

**Burden = count of `interrupt` alerts.** Passive and batch are not free in
reality, but they do not consume attention at the moment of prescribing, which
is the resource being budgeted. This is stated so that "burden reduction" can
never be achieved by relabelling without saying what happened to the alerts.

---

## 6. The four policies

Fixed definitions. No policy may be modified after results are seen.

| | Policy | Rule |
|---|---|---|
| **A** | Alert everything | Every DDInter hit interrupts. The conventional system and the burden baseline. |
| **B** | Severity-only | `Major` → interrupt; `Moderate` → passive; `Minor` → batch; `Unknown` → passive. |
| **C** | Frequency suppression | Policy B, plus: an alert that repeats a (patient, pair) combination already notified within the preceding **90 days** is demoted to `batch`, **unless it is `Major`**, which always survives. Also demotes to `batch` any non-`Major` alert whose pair is among the **top 10 alert-generating pairs**. |
| **D** | Context-aware | Ranks alerts by a transparent additive score over patient-context features only (§7), then assigns tiers by the budget in force. Severity is excluded from the score. |

Policy C's `Major`-always-survives carve-out is fixed here deliberately: without
it, frequency suppression would win the burden comparison by silencing exactly
the alerts the safety metric is meant to protect.

---

## 7. Policy D features — the permitted list

Patient-context only. This list is closed; nothing may be added later.

- Age band at prescribing (`<40`, `40–64`, `65–74`, `75+`)
- Count of concurrently active distinct ingredients (polypharmacy)
- Presence of a renal-relevant active condition
- Comorbidity burden (count of distinct active conditions)
- Number of simultaneous alerts for that patient on that day
- Repeat frequency of this (patient, pair) combination to date
- Whether the pair is a high-volume contributor cohort-wide
- Whether either drug is non-systemic by route

**Weights are assigned by reasoning and stated in the code, not fitted.** A
transparent weighted policy is preferred to an opaque model because every
component must be explainable. Weights are not tuned against the safety metric;
if they were, D's advantage would be circular.

---

## 8. Alert budgets

Budget is expressed **per patient-prescribing-day** (Decision Point 1: the
prescriber unit in Synthea cannot support a workload rate).

Levels are set from the observed Policy A distribution, which is known before
any prioritisation exists. On days generating ≥1 alert: mean 6.51, median 4,
p90 15, max 38.

| Budget | Rationale |
|---|---|
| Unlimited | Policy A baseline |
| 10 / patient-day | Caps 15.7% of alerting days |
| 5 / patient-day | Caps 38.8% |
| 3 / patient-day | Caps 52.1% |
| 1 / patient-day | Caps 83.4% — the severe-constraint case |

The plan's 20/day is **not** used: it binds on only 6.4% of days and would
produce a table of near-identical rows.

---

## 9. Metrics

### Primary safety metric

**Share of `Major` alerts retained as `interrupt`** — of the 1,938 `Major`
alerts in the primary alert set, what fraction still interrupts under a given
policy and budget.

### Primary burden metric

**Count and share of alerts assigned `interrupt`**, and alerts per
patient-prescribing-day.

### Pre-registered guard metric

**Share of distinct (patient, pair) `Major` combinations ever notified.**
Because 91.8% of alerts are repeats, a policy could score well on the primary
safety metric while never warning some patients at all, or vice versa. This
guard is declared now so it cannot be introduced later to rescue a result. It
is reported alongside the primary metric but does not replace it.

### Reporting rule

**Both sides of every threshold, always.** No burden figure appears in this
project without its high-severity capture, and no capture figure appears
without its burden. Either alone is misleading. Every rate carries its
denominator.

---

## 10. What counts as success — decided now

**The question is whether Policy D beats Policy B. The pre-registered answer
condition:**

> Policy D is judged to beat Policy B if, at the **5 alerts per
> patient-prescribing-day** budget, D retains **at least 5 percentage points
> more `Major` alerts** than B at equal or lower interrupt burden.

Anything less than that margin is reported as **"context-aware prioritisation
did not outperform a simple severity rule"**, and that is a legitimate result,
not a failure of the project. It is arguably the more useful finding for a
pharmacy lead, who would rather deploy a rule they can explain.

**Policy D will not be tuned until it wins.** If the first honest
parameterisation loses, the loss is reported. Any change to D's weights after
seeing results must be recorded in `NOTES.md` with its justification and the
before-and-after numbers.

---

## 11. Subgroups for Stage 8

Fixed in advance to prevent subgroup-hunting:

- **Older adults** — age ≥ 65 at the prescribing date
- **High polypharmacy** — ≥ 5 concurrently active distinct ingredients
- **Renal-relevant** — an active renal condition at the prescribing date

No other subgroup may be reported as a finding. Additional cuts, if explored,
are labelled exploratory.

---

## 12. Sensitivity that must be reported, not chosen

The overlap assumption changes total burden by a factor of two (36,929 alerts
under `assume_active` against ~18,000 under bounded rules). **Every headline
burden figure must be accompanied by its sensitivity range.** Selecting the
overlap rule that makes a policy look best is forbidden; `assume_active` is
primary because it matches Synthea's semantics, fixed here, before results.

---

## 13. What this evaluation cannot establish

Stated now so it cannot be quietly forgotten in the write-up:

- That any policy reduces adverse drug events. No outcome data exists.
- That suppressed alerts were safe to suppress. There is no override log, no
  clinician judgement, and no follow-up.
- That the burden figures resemble a real hospital's. Synthea prescribes to
  clinical guidelines and is tidier than reality; **every figure here is an
  optimistic upper bound on how clean a real alert set would look.**
- That DDInter severity is correct. It is one expert knowledge base among
  several that disagree with each other.
