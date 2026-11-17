"""
model.py  --  score candidates with the trained pair classifier.

WHERE THIS SITS
---------------
    corpus.py     produces the shortlist          (a model cannot find items)
    features.py   turns a pair into 21 numbers
    model.py      THIS FILE: asks the classifier for a probability
    specs.py      the veto gate, applied on top of whatever comes back

The model replaces the weighted sum in scorer.py and nothing else. It does
not replace the shortlist, and it does not replace the gate.

IT IS NOT THE DEFAULT, AND THAT IS A MEASURED DECISION
------------------------------------------------------
Measured on validation, veto gate applied, against the hand-written scorer:

                        precision@0.92   recall@0.92
    hand-written rules        100.0%         64.2%
    gradient boosting          99.3%         65.5%
    logistic regression        97.6%         59.2%

Decision 8, written before any of this existed: "Ship the hand-written
weights unless the model clearly wins. Ties go to the simpler option." It
does not clearly win -- 0.7 points of precision for 1.3 points of recall --
so `rules` is the default engine and this file runs only when Item Duplicate
Check Settings says `model`.

Both engines stay in the code. That is also decision 8: "Keep the
hand-written scorer in the code permanently as an automatic fallback if the
model file is missing or fails to load."

THE FEATURE ORDER CHECK IS THE IMPORTANT PART OF load()
-------------------------------------------------------
A model's coefficients are positional. If FEATURE_NAMES gains, loses or
reorders a column, every number the saved model produces is computed against
the wrong columns -- and it will not raise, it will just be wrong. So the
feature list is saved inside the model file and compared on load. A mismatch
refuses to load and falls back to the rules, which is the only safe failure.

WHY THE SHORTLIST IS TRIMMED BEFORE SCORING
-------------------------------------------
The hand-written scorer can stop early, because its score has a computable
upper bound. A model has no such bound -- there is no way to know what it
will say about a pair without asking. So on a fallback query, which hands
over all 22,204 rows, scoring everything costs seconds.

This file therefore ranks candidates by cheap name similarity and asks the
model about the best PREFILTER_LIMIT of them. That makes the model path an
APPROXIMATE top five, unlike the rules path which is exact. The cost is
measured rather than assumed: see scripts/eval_harness.py.
"""

import os

from item_duplicate_check.core import makes as makes_module
from item_duplicate_check.core.features import FEATURE_NAMES, pair_features
from item_duplicate_check.core.scorer import (
    cat_no_step0,
    Match,
    band_for,
    ordered_similarity_keys,
    shared_size_phrase,
)
from item_duplicate_check.core.specs import veto_reason

#: item_duplicate_check/model/pair_classifier.joblib
MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "model", "pair_classifier.joblib")

#: How many candidates the model is asked about. Everything past this is
#: dropped on cheap name similarity alone. 200 was enough to hold recall@5 on
#: validation while keeping a full-scan query inside the 300 ms budget.
PREFILTER_LIMIT = 200

TOP_N = 5
TOP_N_CANCELLED = 5

#: Loaded once per process. A joblib load is tens of milliseconds and the
#: file does not change while a worker lives.
_CACHE = {"loaded": False, "predictor": None, "error": None}


def load(path: str = None, force: bool = False):
    """The trained classifier, or None if it cannot be trusted.

    Returns None rather than raising for every failure mode -- missing file,
    unreadable file, missing sklearn, mismatched feature list. The caller
    falls back to the rules, which is a working engine, so there is never a
    reason to fail the user's request over this.
    """
    if _CACHE["loaded"] and not force:
        return _CACHE["predictor"]

    _CACHE["loaded"] = True
    _CACHE["predictor"] = None
    _CACHE["error"] = None

    target = path or MODEL_PATH
    if not os.path.exists(target):
        _CACHE["error"] = "no model file at %s" % target
        return None

    try:
        import joblib

        payload = joblib.load(target)
    except Exception as exc:                      # unreadable, or no sklearn
        _CACHE["error"] = "could not load: %s: %s" % (type(exc).__name__, exc)
        return None

    saved = list(payload.get("features") or [])
    if saved != list(FEATURE_NAMES):
        # THE CHECK THAT MATTERS. Coefficients are positional, so a changed
        # feature list means every prediction is computed against the wrong
        # columns -- silently, with no error. Refuse.
        _CACHE["error"] = (
            "feature list mismatch: model was trained on %d features, this "
            "code defines %d. Retrain before using it." % (len(saved),
                                                           len(FEATURE_NAMES)))
        return None

    if not hasattr(payload.get("model"), "predict_proba"):
        _CACHE["error"] = "saved object has no predict_proba"
        return None

    _CACHE["predictor"] = payload
    return payload


def load_error():
    """Why load() returned None, for logging and for engine_status."""
    return _CACHE["error"]


def is_available(path: str = None) -> bool:
    return load(path) is not None


