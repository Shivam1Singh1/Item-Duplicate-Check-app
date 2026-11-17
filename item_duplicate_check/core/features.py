"""
features.py  --  turn a PAIR of items into the numbers the model learns from.

WHAT THIS FILE IS
-----------------
The model is a pair classifier: give it two items, it returns the chance they
are the same product. It cannot read an item name. This file is the
translation layer -- two rows in, a fixed-length vector of numbers out.

`FEATURE_NAMES` is the contract. It is an ordered tuple, and the order is
part of the saved model: a trained model's coefficients are positional, so
reordering this tuple silently invalidates every model ever trained with it.
Append at the end, never insert, and bump the engine version when you do.

THE TWO FIELDS THAT MAY NEVER BECOME FEATURES
---------------------------------------------
This is the single most important rule in the file, and it is not obvious.

    cat_no          A positive training pair IS "these two items share a
                    Cat No". So feeding Cat No to the model teaches it one
                    useless rule -- "same Cat No means same item" -- and
                    nothing about names. It would score beautifully in
                    testing and fail completely on the 30% of items that
                    have no Cat No at all.

    old_item_code   The same trap, one step removed. It is one of the three
                    signals that build the product groups, and the product
                    groups are what the split and the negatives are built
                    from. Using it as a feature leaks the label the same way.

The mistake has a name -- label leakage -- and it is the reason a model can
look excellent in every test you run and then be useless in production.
tests/test_features.py asserts that neither value can reach the vector.

FIELDS EXCLUDED FOR MEASURED REASONS, NOT FOR LEAKAGE
-----------------------------------------------------
    stock_uom       separation -23.4. It does not merely fail to help, it
                    points the WRONG way: in confirmed duplicates -- same
                    Cat No, same manufacturer, same product -- the unit
                    disagrees 52.4% of the time
    description     80.9% are a 95%-or-better copy of item_name, so scoring
                    it double-counts the name. Display and logging only
    item_group      the largest group holds 36% of the catalogue
    item_child_group 80.5% of true duplicate pairs sit in DIFFERENT child
                    groups, so it is close to noise for this question
    brand           filled on 0.7% of items

THREE-STATE FIELDS, AND WHY EACH ONE COSTS TWO COLUMNS
------------------------------------------------------
A field can agree, disagree, or have nothing to say. Those are three states,
and collapsing them into one number forces a choice between two wrong
answers: blank-as-0 means a missing manufacturer counts as EVIDENCE AGAINST
the pair, and blank-as-0.5 invents a half-opinion.

So each such field gets two columns, `*_agree` and `*_abstain`:

    agree       1, 0     both sides present and equal / not equal
    abstain     1        either side missing or junk

The model then learns what an abstention is worth instead of being told. This
matters a great deal here, because coverage is poor on exactly the fields
with the best separation:

    makes       72.6% filled      hsn        69.9%
    department  49.0%             requester  19.8%

PURE PYTHON
-----------
No Frappe, no numpy, no sklearn. Document frequencies arrive as an argument.
That is what lets the whole feature set be tested in milliseconds and keeps
training and serving using literally the same code -- the usual way a model
goes wrong in production is that the two computed features differently.
"""

import math
from difflib import SequenceMatcher

from item_duplicate_check.core import makes as makes_module
from item_duplicate_check.core.itemcode import same_category_different_division
from item_duplicate_check.core.normalize import normalize

# --------------------------------------------------------------------------
# THE CONTRACT. Order is part of every saved model. Append only.
# --------------------------------------------------------------------------
FEATURE_NAMES = (
    # ---- how alike are the names? (10) ---------------------------------
    "ordered_similarity",        # words sorted, then compared character-wise
    "raw_similarity",            # compared in the order typed
    "set_overlap",               # shared words / words in the shorter name
    "length_ratio",              # words in shorter / words in longer
    "set_overlap_x_length",      # the product -- see the note below
    "idf_shared_fraction",       # shared words weighted by how rare they are
    "shared_token_count",
    "token_count_min",
    "token_count_diff",
    "digit_token_overlap",       # sizes and codes, agreeing or not
    "first_token_match",         # same head word

    # ---- do the fields agree? (9) --------------------------------------
    "makes_agree", "makes_abstain",
    "hsn_agree", "hsn_abstain",
    "department_agree", "department_abstain",
    "requester_agree", "requester_abstain",
    "naming_series_differs",     # INVERTED: different series => more likely

    # ---- what the item code says (1) -----------------------------------
    "same_category_other_division",
)

#: Fields that must never appear above. Asserted by the tests.
FORBIDDEN_FIELDS = ("cat_no", "old_code", "stock_uom", "description",
                    "item_group", "item_child_group", "brand")


# ==========================================================================
# Small helpers
# ==========================================================================

