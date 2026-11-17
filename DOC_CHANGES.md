# Document change log

Every change made to the documents in this app is recorded here.

**The rule for this app:** `README.md` is the source of truth. Anything else — the PDF, the
HTML, any summary — is derived from it. Before changing a document, read `README.md` first.
After changing it, add a row below.

| File | What it is |
|---|---|
| `README.md` | **The source.** The decision log. Every number, every rejected option. |
| `Item_Duplicate_Check_Record.html` | The printable version. Edit this, then re-print. |
| `Item_Duplicate_Check_Record.pdf` | Generated from the HTML. Never edit directly. |

Re-printing the PDF after editing the HTML:

```
chrome --headless --no-pdf-header-footer \
  --print-to-pdf=Item_Duplicate_Check_Record.pdf \
  file:///<full-path>/Item_Duplicate_Check_Record.html
```

---

## 2026-10-19 — Created the printable record

Read `README.md` (1,268 lines) and produced a 13-page A4 PDF of it. **No app code was
changed** — no Python file, no `hooks.py`, no data file was touched. The changes are
presentation only.

| # | Change | Detail |
|---|---|---|
| 1 | Language simplified | Technical terms moved out of the prose into a glossary on page 2 — precision, recall, separation, ablation, veto gate, label leakage. "Digit-bearing tokens" → "words containing a number". "Positive pairs" → "duplicate pairs". "Hard negatives" → "hard non-duplicates". Long sentences split. |
| 2 | Sections merged | README 10+11 → PDF 9. README 12+13 → PDF 10 and 11. README 8+9 → PDF 8. README 14+16 → PDF 12. README 17+18 → PDF 14. README 19 → PDF 15. **README section numbers no longer match PDF section numbers.** |
| 3 | Condensed | Repeated explanations removed; "other options" tables folded into prose where the rejection reason was one line. Went from 35 pages to 13. No decision, measurement table, or figure was dropped. |
| 4 | Design added | Cover page, pipeline diagram, orange-and-purple theme, highlighted rows for headline numbers, page-break rules so no table row, card or code block splits across a page. |
| 5 | Added — not in README | The "suggested order of work" list. It follows from the README's own findings but was never written down as a sequence. |
| 6 | Added — not in README | `tests/test_specs.py` added to the status table as **Not started**. The README calls it "the living rulebook" in two places, but the file does not exist — `tests/` holds only an empty `__init__.py`. |

---

## Open items for `README.md`

Two things found while reading it. **Neither has been changed in `README.md`** — they need a
decision first.

### 1. Two different numbers for the same field

| Field | Section 7 says | Section 17/18 says |
|---|---|---|
| `makes` separation | **+60.0** | **+57.1** |
| `custom_reference_old_item_code` separation | **+80.2** | **+79.3** |

Both were measured, on slightly different pair samples. The PDF quotes the section-7 figures
and says the ranking, not the decimal, is the finding.

This is worth reconciling, because it is the one place the README's claim that *"every number
here was measured"* contradicts itself. **Pick one sample, re-measure, and state which.**

### 2. A "known issue to fix" that is already fixed

README section 3 lists the size-set equality problem under **"Known issue to fix"**:

> The current size rule compares size *sets* for equality. That is too strict.

But `core/specs.py` already implements the subset rule (see `_check_size`, the SUBSET RULE
comment), and README section 19.4 measures the gain from it: **+14.5 points of recall for 0.6
of precision**. So section 3 contradicts section 19.4.

The PDF records it as **fixed**. **README section 3 should be updated to match.**

---

## 2026-10-21 — built steps A to I (corpus, scorer, doctypes, API, panel, harness)

Read `README.md` first: yes — sections 4, 5, 6, 7, 10, 11, 12, 13, 16, 19.
App code changed: `core/normalize.py`, `core/specs.py` (two bug fixes each
backed by a regression test), plus everything new listed below.

Full record with commands and output: **README.md section 20**.

