"""
test_features.py  --  the model's input, and the two guards that matter most.

The tests to read first are in TestNoLabelLeakage. A feature set that leaks
the label produces a model with excellent numbers and no use, and nothing
else in the test suite would notice. So those two values are asserted
unreachable rather than merely left out.

RUN
---
    cd ~/frappe-bench/frappe-bench
    PYTHONPATH=apps/item_duplicate_check env/bin/python -m unittest \\
        item_duplicate_check.tests.test_features -v
"""

import unittest

from item_duplicate_check.core.corpus import build_corpus, rows_from_dicts
from item_duplicate_check.core.features import (
    FEATURE_NAMES,
    FORBIDDEN_FIELDS,
    feature_vector,
    pair_features,
)

ALIASES = {
    "thermo": "thermo",
    "thermo scientific": "thermo",
    "thermo fisher scientific": "thermo",
    "sigma": "sigma",
    "sigma aldrich": "sigma",
}


def rows():
    """A small catalogue whose fields are filled in a deliberate pattern."""
    return rows_from_dicts([
        # a real duplicate pair, same everything
        {"name": "RDCN02030001", "item_name": "Serological Pipette 50 mL",
         "cat_no": "167008", "makes": "Thermo Scientific", "hsn": "90183100",
         "department": "R&D - MCPL", "requester": "a@x.com",
         "naming_series": "RDCN/.######"},
        {"name": "CN02030002", "item_name": "50 ml Serological pipette",
         "cat_no": "167008", "makes": "Thermo", "hsn": "90183100",
         "department": "R&D - MCPL", "requester": "a@x.com",
         "naming_series": "CN/.######"},

        # a look-alike: same name shape, different size
        {"name": "RDCN02030003", "item_name": "Serological Pipette 5 mL",
         "cat_no": "167001", "makes": "Thermo", "hsn": "90183100",
         "department": "R&D - MCPL", "requester": "a@x.com",
         "naming_series": "RDCN/.######"},

        # unrelated
        {"name": "RDRM01070004", "item_name": "Glycine 500g",
         "cat_no": "G8898", "makes": "Sigma-Aldrich", "hsn": "29220000",
         "department": "Manufacturing - MCPL", "requester": "b@x.com",
         "naming_series": "RDRM/.######"},

        # fields missing, to exercise every abstention
        {"name": "R&D05000", "item_name": "Plate sealing film"},

        # subset name, for the length penalty
        {"name": "R&D05001", "item_name": "DNAse"},
        {"name": "R&D05002", "item_name": "DNase I, RNase-free (1 U/uL)"},
    ])


class FeatureTestCase(unittest.TestCase):

    def setUp(self):
        self.corpus = build_corpus(rows())
        self.by_code = {r.item_code: r for r in self.corpus.rows}

    def features(self, code_a, code_b):
        return pair_features(self.by_code[code_a], self.by_code[code_b],
                             df=self.corpus.df,
                             corpus_size=len(self.corpus),
                             aliases=ALIASES)


class TestNoLabelLeakage(FeatureTestCase):
    """The guards. A leak here is invisible everywhere else."""

    def test_no_forbidden_field_is_named_in_the_contract(self):
        for field in FORBIDDEN_FIELDS:
            for name in FEATURE_NAMES:
                self.assertNotIn(field, name,
                                 "feature %r mentions the forbidden field %r"
                                 % (name, field))

    def test_cat_no_cannot_change_a_single_feature(self):
        """THE test. Cat No is what makes the training labels, so if the
        feature vector moves when only the Cat No changes, the model is
        learning its own answer.
        """
        a, b = self.by_code["RDCN02030001"], self.by_code["CN02030002"]
        before = feature_vector(a, b, self.corpus.df, len(self.corpus),
                                ALIASES)

        a.cat_no, b.cat_no = "totally", "different"
        after_different = feature_vector(a, b, self.corpus.df,
                                         len(self.corpus), ALIASES)
        a.cat_no = b.cat_no = "identical"
        after_identical = feature_vector(a, b, self.corpus.df,
                                         len(self.corpus), ALIASES)

        self.assertEqual(before, after_different)
        self.assertEqual(before, after_identical)

    def test_old_item_code_cannot_change_a_single_feature(self):
        """The same trap one step removed: old_code is one of the three
        signals that build the product groups, and the groups are what the
        split and the negatives come from."""
        a, b = self.by_code["RDCN02030001"], self.by_code["CN02030002"]
        before = feature_vector(a, b, self.corpus.df, len(self.corpus),
                                ALIASES)

        a.old_code, b.old_code = "OLD-1", "OLD-2"
        self.assertEqual(before, feature_vector(a, b, self.corpus.df,
                                                len(self.corpus), ALIASES))
        a.old_code = b.old_code = "OLD-SAME"
        self.assertEqual(before, feature_vector(a, b, self.corpus.df,
                                                len(self.corpus), ALIASES))

    def test_description_cannot_change_a_single_feature(self):
        """Display only: 80.9% of descriptions are a near-copy of the name,
        so scoring one double-counts the other."""
        a, b = self.by_code["RDCN02030001"], self.by_code["CN02030002"]
        before = feature_vector(a, b, self.corpus.df, len(self.corpus),
                                ALIASES)
        a.description = "a completely different description"
        b.description = "another one"
        self.assertEqual(before, feature_vector(a, b, self.corpus.df,
                                                len(self.corpus), ALIASES))


