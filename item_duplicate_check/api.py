"""
api.py  --  the one entry point the browser calls.

This is the only file that knows about both Frappe and the search. Everything
under it in core/ is pure Python; everything above it is JavaScript. It does
five things in a fixed order and then gets out of the way:

    1. the kill switch
    2. HARD RULE 2  -- skip non-stock items before any scoring
    3. pick the engine
    4. search
    5. log, always, even when nothing is shown

TWO ENGINES, AND WHY RULES IS THE DEFAULT
-----------------------------------------
    rules   core/scorer.py   a hand-written weighted sum
    model   core/model.py    the trained pair classifier

Measured on validation, veto gate applied:

                        precision@0.92   recall@0.92
    rules                     100.0%         64.2%
    model (gradient boosting)  99.3%         65.5%

Decision 8, written before either existed: "Ship the hand-written weights
unless the model clearly wins. Ties go to the simpler option." 0.7 points of
precision for 1.3 points of recall is not a clear win, so `rules` is the
default and `model` is opt-in through Item Duplicate Check Settings.

The engine falls back to `rules` on its own if the model file is missing,
unreadable, or was trained on a different feature list. That is decision 8
as well: "Keep the hand-written scorer in the code permanently as an
automatic fallback."

THE RULE THAT OVERRIDES EVERY OTHER CONSIDERATION
-------------------------------------------------
A duplicate checker must never be the reason somebody cannot create an item.
check_duplicate() catches everything -- not "the errors we thought of", but
everything. On failure it logs and returns an empty, well-formed answer, and
the form carries on as if the app were not installed.

That is also why this is a read-only whitelisted method and not a hook on
Item. There is no validate(), no before_save and no frappe.throw anywhere in
this app. The check cannot block a save because it is never on the path of
one.

WHY NON-STOCK ITEMS ARE SKIPPED
-------------------------------
79 items in this system are all named "electrical installation at mrna lab".
They are not mistakes -- they are CAPEX service entries, one per purchase
order, and they are correct. One line removes that entire class of false
warning before any scoring happens. On site_local the skip covers 4,340 rows.

SILENT MODE
-----------
While silent_mode is on, the check runs in full and the log row is written in
full, but the client is told band "none" and handed no matches. The log is
the point of the exercise, so it is not weakened to match what the user sees.
"""

import time

import frappe

from item_duplicate_check.core import log as match_log
from item_duplicate_check.core import model as model_engine
from item_duplicate_check.core.corpus import get_corpus
from item_duplicate_check.core.makes import _ALIASES
from item_duplicate_check.core.scorer import Query, search
from item_duplicate_check.core.settings import get_settings

#: What the client gets when the app is off, the item is out of scope, or
#: something went wrong. Always the same shape, so the JavaScript never has
#: to special-case a failure.
EMPTY = {
    "band": "none",
    "matches": [],
    "cancelled": [],
    "total_similar": 0,
    "elapsed_ms": 0,
    "log": None,
    "engine_version": None,
    "engine": None,
    "skipped": None,
}


def _empty(reason=None, elapsed_ms=0):
    out = dict(EMPTY)
    out["skipped"] = reason
    out["elapsed_ms"] = int(elapsed_ms)
    return out


def _run_engine(settings, corpus, query):
    """Score with the configured engine, falling back to rules if needed.

    Returns (result, engine_actually_used).
    """
    wanted = (settings.get("engine") or "rules").strip().lower()

    if wanted == "model":
        predictor = model_engine.load()
        if predictor is None:
            # Not an error the user should see. The rules are a working
            # engine, so log why and use them.
            frappe.logger("item_duplicate_check").warning(
                "engine=model requested but unavailable (%s); using rules"
                % model_engine.load_error())
        else:
            result = model_engine.search(
                corpus=corpus, query=query, predictor=predictor,
                strong=settings["strong_threshold"],
                soft=settings["soft_threshold"], aliases=_ALIASES)
            return result, "model"

    result = search(
        corpus=corpus, query=query, weights=settings["weights"],
        strong=settings["strong_threshold"],
        soft=settings["soft_threshold"], aliases=_ALIASES)
    return result, "rules"


