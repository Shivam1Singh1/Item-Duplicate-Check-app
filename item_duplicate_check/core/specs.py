"""
specs.py  --  block matches between products that only LOOK alike.

WHAT THIS FILE DOES
-------------------
Text similarity cannot tell these apart, and every pair below is two
DIFFERENT products:

    Glycine 500g                vs  Glycine 1kg
    Glass bent inlet adaptor B-34 vs B-24
    Sartocon Slice Cassette 100kDa vs 50kDa
    Festo Push-in T-connector QST-6 vs QST-8
    Dengue virus strain 2       vs  strain 1
    TrypLE Express no phenol red vs Phenol Red

This file pulls the technical specification out of a name and cancels the
match when the two specs disagree. The cancel OVERRIDES the similarity score
completely -- a pair can look 100% identical and still be rejected.

This is the single highest-value module in the app. Measured on the catalogue:

    no spec checking at all           precision 78.1%
    size and grade only               precision 81.4%
    all four checks (this file)       precision 91.0%

HOW IT WORKS, TECHNICALLY
-------------------------
Four independent checks. Each returns a reason string if it wants to cancel,
otherwise None. veto() runs them in cheapest-first order and stops at the
first hit.

  1. SIZE        Parse every "number + unit" into (dimension, magnitude in a
                 base unit) so 1kg and 1000g compare equal. Cancel only when a
                 dimension present on BOTH sides holds different values.

  2. SPEC CODES  Any token containing a digit that is not a recognised unit --
                 b34, qst6, strain2. Group them by their letter stem and
                 cancel when a shared stem carries different numbers.

  3. NEGATION    "no", "without", "non", "free", "reduced", "anti".
                 Different negation sets mean opposite products.

  4. GRADE       absolute, HPLC, sterile, RNase-free, 70% and friends.
                 Same chemical, different purity = different item.

THE TWO RULES THAT STOP IT OVER-FIRING
--------------------------------------
ABSTENTION -- a check only fires when BOTH names have something to compare.
If one name has no size at all, the size check stays out of it. Without this
rule "Paraformaldehyde" could never match "Paraformaldehyde for synthesis",
and that class of match is one of the most valuable we have.

CONFLICT, NOT DIFFERENCE -- we compare per dimension and per stem, never whole
sets. An earlier version compared size sets for equality and wrongly killed:

    {0.22um, 33mm}  vs  {0.22um}      same product, one name is just fuller

There is no dimension where those disagree, so the current rule allows it.

MAINTAINING THIS FILE
---------------------
Every false warning seen in production becomes a test case in
tests/test_specs.py FIRST, then the rule is fixed. Never patch a rule without
leaving a test behind -- that is what stops an old mistake coming back.
"""

import re

from item_duplicate_check.core.normalize import normalize

# --------------------------------------------------------------------------
# Unit table: spelling -> (dimension, multiplier into the base unit)
# Dimensions never convert into each other. 5 ml and 5 g are not comparable,
# so they simply never meet in the same bucket.
# --------------------------------------------------------------------------
UNITS = {
    # volume, base = millilitre
    "ml": ("volume", 1.0), "milliliter": ("volume", 1.0), "millilitre": ("volume", 1.0),
    "l": ("volume", 1000.0), "lt": ("volume", 1000.0), "liter": ("volume", 1000.0),
    "litre": ("volume", 1000.0),
    "ul": ("volume", 0.001), "mcl": ("volume", 0.001), "microlitre": ("volume", 0.001),

    # mass, base = gram
    "g": ("mass", 1.0), "gm": ("mass", 1.0), "gms": ("mass", 1.0), "gram": ("mass", 1.0),
    "grams": ("mass", 1.0),
    "mg": ("mass", 0.001), "kg": ("mass", 1000.0), "kgs": ("mass", 1000.0),
    "ug": ("mass", 1e-6), "mcg": ("mass", 1e-6), "microgram": ("mass", 1e-6),
    "ng": ("mass", 1e-9),

    # length, base = millimetre
    "mm": ("length", 1.0), "cm": ("length", 10.0), "m": ("length", 1000.0),
    "um": ("length", 0.001), "nm": ("length", 1e-6),

    # molecular weight, base = dalton
    "kda": ("mw", 1000.0), "da": ("mw", 1.0),

    # counts and activity
    "well": ("count", 1.0), "wells": ("count", 1.0),
    "pk": ("count", 1.0), "pack": ("count", 1.0),
    "nos": ("count", 1.0), "pcs": ("count", 1.0), "rxn": ("count", 1.0),
    "u": ("activity", 1.0), "iu": ("activity", 1.0),
}

