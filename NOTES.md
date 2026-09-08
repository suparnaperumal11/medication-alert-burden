# NOTES — Medication Alert Burden

Running log of decisions, rejected alternatives and surprises. Feeds README
sections 5 (what I chose not to build) and 6 (the evaluation problem).

---

## Stage 1 — Environment

**Date:** 2026-09-07

| Component | Resolved to |
|---|---|
| Java | OpenJDK 21.0.12 LTS (Temurin) |
| Python | 3.11.1 via `py -3.11`. **Not** bare `python`, which resolves to a Python 2.7.11 MGLTools shim on this machine. |
| R | 4.5.3 at `C:\Program Files\R\R-4.5.3` — **not on PATH**, invoked by full path |
| Git | 2.55.0.windows.5 |
| Synthea | `synthea-with-dependencies.jar`, reused from an earlier project rather than re-downloaded |

### Decisions

**Python 3.11, not 3.14.** Both installed. 3.11 has settled wheel coverage for
the duckdb/streamlit/pyarrow chain; 3.14 is the newer default on this machine but
is the likelier place to lose an hour to a missing wheel. Nothing in this project
needs a 3.12+ language feature.

**Reused the seed, not the files.** An earlier FHIR→OMOP project on this machine
already had a seed-42 Synthea cohort, but exported as FHIR JSON (4.1 GB, 1,114
bundles). Re-exported the same seed as CSV instead of parsing that. Reasons: the
CSV exporter emits `medications.csv` with native RxNorm codes and `START`/`STOP`
columns, which is the exact shape this project needs, and walking 1,114 nested
FHIR bundles to recover MedicationRequests would be an ingest layer built for no
analytical gain. Same seed means the same simulated population either way.

**Hand-written net benefit rather than a package (Stage 8).** Neither `dcurves`
nor `rmda` is installed. Net benefit is
`NB = TP/n − (FP/n) × (p_t / (1 − p_t))` — about fifteen lines of dplyr. Writing
it keeps the threshold-weighting term visible in the code rather than inside a
function call. Rejected: installing `dcurves` for conventional plots.

**Power BI deferred.** Power BI Desktop is not installed on this machine.
Streamlit dashboard is built this session; the `.pbix` binds to a flat exported
table (a documented data contract) and is added later. Recorded so the README
does not claim a deliverable that is not in the repo.

### Repo trap worth remembering

`.gitignore` ignores `output/` (singular, scratch) but the committed deliverables
live in `outputs/` (plural). One character apart. Ignore rules were verified by
running `git check-ignore` against **real file paths** (`data/raw/medications.csv`,
`outputs/burden_summary.csv`), not the trailing-slash directory form — on git
2.55 the directory form reports matches that do not reflect what actually gets
staged.

---

## Stage 2 — Data

### Cohort

    java -jar synthea-with-dependencies.jar \
      -p 1000 -s 42 -cs 42 \
      --exporter.baseDirectory ./data/raw/synthea \
      --exporter.csv.export true \
      --exporter.fhir.export false

`-s 42` seeds the population, `-cs 42` seeds clinician generation. The earlier
project set only `-s`; adding `-cs` makes provider assignment reproducible too,
which matters here because providers are a candidate denominator. Consequence:
this cohort is **1,127 patients** where the earlier `-s 42`-only run produced
1,112. The populations are not identical and should not be compared across
projects.

**Cohort as generated:** 1,127 patients (1,000 alive, 127 deceased), 58,803
prescriptions, 65,896 encounters, 817 providers, 817 organizations.

### 🔍 DECISION POINT 1 — validating the prescriber denominator

**Finding 1 — there is no prescriber field on a prescription.**
`medications.csv` columns are `START, STOP, PATIENT, PAYER, ENCOUNTER, CODE,
DESCRIPTION, BASE_COST, PAYER_COVERAGE, DISPENSES, TOTALCOST, REASONCODE,
REASONDESCRIPTION`. No provider. The only route to a prescriber is
`medications.ENCOUNTER → encounters.Id → encounters.PROVIDER`.

**Finding 2 — that join is clean.** 58,803 of 58,803 prescriptions (100%) join
to an encounter, and 100% of those encounters carry a non-null `PROVIDER`. There
is no missingness to work around.

