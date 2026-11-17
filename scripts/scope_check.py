"""
scope_check.py  --  STEP A. How many Items are still being created?

WHY WE ASK
----------
The whole app only earns its keep if people are still adding Items. The
decision record says new-item creation stopped in Feb 2026. That claim decides
whether this is a live guard or a one-off cleanup tool, so it gets measured
against the database rather than taken on trust.

WHAT IT PRINTS
--------------
  1. Items created per month for the last 12 months
  2. The overall first / last creation date
  3. The Maintain Stock split, because HARD RULE 2 skips non-stock rows and we
     need the real size of that exclusion

Read-only. Touches nothing.

NOTE ON COLUMN ALIASES
----------------------
Rows come back as frappe._dict, which is a dict subclass, so an alias named
`items` would be shadowed by the dict method of the same name and silently
return a function. Every alias here is prefixed `n_` for that reason.

RUN
---
    cd ~/frappe-bench/frappe-bench
    env/bin/python apps/item_duplicate_check/scripts/scope_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import connect                                   # noqa: E402


def monthly_creation(frappe, months: int = 12):
    """Items created per calendar month, newest first."""
    return frappe.db.sql(
        """
        SELECT  DATE_FORMAT(creation, '%%Y-%%m') AS month,
                COUNT(*)                         AS n_items,
                SUM(IFNULL(is_stock_item, 0))    AS n_stock,
                SUM(IFNULL(disabled, 0))         AS n_disabled
        FROM    `tabItem`
        WHERE   creation >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)
        GROUP BY month
        ORDER BY month DESC
        """,
        (months,),
        as_dict=True,
    )


def recent_years(frappe):
    """Items created per year, so a 12-month window of zeros has context."""
    return frappe.db.sql(
        """
        SELECT  YEAR(creation) AS yr, COUNT(*) AS n_items
        FROM    `tabItem`
        GROUP BY yr
        ORDER BY yr DESC
        LIMIT 6
        """,
        as_dict=True,
    )


def totals(frappe):
    """Catalogue-wide counts, including the Maintain Stock split."""
    return frappe.db.sql(
        """
        SELECT  COUNT(*)                                   AS n_items,
                SUM(IFNULL(is_stock_item, 0))              AS n_stock,
                SUM(CASE WHEN IFNULL(is_stock_item, 0) = 0
                         THEN 1 ELSE 0 END)                AS n_non_stock,
                SUM(IFNULL(disabled, 0))                   AS n_disabled,
                MIN(creation)                              AS first_created,
                MAX(creation)                              AS last_created
        FROM    `tabItem`
        """,
        as_dict=True,
    )[0]


def main():
    frappe = connect()

    rows = monthly_creation(frappe)
    print("STEP A -- Items created per month, last 12 months (site_local)")
    print()
    print("  %-9s %8s %10s %10s" % ("month", "items", "stock", "disabled"))
    print("  " + "-" * 41)
    if not rows:
        print("  (no Items created in the last 12 months)")
    for r in rows:
        print("  %-9s %8d %10d %10d"
              % (r.month, r.n_items, r.n_stock or 0, r.n_disabled or 0))
    print("  " + "-" * 41)
    print("  %-9s %8d" % ("total", sum(r.n_items for r in rows)))

    print()
    print("Items created per year (last 6 years with data)")
    print("  %-6s %8s" % ("year", "items"))
    print("  " + "-" * 16)
    for r in recent_years(frappe):
        print("  %-6s %8d" % (r.yr, r.n_items))

    t = totals(frappe)
    print()
    print("Catalogue totals")
    print("  items in tabItem        %6d" % t.n_items)
    print("  maintain stock = 1      %6d" % (t.n_stock or 0))
    print("  maintain stock = 0      %6d   <- skipped by HARD RULE 2" % (t.n_non_stock or 0))
    print("  disabled = 1            %6d" % (t.n_disabled or 0))
    print("  first created           %s" % t.first_created)
    print("  last created            %s" % t.last_created)

    frappe.destroy()


if __name__ == "__main__":
    main()
