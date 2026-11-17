# Item Duplicate Check

> ## DIRECTIVE — READ THIS BEFORE CHANGING ANYTHING
>
> **The chosen method is a MACHINE LEARNING MODEL.**
> Instructed by the project owner on 2026-10-21. This overrides decision 0
> below, which chose a search-first approach with the model added last.
>
> Any agent or developer picking this repository up: do not quietly revert to
> the hand-written scorer as "the method". The model is the method.
>
> **What that means mechanically, so nobody has to guess:**
>
> - The model replaces the **scoring function** — the weighted sum in
>   `core/scorer.py`. That is the part being swapped out.
> - A model compares **two** items. It cannot go and find the second one.
>   22,204 items is about 246 million possible pairs, so something must
>   shortlist candidates before the model can score them. Whatever supplies
>   that shortlist stays, because without it the model has nothing to score.
> - The **veto gate** in `core/specs.py` stays on top of the model's score.
>   A model that has never seen a 500 g pack next to a 1 kg pack will call
>   them the same product; the gate is what stops that.
> - `cat_no` must **never** become a model feature. It is what makes the
>   training labels, so feeding it in teaches the model one useless rule and
>   nothing about names. See decision 6.
>
> **Status: the model IS BUILT** (section 22). Measured against the
> hand-written rules it is a near-tie -- 0.7 points of precision given
> up for 1.3 points of recall -- so decision 8's own rule applies and
> `rules` is the shipped engine, with `model` selectable in Settings.
> Both are permanent. Changing that is a decision, not a bug fix.

Warns when someone is creating an Item that already exists in the catalogue.

The check runs **while the user is typing**, before they save. It searches all existing
items, and shows the ones that look like the same product. It **never stops the user from
saving** — it only warns.

---

## How to read this file

This is a **decision log**. For every step of the system it records:

- **What we do** — the approach we chose
- **Other options** — what else we could have done
- **Why not those** — the reason we rejected them
- **Proof** — the measurement that decided it

Every number here was measured on real data. Two sources are used, and each
figure says which:

- **`site_local`** — 22,204 items, the database snapshot used for the early analysis
- **`Item.xlsx`** — 25,590 items, the current Excel export used from section 14 onward

Nothing here is a guess. Where something is *not* measured, it says so.

**Simple rule for anyone changing this app:** if you change a decision below, measure it
first and update this file. The numbers are the reason, not the opinion.

---

## Table of contents

| # | Decision |
|---|---|
| 0 | The method: a machine learning model |
| 1 | A separate app, not code inside another app |
| 2 | Cleaning up the text |
| 3 | The safety rules (the "veto gate") |
| 4 | How we find candidate items — REMOVED |
| 5 | How we score a pair — REMOVED |
| 6 | Cat No is a lookup, not a clue |
| 7 | Which Item fields we use |
| 8 | The machine learning model |
| 9 | Where training examples come from |
| 10 | What the user sees |
| 11 | Never block a save |
| 12 | Logging every check |
| 13 | How we measure success |
| 14 | The dataset we work from |
| 15 | How Item Code is built |
| 16 | Train / validation / test split |
| 17 | Which columns we keep |
| 18 | Major and minor attributes |
| 19 | The core modules — what each file does |
| 20 | The build — steps A to I |
| 21 | Data preprocessing for the model |
| 22 | The model, and which engine ships |
| 23 | Item Creation Form — where the check actually belongs |

---

## 0. The method: a machine learning model

### What we do

**A machine learning model decides whether two items are the same product.**

Instructed by the project owner on 2026-10-21. This replaces the earlier
search-first decision that used to occupy this section.

### What was here before, and what happened to it

This section used to argue for a search system with a hand-written scorer,
with a model added last as one small part. That approach was built, measured
and then **removed on instruction**:

| Removed | Was |
|---|---|
| `core/corpus.py` | rare-word candidate index, 482 lines |
| `core/scorer.py` | the hand-written weighted sum, 643 lines |
| `tests/test_corpus.py`, `tests/test_scorer.py` | 732 lines, 76 tests |
| `scripts/index_stats.py`, `scripts/latency_check.py` | measurement tools |
| decisions 4 and 5 below | the candidate search and the weighted sum |

A backup of the app as it stood before the removal is at
`/home/shivam/item_duplicate_check_BACKUP_2026-10-21.tar.gz`. This app is not
a git repository, so that archive is the only copy.

Section 20 keeps the build record and the measurements taken at the time.
They are history, not a recommendation: they describe code that is no longer
in the app.

### What the model still has to work around

Three constraints survive the change of method, because they are properties
of the data rather than of the approach:

1. **A model compares two items. It cannot find the second one.** 22,204
   items is about 246 million possible pairs, and the budget is 300 ms per
   keystroke, so something has to shortlist candidates before the model is
   asked anything. **There is currently no candidate source in this app.**
   This is the first thing the model needs and it does not exist yet.

2. **`cat_no` cannot be a feature.** It is what makes the training labels, so
   using it as a clue is label leakage: the model would score beautifully in
   testing and fail on the 31% of items that have no Cat No. See decision 6.

3. **The veto gate stays on top of the model.** A model trained on this data
   will call a 500 g pack and a 1 kg pack the same product, because by name
   they almost are. Decision 3 measured what the gate is worth: precision
   78.1% without it, 91.0% with it.

### Status

**The model is not built.** `api.check_duplicate` is wired, logs every call
and returns `skipped="model not built"`. The two places it plugs into are
marked `MODEL GOES HERE` in `api.py`.

---

## 1. A separate app, not code inside another app

### What we do

A standalone Frappe app called `item_duplicate_check`.

### Other options

| Option | Why someone might pick it |
|---|---|
| Add the code to an existing app | Fewer apps to manage |
| Server Script / Client Script in the UI | No deployment needed |
| Change ERPNext's Item doctype directly | Everything in one place |

### Why not those

- **Inside another app** — you cannot switch it off on its own. If this misbehaves during
  live item creation, you need to disable *one* thing, fast.
- **Server Scripts** — this app has several modules, cached data, background jobs, unit
  tests and later a model file. That does not fit in a script box, and cannot be tested.
- **Changing ERPNext directly** — breaks on the next ERPNext upgrade. This app only adds
  hooks and new doctypes. Core Item is untouched.

### Proof

Not a measurement, but a real example from this very system: three custom fields
(`custom_item_creation_reference`, `custom_item_creation_type`, `custom_category`) still
exist on Item and still hold data, but the app that created them was removed. Their link
targets are gone. That is exactly the mess a clean, removable app avoids.

---

## 2. Cleaning up the text

### What we do

Before comparing two names, we clean them the same way. **Ten rules, and the
order is locked** — several only work if an earlier one has already run.

| # | Rule | Found in the data (25,590 items) |
|---|---|---|
| 1 | Repair mojibake `Âµ` → `µ` | 8 rows, e.g. `Millex,33mm,0.22Âµm` |
| 2 | **Remove ™ ® ©** | **3,249 items (12.7%)** |
| 3 | Unicode NFKC normalise | — |
| 4 | Non-breaking space → normal space | **237 items (0.9%)** |
| 5 | µ and μ → `u` | **540 items (2.1%)** |
| 6 | Lower case | — |
| 7 | Join number to unit (`50 ml` → `50ml`) | — |
| 8 | Join number to percent (`70 %` → `70%`) | — |
| 9 | Strip punctuation, keep `.` and `%` | decimals and grades must survive |
| 10 | Drop trailing dot, collapse spaces | **1,369 (5.3%)** dots, **428 (1.7%)** double spaces |

Rule 2 must come **before** rule 3, rule 1 before rule 5, and rule 7 before
rule 9. See section 19.1 for the bug that proved it.

### Other options

| Option | Why not |
|---|---|
| Compare names as they are | 12% of names carry ™ alone — they would never match |
| Remove *all* punctuation including digits | Destroys sizes, which we need for step 3 |
| Use a spell-checker | Scientific names are not dictionary words. It would corrupt them. |
| Sound-alike codes (Soundex, Metaphone) | **Rejected outright** — see below |

### Why we rejected sound-alike matching

Soundex and Metaphone turn a word into a code based on how it *sounds*, so `Smith` and
`Smyth` match. They were designed for **English surnames**.

Your catalogue is scientific names — `PmeI`, `Axygen`, `2-Mercaptoethanol`, `Nunc`.
Sound-codes over chemical names produce near-random matches. And the one case it would
have helped (`DNase` vs `DNAse`) is already solved by rule 6, lower-casing.

It would add wrong answers and find nothing new.

---

## 3. The safety rules (the "veto gate")

**This is the most important part of the app.** More accuracy comes from this one module
than from everything else combined.

### What we do

After scoring, we check whether the two items are actually *different products wearing
similar names*. If so, the match is **cancelled** — score forced to zero — no matter how
similar the text was.

Four checks:

| # | Check | Rule |
|---|---|---|
| 1 | **Size** | Convert to a base unit and compare. `1kg` = `1000g`. Conflict on a shared dimension → cancel. |
| 2 | **Digit tokens** | Any word containing a number. Both sides have them and they differ → cancel. |
| 3 | **Negation words** | `no`, `without`, `non`, `free`, `reduced`, `anti`. Different → cancel. |
| 4 | **Grade words** | `absolute`, `HPLC`, `sterile`, `RNase free`, `70%`, etc. Different → cancel. |

### Why this matters — real pairs from your catalogue

Every one of these scored high on text similarity, and every one is a **different product**:

```
Glass bent inlet adaptor B-34   vs  B-24             different size
Sartocon Slice Cassette 100kDa  vs  50kDa            different filter
Vivaspin 20 MWCO 5 kDa          vs  3 kDa            different cut-off
Festo Push-in T-connector QST-6 vs  QST-8            different part
Dengue virus strain 2           vs  strain 1         different strain
TrypLE Express no phenol red    vs  Phenol Red       opposite product
Nuclease-free Water B1500S      vs  B1500L           small vs large pack
-20°C mini cooler               vs  0°C mini cooler  different
Glycine 500g                    vs  1kg              different size
```

The pattern: **the distinguishing detail is almost always a word containing a number, or a
negation word.** That one observation is the whole design.

### Other options

| Option | Why not |
|---|---|
| Only check pack size (ml, g, kg) | Misses `B-34` vs `B-24`, `100kDa` vs `50kDa`, `no phenol red`. Measured 10 points worse. |
| Let the model learn it | **Tested. The model gave size conflict a weight of only −0.23** — it treats an absolute rule as a mild preference. A hard rule is better. |
| No safety rules at all | 78.1% precision. Not usable. |

### Proof

| Version of the gate | Precision | Recall |
|---|---|---|
| No gate | 78.1% | 88.5% |
| Size and grade only | 81.4% | 89.7% |
| **All four checks** | **91.0%** | **79.5%** |

The full gate trades 10 points of recall for 10 points of precision. We take that trade,
because a wrong warning costs user trust permanently, while a missed duplicate costs one
extra row.

### Known issue to fix

The current size rule compares size *sets* for equality. That is too strict:

```
{0.22um, 33mm}  vs  {0.22um}     ← same product, one name lists more detail
```

The correct rule is **conflict on a shared dimension**, not set inequality. `500g` vs
`1000g` are both mass and differ → cancel. `{0.22um, 33mm}` vs `{0.22um}` have no
disagreement → allow. This should win back several recall points.

### The rule for maintaining this module

**Every time the tool shows a wrong warning in real use, add it to `tests/test_specs.py`
first, watch it fail, then fix the rule.** Never fix a false positive without leaving a
test behind. This test file is the living rulebook.

---

## 4. How we find candidate items

**REMOVED on 2026-10-21.** The method is a machine learning model; see the
DIRECTIVE at the top of this file and decision 0.

This section described a rare-word index that shortlisted candidates. The
code (`core/corpus.py`) has been deleted.

**The requirement it existed to meet has not gone away.** A model scores a
pair; it cannot find the second item. Something must still shortlist
candidates before the model runs, and **nothing in the app does that at the
moment**. Whatever replaces it has one hard constraint worth keeping from the
measurement that was done here:

> Narrowing candidates by Item Child Group was the original plan and it
> failed: **80.5% of true duplicate pairs sit in different child groups**,
> because the people who created the duplicates also filed them under
> different categories. Measured recall@5 of 24.7% against 89.7%. Do not
> shortlist by category.

---

## 5. How we score a pair

**REMOVED on 2026-10-21.** The model does the scoring. See decision 0.

This section described a hand-written weighted sum over four components.
The code (`core/scorer.py`) has been deleted.

Two findings from it are worth carrying into the model, because both were
measured rather than assumed:

- **A length penalty is needed.** Raw word-set overlap scores a perfect 1.0
  whenever one name's words are a subset of the other's, which is misleading:
  `"DNase I, RNase-free (1 U/uL)"` against `"DNAse"` scored 1.00. When a
  model was trained on this data it **independently learned the same
  correction** - a negative weight on raw set overlap and a positive one on
  set overlap times length.