**Finding 3 — but `PROVIDER` and `ORGANIZATION` are the same partition.**
817 providers across 817 organizations, exactly one provider per organisation,
and every one of them has `SPECIALITY = 'GENERAL PRACTICE'`. Steps 2 and 3 of the
planned fallback ladder (encounter provider → organisation level) are not two
options. They are one option with two names. There are no specialists, no
hospital prescribers, and no multi-prescriber practices in this data.

**Finding 4 — the prescriber unit is far too thin to carry a workload metric.**
Within a five-year window (2021-09-07 to 2026-09-07):

| | |
|---|---|
| Providers issuing any prescription | 472 |
| Active prescriber-days (provider × day with ≥1 order) | 11,428 |
| Active prescribing days per provider | median 8, IQR 4–18, max 823 |
| Orders per active prescriber-day | mean 1.83, max 11 |

A real prescriber writes dozens of orders a day and fields tens of alerts. A
Synthea "prescriber" issues 1.83 orders on each of the 8 days in five years that
they are active. **Alerts per prescriber per day cannot be honestly
reconstructed from this data.** Reporting one would be a fabricated concept
dressed as a measurement.

**Finding 5 — the patient-day denominator is sound.** Same window: 11,659
patient-prescribing-days across 951 patients, mean 1.8 orders per patient-day,
and **41% of those days involve ≥2 distinct drug codes** — so co-prescription,
which is the precondition for any interaction pair, actually occurs at scale.

**Finding 6 — the full time span is unusable as a denominator.** Prescriptions
run 1934-06-24 to 2026-09-07 — 33,678 days. Synthea simulates whole lifetimes,
so an all-time rate divides by 92 years of mostly-empty history. Prescribing
volume only becomes dense from 2016 (2,519 orders, rising to ~4,000/year).

### Surprise worth flagging early

**The formulary is tiny: 245 distinct RxNorm codes across all 58,803
prescriptions.** Real prescribing runs to thousands of distinct products. This
caps how many DDInter pairs can ever fire, and it means the alert set will be
dominated by a handful of drugs regardless of anything the analysis does. The
Stage 4 concentration finding is therefore partly a property of Synthea's drug
model, not only of prescribing behaviour — and the README must say so rather
than presenting concentration as a discovered result.

Combination products are present and will need handling at ingredient level —
e.g. `Acetaminophen 300 MG / Hydrocodone Bitartrate 5 MG Oral Tablet` and
`insulin isophane human 70 / insulin regular human 30 [Humulin]`.

### ✅ DECISION POINT 1 — resolved

**Denominator: alerts per patient-prescribing-day, primary. Provider retained as
a concentration axis only.**

The planned fallback ladder was prescriber ID → encounter provider →
organisation → patient-day. Rung 1 does not exist in the data. Rungs 2 and 3 are
the same partition as each other (817 providers, 817 organizations, 1:1, all
general practice). Rung 2/3 is 100% populated and could have been used, and that
is the trap: it would have produced a clean-looking `alerts per prescriber-day`
for a prescriber who is active on a median of 8 days in five years and writes
1.83 orders on each. Computable, reproducible, and a fabrication. Went to rung 4.

Provider is still used, but only to answer *"is burden concentrated across
practices?"* — a question the data can support. It is never used as a workload
rate. Any figure with a per-prescriber denominator is out of scope for this
project by decision, not by oversight.

**Analysis window: 2021-09-07 to 2026-09-07 (five years, 1,826 days).**
Fixed before any alert logic was written. Contains 20,928 prescriptions, 951
patients, 472 providers, 11,659 patient-prescribing-days. Rejected: all-time
(divides by 92 years of near-empty simulated history) and last-3-years (thinner
cells for the Stage 8 subgroup analysis).

**Alert budget expressed per patient-prescribing-day.** Stage 7's table was
specified as alerts/day against one prescriber's attention. With the prescriber
unit unusable, the budget is instead "at most N alerts may fire while this
patient is being prescribed for". This keeps the clinical reading intact — a
prescriber attends to one patient at a time — without inventing a workload.
Rejected: a system-wide daily cap (the unit becomes an organisation, not a
person's attention) and a top-X% framing (unit-free, but loses the concrete
"how many interruptions" answer a pharmacy lead is actually asking for).

---

## Stage 2 — Reference data

### DDInter

