"""
preprocess_audit.py  --  is the data preprocessing actually finished?

WHY THIS EXISTS
---------------
`data/slim_*.csv` exist and carry a `dataset_split` column, which looks like
the preprocessing is done. This checks what is really in them before any
model is trained on them, because every later number depends on it.

It answers five questions, each with a number rather than an opinion:

  1. Do the split sizes match what README decision 16 claims?
  2. Do the splits LEAK? Same product on two sides makes every score
     meaningless, and it is the one thing decision 16 promises is clean.
  3. How much of each feature field is actually filled? A field present on
     4% of rows cannot carry weight however good its separation looks.
  4. Do the CSV rows still exist in `site_local`? The CSVs come from the
     25,590-row Excel export; the live database has 22,204.
  5. Are there pair rows, labels or features anywhere? That is what a model
     trains on, and it is the part most likely to be missing.

Read-only. Touches nothing.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/preprocess_audit.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT, connect                         # noqa: E402

from item_duplicate_check.core.corpus import clean_cat_no         # noqa: E402
from item_duplicate_check.core.makes import canonical            # noqa: E402
from item_duplicate_check.core.normalize import normalize         # noqa: E402

DATA = os.path.join(APP_ROOT, "data")

#: What README decision 16 says the split should be.
README_SPLIT = {"train": 17914, "validation": 3838, "test": 3838}

#: The feature fields, as spelled in slim_*.csv.
FIELDS = ("Item Name", "Cat No", "Makes", "HSN/SAC", "Department",
          "Reference Old Item Code", "Description")


def read_slim():
    """All three slim files as one list of dicts."""
    rows = []
    for name in ("slim_train.csv", "slim_validation.csv", "slim_test.csv"):
        path = os.path.join(DATA, name)
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                row["_file"] = name
                rows.append(row)
    return rows


def main():
    rows = read_slim()

    print("=" * 74)
    print("PREPROCESSING AUDIT -- data/slim_*.csv")
    print("=" * 74)

    # ---- 1. split sizes -------------------------------------------------
    counts = {}
    for row in rows:
        counts[row.get("dataset_split") or "(blank)"] = \
            counts.get(row.get("dataset_split") or "(blank)", 0) + 1

    print()
    print("1. SPLIT SIZES")
    print()
    print("   %-14s %8s %8s %9s %8s"
          % ("split", "in CSV", "README", "diff", "share"))
    print("   " + "-" * 50)
    total = len(rows)
    for split in ("train", "validation", "test"):
        got = counts.get(split, 0)
        want = README_SPLIT[split]
        print("   %-14s %8d %8d %+9d %7.1f%%"
              % (split, got, want, got - want, got * 100.0 / total))
    for other in sorted(set(counts) - set(README_SPLIT)):
        print("   %-14s %8d %8s %9s" % (other, counts[other], "-", "-"))
    print("   " + "-" * 50)
    print("   %-14s %8d %8d %+9d"
          % ("total", total, sum(README_SPLIT.values()),
             total - sum(README_SPLIT.values())))

    # ---- 2. leakage -----------------------------------------------------
    # Three identity signals, the same three decision 16 used to build the
    # product groups. If any of them appears on two sides, the split leaks.
    print()
    print("2. LEAKAGE -- the same product on two sides of the split")
    print()

    def leak_check(label, keyfn):
        seen = {}
        for row in rows:
            key = keyfn(row)
            if not key:
                continue
            seen.setdefault(key, set()).add(row.get("dataset_split"))
        spanning = {k: v for k, v in seen.items() if len(v) > 1}
        print("   %-28s %6d distinct, %5d span >1 split   %s"
              % (label, len(seen), len(spanning),
                 "OK" if not spanning else "LEAK"))
        return spanning

    by_name = leak_check("identical normalised name",
                         lambda r: normalize(r.get("Item Name")))
    by_cat = leak_check("shared cleaned Cat No",
                        lambda r: clean_cat_no(r.get("Cat No")))
    by_old = leak_check("shared old item code",
                        lambda r: (r.get("Reference Old Item Code")
                                   or "").strip().lower() or None)

    for label, spanning in (("name", by_name), ("cat no", by_cat),
                            ("old code", by_old)):
        if spanning:
            print()
            print("   examples of leaking %s (first 5):" % label)
            for key, splits in list(spanning.items())[:5]:
                print("     %-34s %s" % (str(key)[:34], sorted(splits)))

    # ---- 3. field coverage ----------------------------------------------
    print()
    print("3. FIELD COVERAGE -- can a feature built on this carry any weight?")
    print()
    print("   %-28s %8s %8s   %s" % ("field", "filled", "share", "note"))
    print("   " + "-" * 64)
    for field in FIELDS:
        filled = sum(1 for r in rows if (r.get(field) or "").strip())
        note = ""
        if field == "Cat No":
            usable = sum(1 for r in rows if clean_cat_no(r.get("Cat No")))
            note = "%d usable after junk removal" % usable
        if field == "Makes":
            usable = sum(1 for r in rows if canonical(r.get("Makes")))
            note = "%d usable after alias + junk" % usable
        if field == "Reference Old Item Code":
            note = "second lookup key, decision 7"
        print("   %-28s %8d %7.1f%%   %s"
              % (field, filled, filled * 100.0 / total, note))

    # ---- 4. do these rows still exist live? ------------------------------
    frappe = connect()
    live = {r[0] for r in frappe.db.sql("SELECT `name` FROM `tabItem`")}
    csv_codes = {(r.get("Item Code") or "").strip() for r in rows}
    csv_codes.discard("")

    print()
    print("4. CSV AGAINST THE LIVE DATABASE")
    print()
    print("   rows in slim_*.csv            %6d" % total)
    print("   distinct item codes in CSV    %6d" % len(csv_codes))
    print("   items in site_local           %6d" % len(live))
    print("   in CSV and live               %6d" % len(csv_codes & live))
    print("   in CSV but NOT live           %6d" % len(csv_codes - live))
    print("   live but NOT in CSV           %6d" % len(live - csv_codes))

    # ---- 5. the part a model actually needs ------------------------------
    print()
    print("5. WHAT A MODEL TRAINS ON")
    print()
    headers = set()
    for name in sorted(os.listdir(DATA)):
        if name.endswith(".csv"):
            path = os.path.join(DATA, name)
            with open(path, newline="", encoding="utf-8-sig") as handle:
                headers.update(next(csv.reader(handle)))
    pairish = [h for h in sorted(headers)
               if any(w in h.lower() for w in
                      ("label", "target", "is_dup", "pair", "_a", "_b",
                       "left", "right", "feature", "score"))]
    print("   pair rows / label column / feature matrix found: %s"
          % (", ".join(pairish) if pairish else "NONE"))
    print()
    print("   Every file in data/ is ITEM-level: one row per item, raw field")
    print("   values, plus a dataset_split column. A pair classifier needs")
    print("   PAIR-level rows -- two item codes, a label, and the features")
    print("   computed between them. None of that exists yet.")

    frappe.destroy()


if __name__ == "__main__":
    main()