#: "500g", "0.22um", "96well" -- a number immediately followed by letters.
#: normalize() has already glued these together, so no space is allowed here.
#:
#: THE LOOKBEHIND IS LOAD-BEARING (added in STEP B).
#: Without it the pattern happily reads the tail of a catalogue code as a
#: measurement: "b1500l" yields ("1500", "l") -> 1500 litres. A bottle of
#: water then carried a 1.5-million-millilitre size, which collided with
#: anything that had a real volume, and the code itself was swallowed before
#: spec_codes() could see it. A measurement always starts its own token, so
#: refuse to start one anywhere except at the start of a token.
#:
#: Blocking only letters is not enough: re.findall shifts one character to the
#: right and matches the tail instead, so "b1500l" fails at the "1" and then
#: happily matches "500l" -- 500 litres. The class below blocks letters,
#: digits and the decimal dot, which leaves exactly one legal starting point
#: per token.
QTY = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)([a-z]+)")

#: A bare percentage such as "70%". Treated as its own dimension.
PCT = re.compile(r"(\d+(?:\.\d+)?)%")

#: Splits a spec code into its letter stem and its number: b34 -> ("b", 34)
STEM = re.compile(r"^([a-z]*?)(\d+(?:\.\d+)?)([a-z]*)$")

#: Words that flip the meaning of the product.
NEGATIONS = ("no", "without", "non", "free", "reduced", "anti")

#: Purity and grade markers. Matched against the name with spaces removed, so
#: "molecular biology" catches "molecular-biology" and "molecularbiology" too.
GRADES = (
    "absolute", "anhydrous", "molecularbiology", "hplc", "acs", "usp",
    "technical", "analytical", "reagentgrade", "foodgrade",
    "rnasefree", "dnasefree", "nucleasefree",
    "sterile", "nonsterile", "tissueculture", "cellculture",
)


# ==========================================================================
# Extraction -- turn a name into comparable structures
# ==========================================================================

def sizes(name: str) -> set:
    """Every measurement in the name as (dimension, magnitude in base unit).

        "Glycine 1kg"   -> {("mass", 1000.0)}
        "Glycine 500g"  -> {("mass", 500.0)}
        "Sterile IPA 70%, 1L" -> {("percent", 70.0), ("volume", 1000.0)}
    """
    text = normalize(name)
    found = set()

    # number + unit, converted into the dimension's base unit so that
    # 1kg and 1000g land on the same value and compare equal.
    for magnitude, unit in QTY.findall(text):
        entry = UNITS.get(unit)
        if entry:
            dimension, factor = entry
            # round() kills float noise: 0.001 * 500 must equal 0.5 exactly
            found.add((dimension, round(float(magnitude) * factor, 9)))

    # bare percentages get their own dimension; they never convert
    for magnitude in PCT.findall(text):
        found.add(("percent", round(float(magnitude), 4)))

    return found


def spec_codes(name: str) -> dict:
    """Digit-bearing tokens that are NOT measurements, grouped by letter stem.

        "B1500S"        -> {"b": {"1500s"}}
        "adaptor B-34"  -> {"": {"34"}}      hyphen, so two tokens
        "QST-6"         -> {"": {"6"}}       hyphen, so two tokens
        "strain 2"      -> {"": {"2"}}

    Note the hyphen cases. Rule 8 of normalize() turns punctuation into a
    space, so "B-34" reaches this function as the two tokens "b" and "34" and
    the letter stem is lost -- the number lands in the bare-number group "".
    That is still safe, because the pair we care about ("B-34" vs "B-24")
    compares 34 against 24 inside that one group and cancels. Only codes
    written WITHOUT a separator ("B1500S") keep their letter prefix.

    The 10 normalisation rules are locked, so this is documented rather than
    "fixed": adding a letter-digit join would be an eleventh rule, and the
    cancel already works without it.

    Grouping by stem is what makes the comparison safe. Two names can each
    carry extra codes; only a SHARED stem with different numbers is a real
    conflict.
    """
    groups = {}
    for token in normalize(name).split():
        if not any(ch.isdigit() for ch in token):
            continue

        match = STEM.match(token)
        if not match:
            continue
        prefix, number, suffix = match.groups()

        # Skip anything the size check already owns, or we would report the
        # same disagreement twice and lose the dimension-aware conversion.
        # Only a PURE measurement belongs to the size check: no letters in
        # front, and a trailing unit. "500g" is a measurement; "b1500l" has a
        # letter prefix, so it is a catalogue code that merely ends in
        # something that looks like a unit. Before STEP B this read
        # `suffix in UNITS or prefix in UNITS`, which threw b1500l away.
        if not prefix and suffix in UNITS:
            continue

        # GROUPING -- what counts as "the same slot" on both names.
        #
        # With a letter prefix, the prefix IS the slot and everything after it
        # is the value, suffix included:
        #     b1500s -> "b": {"1500s"}      b1500l -> "b": {"1500l"}
        # so the two land in one group and visibly disagree. Grouping on
        # prefix+suffix instead put them in "bs" and "bl", which share no
        # stem, so the clash was invisible and the pair was allowed (STEP B).
        #
        # With no letter prefix we keep the original behaviour and group on the
        # trailing letters, so a bare "2" and a "2d" stay apart rather than
        # cancelling each other:
        #     2  -> "":  {"2"}              2d     -> "d": {"2"}
        if prefix:
            stem, value = prefix, number + suffix
        else:
            stem, value = suffix, number

        groups.setdefault(stem.strip(), set()).add(value)
    return groups


