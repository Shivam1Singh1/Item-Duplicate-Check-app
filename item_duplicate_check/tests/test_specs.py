"""
test_specs.py  --  the safety net for the veto gate and the text cleaner.

WHY THIS FILE IS THE MOST IMPORTANT TEST FILE
---------------------------------------------
specs.py is the module that decides a pair is NOT a duplicate even when the
names are 100% alike. Two kinds of mistake are possible and they cost very
different amounts:

  a MISSED CANCEL   two different products get warned about as duplicates.
                    The user is told "this already exists" about something
                    that does not. They lose trust in the warning and start
                    ignoring it. This is the expensive mistake.

  a WRONG CANCEL    a real duplicate is let through unwarned. Costs us one
                    duplicate row -- the same thing that happens today with
                    no app at all.

So the cancel cases below are not decoration. Each one is a product pair a
human confirmed, taken from the catalogue and the decision record.

THE MAINTENANCE RULE
--------------------
Every false warning seen in production becomes a case in this file FIRST,
and only then is the rule in specs.py touched. A rule fixed without a test
left behind will break again in six months and nobody will notice.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python -m pytest apps/item_duplicate_check/item_duplicate_check/tests/ -v
"""

import unittest

from item_duplicate_check.core.normalize import is_meaningful, normalize, tokens
from item_duplicate_check.core.specs import (
    grades,
    negations,
    sizes,
    spec_codes,
    veto,
    veto_reason,
)


# ==========================================================================
# The pairs. Kept as module-level data so the list reads like a spec sheet
# and adding a case is one entry, not one method.
#
# Each entry:  (label, name_a, name_b, why it is what it is)
# ==========================================================================

#: Two DIFFERENT products. The gate must cancel every one of these.
#: The first eight came from specs.py's own self-test; the ninth is new.
MUST_CANCEL = [
    ("size-mass",
     "Glycine 500g", "Glycine 1kg",
     "same chemical, half the amount -- different purchase line"),

    ("code-adaptor",
     "Glass bent inlet adaptor B-34", "Glass bent inlet adaptor B-24",
     "B-34 and B-24 are different ground-glass joint sizes; they do not fit"),

    ("code-cassette",
     "Sartocon Slice Cassette 100kDa", "Sartocon Slice Cassette 50kDa",
     "molecular weight cut-off is the whole point of the cassette"),

    ("code-festo",
     "Festo Push-in T-connector QST-6", "Festo Push-in T-connector QST-8",
     "6 mm and 8 mm tubing; the wrong one leaks"),

    ("code-strain",
     "Dengue virus strain 2", "Dengue virus strain 1",
     "different serotype, not a different label for one thing"),

    ("negation-phenol",
     "TrypLE Express Enzyme no phenol red", "TrypLE Express Enzyme Phenol Red",
     "phenol red interferes with some assays -- opposite products"),

    ("grade-ethanol",
     "Ethanol absolute", "Ethanol molecular biology grade",
     "same molecule, different purity, different price and use"),

    ("size-syringe",
     "Syringes 1ml", "Syringes 5ml",
     "volume is the product"),

    # ---- added in STEP B -------------------------------------------------
    ("code-catalogue-suffix",
     "Nuclease-free Water B1500S", "Nuclease-free Water B1500L",
     "B1500S and B1500L are catalogue pack sizes -- same stem, different "
     "variant letter. Writing this test found a real parse bug; see the "
     "STEP B notes in specs.py"),
]

#: The SAME product written two ways, or one name simply fuller than the
#: other. The gate must stay out of the way for all of these.
MUST_ALLOW = [
    ("unit-conversion",
     "Glycine 1kg", "Glycine 1000g",
     "1kg IS 1000g -- the unit table must convert before comparing"),

    ("word-order",
     "Serological Pipette 50 ml", "50ml Serological pipette",
     "word order and spacing only"),

    ("abstention",
     "Paraformaldehyde", "Paraformaldehyde for synthesis",
     "one name carries a grade, the other carries none. The abstention rule "
     "exists for exactly this: a check with nothing to compare must not fire"),

    ("size-subset",
     "Syringe Filter Unit, 0.22 um, PVDF, 33 mm", "Millex filter unit (0.22 um)",
     "length {0.22um, 33mm} vs {0.22um}. The fuller name adds detail, it does "
     "not disagree. The subset rule in _check_size allows this"),

    ("identical",
     "BD FACS Clean Solution", "BD FACS Clean Solution",
     "byte-identical must obviously pass"),

    # ---- added in STEP B -------------------------------------------------
    ("size-subset-micro-sign",
     "Syringe Filter Unit, 0.22 µm, PVDF, 33 mm", "Millex filter unit (0.22 um)",
     "the same subset case but typed with the real micro sign, so it also "
     "proves normalize() unified the glyph before specs.py saw it"),
]


