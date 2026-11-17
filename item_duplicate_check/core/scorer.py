"""
scorer.py  --  decide how alike two items are, and say why in English.

THE FORMULA
-----------
    score = veto_gate x (  0.47 x ordered word similarity
                         + 0.21 x word set similarity x length penalty
                         + 0.18 x rare word overlap (IDF weighted)
                         + 0.14 x makes agreement )

    veto_gate is 0 or 1. Zero cancels everything and returns the reason.
    An exact Cat No match jumps straight to 0.97 and skips the weights.

Every weight and threshold comes from Item Duplicate Check Settings. None of
them are written into this file, because the two-week silent run is expected
to move them and that must not need a deploy.

WHY A HAND-WRITTEN SUM AND NOT A MODEL
--------------------------------------
Three reasons, all of which still hold:

  1. IT CAN BE EXPLAINED. The panel says "names 97% alike, same manufacturer,
     both 50 mL" instead of "0.94". Users trust a tool that explains itself,
     and a reason string is also what makes a wrong warning diagnosable.
  2. IT CAN BE TUNED WITHOUT RETRAINING. Every weight is a setting.
  3. IT WORKED ON DAY ONE, with no training data.

Measured: a trained model on 18 features beat these rules by +1.2 points.
Almost all the value is in the candidate search and the veto gate, which is
why the model is last and optional.

THE FOUR COMPONENTS
-------------------
1. ORDERED WORD SIMILARITY (0.47) -- the workhorse.
   Sort both names' words, then compare the two strings character by
   character. Sorting first means "Serological Pipette 50ml" and
   "50ml Serological pipette" are the same thing; comparing characters
   afterwards still catches a typo inside a word.

2. WORD SET SIMILARITY x LENGTH PENALTY (0.21) -- and the penalty is the
   point. Set overlap alone scores a perfect 1.0 whenever one name's words
   are a subset of the other's:

       1.00   "DNase I, RNase-free (1 U/uL)"   vs   "DNAse"
       1.00   "PmeI, recombinant"              vs   "PmeI"
       1.00   "Plasmid DNA - qwp"              vs   "Plasmid DNA"

   Those are misleading. Multiplying by min(words)/max(words) scales the
   component by how much of the longer name was actually accounted for.
   When the model was trained it independently learned this same correction,
   which is a good sign the hand-written rule is right.

3. RARE WORD OVERLAP (0.18) -- weight the shared words by how rare they are.
   Two items sharing "nunc" is strong evidence. Two items sharing "plate" is
   almost none. Plain string similarity cannot tell those apart; inverse
   document frequency can, by counting.

4. MAKES AGREEMENT (0.14) -- same manufacturer, after the alias table has
   collapsed 1,438 spellings. ABSTAINS when either side is blank: see the
   renormalisation note on score_pair().

WHAT IS DELIBERATELY NOT IN HERE
--------------------------------
    cat_no          it made the labels, so using it as a clue would be label
                    leakage and the scorer would score beautifully in testing
                    while learning nothing about names (README decision 6)
    stock_uom       separation -23.4: in confirmed duplicates the unit
                    disagrees 52.4% of the time. It points the wrong way
    description     80.9% are a near-copy of item_name. Display only
    item_group      largest group holds 36% of the catalogue
    brand           filled on 0.7% of items
    item_code       parsed by itemcode.py, never text-matched: two unrelated
                    codes are 92% similar as strings

PURE PYTHON
-----------
No Frappe import. Weights arrive as an argument, the catalogue arrives as a
Corpus, and the alias table is a dict. That is what lets scripts/eval_harness
run this exact code over thousands of pairs with no site.
"""

import math
from difflib import SequenceMatcher

from item_duplicate_check.core import makes as makes_module
from item_duplicate_check.core.normalize import normalize
from item_duplicate_check.core.specs import sizes, veto_reason

# --------------------------------------------------------------------------
# Defaults. These are the fallback for when Settings cannot be read -- the
# real values live in the Item Duplicate Check Settings single doctype.
# --------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "ordered": 0.47,
    "word_set": 0.21,
    "rare": 0.18,
    "makes": 0.14,
}

#: A shared Cat No is strong evidence, not proof -- so it is high, not 1.0,
#: and the veto gate still runs on top of it. Real counter-example from the
#: catalogue, same Cat No, different products:
#:     SUFR38200  "Tyvek roll Flat reels-38 cm x 200 mtr"
#:                "Tyvek roll Flat reels- 40cm x 200 mtr"
CAT_NO_SCORE = 0.97

