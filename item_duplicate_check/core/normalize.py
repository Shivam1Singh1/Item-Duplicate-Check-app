"""
normalize.py  --  make two item names comparable.

WHAT THIS FILE DOES
-------------------
The same product gets typed differently by different people:

    "Nunc(tm) 96-Well Plate "      and      "nunc 96 well plate."

To a computer those are two unrelated strings. This file rewrites both into one
agreed form so the matcher can see they are the same:

    "nunc 96well plate"

Nothing here knows about Frappe, the database or duplicates. It is pure text in,
pure text out, so it can be unit-tested in milliseconds without a site.

HOW IT WORKS, TECHNICALLY
-------------------------
A fixed pipeline of small rewrites. Every rule was added because that exact
problem was measured in the real catalogue, and the ORDER MATTERS -- several
rules only work if an earlier one has already run:

  1. repair mojibake       "0.22Amu m"  -> must happen BEFORE the micro-sign rule,
                           otherwise the stray A is left behind as a word
  2. Unicode NFKC          folds compatibility characters (full-width digits,
                           ligatures) into their plain equivalents
  3. non-breaking space    U+00A0 looks like a space but str.split() does not
                           break on it, so words silently fuse together
  4. drop (tm)(r)(c)       12.7% of names carry one; they are decoration
  5. micro sign -> "u"     two different code points both render as the same
                           glyph, so they must be unified
  6. lower case            solves DNase / DNAse without any phonetic trickery
  7. join number + unit    "50 ml" and "96-Well" -> "50ml" and
                           "96well", so word-order scorers treat the
                           measurement as ONE token, not two
  8. strip punctuation     keep digits, letters, "." and "%" -- the dot is
                           needed for decimals, the percent for grades
  9. drop a trailing dot   5.3% of names end in a stray full stop
 10. collapse whitespace   1.7% have double spaces, 0.9% have edge spaces

WHY THE ORDER IS LOCKED
-----------------------
Rule 7 must run after rule 6 (the unit list is lowercase) and before rule 8
(which would delete the space it needs to see). Rule 1 must run before rule 5.
Changing the order silently changes the output -- do not reorder without
re-running the tests.

MEASURED IN THE CATALOGUE (25,590 items)
----------------------------------------
    trademark symbols      3,249  (12.7%)
    trailing full stop     1,369  ( 5.3%)
    micro sign               540  ( 2.1%)
    double spaces            428  ( 1.7%)
    non-breaking space       237  ( 0.9%)
    mojibake                   8  ( 0.0%)
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# Lookup tables. Module level so they are compiled once, not per call --
# normalize() runs tens of thousands of times when the index is built.
# --------------------------------------------------------------------------

NBSP = " "

#: Characters that are pure decoration in a product name.
TRADEMARK = re.compile(r"[™®©]")       # (tm) (r) (c)

#: Two different code points that both display as the micro sign.
MICRO = re.compile(r"[µμ]")                 # MICRO SIGN, GREEK MU

#: UTF-8 micro sign read as Latin-1 produces this two-character sequence.
MOJIBAKE = {
    "Âµ": "µ",   # Amu  -> micro
    "Ã—": "x",        # A-   -> multiplication sign
    "Â°": " degree ", # A deg
}

#: Units that may legally be glued to the number in front of them.
#: Kept deliberately narrow -- adding a common English word here would fuse
#: unrelated tokens (e.g. "2 in" is a size, "2 inch pipe in stock" is not).
_UNITS = (
    "ml|l|ul|mcl|mg|g|kg|gm|gms|ng|ug|mcg"      # volume and mass
    "|mm|cm|m|nm|um"                            # length
    "|kda|da"                                   # molecular weight
    "|well|wells|pk|pack|nos|pcs|rxn|iu|u"      # counts and activity
)
#: The separator between a number and its unit. A plain space is the common
#: case, but "96-Well" and "0.22-um" are written with a hyphen, and some rows
#: carry a typographic dash. All of them mean the same thing, so all of them
#: must be absorbed -- otherwise "96-Well Plate" normalises to "96 well plate"
#: and the measurement is two tokens again, which is exactly what rule 7
#: exists to prevent. Found by the STEP B test of the documented example
#: "Nunc(tm) 96-Well Plate " -> "nunc 96well plate".
_SEP = r"[\s\-\u2010-\u2015]+"

UNIT_JOIN = re.compile(r"(\d)" + _SEP + r"(" + _UNITS + r")\b")

#: "70 %" -> "70%". Percentages are grade markers, and specs.py expects the
#: sign attached to the number.
PCT_JOIN = re.compile(r"(\d)" + _SEP + r"%")

#: Everything that is not a letter, digit, dot or percent becomes a space.
PUNCT = re.compile(r"[^a-z0-9.%]+")

#: One or more dots hanging off the end of the whole string.
TRAILING_DOT = re.compile(r"\.+$")


def normalize(text: str) -> str:
    """Rewrite an item name into the one agreed comparable form.

    Deterministic and side-effect free: the same input always gives the same
    output, and nothing outside this function is touched.
    """
    # ---- step 0 -----------------------------------------------------------
    # Accept None, numbers, NaN -- callers read straight from a spreadsheet,
    # so we must not assume we were handed a string.
    if text is None:
        return ""
    s = str(text)

    # ---- step 1: repair mojibake -----------------------------------------
    # Must run first. Once step 5 turns the micro sign into "u", the stray
    # "A" in front of it is stranded and becomes a meaningless word.
    for bad, good in MOJIBAKE.items():
        if bad in s:
            s = s.replace(bad, good)

    # ---- step 2: drop trademark decoration -------------------------------
    # MUST run before NFKC. NFKC expands U+2122 into the two letters "TM",
    # which then glue onto the previous word: "Nunc(tm)" -> "nunctm". Caught
    # by the self-test at the bottom of this file.
    s = TRADEMARK.sub(" ", s)

    # ---- step 3: Unicode NFKC --------------------------------------------
    # Folds compatibility forms into plain ones, so full-width digits and
    # ligatures compare equal to the characters people actually type.
    s = unicodedata.normalize("NFKC", s)

    # ---- step 4: non-breaking space --------------------------------------
    # Invisible, and str.split() does NOT treat it as a separator, so without
    # this "0.22 um" stays one glued token and never matches "0.22um".
    s = s.replace(NBSP, " ")

    # ---- step 5: unify the micro sign ------------------------------------
    # Done before lower-casing so both the Latin-1 and Greek code points are
    # caught by one rule.
    s = MICRO.sub("u", s)

    # ---- step 6: lower case ----------------------------------------------
    # This alone solves DNase / DNAse / dnase. It is also why the unit list
    # in step 7 can be written in lower case only.
    s = s.lower()

    # ---- step 7: glue a number to its unit -------------------------------
    # "50 ml" -> "50ml". Word-order-insensitive scorers compare bags of
    # tokens; leaving "50" and "ml" apart lets "50 ml" match "10 ml" on the
    # shared "ml" token, which is noise. As one token it cannot.
    s = UNIT_JOIN.sub(r"\1\2", s)
    s = PCT_JOIN.sub(r"\1%", s)

    # ---- step 8: strip punctuation ---------------------------------------
    # Dots and percent signs survive: "0.22" and "70%" carry real meaning
    # that the size and grade checks in specs.py depend on.
    s = PUNCT.sub(" ", s)

    # ---- step 9: drop a trailing full stop -------------------------------
    # Applied per token, because step 8 has already split the string, so a
    # name ending "Buffer ." leaves a lone dot behind.
    s = " ".join(TRAILING_DOT.sub("", w) for w in s.split())

    # ---- step 10: collapse whitespace ------------------------------------
    # split()/join() removes runs of spaces and both edges in one pass.
    return " ".join(s.split())


def tokens(text: str) -> list:
    """Normalised name as a list of words. Convenience for the scorers."""
    return normalize(text).split()


def is_meaningful(text: str, min_tokens: int = 2) -> bool:
    """True if there is enough text to search on.

    Very short names ("CPU", "WFI") produce no rare word, so the inverted
    index returns nothing and the caller must fall back to a full scan.
    """
    return len(tokens(text)) >= min_tokens


if __name__ == "__main__":
    # Real rows from the catalogue, left to right: input -> output.
    SAMPLES = [
        "Nunc™ 96-Well Plate",
        "  Wide mouth bottle 1L ",
        "ACK Lysis Buffer.",
        "0.22 µm PVDF",
        "Millex,33mm,0.22Âµm,Dur.(PVDF)",
        "Serological Pipette 50 ml",
        "Sterile IPA 70 %,  1L",
        "DNase I, RNase-free (1 U/µL)",
        "CTS™ AIM-V™ Medium, without phenol red",
    ]
    width = max(len(repr(s)) for s in SAMPLES)
    for raw in SAMPLES:
        print("%-*s  ->  %s" % (width, repr(raw), normalize(raw)))
