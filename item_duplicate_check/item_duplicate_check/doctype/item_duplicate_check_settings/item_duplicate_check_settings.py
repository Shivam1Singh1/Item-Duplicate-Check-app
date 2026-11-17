"""
Item Duplicate Check Settings  --  the controller for the single doctype.

There is almost nothing here on purpose. The settings are read by
core/settings.py, which does not go through the ORM, so this class only has
to do one job: refuse to save values that would make the search behave
strangely.

It validates rather than corrects. Silently rewriting a number somebody typed
is worse than telling them it was wrong.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ItemDuplicateCheckSettings(Document):

    def validate(self):
        self.validate_thresholds()
        self.validate_weights()

    def validate_thresholds(self):
        """Both bands must be real probabilities, and strong must be the
        higher of the two -- otherwise every soft match would also be strong
        and the quiet note would never appear."""
        for field in ("strong_threshold", "soft_threshold"):
            value = self.get(field)
            if value is None or value < 0 or value > 1:
                frappe.throw(_("{0} must be between 0 and 1.")
                             .format(_(self.meta.get_label(field))))

        if self.soft_threshold > self.strong_threshold:
            frappe.throw(_("Soft Threshold ({0}) cannot be above Strong "
                           "Threshold ({1}). Nothing would ever fall into the "
                           "quiet band.")
                         .format(self.soft_threshold, self.strong_threshold))

    def validate_weights(self):
        """Weights may be any non-negative numbers -- they are normalised when
        the score is computed -- but they cannot ALL be zero, because then
        every pair would score zero and the app would look broken rather than
        disabled. Use the Enabled switch for that."""
        weights = ("weight_ordered", "weight_word_set", "weight_rare",
                   "weight_makes")

        for field in weights:
            if (self.get(field) or 0) < 0:
                frappe.throw(_("{0} cannot be negative.")
                             .format(_(self.meta.get_label(field))))

        if not any((self.get(f) or 0) > 0 for f in weights):
            frappe.throw(_("At least one weight must be above zero. To turn "
                           "the check off, uncheck Enabled instead."))