| # | Change | Detail |
|---|---|---|
| 1 | `core/specs.py` — `QTY` anchored with `(?<![a-z0-9.])` | `b1500l` was parsed as 1,500 litres. Blocking only letters is not enough: `re.findall` shifts right and matches `500l` |
| 2 | `core/specs.py` — `spec_codes()` groups on the letter prefix | `b1500s` and `b1500l` were in stems `bs` and `bl`, so the clash was invisible and the pair was allowed |
| 3 | `core/specs.py` — skip narrowed to `not prefix and suffix in UNITS` | the old condition discarded any code ending in something unit-shaped |
| 4 | `core/normalize.py` — rule 7 separator now `[\s\-\u2010-\u2015]+` | `96-Well` stayed two tokens, contradicting section 19.1's own documented output |
| 5 | `api.py` — every input coerced with `cstr` | passing an int as `item_name` raised |
| 6 | `item_hooks.py` — handlers swallow everything | a `doc_events` handler that raises **aborts the save**; `corpus.invalidate()` guarding its own body is not enough |
| 7 | New: `core/corpus.py`, `core/makes.py`, `core/scorer.py`, `core/settings.py`, `core/log.py` | |
| 8 | New: three doctypes, installed on `site_local`, app disabled by default | |
| 9 | New: `api.py`, `hooks.py` rewritten, `item_hooks.py`, `tasks.py`, `install.py` | |
| 10 | New: `public/js/item_duplicate_check.js` | |
| 11 | New: 107 unit tests, 5 scripts in `scripts/` | |

### Documentation corrected in README.md

| Where | Was | Now |
|---|---|---|
| Section 19.1 example | `"Nunc(tm) 96-Well Plate " -> "nunc 96 well plate"` | `-> "nunc 96well plate"`, which is what rule 7 in its own table says |
| Installation | "Item Dedup Settings" | "Item Duplicate Check Settings" — the doctype's actual name |
| `spec_codes()` docstring | `"adaptor B-34" -> {"b": {34.0}}` | `-> {"": {"34"}}`. Rule 8 removes the hyphen before `specs.py` sees it. Documented, not fixed: the pair still cancels, and joining a letter to a digit would be an eleventh normalisation rule |
| Section 14 open question | unanswered | answered: creation stopped Feb 2026, measured |
| Current status table | 6 parts "Not started" | all done |

### Numbers that disagree with the decision record, and why

| Figure | Record | Measured now | Cause |
|---|---|---|---|
| Items skipped by Maintain Stock | "79 CAPEX rows" | **4,340** | the 79 are one example, not the size of the exclusion |
| Cat No positive pairs | 5,473 | **6,416** | +175 from treating separators as meaningless (all 65 merges inspected, all genuine); the rest is Excel 25,590 rows vs `site_local` 22,204 |
| Items in a Cat No clash | 7,781 | 7,788 | same two snapshots, 0.1% apart |
| Distinct Cat No values | ~10,500 | 10,481 | as above |
| Precision at 0.92 | 97.9% | **100.0%** | mostly changes 1 and 2 above |
| Recall@5 | 89.7% search / 79.5% with veto | **85.5%** | end to end, veto included |

### Still open

- `miltenyi` vs `miltenyi biotec`, `millipore` vs `merck`, and whether
  `invitrogen` / `fisher scientific` should fold into `thermo`. Listed for human
  review by `scripts/build_makes_alias.py`; not guessed.
- Thresholds are still the starting guess. The silent run cannot supply the
  traffic to tune them (step A), so tuning has to come from a replay.

---

## 2026-10-21 (later the same day) — search removed, method changed to ML

Instructed by the project owner: the method is a **machine learning model**,
and the search implementation was to be removed. Concern was raised twice
before acting (a model cannot supply its own candidates; there is no git
history to recover from) and the instruction was confirmed.

**Backup, because this app is not a git repository:**
`/home/shivam/item_duplicate_check_BACKUP_2026-10-21.tar.gz` (28 MB, verified
to contain `core/corpus.py`, `core/scorer.py`, both test files and the README
as they stood before the removal). That archive is the only copy.

