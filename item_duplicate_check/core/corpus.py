"""
corpus.py  --  find the handful of items worth comparing against.

WHAT THIS FILE DOES
-------------------
Scoring a pair is cheap. Scoring 22,204 pairs on every keystroke is not. This
file answers one question fast:

    "given this name, which existing items could possibly be the same thing?"

It returns a shortlist, usually a few dozen rows. core/scorer.py then does the
expensive work on just those.

HOW IT WORKS, TECHNICALLY
-------------------------
A rare-word index. For every item we keep its four rarest words and build an
inverted index from word -> items. A lookup normalises the typed name, takes
its rare words, and unions the posting lists.

    "Nunc 96-Well Plate"  ->  rare words {nunc}
                          ->  the 23 items that contain "nunc"

Why rare words and not all words: "plate" appears in hundreds of names, so it
points at hundreds of items and tells you nothing. "nunc" appears in a few and
points straight at them. Document frequency is how we tell the two apart --
no ML, just counting.

WHY NOT NARROW BY CATEGORY -- MEASURED
--------------------------------------
    Item Child Group filter    recall@5 24.7%    48.5 ms
    rare-word index            recall@5 89.7%     4.7 ms   <- chosen
    full catalogue scan        recall@5 89.8%    24.9 ms

The people who created the duplicates filed them under different categories:
80.5% of true duplicate pairs sit in different child groups. Filtering by
category throws the right answer away before scoring ever starts. The index is
both more accurate and five times faster.

THE FALLBACK
------------
A name with fewer than two usable words ("CPU", "WFI") produces no useful
posting list, so the lookup degrades to a full scan. At 24.9 ms that is still
far inside the 300 ms budget, so there is no need to be clever about it.

CAT NO
------
Step 0 of the search is an exact Cat No lookup, so this file also keeps a
dict of cleaned Cat No -> items. Cat No is a KEY here and nothing else; it is
never handed to the scorer. See decision 6 in README.md.

Junk must be removed before that dict is built. 172 items carry the Cat No
"0" and 133 carry "na". Left in, the "0" bucket alone manufactures 14,706
phantom duplicate pairs.

WHAT IS PURE AND WHAT IS NOT
----------------------------
Everything above `build_corpus()` is pure Python and unit-testable without a
site. Only the three functions at the bottom touch Frappe: they read rows,
hold the built corpus in a process-local cache, and listen for invalidation.
Keeping the split sharp is what lets the harness in scripts/ run the real
production code path against a plain list of dicts.
"""

import math
import re

from item_duplicate_check.core.normalize import normalize

# --------------------------------------------------------------------------
# Index tuning. Module constants, not settings: these change the shape of the
# index, so a change means a rebuild, not a live tweak. The scoring weights
# and the warning thresholds are the things that belong in Settings.
# --------------------------------------------------------------------------

#: How many of an item's rarest words go into the index. Four is enough to
#: survive one or two words being typed differently, and keeps the posting
#: lists short. Measured recall@5 89.7% at this setting.
RARE_WORDS_PER_ITEM = 4

#: A word this common is useless as a lookup key: "plate", "sterile", "tips".
#: Expressed as a share of the catalogue so it scales with the data, with an
#: absolute floor so a tiny test corpus does not treat every word as common.
DF_CEILING_RATIO = 0.01
DF_CEILING_FLOOR = 25

#: Below this many usable words we stop trusting the index and scan everything.
MIN_USABLE_WORDS = 2

#: Values people type into Cat No when they have no Cat No. Measured on
#: site_local: "0" x172, "na" x133, "-" x46, "custom" x16, "n a" x10.
#: Each of these is one fake group, and a fake group of n items invents
#: n*(n-1)/2 fake duplicate pairs.
CAT_NO_JUNK = frozenset((
    "0", "00", "000", "0000",
    "na", "n a", "n.a", "n.a.", "n/a", "na.",
    "nil", "none", "null", "no", "not available", "notavailable",
    "-", "--", "---", ".", "..", "x", "xx", "xxx",
    "custom", "generic", "local", "tbd", "asp", "as per", "aspec",
))