**Licence, verified 2026-09-07: CC BY-NC-SA 4.0**, not "open access" without
qualification. Non-commercial and ShareAlike. Consequences honoured here: the
raw download is never committed, `src/ingest/fetch_ddinter.py` and a manifest
carrying attribution are committed instead, and the derived interaction tables
in `outputs/` are marked with the source. Fine for a non-commercial portfolio
project; would need renegotiating for anything else.

**The download page under-advertises the data.** It links eight ATC-letter CSVs
(A, B, D, H, L, P, R, V). Six more — **C, G, J, M, N, S** — are served from the
same URL pattern but are not linked anywhere on the page. They are not
marginal: N (nervous system, 91,595 rows) and C (cardiovascular, 60,454 rows)
are the two largest files, and between them cover most of what this cohort is
actually prescribed — lisinopril, amlodipine, HCTZ, simvastatin, hydrocodone.

Taking the download page at face value would have produced an alert set missing
the majority of relevant interactions, and nothing in the output would have
looked wrong. The burden numbers would simply have been quietly, plausibly too
low. `fetch_ddinter.py` requests all fourteen letters and validates each
response on its header row, because the server answers 200 for absent files.

**Consolidated reference standard:** 507,655 raw rows across 14 files →
**234,981 distinct unordered pairs** after canonicalising each pair to
(low ID, high ID). **Zero pairs carry conflicting severities** across files, so
the reference standard is at least internally consistent.

| Severity | Pairs | Share |
|---|---:|---:|
| Moderate | 143,748 | 61.17% |
| **Unknown** | **42,415** | **18.05%** |
| Major | 39,082 | 16.63% |
| Minor | 9,736 | 4.14% |

**`Unknown` at 18% was not anticipated and needs an explicit rule.** Policy B is
specified as "interrupt major, passive moderate, suppress minor" — it has no
branch for nearly a fifth of the knowledge base. Deferred to `eval/criteria.md`
so the decision is fixed in writing before any policy runs. Whatever is chosen,
suppressing Unknown and counting it as burden avoided would be a way of
manufacturing a good result.

### RxNorm

**No UMLS account is needed.** The plan budgeted for one. Synthea's
`medications.csv` CODE is already RxNorm at clinical-drug level, and RxNav's
public REST API resolves it to ingredients with no key and no registration.
Dependency dropped.

**All 245 distinct codes resolved, covering 58,803/58,803 prescriptions (100%).**
That required two fixes:

*Grouping bug, caught and corrected.* The first version grouped products by
(CODE, DESCRIPTION) while keying results by CODE. Seven codes carry two
description strings in this cohort, so those products split into two rows and
the second silently overwrote the first — reported as "240/252 resolved,
93.23% coverage" when the true figure was different. Both the numerator and the
denominator were wrong, and the output looked entirely plausible. Now grouped by
CODE with descriptions aggregated.

*Five retired RxCUIs.* `897685, 757594, 1723208, 749882, 105078` return nothing
from `/related`, `/allrelated` or `/properties` — they have been withdrawn from
RxNorm since Synthea's snapshot. 897685 is **verapamil**, a CYP3A4 inhibitor,
and alone accounts for 230 of the 231 affected prescriptions in the analysis
window. Resolved by an explicit five-row override table with ingredient RxCUIs
confirmed by name lookup, rather than by parsing description strings. Dropping
them would have shrunk the alert set in precisely the direction that flatters
burden figures.

**173 distinct ingredients; 39 combination products.**

### The RxNorm → DDInter join, and what it costs

RxNorm uses United States Adopted Names, DDInter uses International
Nonproprietary Names. Exact matching alone gives 149/172 ingredients but only
**58.2%** of prescriptions, because the highest-volume drugs are precisely the
ones whose names differ: albuterol/Salbutamol, aspirin/Acetylsalicylic acid,
alendronate/Alendronic acid, norethindrone/Norethisterone,
insulin isophane/`Insulin human (isophane)`.

Sixteen hand-checked synonym rows lift coverage to **82.0% of prescriptions
(165/172 ingredients)**. Rejected: fuzzy string matching. It would have mapped
`epoetin alfa` onto `Darbepoetin alfa` — a different drug with a different
interaction profile — and produced alerts indistinguishable from real ones.
Every mapping is exact and one line long so it can be audited.

Denominator note: these percentages are over 68,505 ingredient-prescription
rows, not 58,803 prescriptions, because a combination product contributes one
row per ingredient.

