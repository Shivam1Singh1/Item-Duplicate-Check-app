"""
install.py  --  what happens when the app is installed.

One job, and it is a safety property rather than a feature: create the
Settings row with the app switched OFF and silent mode ON.

WHY SHIP DISABLED
-----------------
Installing an app on a live ERPNext should not change what anybody sees.
The thresholds in this app are a starting guess, not a measured result --
at 0.92 the alert rate is 33%, which is high -- and they are meant to be
tuned on two weeks of real traffic before a single warning is shown.

So the sequence is: install (nothing happens), enable with silent_mode on
(the log fills up, still nothing happens), tune the thresholds on that log,
then turn silent_mode off.
"""

import frappe

from item_duplicate_check.core.settings import SETTINGS_DOCTYPE


def after_install():
    """Create Item Duplicate Check Settings, disabled, with the documented
    defaults. Idempotent: re-running leaves an existing row alone."""
    doc = frappe.get_single(SETTINGS_DOCTYPE)

    if doc.get("engine_version"):
        # Already configured by somebody. Do not touch their numbers.
        return

    doc.enabled = 0
    doc.silent_mode = 1
    doc.strong_threshold = 0.92
    doc.soft_threshold = 0.75
    doc.weight_ordered = 0.47
    doc.weight_word_set = 0.21
    doc.weight_rare = 0.18
    doc.weight_makes = 0.14
    doc.engine_version = "rules-1.0"
    doc.save(ignore_permissions=True)

    frappe.db.commit()
