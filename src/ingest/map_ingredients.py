"""Bridge RxNorm ingredient names to DDInter drug names.

This is the join on which the entire alert set depends, and it is lossy in two
different ways that must not be confused with each other:

  1. NAMING. RxNorm uses United States Adopted Names; DDInter uses
     International Nonproprietary Names. albuterol/Salbutamol,
     aspirin/Acetylsalicylic acid, norethindrone/Norethisterone are the same
     molecules under different national conventions. A missed match here is a
     defect in this code -- a real interaction the system fails to detect.

  2. COVERAGE. Some drugs are genuinely not in DDInter at all. Nothing can be
     done about those in this project, and pretending otherwise by mapping a
     drug to a near neighbour would corrupt the reference standard. They are
     listed explicitly below and reported as a ceiling on the analysis.

Distinguishing (1) from (2) is the point of this module. Fuzzy string matching
would blur them: it would silently map "epoetin alfa" to "Darbepoetin alfa",
which is a different drug with a different interaction profile, and the
resulting alerts would look like findings. Every mapping here is exact,
hand-checked and one line long.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

RX = Path("data/raw/rxnorm_ingredients.json")
DD = Path("data/raw/ddinter/ddinter_pairs_deduped.csv")
OUT = Path("outputs/ingredient_mapping.csv")

# RxNorm (USAN) name -> DDInter (INN) name. Each confirmed by checking the
# target string exists verbatim in the DDInter vocabulary.
USAN_TO_INN = {
    "albuterol": "Salbutamol",
    "alendronate": "Alendronic acid",
    "aspirin": "Acetylsalicylic acid",
    "ethinyl estradiol": "Ethinylestradiol",
    "vitamin B12": "Cyanocobalamin",
    "ferrous sulfate": "Ferrous sulfate anhydrous",
    "penicillin V": "Phenoxymethylpenicillin",
    "penicillin G": "Benzylpenicillin",
    "clavulanate": "Clavulanic acid",
    "medroxyprogesterone": "Medroxyprogesterone acetate",
    "norethindrone": "Norethisterone",
    "beclomethasone": "Beclomethasone dipropionate",
    "dorzolamide": "Dorzolamide (ophthalmic)",
    "proparacaine": "Proparacaine (ophthalmic)",
    "insulin isophane": "Insulin human (isophane)",
    "insulin, regular, human": "Insulin human (regular)",
}

# Genuinely absent from DDInter. Not naming variants -- there is no entry for
# these molecules at any spelling. Each is recorded with the reason, because
# the largest of them caps what this project can ever detect.
NOT_IN_DDINTER = {
    "epoetin alfa": "no epoetin entry; DDInter carries only Darbepoetin alfa, a different drug",
    "sodium fluoride": "no fluoride entry; the only match on 'fluoride' is Sulfur hexafluoride",
    "tenofovir disoproxil": "no tenofovir entry; only Adefovir dipivoxil and Cidofovir",
    "tenofovir alafenamide": "no tenofovir entry; only Adefovir dipivoxil and Cidofovir",
    "norethynodrel": "not present at any spelling",
    "protamine sulfate (USP)": "present only inside combination insulin names, not standalone",
    "inert ingredients": "not a drug; an artefact of Synthea's contraceptive pack descriptions",
}


def main() -> int:
    rx = json.loads(RX.read_text())
    dd = pd.read_csv(DD)

    vocab = pd.unique(pd.concat([dd.drug_lo, dd.drug_hi]).astype(str))
    by_lower = {n.strip().lower(): n for n in vocab}

    # Fail loudly if a curated target has drifted out of DDInter's vocabulary
    # rather than silently degrading to an unmatched ingredient.
    bad = [v for v in USAN_TO_INN.values() if v.strip().lower() not in by_lower]
    if bad:
        raise SystemExit(f"curated INN targets absent from DDInter: {bad}")

    ingredients: dict = {}
    for code, prod in rx["products"].items():
        for ing in prod["ingredients"]:
            rec = ingredients.setdefault(
                ing["name"], {"rxcui": ing["rxcui"], "prescriptions": 0, "routes": set()})
            rec["prescriptions"] += prod["prescriptions"]
            rec["routes"].add(prod["route_flag"])

    rows = []
    for name, rec in ingredients.items():
        target = USAN_TO_INN.get(name, name)
        matched = by_lower.get(target.strip().lower())
        if matched is not None:
            method = "curated_inn_synonym" if name in USAN_TO_INN else "exact"
            reason = ""
        else:
            method = "unmatched"
            reason = NOT_IN_DDINTER.get(name, "UNEXPLAINED - investigate before trusting results")
        rows.append({
            "rxnorm_ingredient": name,
            "rxcui": rec["rxcui"],
            "ddinter_drug": matched or "",
            "match_method": method,
            "prescriptions": rec["prescriptions"],
            "routes": "|".join(sorted(rec["routes"])),
            "unmatched_reason": reason,
        })

    m = pd.DataFrame(rows).sort_values("prescriptions", ascending=False)

    unexplained = m[m.unmatched_reason.str.startswith("UNEXPLAINED")]
    if len(unexplained):
        print("!! unmatched ingredients with no recorded reason:")
        print(unexplained[["rxnorm_ingredient", "prescriptions"]].to_string(index=False))

    total_rx = int(m.prescriptions.sum())
    for method, grp in m.groupby("match_method"):
        print(f"  {method:<20} {len(grp):>4} ingredients  "
              f"{int(grp.prescriptions.sum()):>7,} prescriptions "
              f"({100 * grp.prescriptions.sum() / total_rx:>5.1f}%)")

    matched = m[m.match_method != "unmatched"]
    print(f"\n  MAPPED   {len(matched):>4}/{len(m)} ingredients, "
          f"{int(matched.prescriptions.sum()):,}/{total_rx:,} prescriptions "
          f"({100 * matched.prescriptions.sum() / total_rx:.1f}%)")

    print("\n  Prescriptions that can NEVER generate an alert (drug absent from DDInter):")
    for r in m[m.match_method == "unmatched"].itertuples(index=False):
        print(f"    {r.prescriptions:>6,}  {r.rxnorm_ingredient:<26} {r.unmatched_reason[:58]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(OUT, index=False)
    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