**A hard ceiling on the analysis.** 12,310 ingredient-prescriptions (18.0%)
involve drugs genuinely absent from DDInter and can never generate an alert
under any policy:

| Ingredient | Rows | Why |
|---|---:|---|
| **epoetin alfa** | **8,760** | No epoetin entry at all. DDInter has only Darbepoetin alfa. |
| sodium fluoride | 3,108 | No fluoride entry. Dental gel — arguably non-systemic anyway. |
| inert ingredients | 391 | Not a drug; artefact of Synthea's contraceptive pack descriptions. |
| protamine sulfate (USP) | 34 | Only appears inside combination insulin names. |
| tenofovir disoproxil / alafenamide | 16 | No tenofovir entry; only Adefovir dipivoxil, Cidofovir. |
| norethynodrel | 1 | Absent at any spelling. |

Epoetin alfa is the **single most-prescribed product in the cohort** and has
zero interaction coverage. This is a limitation of the reference standard, not
of the detection code, and it belongs in README section 6: any claim about
"total alert burden" here is a claim about the 82% of prescribing that DDInter
can see.

### Route, and a trap being set for Stage 3

Ingredient-level normalisation destroys route. 13.4% of prescriptions are
plausibly non-systemic — inhalation 5.79%, dental gel 5.29%, ophthalmic 1.33%,
topical 0.51%. Treating an eye drop or a fluoride gel as systemic exposure
manufactures interactions that cannot physically occur.

Usefully, **DDInter names carry route qualifiers themselves** — 169 entries such
as `Dorzolamide (ophthalmic)`, `Estradiol (topical)`,
`Beclomethasone dipropionate (nasal)`. So route is partly recoverable on the
reference side. A route flag is carried through from the product description so
Stage 3 can *measure* how many alerts depend on non-systemic exposure rather
than assuming the question away.

---

## Stage 3 — Alert generation (Policy A, the naive set)

### The alert model, and why it is not per-day

**An alert fires at the moment of prescribing, once per (triggering
prescription, interacting pair).** Not once per overlapping day, and not once
per overlapping prescription.

*Not per day*: the resource being budgeted is clinician attention, and the
clinician is interrupted when they write the order. One alert per day of
overlap would measure exposure rather than interruption, and would inflate
burden by whatever factor prescription durations happen to have.

*Not per overlapping prescription*: a patient with five active refills of
lisinopril has one lisinopril interaction, not five. Counting rows here would
measure repeat dispensing and call it alert burden. The collapse is done in
SQL with `COUNT(DISTINCT concurrent_rx_id)` retained on each alert, so the
number of prescriptions behind each alert stays inspectable.

Self-pairs (a drug against itself) are excluded — refills and dose changes
overlap constantly. Intra-product pairs are excluded and counted separately:
the two ingredients of a licensed combination tablet are co-formulated on
purpose, so warning about them is noise by construction.

### The overlap assumption is the weakest joint, and it moves burden 2×

Synthea's `STOP` is null for 4.87% of prescriptions, meaning "still active".
Concurrency therefore rests on an assumption, so the rule is a parameter and
every variant is reported:

| Overlap rule | Alerts | vs primary | Major | Distinct pairs |
|---|---:|---:|---:|---:|
| **assume_active** (primary) | **36,929** | 100.0% | 1,938 | 580 |
| assume_90d | 18,576 | 50.3% | 639 | 341 |
| assume_30d | 18,416 | 49.9% | 636 | 330 |
| assume_point | 18,042 | 48.9% | 631 | 307 |
| same_day_only | 15,230 | 41.2% | 662 | 164 |

**4.87% of prescriptions determine 50% of the alert burden.** Under
`assume_active` a null-STOP prescription stays live to the end of the window,
so every chronic medication interacts with everything subsequently prescribed —
which is clinically true and is also why the number is so assumption-sensitive.
`assume_active` is primary because it matches Synthea's own semantics, but no
burden figure in this project should be quoted without it.

Note the major-severity count barely moves between the bounded rules (631–662)
while total burden moves by 3,300. The assumption inflates *noise* far more
than it inflates the alerts that matter.

---

## Stage 4 — Burden (headline, before any prioritisation)

