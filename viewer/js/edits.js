/* =============================================================================
   Handbook viewer: edits.js, the owner's edits and rule decisions.
   Loads after components.js and before the views and app.js. It talks to the
   Artifact database (claude.use("db") and claude.use("user")), which answers
   later than first paint and may not answer at all (the local preview, a view
   with no access): then HB.edits.ready stays false and every function that
   writes refuses with a plain sentence.

   HB.edits = {
     ready                 false until both capabilities answered
     pending()             number of saved edits and decisions whose id is not in
                           bundle.applied_edits (Claude has not applied them yet)
     pendingCounts()       the same split by kind: {edits, decisions}
     pendingDecision(rule) the latest saved decision for a rule id that is not applied
                           yet, as {id, rule, option, text, by, at}, or null
     canWrite()            true, false or null (null: the page was not told)
     decide(rule, option, text)       Promise of the saved decision doc; option is an
                                      option id or null, text the owner's own wording
     skip(rule)            Promise of a saved skip ({rule, option: null, text: null,
                           skip: true}): apply-edits marks an open rule skipped, so
                           Claude's reading stays in use; a later decision still wins
     edit(target, id, field, value)   Promise of the saved edit doc (shapes: "Viewer edits")
     confirm(what)         "questions" | "units": opens a confirmation dialog in the
                           page; Promise of true once saved, false when cancelled
     editButton(target, id, field)    a small "Edit" button that opens that field's
                                      dialog, or null while not ready
     save(collection, body)           Promise of the saved doc in another collection
                                      (the run bar's `control`), with id, by and at
     follow(collection, onDocs)       onDocs([docs]) on every change; false without a store
     dialog(spec)          opens the edit dialog for another form (a step brief's Send):
                           spec {kicker, title, intro, body, focus, saveLabel, done,
                           submit()}; Promise of true once saved
     field(id, label, control, hint), textControl(id, value, multiline)
                           the dialog's form parts
   }
   Docs go to collections `edits` and `decisions`, one doc each, id from
   crypto.randomUUID(), with by (user id) and at (ISO time). The page subscribes
   once to each collection and calls HB.refreshBanner() when the pending count
   changes. Dialogs are <dialog> elements built here (alert, confirm and prompt
   are blocked in the Artifact frame); every control has an id.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;

  const EDIT_FIELDS = {
    question: ["wording", "short", "rule", "applies_to", "levels_points"],
    unit: ["name", "labels"],
    confirm: ["questions", "units"],
  };
  const FIELD_WORDS = {
    wording: "wording", short: "short name", rule: "coding rule", applies_to: "applies-to setting",
    levels_points: "points per level", name: "name", labels: "labels",
  };
  const NOT_READY = () => HB.cantSave();

  const CSS = `
.hb-dialog {
  width: min(36rem, calc(100vw - 2 * var(--gutter)));
  max-height: calc(100dvh - 2 * var(--s4));
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 1.25rem 3rem var(--shadow);
}
.hb-dialog[open] { display: grid; }
.hb-dialog::backdrop { background: var(--scrim); }
.hb-dialog__form { display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto; min-height: 0; max-height: calc(100dvh - 2 * var(--s4)); }
.hb-dialog__head { display: grid; gap: var(--s1); padding: var(--s4) var(--s4) var(--s3); border-bottom: 1px solid var(--rule); }
.hb-dialog__body { display: grid; gap: var(--s4); align-content: start; min-height: 0; overflow-y: auto; padding: var(--s4); }
.hb-dialog__fields { display: grid; gap: var(--s4); min-width: 0; margin: 0; padding: 0; border: 0; }
.hb-dialog__status:empty { display: none; }
.hb-dialog__status { padding: 0 var(--s4) var(--s3); }
.hb-dialog__foot { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--s2); padding: var(--s3) var(--s4); border-top: 1px solid var(--rule); background: var(--paper); }
.hb-hint { color: var(--ink-2); font-size: var(--fs-14); }
.hb-levels { display: grid; gap: var(--s3); }
.hb-levels .input { max-width: 8rem; }
.hb-pick-set { display: grid; gap: var(--s3); }
.hb-pick { display: flex; flex-wrap: wrap; gap: var(--s2) var(--s4); min-width: 0; margin: 0; padding: var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); }
.hb-pick legend { padding-inline: var(--s1); font-size: var(--fs-14); font-weight: 600; }
.hb-check, .hb-radio { display: inline-flex; align-items: center; gap: var(--s2); min-height: 2rem; font-size: var(--fs-14); }
.hb-check input, .hb-radio input { width: 1.125rem; height: 1.125rem; accent-color: var(--accent); }
.hb-radios { display: grid; gap: var(--s1); }
.hb-edit-btn { margin-inline-start: var(--s2); }
@media (pointer: coarse) { .hb-check, .hb-radio { min-height: 2.75rem; } }
`;
  HB.addStyles("edits", CSS);

  /* ---- What the page knows about saved edits ---------------------------------- */

  const remote = { edits: new Map(), decisions: new Map() };   // as the store last said
  const own = { edits: new Map(), decisions: new Map() };      // saved here, not yet seen back
  let db = null;
  let user = null;
  let writeRight = null;
  let lastCount = 0;

  function appliedIds() {
    const b = HB.data && HB.data.bundle();
    return new Set(b && Array.isArray(b.applied_edits) ? b.applied_edits : []);
  }

  function waiting(name) {
    const done = appliedIds();
    const all = new Map(remote[name]);
    own[name].forEach((doc, id) => { if (!all.has(id)) all.set(id, doc); });
    return Array.from(all.values()).filter((d) => !done.has(d.id));
  }

  function pending() {
    return waiting("edits").length + waiting("decisions").length;
  }

  function pendingCounts() {
    return { edits: waiting("edits").length, decisions: waiting("decisions").length };
  }

  function pendingDecision(rule) {
    const mine = waiting("decisions").filter((d) => d.rule === rule);
    mine.sort((a, b) => String(b.at).localeCompare(String(a.at)));
    return mine[0] || null;
  }

  function changed() {
    const n = pending();
    if (n === lastCount) return;
    lastCount = n;
    if (typeof HB.refreshBanner === "function") HB.refreshBanner();
  }

  function subscribe(name) {
    try {
      db.collection(name).onSnapshot((snap) => {
        const next = new Map();
        snap.docs.forEach((d) => next.set(d.id, Object.assign({}, d.data() || {}, { id: d.id })));
        own[name].forEach((doc, id) => {
          if (next.has(id) || remote[name].has(id)) own[name].delete(id);   // seen back, or removed since
        });
        remote[name] = next;
        changed();
      }, (err) => console.warn("HB.edits: could not follow " + name + ":", err && err.code ? err.code : err));
    } catch (err) {
      console.warn("HB.edits: could not follow " + name + ":", err);
    }
  }

  /* ---- Writing ------------------------------------------------------------------ */

  const SAYS = {
    invalid_argument: "The page refused this save. You can probably read this page but not change it. Ask its owner for edit access.",
    quota_exceeded: "The page's store is full, so this could not be saved.",
    resource_exhausted: "Too many saves in a short time. Wait a moment and try again.",
    unavailable: "The page's store did not answer. Try again in a moment.",
    revoked: "This page has lost its link to the store, for example because its sharing changed. Reopen the page and try again.",
  };

  function problem(err) {
    const code = err && err.code ? String(err.code) : "unknown";
    const e = new Error("Not saved. " + (SAYS[code] || "The save failed (" + code + "). Try again in a moment."));
    e.code = code;
    return e;
  }

  function newId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return "e-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function write(name, body) {
    if (!api.ready) throw new Error(NOT_READY());
    let by = null;
    try {
      by = await user.id();
    } catch (err) {
      by = null;
    }
    const id = newId();
    const doc = Object.assign({ id }, body, { by, at: new Date().toISOString() });
    const ref = db.collection(name).doc(id);
    try {
      await ref.set(doc);
    } catch (first) {
      if (!(first && first.code === "unavailable")) throw problem(first);
      await wait(300 + Math.random() * 400);
      try {
        await ref.set(doc);
      } catch (second) {
        throw problem(second);
      }
    }
    if (own[name]) own[name].set(id, doc);
    changed();
    return doc;
  }

  async function edit(target, id, field, value) {
    if (!api.ready) throw new Error(NOT_READY());
    if (!EDIT_FIELDS[target] || !EDIT_FIELDS[target].includes(field)) {
      throw new Error("A " + target + " has no field called " + field + " that can be edited here.");
    }
    if (id == null || String(id) === "") throw new Error("Say which " + target + " this edit is for.");
    return write("edits", { target, target_id: String(id), field, value });
  }

  async function decide(rule, option, text) {
    if (!api.ready) throw new Error(NOT_READY());
    const own_words = typeof text === "string" ? text.trim() : "";
    if (!option && !own_words) throw new Error("Choose an option or write the rule in your own words.");
    return write("decisions", { rule: String(rule), option: option || null, text: own_words || null, ...seenRule(rule) });
  }

  // What the page showed of the rule, so a colleague's newer decision is not overwritten unseen (team.py).
  function seenRule(rule) {
    const b = HB.data && HB.data.bundle();
    const rev = b && b.rule_revs ? b.rule_revs[String(rule)] : null;
    return rev ? { seen: rev } : {};
  }

  async function skip(rule) {
    if (!api.ready) throw new Error(NOT_READY());
    return write("decisions", { rule: String(rule), option: null, text: null, skip: true, ...seenRule(rule) });
  }

  /* Other collections (the run bar's `control` requests) use the same save, and can be
     followed: onDocs([doc, ...]) runs on every change. follow() returns false when the
     page has no store. */
  function save(name, body) {
    return write(String(name), body || {});
  }

  function follow(name, onDocs) {
    if (!db) return false;
    try {
      db.collection(String(name)).onSnapshot((snap) => {
        onDocs(snap.docs.map((d) => Object.assign({}, d.data() || {}, { id: d.id })));
      }, (err) => console.warn("HB.edits: could not follow " + name + ":", err && err.code ? err.code : err));
      return true;
    } catch (err) {
      console.warn("HB.edits: could not follow " + name + ":", err);
      return false;
    }
  }

  /* ---- Dialog -------------------------------------------------------------------- */

  let dialog = null;

  function dialogEl() {
    if (dialog && dialog.isConnected) return dialog;
    dialog = h("dialog", { class: "hb-dialog", id: "hb-edit-dialog", "aria-labelledby": "hb-edit-title" });
    dialog.addEventListener("click", (ev) => { if (ev.target === dialog) dialog.close(); });
    (document.querySelector(".hb") || document.body).append(dialog);
    return dialog;
  }

  /* spec: {kicker, title, intro, body (Node), focus (Node), saveLabel, done, submit()}
     submit() saves or throws an Error whose message is shown. Resolves true when saved. */
  function openDialog(spec) {
    return new Promise((resolve) => {
      const d = dialogEl();
      let saved = false;
      const status = h("div", { id: "hb-edit-status", class: "hb-dialog__status", "aria-live": "polite" });
      const save = h("button", { type: "submit", id: "hb-edit-save", class: "btn btn--primary" }, spec.saveLabel || "Save edit");
      const cancel = h("button", { type: "button", id: "hb-edit-cancel", class: "btn btn--quiet", onclick: () => d.close() }, "Cancel");
      const fields = h("fieldset", { class: "hb-dialog__fields", id: "hb-edit-fields" },
        writeRight === false
          ? HB.notice("warning", "You have view-only access to this page, so a save will probably be refused.")
          : null,
        spec.body);
      const form = h("form", {
        id: "hb-edit-form",
        novalidate: true,
        onsubmit: async (ev) => {
          ev.preventDefault();
          status.replaceChildren();
          save.disabled = true;
          try {
            await spec.submit();
          } catch (err) {
            save.disabled = false;
            status.replaceChildren(HB.notice("critical", err && err.message ? err.message : "This could not be saved."));
            return;
          }
          saved = true;
          fields.disabled = true;
          save.hidden = true;
          cancel.textContent = "Close";
          status.replaceChildren(HB.notice("good", spec.done || HB.when("Saved and applied.", "Saved. Claude applies it the next time it works on this project.")));
          cancel.focus();
        },
      },
      h("div", { class: "hb-dialog__head" },
        spec.kicker ? h("p", { class: "eyebrow" }, spec.kicker) : null,
        h("h2", { id: "hb-edit-title" }, spec.title),
        spec.intro ? h("p", { class: "hb-hint" }, spec.intro) : null),
      h("div", { class: "hb-dialog__body" }, fields),
      status,
      h("div", { class: "hb-dialog__foot" }, cancel, save));
      form.classList.add("hb-dialog__form");
      d.replaceChildren(form);
      d.addEventListener("close", () => resolve(saved), { once: true });
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
      if (spec.focus && typeof spec.focus.focus === "function") spec.focus.focus();
    });
  }

  /* ---- Form parts ---------------------------------------------------------------- */

  const nice = (s) => {
    const t = String(s).replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  };

  function field(id, label, control, hint) {
    if (hint) control.setAttribute("aria-describedby", id + "-hint");
    return h("div", { class: "field" },
      h("label", { class: "field__label", for: id }, label),
      control,
      hint ? h("p", { class: "hb-hint", id: id + "-hint" }, hint) : null);
  }

  function textControl(id, value, multiline) {
    const el = h(multiline ? "textarea" : "input", Object.assign({ class: "input", id }, multiline ? { rows: 6 } : { type: "text" }));
    el.value = value == null ? "" : String(value);
    return el;
  }

  function tick(id, name, type, value, on, text) {
    const input = h("input", { type, id, name, value });
    input.checked = Boolean(on);
    return { input, node: h("label", { class: type === "radio" ? "hb-radio" : "hb-check", for: id }, input, h("span", null, text)) };
  }

  const norm = (v) => (v == null ? "null" : JSON.stringify(Object.keys(v).sort().map((k) => [k, v[k].slice().sort()])));

  /* ---- The dialogs ----------------------------------------------------------------- */

  function specFor(target, id, fieldName) {
    const b = HB.data.bundle() || {};
    const project = b.project || {};
    const labels = Array.isArray(project.labels) ? project.labels : [];
    const save = (value) => edit(target, id, fieldName, value);

    if (target === "question") {
      const q = HB.data.question(id);
      if (!q) return null;
      const kicker = "Question " + id;
      if (fieldName === "wording" || fieldName === "short" || fieldName === "rule") {
        const long = fieldName !== "short";
        const control = textControl("hb-edit-value", q[fieldName], long);
        const hint = fieldName === "short"
          ? "The label shown in the grid and in tables."
          : "Saving this marks every answer to the question as out of date, so Claude re-checks it.";
        return {
          kicker, title: "Edit the " + FIELD_WORDS[fieldName], saveLabel: "Save edit", focus: control,
          body: field("hb-edit-value", nice(FIELD_WORDS[fieldName]), control, hint),
          submit: async () => {
            const v = control.value.trim();
            if (!v) throw new Error("Write something before saving.");
            if (v === String(q[fieldName] == null ? "" : q[fieldName]).trim()) throw new Error("Nothing has changed.");
            await save(v);
          },
        };
      }
      if (fieldName === "levels_points") {
        const levels = (q.answer && q.answer.levels) || [];
        const inputs = levels.map((lv, i) => {
          const el = h("input", { class: "input", id: "hb-edit-points-" + i, type: "number", step: "any", inputmode: "decimal" });
          el.value = lv.points == null ? "" : String(lv.points);
          return el;
        });
        return {
          kicker, title: "Edit the points per level", saveLabel: "Save edit", focus: inputs[0],
          intro: "Scores are worked out again from the points. Answers are not read again.",
          body: h("div", { class: "hb-levels" }, levels.map((lv, i) => h("div", { class: "field" },
            h("label", { class: "field__label", for: "hb-edit-points-" + i },
              "Level ", h("span", { class: "mono" }, String(lv.value)), lv.meaning ? ": " + lv.meaning : ""),
            inputs[i]))),
          submit: async () => {
            const value = {};
            levels.forEach((lv, i) => {
              const raw = inputs[i].value.trim();
              const n = Number(raw);
              if (raw === "" || !Number.isFinite(n)) throw new Error("Points for level " + lv.value + " must be a number.");
              if (n !== lv.points) value[String(lv.value)] = n;
            });
            if (!Object.keys(value).length) throw new Error("Nothing has changed.");
            await save(value);
          },
        };
      }
      if (fieldName === "applies_to") {
        const current = q.applies_to && typeof q.applies_to === "object" ? q.applies_to : null;
        const all = tick("hb-edit-applies-all", "hb-edit-applies", "radio", "all", !current, "Every row");
        const some = tick("hb-edit-applies-some", "hb-edit-applies", "radio", "some", Boolean(current), "Only rows with certain labels");
        const groups = labels.map((label, i) => {
          const values = HB.data.labelValues(label).slice();
          ((current && current[label]) || []).forEach((v) => { if (!values.includes(v)) values.push(v); });
          const boxes = values.map((v, j) => tick("hb-edit-applies-" + i + "-" + j, "hb-edit-applies-" + i, "checkbox", v,
            current && (current[label] || []).includes(v), v));
          return { label, boxes, node: h("fieldset", { class: "hb-pick", id: "hb-edit-applies-set-" + i }, h("legend", null, nice(label)), boxes.map((x) => x.node)) };
        });
        const pick = h("div", { class: "hb-pick-set", id: "hb-edit-applies-pick" }, groups.map((g) => g.node));
        pick.hidden = !current;
        const sync = () => { pick.hidden = !some.input.checked; };
        all.input.addEventListener("change", sync);
        some.input.addEventListener("change", sync);
        return {
          kicker, title: "Edit who the question applies to", saveLabel: "Save edit", focus: all.input,
          intro: "Rows that stop being covered are set aside. Rows that start being covered need scoring.",
          body: h("div", { class: "stack" }, h("div", { class: "hb-radios" }, all.node, labels.length ? some.node : null), pick),
          submit: async () => {
            let value = null;
            if (some.input.checked) {
              value = {};
              groups.forEach((g) => {
                const ticked = g.boxes.filter((x) => x.input.checked).map((x) => x.input.value);
                if (ticked.length) value[g.label] = ticked;
              });
              if (!Object.keys(value).length) throw new Error("Tick at least one value, or choose every row.");
            }
            if (norm(value) === norm(current)) throw new Error("Nothing has changed.");
            await save(value);
          },
        };
      }
    }

    if (target === "unit") {
      const u = HB.data.unit(id);
      if (!u) return null;
      const word = nice(project.unit_word || "row");
      const kicker = word + " " + id;
      if (fieldName === "name") {
        const control = textControl("hb-edit-value", u.name, false);
        return {
          kicker, title: "Edit the name", saveLabel: "Save edit", focus: control,
          body: field("hb-edit-value", "Name", control),
          submit: async () => {
            const v = control.value.trim();
            if (!v) throw new Error("Write a name before saving.");
            if (v === String(u.name || "").trim()) throw new Error("Nothing has changed.");
            await save(v);
          },
        };
      }
      if (fieldName === "labels") {
        const have = u.labels || {};
        const controls = labels.map((label, i) => {
          const el = h("input", { class: "input", id: "hb-edit-label-" + i, type: "text", list: "hb-edit-values-" + i });
          el.value = have[label] == null ? "" : String(have[label]);
          const list = h("datalist", { id: "hb-edit-values-" + i }, HB.data.labelValues(label).map((v) => h("option", { value: v })));
          return { label, el, node: field("hb-edit-label-" + i, nice(label), el), list };
        });
        return {
          kicker, title: "Edit the labels", saveLabel: "Save edit", focus: controls[0] && controls[0].el,
          intro: "Pick a value already in use or type a new one, which starts a new group. Labels you leave alone stay as they are.",
          body: h("div", { class: "stack" }, controls.map((c) => [c.node, c.list])),
          submit: async () => {
            const value = {};
            controls.forEach((c) => {
              const v = c.el.value.trim();
              if (v && v !== String(have[c.label] == null ? "" : have[c.label])) value[c.label] = v;
            });
            if (!Object.keys(value).length) throw new Error("Nothing has changed.");
            await save(value);
          },
        };
      }
    }
    return null;
  }

  function editButton(target, id, fieldName) {
    if (!api.ready) return null;
    if (!EDIT_FIELDS[target] || target === "confirm" || !EDIT_FIELDS[target].includes(fieldName)) return null;
    if (!specFor(target, id, fieldName)) return null;
    const what = target === "question" ? "question " + id : (HB.data.unit(id) || {}).name || id;
    return h("button", {
      type: "button",
      class: "btn btn--quiet btn--small hb-edit-btn",
      id: "hb-edit-" + target + "-" + String(id).replace(/[^A-Za-z0-9_-]/g, "_") + "-" + fieldName,
      "aria-label": "Edit the " + FIELD_WORDS[fieldName] + " of " + what,
      onclick: () => {
        const spec = specFor(target, id, fieldName);
        if (spec) openDialog(spec);
      },
    }, "Edit");
  }

  function confirm(what) {
    if (!api.ready) return Promise.reject(new Error(NOT_READY()));
    if (what !== "questions" && what !== "units") return Promise.reject(new Error("Only the questions or the rows can be confirmed."));
    const b = HB.data.bundle() || {};
    const items = (what === "questions" ? b.questions : b.units) || [];
    const draft = items.filter((x) => x && x.status === "draft").length;
    const p = b.project || {};
    const noun = what === "questions" ? "questions" : (p.unit_word_plural || (p.unit_word || "row") + "s");
    return openDialog({
      kicker: "Confirm", title: "Confirm all " + noun, saveLabel: "Confirm all " + noun,
      intro: "This tells Claude you have read all " + fmtInt(items.length) + " " + noun + " and they are right as they stand"
        + (draft ? " (" + fmtInt(draft) + " " + (draft === 1 ? "is" : "are") + " still marked draft)" : "") + HB.when(". It is recorded as soon as you confirm.", ". Claude records it the next time it works on this project."),
      body: h("p", { class: "hb-hint", id: "hb-edit-confirm-note" }, "Edit anything that is wrong first; you can still change it afterwards."),
      submit: async () => { await edit("confirm", what, what, true); },
      done: HB.when("Saved. They are marked confirmed.", "Saved. Claude marks them confirmed the next time it works on this project."),
    });
  }

  /* ---- Start ----------------------------------------------------------------------- */

  const api = {
    ready: false,
    pending,
    pendingCounts,
    pendingDecision,
    canWrite: () => writeRight,
    decide,
    skip,
    edit,
    confirm,
    editButton,
    save,
    follow,
    dialog: openDialog,
    field,
    textControl,
  };
  HB.edits = api;

  async function start() {
    const c = typeof window !== "undefined" ? window.claude : null;
    if (!c || typeof c.use !== "function") return;
    let d = null;
    let u = null;
    try {
      [d, u] = await Promise.all([c.use("db"), c.use("user")]);
    } catch (err) {
      return;
    }
    if (!d || !u) return;
    db = d;
    user = u;
    try {
      writeRight = await u.can("data.write");
    } catch (err) {
      writeRight = null;
    }
    subscribe("edits");
    subscribe("decisions");
    api.ready = true;
    if (typeof HB.render === "function") HB.render();
  }

  start();
})((window.HB = window.HB || {}));
