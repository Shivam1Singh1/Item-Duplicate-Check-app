"""
settings.py  --  read Item Duplicate Check Settings, and never fail doing it.

WHY THIS FILE EXISTS
--------------------
Nothing in the app is allowed to hard-code a threshold. The thresholds are a
starting guess, not a measured result, and they are expected to move once
there is real traffic to tune them on. Moving them must not need a deploy.

THE ONE RULE
------------
This function cannot raise. It is called at the start of every check, and
HARD RULE 1 says the check must never be the reason an Item cannot be saved.
A missing doctype (app installed, migration not yet run), a corrupt row, a
Redis hiccup -- all of them fall back to the shipped defaults.

A FALLBACK IS NOT A SILENT FAILURE
----------------------------------
`from_defaults` comes back True whenever the real settings could not be read,
and api.py logs that. Otherwise a broken Settings row would look exactly like
a working one with default values, which is the kind of thing that is
discovered six months later.

WHICH ENGINE, AND WHAT EACH ENGINE READS
----------------------------------------
`engine` picks the scorer: "rules" (core/scorer.py) or "model"
(core/model.py). Both are in the code permanently, which decision 8 requires.

  - BOTH read `strong_threshold` and `soft_threshold`. The bands are a
    property of the warning, not of the scorer.
  - ONLY "rules" reads `weights`. They are the four weights of the
    hand-written sum. The model has no weights to configure; its parameters
    live in the saved model file.

An unrecognised value in `engine` falls back to "rules", because a typo in a
Select should not quietly disable the check.
"""

SETTINGS_DOCTYPE = "Item Duplicate Check Settings"

#: Band edges. Decision 10: a starting guess. At 0.92 the measured alert rate
#: was 33%, which is high -- expect to raise it once there is traffic.
DEFAULT_STRONG_THRESHOLD = 0.92
DEFAULT_SOFT_THRESHOLD = 0.75

#: Previously the weights of the removed weighted sum. Kept as the shape of
#: the field so a Settings row that has never been opened still reads cleanly.
DEFAULT_WEIGHTS = {
    "ordered": 0.47,
    "word_set": 0.21,
    "rare": 0.18,
    "makes": 0.14,
}

#: Shipped defaults. A site that has never opened the Settings form behaves
#: exactly as documented.
#: Which scorer runs. "rules" is the default because the model does not
#: clearly beat it -- 99.3% precision against 100.0%, for 1.3 points of
#: recall -- and decision 8 gives ties to the simpler option.
DEFAULT_ENGINE = "rules"

DEFAULTS = {
    "enabled": 0,                 # off until somebody turns it on, on purpose
    "silent_mode": 1,             # log, show nothing
    "engine": DEFAULT_ENGINE,
    "strong_threshold": DEFAULT_STRONG_THRESHOLD,
    "soft_threshold": DEFAULT_SOFT_THRESHOLD,
    "weights": dict(DEFAULT_WEIGHTS),
    "engine_version": "rules-1.0",
    "from_defaults": True,
}


def get_settings() -> dict:
    """Current settings as a plain dict. Falls back to DEFAULTS on any error.

    frappe.get_cached_doc is used rather than get_doc: a single doctype is
    cached per site, so this costs nothing per keystroke, and the cache is
    cleared automatically when somebody saves the form.
    """
    settings = dict(DEFAULTS)
    settings["weights"] = dict(DEFAULT_WEIGHTS)

    try:
        import frappe

        doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)

        settings["enabled"] = int(doc.enabled or 0)
        settings["silent_mode"] = int(doc.silent_mode or 0)
        settings["engine_version"] = (doc.engine_version
                                      or DEFAULTS["engine_version"])

        # Anything other than the two known values falls back to rules. A
        # typo in a Select should not silently disable the check.
        engine = (doc.get("engine") or "").strip().lower()
        settings["engine"] = engine if engine in ("rules", "model") \
            else DEFAULT_ENGINE

        # A zero threshold would warn about everything, which is far more
        # likely to be an empty field than a deliberate choice. Treat
        # "not set" as "use the default" rather than as zero.
        if doc.strong_threshold:
            settings["strong_threshold"] = float(doc.strong_threshold)
        if doc.soft_threshold:
            settings["soft_threshold"] = float(doc.soft_threshold)

        weights = {
            "ordered": doc.weight_ordered,
            "word_set": doc.weight_word_set,
            "rare": doc.weight_rare,
            "makes": doc.weight_makes,
        }
        if any(value is not None for value in weights.values()):
            settings["weights"] = {key: float(value or 0.0)
                                   for key, value in weights.items()}

        settings["from_defaults"] = False

    except Exception:
        # Deliberately broad. Whatever went wrong, the documented defaults
        # are a working configuration and a save must not be at risk.
        settings["from_defaults"] = True

    return settings


def is_enabled() -> bool:
    """The kill switch, on its own, for callers that need nothing else."""
    return bool(get_settings()["enabled"])