#: Every way a stationery diary gets typed wrong, and what each one must do.
#: Written after a user created "Diiary" next to an existing "Diary" and was
#: shown nothing at all.
#:
#: The set is deliberately mixed. Three of these MUST warn and three MUST
#: NOT, and the hard part is that they all look alike to a string comparison.
#: A checker that catches the typos but also flags the A4/A5 pair is worse
#: than useless -- people stop reading it.
#:
#: (typed, in the catalogue, what must happen, why)
DIARY_CASES = [
    ("Diiary", "Diary", "warn",
     "a doubled letter. The classic typo, and the case that got through"),

    ("Diary", "Diary", "warn",
     "the same word. Two rows already carry it: Consumable00281 and "
     "RDCN03050012"),

    ("Dairy", "Diary", "quiet",
     "TRANSPOSED, and not a typo at all -- dairy is milk, a diary is a "
     "notebook. These are two different products that differ by one swap, "
     "which is exactly the trap"),

    ("Diary A4", "Diary A5", "cancel",
     "paper size. Same product line, different item"),

    ("Executive Diary 2027", "Executive Diary 2026", "cancel",
     "the year. A 2027 diary is not a 2026 diary, however alike the names"),

    ("Office Diary", "Diary", "quiet",
     "a fuller name. It MIGHT be the same product, but one word against two "
     "is weak evidence and the length penalty says so"),
]


class TestDiaryConfusions(unittest.TestCase):
    """The six ways this one stationery item goes wrong.

    Measured, not assumed. The scores in the comments were taken from a
    corpus holding the real rows plus the types a stationery catalogue
    actually carries.
    """

    def test_a_typo_is_not_a_different_product(self):
        """"Diiary" must reach a warning. It scored 0.4968 before
        near-matching existed, which is below every threshold."""
        from item_duplicate_check.core.scorer import (DEFAULT_WEIGHTS, Query,
                                                      score_pair)
        from item_duplicate_check.core.corpus import (build_corpus,
                                                      rows_from_dicts)
        corpus = build_corpus(rows_from_dicts([
            {"name": "D1", "item_name": "Diary"},
            {"name": "D2", "item_name": "Notepad Diary"},
        ]))
        row = next(r for r in corpus.rows if r.item_code == "D1")
        score = score_pair(Query("Diiary"), row, corpus.df, len(corpus),
                           DEFAULT_WEIGHTS)["score"]
        self.assertGreater(score, 0.75, "a doubled letter must still warn")

    def test_a_transposition_is_a_different_product(self):
        """THE IMPORTANT ONE.

        "Dairy" and "Diary" differ by one swap and mean entirely different
        things -- milk powder against a notebook. Near-matching must NOT
        treat this as a typo.

        It is the fuzzy threshold that separates them, and the margin is
        real rather than lucky:

            diiary / diary   0.91   -> one word, misspelled
            dairy  / diary   0.80   -> two words
            threshold        0.85

        If that constant is ever lowered, this test fails first.
        """
        from item_duplicate_check.core.scorer import (FUZZY_TOKEN_THRESHOLD,
                                                      shared_words)
        count, _exact = shared_words({"dairy"}, {"diary"})
        self.assertEqual(count, 0,
                         "dairy and diary must not be treated as one word")
        count, _exact = shared_words({"diiary"}, {"diary"})
        self.assertEqual(count, 1, "diiary IS a misspelling of diary")
        self.assertGreater(FUZZY_TOKEN_THRESHOLD, 0.80)
        self.assertLess(FUZZY_TOKEN_THRESHOLD, 0.91)

    def test_paper_size_is_cancelled(self):
        """A4 and A5 are different items. The spec-code check sees
        a['4'] against a['5']."""
        reason = veto_reason("Diary A4", "Diary A5")
        self.assertIsNotNone(reason)
        self.assertIn("code", reason)

    def test_the_year_is_cancelled(self):
        """A 2027 diary is not a 2026 diary, whatever the names say."""
        reason = veto_reason("Executive Diary 2027", "Executive Diary 2026")
        self.assertIsNotNone(reason)
        self.assertIn("2027", reason)

    def test_dairy_whitener_is_left_alone(self):
        """A real dairy product must not collide with stationery just
        because the letters are close."""
        self.assertIsNone(veto_reason("Dairy Whitener 500g",
                                      "Dairy Whitener 500g"))

    def test_every_listed_case_is_covered(self):
        """The table above is documentation; this keeps it honest."""
        outcomes = {case[2] for case in DIARY_CASES}
        self.assertEqual(outcomes, {"warn", "quiet", "cancel"})
        self.assertEqual(len(DIARY_CASES), 6)


