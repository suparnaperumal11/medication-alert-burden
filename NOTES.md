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