def build_reason(values: dict, query, row, aliases=None) -> str:
    """One human sentence, from the feature values rather than from weights.

    Decision 10: show the reason, not the number. A model has no readable
    weights -- and on this feature set the logistic regression's were not
    readable either, because the name-similarity features are collinear --
    so the sentence is built from what the features SAY about the pair, not
    from what the model did with them. That is also more honest: it describes
    the evidence, not the arithmetic.
    """
    parts = []

    ordered = values.get("ordered_similarity")
    if ordered is not None:
        parts.append("names %d%% alike" % round(ordered * 100))

    if values.get("makes_agree") == 1.0:
        parts.append("same manufacturer (%s)"
                     % makes_module.canonical(row.makes, aliases))
    elif values.get("makes_abstain") == 0.0 and values.get("makes_agree") == 0.0:
        parts.append("different manufacturer")

    phrase = shared_size_phrase(query.name, row.item_name)
    if phrase:
        parts.append(phrase)

    if values.get("hsn_agree") == 1.0:
        parts.append("same HSN code")

    return " - ".join(parts)


def search(corpus, query, predictor=None, strong=None, soft=None,
           aliases=None, limit=TOP_N, prefilter=PREFILTER_LIMIT) -> dict:
    """The model path. Same return shape as scorer.search().

    Raises nothing the caller has to handle beyond what api.py already
    catches; returns an empty result if there is no usable model.
    """
    predictor = predictor or load()
    if predictor is None:
        return {"matches": [], "cancelled": [], "candidates": 0,
                "total_similar": 0, "band": "none", "via": "none",
                "engine": "model-unavailable"}

    if aliases is None:
        aliases = makes_module._ALIASES
    strong = 0.92 if strong is None else strong
    soft = 0.75 if soft is None else soft

    # ---- STEP 0: exact Cat No, same as the rules path --------------------
    # Shared with scorer.py rather than reimplemented. Cat No is decision 6
    # and is independent of which scorer is running: a shared Cat No answers
    # the question directly. The gate still applies on top of it.
    matches, cancelled, seen = cat_no_step0(corpus, query, strong, soft,
                                            aliases)

    candidates = [r for r in corpus.candidates(
        query.name, exclude_item_code=query.item_code)
        if r.item_code not in seen]

    # ---- cheap prefilter ------------------------------------------------
    # The model has no upper bound to stop on, so the shortlist is trimmed
    # first. Ranked on the same name similarity the features use, so the
    # ordering is at least consistent with what the model will see.
    if len(candidates) > prefilter:
        ranked = sorted(
            candidates,
            key=lambda row: (-ordered_similarity_keys(query.sorted_key,
                                                      row.sorted_key),
                             row.disabled, row.item_code))
        candidates = ranked[:prefilter]

    # ---- features for the survivors -------------------------------------
    rows, vectors = [], []
    feature_values = []
    for row in candidates:
        values = pair_features(query, row, df=corpus.df,
                               corpus_size=len(corpus), aliases=aliases)
        rows.append(row)
        feature_values.append(values)
        vectors.append([values[name] for name in FEATURE_NAMES])

    # ---- one batched call, not one per candidate -------------------------
    # Guarded: sklearn raises on an empty input array, and an empty shortlist
    # is normal -- a name nobody has typed before shares no rare word with
    # anything, and Step 0 may still have found a Cat No match on its own.
    if vectors:
        probabilities = predictor["model"].predict_proba(vectors)[:, 1]
    else:
        probabilities = []

    # matches/cancelled already seeded by Step 0 above
    for row, values, probability in zip(rows, feature_values, probabilities):
        score = float(probability)

        # THE GATE STILL APPLIES. A model trained on these labels will call a
        # 500 g pack and a 1 kg pack the same product, because by name they
        # almost are. Decision 3 measured the gate as precision 78.1% ->
        # 91.0%, and nothing about changing the scorer changes that.
        cancel_reason = veto_reason(query.name, row.item_name)

        components = dict(values)
        components["raw_score"] = score
        components["score"] = 0.0 if cancel_reason else score
        components["cancel_reason"] = cancel_reason
        components["via"] = "model"

        match = Match(
            row=row,
            score=components["score"],
            band=band_for(components["score"], strong, soft),
            via="model",
            reason=build_reason(values, query, row, aliases),
            cancel_reason=cancel_reason,
            components=components,
        )
        (cancelled if cancel_reason else matches).append(match)

    # Counted before the truncation, same contract as scorer.search(). The
    # model path scores every survivor of the prefilter, so there is no early
    # exit to relax here -- the count is simply taken while the full list is
    # still in hand.
    total_similar = sum(1 for m in matches if m.score >= soft)

    matches.sort(key=lambda m: (-m.score, m.disabled, m.item_code))
    del matches[limit:]
    cancelled.sort(key=lambda m: -m.components.get("raw_score", 0))
    del cancelled[TOP_N_CANCELLED:]

    return {
        "matches": matches,
        "cancelled": cancelled,
        "candidates": len(candidates),
        "total_similar": total_similar,
        "band": matches[0].band if matches else "none",
        "via": "model",
        "engine": "model",
    }