- **Manufacturer agreement must be able to abstain.** 29% of items have no
  usable `makes`. Scoring a blank as disagreement caps those items below the
  strong threshold permanently, so a blank has to drop out of the
  calculation rather than count against the pair.

---

## 6. Cat No is a lookup, not a clue

### What we do

`cat_no` (the manufacturer's catalogue number) is used in **Step 0 as a direct lookup**.
If two items have the same Cat No, we return that answer immediately with high confidence.

It is **never** given to the scorer or to the model as a clue.

### Why — this is subtle and important

Our only source of training examples is "these two items share a Cat No, so they are the
same product."

If the model *also* sees Cat No as a clue, it learns one useless rule — *"same Cat No means
same item"* — and learns nothing about names. It would score beautifully in testing and
fail completely on the **31% of items that have no Cat No at all**.

This mistake is called **label leakage**. Keeping the two apart avoids it.

It also means our accuracy numbers are honest: we measured the scorer *against* Cat No,
which is a fair test precisely because the scorer never sees it.

### Proof it is worth using as a key

- 10,500 different Cat No values in use
- **3,319 of them are already shared by more than one item, covering 7,781 items (35% of
  the catalogue)**
- We checked 8 random groups by hand: **all 8 were genuine duplicates**

### One warning

A Cat No match is strong evidence, **not proof**. The safety rules in step 3 still apply on
top of it. Real example — same Cat No, different products:

```
cat=SUFR38200   "Tyvek roll Flat reels-38 cm x 200 mtr"
                "Tyvek roll Flat reels- 40cm x 200 mtr"      ← 38cm vs 40cm
```

### Also: clean the junk first

320 items use placeholder values (`0` ×172, `na` ×144). Left in, they create one giant fake
group — 172 items sharing `"0"` produces **14,706 phantom duplicate pairs** on its own.

```
Cat No pairs, nothing removed    24,569 pairs   ← mostly rubbish
After junk removal                5,473 pairs   ← real
```

Cleaning this is not tidying — it is the difference between good training data and poisoned
training data.

---

## 7. Which Item fields we use

We tested **every field on the Item doctype** (94 of them) with one method:

> Build pairs we *know* are duplicates, and pairs we *know* are not.
> **Separation = how often the field agrees in duplicates − how often it agrees in non-duplicates.**

A field that agrees 90% of the time in both cases tells you nothing. Only the gap matters.

### Results

| Field | Agrees in duplicates | In non-duplicates | Separation | Decision |
|---|---|---|---|---|
| `custom_reference_old_item_code` | 81.3% | 1.1% | **+80.2** | Second lookup key — but only 4% coverage |
| `makes` | 80.0% | 20.0% | **+60.0** | **Use** — after the alias table is built |
| `gst_hsn_code` | 58.6% | 25.5% | **+33.1** | Use, small weight |
| `department` | 73.5% | 45.2% | **+28.2** | Use, small weight |
| `custom_item_creation_requester_` | 56.9% | 29.5% | **+27.4** | Use, small weight |
| `item_chid_group` | — | — | weak | Scoring clue only, **not** for finding candidates |
| `naming_series` | 72.5% | 83.6% | **−11.1** | Inverted — different series means *more* likely duplicate |
| `stock_uom` | 52.6% | 76.0% | **−23.4** | **Do not use** |

### Fields we deliberately refuse

| Field | Why |
|---|---|
| **`stock_uom`** | **Separation −23.4.** It does not just fail to help, it points the wrong way. In confirmed duplicates — same Cat No, same manufacturer, same product — the unit disagrees **52.4%** of the time. Example: `Human AB Serum` is filed as Millilitre, Millilitre *and* Nos. |
| **`description`** | **80.9%** of descriptions are a 95%-or-better copy of `item_name`. Only 9.6% add anything. |
| **`brand`** | Filled on 145 of 22,204 items — **0.7%**. Abandoned field; `makes` replaced it. |
| **`item_group`** | Too coarse. The largest group holds 7,915 items — 36% of the catalogue. |
| **`custom_category`** | 71% filled, but the doctype it links to no longer exists. |
| **Item Supplier / `supplier_part_no`** | Only **2 rows** exist in the whole system. |

### The one that surprises people

Excluding `stock_uom` feels wrong — surely the same product has the same unit? In this data
it does not, half the time. Including it was the most tempting mistake available, and only
measurement caught it.

### `makes` needs cleaning before it earns its weight

There are **1,785 different spellings** of manufacturer names. "Thermo" alone has **111
spellings** across 1,916 items:

```
Thermo(424)              Thermo Scientific(331)
Thermo Fisher Scientific(192)   Thermo Scientific(tm)(111)
Thermofisher(104)        Thermo Fisher(79)   Gibco/Thermo(76) ...
```

Sigma has 47 spellings, Merck 40, BD 37, Gibco 22. Until an alias table maps these to one
name each, this field is noise rather than signal.

---

## 8. The machine learning model

### What we do

A **pair classifier**: give it two items, it returns the chance they are the same. It
replaces only the scoring function in step 5, and only if it beats the hand-written weights
on a held-out test set.

Main model: **Logistic Regression**. Challenger: **Gradient Boosting**, trained only for
comparison.

### Other options

| Option | Why not |
|---|---|
| Neural network | Far too little data. No benefit at ~20 clues and a few thousand examples. |
| Random Forest / SVM | No real gain over logistic regression here, and harder to explain. |
| Meaning-based embeddings as the main judge | **They rate `Glycine 500g` and `Glycine 1kg` as about 97% the same.** Actively dangerous on this catalogue. Kept as an optional extra later, placed *behind* the safety rules with a small weight. |
| Fine-tuning our own embedding model | 3 to 5 weeks, needs PyTorch and a GPU, and thousands of clean labels. Revisit in a year if needed. |

### Why logistic regression, specifically

- Right size for ~20 clues and a few thousand examples
- Trains in under a second
- **The weights are readable** — you can see and explain why something was flagged
- Probabilities behave sensibly after calibration

### Honest expectation

**The model adds about +1.2 points over good rules.** Not 15. Measured, not estimated.

The reason: today's training examples come from Cat No agreement, and those are *easy*
pairs — mostly near-identical names. There is little for a model to learn from them.

The real gain comes later, from the production log, where the examples are *hard* cases a
human actually had to think about. Expect +6 to +8 points there.

### The rule

**Ship the hand-written weights unless the model clearly wins on the expert test set.**
Ties go to the simpler option. Keep the hand-written scorer in the code permanently as an
automatic fallback if the model file is missing or fails to load.

---

## 9. Where training examples come from

| Source | Gives us | Quality | When |
|---|---|---|---|
| Items sharing a Cat No (junk removed) | **5,473** "same" pairs | Good — 8 of 8 checked by hand were real | Now |
| Hard negatives from our own search | **592,497** available, ~21,900 needed | Good | Now |
| AI labelling (optional) | A few thousand borderline pairs | Better | Optional |
| **The production log** | What users actually did | **Best** | After 2–3 months |
| Expert test set | 300–500 hand-checked pairs | Trusted | **Never used for training** |

### Why negatives must come from our own search

If the model trains on easy pairs but the live system shows it hard ones, it fails in real
use. Training and live scoring must see the **same kind of pair**. So we take negatives
from the same candidate list the live system produces — close names, different Cat No.

### Why we split by Cat No group, not by pair

If the same product appears in both the training set and the test set, the test is
meaningless. We split whole groups, so a product is only ever on one side.

---

## 10. What the user sees

| Score | Message | What they can do |
|---|---|---|
| **0.92 and above** | "This item looks like it already exists", with the match and a link | Open it, or save anyway |
| **0.75 to 0.92** | A quiet note: "Some similar items exist" | Look, or ignore |
| **Below 0.75** | Nothing shown. Still written to the log. | Nothing |

**These thresholds are a starting guess, not a result.** They get set properly during the
two-week silent run, using real traffic. At 0.92 the current alert rate is 33%, which is
high — expect to raise it.

### Always explain why

Show the reason, not the number: *"names 97% alike · same manufacturer · both 50 mL"*.
This comes free from the component scores and does more for adoption than any accuracy
improvement.

**Where the reason appears, as of 2026-10-22:** on the inline panel, once, under the top
match. It was also printed under every row of the save dialog, and was removed from there —
see below.

### Say how many exist, not just the closest five

**What we do.** The dialog opens with a count: *"81 items like this already exist. Closest
5:"*. Then the five rows — **code, name, score** — and one muted line.

**Why.** `TOP_N = 5` caps the list, and a list of five with no count reads as *"there are
five"*. The catalogue holds **79** rows named `electrical installation at mrna lab`; the
dialog showed five of them and said nothing about the other 74. That was reported as a bug
in the matcher. It was not a bug — it was the display telling the user something untrue.

`search()` now returns `total_similar`, which is **every** existing item at or above the
soft band, counted before the top-five truncation.

| What was removed from the dialog | Why |
|---|---|
| *"The catalogue already has:"* | The count line says it, with a number |
| The per-row reason sub-line | On these rows it is the same sentence five times — *"names 99% alike - shares mrna, at"* under each of five identical names. The inline panel still shows it once |
| *"Do you still want to create this one?"* + the two-sentence explanation | The buttons already ask it. Replaced with one muted line: *"Warning only -- the record is saved. Use the existing code if it is the same product."* |
| Rows scoring below the soft band | The dialog listed the top five whatever they scored, so a one-match query showed four rows the count does not include |

**Cost of the count.** The early exit used to stop the moment the top five was settled,
which made everything after the fifth-best invisible. It now stops when the bound can
neither enter the top five **nor** reach the soft band. Measured on the live catalogue
(22,207 items, best of 5 runs):

| Query | Before | After | `total_similar` |
|---|---|---|---|
| `electrical installation at MRNA labb` | 1.6 ms | 11.0 ms | 81 |
| `Serological Pipette 10 mL` | 1.9 ms | 6.9 ms | 17 |
| `Tween 20` | 0.6 ms | 0.7 ms | 7 |
| `Glycine` (worst case) | 110.0 ms | **117.3 ms** | 10 |
| `CPU` (full scan) | 94.4 ms | 76.6 ms | 1 |
| `Plate` (all common words) | 95.6 ms | 97.9 ms | 0 |

Worst case **110 ms → 117 ms against a 300 ms budget**. The duplicate-heavy queries cost
more in relative terms and nothing in absolute ones, because the extra work is bounded by
how many items genuinely reach the soft band.

**Note on what the count means.** It counts *similar*, not *identical*: the typo query above
reports 81, which is the 79 identical rows plus two spelled `Electrical Installation - MRNA
Lab`. Counting only exact normalised-name matches would have been cheaper and would have
reported 0 for every typo — which is the case the warning exists for.

---

## 11. Never block a save

### What we do

The check **warns**. It never calls `frappe.throw`. The user can always save.

Non-stock items (`is_stock_item = 0`) are skipped entirely.

### Why

79 items in your system are all named **"electrical installation at mrna lab"**. They are
not mistakes — they are CAPEX service entries, one per purchase order, and they are
correct.

Blocking duplicate saves would break a working business process on day one.

The `is_stock_item` check removes this entire class of false warning with one line, before
any scoring happens.

### Also

The whole check is wrapped in error handling that logs the problem and returns no
suggestions. **A duplicate checker must never be the reason somebody cannot create an
item.**

---

## 12. Logging every check

### What we do

Every check writes one row to **Item Match Log** — including the silent ones nobody sees.

```
what the user typed
the top 5 matches, with scores and why each was or was not cancelled
which band it fell into
WHAT THE USER DID NEXT   ← used the existing item / created anyway / gave up
why they created anyway  <- different size / different grade
                            / genuinely new / service line
how long it took
which version of the engine ran
```

### Why this matters more than it looks

The two highlighted lines are **real human judgements on real pairs, captured for free**.

Three months of these is better training data than any amount of hand-labelling, and it is
the only source of labels free from the Cat No leakage problem in step 6.

**Build this on day one, even though nothing reads it yet.**

### One caution to design around

Users only judge the pairs we chose to show them. So we log the full top 5 including the
hidden ones, and occasionally ask someone to review a random sample of hidden pairs.

---

## 13. How we measure success

| Measure | Plain meaning | Target |
|---|---|---|
| **Precision of strong warnings** | Of all "this already exists" messages, how many were right | **90%+** ← the one that matters |
| Recall at top 5 | Of real duplicates, how many appear in the list | 70% → 80% with rules → 85% later |
| Look-alikes rejected | Different sizes and grades correctly left alone | 95%+ *(not yet measured)* |
| Speed | What the user waits | under 300 ms |
| Ignore rate | Strong warnings the user clicked past | under 25% |
| Duplicates prevented | Times an existing item was used instead | track monthly |

### Why precision is the headline, not recall

A missed duplicate costs one extra row in the catalogue.

A **wrong** warning teaches the user the tool is unreliable. After three of those they
click past every warning forever, including the correct ones. Recall can improve for a
year; trust does not come back.

### The method

1. **Build the test harness before tuning anything.** Otherwise you are guessing.
2. **Replay history** — go through item creation in date order and ask whether the tool
   would have caught each one. Free accuracy estimate at full scale.
3. **Run silently for two weeks** before any user sees a warning. Tune on real traffic.
4. **Never test on the training data.** No exceptions.

---

## 14. The dataset we work from

### What we do

We work from an Excel export of the Item list, **not** from a database backup.

```
Item.xlsx            25,590 items, 56 columns      ← original, never modified
  md5 f4d093698cc6cf5f52ad5b16a34fbbcb
```

A copy is taken before any processing. Every script reads the copy.

### Why not a database backup

A fresh backup was not available. The Excel export is the current data.

### What this costs us

The export has **no creation date column**. That means **Gate 0 cannot be answered** —
we cannot tell from this file whether items are still being created.

> **Action needed:** re-export with the **Created On** column. It is one column and it
> decides whether half this plan is worth building.

### Files

| File | What |
|---|---|
| `Item.xlsx` | The original. Read-only. Never written to. |
| `data/Item_working_copy.xlsx` | Exact copy, used for all processing |
| `data/Item_split.xlsx` | Copy + `dataset_split` column, and one sheet per split |
| `data/Item_slim.xlsx` | Only the 11 columns we use |
| `data/slim_train.csv` etc. | The three splits as CSV |

---

## 15. How Item Code is built

Worked out from the data, not from documentation.

### The structured scheme

```
RDCN  02  03  0516
 │     │   │    └──── serial number, runs in sequence inside each category
 │     │   └───────── sub-category
 │     └───────────── category
 └─────────────────── division / type prefix
```

`PREFIX + CC(2 digits) + SS(2 digits) + NNNN(4 digit serial)`

How well each part predicts the category fields:

| Digits | Predicts Item Group | Predicts Item Child Group |
|---|---|---|
| 1–2 | **99.8%** | 68.3% |
| 3–4 | 88.0% | 87.9% |
| **1–4** | **99.9%** | **99.3%** |
| 7–8 | 69.0% | 37.4% — this is the serial, it means nothing |

Examples:

```
RDCN 02 03 0516  →  CN-General Consumable   /  GC-Others
RDRM 01 07 0123  →  RM-Reagents             /  RG-Chemicals
RDCP 01 01 0435  →  CP-Plant & Machinery    /  P&M-Instruments
CN   05 05 0004  →  Plasticware             /  Serological Pippette
```

The serial is dense — it runs 0001, 0002, 0003 inside each category, with no gaps in
**100%** of the 154 groups tested.

### But only 47% of items use this scheme

| Shape | Items | Share | Example |
|---|---|---|---|
| `AAAA99999999` | 13,906 | 54.3% | `RDCN02030516` ← structured |
| `A&A99999` | 8,334 | 32.6% | `R&D08518` ← **no category encoded** |
| `AAAAA-9999` | 2,841 | 11.1% | `CAPEX-02961` |
| `AA/999999` | 500 | 2.0% | `RM/000707` |
| others | ~9 | — | `Consumable00726`, `Spares…` |

### What the prefixes mean

| Prefix | Items | Mostly |
|---|---|---|
| `R&D` | 8,334 | R&D (95%) — generic, no category |
| `RDRM` | 5,552 | RM-Reagents (98%) |
| `RDCN` | 3,736 | Consumables |
| `CAPEX` | 2,847 | R&D-Capex/Asset (98%) |
| `RDCP` | 889 | CP-Plant & Machinery (79%) |
| `CN` / `RM` / `CP` | 1,433 | Same categories, **Manufacturing** division |

### Why this matters — the root cause, visible in the code

`RDCN…` is the **R&D** division. `CN…` is the **Manufacturing** division. Same categories,
separate numbering. The same product bought by two divisions gets two unrelated codes, by
design.

And `R&D08518` encodes no category at all, so 32.6% of items carry no structure.

This is why 92% of duplicate groups span more than one code prefix. **The duplicate problem
is built into the numbering system**, not caused by careless users.

### Three rules that follow

1. **Never text-match Item Code.** `RDCN02030516` and `RDCN02030517` are 92% similar as
   strings and are unrelated products. Treating codes as text would be a disaster.
2. **Parse it instead** — prefix, category, sub-category, serial. Throw the serial away.
3. **The code's category beats the field.** 9.6% of code-groups have an Item Child Group
   that disagrees with their own code. The code is set at creation; the field gets edited
   later.

### A useful inverted signal

Same category but a *different division prefix* (`RDCN…` vs `CN…`) is **evidence of
duplication**, not evidence against it.

---

## 16. Train / validation / test split

### What we do

**70 / 15 / 15**, split by **product group**, not by row.

| Split | Items | Share | Groups | Positive pairs |
|---|---|---|---|---|
| Train | 17,914 | 70.0% | 13,191 | 4,276 |
| Validation | 3,838 | 15.0% | 2,828 | 978 |
| Test | 3,838 | 15.0% | 2,829 | 888 |

- **Train** — fit the model
- **Validation** — tune thresholds, choose between Logistic Regression and Gradient Boosting
- **Test** — touch **once**, at the very end

Separately: an **expert test set** of 300–500 hand-labelled pairs, never trained on. That
is the final gate.

### How the groups were built

A product group is a connected set of rows linked by any of three identity signals
(union-find):

```
linked by Cat No                4,977 merges
linked by identical Item Name   4,256 merges
linked by Old Item Code           197 merges
   →  18,848 product groups from 25,590 items
      4,408 groups hold more than one item (11,150 items)
```

### Other options

| Option | Why not |
|---|---|
| Shuffle rows and split 70/15/15 | **Leakage.** The same product lands in train and test. The model sees "Glycine 500g" in training and "Glycine 500g." in testing, scores brilliantly, and fails in production. |
| Group by Cat No only | Misses products with a mistyped Cat No but an identical name |
| 80/10/10 | Fine too. 15% gives ~900 test pairs, which is comfortably enough for a stable precision estimate. |

### Proof it is clean

```
product groups spanning more than one split : 0   OK
identical names split across sides          : 0   OK
```

Reproducible with `random_state = 42`.

### Settings that go with it

- **4 negatives per positive**, drawn from name-similar candidates (the rare-word index),
  not random pairs. Random pairs are too easy and teach nothing.
- **Remove Cat No junk first** — otherwise 14,814 fake pairs enter the split.
- **Cat No stays out of the features** — it made the labels, so it cannot be a clue.

---

## 17. Which columns we keep

Cut from **57 columns to 11**.

### The five core attributes

| Column | Filled | Role |
|---|---|---|
| **Item Name** | 100% | **MAIN ATTRIBUTE** — 86% of the matching score |
| **Cat No** | 70.6% | **MAIN KEY** — exact lookup, never a model feature |
| **Makes** | 72.6% | Separation +57.1 — needs the alias table first |
| **Item Code** | 100% | Identity. Parsed, never text-matched. |
| **Description** | 100% | Weak — 72.4% is a copy of Item Name |

### Six more we kept, and why

| Column | Filled | Why it had to stay |
|---|---|---|
| **Maintain Stock** | 100% | **The gate.** Without it the 79 CAPEX rows come back as false duplicates. |
| **Reference Old Item Code** | 6.0% | **Separation +79.3 — the strongest field in the system.** Low coverage, so it is a second lookup key, not a model feature. |
| **HSN/SAC** | 74.3% | Separation +34.2 |
| **Department** | 55.7% | Separation +26.9 |
| **Disabled** | 100% | Ranks disabled items lower |
| **dataset_split** | 100% | Holds the 70/15/15 assignment |

### What we dropped — 46 columns

The notable ones, with the reason:

| Column | Why dropped |
|---|---|
| **Default Unit of Measure** | Separation **−5.0**. In confirmed duplicates the unit disagrees 52.4% of the time. Points the wrong way. |
| **Item Child Group** | Separation **−1.9**. Agrees in only 18.9% of real duplicates. |
| **Item Group / Category** | −10.0 / −11.1 |
| Weight Per Unit | 100% filled but every value is 0 |
| Weight UOM | 0.2% filled |
| Brand | 0.7% filled |
| Excluded From Purchase Order / Material Request / Receipt | −26 to −18 |
| The remaining ~37 | Flags and defaults with near-zero separation |

### A caution about Reference Old Item Code

It has the highest separation of any field (**+79.3**) but is filled on only **6.0%** of
items, and both sides of a pair have it in only about **4%** of cases.

So: use it as a **deterministic lookup key** alongside Cat No. Do **not** give it to the
model as a feature — it is too sparse for a model to learn anything from.

---

## 18. Major and minor attributes

### How we decided, and why the first method was wrong

Earlier we ranked fields by **separation** — how often a field agrees in duplicates minus
how often it agrees in non-duplicates. That answers *"when this field is filled in, how
useful is it?"*

It does **not** answer *"how many real decisions does it change?"* — because it ignores how
often the field is actually present.

So we ran an **ablation test** instead: score every pair with the full attribute set, then
remove one attribute at a time and measure how far precision and recall move. Anything that
moves them by nothing is minor.

Run on the validation split only — 3,838 items, 978 positive pairs, 978 hard negatives. The
test split was not touched.

### Results

| Attribute removed | Change at 0.60 | at 0.85 | at 0.92 |
|---|---|---|---|
| **Makes** | recall **−1.3** | recall **−5.0** | recall **−11.7** |
| HSN/SAC | recall +1.2 | prec +1.1, recall +0.8 | recall +1.4 |
| Department | recall +1.2 | recall +1.7 | recall +2.5 |
| Description | +0.2 | — | — |
| Disabled | +0.2 | — | — |
| Reference Old Item Code | **0.0** | **0.0** | −0.2 |

A **positive** number means the system got *better* when the attribute was removed.

### MAJOR — 6 attributes

| Attribute | Role | Evidence |
|---|---|---|
| **Item Name** | The decision itself | 86% of the score. Nothing to match on without it. |
| **Cat No** | Step 0 lookup key | 3,319 groups already share one; 8 of 8 checked by hand were real duplicates |
| **Makes** | Scoring | Removing it costs **11.7 points of recall** at the loud threshold |
| **Maintain Stock** | The gate | Without it the 79 CAPEX service rows return as false warnings |
| **Item Code** | Row identity | Structural. Parsed for its category, **never text-matched.** |
| **Description** | **Shown to the user, not scored** | See below — it carries no scoring value but the user needs to read it to judge a suggestion |

### MINOR — 5 attributes

| Attribute | Measured impact | Verdict |
|---|---|---|
| **HSN/SAC** | Removing it **improves** precision and recall | Drop from scoring |
| **Department** | Removing it **improves** recall by 1.2–2.5 | Drop from scoring |
| **Reference Old Item Code** | **Exactly 0.00** at every threshold | Drop |
| **Disabled** | 0.2 | Keep only for display ordering |
| `dataset_split` | Not an attribute | Housekeeping column |

### Description — major for display, not for scoring

Description is a **major attribute with a non-scoring role**. The distinction matters, so
it is written down here rather than left to be rediscovered.

**Why it is major.** When the panel says *"this item may already exist"*, the user has to
decide. A name alone is often not enough — they need to see what the existing item actually
is. Description is what makes a suggestion actionable instead of a guess. It also goes into
the Item Match Log, so later review of past decisions has the full context.

**Why it is not in the score.** Five designs were tested on the validation split:

| Design | P @0.60 | R @0.60 | P @0.85 | R @0.85 |
|---|---|---|---|---|
| Ignore it completely | 87.8% | **63.8%** | 91.7% | **51.9%** |
| Flat 5% weight | 87.6% | 63.8% | 92.1% | 51.5% |
| 15% weight, always | 87.8% | 62.8% | 92.2% | 50.9% |
| 15% weight, only when it differs from the name | 88.2% | 62.8% | 92.2% | 50.5% |
| Merge its new words into the name, then match | 89.2% | 56.9% | 92.4% | 46.0% |

No design beats ignoring it. The last one buys 1.4 points of precision for **6.9 points of
recall** — a bad trade.

The reason is in the data:

```
adds 0 new words to the name : 75.9%   <- three in four carry nothing
adds 1-2 new words                       :  7.0%
adds 3+ new words                        : 17.1%
average new words added                  : 2.75
```

**Rule: show Description, never score it.** If descriptions become richer later, re-run
`desc_test.py` and revisit — the 17.1% that already add 3+ words is where the value would
come from.

### Why two weak fields made things worse

`HSN/SAC` and `Department` sat in the same "field agreement" term as `Makes`. Adding weak
signals to that term **diluted the strong one**. Measured directly:

```
agreement from Makes + HSN + Department   P 93.7%   R 47.0%
agreement from Makes alone                P 94.9%   R 49.4%
                                          better on BOTH axes
```

**The agreement term should use `Makes` only.**

### The correction on Reference Old Item Code

We previously recommended keeping it because its separation was the highest of any field,
**+79.3**. The ablation shows it changes **zero decisions**.

Both numbers are right; they measure different things. It is filled on only **6.0%** of
items, and both sides of a pair have it in about **4%** of cases — and in those few cases
the name score had already decided the answer.

**Lesson: separation ranks a field's quality. Ablation ranks its usefulness. For keep-or-drop,
ablation is the one that counts.**

### What this changes

- Agreement term becomes `Makes` only
- `HSN/SAC`, `Department`, `Reference Old Item Code`, `Description` leave the scorer
- Working set drops from 11 columns to **5 major + `Disabled` for ordering**
- The minor columns stay in the data files for now, in case the picture changes after the
  manufacturer alias table is built — `Makes` is the one field whose measured value should
  *rise* once its 2,083 spellings are merged

---

## 19. The core modules — what each file does

Three files in `item_duplicate_check/core/`. All three are **pure Python** — no
Frappe import, no database — so they run and test in milliseconds without a site.

Each file carries a long header comment explaining what it does, how it works
technically, and why each rule exists. Each major step inside the code is
commented too.

### 19.1 `normalize.py` — make two names comparable

**Job:** the same product typed two ways must end up as one string.

```
"Nunc(tm) 96-Well Plate "   ->  "nunc 96well plate"
"nunc 96 well plate."       ->  "nunc 96well plate"
```

**How it works:** a fixed pipeline of ten rewrites. The order is locked,
because several rules only work if an earlier one has already run.

| Step | Rule | Why |
|---|---|---|
| 1 | Repair mojibake | Must precede the micro rule, or a stray `A` is left behind |
| 2 | Drop ™ ® © | **Must precede NFKC** — see the bug below |
| 3 | Unicode NFKC | Folds compatibility characters |
| 4 | Non-breaking space → space | `split()` does not break on U+00A0 |
| 5 | µ and μ → `u` | Two code points, one glyph |
| 6 | Lower case | Solves DNase / DNAse |
| 7 | Join number + unit | `50 ml` → `50ml`, one token not two |
| 8 | Join number + `%` | `70 %` → `70%` for the grade check |
| 9 | Strip punctuation, keep `.` and `%` | Decimals and percentages must survive |
| 10 | Drop trailing dot, collapse spaces | 5.3% end in a stray full stop |

**A bug the self-test caught.** Trademark stripping originally ran *after*
NFKC. But NFKC expands `™` into the two letters `TM`, which then glue onto the
word in front:

```
"Nunc(tm) 96-Well Plate"   ->   "nunctm 96 well plate"     WRONG
```

Moving the strip before NFKC fixed it. This would have silently broken
matching for the 12.7% of names that carry a trademark symbol.

### 19.2 `specs.py` — block matches between look-alikes

**Job:** cancel a match when the two names describe different products. The
cancel overrides the similarity score completely.

**How it works:** four independent checks, cheapest first. Each returns a
reason string or `None`.

| Check | What it compares | Example that gets cancelled |
|---|---|---|
| Size | `(dimension, magnitude)` in a base unit, so 1kg = 1000g | `Glycine 500g` vs `Glycine 1kg` |
| Spec codes | Digit-bearing tokens grouped by letter stem | `B-34` vs `B-24`, `QST-6` vs `QST-8` |
| Negation | no, without, non, free, reduced, anti | `no phenol red` vs `Phenol Red` |
| Grade | absolute, HPLC, sterile, RNase-free, 70% | `Ethanol absolute` vs `Ethanol molecular biology` |

**Two rules stop it over-firing:**

- **Abstention** — a check only fires when *both* names have something to
  compare. Otherwise `Paraformaldehyde` could never match
  `Paraformaldehyde for synthesis`.

- **Subset, not equality** — within a shared dimension, if one side's values
  are contained in the other's, nothing actually disagrees:

```
{mass 500}            vs {mass 1000}       -> CANCEL
                                           neither contains the other
{length 0.22um,33mm}  vs {length 0.22um}   -> ALLOW
                                           one name is just fuller
```

The second case is real: `Syringe Filter Unit, 0.22 µm, PVDF, 33 mm` and
`Millex filter unit (0.22 um)` are the same product. An earlier version
compared sets for equality and wrongly cancelled it.

**Why the reason string matters.** Every cancel returns *why*
(`"size: mass [500.0] vs [1000.0]"`). It is shown to the user and written to
Item Match Log, so a human can later see which rule fired and judge whether it
was right. That log is how this file gets improved.

### 19.3 `itemcode.py` — read the code, discard the serial

**Job:** split an item code into its parts and throw away the part that is noise.

```
RDCN 02 03 0516
 |    |  |   +-- serial, a running counter -- DISCARD
 |    |  +------ sub-category
 |    +--------- category
 +-------------- division prefix
```

**Why the serial must go.** `RDCN02030516` and `RDCN02030517` are 92% identical
as strings and are unrelated products. If codes were ever text-matched, every
item would match its neighbour. Codes are **parsed, never compared as text**.

**Four shapes exist, not one.** The parser handles all of them and sets
`has_category` so callers know when the category is real:

| Shape | Items | Category encoded? |
|---|---|---|
| `RDCN02030516` | 54.3% | Yes |
| `R&D08518` | 32.6% | **No** |
| `CAPEX-02961` | 11.1% | No |
| `RM/000707` | 2.0% | No |

**A signal that falls out of this.** `same_category_different_division()`
returns `True` when two codes share a category but sit under different
divisions — `RDCN…` is R&D, `CN…` is Manufacturing. That is **evidence for
duplication**, since the numbering scheme guarantees the same product bought by
two divisions gets two unrelated codes.

### 19.4 Measured result of these three files

> **History.** These figures were produced with the scorer that has since
> been removed. They are the benchmark the model has to beat, not a
> description of current behaviour.

Running the real modules over the validation split (3,838 items, 978 positive
pairs, 978 hard negatives):

| Threshold | Precision | Recall |
|---|---|---|
| 0.60 | 85.4% | 73.3% |
| 0.75 | 91.0% | 65.0% |
| 0.85 | 92.1% | 60.7% |
| **0.92** | **97.9%** | **53.7%** |

Compare with the earlier prototype, which used strict set-equality on sizes and
diluted the agreement term with HSN and Department:

```
prototype at 0.92   precision 98.5%   recall 39.2%
these modules       precision 97.9%   recall 53.7%
                    ------------------------------
                    +14.5 points of recall for 0.6 of precision
```

Almost all of that gain came from the **subset rule** in `specs.py`.

### 19.5 Running the self-tests

Each file has a `__main__` block with real catalogue rows:

```
cd apps/item_duplicate_check
../../env/bin/python -m item_duplicate_check.core.normalize
../../env/bin/python -m item_duplicate_check.core.specs
../../env/bin/python -m item_duplicate_check.core.itemcode
```

`specs.py` prints 13 known pairs — 8 that must be cancelled and 5 that must be
allowed. All 13 currently pass. **Add every false warning seen in production to
that list before fixing the rule.**

---

## 20. The build — steps A to I

> **This section is HISTORY, not a description of the app as it stands.**
> Steps C, D, H and I below measured the search index and the hand-written
> scorer, which were removed on 2026-10-21. The numbers were real when taken
> and are kept because they are the only measurements anyone has of this
> data — the model will be judged against them. The code they describe is no
> longer in the app.
>
> Steps A, B, E, F and G still describe live code.

Sections 0 to 19 are the decisions. This section is the record of carrying them
out: what was built, the command that proves it, what the output said, and
every place reality disagreed with the plan.

Nothing here changes a decision. Where something had to change, it was a bug in
the code or a mistake in the documentation, and it says which.

---

### Step A — are items still being created?

```
env/bin/python apps/item_duplicate_check/scripts/scope_check.py
```

```
  month        items      stock   disabled
  -----------------------------------------
  2026-05          6          6          0
  2026-03          1          1          0
  2026-02        304        230          0
  2026-01        552        505          1
  2025-12        548        463          3
  2025-11        671        609          4
  2025-10         65         47          0
  -----------------------------------------
  total         2147

  year      items
  ----------------
  2026        863
  2025       9013
  2024       4996
  2023       7332

  items in tabItem         22204
  maintain stock = 1       17864
  maintain stock = 0        4340   <- skipped by HARD RULE 2
  disabled = 1              4300
  first created           2023-05-11 18:59:52.809114
  last created            2026-05-16 17:13:50.948954
```

**The open question in section 14 is answered: creation has effectively stopped.**
304 items in February 2026, then 1 in March and 6 in May. Before that the site
was running at 550–670 items a month.

Two consequences, both reported rather than acted on, because acting on them
would be a change of decision:

1. The live panel will see very little traffic as things stand. The two-week
   silent run described in decision 10 will not collect 700 decisions; it will
   collect a handful. The panel is built and works, but the tuning data it is
   supposed to produce has to come from somewhere else — most likely a replay of
   history, which the harness in step H already does.
2. **The Maintain Stock skip is much larger than decision 11 implies.** The
   decision record describes 79 CAPEX rows. The real count on `site_local` is
   **4,340 non-stock items**, 19.5% of the catalogue. The rule is unchanged —
   those rows are still skipped before any scoring — but the number in decision
   11 refers to one example, not to the size of the exclusion.

---

### Step B — the spec tests, and two real bugs in the veto gate

```
PYTHONPATH=apps/item_duplicate_check env/bin/python -m unittest \
    item_duplicate_check.tests.test_specs
```

`pytest` is not installed in this bench's environment, so the tests are written
against `unittest` from the standard library. The file runs unchanged under
`pytest` for anyone who has it.

The 13 self-test pairs moved out of `specs.py`'s `__main__` block into
`tests/test_specs.py`, plus the five new pairs and the normalisation cases.
Writing them found **three things, two of them real bugs**.

#### Bug 1 — a catalogue code read as a measurement

`Nuclease-free Water B1500S` vs `B1500L` was **allowed**. It should cancel.

```
A: nuclease free water b1500s
   sizes=[]                        codes={'bs': {1500.0}}
B: nuclease free water b1500l
   sizes=[('volume', 1500000.0)]   codes={}
```

`QTY` matched the `1500l` inside `b1500l` and read it as **1,500 litres** — 1.5
million millilitres of water in a bottle. Two failures followed from one cause:
the bogus size collided with anything that had a real volume, and the token was
then skipped by `spec_codes()` because `l` is a unit, so the code itself became
invisible.

Fixed by anchoring the measurement pattern so it can only start where a token
starts:

```python
QTY = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)([a-z]+)")
```

Blocking only letters was not enough: `re.findall` shifts one character right
and matches `500l` instead. Letters, digits and the decimal dot all have to be
excluded, which leaves exactly one legal starting point per token.

#### Bug 2 — the stem grouping hid the clash

Even with the size fixed, the pair still did not cancel. `b1500s` was grouped
under stem `bs` and `b1500l` under `bl` — no shared stem, nothing to disagree
about.

`spec_codes()` now groups on the letter PREFIX when there is one, with
everything after it as the value:

```
b1500s -> "b": {"1500s"}        b1500l -> "b": {"1500l"}      -> CANCEL
```

A bare number with no prefix keeps the original grouping, so `2` and `2d` stay
in separate buckets and do not cancel each other. The skip condition changed
from `suffix in UNITS or prefix in UNITS` to `not prefix and suffix in UNITS`:
only a *pure* measurement belongs to the size check.

#### Bug 3 — rule 7 did not fire across a hyphen

Section 19.1 documents `"Nunc(tm) 96-Well Plate " -> "nunc 96well plate"`, and
rule 7 is supposed to join a number to its unit. It did not, because `96-Well`
has a hyphen rather than a space, so the output was `nunc 96 well plate` — the
measurement back as two tokens, which is the exact thing rule 7 exists to
prevent.

The separator in rule 7 now absorbs spaces, hyphens and typographic dashes.
**Section 19.1's example was also wrong in the other direction** and has been
corrected in this file: it showed `nunc 96 well plate` as the expected output
while its own rule table said rule 7 joins the number to the unit.

#### One thing documented rather than fixed

`spec_codes("adaptor B-34")` returns `{"": {"34"}}`, not `{"b": {"34"}}` — rule
8 turns the hyphen into a space long before `specs.py` sees it, so the letter
stem is already gone. The docstring claimed otherwise. The docstring was
corrected, because the pair that matters (`B-34` vs `B-24`) still cancels
correctly through the bare-number bucket, and joining a letter to a digit would
be an **eleventh** normalisation rule. The ten are locked.

#### Result

```
Ran 31 tests in 0.018s
OK
```

Three more tests were added as regressions for the bugs above, each naming the
bug in its docstring. A fourth test had to be fixed rather than the code: the
non-breaking-space test originally contained an ordinary space, because a
literal U+00A0 in source looks exactly like one. It now uses an escape.

---

### Step C — the rare-word index

```
env/bin/python apps/item_duplicate_check/scripts/index_stats.py
```

```
  Corpus(22204 items, 13007 words, df ceiling 222, 10481 cat numbers)
  load from MariaDB        683.1 ms
  build index              135.9 ms
    enabled                17904
    disabled (kept)         4300
  df ceiling for lookup      222   (words above this are ignored as keys)

  most common words (excluded from lookup):
    human            1379 items        kit              1085 items
    anti             1122 items        antibody          972 items
    x                1098 items        for               944 items

  posting list length: median 2, p95 24, max 182

  typed name                                    candidates      ms
    Nunc 96 Well Plate                                  106    0.07
    Serological Pipette 50 ml                           284    0.16
    Glycine 500g                                         73    0.02
    CPU                                               22204    0.23  <- full scan
    sterile plate                                     22204    0.22  <- full scan
```

Lookup is 0.02–0.23 ms against the 4.7 ms in decision 4, on a corpus of the
same size. The fallback to a full scan behaves as designed for a one-word name
and for a name made entirely of common words.

Note which words the ceiling actually excludes on this data: not `plate` and
`tips` as decision 4 guessed, but `human`, `anti`, `antibody`, `kit` — this is
a catalogue with a lot of immunology in it. The rule is the same; the words it
catches are simply the ones the data has.

#### Cat No junk removal, measured against the decision record

```
    Cat No as a Step 0 key                values groups>1      pairs
    nothing removed                        10557     3319      30928
    after clean_cat_no()                   10481     3327       6416
    decision record says                 ~10,500    3,319      5,473

    items in a Cat No clash  7788       (decision record: 7,781)
```

Distinct values, groups and items in a clash all land within 0.2% of the
figures in decision 6. **The pair count does not: 6,416 against 5,473.** Two
causes, both measured rather than assumed:

```
junk removed, separators kept :  3313 groups>1    6241 pairs
junk removed + separators out :  3327 groups>1    6416 pairs

groups that MERGED because separators were stripped: 65
  11140050         8 items  <- ['11140-050', '11140050']
  10270106         8 items  <- ['10270-106', '10270106']
  1009830511       6 items  <- ['1.00983.0511', '1009830511']
```

1. **175 of the extra pairs come from this app treating separators as
   meaningless** in a catalogue number, so `11140-050` and `11140050` are one
   key. All 65 merges were inspected and all 65 are the same Gibco/Sigma number
   written two ways. This is a deliberate addition, and it makes Step 0 find
   more.
2. **The rest is the dataset.** 5,473 was measured on the 25,590-row Excel
   export; this is the 22,204-row `site_local` database. The three numbers that
   *are* comparable across the two snapshots match.

---

### Step D — the scorer, and the manufacturer alias table

```
env/bin/python apps/item_duplicate_check/scripts/build_makes_alias.py --write
```

```
  raw spellings in tabItem          1464
  after normalisation               1318
  rows with junk makes (ignored)     244
  spellings mapped by seed rules     119

  what the table merges:
    thermo      1916 items across 60 spellings
    sigma       1078 items across 20 spellings
    tarsons      925 items across  2 spellings
    bd           685 items across 20 spellings
    merck        393 items across 10 spellings
    biorad       297 items across  3 spellings
    gibco        286 items across  4 spellings
```

5,580 items across seven manufacturer families. `Gibco/Thermo` maps to `thermo`
and plain `Gibco` to `gibco`, which is achieved by rule order rather than by a
special case. Two families were added from the data that the brief did not
list, both pure spelling variants rather than brand decisions: `tarson`/`tarsons`
(925 items, the single largest merge available) and `bio rad`/`biorad`.

The generated file is **exact match only**. The substring rules live in the
generator, where they are applied once to a finite list under human review; a
substring rule in production would eventually map `abdominal` to Becton
Dickinson.

`0` (140 items) and `na` (69 items) are treated as **blank, not as a
manufacturer**. Without that, 140 unrelated items would agree with each other.

#### Top unmapped spellings — the human review queue

```
   496  neb            338  cytiva         204  srl          149  miltenyi biotec
   425  biolegend      271  himedia        181  abcam        144  qiagen
   349  invitrogen     255  corning        176  atcc         138  eppendorf
```

Three of these are worth a human decision and were deliberately **not** guessed:

| Spelling | Question |
|---|---|
| `miltenyi` (36) vs `miltenyi biotec` (149) | almost certainly one company |
| `millipore` (46) vs `merck` (341) | Merck Millipore, or a separate supplier here? |
| `invitrogen` (349), `fisher scientific` (38) | Thermo brands. Merging them is a judgement about how coarse "same manufacturer" should be |

#### Latency, and why the scorer has an upper bound in it

```
env/bin/python apps/item_duplicate_check/scripts/latency_check.py
```

The first working version missed the 300 ms budget by an order of magnitude on
a fallback query. Three versions, same hardware, same data:

| Version | Median | Worst case |
|---|---|---|
| first working version | 22 ms | **3,842 ms** |
| + precomputed token sets and IDF sums | 6 ms | 2,246 ms |
| + `quick_ratio` prefilter, lazy veto gate | **2 ms** | **286 ms** |

Three changes, none of which alter a score:

1. `token_set`, `sorted_key` and `idf_sum` are computed once per rebuild instead
   of once per comparison. On a full scan that is 22,204 times.
2. The candidate list is sorted by a **true upper bound** on the final score and
   walked until the bound cannot beat the current fifth-best. `difflib` offers
   three comparisons of increasing cost — `real_quick_ratio`, `quick_ratio`,
   `ratio` — each an upper bound on the next, so a candidate is dropped at the
   cheapest stage that can rule it out. The top five is still exact, and
   `test_scorer.TestEarlyExit` checks that against brute force over the same
   shortlist.
3. The veto gate parses both names, so it is not free either. It is now asked
   only about pairs that could actually reach the top five.

The 286 ms worst case is a query built from the eight most common words in the
catalogue. It is not a name anybody would type; the hardest real name measured
is `sterile plate` at 39 ms, and the p95 over real catalogue names in step H is
**21 ms**.

#### The renormalisation, which is easy to get wrong

29% of items have no usable `makes`. If an abstention contributed 0.0 the best
achievable score would be 0.86 — below the 0.92 strong threshold — so **no item
without a manufacturer could ever raise a strong warning**, however identical
the name. The abstaining weight is therefore removed from the divisor, not
counted as a zero. Abstain means "no opinion", never "no".

---

### Step E — the doctypes

Three, in `item_duplicate_check/item_duplicate_check/doctype/`:

| Doctype | What it is for |
|---|---|
| **Item Duplicate Check Settings** (single) | the kill switch, silent mode, both thresholds, all four weights, engine version |
| **Item Match Log** | one row per check, including silent ones |
| **Item Match Log Entry** (child) | one suggested item, with its score, band, reason and cancel reason |

```
bench --site site_local install-app item_duplicate_check
```

```
name                           issingle istable
Item Duplicate Check Settings  1        0
Item Match Log                 0        0
Item Match Log Entry           0        1

enabled          0
silent_mode      1
strong_threshold 0.92
soft_threshold   0.75
weight_ordered   0.47
weight_word_set  0.21
weight_rare      0.18
weight_makes     0.14
engine_version   rules-1.0
```

The app ships **disabled**, with silent mode on. Installing it changes nothing
a user can see.

The log carries two columns beyond the brief, both for the same reason —
decision 12 warns that users only judge pairs they were shown:

- `shown_to_user`, so that bias stays visible when the log is analysed;
- `cancelled_matches`, the near misses the veto gate killed. A pair that scored
  high and was then cancelled is the first place a wrong rule shows up.

Note the name: the brief and this file now both say **Item Duplicate Check
Settings**. The installation section used to call it "Item Dedup Settings";
that was the only mention and it has been corrected.

---

### Step F — the API and the hooks

`api.check_duplicate` does four things in a fixed order: the kill switch, the
Maintain Stock skip, the search, the log. It catches everything and returns a
well-formed empty answer on failure.

```
env/bin/python /tmp/api_check.py        (the state matrix, run during the build)
```

```
1. enabled=0            band=none   matches=0 skipped=disabled in settings
2. enabled=1 silent=1   band=none   matches=0 skipped=silent mode     log WRITTEN
     log row: band=strong top_score=1.0000 shown_to_user=0 matches=5 candidates=284
3. enabled=1 silent=0   band=strong matches=5 skipped=None
4. maintain_stock=0     band=none   matches=0 skipped=maintain stock is off
   maintain_stock="0"   band=none   matches=0 skipped=maintain stock is off
5. item_name=None/""    band=none   matches=0 skipped=no item name typed
6. forced exception     band=none   matches=0 skipped=error (logged)
7. record_action        {'ok': True}  -> user_action='created anyway'
                                         override_reason='different size'
```

Silent mode logs in full and tells the client nothing. That asymmetry is the
point of the two-week run.

One bug found here: passing an integer as `item_name` raised, because
`(item_name or "").strip()` does not work on an int. Every incoming value is
now coerced with `cstr`.

**Four hooks on Item, and not one of them can block a save**: `after_insert`,
`on_update`, `on_trash`, `after_rename`, each of which only bumps a Redis
counter. There is no `validate`, no `before_save`, no override of any ERPNext
class. The check is not on the save path at all.

While writing step I it became clear that was not quite enough: a `doc_events`
handler that raises **does** abort a save, and `corpus.invalidate()` guarding
its own body does not protect against an import error or a future bug in the
handler itself. The handlers now swallow everything and log, which step I
tests directly.

---

### Step G — the form panel

`public/js/item_duplicate_check.js`, loaded only on the Item form.

- 600 ms debounce on `item_name`, `cat_no`, `makes`;
- at or above 0.92, a banner with the matched item, the reason sentence, and
  **Open it** / **Save anyway**;
- between 0.75 and 0.92, one quiet line of links;
- below 0.75, nothing;
- nothing at all when Maintain Stock is unticked, or on an existing Item.

Search the file for `validate` and the only hits are the comments saying there
isn't one.

**Open it** and **Save anyway** are not just navigation — each writes a human
judgement back to the log row that produced the warning. **Save anyway** asks
one optional follow-up question (different size / different grade / genuinely
new / service line), and records the override even if that question is
dismissed. Nagging would get the panel switched off.

---

### Step H — does it actually detect duplicates?

```
env/bin/python apps/item_duplicate_check/scripts/eval_harness.py
```

Positive pairs are items sharing a cleaned Cat No. **The scorer is asked about
them with the Cat No hidden** — name and manufacturer only — so it never sees
the thing that made the label. Hard negatives are the top-scoring candidate the
search itself returns from a *different product group*; random pairs would be
too easy to mean anything.

```
  product groups built by union-find:
    linked by Cat No               4461 merges
    linked by identical name       1919 merges
    linked by old item code           2 merges
    distinct product groups       15822  (from 22204 items)

  split           items    share   groups
  train           15543    70.0%    11113
  validation       3332    15.0%     2390
  test             3329    15.0%     2319

  leakage check:
    product groups spanning more than one split     0   OK
    identical names split across sides              0   OK
```

#### Validation — 822 positive pairs

| Threshold | Precision | Recall | Precision at 4:1 |
|---|---|---|---|
| 0.60 | 79.2% | 78.1% | 48.7% |
| 0.75 | 93.1% | 72.4% | 77.2% |
| 0.85 | 98.3% | 69.1% | 93.4% |
| **0.92** | **100.0%** | **64.2%** | **100.0%** |

```
  recall@5         85.5%
  latency median    2.4 ms      p95  21.0 ms      worst  59.9 ms
```

#### Test — 870 positive pairs, looked at once

| Threshold | Precision | Recall |
|---|---|---|
| 0.60 | 82.1% | 81.7% |
| 0.75 | 93.8% | 74.4% |
| 0.85 | 97.9% | 68.6% |
| **0.92** | **100.0%** | **63.4%** |

```
  recall@5         88.2%
  latency median    1.9 ms      p95  17.7 ms
```

#### Against the earlier prototype in section 19.4

| At 0.92 | Precision | Recall |
|---|---|---|
| section 19.4 (prototype) | 97.9% | 53.7% |
| these modules | **100.0%** | **64.2%** |

Both precision and recall improved. Most of the recall gain is the two veto-gate
fixes from step B — a bogus 1,500-litre size was cancelling real matches — and
the rest is the manufacturer alias table letting the agreement term fire.

#### Three honest caveats about these numbers

1. **Precision at 0.92 is reported against one hard negative per positive.**
   Precision depends on that ratio, so the 4:1 column from decision 16 is
   printed alongside it. A single precision number without its negative ratio
   is not interpretable.
2. **Some "hard negatives" are really duplicates**, which means precision is
   understated rather than flattered. The label is "a different product group",
   which is a proxy. Caught in the output:
   ```
   0.753  typed: 5 mL Leur Lock Syringe
          shown: 5 mL Leur Lcok Syringe      <- a typo, not a different product
   ```
3. **Positive pairs are capped at six per Cat No group.** One group of 17 items
   contributes 136 pairs on its own and would dominate the measurement.

#### The veto gate, against every known pair

Imported from `tests/test_specs.py` rather than copied, so the harness and the
tests cannot disagree about what the gate is for.

```
    must CANCEL:
      ok   size-mass              size: mass [500.0] vs [1000.0]
      ok   code-adaptor           code: number ['34'] vs ['24']
      ok   code-cassette          size: mw [100000.0] vs [50000.0]
      ok   code-festo             code: number ['6'] vs ['8']
      ok   code-strain            code: number ['2'] vs ['1']
      ok   negation-phenol        negation: ['no'] vs -
      ok   grade-ethanol          grade: ['absolute'] vs ['molecularbiology']
      ok   size-syringe           size: volume [1.0] vs [5.0]
      ok   code-catalogue-suffix  code: b ['1500s'] vs ['1500l']
    must ALLOW:
      ok   unit-conversion / word-order / abstention / size-subset /
      ok   identical / size-subset-micro-sign

    15 of 15 known pairs behave correctly
```

#### Acceptance

```
    PASS precision at 0.92 >= 90%         100.0%
    PASS recall@5 >= 70%                   85.5%
    PASS latency p95 < 300 ms               21.0 ms
    PASS all known spec pairs pass         15 pairs

    ALL TARGETS MET
```

---

### Step I — fifteen real names, and the rule that overrides everything

```
env/bin/python apps/item_duplicate_check/scripts/smoke_test.py
```

Five exact duplicates typed the way people type them, five real look-alike
pairs, five names that do not exist. Every name is a real row in `tabItem`, or
deliberately absent.

```
  ok  serological pipette  50 ml              -> 1.0000 Serological Pipette 50 mL
      names 100% alike - same manufacturer (spl) - both 50 mL - shares serological
  ok  Implen OD600 DiluPhotometer             -> 1.0000 Implen™ OD600 DiluPhotometer™
  ok  Corning 40um Cell Strainer, Blue...     -> 1.0000 Corning® 40µm Cell Strainer
  ok  invitrogen ibright fl1500 imaging...    -> 1.0000 Invitrogen™ iBright™ FL1500
  ok  Multi C Strainer (40 um).               -> 1.0000 Multi C-Strainer (40 μm)
```

Trademark symbols, doubled spaces, `u` for the micro sign, a hyphen, a trailing
dot and the wrong case — all five found at 1.0000, in 1–9 ms.

The look-alikes are where this script was **wrong the first time**, and it is
worth recording. It asserted "band must be `none`", reported 0 of 5, and the
gate had in fact worked perfectly in all five cases. The names are themselves
real catalogue rows, filed more than once, so a strong match against their own
copies is the correct answer. The assertion was wrong, not the rule:

```
  ok  typed   : Corning 70um Cell Strainer, White, Sterile
      offered : 1.0000  Corning® 70µm Cell Strainer, White, Sterile   (own copy)
      withheld: would have scored 0.7477  Corning® 40µm Cell Strainer
                rule: size: length [0.07] vs [0.04]

  ok  typed   : Ethanol absolute ... Ph Eur 2.5L
      withheld: would have scored 0.9371  Ethanol absolute ... Ph Eur 1L
                rule: size: volume [2500.0] vs [1000.0]

  ok  typed   : AseptiCap KS (0.45 + 0.2 um) Capsule filter 8 inch
      withheld: would have scored 0.8583  ... Capsule filter 10"
                rule: code: number ['0.45', '8'] vs ['0.45', '10']

  5 of 5 look-alike partners were correctly withheld
```

The 0.9371 is the one to look at: without the gate, the 1 L bottle would have
raised a strong warning against the 2.5 L bottle.

All five genuinely-new names returned nothing, the closest being 0.39.

#### HARD RULE 1, tested with failures injected on purpose

```
  4a. search raises inside the API
      returned band='none' matches=0 skipped='error (logged)'  (no exception)

  4b. the index hook raises -- the handler must swallow it
      both handlers returned normally with invalidate() raising

  4c. a real Item is re-saved while the hook raises
      item: SERVICE/000034
      Item SAVED  <- on_update fired, the hook raised, the save went through
      rolled back

  4d. hooks registered on Item: ['after_insert', 'after_rename', 'on_trash',
                                 'on_update']
      handlers that could block a save: none
```

4c re-saves an existing Item rather than creating one. The first version tried
to create one and it failed on `HSN/SAC Code is required` and then on
`Item Group None not found` — both belonging to other apps installed on this
site, and neither saying anything about this app's hook. A re-save fires
`on_update`, which is the hook under test, and avoids all of that. Everything
is rolled back.

---

### What the build changed in the three "already done" modules

Only these. No decision was altered.

| File | Change | Why |
|---|---|---|
| `core/specs.py` | `QTY` anchored with a lookbehind | `b1500l` was read as 1,500 litres |
| `core/specs.py` | `spec_codes()` groups on the letter prefix | `bs` vs `bl` hid a real clash |
| `core/specs.py` | skip condition narrowed to pure measurements | it was discarding catalogue codes |
| `core/specs.py` | docstring corrected for hyphenated codes | it described behaviour the code never had |
| `core/normalize.py` | rule 7 separator now covers hyphens and dashes | `96-Well` stayed two tokens, against section 19.1 |

Every one of them has a regression test naming the bug.

---

## 21. Data preprocessing for the model

The model is a pair classifier. It cannot read an item name, so everything
below exists to turn the catalogue into rows of numbers with a label.

### What already existed, and what did not

`data/slim_*.csv` looked like finished preprocessing — the 11 kept columns
from decision 17, plus a `dataset_split` column. An audit
(`scripts/preprocess_audit.py`) found what was really in them:

| Checked | Result |
|---|---|
| Split sizes against decision 16 | 17,914 / 3,838 / 3,838 — **exact match** |
| Shared Cat No across splits | 0 — OK |
| Shared old item code across splits | 0 — OK |
| **Identical NORMALISED name across splits** | **141 — LEAK** |
| Rows that still exist in `site_local` | 22,204 of 25,590; **3,386 are gone** |
| Pair rows, labels, feature matrix | **NONE** |

Two problems and one absence:

1. **The split leaked.** Decision 16 built its product groups by linking on
   the raw item name, so `"Sodium Carbonate"` and `"sodium carbonate."` were
   treated as two products and landed on opposite sides. 141 names. Small —
   0.7% — but it is exactly the class of leakage decision 16 promises is
   zero, and it inflates any test number taken on those files.
2. **The snapshot was stale.** The CSVs are the Excel export. 3,386 of their
   items no longer exist, and the app scores against the live database.
3. **The pair-level data did not exist at all.** Every row in `data/` is one
   item. A pair classifier trains on two item codes, a label, and the
   features between them.

### What we do now

Regenerate from `site_local` with `scripts/build_training_set.py`. The
grouping normalises the name before linking, which removes cause 1; reading
the live database removes cause 2.

```
items in tabItem               22204
maintain stock = 0, excluded    4340   (HARD RULE 2 — never scored at runtime)
items used                     17864

product groups (union-find on three signals):
  shared cleaned Cat No           3929 merges
  identical NORMALISED name       1066 merges
  shared old item code               2 merges
  distinct product groups        12867
  groups holding >1 item          3379

split by product group (random_state 42):
  train           12507    70.0%     9019 groups
  validation       2679    15.0%     1919 groups
  test             2678    15.0%     1929 groups

LEAKAGE CHECK:
  product group spans splits      12867 distinct,  0 span >1 split   OK
  identical normalised name       14047 distinct,  0 span >1 split   OK
  shared cleaned Cat No            9666 distinct,  0 span >1 split   OK
  shared old item code             1332 distinct,  0 span >1 split   OK
```

Non-stock items are excluded. 4,340 rows on `site_local`, including the 79
intentional CAPEX service entries. The model will never be asked about them
at runtime, so training on them would fit a distribution that is never
served.

### The labels

**Positive** — two items sharing a cleaned Cat No. Genuine duplicates: 8
random groups were checked by hand and all 8 were real. Capped at 6 pairs per
group, because one group of 17 items contributes 136 pairs on its own and
would dominate the measurement.

**Negative** — a name-similar item from a **different product group**, four
per positive. Random pairs would teach nothing: `Glycine 500g` against
`Mindray probe cable` is a question no system gets wrong.

Negatives are ranked by plain name similarity, deliberately **not** by the
hand-written scorer's weighted score. Using the scorer's opinion of "similar"
would bake that scorer into the data the model is meant to improve on.

```
split          rows   duplicate   non-dup   ratio
train         16250        3311     12939   1:3.9
validation     3090         692      2398   1:3.5
test           3259         734      2525   1:3.4
total         22599
```

### The features — 21 columns, and two fields that may never be among them

`core/features.py`. `FEATURE_NAMES` is an ordered tuple and the order is part
of every saved model: coefficients are positional, so reordering it silently
invalidates every model trained before. Append only.

**The two forbidden fields.** This is the most important rule in the file:

| Never a feature | Why |
|---|---|
| `cat_no` | it IS the label. A positive pair is "these two share a Cat No", so feeding it in teaches one useless rule and nothing about names — and fails on the 30% of items with no Cat No |
| `custom_reference_old_item_code` | the same trap one step removed: it is one of the three signals that build the product groups, which build the split and the negatives |

`tests/test_features.py` asserts that changing either value cannot move a
single number in the vector. Also excluded, for measured reasons rather than
leakage: `stock_uom` (separation −23.4, points the wrong way),
`description` (80.9% a copy of the name), `item_group`, `item_child_group`,
`brand`.

**Three-state fields cost two columns each.** A field can agree, disagree, or
have nothing to say. Collapsing that into one number forces a choice between
two wrong answers — blank-as-0 makes a missing manufacturer count as evidence
*against* the pair, blank-as-0.5 invents a half-opinion. So each gets
`*_agree` and `*_abstain`, and the model learns what an abstention is worth.
This matters here because coverage is poor on exactly the fields with the
best separation: makes 72.6%, HSN 69.9%, department 49.0%, requester 19.8%.

### Measured separation on the train split

mean(duplicate) − mean(non-duplicate). This is the column to read.

| Feature | Duplicate | Non-dup | Separation |
|---|---|---|---|
| `shared_token_count` | 4.352 | 2.742 | **+1.610** |
| `makes_agree` | 0.880 | 0.400 | **+0.480** |
| `idf_shared_fraction` | 0.789 | 0.349 | **+0.440** |
| `first_token_match` | 0.830 | 0.403 | +0.427 |
| `digit_token_overlap` | 0.560 | 0.181 | +0.380 |
| `token_count_diff` | 0.821 | 1.186 | −0.365 |
| `set_overlap_x_length` | 0.810 | 0.467 | +0.343 |
| `set_overlap` | 0.893 | 0.580 | +0.312 |
| `hsn_agree` | 0.463 | 0.194 | +0.269 |
| `raw_similarity` | 0.868 | 0.670 | +0.199 |
| `ordered_similarity` | 0.876 | 0.707 | +0.169 |
| `makes_abstain` | 0.019 | 0.157 | −0.138 |
| `naming_series_differs` | 0.254 | 0.347 | −0.093 |
| `department_agree` | 0.219 | 0.141 | +0.078 |
| `length_ratio` | 0.878 | 0.807 | +0.071 |
| `same_category_other_division` | 0.026 | 0.013 | +0.013 |
| `requester_agree` | 0.008 | 0.005 | +0.003 |
| `requester_abstain` | 0.986 | 0.988 | −0.002 |

Three things worth noticing before any training happens:

- **`makes_agree` is the strongest single field**, +0.480, which is the alias
  table earning its keep. Compared raw, 1,438 spellings make this field noise.
- **Four features carry nothing on this data**: both `requester_*` columns
  (filled on 19.8% of items, and when it is filled the two sides almost never
  match), `department_abstain`, and `same_category_other_division` — only 2.6%
  of duplicate pairs are cross-division, so the signal decision 15 predicted
  is real but rare. They are kept rather than dropped, because a logistic
  regression will give them near-zero weight anyway and dropping them now
  would hide the finding.
- **`naming_series_differs` is negative here (−0.093)**, where decision 7
  measured the signal as inverted and therefore expected it positive. On this
  sample, duplicates are slightly *less* likely to cross naming series. Worth
  a look before relying on it.

### What the veto gate already settles

| Split | Duplicates it cancels | Non-duplicates it catches |
|---|---|---|
| train | 171 of 3,311 (5.2%) | 4,737 of 12,939 (36.6%) |
| validation | 19 of 692 (2.7%) | 776 of 2,398 (32.4%) |
| test | 42 of 734 (5.7%) | 727 of 2,525 (28.8%) |

The left column is what the gate costs: real duplicates it refuses. The right
is what it is worth: about a third of the wrong answers never reach the model
at all. Decision 3 measured that trade as precision 78.1% → 91.0%.

### Validation, and two bugs it caught

`scripts/validate_training_set.py` runs six checks and refuses the dataset if
any fails. Both of these were found by it rather than by the build, and
neither would have been noticed by a training script:

1. **5,180 duplicate rows.** Negatives were generated per positive *pair*, so
   a Cat No group of three items asked for the same left item's negatives
   twice. Fixed by generating per distinct left item.
2. **1,561 more duplicate rows, in the other direction.** Every feature is
   symmetric, so `(X, Y)` and `(Y, X)` are the same example written twice —
   and both orders really do occur, because X and Y each find the other among
   their name-similar negatives. Fixed by keying on the sorted pair. Without
   it the dataset double-weights whichever examples are mutually similar,
   which is exactly the hardest and most influential subset.

A third, smaller one came from the feature code itself:
`idf_shared_fraction` returned `1.0000000000000002` on 181 rows, because when
the two token sets are identical the numerator and denominator are the same
quantity summed in a different order.

```
  an item paired with itself            0   OK
  the same pair written twice           0   OK
  all 21 features x 22599 rows: finite, and the 18 bounded ones in [0,1]  OK
  feature columns naming a forbidden field  0   OK
  rows whose split column disagrees     0   OK
  items appearing in two splits         0   OK

  DATASET IS FIT TO TRAIN ON -- all six checks passed
```

### Files

| File | What |
|---|---|
| `core/features.py` | the 21 features. Pure Python, no sklearn |
| `tests/test_features.py` | 26 tests, including the two leakage guards |
| `scripts/preprocess_audit.py` | what was wrong with `data/slim_*.csv` |
| `scripts/build_training_set.py` | groups, split, pairs, features |
| `scripts/validate_training_set.py` | six checks; exit code 1 if unfit |
| `data/pairs_{train,validation,test}.csv` | 22,599 pair rows, 31 columns |
| `data/pairs_summary.json` | counts, leakage results, feature list |

Only the `FEATURE_NAMES` columns may be fed to a model. The six columns
prefixed `_` are for a human auditing a row, and `_cat_no_a` is the value
that made the label.

---

## 22. The model, and which engine ships

### What was built

A pair classifier, trained on the 22,599 pair rows from section 21.
Decision 8's choice was Logistic Regression with Gradient Boosting as the
challenger; both were fitted, and the challenger won.

```
env/bin/python apps/item_duplicate_check/scripts/train_model.py --test --save
```

Measured on **validation**, with the veto gate applied exactly as production
applies it:

| At 0.92 | Rules (baseline) | Logistic Regression | Gradient Boosting |
|---|---|---|---|
| Precision | **100.0%** | 97.6% | 99.3% |
| Recall | 64.2% | 59.2% | **65.5%** |
| ROC AUC | — | 0.9483 | **0.9571** |
| PR AUC | — | 0.8925 | **0.9114** |

Read once on **test**, after the choice was already made on validation:
precision 99.6%, recall 65.3% at 0.92. Validation and test agree to within
0.3 points, which is the useful signal here — the split is clean and the
model is not overfitted.

### The verdict, which is decision 8's and not ours

**Net change against the rules: −0.7 precision, +1.3 recall.**

Decision 8, written before any of this existed:

> "The model adds about +1.2 points over good rules. Not 15. Measured, not
> estimated." … "Ship the hand-written weights unless the model clearly
> wins. Ties go to the simpler option."

It predicted the size of the gain correctly, and the result is not a clear
win. So **`rules` is the shipped engine** and the model is opt-in. Both live
in the code permanently, which decision 8 also requires.

The reason the ceiling is this low is in decision 8 as well: the labels come
from Cat No agreement, and those are *easy* pairs — mostly near-identical
names. There is little in them a model can learn that the rules do not
already capture. The gain was always supposed to come from Item Match Log,
where the examples are cases a human had to think about — and step A showed
that traffic is not there.

### One finding that costs decision 8 a reason

Decision 8 chose logistic regression partly because **"the weights are
readable — you can see and explain why something was flagged"**. On this
feature set they are not:

```
idf_shared_fraction      +4.351
set_overlap              -1.454      <- negative?
ordered_similarity       -1.201      <- negative?
set_overlap_x_length     -0.933
makes_agree              +0.776
```

`ordered_similarity` carrying a negative weight does not mean "more alike
means less likely duplicate". It is collinearity: the name-similarity
features are strongly correlated, `idf_shared_fraction` absorbed the signal,
and the rest became correction terms. So the coefficients cannot be read as
importance — and the model that actually won, gradient boosting, has no
readable weights at all.

That removes one of the three reasons decision 8 gave for preferring a
model-with-readable-weights, and it is why `core/model.py` builds its reason
string from **feature values** rather than from weights. The panel still says
"names 97% alike · same manufacturer · both 50 mL"; it just no longer claims
that is what the model weighted.

### Two engines, one switch

`Item Duplicate Check Settings → Scoring Engine`, default `rules`.

```
engine configured = rules  -> engine used = rules   band=strong
     1.0000 strong  name    Corning(R) 40um Cell Strainer, Blue, Sterile
     cancelled: size: length [0.04] vs [0.07]

engine configured = model  -> engine used = model   band=strong
     0.9879 strong  model   Corning(R) 40um Cell Strainer, Blue, Sterile
     cancelled: grade: ['sterile'] vs ['tissueculture']
```

What stays identical whichever engine runs:

- **Step 0, the exact Cat No lookup.** Extracted from `scorer.py` into
  `cat_no_step0()` and shared. Before that the model path had no Step 0 at
  all, which would have made the strongest single signal in the system
  conditional on a settings switch.
- **The veto gate.** Applied on top of whatever the scorer produced. A model
  trained on these labels calls a 500 g pack and a 1 kg pack the same
  product; decision 3 measured the gate as precision 78.1% → 91.0%.
- **The log.** Every check writes one Item Match Log row, with `engine`
  recorded, so a logged decision can be attributed to the engine that made
  it.

### Three ways the model is refused, all of them silent fallbacks to rules

`core/model.py:load()` returns `None` rather than raising, for every failure.
The rules are a working engine, so nothing here is worth failing a user's
request over.

| Refused when | Why it matters |
|---|---|
| the model file is missing or unreadable | a fresh checkout has no model |
| the saved feature list differs from `FEATURE_NAMES` | **coefficients are positional.** A changed, or merely reordered, feature list computes every number against the wrong columns — silently, with no error |
| the saved object has no `predict_proba` | wrong file |

`tests/test_model.py` asserts all three, including the reordered-list case,
which is the dangerous one because nothing about the file looks wrong.

### The cost of using the model path

The rules engine returns an **exact** top five: its score has a computable
upper bound, so candidates that cannot place are skipped without being
scored. A model has no such bound — there is no way to know what it will say
without asking it.

So `core/model.py` ranks candidates by cheap name similarity and asks the
model about the best 200. That makes the model path an **approximate** top
five. On the probe above it also ran faster (112 ms against 751 ms on a cold
index), because the prefilter caps the work.

### Four bugs found while wiring this up

| # | Bug | Found by |
|---|---|---|
| 1 | `difflib.ratio()` is **not symmetric** — 0.381 one way, 0.333 the other. Both `features.py` and `scorer.py` had it | the symmetry test in `test_features.py` |
| 2 | `search()` used a cached `SequenceMatcher` for the exact value, which then disagreed with `score_pair()` on the same pair | the above |
| 3 | IDF sums were asymmetric in the last bits: set iteration order varies and floating-point addition is not associative, so `1.0` one way and `0.9999999999999999` the other. Fixed by sorting before summing | the same test |
| 4 | `predict_proba` raises on an empty array, and an empty shortlist is normal | `test_model.py` |

### Stale state the delete-and-restore cycle left behind

Restoring `corpus.py` and `scorer.py` from the backup did not restore what
had been changed *around* them. Found by re-checking rather than assumed:

| Was | Effect |
|---|---|
| `item_hooks.py` still a no-op | **the index would never have been invalidated** — every Item change silently ignored until a worker restart |
| `tasks.py` still a no-op | no nightly rebuild |
| `eval_harness.py`, `smoke_test.py` still carrying a "does not run" guard | the two measurement scripts refused to run although their dependencies were back |
| `api.py` still the gutted shell | **the app's entry point did nothing** — the smoke test reported 0 of 5 exact duplicates found, with both engines sitting unused in the codebase |
| `site_local` left at `enabled=1, silent_mode=0` | the app was live and showing warnings, because test scripts "restore what they found" and a previous run had left it on |

The last one is worth keeping in mind: a script that saves and restores a
setting preserves whatever state it started in, so a chain of such scripts
drifts. `scripts/` now restores the SHIPPED state explicitly — disabled,
silent, rules.

### Where it stands

```
149 unit tests                                              OK
dataset, six checks                                         OK
eval harness, rules engine, validation                      precision 100.0%
                                                            recall@5  85.5%
                                                            p95       16.3 ms
                                                            15/15 spec pairs
smoke test, 15 hand-chosen names                            15/15
HARD RULE 1, three injected failures                        save never blocked
site_local                                                  enabled=0, silent=1,
                                                            engine=rules
```

---

## 23. Item Creation Form -- where the check actually belongs

### The mistake in step A

Step A asked "are items still being created?", measured `tabItem`, found
creation stopped in February 2026, and concluded the live panel would see
almost no traffic.

**It measured the wrong table.** Nobody types into `tabItem` directly. They
fill in an **Item Creation Form**, which is approved and only then becomes an
Item.

```
Item Creation Form rows: 11396
  first  2024-03-18
  last   2026-10-21 16:07     <- the day this was found
```

| Month | Item Creation Form | tabItem |
|---|---|---|
| 2025-07 | 944 | — |
| 2025-11 | 504 | 671 |
| 2025-12 | 538 | 548 |
| 2026-01 | 658 | 552 |
| 2026-02 | 241 | 304 |
| 2026-10 | 1 | 0 |

The two tracks each other, which is what you would expect if approving a form
creates an Item. Both slowed after February, so step A's conclusion about
VOLUME still stands -- but its conclusion about WHERE was wrong, and that is
the part that mattered: a warning attached only to `Item` sits on a screen
nobody opens.

By status: 10,075 approved, 759 open, 33 rejected.

### Field coverage is better here than on the catalogue

| Field | Item Creation Form | tabItem |
|---|---|---|
| `item_name` | 100.0% | 100.0% |
| `cat_no` | **88.4%** | 70.6% |
| `makes` | **91.0%** | 72.6% |
| `department` | **100.0%** | 49.0% |
| `maintain_stock` | 84.6% | 80.5% |

The request form is filled in more carefully than the row it creates. So the
check sees BETTER input here, not worse.

### What it would have caught

Replaying the 40 most recent stock requests through the rules engine, with
the Cat No supplied exactly as the panel supplies it:

```
strong   28 of 40  (70%)
soft      3 of 40  ( 8%)
none      9 of 40  (22%)

0.9700  typed: Mouse IL-4 ELISA Kit.      found: Mouse IL-4 ELISA Kit
1.0000  typed: Pig Semen Cup              found: Pig Semen Cup
1.0000  typed: Nursery pens               found: Nursery pens
0.9700  typed: Mouse IFN gamma ELISA Kit (BMS606-2) - Invitrogen
        found: Mouse IFN gamma ELISA Kit.      same Cat No (BMS6062)
```

70% of recent real requests would have raised a strong warning. That number
is an alert RATE, not an accuracy: it says these requests really were mostly
for things the catalogue already holds.

### The one thing that differs between the two forms

`Item` calls it `is_stock_item`. `Item Creation Form` calls it
`maintain_stock`. Same meaning, same HARD RULE 2, different spelling.

So the panel carries a field map per doctype rather than assuming:

```javascript
item_duplicate_check.FORMS = {
  "Item":               { maintain_stock: "is_stock_item", ... },
  "Item Creation Form": { maintain_stock: "maintain_stock", ... },
};
```

Adding a third form is one entry in that map. Reading the field name off the
wrong doctype would have silently disabled the CAPEX skip, which is the rule
protecting the 79 intentional service-line duplicates.

`item_code` is only sent from the `Item` form, where an existing row must not
match itself. A request form is never a catalogue row, so there is nothing to
exclude.

### Verified against real payloads

```
typed : Mouse IL-4 ELISA Kit.      cat_no='BMS613' makes='Thermo fisher Scintific'
  -> band=strong  112 ms
     0.9700 strong  Mouse IL-4 ELISA Kit
             same Cat No (BMS613) - names 100% alike - same manufacturer (thermo)

typed : Duo mix single use mixing bag 500l
  -> band=soft      9 ms
     0.9022 soft    Duo mix single use mixing bag
     withheld: size: volume [500000.0] vs [50000.0]      <- the gate, working

typed : electrical installation at mrna lab    maintain_stock=0
  -> band=none      0 ms   maintain stock is off (CAPEX / service line)
```

Note the middle one: a 500 L bag and a 50 L bag, correctly withheld. And the
last one: HARD RULE 2 skipping the CAPEX service line before any scoring.

---

### The bug that made it invisible anyway

Wiring the panel to the form was not enough. It drew nothing, for everybody,
whatever they typed — and it failed silently, which is the worst way to fail.

On **Item Creation Form**, `maintain_stock` is a Check whose **default is 0**:

```
maintain_stock default = '0'       a new doc starts at 0
saved rows:  maintain_stock=1  9638      maintain_stock=0  1758
```

So 84.6% of requests end up as stock items — the user ticks the box at some
point — but on a new form the value is 0 before anybody has decided anything.
HARD RULE 2 skips on 0, so the check exited before scoring, every time.

**An explicit 0 and an untouched default are not the same thing.** One is a
statement; the other is the absence of one. Treating them as one is what
broke it.

The panel now sends `null` until the user actually touches the field, and the
API honours only a stated 0:

```python
stated = maintain_stock is not None and str(maintain_stock).strip() != ""
if stated and not frappe.utils.cint(maintain_stock):
    return _empty("maintain stock is off (CAPEX / service line)")
```

The rule itself is unchanged. The 79 CAPEX rows all named "electrical
installation at mrna lab" carry a deliberate 0 and are still skipped before
any scoring happens.

Checked by `scripts/smoke_test.py` section 5, which is now part of the suite:

```
  ok   maintain_stock=None   new form, user has not touched it  -> check  band=strong
  ok   maintain_stock=''     empty string, as HTTP sends it     -> check  band=strong
  ok   maintain_stock=0      user unticked it                   -> skip   band=none
  ok   maintain_stock='0'    unticked, via HTTP                 -> skip   band=none
  ok   maintain_stock=1      user ticked it                     -> check  band=strong
  ok   maintain_stock='1'    ticked, via HTTP                   -> check  band=strong
  6 of 6 behaved correctly
```

The lesson generalises: a doctype default is not a user's answer, and any
rule that skips on a falsy value will misfire wherever the field defaults to
falsy.

---

### One warning before saving, with the item code

Typing warnings are easy to miss: the banner is above the fold, the user is
looking at the field they are filling in. So there is now a second, louder
moment -- **one dialog, when Save is pressed**, listing the **item code** and
**item name** of everything that already matches.

```
  This item looks like it already exists

  You typed: Mouse IL-4 ELISA Kit
  The catalogue already has:

     R&D07879        Mouse IL-4 ELISA Kit                      97%
                     same Cat No (BMS613) - names 100% alike
     RDRM01090557    Mouse IL-4 ELISA Kit.                     97%
                     same Cat No (BMS613) - names 100% alike

  [ Let me check ]                              [ Save anyway ]
```

The code is shown because the name alone is not actionable -- the whole
problem is that several rows share a name, and the code is what has to go
into a Material Request.

#### How it pauses a save without blocking one

Frappe already provides the mechanism, in `form.js`:

```javascript
() => this.script_manager.trigger("validate"),
() => this.script_manager.trigger("before_save"),
() => {
    if (!frappe.validated) { fail(); return; }
    frappe.ui.form.save(...);
},
```

`trigger()` awaits anything a handler returns, and `fail()` does one thing:
re-enables the Save button. So `frappe.validated = false` **pauses** the save
-- nothing is raised, nothing is stuck, and pressing Save again works.

That is why this uses `before_save` and not `validate`. `validate` is the
hook that can refuse a save outright; it is still not used anywhere in this
app, and neither is `frappe.throw`.

Five things keep it a warning rather than an obstacle:

| | |
|---|---|
| **Save anyway** is one click | and it is the primary button |
| shown **once per form** | `warned` stays true; the second Save goes straight through, even if the user did nothing |
| a slow server does not hold the save | 2.5 second timeout, then the save proceeds |
| any failure proceeds | the promise resolves, never rejects -- a rejection would reach `.catch(fail)` and stop the save for a reason the user cannot see |
| it never fires out of scope | disabled app, existing record, a deliberate `maintain_stock = 0`, or a name under three characters: no dialog, no delay |

Closing the dialog with Esc or the X counts as "let me check", not as
consent, and is logged as `gave up`.

#### What gets logged

Both buttons write to Item Match Log against the row that produced the
warning: **Save anyway** records `created anyway`, **Let me check** records
`gave up`. Those, with the override reason, are the judgements decision 12
is built to collect.

---

## Summary of every decision

| Step | We chose | We rejected | Decided by |
|---|---|---|---|
| 0 | **ML model** (owner instruction, 2026-10-21) | search first, ML last — removed | owner decision; supersedes the +1.2 point measurement |
| 1 | Separate app | Code in another app; server scripts | Needs an independent kill switch |
| 2 | 9 normalisation rules | Raw text; sound-alike codes | 12% have ™, 8% have stray spaces |
| 3 | 4-part veto gate | Size only; let the model learn it | 81.4% → **91.0%** precision |
| 4 | **REMOVED** — no candidate source exists | rare-word index (deleted); child group filter | the child-group finding still holds: 80.5% of true pairs are in different groups |
| 5 | **REMOVED** — the model scores | hand-weighted sum (deleted) | superseded by decision 0 |
| 6 | Cat No as lookup only | Cat No as a model clue | Avoids label leakage |
| 7 | name + cat_no + makes | stock_uom, description, brand | Separation table |
| 8 | Logistic Regression | Neural net; embeddings as judge | Too little data; embeddings fail on sizes |
| 9 | Hard negatives from our own search | Random pairs | Training must match live conditions |
| 10 | Warn in 3 bands | One threshold | Needs tuning on real traffic |
| 11 | Never block | Block high-confidence duplicates | 79 CAPEX rows are intentional |
| 12 | Log everything from day one | Add logging later | It is the future training data |
| 13 | Precision first | Recall first | Trust does not come back |
| 14 | Work from the Excel export | Wait for a database backup | No backup available |
| 15 | Parse Item Code, never match it as text | Treat the code as text | Two unrelated codes are 92% similar as strings |
| 16 | 70/15/15 split **by product group** | Shuffle rows and split | Row-splitting leaks the same product into both sides |
| 17 | Keep 11 columns of 57 | Keep all 57; keep only 5 | Measured separation; dropping `Maintain Stock` breaks the CAPEX gate |
| 18 | 6 major / 5 minor, by ablation | Rank by separation | Separation ignores coverage; ablation counts decisions actually changed |
| 19 | Strip ™ BEFORE Unicode NFKC | Strip it after | NFKC expands ™ into the letters "TM", which glue onto the previous word |
| 19b | Size check uses SUBSET, not equality | Compare size sets for equality | Equality wrongly cancels `{0.22um, 33mm}` vs `{0.22um}` — same product |
| 18b | Description is major for DISPLAY, never scored | Score it; drop it entirely | 5 scoring designs tested, none beat ignoring it; but the user must read it to judge a suggestion |

---

## All measured numbers

| What | Value |
|---|---|
| Items in catalogue | 22,204 (4,300 disabled) |
| Items sharing a Cat No with another | 7,781 (35%) |
| Groups with identical names | 2,782 |
| Precision, full veto gate | **91.0%** |
| Precision, size-only veto | 81.4% |
| Precision, no veto | 78.1% |
| Recall@5, rare-word index | 89.7% |
| Recall@5, with full veto | 79.5% |
| Recall@5, child-group filter | 24.7% |
| True pairs in a *different* child group | 80.5% |
| Median search time | 4.7 ms (7.5 ms full pipeline) |
| Alert rate at 0.92 | 33% |
| ML gain over rules | +1.2 points |
| Positive training pairs available | 5,473 (Excel export) / **6,416** (`site_local`, step C) |
| Negative pairs available | 592,497 |

### Measured in step H, on `site_local`, by `scripts/eval_harness.py`

| What | Validation | Test |
|---|---|---|
| Positive pairs | 822 | 870 |
| Precision at 0.92 | **100.0%** | 100.0% |
| Recall at 0.92 | 64.2% | 63.4% |
| Precision at 0.85 | 98.3% | 97.9% |
| Precision at 0.75 | 93.1% | 93.8% |
| Recall@5 | **85.5%** | 88.2% |
| Latency median | 2.4 ms | 1.9 ms |
| Latency p95 | 21.0 ms | 17.7 ms |
| Known spec pairs passing | 15 of 15 | 15 of 15 |
| Items skipped by Maintain Stock | 4,340 | — |
| Index build, 22,204 items | 136 ms | — |
| Candidate lookup | 0.02–0.23 ms | — |

---

## Current status

| Part | Status |
|---|---|
| App created | **Done** |
| Data audited (25,590 items, 56 columns) | **Done** |
| Item Code scheme decoded | **Done** |
| Train / validation / test split, leakage-checked | **Done** |
| Columns cut from 57 to 11 | **Done** |
| Attributes split into major / minor by ablation | **Done** |
| `core/normalize.py` | **Done** — 10 rules, 1 fix in step B |
| `core/specs.py` (veto gate) | **Done** — 4 checks, 2 fixes in step B |
| `core/itemcode.py` | **Done** — 4 code shapes parsed |
| `core/corpus.py` (rare-word index) | **REMOVED** 2026-10-21 |
| `core/scorer.py` (weighted sum) | **REMOVED** 2026-10-21 |
| `core/makes.py` + `config/makes_alias.json` | **Done** — 119 spellings → 7 names |
| `core/settings.py`, `core/log.py` | **Done** |
| Doctypes (Settings, Match Log, child) | **Done** — installed on `site_local` |
| API and hooks | **Shell only** — wired, logs, returns `model not built` |
| Form panel | **Done** — on **Item Creation Form** and Item |
| Test harness | **Blocked** — needs a candidate source and a scorer |
| Unit tests | **31 passing** (was 107; 76 removed with the two modules) |
| `core/features.py` (21 features) | **Done** — 26 tests, leakage guarded |
| Pair-level dataset, 22,599 rows | **Done** — 0 leakage, all 6 checks pass |
| `core/corpus.py` (candidate feeder) | **Restored** — the model needs a shortlist |
| `core/scorer.py` (baseline / fallback) | **Restored** — decision 8 requires it |
| `core/model.py` + trained classifier | **Done** — gradient boosting, 16 tests |
| Two engines behind one Settings switch | **Done** — `rules` is the default |
| **Which engine ships** | **rules** — the model does not clearly win (decision 8) |

### Open question that blocks half the plan

**Are items still being created?** On `site_local`, new items stopped in **February 2026**,
while Stock Entries and Material Requests continue to October 2026.

- **If yes** — build everything, including the live panel and the model.
- **If no** — skip the live panel and the model. Focus on cleaning the 7,781 items already
  in Cat No clashes. The rules module in step 3 is still needed either way.

Before February the site created about 708 items a month, which would give roughly 700 real
decisions in three months — plenty. After February it is close to zero.

### ANSWERED — measured on `site_local`, step A

Creation has effectively stopped. Measured with
`scripts/scope_check.py`, not inferred:

| Month | Items created |
|---|---|
| 2025-11 | 671 |
| 2025-12 | 548 |
| 2026-01 | 552 |
| 2026-02 | 304 |
| 2026-03 | **1** |
| 2026-04 | 0 |
| 2026-05 | **6** |

Last Item created: **2026-05-16**.

> **CORRECTED in section 23.** This measured `tabItem`, which nobody
> types into directly. The form people actually fill in is **Item
> Creation Form**: 11,396 rows, last written to 2026-10-21. The
> volume conclusion below still holds; the conclusion about where
> the panel belongs did not.

**What this changes:** everything was built anyway, including the live panel, so
nothing is blocked. But the two-week silent run in decision 10 will not produce
the ~700 decisions it was supposed to produce, because the traffic is not there.
The thresholds will have to be tuned on a **replay of history** instead, which
is what `scripts/eval_harness.py` already does. See section 20, step A.

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO
bench --site <your-site> install-app item_duplicate_check
```

The app ships **disabled by default**. Turn it on in **Item Duplicate Check Settings**, which also
holds the kill switch, the thresholds and every weight listed above.

## License

MIT
