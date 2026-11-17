"""
Item Match Log  --  the controller for the log rows.

Nothing happens on save. The row is written by core/log.py, which builds it
from a search result, and the two judgement fields are filled in later by the
form panel when the user acts on a warning.

The class exists so the doctype has a controller at all, and to hold the one
piece of behaviour the log needs: never let a logging problem reach the user.
"""

import frappe
from frappe.model.document import Document


class ItemMatchLog(Document):

    def before_insert(self):
        """Stamp the user if the caller did not.

        frappe.session.user is correct in a request; in a background job or a
        script it is Administrator, which is still the truth about who ran it.
        """
        if not self.user:
            self.user = frappe.session.user