DEFAULT_STRONG_THRESHOLD = 0.92
DEFAULT_SOFT_THRESHOLD = 0.75

#: How many matches the caller gets back.
TOP_N = 5

#: How many gate-cancelled pairs to keep for the log. They are the most
#: useful rows in Item Match Log: a pair that scored high and was cancelled
#: is exactly where a wrong rule would show up.
TOP_N_CANCELLED = 5

#: Friendly spelling for each dimension's base unit, used by the reason
#: string. (base unit label, [(multiplier, label), ...] preferred larger
#: units first) so 1000 ml reads "1 L" rather than "1000 mL".
_UNIT_DISPLAY = {
    "volume": [(1000.0, "L"), (1.0, "mL"), (0.001, "uL")],
    "mass": [(1000.0, "kg"), (1.0, "g"), (0.001, "mg"), (1e-6, "ug")],
    "length": [(1000.0, "m"), (10.0, "cm"), (1.0, "mm"), (0.001, "um")],
    "mw": [(1000.0, "kDa"), (1.0, "Da")],
    "count": [(1.0, "")],
    "percent": [(1.0, "%")],
    "activity": [(1.0, "U")],
}


# ==========================================================================
# The four components
# ==========================================================================

def ordered_similarity(tokens_a, tokens_b) -> float:
    """Component 1. Word-order-insensitive character similarity, 0..1.

    Sorting the words before comparing is what makes word order irrelevant;
    comparing characters afterwards is what still catches "serologcal".
    """
    return ordered_similarity_keys(" ".join(sorted(tokens_a)),
                                   " ".join(sorted(tokens_b)))


def ordered_similarity_keys(key_a: str, key_b: str) -> float:
    """ordered_similarity() for callers that already sorted the words.

    This is the expensive component -- quadratic in the length of the names --
    and it is the reason search() bothers with an upper bound at all. The
    corpus precomputes `sorted_key` for every row so this is the only work
    left to do here.

    autojunk is switched off. It is a speed heuristic that starts ignoring
    characters appearing in more than 1% of a long string, and on strings
    this short it only ever changes the answer.
    """
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    # difflib's ratio() is NOT symmetric -- the matching-block search is
    # greedy and indexes only the second sequence, so swapping the arguments
    # can change the answer (0.381 vs 0.333 on real rows from this
    # catalogue). Sorting the pair makes the score independent of which item
    # the user opened first. Found while testing core/features.py, which has
    # the same guard for the same reason.
    if key_a > key_b:
        key_a, key_b = key_b, key_a
    return SequenceMatcher(None, key_a, key_b, autojunk=False).ratio()


def ordered_upper_bound(len_a: int, len_b: int) -> float:
    """The highest ordered_similarity() two strings of these lengths can reach.

    difflib's ratio is 2*M/T, where M is the number of matching characters
    and T the total length. M cannot exceed the shorter string, so the ratio
    cannot exceed 2*min/(la+lb). Costs one division instead of a quadratic
    diff, and it is a true ceiling -- which is what makes the early exit in
    search() exact rather than approximate.

    This is difflib's own real_quick_ratio(), inlined to avoid building a
    SequenceMatcher for every one of 22,204 candidates.
    """
    total = len_a + len_b
    if total == 0:
        return 0.0
    return 2.0 * min(len_a, len_b) / total


#: Two words this alike count as the same word. See shared_words().
#: 0.85 is deliberately high: "diiary"/"diary" is 0.91 and must pass,
#: "b1500s"/"b1500l" is 0.83 and must NOT, "1ml"/"5ml" is 0.67 and must not.
FUZZY_TOKEN_THRESHOLD = 0.85

#: Near-matching only applies to SHORT names, and that limit is as much about
#: correctness as speed.
#:
#: A typo is fatal to a one-word name -- "Diiary" and "Diary" share nothing,
#: so two of the four components read zero. In a six-word name the same typo
#: leaves five words matching exactly, the components are already high, and
#: near-matching would change the score by a rounding error.
#:
#: It is also where the cost is. Near-matching weakens the early exit,
#: because a candidate can no longer be ruled out on exact overlap alone.
#: Applying it to every pair took the worst case from 60 ms to 630 ms,
#: through the 300 ms budget. Confining it to short names puts the common
#: case back on the fast path.
FUZZY_MAX_TOKENS = 3