class TestContract(FeatureTestCase):

    def test_vector_matches_the_names_in_length_and_order(self):
        vector = feature_vector(self.by_code["RDCN02030001"],
                                self.by_code["CN02030002"],
                                self.corpus.df, len(self.corpus), ALIASES)
        values = self.features("RDCN02030001", "CN02030002")
        self.assertEqual(len(vector), len(FEATURE_NAMES))
        self.assertEqual(vector, [values[n] for n in FEATURE_NAMES])

    def test_feature_names_are_unique(self):
        self.assertEqual(len(FEATURE_NAMES), len(set(FEATURE_NAMES)))

    def test_every_value_is_a_finite_number(self):
        """A NaN or an infinity reaches the model as a crash or as silent
        nonsense, depending on the library. Neither is acceptable."""
        for code_a in self.by_code:
            for code_b in self.by_code:
                values = self.features(code_a, code_b)
                for name, value in values.items():
                    self.assertIsInstance(value, float, name)
                    self.assertEqual(value, value, "%s is NaN" % name)
                    self.assertLess(abs(value), 1e6, name)

    def test_similarity_features_stay_within_zero_and_one(self):
        bounded = ("ordered_similarity", "raw_similarity", "set_overlap",
                   "length_ratio", "set_overlap_x_length",
                   "idf_shared_fraction", "digit_token_overlap")
        for code_a in self.by_code:
            for code_b in self.by_code:
                values = self.features(code_a, code_b)
                for name in bounded:
                    self.assertGreaterEqual(values[name], 0.0, name)
                    self.assertLessEqual(values[name], 1.0, name)

    def test_every_feature_is_symmetric(self):
        """"Does this already exist?" has no direction. An asymmetric
        feature would make the warning depend on which item the user opened
        first."""
        for code_a in self.by_code:
            for code_b in self.by_code:
                forward = self.features(code_a, code_b)
                backward = self.features(code_b, code_a)
                self.assertEqual(forward, backward,
                                 "%s vs %s is asymmetric" % (code_a, code_b))

    def test_missing_attributes_do_not_raise(self):
        """The query side at runtime is whatever the user has typed so far,
        which may be a bare object with only a name."""

        class Typed(object):
            item_code = ""
            item_name = "Serological Pipette 50 ml"
            makes = None

        values = pair_features(Typed(), self.by_code["RDCN02030001"],
                               self.corpus.df, len(self.corpus), ALIASES)
        self.assertEqual(len(values), len(FEATURE_NAMES))
        self.assertGreater(values["ordered_similarity"], 0.9)
        self.assertEqual(values["makes_abstain"], 1.0)


class TestNameFeatures(FeatureTestCase):

    def test_a_reordered_name_is_still_a_perfect_match(self):
        values = self.features("RDCN02030001", "CN02030002")
        self.assertEqual(values["ordered_similarity"], 1.0)
        self.assertEqual(values["set_overlap"], 1.0)
        self.assertEqual(values["length_ratio"], 1.0)

    def test_raw_similarity_notices_the_reordering(self):
        """ordered_ and raw_ are both present because the gap between them
        is itself informative -- it separates "same words, shuffled" from
        "same words, same order"."""
        values = self.features("RDCN02030001", "CN02030002")
        self.assertLess(values["raw_similarity"],
                        values["ordered_similarity"])

    def test_the_subset_case_is_penalised(self):
        """set_overlap alone gives a misleading 1.0; the product corrects it."""
        values = self.features("R&D05001", "R&D05002")   # DNAse vs DNase I...
        self.assertEqual(values["set_overlap"], 1.0)
        self.assertLess(values["length_ratio"], 0.4)
        self.assertLess(values["set_overlap_x_length"], 0.4)

    def test_unrelated_names_score_low_everywhere(self):
        values = self.features("RDCN02030001", "RDRM01070004")
        self.assertLess(values["ordered_similarity"], 0.5)
        self.assertLess(values["set_overlap"], 0.5)
        self.assertEqual(values["first_token_match"], 0.0)

    def test_digit_token_overlap_separates_the_look_alike(self):
        """50 mL against 50 mL agrees; 50 mL against 5 mL does not. This is
        the column that carries size information to the model -- the veto
        gate still judges the conflict itself."""
        same = self.features("RDCN02030001", "CN02030002")
        different = self.features("RDCN02030001", "RDCN02030003")
        self.assertEqual(same["digit_token_overlap"], 1.0)
        self.assertEqual(different["digit_token_overlap"], 0.0)

    def test_rare_words_count_for_more_than_common_ones(self):
        values = self.features("RDCN02030001", "CN02030002")
        self.assertGreater(values["idf_shared_fraction"], 0.9)


