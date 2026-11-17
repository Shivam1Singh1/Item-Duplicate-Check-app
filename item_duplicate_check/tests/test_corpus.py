"""
test_corpus.py  --  the rare-word index, tested without a site.

Everything here runs against a small hand-made catalogue, which is the point
of keeping corpus.py's pure half separate from its Frappe half: the index
logic is testable in milliseconds, and the live measurements in
scripts/index_stats.py then confirm the same code behaves on 22,204 real rows.

RUN
---
    cd ~/frappe-bench/frappe-bench
    PYTHONPATH=apps/item_duplicate_check env/bin/python -m unittest \\
        item_duplicate_check.tests.test_corpus -v
"""

import unittest

from item_duplicate_check.core.corpus import (
    CAT_NO_JUNK,
    Corpus,
    ItemRow,
    build_corpus,
    clean_cat_no,
    df_ceiling,
    document_frequency,
    rare_tokens,
    rows_from_dicts,
    usable_tokens,
)


def tiny_catalogue():
    """A catalogue small enough to reason about by hand.

    "plate" is deliberately the most common word and "nunc" the rarest, so
    the selectivity rules have something real to separate.
    """
    return rows_from_dicts([
        {"name": "A1", "item_name": "Nunc 96-Well Plate", "cat_no": "167008",
         "makes": "Thermo"},
        {"name": "A2", "item_name": "96 Well Plate sterile", "cat_no": "0"},
        {"name": "A3", "item_name": "384 Well Plate", "cat_no": "na"},
        {"name": "A4", "item_name": "Axygen PCR Plate", "cat_no": "PCR-96-FS-C"},
        {"name": "A5", "item_name": "Serological Pipette 50 ml",
         "cat_no": "167008", "makes": "Thermo Scientific"},
        {"name": "A6", "item_name": "Glycine 500g", "cat_no": "G8898",
         "disabled": 1},
        {"name": "A7", "item_name": "Plate sealing film", "cat_no": "-"},
        {"name": "A8", "item_name": "CPU", "cat_no": None, "is_stock_item": 0},
    ])


class TestCleanCatNo(unittest.TestCase):
    """Cat No is the Step 0 key, so cleaning it wrong is expensive in both
    directions: junk left in invents duplicate groups, over-cleaning loses
    real ones."""

    def test_separators_are_removed_so_spellings_meet(self):
        self.assertEqual(clean_cat_no("B-1500S"), "b1500s")
        self.assertEqual(clean_cat_no("b 1500 s"), "b1500s")
        self.assertEqual(clean_cat_no("B1500S"), "b1500s")
        self.assertEqual(clean_cat_no(" b1500s "), "b1500s")

    def test_known_junk_values_become_empty(self):
        for junk in ("0", "na", "n/a", "N.A.", "-", "--", " ", "", "NIL",
                     "none", "custom", "not available"):
            self.assertEqual(clean_cat_no(junk), "",
                             "%r should have been treated as junk" % junk)

    def test_every_listed_junk_value_really_is_rejected(self):
        """The list and the cleaner must not drift apart."""
        for junk in CAT_NO_JUNK:
            self.assertEqual(clean_cat_no(junk), "",
                             "%r is in CAT_NO_JUNK but survived cleaning" % junk)

    def test_all_zero_values_in_any_spelling_are_junk(self):
        self.assertEqual(clean_cat_no("0-0-0"), "")
        self.assertEqual(clean_cat_no("00000"), "")

    def test_too_short_to_identify_anything(self):
        self.assertEqual(clean_cat_no("A1"), "")
        self.assertEqual(clean_cat_no("x"), "")

    def test_real_catalogue_numbers_survive(self):
        self.assertEqual(clean_cat_no("167008"), "167008")
        self.assertEqual(clean_cat_no("PCR-96-FS-C"), "pcr96fsc")
        self.assertEqual(clean_cat_no("G8898"), "g8898")

    def test_none_and_numbers_do_not_raise(self):
        self.assertEqual(clean_cat_no(None), "")
        self.assertEqual(clean_cat_no(167008), "167008")


class TestDocumentFrequency(unittest.TestCase):

    def test_a_word_counts_once_per_item(self):
        """We want "how many items can this word point at", not how many
        times it was typed, so a repeat inside one name must not inflate it."""
        df = document_frequency([["plate", "plate", "nunc"], ["plate"]])
        self.assertEqual(df["plate"], 2)
        self.assertEqual(df["nunc"], 1)

    def test_ceiling_scales_with_the_catalogue_but_has_a_floor(self):
        self.assertEqual(df_ceiling(22204), 222)
        self.assertEqual(df_ceiling(8), 25)        # floor, not 0

    def test_common_words_are_not_usable_as_keys(self):
        df = {"plate": 500, "sterile": 400, "nunc": 3}
        self.assertEqual(usable_tokens(["nunc", "plate", "sterile"], df, 222),
                         ["nunc"])

    def test_an_unknown_word_is_treated_as_rare(self):
        """A brand new spelling is exactly the signal we want to keep."""
        self.assertEqual(usable_tokens(["qwertyol"], {"plate": 500}, 222),
                         ["qwertyol"])

    def test_rare_tokens_are_rarest_first_and_deterministic(self):
        df = {"a": 10, "b": 2, "c": 2, "d": 1}
        self.assertEqual(rare_tokens(["a", "b", "c", "d"], df, 222, limit=3),
                         ("d", "b", "c"))       # ties broken alphabetically

    def test_rare_tokens_drops_duplicates_within_a_name(self):
        df = {"plate": 2, "well": 3}
        self.assertEqual(rare_tokens(["plate", "plate", "well"], df, 222),
                         ("plate", "well"))


