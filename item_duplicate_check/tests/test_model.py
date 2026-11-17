"""
test_model.py  --  the model path, and the four ways it is allowed to fail.

The tests that matter are in TestLoadRefusesBadModels. A model that loads
when it should not produces numbers computed against the wrong columns, and
it does not raise -- it is simply wrong, quietly, for as long as nobody
checks. So every refusal is asserted.

These tests do not require a trained model to exist. The ones that do are
skipped when it is absent, so the suite passes on a fresh checkout.

RUN
---
    cd ~/frappe-bench/frappe-bench
    PYTHONPATH=apps/item_duplicate_check env/bin/python -m unittest \\
        item_duplicate_check.tests.test_model -v
"""

import os
import tempfile
import unittest

from item_duplicate_check.core import model as model_engine
from item_duplicate_check.core.corpus import build_corpus, rows_from_dicts
from item_duplicate_check.core.features import FEATURE_NAMES
from item_duplicate_check.core.scorer import Query

ALIASES = {"thermo": "thermo", "thermo scientific": "thermo"}


def catalogue():
    return build_corpus(rows_from_dicts([
        {"name": "I1", "item_name": "Serological Pipette 50 mL",
         "cat_no": "167008", "makes": "Thermo Scientific"},
        {"name": "I2", "item_name": "Serological Pipette 5 mL",
         "cat_no": "167001", "makes": "Thermo"},
        {"name": "I3", "item_name": "Glycine 500g", "cat_no": "G8898"},
        {"name": "I4", "item_name": "Nunc 96-Well Plate"},
    ]))


class ConstantPredictor(object):
    """Stands in for a trained classifier. Returns a fixed probability, so a
    test can assert the plumbing without depending on what a real model
    happens to think.

    Returns a numpy array, because that is what sklearn returns and the
    caller slices it as `[:, 1]`. The first version of this stub returned a
    list of lists and the error it produced looked like a bug in model.py.
    """

    def __init__(self, probability=0.99):
        self.probability = probability
        self.calls = 0

    def predict_proba(self, vectors):
        import numpy as np

        self.calls += 1
        return np.array([[1.0 - self.probability, self.probability]
                         for _ in vectors])


def wrapped(predictor):
    return {"model": predictor, "features": list(FEATURE_NAMES),
            "engine_version": "test"}


class TestLoadRefusesBadModels(unittest.TestCase):
    """Every refusal returns None and records why. None of them raise: the
    caller falls back to the rules, which is a working engine."""

    def setUp(self):
        model_engine._CACHE.update({"loaded": False, "predictor": None,
                                    "error": None})

    def tearDown(self):
        model_engine._CACHE.update({"loaded": False, "predictor": None,
                                    "error": None})

    def test_a_missing_file_is_not_an_error(self):
        self.assertIsNone(model_engine.load("/nonexistent/model.joblib"))
        self.assertIn("no model file", model_engine.load_error())

    def test_an_unreadable_file_is_not_an_error(self):
        handle = tempfile.NamedTemporaryFile(suffix=".joblib", delete=False)
        handle.write(b"this is not a joblib file")
        handle.close()
        try:
            self.assertIsNone(model_engine.load(handle.name, force=True))
            self.assertIn("could not load", model_engine.load_error())
        finally:
            os.unlink(handle.name)

    def test_a_feature_list_mismatch_is_refused(self):
        """THE test.

        Coefficients are positional. A model trained on a different feature
        list computes every number against the wrong columns, silently. There
        is no safe way to use it, so it must not load.
        """
        import joblib

        path = tempfile.mktemp(suffix=".joblib")
        joblib.dump({"model": ConstantPredictor(),
                     "features": list(FEATURE_NAMES[:-3]),   # three missing
                     "engine_version": "stale"}, path)
        try:
            self.assertIsNone(model_engine.load(path, force=True))
            self.assertIn("feature list mismatch", model_engine.load_error())
        finally:
            os.unlink(path)

    def test_a_reordered_feature_list_is_refused(self):
        """Same length, different order -- the most dangerous case, because
        nothing about the file looks wrong."""
        import joblib

        reordered = list(FEATURE_NAMES)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        path = tempfile.mktemp(suffix=".joblib")
        joblib.dump({"model": ConstantPredictor(), "features": reordered,
                     "engine_version": "reordered"}, path)
        try:
            self.assertIsNone(model_engine.load(path, force=True))
            self.assertIn("feature list mismatch", model_engine.load_error())
        finally:
            os.unlink(path)

    def test_an_object_without_predict_proba_is_refused(self):
        import joblib

        path = tempfile.mktemp(suffix=".joblib")
        joblib.dump({"model": "not a model", "features": list(FEATURE_NAMES)},
                    path)
        try:
            self.assertIsNone(model_engine.load(path, force=True))
            self.assertIn("predict_proba", model_engine.load_error())
        finally:
            os.unlink(path)