### Deleted

| File | Lines |
|---|---|
| `core/corpus.py` | 482 |
| `core/scorer.py` | 643 |
| `tests/test_corpus.py` | 254 |
| `tests/test_scorer.py` | 478 |
| `scripts/index_stats.py` | 163 |
| `scripts/latency_check.py` | 123 |

Unit tests went from **107 to 31**.

### Rewired first, so the site was never left broken

The app is installed on `site_local`, and `item_hooks.py` was imported on
**every Item save**. Deleting `core/corpus.py` without rewiring would have
made every Item save fail on a site with 22,204 items. So these four were
changed before anything was deleted:

| File | Now |
|---|---|
| `api.py` | validates input, applies the kill switch and the Maintain Stock rule, logs, returns `skipped="model not built"`. Two seams marked `MODEL GOES HERE` |
| `core/settings.py` | thresholds inlined; no longer imports from the deleted scorer |
| `item_hooks.py` | handlers are deliberate no-ops, guard retained |
| `tasks.py` | nightly job is a no-op that logs why |

Verified afterwards: all 11 runtime modules import, all four `doc_events`
resolve to callables, `api.check_duplicate` answers safely in five states,
the nightly job runs, and a real Item re-saves on `site_local`.

### Kept but not runnable

`scripts/eval_harness.py` and `scripts/smoke_test.py` now exit with an
explanation instead of an `ImportError`. They are kept because the
measurement method in them — Cat No labels with the Cat No hidden, hard
negatives from the system's own top results, 70/15/15 by product group with
`random_state=42` — is independent of how a pair is scored, and the model has
to be judged the same way or its numbers compare to nothing.

### README

- A **DIRECTIVE** block at the very top: the method is a machine learning
  model. It also records the three constraints that survive the change, so
  nobody re-derives them: no candidate source exists, `cat_no` can never be a
  feature, and the veto gate stays on top of the model.
- Decision 0 rewritten. Decisions 4 and 5 replaced by stubs — stubs rather
  than deletions, because sections 6 to 20 refer to decisions by number.
- Section 20 marked as history. Section 19.4 marked as the benchmark.
- Status table: the two modules marked REMOVED, and two new rows added for
  what is now missing.

### What this leaves

The app is **non-functional by design** until the model and a candidate
source exist. `enabled=0` and `silent_mode=1`, so nothing is visible to users
either way.

---

## 2026-10-21 (third entry) — data preprocessing completed for the model

Instructed: complete the data preprocessing. `sklearn 1.9.1` and
`scipy 1.17.1` installed into the shared bench env on instruction.
`core/corpus.py` and `core/scorer.py` restored from the backup — the model
needs a candidate shortlist, and decision 8 requires the hand-written scorer
as both the baseline to beat and the fallback.

Full record: **README.md section 21**.

### Audited what was there first

`data/slim_*.csv` split sizes match decision 16 exactly (17,914 / 3,838 /
3,838). But: **141 identical normalised names span two splits** — decision 16
linked product groups on the RAW name, so `"Sodium Carbonate"` and
`"sodium carbonate."` landed on opposite sides. And 3,386 of the 25,590 rows
no longer exist in `site_local`. And there were no pair rows, labels or
features anywhere.

### Built

| File | Lines | What |
|---|---|---|
| `core/features.py` | 332 | 21 pair features, pure Python |
| `tests/test_features.py` | 331 | 26 tests, 2 of them leakage guards |
| `scripts/preprocess_audit.py` | 193 | what was wrong with the CSVs |
| `scripts/build_training_set.py` | 394 | groups, split, pairs, features |
| `scripts/validate_training_set.py` | 268 | six checks, exit 1 if unfit |

Output: `data/pairs_{train,validation,test}.csv` — **22,599 pair rows**,
3,311 / 692 / 734 positives at roughly 1:3.5 negatives. Leakage 0 on all four
signals. Unit tests 107 → **133**.

### Bugs found while building