#: Anything shorter than this cannot identify a product, whatever it says.
#: Catches further placeholder spellings without needing them listed.
CAT_NO_MIN_LENGTH = 3

#: Cat No comparison is exact, but not literal: people type "B-1500S",
#: "B1500S" and "b 1500 s" for the same number. Separators carry no meaning
#: in a catalogue number, so they are removed before comparing.
_CAT_NO_STRIP = re.compile(r"[^a-z0-9]+")


# ==========================================================================
# Pure functions -- no Frappe, no database
# ==========================================================================

def clean_cat_no(value) -> str:
    """Comparable form of a Cat No, or "" if the value is junk.

        "B-1500S"  -> "b1500s"
        "b 1500 s" -> "b1500s"
        "0"        -> ""          junk
        "na"       -> ""          junk
        None       -> ""

    Returning "" for junk means callers never have to remember the junk list;
    an empty string simply never matches anything.
    """
    if value is None:
        return ""

    text = str(value).strip().lower()
    if not text:
        return ""

    # Judge the junk list on the text as typed, so "n a" and "not available"
    # are caught before their spaces are stripped away.
    if text in CAT_NO_JUNK:
        return ""

    squashed = _CAT_NO_STRIP.sub("", text)
    if squashed in CAT_NO_JUNK:
        return ""
    if len(squashed) < CAT_NO_MIN_LENGTH:
        return ""
    # "0", "00", "0000" and friends in any separator spelling
    if squashed.strip("0") == "":
        return ""

    return squashed


def document_frequency(token_lists) -> dict:
    """How many items each word appears in.

    One pass over the catalogue. A word is counted once per item, not once per
    occurrence, because we care how many items a word can point at.
    """
    df = {}
    for tokens in token_lists:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    return df


def df_ceiling(corpus_size: int) -> int:
    """The document frequency above which a word is too common to look up."""
    return max(DF_CEILING_FLOOR, int(corpus_size * DF_CEILING_RATIO))


def usable_tokens(tokens, df: dict, ceiling: int) -> list:
    """The words of a name that are selective enough to be lookup keys.

    A word unknown to the index (df 0) is kept: it is as rare as it gets, and
    it is exactly the kind of word a brand new spelling arrives with.
    """
    return [t for t in dict.fromkeys(tokens) if df.get(t, 0) <= ceiling]


def rare_tokens(tokens, df: dict, ceiling: int, limit: int = RARE_WORDS_PER_ITEM) -> tuple:
    """The `limit` rarest usable words of a name, rarest first.

    Sorted by (document frequency, word) so the result is deterministic -- two
    words with the same frequency must always come out in the same order, or
    the index would differ between rebuilds for no reason.
    """
    candidates = usable_tokens(tokens, df, ceiling)
    candidates.sort(key=lambda t: (df.get(t, 0), t))
    return tuple(candidates[:limit])


