/* =============================================================================
   Handbook viewer: views/nextstep.js, the one green button that says what to do
   next, in the header of the local page (under hb serve only).

   19 Oct 2026, the first newcomer test: once the set-up was done there was no way
   to start scoring from the page, and nothing said what came next. So one button,
   always in the same place, always naming the next step:
     set-up not settled   Finish set-up (to the Set-up tab)
     nothing scored yet   Score a few first (the Score panel)
     a run left open      Carry on scoring
     rows still to score  Score the rest (the Score panel)
     all scored           See the results
   While Claude works (a set-up draft, or scoring) it shows what Claude is doing
   now, moving, with Pause for scoring.

   HB.nextStep = {
     node()                 the header's button, or what Claude is doing (null off the local page)
     openScore(choice)      the Score panel: "few" | "choose" | "all"
     start({kind, rows, questions})  saves one scoring request (the chat's start_scoring too):
                            kind "trial" | "full", rows and questions null for all;
                            Promise of the saved doc
   }
   The request goes to the `scoring` collection; serve.py finds the documents and
   scores, row by row, as the trial and run skills do.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;
  const arr = (x) => (Array.isArray(x) ? x : []);
  const MUST_HAVES = ["questions", "units", "scoresheet", "group_labels", "cutoff", "where_to_look", "needs", "rules"];
  const SETTLED = new Set(["confirmed", "skipped", "not_needed"]);
  const TRY = 3;

  const CSS = `
.hb-next { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); margin-left: auto; }
.hb-next__go { min-height: 2.75rem; padding-inline: var(--s5, 1.5rem); font-size: var(--fs-16); }
.hb-next__hint { color: var(--ink-2); font-size: var(--fs-12); }
.hb-score__choices { display: grid; gap: var(--s2); margin: 0; padding: 0; border: 0; }
.hb-score__choice { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--s1) var(--s2); align-items: start;
  padding: var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); cursor: pointer; }
.hb-score__choice:has(input:checked) { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
.hb-score__choice input { margin-top: 0.25rem; }
.hb-score__choice strong { display: block; }
.hb-score__choice span { color: var(--ink-2); font-size: var(--fs-14); }
.hb-score__rows { display: grid; gap: var(--s1); max-height: 14rem; overflow: auto; padding: var(--s2); border: 1px solid var(--rule); border-radius: var(--radius); }
.hb-score__cost { margin: 0; font-size: var(--fs-14); }
`;

  const bundle = () => (HB.data && HB.data.bundle ? HB.data.bundle() : null) || {};
  const words = () => {
    const p = bundle().project || {};
    return { one: p.unit_word || "row", many: p.unit_word_plural || "rows" };
  };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  /** Claude's job on this computer that the header follows: a set-up draft or scoring. */
  function busyJob() {
    const s = HB.local && typeof HB.local.state === "function" ? HB.local.state() : null;
    if (!s) return null;
    const mine = (j) => j && (j.kind === "scoring" || j.kind === "setup");
    if (mine(s.job)) return s.job;
    return arr(s.queued).find(mine) || null;
  }

  /** Rows with a question still to score (not run, missing or out of date). */
  function rowsLeft() {
    const b = bundle();
    const cells = b.cells || {};
    return arr(b.units).map((u) => u.id).filter((id) => Object.values(cells[id] || {}).some((c) =>
      !c || c.state === "not_run" || c.state === "missing" || c.out_of_date));
  }

  function step() {
    const b = bundle();
    const status = (b.project && b.project.checklist) || {};
    const open = MUST_HAVES.filter((k) => !SETTLED.has(status[k] || "missing"));
    if (open.length) {
      return { label: "Finish set-up", hint: fmtInt(open.length) + " of " + fmtInt(MUST_HAVES.length) + " still to settle",
        go: () => HB.go("setup") };
    }
    const runs = arr(b.runs);
    const full = runs.find((r) => r.kind === "full" && !r.finished);
    if (full) return { label: "Carry on scoring", hint: "the run stopped part way", go: () => carryOn() };
    const left = rowsLeft();
    const total = arr(b.units).length;
    if (!left.length && total) return { label: "See the results", go: () => HB.go("results") };
    if (left.length === total && !runs.some((r) => r.kind === "trial")) {
      return { label: "Score a few first", hint: "check the answers before scoring all " + fmtInt(total), go: () => openScore("few") };
    }
    return { label: "Score the rest", hint: fmtInt(left.length) + " " + (left.length === 1 ? words().one : words().many) + " to go",
      go: () => openScore("all") };
  }

  let timer = null;
  function node() {
    if (!HB.local) return null;
    clearTimeout(timer);
    const job = busyJob();
    let box;
    if (job) {
      const parts = [HB.working(job.line && job.line !== "Waiting to start" ? job.line : job.title || "Claude is working on it", { large: true })];
      if (job.kind === "scoring") {
        const pause = h("button", { type: "button", class: "btn btn--small", id: "hb-next-pause" }, "Pause");
        pause.addEventListener("click", async () => {
          pause.disabled = true;
          try {
            await HB.edits.save("control", { action: "pause" });
            pause.textContent = "Pausing after this " + words().one;
          } catch (err) {
            pause.disabled = false;
          }
        });
        parts.push(pause);
      }
      box = h("div", { class: "hb-next", id: "hb-next" }, parts);
      timer = setTimeout(() => { const old = document.getElementById("hb-next"); if (old) old.replaceWith(node() || ""); }, 2000);
    } else {
      const s = step();
      const go = h("button", { type: "button", class: "btn btn--primary hb-next__go", id: "hb-next-go",
        disabled: !(HB.edits && HB.edits.ready) && s.label !== "Finish set-up" && s.label !== "See the results" }, s.label);
      go.addEventListener("click", s.go);
      box = h("div", { class: "hb-next", id: "hb-next" }, go, s.hint ? h("span", { class: "hb-next__hint" }, s.hint) : null);
    }
    return box;
  }

  async function start(req) {
    if (!(HB.edits && HB.edits.ready)) throw new Error(HB.cantSave ? HB.cantSave() : "This page can't save just now.");
    const body = { action: "start", kind: req.kind === "full" ? "full" : "trial",
      rows: Array.isArray(req.rows) && req.rows.length ? req.rows.map(String) : null,
      questions: Array.isArray(req.questions) && req.questions.length ? req.questions.map(String) : null };
    const doc = await HB.edits.save("scoring", body);
    if (HB.local && typeof HB.local.refresh === "function") HB.local.refresh();
    return doc;
  }

  async function carryOn() {
    try {
      await HB.edits.save("control", { action: "resume" });
      await start({ kind: "full" });
    } catch (err) {
      if (HB.local && HB.local.flag) HB.local.flag(err && err.message ? err.message : "This could not be saved.");
    }
  }

  /** The cost line for scoring these rows (HB.cost; finding documents adds a little more). */
  function costOf(ids) {
    const est = HB.cost && ids.length ? HB.cost.rows(ids, null) : null;
    return est ? HB.cost.line(est) + " Finding the documents adds a little more." : "";
  }

  function openScore(choice) {
    const old = document.getElementById("hb-score");
    if (old) old.remove();
    const b = bundle();
    const w = words();
    const left = rowsLeft();
    const names = new Map(arr(b.units).map((u) => [u.id, u.name || u.id]));
    const tried = arr(b.runs).some((r) => r.kind === "trial" && r.finished);

    const radio = (value, title, text) => {
      const input = h("input", { type: "radio", name: "hb-score-kind", value, id: "hb-score-" + value });
      input.checked = value === (choice || (tried ? "all" : "few"));
      return { input, node: h("label", { class: "hb-score__choice", for: "hb-score-" + value }, input,
        h("div", null, h("strong", null, title), h("span", null, text))) };
    };
    const few = radio("few", "Try " + fmtInt(Math.min(TRY, left.length)) + " " + w.many + " first" + (tried ? "" : " (recommended)"),
      "Claude picks " + w.many + " that between them cover your groups, finds their documents and scores every question. You check the answers, and the trial gives a firmer cost for the rest.");
    const pick = radio("choose", "Choose which " + w.many, "Tick the " + w.many + " to score now, for example one you know well.");
    const all = radio("all", "Score all " + fmtInt(left.length) + " still to do",
      tried ? "Uses what your trial measured for the estimate." : "There is no trial yet, so this estimate is rough. Trying a few first costs far less and gives a firmer figure.");

    const filter = h("input", { type: "search", class: "input", id: "hb-score-filter", placeholder: "Find a " + w.one, "aria-label": "Find a " + w.one });
    const ticks = left.map((id, i) => {
      const input = h("input", { type: "checkbox", id: "hb-score-row-" + i, value: id });
      return { id, input, node: h("label", { class: "hb-check", for: "hb-score-row-" + i }, input, h("span", null, names.get(id))) };
    });
    const list = h("div", { class: "hb-score__rows", id: "hb-score-rows" }, ticks.map((t) => t.node));
    const pickBox = h("div", { class: "stack", hidden: true }, filter, list);
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      ticks.forEach((t) => { t.node.hidden = q && !String(names.get(t.id)).toLowerCase().includes(q); });
    });

    const cost = h("p", { class: "hb-score__cost", id: "hb-score-cost", "aria-live": "polite" });
    const status = h("div", { class: "hb-dialog__status", id: "hb-score-status", "aria-live": "polite" });
    const go = h("button", { type: "submit", class: "btn btn--primary", id: "hb-score-start" }, "Start");

    const chosen = () => (few.input.checked ? "few" : pick.input.checked ? "choose" : "all");
    const rowsFor = (c) => (c === "few" ? left.slice(0, TRY) : c === "choose" ? ticks.filter((t) => t.input.checked).map((t) => t.id) : left);
    function sync() {
      const c = chosen();
      pickBox.hidden = c !== "choose";
      const ids = rowsFor(c);
      cost.textContent = ids.length ? (c === "few" ? "About this much for " + fmtInt(ids.length) + " " + w.many + ": " : "") + costOf(ids)
        : "Tick at least one " + w.one + ".";
      go.disabled = !ids.length;
      go.textContent = c === "few" ? "Start the trial" : "Start scoring " + fmtInt(ids.length) + " " + (ids.length === 1 ? w.one : w.many);
    }
    [few, pick, all].forEach((x) => x.input.addEventListener("change", sync));
    ticks.forEach((t) => t.input.addEventListener("change", sync));

    const d = h("dialog", { class: "hb-dialog", id: "hb-score", "aria-labelledby": "hb-score-title" });
    const cancel = h("button", { type: "button", class: "btn btn--quiet", id: "hb-score-cancel", onclick: () => d.close() }, "Cancel");
    const form = h("form", {
      class: "hb-dialog__form", novalidate: true,
      onsubmit: async (ev) => {
        ev.preventDefault();
        const c = chosen();
        go.disabled = true;
        try {
          // A trial is the server's pick (rows that cover the groups) unless the owner chose rows.
          await start({ kind: c === "all" ? "full" : "trial", rows: c === "few" ? null : c === "choose" ? rowsFor(c) : null });
          d.close();
          if (HB.render) HB.render();
        } catch (err) {
          go.disabled = false;
          status.replaceChildren(HB.notice("critical", err && err.message ? err.message : "This could not be saved."));
        }
      },
    },
    h("div", { class: "hb-dialog__head" },
      h("p", { class: "eyebrow" }, "Scoring"),
      h("h2", { id: "hb-score-title" }, "Score your " + w.many),
      h("p", { class: "hb-hint" }, "Claude finds each " + w.one + "'s documents, then scores it from them, one " + w.one +
        " at a time. The Grid fills in as it goes, and you can pause at any point without losing anything.")),
    h("div", { class: "hb-dialog__body stack" },
      h("fieldset", { class: "hb-score__choices" }, h("legend", { class: "visually-hidden" }, "What to score"), few.node, pick.node, pickBox, all.node),
      cost),
    status,
    h("div", { class: "hb-dialog__foot" }, cancel, go));
    d.append(form);
    d.addEventListener("click", (ev) => { if (ev.target === d) d.close(); });
    d.addEventListener("close", () => { d.remove(); const back = document.getElementById("hb-next-go"); if (back) back.focus(); });
    (document.querySelector(".hb") || document.body).append(d);
    sync();
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
  }

  if (HB.addStyles) HB.addStyles("hb-nextstep", CSS);
  HB.nextStep = { node, openScore, start, step };
})((window.HB = window.HB || {}));
