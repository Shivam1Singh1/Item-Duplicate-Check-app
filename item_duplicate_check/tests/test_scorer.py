"""
test_scorer.py  --  the weighted sum, its components, and the search.

The test that matters most here is
TestEarlyExit.test_early_exit_returns_exactly_the_brute_force_top_five.
search() skips the expensive component for candidates that cannot reach the
top five. That is a real speed-up and a real opportunity to silently return
the wrong answer, so it is checked against an exhaustive score of every row.

RUN
---
    cd ~/frappe-bench/frappe-bench
    PYTHONPATH=apps/item_duplicate_check env/bin/python -m unittest \\
        item_duplicate_check.tests.test_scorer -v
"""

import unittest

from item_duplicate_check.core.corpus import build_corpus, rows_from_dicts
from item_duplicate_check.core.makes import canonical, normalize_makes
from item_duplicate_check.core.normalize import tokens
from item_duplicate_check.core.specs import veto_reason
from item_duplicate_check.core.scorer import (
    shared_words,
    CAT_NO_SCORE,
    DEFAULT_WEIGHTS,
    Query,
    band_for,
    build_reason,
    makes_agreement,
    ordered_similarity,
    rare_word_overlap,
    score_pair,
    search,
    shared_size_phrase,
    word_set_similarity,
)

#: The alias table the tests use. Hard-coded rather than read from
#: config/makes_alias.json so a regenerated file cannot break the tests, and
#: so what is being asserted is visible here.
ALIASES = {
    "thermo": "thermo",
    "thermo scientific": "thermo",
    "thermo fisher scientific": "thermo",
    "thermofisher": "thermo",
    "gibco thermo": "thermo",
    "sigma": "sigma",
    "sigma aldrich": "sigma",
    "tarson": "tarsons",
    "tarsons": "tarsons",
}


def catalogue():
    """A catalogue with one real duplicate, two look-alikes and some noise."""
    return build_corpus(rows_from_dicts([
        {"name": "I1", "item_name": "Serological Pipette 50 ml",
         "cat_no": "167008", "makes": "Thermo Scientific"},
        {"name": "I2", "item_name": "Glycine 500g", "makes": "Sigma-Aldrich"},
        {"name": "I3", "item_name": "Glycine 1kg", "makes": "Sigma"},
        {"name": "I4", "item_name": "Nunc 96-Well Plate", "makes": "Thermo"},
        {"name": "I5", "item_name": "Axygen PCR Plate 96 well",
         "makes": "Corning"},
        {"name": "I6", "item_name": "Plate sealing film"},
        {"name": "I7", "item_name": "DNase I, RNase-free (1 U/uL)",
         "makes": "Thermo"},
        {"name": "I8", "item_name": "Tarson Serological Pipette 50ml",
         "makes": "Tarson"},
        {"name": "I9", "item_name": "Ethanol absolute", "makes": "Merck"},
        {"name": "I10", "item_name": "Ethanol molecular biology grade",
         "makes": "Merck"},
    ]))


class TestOrderedSimilarity(unittest.TestCase):

    def test_word_order_does_not_matter(self):
        a = tokens("Serological Pipette 50 ml")
        b = tokens("50ml Serological pipette")
        self.assertEqual(ordered_similarity(a, b), 1.0)

    def test_a_typo_inside_a_word_still_scores_high(self):
        """Sorting the words must not throw away character-level similarity."""
        score = ordered_similarity(tokens("Serological Pipette"),
                                   tokens("Serologcal Pipette"))
        self.assertGreater(score, 0.9)
        self.assertLess(score, 1.0)

    def test_unrelated_names_score_low(self):
        self.assertLess(ordered_similarity(tokens("Glycine 500g"),
                                           tokens("Nunc 96 Well Plate")), 0.4)

    def test_symmetric(self):
        a, b = tokens("Nunc 96 Well Plate"), tokens("96 well plate")
        self.assertEqual(ordered_similarity(a, b), ordered_similarity(b, a))

    def test_empty_input_scores_zero_not_one(self):
        self.assertEqual(ordered_similarity([], tokens("Plate")), 0.0)
        self.assertEqual(ordered_similarity([], []), 0.0)