class ItemRow(object):
    """One catalogue row, pre-chewed for matching.

    __slots__ because there are 22,204 of these and they live in memory for
    the life of the worker.

    `description` is carried for display and logging only. It is never scored:
    80.9% of descriptions are a near-copy of the name, so scoring it would
    double-count the name and nothing else (README decision 7).

    WHY SO MUCH IS PRECOMPUTED
    --------------------------
    token_set, sorted_key and idf_sum are all derivable from `tokens`, and
    deriving them per comparison is what made the first version of the search
    too slow to ship. On a fallback full scan the scorer touches every one of
    the 22,204 rows, so anything recomputed in that loop is multiplied by
    22,204: building the token set and summing its inverse document
    frequencies on the fly cost 3.8 SECONDS for one query. Hoisted here they
    are computed once per rebuild. Measured in scripts/latency_check.py.
    """

    __slots__ = ("item_code", "item_name", "normalized", "tokens", "rare",
                 "cat_no", "makes", "description", "disabled", "is_stock_item",
                 "token_set", "sorted_key", "idf_sum",
                 "hsn", "department", "requester", "naming_series", "old_code")

    def __init__(self, item_code, item_name, cat_no=None, makes=None,
                 description=None, disabled=0, is_stock_item=1,
                 hsn=None, department=None, requester=None,
                 naming_series=None, old_code=None):
        self.item_code = item_code
        self.item_name = item_name or ""
        self.normalized = normalize(self.item_name)
        self.tokens = tuple(self.normalized.split())
        self.rare = ()                      # filled in by build_corpus()
        self.cat_no = clean_cat_no(cat_no)
        self.makes = (makes or "").strip()
        self.description = description or ""
        self.disabled = int(disabled or 0)
        self.is_stock_item = int(is_stock_item or 0)

        # ---- fields the MODEL uses, not the index -----------------------
        # The index never reads these; core/features.py does. They live here
        # because a second structure holding half an item's fields is the
        # kind of thing that silently falls out of step.
        #
        # Measured separation (agrees in duplicates minus agrees in
        # non-duplicates), from decision 7:
        #     hsn        +33.1      department   +28.2
        #     requester  +27.4      naming_series -11.1  (INVERTED signal)
        #
        # old_code is carried for GROUPING only -- it is one of the three
        # signals that build the product groups, so using it as a feature
        # would be label leakage. core/features.py must never read it.
        self.hsn = (hsn or "").strip()
        self.department = (department or "").strip()
        self.requester = (requester or "").strip()
        self.naming_series = (naming_series or "").strip()
        self.old_code = (old_code or "").strip()

        #: distinct words, for set overlap
        self.token_set = frozenset(self.tokens)
        #: words sorted and rejoined -- the string the character-level
        #: comparison in scorer.ordered_similarity() actually works on.
        #: Built from `tokens`, not `token_set`, so it matches that function's
        #: contract exactly: a word repeated in a name is compared twice.
        self.sorted_key = " ".join(sorted(self.tokens))
        #: total inverse document frequency of this row's words. Filled in by
        #: build_corpus(), which is the only place that knows the frequencies.
        self.idf_sum = 0.0

    def __repr__(self):
        return "ItemRow(%s, %r%s)" % (
            self.item_code, self.item_name[:40],
            ", disabled" if self.disabled else "")


class Corpus(object):
    """The built index. Read-only once constructed.

    Rebuilt rather than mutated. An index that can be edited in place drifts
    out of step with the table it describes, and the whole thing takes well
    under a second to build from scratch.
    """

    __slots__ = ("rows", "df", "ceiling", "inverted", "cat_no_map",
                 "generation", "idf", "default_idf")

    def __init__(self, rows, df, ceiling, inverted, cat_no_map, generation=0,
                 idf=None, default_idf=None):
        self.rows = rows
        self.df = df
        self.ceiling = ceiling
        self.inverted = inverted
        self.cat_no_map = cat_no_map
        self.generation = generation
        #: word -> log(N / df). Precomputed because the scorer needs it for
        #: every word of every candidate, and math.log in that loop was
        #: measurable (see the note on ItemRow).
        self.idf = idf if idf is not None else {}
        #: what a word nobody has used before is worth: the maximum, because
        #: a brand new spelling is the rarest thing there is.
        self.default_idf = (default_idf if default_idf is not None
                            else math.log(max(len(rows), 2)))

    def idf_of(self, token) -> float:
        """Inverse document frequency of one word, unseen words included."""
        return self.idf.get(token, self.default_idf)

    # -- Step 0 -----------------------------------------------------------
    def lookup_cat_no(self, cat_no) -> list:
        """Items carrying exactly this Cat No. Junk returns nothing."""
        cleaned = clean_cat_no(cat_no)
        if not cleaned:
            return []
        return list(self.cat_no_map.get(cleaned, ()))

    # -- candidate generation ---------------------------------------------
    def candidates(self, name: str, exclude_item_code=None) -> list:
        """The shortlist for `name`.

        Union of the posting lists of the typed name's usable words. Falls
        back to the whole catalogue when the name has fewer than two of them,
        which is the honest answer: with nothing selective to look up, the
        index has no opinion and the full scan is cheap enough.
        """
        tokens = normalize(name).split()
        keys = usable_tokens(tokens, self.df, self.ceiling)

        if len(keys) < MIN_USABLE_WORDS:
            found = self.rows
        else:
            seen = set()
            found = []
            for key in keys:
                for row in self.inverted.get(key, ()):
                    if id(row) not in seen:
                        seen.add(id(row))
                        found.append(row)

        if exclude_item_code:
            return [r for r in found if r.item_code != exclude_item_code]
        return list(found)

    def __len__(self):
        return len(self.rows)

    def __repr__(self):
        return ("Corpus(%d items, %d words, df ceiling %d, %d cat numbers)"
                % (len(self.rows), len(self.df), self.ceiling,
                   len(self.cat_no_map)))


