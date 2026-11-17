"""
train_model.py  --  fit the pair classifier, and decide honestly whether it
is worth shipping.

WHAT IT DOES
------------
  1. loads data/pairs_*.csv -- the FEATURE_NAMES columns ONLY
  2. fits Logistic Regression (decision 8's choice) and Gradient Boosting
     (the challenger, trained for comparison and nothing else)
  3. calibrates the probabilities, because the output is banded at 0.92 and
     0.75 and an uncalibrated score makes those numbers meaningless
  4. applies the veto gate on top, exactly as production will
  5. reports precision and recall at four thresholds on VALIDATION
  6. prints the fitted coefficients, because decision 8 chose logistic
     regression partly so they could be read
  7. saves the winner with its feature order and a version stamp

THE DECISION RULE IS NOT MINE TO MAKE
-------------------------------------
Decision 8, written before any of this was built:

    "Ship the hand-written weights unless the model clearly wins on the
     expert test set. Ties go to the simpler option."

So this script reports; it does not promote. It prints the comparison against
the hand-written scorer's measured baseline and says which rule applies. The
baseline to beat, measured on validation in step H:

    precision at 0.92   100.0%
    recall at 0.92       64.2%
    recall@5             85.5%

Expect a small result. Decision 8 predicted +1.2 points and explained why:
the labels come from Cat No agreement, and those are easy pairs -- mostly
near-identical names. There is little in them for a model to learn that the
rules do not already capture. The real gain is supposed to come later, from
Item Match Log, where the examples are cases a human had to think about.

WHY THE TEST SPLIT IS NOT TOUCHED HERE
--------------------------------------
Every choice in this script -- which model, which calibration, which
threshold -- is made on validation. Test is read once, at the very end, by
whoever accepts the result. Choosing on test and then reporting test is how
a model comes to look better than it is.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/train_model.py
    env/bin/python apps/item_duplicate_check/scripts/train_model.py --save
    env/bin/python apps/item_duplicate_check/scripts/train_model.py --test
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP_ROOT                                  # noqa: E402

from item_duplicate_check.core.features import FEATURE_NAMES      # noqa: E402

DATA = os.path.join(APP_ROOT, "data")
MODEL_DIR = os.path.join(APP_ROOT, "item_duplicate_check", "model")

THRESHOLDS = (0.60, 0.75, 0.85, 0.92)
RANDOM_STATE = 42

#: Measured on validation in step H with the hand-written scorer. This is
#: what "clearly wins" has to be measured against.
BASELINE = {
    "precision_at_0.92": 1.000,
    "recall_at_0.92": 0.642,
    "recall_at_5": 0.855,
}


def load(split):
    """Features, labels and the veto flag. Features by name, never by
    position -- 'all numeric columns' would pick up the diagnostics, one of
    which is the Cat No that made the label."""
    path = os.path.join(DATA, "pairs_%s.csv" % split)
    X, y, vetoed, pairs = [], [], [], []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            X.append([float(row[name]) for name in FEATURE_NAMES])
            y.append(int(row["label"]))
            vetoed.append(bool(row["_veto_reason"]))
            pairs.append((row["item_a"], row["item_b"]))
    return X, y, vetoed, pairs


def metrics(scores, labels, vetoed, threshold):
    """Precision and recall at one threshold, WITH the gate applied.

    The gate is part of the system, so measuring the model without it would
    report a number that nobody will ever experience. A cancelled pair scores
    zero whatever the model said.
    """
    tp = fp = fn = 0
    for score, label, veto in zip(scores, labels, vetoed):
        effective = 0.0 if veto else score
        flagged = effective >= threshold
        if flagged and label == 1:
            tp += 1
        elif flagged and label == 0:
            fp += 1
        elif not flagged and label == 1:
            fn += 1
    precision = tp / float(tp + fp) if (tp + fp) else 1.0
    recall = tp / float(tp + fn) if (tp + fn) else 0.0
    return precision, recall, tp, fp


def report(name, scores, labels, vetoed):
    print()
    print("   %s" % name)
    print("     %-10s %10s %9s %8s %8s"
          % ("threshold", "precision", "recall", "TP", "FP"))
    print("     " + "-" * 50)
    out = {}
    for threshold in THRESHOLDS:
        p, r, tp, fp = metrics(scores, labels, vetoed, threshold)
        out[threshold] = (p, r)
        print("     %-10.2f %9.1f%% %8.1f%% %8d %8d"
              % (threshold, p * 100, r * 100, tp, fp))
    return out


def main():
    save = "--save" in sys.argv
    touch_test = "--test" in sys.argv

    import numpy as np
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    print("=" * 74)
    print("TRAINING THE PAIR CLASSIFIER")
    print("=" * 74)

    X_train, y_train, veto_train, _ = load("train")
    X_val, y_val, veto_val, _ = load("validation")
    X_train, y_train = np.array(X_train), np.array(y_train)
    X_val, y_val = np.array(X_val), np.array(y_val)

    print()
    print("   train       %6d rows, %4d duplicates (%.1f%%)"
          % (len(y_train), y_train.sum(), 100.0 * y_train.mean()))
    print("   validation  %6d rows, %4d duplicates (%.1f%%)"
          % (len(y_val), y_val.sum(), 100.0 * y_val.mean()))
    print("   features    %6d" % len(FEATURE_NAMES))
    print("   NOT given to the model: cat_no, old item code, stock_uom,")
    print("                           description, item_group, brand")

    # ---- the two models -------------------------------------------------
    # Scaled for the logistic regression: the features mix proportions in
    # [0,1] with raw counts, and without scaling the counts dominate the
    # coefficients and the readability that decision 8 chose this model for
    # is lost.
    logistic = Pipeline([
        ("scale", StandardScaler()),
        ("lr", LogisticRegression(max_iter=2000, random_state=RANDOM_STATE,
                                  class_weight="balanced")),
    ])

    # Calibration matters here, not as polish. The output is banded at 0.92
    # and 0.75, so the probabilities have to MEAN something. Sigmoid on a
    # held-out fold, 5-fold, so calibration never sees its own training data.
    boosted = CalibratedClassifierCV(
        GradientBoostingClassifier(random_state=RANDOM_STATE),
        method="sigmoid", cv=5)

    results = {}
    for name, model in (("logistic regression", logistic),
                        ("gradient boosting", boosted)):
        started = time.perf_counter()
        model.fit(X_train, y_train)
        elapsed = time.perf_counter() - started
        scores = model.predict_proba(X_val)[:, 1]

        print()
        print("   %-22s fitted in %.1f s" % (name, elapsed))
        print("     ROC AUC   %.4f        PR AUC  %.4f"
              % (roc_auc_score(y_val, scores),
                 average_precision_score(y_val, scores)))
        results[name] = {
            "model": model,
            "scores": scores,
            "bands": report("%s -- validation, veto gate applied" % name,
                            scores, y_val, veto_val),
            "roc_auc": float(roc_auc_score(y_val, scores)),
            "pr_auc": float(average_precision_score(y_val, scores)),
        }

    # ---- readable coefficients ------------------------------------------
    # Decision 8 picked logistic regression partly for this. A coefficient
    # whose sign is surprising is a finding, not a detail.
    print()
    print("   LOGISTIC REGRESSION COEFFICIENTS (standardised, largest first)")
    print()
    coefficients = logistic.named_steps["lr"].coef_[0]
    ranked = sorted(zip(FEATURE_NAMES, coefficients),
                    key=lambda kv: -abs(kv[1]))
    print("     %-32s %10s" % ("feature", "weight"))
    print("     " + "-" * 44)
    for feature, weight in ranked:
        note = ""
        if abs(weight) < 0.02:
            note = "  <- ignored by the model"
        if feature == "naming_series_differs" and weight > 0:
            note = "  <- POSITIVE: check this, see features.py"
        print("     %-32s %+10.3f%s" % (feature, weight, note))

    # ---- the decision ---------------------------------------------------
    print()
    print("=" * 74)
    print("  AGAINST THE HAND-WRITTEN BASELINE (validation, measured step H)")
    print("=" * 74)
    print()
    print("   %-26s %12s %12s %10s"
          % ("measure", "rules", "best model", "change"))
    print("   " + "-" * 62)

    best_name = max(results, key=lambda n: results[n]["pr_auc"])
    best = results[best_name]
    p92, r92 = best["bands"][0.92]

    for label, baseline, got in (
            ("precision at 0.92", BASELINE["precision_at_0.92"], p92),
            ("recall at 0.92", BASELINE["recall_at_0.92"], r92)):
        print("   %-26s %11.1f%% %11.1f%% %+9.1f"
              % (label, baseline * 100, got * 100,
                 (got - baseline) * 100))

    print()
    print("   best by PR AUC: %s" % best_name)
    print()
    clearly_wins = (p92 >= BASELINE["precision_at_0.92"]
                    and r92 > BASELINE["recall_at_0.92"] + 0.02)
    if clearly_wins:
        print("   The model CLEARLY WINS: precision held and recall improved")
        print("   by more than two points. Decision 8 allows promoting it.")
    else:
        print("   The model does NOT clearly win.")
        print()
        print("   Decision 8: \"Ship the hand-written weights unless the model")
        print("   clearly wins on the expert test set. Ties go to the simpler")
        print("   option.\" So the rules stay in place and this model is not")
        print("   promoted. Nothing here is weakened to change that verdict.")

    # ---- save -----------------------------------------------------------
    payload = {
        "engine_version": "model-1.0",
        "features": list(FEATURE_NAMES),
        "trained_on": "site_local",
        "random_state": RANDOM_STATE,
        "rows_train": int(len(y_train)),
        "validation": {
            name: {"roc_auc": r["roc_auc"], "pr_auc": r["pr_auc"],
                   "bands": {str(k): [v[0], v[1]]
                             for k, v in r["bands"].items()}}
            for name, r in results.items()
        },
        "coefficients": {f: float(w) for f, w in zip(FEATURE_NAMES,
                                                     coefficients)},
        "best_by_pr_auc": best_name,
        "clearly_wins_baseline": bool(clearly_wins),
        "baseline": BASELINE,
    }

    if save:
        import joblib

        if not os.path.isdir(MODEL_DIR):
            os.makedirs(MODEL_DIR)
        joblib.dump({"model": best["model"],
                     "features": list(FEATURE_NAMES),
                     "engine_version": payload["engine_version"]},
                    os.path.join(MODEL_DIR, "pair_classifier.joblib"))
        with open(os.path.join(MODEL_DIR, "metrics.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        print()
        print("   saved to item_duplicate_check/model/")
        print("   NOTE: saving is not promoting. api.py only uses a model")
        print("   when the Settings engine version says so.")
    else:
        with open(os.path.join(DATA, "model_metrics.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        print()
        print("   metrics written to data/model_metrics.json")
        print("   (re-run with --save to write the model file itself)")

    # ---- test, once, only if asked --------------------------------------
    if touch_test:
        X_test, y_test, veto_test, _ = load("test")
        scores = best["model"].predict_proba(np.array(X_test))[:, 1]
        print()
        print("=" * 74)
        print("  TEST SPLIT -- read once. Nothing above was chosen using it.")
        print("=" * 74)
        report("%s -- test, veto gate applied" % best_name,
               scores, y_test, veto_test)


if __name__ == "__main__":
    main()