class TestWordSetAndLengthPenalty(unittest.TestCase):
    """The three cases from the decision record that the penalty exists for."""

    SUBSET_CASES = [
        ("DNase I, RNase-free (1 U/uL)", "DNAse"),
        ("PmeI, recombinant", "PmeI"),
        ("Plasmid DNA - qwp", "Plasmid DNA"),
    ]

    def test_set_overlap_alone_is_a_perfect_score_on_a_subset(self):
        """Which is exactly why it cannot be used on its own."""
        for long_name, short_name in self.SUBSET_CASES:
            with self.subTest(pair=short_name):
                overlap, _penalty = word_set_similarity(tokens(long_name),
                                                        tokens(short_name))
                self.assertEqual(overlap, 1.0)

    def test_the_length_penalty_pulls_those_cases_down(self):
        for long_name, short_name in self.SUBSET_CASES:
            with self.subTest(pair=short_name):
                overlap, penalty = word_set_similarity(tokens(long_name),
                                                       tokens(short_name))
                self.assertLess(overlap * penalty, 0.7,
                                "%r vs %r was not penalised"
                                % (long_name, short_name))

    def test_identical_names_are_not_penalised(self):
        overlap, penalty = word_set_similarity(tokens("Nunc 96 Well Plate"),
                                               tokens("nunc 96well plate"))
        self.assertEqual(overlap, 1.0)
        self.assertEqual(penalty, 1.0)

    def test_no_shared_words_scores_zero(self):
        overlap, _ = word_set_similarity(tokens("Glycine"), tokens("Plate"))
        self.assertEqual(overlap, 0.0)

    def test_empty_side_scores_zero(self):
        self.assertEqual(word_set_similarity([], tokens("Plate")), (0.0, 0.0))


class TestTypoInAShortName(unittest.TestCase):
    """REGRESSION. A user created "Diiary" beside an existing "Diary" and
    was shown nothing at all.

    Exact token matching treats a typo as a different word. For a ONE-WORD
    name that zeroes two of the four components, and the pair cannot exceed

        0.47 / (0.47 + 0.21 + 0.18) = 0.547

    which is under the 0.75 soft threshold. It was unwarnable by
    construction, whatever the characters said. 768 catalogue items (3.5%)
    have a one-word name.
    """

    def setUp(self):
        self.corpus = build_corpus(rows_from_dicts([
            {"name": "D1", "item_name": "Diary"},
            {"name": "D2", "item_name": "Dryer"},
            {"name": "D3", "item_name": "Notepad Diary"},
            {"name": "S1", "item_name": "Syringes 1ml"},
            {"name": "S2", "item_name": "Syringes 5ml"},
            {"name": "B1", "item_name": "Nuclease-free Water B1500S"},
            {"name": "B2", "item_name": "Nuclease-free Water B1500L"},
        ]))

    def score(self, typed, code):
        row = next(r for r in self.corpus.rows if r.item_code == code)
        return score_pair(Query(typed), row, self.corpus.df,
                          len(self.corpus), DEFAULT_WEIGHTS, ALIASES)["score"]

    def test_the_typo_now_scores_above_the_soft_threshold(self):
        self.assertGreater(self.score("Diiary", "D1"), 0.75)

    def test_the_whole_search_surfaces_it(self):
        out = search(self.corpus, Query("Diiary"), aliases=ALIASES)
        self.assertIn("D1", [m.item_code for m in out["matches"]])
        self.assertIn(out["band"], ("soft", "strong"))

    def test_a_different_short_word_is_not_pulled_in(self):
        """"Dryer" is not a misspelling of "Diary" and must stay out."""
        self.assertLess(self.score("Diiary", "D2"), 0.75)

    def test_near_matching_does_not_break_the_size_look_alikes(self):
        """The whole point of the veto gate is these pairs. Near-matching
        must not quietly make "1ml" and "5ml" the same word."""
        overlap, _penalty = word_set_similarity(tokens("Syringes 1ml"),
                                                tokens("Syringes 5ml"))
        self.assertEqual(overlap, 0.5)          # only "syringes" is shared
        self.assertIsNotNone(veto_reason("Syringes 1ml", "Syringes 5ml"))

    def test_near_matching_does_not_merge_catalogue_variants(self):
        """b1500s and b1500l are 0.83 alike -- under the 0.85 threshold on
        purpose, because they are different pack sizes."""
        count, _exact = shared_words({"b1500s"}, {"b1500l"})
        self.assertEqual(count, 0)

    def test_it_is_off_for_long_names(self):
        """With five words matching exactly, a typo in the sixth moves the
        score by a rounding error -- and running it on every pair took the
        worst case through the latency budget."""
        long_a = tokens("alpha beta gamma delta epsilon zeta")
        long_b = tokens("alpha beta gamma delta epsilon zetaa")
        count, exact = shared_words(set(long_a), set(long_b))
        self.assertEqual(count, len(exact))     # no near-match credited

    def test_it_is_off_when_the_lengths_are_far_apart(self):
        """A typo adds a character or two. It does not turn a six-character
        name into a forty-character one, and pretending it might is what
        destroyed the early exit."""
        count, exact = shared_words(
            {"diiary"}, {"chromatography"})
        self.assertEqual(count, len(exact))


