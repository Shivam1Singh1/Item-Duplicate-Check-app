"""
build_training_set.py  --  the pair-level dataset the model trains on.

WHAT WAS MISSING, AND WHY THIS EXISTS
-------------------------------------
`data/slim_*.csv` looked like finished preprocessing: the 11 kept columns and
a `dataset_split` column. But every row in them is ONE ITEM. A pair
classifier trains on PAIRS -- two item codes, a label, and the features
computed between them. None of that existed. This script produces it.

It also fixes two problems the audit in scripts/preprocess_audit.py found in
those CSVs:

  1. THE SPLIT LEAKED. 141 normalised names appeared on two sides. Decision
     16 built its product groups by linking on the raw item name, so
     "Sodium Carbonate" and "sodium carbonate." were treated as two products
     and landed in train and test. Here the grouping normalises first.

  2. THE SNAPSHOT WAS STALE. The CSVs are the 25,590-row Excel export; 3,386
     of those items no longer exist. This reads `site_local`, which is also
     what the app scores against at runtime, so training and serving see the
     same catalogue.

HOW THE LABELS ARE MADE
-----------------------
POSITIVE = two items sharing a cleaned Cat No. Genuine duplicates: 8 random
groups were checked by hand and all 8 were real. The Cat No never reaches the
features -- see the leakage note in core/features.py. It makes the label and
then gets out of the way.

NEGATIVE = a NAME-SIMILAR item from a different product group, four per
positive. Random pairs would be useless: "Glycine 500g" against "Mindray
probe cable" is a question nothing gets wrong, and a model trained on those
learns to answer an easy question. Negatives are drawn from the rare-word
index and ranked by plain name similarity -- deliberately NOT by the
hand-written scorer's weighted score, which would bake that scorer's opinion
into the data the model is supposed to improve on.

THE SPLIT
---------
70/15/15 by PRODUCT GROUP, never by row. A product group is a connected set
of items linked by any of three signals, via union-find:

    a shared cleaned Cat No
    an identical NORMALISED item name
    a shared old item code

Splitting rows instead of groups puts "Glycine 500g" in train and
"Glycine 500g." in test, scores brilliantly and means nothing.

Both items of a pair always come from the same split, by construction: pairs
are built inside a split, never across.

WHAT IT WRITES
--------------
    data/pairs_train.csv
    data/pairs_validation.csv
    data/pairs_test.csv
    data/pairs_summary.json

Columns: `item_a`, `item_b`, `label`, `split`, the features in
FEATURE_NAMES order, then diagnostic columns prefixed `_` (names, Cat No,
veto reason). ONLY the FEATURE_NAMES columns may be fed to a model -- the
`_` columns include the Cat No that made the label and exist so a human can
audit a row.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/build_training_set.py
    env/bin/python apps/item_duplicate_check/scripts/build_training_set.py --quick
"""

import csv
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT, connect                         # noqa: E402

from item_duplicate_check.core.corpus import (                   # noqa: E402
    build_corpus,
    clean_cat_no,
    load_rows,
)
from item_duplicate_check.core.features import (                 # noqa: E402
    FEATURE_NAMES,
    pair_features,
)
from item_duplicate_check.core.makes import _ALIASES             # noqa: E402
from item_duplicate_check.core.normalize import normalize        # noqa: E402
from item_duplicate_check.core.scorer import (                   # noqa: E402
    ordered_similarity_keys,
    veto_reason,
)

DATA = os.path.join(APP_ROOT, "data")

RANDOM_STATE = 42
SPLIT_SHARES = {"train": 0.70, "validation": 0.15, "test": 0.15}

#: Decision 16. Four name-similar negatives per positive.
NEGATIVES_PER_POSITIVE = 4

#: A Cat No group of 17 items contributes 136 pairs on its own and would
#: dominate everything. Capped so no single product carries the dataset.
MAX_PAIRS_PER_GROUP = 6

#: Non-stock items are skipped at runtime by HARD RULE 2 -- 4,340 rows on
#: site_local, including the 79 intentional CAPEX service entries. The model
#: will never be asked about them, so training on them would fit a
#: distribution that is never served.
INCLUDE_NON_STOCK = False


# ==========================================================================
# Product groups
# ==========================================================================