class TestFieldFeatures(FeatureTestCase):

    def test_the_alias_table_makes_two_spellings_agree(self):
        """Without it this field is noise: 1,438 spellings, a few hundred
        companies. "Thermo Scientific" and "Thermo" are one manufacturer."""
        values = self.features("RDCN02030001", "CN02030002")
        self.assertEqual(values["makes_agree"], 1.0)
        self.assertEqual(values["makes_abstain"], 0.0)

    def test_different_manufacturers_disagree_without_abstaining(self):
        values = self.features("CN02030002", "RDRM01070004")
        self.assertEqual(values["makes_agree"], 0.0)
        self.assertEqual(values["makes_abstain"], 0.0)

    def test_a_missing_field_abstains_rather_than_disagreeing(self):
        """Two columns per field exist for this one reason. Blank-as-zero
        would make a missing manufacturer count as evidence AGAINST the
        pair, and 29% of items have no usable manufacturer."""
        values = self.features("RDCN02030001", "R&D05000")
        for field in ("makes", "hsn", "department", "requester"):
            self.assertEqual(values["%s_abstain" % field], 1.0, field)
            self.assertEqual(values["%s_agree" % field], 0.0, field)

    def test_junk_field_values_abstain_too(self):
        """140 items have makes "0" and 69 have "na". If those agreed with
        each other, 140 unrelated items would support one another."""
        a = self.by_code["RDCN02030001"]
        b = self.by_code["CN02030002"]
        a.makes, b.makes = "0", "na"
        values = pair_features(a, b, self.corpus.df, len(self.corpus),
                               ALIASES)
        self.assertEqual(values["makes_abstain"], 1.0)
        self.assertEqual(values["makes_agree"], 0.0)

    def test_fields_are_compared_case_and_space_insensitively(self):
        a = self.by_code["RDCN02030001"]
        b = self.by_code["CN02030002"]
        a.department, b.department = "R&D - MCPL", " r&d - mcpl "
        values = pair_features(a, b, self.corpus.df, len(self.corpus),
                               ALIASES)
        self.assertEqual(values["department_agree"], 1.0)

    def test_naming_series_differs_is_computed_as_named(self):
        """Only the mechanics are asserted here, not the direction.

        Decision 7 called this an inverted signal. It does not reproduce on
        site_local: duplicates agree on the series 74.6% of the time against
        65.2% for hard negatives, so it is an ordinary positive signal and
        the fitted coefficient on "differs" should be negative. See the long
        note in core/features.py.
        """
        crossing = self.features("RDCN02030001", "CN02030002")
        same = self.features("RDCN02030001", "RDCN02030003")
        self.assertEqual(crossing["naming_series_differs"], 1.0)
        self.assertEqual(same["naming_series_differs"], 0.0)


class TestItemCodeFeature(FeatureTestCase):

    def test_same_category_across_divisions_is_flagged(self):
        """RDCN0203xxxx is R&D, CN0203xxxx is Manufacturing: same category,
        two divisions, which is evidence FOR duplication."""
        values = self.features("RDCN02030001", "CN02030002")
        self.assertEqual(values["same_category_other_division"], 1.0)

    def test_two_codes_in_one_division_are_not_flagged(self):
        values = self.features("RDCN02030001", "RDCN02030003")
        self.assertEqual(values["same_category_other_division"], 0.0)

    def test_an_unstructured_code_is_not_flagged(self):
        """53% of codes encode no category at all, so there is nothing to
        compare and the feature must stay at zero rather than guess."""
        values = self.features("RDCN02030001", "R&D05000")
        self.assertEqual(values["same_category_other_division"], 0.0)

    def test_the_serial_is_never_compared_as_text(self):
        """RDCN02030516 and RDCN02030517 are 92% identical as strings and are
        unrelated products. No feature may move when only the serial does."""
        a, b = self.by_code["RDCN02030001"], self.by_code["CN02030002"]
        before = feature_vector(a, b, self.corpus.df, len(self.corpus),
                                ALIASES)
        a.item_code = "RDCN02039999"      # same category, new serial
        after = feature_vector(a, b, self.corpus.df, len(self.corpus),
                               ALIASES)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