class TestRareWordOverlap(unittest.TestCase):

    DF = {"plate": 900, "well": 800, "nunc": 3, "axygen": 5}
    N = 22204

    def test_sharing_a_rare_word_beats_sharing_a_common_one(self):
        rare = rare_word_overlap(["nunc", "plate"], ["nunc", "tips"],
                                 self.DF, self.N)
        common = rare_word_overlap(["nunc", "plate"], ["axygen", "plate"],
                                   self.DF, self.N)
        self.assertGreater(rare, common)

    def test_identical_token_sets_score_one(self):
        self.assertEqual(
            rare_word_overlap(["nunc", "plate"], ["plate", "nunc"],
                              self.DF, self.N), 1.0)

    def test_nothing_shared_scores_zero(self):
        self.assertEqual(
            rare_word_overlap(["nunc"], ["axygen"], self.DF, self.N), 0.0)

    def test_an_unseen_word_is_treated_as_the_rarest_possible(self):
        """A brand new spelling is a signal, not a problem."""
        score = rare_word_overlap(["qwertyol"], ["qwertyol"], self.DF, self.N)
        self.assertEqual(score, 1.0)

    def test_symmetric(self):
        a, b = ["nunc", "plate"], ["nunc", "well", "axygen"]
        self.assertEqual(rare_word_overlap(a, b, self.DF, self.N),
                         rare_word_overlap(b, a, self.DF, self.N))


class TestMakesAgreement(unittest.TestCase):

    def test_alias_table_collapses_spellings(self):
        self.assertEqual(canonical("Thermo Fisher Scientific", ALIASES),
                         "thermo")
        # the real spelling in the catalogue carries the symbol, which
        # normalize_makes() strips before the lookup -- 112 items
        self.assertEqual(canonical("Thermo Scientific™", ALIASES),
                         "thermo")
        self.assertEqual(makes_agreement("Thermo", "Thermo Scientific",
                                         ALIASES), 1.0)

    def test_gibco_thermo_goes_to_thermo_but_plain_gibco_does_not(self):
        self.assertEqual(canonical("Gibco/Thermo", ALIASES), "thermo")
        self.assertEqual(canonical("Gibco", ALIASES), "gibco")

    def test_different_manufacturers_disagree(self):
        self.assertEqual(makes_agreement("Thermo", "Sigma", ALIASES), 0.0)

    def test_a_blank_or_junk_value_abstains(self):
        """None, not 0.0. Abstain means no opinion, never no."""
        for blank in (None, "", "   ", "0", "na", "NA", "-", "unknown"):
            self.assertIsNone(makes_agreement("Thermo", blank, ALIASES),
                              "%r should abstain, not disagree" % blank)
            self.assertIsNone(makes_agreement(blank, "Thermo", ALIASES))

    def test_an_unmapped_spelling_is_its_own_name(self):
        self.assertEqual(canonical("Some New Vendor", ALIASES),
                         "some new vendor")
        self.assertEqual(makes_agreement("Some New Vendor", "some new vendor",
                                         ALIASES), 1.0)

    def test_normalise_strips_decoration_and_punctuation(self):
        self.assertEqual(normalize_makes("Thermo Scientific™"),
                         "thermo scientific")
        self.assertEqual(normalize_makes("Sigma-Aldrich"), "sigma aldrich")


