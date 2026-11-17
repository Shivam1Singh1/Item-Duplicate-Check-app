/**
 * item_duplicate_check.js  --  the warning panel, on both forms where a new
 * item is actually typed.
 *
 * WHICH FORMS, AND WHY BOTH
 * -------------------------
 *   Item                 the catalogue table itself
 *   Item Creation Form   the request form people really fill in
 *
 * The second one is the one that matters. `tabItem` stopped being written to
 * directly in February 2026, but Item Creation Form has 11,396 rows and was
 * last written to today. A warning attached only to Item would sit on a
 * screen nobody opens.
 *
 * The two forms do not spell their fields the same way -- Item has
 * `is_stock_item`, Item Creation Form has `maintain_stock` -- so this file
 * carries a FIELD MAP per doctype instead of assuming. Adding a third form
 * is one entry in that map.
 *
 * WHAT IT DOES
 * ------------
 * While somebody is creating an item, this asks the server every 600 ms of
 * quiet "does this already exist?" and draws the answer above the form.
 *
 *     score >= strong threshold (0.92)   a banner, with the matched item,
 *                                        the reason, and two buttons
 *     between soft and strong (0.75)     a quiet one-line note with links
 *     below soft                         nothing at all, but still logged
 *
 * Measured against the 40 most recent real requests on Item Creation Form:
 * 28 would have raised a strong warning, 3 a quiet note, 9 nothing.
 *
 * TWO PLACES IT SPEAKS
 * --------------------
 *   WHILE TYPING   a banner or a quiet note, 600 ms after the last keystroke
 *   AT SAVE TIME   ONE dialog, naming the item CODE and NAME of everything
 *                  that already matches
 *
 * The save-time one is shown once per QUESTION. Press Save again without
 * changing anything and it does not ask twice -- that is how a warning turns
 * into an obstacle. Edit the item name, the Cat No or the manufacturer and it
 * asks again, because that is a different question and the previous answer
 * does not cover it.
 *
 * WHAT IT DOES NOT DO, ON PURPOSE
 * -------------------------------
 * There is no `validate` handler in this file -- that is the hook that can
 * refuse a save outright, and it is not used. The save-time dialog uses
 * `before_save` and Frappe's own `frappe.validated` flag, which pauses the
 * save cleanly: the Save button is re-enabled and nothing is raised. "Save
 * anyway" is always one click, a slow or failing server lets the save
 * through, and the second Save press never asks again.
 *
 * Everything is also wrapped so that a failure draws nothing rather than
 * breaking the form. If the server is down, the app is uninstalled, or the
 * response is a shape this code does not expect, the form behaves exactly as
 * it would without the app.
 *
 * WHY 600 ms
 * ----------
 * Long enough that typing "Serological Pipette 50 ml" is one request rather
 * than twenty-five, short enough that the answer arrives while the user is
 * still looking at the field. The server side is a few milliseconds, so the
 * debounce is the whole of the perceived delay.
 *
 * WHY THE BUTTONS MATTER MORE THAN THE BANNER
 * -------------------------------------------
 * "Open it" and "Save anyway" are not just navigation -- each one writes a
 * human judgement on a real pair back to Item Match Log. Three months of
 * those is the training data this app is eventually meant to learn from, and
 * it is the only source of labels free of the Cat No leakage problem. That
 * is why "Save anyway" asks one short follow-up question.
 */

frappe.provide("item_duplicate_check");

item_duplicate_check.DEBOUNCE_MS = 600;

/**
 * Per-doctype field map. The keys are what the API expects; the values are
 * what the form calls them.
 *
 * `maintain_stock` is the one that differs, and it is not cosmetic: it is
 * HARD RULE 2, the check that keeps the 79 intentional CAPEX service rows
 * from being warned about.
 */
item_duplicate_check.FORMS = {
	Item: {
		item_name: "item_name",
		cat_no: "cat_no",
		makes: "makes",
		maintain_stock: "is_stock_item",
		description: "description",
	},
	"Item Creation Form": {
		item_name: "item_name",
		cat_no: "cat_no",
		makes: "makes",
		maintain_stock: "maintain_stock",
		description: "description",
	},
};

