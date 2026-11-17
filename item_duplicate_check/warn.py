"""
warn.py  --  the warning that cannot be bypassed.

WHY A SERVER-SIDE WARNING EXISTS AT ALL
---------------------------------------
The form panel in public/js is the good experience: it warns while the user
types and asks one question before saving. But it only runs in a browser, on
the save button, and Item Creation Form has an active workflow. Workflow
transitions save through `frappe.model.workflow.apply_workflow` on the
SERVER, so no client-side `validate` or `before_save` runs at all.

That is not a corner case -- it is how the form is actually used. A request
created and moved through its workflow never touched the panel, and the
duplicate went in silently. Measured: an Item Match Log with zero rows after
a real save.

So the check is also wired to the doctype's `validate`, where nothing can
route around it: workflow actions, the REST API, a bulk import, a patch, or
somebody calling `doc.insert()` in a console.

IT WARNS. IT DOES NOT REFUSE.
-----------------------------
`frappe.msgprint` without `raise_exception` shows the message and lets the
save complete. There is no `frappe.throw` here and there must never be one:

  - HARD RULE 1 says the check must never be the reason an item cannot be
    created;
  - decision 11's 79 CAPEX service rows are deliberately identical and would
    all fail;
  - and a hard block does not even work. The catalogue already has a
    client-side block on exact names, and the response to it was to type
    "Diiary" instead of "Diary". Both are now in the catalogue. A block
    teaches people to vary the name; it does not stop the duplicate.

What it does instead is name the existing item -- CODE and NAME -- and ask.
The code matters because the name alone is not actionable: the whole problem
is that several rows share a name, and the code is what goes into a Material
Request.

EVERY FAILURE PATH SAVES
------------------------
The whole function is wrapped. A missing Settings row, an unreadable index, a
bug introduced here next year -- all of them log and return, and the document
saves. A duplicate checker that stops work is worse than no duplicate checker.
"""

import time

import frappe

from item_duplicate_check.core import log as match_log
from item_duplicate_check.core.corpus import get_corpus
from item_duplicate_check.core.makes import _ALIASES
from item_duplicate_check.core.scorer import Query, search
from item_duplicate_check.core.settings import get_settings

#: Fields to read off the request document. Item Creation Form spells
#: Maintain Stock differently from Item, which is the kind of thing that
#: silently disables a rule if it is assumed rather than looked up.
FIELD_MAP = {
    "Item Creation Form": {
        "item_name": "item_name",
        "cat_no": "cat_no",
        "makes": "makes",
        "maintain_stock": "maintain_stock",
        "description": "description",
    },
    "Item": {
        "item_name": "item_name",
        "cat_no": "cat_no",
        "makes": "makes",
        "maintain_stock": "is_stock_item",
        "description": "description",
    },
}


def _format(matches) -> str:
    """The existing items, as a table a human can act on.

    CODE, NAME, SCORE -- one line each, nothing else. The per-row reason
    ("names 99% alike - shares mrna, at") used to sit under every row and was
    removed: on the rows this warning actually fires for, the five matches are
    the same name five times, so the reason is the same sentence five times.
    The inline panel in public/js still carries it, which is where decision 10
    ("show the reason, not the number") is served -- there it appears once.
    """
    rows = []
    for match in matches:
        payload = match.as_dict()
        link = "/app/item/%s" % payload["item"]
        rows.append(
            "<tr>"
            "<td style='padding:3px 10px 3px 0;white-space:nowrap'>"
            "<a href='%s' target='_blank'><b>%s</b></a></td>"
            "<td style='padding:3px 10px 3px 0'>%s</td>"
            "<td style='padding:3px 0;text-align:right;white-space:nowrap'>"
            "%d%%</td>"
            "</tr>"
            % (link,
               frappe.utils.escape_html(payload["item"]),
               frappe.utils.escape_html(payload["item_name"] or ""),
               round(payload["score"] * 100))
        )
    return "<table style='width:100%%'>%s</table>" % "".join(rows)


def _headline(total: int, shown: int) -> str:
    """How many already exist -- the one fact the old dialog never gave.

    `total` is every existing item at or above the soft band, `shown` is how
    many of them fit in the table (TOP_N, five). When they differ the line
    says so, because five rows with no count read as "there are five".
    """
    if total <= 1:
        return _("1 item like this already exists:")

    if total > shown:
        return _("{0} items like this already exist. Closest {1}:").format(
            total, shown)

    return _("{0} items like this already exist:").format(total)