@frappe.whitelist()
def check_duplicate(item_name, cat_no=None, makes=None, item_code=None,
                    maintain_stock=1, description=None):
    """Does an item like this already exist?

    Returns
    -------
    dict
        band                "strong" | "soft" | "none"
        matches             up to 5 dicts, best first
        total_similar       how many exist at or above the soft band. Not
                            len(matches) -- that one is capped at 5, and the
                            catalogue holds 79 rows of one CAPEX name.
        cancelled           near misses the safety rules killed, for the log
        elapsed_ms          server time
        engine              "rules" or "model" -- which one actually ran
        log                 the Item Match Log row name, so the panel can
                            report back what the user did about it
        skipped             why nothing was checked, when nothing was

    Never raises. Never blocks anything.
    """
    started = time.perf_counter()

    try:
        settings = get_settings()

        # ---- 1. the kill switch -----------------------------------------
        if not settings["enabled"]:
            return _empty("disabled in settings",
                          (time.perf_counter() - started) * 1000)

        # ---- 2. HARD RULE 2: non-stock items are out of scope -----------
        #
        # AN EXPLICIT 0 SKIPS. A MISSING VALUE DOES NOT.
        #
        # The two are not the same thing and treating them as one broke the
        # feature. On Item Creation Form -- the form people actually use --
        # `maintain_stock` is a Check whose DEFAULT is 0, and the user ticks
        # it later: 9,638 of 11,396 saved rows have it set to 1. So on a new
        # form the value is 0 before anybody has decided anything, and
        # skipping on that meant the check never ran for anyone, whatever
        # they typed.
        #
        # "Maintain stock is off" is a statement. The absence of a statement
        # is not. The panel sends None until the user touches the field, and
        # only a deliberate 0 is honoured here.
        #
        # The rule itself is unchanged: the 79 CAPEX service rows all named
        # "electrical installation at mrna lab" carry a deliberate 0 and are
        # still skipped before any scoring.
        stated = maintain_stock is not None and str(maintain_stock).strip() != ""
        if stated and not frappe.utils.cint(maintain_stock):
            return _empty("maintain stock is off (CAPEX / service line)",
                          (time.perf_counter() - started) * 1000)

        # Everything arrives as a string over HTTP, but this method is also
        # called from the console and from scripts, where an Item Code can
        # genuinely be an int. cstr rather than str so None becomes ""
        # instead of "None".
        item_name = frappe.utils.cstr(item_name).strip()
        cat_no = frappe.utils.cstr(cat_no).strip() or None
        makes = frappe.utils.cstr(makes).strip() or None
        item_code = frappe.utils.cstr(item_code).strip() or None

        if not item_name:
            return _empty("no item name typed",
                          (time.perf_counter() - started) * 1000)

        # ---- 3 and 4. pick the engine, then search ----------------------
        query = Query(name=item_name, cat_no=cat_no, makes=makes,
                      item_code=item_code)
        result, engine = _run_engine(settings, get_corpus(), query)

        elapsed_ms = (time.perf_counter() - started) * 1000
        silent = bool(settings["silent_mode"])
        band = result["band"]
        shown = (not silent) and band in ("strong", "soft")

        # ---- 5. the log, whatever happens next --------------------------
        log_name = match_log.write(
            query=query,
            result=result,
            elapsed_ms=elapsed_ms,
            settings=settings,
            shown_to_user=shown,
            # Logged as 1 when nobody has said otherwise, which is what the
            # check actually assumed when it ran.
            maintain_stock=(frappe.utils.cint(maintain_stock) if stated else 1),
            description=description,
        )

        if settings["from_defaults"]:
            frappe.logger("item_duplicate_check").info(
                "settings unreadable, using shipped defaults")

        if silent:
            # Logged in full above; the user is told nothing.
            return {
                "band": "none",
                "matches": [],
                "cancelled": [],
                "total_similar": 0,
                "elapsed_ms": int(elapsed_ms),
                "log": log_name,
                "engine_version": settings["engine_version"],
                "engine": engine,
                "skipped": "silent mode",
            }

        return {
            "band": band,
            "matches": [m.as_dict() for m in result["matches"]],
            "cancelled": [m.as_dict() for m in result["cancelled"]],
            # How many exist in total, not how many fit in the list. The
            # dialog says "79 items like this already exist. Closest 5:".
            "total_similar": int(result.get("total_similar")
                                 or len(result["matches"])),
            "elapsed_ms": int(elapsed_ms),
            "log": log_name,
            "engine_version": settings["engine_version"],
            "engine": engine,
            "skipped": None,
        }

    except Exception:
        # HARD RULE 1. Whatever happened, the user is creating an item and
        # that must continue to work. Log it where a developer will find it
        # and hand back the same empty shape as "disabled".
        try:
            frappe.log_error(
                title="item_duplicate_check: check_duplicate failed",
                message=frappe.get_traceback())
        except Exception:
            pass
        return _empty("error (logged)", (time.perf_counter() - started) * 1000)


@frappe.whitelist()
def record_action(log_name, user_action, override_reason=None):
    """Record what the user did after a warning. Never raises.

    This is the column the whole log exists for, so it is a separate call:
    the user acts seconds or minutes after the check ran, and the answer must
    land on the row that produced the warning they actually saw.
    """
    try:
        ok = match_log.record_user_action(log_name, user_action,
                                          override_reason)
        return {"ok": bool(ok)}
    except Exception:
        try:
            frappe.log_error(
                title="item_duplicate_check: record_action failed",
                message=frappe.get_traceback())
        except Exception:
            pass
        return {"ok": False}


@frappe.whitelist()
def engine_status():
    """What is switched on, for the form panel and for support questions.

    Cheap, read-only, and safe to call on form load: the panel uses it to
    decide whether to attach any handlers at all.
    """
    try:
        settings = get_settings()
        corpus = get_corpus()
        wanted = (settings.get("engine") or "rules").strip().lower()
        model_ready = model_engine.is_available()
        return {
            "enabled": bool(settings["enabled"]),
            "silent_mode": bool(settings["silent_mode"]),
            "strong_threshold": settings["strong_threshold"],
            "soft_threshold": settings["soft_threshold"],
            "engine_version": settings["engine_version"],
            "engine_configured": wanted,
            "engine_effective": ("model" if (wanted == "model" and model_ready)
                                 else "rules"),
            "model_available": bool(model_ready),
            "model_error": model_engine.load_error(),
            "using_default_settings": settings["from_defaults"],
            "items_indexed": len(corpus),
            "index_generation": corpus.generation,
        }
    except Exception:
        try:
            frappe.log_error(
                title="item_duplicate_check: engine_status failed",
                message=frappe.get_traceback())
        except Exception:
            pass
        return {"enabled": False, "silent_mode": True, "error": True}
