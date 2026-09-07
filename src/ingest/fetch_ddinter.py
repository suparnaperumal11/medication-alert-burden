"""Fetch the DDInter drug-drug interaction reference standard.

DDInter is the *reference standard* for this project: an expert-assigned
severity property of a knowledge base. It is not an observed outcome. Nothing
downstream may treat a DDInter severity as evidence that harm occurred.

Licence: the DDInter download data are published under CC BY-NC-SA 4.0. This
repository therefore commits fetch code and derived tables only, never the raw
download, and the derived interaction table carries the attribution recorded in
data/raw/ddinter/MANIFEST.json.

Source: https://ddinter.scbdd.com/download/
Citation: Xiong et al., DDInter, Nucleic Acids Research 50(D1):D1200-D1207, 2022.
          Updated in DDInter 2.0, Nucleic Acids Research 53(D1):D1356-D1362, 2025.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://ddinter.scbdd.com/static/media/download/ddinter_downloads_code_{}.csv"

# The download page links only eight of these. The remaining six -- C, G, J, M,
# N, S -- are served at the same URL pattern but are not advertised. They are
# not optional extras: C (cardiovascular) and N (nervous system) are the two
# largest files and cover most of the drugs this cohort actually prescribes.
# Fetching only the linked eight would silently drop the majority of relevant
# interactions, and the resulting alert set would look clean rather than
# incomplete.
ATC_LETTERS = ["A", "B", "C", "D", "G", "H", "J", "L", "M", "N", "P", "R", "S", "V"]

EXPECTED_COLUMNS = ["DDInterID_A", "Drug_A", "DDInterID_B", "Drug_B", "Level"]

RAW_DIR = Path("data/raw/ddinter")


def fetch_one(letter: str, session: requests.Session) -> tuple[Path, dict]:
    """Download one ATC-letter file and validate it is really the CSV we want.

    The server answers 200 for absent files too, so a status check alone proves
    nothing. Validate on the header row instead.
    """
    url = BASE.format(letter)
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    body = resp.content

    header = body[:200].decode("utf-8", errors="replace").splitlines()[0].strip()
    if header != ",".join(EXPECTED_COLUMNS):
        raise ValueError(
            f"ATC {letter}: unexpected header {header!r}. "
            "DDInter's schema may have changed; do not proceed on a guess."
        )

    path = RAW_DIR / f"ddinter_code_{letter}.csv"
    path.write_bytes(body)
    return path, {
        "atc_letter": letter,
        "url": url,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "advertised_on_download_page": letter in set("ABDHLPRV"),
    }


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "medication-alert-burden/0.1 (research; contact via repo)"

    files, frames = [], []
    for letter in ATC_LETTERS:
        path, meta = fetch_one(letter, session)
        df = pd.read_csv(path)
        meta["rows"] = len(df)
        files.append(meta)
        frames.append(df)
        print(f"  {letter}  {meta['rows']:>7,} rows  {meta['bytes']:>10,} bytes"
              f"  {'' if meta['advertised_on_download_page'] else '(unadvertised)'}")

    raw = pd.concat(frames, ignore_index=True)

    # A pair spanning two ATC classes appears in both letter files, so the
    # concatenation double-counts. Deduplicate on the *unordered* pair by
    # sorting the two DDInter IDs into a canonical (low, high) orientation --
    # an interaction between X and Y is the same fact as one between Y and X.
    lo = raw[["DDInterID_A", "DDInterID_B"]].min(axis=1)
    hi = raw[["DDInterID_A", "DDInterID_B"]].max(axis=1)
    name_lo = raw["Drug_A"].where(raw["DDInterID_A"] == lo, raw["Drug_B"])
    name_hi = raw["Drug_B"].where(raw["DDInterID_A"] == lo, raw["Drug_A"])

    pairs = pd.DataFrame({
        "ddinter_id_lo": lo, "drug_lo": name_lo,
        "ddinter_id_hi": hi, "drug_hi": name_hi,
        "severity": raw["Level"],
    })

    # Before collapsing duplicates, check the duplicates agree. A pair carrying
    # two different severities in two files would mean the reference standard
    # contradicts itself, which the analysis must not average away silently.
    conflicts = (pairs.groupby(["ddinter_id_lo", "ddinter_id_hi"])["severity"]
                      .nunique().pipe(lambda s: s[s > 1]))

    deduped = pairs.drop_duplicates(subset=["ddinter_id_lo", "ddinter_id_hi"]).reset_index(drop=True)

    print(f"\n  raw rows across 14 files : {len(raw):,}")
    print(f"  distinct unordered pairs : {len(deduped):,}")
    print(f"  pairs with conflicting severity across files : {len(conflicts):,}")
    print("\n  severity distribution:")
    for level, n in deduped["severity"].value_counts(dropna=False).items():
        print(f"    {str(level):<12} {n:>7,}  ({100*n/len(deduped):>5.2f}%)")

    manifest = {
        "source": "DDInter (https://ddinter.scbdd.com/download/)",
        "licence": "CC BY-NC-SA 4.0 (download data)",
        "retrieved": date.today().isoformat(),
        "note": ("Reference standard only. DDInter severity is an expert-assigned "
                 "property of a knowledge base, not an observed patient outcome."),
        "atc_letters_requested": ATC_LETTERS,
        "raw_rows": int(len(raw)),
        "distinct_unordered_pairs": int(len(deduped)),
        "severity_conflicts_across_files": int(len(conflicts)),
        "severity_distribution": {str(k): int(v) for k, v in
                                  deduped["severity"].value_counts(dropna=False).items()},
        "files": files,
    }
    (RAW_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    deduped.to_csv(RAW_DIR / "ddinter_pairs_deduped.csv", index=False)
    print(f"\n  wrote {RAW_DIR/'ddinter_pairs_deduped.csv'} and MANIFEST.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
