"""
tasks.py  --  scheduled work.

One job: rebuild the search index overnight.

WHY IT EXISTS WHEN THE HOOKS ALREADY INVALIDATE
-----------------------------------------------
The doc_events in hooks.py catch every change made through the document API.
They do not catch anything that writes to tabItem some other way -- a patch,
a bulk import, a `bench mariadb` fix, a restore. Those are exactly the
situations where a stale index would be most confusing, because nobody would
connect "the duplicate check missed it" with "we loaded items with a script
last night".

So the index is also rebuilt from scratch once a day. It costs about half a
second and nothing is waiting on it.
"""

import frappe

from item_duplicate_check.core import corpus
from item_duplicate_check.core.settings import get_settings


def rebuild_index():
    """Rebuild the rare-word index. Safe to run when the app is disabled.

    Returns the rebuilt Corpus, or None if it was skipped or failed, so the
    job is callable by hand from a console and tells you what happened.
    """
    settings = get_settings()
    if not settings["enabled"]:
        # Nothing reads the index while the app is off. Building it anyway
        # would be half a second of work every night for no reason.
        frappe.logger("item_duplicate_check").info(
            "nightly rebuild skipped: app disabled")
        return None

    try:
        return corpus.rebuild()
    except Exception:
        # A failed scheduled job must not stop the rest of the scheduler.
        frappe.log_error(
            title="item_duplicate_check: nightly index rebuild failed",
            message=frappe.get_traceback())
        return None