class UnionFind(object):
    """Merges "these two rows are the same product". Path compression only;
    at 22,204 items union-by-rank is unmeasurable and this is easier to
    trust."""

    def __init__(self):
        self.parent = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
            return True
        return False


#: Old item codes people type when they have no old item code.
OLD_CODE_JUNK = frozenset(("", "0", "00", "na", "n/a", "n a", "-", "--",
                           "nil", "none", "null", "."))


def build_groups(rows):
    """Product groups from the three identity signals. Returns
    (item_code -> group id, merge counts)."""
    uf = UnionFind()
    merges = {"cat_no": 0, "item_name": 0, "old_code": 0}
    by_cat_no, by_name, by_old = {}, {}, {}

    for row in rows:
        uf.find(row.item_code)                   # every item starts alone

        if row.cat_no:                           # already cleaned by ItemRow
            by_cat_no.setdefault(row.cat_no, []).append(row.item_code)

        # NORMALISED, not raw. This is the fix: linking on the raw name left
        # "Sodium Carbonate" and "sodium carbonate." in different groups, and
        # 141 of those pairs ended up on opposite sides of the old split.
        if row.normalized:
            by_name.setdefault(row.normalized, []).append(row.item_code)

        old = row.old_code.strip().lower()
        if old not in OLD_CODE_JUNK:
            by_old.setdefault(old, []).append(row.item_code)

    for signal, buckets in (("cat_no", by_cat_no), ("item_name", by_name),
                            ("old_code", by_old)):
        for members in buckets.values():
            first = members[0]
            for other in members[1:]:
                if uf.union(first, other):
                    merges[signal] += 1

    return {code: uf.find(code) for code in uf.parent}, merges


def split_groups(group_of, rows):
    """Deal whole product groups into train / validation / test.

    Shuffled with a fixed seed, then dealt until each split has its share of
    ITEMS rather than of groups -- a handful of large groups would otherwise
    skew the sizes badly.
    """
    members = {}
    for row in rows:
        members.setdefault(group_of[row.item_code], []).append(row.item_code)

    group_ids = sorted(members)
    random.Random(RANDOM_STATE).shuffle(group_ids)

    total = len(rows)
    want_train = total * SPLIT_SHARES["train"]
    want_validation = total * (SPLIT_SHARES["train"]
                               + SPLIT_SHARES["validation"])

    split_of_group, running = {}, 0
    for gid in group_ids:
        if running < want_train:
            split_of_group[gid] = "train"
        elif running < want_validation:
            split_of_group[gid] = "validation"
        else:
            split_of_group[gid] = "test"
        running += len(members[gid])

    return split_of_group, members


# ==========================================================================
# Pairs
# ==========================================================================

def positive_pairs(rows):
    """Pairs sharing a cleaned Cat No, capped per group."""
    buckets = {}
    for row in rows:
        if row.cat_no:
            buckets.setdefault(row.cat_no, []).append(row)

    pairs = []
    for cat_no, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        taken = 0
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.append((members[i], members[j], cat_no))
                taken += 1
                if taken >= MAX_PAIRS_PER_GROUP:
                    break
            if taken >= MAX_PAIRS_PER_GROUP:
                break
    return pairs


def hard_negatives(corpus, row, group_of, wanted):
    """The `wanted` most name-similar items from a DIFFERENT product group.

    Ranked by plain name similarity, not by the hand-written scorer's
    weighted score. The point of a hard negative is "this looks similar and
    is not the same product"; using the scorer's own opinion of similar would
    teach the model to agree with the scorer rather than with the data.
    """
    own_group = group_of.get(row.item_code)
    scored = []
    for candidate in corpus.candidates(row.item_name,
                                       exclude_item_code=row.item_code):
        if group_of.get(candidate.item_code) == own_group:
            continue
        scored.append((ordered_similarity_keys(row.sorted_key,
                                               candidate.sorted_key),
                       candidate.item_code, candidate))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [candidate for _s, _c, candidate in scored[:wanted]]