class TestBuildCorpus(unittest.TestCase):

    def setUp(self):
        self.corpus = build_corpus(tiny_catalogue())

    def test_every_item_is_indexed(self):
        self.assertEqual(len(self.corpus), 8)
        self.assertIsInstance(self.corpus, Corpus)

    def test_enabled_items_rank_before_disabled_ones(self):
        """A disabled duplicate is still an answer, but it must not outrank a
        live item. Sorting once here means no caller has to remember to."""
        disabled_flags = [r.disabled for r in self.corpus.rows]
        self.assertEqual(disabled_flags, sorted(disabled_flags))
        self.assertEqual(self.corpus.rows[-1].item_code, "A6")

    def test_disabled_items_are_kept_not_dropped(self):
        self.assertIn("A6", [r.item_code for r in self.corpus.rows])

    def test_rare_words_are_stored_per_item(self):
        a1 = self._row("A1")
        self.assertIn("nunc", a1.rare)
        self.assertLessEqual(len(a1.rare), 4)

    def test_inverted_index_points_at_the_right_items(self):
        self.assertEqual([r.item_code for r in self.corpus.inverted["nunc"]],
                         ["A1"])
        self.assertEqual([r.item_code for r in self.corpus.inverted["axygen"]],
                         ["A4"])

    def test_cat_no_map_groups_shared_numbers(self):
        """A1 and A5 share Cat No 167008 -- that is the Step 0 group."""
        self.assertEqual(
            sorted(r.item_code for r in self.corpus.lookup_cat_no("167008")),
            ["A1", "A5"])

    def test_cat_no_map_ignores_junk_rows(self):
        """A2 ("0"), A3 ("na"), A7 ("-") and A8 (None) must not form a group."""
        self.assertEqual(self.corpus.lookup_cat_no("0"), [])
        self.assertEqual(self.corpus.lookup_cat_no("na"), [])
        self.assertEqual(self.corpus.lookup_cat_no("-"), [])
        self.assertEqual(self.corpus.lookup_cat_no(None), [])
        indexed = set()
        for rows in self.corpus.cat_no_map.values():
            indexed.update(r.item_code for r in rows)
        self.assertNotIn("A2", indexed)
        self.assertNotIn("A3", indexed)
        self.assertNotIn("A7", indexed)

    def test_cat_no_lookup_is_separator_insensitive(self):
        self.assertEqual(
            [r.item_code for r in self.corpus.lookup_cat_no("pcr 96 fs c")],
            ["A4"])

    def test_normalisation_is_applied_on_the_way_in(self):
        self.assertEqual(self._row("A1").normalized, "nunc 96well plate")
        self.assertEqual(self._row("A5").normalized,
                         "serological pipette 50ml")

    def test_description_is_carried_but_never_tokenised(self):
        """Display and logging only -- 80.9% of descriptions copy the name."""
        rows = rows_from_dicts([{"name": "Z1", "item_name": "Plate",
                                 "description": "a very long description"}])
        row = rows[0]
        self.assertEqual(row.description, "a very long description")
        self.assertEqual(row.tokens, ("plate",))

    def _row(self, code):
        return next(r for r in self.corpus.rows if r.item_code == code)


class TestCandidateLookup(unittest.TestCase):

    def setUp(self):
        self.corpus = build_corpus(tiny_catalogue())

    def test_a_rare_word_finds_its_item(self):
        found = self.corpus.candidates("Nunc 96 Well Plate")
        self.assertIn("A1", [r.item_code for r in found])

    def test_the_item_being_edited_can_be_excluded(self):
        found = self.corpus.candidates("Nunc 96 Well Plate",
                                       exclude_item_code="A1")
        self.assertNotIn("A1", [r.item_code for r in found])

    def test_a_short_name_falls_back_to_the_full_scan(self):
        """"CPU" has one word, so the index has no opinion and the honest
        answer is everything. 24.9 ms, still inside budget."""
        self.assertEqual(len(self.corpus.candidates("CPU")), len(self.corpus))

    def test_a_name_of_only_common_words_falls_back_too(self):
        """If every word is above the ceiling there is nothing selective to
        look up, which is the same situation as a one-word name."""
        df = {"plate": 999, "well": 999}
        corpus = Corpus(rows=self.corpus.rows, df=df, ceiling=222,
                        inverted={}, cat_no_map={})
        self.assertEqual(len(corpus.candidates("well plate")),
                         len(self.corpus.rows))

    def test_results_are_deduplicated_across_words(self):
        """An item posted under two of the query's words must appear once."""
        found = self.corpus.candidates("Axygen PCR Plate")
        codes = [r.item_code for r in found]
        self.assertEqual(len(codes), len(set(codes)))

    def test_candidates_never_raises_on_empty_input(self):
        self.assertEqual(len(self.corpus.candidates("")), len(self.corpus))
        self.assertEqual(len(self.corpus.candidates(None)), len(self.corpus))


class TestItemRow(unittest.TestCase):

    def test_missing_fields_do_not_raise(self):
        row = ItemRow(item_code="X1", item_name=None)
        self.assertEqual(row.normalized, "")
        self.assertEqual(row.tokens, ())
        self.assertEqual(row.cat_no, "")
        self.assertEqual(row.makes, "")

    def test_flags_are_coerced_to_int(self):
        row = ItemRow("X1", "Plate", disabled=None, is_stock_item=None)
        self.assertEqual(row.disabled, 0)
        self.assertEqual(row.is_stock_item, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