#: And the two names must be of comparable LENGTH. A typo adds or drops a
#: character or two; it does not turn a six-character name into a forty-
#: character one.
#:
#: This second gate is what keeps the search fast. The token-count gate alone
#: is not enough: if the QUERY is short, then min(query, row) is short for
#: every row in the catalogue, so every candidate became fuzzy-eligible, the
#: early exit lost all its pruning power, and the worst case sat at 665 ms
#: against a 300 ms budget.
FUZZY_MIN_LENGTH_RATIO = 0.80


def _fuzzy_applies(set_a, set_b) -> bool:
    """Is this a pair where a single typo could decide the answer?

    Two gates, both cheap, both about when near-matching can possibly
    matter rather than about how fast it is -- although it is also what
    makes it affordable.
    """
    if min(len(set_a), len(set_b)) > FUZZY_MAX_TOKENS:
        return False
    length_a = sum(len(word) for word in set_a)
    length_b = sum(len(word) for word in set_b)
    return ordered_upper_bound(length_a, length_b) >= FUZZY_MIN_LENGTH_RATIO


def shared_words(set_a, set_b):
    """Words the two names have in common, typos included.

    WHY THIS IS NOT JUST set_a & set_b
    ----------------------------------
    Exact token matching treats a word with a typo as a different word, and
    for a ONE-WORD name that is catastrophic: "Diiary" against "Diary" shares
    no token, so both token-based components collapse to zero and the pair
    can never score above

        0.47 / (0.47 + 0.21 + 0.18) = 0.547

    which is below the 0.75 soft threshold. A one-word name with a typo was
    therefore unwarnable, whatever the characters said. 768 items in the
    catalogue (3.5%) have a one-word name. Found when a user created
    "Diiary" next to an existing "Diary" and saw nothing.

    So a word counts as shared when it matches exactly OR is at least
    FUZZY_TOKEN_THRESHOLD similar to a word on the other side. This is the
    same idea as the DNase/DNAse case in normalize.py -- one product, two
    spellings -- applied at compare time rather than at clean time, because
    a typo cannot be enumerated in advance.

    Returns (count of shared words, the exactly-shared set). The exact set is
    returned separately because the IDF weighting needs real words to look
    frequencies up for.

    COST. Only words with no exact partner are compared, which is usually
    none and at worst a handful. The quadratic part never sees the common
    case.
    """
    exact = set_a & set_b
    if not _fuzzy_applies(set_a, set_b):
        return len(exact), exact

    left = set_a - exact
    right = set_b - exact
    if not left or not right:
        return len(exact), exact

    matched = len(exact)
    available = set(right)
    for word in sorted(left):
        best, best_word = 0.0, None
        for other in sorted(available):
            # Length guard first: difflib cannot exceed 2*min/(la+lb), so a
            # pair that far apart is ruled out without a diff.
            if ordered_upper_bound(len(word), len(other)) < FUZZY_TOKEN_THRESHOLD:
                continue
            score = _ratio_symmetric(word, other)
            if score > best:
                best, best_word = score, other
        if best >= FUZZY_TOKEN_THRESHOLD:
            matched += 1
            available.discard(best_word)
    return matched, exact


def _ratio_symmetric(a: str, b: str) -> float:
    """difflib ratio with the arguments ordered, so it cannot be asymmetric."""
    if a == b:
        return 1.0
    if a > b:
        a, b = b, a
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def word_set_similarity(tokens_a, tokens_b):
    """Component 2, as its two separate factors: (set overlap, length penalty).

    Returned unmultiplied so the caller can show both in the log. Overlap is
    measured against the SHORTER name, which is what makes it hit 1.0 on a
    subset -- and the length penalty is what then corrects for it.
    """
    set_a, set_b = set(tokens_a), set(tokens_b)
    if not set_a or not set_b:
        return 0.0, 0.0

    shared, _exact = shared_words(set_a, set_b)
    shorter, longer = sorted((len(set_a), len(set_b)))

    overlap = min(1.0, shared / float(shorter))
    penalty = shorter / float(longer)
    return overlap, penalty