All normalisation sanity checks passed before concentration was reported: no
self-pairs, no non-canonical orderings, no duplicate (trigger, pair) rows, no
unmapped names. The top pairs are distinct ingredients, not one drug matched
against itself under two spellings.

### Headline

**36,929 alerts across 20,922 prescriptions, 951 patients and 472 practices
over 1,826 days — 3.17 alerts per patient-prescribing-day**, 1.77 per
prescription, 38.8 per patient over five years. 524 patients (55.1% of those
prescribed for) got at least one. 580 distinct interacting pairs.

### Severity — and a problem for Policy B

| Severity | Alerts | Share | Per patient-prescribing-day |
|---|---:|---:|---:|
| **Unknown** | **20,681** | **56.0%** | 1.774 |
| Moderate | 11,960 | 32.4% | 1.026 |
| Minor | 2,350 | 6.4% | 0.202 |
| Major | 1,938 | 5.2% | 0.166 |

`Unknown` was 18% of the DDInter knowledge base but is **56% of the realised
alert set** — the drugs this cohort actually co-prescribes are
disproportionately ones DDInter has not graded. Policy B is specified as
"interrupt major, passive moderate, suppress minor" and has no branch for the
majority of its own input. This must be settled in `eval/criteria.md` before
any policy runs.

### Concentration — weaker here than the literature, and that is the finding

| | Share of all alerts |
|---|---:|
| Top 1 pair | 4.7% |
| Top 5 pairs | 18.8% |
| Top 10 pairs | 28.2% |
| Top 20 pairs | 40.7% |

**90 pairs — 15.5% of the 580 distinct pairs — generate 80% of alerts.**

The published figure motivating this project was a single pair producing 49.8%
of all alerts at one hospital. Here the top pair produces 4.7%. **The
concentration is real but roughly an order of magnitude flatter**, and the
honest reading is that this is a property of Synthea rather than a
contradiction of the literature: a 245-product formulary prescribed to
guidelines cannot produce the long tail of a real hospital, and it has no
equivalent of the one badly-configured rule that generates half a real system's
alerts. Reported as a limitation, not as a softer version of the published
result.

Top pairs are clinically coherent — hydrochlorothiazide with insulin (thiazides
raise blood glucose), amlodipine with hydrochlorothiazide, lisinopril with
metoprolol — which is corroboration that the pipeline works, not a finding.

### Repeat exposure — this is the actual headline

**3,044 distinct (patient, pair) combinations generate 36,929 alerts. 91.8% of
all alerts are a repetition of a warning that patient has already received.**
Mean 12.1 repeats of the same warning to the same patient; median 3; **max
350**.

This is the "hundred distinct alerts versus the same three warnings a hundred
times" question, and the answer is emphatically the latter. It is the strongest
result in the project and it is what makes Policy C worth testing: the burden
is dominated not by breadth of interactions but by re-notification of
interactions already seen and evidently already tolerated.

### Concentration across practices

408 of 472 prescribing practices generated at least one alert. Median 23 alerts
per practice, p90 167, max 3,274. **Top 10 practices account for 39.5%, top 50
for 71.0%.** Reported as concentration only — never as a per-prescriber rate,
per Decision Point 1.

### Route — a measurable false-positive source

**19.5% of alerts (7,201) have a non-systemic drug on at least one side**, of
which 358 are major severity. The largest contributor is inhaled-with-systemic
at 3,469. Ingredient-level normalisation cannot distinguish an inhaled
corticosteroid from an oral one, so a fifth of this burden rests on exposure
that may not be systemic. Quantified rather than assumed away, and available as
a prioritisation feature in Stage 6.

---

## Stage 6 — Prioritisation features

**Leakage enforced mechanically.** `src/prioritisation/features.py` asserts that
no column in the feature matrix has a severity-derived name, and raises
`SystemExit` if one does. A warning would have been ignorable; this fails the
build. Policy D never sees the label it is judged against.

**Every feature is computed as at the instant the alert fires.** The repeat
counter is a running `ROW_NUMBER()` ordered by time, so an alert cannot be
scored using notifications that had not happened yet. A live system could not
have that information either.

**Comorbidity burden counts `(disorder)` rows only.** Synthea's conditions
table is dominated by social determinants — *Full-time employment (finding)*,
*Stress (finding)*, *Social isolation (finding)*, *Medication review due
(situation)* — recorded for nearly every patient. Counting all condition rows
would have produced a "comorbidity score" that largely measured how thoroughly
Synthea populated a social history. Renal conditions are enumerated by explicit
SNOMED code rather than substring match, so the list is auditable.