def feature_row(row_a, row_b, label, split, corpus, cat_no=None):
    """One output row: ids, label, features, then diagnostics."""
    values = pair_features(row_a, row_b, df=corpus.df,
                           corpus_size=len(corpus), aliases=_ALIASES)
    out = {
        "item_a": row_a.item_code,
        "item_b": row_b.item_code,
        "label": label,
        "split": split,
    }
    out.update({name: values[name] for name in FEATURE_NAMES})

    # Diagnostics. Prefixed so no training script can mistake them for
    # features -- `_cat_no_a` in particular is the value that MADE the label.
    out["_name_a"] = row_a.item_name
    out["_name_b"] = row_b.item_name
    out["_cat_no_a"] = row_a.cat_no
    out["_cat_no_b"] = row_b.cat_no
    out["_label_source"] = cat_no or ""
    out["_veto_reason"] = veto_reason(row_a.item_name, row_b.item_name) or ""
    return out


# ==========================================================================
# Main
# ==========================================================================

def main():
    quick = "--quick" in sys.argv
    frappe = connect()

    print("=" * 74)
    print("DATA PREPROCESSING -- building the pair-level dataset")
    print("=" * 74)
    print()
    print("  source          site_local (live), not data/slim_*.csv")
    print("  random_state    %d" % RANDOM_STATE)
    print("  negatives       %d per positive, name-similar, different group"
          % NEGATIVES_PER_POSITIVE)
    print("  cap             %d pairs per Cat No group" % MAX_PAIRS_PER_GROUP)
    print("  features        %d" % len(FEATURE_NAMES))
    if quick:
        print("  MODE            --quick")

    # ---- load -----------------------------------------------------------
    all_rows = load_rows(frappe)
    rows = [r for r in all_rows if r.is_stock_item or INCLUDE_NON_STOCK]
    print()
    print("  items in tabItem              %6d" % len(all_rows))
    print("  maintain stock = 0, excluded  %6d   (HARD RULE 2)"
          % (len(all_rows) - len(rows)))
    print("  items used                    %6d" % len(rows))

    # ---- group ----------------------------------------------------------
    group_of, merges = build_groups(rows)
    split_of_group, members = split_groups(group_of, rows)

    print()
    print("  product groups (union-find on three signals):")
    print("    shared cleaned Cat No         %6d merges" % merges["cat_no"])
    print("    identical NORMALISED name     %6d merges" % merges["item_name"])
    print("    shared old item code          %6d merges" % merges["old_code"])
    print("    distinct product groups       %6d" % len(members))
    print("    groups holding >1 item        %6d"
          % sum(1 for m in members.values() if len(m) > 1))

    rows_by_split = {"train": [], "validation": [], "test": []}
    for row in rows:
        rows_by_split[split_of_group[group_of[row.item_code]]].append(row)

    print()
    print("  split by product group:")
    print("    %-12s %8s %8s %8s" % ("split", "items", "share", "groups"))
    print("    " + "-" * 40)
    for name in ("train", "validation", "test"):
        count = len(rows_by_split[name])
        groups = sum(1 for g, s in split_of_group.items() if s == name)
        print("    %-12s %8d %7.1f%% %8d"
              % (name, count, count * 100.0 / len(rows), groups))

    # ---- leakage proof --------------------------------------------------
    print()
    print("  LEAKAGE CHECK -- the thing the old CSVs got wrong:")
    checks = []
    for label, keyfn in (
            ("product group spans splits",
             lambda r: group_of[r.item_code]),
            ("identical normalised name",
             lambda r: r.normalized or None),
            ("shared cleaned Cat No",
             lambda r: r.cat_no or None),
            ("shared old item code",
             lambda r: (r.old_code.strip().lower()
                        if r.old_code.strip().lower() not in OLD_CODE_JUNK
                        else None)),
    ):
        seen = {}
        for row in rows:
            key = keyfn(row)
            if key is None:
                continue
            seen.setdefault(key, set()).add(
                split_of_group[group_of[row.item_code]])
        spanning = sum(1 for v in seen.values() if len(v) > 1)
        checks.append((label, spanning))
        print("    %-30s %6d distinct, %4d span >1 split   %s"
              % (label, len(seen), spanning, "OK" if spanning == 0 else "LEAK"))

    # ---- pairs ----------------------------------------------------------
    summary = {"random_state": RANDOM_STATE,
               "negatives_per_positive": NEGATIVES_PER_POSITIVE,
               "max_pairs_per_group": MAX_PAIRS_PER_GROUP,
               "features": list(FEATURE_NAMES),
               "leakage": {label: n for label, n in checks},
               "splits": {}}

    columns = (["item_a", "item_b", "label", "split"] + list(FEATURE_NAMES)
               + ["_name_a", "_name_b", "_cat_no_a", "_cat_no_b",
                  "_label_source", "_veto_reason"])

    for split in ("train", "validation", "test"):
        started = time.perf_counter()
        split_rows = rows_by_split[split]
        corpus = build_corpus(split_rows)
        pairs = positive_pairs(split_rows)
        if quick:
            pairs = pairs[:150]

        out_rows = []
        negatives_found = 0

        # EVERY FEATURE IS SYMMETRIC, so (X, Y) and (Y, X) are the same
        # training example written twice. Both orders really do get
        # generated: X and Y are each the left-hand item of their own
        # positive pair, and each finds the other among its name-similar
        # negatives. Keyed on the SORTED pair for that reason.
        #
        # Without this the dataset double-weights whichever examples happen
        # to be mutually similar -- which is precisely the hardest and most
        # influential subset. 1,561 such rows on the real data.
        seen_pairs = set()

        def key(row_x, row_y, label):
            return (label,) + tuple(sorted((row_x.item_code,
                                            row_y.item_code)))

        for row_a, row_b, cat_no in pairs:
            pair_key = key(row_a, row_b, 1)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            out_rows.append(feature_row(row_a, row_b, 1, split, corpus,
                                        cat_no))

        # NEGATIVES ARE GENERATED PER DISTINCT LEFT ITEM, NOT PER PAIR.
        #
        # A Cat No group holding three items produces the pairs (X,Y) and
        # (X,Z), both with X on the left. Asking for X's negatives once per
        # pair returns the same top-ranked items twice, and the first version
        # of this script wrote 5,180 duplicate rows that way -- found by
        # scripts/validate_training_set.py, not by the training script, which
        # would have fitted them happily and counted them twice.
        #
        # So each left item is asked once, for as many negatives as it has
        # positives. The ratio stays at 4:1 and every row is distinct.
        positives_per_left = {}
        left_rows = {}
        for row_a, _row_b, _cat_no in pairs:
            positives_per_left[row_a.item_code] = (
                positives_per_left.get(row_a.item_code, 0) + 1)
            left_rows[row_a.item_code] = row_a

        for code in sorted(positives_per_left):
            row_a = left_rows[code]
            wanted = NEGATIVES_PER_POSITIVE * positives_per_left[code]

            # Over-fetch, then take the first `wanted` that are not already
            # in the dataset in either order, so deduplication does not
            # quietly cost us the 4:1 ratio.
            taken = 0
            for negative in hard_negatives(corpus, row_a, group_of,
                                           wanted * 3):
                if taken >= wanted:
                    break
                pair_key = key(row_a, negative, 0)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                out_rows.append(feature_row(row_a, negative, 0, split,
                                            corpus))
                negatives_found += 1
                taken += 1

        path = os.path.join(DATA, "pairs_%s.csv" % split)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(out_rows)

        elapsed = time.perf_counter() - started
        positives = sum(1 for r in out_rows if r["label"] == 1)
        vetoed_positives = sum(1 for r in out_rows
                               if r["label"] == 1 and r["_veto_reason"])
        summary["splits"][split] = {
            "items": len(split_rows),
            "positive_pairs": positives,
            "negative_pairs": negatives_found,
            "rows": len(out_rows),
            "positives_cancelled_by_veto": vetoed_positives,
            "seconds": round(elapsed, 1),
            "file": os.path.basename(path),
        }
        print()
        print("  %s: %d rows written to %s in %.1f s"
              % (split, len(out_rows), os.path.basename(path), elapsed))
        print("    positives %5d   negatives %5d   ratio 1:%.1f"
              % (positives, negatives_found,
                 negatives_found / float(positives) if positives else 0))
        print("    positives the veto gate would cancel: %d (%.1f%%)"
              % (vetoed_positives,
                 vetoed_positives * 100.0 / positives if positives else 0))

    path = os.path.join(DATA, "pairs_summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print()
    print("  summary written to %s" % os.path.basename(path))

    frappe.destroy()


if __name__ == "__main__":
    main()
