"""
makes.py  --  turn 1,438 spellings of a manufacturer into one name each.

WHY THIS EXISTS
---------------
`makes` is the second most useful field on the Item doctype. Measured
separation (how often it agrees in confirmed duplicates minus how often it
agrees in confirmed non-duplicates):

    custom_reference_old_item_code   +80.2   but only 4% filled
    makes                            +60.0   <- this one
    gst_hsn_code                     +33.1
    stock_uom                        -23.4   points the WRONG way

But it only earns that +60 once the spellings are collapsed. On site_local:

    thermo                      454     tarsons   489
    thermo scientific           352     tarson    435   <- same company
    thermo fisher scientific    236
    thermofisher                192
    thermofisher scientific     135
    thermo scientific(tm)       112
    thermo fisher               106
    gibco/thermo                 76

Compared raw, "Thermo" and "Thermo Scientific" disagree, so two rows of the
same product score as different manufacturers and the field actively hurts.

HOW IT WORKS
------------
Two stages, both deliberately dumb:

  1. NORMALISE   lower case, drop trademark decoration, punctuation to
                 spaces, collapse. This alone merges "sigma-aldrich" and
                 "sigma aldrich".

  2. LOOK UP     an exact-match table of spelling -> canonical name, held in
                 config/makes_alias.json.

The table is EXACT MATCH ON PURPOSE. A substring rule ("anything containing
bd") looks tidy and then quietly maps "abdominal" to Becton Dickinson. Every
mapping in the file is a spelling somebody really typed, generated from the
data by scripts/build_makes_alias.py and reviewed by a human.

A spelling that is not in the table is its own canonical name. That is the
safe default: an unmapped manufacturer can still agree with itself, it just
cannot agree with a different spelling of itself yet.

NO FRAPPE IMPORT
----------------
The table is a JSON file on disk, not a doctype, so this module stays pure
and the harness can score pairs without a site. It is still configuration --
edit the JSON and the behaviour changes with no deploy and no code review.
"""

import json
import os
import re

#: config/makes_alias.json, relative to this file. Resolved once at import.
ALIAS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "makes_alias.json")

#: Decoration, the same characters normalize.py strips from item names.
_TRADEMARK = re.compile(r"[™®©]")

#: Everything that is not a letter or a digit becomes a space, so
#: "sigma-aldrich", "sigma aldrich" and "Sigma/Aldrich" converge.
_PUNCT = re.compile(r"[^a-z0-9]+")

#: What people type into Makes when they do not know the manufacturer.
#: Measured on site_local: "0" x140, "na" x69. These must behave as EMPTY,
#: not as a manufacturer -- otherwise 140 unrelated items all "agree".
JUNK = frozenset((
    "", "0", "00", "na", "n a", "n/a", "nil", "none", "null", "no",
    "not available", "notavailable", "-", "--", ".", "x", "xx",
    "unknown", "other", "others", "misc", "miscellaneous", "local",
    "generic", "tbd", "any",
))


def normalize_makes(value) -> str:
    """Comparable form of a Makes value, or "" if it says nothing.

        "Thermo Scientific(tm)" -> "thermo scientific"
        "Sigma-Aldrich"         -> "sigma aldrich"
        "0"                     -> ""
    """
    if value is None:
        return ""
    text = _TRADEMARK.sub(" ", str(value)).lower()
    text = " ".join(_PUNCT.sub(" ", text).split())
    if text in JUNK:
        return ""
    return text


def load_aliases(path: str = None) -> dict:
    """Read the alias table. A missing or broken file is not fatal.

    If the file cannot be read we return an empty table, which degrades the
    makes component to exact-spelling agreement. That is worse, but it is not
    broken -- and a duplicate checker must never be the reason an Item cannot
    be saved (HARD RULE 1).
    """
    try:
        with open(path or ALIAS_PATH, "r") as handle:
            payload = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}

    table = payload.get("alias", payload)
    if not isinstance(table, dict):
        return {}
    # Normalise the keys as well, so a hand-edited entry like
    # "Thermo Scientific" still matches.
    return {normalize_makes(k): str(v).strip().lower()
            for k, v in table.items()
            if normalize_makes(k) and v}


#: Loaded once per process. The file is a few kilobytes and changes rarely.
#: reload_aliases() exists for the harness and for anybody editing the file
#: on a running bench.
_ALIASES = load_aliases()


def reload_aliases(path: str = None) -> dict:
    """Re-read the table from disk and return it."""
    global _ALIASES
    _ALIASES = load_aliases(path)
    return _ALIASES


def canonical(value, aliases: dict = None) -> str:
    """The one agreed name for this manufacturer, or "" if unknown.

        "Thermo Fisher Scientific" -> "thermo"
        "Gibco/Thermo"             -> "thermo"
        "Gibco"                    -> "gibco"
        "0"                        -> ""
        "Some New Vendor"          -> "some new vendor"   (its own name)
    """
    spelling = normalize_makes(value)
    if not spelling:
        return ""
    table = _ALIASES if aliases is None else aliases
    return table.get(spelling, spelling)


def agreement(makes_a, makes_b, aliases: dict = None):
    """Do two Makes values describe the same manufacturer?

    Returns 1.0 for agree, 0.0 for disagree, and None to ABSTAIN when either
    side is blank or junk.

    The abstention is the important part. 29% of items carry no usable Makes
    value. Scoring a blank as 0.0 would quietly cap those items below the
    strong threshold forever, so a blank must remove the component from the
    weighted sum instead of voting against the match. scorer.py renormalises
    the remaining weights when this returns None.
    """
    a, b = canonical(makes_a, aliases), canonical(makes_b, aliases)
    if not a or not b:
        return None
    return 1.0 if a == b else 0.0
