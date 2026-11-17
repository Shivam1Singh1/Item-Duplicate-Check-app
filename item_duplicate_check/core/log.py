"""
log.py  --  write one Item Match Log row per check, including silent ones.

WHY THIS IS BUILT ON DAY ONE, BEFORE ANYTHING READS IT
------------------------------------------------------
Two of the columns are worth more than the rest of the app:

    user_action        used existing / created anyway / gave up
    override_reason    different size / different grade / genuinely new
                       / service line

Those are real human judgements on real pairs, captured for free as a
side-effect of people doing their jobs. Three months of them is better
training data than any amount of hand-labelling, and they are the only labels
free of the Cat No leakage problem -- because a user judging "is this the same
product" is not reading the Cat No off a spreadsheet.

If logging starts later, those three months do not exist.

WHAT ELSE IT CAPTURES, AND WHY
------------------------------
    shown_to_user           users only judge pairs they were shown. Without
                            this column that bias is invisible in the data
    cancelled_matches       pairs the veto gate killed. If a rule is wrong,
                            this is where it shows up first
    candidates_considered   whether the rare-word index worked or the search
                            fell back to a full scan
    elapsed_ms              against the 300 ms budget
    engine_version          which version of the rules made this decision

THE ONE RULE
------------
write() cannot raise. A failed log row must never become a failed save
(HARD RULE 1). If the insert fails the error goes to the error log and the
caller is handed None.
"""

import json


def _entry(match) -> dict:
    """One Match turned into an Item Match Log Entry row."""
    payload = match.as_dict()
    return {
        "item": payload["item"],
        "item_name": payload["item_name"][:255],
        "score": payload["score"],
        "band": payload["band"],
        "via": payload["via"],
        "reason": payload["reason"],
        "cancel_reason": payload["cancel_reason"],
        "disabled": payload["disabled"],
        "item_description": (payload.get("description") or "")[:500],
        # json.dumps with default=str so a tuple of rare words, or anything
        # else that is not natively JSON, is written rather than raising.
        "components": json.dumps(payload["components"], default=str),
    }


def write(query, result, elapsed_ms, settings, shown_to_user=False,
          maintain_stock=1, description=None):
    """Insert one Item Match Log row. Returns its name, or None on failure.

    ignore_permissions is set because this is a system record of something
    that already happened, not a document the user is authoring. A user who
    may create an Item may not have been granted insert rights on the log,
    and the log must not be the thing that stops them.
    """
    try:
        import frappe

        doc = frappe.get_doc({
            "doctype": "Item Match Log",

            # what was typed
            "typed_item_name": (query.name or "")[:255],
            "cat_no": (query.cat_no or "")[:255] if query.cat_no else None,
            "makes": (query.makes or "")[:255] if query.makes else None,
            "item_code": (query.item_code or "")[:255] if query.item_code else None,
            "typed_description": (description or "")[:500] or None,
            "maintain_stock": int(maintain_stock or 0),

            # what came back
            "band": result.get("band") or "none",
            "top_score": (result["matches"][0].score
                          if result.get("matches") else 0.0),
            "shown_to_user": int(bool(shown_to_user)),
            "elapsed_ms": int(elapsed_ms),
            "candidates_considered": int(result.get("candidates") or 0),
            "engine_version": settings.get("engine_version"),
            "user": frappe.session.user,

            "matches": [_entry(m) for m in result.get("matches", [])],
            "cancelled_matches": [_entry(m)
                                  for m in result.get("cancelled", [])],
        })
        doc.insert(ignore_permissions=True)
        return doc.name

    except Exception:
        try:
            import frappe

            frappe.log_error(
                title="item_duplicate_check: could not write Item Match Log",
                message=frappe.get_traceback())
        except Exception:
            pass
        return None


def record_user_action(log_name: str, user_action: str,
                       override_reason: str = None) -> bool:
    """Fill in what the user did after seeing a warning.

    Called from the form panel. Written with db_set rather than a full save
    so that it cannot trip a validation hook on a row that is already closed
    business, and so it stays one UPDATE.
    """
    try:
        import frappe

        if not log_name:
            return False

        doc = frappe.get_doc("Item Match Log", log_name)
        doc.db_set("user_action", user_action, update_modified=False)
        if override_reason:
            doc.db_set("override_reason", override_reason,
                       update_modified=False)
        return True

    except Exception:
        try:
            import frappe

            frappe.log_error(
                title="item_duplicate_check: could not record user action",
                message=frappe.get_traceback())
        except Exception:
            pass
        return False
