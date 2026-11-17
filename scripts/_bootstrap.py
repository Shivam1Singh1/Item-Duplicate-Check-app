"""
_bootstrap.py  --  let the dev scripts in this folder talk to a real site.

WHY THIS FILE EXISTS
--------------------
The scripts in scripts/ are developer tools, not part of the app that ships.
They are run by hand from the bench directory:

    env/bin/python apps/item_duplicate_check/scripts/scope_check.py

`bench execute` would also work, but it only takes an importable dotted path,
which would force these throw-away tools to live inside the shipped package.
Keeping them outside means nothing here can ever be imported by the app at
runtime -- a one-way dependency, deliberately.

WHAT IT DOES
------------
Boots Frappe against a site and makes the app importable. Nothing else.
"""

import os
import sys

#: apps/item_duplicate_check -- so `import item_duplicate_check.core...` works
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: the bench's sites/ directory, which is what frappe.init() wants
BENCH_ROOT = os.path.dirname(os.path.dirname(APP_ROOT))
SITES_PATH = os.path.join(BENCH_ROOT, "sites")

DEFAULT_SITE = "site_local"


def connect(site: str = None):
    """Open a Frappe connection to `site` and return the frappe module.

    Safe to call twice; frappe.init() is a no-op once initialised.
    """
    sys.path.insert(0, APP_ROOT)
    os.chdir(SITES_PATH)

    import frappe

    site = site or os.environ.get("IDC_SITE", DEFAULT_SITE)
    frappe.init(site=site, sites_path=SITES_PATH)
    frappe.connect()
    return frappe
