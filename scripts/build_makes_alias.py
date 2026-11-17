"""
build_makes_alias.py  --  generate config/makes_alias.json from the real data.

WHY GENERATED AND NOT HAND-WRITTEN
----------------------------------
There are 1,438 distinct Makes spellings on site_local. Guessing which ones
matter wastes effort on the long tail; the data says plainly where the value
is. This script counts the spellings, applies a short list of seed rules, and
writes out an EXACT-MATCH table of the spellings that really occur.

WHY THE OUTPUT IS EXACT MATCH
-----------------------------
The seed rules below use substrings, because that is how you find candidates.
The file they produce does not: every line is one spelling somebody actually
typed, mapped to one canonical name. So a rule that would have been dangerous
in production ("anything containing bd") is only ever used here, under human
review, against a finite list we can read.

THE REVIEW STEP IS NOT OPTIONAL
-------------------------------
The script prints the top 50 spellings it could NOT map. That list is the
work queue for a human: anything there which is really a variant of something
already in the table should be added to the JSON by hand. The file is
configuration -- editing it needs no deploy and no code review.

Nothing here runs in production. The app only ever reads the JSON.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/build_makes_alias.py
    env/bin/python apps/item_duplicate_check/scripts/build_makes_alias.py --write
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT, connect                         # noqa: E402

from item_duplicate_check.core.makes import normalize_makes       # noqa: E402

OUTPUT = os.path.join(APP_ROOT, "item_duplicate_check", "config",
                      "makes_alias.json")

# --------------------------------------------------------------------------
# Seed rules. First match wins, so ORDER MATTERS.
#
# Each entry is (canonical name, [substrings that identify it]).
# Only used to generate the file. Read top to bottom:
#
#   "gibco/thermo" must land on thermo, not gibco, which is why the thermo
#   rule sits above the gibco rule. That specific mapping is called for in
#   the build brief, and the ordering is how it is achieved rather than a
#   special case.
# --------------------------------------------------------------------------
SEEDS = [
    # Thermo owns several of these brand names and the catalogue mixes them
    # freely, including as "gibco/thermo" and "invitrogen/thermo".
    ("thermo", ["thermo"]),

    # Sigma-Aldrich. "aldrich" alone appears without "sigma".
    ("sigma", ["sigma", "aldrich"]),

    # Merck. Millipore is deliberately NOT included: Merck Millipore and
    # Millipore-as-typed may or may not mean the same supplier here, so it
    # goes to the human review list instead of being guessed.
    ("merck", ["merck"]),

    # Becton Dickinson. Matched on whole words only, because "bd" as a
    # substring hits unrelated spellings.
    ("bd", ["becton", "bd biosciences", "bd bioscience", "bd pharmingen",
            "bd falcon", "bd difco", "bd bbl"]),

    # Gibco on its own, now that anything co-branded with Thermo has gone.
    ("gibco", ["gibco"]),

    # The two biggest pure spelling variants in the data. Not brand
    # families -- one is simply the singular of the other, and the pair is
    # the single largest merge available (489 + 435 = 924 items).
    ("tarsons", ["tarson"]),
    ("biorad", ["bio rad", "biorad", "bio-rad"]),
]

#: Whole-word seeds: matched against the token list, not as substrings.
#: "bd" must not match "abdominal", and "srl" must not match "srlabs".
WORD_SEEDS = [
    ("bd", ["bd"]),
]


def canonical_for(spelling: str):
    """Apply the seed rules to one normalised spelling, or None."""
    words = spelling.split()
    for name, needles in SEEDS:
        for needle in needles:
            if needle in spelling:
                return name
    for name, needles in WORD_SEEDS:
        for needle in needles:
            if needle in words:
                return name
    return None


def main():
    write = "--write" in sys.argv
    frappe = connect()

    counts = frappe.db.sql(
        """
        SELECT  `makes` AS raw, COUNT(*) AS n
        FROM    `tabItem`
        WHERE   IFNULL(`makes`, '') <> ''
        GROUP BY `makes`
        ORDER BY n DESC
        """,
        as_dict=True,
    )

    # Fold the raw spellings into normalised ones first; several raw values
    # collapse together before any alias rule is involved.
    spellings = {}
    junk_rows = 0
    for row in counts:
        normalised = normalize_makes(row.raw)
        if not normalised:
            junk_rows += row.n
            continue
        spellings[normalised] = spellings.get(normalised, 0) + row.n

    mapped, unmapped = {}, {}
    for spelling, n in spellings.items():
        name = canonical_for(spelling)
        if name and name != spelling:
            mapped[spelling] = name
        elif name:
            mapped[spelling] = name        # the canonical spelling itself
        else:
            unmapped[spelling] = n

    # ---- report ---------------------------------------------------------
    print("makes alias table, generated from site_local")
    print()
    print("  raw spellings in tabItem        %6d" % len(counts))
    print("  after normalisation             %6d" % len(spellings))
    print("  rows with junk makes (ignored)  %6d" % junk_rows)
    print("  spellings mapped by seed rules  %6d" % len(mapped))
    print("  spellings left unmapped         %6d" % len(unmapped))
    print()

    families = {}
    for spelling, name in mapped.items():
        families.setdefault(name, []).append((spellings[spelling], spelling))
    print("  what the table merges:")
    for name, members in sorted(families.items(),
                                key=lambda kv: -sum(m[0] for m in kv[1])):
        total = sum(m[0] for m in members)
        print("    %-10s %5d items across %2d spellings"
              % (name, total, len(members)))
        for n, spelling in sorted(members, reverse=True)[:8]:
            print("        %5d  %s" % (n, spelling))
        if len(members) > 8:
            print("        ... and %d more" % (len(members) - 8))

    print()
    print("  TOP 50 UNMAPPED SPELLINGS -- for human review.")
    print("  Add any that are a variant of something above to the JSON by hand.")
    print()
    print("    %6s  %s" % ("items", "spelling"))
    print("    " + "-" * 46)
    for spelling, n in sorted(unmapped.items(), key=lambda kv: -kv[1])[:50]:
        print("    %6d  %s" % (n, spelling))

    # ---- write ----------------------------------------------------------
    payload = {
        "_about": [
            "Manufacturer spelling -> one canonical name.",
            "Generated by scripts/build_makes_alias.py from site_local, then",
            "edited by hand. Exact match only: every key is a spelling that",
            "really occurs in tabItem.makes after normalize_makes().",
            "Safe to edit on a running bench -- it is configuration, not code.",
            "An unmapped spelling is its own canonical name, so adding an",
            "entry can only ever merge two spellings, never split one.",
        ],
        "_generated_from": {
            "site": frappe.local.site,
            "raw_spellings": len(counts),
            "normalised_spellings": len(spellings),
            "mapped": len(mapped),
            "unmapped": len(unmapped),
        },
        "alias": dict(sorted(mapped.items())),
    }

    if write:
        if not os.path.isdir(os.path.dirname(OUTPUT)):
            os.makedirs(os.path.dirname(OUTPUT))
        with open(OUTPUT, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
        print()
        print("  written: %s" % OUTPUT)
    else:
        print()
        print("  dry run. Re-run with --write to save %s" % OUTPUT)

    frappe.destroy()


if __name__ == "__main__":
    main()