def warn_if_duplicate(doc, method=None):
    """Warn, on the server, that this looks like something that exists.

    Wired to `validate` so it runs whatever saved the document. Never raises,
    never refuses a save.
    """
    try:
        mapping = FIELD_MAP.get(doc.doctype)
        if not mapping:
            return

        # Only when the record is being created. `validate` fires on every
        # later edit and on every workflow transition, and repeating the
        # same warning five times is how it gets ignored.
        if not doc.is_new():
            return

        settings = get_settings()
        if not settings["enabled"]:
            return

        # HARD RULE 2. A deliberate 0 is out of scope -- the CAPEX service
        # lines. On a document being saved the value IS a decision, unlike
        # the untouched default on a form nobody has filled in yet.
        if not frappe.utils.cint(doc.get(mapping["maintain_stock"])):
            return

        item_name = frappe.utils.cstr(doc.get(mapping["item_name"])).strip()
        if len(item_name) < 3:
            return

        started = time.perf_counter()
        query = Query(
            name=item_name,
            cat_no=doc.get(mapping["cat_no"]),
            makes=doc.get(mapping["makes"]),
            # An Item must not match itself. A request form is not a
            # catalogue row, so there is nothing to exclude.
            item_code=doc.name if doc.doctype == "Item" else None,
        )
        result = search(
            corpus=get_corpus(), query=query, weights=settings["weights"],
            strong=settings["strong_threshold"],
            soft=settings["soft_threshold"], aliases=_ALIASES)
        elapsed_ms = (time.perf_counter() - started) * 1000

        matches = result["matches"]
        band = result["band"]
        silent = bool(settings["silent_mode"])
        has_warning = bool(matches) and band != "none"

        # ASKED ONCE, BEFORE THE SAVE -- NOT TWICE, AFTER IT.
        #
        # The desk form asks first: public/js confirm_before_save() runs on
        # before_save, lists the same rows, and offers "Save anyway". When
        # the user takes it, the client sets __idc_warned on the document.
        #
        # Without this, that user is warned twice about the same three rows:
        # once in a dialog they answered, and again in a msgprint after the
        # record is already saved and the answer is moot. Being asked twice
        # is how a warning becomes something people click past.
        #
        # It is a suppression of the MESSAGE, never of the check. Everything
        # below still runs, and the routes this hook exists for -- the
        # workflow, the REST API, a bulk import, doc.insert() in a console --
        # have no client to set the flag and are unaffected.
        client_warned = bool(doc.get("__idc_warned"))

        # Was the user shown these pairs AT ALL -- by the dialog before the
        # save, or by the msgprint after it. This is the column that tells
        # later analysis which pairs a human actually judged, so it tracks
        # what was on screen, not which of the two put it there.
        seen = has_warning and not silent
        shown = seen and not client_warned

        # Logged whether or not anybody is shown anything. Decision 12: the
        # log is the point, and it must not be weakened to match the display.
        match_log.write(
            query=query, result=result, elapsed_ms=elapsed_ms,
            settings=settings, shown_to_user=seen, maintain_stock=1,
            description=doc.get(mapping["description"]))

        if not shown:
            return

        # Only the rows the count is about. `matches` is the top five by
        # score whatever they scored, so a query with one real match used to
        # list four more that were below the soft band -- which now reads as
        # a contradiction next to "1 item like this already exists".
        listed = [m for m in matches if m.band != "none"] or matches[:1]
        total = result.get("total_similar") or len(listed)

        frappe.msgprint(
            title=(_("A similar item already exists")
                   if band == "strong" else _("Some similar items exist")),
            msg=(
                "<p>%s: <b>%s</b></p>"
                "<p><b>%s</b></p>%s"
                "<p class='text-muted small' style='margin-top:10px'>%s</p>"
                % (_("You are creating"),
                   frappe.utils.escape_html(item_name),
                   _headline(total, len(listed)),
                   _format(listed),
                   _("Warning only -- the record is saved. Use the existing "
                     "code if it is the same product."))
            ),
            indicator="orange",
        )

    except Exception:
        # Deliberately broad. Whatever went wrong, somebody is trying to
        # create an item and that must continue to work.
        try:
            frappe.log_error(
                title="item_duplicate_check: server-side warning failed",
                message=frappe.get_traceback())
        except Exception:
            pass


def _(text):
    """frappe._ without importing it under a name flake8 complains about."""
    return frappe._(text)