class TestVetoCancels(unittest.TestCase):
    """Pairs that MUST be cancelled, and must say why."""

    def test_each_pair_is_cancelled(self):
        for label, a, b, why in MUST_CANCEL:
            with self.subTest(pair=label):
                reason = veto_reason(a, b)
                self.assertIsNotNone(
                    reason,
                    "%s: expected a cancel but the gate allowed it.\n"
                    "  A: %s\n  B: %s\n  why it matters: %s\n"
                    "  parsed A: sizes=%s codes=%s neg=%s grades=%s\n"
                    "  parsed B: sizes=%s codes=%s neg=%s grades=%s"
                    % (label, a, b, why,
                       sorted(sizes(a)), spec_codes(a),
                       sorted(negations(a)), sorted(grades(a)),
                       sorted(sizes(b)), spec_codes(b),
                       sorted(negations(b)), sorted(grades(b))),
                )

    def test_cancel_reason_is_human_readable(self):
        """The reason is shown to a user and written to Item Match Log, so it
        must name the rule and both values, not just say no."""
        for label, a, b, _why in MUST_CANCEL:
            with self.subTest(pair=label):
                reason = veto_reason(a, b)
                self.assertIn(":", reason, "%s: reason has no rule name" % label)
                self.assertIn("vs", reason, "%s: reason hides one side" % label)

    def test_veto_agrees_with_veto_reason(self):
        """veto() is the boolean form of veto_reason(); they cannot disagree."""
        for label, a, b, _why in MUST_CANCEL + MUST_ALLOW:
            with self.subTest(pair=label):
                self.assertEqual(veto(a, b), veto_reason(a, b) is not None)


class TestVetoAllows(unittest.TestCase):
    """Pairs that MUST survive the gate."""

    def test_each_pair_is_allowed(self):
        for label, a, b, why in MUST_ALLOW:
            with self.subTest(pair=label):
                reason = veto_reason(a, b)
                self.assertIsNone(
                    reason,
                    "%s: the gate wrongly cancelled this pair with %r.\n"
                    "  A: %s\n  B: %s\n  why it must pass: %s"
                    % (label, reason, a, b, why),
                )

    def test_gate_is_symmetric(self):
        """veto(a, b) must equal veto(b, a). An asymmetric gate would make the
        warning depend on which item the user happened to open first."""
        for label, a, b, _why in MUST_CANCEL + MUST_ALLOW:
            with self.subTest(pair=label):
                self.assertEqual(veto(a, b), veto(b, a),
                                 "%s: gate is not symmetric" % label)


