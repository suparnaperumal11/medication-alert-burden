"""Normalise Synthea's prescribed products to RxNorm ingredient level.

Synthea's medications.csv CODE column is already an RxNorm code, but at the
clinical-drug level -- "Acetaminophen 300 MG / Hydrocodone Bitartrate 5 MG Oral
Tablet". Interaction knowledge is stated between *ingredients*, so every
prescription has to be reduced to the ingredient(s) it delivers.

This uses RxNav's public REST API, which needs no UMLS account and no API key.

WHAT NORMALISING TO INGREDIENT LEVEL DISCARDS -- and why each matters:

  Salt form.  hydrocodone bitartrate (PIN 142439) collapses to hydrocodone
    (IN 5489). Correct here: interaction knowledge is about the moiety. Costs
    nothing analytically.

  Dose and frequency.  amlodipine 2.5 MG and amlodipine 10 MG become the same
    ingredient. This is a real loss -- interaction severity is often
    dose-dependent -- and it is not recoverable. Recorded as a limitation.

  Route.  THE DANGEROUS ONE. An ingredient-level match cannot tell a systemic
    tablet from a topical gel, an inhaler, or an eye drop. Sodium fluoride oral
    gel and albuterol inhalation solution are both high-volume in this cohort.
    Treating them as systemic exposure manufactures interactions that could not
    occur. Route is captured here (from the product description) so Stage 3 can
    quantify how many alerts depend on it, rather than discarding it silently.

  Combination products.  One prescription can deliver two ingredients, so it
    explodes into two rows. Consequence: a single co-prescribing event may
    generate several candidate pairs, and a combination product can even
    interact with itself. Handled explicitly in Stage 3, not here.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import requests

MEDS = Path("data/raw/synthea/csv/medications.csv")
OUT = Path("data/raw/rxnorm_ingredients.json")
API = "https://rxnav.nlm.nih.gov/REST/rxcui/{}/related.json?tty=IN"

# Route inferred from the product description. Only used to flag prescriptions
# whose exposure is plausibly non-systemic, so their contribution to the alert
# set can be measured. Deliberately coarse -- a screening flag, not a route
# ontology.
NON_SYSTEMIC_PATTERNS = [
    (r"\b(topical|cream|ointment|lotion|transdermal|patch)\b", "topical"),
    (r"\b(ophthalmic|eye drops?)\b", "ophthalmic"),
    (r"\b(otic|ear drops?)\b", "otic"),
    (r"\b(inhalation|inhaler|nebuli[sz]|metered dose|dry powder)\b", "inhalation"),
    (r"\b(oral gel|dental|toothpaste)\b", "dental"),
    (r"\b(vaginal|rectal|suppositor)\b", "local"),
    (r"\b(intrauterine|implant)\b", "implant"),
]

# Five product codes in Synthea's RxNorm snapshot have since been retired from
# RxNorm: /related, /allrelated and /properties all return nothing for them.
# They are not obscure. 897685 is verapamil, a CYP3A4 inhibitor that interacts
# with a good deal of this cohort's formulary, and it alone accounts for 230 of
# the 231 affected prescriptions inside the analysis window.
#
# Dropping them would quietly shrink the alert set in exactly the direction
# that flatters the burden numbers. Parsing the description string instead
# would be fragile. So the mapping is stated explicitly, one line per code,
# with ingredient RxCUIs confirmed by name lookup against current RxNorm.
# Five hand-checked rows beat a regex nobody can audit.
RETIRED_CODE_OVERRIDES = {
    "897685": [("11170", "verapamil")],                               # Calan 80 MG
    "757594": [("31983", "norethindrone")],                           # Jolivette 28 Day pack
    "749882": [("6782", "mestranol"), ("31983", "norethindrone")],    # Norinyl 1+50 pack
    "1723208": [("203203", "alfentanil")],                            # alfentanil injection
    "105078": [("7980", "penicillin G")],                             # penicillin G injection
}


def infer_route(description: str) -> str:
    d = description.lower()
    for pattern, label in NON_SYSTEMIC_PATTERNS:
        if re.search(pattern, d):
            return label
    return "systemic"


def main() -> int:
    meds = pd.read_csv(MEDS, usecols=["CODE", "DESCRIPTION"])

    # Group by CODE alone, not by (CODE, DESCRIPTION). Seven codes in this
    # cohort carry two different description strings, so grouping on the pair
    # splits one product across two rows and then -- because results are keyed
    # by code -- silently overwrites one with the other, understating both the
    # resolved count and the prescription coverage.
    products = (meds.groupby("CODE")
                    .agg(prescriptions=("DESCRIPTION", "size"),
                         DESCRIPTION=("DESCRIPTION", lambda s: s.mode().iat[0]),
                         n_descriptions=("DESCRIPTION", "nunique"))
                    .reset_index()
                    .sort_values("prescriptions", ascending=False))
    n_multi = int((products["n_descriptions"] > 1).sum())
    print(f"  {len(products):,} distinct RxNorm codes across {len(meds):,} prescriptions")
    print(f"  {n_multi} codes carry more than one description string")

    session = requests.Session()
    session.headers["User-Agent"] = "medication-alert-burden/0.1 (research)"

    resolved: dict = {}
    failed: list = []
    overridden: list = []

    for i, row in enumerate(products.itertuples(index=False), 1):
        code = str(row.CODE)
        try:
            resp = session.get(API.format(code), timeout=60)
            resp.raise_for_status()
            groups = (resp.json().get("relatedGroup") or {}).get("conceptGroup") or []
            ingredients = [
                {"rxcui": p["rxcui"], "name": p["name"]}
                for g in groups if g.get("tty") == "IN"
                for p in (g.get("conceptProperties") or [])
            ]
        except Exception as exc:                      # noqa: BLE001
            failed.append({"code": code, "description": row.DESCRIPTION, "error": str(exc)})
            continue

        source = "rxnav_related_IN"
        if not ingredients and code in RETIRED_CODE_OVERRIDES:
            ingredients = [{"rxcui": rxcui, "name": name}
                           for rxcui, name in RETIRED_CODE_OVERRIDES[code]]
            source = "retired_code_override"
            overridden.append(code)

        if not ingredients:
            failed.append({"code": code, "description": row.DESCRIPTION,
                           "error": "no IN concept returned and no override"})
            continue

        resolved[code] = {
            "description": row.DESCRIPTION,
            "prescriptions": int(row.prescriptions),
            "route_flag": infer_route(row.DESCRIPTION),
            "ingredients": ingredients,
            "mapping_source": source,
        }
        if i % 50 == 0:
            print(f"    resolved {i}/{len(products)}")
        time.sleep(0.06)                              # RxNav asks for <= 20 req/s

    n_rx_total = int(products["prescriptions"].sum())
    n_rx_resolved = sum(v["prescriptions"] for v in resolved.values())
    combos = {k: v for k, v in resolved.items() if len(v["ingredients"]) > 1}
    routes = Counter(v["route_flag"] for v in resolved.values())
    rx_by_route: Counter = Counter()
    for v in resolved.values():
        rx_by_route[v["route_flag"]] += v["prescriptions"]
    distinct_ingredients = {i["rxcui"]: i["name"]
                            for v in resolved.values() for i in v["ingredients"]}

    print(f"\n  codes resolved         : {len(resolved):,} / {len(products):,}")
    print(f"    via RxNav            : {len(resolved) - len(overridden):,}")
    print(f"    via retired override : {len(overridden):,}  {overridden}")
    print(f"  prescriptions covered  : {n_rx_resolved:,} / {n_rx_total:,} "
          f"({100 * n_rx_resolved / n_rx_total:.2f}%)")
    print(f"  unresolved codes       : {len(failed):,}")
    for f in failed:
        print(f"      {f['code']}  {f['description'][:60]}  <- {f['error'][:45]}")
    print(f"  distinct ingredients   : {len(distinct_ingredients):,}")
    print(f"  combination products   : {len(combos):,}")
    print("\n  prescriptions by inferred route:")
    for r, n in rx_by_route.most_common():
        print(f"    {r:<12} {n:>7,}  ({100 * n / n_rx_resolved:>5.2f}%)  [{routes[r]} codes]")

    OUT.write_text(json.dumps({
        "source": "RxNav REST API (https://rxnav.nlm.nih.gov/) - no account required",
        "retrieved": date.today().isoformat(),
        "tty_requested": "IN",
        "codes_resolved": len(resolved),
        "resolved_via_retired_code_override": overridden,
        "codes_failed": len(failed),
        "failed": failed,
        "prescriptions_covered": n_rx_resolved,
        "prescriptions_total": n_rx_total,
        "distinct_ingredients": len(distinct_ingredients),
        "products": resolved,
    }, indent=2))
    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