def build_corpus(rows, generation: int = 0) -> Corpus:
    """Build the index from ItemRow objects. Pure: no Frappe, no I/O.

    Two passes, because the rarest words of an item cannot be known until
    every item has been counted.

    ROW ORDER MATTERS. Rows are sorted enabled-before-disabled here, once, so
    that every posting list is already in preference order and no caller has
    to re-sort. Disabled items are KEPT: a disabled duplicate is still the
    answer to "does this already exist?", it just should not outrank a live
    one.
    """
    rows = sorted(rows, key=lambda r: (r.disabled, r.item_code))

    # pass 1 -- count
    df = document_frequency(r.tokens for r in rows)
    ceiling = df_ceiling(len(rows))

    # the weight of every known word, computed once
    size = max(len(rows), 2)
    default_idf = math.log(size)
    idf = {token: math.log(size / float(max(count, 1)))
           for token, count in df.items()}

    # pass 2 -- pick each item's rare words and post it under them
    inverted = {}
    cat_no_map = {}
    for row in rows:
        row.rare = rare_tokens(row.tokens, df, ceiling)
        row.idf_sum = sum(idf.get(t, default_idf) for t in row.token_set)
        for token in row.rare:
            inverted.setdefault(token, []).append(row)
        if row.cat_no:
            cat_no_map.setdefault(row.cat_no, []).append(row)

    return Corpus(rows, df, ceiling, inverted, cat_no_map, generation,
                  idf=idf, default_idf=default_idf)


def rows_from_dicts(records) -> list:
    """ItemRow list from plain dicts.

    The bridge that lets the harness and the unit tests feed the real index
    without a database. Accepts the column names the Item table uses.
    """
    return [
        ItemRow(
            item_code=r.get("name") or r.get("item_code"),
            item_name=r.get("item_name"),
            cat_no=r.get("cat_no"),
            makes=r.get("makes"),
            description=r.get("description"),
            disabled=r.get("disabled", 0),
            is_stock_item=r.get("is_stock_item", 1),
            hsn=r.get("hsn") or r.get("gst_hsn_code") or r.get("HSN/SAC"),
            department=r.get("department") or r.get("Department"),
            requester=(r.get("requester")
                       or r.get("custom_item_creation_requester_")),
            naming_series=r.get("naming_series"),
            old_code=(r.get("old_code")
                      or r.get("custom_reference_old_item_code")
                      or r.get("Reference Old Item Code")),
        )
        for r in records
    ]


# ==========================================================================
# Frappe-backed cache. Everything below this line needs a site.
# ==========================================================================

#: Process-local. Each gunicorn worker and each background worker builds its
#: own copy; building is sub-second, so that is cheaper than serialising a
#: 22,000-row index in and out of Redis on every request.
_CACHE = {"corpus": None, "generation": None}