def rare_word_overlap(tokens_a, tokens_b, df: dict, corpus_size: int) -> float:
    """Component 3. IDF-weighted overlap, 0..1.

    Each shared word is worth log(N / df) -- a word in 3 items out of 22,204
    is worth about 8.9, a word in 1,000 of them about 3.1. The score is the
    shared weight as a fraction of the total weight on the table, so it is
    symmetric and cannot exceed 1.

    An unseen word (df 0) is treated as appearing once: it is the rarest
    thing possible, which is correct, and it avoids dividing by zero.
    """
    set_a, set_b = set(tokens_a), set(tokens_b)
    if not set_a or not set_b:
        return 0.0

    def idf(token):
        return math.log(max(corpus_size, 2) / float(max(df.get(token, 0), 1)))

    # sorted() fixes the order of summation. Set iteration order depends on
    # the set's internal layout, and floating-point addition is not
    # associative, so without it this function is asymmetric in the last
    # bits -- 0.3737632744068581 one way, 0.373763274406858 the other.
    #
    # Typo-matched words count here too, at the rarity of the word that was
    # actually looked up. Without it "Diiary" and "Diary" share no rarity at
    # all, which is a third of the reason that pair was unwarnable.
    count, exact = shared_words(set_a, set_b)
    shared = sum(idf(t) for t in sorted(exact))
    if count > len(exact):
        # One or more words matched only approximately. Credit them at the
        # average weight of the words on the shorter side, which is the
        # honest estimate: we know a word matched, we just cannot look up a
        # frequency for a spelling that occurs nowhere.
        smaller = set_a if len(set_a) <= len(set_b) else set_b
        average = (sum(idf(t) for t in sorted(smaller)) / float(len(smaller))
                   if smaller else 0.0)
        shared += average * (count - len(exact))

    total = sum(idf(t) for t in sorted(set_a | set_b))
    if total <= 0:
        return 0.0
    return shared / total


def makes_agreement(makes_a, makes_b, aliases=None):
    """Component 4. 1.0 agree, 0.0 disagree, None to abstain."""
    return makes_module.agreement(makes_a, makes_b, aliases)


# ==========================================================================
# Reason strings -- what the user actually reads
# ==========================================================================

def _format_size(dimension, magnitude) -> str:
    """("volume", 50.0) -> "50 mL";  ("volume", 1000.0) -> "1 L"."""
    for factor, label in _UNIT_DISPLAY.get(dimension, [(1.0, "")]):
        if magnitude >= factor or factor == 1.0:
            value = magnitude / factor
            text = ("%g" % value)
            return (text + " " + label).strip() if label else text
    return "%g" % magnitude


def shared_size_phrase(name_a: str, name_b: str) -> str:
    """"both 50 mL" when the two names carry the same measurement.

    This is the part of the reason users find most convincing, because size
    is the thing they are most often worried about getting wrong.
    """
    shared = sizes(name_a) & sizes(name_b)
    if not shared:
        return ""
    # Largest first: "both 1 L" is more informative than "both 96"
    parts = [_format_size(d, m) for d, m in
             sorted(shared, key=lambda s: -s[1])[:2]]
    return "both " + " and ".join(parts)


def build_reason(components: dict, query, row, aliases=None) -> str:
    """One short human sentence, assembled from the component scores.

        "names 97% alike - same manufacturer (thermo) - both 50 mL"

    Shown in the panel and written to Item Match Log. Decision 10: show the
    reason, not the number. It does more for adoption than any accuracy
    improvement, and it is the only thing that makes a wrong warning
    reportable by the person who saw it.
    """
    parts = []

    if components.get("via") == "cat_no":
        parts.append("same Cat No (%s)" % components.get("cat_no", "").upper())

    ordered = components.get("ordered")
    if ordered is not None:
        parts.append("names %d%% alike" % round(ordered * 100))

    agreement = components.get("makes")
    if agreement == 1.0:
        canonical = makes_module.canonical(row.makes, aliases)
        parts.append("same manufacturer (%s)" % canonical)
    elif agreement == 0.0:
        parts.append("different manufacturer")

    phrase = shared_size_phrase(query.name, row.item_name)
    if phrase:
        parts.append(phrase)

    rare_shared = components.get("rare_words") or ()
    if rare_shared:
        parts.append("shares %s" % ", ".join(rare_shared[:2]))

    return " - ".join(parts)


# ==========================================================================
# Query, Match
# ==========================================================================

