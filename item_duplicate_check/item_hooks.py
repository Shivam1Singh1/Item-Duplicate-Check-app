"""
item_hooks.py  --  what happens to the search index when an Item changes.

Wired to Item's after_insert / on_update / on_trash / after_rename in
hooks.py. There is nothing else on Item: no validate, no before_save, no
throw. See the header of hooks.py for why that is the whole point.

ALL EITHER HANDLER DOES IS BUMP A COUNTER
-----------------------------------------
The index lives in memory inside each worker. These handlers do not rebuild
it -- they increment one number in Redis, and the worker that next serves a
duplicate check notices it has fallen behind and rebuilds then. So an Item
save pays for one Redis command, not for a 22,000-row rebuild.

corpus.invalidate() swallows its own exceptions. A missed bump means one
worker serves a slightly stale index until the nightly rebuild, which is a
far better outcome than an Item failing to save.
"""

from item_duplicate_check.core import corpus


def _invalidate_quietly():
    """Bump the index generation and swallow anything that goes wrong.

    THIS try/except IS THE POINT OF THE FUNCTION.
    A doc_events handler that raises ABORTS THE SAVE. corpus.invalidate()
    already guards its own body, but that is not enough on its own: an import
    error, a missing Redis, or a bug introduced here later would propagate
    out of this module and an Item would fail to save because the duplicate
    checker could not update a cache. That is precisely the outcome HARD
    RULE 1 forbids.

    The cost of swallowing is that one worker may serve a slightly stale
    index until the nightly rebuild. That is not a close call.
    """
    try:
        corpus.invalidate()
    except Exception:
        try:
            import frappe

            frappe.log_error(
                title="item_duplicate_check: index refresh hook failed",
                message=frappe.get_traceback())
        except Exception:
            # Even the error log is optional. Nothing here may raise.
            pass


def refresh_index(doc, method=None):
    """Mark the index stale after an Item is created, edited or deleted.

    on_update fires for every edit, including ones that touch nothing we
    index. Checking which fields changed would cost more than the increment
    it saves, so it is not worth the branch.
    """
    _invalidate_quietly()


def refresh_index_on_rename(doc, method=None, old_name=None, new_name=None,
                            merge=False):
    """Mark the index stale after an Item is renamed.

    A separate handler because Frappe calls after_rename with extra
    arguments. The index stores item codes, so a rename changes it even
    though no indexed field changed.
    """
    _invalidate_quietly()