/** Why somebody created a duplicate anyway. Four answers cover the real
 *  cases; anything else means the rules need a look. */
item_duplicate_check.OVERRIDE_REASONS = [
	"different size",
	"different grade",
	"genuinely new",
	"service line",
];

/**
 * Engine status, fetched once per page load and cached.
 *
 * The panel needs to know whether the app is enabled before it does
 * anything. Asking on every keystroke would be wasteful; asking never would
 * mean a disabled app still generates traffic.
 */
item_duplicate_check.status = null;

item_duplicate_check.get_status = function () {
	if (item_duplicate_check.status) {
		return Promise.resolve(item_duplicate_check.status);
	}
	return frappe
		.call({ method: "item_duplicate_check.api.engine_status" })
		.then((r) => {
			item_duplicate_check.status = (r && r.message) || { enabled: false };
			return item_duplicate_check.status;
		})
		.catch(() => {
			// A failure here must not leave the form in a broken state, and
			// must not retry on every keystroke either.
			item_duplicate_check.status = { enabled: false };
			return item_duplicate_check.status;
		});
};

/**
 * The panel itself. One instance per form.
 */
item_duplicate_check.Panel = class Panel {
	constructor(frm) {
		this.frm = frm;
		this.map = item_duplicate_check.FORMS[frm.doctype] || {};
		this.$wrapper = null;
		this.last_query = null;
		this.last_log = null;
		this.last_band = "none";
		this.timer = null;
		//: has the user touched Maintain Stock? Until they do, its value is
		//: a doctype default rather than a decision. See
		//: maintain_stock_state().
		this.stock_touched = false;
		//: WHAT the save-time warning was last shown for, or null if it has
		//: not been shown. Deliberately not a boolean.
		//:
		//: A boolean meant one warning per FORM: dismiss it, add a letter to
		//: the name, press Save, and nothing was shown. That is precisely the
		//: "type Diiary instead of Diary" move this app exists to catch, and
		//: the flag was helping it through -- it also set __idc_warned, so the
		//: server-side message was suppressed as well and the user got
		//: nothing at all. Measured on REQ/ITEM/2026-2027/00796.
		//:
		//: Keyed on the question instead: pressing Save twice on unchanged
		//: values still asks once, and changing any field the answer depends
		//: on asks again.
		this.warned_for = null;
	}

	/** Read a mapped field off the document. */
	value(key) {
		const fieldname = this.map[key];
		return fieldname ? this.frm.doc[fieldname] : null;
	}

	/** Our own container, created once and reused. Writing into a div we own
	 *  means redrawing cannot accumulate stale banners, which is what happens
	 *  if you keep calling dashboard.add_comment(). */
	container() {
		if (this.$wrapper && this.$wrapper.closest("body").length) {
			return this.$wrapper;
		}
		const $form = $(this.frm.layout.wrapper).find(".form-layout").first();
		this.$wrapper = $('<div class="idc-panel"></div>');
		if ($form.length) {
			$form.prepend(this.$wrapper);
		} else {
			$(this.frm.layout.wrapper).prepend(this.$wrapper);
		}
		return this.$wrapper;
	}

	clear() {
		if (this.$wrapper) {
			this.$wrapper.empty();
		}
	}

	/** Schedule a check. Every call resets the clock, so a burst of typing
	 *  produces exactly one request, 600 ms after the last keystroke. */
	schedule() {
		if (this.timer) {
			clearTimeout(this.timer);
		}
		this.timer = setTimeout(() => this.run(), item_duplicate_check.DEBOUNCE_MS);
	}

	/**
	 * Ask the server, then draw. Never throws.
	 */
	/**
	 * What to tell the server about Maintain Stock.
	 *
	 * null means "nobody has said". That is NOT the same as 0, and the
	 * difference is the whole reason the check works on this form at all:
	 * on Item Creation Form the field is a Check whose DEFAULT is 0, and the
	 * user ticks it later -- 9,638 of 11,396 saved rows end up at 1. Reading
	 * the untouched default as a decision meant the check never ran for
	 * anyone, whatever they typed.
	 *
	 * Once the user actually touches the field, the real value is sent and
	 * HARD RULE 2 applies exactly as before.
	 */
	maintain_stock_state() {
		if (this.stock_touched) {
			return cint(this.value("maintain_stock"));
		}
		// On an existing record the stored value IS a decision somebody made.
		if (!this.frm.is_new()) {
			return cint(this.value("maintain_stock"));
		}
		return null;
	}

	/**
	 * The question the save-time warning answers.
	 *
	 * These are exactly the three fields sent to check_duplicate that can
	 * change which items come back, so two saves share one warning only when
	 * all three are unchanged. Compared case-insensitively and trimmed, so
	 * re-typing the same name differently cased is not treated as a new
	 * question. JSON.stringify rather than a join, so that "ab" + "c"
	 * and "a" + "bc" cannot produce the same signature.
	 */
	save_signature() {
		return JSON.stringify(
			["item_name", "cat_no", "makes"].map((key) =>
				(this.value(key) || "").trim().toLowerCase()
			)
		);
	}

	run() {
		// HARD RULE 2, enforced on this side too so a deliberately non-stock
		// item does not even generate traffic. The 79 CAPEX service rows all
		// named "electrical installation at mrna lab" are intentional
		// duplicates and carry a deliberate 0.
		const stock = this.maintain_stock_state();
		if (stock !== null && !cint(stock)) {
			this.clear();
			return;
		}

		const name = (this.value("item_name") || "").trim();
		if (name.length < 3) {
			this.clear();
			return;
		}

		// Nothing the server cares about has changed since the last answer.
		const query = JSON.stringify([
			name,
			this.value("cat_no") || "",
			this.value("makes") || "",
		]);
		if (query === this.last_query) {
			return;
		}
		this.last_query = query;

		frappe
			.call({
				method: "item_duplicate_check.api.check_duplicate",
				args: {
					item_name: name,
					cat_no: this.value("cat_no") || null,
					makes: this.value("makes") || null,
					// Only meaningful on the Item form: an existing Item must
					// not match itself. A request form is never a catalogue
					// row, so there is nothing to exclude.
					item_code:
						this.frm.doctype === "Item" && !this.frm.is_new()
							? this.frm.doc.name
							: null,
					// null until the user touches the field -- see
					// maintain_stock_state(). The server honours a stated 0
					// and ignores an unstated one.
					maintain_stock: stock,
					description: this.value("description") || null,
				},
				// No freeze, no spinner: this is a background hint, not an
				// action the user asked for and is waiting on.
				freeze: false,
				silent: true,
			})
			.then((r) => this.draw((r && r.message) || {}))
			.catch(() => {
				// The server already logs its own failures. Here, draw
				// nothing: a duplicate checker that breaks the form is worse
				// than one that is quietly absent.
				this.clear();
			});
	}

	draw(result) {
		this.clear();
		this.last_log = result.log || null;
		this.last_band = result.band || "none";

		const matches = result.matches || [];
		if (!matches.length || this.last_band === "none") {
			// Below the soft threshold, or silent mode. Logged server side,
			// shown to nobody. This is the normal case during the silent run.
			return;
		}

		if (this.last_band === "strong") {
			this.draw_strong(matches[0], matches.slice(1), result);
		} else {
			this.draw_soft(matches, result);
		}
	}

	/** 0.92 and above: say it plainly, and offer the two real choices. */
	draw_strong(top, others, result) {
		const $banner = $(`
			<div class="idc-strong alert alert-warning" style="margin-bottom:12px">
				<div style="font-weight:600;margin-bottom:4px">
					${__("This item looks like it already exists")}
				</div>
				<div class="idc-match" style="margin-bottom:6px"></div>
				<div class="idc-others small text-muted" style="margin-bottom:8px"></div>
				<div class="idc-actions"></div>
			</div>
		`);

		$banner.find(".idc-match").append(this.match_line(top, true));

		if (others.length) {
			$banner
				.find(".idc-others")
				.append(__("Also similar:") + " ")
				.append(
					others.map((m) => this.item_link(m).prop("outerHTML")).join(", ")
				);
		}

		const $actions = $banner.find(".idc-actions");

		$(`<button class="btn btn-xs btn-primary" style="margin-right:6px">${__(
			"Open it"
		)}</button>`)
			.appendTo($actions)
			.on("click", () => {
				this.record("used existing");
				frappe.set_route("Form", "Item", top.item);
			});

		$(`<button class="btn btn-xs btn-default">${__("Save anyway")}</button>`)
			.appendTo($actions)
			.on("click", () => this.ask_override_reason());

		if (result.elapsed_ms) {
			$(
				`<span class="small text-muted" style="margin-left:8px">${result.elapsed_ms} ms</span>`
			).appendTo($actions);
		}

		this.container().append($banner);
	}

	/** 0.75 to 0.92: one quiet line. Easy to ignore, which is the intent. */
	draw_soft(matches, result) {
		const $note = $(`
			<div class="idc-soft small text-muted" style="margin-bottom:10px">
				<span class="idc-label"></span>
				<span class="idc-links"></span>
			</div>
		`);
		$note.find(".idc-label").text(__("Some similar items exist:") + " ");
		$note
			.find(".idc-links")
			.append(matches.map((m) => this.item_link(m).prop("outerHTML")).join(", "));
		this.container().append($note);
	}

	/** "Mouse IL-4 ELISA Kit - names 100% alike - same manufacturer" */
	match_line(match, with_reason) {
		const $line = $("<div></div>");
		$line.append(this.item_link(match));
		if (with_reason && match.reason) {
			// Decision 10: show the reason, not the number. It does more for
			// adoption than any accuracy improvement.
			$line.append($("<span class='text-muted'></span>").text(" - " + match.reason));
		}
		if (match.disabled) {
			$line.append($("<span class='text-muted'></span>").text(" " + __("(disabled)")));
		}
		return $line;
	}

	item_link(match) {
		// Always an Item: the matches come from the catalogue, whichever
		// form the user is typing into.
		//
		// The CODE is shown as well as the name, because the name alone is
		// not enough to act on -- the whole problem is that several items
		// share a name, and the code is what the user has to type into a
		// Material Request.
		return $("<a></a>")
			.attr("href", "/app/item/" + encodeURIComponent(match.item))
			.attr("target", "_blank")
			.text(match.item + " - " + (match.item_name || ""));
	}

	/**
	 * ONE warning, at save time, naming the item code and item name.
	 *
	 * HOW IT STOPS THE SAVE WITHOUT BLOCKING IT
	 * -----------------------------------------
	 * Frappe runs `validate` then `before_save` serially and awaits anything
	 * either returns, then checks one global:
	 *
	 *     () => this.script_manager.trigger("before_save"),
	 *     () => {
	 *         if (!frappe.validated) { fail(); return; }
	 *         frappe.ui.form.save(...);
	 *     },
	 *
	 * `fail()` re-enables the Save button and does nothing else. So setting
	 * `frappe.validated = false` pauses the save cleanly -- no exception, no
	 * stuck button -- and the user can press Save again immediately.
	 *
	 * That is why this does NOT use frappe.throw, and why the whole thing is
	 * wrapped so that any failure resolves rather than rejects. A rejected
	 * promise would reach `.catch(fail)` and stop the save for a reason the
	 * user cannot see, which is exactly the outcome HARD RULE 1 forbids.
	 *
	 * ONE warning per QUESTION -- not one per attempt, and not one per form.
	 * The values it was shown for are remembered in `warned_for`, and a Save
	 * that changes none of them goes straight through, because being asked
	 * twice about the same thing is how a warning becomes an obstacle.
	 *
	 * Changing the item name, the Cat No or the manufacturer asks again. It
	 * has to: the old answer was about different values, and a user who was
	 * warned and then edited the name is the single case this warning most
	 * needs to cover.
	 */
	confirm_before_save() {
		const self = this;

		return new Promise((resolve) => {
			// Anything unexpected: save, do not stand in the way.
			const proceed = () => resolve();

			try {
				// Already asked about EXACTLY these values. The second Save
				// goes straight through -- and carries the flag, so warn.py
				// does not put the same rows on screen again after the save.
				//
				// Anything else is a question that has not been asked yet,
				// including a name the user edited after being warned.
				const signature = self.save_signature();
				if (self.warned_for === signature) {
					self.frm.doc.__idc_warned = 1;
					return proceed();
				}

				if (!self.frm.is_new()) {
					return proceed();
				}

				const stock = self.maintain_stock_state();
				if (stock !== null && !cint(stock)) {
					return proceed();
				}

				const name = (self.value("item_name") || "").trim();
				if (name.length < 3) {
					return proceed();
				}

				// The live panel may have nothing current -- the user can
				// type and hit Save inside the 600 ms debounce. So ask
				// again here rather than trusting what is on screen.
				let settled = false;
				const once = (fn) => {
					if (!settled) {
						settled = true;
						fn();
					}
				};

				// Never make somebody wait on us. If the server has not
				// answered in two and a half seconds, the save goes ahead.
				const timer = setTimeout(() => once(proceed), 2500);

				frappe
					.call({
						method: "item_duplicate_check.api.check_duplicate",
						args: {
							item_name: name,
							cat_no: self.value("cat_no") || null,
							makes: self.value("makes") || null,
							item_code:
								self.frm.doctype === "Item" && !self.frm.is_new()
									? self.frm.doc.name
									: null,
							maintain_stock: stock,
							description: self.value("description") || null,
						},
						freeze: false,
						silent: true,
					})
					.then((r) => {
						clearTimeout(timer);
						const result = (r && r.message) || {};
						const matches = result.matches || [];

						if (!matches.length || result.band === "none") {
							return once(proceed);
						}

						self.last_log = result.log || self.last_log;
						self.last_band = result.band;
						self.warned_for = signature;
						once(() => self.show_save_dialog(matches, result, resolve));
					})
					.catch(() => {
						clearTimeout(timer);
						once(proceed);
					});
			} catch (e) {
				proceed();
			}
		});
	}

	/** The dialog itself. Lists every match with its CODE and NAME. */
	show_save_dialog(matches, result, resolve) {
		const self = this;
		let answered = false;

		const finish = (save_it, action) => {
			if (answered) {
				return;
			}
			answered = true;
			if (action) {
				self.record(action);
			}
			// false pauses the save; Frappe re-enables the button and the
			// user can press Save again, which goes straight through as long
			// as they did not change what they are saving.
			// The question has now been asked HERE, before the save. Tell
			// the server, so warn.py logs the save but does not repeat the
			// same three rows in a msgprint once it has landed.
			if (save_it) {
				self.frm.doc.__idc_warned = 1;
			}
			frappe.validated = !!save_it;
			resolve();
		};

		// Only the rows the count is about -- `matches` is the top five by
		// score whatever they scored, and listing a below-threshold row next
		// to "1 item like this already exists" contradicts itself.
		const listed = matches.filter((m) => m.band !== "none");
		const shown_matches = listed.length ? listed : matches.slice(0, 1);

		// CODE, NAME, SCORE. The per-row reason was dropped here on purpose:
		// when a name exists 79 times, five rows repeat the same sentence
		// five times. The inline panel above the form still shows it once.
		const rows = shown_matches
			.map(
				(m) => `
			<tr>
				<td style="padding:4px 10px 4px 0;white-space:nowrap">
					<a href="/app/item/${encodeURIComponent(m.item)}" target="_blank"><b>${frappe.utils.escape_html(
					m.item
				)}</b></a>
				</td>
				<td style="padding:4px 10px 4px 0">${frappe.utils.escape_html(m.item_name || "")}</td>
				<td style="padding:4px 0;white-space:nowrap;text-align:right">${Math.round(
					m.score * 100
				)}%</td>
			</tr>`
			)
			.join("");

		// How many exist, not how many are listed. Falls back to the list
		// length so an older server that does not send the count still reads
		// correctly rather than saying "0".
		const total = result.total_similar || shown_matches.length;
		const headline =
			total <= 1
				? __("1 item like this already exists:")
				: total > shown_matches.length
				? __("{0} items like this already exist. Closest {1}:", [
						total,
						shown_matches.length,
				  ])
				: __("{0} items like this already exist:", [total]);

		const dialog = new frappe.ui.Dialog({
			title:
				result.band === "strong"
					? __("This item looks like it already exists")
					: __("Some similar items already exist"),
			fields: [
				{
					fieldtype: "HTML",
					options: `
						<p>${__("You typed")}: <b>${frappe.utils.escape_html(
						(this.value("item_name") || "").trim()
					)}</b></p>
						<p><b>${frappe.utils.escape_html(headline)}</b></p>
						<table style="width:100%">${rows}</table>
						<p class="text-muted small" style="margin-top:10px">
							${__("Use the existing code if it is the same product.")}
						</p>`,
				},
			],
			primary_action_label: __("Save anyway"),
			primary_action: () => {
				dialog.hide();
				finish(true, "created anyway");
			},
			secondary_action_label: __("Let me check"),
			secondary_action: () => {
				dialog.hide();
				finish(false, "gave up");
			},
		});

		// Closing with Esc or the X is "let me check", not "save".
		dialog.$wrapper.on("hidden.bs.modal", () => finish(false, "gave up"));
		dialog.show();
	}

	/**
	 * The follow-up question, asked only when a STRONG warning is being
	 * ignored. This is the single most valuable field in the log: a person
	 * telling us why our rules were wrong about a pair they can see.
	 *
	 * It is a prompt, not a mandatory field. Cancelling it still dismisses
	 * the banner and still records the override -- nagging would get the
	 * panel switched off, and a missing reason is better than a wrong one.
	 */
	ask_override_reason() {
		const self = this;

		if (this.last_band !== "strong" || !this.last_log) {
			this.clear();
			return;
		}

		frappe.prompt(
			[
				{
					fieldname: "override_reason",
					fieldtype: "Select",
					label: __("Why is this one different?"),
					options: item_duplicate_check.OVERRIDE_REASONS.join("\n"),
					reqd: 0,
					description: __("Optional. This is how the safety rules get better."),
				},
			],
			(values) => {
				self.record("created anyway", values.override_reason);
				self.clear();
			},
			__("Save anyway"),
			__("Continue")
		);
	}

	/** Send what the user did back to the log row that warned them. */
	record(user_action, override_reason) {
		if (!this.last_log) {
			return;
		}
		frappe
			.call({
				method: "item_duplicate_check.api.record_action",
				args: {
					log_name: this.last_log,
					user_action: user_action,
					override_reason: override_reason || null,
				},
				freeze: false,
				silent: true,
			})
			.catch(() => {
				// Losing one judgement is acceptable. Interrupting the user
				// to tell them so is not.
			});
	}
};