def negations(name: str) -> frozenset:
    """Negation words present, e.g. "no phenol red" -> {"no"}."""
    padded = " " + normalize(name) + " "
    return frozenset(word for word in NEGATIONS if (" " + word + " ") in padded)


def grades(name: str) -> frozenset:
    """Grade and purity markers present, e.g. "Ethanol absolute" -> {"absolute"}."""
    squashed = normalize(name).replace(" ", "")
    return frozenset(grade for grade in GRADES if grade in squashed)


# ==========================================================================
# The four checks. Each returns a reason, or None to allow.
# ==========================================================================

def _check_size(name_a: str, name_b: str):
    """Cancel when a dimension on BOTH sides holds different magnitudes."""
    a, b = sizes(name_a), sizes(name_b)
    if not a or not b:
        return None                                   # abstain, nothing to compare

    shared = {dim for dim, _ in a} & {dim for dim, _ in b}
    for dimension in shared:
        values_a = {v for d, v in a if d == dimension}
        values_b = {v for d, v in b if d == dimension}

        # SUBSET RULE. Equality is too strict: one name often just lists more
        # detail than the other within the SAME dimension. Real example --
        #     "Syringe Filter 0.22um, 33mm"  ->  length {0.00022, 33.0}
        #     "Millex filter unit (0.22um)"  ->  length {0.00022}
        # Same product. If either side's values are contained in the other's,
        # nothing actually disagrees, so allow it.
        if values_a <= values_b or values_b <= values_a:
            continue

        return "size: %s %s vs %s" % (dimension, sorted(values_a), sorted(values_b))
    return None                                       # extra dimensions are fine


def _check_spec_codes(name_a: str, name_b: str):
    """Cancel when a shared letter stem carries different numbers."""
    a, b = spec_codes(name_a), spec_codes(name_b)
    if not a or not b:
        return None

    for stem in set(a) & set(b):
        if a[stem] != b[stem]:
            label = stem or "number"
            return "code: %s %s vs %s" % (label, sorted(a[stem]), sorted(b[stem]))
    return None


def _check_negation(name_a: str, name_b: str):
    """Cancel when one name negates something the other does not."""
    a, b = negations(name_a), negations(name_b)
    if a != b:
        return "negation: %s vs %s" % (sorted(a) or "-", sorted(b) or "-")
    return None


def _check_grade(name_a: str, name_b: str):
    """Cancel when the purity or grade markers disagree."""
    a, b = grades(name_a), grades(name_b)
    if not a or not b:
        return None
    if a != b:
        return "grade: %s vs %s" % (sorted(a), sorted(b))
    return None


# Cheapest check first, so most pairs exit early.
CHECKS = (
    ("size", _check_size),
    ("spec_code", _check_spec_codes),
    ("negation", _check_negation),
    ("grade", _check_grade),
)


# ==========================================================================
# Public API
# ==========================================================================

def veto_reason(name_a: str, name_b: str):
    """Return why this pair must be cancelled, or None if it may proceed.

    The reason string is shown to the user and written to Item Match Log, so
    a human can later see exactly which rule fired and judge whether it was
    right. That log is how this file gets improved.
    """
    for _label, check in CHECKS:
        reason = check(name_a, name_b)
        if reason:
            return reason
    return None


def veto(name_a: str, name_b: str) -> bool:
    """True = cancel this match, whatever the similarity score says."""
    return veto_reason(name_a, name_b) is not None


if __name__ == "__main__":
    PAIRS = [
        # must be CANCELLED -- real pairs from the catalogue
        ("Glycine 500g", "Glycine 1kg"),
        ("Glass bent inlet adaptor B-34", "Glass bent inlet adaptor B-24"),
        ("Sartocon Slice Cassette 100kDa", "Sartocon Slice Cassette 50kDa"),
        ("Festo Push-in T-connector QST-6", "Festo Push-in T-connector QST-8"),
        ("Dengue virus strain 2", "Dengue virus strain 1"),
        ("TrypLE Express Enzyme no phenol red", "TrypLE Express Enzyme Phenol Red"),
        ("Ethanol absolute", "Ethanol molecular biology grade"),
        ("Syringes 1ml", "Syringes 5ml"),
        # must be ALLOWED
        ("Glycine 1kg", "Glycine 1000g"),
        ("Serological Pipette 50 ml", "50ml Serological pipette"),
        ("Paraformaldehyde", "Paraformaldehyde for synthesis"),
        ("Syringe Filter Unit, 0.22 µm, PVDF, 33 mm", "Millex filter unit (0.22 um)"),
        ("BD FACS Clean Solution", "BD FACS Clean Solution"),
    ]
    for left, right in PAIRS:
        reason = veto_reason(left, right)
        verdict = "CANCEL" if reason else "allow "
        print("%s  %-44s %-40s %s" % (verdict, left[:44], right[:40], reason or ""))
