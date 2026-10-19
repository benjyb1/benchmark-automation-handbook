/* =============================================================================
   Handbook viewer: views/runbar.js, the run bar on the Grid tab.
   Defines HB.runBar(viewId, root, ctx); app.js calls it after drawing a tab, and
   it puts the bar under the tab's heading (Grid only).

   What it shows (from the bundle):
     the open run (runs with finished null: kind, when it started, rows in scope
     scored, and the row it takes to be in progress), the run control
     (bundle.control: paused, stop now, stop after a row, scope, stop point).
   The row in progress is worked out the way the run skill takes rows: rows in
   scope in group order (group_by value, groups as they first appear), first any
   row whose documents were not looked for yet (finding), else the first row with
   cells still to score (scoring).

   What it does: Pause or Resume, "Stop after <row>", Stop now, "Choose what to
   run" (a dialog: rows by label value, questions, each with "all"), and "Stop
   after: Finding documents | Scoring | Carry on to the end". Each writes one
   request to the Artifact database collection `control` through HB.edits.save:
     {action: "pause" | "resume" | "stop_now" | "stop_after_row" | "scope" |
      "stop_after", row?, rows?, questions?, stage?}  (+ id, by, at)
   and the bar says "Waiting for Claude" until the bundle's control.applied holds
   the id (Claude applies requests with `hb control apply` before the next row and
   republishes). Without the store (HB.edits.ready false) the bar says the run
   can't be changed from this view. HB.runBar.request(body) and HB.runBar.describe(body)
   are the same save and words, for the corner chat (views/chat.js).
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;
  const TABS = new Set(["grid"]);
  const KIND_WORDS = { trial: "Trial run", full: "Full run", rerun: "Rerun" };
  const STOP_WORDS = { find: "Finding documents", score: "Scoring", none: "Carry on to the end" };
  const WHEN = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

  const CSS = `
.rb { display: grid; gap: var(--s2); min-width: 0; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); font-size: var(--fs-14); }
.rb[data-state="paused"], .rb[data-state="stopped"] { border-color: color-mix(in srgb, var(--warning) 55%, var(--surface)); }
.rb__top { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); min-width: 0; }
.rb__title { font-family: var(--sans); font-size: var(--fs-14); font-weight: 650; }
.rb__facts { display: flex; flex-wrap: wrap; gap: 0 var(--s2); min-width: 0; margin: 0; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.rb__facts > span + span::before { content: "\\00b7"; margin-right: var(--s2); color: var(--rule-strong); }
.rb__facts b { color: var(--ink); font-weight: 600; }
.rb__actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); min-width: 0; }
.rb__stop { display: inline-flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); margin-left: auto; color: var(--ink-2); font-size: var(--fs-12); }
.rb__stop .segmented > .btn { min-height: 1.75rem; padding-inline: 0.625rem; font-size: var(--fs-12); }
.rb__wait { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); margin: 0; color: var(--ink-2); font-size: var(--fs-12); }
.rb__wait b { color: var(--ink); font-weight: 600; }
.rb__chat { margin: 0; color: var(--ink-2); font-size: var(--fs-12); }
.rb__cost { margin: 0; color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
.rb__cards { display: grid; gap: var(--s2); }
.rb__card { display: grid; gap: var(--s1); padding: var(--s2); border: 1px solid color-mix(in srgb, var(--warning) 45%, var(--surface)); border-radius: var(--radius); }
.rb__card p { margin: 0; }
.rb__chat q { color: var(--ink); font-weight: 600; }
.rb__msg:empty { display: none; }
.rb-pick__count { color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
.rb-pick__list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 14rem), 1fr)); gap: 0 var(--s4); }
.rb-pick__list .hb-check { align-items: flex-start; }
.rb-pick__list .hb-check input { margin-top: 0.3rem; }
@media (width < 40rem) {
  .rb__facts { display: grid; gap: 2px; font-size: var(--fs-12); }
  .rb__facts > span + span::before { content: none; }
  .rb__stop { margin-left: 0; flex-basis: 100%; }
}
`;
  HB.addStyles("runbar", CSS);

  /* ---- The control requests in the store ------------------------------------------ */

  let docs = [];                  // every doc in `control`, as the store last said
  const mine = new Map();         // saved here and not seen back yet
  let following = false;
  let current = null;             // {bar, viewId, ctx} of the bar on screen
  let message = null;             // {tone, text} after a save, shown once
  let focusId = null;             // the control to focus again after a redraw

  const arr = (v) => (Array.isArray(v) ? v : []);

  function control(b) {
    const c = (b && b.control && typeof b.control === "object") ? b.control : {};
    const scope = c.scope && typeof c.scope === "object" ? c.scope : {};
    return {
      paused: c.paused === true,
      stop_now: c.stop_now === true,
      stop_after_row: typeof c.stop_after_row === "string" ? c.stop_after_row : null,
      scope: { rows: Array.isArray(scope.rows) ? scope.rows : null, questions: Array.isArray(scope.questions) ? scope.questions : null },
      stop_after: c.stop_after === "find" || c.stop_after === "score" ? c.stop_after : null,
      applied: new Set(arr(c.applied)),
    };
  }

  function pending(ctl) {
    const all = new Map(docs.map((d) => [d.id, d]));
    mine.forEach((d, id) => { if (!all.has(id)) all.set(id, d); });
    return Array.from(all.values())
      .filter((d) => d && !ctl.applied.has(d.id))
      .sort((a, b) => String(a.at).localeCompare(String(b.at)));
  }

  function follow() {
    if (following || !HB.edits || !HB.edits.ready || typeof HB.edits.follow !== "function") return;
    following = HB.edits.follow("control", (next) => {
      docs = next;
      next.forEach((d) => mine.delete(d.id));
      redraw();
    });
  }

  /* ---- What the bundle says about the run ---------------------------------------- */

  function openRun(b) {
    const open = arr(b.runs).filter((r) => r && typeof r === "object" && !r.finished);
    open.sort((x, y) => String(y.started).localeCompare(String(x.started)));
    return open[0] || null;
  }

  function name(row) {
    const u = HB.data.unit(row);
    return u && u.name ? String(u.name) : String(row);
  }

  function inGroups(units, label) {
    if (!label) return units.slice();
    const groups = new Map();
    units.forEach((u) => {
      const v = (u.labels || {})[label];
      const key = v == null ? "\u0000" : String(v);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(u);
    });
    return [].concat(...groups.values());
  }

  function progress(b, ctl, run) {
    const units = arr(b.units).filter((u) => u && u.id);
    const keep = (list) => (list ? new Set(list) : null);
    const rowsIn = keep(ctl.scope.rows);
    const qsIn = keep(ctl.scope.questions);
    const runRows = keep(run && Array.isArray(run.rows) ? run.rows : null);
    const runQs = keep(run && Array.isArray(run.questions) ? run.questions : null);
    const inScope = units.filter((u) => (!rowsIn || rowsIn.has(u.id)) && (!runRows || runRows.has(u.id)));
    const looked = new Set();
    arr(b.runs).forEach((r) => {
      const usage = r && r.usage && typeof r.usage === "object" ? r.usage : {};
      Object.keys(usage).forEach((row) => { if (usage[row] && usage[row].find != null) looked.add(row); });
    });
    const toFind = (u) => {
      const f = (b.found || {})[u.id] || {};
      return !(arr(f.documents).length || arr(f.expected_not_found).length || f.second_search || looked.has(u.id));
    };
    const left = (u) => {
      const cells = (b.cells || {})[u.id] || {};
      return Object.keys(cells).some((q) => (!qsIn || qsIn.has(q)) && (!runQs || runQs.has(q))
        && !(HB.data.isScored(cells[q]) && cells[q].out_of_date !== true));
    };
    const ordered = inGroups(inScope, b.project && b.project.group_by);
    const finding = ordered.find(toFind);
    const scoring = finding ? null : ordered.find(left);
    return {
      rows: inScope.length,
      done: inScope.filter((u) => !left(u)).length,
      anyLeft: units.some(left),
      leftIds: inScope.filter(left).map((u) => u.id),
      row: (finding || scoring || {}).id || null,
      phase: finding ? "finding documents" : scoring ? "scoring" : null,
    };
  }

  function isDefault(ctl) {
    return !ctl.paused && !ctl.stop_now && !ctl.stop_after_row && !ctl.scope.rows && !ctl.scope.questions && !ctl.stop_after;
  }

  /* ---- Words ------------------------------------------------------------------------ */

  function scopeWords(rows, questions, b) {
    const nRows = arr(b.units).length;
    const nQs = arr(b.questions).length;
    const r = rows ? fmtInt(rows.length) + " of " + HB.data.unitWords(nRows) : "every " + ((b.project && b.project.unit_word) || "row");
    const q = questions ? fmtInt(questions.length) + " of " + fmtInt(nQs) + " questions" : "every question";
    return r + ", " + q;
  }

  function requestWords(d, b) {
    switch (d.action) {
      case "pause": return "Pause";
      case "resume": return "Resume";
      case "stop_now": return "Stop now";
      case "stop_after_row": return "Stop after " + name(d.row);
      case "scope": return "Run " + scopeWords(d.rows, d.questions, b);
      case "stop_after": return "Stop after: " + STOP_WORDS[d.stage || "none"];
      case "rerun": return "Score again: " + arr(d.rows).map(name).join(", ");
      case "row_ok": return (d.what === "slow" ? "Carry on with " : "Leave ") + name(d.row);
      default: return String(d.action);
    }
  }

  function startedWords(run) {
    const when = new Date(String(run.started));
    return Number.isNaN(when.getTime()) ? String(run.started || "") : WHEN.format(when);
  }

  /* ---- Saving a request ------------------------------------------------------------- */

  /* One request saved, as the bar's buttons save it; throws the save's own message. The corner
     chat (views/chat.js) uses it too, as HB.runBar.request. */
  async function request(body) {
    const doc = await HB.edits.save("control", body);
    mine.set(doc.id, doc);
    redraw();
    return doc;
  }

  async function send(body, id) {
    focusId = id || null;
    try {
      await request(body);
      message = null;
    } catch (err) {
      message = { tone: "critical", text: err && err.message ? err.message : "This could not be saved." };
    }
    redraw();
  }

  /* ---- Choose what to run (a dialog, styled like the edit dialogs) ------------------ */

  function tick(id, value, on, text) {
    const input = h("input", { type: "checkbox", id, value });
    input.checked = Boolean(on);
    return { input, node: h("label", { class: "hb-check", for: id }, input, h("span", null, text)) };
  }

  function chooseDialog(b, ctl) {
    const old = document.getElementById("rb-dialog");
    if (old) old.remove();
    const units = arr(b.units).filter((u) => u && u.id);
    const questions = arr(b.questions).filter((q) => q && q.id);
    const labels = arr(b.project && b.project.labels);
    const plural = (b.project && b.project.unit_word_plural) || "rows";
    const scoped = ctl.scope.rows ? units.filter((u) => ctl.scope.rows.includes(u.id)) : units;

    const allRows = tick("rb-rows-all", "all", !ctl.scope.rows, "All " + plural);
    const groups = labels.map((label, i) => {
      const values = HB.data.labelValues(label);
      const present = new Set(scoped.map((u) => String((u.labels || {})[label])));
      const every = values.every((v) => present.has(v));
      const boxes = values.map((v, j) => tick("rb-rows-" + i + "-" + j, v, ctl.scope.rows && !every && present.has(v), v));
      return { label, boxes, node: h("fieldset", { class: "hb-pick", id: "rb-rows-set-" + i },
        h("legend", null, label.charAt(0).toUpperCase() + label.slice(1).replace(/_/g, " ")), boxes.map((x) => x.node)) };
    });
    const rowPick = h("div", { class: "hb-pick-set", id: "rb-rows-pick" }, groups.map((g) => g.node));
    const rowCount = h("p", { class: "rb-pick__count", id: "rb-rows-count", "aria-live": "polite" });

    const allQs = tick("rb-qs-all", "all", !ctl.scope.questions, "All questions");
    const qBoxes = questions.map((q, i) => tick("rb-q-" + i, q.id, ctl.scope.questions && ctl.scope.questions.includes(q.id),
      [h("span", { class: "mono" }, String(q.id)), q.short ? " " + q.short : ""]));
    const qPick = h("div", { class: "rb-pick__list", id: "rb-qs-pick" }, qBoxes.map((x) => x.node));
    const qCount = h("p", { class: "rb-pick__count", id: "rb-qs-count", "aria-live": "polite" });

    function chosenRows() {
      if (allRows.input.checked) return null;
      const filter = groups.map((g) => ({ label: g.label, values: g.boxes.filter((x) => x.input.checked).map((x) => x.input.value) }))
        .filter((f) => f.values.length);
      if (!filter.length) return null;
      return units.filter((u) => filter.every((f) => f.values.includes(String((u.labels || {})[f.label])))).map((u) => u.id);
    }
    function chosenQs() {
      return allQs.input.checked ? null : qBoxes.filter((x) => x.input.checked).map((x) => x.input.value);
    }
    function sync() {
      rowPick.hidden = allRows.input.checked || !labels.length;
      qPick.hidden = allQs.input.checked;
      const r = chosenRows();
      const q = chosenQs();
      rowCount.textContent = (r ? fmtInt(r.length) : fmtInt(units.length)) + " of " + HB.data.unitWords(units.length) + " chosen.";
      qCount.textContent = (q ? fmtInt(q.length) : fmtInt(questions.length)) + " of " + fmtInt(questions.length) + " questions chosen.";
    }
    [allRows, allQs, ...qBoxes, ...groups.flatMap((g) => g.boxes)].forEach((x) => x.input.addEventListener("change", sync));

    const status = h("div", { class: "hb-dialog__status", id: "rb-dialog-status", "aria-live": "polite" });
    const save = h("button", { type: "submit", class: "btn btn--primary", id: "rb-dialog-save" }, "Save");
    const d = h("dialog", { class: "hb-dialog", id: "rb-dialog", "aria-labelledby": "rb-dialog-title" });
    const cancel = h("button", { type: "button", class: "btn btn--quiet", id: "rb-dialog-cancel", onclick: () => d.close() }, "Cancel");
    const same = (a, x) => (a === null && x === null) || (Array.isArray(a) && Array.isArray(x) && a.length === x.length && a.every((v) => x.includes(v)));
    const form = h("form", {
      class: "hb-dialog__form",
      novalidate: true,
      onsubmit: async (ev) => {
        ev.preventDefault();
        const rows = chosenRows();
        const qs = chosenQs();
        const problem = rows && !rows.length ? "No " + plural + " match these values. Tick other values, or all " + plural + "."
          : qs && !qs.length ? "Tick at least one question, or all questions."
            : same(rows, ctl.scope.rows) && same(qs, ctl.scope.questions) ? "Nothing has changed." : null;
        if (problem) {
          status.replaceChildren(HB.notice("critical", problem));
          return;
        }
        save.disabled = true;
        try {
          const doc = await HB.edits.save("control", { action: "scope", rows, questions: qs });
          mine.set(doc.id, doc);
        } catch (err) {
          save.disabled = false;
          status.replaceChildren(HB.notice("critical", err && err.message ? err.message : "This could not be saved."));
          return;
        }
        d.close();
        message = null;
        focusId = "rb-choose";
        redraw();
      },
    },
    h("div", { class: "hb-dialog__head" },
      h("p", { class: "eyebrow" }, "Run control"),
      h("h2", { id: "rb-dialog-title" }, "Choose what to run"),
      h("p", { class: "hb-hint" }, "Claude runs only what you tick, from the next " + ((b.project && b.project.unit_word) || "row")
        + " on. " + plural.charAt(0).toUpperCase() + plural.slice(1) + " and questions left out keep the answers they have.")),
    h("div", { class: "hb-dialog__body" },
      h("fieldset", { class: "hb-dialog__fields", id: "rb-rows" },
        h("legend", { class: "field__label" }, plural.charAt(0).toUpperCase() + plural.slice(1)),
        allRows.node, labels.length ? h("p", { class: "hb-hint" }, "Or tick label values: the " + plural + " run are those with a ticked value in every label you tick.") : null,
        rowPick, rowCount),
      h("fieldset", { class: "hb-dialog__fields", id: "rb-qs" },
        h("legend", { class: "field__label" }, "Questions"), allQs.node, qPick, qCount)),
    status,
    h("div", { class: "hb-dialog__foot" }, cancel, save));
    d.append(form);
    d.addEventListener("click", (ev) => { if (ev.target === d) d.close(); });
    d.addEventListener("close", () => {
      d.remove();
      const back = document.getElementById("rb-choose");
      if (back) back.focus();
    });
    (document.querySelector(".hb") || document.body).append(d);
    sync();
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
    allRows.input.focus();
  }

  /* ---- The bar ------------------------------------------------------------------------ */

  function button(id, text, onclick, opts) {
    const o = opts || {};
    return h("button", {
      type: "button",
      id,
      class: ["btn", "btn--small", o.primary && "btn--primary"],
      disabled: Boolean(o.disabled),
      "aria-pressed": o.pressed == null ? null : String(o.pressed),
      title: o.title || null,
      onclick,
    }, text);
  }

  function chatLine() {
    return h("p", { class: "rb__chat", id: "rb-chat" },
      HB.when(HB.cantSave(), "The run can't be changed from this view. Open the project's own page to pause it, stop it or choose what to run."));
  }

  function fill(bar, b) {
    const ctl = control(b);
    const run = openRun(b);
    const prog = progress(b, ctl, run);
    const waiting = pending(ctl);
    const ready = Boolean(HB.edits && HB.edits.ready && typeof HB.edits.save === "function");
    const wants = (action, extra) => waiting.some((d) => d.action === action && (!extra || extra(d)));

    const state = ctl.paused ? "paused" : ctl.stop_now ? "stopped" : run ? "running" : "idle";
    bar.setAttribute("data-state", state);
    const chip = state === "paused" ? HB.statusChip("warning", "Paused")
      : state === "stopped" ? HB.statusChip("warning", "Stopped")
        : state === "running" ? HB.statusChip("good", "Running")
          : HB.statusChip("neutral", "No run open");

    const facts = [];
    if (run) {
      facts.push(h("span", null, h("b", null, KIND_WORDS[run.kind] || "Run"), ", started " + startedWords(run)));
      facts.push(h("span", null, h("b", null, fmtInt(prog.done)), " of " + HB.data.unitWords(prog.rows) + " done"));
      if (prog.row) facts.push(h("span", null, "In progress: ", h("b", null, name(prog.row)), " (" + prog.phase + ")"));
    } else {
      facts.push(h("span", null, "Settings for the next run"));
    }
    facts.push(h("span", null, "Runs ", h("b", null, scopeWords(ctl.scope.rows, ctl.scope.questions, b))));
    if (ctl.stop_after_row) facts.push(h("span", null, "Stops after ", h("b", null, name(ctl.stop_after_row))));
    facts.push(h("span", null, ctl.stop_after === "find" ? ["Stops after ", h("b", null, "finding documents")]
      : ctl.stop_after === "score" ? ["Stops after ", h("b", null, "scoring")] : "Carries on to the end"));

    const top = h("div", { class: "rb__top" }, h("h3", { class: "rb__title", id: "rb-title" }, "Run"), chip, h("p", { class: "rb__facts" }, facts));
    const cost = costLine(b, ctl, run, prog);
    const stopped = rowCards(b);

    let actions = null;
    if (ready) {
      const halted = ctl.paused || ctl.stop_now;
      const list = [];
      if (run || halted) {
        list.push(halted
          ? button("rb-resume", wants("resume") ? "Resume asked" : "Resume", () => send({ action: "resume" }, "rb-resume"), { primary: true, disabled: wants("resume") })
          : button("rb-pause", wants("pause") ? "Pause asked" : "Pause", () => send({ action: "pause" }, "rb-pause"), { disabled: wants("pause") }));
      }
      if (run && prog.row && !halted) {
        const asked = ctl.stop_after_row === prog.row || wants("stop_after_row", (d) => d.row === prog.row);
        list.push(button("rb-stop-row", "Stop after " + name(prog.row), () => send({ action: "stop_after_row", row: prog.row }, "rb-stop-row"),
          { disabled: asked, title: "Finish " + name(prog.row) + ", then stop" }));
      }
      if (run && !halted) {
        list.push(button("rb-stop-now", wants("stop_now") ? "Stop asked" : "Stop now", () => send({ action: "stop_now" }, "rb-stop-now"),
          { disabled: wants("stop_now"), title: "Stop before the next row starts" }));
      }
      list.push(button("rb-choose", "Choose what to run", () => chooseDialog(b, ctl)));
      const stopAt = ctl.stop_after || "none";
      const seg = h("div", { class: "segmented", role: "group", "aria-label": "Stop after" },
        ["find", "score", "none"].map((stage) => button("rb-after-" + stage, STOP_WORDS[stage],
          () => { if (stage !== stopAt) send({ action: "stop_after", stage: stage === "none" ? null : stage }, "rb-after-" + stage); },
          { pressed: stage === stopAt })));
      list.push(h("div", { class: "rb__stop" }, h("span", { "aria-hidden": "true" }, "Stop after:"), seg));
      actions = h("div", { class: "rb__actions" }, list);
    }

    const wait = waiting.length
      ? h("p", { class: "rb__wait", id: "rb-wait", role: "status" },
        HB.statusChip("neutral", "Waiting for Claude"),
        h("span", null, h("b", null, waiting.map((d) => requestWords(d, b)).join("; ")), ". Claude applies "
          + (waiting.length === 1 ? "this" : "these") + HB.when(" before the next row.", " before the next row, or the next time it works on this project.")))
      : null;
    const msg = h("div", { class: "rb__msg", id: "rb-msg", role: "status" }, message ? HB.notice(message.tone, message.text) : null);

    // Under hb serve, local.js shows the job running on this computer (a re-run, a re-read) here.
    let local = null;
    try {
      local = HB.local && typeof HB.local.runLine === "function" ? HB.local.runLine() : null;
    } catch (err) {
      local = null;
    }
    bar.replaceChildren(...[top, cost, stopped, actions || chatLine(), local, wait, msg].filter(Boolean));
    return { show: Boolean(run) || Boolean(stopped) || Boolean(local) || waiting.length > 0 || !isDefault(ctl) || (b.project && b.project.stage !== "setup" && prog.anyLeft) };
  }

  /** Tokens on the owner's Claude plan (HB.cost): during a run, what it has used so far; before one, a
      rough range for the rows left in scope (the trial's estimate when there is one, which includes finding). */
  function costLine(b, ctl, run, prog) {
    if (!HB.cost || !HB.cost.part()) return null;
    const est = b.estimate && b.estimate.point != null && b.estimate.rows ? b.estimate : null;
    let text = null;
    if (run) {
      const usage = run.usage && typeof run.usage === "object" ? run.usage : {};
      let spent = Object.values(usage).reduce((sum, steps) => sum + Object.values(steps || {}).reduce((x, v) => x + (Number(v) || 0), 0), 0);
      spent += Object.values(run.group_usage || {}).reduce((x, v) => x + (Number(v) || 0), 0);
      text = "Used so far: " + HB.cost.tokens(spent) + " tokens of your Claude plan" +
        (run.kind === "full" && est ? ", against an estimate of " + HB.cost.range(est) + " for the whole run." : ".");
    } else if (prog.leftIds.length && b.project && b.project.stage !== "setup") {
      if (est && !ctl.scope.rows && !ctl.scope.questions) {
        text = "The full run of the " + HB.data.unitWords(est.rows) + " left: " + HB.cost.line(est).replace(/^A/, "a") + " This comes from your trial and includes finding documents.";
      } else {
        const r = HB.cost.rows(prog.leftIds, ctl.scope.questions);
        if (r) text = "Scoring the " + HB.data.unitWords(r.rows) + " left: " + HB.cost.line(r).replace(/^A/, "a") + " Finding their documents adds more.";
      }
    }
    return text ? h("p", { class: "rb__cost", id: "rb-cost" }, text) : null;
  }

  /* ---- Rows waiting on the owner (the bundle's `watch`, rowwatch.py) --------------------------
     A row stopped at its token budget: Carry on (a re-run of the questions left, in a fresh kit),
     Upload a document, or Leave it. A row much slower than similar ones (only rows whose documents
     Claude found on the web): Upload a document, or Carry on. Neither holds up other rows. */

  const SLOW_WHAT = "A document that may be missing";     // serve.py knows this one (rowwatch.SLOW_WHAT)
  const WHY = { budget: "its token budget", turns: "its turn limit", time: "the time limit" };

  function rowCards(b) {
    const watch = b.watch && typeof b.watch === "object" ? b.watch : {};
    const tokens = (n) => (HB.cost ? HB.cost.tokens(n) : fmtInt(n));
    const ready = Boolean(HB.edits && HB.edits.ready && typeof HB.edits.save === "function");
    const canSend = HB.supply && typeof HB.supply.ready === "function" && HB.supply.ready();
    const ctl = control(b);
    const pend = pending(ctl);
    const asked = (row, action) => pend.some((d) => d.action === action && (d.row === row || arr(d.rows).includes(row)));
    const cards = [];
    for (const [row, w] of Object.entries(watch)) {
      if (!w || (!w.stopped && !w.slow)) continue;
      const upload = canSend ? button("rb-up-" + row, "Upload a document", () => HB.supply.send({ row, name: name(row), pattern: "not_found",
        what: SLOW_WHAT, kicker: "Documents", intro: "If you have a document Claude may be missing for " + name(row) + ", send the file or a link. " +
          HB.when("Its unfinished and doubtful answers are then scored again.", "Claude scores those answers again the next time it works on this project.") })) : null;
      if (w.stopped) {
        const s = w.stopped;
        const n = arr(s.left).length;
        const text = name(row) + " was stopped at " + (WHY[s.why] || "a limit") + " after " + tokens(s.tokens || 0) + " tokens, with " +
          fmtInt(n) + (n === 1 ? " question" : " questions") + " left.";
        const going = s.carrying_on || asked(row, "rerun");
        cards.push(h("div", { class: "rb__card", id: "rb-row-" + row },
          h("p", null, text + (going ? " Carrying on in a fresh session." : "")),
          !going && ready ? h("p", { class: "rb__actions" },
            button("rb-go-" + row, "Carry on", () => send({ action: "rerun", rule: null, rows: [row], questions: arr(s.left),
              why: "Carry on after " + (WHY[s.why] || "a limit") }, "rb-go-" + row), { primary: true }),
            upload,
            button("rb-leave-" + row, asked(row, "row_ok") ? "Left" : "Leave it", () => send({ action: "row_ok", row, what: "stopped" }, "rb-leave-" + row),
              { disabled: asked(row, "row_ok"), title: "Leave these questions not scored" })) : null));
      } else if (w.slow) {
        const sl = w.slow;
        const like = sl.similar ? "similar " + ((b.project && b.project.unit_word_plural) || "rows") + " (" + sl.similar + ")"
          : "similar " + ((b.project && b.project.unit_word_plural) || "rows");
        cards.push(h("div", { class: "rb__card", id: "rb-row-" + row },
          h("p", null, name(row) + " took " + String(sl.times) + " times as long as " + like + ", so a document may be missing."),
          ready ? h("p", { class: "rb__actions" }, upload,
            button("rb-ok-" + row, asked(row, "row_ok") ? "Carrying on" : "Carry on", () => send({ action: "row_ok", row, what: "slow" }, "rb-ok-" + row),
              { disabled: asked(row, "row_ok"), title: "The answers stand as they are" })) : null));
      }
    }
    return cards.length ? h("div", { class: "rb__cards", id: "rb-rows" }, cards.slice(0, 6),
      cards.length > 6 ? h("p", { class: "rb__cost" }, fmtInt(cards.length - 6) + " more rows are waiting too.") : null) : null;
  }

  function redraw() {
    if (!current || !current.bar.isConnected) return;
    const b = HB.data.bundle();
    if (!b) return;
    fill(current.bar, b);
    if (focusId) {
      const el = document.getElementById(focusId);
      if (el && !el.disabled) el.focus();
      else if (el) {
        const fallback = current.bar.querySelector("button:not([disabled])");
        if (fallback) fallback.focus();
      }
      focusId = null;
    }
  }

  HB.runBar = function runBar(viewId, root, ctx) {
    if (!TABS.has(viewId)) return null;
    const b = ctx && ctx.bundle;
    if (!b || !arr(b.units).length || !arr(b.questions).length) return null;
    follow();
    const bar = h("section", { class: "rb", id: "rb", "aria-labelledby": "rb-title" });
    const shown = fill(bar, b);
    if (!shown.show) return null;
    const head = root.querySelector("h2");
    const anchor = head ? (head.closest("header") || head) : null;
    if (anchor && root.contains(anchor)) anchor.after(bar);
    else root.prepend(bar);
    current = { bar, viewId, ctx };
    return bar;
  };
  // For the corner chat: save a request (Promise of the doc), and the bar's words for one.
  HB.runBar.request = request;
  HB.runBar.redraw = redraw;
  HB.runBar.describe = (body) => requestWords(body || {}, HB.data.bundle() || {});
})((window.HB = window.HB || {}));