class TestScorePair(unittest.TestCase):

    def setUp(self):
        self.corpus = catalogue()

    def _row(self, code):
        return next(r for r in self.corpus.rows if r.item_code == code)

    def test_identical_name_and_manufacturer_scores_one(self):
        query = Query("Serological Pipette 50 ml", makes="Thermo Scientific")
        out = score_pair(query, self._row("I1"), self.corpus.df,
                         len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        self.assertEqual(out["score"], 1.0)

    def test_a_blank_makes_can_still_reach_a_perfect_score(self):
        """THE RENORMALISATION.

        Without it the makes weight (0.14) would be lost from the numerator
        but kept in the denominator, capping every item that has no
        manufacturer at 0.86 -- below the 0.92 strong threshold. 29% of the
        catalogue would then never be able to raise a strong warning.
        """
        query = Query("Plate sealing film", makes=None)
        out = score_pair(query, self._row("I6"), self.corpus.df,
                         len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        self.assertIsNone(out["makes"])
        self.assertEqual(out["score"], 1.0)

    def test_a_disagreeing_manufacturer_costs_the_full_weight(self):
        query = Query("Serological Pipette 50 ml", makes="Tarson")
        out = score_pair(query, self._row("I1"), self.corpus.df,
                         len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        self.assertEqual(out["makes"], 0.0)
        self.assertAlmostEqual(out["score"], 1.0 - 0.14, places=6)

    def test_the_veto_gate_forces_zero_and_reports_why(self):
        query = Query("Glycine 500g", makes="Sigma")
        out = score_pair(query, self._row("I3"), self.corpus.df,
                         len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        self.assertEqual(out["score"], 0.0)
        self.assertIn("size", out["cancel_reason"])
        # The pre-veto score is kept, because a high one is a warning sign.
        # 0.66 here: the names agree on "glycine" and the manufacturer
        # agrees, while 500g and 1kg are different tokens.
        self.assertGreater(out["raw_score"], 0.6)

    def test_the_gate_can_be_switched_off_for_diagnosis(self):
        query = Query("Glycine 500g", makes="Sigma")
        out = score_pair(query, self._row("I3"), self.corpus.df,
                         len(self.corpus), DEFAULT_WEIGHTS, ALIASES,
                         apply_veto=False)
        self.assertIsNone(out["cancel_reason"])
        self.assertGreater(out["score"], 0.6)

    def test_weights_come_from_the_caller(self):
        """Nothing in the formula may be hard-coded, or the silent run cannot
        retune it without a deploy."""
        query = Query("Nunc 96 Well Plate", makes="Sigma")
        only_makes = {"ordered": 0.0, "word_set": 0.0, "rare": 0.0,
                      "makes": 1.0}
        out = score_pair(query, self._row("I4"), self.corpus.df,
                         len(self.corpus), only_makes, ALIASES)
        self.assertEqual(out["score"], 0.0)       # Sigma vs Thermo


class TestReasonString(unittest.TestCase):

    def setUp(self):
        self.corpus = catalogue()

    def _row(self, code):
        return next(r for r in self.corpus.rows if r.item_code == code)

    def test_reason_reads_like_the_decision_record_example(self):
        query = Query("Serological Pipette 50 ml", makes="Thermo")
        components = score_pair(query, self._row("I1"), self.corpus.df,
                                len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        reason = build_reason(components, query, self._row("I1"), ALIASES)
        self.assertIn("names 100% alike", reason)
        self.assertIn("same manufacturer (thermo)", reason)
        self.assertIn("both 50 mL", reason)

    def test_shared_size_is_formatted_in_readable_units(self):
        self.assertEqual(shared_size_phrase("Pipette 50 ml", "50ml pipette"),
                         "both 50 mL")
        self.assertEqual(shared_size_phrase("Bottle 1L", "1000 ml bottle"),
                         "both 1 L")
        self.assertEqual(shared_size_phrase("Glycine 500g", "Glycine 500 g"),
                         "both 500 g")

    def test_no_shared_size_says_nothing_about_size(self):
        self.assertEqual(shared_size_phrase("Glycine 500g", "Glycine 1kg"), "")

    def test_a_different_manufacturer_is_stated_plainly(self):
        query = Query("Serological Pipette 50 ml", makes="Tarson")
        components = score_pair(query, self._row("I1"), self.corpus.df,
                                len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        reason = build_reason(components, query, self._row("I1"), ALIASES)
        self.assertIn("different manufacturer", reason)

    def test_a_blank_manufacturer_is_not_mentioned_at_all(self):
        query = Query("Plate sealing film", makes=None)
        components = score_pair(query, self._row("I6"), self.corpus.df,
                                len(self.corpus), DEFAULT_WEIGHTS, ALIASES)
        reason = build_reason(components, query, self._row("I6"), ALIASES)
        self.assertNotIn("manufacturer", reason)


class TestSearch(unittest.TestCase):

    def setUp(self):
        self.corpus = catalogue()

    def test_an_exact_cat_no_short_circuits_to_point_nine_seven(self):
        query = Query("Something completely different", cat_no="167008")
        out = search(self.corpus, query, aliases=ALIASES)
        top = out["matches"][0]
        self.assertEqual(top.item_code, "I1")
        self.assertEqual(top.score, CAT_NO_SCORE)
        self.assertEqual(top.via, "cat_no")
        self.assertIn("same Cat No", top.reason)

    def test_the_gate_still_applies_on_top_of_a_cat_no_match(self):
        """The Tyvek case: same Cat No, 38 cm vs 40 cm, different products."""
        corpus = build_corpus(rows_from_dicts([
            {"name": "T1", "item_name": "Tyvek roll Flat reels-38 cm x 200 mtr",
             "cat_no": "SUFR38200"},
        ]))
        query = Query("Tyvek roll Flat reels- 40cm x 200 mtr",
                      cat_no="SUFR38200")
        out = search(corpus, query, aliases=ALIASES)
        self.assertEqual(out["matches"], [])
        self.assertEqual(len(out["cancelled"]), 1)
        self.assertIn("size", out["cancelled"][0].cancel_reason)

    def test_junk_cat_no_does_not_trigger_step_zero(self):
        query = Query("Glycine 500g", cat_no="0")
        out = search(self.corpus, query, aliases=ALIASES)
        self.assertTrue(all(m.via == "name" for m in out["matches"]))

    def test_a_real_duplicate_is_found_by_name_alone(self):
        query = Query("50 ml Serological Pipette", makes="Thermo")
        out = search(self.corpus, query, aliases=ALIASES)
        self.assertEqual(out["matches"][0].item_code, "I1")
        self.assertGreaterEqual(out["matches"][0].score, 0.92)
        self.assertEqual(out["band"], "strong")

    def test_look_alikes_are_cancelled_not_returned(self):
        query = Query("Glycine 500g", makes="Sigma")
        out = search(self.corpus, query, aliases=ALIASES)
        cancelled = {m.item_code for m in out["cancelled"]}
        self.assertIn("I3", cancelled)              # Glycine 1kg
        self.assertNotIn("I3", {m.item_code for m in out["matches"]})

    def test_at_most_five_matches_come_back(self):
        query = Query("Plate")
        out = search(self.corpus, query, aliases=ALIASES)
        self.assertLessEqual(len(out["matches"]), 5)

    def test_the_item_being_edited_never_matches_itself(self):
        query = Query("Serological Pipette 50 ml", cat_no="167008",
                      item_code="I1")
        out = search(self.corpus, query, aliases=ALIASES)
        self.assertNotIn("I1", {m.item_code for m in out["matches"]})

    def test_thresholds_decide_the_band(self):
        """The same pair must land in a different band when the thresholds
        move, because the silent run is expected to move them."""
        # A near-match, not an identical one, so there is room either side.
        query = Query("Axygen PCR Plate", makes="Corning")
        score = search(self.corpus, query, aliases=ALIASES)["matches"][0].score
        self.assertLess(score, 1.0)

        strict = search(self.corpus, query, strong=score + 0.01,
                        soft=score - 0.01, aliases=ALIASES)
        self.assertEqual(strict["band"], "soft")

        loose = search(self.corpus, query, strong=score - 0.01,
                       soft=score - 0.02, aliases=ALIASES)
        self.assertEqual(loose["band"], "strong")

        silent = search(self.corpus, query, strong=0.999, soft=0.998,
                        aliases=ALIASES)
        self.assertEqual(silent["band"], "none")

    def test_band_for_boundaries_are_inclusive(self):
        self.assertEqual(band_for(0.92, 0.92, 0.75), "strong")
        self.assertEqual(band_for(0.75, 0.92, 0.75), "soft")
        self.assertEqual(band_for(0.7499, 0.92, 0.75), "none")

    def test_an_empty_query_returns_nothing_and_does_not_raise(self):
        out = search(self.corpus, Query(""), aliases=ALIASES)
        self.assertEqual(out["band"], "none")

    def test_match_as_dict_is_json_safe(self):
        query = Query("50 ml Serological Pipette", makes="Thermo")
        out = search(self.corpus, query, aliases=ALIASES)
        payload = out["matches"][0].as_dict()
        self.assertEqual(payload["item"], "I1")
        self.assertIsInstance(payload["score"], float)
        self.assertIsInstance(payload["reason"], str)
        self.assertIn("components", payload)


class TestEarlyExit(unittest.TestCase):
    """search() skips the expensive component for candidates that cannot
    reach the top five. If that bound is ever wrong, the wrong answer comes
    back silently -- so it is checked against an exhaustive score."""

    QUERIES = [
        Query("50 ml Serological Pipette", makes="Thermo"),
        Query("Nunc 96 Well Plate", makes="Thermo"),
        Query("Plate"),                                   # all common words
        Query("Ethanol"),                                 # grade abstention
        Query("Glycine 1000 g", makes="Sigma"),
        Query("Something nobody has ever typed"),
    ]

    def setUp(self):
        self.corpus = catalogue()

    def test_early_exit_returns_exactly_the_brute_force_top_five(self):
        """Brute force runs over the SAME shortlist the index produced.

        That isolation is the point. Comparing against every row in the
        catalogue would measure two things at once -- the bound and the
        index's own recall -- and the first version of this test did exactly
        that, then reported a bound error that was really just the index
        correctly declining to shortlist items scoring 0.1.
        """
        for query in self.QUERIES:
            with self.subTest(query=query.name):
                fast = search(self.corpus, query, aliases=ALIASES)
                fast_top = [(m.item_code, round(m.score, 6))
                            for m in fast["matches"]]

                shortlist = self.corpus.candidates(
                    query.name, exclude_item_code=query.item_code)
                brute = []
                for row in shortlist:
                    out = score_pair(query, row, self.corpus.df,
                                     len(self.corpus), DEFAULT_WEIGHTS,
                                     ALIASES)
                    if not out["cancel_reason"]:
                        brute.append((out["score"], row.disabled,
                                      row.item_code))
                brute.sort(key=lambda t: (-t[0], t[1], t[2]))
                brute_top = [(code, round(score, 6))
                             for score, _disabled, code in brute[:5]]

                self.assertEqual(fast_top, brute_top,
                                 "early exit disagreed with brute force for "
                                 "%r" % query.name)

    def test_the_shortlist_is_what_limits_recall_not_the_bound(self):
        """The companion to the test above, stated explicitly so nobody
        mistakes it for a bug later: candidates the index does not shortlist
        are genuinely absent from the result, and they are the low scorers.
        Measured recall@5 for this trade-off is 89.7%."""
        query = Query("Glycine 1000 g", makes="Sigma")
        shortlisted = {r.item_code for r in self.corpus.candidates(query.name)}
        self.assertNotIn("I4", shortlisted)             # Nunc 96-Well Plate
        out = search(self.corpus, query, aliases=ALIASES)
        self.assertNotIn("I4", {m.item_code for m in out["matches"]})


class TestTotalSimilar(unittest.TestCase):
    """`total_similar` is how many exist, `matches` is how many are shown.

    The warning dialog listed five rows and said nothing about the total, so
    a name the catalogue holds 79 copies of read as though it had five. The
    count has to survive the top-N truncation AND the early exit, which used
    to stop walking the moment the fifth-best was settled -- that is the part
    most likely to break again, so it is tested with more duplicates than the
    limit.
    """

    #: Twelve rows of one name, which is the shape of the real case: 79 CAPEX
    #: rows all called "electrical installation at mrna lab".
    def setUp(self):
        self.corpus = build_corpus(rows_from_dicts(
            [{"name": "E%d" % i, "item_name": "electrical installation at MRNA lab"}
             for i in range(1, 13)]
            + [{"name": "X1", "item_name": "Glycine 500g", "makes": "Sigma"}]
        ))

    def test_it_counts_past_the_top_five(self):
        out = search(self.corpus, Query("electrical installation at MRNA labb"),
                     aliases=ALIASES)
        self.assertEqual(len(out["matches"]), 5)
        self.assertEqual(out["total_similar"], 12)

    def test_it_counts_only_the_soft_band_and_above(self):
        """The unrelated row is in the catalogue and must not be counted."""
        out = search(self.corpus, Query("Glycine 500 g", makes="Sigma"),
                     aliases=ALIASES)
        self.assertEqual(out["total_similar"], 1)

    def test_nothing_similar_counts_zero(self):
        out = search(self.corpus, Query("Something nobody has ever typed"),
                     aliases=ALIASES)
        self.assertEqual(out["total_similar"], 0)

    def test_the_count_never_undercounts_what_is_shown(self):
        """The dialog reads 'N exist, closest 5' -- N < 5 would be nonsense."""
        for name in ("electrical installation at MRNA lab", "Glycine 500g",
                     "Plate"):
            with self.subTest(name=name):
                out = search(self.corpus, Query(name), aliases=ALIASES)
                shown = [m for m in out["matches"] if m.band != "none"]
                self.assertGreaterEqual(out["total_similar"], len(shown))


if __name__ == "__main__":
    unittest.main(verbosity=2)
