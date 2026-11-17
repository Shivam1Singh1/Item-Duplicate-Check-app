"""
hooks.py  --  how this app attaches to ERPNext, and how little it touches.

THE SHAPE OF THE INTEGRATION
----------------------------
Four attachments, and deliberately no others:

    doctype_js         adds the warning panel to the Item form
    doc_events         marks the search index stale when an Item changes
    scheduler_events   rebuilds the index nightly, as a safety net
    whitelisted method api.check_duplicate, called by the panel

WHAT IS NOT HERE, AND WHY THAT MATTERS
--------------------------------------
There is no `validate`, no `before_save`, no `before_submit` and no override
of any ERPNext class. That is the mechanical guarantee behind HARD RULE 1:
the check is not on the save path at all, so it cannot block a save even if
every line of it were wrong. The panel asks a read-only question over AJAX
and draws the answer.

The doc_events that ARE here only ever call corpus.invalidate(), which is one
Redis increment. They cannot fail a save either -- invalidate() swallows its
own errors -- and they do no work: the rebuild happens lazily in whichever
worker next serves a check.

Nothing in ERPNext or the core Item doctype is modified.
"""

app_name = "item_duplicate_check"
app_title = "Item Duplicate Check"
app_publisher = "MCPL"
app_description = ("Warns when a new Item looks like one that already exists "
                   "in the catalogue")
app_email = "shivam.singh@microcrispr.com"
app_license = "mit"

# --------------------------------------------------------------------------
# The form panel
#
# Loaded on the two forms where a new item is actually typed, not site-wide.
# It calls api.check_duplicate on a 600 ms debounce while the user types and
# draws a banner or a quiet note. It attaches no validate handler of any kind.
#
# ITEM CREATION FORM IS THE ONE THAT MATTERS.
# `tabItem` stopped being written to directly in February 2026, but Item
# Creation Form -- the request form people actually fill in -- has 11,396
# rows and was last written to today. Attaching only to Item would have put
# the warning on a screen nobody opens.
#
# The two forms spell one field differently: Item has `is_stock_item`, Item
# Creation Form has `maintain_stock`. The JavaScript carries a field map per
# doctype rather than assuming.
# --------------------------------------------------------------------------
doctype_js = {
    "Item": "public/js/item_duplicate_check.js",
    "Item Creation Form": "public/js/item_duplicate_check.js",
}

# --------------------------------------------------------------------------
# WHY THE SAME FILE IS ALSO LOADED SITE-WIDE
#
# doctype_js DOES NOT REACH ITEM CREATION FORM. That form is a CUSTOM
# doctype (`custom = 1`), and FormMeta.add_code() in frappe/desk/form/meta.py
# begins:
#
#     def add_code(self):
#         if self.custom:
#             return
#
# add_code_via_hook("doctype_js", ...) is called INSIDE add_code(), so for a
# custom doctype the hook above is read and then silently dropped. Measured:
# the form script Frappe sends for Item Creation Form is 0 bytes, against
# 53,551 for Item. The entry stays in doctype_js because Item is not custom
# and is served correctly by it.
#
# That is why the panel and the before-save question never appeared on the
# form that matters, and why the only thing anybody ever saw was warn.py's
# msgprint AFTER the save had already gone through.
#
# app_include_js is the one delivery route that does not care whether a
# doctype is custom. A bare /assets/ path rather than a .bundle.js is
# deliberate: it needs no `bench build`, and it is the pattern already used
# in this bench by recruitment and custom_batch_planning.
#
# The file is now loaded twice on an Item form -- once here, once through
# doctype_js. item_duplicate_check.bind() is idempotent for exactly that
# reason; binding twice would run before_save twice and stack two dialogs.
# --------------------------------------------------------------------------
app_include_js = [
    "/assets/item_duplicate_check/js/item_duplicate_check.js",
]

# --------------------------------------------------------------------------
# Keeping the index honest
#
# The rare-word index is built in memory, per worker, from tabItem. These
# four events mark it stale the moment the table changes, so a newly created
# item is a candidate for the very next check rather than after the nightly
# rebuild.
#
# on_update covers edits to item_name, cat_no and makes -- all three are
# indexed, so an edit matters as much as an insert. on_trash and after_rename
# matter because the index stores item codes.
#
# Each handler is one Redis increment and swallows its own errors. An Item
# save can never fail because of them.
# --------------------------------------------------------------------------
doc_events = {
    "Item": {
        "after_insert": "item_duplicate_check.item_hooks.refresh_index",
        "on_update": "item_duplicate_check.item_hooks.refresh_index",
        "on_trash": "item_duplicate_check.item_hooks.refresh_index",
        "after_rename": "item_duplicate_check.item_hooks.refresh_index_on_rename",
        # The server-side warning. Same reasoning as the one below, and it
        # also covers an Item created straight through the API.
        "validate": "item_duplicate_check.warn.warn_if_duplicate",
    },

    # ----------------------------------------------------------------------
    # THE WARNING THAT CANNOT BE ROUTED AROUND
    #
    # The form panel is the good experience, but it only runs in a browser,
    # on the Save button. Item Creation Form has an ACTIVE WORKFLOW, and
    # workflow transitions save through apply_workflow on the server -- no
    # client-side validate or before_save runs at all. A request created and
    # moved through its workflow never touched the panel, and the duplicate
    # went in with Item Match Log showing zero rows.
    #
    # `validate` catches every route: the workflow, the REST API, a bulk
    # import, a patch, doc.insert() in a console.
    #
    # It WARNS. warn.py uses frappe.msgprint and never frappe.throw -- see
    # the header there for why a hard block is both forbidden by HARD RULE 1
    # and, on this data, ineffective.
    # ----------------------------------------------------------------------
    "Item Creation Form": {
        "validate": "item_duplicate_check.warn.warn_if_duplicate",
    },
}

# --------------------------------------------------------------------------
# Nightly rebuild
#
# The doc_events above already catch every change made through the document
# API. This is the safety net for everything that is not: a patch, a bulk
# import, a direct SQL fix. daily_long rather than daily because it reads the
# whole Item table, and nothing is waiting on it.
# --------------------------------------------------------------------------
scheduler_events = {
    "daily_long": [
        "item_duplicate_check.tasks.rebuild_index",
    ],
}

# --------------------------------------------------------------------------
# Fixtures, installation
#
# The app ships DISABLED. after_install creates the Settings row with
# enabled = 0 and silent_mode = 1, so installing it changes nothing a user
# can see until somebody deliberately turns it on.
# --------------------------------------------------------------------------
after_install = "item_duplicate_check.install.after_install"