#: Redis key holding an integer that is bumped whenever an Item changes.
#: This is the only cross-process signal: a worker compares the number it
#: built against the current one and rebuilds if it has fallen behind. It is
#: a counter rather than a flag so that two workers invalidating at the same
#: moment cannot cancel each other out.
GENERATION_KEY = "item_duplicate_check:corpus_generation"

#: Columns read from tabItem. The first group is what the INDEX needs; the
#: second is what the MODEL needs (decision 7's separation table).
#:
#: Still deliberately short. stock_uom is excluded because its separation is
#: -23.4 -- in confirmed duplicates the unit disagrees 52.4% of the time, so
#: it points the wrong way. item_group, item_child_group, brand and
#: description are excluded too; description is display only.
ITEM_FIELDS = ("name", "item_name", "cat_no", "makes", "description",
               "disabled", "is_stock_item",
               "gst_hsn_code", "department", "custom_item_creation_requester_",
               "naming_series", "custom_reference_old_item_code")


def _generation(frappe) -> int:
    """Current index generation, creating the key on first use."""
    value = frappe.cache().get_value(GENERATION_KEY)
    if value is None:
        frappe.cache().set_value(GENERATION_KEY, 1)
        return 1
    try:
        return int(value)
    except (TypeError, ValueError):
        # A corrupt value must not take the search down; treat it as new.
        frappe.cache().set_value(GENERATION_KEY, 1)
        return 1


def invalidate():
    """Mark the index stale for every worker. Called from the Item hooks.

    Cheap on purpose -- one Redis increment. The rebuild happens lazily in
    whichever worker next serves a check, so an Item save never pays for it.
    """
    import frappe

    try:
        frappe.cache().set_value(GENERATION_KEY, _generation(frappe) + 1)
    except Exception:
        # Invalidation is best-effort. A missed bump means a worker serves a
        # slightly stale index until the nightly rebuild, which is a far
        # better outcome than an Item failing to save.
        frappe.log_error(title="item_duplicate_check: index invalidate failed")


def load_rows(frappe, include_non_stock: bool = True) -> list:
    """Read the catalogue. One query, no ORM.

    Non-stock rows are loaded by default so that the index can still EXPLAIN a
    non-stock item if asked. HARD RULE 2 is enforced at the entry point in
    api.py, not here, so the skip is one obvious line in one obvious place.
    """
    records = frappe.db.sql(
        """
        SELECT  `name`, `item_name`, `cat_no`, `makes`, `description`,
                `disabled`, `is_stock_item`,
                `gst_hsn_code`                      AS hsn,
                `department`                        AS department,
                `custom_item_creation_requester_`   AS requester,
                `naming_series`                     AS naming_series,
                `custom_reference_old_item_code`     AS old_code
        FROM    `tabItem`
        """ + ("" if include_non_stock else "WHERE IFNULL(is_stock_item, 0) = 1"),
        as_dict=True,
    )
    return rows_from_dicts(records)


def get_corpus(force: bool = False) -> Corpus:
    """The index, built if needed. This is what api.py calls.

    Rebuilds when the Redis generation has moved past the cached one, or when
    `force` is set by the nightly job.
    """
    import frappe

    generation = _generation(frappe)
    if (force
            or _CACHE["corpus"] is None
            or _CACHE["generation"] != generation):
        _CACHE["corpus"] = build_corpus(load_rows(frappe), generation)
        _CACHE["generation"] = generation
    return _CACHE["corpus"]


def rebuild():
    """Force a rebuild in this process and bump every other worker.

    Wired to a nightly scheduler event. The hooks already invalidate on every
    Item change, so this is a safety net for anything that writes to tabItem
    without going through the document API -- a patch, a bulk import, a direct
    SQL fix.
    """
    import frappe

    invalidate()
    corpus = get_corpus(force=True)
    frappe.logger("item_duplicate_check").info("corpus rebuilt: %r" % (corpus,))
    return corpus