class Query(object):
    """What the user has typed so far.

    `cat_no` is carried for the Step 0 lookup only. `item_code` is the row
    being edited, so an existing Item never matches itself.

    The derived forms mirror ItemRow's, for the same reason: they are used
    once per candidate, so they are computed once per query instead.
    """

    __slots__ = ("name", "cat_no", "makes", "item_code", "normalized",
                 "tokens", "token_set", "sorted_key")

    def __init__(self, name, cat_no=None, makes=None, item_code=None):
        self.name = name or ""
        self.cat_no = cat_no
        self.makes = makes
        self.item_code = item_code
        self.normalized = normalize(self.name)
        self.tokens = tuple(self.normalized.split())
        self.token_set = frozenset(self.tokens)
        self.sorted_key = " ".join(sorted(self.tokens))

    def __repr__(self):
        return "Query(%r, cat_no=%r, makes=%r)" % (
            self.name[:40], self.cat_no, self.makes)


class Match(object):
    """One scored candidate, ready to be shown and logged."""

    __slots__ = ("item_code", "item_name", "description", "score", "band",
                 "via", "reason", "cancel_reason", "components", "disabled")

    def __init__(self, row, score, band, via, reason, cancel_reason,
                 components):
        self.item_code = row.item_code
        self.item_name = row.item_name
        self.description = row.description
        self.score = score
        self.band = band
        self.via = via
        self.reason = reason
        self.cancel_reason = cancel_reason
        self.components = components
        self.disabled = row.disabled

    def as_dict(self) -> dict:
        """Plain dict for the API, the child table and the log."""
        return {
            "item": self.item_code,
            "item_name": self.item_name,
            "description": self.description,
            "score": round(self.score, 4),
            "band": self.band,
            "via": self.via,
            "reason": self.reason,
            "cancel_reason": self.cancel_reason,
            "disabled": self.disabled,
            "components": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in self.components.items()},
        }

    def __repr__(self):
        return "Match(%s %.3f %s)" % (self.item_code, self.score, self.band)


def band_for(score: float, strong: float, soft: float) -> str:
    """Which of the three warning bands a score falls into."""
    if score >= strong:
        return "strong"
    if score >= soft:
        return "soft"
    return "none"


# ==========================================================================
# Scoring one pair
# ==========================================================================

def score_pair(query, row, df: dict, corpus_size: int, weights: dict = None,
               aliases=None, apply_veto: bool = True) -> dict:
    """Score one candidate against the query. Returns the component breakdown.

    THE RENORMALISATION, which is the subtle part.
    Makes abstains on 29% of items, because that many carry no usable value.
    If an abstention contributed 0.0 the best achievable score would be 0.86,
    which is below the 0.92 strong threshold -- so no item without a
    manufacturer could ever raise a strong warning, however identical the
    name. Instead the abstaining weight is removed from the denominator, so
    the remaining components are judged on their own terms. Abstain means
    "no opinion", never "no".
    """
    weights = weights or DEFAULT_WEIGHTS

    ordered = ordered_similarity(query.tokens, row.tokens)
    overlap, penalty = word_set_similarity(query.tokens, row.tokens)
    rare = rare_word_overlap(query.tokens, row.tokens, df, corpus_size)
    agreement = makes_agreement(query.makes, row.makes, aliases)

    raw = _combine(weights, ordered, overlap * penalty, rare, agreement)

    shared_rare = tuple(t for t in row.rare if t in query.token_set)

    components = {
        "ordered": ordered,
        "word_set": overlap,
        "length_penalty": penalty,
        "rare": rare,
        "makes": agreement,
        "rare_words": shared_rare,
        "raw_score": raw,
        "via": "name",
    }

    cancel_reason = veto_reason(query.name, row.item_name) if apply_veto else None
    components["score"] = 0.0 if cancel_reason else raw
    components["cancel_reason"] = cancel_reason
    return components


def _combine(weights, ordered, word_set, rare, agreement) -> float:
    """The weighted sum, with the abstaining weight removed from the divisor.

    One place, so the exact score, the upper bound and score_pair() cannot
    drift apart.
    """
    total = weights["ordered"] + weights["word_set"] + weights["rare"]
    value = (weights["ordered"] * ordered
             + weights["word_set"] * word_set
             + weights["rare"] * rare)
    if agreement is not None:
        total += weights["makes"]
        value += weights["makes"] * agreement
    return value / total if total > 0 else 0.0


# ==========================================================================
# Step 0 -- the exact Cat No lookup
# ==========================================================================

