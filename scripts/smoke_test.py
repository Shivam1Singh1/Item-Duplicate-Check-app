"""
smoke_test.py  --  STEP I. Fifteen real names, through the real API, on the
real site.

The harness in eval_harness.py measures thousands of pairs and prints
averages. This does the opposite: it runs a small number of names that were
chosen BY HAND from the catalogue and prints everything, so a human can read
each answer and agree or disagree with it.

THE FIFTEEN
-----------
  5 EXACT DUPLICATES, typed the way a person types them -- without the
    trademark symbol, with doubled spaces, with "u" instead of the micro
    sign, in the wrong case. All five must come back strong.

  5 LOOK-ALIKES, real pairs from the catalogue that differ only in size or
    grade. For each one, the LOOK-ALIKE PARTNER must never be offered. These
    are the expensive mistake: a wrong warning here teaches the user to
    ignore the tool. (See the comment above LOOK_ALIKES for why this is not
    the same as "must return nothing", which is what this script asserted at
    first and was wrong about.)

  5 GENUINELY NEW names, which must come back with nothing.

Every name is a real row in tabItem (or deliberately absent). Nothing is
invented to make the output look good.

AND THEN THE RULE THAT MATTERS MOST
-----------------------------------
Section 4 proves a save is not blocked even when the app is broken on
purpose, with failures injected at three levels:

    4a  the search raises inside the API      -> an empty answer, no exception
    4b  invalidate() raises under the hook    -> the handler still returns
    4c  the hook raises during a real save    -> the Item still saves
    4d  no save-path hook is registered at all

4c re-saves an EXISTING Item rather than creating one. Creating an Item on
this site runs mandatory-field and Item Group validations belonging to other
installed apps; a failure there would say nothing about this app's hook.
on_update fires on a re-save, which is the hook under test. Everything is
rolled back, so this script leaves the database exactly as it found it.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/smoke_test.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import connect                                   # noqa: E402

# --------------------------------------------------------------------------
# The fifteen names. (expected band, typed name, cat_no, makes, note)
#
# "expect" is what a human says the right answer is, written down BEFORE the
# run, so the output can be wrong rather than merely surprising.
# --------------------------------------------------------------------------

EXACT_DUPLICATES = [
    ("strong", "serological pipette  50 ml", None, "SPL",
     "real row is 'Serological Pipette 50 mL'. Lower case, doubled space"),
    ("strong", "Implen OD600 DiluPhotometer", None, "Implen",
     "real row carries two trademark symbols"),
    ("strong", "Corning 40um Cell Strainer, Blue, Sterile", None, "Corning",
     "real row uses the micro sign; typed here as 'u'"),
    ("strong", "invitrogen ibright fl1500 imaging system", None, "Invitrogen",
     "real row is 'Invitrogen(tm) iBright(tm) FL1500 Imaging System'"),
    ("strong", "Multi C Strainer (40 um).", None, "SPL Lifesciences",
     "real row is 'Multi C-Strainer (40 micron)'. Hyphen and trailing dot"),
]

# --------------------------------------------------------------------------
# The look-alikes are checked differently, and the first version of this
# script got it wrong in a way worth recording.
#
# It asserted "band must be none". That was the wrong question. Every one of
# these names is itself a real catalogue row -- often filed more than once --
# so a strong match against its OWN copies is the correct answer, and the
# script reported five misses while the gate was working perfectly.
#
# The real question is narrower: the LOOK-ALIKE PARTNER must never be offered
# as a match. So each case names the partner, and the assertion is:
#
#     partner NOT in matches          always
#     partner in cancelled, w/ reason when it scored high enough to be judged
#
# The second part is conditional because the gate is evaluated lazily -- a
# partner that could not have reached the top five is never parsed at all,
# which is a speed decision, not a verdict. Absent from `matches` is the
# property that matters to the user.
#
# (expected partner substring, typed name, cat_no, makes, note)
# --------------------------------------------------------------------------
LOOK_ALIKES = [
    ("Ph Eur 1L",
     "Ethanol absolute for analysis EMSURE ACS,ISO,Reag. Ph Eur 2.5L",
     None, "Sigma",
     "must NOT offer the 1L bottle of the same product"),
    ("40",
     "Corning 70um Cell Strainer, White, Sterile", None, "Corning",
     "must NOT offer the 40um strainer"),
    ("50 mL",
     "Serological Pipette 5 mL", None, "SPL",
     "must NOT offer the 50 mL pipette"),
    ("10\"",
     "AseptiCap KS (0.45 + 0.2 um) Capsule filter 8 inch", None, "mdi",
     "must NOT offer the 10 inch capsule filter"),
    ("500G",
     "Glycine 1kg", None, "Sigma",
     "must NOT offer a 500g pack of glycine"),
]

GENUINELY_NEW = [
    ("none", "Quantum flux capacitor module QX-9", None, "Acme",
     "nothing like this exists"),
    ("none", "Hydrogel bioprinting cartridge 12 mL", None, None,
     "plausible lab item, not in this catalogue"),
    ("none", "Underwater acoustic transponder beacon", None, None,
     "wrong industry entirely"),
    ("none", "Saffron harvesting basket, cane", None, None,
     "wrong industry entirely"),
    ("none", "Tungsten crucible liner 18 mm, zone-refined", None, None,
     "plausible but absent"),
]

GROUPS = [
    ("1. EXACT DUPLICATES -- must be found", EXACT_DUPLICATES),
    ("3. GENUINELY NEW -- must find nothing", GENUINELY_NEW),
]


def set_settings(frappe, **kw):
    doc = frappe.get_single("Item Duplicate Check Settings")
    for key, value in kw.items():
        setattr(doc, key, value)
    doc.save(ignore_permissions=True)
    frappe.db.commit()


def run_group(api, title, cases):
    """Print every field of every answer. No summarising."""
    print()
    print("  " + "=" * 86)
    print("  " + title)
    print("  " + "=" * 86)

    agreed = 0
    for expect, name, cat_no, makes, note in cases:
        out = api.check_duplicate(item_name=name, cat_no=cat_no, makes=makes,
                                  maintain_stock=1)
        band = out.get("band")
        ok = (band == expect) if expect != "strong" else (band == "strong")
        agreed += 1 if ok else 0

        print()
        print("  %-4s typed : %s" % ("ok" if ok else "MISS", name))
        print("       makes : %s      cat_no : %s" % (makes, cat_no))
        print("       note  : %s" % note)
        print("       expect: %-8s got: %-8s  %d ms   log: %s"
              % (expect, band, out.get("elapsed_ms", 0), out.get("log")))

        matches = out.get("matches") or []
        if matches:
            print("       matches:")
            for m in matches:
                print("         %.4f %-7s %-14s %s"
                      % (m["score"], m["band"], m["item"], m["item_name"][:52]))
                print("                 why: %s" % m["reason"][:76])
        else:
            print("       matches: (none)")

        cancelled = out.get("cancelled") or []
        if cancelled:
            print("       cancelled by a safety rule:")
            for m in cancelled:
                print("         would have scored %.4f  %s"
                      % (m["components"].get("raw_score", 0.0),
                         m["item_name"][:48]))
                print("                 rule: %s" % m["cancel_reason"])

    print()
    print("  %d of %d agreed with the expected answer" % (agreed, len(cases)))
    return agreed, len(cases)


def run_look_alikes(api):
    """The look-alike group, with the assertion the first version got wrong.

    Passes when the partner is absent from `matches`. Prints the cancel
    reason when the gate recorded one, because that reason is what a user
    would eventually be shown and what a reviewer needs in order to disagree.
    """
    print()
    print("  " + "=" * 86)
    print("  2. LOOK-ALIKES -- the partner must never be offered")
    print("  " + "=" * 86)

    agreed = 0
    for partner, name, cat_no, makes, note in LOOK_ALIKES:
        out = api.check_duplicate(item_name=name, cat_no=cat_no, makes=makes,
                                  maintain_stock=1)
        matches = out.get("matches") or []
        cancelled = out.get("cancelled") or []

        offered = [m for m in matches if partner.lower() in
                   (m["item_name"] or "").lower()]
        blocked = [m for m in cancelled if partner.lower() in
                   (m["item_name"] or "").lower()]

        ok = not offered
        agreed += 1 if ok else 0

        print()
        print("  %-4s typed   : %s" % ("ok" if ok else "FAIL", name))
        print("       note    : %s" % note)
        print("       partner : a name containing %r" % partner)
        print("       band    : %-7s  matches: %d   cancelled: %d   %d ms"
              % (out.get("band"), len(matches), len(cancelled),
                 out.get("elapsed_ms", 0)))

        if offered:
            print("       OFFERED THE PARTNER (this is the failure):")
            for m in offered:
                print("         %.4f %s" % (m["score"], m["item_name"][:60]))
        if blocked:
            print("       partner cancelled by a rule:")
            for m in blocked:
                print("         would have scored %.4f  %s"
                      % (m["components"].get("raw_score", 0.0),
                         m["item_name"][:52]))
                print("           rule: %s" % m["cancel_reason"])
        if not blocked and not offered:
            print("       partner never reached the top five, so the gate was")
            print("       not asked. Absent from the results either way.")

        if matches:
            print("       what WAS offered (its own copies, correctly):")
            for m in matches[:3]:
                print("         %.4f %-7s %s"
                      % (m["score"], m["band"], m["item_name"][:56]))

    print()
    print("  %d of %d look-alike partners were correctly withheld"
          % (agreed, len(LOOK_ALIKES)))
    return agreed, len(LOOK_ALIKES)


def prove_maintain_stock_distinction(api):
    """Section 5. A stated 0 skips; an unstated one does not.

    This is the bug that made the check invisible on Item Creation Form.
    That form's Maintain Stock is a Check whose DEFAULT is 0 and which the
    user ticks later -- 9,638 of 11,396 saved rows end up at 1. Reading the
    untouched default as a decision meant the check never ran for anybody,
    whatever they typed, and the failure was silent: the panel simply drew
    nothing.

    HARD RULE 2 is unchanged. A deliberate 0 is still skipped before any
    scoring, which is what protects the 79 CAPEX service rows.
    """
    print()
    print("  " + "=" * 86)
    print("  5. MAINTAIN STOCK -- a stated 0 skips, an unstated one does not")
    print("  " + "=" * 86)
    print()

    cases = [
        ("new form, user has not touched it", None, "check"),
        ("empty string, as HTTP sends it", "", "check"),
        ("user unticked it", 0, "skip"),
        ("unticked, via HTTP", "0", "skip"),
        ("user ticked it", 1, "check"),
        ("ticked, via HTTP", "1", "check"),
    ]

    agreed = 0
    for label, value, expect in cases:
        out = api.check_duplicate("Mouse IL-4 ELISA Kit.", cat_no="BMS613",
                                  makes="Thermo", maintain_stock=value)
        skipped = bool(out.get("skipped"))
        got = "skip" if skipped else "check"
        ok = got == expect
        agreed += 1 if ok else 0
        print("  %-4s maintain_stock=%-10r %-36s -> %-5s band=%-6s %s"
              % ("ok" if ok else "FAIL", value, label, got, out["band"],
                 out.get("skipped") or ""))

    print()
    print("  %d of %d behaved correctly" % (agreed, len(cases)))
    return agreed, len(cases)


def prove_save_is_never_blocked(frappe, api):
    """Section 4. The rule that overrides every other consideration."""
    print()
    print("  " + "=" * 86)
    print("  4. HARD RULE 1 -- a save is never blocked, even when this app is broken")
    print("  " + "=" * 86)

    # ---- 4a: the search itself raising ---------------------------------
    original = api.get_corpus

    def exploding(*args, **kwargs):
        raise RuntimeError("deliberate failure injected by smoke_test.py")

    api.get_corpus = exploding
    try:
        out = api.check_duplicate("Serological Pipette 50 ml", makes="SPL")
        print()
        print("  4a. search raises inside the API")
        print("      returned band=%r matches=%d skipped=%r  (no exception)"
              % (out.get("band"), len(out.get("matches") or []),
                 out.get("skipped")))
    finally:
        api.get_corpus = original

    # ---- 4b: the index hook raising during a real Item insert ----------
    # This is the dangerous one. A doc_events handler that raises aborts the
    # save, so the handler has to swallow its own failures.
    from item_duplicate_check.core import corpus as corpus_module

    original_invalidate = corpus_module.invalidate

    def exploding_invalidate(*args, **kwargs):
        raise RuntimeError("deliberate hook failure injected by smoke_test.py")

    corpus_module.invalidate = exploding_invalidate

    # The handler is called directly first. This is the narrowest possible
    # statement of the property: with the thing it depends on raising, the
    # handler itself must still return normally.
    from item_duplicate_check import item_hooks

    print()
    print("  4b. the index hook raises -- the handler must swallow it")
    try:
        item_hooks.refresh_index(doc=None, method="on_update")
        item_hooks.refresh_index_on_rename(doc=None, method="after_rename")
        print("      both handlers returned normally with invalidate() raising")
    except Exception as exc:
        print("      HANDLER RAISED: %s: %s   <- this would abort a save"
              % (type(exc).__name__, exc))

    # Then the same thing through a real save. An EXISTING Item is re-saved
    # rather than a new one created: creating an Item on this site runs
    # mandatory-field and Item Group validations belonging to other installed
    # apps, and a failure there would say nothing about our hook. on_update
    # fires either way, which is the hook under test.
    victim = frappe.db.sql(
        """
        SELECT `name` FROM `tabItem`
        WHERE  IFNULL(`disabled`, 0) = 1 AND IFNULL(`is_stock_item`, 0) = 1
        ORDER BY `creation` DESC LIMIT 1
        """,
        as_dict=True,
    )[0]

    print()
    print("  4c. a real Item is re-saved while the hook raises")
    print("      item: %s  (disabled row, re-saved then rolled back)"
          % victim.name)
    try:
        doc = frappe.get_doc("Item", victim.name)
        doc.save(ignore_permissions=True)
        print("      Item SAVED  <- on_update fired, the hook raised, the "
              "save went through")
    except Exception as exc:
        print("      Item FAILED TO SAVE: %s: %s" % (type(exc).__name__, exc))
    finally:
        corpus_module.invalidate = original_invalidate

    # ---- clean up: leave the database exactly as it was ----------------
    frappe.db.rollback()
    print("      rolled back")

    # ---- 4c: no save-path hooks exist at all ---------------------------
    from item_duplicate_check import hooks

    item_events = hooks.doc_events.get("Item", {})
    blocking = [e for e in item_events
                if e in ("validate", "before_save", "before_insert",
                         "before_submit", "on_submit", "before_validate")]
    print()
    print("  4d. hooks registered on Item: %s" % sorted(item_events))
    print("      handlers that could block a save: %s"
          % (blocking or "none -- the check is not on the save path"))


def main():
    frappe = connect()
    from item_duplicate_check import api

    # The app ships disabled. Turn it on, and turn silent mode OFF, because
    # this script is here to read the answers rather than the log.
    before = frappe.get_single("Item Duplicate Check Settings")
    was_enabled, was_silent = before.enabled, before.silent_mode
    set_settings(frappe, enabled=1, silent_mode=0)
    frappe.clear_cache()

    print("=" * 90)
    print("STEP I -- live smoke test on site_local, through api.check_duplicate")
    print("=" * 90)
    print()
    status = api.engine_status()
    print("  engine: %s" % status)

    agreed, total = run_group(api, GROUPS[0][0], GROUPS[0][1])

    a, t = run_look_alikes(api)
    agreed, total = agreed + a, total + t

    a, t = run_group(api, GROUPS[1][0], GROUPS[1][1])
    agreed, total = agreed + a, total + t

    a, t = prove_maintain_stock_distinction(api)
    agreed, total = agreed + a, total + t

    prove_save_is_never_blocked(frappe, api)

    print()
    print("=" * 90)
    print("  %d of %d hand-chosen names answered as expected" % (agreed, total))
    print("=" * 90)

    # Put the switches back exactly as they were.
    set_settings(frappe, enabled=was_enabled, silent_mode=was_silent)
    print()
    print("  settings restored: enabled=%s silent_mode=%s"
          % (was_enabled, was_silent))

    frappe.destroy()


if __name__ == "__main__":
    main()
