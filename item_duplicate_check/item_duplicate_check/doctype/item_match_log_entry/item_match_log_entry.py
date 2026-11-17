"""
Item Match Log Entry  --  one suggested item inside one logged check.

A child table with no behaviour of its own. Frappe still expects a controller
class to exist for the doctype, so this is it.
"""

from frappe.model.document import Document


class ItemMatchLogEntry(Document):
    pass