| # | Bug | Found by |
|---|---|---|
| 1 | `difflib.ratio()` is **not symmetric** — 0.381 one way, 0.333 the other. An asymmetric feature makes the answer depend on which item the user opened first | `test_features` symmetry test |
| 2 | Same latent bug in `core/scorer.py`; `search()` also had to stop using its cached matcher for the exact value, or it would disagree with `score_pair()` | the above |
| 3 | 5,180 duplicate rows — negatives generated per positive *pair*, so a 3-item Cat No group asked for the same left item twice | `validate_training_set.py` |
| 4 | 1,561 more — `(X,Y)` and `(Y,X)` are the same example, and both orders occur | `validate_training_set.py` |
| 5 | `idf_shared_fraction` = `1.0000000000000002` on 181 rows | `validate_training_set.py` |

### Findings worth acting on before training

- `makes_agree` is the strongest single field, separation **+0.480** — the
  alias table earning its keep.
- **Four features carry nothing**: both `requester_*` (19.8% coverage),
  `department_abstain`, `same_category_other_division` (only 2.6% of
  duplicates are cross-division). Kept so the finding is visible.
- `naming_series_differs` separation is **−0.093**, where decision 7 measured
  the signal as inverted and so predicted positive. Worth checking before
  relying on it.
- The veto gate already rejects ~33% of the non-duplicates, at a cost of
  2.7–5.7% of the real duplicates.

### Next

Train the model (decision 8: Logistic Regression, Gradient Boosting as
challenger), then plug it into the two `MODEL GOES HERE` seams in `api.py`.

---

## 2026-10-21 (fourth entry) -- model trained, both engines wired, stale state swept

Full record: **README.md section 22**.

### The model

Trained on the 22,599 pair rows from section 21. Gradient boosting beat
logistic regression (PR AUC 0.9114 against 0.8925) and was saved.

| At 0.92, validation | Rules | LR | GB |
|---|---|---|---|
| precision | **100.0%** | 97.6% | 99.3% |
| recall | 64.2% | 59.2% | **65.5%** |

Test, read once: precision 99.6%, recall 65.3% -- agrees with validation to
0.3 points, so the split is clean and nothing is overfitted.

**Net: -0.7 precision, +1.3 recall.** Decision 8 predicted "+1.2 points" and
says to ship the rules unless the model clearly wins. It does not, so
`rules` is the default engine. Both engines are permanent, which decision 8
also requires.

### Decision 8 loses one of its three reasons

It chose logistic regression partly for readable weights. They are not
readable on this feature set: `ordered_similarity` fits at **-1.201** because
the name-similarity features are collinear and `idf_shared_fraction` (+4.351)
absorbed the signal. The winning model, gradient boosting, has no readable
weights at all. `core/model.py` therefore builds its reason string from
feature VALUES, not from weights.

### Built

| File | What |
|---|---|
| `scripts/train_model.py` | fits both, calibrates, compares against the baseline, reports the decision-8 verdict |
| `core/model.py` | load with a feature-order check, prefilter, batched predict, veto on top |
| `tests/test_model.py` | 16 tests; the important ones assert the three ways a model is refused |
| `scorer.cat_no_step0()` | Step 0 extracted and shared -- the model path had none |
| Settings: `engine` select | `rules` / `model`, default `rules` |

Unit tests 133 -> **149**.

### Bugs found

| # | Bug |
|---|---|
| 1 | `difflib.ratio()` is not symmetric (0.381 vs 0.333). Fixed in `features.py` and `scorer.py` |
| 2 | `search()`'s cached matcher disagreed with `score_pair()` on the same pair |
| 3 | IDF sums asymmetric in the last bits -- set iteration order plus non-associative float addition. Fixed by sorting before summing |
| 4 | `predict_proba` raises on an empty array; an empty shortlist is normal |

### Stale state from the earlier delete-and-restore

Restoring `corpus.py` and `scorer.py` did not restore what had changed
around them. All five found by re-checking:

| Was | Effect |
|---|---|
| `item_hooks.py` a no-op | **the index would never have been invalidated** |
| `tasks.py` a no-op | no nightly rebuild |
| guards on `eval_harness.py`, `smoke_test.py` | both measurement scripts refused to run |
| `api.py` the gutted shell | **the entry point did nothing** -- smoke test found 0 of 5 exact duplicates while both engines sat unused |
| `site_local` at `enabled=1, silent_mode=0` | the app was live and warning users |

The last one has a cause worth remembering: a script that saves a setting and
"restores what it found" preserves whatever state it started in, so a chain
of them drifts. The scripts now restore the SHIPPED state explicitly.

### State

149 tests OK. Dataset six checks OK. Rules engine on validation: precision
100.0%, recall@5 85.5%, p95 16.3 ms, 15/15 spec pairs. Smoke test 15/15.
HARD RULE 1 holds under three injected failures. `site_local`: `enabled=0`,
`silent_mode=1`, `engine=rules`; Item Match Log emptied of test rows.

---

## 2026-10-21 (fifth entry) -- the check was on the wrong form

The panel was attached to `Item`. Nobody types into `Item`. The form people
fill in is **Item Creation Form**, which is approved and only then becomes an
Item.

```
Item Creation Form rows: 11396     first 2024-03-18     last 2026-10-21 16:07
```

**Step A measured the wrong table.** Its volume conclusion stands -- both
tracks slowed after February 2026 -- but its conclusion about WHERE the panel
belongs was wrong, and that was the part that mattered.

### Measured on the real form

Field coverage is BETTER than on the catalogue: `cat_no` 88.4% against 70.6%,
`makes` 91.0% against 72.6%, `department` 100% against 49.0%. The request
form is filled in more carefully than the row it creates.

Replaying the 40 most recent stock requests through the rules engine:
**28 strong (70%), 3 soft, 9 none.** Examples: "Mouse IL-4 ELISA Kit." against
"Mouse IL-4 ELISA Kit", "Pig Semen Cup" against itself, "Nursery pens"
against itself.

### Changed

| File | What |
|---|---|
| `hooks.py` | `doctype_js` now covers both forms |
| `public/js/item_duplicate_check.js` | a FIELD MAP per doctype, and bindings generated from it |

The one field that differs: `Item` has `is_stock_item`, `Item Creation Form`
has `maintain_stock`. Reading the wrong name would have silently disabled
HARD RULE 2 -- the rule protecting the 79 intentional CAPEX service rows.
`item_code` is sent only from the Item form, where a row must not match
itself.

Verified with real payloads from the form, including the gate withholding a
50 L bag from a 500 L one, and the CAPEX skip firing on
"electrical installation at mrna lab".

### The app is now ON

`enabled=1, silent_mode=0, engine=rules` on `site_local`, so the warning is
visible on the form. This departs from decision 10, which asks for a two-week
silent run first; it was switched on deliberately so the behaviour could be
seen on the screen it was asked for. To go back:

```
Item Duplicate Check Settings -> Silent Mode = 1   (logs, shows nothing)
Item Duplicate Check Settings -> Enabled    = 0   (does nothing at all)
```

149 tests OK. Item Match Log emptied of test rows.

---

## 2026-10-21 (sixth entry) -- the check was invisible on the form it was wired to

Attaching the panel to Item Creation Form was not enough: it drew nothing,
for everybody, whatever they typed, and it failed silently.

**Cause.** On that form `maintain_stock` is a Check whose **default is 0**.
HARD RULE 2 skips on 0, so the check exited before scoring on every new form.
Saved rows show the field really is set later: 9,638 at 1 against 1,758 at 0.

**Fix.** An explicit 0 and an untouched default are different things -- one is
a statement, the other is the absence of one. The panel now sends `null`
until the user touches the field, and `api.check_duplicate` honours only a
stated 0:

```python
stated = maintain_stock is not None and str(maintain_stock).strip() != ""
if stated and not frappe.utils.cint(maintain_stock):
    return _empty("maintain stock is off (CAPEX / service line)")
```