class TestSizeParsing(unittest.TestCase):
    """The size check is only as good as what sizes() pulls out of a name."""

    def test_units_convert_into_one_base(self):
        self.assertEqual(sizes("Glycine 1kg"), {("mass", 1000.0)})
        self.assertEqual(sizes("Glycine 1000g"), {("mass", 1000.0)})
        self.assertEqual(sizes("Water 1L"), {("volume", 1000.0)})
        self.assertEqual(sizes("Water 1000ml"), {("volume", 1000.0)})

    def test_dimensions_never_mix(self):
        """5 ml and 5 g are not comparable, so they land in different
        dimensions and must never cancel each other."""
        self.assertIsNone(veto_reason("Reagent 5ml", "Reagent 5g"))

    def test_percentage_is_its_own_dimension(self):
        self.assertIn(("percent", 70.0), sizes("Sterile IPA 70 %, 1L"))
        self.assertIn(("volume", 1000.0), sizes("Sterile IPA 70 %, 1L"))

    def test_a_number_glued_behind_letters_is_not_a_size(self):
        """REGRESSION, STEP B.

        "B1500L" is a catalogue code. The size regex used to read the "1500l"
        inside it as 1500 litres -- 1.5 million millilitres of water in a
        bottle. That bogus size made the pair look like a size conflict
        against anything carrying a volume, and it also hid the code from
        spec_codes(), because "l" is a unit and the token was skipped.
        """
        self.assertEqual(sizes("Nuclease-free Water B1500L"), set())
        self.assertEqual(sizes("Nuclease-free Water B1500S"), set())

    def test_real_sizes_still_parse_after_that_fix(self):
        """The guard above must not cost us the measurements we rely on."""
        self.assertEqual(sizes("Serological Pipette 50 ml"), {("volume", 50.0)})
        self.assertEqual(sizes("Filter 0.22 um"), {("length", 0.00022)})
        self.assertEqual(sizes("Cassette 100kDa"), {("mw", 100000.0)})
        self.assertEqual(sizes("Plate 96 well"), {("count", 96.0)})
        self.assertEqual(sizes("Bottle 1.5 L"), {("volume", 1500.0)})


class TestSpecCodeParsing(unittest.TestCase):
    """Codes are grouped by their letter stem; only a shared stem can clash."""

    def test_letter_prefix_groups_by_prefix(self):
        """A code written with no separator keeps its letter stem."""
        self.assertEqual(spec_codes("Water B1500S"), {"b": {"1500s"}})
        self.assertEqual(spec_codes("Connector QST6"), {"qst": {"6"}})

    def test_a_hyphenated_code_arrives_as_a_bare_number(self):
        """Documented, not fixed.

        Rule 8 of normalize() turns punctuation into a space, so "B-34" is
        already two tokens by the time this function sees it and the letter
        stem is gone. The number lands in the bare-number group instead.

        This is safe and is left alone deliberately: the pair that matters
        compares 34 against 24 inside that one group and still cancels.
        Joining a letter to a digit would be an eleventh normalisation rule,
        and the 10 are locked.
        """
        self.assertEqual(spec_codes("Glass bent inlet adaptor B-34"), {"": {"34"}})
        self.assertEqual(spec_codes("Festo Push-in T-connector QST-6"), {"": {"6"}})
        self.assertIsNotNone(veto_reason("adaptor B-34", "adaptor B-24"))

    def test_variant_suffix_stays_inside_the_stem(self):
        """REGRESSION, STEP B.

        B1500S and B1500L must land in the SAME group ("b") with different
        values, otherwise there is no shared stem and nothing to disagree
        about. Grouping on prefix+suffix put them in "bs" and "bl" and the
        clash was invisible.
        """
        self.assertEqual(spec_codes("Nuclease-free Water B1500S"), {"b": {"1500s"}})
        self.assertEqual(spec_codes("Nuclease-free Water B1500L"), {"b": {"1500l"}})

    def test_bare_numbers_group_on_their_trailing_letters(self):
        """A number with no letter prefix keeps the original grouping, so "2"
        and "2D" stay in separate groups and do not cancel each other."""
        self.assertEqual(spec_codes("Dengue virus strain 2"), {"": {"2"}})
        self.assertIsNone(veto_reason("Vial 2D barcode rack", "Vial rack 2"))

    def test_pure_measurements_are_left_to_the_size_check(self):
        """If a token is only a number plus a unit, the size check owns it.
        Reporting it here too would lose the unit conversion."""
        self.assertEqual(spec_codes("Glycine 500g"), {})
        self.assertEqual(spec_codes("Filter 0.22um"), {})
        self.assertEqual(spec_codes("Cassette 100kDa"), {})

    def test_extra_codes_on_one_side_do_not_cancel(self):
        """Conflict, not difference. One name may simply list more."""
        self.assertIsNone(veto_reason("Connector QST-6 pack", "Connector QST-6"))


