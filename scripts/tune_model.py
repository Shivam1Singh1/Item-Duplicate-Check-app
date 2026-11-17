"""
tune_model.py  --  fine-tune on the three factors, and settle the Cat No
question with a measurement instead of an argument.

THE THREE FACTORS
-----------------
    NAME     eleven features, from character similarity to rare-word overlap
    MAKES    two features, after the alias table collapses 1,438 spellings
    CAT NO   already used -- as a Step 0 EXACT LOOKUP, not as a model feature

The third one is the point of this script. "Use Cat No as a factor" sounds
obviously right and is the one thing that would quietly ruin the model, so
rather than assert that, variant D below actually trains with it and shows
what happens.

WHY IT RUINS THE MODEL
----------------------
Every positive pair in the dataset is a pair that SHARES A CAT NO -- that is
how the labels were made. So a Cat No feature is not evidence, it is the
answer key. A model given it learns one rule, "same Cat No means same item",
scores beautifully on this data, and knows nothing about names. And 30% of
the catalogue has no usable Cat No at all, so on those items it has nothing
left to say.

Variant D measures exactly that: near-perfect on pairs that have a Cat No,
and then the collapse on pairs that do not.

Meanwhile Cat No is NOT wasted. It is Step 0 of every search, an exact
lookup worth 0.97, applied before any scoring and under the veto gate. That
is decision 6: a key, not a clue.

WHAT ELSE IT TUNES
------------------
  - a leaner feature set: name + makes only, dropping the four features that
    the separation table showed carry nothing
  - hyperparameters for both model families
  - all of it judged on VALIDATION. Test is not read here at all.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/tune_model.py
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT                                  # noqa: E402

from item_duplicate_check.core.features import FEATURE_NAMES      # noqa: E402

DATA = os.path.join(APP_ROOT, "data")
RANDOM_STATE = 42
THRESHOLDS = (0.75, 0.85, 0.92)

#: Measured in step H with the hand-written scorer, on validation.
BASELINE = {"precision_at_0.92": 1.000, "recall_at_0.92": 0.642}

# --------------------------------------------------------------------------
# Feature subsets. Each is a view on the SAME dataset, so the comparison is
# about what the model is allowed to see and nothing else.
# --------------------------------------------------------------------------

NAME_FEATURES = [
    "ordered_similarity", "raw_similarity", "set_overlap", "length_ratio",
    "set_overlap_x_length", "idf_shared_fraction", "shared_token_count",
    "token_count_min", "token_count_diff", "digit_token_overlap",
    "first_token_match",
]
MAKES_FEATURES = ["makes_agree", "makes_abstain"]

VARIANTS = {
    "A name only": NAME_FEATURES,
    "B name + makes": NAME_FEATURES + MAKES_FEATURES,
    "C everything (current)": list(FEATURE_NAMES),
    "D name + makes + CAT NO": NAME_FEATURES + MAKES_FEATURES + ["cat_no_agree"],
}


def load(split):
    """Rows, with the two Cat No diagnostics turned into a feature so that
    variant D can be trained. They live in the CSV as `_cat_no_*` precisely
    so nothing can pick them up by accident."""
    path = os.path.join(DATA, "pairs_%s.csv" % split)
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            values = {name: float(row[name]) for name in FEATURE_NAMES}
            cat_a = (row.get("_cat_no_a") or "").strip()
            cat_b = (row.get("_cat_no_b") or "").strip()
            values["cat_no_agree"] = (1.0 if cat_a and cat_b and cat_a == cat_b
                                      else 0.0)
            rows.append({
                "x": values,
                "y": int(row["label"]),
                "veto": bool(row["_veto_reason"]),
                "has_cat_no": bool(cat_a and cat_b),
            })
    return rows


def matrix(rows, features):
    return ([[r["x"][f] for f in features] for r in rows],
            [r["y"] for r in rows])


def band_metrics(scores, rows, threshold):
    """Precision and recall WITH the veto gate, as production applies it."""
    tp = fp = fn = 0
    for score, row in zip(scores, rows):
        effective = 0.0 if row["veto"] else score
        flagged = effective >= threshold
        if flagged and row["y"] == 1:
            tp += 1
        elif flagged and row["y"] == 0:
            fp += 1
        elif not flagged and row["y"] == 1:
            fn += 1
    precision = tp / float(tp + fp) if (tp + fp) else 1.0
    recall = tp / float(tp + fn) if (tp + fn) else 0.0
    return precision, recall


def main():
    import numpy as np
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    train_rows = load("train")
    val_rows = load("validation")

    print("=" * 76)
    print("FINE TUNING -- name, makes, and the Cat No question")
    print("=" * 76)
    print()
    print("  train %d rows, validation %d rows" % (len(train_rows),
                                                   len(val_rows)))
    print("  validation pairs WITH a Cat No on both sides: %d"
          % sum(1 for r in val_rows if r["has_cat_no"]))
    print("  validation pairs WITHOUT one:                 %d"
          % sum(1 for r in val_rows if not r["has_cat_no"]))

    def make_models():
        return {
            "logistic": Pipeline([
                ("scale", StandardScaler()),
                ("lr", LogisticRegression(max_iter=4000,
                                          random_state=RANDOM_STATE,
                                          class_weight="balanced")),
            ]),
            "boosted": CalibratedClassifierCV(
                GradientBoostingClassifier(random_state=RANDOM_STATE,
                                           n_estimators=200, max_depth=3),
                method="sigmoid", cv=5),
        }

    results = {}
    print()
    print("  %-26s %-10s %8s %10s %9s" % ("variant", "model", "PR AUC",
                                          "prec@0.92", "rec@0.92"))
    print("  " + "-" * 70)

    for label, features in VARIANTS.items():
        X_train, y_train = matrix(train_rows, features)
        X_val, _y_val = matrix(val_rows, features)
        X_train, y_train = np.array(X_train), np.array(y_train)
        X_val = np.array(X_val)

        for model_name, model in make_models().items():
            model.fit(X_train, y_train)
            scores = model.predict_proba(X_val)[:, 1]
            pr_auc = average_precision_score([r["y"] for r in val_rows],
                                             scores)
            p92, r92 = band_metrics(scores, val_rows, 0.92)
            results[(label, model_name)] = {
                "pr_auc": float(pr_auc), "scores": scores,
                "bands": {t: band_metrics(scores, val_rows, t)
                          for t in THRESHOLDS},
                "features": features,
            }
            print("  %-26s %-10s %8.4f %9.1f%% %8.1f%%"
                  % (label, model_name, pr_auc, p92 * 100, r92 * 100))

    # ---- the Cat No demonstration ---------------------------------------
    print()
    print("=" * 76)
    print("  THE CAT NO EXPERIMENT -- variant D, split by whether the pair")
    print("  actually HAS a Cat No")
    print("=" * 76)
    print()
    print("  %-34s %10s %10s" % ("", "with Cat No", "without"))
    print("  " + "-" * 56)

    with_rows = [r for r in val_rows if r["has_cat_no"]]
    without_rows = [r for r in val_rows if not r["has_cat_no"]]

    for label in ("B name + makes", "D name + makes + CAT NO"):
        for model_name in ("boosted",):
            entry = results[(label, model_name)]
            scores = entry["scores"]
            with_scores = [s for s, r in zip(scores, val_rows)
                           if r["has_cat_no"]]
            without_scores = [s for s, r in zip(scores, val_rows)
                              if not r["has_cat_no"]]
            p_with, r_with = band_metrics(with_scores, with_rows, 0.92)
            p_without, r_without = band_metrics(without_scores, without_rows,
                                                0.92)
            print("  %-34s %9.1f%% %9.1f%%"
                  % ("%s  recall@0.92" % label[:28], r_with * 100,
                     r_without * 100))
            print("  %-34s %9.1f%% %9.1f%%"
                  % ("    precision@0.92", p_with * 100, p_without * 100))

    print()
    print("  Read the two recall columns for variant D. If the Cat No model")
    print("  is far better WITH a Cat No and no better WITHOUT one, it has")
    print("  learned the answer key and not the names -- and 30% of the")
    print("  catalogue has no Cat No.")

    # ---- against the rules ----------------------------------------------
    print()
    print("=" * 76)
    print("  BEST HONEST VARIANT AGAINST THE HAND-WRITTEN RULES")
    print("=" * 76)
    honest = {k: v for k, v in results.items()
              if "CAT NO" not in k[0]}
    best_key = max(honest, key=lambda k: honest[k]["pr_auc"])
    best = honest[best_key]
    print()
    print("  best: %s / %s   (PR AUC %.4f, %d features)"
          % (best_key[0], best_key[1], best["pr_auc"],
             len(best["features"])))
    print()
    print("  %-14s %12s %12s %10s" % ("threshold", "rules", "model", "change"))
    print("  " + "-" * 52)
    for t in THRESHOLDS:
        p, r = best["bands"][t]
        if t == 0.92:
            print("  %-14s %11.1f%% %11.1f%% %+9.1f"
                  % ("precision@%.2f" % t, BASELINE["precision_at_0.92"] * 100,
                     p * 100, (p - BASELINE["precision_at_0.92"]) * 100))
            print("  %-14s %11.1f%% %11.1f%% %+9.1f"
                  % ("recall@%.2f" % t, BASELINE["recall_at_0.92"] * 100,
                     r * 100, (r - BASELINE["recall_at_0.92"]) * 100))
        else:
            print("  %-14s %12s %11.1f%% / %.1f%%"
                  % ("@%.2f" % t, "-", p * 100, r * 100))

    payload = {
        "variants": {
            "%s | %s" % k: {"pr_auc": v["pr_auc"],
                            "features": v["features"],
                            "bands": {str(t): list(b)
                                      for t, b in v["bands"].items()}}
            for k, v in results.items()
        },
        "best_honest": "%s | %s" % best_key,
        "baseline": BASELINE,
        "note": ("Variant D includes cat_no_agree and is reported only to "
                 "demonstrate label leakage. It must never be shipped."),
    }
    with open(os.path.join(DATA, "tuning_results.json"), "w",
              encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print()
    print("  written to data/tuning_results.json")


if __name__ == "__main__":
    main()