**Feature realised distributions:** age 75+ on 18,267 alerts; median 9
concurrently active ingredients; median 13 comorbidities; renal condition on
74.0% of alerts; median 24 prior notifications of the same warning (max 349).

### A concentration finding that emerged from the features

**69 of the 524 alerting patients (13.2%) have a renal condition, and they
average 399 alerts each against 21 for everyone else.** Burden concentrates far
more sharply in patients than in drug pairs: the top-1 pair is 4.7% of alerts,
but 13% of patients carry 74%. If a real system wanted a lever, patient
complexity is a stronger one than pair frequency.

---

## Stage 7 — The four policies

### Policy D weights, and why they are not fitted

Weights are assigned by clinical reasoning and stated in
`src/prioritisation/policies.py`, not learned. Fitting them would make D's
advantage circular — it would be optimised against the very label
`criteria.md` forbids it from seeing. Positive: age, polypharmacy, renal
impairment, comorbidity. Negative: crowded alert days, prior repeats of the
same warning, high-volume pairs, non-systemic route.

### Result: **D does not beat B. Decisively.**

Pre-registered test (criteria.md §10): at the 5-per-patient-day budget, D must
retain ≥5 pp more Major alerts than B at equal or lower burden.

| At budget 5/patient-day | Interrupts | Per patient-day | Major retained |
|---|---:|---:|---:|
| **B — severity only** | **1,691** | **0.30** | **87.3%** |
| D — context-aware | 19,372 | 3.41 | 28.0% |

Margin **−59.3 pp**, at 11× the burden. Not close. **Policy D has not been
retuned**, per §10.

Worse: **D is beaten by Policy A — random selection — at every budget below
10.** At budget 5, A retains 33.1% of Major alerts against D's 28.0% on an
identical interrupt count. A context-aware ranking performs *worse than
arbitrary*.

### Why — and this is the substantive finding

D's score is **almost orthogonal to severity**, and where it correlates, it
correlates the wrong way.

| Severity | Mean D score | Mean prior notifications | Share that are high-volume pairs |
|---|---:|---:|---:|
| Major | 0.15 | 54.5 | 0.0% |
| Moderate | 0.19 | 53.4 | 34.8% |
| Unknown | −0.06 | 45.6 | 22.4% |
| Minor | −0.50 | 42.4 | 68.5% |

Major and Moderate are indistinguishable by D's score (0.15 vs 0.19). And
**Major alerts have the *highest* mean repeat count of any severity (54.5)** —
so the repeat penalty, which is the single most powerful burden-reduction lever
available, systematically demotes exactly the alerts the safety metric protects.

The general statement: **patient context tells you which patients are complex.
It does not tell you which interactions are dangerous.** Those are different
questions, and only the second is what a severity grade answers. Polypharmacy,
age and renal impairment are properties of a person; severity is a property of
a drug pair. A prioritisation score built from the former cannot recover the
latter, and in this cohort it actively fights it.

This is the honest answer to the project's central question, and it favours the
simpler system: a pharmacy lead should deploy the severity rule, which they can
explain in a sentence, over a context score that is harder to justify and
performs worse.

### Policy C is identical to Policy B — also a finding

At every budget, C and B produce **exactly the same interrupt set**. Once
severity routing has already sent every non-Major alert to passive or batch,
frequency suppression has nothing left above the interruption line to suppress.

C is not inert; it moves **26,277 alerts from passive to batch** (B: 32,641
passive / 2,350 batch; C: 6,364 passive / 28,627 batch). Its entire effect is
on what a pharmacist reviews later, not on what interrupts a prescriber. That
is a real operational difference and worth having — but it is invisible to a
burden metric defined as interrupt count, which is how `criteria.md` defined it
in advance. Reported rather than redefined.

### Post-hoc observation — explicitly NOT part of the pre-registered comparison

**1,938 Major alerts arise from only 78 distinct (patient, pair) combinations —
a 24.8× repeat rate.** Even Policy B's interrupts are ~96% re-notifications. A
policy that interrupted once per (patient, pair) and then went passive would
notify 100% of major combinations at roughly 78 interrupts.

