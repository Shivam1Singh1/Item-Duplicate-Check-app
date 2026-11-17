"""
eval_harness.py  --  STEP H. Does this thing actually detect duplicates?

Everything else in this app is an opinion until this script runs.

HOW THE LABELS ARE MADE, AND WHY THEY ARE HONEST
------------------------------------------------
A positive pair is two items that share a cleaned Cat No. Those are genuine
duplicates: 8 random groups were checked by hand and all 8 were real.

The scorer is then asked about those pairs WITH THE CAT NO HIDDEN. It is
given the name and the manufacturer only. So we are measuring whether the
names alone find a pair that the catalogue numbers already prove is the same
product. The scorer never sees the thing that made the label, which is what
makes the result meaningful rather than circular -- the mistake has a name,
label leakage, and avoiding it is decision 6 in README.md.

A hard negative is the top-scoring candidate OUR OWN SEARCH returns that is
NOT in the query's product group. Random pairs would be useless: "Glycine
500g" against "Mindray probe cable" is a question no system gets wrong, and
measuring on those produces a flattering number that predicts nothing.

THE SPLIT
---------
70 / 15 / 15 by PRODUCT GROUP, never by row. A product group is a connected
set of items linked by a shared Cat No, an identical item name, or a shared
old item code (union-find). Splitting rows instead of groups puts "Glycine
500g" in train and "Glycine 500g." in test, scores brilliantly, and means
nothing.

Validation is where you are allowed to look. Test is reported here for
completeness and must not be used to choose anything.

WHAT IT PRINTS
--------------
  1. the split, and proof it does not leak
  2. precision and recall at 0.60 / 0.75 / 0.85 / 0.92
  3. recall@5
  4. latency, median and p95, over real catalogue names
  5. the veto gate against every known look-alike pair
  6. pass or fail against the acceptance targets

Read-only. Writes no Item Match Log rows -- it calls the scorer directly, not
the API, because the API logs and that would put thousands of synthetic rows
into the table the real training data is supposed to come from.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/eval_harness.py
    env/bin/python apps/item_duplicate_check/scripts/eval_harness.py --quick
    env/bin/python apps/item_duplicate_check/scripts/eval_harness.py --split train
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import connect                                   # noqa: E402

from item_duplicate_check.core.corpus import (                   # noqa: E402
    build_corpus,
    clean_cat_no,
    rows_from_dicts,
)
from item_duplicate_check.core.makes import _ALIASES             # noqa: E402
from item_duplicate_check.core.normalize import normalize        # noqa: E402
from item_duplicate_check.core.scorer import (                   # noqa: E402
    DEFAULT_WEIGHTS,
    Query,
    search,
    veto_reason,
)
from item_duplicate_check.tests.test_specs import (              # noqa: E402
    MUST_ALLOW,
    MUST_CANCEL,
)

RANDOM_STATE = 42
SPLIT_SHARES = {"train": 0.70, "validation": 0.15, "test": 0.15}
THRESHOLDS = (0.60, 0.75, 0.85, 0.92)
TOP_N = 5

#: From README decision 13. These are the numbers the build is judged on.
TARGETS = {
    "precision_at_0.92": 0.90,
    "recall_at_5": 0.70,
    "latency_p95_ms": 300.0,
}

#: How many hard negatives per positive pair. 1 keeps the headline numbers
#: comparable with the measurement in README 19.4 (978 positives against 978
#: hard negatives); the 4:1 figure from decision 16 is reported alongside as
#: a sensitivity check, because precision depends on that ratio and a single
#: number without it is not interpretable.
NEGATIVE_RATIOS = (1, 4)


# ==========================================================================
# Loading and grouping
# ==========================================================================

def load_items(frappe):
    """Every Item, with the three fields the grouping needs."""
    return frappe.db.sql(
        """
        SELECT  `name`, `item_name`, `cat_no`, `makes`, `description`,
                `disabled`, `is_stock_item`,
                `custom_reference_old_item_code` AS old_code
        FROM    `tabItem`
        """,
        as_dict=True,
    )


class UnionFind(object):
    """Smallest thing that can merge "these two rows are the same product".

    Path compression only -- no union by rank. At 22,204 items the difference
    is unmeasurable and the simpler version is easier to trust.
    """

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


def build_groups(records):
    """Product groups, by the three identity signals from decision 16.

    Returns (code -> group id, merge counts per signal).
    """
    uf = UnionFind()
    merges = {"cat_no": 0, "item_name": 0, "old_code": 0}

    by_cat_no, by_name, by_old = {}, {}, {}
    for r in records:
        uf.find(r["name"])                       # every item starts alone

        cleaned = clean_cat_no(r.get("cat_no"))
        if cleaned:
            by_cat_no.setdefault(cleaned, []).append(r["name"])

        # Identical NORMALISED name, not identical raw name -- otherwise
        # "ACK Lysis Buffer" and "ACK Lysis Buffer." are two products.
        norm = normalize(r.get("item_name"))
        if norm:
            by_name.setdefault(norm, []).append(r["name"])

        old = (r.get("old_code") or "").strip().lower()
        if old and old not in ("0", "na", "-"):
            by_old.setdefault(old, []).append(r["name"])

    for signal, buckets in (("cat_no", by_cat_no), ("item_name", by_name),
                            ("old_code", by_old)):
        for members in buckets.values():
            first = members[0]
            for other in members[1:]:
                if uf.union(first, other):
                    merges[signal] += 1

    return {code: uf.find(code) for code in uf.parent}, merges


def split_groups(group_of, records):
    """Assign whole product groups to train / validation / test.

    Groups are shuffled with a fixed seed and then dealt out until each split
    has its share of ITEMS, not of groups -- a handful of large groups would
    otherwise skew the sizes badly.
    """
    members = {}
    for r in records:
        members.setdefault(group_of[r["name"]], []).append(r["name"])

    group_ids = sorted(members)
    random.Random(RANDOM_STATE).shuffle(group_ids)

    total = len(records)
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
# Pair building
# ==========================================================================

def positive_pairs(rows, group_of):
    """Pairs sharing a cleaned Cat No, within one split.

    Capped per group: a group of 17 items would contribute 136 pairs on its
    own and dominate the measurement. Taking the first 6 pairs per group
    keeps one product from carrying the result.
    """
    buckets = {}
    for row in rows:
        if row.cat_no:
            buckets.setdefault(row.cat_no, []).append(row)

    pairs = []
    for cat_no, members in buckets.items():
        if len(members) < 2:
            continue
        taken = 0
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.append((members[i], members[j], cat_no))
                taken += 1
                if taken >= 6:
                    break
            if taken >= 6:
                break
    return pairs


# ==========================================================================
# Measuring
# ==========================================================================

def evaluate(split_name, rows, group_of, weights, quick=False, verbose=False):
    """Run the real search over one split and collect every number."""
    corpus = build_corpus(rows)
    pairs = positive_pairs(rows, group_of)

    if quick:
        pairs = pairs[:300]

    positives = []          # score the scorer gave the known duplicate
    negatives = []          # score it gave the best non-duplicate
    found_at_5 = 0
    timings = []
    missed_examples, negative_examples = [], []

    for left, right, cat_no in pairs:
        # CAT NO IS HIDDEN. The whole point: the scorer is asked to find the
        # pair from the name and manufacturer alone, and the Cat No is only
        # used afterwards, by us, to know the answer.
        query = Query(name=left.item_name, cat_no=None, makes=left.makes,
                      item_code=left.item_code)

        started = time.perf_counter()
        result = search(corpus, query, weights=weights, aliases=_ALIASES,
                        limit=TOP_N)
        timings.append((time.perf_counter() - started) * 1000)

        matches = result["matches"]
        codes = [m.item_code for m in matches]

        if right.item_code in codes:
            found_at_5 += 1
            score = matches[codes.index(right.item_code)].score
        else:
            # Not in the top five. For precision and recall that is a score
            # of zero: the user would never have been shown it.
            score = 0.0
            cancelled = [m for m in result["cancelled"]
                         if m.item_code == right.item_code]
            if len(missed_examples) < 8:
                missed_examples.append((
                    left.item_name, right.item_name, cat_no,
                    cancelled[0].cancel_reason if cancelled else
                    ("not in top %d" % TOP_N)))
        positives.append(score)

        # HARD NEGATIVE: the best thing the search found that is genuinely a
        # different product -- a different product group.
        same_group = group_of[left.item_code]
        for match in matches:
            if group_of.get(match.item_code) != same_group:
                negatives.append(match.score)
                if len(negative_examples) < 8 and match.score >= 0.75:
                    negative_examples.append((left.item_name, match.item_name,
                                              match.score, match.reason))
                break
        else:
            negatives.append(0.0)

    out = {
        "split": split_name,
        "items": len(rows),
        "pairs": len(pairs),
        "positives": positives,
        "negatives": negatives,
        "recall_at_5": found_at_5 / float(len(pairs)) if pairs else 0.0,
        "timings": sorted(timings),
        "missed_examples": missed_examples,
        "negative_examples": negative_examples,
    }
    return out


def precision_recall(positives, negatives, threshold, ratio=1):
    """Precision and recall at one threshold.

    `ratio` repeats the negatives, which is how a 4-negatives-per-positive
    world is simulated without re-running the search: the negative scores are
    the same scores, there are just four times as many chances to raise a
    false warning.
    """
    true_positive = sum(1 for s in positives if s >= threshold)
    false_positive = sum(1 for s in negatives if s >= threshold) * ratio
    flagged = true_positive + false_positive

    precision = true_positive / float(flagged) if flagged else 1.0
    recall = true_positive / float(len(positives)) if positives else 0.0
    return precision, recall, true_positive, false_positive


# ==========================================================================
# Reporting
# ==========================================================================

def report_split(result, verbose=False):
    positives, negatives = result["positives"], result["negatives"]

    print()
    print("  %s split -- %d items, %d positive pairs, %d hard negatives"
          % (result["split"].upper(), result["items"], result["pairs"],
             len(negatives)))
    print()
    for ratio in NEGATIVE_RATIOS:
        label = ("%d hard negative per positive" % ratio if ratio == 1
                 else "%d hard negatives per positive" % ratio)
        print("    %s" % label)
        print("      %-10s %10s %10s %8s %8s"
              % ("threshold", "precision", "recall", "TP", "FP"))
        print("      " + "-" * 50)
        for threshold in THRESHOLDS:
            p, r, tp, fp = precision_recall(positives, negatives, threshold,
                                            ratio)
            print("      %-10.2f %9.1f%% %9.1f%% %8d %8d"
                  % (threshold, p * 100, r * 100, tp, fp))
        print()

    timings = result["timings"]
    print("    recall@5                  %.1f%%" % (result["recall_at_5"] * 100))
    print("    latency median            %.1f ms" % timings[len(timings) // 2])
    print("    latency p95               %.1f ms"
          % timings[int(len(timings) * 0.95)])
    print("    latency worst             %.1f ms" % timings[-1])

    if verbose and result["missed_examples"]:
        print()
        print("    pairs the search did NOT surface (first few):")
        for left, right, cat_no, why in result["missed_examples"]:
            print("      cat %-12s %s" % (cat_no, why))
            print("        A: %s" % left[:72])
            print("        B: %s" % right[:72])

    if verbose and result["negative_examples"]:
        print()
        print("    hard negatives that scored 0.75 or more (would be shown):")
        for left, right, score, why in result["negative_examples"]:
            print("      %.3f  %s" % (score, why[:60]))
            print("        typed: %s" % left[:72])
            print("        shown: %s" % right[:72])


def report_veto_gate():
    """Every known look-alike pair, from the same list the unit tests use.

    Imported from tests/test_specs.py rather than copied, so the harness and
    the tests can never disagree about what the gate is supposed to do.
    """
    print()
    print("  VETO GATE -- the known pairs")
    print()
    cancel_failures = allow_failures = 0

    print("    must CANCEL (different products):")
    for label, a, b, _why in MUST_CANCEL:
        reason = veto_reason(a, b)
        ok = reason is not None
        cancel_failures += 0 if ok else 1
        print("      %-4s %-24s %s" % ("ok" if ok else "FAIL", label,
                                       reason or "NOT CANCELLED"))

    print()
    print("    must ALLOW (same product, written two ways):")
    for label, a, b, _why in MUST_ALLOW:
        reason = veto_reason(a, b)
        ok = reason is None
        allow_failures += 0 if ok else 1
        print("      %-4s %-24s %s" % ("ok" if ok else "FAIL", label,
                                       reason or ""))

    total = len(MUST_CANCEL) + len(MUST_ALLOW)
    passed = total - cancel_failures - allow_failures
    print()
    print("    %d of %d known pairs behave correctly" % (passed, total))
    return cancel_failures == 0 and allow_failures == 0


def main():
    quick = "--quick" in sys.argv
    verbose = "--quiet" not in sys.argv
    only = None
    if "--split" in sys.argv:
        only = sys.argv[sys.argv.index("--split") + 1]

    frappe = connect()

    # Weights come from Settings, like everywhere else in this app, and are
    # printed so a number in this report can be reproduced later.
    try:
        from item_duplicate_check.core.settings import get_settings
        weights = get_settings()["weights"]
    except Exception:
        weights = dict(DEFAULT_WEIGHTS)

    print("=" * 72)
    print("STEP H -- evaluation harness, site_local")
    print("=" * 72)
    print()
    print("  weights       %s" % weights)
    print("  thresholds    %s" % list(THRESHOLDS))
    print("  random_state  %d" % RANDOM_STATE)
    if quick:
        print("  MODE          --quick (300 pairs per split, for a fast check)")

    # ---- load and group -------------------------------------------------
    records = load_items(frappe)
    group_of, merges = build_groups(records)
    split_of_group, members = split_groups(group_of, records)

    rows_by_split = {"train": [], "validation": [], "test": []}
    for row, record in zip(rows_from_dicts(records), records):
        rows_by_split[split_of_group[group_of[record["name"]]]].append(row)

    group_to_item = {code: group_of[code] for code in group_of}

    print()
    print("  product groups built by union-find:")
    print("    linked by Cat No             %6d merges" % merges["cat_no"])
    print("    linked by identical name     %6d merges" % merges["item_name"])
    print("    linked by old item code      %6d merges" % merges["old_code"])
    print("    distinct product groups      %6d  (from %d items)"
          % (len(members), len(records)))
    print("    groups with >1 item          %6d"
          % sum(1 for m in members.values() if len(m) > 1))

    print()
    print("  split (70/15/15 by product group):")
    print("    %-12s %8s %8s %8s" % ("split", "items", "share", "groups"))
    print("    " + "-" * 40)
    for name in ("train", "validation", "test"):
        count = len(rows_by_split[name])
        groups = sum(1 for g, s in split_of_group.items() if s == name)
        print("    %-12s %8d %7.1f%% %8d"
              % (name, count, count * 100.0 / len(records), groups))

    # ---- leakage proof --------------------------------------------------
    spans = 0
    for gid, codes in members.items():
        if len({split_of_group[group_of[c]] for c in codes}) > 1:
            spans += 1
    names_by_split = {}
    for name in ("train", "validation", "test"):
        names_by_split[name] = {r.normalized for r in rows_by_split[name]
                                if r.normalized}
    shared_names = (
        len(names_by_split["train"] & names_by_split["validation"])
        + len(names_by_split["train"] & names_by_split["test"])
        + len(names_by_split["validation"] & names_by_split["test"]))
    print()
    print("  leakage check:")
    print("    product groups spanning more than one split  %4d   %s"
          % (spans, "OK" if spans == 0 else "LEAK"))
    print("    identical names split across sides           %4d   %s"
          % (shared_names, "OK" if shared_names == 0 else "LEAK"))

    # ---- evaluate -------------------------------------------------------
    wanted = [only] if only else ["validation", "test"]
    results = {}
    for name in wanted:
        started = time.perf_counter()
        results[name] = evaluate(name, rows_by_split[name], group_to_item,
                                 weights, quick=quick, verbose=verbose)
        print()
        print("  (%s split evaluated in %.1f s)"
              % (name, time.perf_counter() - started))
        report_split(results[name], verbose=verbose)

    gate_ok = report_veto_gate()

    # ---- acceptance -----------------------------------------------------
    judged = results.get("validation") or results.get(only)
    print()
    print("=" * 72)
    print("  ACCEPTANCE (judged on the VALIDATION split -- test is never tuned on)")
    print("=" * 72)
    print()
    if judged:
        p92, _r92, _tp, _fp = precision_recall(judged["positives"],
                                               judged["negatives"], 0.92)
        p95_latency = judged["timings"][int(len(judged["timings"]) * 0.95)]
        checks = [
            ("precision at 0.92 >= 90%", p92 >= TARGETS["precision_at_0.92"],
             "%.1f%%" % (p92 * 100)),
            ("recall@5 >= 70%",
             judged["recall_at_5"] >= TARGETS["recall_at_5"],
             "%.1f%%" % (judged["recall_at_5"] * 100)),
            ("latency p95 < 300 ms",
             p95_latency < TARGETS["latency_p95_ms"],
             "%.1f ms" % p95_latency),
            ("all known spec pairs pass", gate_ok,
             "%d pairs" % (len(MUST_CANCEL) + len(MUST_ALLOW))),
        ]
        for label, ok, value in checks:
            print("    %-4s %-32s %s" % ("PASS" if ok else "FAIL", label, value))
        print()
        print("    %s" % ("ALL TARGETS MET" if all(c[1] for c in checks)
                          else "ONE OR MORE TARGETS MISSED"))

    frappe.destroy()


if __name__ == "__main__":
    main()
