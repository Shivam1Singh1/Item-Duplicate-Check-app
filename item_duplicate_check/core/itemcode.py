"""
itemcode.py  --  read meaning out of an Item Code, and throw away the noise.

WHAT THIS FILE DOES
-------------------
An Item Code is not a name. It is a structured identifier:

    RDCN 02 03 0516
     |    |  |   |
     |    |  |   +-- serial, a running counter -- MEANINGLESS, discard it
     |    |  +------ sub-category
     |    +--------- category
     +-------------- division / type prefix

This file splits a code into those parts so the category can be used, and the
serial can be thrown away.

WHY THE SERIAL MUST BE DISCARDED
--------------------------------
    RDCN02030516   and   RDCN02030517

are 92% identical as strings and are completely unrelated products. If item
codes were ever fed to a text matcher, every item would "match" its neighbour.
Codes are parsed here, never compared as text. This is the whole reason the
file exists.

HOW WELL THE DIGITS PREDICT THE CATEGORY -- MEASURED
----------------------------------------------------
    digits 1-2   ->  Item Group       99.8%
    digits 1-4   ->  Item Group       99.9%   Item Child Group  99.3%
    digits 7-8   ->  Item Child Group 37.4%   (noise, as expected -- serial)

So digits 1-4 carry the category, digits 5-8 are the serial. Confirmed from
the other direction too: inside every category the serial runs as a dense
sequence (0001, 0002, 0003...) with no gaps, in 100% of the 154 groups tested.

FOUR CODE SHAPES EXIST, NOT ONE
-------------------------------
    RDCN02030516     13,906  54.3%   structured, category encoded
    R&D08518          8,334  32.6%   NO category at all
    CAPEX-02961       2,841  11.1%   no category
    RM/000707           500   2.0%   no category
    Consumable00726       9   0.0%   no category

Only 47% of the catalogue carries a machine-readable category. Callers must
always check `has_category` before trusting those fields.

WHY THIS MATTERS BEYOND PARSING
-------------------------------
`RDCN...` is the R&D division. `CN...` is Manufacturing. Same categories,
separate numbering. The same product bought by two divisions is GUARANTEED to
get two unrelated codes. Together with the 32.6% that encode no category at
all, this is why 92% of duplicate groups span more than one code prefix.

The duplicate problem is built into the numbering scheme. It is not caused by
careless users.

A USEFUL SIGNAL FALLS OUT OF THIS
---------------------------------
Two items in the same category but under DIFFERENT division prefixes is
evidence FOR duplication, not against it. See same_category_different_division().
"""

import re

# --------------------------------------------------------------------------
# One pattern per observed shape. Ordered most specific first, because
# STRUCTURED would also match the looser PLAIN pattern.
# --------------------------------------------------------------------------

#: RDCN02030516 -- letters then exactly 8 digits. The only shape with a category.
STRUCTURED = re.compile(r"^([A-Za-z&]+)(\d{2})(\d{2})(\d{4})$")

#: R&D08518, Consumable00726 -- letters then any run of digits.
PLAIN = re.compile(r"^([A-Za-z&]+)(\d+)$")

#: CAPEX-02961 and RM/000707 -- letters, a separator, then digits.
SEPARATED = re.compile(r"^([A-Za-z&]+)\s*[-/]\s*(\d+)$")

#: Division prefixes that mean "R&D". Everything else is treated as another
#: division. Used only by same_category_different_division().
RD_PREFIXES = {"RD", "R&D"}