def _ratio(a: str, b: str) -> float:
    """Character-level similarity of two strings, 0..1.

    THE SWAP IS NOT TIDYING. difflib's ratio() is NOT symmetric:
    SequenceMatcher(None, x, y).ratio() can differ from
    SequenceMatcher(None, y, x).ratio(), because the matching-block search is
    greedy and indexes only the second sequence. Measured on real rows from
    this catalogue: 0.381 one way, 0.333 the other.

    An asymmetric feature would make the model's answer depend on which item
    the user happened to open first, and "does this already exist?" has no
    direction. Fixing the argument order by sorting makes the result
    independent of how the caller passed them in. Found by
    test_features.TestContract.test_every_feature_is_symmetric.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a > b:
        a, b = b, a
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _agreement(value_a, value_b):
    """(agree, abstain) for one three-state field.

    Compared case-folded and stripped, because "R&D - MCPL" and
    "r&d - mcpl " are the same department typed twice.
    """
    a = (value_a or "").strip().lower()
    b = (value_b or "").strip().lower()
    if not a or not b:
        return 0.0, 1.0
    return (1.0 if a == b else 0.0), 0.0


def _digit_tokens(tokens):
    """Words containing a digit: sizes, pack counts, catalogue codes.

    Pulled out separately because they carry most of the "is this actually
    the same variant" information, and a plain word overlap drowns them in
    the surrounding prose.
    """
    return {t for t in tokens if any(ch.isdigit() for ch in t)}


# ==========================================================================
# The feature set
# ==========================================================================

def pair_features(row_a, row_b, df: dict = None, corpus_size: int = 0,
                  aliases: dict = None) -> dict:
    """Every feature for one pair, as a name -> number dict.

    `row_a` and `row_b` are corpus.ItemRow objects, or anything carrying the
    same attributes. `df` maps word -> how many items contain it; pass the
    corpus's, so rarity is judged against the real catalogue.
    """
    df = df or {}
    size = max(corpus_size or len(df) or 2, 2)

    tokens_a = getattr(row_a, "tokens", None)
    if tokens_a is None:
        tokens_a = tuple(normalize(row_a.item_name).split())
    tokens_b = getattr(row_b, "tokens", None)
    if tokens_b is None:
        tokens_b = tuple(normalize(row_b.item_name).split())

    set_a, set_b = set(tokens_a), set(tokens_b)
    shared = set_a & set_b

    # ---- names ---------------------------------------------------------
    sorted_a = getattr(row_a, "sorted_key", None) or " ".join(sorted(tokens_a))
    sorted_b = getattr(row_b, "sorted_key", None) or " ".join(sorted(tokens_b))
    normalized_a = getattr(row_a, "normalized", None) or " ".join(tokens_a)
    normalized_b = getattr(row_b, "normalized", None) or " ".join(tokens_b)

    ordered = _ratio(sorted_a, sorted_b)
    raw = _ratio(normalized_a, normalized_b)

    if set_a and set_b:
        shorter = min(len(set_a), len(set_b))
        longer = max(len(set_a), len(set_b))
        set_overlap = len(shared) / float(shorter)
        length_ratio = shorter / float(longer)
    else:
        shorter = longer = 0
        set_overlap = length_ratio = 0.0

    # WHY THE PRODUCT IS ITS OWN COLUMN.
    # set_overlap alone is a perfect 1.0 whenever one name's words are a
    # subset of the other's -- "DNAse" against "DNase I, RNase-free (1 U/uL)"
    # scored 1.00. Multiplying by length_ratio corrects it. When a model was
    # trained on this data it independently discovered the same correction:
    # a negative weight on raw overlap and a positive one on the product. It
    # is given directly so the model does not have to spend capacity
    # rediscovering it.
    set_overlap_x_length = set_overlap * length_ratio

    def idf(token):
        return math.log(size / float(max(df.get(token, 0), 1)))

    # sorted() IS LOAD-BEARING, not tidiness. Python's set iteration order
    # depends on the set's internal layout, so `set_a | set_b` and
    # `set_b | set_a` yield the same words in a different order -- and
    # floating-point addition is not associative, so the two sums differ in
    # the last bits. That made this feature asymmetric (1.0 one way,
    # 0.9999999999999999 the other) and an asymmetric feature makes the
    # answer depend on which item the user opened first. Sorting fixes the
    # order of summation and therefore the result.
    shared_idf = sum(idf(t) for t in sorted(shared))
    union_idf = sum(idf(t) for t in sorted(set_a | set_b))
    # min() is not cosmetic. When the two token sets are identical the two
    # sums are the same quantity added in a different order, and floating
    # point makes the ratio 1.0000000000000002 -- which then fails a
    # "this is a proportion" bound check downstream. Measured on 181 of
    # 22,921 real pairs.
    idf_shared_fraction = (min(1.0, shared_idf / union_idf)
                           if union_idf > 0 else 0.0)

    digits_a, digits_b = _digit_tokens(set_a), _digit_tokens(set_b)
    if digits_a and digits_b:
        digit_overlap = (len(digits_a & digits_b)
                         / float(min(len(digits_a), len(digits_b))))
    else:
        # Neither side states a size or a code: nothing to compare, which is
        # not the same as disagreeing. 0.0 is the honest neutral here because
        # the gate in specs.py is what actually judges size conflicts.
        digit_overlap = 0.0

    first_match = (1.0 if tokens_a and tokens_b and tokens_a[0] == tokens_b[0]
                   else 0.0)

    # ---- fields --------------------------------------------------------
    # Makes goes through the alias table first, so "Thermo Scientific" and
    # "Thermo Fisher Scientific" agree instead of disagreeing. Without that
    # step this field is noise: 1,438 spellings for a few hundred companies.
    makes_a = makes_module.canonical(getattr(row_a, "makes", ""), aliases)
    makes_b = makes_module.canonical(getattr(row_b, "makes", ""), aliases)
    makes_agree, makes_abstain = _agreement(makes_a, makes_b)

    hsn_agree, hsn_abstain = _agreement(getattr(row_a, "hsn", ""),
                                        getattr(row_b, "hsn", ""))
    dept_agree, dept_abstain = _agreement(getattr(row_a, "department", ""),
                                          getattr(row_b, "department", ""))
    req_agree, req_abstain = _agreement(getattr(row_a, "requester", ""),
                                        getattr(row_b, "requester", ""))

    # THE NAME OF THIS FEATURE IS A LEFTOVER. READ THIS BEFORE TRUSTING IT.
    #
    # Decision 7 measured naming_series as an INVERTED signal -- separation
    # -11.1, agreement 72.5% in duplicates against 83.6% in non-duplicates --
    # concluding that a DIFFERENT series means MORE likely duplicate. The
    # feature is spelled "differs" because of that conclusion.
    #
    # It does not reproduce on site_local, measured two ways:
    #
    #                             duplicates agree   non-dups agree   sep
    #     hard negatives                    74.6%            65.2%   +9.4
    #     random negatives                  74.6%            60.9%  +13.7
    #
    # Both directions say the opposite: duplicates agree on the series MORE
    # often, so this is an ordinary positive signal and the fitted
    # coefficient on "differs" should come out NEGATIVE. If it comes out
    # positive, that is worth investigating rather than shipping.
    #
    # It is also weak information whichever way it points: 75.4% of items
    # share one series (RM/.######), so the column is mostly constant.
    #
    # The name is kept rather than flipped because FEATURE_NAMES is the
    # contract every saved model is fitted against, and renaming it would
    # invalidate models for no gain -- the value carries the same
    # information either way.
    series_a = (getattr(row_a, "naming_series", "") or "").strip().lower()
    series_b = (getattr(row_b, "naming_series", "") or "").strip().lower()
    series_differs = (1.0 if series_a and series_b and series_a != series_b
                      else 0.0)

    # ---- the item code -------------------------------------------------
    # Parsed, never text-matched: RDCN02030516 and RDCN02030517 are 92%
    # identical as strings and are unrelated products. True when two codes
    # share a category but sit under different divisions, which the numbering
    # scheme guarantees happens to any product two teams both buy.
    cross_division = (
        1.0 if same_category_different_division(
            getattr(row_a, "item_code", ""), getattr(row_b, "item_code", ""))
        else 0.0)

    return {
        "ordered_similarity": ordered,
        "raw_similarity": raw,
        "set_overlap": set_overlap,
        "length_ratio": length_ratio,
        "set_overlap_x_length": set_overlap_x_length,
        "idf_shared_fraction": idf_shared_fraction,
        "shared_token_count": float(len(shared)),
        "token_count_min": float(shorter),
        "token_count_diff": float(longer - shorter),
        "digit_token_overlap": digit_overlap,
        "first_token_match": first_match,

        "makes_agree": makes_agree,
        "makes_abstain": makes_abstain,
        "hsn_agree": hsn_agree,
        "hsn_abstain": hsn_abstain,
        "department_agree": dept_agree,
        "department_abstain": dept_abstain,
        "requester_agree": req_agree,
        "requester_abstain": req_abstain,
        "naming_series_differs": series_differs,

        "same_category_other_division": cross_division,
    }


def feature_vector(row_a, row_b, df: dict = None, corpus_size: int = 0,
                   aliases: dict = None) -> list:
    """The same features as a list, in FEATURE_NAMES order.

    This is what goes to the model. Always built from FEATURE_NAMES rather
    than from the dict's own order, so a dict that gains a key cannot shift
    every column by one.
    """
    values = pair_features(row_a, row_b, df, corpus_size, aliases)
    return [values[name] for name in FEATURE_NAMES]


def is_symmetric() -> bool:
    """Documentation, not a check.

    Every feature here is symmetric: pair_features(a, b) equals
    pair_features(b, a). That is deliberate. "Does this already exist?" has
    no direction, and an asymmetric feature would make the warning depend on
    which item the user happened to open first. tests/test_features.py
    asserts it on real pairs.
    """
    return True