This is flagged as an observation, not a result. It was noticed after
unblinding and evaluating it as a fifth policy would be precisely the
after-the-fact tuning §10 forbids. It belongs in "what would be needed for real
use" as a hypothesis to test prospectively — and it would need override data to
test honestly, since the reason to re-warn is that the first warning may not
have been read.

---

## Stage 8 — R evaluation

### The clustering lesson, which is the methodological point of this stage

A logistic regression of `is_major ~ score_D_scaled` gives slope 0.569,
SE 0.165, **p = 0.00057**. Read naively that is a highly significant effect and
Policy D looks vindicated.

It is an artefact of pretending 36,929 alerts are 36,929 independent
observations. They are 524 patients, one of whom contributes 350 alerts, and
alerts within a patient share drugs, comorbidity and the same repeated pair.

Bootstrapping **patients** rather than alerts (B = 400, whole clusters
resampled):

| Statistic | 2.5% | Median | 97.5% |
|---|---:|---:|---:|
| AUC, Policy D score vs Major | **0.304** | 0.523 | **0.622** |
| Net benefit, Policy B @ budget 5 | 0.0178 | 0.0430 | 0.0834 |
| Net benefit, Policy D @ budget 5 | −0.0226 | **−0.0126** | **−0.0002** |
| Net benefit, interrupt all | −0.0329 | −0.0008 | 0.0498 |
| Major retained, B | 0.846 | 0.876 | 0.966 |
| Major retained, D | 0.199 | 0.289 | 0.620 |

**The AUC interval comfortably contains 0.5.** Policy D's discrimination is not
distinguishable from chance once patient clustering is respected. The naive
p-value said otherwise. This is the single most important statistical point in
the project and the one to be ready to defend.

**Policy D's net benefit is negative and its interval excludes zero.** At a 5%
threshold, D is worse than interrupting nobody at all — it spends interruptions
on non-Major alerts faster than it captures Major ones. Policy B's is positive
and excludes zero.

### Calibration

Decile bins with Wilson intervals (Wald intervals misbehave at a 5.25% base
rate). The curve is **not flat — it is hump-shaped**: 5.5% Major in the lowest
decile, peaking at 8.9% in decile 6, falling to **4.3% in the top decile, below
the 5.25% overall rate**. The alerts Policy D ranks most urgent are slightly
*less* likely to be Major than average. Figure text was corrected from "flat" to
describe this accurately.

### Subgroups (fixed in criteria.md before results)

| Subgroup | Alerts | Patients | Major kept, B | Major kept, D | D's AUC |
|---|---:|---:|---:|---:|---:|
| All | 36,929 | 524 | 87.3% | 28.0% | 0.539 |
| Older adults 65+ | 25,732 | 135 | 85.9% | 18.6% | 0.557 |
| High polypharmacy | 33,437 | 301 | 87.2% | 23.9% | 0.544 |
| Renal-relevant | 27,340 | 69 | 85.6% | 18.5% | 0.610 |

B leads in every subgroup. D is at its least bad on renal patients (AUC 0.610),
which is the one place its features carry a little signal — but it still loses
by ~67 percentage points on major retention.

### A circularity that must be stated, not hidden

**Policy B's dominance in the decision curve is partly definitional.** B's rule
is "interrupt iff severity is Major" and the outcome is "severity is Major", so
B has *zero false positives by construction* — which is exactly why its curve is
flat and positive across the whole threshold range. B is being scored against
the quantity it is defined on.

This does not make the analysis vacuous, but it relocates where the information
is. The real content is among the **label-blind** strategies: Policy D and
budgeted Policy A never see severity, and D fails to beat arbitrary selection.
The defensible claim is therefore not *"B is excellent"* but **"nothing that
avoids using severity manages to approximate it"** — the same conclusion Stage 7
reached by another route. This caveat is written into `R/evaluation.R` and
belongs in the README.

### Why net benefit was hand-written

`NB = TP/n − (FP/n) × (p_t/(1−p_t))`, about fifteen lines. Neither `dcurves`
nor `rmda` was installed, and writing it keeps the exchange-rate term visible:
at p_t = 0.05 the analyst is stating that one missed Major is worth nineteen
needless interruptions. A package call hides the one term that carries the
argument. Threshold range 1–30%: past ~20% the stated willingness-to-be-
interrupted exceeds the 5.25% base rate and every strategy collapses.

