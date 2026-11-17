"""
validate_training_set.py  --  is the dataset fit to train on?

Six checks. Every one of them is a thing that, if wrong, produces a model
with good numbers and no use -- and none of them would be caught by the
training script, which will happily fit whatever it is handed.

  1. SHAPE          row counts, class balance, no duplicate pairs, no item
                    paired with itself
  2. NUMBERS        every feature finite and in range. A NaN reaches a model
                    as either a crash or silent nonsense depending on the
                    library
  3. NO LEAKED COLUMNS   the Cat No that made the label must not be sitting
                    in a feature column under another name
  4. SPLIT INTEGRITY   both items of a pair in one split, and no pair
                    repeated across splits
  5. SEPARATION     per feature, the mean for duplicates minus the mean for
                    non-duplicates. This is the one to read: a feature with
                    separation near zero is carrying nothing, and a feature
                    with suspiciously perfect separation is probably a leak
  6. VETO INTERACTION   how many pairs the safety gate already settles, so
                    the model's real job is visible rather than assumed

Read-only.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/validate_training_set.py
"""

import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT                                  # noqa: E402

from item_duplicate_check.core.features import (                 # noqa: E402
    FEATURE_NAMES,
    FORBIDDEN_FIELDS,
)

DATA = os.path.join(APP_ROOT, "data")
SPLITS = ("train", "validation", "test")

#: Features that are a proportion and must stay inside [0, 1].
BOUNDED = ("ordered_similarity", "raw_similarity", "set_overlap",
           "length_ratio", "set_overlap_x_length", "idf_shared_fraction",
           "digit_token_overlap", "first_token_match",
           "makes_agree", "makes_abstain", "hsn_agree", "hsn_abstain",
           "department_agree", "department_abstain",
           "requester_agree", "requester_abstain",
           "naming_series_differs", "same_category_other_division")


def read(split):
    path = os.path.join(DATA, "pairs_%s.csv" % split)
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mean(values):
    return sum(values) / float(len(values)) if values else 0.0