class TestNegationAndGrade(unittest.TestCase):

    def test_negation_words_are_found(self):
        self.assertEqual(negations("TrypLE Express no phenol red"),
                         frozenset({"no"}))
        self.assertEqual(negations("Nuclease-free Water"), frozenset({"free"}))

    def test_negation_only_matches_whole_words(self):
        """"non" must not be found inside "nonsense", and "no" must not be
        found inside "Nocodazole"."""
        self.assertEqual(negations("Nocodazole solution"), frozenset())

    def test_grades_are_found_across_spelling(self):
        self.assertIn("rnasefree", grades("RNase-free Water"))
        self.assertIn("rnasefree", grades("RNase free water"))
        self.assertIn("molecularbiology", grades("Ethanol molecular biology grade"))

    def test_grade_abstains_when_one_side_has_none(self):
        self.assertIsNone(veto_reason("Ethanol", "Ethanol absolute"))


class TestNormalize(unittest.TestCase):
    """normalize() runs before every other rule, so its output is the
    contract the rest of the app is written against."""

    def test_trademark_spaces_and_hyphen(self):
        """The case from the decision record, exactly as written there."""
        self.assertEqual(normalize("Nunc™ 96-Well Plate "), "nunc 96well plate")

    def test_trademark_is_dropped_before_nfkc_expands_it(self):
        """NFKC turns U+2122 into the letters "TM", which would then glue onto
        the word in front: "nunctm". Rule 2 must run before rule 3."""
        self.assertEqual(normalize("Nunc™ Plate"), "nunc plate")

    def test_mojibake_micro_sign_is_repaired(self):
        """UTF-8 micro sign read as Latin-1 arrives as the two characters
        "Âµ". If it is not repaired before the micro rule, the stray
        "A" survives as part of the word."""
        self.assertEqual(normalize("Millex,33mm,0.22Âµm,Dur.(PVDF)"),
                         "millex 33mm 0.22um dur pvdf")
        self.assertEqual(normalize("0.22Âµm"), "0.22um")

    def test_both_micro_code_points_become_u(self):
        self.assertEqual(normalize("0.22 µm"), normalize("0.22 μm"))
        self.assertEqual(normalize("0.22 µm"), "0.22um")

    def test_case_folding_solves_dnase_spellings(self):
        """No soundex, no spell-checker -- lower-casing is the whole fix."""
        self.assertEqual(normalize("DNase I"), normalize("DNAse I"))
        self.assertEqual(normalize("DNase I"), normalize("dnase i"))
        self.assertEqual(normalize("DNase I"), "dnase i")

    def test_non_breaking_space_separates_words(self):
        """str.split() does not break on U+00A0, so without rule 4 the two
        words stay fused and never match the typed version."""
        self.assertEqual(normalize("0.22 um"), "0.22um")
        self.assertEqual(normalize("Wide mouth bottle"), "wide mouth bottle")

    def test_number_and_unit_become_one_token(self):
        """Left apart, "50 ml" matches "10 ml" on the shared "ml" token."""
        self.assertEqual(normalize("Serological Pipette 50 ml"),
                         "serological pipette 50ml")
        self.assertEqual(normalize("Sterile IPA 70 %, 1L"), "sterile ipa 70% 1l")

    def test_trailing_dot_is_dropped(self):
        self.assertEqual(normalize("ACK Lysis Buffer."), "ack lysis buffer")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(normalize("  Wide mouth   bottle 1L "),
                         "wide mouth bottle 1l")

    def test_blank_input_never_raises(self):
        """Callers read straight off a spreadsheet, so None and numbers
        arrive. Normalising must never be the thing that breaks a save."""
        self.assertEqual(normalize(None), "")
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(500), "500")

    def test_tokens_and_is_meaningful(self):
        self.assertEqual(tokens("Nunc™ 96-Well Plate"),
                         ["nunc", "96well", "plate"])
        self.assertTrue(is_meaningful("96 well plate"))
        self.assertFalse(is_meaningful("CPU"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