HARD RULE 2 is unchanged -- the 79 CAPEX rows carry a deliberate 0 and are
still skipped. The log records 1 when nothing was stated, which is what the
check actually assumed.

| File | Change |
|---|---|
| `api.py` | skip on a STATED 0 only |
| `public/js/item_duplicate_check.js` | `stock_touched` flag, `maintain_stock_state()` returns null until touched |
| `scripts/smoke_test.py` | section 5, the six-case matrix |

Smoke test now 21 of 21 (was 15). 149 unit tests OK. Assets rebuilt, cache
cleared, Item Match Log emptied of test rows.

**The general lesson:** a doctype default is not a user's answer. Any rule
that skips on a falsy value will misfire wherever that field defaults to
falsy, and it will do so without any error.

---

## 2026-10-21 (seventh entry) -- one warning before saving, naming the item code

Asked for: a single warning before the save goes through, showing the item
code and name of the existing item.

### What it does

`before_save` on both forms shows ONE dialog listing every match with its
**item code**, item name, score and reason. The code matters: the name alone
is not actionable, because the whole problem is that several rows share a
name.

### How it pauses a save without blocking one

Checked in `frappe/public/js/frappe/form/form.js` rather than assumed:

```javascript
() => this.script_manager.trigger("before_save"),
() => {
    if (!frappe.validated) { fail(); return; }
    frappe.ui.form.save(...);
},
```

`trigger()` awaits what a handler returns, and `fail()` only re-enables the
Save button. So `frappe.validated = false` pauses the save cleanly -- nothing
raised, nothing stuck, Save works on the next press.

`validate` -- the hook that can refuse a save outright -- is still not used
anywhere in this app, and neither is `frappe.throw`.

### Why it is still a warning

- "Save anyway" is one click, and it is the primary button
- shown **once per form**; the second Save press never asks again
- 2.5 second timeout: a slow server does not hold the save
- the promise resolves on every failure path, never rejects (a rejection
  would reach `.catch(fail)` and stop the save invisibly)
- no dialog at all when the app is disabled, the record exists, the name is
  under three characters, or `maintain_stock` is a deliberate 0

Esc or the X counts as "let me check", not consent, and is logged `gave up`.

### Also

The live banner now shows `CODE - Name` instead of just the name.

Both buttons write back to Item Match Log -- `created anyway` / `gave up` --
which is the judgement data decision 12 exists to collect.

149 tests OK. Assets rebuilt, cache cleared, test log rows removed.

---

## 2026-10-22 — The warning says how many exist, and stops padding

Read `README.md` first: yes — section 10 (*What the user sees*) and section 11.
App code changed: `core/scorer.py`, `core/model.py`, `api.py`, `warn.py`,
`public/js/item_duplicate_check.js`, `tests/test_scorer.py`.

**Reported as:** *"since there are 79 items why am I getting only 4"* — the server dialog on
Item Creation Form listed five rows for a name the catalogue holds 79 of, and said nothing
about the rest. `TOP_N = 5` was working as designed; the display was not.

| # | Change | Detail |
|---|---|---|
| 1 | `search()` returns `total_similar` | Every existing item at or above the soft band, counted **before** the top-five truncation. Added to the rules engine (`scorer.py`) and the model engine (`model.py`), so both paths return the same shape |
| 2 | Early exit relaxed | It stopped as soon as the top five was settled, so nothing past the fifth-best could be counted. It now stops when the bound can neither enter the top five **nor** reach the soft band. Still a true ceiling — `TestEarlyExit` against brute force still passes unchanged |
| 3 | Dialog headline | *"81 items like this already exist. Closest 5:"* — singular/plural handled, and the "closest N" clause only appears when the total is larger than the list |
| 4 | Removed from the dialog | *"The catalogue already has:"*; the per-row reason sub-line; *"Do you still want to create this one?"* and its two-sentence explanation. Replaced by one muted line |
| 5 | Below-threshold rows no longer padded in | The dialog listed the top five whatever they scored. A one-match query (`CPU`) showed five rows and now shows one, which is what the count says |
| 6 | Same change in the client dialog | `public/js` carried the identical wording. Both dialogs now read the same; `total_similar` falls back to the list length so an older server cannot make it say "0" |
| 7 | Reason text kept on the inline panel | Decision 10 (*show the reason, not the number*) is still served there, where it appears once instead of five times |