def main():
    data = {split: read(split) for split in SPLITS}
    failures = []

    print("=" * 78)
    print("TRAINING SET VALIDATION")
    print("=" * 78)

    # ---- 1. shape -------------------------------------------------------
    print()
    print("1. SHAPE")
    print()
    print("   %-12s %8s %9s %9s %8s %10s"
          % ("split", "rows", "duplicate", "non-dup", "ratio", "columns"))
    print("   " + "-" * 62)
    for split in SPLITS:
        rows = data[split]
        pos = sum(1 for r in rows if r["label"] == "1")
        neg = len(rows) - pos
        print("   %-12s %8d %9d %9d  1:%-5.1f %10d"
              % (split, len(rows), pos, neg,
                 neg / float(pos) if pos else 0, len(rows[0])))

    all_rows = [r for split in SPLITS for r in data[split]]
    print("   " + "-" * 62)
    print("   %-12s %8d" % ("total", len(all_rows)))

    self_paired = [r for r in all_rows if r["item_a"] == r["item_b"]]
    seen, duplicated = set(), 0
    for r in all_rows:
        key = (r["split"], r["label"]) + tuple(sorted((r["item_a"],
                                                       r["item_b"])))
        if key in seen:
            duplicated += 1
        seen.add(key)
    print()
    print("   an item paired with itself        %5d   %s"
          % (len(self_paired), "OK" if not self_paired else "BAD"))
    print("   the same pair written twice       %5d   %s"
          % (duplicated, "OK" if not duplicated else "BAD"))
    if self_paired:
        failures.append("item paired with itself")
    if duplicated:
        failures.append("duplicate pair rows")

    # ---- 2. numbers -----------------------------------------------------
    print()
    print("2. NUMBERS -- every feature finite and in range")
    print()
    bad_values = {}
    for r in all_rows:
        for name in FEATURE_NAMES:
            raw = r[name]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                bad_values.setdefault(name, []).append("not a number: %r" % raw)
                continue
            if math.isnan(value) or math.isinf(value):
                bad_values.setdefault(name, []).append("NaN or inf")
            elif name in BOUNDED and not (0.0 <= value <= 1.0):
                bad_values.setdefault(name, []).append("out of [0,1]: %r"
                                                       % value)
    if bad_values:
        for name, problems in sorted(bad_values.items()):
            print("   BAD %-28s %d rows, e.g. %s"
                  % (name, len(problems), problems[0]))
        failures.append("bad feature values")
    else:
        print("   all %d features x %d rows: finite, and the %d bounded ones"
              % (len(FEATURE_NAMES), len(all_rows), len(BOUNDED)))
        print("   are inside [0, 1]                                      OK")

    # ---- 3. no leaked columns -------------------------------------------
    print()
    print("3. NO LEAKED COLUMNS -- the Cat No made the label and must not be")
    print("   reachable as a feature")
    print()
    header = list(data["train"][0].keys())
    feature_columns = [c for c in header
                       if c in FEATURE_NAMES]
    leaked = [c for c in feature_columns
              if any(f in c for f in FORBIDDEN_FIELDS)]
    diagnostics = [c for c in header if c.startswith("_")]
    print("   feature columns                   %5d" % len(feature_columns))
    print("   of those, naming a forbidden field %4d   %s"
          % (len(leaked), "OK" if not leaked else "LEAK: %s" % leaked))
    print("   diagnostic columns (prefixed _)   %5d   %s"
          % (len(diagnostics), ", ".join(diagnostics)))
    print("   -> a training script must select FEATURE_NAMES, never 'all")
    print("      numeric columns'. _cat_no_a is the value that made the label.")
    if leaked:
        failures.append("leaked feature column")
    if len(feature_columns) != len(FEATURE_NAMES):
        failures.append("feature columns do not match FEATURE_NAMES")

    # ---- 4. split integrity ---------------------------------------------
    print()
    print("4. SPLIT INTEGRITY")
    print()
    wrong_split = 0
    for split in SPLITS:
        for r in data[split]:
            if r["split"] != split:
                wrong_split += 1
    items_by_split = {}
    for split in SPLITS:
        codes = set()
        for r in data[split]:
            codes.add(r["item_a"])
            codes.add(r["item_b"])
        items_by_split[split] = codes
    overlap = (
        len(items_by_split["train"] & items_by_split["validation"])
        + len(items_by_split["train"] & items_by_split["test"])
        + len(items_by_split["validation"] & items_by_split["test"]))
    print("   rows whose split column disagrees  %4d   %s"
          % (wrong_split, "OK" if not wrong_split else "BAD"))
    print("   items appearing in two splits      %4d   %s"
          % (overlap, "OK" if not overlap else "LEAK"))
    for split in SPLITS:
        print("     %-12s %6d distinct items in pairs"
              % (split, len(items_by_split[split])))
    if wrong_split:
        failures.append("split column disagrees")
    if overlap:
        failures.append("items shared across splits")

    # ---- 5. separation --------------------------------------------------
    print()
    print("5. SEPARATION on the TRAIN split -- mean(duplicate) minus")
    print("   mean(non-duplicate). Read this column first.")
    print()
    train = data["train"]
    pos = [r for r in train if r["label"] == "1"]
    neg = [r for r in train if r["label"] == "0"]
    print("   %-30s %9s %9s %11s" % ("feature", "dup", "non-dup", "separation"))
    print("   " + "-" * 62)
    rows_out = []
    for name in FEATURE_NAMES:
        m_pos = mean([float(r[name]) for r in pos])
        m_neg = mean([float(r[name]) for r in neg])
        rows_out.append((abs(m_pos - m_neg), name, m_pos, m_neg))
    for gap, name, m_pos, m_neg in sorted(rows_out, reverse=True):
        flag = ""
        if gap < 0.02:
            flag = "  <- carries nothing"
        # Only a PROPORTION can be "suspiciously perfect". A count feature
        # like shared_token_count is measured in words, so a gap of 1.46 just
        # means duplicates share about one and a half more words -- the first
        # version of this check flagged it as a leak for that reason.
        if name in BOUNDED and gap > 0.95:
            flag = "  <- suspiciously perfect, check for a leak"
        print("   %-30s %9.3f %9.3f %+11.3f%s"
              % (name, m_pos, m_neg, m_pos - m_neg, flag))

    # ---- 6. veto interaction --------------------------------------------
    print()
    print("6. VETO GATE INTERACTION -- what the gate already settles")
    print()
    print("   %-12s %26s %26s" % ("split",
                                  "duplicates it would cancel",
                                  "non-duplicates it catches"))
    print("   " + "-" * 68)
    for split in SPLITS:
        rows = data[split]
        p = [r for r in rows if r["label"] == "1"]
        n = [r for r in rows if r["label"] == "0"]
        p_veto = sum(1 for r in p if r["_veto_reason"])
        n_veto = sum(1 for r in n if r["_veto_reason"])
        print("   %-12s %13d of %5d (%4.1f%%) %13d of %5d (%4.1f%%)"
              % (split,
                 p_veto, len(p), p_veto * 100.0 / len(p) if p else 0,
                 n_veto, len(n), n_veto * 100.0 / len(n) if n else 0))
    print()
    print("   The left column is the cost of the gate: real duplicates it")
    print("   refuses. The right column is its value: wrong pairs the model")
    print("   never has to get right. Decision 3 measured the trade as")
    print("   precision 78.1% -> 91.0%.")

    # ---- verdict --------------------------------------------------------
    path = os.path.join(DATA, "pairs_summary.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            summary = json.load(handle)
        print()
        print("   build summary: random_state=%s, %d features, leakage=%s"
              % (summary.get("random_state"), len(summary.get("features", [])),
                 summary.get("leakage")))

    print()
    print("=" * 78)
    if failures:
        print("  NOT FIT TO TRAIN ON: %s" % "; ".join(failures))
    else:
        print("  DATASET IS FIT TO TRAIN ON -- all six checks passed")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