class TestModelSearch(unittest.TestCase):
    """The scoring path, driven by a stand-in predictor."""

    def setUp(self):
        self.corpus = catalogue()

    def test_an_unavailable_model_returns_an_empty_result_not_an_error(self):
        """`predictor=None` makes search() load from disk, and on a machine
        where a model HAS been trained that succeeds -- which is why the
        cache is forced into the unavailable state here rather than relying
        on the file being absent."""
        saved = dict(model_engine._CACHE)
        model_engine._CACHE.update({"loaded": True, "predictor": None,
                                    "error": "forced by test"})
        try:
            out = model_engine.search(self.corpus, Query("Plate"),
                                      predictor=None, aliases=ALIASES)
            self.assertEqual(out["matches"], [])
            self.assertEqual(out["engine"], "model-unavailable")
        finally:
            model_engine._CACHE.update(saved)

    def test_the_probability_becomes_the_score(self):
        predictor = ConstantPredictor(0.99)
        out = model_engine.search(self.corpus,
                                  Query("Serological Pipette 50 ml"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES)
        self.assertTrue(out["matches"])
        self.assertAlmostEqual(out["matches"][0].score, 0.99, places=6)
        self.assertEqual(out["matches"][0].via, "model")
        self.assertEqual(out["band"], "strong")

    def test_the_model_is_called_once_for_the_whole_shortlist(self):
        """One batched call, not one per candidate. On a fallback query the
        difference is 200 calls against 1."""
        predictor = ConstantPredictor()
        model_engine.search(self.corpus, Query("Serological Pipette 50 ml"),
                            predictor=wrapped(predictor), aliases=ALIASES)
        self.assertEqual(predictor.calls, 1)

    def test_the_veto_gate_still_overrides_the_model(self):
        """A model trained on these labels calls a 5 mL and a 50 mL pipette
        the same product, because by name they almost are. The gate is what
        stops that, and it does not care which scorer produced the score."""
        predictor = ConstantPredictor(0.99)
        out = model_engine.search(self.corpus,
                                  Query("Serological Pipette 5 mL"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES)
        offered = {m.item_code for m in out["matches"]}
        cancelled = {m.item_code for m in out["cancelled"]}
        self.assertNotIn("I1", offered)          # the 50 mL one
        self.assertIn("I1", cancelled)
        reason = next(m.cancel_reason for m in out["cancelled"]
                      if m.item_code == "I1")
        self.assertIn("size", reason)

    def test_step_zero_runs_on_the_model_path_too(self):
        """Cat No is decision 6 and has nothing to do with which scorer is
        selected. Before it was shared, the model path had no Step 0 at all,
        which would have made the strongest signal in the system depend on a
        settings switch."""
        predictor = ConstantPredictor(0.10)      # model says "no"
        out = model_engine.search(self.corpus,
                                  Query("Something unrelated entirely",
                                        cat_no="167008"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES)
        top = out["matches"][0]
        self.assertEqual(top.item_code, "I1")
        self.assertEqual(top.via, "cat_no")
        self.assertGreater(top.score, 0.9)

    def test_at_most_five_matches(self):
        predictor = ConstantPredictor(0.99)
        out = model_engine.search(self.corpus, Query("Pipette"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES, limit=2)
        self.assertLessEqual(len(out["matches"]), 2)

    def test_the_prefilter_caps_how_many_pairs_reach_the_model(self):
        """The model has no upper bound to stop on, so the shortlist is
        trimmed. Without this a one-word name hands over all 22,204 rows."""
        predictor = ConstantPredictor(0.99)
        out = model_engine.search(self.corpus, Query("Pipette"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES, prefilter=1)
        self.assertLessEqual(out["candidates"], 1)

    def test_an_empty_shortlist_does_not_call_the_model(self):
        """sklearn raises on an empty input array, and an empty shortlist is
        normal for a name nobody has typed before."""
        predictor = ConstantPredictor()
        out = model_engine.search(
            self.corpus, Query("qwertyuiop asdfghjkl zxcvbnm"),
            predictor=wrapped(predictor), aliases=ALIASES)
        self.assertEqual(predictor.calls, 0)
        self.assertEqual(out["matches"], [])

    def test_the_reason_describes_the_evidence(self):
        """Decision 10: show the reason, not the number. A model has no
        readable weights, so the sentence comes from the feature values."""
        predictor = ConstantPredictor(0.99)
        out = model_engine.search(self.corpus,
                                  Query("Serological Pipette 50 ml",
                                        makes="Thermo"),
                                  predictor=wrapped(predictor),
                                  aliases=ALIASES)
        reason = out["matches"][0].reason
        self.assertIn("names 100% alike", reason)
        self.assertIn("same manufacturer (thermo)", reason)
        self.assertIn("both 50 mL", reason)


@unittest.skipUnless(os.path.exists(model_engine.MODEL_PATH),
                     "no trained model on disk")
class TestTheRealTrainedModel(unittest.TestCase):
    """Only runs when a model has actually been trained."""

    def test_it_loads_and_its_feature_list_matches_this_code(self):
        payload = model_engine.load(force=True)
        self.assertIsNotNone(payload, model_engine.load_error())
        self.assertEqual(list(payload["features"]), list(FEATURE_NAMES))

    def test_it_scores_a_real_duplicate_higher_than_an_unrelated_item(self):
        corpus = catalogue()
        payload = model_engine.load(force=True)
        out = model_engine.search(corpus, Query("Serological Pipette 50 ml",
                                                makes="Thermo"),
                                  predictor=payload, aliases=ALIASES)
        self.assertTrue(out["matches"])
        scores = {m.item_code: m.score for m in out["matches"]}
        self.assertIn("I1", scores)
        if "I4" in scores:                       # Nunc plate, unrelated
            self.assertGreater(scores["I1"], scores["I4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
