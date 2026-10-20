/* =============================================================================
   Handbook viewer: views/bulk.js, "Add documents or instructions" (the local page
   only). Registers HB.bulk; the Grid's toolbar shows HB.bulk.button().

   One dialog in four steps:
     Drop      the owner drops, in any mix: a list of documents (an Excel naming
               each document's row, title, file name or web address, date in
               force and type), the documents or a zip of them, and an Excel of
               extra coding instructions by question number. Two CSV templates
               show the headings. "Read them" uploads the files (assets) and asks
               the page's server for a plan (POST /api/bulk/read, bulk.py): one
               small Claude call matches what the list could not and suggests,
               for each document, the questions it could change.
     Confirm   one table per kind. A document: its rows (chips; a select adds
               one), title, date, type, and the questions to mark out of date
               (chips, ticked by Claude's suggestion; a select adds one), with
               what was guessed and what is wrong said beside it. An instruction:
               its question's coding instructions before and after (the added
               words marked) and how many scored answers it touches. Nothing is
               filed until Confirm, which saves {plan, documents, instructions}
               to the "bulk" collection (serve.py's _take_bulk runs hb bulk apply,
               then hb prepare).
     Filing    the dialog reads the plan (GET /api/bulk/<id>) every two seconds
               until Confirm's work is done.
     Done      what was filed, what was not and why, and the answers now out of
               date, with an offer to score them again: one re-run per set of
               rows with the same questions (control {action: "rerun"}), its
               token range shown first. "Not now" leaves them marked on the Grid.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;
  const POLL_MS = 2000;
  const POLL_LIMIT = 900;     // half an hour of polls: prepare can take a while on many documents

  const STYLE = `
.hbb.hb-dialog { width: min(76rem, calc(100vw - 2rem)); max-width: none; }
.hbb .hb-dialog__body { display: grid; gap: var(--s4); }
.hbb-kinds { margin: 0; padding-left: 1.25rem; display: grid; gap: 2px; }
.hbb-drop { display: grid; gap: var(--s2); justify-items: start; padding: var(--s5); border: 2px dashed var(--rule-strong); border-radius: var(--radius); background: var(--paper); }
.hbb-drop.is-over { border-color: var(--accent); background: var(--accent-soft); }
.hbb-drop p { margin: 0; max-width: var(--measure); }
.hbb-files { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s2); margin: 0; padding: 0; list-style: none; }
.hbb-chip { display: inline-flex; align-items: center; gap: 0.25rem; max-width: 100%; padding: 0.125rem 0.25rem 0.125rem 0.5rem; border: 1px solid var(--rule-strong); border-radius: 999px; background: var(--surface); font-size: var(--fs-12); line-height: 1.4; }
.hbb-chip > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hbb-chip button { display: inline-grid; place-items: center; width: 1.25rem; height: 1.25rem; padding: 0; border: 0; border-radius: 50%; background: none; color: var(--ink-2); font: inherit; cursor: pointer; }
.hbb-chip button:hover { background: var(--rule); color: var(--ink); }
.hbb-chip.is-guess { border-style: dashed; }
.hbb-templates { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4); font-size: var(--fs-14); }
.hbb-sum { margin: 0; font-size: var(--fs-16); font-weight: 600; }
.hbb-scroll { overflow: auto; max-height: calc(100dvh - 22rem); border: 1px solid var(--rule); border-radius: var(--radius); }
.hbb-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: var(--fs-14); }
.hbb-table th, .hbb-table td { padding: var(--s2); border-bottom: 1px solid var(--rule); background: var(--surface); text-align: left; vertical-align: top; }
.hbb-table thead th { position: sticky; top: 0; z-index: 1; color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; white-space: nowrap; }
.hbb-table tr.is-off td { color: var(--ink-2); }
.hbb-table tr.is-off td > :not(.hbb-keep) { opacity: 0.55; }
.hbb-table .input { min-height: 2rem; padding-block: 0.125rem; font-size: var(--fs-14); }
.hbb-table select.input { width: auto; max-width: 14rem; }
.hbb-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-bottom: 0.25rem; }
.hbb-sub { display: block; margin-top: 0.25rem; color: var(--ink-2); font-size: var(--fs-12); overflow-wrap: anywhere; }
.hbb-warn { display: block; margin-top: 0.25rem; color: var(--critical); font-size: var(--fs-12); }
.hbb-why { display: block; margin-top: 0.25rem; color: var(--ink-2); font-size: var(--fs-12); font-style: italic; }
.hbb-text { max-width: 34rem; white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--fs-12); line-height: 1.45; }
.hbb-text mark { background: var(--accent-soft); color: inherit; }
.hbb-col-q { min-width: 14rem; }
.hbb-col-row { min-width: 10rem; }
.hbb-col-doc { min-width: 12rem; }
.hbb-col-doc .input, .hbb-col-type .input { width: 100%; }
.hbb-col-type { min-width: 7.5rem; }
.hbb-col-date .input { width: 9.5rem; }
.hbb-col-notes { min-width: 10rem; max-width: 18rem; }
.hbb-done { display: grid; gap: var(--s2); }
.hbb-done ul { margin: 0; padding-left: 1.25rem; }
.hbb-offer { display: grid; gap: var(--s2); padding: var(--s4); border: 1px solid var(--accent); border-radius: var(--radius); background: var(--accent-soft); }
.hbb-offer p { margin: 0; }
.hbb-offer__btns { display: flex; flex-wrap: wrap; gap: var(--s2); }
.hbb .hb-dialog__head { position: relative; padding-right: 3.5rem; }
.hbb-x { position: absolute; top: var(--s3); right: var(--s3); display: grid; place-items: center; width: 2.25rem; height: 2.25rem; padding: 0; border: 1px solid transparent; border-radius: 50%; background: none; color: var(--ink-2); font-size: 1.375rem; line-height: 1; cursor: pointer; }
.hbb-x:hover { border-color: var(--rule-strong); color: var(--ink); }
.hbb-x:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.hbb-wait { display: grid; gap: var(--s2); }
.hbb-wait p { margin: 0; }
.hbb-bar { position: relative; height: 6px; overflow: hidden; border-radius: 3px; background: var(--rule); }
.hbb-bar > span { position: absolute; top: 0; bottom: 0; left: 0; border-radius: 3px; background: var(--accent); }
.hbb-bar.is-known > span { transition: width var(--dur) var(--ease); }
.hbb-bar:not(.is-known) > span { width: 35%; animation: hbb-slide 1.4s ease-in-out infinite; }
@keyframes hbb-slide { 0% { left: -35%; } 100% { left: 100%; } }
@media (prefers-reduced-motion: reduce) { .hbb-bar:not(.is-known) > span { width: 100%; animation: none; opacity: 0.5; } }
.hbb-clock { color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
@media print { .hbb { display: none !important; } }
`;

  const isLocal = () => Boolean(HB.local && typeof HB.local.post === "function");
  const plural = (n, one, many) => fmtInt(n) + " " + (n === 1 ? one : many || one + "s");
  const extOf = (name) => (String(name).toLowerCase().match(/\.([a-z0-9]+)$/) || [null, ""])[1];
  const ACCEPT = (HB.uploadAccept || ".pdf,.xlsx,.csv,.docx,.html,.txt") + ",.zip";
  const qKey = (q) => String(q).split(/(\d+)/).map((x) => (/^\d+$/.test(x) ? x.padStart(6, "0") : x)).join("");

  let ui = null;     // the open dialog's state

  function words() {
    const b = HB.data.bundle() || {};
    const p = b.project || {};
    const one = p.unit_word || "row";
    return { one, many: p.unit_word_plural || one + "s" };
  }

  function unitsById() {
    const out = new Map();
    for (const u of (HB.data.bundle() || {}).units || []) out.set(u.id, u);
    return out;
  }

  function questionsById() {
    const out = new Map();
    for (const q of (HB.data.bundle() || {}).questions || []) out.set(String(q.id), q);
    return out;
  }

  /* ---- Templates: the headings a list needs, as CSV (Excel opens them) ------------------------ */

  function csvLink(name, rows, text) {
    const csv = rows.map((r) => r.map((c) => (/[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c)).join(",")).join("\r\n");
    return h("a", { href: "data:text/csv;charset=utf-8," + encodeURIComponent("﻿" + csv), download: name }, text);
  }

  function templates() {
    const w = words();
    const one = w.one.charAt(0).toUpperCase() + w.one.slice(1);
    const someUnit = ((HB.data.bundle() || {}).units || [])[0];
    const q = ((HB.data.bundle() || {}).questions || [])[0];
    return h("p", { class: "hbb-templates" },
      csvLink("documents-list.csv", [[one, "Document title", "File name or link", "Date in force", "Type"],
        [someUnit ? someUnit.name : "", "Access to Information Act, 2023", "ati-act-2023.pdf", "2023-12-26", "law"]],
      "Template: a list of documents"),
      csvLink("extra-instructions.csv", [["Question", "Extra instruction"],
        [q ? String(q.id) : "1", "Write the extra instruction for this question here."]],
      "Template: extra instructions by question"));
  }

  /* ---- Step 1: drop ------------------------------------------------------------------------- */

  function dropStep() {
    const w = words();
    const input = h("input", { type: "file", multiple: true, accept: ACCEPT, class: "visually-hidden", id: "hbb-input",
      onchange: () => { add(input.files); input.value = ""; } });
    const zone = h("div", { class: "hbb-drop" },
      h("p", null, h("b", null, "Drop files here"), ", or ",
        h("button", { type: "button", class: "btn btn--small", onclick: () => input.click() }, "choose files"), "."),
      h("p", { class: "hb-hint" }, "You can drop any mix of:"),
      h("ul", { class: "hb-hint hbb-kinds" },
        h("li", null, "the documents themselves, or a zip of them"),
        h("li", null, "a list of documents: an Excel with one row each (its " + w.one + ", title, file name or web address, the date it is in force from, its type)"),
        h("li", null, "documents with no list: Claude reads the start of each and says whose it is"),
        h("li", null, "extra instructions: an Excel with one row per question number")),
      input);
    zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("is-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
    zone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      zone.classList.remove("is-over");
      add(ev.dataTransfer && ev.dataTransfer.files);
    });
    const list = h("ul", { class: "hbb-files", "aria-label": "Files to read" }, ui.files.map((f, i) => h("li", { class: "hbb-chip" },
      h("span", { title: f.name }, f.name),
      h("button", { type: "button", "aria-label": "Take " + f.name + " out", onclick: () => { ui.files.splice(i, 1); draw(); } }, "×"))));
    return [zone, ui.files.length ? list : null, templates(),
      h("p", { class: "hb-hint" }, "Nothing is filed until you have seen the table and confirmed it. Reading is one small Claude call on your plan.")];
  }

  function add(fileList) {
    const files = Array.from(fileList || []).filter((f) => f && f.name);
    for (const f of files) if (!ui.files.some((x) => x.name === f.name && x.size === f.size)) ui.files.push(f);
    ui.error = null;
    draw();
  }

  async function readThem() {
    if (!ui.files.length) {
      ui.error = "Drop a list, some documents or a zip first.";
      return draw();
    }
    const mine = ui;              // the owner may close the dialog while this runs: then its answer is dropped
    ui.step = "reading";
    ui.error = null;
    ui.started = Date.now();
    ui.stage = { text: "Sending your files", done: 0, total: ui.files.length };
    draw();
    try {
      const assets = await window.claude.use("assets");
      const sent = [];
      const failed = [];
      for (const file of mine.files) {
        if (ui !== mine) return;
        mine.stage = { text: "Sending " + file.name, done: sent.length + failed.length, total: mine.files.length };
        draw();
        const ext = extOf(file.name);
        const type = ext === "zip" ? "application/zip" : HB.uploadTypes[ext] || file.type || "application/octet-stream";
        try {
          const up = await assets.upload(file, { type });
          sent.push({ asset: up.id, name: file.name });
        } catch (e) {
          failed.push(file.name + ": " + (e && e.message ? e.message : "it could not be sent"));
        }
      }
      if (!sent.length) throw new Error(failed.join(" "));
      if (ui !== mine) return;
      mine.stage = { text: "Claude is reading the list and the start of each document", done: null, total: null };
      draw();
      const plan = await HB.local.post("bulk/read", { files: sent });
      if (ui !== mine) return;
      ui.plan = plan;
      ui.sendFailed = failed;
      ui.docs = plan.documents.map((d) => ({ n: d.n, include: d.include, rows: d.rows.slice(), title: d.title || "",
        date: d.date || "", type: d.type || "", questions: d.questions.slice() }));
      ui.ins = plan.instructions.map((e) => ({ qid: e.qid, include: Boolean(e.include) }));
      ui.step = "confirm";
    } catch (e) {
      if (ui !== mine) return;
      ui.step = "drop";
      ui.error = e && e.message ? e.message : "What you sent could not be read.";
    }
    draw();
  }

  /* ---- Waiting: a moving bar, what is happening, and the seconds so far ---------------------------- */

  function waiting(text, hint) {
    const st = ui.stage || {};
    const known = typeof st.done === "number" && st.total;
    const bar = h("div", { class: "hbb-bar" + (known ? " is-known" : ""), role: "progressbar", "aria-label": text,
      ...(known ? { "aria-valuemin": "0", "aria-valuemax": String(st.total), "aria-valuenow": String(st.done) } : {}) },
    h("span", { style: known ? { width: Math.max(4, Math.round(100 * st.done / st.total)) + "%" } : null }));
    return [h("div", { class: "hbb-wait" },
      h("p", { role: "status" }, h("b", null, st.text || text), known ? " (" + (st.done + 1) + " of " + st.total + ")" : ""),
      bar,
      h("p", { class: "hbb-clock", id: "hbb-clock" }, clock()),
      h("p", { class: "hb-hint" }, hint))];
  }

  function clock() {
    const s = ui && ui.started ? Math.round((Date.now() - ui.started) / 1000) : 0;
    return s < 60 ? s + " seconds so far" : Math.floor(s / 60) + " min " + (s % 60) + " s so far";
  }

  function tick() {
    const el = document.getElementById("hbb-clock");
    if (el && ui && (ui.step === "reading" || ui.step === "filing")) el.textContent = clock();
  }

  /* ---- Step 2: confirm ---------------------------------------------------------------------- */

  function chips(ids, nameOf, onRemove, guessed) {
    return h("div", { class: "hbb-chips" }, ids.map((id) => h("span", { class: "hbb-chip" + (guessed && guessed.has(id) ? " is-guess" : "") },
      h("span", { title: nameOf(id) }, nameOf(id)),
      h("button", { type: "button", "aria-label": "Take " + nameOf(id) + " off", onclick: () => onRemove(id) }, "×"))));
  }

  function adder(label, options, onPick) {
    const sel = h("select", { class: "input", "aria-label": label, onchange: () => { if (sel.value) onPick(sel.value); } },
      h("option", { value: "" }, label), options.map(([v, t]) => h("option", { value: v }, t)));
    return sel;
  }

  function docRow(d, edit, units, questions) {
    const w = words();
    const unitName = (id) => (units.get(id) && units.get(id).name) || id;
    const qName = (id) => id + (questions.get(id) && questions.get(id).short ? " " + questions.get(id).short : "");
    const guessed = new Set((d.loose || []).map((x) => x.row));
    const scored = d.scored || {};
    const touched = edit.rows.reduce((n, r) => n + edit.questions.filter((q) => (scored[r] || []).includes(q)).length, 0);
    const notScored = edit.rows.filter((r) => !(scored[r] || []).length);
    const notes = [];
    for (const x of d.loose || []) notes.push(h("span", { class: "hbb-sub" }, "“" + x.text + "” read as " + unitName(x.row)
      + (x.how === "Claude" ? " (Claude's reading)" : x.how === "close" ? " (a close spelling)" : "") + "."));
    if (d.rows_from === "Claude") notes.push(h("span", { class: "hbb-sub" }, "Not in a list: Claude placed it from its first page."));
    for (const x of d.notes || []) notes.push(h("span", { class: "hbb-sub" }, x));
    for (const p of d.problems || []) notes.push(h("span", { class: "hbb-warn" }, p));
    if (d.why) notes.push(h("span", { class: "hbb-why" }, "Claude: " + d.why));
    const fileLine = d.link ? h("a", { href: d.link, target: "_blank", rel: "noopener" }, d.link) : d.file_name || "";
    const canFile = Boolean(d.file || d.link);
    return h("tr", { class: edit.include ? null : "is-off" },
      h("td", { class: "hbb-keep" }, h("input", { type: "checkbox", checked: edit.include, disabled: !canFile,
        "aria-label": "File " + (edit.title || d.file_name || "this document"),
        onchange: (ev) => { edit.include = ev.target.checked; draw(); } })),
      h("td", { class: "hbb-col-row" },
        chips(edit.rows, unitName, (id) => { edit.rows = edit.rows.filter((x) => x !== id); draw(); }, guessed),
        adder(edit.rows.length ? "Add a " + w.one : "Pick a " + w.one,
          Array.from(units.values()).filter((u) => !edit.rows.includes(u.id)).map((u) => [u.id, u.name || u.id]),
          (id) => { edit.rows.push(id); if (canFile) edit.include = true; draw(); }),
        d.row_text ? h("span", { class: "hbb-sub" }, "The list says: " + d.row_text) : null),
      h("td", { class: "hbb-col-doc" },
        h("input", { class: "input", type: "text", value: edit.title, "aria-label": "Title", oninput: (ev) => { edit.title = ev.target.value; } }),
        h("span", { class: "hbb-sub" }, fileLine, d.source ? " · " + d.source + ", row " + d.line : "")),
      h("td", { class: "hbb-col-date" }, h("input", { class: "input", type: "date", value: edit.date, "aria-label": "Date in force",
        oninput: (ev) => { edit.date = ev.target.value; } }),
        d.date_from === "Claude" ? h("span", { class: "hbb-sub" }, "Claude's reading") : null),
      h("td", { class: "hbb-col-type" }, h("input", { class: "input", type: "text", value: edit.type, "aria-label": "Type",
        oninput: (ev) => { edit.type = ev.target.value; } })),
      h("td", { class: "hbb-col-q" },
        chips(edit.questions.slice().sort((a, b) => (qKey(a) < qKey(b) ? -1 : 1)), qName, (id) => { edit.questions = edit.questions.filter((x) => x !== id); draw(); }),
        adder(edit.questions.length ? "Add a question" : "Pick questions",
          Array.from(questions.values()).filter((q) => !edit.questions.includes(String(q.id))).map((q) => [String(q.id), qName(String(q.id))]),
          (id) => { edit.questions.push(id); draw(); }),
        h("span", { class: "hbb-sub" }, touched ? plural(touched, "scored answer") + " will be marked out of date."
          : edit.rows.length && notScored.length === edit.rows.length ? "Not scored yet: it is used when it is scored."
            : "No scored answer is marked out of date.")),
      h("td", { class: "hbb-col-notes" }, notes));
  }

  function marked(before, after) {
    if (after.startsWith(before)) return [before, h("mark", null, after.slice(before.length))];
    return [after];
  }

  function insRow(e, edit, questions) {
    const q = e.qid ? questions.get(e.qid) : null;
    return h("tr", { class: edit.include ? null : "is-off" },
      h("td", { class: "hbb-keep" }, h("input", { type: "checkbox", checked: edit.include, disabled: !e.qid || !e.after || e.after === e.before,
        "aria-label": "Add the instruction for question " + (e.qid || "?"), onchange: (ev) => { edit.include = ev.target.checked; draw(); } })),
      h("td", null, h("b", null, e.qid || e.question_text || "?"), q && q.short ? h("span", { class: "hbb-sub" }, q.short) : null),
      h("td", null, h("div", { class: "hbb-text" }, e.before || "(none)")),
      h("td", null, h("div", { class: "hbb-text" }, e.after ? marked(e.before || "", e.after) : e.lines.map((l) => l.text).join("\n"))),
      h("td", null, e.qid ? plural(e.touches || 0, "answer") : "",
        (e.problems || []).map((p) => h("span", { class: "hbb-warn" }, p))));
  }

  function confirmStep() {
    const plan = ui.plan;
    const units = unitsById();
    const questions = questionsById();
    const w = words();
    const out = [h("p", { class: "hbb-sum" }, plan.summary)];
    const probs = (plan.problems || []).concat(ui.sendFailed || []);
    if (probs.length) out.push(HB.notice("warning", h("ul", null, probs.map((p) => h("li", null, p)))));
    if (plan.documents.length) {
      out.push(h("h3", null, "Documents"),
        h("p", { class: "hb-hint" }, "Claude ticked the questions each document could change. Their scored answers are marked out of date "
          + "when you confirm, and you are offered to score just those again. Take off a question it cannot change, or add one."),
        h("div", { class: "hbb-scroll" }, h("table", { class: "hbb-table" },
          h("thead", null, h("tr", null, h("th", null, h("span", { class: "visually-hidden" }, "File it")),
            h("th", null, w.one.charAt(0).toUpperCase() + w.one.slice(1)), h("th", null, "Document"), h("th", null, "In force from"),
            h("th", null, "Type"), h("th", null, "Questions it could change"), h("th", null, "Notes"))),
          h("tbody", null, plan.documents.map((d, i) => docRow(d, ui.docs[i], units, questions))))));
    }
    if (plan.instructions.length) {
      out.push(h("h3", null, "Extra instructions"),
        h("p", { class: "hb-hint" }, "Each is added to the end of its question's coding instructions (marked). Nothing else in the set-up changes. "
          + "The question's scored answers are marked out of date."),
        h("div", { class: "hbb-scroll" }, h("table", { class: "hbb-table" },
          h("thead", null, h("tr", null, h("th", null, h("span", { class: "visually-hidden" }, "Add it")), h("th", null, "Question"),
            h("th", null, "Coding instructions now"), h("th", null, "With the extra instruction"), h("th", null, "Scored answers"))),
          h("tbody", null, plan.instructions.map((e, i) => insRow(e, ui.ins[i], questions))))));
    }
    return out;
  }

  function counts() {
    const docs = ui.docs.filter((d) => d.include && d.rows.length).length;
    const ins = ui.ins.filter((e) => e.include).length;
    return { docs, ins };
  }

  async function confirm() {
    const c = counts();
    if (!c.docs && !c.ins) {
      ui.error = "Tick at least one document or instruction.";
      return draw();
    }
    const missing = ui.docs.filter((d) => d.include && !d.rows.length).length;
    if (missing) {
      ui.error = "Pick a " + words().one + " for every ticked document, or untick it.";
      return draw();
    }
    ui.step = "filing";
    ui.error = null;
    ui.started = Date.now();
    ui.stage = { text: "Filing what you confirmed and getting the documents ready", done: null, total: null };
    draw();
    try {
      await HB.edits.save("bulk", { plan: ui.plan.id, documents: ui.docs, instructions: ui.ins });
    } catch (e) {
      ui.step = "confirm";
      ui.error = e && e.message ? e.message : "This could not be saved.";
      return draw();
    }
    poll(ui.plan.id, 0);
  }

  async function poll(id, n) {
    if (!ui || ui.step !== "filing" || !ui.plan || ui.plan.id !== id) return;
    try {
      const res = await HB.local.get("bulk/" + encodeURIComponent(id));
      const plan = res.ok ? await res.json() : null;
      if (plan && plan.status === "done") {
        ui.result = plan.result;
        ui.step = "done";
        if (HB.local.refresh) HB.local.refresh();
        return draw();
      }
      if (plan && plan.status === "failed") {
        ui.step = "confirm";
        ui.error = "It could not be filed: " + (plan.error || "see the note in the bar") + ".";
        return draw();
      }
    } catch (e) { /* the server is busy filing; ask again */ }
    if (n > POLL_LIMIT) {
      ui.error = "Filing is taking long. It carries on; the bar at the top says when it is done.";
      return draw();
    }
    setTimeout(() => poll(id, n + 1), POLL_MS);
  }

  /* ---- Step 4: done, and the offer to score again ------------------------------------------- */

  function doneStep() {
    const r = ui.result || {};
    const units = unitsById();
    const unitName = (id) => (units.get(id) && units.get(id).name) || id;
    const out = [];
    const filed = r.filed || [];
    if (filed.length) {
      out.push(h("p", null, h("b", null, "Filed " + plural(filed.length, "document") + ": "),
        filed.map((f) => f.title + " (" + f.rows.map(unitName).join(", ") + (f.date ? ", in force from " + f.date : "") + ")").join("; ") + "."));
    }
    const added = (r.instructions || []).filter((i) => i.added);
    if (added.length) out.push(h("p", null, h("b", null, "Added " + plural(added.length, "extra instruction") + ": "),
      "question" + (added.length === 1 ? " " : "s ") + added.map((i) => i.qid).join(", ") + "."));
    if ((r.failed || []).length) {
      out.push(HB.notice("warning", h("div", null, h("p", null, plural(r.failed.length, "document") + " could not be filed:"),
        h("ul", null, r.failed.map((f) => h("li", null, (f.title || "A document") + ": " + f.why))))));
    }
    const stale = r.out_of_date || {};
    const nStale = Object.values(stale).reduce((n, qs) => n + qs.length, 0);
    if (!nStale) {
      out.push(h("p", null, "No scored answer was marked out of date, so there is nothing to score again."));
      return out;
    }
    const rows = Object.keys(stale);
    const which = rows.slice(0, 6).map((row) => unitName(row) + ": question" + (stale[row].length === 1 ? " " : "s ") + stale[row].join(", ")).join("; ")
      + (rows.length > 6 ? "; and " + plural(rows.length - 6, words().one, words().many) + " more" : "");
    const est = r.estimate;
    if (ui.rescoreSent) {
      out.push(HB.notice("good", "Scoring them again has started. The bar at the top shows how it is going."));
      return out;
    }
    out.push(h("div", { class: "hbb-offer" },
      h("p", null, h("b", null, plural(nStale, "answer") + " " + (nStale === 1 ? "is" : "are") + " now out of date"), " (" + which + ")."),
      h("p", null, "Score just these again now? " + (est ? HB.cost.range(est) + " of your Claude plan." : "")),
      h("div", { class: "hbb-offer__btns" },
        h("button", { type: "button", class: "btn btn--primary", id: "hbb-rescore", onclick: rescore }, "Score them again"),
        h("button", { type: "button", class: "btn btn--quiet", onclick: () => ui.dialog.close() }, "Not now")),
      h("p", { class: "hb-hint" }, "Not now leaves them marked out of date on the Grid (a dark corner), to score again later.")));
    return out;
  }

  async function rescore() {
    const r = ui.result || {};
    const btn = document.getElementById("hbb-rescore");
    if (btn) btn.disabled = true;
    try {
      let i = 0;
      for (const g of r.rescore || []) {
        i += 1;
        await HB.edits.save("control", { action: "rerun", rule: null, why: "after what you added" + (r.rescore.length > 1 ? " (" + i + " of " + r.rescore.length + ")" : ""),
          rows: g.rows, questions: g.questions });
      }
      ui.rescoreSent = true;
    } catch (e) {
      ui.error = e && e.message ? e.message : "The re-score could not be started.";
    }
    draw();
  }

  /* ---- The dialog --------------------------------------------------------------------------- */

  function draw() {
    if (!ui || !ui.dialog) return;
    const step = ui.step;
    const body = step === "drop" ? dropStep()
      : step === "reading" ? waiting("Reading what you sent", "Usually under a minute for a few documents, longer for many. You can close this: nothing is filed until you confirm, but you would need to send the files again.")
        : step === "confirm" ? confirmStep()
          : step === "filing" ? waiting("Filing", "Usually under a minute; a few minutes for many documents. You can close this: the filing carries on, and the bar at the top of the page says when it is done.")
            : doneStep();
    const c = step === "confirm" ? counts() : null;
    const foot = [];
    if (step === "drop") {
      foot.push(h("button", { type: "button", class: "btn btn--quiet", onclick: () => ui.dialog.close() }, "Cancel"),
        h("button", { type: "button", class: "btn btn--primary", disabled: !ui.files.length, onclick: readThem }, "Read them"));
    } else if (step === "confirm") {
      const what = [c.docs ? "file " + plural(c.docs, "document") : null, c.ins ? "add " + plural(c.ins, "instruction") : null].filter(Boolean).join(" and ");
      foot.push(h("button", { type: "button", class: "btn btn--quiet", onclick: () => { ui.step = "drop"; ui.plan = null; draw(); } }, "Start again"),
        h("button", { type: "button", class: "btn btn--primary", disabled: !what, onclick: confirm },
          what ? "Confirm: " + what : "Nothing ticked"));
    } else if (step === "done") {
      foot.push(h("button", { type: "button", class: "btn", onclick: () => ui.dialog.close() }, "Close"));
    }
    const status = ui.error ? HB.notice("critical", ui.error) : null;
    ui.dialog.replaceChildren(h("div", { class: "hb-dialog__form" },
      h("div", { class: "hb-dialog__head" },
        h("button", { type: "button", class: "hbb-x", "aria-label": "Close", title: "Close", onclick: () => ui.dialog.close() }, "\u00d7"),
        h("p", { class: "eyebrow" }, "Documents and instructions"),
        h("h2", { id: "hbb-title" }, step === "done" ? "Added" : "Add documents or instructions")),
      h("div", { class: "hb-dialog__body" }, body),
      h("div", { class: "hb-dialog__status", "aria-live": "polite" }, status),
      h("div", { class: "hb-dialog__foot" }, foot)));
  }

  /** Opens the dialog; files (a FileList or array, such as those dropped on the corner chat) go straight in. */
  function open(files) {
    if (!isLocal()) return;
    HB.addStyles("bulk", STYLE);
    const given = files && typeof files.length === "number" ? files : null;
    if (ui && ui.dialog && ui.dialog.open) {
      if (given && ui.step === "drop") add(given);
      return;
    }
    const d = h("dialog", { class: "hb-dialog hbb", "aria-labelledby": "hbb-title" });
    ui = { step: "drop", files: [], plan: null, docs: [], ins: [], error: null, dialog: d };
    const timer = setInterval(tick, 1000);
    d.addEventListener("close", () => {
      clearInterval(timer);
      d.remove();
      ui = null;
      const back = document.getElementById("hbb-open");
      if (back) back.focus();
    });
    (document.querySelector(".hb") || document.body).append(d);
    draw();
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
    if (given) add(given);
  }

  /** The Grid toolbar's button; null on a page that is not the local one. */
  function button() {
    if (!isLocal()) return null;
    return h("button", { type: "button", class: "btn btn--small", id: "hbb-open", onclick: () => open() }, "Add documents or instructions");
  }

  HB.bulk = { button, open };
})((window.HB = window.HB || {}));