/**
 * Bind the panel to one doctype, using that doctype's own field names.
 *
 * `before_save` IS in this list, and it is the only handler the save waits
 * on. It is still not a block -- see confirm_before_save() for exactly why.
 * What is NOT here: `validate`, which is the hook that can refuse a save
 * outright, and `on_submit`.
 */
item_duplicate_check.bind = function (doctype) {
	const map = item_duplicate_check.FORMS[doctype];
	if (!map) {
		return;
	}

	// BIND ONCE, however many times this file arrives.
	//
	// It now arrives twice on an Item form: doctype_js injects it into the
	// form script, and app_include_js loads it on every desk page -- which
	// is the only route that reaches Item Creation Form, because that
	// doctype is custom and Frappe drops doctype_js for custom doctypes.
	// See the note in hooks.py.
	//
	// frappe.ui.form.on appends to a list, so a second registration would
	// run before_save twice and put two dialogs on screen -- the exact
	// 'asked twice' failure confirm_before_save() is built to avoid.
	if (!item_duplicate_check.bound) {
		item_duplicate_check.bound = {};
	}
	if (item_duplicate_check.bound[doctype]) {
		return;
	}
	item_duplicate_check.bound[doctype] = true;

	const handlers = {
		setup: function (frm) {
			frm.idc_panel = new item_duplicate_check.Panel(frm);
		},

		refresh: function (frm) {
			if (!frm.idc_panel) {
				frm.idc_panel = new item_duplicate_check.Panel(frm);
			}

			// The live check is for items being created. Running it on all
			// 22,204 existing rows on every form open would be noise: those
			// are the catalogue the check compares against.
			if (!frm.is_new()) {
				frm.idc_panel.clear();
				return;
			}

			item_duplicate_check.get_status().then((status) => {
				if (!status.enabled) {
					return;
				}
				// Something may already be typed -- a duplicated document, or
				// a value carried in from a link.
				frm.idc_panel.schedule();
			});
		},

		/**
		 * The one warning, before the save goes through.
		 *
		 * This is the ONLY handler in this app that the save waits on, and
		 * it is still not a block: "Save anyway" is one click, the warning
		 * is shown once per form, any failure or a slow server lets the save
		 * through, and `frappe.validated = false` is Frappe's own clean
		 * pause -- it re-enables the Save button and raises nothing.
		 *
		 * There is still no `validate` handler and no frappe.throw anywhere
		 * in this app.
		 */
		before_save: function (frm) {
			if (!frm.idc_panel) {
				return;
			}
			return item_duplicate_check.get_status().then((status) => {
				if (!status.enabled) {
					return;
				}
				return frm.idc_panel.confirm_before_save();
			});
		},

		after_save: function (frm) {
			// The row exists now, so the warning is history. If a strong
			// warning was on screen and the user saved past it without
			// pressing either button, that is still an override and worth
			// recording.
			if (frm.idc_panel && frm.idc_panel.last_band === "strong") {
				frm.idc_panel.record("created anyway");
			}
			if (frm.idc_panel) {
				frm.idc_panel.clear();
			}
		},
	};

	// One handler per watched field, under this doctype's own fieldname, so
	// nothing else on the form triggers a request.
	["item_name", "cat_no", "makes"].forEach((key) => {
		const fieldname = map[key];
		if (fieldname && !handlers[fieldname]) {
			handlers[fieldname] = function (frm) {
				item_duplicate_check.on_watched_change(frm);
			};
		}
	});

	// Maintain Stock. Touching it at all is what turns the field from a
	// default into a decision, so the flag is set here and nowhere else.
	// Unticking it then takes the item out of scope entirely.
	if (map.maintain_stock && !handlers[map.maintain_stock]) {
		handlers[map.maintain_stock] = function (frm) {
			if (frm.idc_panel) {
				frm.idc_panel.stock_touched = true;
			}
			if (!cint(frm.doc[map.maintain_stock])) {
				if (frm.idc_panel) {
					frm.idc_panel.clear();
				}
				return;
			}
			item_duplicate_check.on_watched_change(frm);
		};
	}

	frappe.ui.form.on(doctype, handlers);
};

item_duplicate_check.on_watched_change = function (frm) {
	if (!frm.is_new() || !frm.idc_panel) {
		return;
	}
	item_duplicate_check.get_status().then((status) => {
		if (status.enabled) {
			frm.idc_panel.schedule();
		}
	});
};

// This file is loaded by doctype_js for each form it is registered against,
// so binding every known doctype here is harmless and keeps the two in step.
Object.keys(item_duplicate_check.FORMS).forEach(item_duplicate_check.bind);