**Measured** on the live catalogue (22,207 items, best of 5 runs): worst case **110.0 ms →
117.3 ms** against a 300 ms budget. Duplicate-heavy queries 1.6 → 11.0 ms. Table in README
section 10.

166 tests OK (4 new: `TestTotalSimilar`).

**Not done:** `Item Match Log` does not store the count — that needs a DocType field and a
migration. The log still records the top five, as before.

---

## 2026-11-17 — The warning stops letting an edited name through

Read `README.md` first: yes — section 11 (*Never block a save*) and section 23.
App code changed: `public/js/item_duplicate_check.js`.

**Reported as:** *"I have created a duplicate item — while saving it should show me the
similar item and ask whether I still want to create it."* On `REQ/ITEM/2026-2027/00796`
the dialog never appeared and `Serological Pipette 50 mLL` was created.

**It had appeared.** Item Match Log holds the whole sequence:

| Time | Typed | Band | `user_action` |
|---|---|---|---|
| 10:38:03 | Serological Pipette 50 mL | strong | |
| 10:38:04 | Serological Pipette 50 mL | strong | `gave up` |
| 10:38:11 | Serological Pipette 50 m**LL** | strong | `created anyway` |
| 10:38:34 | Serological Pipette 50 m**LL** | strong | (server `validate`) |

`gave up` is written only by the save dialog's dismiss handler, so the dialog ran for
`50 mL`. The user then added an `L` and pressed Save, and nothing was shown.

**Cause.** `Panel.warned` was one boolean per FORM. Once the dialog had been shown, every
later Save took the short circuit — which also set `__idc_warned`, suppressing `warn.py`'s
message as well, so the second save produced no warning on either side. A user who is
warned and then edits the name is the exact case section 11 is about (*"the response to a
block was to type Diiary instead of Diary"*), and the flag was helping it through.

| # | Change | Detail |
|---|---|---|
| 1 | `warned` → `warned_for` | Stores the question, not the fact. `null` until a warning is shown |
| 2 | `save_signature()` | `JSON.stringify` of trimmed, lowercased `item_name` + `cat_no` + `makes` — the three fields sent to `check_duplicate` that change which items come back. `JSON.stringify` rather than a join so `"ab"+"c"` cannot collide with `"a"+"bc"` |
| 3 | Short circuit now compares | `warned_for === signature` → proceed. Anything else is a question that has not been asked |
| 4 | Re-asking is bounded | A changed signature only produces a dialog when the new values still match something. Fix the name properly and nothing is shown |

**Unchanged on purpose:** pressing Save twice without editing still asks once — being asked
twice is how a warning becomes an obstacle. No `validate` handler, no `frappe.throw`, and
every failure path still saves. HARD RULE 1 is untouched.

**Verified** with a Node harness that stubs Frappe and replays the sequence above:
first Save asks, unchanged Save does not, the edited name asks again, unchanged Save does
not, a clean name never asks. `node --check` clean; assets rebuilt, cache cleared.

**Not done:** the real Item is created by the Server Script *"Item Creation"* on **After
Submit**, and 11,846 forms have been submitted. `warn.py` returns early on
`if not doc.is_new()`, and `before_save` does not run on submit — so a draft that was saved
past the warning and submitted later is never warned about again. That is the moment the
catalogue row actually appears, and it is still uncovered.

---

## Template for the next entry

```
## YYYY-MM-DD — <what you did>

Read `README.md` first: <yes / which sections>.
App code changed: <none / which files>.

| # | Change | Detail |
|---|---|---|
| 1 |        |        |
```