def cat_no_step0(corpus, query, strong, soft, aliases=None):
    """Items carrying exactly the typed Cat No. Returns (matches, cancelled,
    seen item codes).

    Extracted so BOTH scoring engines use it. Cat No is a decision of its
    own (decision 6) and has nothing to do with which scorer is in use -- a
    shared Cat No answers the question directly, whatever the scorer thinks.
    Before this was pulled out, the model path had no Step 0 at all, which
    would have quietly made the strongest single signal in the system
    conditional on a settings switch.

    The score short-circuits the scorer entirely and the gate still runs:
    the catalogue contains a real pair of different-sized Tyvek rolls filed
    under one Cat No.
    """
    matches, cancelled, seen = [], [], set()

    for row in corpus.lookup_cat_no(query.cat_no):
        if row.item_code == query.item_code:
            continue
        seen.add(row.item_code)

        cancel_reason = veto_reason(query.name, row.item_name)
        components = {
            "via": "cat_no",
            "cat_no": row.cat_no,
            "ordered": ordered_similarity(query.tokens, row.tokens),
            "makes": makes_agreement(query.makes, row.makes, aliases),
            "raw_score": CAT_NO_SCORE,
            "score": 0.0 if cancel_reason else CAT_NO_SCORE,
            "cancel_reason": cancel_reason,
        }
        match = Match(
            row=row,
            score=components["score"],
            band=band_for(components["score"], strong, soft),
            via="cat_no",
            reason=build_reason(components, query, row, aliases),
            cancel_reason=cancel_reason,
            components=components,
        )
        (cancelled if cancel_reason else matches).append(match)

    return matches, cancelled, seen


# ==========================================================================
# The search -- Step 0, then shortlist, then score
# ==========================================================================

