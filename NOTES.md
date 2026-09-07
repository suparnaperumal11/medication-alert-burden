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