class ItemCode:
    """One parsed item code.

    Attributes
    ----------
    raw           the original string, untouched
    scheme        "structured" | "plain" | "separated" | "unknown"
    prefix        letters at the front, upper-cased: "RDCN"
    category      2-digit category, or None
    subcategory   2-digit sub-category, or None
    serial        the running counter as a string -- kept for display only
    has_category  True only when a category was actually decoded
    """

    __slots__ = ("raw", "scheme", "prefix", "category", "subcategory", "serial")

    def __init__(self, raw, scheme, prefix, category, subcategory, serial):
        self.raw = raw
        self.scheme = scheme
        self.prefix = prefix
        self.category = category
        self.subcategory = subcategory
        self.serial = serial

    @property
    def has_category(self):
        """False for the 53% of codes that encode no category."""
        return self.category is not None

    @property
    def category_key(self):
        """Stable key for "same family of products", serial excluded.

            RDCN02030516 -> "RDCN|02|03"
            R&D08518     -> None   (nothing to group on)
        """
        if not self.has_category:
            return None
        return "%s|%s|%s" % (self.prefix, self.category, self.subcategory)

    @property
    def division(self):
        """The division part of the prefix: RDCN -> RD, CN -> CN.

        R&D codes are written RDCN / RDRM / RDCP; the Manufacturing versions
        drop the RD and are just CN / RM / CP.
        """
        if self.prefix.startswith("RD") and len(self.prefix) > 2:
            return "RD"
        if self.prefix == "R&D":
            return "R&D"
        return self.prefix

    @property
    def family(self):
        """Category without the division, so the two divisions line up.

            RDCN -> CN      CN -> CN      RDRM -> RM      RM -> RM
        """
        if self.prefix.startswith("RD") and len(self.prefix) > 2:
            return self.prefix[2:]
        return self.prefix

    def __repr__(self):
        if self.has_category:
            return "ItemCode(%s: %s cat=%s sub=%s serial=%s)" % (
                self.raw, self.prefix, self.category, self.subcategory, self.serial)
        return "ItemCode(%s: %s no-category)" % (self.raw, self.prefix)


def parse(code: str) -> ItemCode:
    """Split an item code into its parts. Never raises -- an unrecognised
    code comes back with scheme "unknown" and everything else None."""
    # ---- step 0: defend against blanks and non-strings --------------------
    if code is None:
        return ItemCode("", "unknown", "", None, None, None)
    raw = str(code).strip()
    if not raw:
        return ItemCode("", "unknown", "", None, None, None)

    # ---- step 1: the structured shape, the only one with a category -------
    # Tried first because PLAIN would also match it and silently lose the
    # category split.
    match = STRUCTURED.match(raw)
    if match:
        prefix, category, subcategory, serial = match.groups()
        return ItemCode(raw, "structured", prefix.upper(), category, subcategory, serial)

    # ---- step 2: letters then digits, no category -------------------------
    match = PLAIN.match(raw)
    if match:
        prefix, digits = match.groups()
        return ItemCode(raw, "plain", prefix.upper(), None, None, digits)

    # ---- step 3: letters, separator, digits -------------------------------
    match = SEPARATED.match(raw)
    if match:
        prefix, digits = match.groups()
        return ItemCode(raw, "separated", prefix.upper(), None, None, digits)

    # ---- step 4: anything else --------------------------------------------
    # Rare (9 rows in 25,590) but must not crash the pipeline.
    letters = re.match(r"^([A-Za-z&]+)", raw)
    return ItemCode(raw, "unknown", letters.group(1).upper() if letters else "", None, None, None)


def same_category_different_division(code_a: str, code_b: str) -> bool:
    """True when two codes sit in the same category but different divisions.

    This is a POSITIVE signal for duplication. RDCN020301xx and CN020301xx
    are the same category of product filed by R&D and by Manufacturing --
    exactly the situation the numbering scheme guarantees will happen.

    Returns False unless both codes are structured, since the other 53%
    carry no category to compare.
    """
    a, b = parse(code_a), parse(code_b)
    if not (a.has_category and b.has_category):
        return False
    same_family = a.family == b.family
    same_cat = (a.category, a.subcategory) == (b.category, b.subcategory)
    return same_family and same_cat and a.division != b.division


if __name__ == "__main__":
    SAMPLES = [
        "RDCN02030516",   # structured, R&D consumable
        "RDRM01070123",   # structured, R&D reagent
        "CN05050004",     # structured, Manufacturing plasticware
        "R&D08518",       # no category
        "CAPEX-02961",    # no category
        "RM/000707",      # no category
        "Consumable00726",
        "",
    ]
    for code in SAMPLES:
        parsed = parse(code)
        print("%-18s %-11s prefix=%-11s family=%-5s division=%-5s key=%s"
              % (repr(code), parsed.scheme, parsed.prefix, parsed.family,
                 parsed.division, parsed.category_key))

    print()
    print("same category, different division (a duplication signal):")
    for a, b in [("RDCN02030516", "CN02030077"), ("RDCN02030516", "RDCN02030517"),
                 ("R&D08518", "CN02030077")]:
        print("   %-14s vs %-14s -> %s"
              % (a, b, same_category_different_division(a, b)))