def search(corpus, query, weights: dict = None, strong: float = None,
           soft: float = None, aliases=None, limit: int = TOP_N) -> dict:
    """Find the items that look like `query`. The whole pipeline.

    Returns {"matches": [Match], "cancelled": [Match], "candidates": int,
             "total_similar": int, "via": "cat_no" | "name" | "none"}.

    `matches` is capped at `limit`. `total_similar` is not: it is how many
    existing items reached the soft band, which is what the warning reports.
    The two differ by a lot on the rows this app was built for -- 79 items
    are named "electrical installation at mrna lab", and a dialog listing
    five of them with no count reads as though there were five.

    STEP 0 -- exact Cat No. A shared Cat No answers the question directly, so
    it short-circuits the weights and returns 0.97. The veto gate still runs:
    the catalogue contains a real pair of different-sized Tyvek rolls filed
    under one Cat No.

    STEP 1 -- the rare-word shortlist from corpus.py.

    STEP 2 -- score, cheapest part first, with an early exit. The expensive
    component is the character-level comparison in ordered_similarity(), so
    the two cheap components are computed first and used to decide whether
    the expensive one can change the answer. On a full-scan query (22,204
    candidates) this is the difference between about 900 ms and about 50 ms.
    """
    weights = weights or DEFAULT_WEIGHTS
    strong = DEFAULT_STRONG_THRESHOLD if strong is None else strong
    soft = DEFAULT_SOFT_THRESHOLD if soft is None else soft
    if aliases is None:
        aliases = makes_module._ALIASES

    corpus_size = max(len(corpus), 1)

    # ---- STEP 0: exact Cat No -------------------------------------------
    matches, cancelled, seen = cat_no_step0(corpus, query, strong, soft,
                                            aliases)

    # ---- STEP 1: shortlist ----------------------------------------------
    candidates = corpus.candidates(query.name,
                                   exclude_item_code=query.item_code)

    # ---- STEP 2: cheap pass, then exact in bound order -------------------
    #
    # Two passes over the shortlist, which on a fallback query is the whole
    # catalogue.
    #
    # The cheap pass costs O(words) per row and uses only values the corpus
    # already precomputed. It ends with a true UPPER BOUND on the final
    # score: every component is exact except the character-level comparison,
    # and that one is replaced by its own cheap ceiling.
    #
    # The exact pass then walks the shortlist in bound order and stops the
    # moment the next bound cannot beat the current fifth-best. Because the
    # bound is a real ceiling the result is identical to scoring everything;
    # test_scorer.TestEarlyExit checks that against brute force.
    #
    # This is not a micro-optimisation. Before it, a one-word name like
    # "CPU" -- which falls back to all 22,204 rows -- took 3.8 SECONDS
    # against a 300 ms budget.
    query_set = query.token_set
    query_key = query.sorted_key
    query_key_len = len(query_key)
    query_words = len(query_set)
    query_idf_sum = sum(corpus.idf_of(t) for t in query_set)
    idf_of = corpus.idf_of

    scored_cheap = []
    for row in candidates:
        if row.item_code in seen:
            continue

        row_set = row.token_set
        shared = query_set & row_set
        row_words = len(row_set)

        if query_words and row_words:
            shorter = query_words if query_words < row_words else row_words
            longer = row_words if query_words < row_words else query_words
            penalty = shorter / float(longer)

            # AN OPTIMISTIC BOUND, NOT THE REAL VALUE.
            #
            # word_set_similarity() and rare_word_overlap() now count a word
            # as shared when it is merely a near-match -- that is what makes
            # "Diiary" findable next to "Diary". Doing that comparison here,
            # for every one of up to 22,204 candidates, would be far too
            # slow, and leaving this stage exact would make it an
            # UNDER-estimate, which is worse than slow: the early exit would
            # prune candidates that could actually have placed.
            #
            # So this assumes the best case -- every unmatched word on the
            # shorter side finds a partner -- which is a true ceiling and
            # costs one subtraction. The real figures are computed below,
            # only for the candidates that survive.
            fuzzy = _fuzzy_applies(query_set, row_set)
            could_match = (shorter - len(shared)) if fuzzy else 0

            overlap = min(1.0, (len(shared) + could_match) / float(shorter))

            shared_idf = sum(idf_of(t) for t in sorted(shared))
            # |A u B| weight = |A| + |B| - |A n B|, so the union never has to
            # be built
            union_idf = query_idf_sum + row.idf_sum - shared_idf
            # Same optimism: an unmatched word might yet count, so the
            # ceiling on the rare-word share is the whole union. Only for
            # short names -- otherwise this is the exact value and the early
            # exit keeps its teeth.
            rare = 1.0 if could_match > 0 else (
                shared_idf / union_idf if union_idf > 0 else 0.0)
        else:
            # One side has no words at all -- an empty query, or a row whose
            # name normalised away to nothing. Nothing to compare and nothing
            # to near-match.
            overlap = penalty = rare = 0.0
            fuzzy = False

        agreement = makes_agreement(query.makes, row.makes, aliases)
        bound = _combine(weights,
                         ordered_upper_bound(query_key_len, len(row.sorted_key)),
                         overlap * penalty, rare, agreement)
        # `shared` and `fuzzy` ride along so the exact pass below does not
        # have to rebuild what was just computed. When `fuzzy` is false the
        # overlap and rare values above are already EXACT, not ceilings, and
        # the exact pass can use them untouched.
        scored_cheap.append((bound, overlap, penalty, rare, agreement, row,
                             shared, fuzzy))

    scored_cheap.sort(key=lambda item: -item[0])

    # One matcher, reused. difflib offers three comparisons of increasing
    # cost and decreasing looseness, and each is an upper bound on the next:
    #
    #     real_quick_ratio()   O(1)      lengths only       (used above)
    #     quick_ratio()        O(n)      bag of characters
    #     ratio()              O(n^2)    the real answer
    #
    # So a candidate is dropped at the cheapest stage that can rule it out.
    # Every stage is a true ceiling, so the top five is still exact.
    matcher = SequenceMatcher(None, query_key, "", autojunk=False)

    exact = []

    #: How many existing items reach the soft band -- the number the warning
    #: reports. It is NOT len(matches): that list is capped at `limit`, which
    #: is why the dialog could say "5" for a name the catalogue holds 79 of.
    #: Step 0's Cat No hits are already in `matches`, so they are counted here
    #: and skipped by `seen` in the loop below -- nothing is counted twice.
    total_similar = sum(1 for m in matches if m.score >= soft)

    for (bound, overlap, penalty, rare, agreement, row,
         shared, fuzzy) in scored_cheap:
        # Nothing further down the list can beat the current fifth-best,
        # because the list is sorted by a true upper bound.
        threshold = exact[limit - 1][0] if len(exact) >= limit else -1.0

        # TWO reasons to keep walking, so two tests. A candidate is still
        # wanted if it can enter the top `limit` (> threshold) OR if it can
        # still be counted (>= soft). Only when it can do neither is the walk
        # over -- and because the list is sorted by a true upper bound,
        # nothing below this point can do either.
        #
        # The counting test is what the early exit used to make impossible:
        # it stopped the moment the top five was settled, so everything after
        # the fifth-best was invisible and uncountable. It costs nothing on a
        # junk query, where the bounds fall below `soft` within a few rows.
        if bound <= threshold and bound < soft:
            break

        # stage 2: the character bag, before paying for the full diff.
        # quick_ratio counts min(count_a, count_b) per character, so unlike
        # ratio() it IS symmetric, and it bounds the diff from either
        # direction. Safe to use as the prefilter.
        matcher.set_seq2(row.sorted_key)
        cheap_bound = _combine(weights, matcher.quick_ratio(),
                               overlap * penalty, rare, agreement)
        if cheap_bound <= threshold and cheap_bound < soft:
            continue

        # The exact value goes through ordered_similarity_keys() rather than
        # this matcher, because that function sorts the pair before diffing
        # and this matcher cannot (its seq1 is fixed to the query). Using the
        # matcher here would make search() and score_pair() disagree on the
        # same pair, which TestEarlyExit would catch as a bound error.
        # Turn the ceiling into the real figure, but ONLY for the short-name
        # pairs where it was a ceiling. For everything else the cheap pass
        # already produced the exact value, and recomputing it here -- which
        # an earlier version did, by calling the public helpers and rebuilding
        # the token sets from scratch -- took the worst case to 811 ms.
        if fuzzy:
            row_set = row.token_set
            count, exact_shared = shared_words(query_set, row_set)
            shorter = min(query_words, len(row_set))
            if shorter:
                overlap = min(1.0, count / float(shorter))
                shared_idf = sum(idf_of(t) for t in sorted(exact_shared))
                if count > len(exact_shared):
                    smaller = query_set if query_words <= len(row_set) else row_set
                    average = (sum(idf_of(t) for t in sorted(smaller))
                               / float(len(smaller))) if smaller else 0.0
                    shared_idf += average * (count - len(exact_shared))
                union_idf = query_idf_sum + row.idf_sum - sum(
                    idf_of(t) for t in sorted(shared))
                rare = (min(1.0, shared_idf / union_idf)
                        if union_idf > 0 else 0.0)

        ordered = ordered_similarity_keys(query_key, row.sorted_key)
        raw = _combine(weights, ordered, overlap * penalty, rare, agreement)

        # The veto gate parses both names, so it is not free either. A pair
        # that can neither place in the top five nor be counted does not need
        # it: a cancel would make the score 0, and an allow would still not
        # place it. A pair that CAN be counted does need it -- a vetoed pair
        # is a different product and must not be counted as an existing one.
        # This is also why `cancelled` only ever holds NEAR MISSES -- the
        # pairs that beat the running fifth-best, or reached the soft band,
        # and were then killed by a rule. Those are the interesting ones: a
        # wrong rule shows up there first.
        if raw <= threshold and raw < soft:
            continue

        cancel_reason = veto_reason(query.name, row.item_name)
        components = {
            "ordered": ordered,
            "word_set": overlap,
            "length_penalty": penalty,
            "rare": rare,
            "makes": agreement,
            "rare_words": tuple(t for t in row.rare if t in query_set),
            "raw_score": raw,
            "score": 0.0 if cancel_reason else raw,
            "cancel_reason": cancel_reason,
            "via": "name",
        }
        match = Match(
            row=row,
            score=components["score"],
            band=band_for(components["score"], strong, soft),
            via="name",
            reason=build_reason(components, query, row, aliases),
            cancel_reason=cancel_reason,
            components=components,
        )

        if cancel_reason:
            # Keep the near-misses the gate killed. A pair that scored high
            # and was cancelled is the first place a wrong rule shows up.
            cancelled.append(match)
            continue

        # Counted before the truncation below, because the truncation is the
        # whole reason the count has to be kept separately.
        if match.score >= soft:
            total_similar += 1

        exact.append((match.score, match))
        exact.sort(key=lambda pair: -pair[0])
        del exact[limit:]

    matches.extend(m for _s, m in exact)
    matches.sort(key=lambda m: (-m.score, m.disabled, m.item_code))
    del matches[limit:]

    cancelled.sort(key=lambda m: -m.components.get("raw_score", 0))
    del cancelled[TOP_N_CANCELLED:]

    top_band = matches[0].band if matches else "none"
    return {
        "matches": matches,
        "cancelled": cancelled,
        "candidates": len(candidates),
        "total_similar": total_similar,
        "band": top_band,
        "via": matches[0].via if matches else "none",
    }
