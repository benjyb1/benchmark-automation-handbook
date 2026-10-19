/* =============================================================================
   Handbook viewer: views/rules.js, the Rules tab (HB.views.rules).
   The judgement calls the methodology leaves open, as a list beside one rule in
   full. Top: how many are decided (in words, and one bar segment per rule) and
   the sort (Stakes: points at most, most first; Question: the questions' order).
   Left: every rule, compact, each a link to #rules~<id>. Right: that rule, with
   its stakes, the cells it touches and the decision form. Below 40rem the two
   take turns: #rules is the list, #rules~R17 the rule with a link back.
   With no rule in the address the first open rule in the sort is shown.

   The rule itself, with its decision form, is HB.ruleEditor(ruleId, ctx, opts),
   which other views use inline too ({compact: true}). Deciding goes through
   HB.edits.decide(ruleId, option, text) (edits.js): the decision is saved for
   Claude to apply later, the rule reads "Waiting for Claude" and the tab moves to
   the next open rule. "Skip" saves a skip (HB.edits.skip) that apply-edits turns
   into status "skipped". A decided rule can be changed the same way ("Change
   decision"). With no HB.edits.ready the rule is read-only and says so. A slim tray at the foot counts
   what waits for Claude (HB.edits.pendingCounts()); on this tab it stands in for
   the shell's edits banner. No score preview: the page never works out what a
   decision would do to the scores.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, svg, fmtInt, plural, notice, store } = HB;

  const KIND = { scoring: "Scoring rule", scope: "Scope rule", evidence: "Evidence rule", instruction: "Standing instruction" };
  const RAISED = { setup: "Raised in set-up", trial: "Raised in the trial", run: "Raised during the run", owner: "Added by you" };
  /* A rule's state here. "waiting": a decision saved in this page that Claude has not applied yet. */
  const STATUS = {
    open: { tone: "warning", icon: "exclamation", word: "Open", short: "Open" },
    waiting: { tone: "accent", icon: "clock", word: "Waiting for Claude", short: "Waiting" },
    skipped: { tone: "neutral", icon: "dash", word: "Skipped", short: "Skipped" },
    decided: { tone: "good", icon: "tick", word: "Decided", short: "Decided" },
  };
  const NOT_READY = () => HB.cantSave();
  const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });
  const NARROW = "(width < 40rem)";

  HB.addStyles("rules", `
.hb:has(.hb-view[data-view="rules"]) #hb-banner { display: none; }
.rl { display: grid; gap: var(--s4); min-width: 0; }
.rl__intro { max-width: var(--measure); }
.rl-top { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: var(--s3) var(--s5); }
.rl-prog { display: grid; gap: var(--s2); flex: 1 1 16rem; max-width: 30rem; min-width: 0; }
.rl-prog__words { margin: 0; color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
.rl-prog__words strong { color: var(--ink); font-size: var(--fs-16); font-weight: 650; }
.rl-prog__bar { display: flex; gap: 2px; height: 0.5rem; }
.rl-prog__seg { flex: 1 1 0; border-radius: 2px; background: var(--rule); }
.rl-prog__seg[data-s="decided"] { background: var(--accent); }
.rl-prog__seg[data-s="waiting"] { background: repeating-linear-gradient(135deg, var(--accent) 0 2px, var(--accent-soft) 2px 4.5px); box-shadow: inset 0 0 0 1px var(--accent); }
.rl-prog__seg[data-s="skipped"] { background: repeating-linear-gradient(135deg, var(--ink-2) 0 1.5px, var(--surface) 1.5px 4px); box-shadow: inset 0 0 0 1px var(--ink-2); }
.rl-sort { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); color: var(--ink-2); font-size: var(--fs-14); }
.rl-panes { display: grid; grid-template-columns: minmax(15rem, 21rem) minmax(0, 1fr); align-items: start; gap: var(--s4); min-width: 0; }
.rl-list { position: sticky; top: var(--s3); max-height: calc(100dvh - 6.5rem); overflow-y: auto; margin: 0; padding: 0; list-style: none; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); scrollbar-width: thin; }
.rl-list > li + li { border-top: 1px solid var(--rule); }
.rl-item { display: grid; grid-template-columns: 1rem minmax(0, 1fr); gap: var(--s1) var(--s2); padding: var(--s3) var(--s3) var(--s3) var(--s3); color: var(--ink); text-decoration: none; border-inline-start: 3px solid transparent; }
.rl-item:hover { background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
.rl-item[aria-current="true"] { border-inline-start-color: var(--accent); background: var(--accent-soft); }
.rl-item:focus-visible { outline-offset: -2px; }
.rl-item__g { display: inline-flex; align-items: center; height: 1.25rem; }
.rl-item__head { display: flex; align-items: baseline; gap: var(--s2); min-width: 0; color: var(--ink-2); font-size: var(--fs-12); }
.rl-item__id { color: var(--ink); font-size: var(--fs-14); font-weight: 700; }
.rl-item__st { margin-inline-start: auto; font-weight: 600; white-space: nowrap; }
.rl-item[data-s="decided"] .rl-item__st, .rl-item[data-s="waiting"] .rl-item__st { color: var(--accent); }
.rl-item__ask, .rl-item__stakes { grid-column: 2; }
.rl-item__ask { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; font-size: var(--fs-14); line-height: 1.35; }
.rl-item:not([data-s="open"]) .rl-item__ask { color: var(--ink-2); }
.rl-item__stakes { display: flex; align-items: center; gap: var(--s2); min-width: 0; margin-top: 0.125rem; font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
.rl-track { flex: none; width: 4rem; height: 0.3rem; overflow: hidden; border-radius: 2px; background: var(--rule); }
.rl-track__fill { display: block; height: 100%; border-radius: 2px; background: var(--series-1); }
.rl-item__q { min-width: 0; overflow: hidden; color: var(--ink-2); text-overflow: ellipsis; white-space: nowrap; }
.rl-tone-accent { --tone: var(--accent); --on-tone: var(--on-accent); }
.rl-detail { display: grid; gap: var(--s4); min-width: 0; padding: var(--s4) var(--s5) var(--s5); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.rl-detail .rl-back { display: none; justify-self: start; margin: calc(-1 * var(--s2)) 0 0 calc(-1 * var(--s3)); }
.rl-rule__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); color: var(--ink-2); font-size: var(--fs-14); }
.rl-rule__id { color: var(--ink); font-weight: 700; }
.rl-rule__ask { max-width: 62ch; margin: 0; font-family: var(--serif); font-size: var(--fs-20); font-weight: 500; line-height: 1.35; text-wrap: pretty; overflow-wrap: anywhere; }
.rl-rule__ask:focus { outline: none; }
.rl-rule__ask:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.rl-rule__bg { max-width: 68ch; margin: calc(-1 * var(--s2)) 0 0; color: var(--ink-2); font-size: var(--fs-14); line-height: 1.5; overflow-wrap: anywhere; }
.rl-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); }
.rl-stats > div { display: flex; flex-direction: column-reverse; justify-content: flex-end; gap: 0.125rem; min-width: 0; padding: var(--s2) var(--s3); }
.rl-stats > div + div { border-inline-start: 1px solid var(--rule); }
.rl-stats dt { color: var(--ink-2); font-size: var(--fs-12); line-height: 1.3; }
.rl-stats dd { margin: 0; font-size: 1.125rem; font-weight: 650; line-height: 1.25; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.rl-stats dd.rl-stats__q { font-family: var(--mono); font-size: var(--fs-14); line-height: 1.6; }
.rl-cells { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s2); margin: calc(-1 * var(--s2)) 0 0 calc(-1 * var(--s3)); }
.rl-facts { margin: 0; font-size: var(--fs-14); overflow-wrap: anywhere; }
.rl-opts { display: grid; gap: var(--s2); min-width: 0; margin: 0; padding: 0; border: 0; list-style: none; }
.rl-legend { padding: 0; margin-bottom: var(--s2); font-size: var(--fs-16); font-weight: 650; }
.rl-opt { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; gap: var(--s2) var(--s3); padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); font-size: var(--fs-14); line-height: 1.45; overflow-wrap: anywhere; }
label.rl-opt { cursor: pointer; }
label.rl-opt:hover { border-color: var(--rule-strong); }
.rl-opt:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--accent); }
.rl-opt input[type="radio"] { width: 1.125rem; height: 1.125rem; margin: 0.15rem 0 0; accent-color: var(--accent); }
.rl-opt--plain { grid-template-columns: minmax(0, 1fr); }
.rl-opt__body { display: grid; gap: var(--s1); justify-items: start; min-width: 0; }
.rl-opt > .chip { justify-self: start; }
.rl-opt__id { display: inline-grid; place-items: center; min-width: 1.375rem; height: 1.375rem; margin-inline-end: var(--s2); border-radius: 3px; background: var(--paper); box-shadow: inset 0 0 0 1px var(--rule); font-family: var(--mono); font-size: var(--fs-12); font-weight: 700; }
.rl-rec { color: var(--accent); font-size: var(--fs-12); font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }
.rl-rec__now { margin-inline-start: var(--s1); color: var(--ink-2); font-weight: 600; letter-spacing: 0; text-transform: none; }
.rl-opt--other > .rl-opt__body { justify-items: stretch; }
.rl-opt__other { cursor: pointer; font-weight: 600; }
.rl-opt--other textarea.input { width: 100%; min-height: 3.25rem; resize: vertical; }
.rl-ed { display: grid; gap: var(--s4); min-width: 0; }
.rl-ed--compact { gap: var(--s3); }
.rl-ed__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); margin: 0; color: var(--ink-2); font-size: var(--fs-12); }
.rl-ed__ask { margin: 0; font-size: var(--fs-14); font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.rl-ed--compact .rl-opt { padding-block: var(--s1); }
.rl-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s3); }
.rl-hint { margin: 0; color: var(--ink-2); font-size: var(--fs-12); }
.rl-problem:empty { display: none; }
.rl-decision { display: grid; gap: var(--s1); padding: var(--s3); border-radius: var(--radius); background: var(--accent-soft); overflow-wrap: anywhere; }
.rl-decision p { margin: 0; }
.rl-details > summary { cursor: pointer; font-size: var(--fs-14); font-weight: 600; }
.rl-details[open] > summary { margin-bottom: var(--s2); }
.rl-last { padding: var(--s2) var(--s3); border: 1px dashed var(--rule-strong); border-radius: var(--radius); font-size: var(--fs-14); overflow-wrap: anywhere; }
.rl-next { justify-self: start; }
.rl-tray { position: sticky; bottom: 0; z-index: 2; display: flex; align-items: center; gap: var(--s2) var(--s3); min-height: 2.75rem; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); box-shadow: 0 -0.5rem 1rem -0.75rem var(--shadow); font-size: var(--fs-14); }
.rl-tray p { margin: 0; min-width: 0; color: var(--ink-2); }
.rl-tray strong { color: var(--ink); }
@media (pointer: coarse) { .rl-opt { padding-block: var(--s3); } }
@media (width < 40rem) {
  .rl-panes { grid-template-columns: minmax(0, 1fr); }
  .rl-list { position: static; max-height: none; overflow: visible; }
  .rl[data-pick="list"] .rl-detail { display: none; }
  .rl[data-pick="rule"] .rl-list, .rl[data-pick="rule"] .rl-top, .rl[data-pick="rule"] .rl__intro { display: none; }
  .rl-detail .rl-back { display: inline-flex; }
  .rl-detail { padding: var(--s3) var(--s3) var(--s4); }
  .rl-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rl-stats > div:nth-child(3) { border-inline-start: 0; }
  .rl-stats > div:nth-child(n + 3) { border-top: 1px solid var(--rule); }
  .rl-stats dd { font-size: var(--fs-16); }
  .rl-rule__ask { font-size: 1.125rem; }
  .rl-actions > .btn { flex: 1 1 auto; }
  .rl-tray { font-size: var(--fs-12); }
}
@media (forced-colors: active) {
  .rl-item[aria-current="true"] { outline: 2px solid Highlight; outline-offset: -2px; }
  .rl-prog__seg, .rl-track { border: 1px solid CanvasText; }
  .rl-prog__seg[data-s="decided"], .rl-track__fill { background: CanvasText; forced-color-adjust: none; }
}
`);

  /* What the reader has typed, chosen and passed over survives a redraw of the tab. */
  const ui = {
    choice: {}, text: {}, changing: {},
    passed: new Set(),     // rules skipped in this visit: "next open rule" goes past them
    sort: null,
    flash: null,           // {id, kind: "recorded" | "skipped"} shown once on the next rule
    after: null,           // what to focus once drawn: "item", "head" or "back"
    listScroll: 0,
    shown: null,           // the rule last drawn in the right-hand pane
  };

  const edits = () => (HB.edits && HB.edits.ready ? HB.edits : null);
  const slugOf = (id) => String(id).replace(/[^A-Za-z0-9_-]/g, "_");
  const narrow = () => typeof window.matchMedia === "function" && window.matchMedia(NARROW).matches;
  const pointsOf = (rule) => (rule.stakes && HB.isNum(rule.stakes.points) ? rule.stakes.points : null);

  /* The latest saved entry for a rule that Claude has not applied yet: a decision
     (pendingOf) or a skip (skipPendingOf), never both. */
  function latestOf(rule) {
    return HB.edits && typeof HB.edits.pendingDecision === "function" ? HB.edits.pendingDecision(rule.id) : null;
  }

  function pendingOf(rule) {
    const d = latestOf(rule);
    return d && d.skip !== true ? d : null;
  }

  function skipPendingOf(rule) {
    const d = latestOf(rule);
    return d && d.skip === true ? d : null;
  }

  function statusOf(rule) {
    if (pendingOf(rule)) return "waiting";
    if (rule.status === "decided") return "decided";
    return rule.status === "skipped" || skipPendingOf(rule) ? "skipped" : "open";
  }

  /* ---- What a rule touches ------------------------------------------------------- */

  function cellsOf(rule) {
    const b = HB.data.bundle() || {};
    const rows = Array.isArray(rule.rows) && rule.rows.length ? rule.rows : (b.units || []).map((u) => u.id);
    const out = [];
    (rule.question_ids || []).forEach((qid) => rows.forEach((row) => { if (HB.data.applies(row, qid)) out.push([row, qid]); }));
    return out;
  }

  /* Cells whose answer cites the rule: for an undecided rule, the answers resting on Claude's reading. */
  function restingByRule(bundle) {
    const out = new Map();
    const cells = (bundle && bundle.cells) || {};
    Object.keys(cells).forEach((row) => Object.keys(cells[row] || {}).forEach((qid) => {
      const c = cells[row][qid];
      (c && Array.isArray(c.rules) ? c.rules : []).forEach((rid) => {
        if (!out.has(rid)) out.set(rid, []);
        out.get(rid).push([row, qid]);
      });
    }));
    return out;
  }

  function questionOrder(bundle) {
    const order = new Map();
    ((bundle && bundle.questions) || []).forEach((q, i) => { if (q && q.id != null) order.set(String(q.id), i); });
    return order;
  }

  /* "All 16 questions", or runs in the questions' own order: "1.1–1.8", "1.7, 2.6". */
  function questionsWords(rule, order) {
    const ids = Array.from(new Set((rule.question_ids || []).map(String)));
    const total = order.size;
    if (total && ids.length >= total && ids.every((q) => order.has(q))) return { all: true, text: "All " + fmtInt(total), n: ids.length };
    const known = ids.filter((q) => order.has(q)).sort((a, b) => order.get(a) - order.get(b));
    const parts = [];
    for (let i = 0; i < known.length;) {
      let j = i;
      while (j + 1 < known.length && order.get(known[j + 1]) === order.get(known[j]) + 1) j += 1;
      if (j - i >= 2) parts.push(known[i] + "–" + known[j]);
      else for (let k = i; k <= j; k += 1) parts.push(known[k]);
      i = j + 1;
    }
    ids.filter((q) => !order.has(q)).forEach((q) => parts.push(q));
    return { all: false, text: parts.join(", ") || "None", n: ids.length };
  }

  /* The rule's question in a line: the bundle's own short question when it has one, else the
     run of sentences in the ask that end in a question mark (the full ask is shown under it). */
  function headline(rule) {
    if (typeof rule.question === "string" && rule.question.trim()) return rule.question.trim();
    const ask = String(rule.ask || "").trim();
    const sentences = ask.split(/(?<!\b(?:e\.g|i\.e|etc|vs|cf)\.)(?<=[.?!]['"’”)]?)\s+(?=[A-Z'"‘“(])/u);
    const first = sentences.findIndex((s) => /\?['"’”)]?$/u.test(s));
    if (first < 0) return ask;
    let last = first;
    while (last + 1 < sentences.length && /\?['"’”)]?$/u.test(sentences[last + 1])) last += 1;
    return sentences.slice(first, last + 1).join(" ");
  }

  function sortRules(rules, by, order) {
    const qKey = (r) => Math.min(...(r.question_ids || []).map((q) => (order.has(String(q)) ? order.get(String(q)) : 1e6)), 1e6);
    const cells = new Map(rules.map((r) => [r.id, cellsOf(r).length]));
    return rules.slice().sort((a, b) => {
      if (by === "question") {
        return qKey(a) - qKey(b) || (a.question_ids || []).length - (b.question_ids || []).length || collator.compare(String(a.id), String(b.id));
      }
      const pa = pointsOf(a), pb = pointsOf(b);
      return (pb == null ? -1 : pb) - (pa == null ? -1 : pa) || cells.get(b.id) - cells.get(a.id) || collator.compare(String(a.id), String(b.id));
    });
  }

  function nextOpen(order, from) {
    const i = order.findIndex((r) => r.id === from);
    for (let k = 1; k < order.length; k += 1) {
      const r = order[(i + k) % order.length];
      if (r.id !== from && statusOf(r) === "open" && !ui.passed.has(r.id)) return r;
    }
    return null;
  }

  /* ---- Small parts ------------------------------------------------------------------- */

  function stIcon(st) {
    const s = STATUS[st] || STATUS.open;
    const icon = s.icon === "clock"
      ? svg("svg", { class: "status-icon", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" },
        svg("circle", { class: "status-icon__disc", cx: 8, cy: 8, r: 8 }),
        svg("path", { class: "status-icon__glyph", d: "M8 4.4V8l2.4 1.6" }))
      : HB.icon(s.icon);
    return icon;
  }

  function toneAttrs(st, cls) {
    const s = STATUS[st] || STATUS.open;
    return s.tone === "accent" ? { class: [cls, "rl-tone-accent"] } : { class: cls, "data-tone": s.tone };
  }

  function chip(st) {
    const s = STATUS[st] || STATUS.open;
    return h("span", toneAttrs(st, "chip"), stIcon(st), h("span", { class: "chip__word" }, s.word));
  }

  const optionText = (rule, id) => {
    const o = (rule.options || []).find((x) => x.id === id);
    return o ? o.text : null;
  };

  function dateOf(stamp) {
    try {
      const d = new Date(stamp);
      return Number.isNaN(d.getTime()) ? null : DATE.format(d);
    } catch (err) {
      return null;
    }
  }

  function whenWhere(decision) {
    const when = dateOf(decision.at);
    const where = decision.via === "viewer" ? "in this viewer" : decision.via === "chat" ? "with Claude" : null;
    if (!when && !where) return "When and where it was decided is not recorded.";
    return "Decided" + (when ? " on " + when : "") + (where ? ", " + where : "") + (when ? "" : " (date not recorded)") + ".";
  }

  /* "Recorded, waiting for Claude to re-run N cells": N is the cells of this rule that already
     hold an answer; the rest have not been scored yet and are scored under the new rule. */
  function recordedNote(rule, cells, pending) {
    const scored = cells.filter(([row, qid]) => HB.data.isScored(HB.data.cell(row, qid))).length;
    const wording = pending.text || optionText(rule, pending.option) || "";
    const head = scored > 0
      ? "Recorded, waiting for Claude to re-run " + plural(scored, "cell")
      : "Recorded. Claude will use it when it scores " + plural(cells.length, "cell");
    const rest = scored > 0 && scored < cells.length
      ? [" (the other " + fmtInt(cells.length - scored) + (cells.length - scored === 1 ? " has" : " have") + " not been scored yet)"] : [];
    const when = dateOf(pending.at);
    return notice("good", [[h("strong", null, head + "."), ...rest],
      wording ? ["Your decision: " + wording] : "",
      when ? h("span", { class: "small muted" }, "Recorded on " + when + ".") : ""].filter(Boolean));
  }

  function optionBody(rule, o, open) {
    return h("span", { class: "rl-opt__body" },
      h("span", null, h("span", { class: "rl-opt__id", "aria-hidden": "true" }, String(o.id)),
        h("span", { class: "visually-hidden" }, "Option " + o.id + ": "), o.text),
      o.id === rule.recommended
        ? h("span", { class: "rl-rec" }, "Claude's recommendation", open ? h("span", { class: "rl-rec__now" }, "· used until you decide") : null)
        : null);
  }

  /* ---- The rule editor: HB.ruleEditor(ruleId, ctx, opts) ------------------------------
     One rule and its decision, for this tab and for any view that shows a rule inline (the
     cell panel, the compare page, the step briefs). The options are radios with Claude's
     recommendation marked and the current decision chosen; "Other" takes the owner's own
     wording. Record decision and Skip save through HB.edits.decide and HB.edits.skip. A
     decided rule shows its decision and "Change decision", which opens the form again on
     the current choice. opts.compact leaves out the stakes and the long text (the
     background, why Claude suggests it, last edition); opts.onDone(kind) runs after a save
     ("recorded" or "skipped"), and without it the editor draws itself again in place.
     Without HB.edits.ready it is read-only and says so (NOT_READY). ctx may be null. */

  let seq = 0;

  function forget(key) {
    delete ui.choice[key];
    delete ui.text[key];
    delete ui.changing[key];
  }

  function waitingChip() {
    return h("span", toneAttrs("waiting", "chip"), stIcon("waiting"), h("span", { class: "chip__word" }, "Your decision, waiting for Claude"));
  }

  function optionsList(rule, decided, pending, open) {
    return h("ul", { class: "rl-opts", "aria-label": "Options" }, (rule.options || []).map((o) => h("li", { class: "rl-opt rl-opt--plain" },
      optionBody(rule, o, open),
      decided && o.id === rule.decision.option ? HB.statusChip("good", "Chosen") : null,
      pending && o.id === pending.option ? waitingChip() : null)));
  }

  /* mode "decide": an open or skipped rule; "change": a decided rule, or a decision waiting for Claude. */
  function editorForm(rule, uid, mode, done, redraw) {
    const key = rule.id;
    const decided = rule.status === "decided" && rule.decision && typeof rule.decision === "object";
    const pending = pendingOf(rule);
    const open = !decided && !pending;
    const problem = h("div", { class: "rl-problem", id: uid + "-problem", "aria-live": "polite" });
    const pick = (value) => { ui.choice[key] = value; };
    const radio = (id, value) => {
      const input = h("input", { type: "radio", name: uid + "-choice", id, value, onchange: () => pick(value) });
      input.checked = ui.choice[key] === value;
      return input;
    };
    const choices = (rule.options || []).map((o) => {
      const id = uid + "-opt-" + slugOf(o.id);
      return h("label", { class: "rl-opt", for: id }, radio(id, o.id), optionBody(rule, o, open));
    });
    const ownId = uid + "-own";
    const own = radio(ownId, "own");
    const area = h("textarea", { class: "input", id: uid + "-text", rows: 2, placeholder: "Write the rule as a test a scorer can apply",
      "aria-label": "Other: your own wording for " + rule.id });
    area.value = ui.text[key] || "";
    area.addEventListener("input", () => {
      ui.text[key] = area.value;
      if (area.value.trim() && !own.checked) {
        own.checked = true;
        pick("own");
      }
    });
    choices.push(h("div", { class: "rl-opt rl-opt--other" }, own,
      h("span", { class: "rl-opt__body" }, h("label", { class: "rl-opt__other", for: ownId }, "Other"), area)));

    const record = h("button", { type: "button", class: "btn btn--primary", id: uid + "-record" }, "Record decision");
    record.addEventListener("click", async () => {
      const chosen = ui.choice[key];
      const words = (ui.text[key] || "").trim();
      problem.replaceChildren();
      if (!chosen) return problem.replaceChildren(notice("critical", "Choose an option, or choose Other and write your own wording."));
      if (chosen === "own" && !words) {
        problem.replaceChildren(notice("critical", "Write your own wording in the Other box, or choose one of the options."));
        area.focus();
        return;
      }
      const now = pending || (decided ? rule.decision : null);
      if (mode === "change" && now && (chosen === "own" ? !now.option && (now.text || "").trim() === words : now.option === chosen)) {
        problem.replaceChildren(notice("neutral", "That is the decision already recorded. Choose another, or keep it."));
        return;
      }
      record.disabled = true;
      record.textContent = "Recording";
      try {
        await HB.edits.decide(rule.id, chosen === "own" ? null : chosen, chosen === "own" ? words : null);
      } catch (err) {
        record.disabled = false;
        record.textContent = "Record decision";
        problem.replaceChildren(notice("critical", err && err.message ? err.message : "This could not be recorded."));
        return;
      }
      forget(key);
      ui.passed.delete(key);
      done("recorded");
    });

    const canSkip = mode === "decide" && rule.status === "open" && !skipPendingOf(rule);
    let second = null;
    if (mode === "change") {
      second = h("button", { type: "button", class: "btn", id: uid + "-keep", onclick: () => { forget(key); redraw("change"); } },
        pending ? "Keep my recorded decision" : "Keep the current decision");
    } else if (canSkip) {
      second = h("button", { type: "button", class: "btn", id: uid + "-skip", "aria-describedby": uid + "-skip-hint",
        onclick: async (ev) => {
          problem.replaceChildren();
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            await HB.edits.skip(rule.id);
          } catch (err) {
            btn.disabled = false;
            problem.replaceChildren(notice("critical", err && err.message ? err.message : "The skip could not be recorded."));
            return;
          }
          forget(key);
          ui.passed.add(key);
          done("skipped");
        } }, "Skip, keep Claude's reading");
    }

    return h("fieldset", { class: "rl-opts rl-form" },
      h("legend", { class: "rl-legend" }, mode === "change" ? "Change the decision" : "Your decision"),
      choices,
      HB.edits && HB.edits.canWrite && HB.edits.canWrite() === false
        ? notice("warning", "You have view-only access to this page, so a decision will probably be refused.") : null,
      problem,
      h("div", { class: "rl-actions" }, record, second),
      canSkip ? h("p", { class: "rl-hint", id: uid + "-skip-hint" },
        "Skipping tells Claude you are leaving this rule for now: Claude keeps its reading, and you can still decide later.") : null);
  }

  function ruleEditor(ruleId, ctx, opts) {
    const o = opts || {};
    const compact = Boolean(o.compact);
    const bundle = (ctx && ctx.bundle) || HB.data.bundle() || {};
    const rule = (bundle.rules || []).find((r) => r && String(r.id) === String(ruleId));
    const box = h(compact ? "section" : "div", { class: compact ? "rl-ed rl-ed--compact" : "rl-ed", "aria-label": compact && rule ? "Rule " + rule.id : null });
    if (!rule) {
      box.append(h("p", { class: "muted" }, "There is no rule " + String(ruleId) + " in this benchmark."));
      return box;
    }
    seq += 1;
    const slug = "rule-" + slugOf(rule.id);
    const uid = compact ? slug + "-c" + seq : slug;
    const st = statusOf(rule);
    const decided = rule.status === "decided" && rule.decision && typeof rule.decision === "object";
    const pending = pendingOf(rule);
    const awaiting = !decided && !pending;
    const live = edits();
    const changing = Boolean(ui.changing[rule.id]) && Boolean(live);
    const redraw = (focus) => {
      const fresh = ruleEditor(ruleId, ctx, o);
      box.replaceWith(fresh);
      const target = focus === "form" ? fresh.querySelector(".rl-form input:checked") || fresh.querySelector(".rl-form input")
        : focus === "change" ? fresh.querySelector(".rl-change") : null;
      if (target) target.focus();
      return fresh;
    };
    const done = (kind) => {
      if (typeof o.onDone === "function") o.onDone(kind);
      else redraw("change");
    };
    const line = headline(rule);

    if (compact) {
      box.append(
        h("p", { class: "rl-ed__meta" }, h("span", { class: "mono rl-rule__id" }, String(rule.id)), h("span", null, KIND[rule.kind] || "Rule"), chip(st)),
        h("p", { class: "rl-ed__ask" }, line || "No question recorded"));
    } else {
      const cells = cellsOf(rule);
      const resting = restingByRule(bundle).get(rule.id) || [];
      const ask = String(rule.ask || "").trim();
      /* Under the question, the rest of the ask; all of it when the question is not where it starts. */
      const background = !ask || ask === line ? "" : ask.startsWith(line) ? ask.slice(line.length).trim() : ask;
      const qs = questionsWords(rule, questionOrder(bundle));
      const pts = pointsOf(rule);
      const stat = (value, label, cls) => h("div", null, h("dt", null, label), h("dd", { class: cls }, value));
      const highlight = (list) => (ctx && typeof ctx.highlight === "function" ? ctx.highlight(list) : HB.highlight(list));
      const show = (list, words) => h("button", {
        type: "button", class: "btn btn--quiet btn--small", disabled: list.length === 0,
        "aria-label": words + " in the grid",
        onclick: () => highlight(list),
      }, words);
      box.append(...[
        h("div", { class: "rl-rule__meta" },
          h("span", { class: "mono rl-rule__id" }, String(rule.id)),
          h("span", null, KIND[rule.kind] || "Rule"),
          RAISED[rule.raised_by] ? h("span", null, RAISED[rule.raised_by]) : null,
          chip(st)),
        h("h3", { class: "rl-rule__ask", id: slug + "-ask", tabindex: "-1" }, line || "No question recorded"),
        background ? h("p", { class: "rl-rule__bg" }, background) : null,
        h("dl", { class: "rl-stats" },
          stat(fmtInt(cells.length), cells.length === 1 ? "cell affected" : "cells affected"),
          stat(pts == null ? "Not counted" : fmtInt(pts), "points at most"),
          stat(fmtInt(resting.length), awaiting ? "rest on Claude's reading now" : "answers cite this rule"),
          stat(qs.text, qs.n === 1 ? "question" : "questions", qs.all ? null : "rl-stats__q")),
        h("p", { class: "rl-cells" },
          show(cells, "Show " + plural(cells.length, "cell")),
          awaiting && resting.length && resting.length !== cells.length
            ? show(resting, "Show the " + fmtInt(resting.length) + " resting on Claude's reading") : null),
      ].filter(Boolean));
    }

    if (awaiting && rule.recommended == null) {
      box.append(h("p", { class: "rl-facts" }, h("strong", null, "Used for now: "), "Claude's own reading of the rule (no option is recommended)."));
    }
    if (awaiting && skipPendingOf(rule)) {
      box.append(h("p", { class: "rl-facts" }, h("strong", null, "Skip recorded, waiting for Claude: "), "Claude's reading stays in use until you decide."));
    } else if (awaiting && rule.status === "skipped") {
      box.append(h("p", { class: "rl-facts" }, h("strong", null, "Skipped: "), "Claude's reading stays in use until you decide."));
    }
    if (decided) {
      box.append(h("div", { class: "rl-decision" },
        h("p", null, h("strong", null, pending ? "Decision in use until Claude applies yours: " : rule.kind === "instruction" ? "Instruction: " : "Decision: "),
          rule.decision.text || optionText(rule, rule.decision.option) || "Not recorded"),
        h("p", { class: "muted small" }, whenWhere(rule.decision)
          + (rule.decision.option == null && (rule.options || []).length ? " Own wording, not one of the options." : ""))));
    }

    const changeButton = (word) => h("div", { class: "rl-actions" }, h("button", {
      type: "button", class: "btn btn--small rl-change", id: uid + "-change",
      onclick: () => {
        const now = pending || rule.decision || {};
        ui.changing[rule.id] = true;
        ui.choice[rule.id] = now.option || "own";
        ui.text[rule.id] = now.option ? "" : now.text || "";
        redraw("form");
      },
    }, word));

    if (live && (changing || awaiting)) {
      box.append(editorForm(rule, uid, awaiting ? "decide" : "change", done, redraw));
    } else if (pending) {
      if (!compact) box.append(optionsList(rule, decided, pending, false));
      box.append(recordedNote(rule, cellsOf(rule), pending));
      if (live) box.append(changeButton("Change my decision"));
    } else if (decided) {
      if (!compact && (rule.options || []).length) box.append(h("details", { class: "rl-details" }, h("summary", null, "Show the options"), optionsList(rule, true, null, false)));
      if (live) box.append(changeButton(rule.kind === "instruction" ? "Change the instruction" : "Change decision"));
    } else {
      box.append(h("div", { class: "rl-opts" },
        compact ? null : h("h4", { class: "rl-legend" }, "Options"),
        optionsList(rule, false, null, true)));
    }
    if (!live) box.append(notice("neutral", NOT_READY()));

    if (!compact && rule.why) box.append(h("p", { class: "small muted" }, h("strong", null, "Why Claude suggests it: "), rule.why));
    if (!compact && rule.last_year_note) box.append(h("p", { class: "rl-last" }, h("strong", null, "Last edition: "), rule.last_year_note));
    return box;
  }

  HB.ruleEditor = ruleEditor;
  /* The cells a rule touches and those whose answer cites it, as [[row, qid], ...]; the corner
     chat (views/chat.js) reads them to explain a rule and to ask for its re-run. */
  HB.ruleCells = {
    touched: (rule) => cellsOf(rule),
    resting: (ruleId) => restingByRule(HB.data.bundle()).get(String(ruleId)) || [],
    status: (rule) => statusOf(rule),
  };

  /* ---- One rule, in full ------------------------------------------------------------ */

  function detail(rule, ctx, order) {
    const st = statusOf(rule);
    const live = edits();
    const slug = "rule-" + slugOf(rule.id);
    const box = h("article", { class: "rl-detail", id: slug, "aria-labelledby": slug + "-ask" });

    const flash = ui.flash;
    ui.flash = null;
    let flashNote = null;
    if (flash && flash.id !== rule.id) {
      flashNote = flash.kind === "recorded"
        ? notice("good", "Your decision on " + flash.id + " is recorded and waits for Claude. Next open rule:")
        : notice("neutral", "Your skip of " + flash.id + " is recorded and waits for Claude; Claude keeps its reading. Next open rule:");
    } else if (flash && flash.last) {
      flashNote = flash.kind === "recorded"
        ? notice("good", "Your decision on " + flash.id + " is recorded and waits for Claude. No other open rules are left.")
        : notice("neutral", "Your skip of " + flash.id + " is recorded and waits for Claude. No other open rules are left.");
    }

    /* After a save the tab moves on to the next open rule. */
    const onDone = (kind) => {
      const next = nextOpen(order, rule.id);
      ui.flash = { id: rule.id, kind, last: !next };
      ui.after = "head";
      ctx.go("rules", next ? next.id : rule.id);
    };

    box.append(...[
      h("a", { class: "btn btn--quiet btn--small rl-back", href: ctx.href("rules"), id: "rules-back",
        onclick: () => { ui.after = "back"; } }, "← All rules"),
      flashNote,
      ruleEditor(rule.id, ctx, { onDone }),
    ].filter(Boolean));

    const deciding = live && (st === "open" || st === "skipped");
    const next = nextOpen(order, rule.id);
    if (next && !deciding) {
      box.append(h("a", { class: "btn btn--small rl-next", href: ctx.href("rules", next.id), id: "rules-next",
        onclick: () => { ui.after = "head"; } }, "Next open rule: " + next.id + " →"));
    } else if (!next && (st === "decided" || st === "waiting")) {
      box.append(h("p", { class: "small muted" }, "No open rules left."));
    }
    return box;
  }

  /* ---- The list -------------------------------------------------------------------- */

  function item(rule, ctx, selected, model) {
    const st = statusOf(rule);
    const pts = pointsOf(rule);
    const qs = questionsWords(rule, model.qOrder);
    const width = pts == null ? 0 : (100 * pts) / model.maxPoints;
    return h("li", null, h("a", {
      class: "rl-item", href: ctx.href("rules", rule.id), "data-s": st, "data-id": rule.id,
      "aria-current": selected ? "true" : null,
      onclick: (ev) => {
        const list = ev.currentTarget.closest(".rl-list");
        ui.listScroll = list ? list.scrollTop : 0;
        ui.after = narrow() ? "head" : "item";
      },
    },
    h("span", toneAttrs(st, "rl-item__g"), stIcon(st)),
    h("span", { class: "rl-item__head" },
      h("span", { class: "mono rl-item__id" }, String(rule.id)),
      h("span", null, KIND[rule.kind] || "Rule"),
      h("span", { class: "rl-item__st" }, STATUS[st].short)),
    h("span", { class: "rl-item__ask" }, headline(rule) || "No question recorded"),
    h("span", { class: "rl-item__stakes" },
      h("span", { class: "rl-track", "aria-hidden": "true" }, h("span", { class: "rl-track__fill", style: { width: width.toFixed(1) + "%" } })),
      h("span", null, pts == null ? "Points not counted" : plural(pts, "point")),
      h("span", { class: "rl-item__q", title: (rule.question_ids || []).join(", ") },
        qs.all ? qs.text + " questions" : qs.text))));
  }

  function progress(order) {
    const t = { open: 0, waiting: 0, skipped: 0, decided: 0 };
    const segs = order.map((r) => {
      const st = statusOf(r);
      t[st] += 1;
      return h("span", { class: "rl-prog__seg", "data-s": st, title: r.id + ": " + STATUS[st].word });
    });
    const words = [h("strong", null, fmtInt(t.decided) + " of " + fmtInt(order.length)), " decided"];
    if (t.waiting) words.push(" · " + fmtInt(t.waiting) + " waiting for Claude");
    if (t.skipped) words.push(" · " + fmtInt(t.skipped) + " skipped");
    words.push(" · " + fmtInt(t.open) + " open");
    const label = fmtInt(t.decided) + " of " + fmtInt(order.length) + " rules decided, " + fmtInt(t.waiting) + " waiting for Claude, "
      + fmtInt(t.skipped) + " skipped, " + fmtInt(t.open) + " open";
    return h("div", { class: "rl-prog" },
      h("p", { class: "rl-prog__words", id: "rules-count" }, words),
      h("div", { class: "rl-prog__bar", role: "img", "aria-label": label }, segs));
  }

  function sortControl(ctx) {
    const button = (value, word) => h("button", {
      type: "button", class: "btn", "aria-pressed": String(ui.sort === value), id: "rules-sort-" + value,
      onclick: () => {
        if (ui.sort === value) return;
        ui.sort = value;
        const b = HB.data.bundle() || {};
        store.set("rules-sort:" + ((b.project || {}).slug || ""), value);
        ui.listScroll = 0;
        ctx.render();
      },
    }, word);
    return h("div", { class: "rl-sort" }, h("span", { id: "rules-sort-label" }, "Sort by"),
      h("div", { class: "segmented", role: "group", "aria-labelledby": "rules-sort-label" },
        button("stakes", "Stakes"), button("question", "Question")));
  }

  /* ---- The tray: what waits for Claude ------------------------------------------------ */

  function trayWords() {
    let counts = { edits: 0, decisions: 0 };
    try {
      if (HB.edits && typeof HB.edits.pendingCounts === "function") counts = HB.edits.pendingCounts();
      else if (HB.edits && typeof HB.edits.pending === "function") counts = { edits: Number(HB.edits.pending()) || 0, decisions: 0 };
    } catch (err) {
      console.error("HB.edits.pendingCounts failed:", err);
    }
    const d = counts.decisions || 0;
    const e = counts.edits || 0;
    if (d + e === 0) return { n: 0, content: ["Nothing waiting for Claude."] };
    const what = [d ? plural(d, "decision") : null, e ? plural(e, "edit") : null].filter(Boolean).join(" and ");
    return { n: d + e, content: HB.when(
      [h("strong", null, what + " being applied."), " This page updates when " + (d + e === 1 ? "it is" : "they are") + " done."],
      [h("strong", null, what + " waiting for Claude."), " Claude applies " + (d + e === 1 ? "it" : "them") + " the next time it works on this project."]) };
  }

  function fillTray(tray) {
    const t = trayWords();
    tray.dataset.n = String(t.n);
    tray.replaceChildren(
      h("span", toneAttrs(t.n ? "waiting" : "skipped", "rl-tray__g"), stIcon("waiting")),
      h("p", null, t.content));
  }

  /* edits.js redraws the shell's banner whenever the count of saved edits changes; follow it. */
  let watching = false;
  function watchBanner() {
    if (watching || typeof MutationObserver !== "function") return;
    const banner = document.getElementById("hb-banner");
    if (!banner) return;
    watching = true;
    new MutationObserver(() => {
      const tray = document.getElementById("rules-tray");
      if (tray) fillTray(tray);
    }).observe(banner, { childList: true, subtree: true });
  }

  /* ---- The tab -------------------------------------------------------------------------- */

  function settle(root, sel, pick) {
    const after = ui.after;
    ui.after = null;
    const changed = ui.shown !== sel;
    ui.shown = sel;
    setTimeout(() => {
      const list = root.querySelector(".rl-list");
      const current = sel ? root.querySelector('.rl-item[data-id="' + CSS.escape(sel) + '"]') : null;
      if (list && list.scrollHeight > list.clientHeight) {
        list.scrollTop = ui.listScroll;
        if (current) {
          const lt = list.getBoundingClientRect();
          const it = current.getBoundingClientRect();
          const tray = root.querySelector(".rl-tray");
          const bottom = Math.min(lt.bottom, window.innerHeight - (tray ? tray.offsetHeight + 8 : 0));
          const top = Math.max(lt.top, 0);
          if (it.top < top || it.bottom > bottom) list.scrollTop += it.top - top - Math.max(0, (bottom - top - it.height) / 2);
        }
      }
      if (pick === "list" && narrow()) {
        if (after === "back" && current) {
          current.scrollIntoView({ block: "center" });
          current.focus({ preventScroll: true });
        }
        return;
      }
      const panes = root.querySelector(narrow() ? ".rl-detail" : ".rl-panes");
      if (changed && panes && ui.shown && panes.getBoundingClientRect().top < 0) {
        window.scrollBy(0, panes.getBoundingClientRect().top - 8);
      }
      if (after === "item" && current) current.focus({ preventScroll: true });
      if (after === "head") {
        const head = root.querySelector(".rl-rule__ask");
        if (head) head.focus({ preventScroll: true });
      }
    }, 0);
  }

  function render(root, ctx) {
    const bundle = ctx.bundle || HB.data.bundle() || {};
    const rules = (bundle.rules || []).filter((r) => r && r.id != null);
    root.append(h("h2", null, "Rules"));
    if (!rules.length) {
      root.append(h("p", { class: "muted", id: "rules-count" }, "No rules have been raised yet."));
      return;
    }
    if (!ui.sort) {
      const kept = store.get("rules-sort:" + ((bundle.project || {}).slug || ""), "stakes");
      ui.sort = kept === "question" ? "question" : "stakes";
    }
    const model = {
      qOrder: questionOrder(bundle),
      maxPoints: Math.max(1, ...rules.map((r) => pointsOf(r) || 0)),
    };
    const order = sortRules(rules, ui.sort, model.qOrder);
    const asked = ctx.route && ctx.route.parts && ctx.route.parts.length ? String(ctx.route.parts[0]) : null;
    const routed = asked != null ? order.find((r) => String(r.id) === asked) || null : null;
    const chosen = routed
      || (ui.shown ? order.find((r) => r.id === ui.shown) : null)
      || order.find((r) => statusOf(r) === "open" && !ui.passed.has(r.id))
      || order.find((r) => statusOf(r) === "open")
      || order[0];
    const pick = routed ? "rule" : "list";

    const list = h("ol", { class: "rl-list", "aria-label": "Rules" }, order.map((r) => item(r, ctx, r.id === chosen.id, model)));
    list.addEventListener("scroll", () => { ui.listScroll = list.scrollTop; }, { passive: true });
    const tray = h("section", { class: "rl-tray", id: "rules-tray", role: "status", "aria-label": "Waiting for Claude" });
    fillTray(tray);

    root.append(h("div", { class: "rl", "data-pick": pick },
      h("p", { class: "rl__intro muted" }, "Judgement calls the methodology leaves open. Until you decide one, Claude uses its recommendation."),
      h("div", { class: "rl-top" }, progress(order), sortControl(ctx)),
      h("div", { class: "rl-panes" }, list, detail(chosen, ctx, order)),
      tray));
    watchBanner();
    settle(root, chosen.id, pick);
  }

  (HB.views = HB.views || {}).rules = { id: "rules", label: "Rules", order: 20, render, visible: () => true };
})((window.HB = window.HB || {}));
