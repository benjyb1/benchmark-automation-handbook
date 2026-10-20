/* =============================================================================
   Handbook viewer: views/cell.js, the cell panel. Sets HB.cellPanel, which the
   shell shows in the drawer for the route #grid~<row>~<qid>: docked beside the
   grid from 64rem wide (the grid stays usable), a modal right-hand panel from
   40rem, a bottom sheet on phones.

   In the head, under the title: Previous and Next through the question's column
   (rows in the grid's display order, HB.gridRows() when the grid sets it), the
   position ("3 of 13 in 1.2") and a "Flagged only" switch that makes Previous
   and Next skip cells with no factual flag. J and K (and the down and up arrows
   outside text fields and the grid) do the same while the panel is open. A step
   replaces the address rather than adding a history entry, and so does a click
   on another grid cell while the panel is open, so closing still goes back to
   the grid. The open cell's grid link gets the class is-open.

   The body, in the order an owner asks the questions: the answer (points and the
   level's name, its full meaning a click away); why (Claude's reasoning, the
   scorer's note from the row's evidence file, labelled as Claude's); what would
   change it (each rule the answer rests on, in plain words: open and skipped ones
   with the decision inline, HB.ruleEditor or a link to the Rules tab, and "Would
   be 5 of 5 if you decide this the other way" when the note states the other
   reading's answer for that one rule; decided ones as "This follows your
   decision"); the flags, each a sentence on what it means for the answer and what
   to do (a quote not found names which quote, a click away); the quote (one at a
   time when there are several; the English with the original a toggle away when
   it was translated) and whether it was found; the source (title, site, date,
   page, links); the saved text of the cited page with the quote marked; then,
   folded, the question's wording and coding rule. The note's machine parts
   ("Judgement call: roadmap_definition: default 0 (...); ... would give 5") are
   reworded by fixed rules, never by a model at view time.

   Correct this, under the answer: the owner gives evidence (a quote, and where
   it is: a document already saved for the row and its page, a link, or a file
   uploaded through HB.supply's upload), the answer it should be, or both. One
   doc per round goes to the `corrections` collection; Claude checks it with
   `hb apply-corrections` and a blind re-read of that one question. The panel
   then says "Your correction, waiting for Claude", "Your answer, confirmed by
   Claude" or "Your answer; Claude disagrees because ...", and after a
   disagreement offers Keep my answer or Try again. Every round stays listed.

   HB.corrections = {
     ready()             true once the page can save to `corrections`
     submit(spec)        Promise of the saved doc. spec {row, qid, value, reason,
                         evidence: {quote, doc, page, link, asset, file},
                         upload (a File, uploaded first), keep}; value is one of
                         the question's level values or null; a plain sentence
                         is thrown for anything missing
     keep(row, qid)      keep an answer Claude disagreed with: submit({keep: true})
     forCell(row, qid)   every round, oldest first: the doc's fields plus state
                         "waiting" | "agreed" | "disagreed" | "kept" | "reread"
                         (evidence only: Claude's re-read is the answer) |
                         "superseded", why (Claude's reason) and claude (the
                         value Claude's re-read gave)
     onChange(fn)        fn() when the collection changes; returns a stop function
   }
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, svg, isNum, fmtNum, fmtInt, store } = HB;
  const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const STATE_WORDS = {
    answered: "Answered",
    no_evidence: "No evidence found",
    not_applicable: "Does not apply",
    not_run: "Not scored yet",
  };
  const FLAG_WORDS = {
    quote_not_found: "Quote not on saved page",
    document_changed: "Document changed since scored",
    group_differs: "Differs across countries",
    after_cutoff: "After cut-off",
    reads_disagree: "Second read differs",
    weak_source: "Not from the company",
    owner_changed: "Changed by the owner",
  };
  // Glyphs for when the grid's HB.flagGlyph is not there; the words beside them
  // carry the meaning.
  const FLAG_GLYPHS = {
    quote_not_found: () => [svg("circle", { cx: 6, cy: 6, r: 4.4 }), svg("path", { d: "M3 9 9 3" })],
    document_changed: () => [svg("rect", { x: 2.6, y: 1.8, width: 6.8, height: 8.4, rx: 1 }), svg("path", { d: "M3.6 10.4 8.4 1.6" })],
    group_differs: () => [svg("path", { d: "M2.2 4.6h7.6M2.2 7.4h7.6M7.6 1.8 4.4 10.2" })],
    after_cutoff: () => [svg("rect", { x: 1.8, y: 2.4, width: 8.4, height: 7.8, rx: 1 }), svg("path", { class: "is-fill", d: "M1.8 3.4a1 1 0 0 1 1-1h6.4a1 1 0 0 1 1 1V5H1.8z" })],
    reads_disagree: () => [svg("circle", { cx: 6, cy: 6, r: 4.4 }), svg("path", { class: "is-fill", d: "M6 1.6a4.4 4.4 0 0 1 0 8.8z" })],
    weak_source: () => [svg("path", { d: "M3 10V7.4" }), svg("path", { d: "M6 10V5M9 10V2.6", opacity: "0.4" })],
    owner_changed: () => [svg("path", { d: "M3.6 8.4 8.8 3.2" }), svg("path", { class: "is-fill", d: "M2.4 9.6l.7-2.3 1.6 1.6z" })],
  };
  const HOST_WORDS = { issuer: "the company's own site", third_party: "another organisation's site", repository: "a document library" };
  const NOT_KEPT = "Saved copy not kept.";
  const FLAGGED_KEY = "panel-flagged-only";
  let languageNames = null;
  try {
    languageNames = new Intl.DisplayNames(["en-GB"], { type: "language" });
  } catch (err) {
    languageNames = null;
  }

  const CSS = `
.cellp { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s5); min-width: 0; font-size: var(--fs-14); line-height: 1.5; }
.cellp-sec { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s2); min-width: 0; }
.cellp-h { color: var(--ink-2); font-family: var(--sans); font-size: 0.6875rem; font-weight: 650; letter-spacing: 0.06em; line-height: 1.3; text-transform: uppercase; }
.cellp-hrow { display: flex; align-items: center; justify-content: space-between; gap: var(--s2); min-height: 1.75rem; }
.cellp-ans { font-size: var(--fs-16); line-height: 1.4; overflow-wrap: anywhere; }
.cellp-ans strong { font-weight: 700; }
.cellp-sub { color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
.cellp-more > summary { width: fit-content; border-radius: var(--radius); color: var(--accent); font-size: var(--fs-12); font-weight: 600; cursor: pointer; }
.cellp-more > summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.cellp-more__body { display: grid; gap: var(--s3); margin-top: var(--s2); padding-left: var(--s3); border-left: 1px solid var(--rule); min-width: 0; }
.cellp-text { white-space: pre-wrap; overflow-wrap: anywhere; }
.cellp-wording { font-family: var(--serif); font-size: var(--fs-16); line-height: 1.5; overflow-wrap: anywhere; }
.cellp-flags { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
.cellp-flags > li { display: grid; grid-template-columns: 1rem minmax(0, 1fr); align-items: start; gap: var(--s2); overflow-wrap: anywhere; }
.cellp-flags > li > :first-child { margin-top: 0.15rem; }
.cellp-glyph { display: inline-grid; place-items: center; width: 1rem; height: 1rem; color: var(--ink); font-size: 0.95rem; font-weight: 700; line-height: 1; }
svg.cellp-glyph { width: 0.9rem; height: 0.9rem; margin: 0.05rem; fill: none; stroke: currentColor; stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; overflow: visible; }
svg.cellp-glyph .is-fill { fill: currentColor; stroke: none; }
.cellp-quote { margin: 0; padding: var(--s2) var(--s3); border-left: 3px solid var(--rule-strong); background: var(--paper); font-family: var(--serif); font-size: var(--fs-16); line-height: 1.55; overflow-wrap: anywhere; }
.cellp-quote.is-missing { border-left-color: var(--critical); }
.cellp-rule { display: grid; gap: var(--s2); min-width: 0; padding: var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); overflow-wrap: anywhere; }
.cellp-would { font-weight: 650; }
.cellp-ask { font-family: var(--serif); font-size: var(--fs-16); line-height: 1.45; }
.cellp-calls { display: grid; gap: var(--s2); margin: 0; padding-left: 1.1rem; }
.cellp-jump { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s3); margin-top: var(--s1); }
.cellp-line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); color: var(--ink-2); font-size: var(--fs-12); }
.cellp-check { display: flex; align-items: flex-start; gap: var(--s2); color: var(--ink); font-size: var(--fs-12); font-weight: 500; }
.cellp-check > .status-icon { margin-top: 1px; }
.cellp-link { padding: 0; border: 0; background: none; color: var(--accent); font: inherit; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.cellp-pager { display: inline-flex; gap: var(--s1); }
.cellp-ibtn { min-width: 1.75rem; padding-inline: var(--s1); }
.cellp-chev { width: 0.875rem; height: 0.875rem; fill: none; stroke: currentColor; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
.cellp-doc { font-weight: 600; overflow-wrap: anywhere; }
.cellp-meta { color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.cellp-links { display: flex; flex-wrap: wrap; gap: var(--s2); }
.cellp-page { position: relative; max-height: 14rem; overflow: auto; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); color: var(--ink-2); white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--fs-12); line-height: 1.6; }
.cellp-page:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.cellp-page mark { padding: 0 1px; border-radius: 2px; background: color-mix(in oklab, var(--warning) 45%, var(--surface)); color: var(--ink); box-shadow: 0 0 0 1px var(--warning); }
.cellp-none { padding: var(--s2) var(--s3); border-radius: var(--radius); background: var(--paper); color: var(--ink-2); }
.cellp-fix { gap: var(--s2); }
.cellp-fix__acts { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.cellp-fix__sent { display: grid; gap: var(--s1); margin: 0; color: var(--ink-2); font-size: var(--fs-12); overflow-wrap: anywhere; }
.cellp-fix__rounds { display: grid; gap: var(--s2); margin: 0; padding-left: 1.1rem; font-size: var(--fs-12); overflow-wrap: anywhere; }
.cellp-fixset { display: grid; gap: var(--s3); min-width: 0; margin: 0; padding: var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); }
.cellp-fixset > legend { padding-inline: var(--s1); font-size: var(--fs-14); font-weight: 600; }
.cellp-fixwhere { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4); }
.cellp-nav { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); color: var(--ink-2); font-size: var(--fs-12); }
.cellp-nav .btn { gap: var(--s1); }
.cellp-nav__pos { margin-right: auto; font-variant-numeric: tabular-nums; }
.cellp-switch { display: inline-flex; align-items: center; gap: var(--s2); min-height: 1.75rem; padding: 0 var(--s1); border: 0; border-radius: var(--radius); background: none; color: var(--ink); font: inherit; font-size: var(--fs-12); cursor: pointer; }
.cellp-switch:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.cellp-switch__track { position: relative; flex: none; width: 1.75rem; height: 1rem; border: 1px solid var(--rule-strong); border-radius: 999px; background: var(--surface); transition: background-color var(--dur-fast) var(--ease); }
.cellp-switch__track::after { content: ""; position: absolute; top: 1px; left: 1px; width: 0.75rem; height: 0.75rem; border-radius: 50%; background: var(--rule-strong); transition: transform var(--dur-fast) var(--ease); }
.cellp-switch[aria-checked="true"] .cellp-switch__track { border-color: var(--accent); background: var(--accent); }
.cellp-switch[aria-checked="true"] .cellp-switch__track::after { background: var(--on-accent); transform: translateX(0.75rem); }
@media (pointer: coarse) {
  .cellp-switch { min-height: 2.75rem; }
  .cellp-ibtn { min-width: 2.75rem; }
}
@media (width < 40rem) {
  .cellp { font-size: var(--fs-16); }
  .cellp-page { font-size: var(--fs-14); }
  .cellp-sub, .cellp-line, .cellp-check, .cellp-meta, .cellp-more > summary { font-size: var(--fs-14); }
  .drawer[open]:not(.drawer--inflow):has(.cellp) { height: min(85vh, 100% - 3rem); height: min(85dvh, 100% - 3rem); }
}
@media (forced-colors: active) {
  .cellp-page mark { background: Mark; color: MarkText; }
  .cellp-switch__track, .cellp-switch__track::after { forced-color-adjust: none; border-color: CanvasText; background: Canvas; }
  .cellp-switch[aria-checked="true"] .cellp-switch__track::after { background: Highlight; }
}`;

  /* ---- Words ------------------------------------------------------------------- */

  function dateText(iso) {
    if (!iso) return null;
    const when = new Date(String(iso).slice(0, 10) + "T00:00:00Z");
    return Number.isNaN(when.getTime()) ? String(iso) : DATE.format(when);
  }

  function languageName(code) {
    if (!code) return "another language";
    try {
      const name = languageNames ? languageNames.of(String(code)) : null;
      if (name && name !== code) return name;
    } catch (err) {
      /* an unknown code shows as itself */
    }
    return String(code);
  }

  function levelFor(q, value) {
    const levels = q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    return levels.find((l) => l && l.value === value) || levels.find((l) => l && String(l.value) === String(value)) || null;
  }

  function topPoints(q) {
    const levels = q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    let top = null;
    for (const l of levels) if (l && isNum(l.points) && (top === null || l.points > top)) top = l.points;
    return top;
  }

  function valueWord(q, value) {
    if (value == null) return "no value";
    if (HB.isFigureQuestion(q)) return isNum(value) ? HB.figureText(q, value) : String(value);
    if (q && q.answer && q.answer.kind === "yes_no") {
      const s = String(value);
      return s.charAt(0).toUpperCase() + s.slice(1);
    }
    return "Level " + value;
  }

  function pointsWords(points, top) {
    if (!isNum(points)) return "Points not recorded";
    const last = isNum(top) ? top : points;
    return fmtNum(points) + (isNum(top) ? " of " + fmtNum(top) : "") + (last === 1 ? " point" : " points");
  }

  // A level's meaning as [name, the rest]: "Limited alignment to ECC: A company
  // would score if..." gives the name before the colon. A meaning cut short
  // ("...") is filled out from the coding rule when the rule holds it.
  function meaningParts(q, level) {
    let m = String((level && level.meaning) || "").trim();
    if (/(\.\.\.|…)$/.test(m) && q && typeof q.rule === "string") {
      const stem = m.replace(/(\.\.\.|…)$/, "").slice(0, 40);
      const at = stem ? q.rule.indexOf(stem) : -1;
      if (at >= 0) {
        const after = q.rule.slice(at);
        const end = after.slice(stem.length).search(/\s\d+ points?:\s/);
        m = (end >= 0 ? after.slice(0, stem.length + end) : after).trim();
      }
    }
    const colon = m.indexOf(": ");
    if (colon > 0 && colon < 60 && m.length - colon > 20) return [m.slice(0, colon), m.slice(colon + 2), m];
    return [m, "", m];
  }

  function groupOf(ctx, unit) {
    const p = ctx.bundle.project || {};
    const groupLabels = Array.isArray(p.group_labels) ? p.group_labels : [];
    const order = groupLabels.includes(ctx.state.groupBy) ? [ctx.state.groupBy].concat(groupLabels) : groupLabels;
    for (const label of order) {
      const v = unit && unit.labels ? unit.labels[label] : null;
      if (v != null && v !== "") return String(v);
    }
    return "the same group";
  }

  function hostName(address) {
    const match = /^(?:https?:)?\/\/([^/?#:]+)/i.exec(String(address || ""));
    return match ? match[1].replace(/^www\./i, "") : null;
  }

  // "1", "1 and 3", "1, 2 and 3".
  function listWords(items) {
    return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
  }

  // Capital first letter and a full stop at the end.
  function sentence(text) {
    const s = String(text || "").trim();
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1) + (/[.!?]$/.test(s) ? "" : ".");
  }

  function sameValue(a, b) {
    return a != null && b != null && String(a) === String(b);
  }

  /* ---- Claude's note ------------------------------------------------------------ */

  // A note is Claude's reasoning, sometimes followed by "Judgement call:" and one
  // part per point where the coding rule was unclear, each led by the call's
  // snake_case name: "roadmap_definition: default 0 (no interim milestones); a
  // public 2026 deadline plus the progress table would give 5." Parts are split
  // by ". ", "; " or " | " before the next name.
  const CALL_AT = /(^|[.;|] )([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?: \/[^:]*)?:\s*/g;
  const SNAKE = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

  // {reasoning, calls: [{key, text}]}; key is null for a part that names no call.
  function readNote(note) {
    const text = String(note || "").replace(/\s+/g, " ").trim();
    const at = text.search(/\bJudgement call:/);
    const out = { reasoning: (at >= 0 ? text.slice(0, at) : text).trim(), calls: [] };
    if (at < 0) return out;
    const rest = text.slice(at).replace(/^Judgement call:\s*/, "");
    const marks = [];
    CALL_AT.lastIndex = 0;
    for (let m = CALL_AT.exec(rest); m; m = CALL_AT.exec(rest)) {
      marks.push({ start: m.index + m[1].length, body: m.index + m[0].length, key: m[2] });
    }
    const parts = [];
    if (!marks.length || marks[0].start > 0) parts.push({ key: null, text: rest.slice(0, marks.length ? marks[0].start : rest.length) });
    marks.forEach((mk, i) => parts.push({ key: mk.key, text: rest.slice(mk.body, i + 1 < marks.length ? marks[i + 1].start : rest.length) }));
    out.calls = parts.map((p) => ({ key: p.key, text: p.text.replace(/\s*\|\s*$/, "").trim() })).filter((p) => p.text);
    return out;
  }

  // "roadmap_definition" -> "Roadmap definition".
  function keyWords(key) {
    return sentence(String(key).replace(/_/g, " ")).replace(/\.$/, "");
  }

  // One part in plain words: "Claude's reading gives 0 (no interim milestones).
  // The other reading: a public 2026 deadline plus the progress table would give 5."
  function plainCall(text) {
    let s = String(text || "").trim().replace(SNAKE, (k) => k.replace(/_/g, " "));
    if (!/^default\b/i.test(s)) return sentence(s);
    s = s.replace(/^default\b\s*(?=\d)/i, "Claude's reading gives ").replace(/^default\b/i, "Claude's reading");
    const semi = s.indexOf("; ");
    if (semi < 0) return sentence(s);
    const rest = s.slice(semi + 2).trim();
    const other = /^(the |an? )?((other|strict\w*|alternative)\b|reading\b)|^if\b/i.test(rest) ? sentence(rest)
      : "The other reading: " + rest + (/[.!?]$/.test(rest) ? "" : ".");
    return sentence(s.slice(0, semi)) + " " + other;
  }

  // {mine, other} when a part says plainly what each reading gives ("default
  // gives 0; any third-party welfare audit counting gives 5"): one number for the
  // default, exactly one "give(s)/giving N" after the semicolon, nothing hedged.
  function readingValues(text) {
    const s = String(text || "").trim();
    const semi = s.indexOf("; ");
    if (!/^default\b/i.test(s) || semi < 0) return null;
    const mine = /^default\b(?:\s*\([^()]*\))?\s*(?:gives\s+)?(\d+(?:\.\d+)?)\b/i.exec(s.slice(0, semi));
    const rest = s.slice(semi + 2);
    const others = Array.from(rest.matchAll(/\b(?:give|gives|giving)\s+(\d+(?:\.\d+)?)\b/gi));
    if (!mine || others.length !== 1 || /\b(at most|at least|only if|may|might|unless|perhaps)\b/i.test(rest)) return null;
    return { mine: mine[1], other: others[0][1] };
  }

  // A rule's first question; the rest of it is on the Rules tab.
  function firstQuestion(ask) {
    const s = String(ask || "").trim();
    const at = s.search(/\?\s/);
    return at < 0 ? s : s.slice(0, at + 1);
  }

  /* ---- Flags ----------------------------------------------------------------------- */

  // A factual flag is anything but "judgement" (which says the answer rests on an
  // open rule); the grid's HB.flagKinds.factual decides when it is there.
  function isFactual(kind) {
    const factual = HB.flagKinds && HB.flagKinds.factual;
    if (Array.isArray(factual)) return factual.includes(kind);
    if (factual instanceof Set) return factual.has(kind);
    return kind !== "judgement";
  }

  function factualFlags(cell) {
    return cell && Array.isArray(cell.flags) ? cell.flags.filter(isFactual) : [];
  }

  function glyphFor(kind) {
    if (typeof HB.flagGlyph === "function") {
      try {
        const made = HB.flagGlyph(kind);
        if (made instanceof Element) {
          made.setAttribute("aria-hidden", "true");
          return made;
        }
        if (typeof made === "string" && made) return h("span", { class: "cellp-glyph", "aria-hidden": "true" }, made);
      } catch (err) {
        console.error("HB.flagGlyph failed:", err);
      }
    }
    const draw = FLAG_GLYPHS[kind];
    if (!draw) return h("span", { class: "cellp-glyph", "aria-hidden": "true" }, "!");
    return svg("svg", { class: "cellp-glyph", viewBox: "0 0 12 12", "aria-hidden": "true", focusable: "false" }, draw());
  }

  /* ---- Finding the quote on the page ------------------------------------------- */

  const DASHES = /[‐-―−]/;
  const SINGLE = /[‘’‚‛′]/;
  const DOUBLE = /[“-‟″]/;
  const SPACE = /\s/;

  // The text folded for matching (case, whitespace runs, dashes, curly quotes,
  // soft hyphens, NFKC; optionally a hyphen at a line end joined), with each
  // folded UTF-16 unit's start and end in the original text.
  function fold(text, joinHyphens) {
    const out = [];
    const starts = [];
    const ends = [];
    let gap = false;
    let i = 0;
    while (i < text.length) {
      const ch = String.fromCodePoint(text.codePointAt(i));
      const w = ch.length;
      if (ch === "­") {
        i += w;
        continue;
      }
      if (SPACE.test(ch)) {
        if (out.length) gap = true;
        i += w;
        continue;
      }
      let norm = DASHES.test(ch) || ch === "-" ? "-" : SINGLE.test(ch) ? "'" : DOUBLE.test(ch) ? '"' : ch;
      if (joinHyphens && norm === "-") {
        let j = i + w;
        let newline = false;
        while (j < text.length && SPACE.test(text[j])) {
          if (text[j] === "\n") newline = true;
          j += 1;
        }
        if (newline && j < text.length) {
          i = j;
          continue;
        }
      }
      if (gap) {
        out.push(" ");
        starts.push(i);
        ends.push(i);
        gap = false;
      }
      norm = norm.normalize("NFKC").toLowerCase();
      for (let k = 0; k < norm.length; k += 1) {
        out.push(norm[k]);
        starts.push(i);
        ends.push(i + w);
      }
      i += w;
    }
    return { s: out.join(""), starts, ends };
  }

  // [start, end) of the quote in the page text, or null when it is not there.
  function findQuote(text, quote) {
    if (typeof text !== "string" || typeof quote !== "string") return null;
    for (const join of [false, true]) {
      const needle = fold(quote, join).s;
      if (!needle) return null;
      const hay = fold(text, join);
      const at = hay.s.indexOf(needle);
      if (at >= 0) return [hay.starts[at], hay.ends[at + needle.length - 1]];
    }
    // Page layout (footnotes, columns) often splits a quote, and the quote check passes a quote that
    // shares 5 words in a row with the document; so fall back to the longest run of 5 or more words.
    const words = fold(quote, true).s.split(" ");
    const hay = fold(text, true);
    let best = null;
    for (let i = 0; i + 5 <= words.length; i += 1) {
      let j = i + 5;
      if (best && j <= i + best.n) j = i + best.n + 1;
      if (j > words.length || hay.s.indexOf(words.slice(i, j).join(" ")) < 0) continue;
      while (j < words.length && hay.s.indexOf(words.slice(i, j + 1).join(" ")) >= 0) j += 1;
      const part = words.slice(i, j).join(" ");
      const at = hay.s.indexOf(part);
      best = { n: j - i, range: [hay.starts[at], hay.ends[at + part.length - 1], true] };
    }
    return best ? best.range : null;
  }

  /* ---- Stepping through the column ------------------------------------------------- */

  const nav = {
    flaggedOnly: store.get(FLAGGED_KEY, false) === true,
    focus: null,      // "next" or "prev": the button to focus after a step from the panel
    scroll: false,    // bring the new cell's grid link into view after a step
    gridFocus: false, // a step from the grid: focus follows to the new cell's link
    step: null,       // (dir, from) => void for the open panel
  };

  function replaceHash(hash) {
    try {
      location.replace(location.href.split("#")[0] + hash);
    } catch (err) {
      location.hash = hash;
    }
  }

  // Row ids in the grid's display order (after its filters): HB.gridRows() when
  // the grid sets it, else the grid's links on the page, else the bundle's order.
  function rowOrder(ctx) {
    if (typeof HB.gridRows === "function") {
      try {
        const rows = HB.gridRows();
        if (Array.isArray(rows)) {
          return rows.map((r) => (typeof r === "string" ? r : r && r.id != null ? String(r.id) : null)).filter(Boolean);
        }
      } catch (err) {
        console.error("HB.gridRows failed:", err);
      }
    }
    if (HB.route().tab === "grid") {
      const seen = new Set();
      for (const a of document.querySelectorAll("#main a[data-row][data-q]")) seen.add(a.getAttribute("data-row"));
      if (seen.size) return Array.from(seen);
    }
    return (Array.isArray(ctx.bundle.units) ? ctx.bundle.units : []).map((u) => u.id);
  }

  function chevron(dir) {
    return svg("svg", { class: "cellp-chev", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" },
      svg("path", { d: dir < 0 ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5" }));
  }

  function navBar(ctx, row, qid) {
    const column = rowOrder(ctx).filter((r) => ctx.data.cell(r, qid));
    const at = column.indexOf(row);
    const flagged = column.filter((r) => factualFlags(ctx.data.cell(r, qid)).length > 0);
    const prev = h("button", { type: "button", class: "btn btn--small", "aria-keyshortcuts": "K ArrowUp" }, chevron(-1), "Previous");
    const next = h("button", { type: "button", class: "btn btn--small", "aria-keyshortcuts": "J ArrowDown" }, "Next", chevron(1));
    const pos = h("span", { class: "cellp-nav__pos" });
    const toggle = h("button", { type: "button", class: "cellp-switch", role: "switch", "aria-checked": String(nav.flaggedOnly) },
      h("span", { class: "cellp-switch__track", "aria-hidden": "true" }), "Flagged only");
    let targets = { prev: null, next: null };

    function update() {
      const list = nav.flaggedOnly ? flagged : column;
      const index = (r) => column.indexOf(r);
      targets = {
        next: at < 0 ? list[0] || null : list.find((r) => index(r) > at) || null,
        prev: at < 0 ? list[list.length - 1] || null : list.slice().reverse().find((r) => index(r) < at) || null,
      };
      prev.disabled = !targets.prev;
      next.disabled = !targets.next;
      toggle.setAttribute("aria-checked", String(nav.flaggedOnly));
      const where = list.indexOf(row);
      // "2 of 142 countries on question 20": which row this is among those the question applies to.
      const pr = ctx.bundle.project || {};
      const many = String(pr.unit_word_plural || "rows");
      const one = String(pr.unit_word || "row");
      const rows = (n) => (n === 1 ? one : many);
      const kind = nav.flaggedOnly ? "flagged " : "";
      if (nav.flaggedOnly && !list.length) pos.textContent = "None flagged on question " + qid;
      else if (where >= 0) pos.textContent = fmtInt(where + 1) + " of " + fmtInt(list.length) + " " + kind + rows(list.length) + " on question " + qid;
      else pos.textContent = fmtInt(list.length) + " " + kind + rows(list.length) + " on question " + qid;
    }

    function go(dir, from) {
      const target = dir > 0 ? targets.next : targets.prev;
      if (!target) return;
      nav.focus = from === "panel" ? (dir > 0 ? "next" : "prev") : null;
      nav.gridFocus = from === "grid";
      nav.scroll = true;
      replaceHash(HB.href(HB.route().tab || "grid", target, qid));
    }

    prev.addEventListener("click", () => go(-1, "panel"));
    next.addEventListener("click", () => go(1, "panel"));
    toggle.addEventListener("click", () => {
      nav.flaggedOnly = !nav.flaggedOnly;
      store.set(FLAGGED_KEY, nav.flaggedOnly ? true : null);
      update();
    });
    update();

    // Focus after a step: the button pressed (or its partner when it is now at
    // the end of the column).
    if (nav.focus) {
      const want = nav.focus === "next" ? (next.disabled ? prev : next) : (prev.disabled ? next : prev);
      if (!want.disabled) want.setAttribute("autofocus", "");
      nav.focus = null;
    }
    nav.step = go;
    return h("div", { class: "cellp-nav", "data-drawer-tools": "", role: "group", "aria-label": "Step through question " + qid },
      prev, next, pos, toggle);
  }

  /* ---- The open cell in the grid ------------------------------------------------------ */

  function cellLinks(cell) {
    const main = document.getElementById("main");
    if (!main || !cell) return [];
    return Array.from(main.querySelectorAll("a[data-row][data-q]"))
      .filter((a) => a.getAttribute("data-row") === cell.row && a.getAttribute("data-q") === cell.qid);
  }

  // Marks the open cell's grid links with is-open (and only those); returns the
  // first one.
  function markOpen(cell) {
    const main = document.getElementById("main");
    if (!main) return null;
    const links = cellLinks(cell);
    for (const el of main.querySelectorAll(".is-open[data-row]")) {
      if (!links.includes(el)) el.classList.remove("is-open");
    }
    for (const el of links) el.classList.add("is-open");
    return links[0] || null;
  }

  function openNow() {
    return HB.state && HB.state.openCell && HB.drawer.isOpen() ? HB.state.openCell : null;
  }

  function afterOpen() {
    const link = markOpen(openNow());
    if (link) {
      HB.drawer.returnTo(link);
      if (nav.scroll) link.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (nav.gridFocus) link.focus({ preventScroll: true });
    }
    nav.scroll = false;
    nav.gridFocus = false;
  }

  function isTextField(el) {
    if (!(el instanceof Element)) return false;
    if (el.isContentEditable) return true;
    if (el.closest("textarea, select")) return true;
    const input = el.closest("input");
    return Boolean(input) && !/^(checkbox|radio|button|submit|reset|range|color|file|image)$/i.test(input.type);
  }

  let listening = false;

  function listen() {
    if (listening) return;
    listening = true;

    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || !openNow() || !nav.step) return;
      const t = event.target;
      if (isTextField(t)) return;
      let dir = 0;
      if (event.key === "j" || event.key === "J") dir = 1;
      else if (event.key === "k" || event.key === "K") dir = -1;
      else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        // The grid and the saved page's scrolling box keep their own arrows.
        if (t instanceof Element && t.closest("#main, .cellp-page, details > summary")) return;
        dir = event.key === "ArrowDown" ? 1 : -1;
      }
      if (!dir) return;
      event.preventDefault();
      const drawerEl = document.getElementById("drawer");
      const from = t instanceof Element && drawerEl && drawerEl.contains(t) ? "panel"
        : t instanceof Element && t.closest("#main a[data-row][data-q]") ? "grid" : null;
      nav.step(dir, from);
    });

    // With the panel docked beside the grid, a click on another cell swaps the
    // panel in place of the same history entry (a new entry per click would make
    // Close step back through every cell seen).
    document.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!openNow()) return;
      const a = event.target instanceof Element ? event.target.closest("#main a[data-row][data-q]") : null;
      if (!a) return;
      event.preventDefault();
      nav.scroll = false;
      replaceHash(HB.href("grid", a.getAttribute("data-row"), a.getAttribute("data-q")));
    }, true);

    const drawerEl = document.getElementById("drawer");
    if (drawerEl) {
      drawerEl.addEventListener("close", () => {
        if (!drawerEl.open) markOpen(null);
      });
    }
    // A tab drawn again (a filter, a grouping) loses the mark: put it back.
    const main = document.getElementById("main");
    if (main && typeof MutationObserver === "function") {
      new MutationObserver(() => {
        if (openNow()) markOpen(openNow());
      }).observe(main, { childList: true });
    }
  }

  /* ---- The owner's corrections (HB.corrections) ----------------------------------- */

  const NO_STORE = () => HB.cantSave();
  const fixes = { docs: new Map(), following: false, listeners: new Set() };

  function fixesChanged() {
    fixes.listeners.forEach((fn) => {
      try {
        fn();
      } catch (err) {
        console.error("HB.corrections listener failed:", err);
      }
    });
  }

  function followFixes() {
    if (fixes.following || !fixReady()) return;
    fixes.following = HB.edits.follow("corrections", (docs) => {
      fixes.docs = new Map(docs.map((d) => [d.id, d]));
      fixesChanged();
    });
  }

  function fixReady() {
    return Boolean(HB.edits && HB.edits.ready);
  }

  // A recorded round's state: Claude's verdict, or still waiting for the re-read.
  function roundState(r) {
    if (r.status === "kept" || r.status === "superseded") return r.status;
    const v = r.verdict;
    if (r.status === "to_check" || !v) return "waiting";
    return v.agree === true ? "agreed" : v.agree === false ? "disagreed" : "reread";
  }

  function forCell(row, qid) {
    followFixes();
    const b = HB.data && HB.data.bundle();
    const out = new Map();
    const checks = new Map();     // round id -> the latest check made in the page
    fixes.docs.forEach((d) => {
      if (d.row !== row || d.qid !== qid) return;
      if (d.kind === "check") {
        const had = checks.get(d.of);
        if (!had || String(d.at) > String(had.at)) checks.set(d.of, d);
        return;
      }
      out.set(d.id, Object.assign({}, d, { state: checking.has(d.id) ? "checking" : "waiting", why: null, claude: null, where: whereChecked(d) }));
    });
    checks.forEach((c, id) => {
      const r = out.get(id);
      if (!r || r.state === "checking" || !c.verdict) return;
      const v = c.verdict;
      out.set(id, Object.assign(r, { state: v.agree === true ? "agreed" : v.agree === false ? "disagreed" : "reread",
        why: v.why || null, claude: v.value ?? null, checked: "page" }));
    });
    for (const r of b && Array.isArray(b.corrections) ? b.corrections : []) {
      if (!r || r.row !== row || r.qid !== qid) continue;
      out.set(r.id, Object.assign({}, r, { state: roundState(r), why: r.verdict ? r.verdict.why : null, claude: r.verdict ? r.verdict.value : null }));
    }
    return Array.from(out.values()).sort((a, b2) => String(a.at).localeCompare(String(b2.at)));
  }

  const clean = (v) => (typeof v === "string" && v.trim() ? v.trim() : null);

  /* ---- Checking a correction in the page --------------------------------------------
     Most of a correction can be checked here, at once, by the Claude in the corner on the owner's
     plan: the words against the saved page (the same test as quotecheck.py: five words running,
     or a whole short quote of three or more), whether the source counts (the company's own, dated
     by the cut-off), then one re-read of this question. A link or a file, or a page this view has
     not loaded, waits for Claude Code at the next run, which records every round either way. */
  const checking = new Set();
  let sampleFn;     // undefined until asked; then the sample function or null

  function whereChecked(d) {
    const e = d.evidence || {};
    if (d.keep) return "code";
    if (e.link || e.asset) return "code";
    return "page";
  }

  function quoteWords(text) {
    return String(text || "").normalize("NFKC").replace(/\u00ad/g, "")
      .replace(/(\p{L})-[ \t]*\r?\n[ \t]*(\p{L})/gu, "$1$2")
      .replace(/[\u2018\u2019\u201a\u201b\u2032\u02bc]/g, "'").replace(/[\u201c\u201d\u201e\u201f\u2033]/g, '"')
      .toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  }

  function onPage(quote, text) {
    const q = quoteWords(quote);
    const src = " " + quoteWords(text).join(" ") + " ";
    if (q.length < 5) return q.length >= 3 && src.includes(" " + q.join(" ") + " ");
    for (let i = 0; i + 5 <= q.length; i += 1) if (src.includes(" " + q.slice(i, i + 5).join(" ") + " ")) return true;
    return false;
  }

  async function sampler() {
    if (sampleFn !== undefined) return sampleFn;
    const c = typeof window !== "undefined" ? window.claude : null;
    try {
      const s = c && typeof c.use === "function" ? await c.use("sample") : null;
      sampleFn = s && typeof s.json === "function" ? s : null;
    } catch (err) {
      sampleFn = null;
    }
    return sampleFn;
  }

  async function saveCheck(round, verdict) {
    await HB.edits.save("corrections", { kind: "check", of: round.id, row: round.row, qid: round.qid, verdict, by_page: true });
  }

  /** Check one waiting round in the page. Resolves to the verdict, or null when it must wait for
      Claude Code (a link or a file, a page this view has not got, or no Claude here). */
  async function checkRound(round) {
    if (!round || round.keep || whereChecked(round) !== "page" || checking.has(round.id) || !fixReady()) return null;
    const b = HB.data.bundle() || {};
    const q = HB.data.questionFor(round.row, round.qid);
    const cellNow = HB.data.cell(round.row, round.qid) || {};
    const e = round.evidence || {};
    const ev = await HB.data.evidence(round.row);
    const pages = (ev && ev.pages) || {};
    let pageText = null;
    if (e.doc) {
      pageText = pages[e.doc + "#" + e.page];
      if (typeof pageText !== "string") return null;      // not loaded here: Claude Code fetches it
    }
    const ask = await sampler();
    const meta = e.doc ? (b.documents || {})[e.doc] || {} : null;
    const title = meta ? String(meta.title || e.doc) : null;
    checking.add(round.id);
    fixesChanged();
    try {
      let verdict;
      const cutoff = (b.project || {}).cutoff;
      if (e.quote && !onPage(e.quote, pageText)) {
        verdict = { agree: false, value: null, why: "Those words aren't on page " + e.page + " of " + title + " as saved. Check the page number, or copy the words exactly." };
      } else if (meta && meta.weak) {
        verdict = { agree: false, value: null, why: title + " isn't the company's own document, so it can't decide the answer." };
      } else if (meta && cutoff && typeof meta.date === "string" && meta.date > cutoff) {
        verdict = { agree: false, value: null, why: title + " is dated after the cut-off (" + cutoff + ")." };
      } else if (!ask) {
        return null;                                        // no Claude here: Claude Code re-reads it
      } else {
        const levels = (q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : []).map((l) => l.value + ": " + (l.meaning || ""));
        const quotes = e.quote ? "The owner points to these words on page " + e.page + " of \u201c" + title + "\u201d:\n\u201c" + e.quote + "\u201d\n\nThat page as saved:\n" + String(pageText).slice(0, 6000)
          : "The owner gives no new evidence. The answer's own evidence:\n" + (Array.isArray(cellNow.evidence) ? cellNow.evidence.map((x) => "\u201c" + (x.quote_en || x.quote) + "\u201d").join("\n") : "none");
        const input = "You check one answer in a benchmark, fairly and briefly.\n\nQuestion " + round.qid + ": " + (q.wording || q.short || "") +
          "\nLevels:\n" + levels.join("\n") + (q.rule ? "\nCoding rule: " + String(q.rule).slice(0, 1500) : "") +
          "\n\n" + quotes + "\n\n" + (round.value != null ? "The owner says the answer is " + round.value + (round.reason ? ", because: " + round.reason : "") + "." : "The owner asks you to read the answer again with this evidence.") +
          "\n\nReply as JSON only: {\"value\": the level value this evidence supports, \"why\": one or two plain sentences for the owner}.";
        const res = await ask.json(input, { modelTier: "default" });
        const value = res && Object.prototype.hasOwnProperty.call(res, "value") ? res.value : null;
        const why = res && typeof res.why === "string" ? res.why : null;
        const level = value == null ? null : levelFor(q, value);
        verdict = round.value != null ? { agree: Boolean(level) && level.value === round.value, value: level ? level.value : null, why }
          : { agree: null, value: level ? level.value : null, why };
      }
      await saveCheck(round, verdict);
      return verdict;
    } catch (err) {
      console.error("Checking a correction in the page failed:", err);
      return null;
    } finally {
      checking.delete(round.id);
      fixesChanged();
    }
  }

  async function submitFix(spec) {
    if (!fixReady()) throw new Error(NO_STORE());
    const s = spec || {};
    const row = clean(s.row == null ? null : String(s.row));
    const qid = clean(s.qid == null ? null : String(s.qid));
    const q = row && qid && HB.data.cell(row, qid) ? HB.data.questionFor(row, qid) : null;
    if (!q) throw new Error("Say which answer this correction is for: a row and a question it applies to.");
    if (s.keep === true) {
      return save({ row, qid, value: null, reason: null, evidence: null, keep: true });
    }
    let value = null;
    if (s.value != null && s.value !== "") {
      const level = levelFor(q, s.value);
      if (!level) throw new Error("That is not one of the answers to question " + qid + ".");
      value = level.value;
    }
    const e = s.evidence || {};
    const page = e.page == null || e.page === "" ? null : Number(e.page);
    const ev = { quote: clean(e.quote), doc: clean(e.doc), page: Number.isInteger(page) && page > 0 ? page : null,
      link: clean(e.link), asset: clean(e.asset), file: clean(e.file) };
    if (e.page != null && e.page !== "" && ev.page == null) throw new Error("The page must be a whole number, 1 or more.");
    const places = ["doc", "link", "asset"].filter((k) => ev[k]).length + (s.upload ? 1 : 0);
    if (!ev.quote && value == null) throw new Error("Give the right answer, the evidence, or both.");
    if (ev.quote && !places) throw new Error("Say where the words are: a saved document, a link or a file.");
    if (places > 1) throw new Error("Give one place for the words: a saved document, a link or a file.");
    if (!ev.quote && places) throw new Error("Copy the words that show it, exactly as they are written.");
    if (ev.doc && ev.page == null) throw new Error("Give the page number the words are on.");
    if (ev.link && !/^https?:\/\//i.test(ev.link)) throw new Error("The link must start with http:// or https://.");
    if (s.upload) {
      if (!(HB.supply && typeof HB.supply.upload === "function")) throw new Error("This page can't take files. Paste a link instead.");
      ev.asset = await HB.supply.upload(s.upload);
      ev.file = s.upload.name || null;
    }
    return save({ row, qid, value, reason: clean(s.reason), evidence: ev });
  }

  async function save(body) {
    // What the page showed, so a colleague's newer change is not overwritten unseen (team.py).
    const shown = HB.data && typeof HB.data.cell === "function" ? HB.data.cell(body.row, body.qid) : null;
    if (shown && shown.rev && !body.keep) body = { ...body, seen: shown.rev };
    const doc = await HB.edits.save("corrections", body);
    fixes.docs.set(doc.id, doc);
    fixesChanged();
    if (!body.keep) checkRound(doc);         // at once, in the page, where it can be
    return doc;
  }

  HB.corrections = {
    ready: () => {
      followFixes();
      return fixReady();
    },
    submit: submitFix,
    keep: (row, qid) => submitFix({ row, qid, keep: true }),
    /** Check the latest waiting correction on an answer in the page now (the chat's "check it"). */
    check: (row, qid) => {
      const r = forCell(row, qid).filter((x) => x.state === "waiting").pop();
      return r ? checkRound(r) : Promise.resolve(null);
    },
    forCell,
    onChange: (fn) => {
      fixes.listeners.add(fn);
      followFixes();
      return () => fixes.listeners.delete(fn);
    },
  };

  /* ---- Parts of the panel -------------------------------------------------------- */

  function heading(text) {
    return h("h3", { class: "cellp-h" }, text);
  }

  function section(cls, ...children) {
    return h("section", { class: "cellp-sec" + (cls ? " " + cls : "") }, children);
  }

  function answerPart(ctx, q, cell, unitWord) {
    if (!cell) {
      return section(null, h("p", { class: "cellp-ans" }, h("strong", null, "Does not apply"), " to this " + unitWord + "."));
    }
    if (cell.state !== "answered" && cell.state !== "no_evidence") {
      return section(null, h("p", { class: "cellp-ans" }, h("strong", null, STATE_WORDS[cell.state] || String(cell.state || "Not recorded"))));
    }
    const top = topPoints(q);
    const level = cell.value == null ? null : levelFor(q, cell.value);
    const [name, rest, full] = meaningParts(q, level);
    const out = [h("p", { class: "cellp-ans" },
      h("strong", null, pointsWords(cell.points, top)),
      cell.value == null ? ". Answer not recorded." : ": " + (name || valueWord(q, cell.value)))];
    const sub = [cell.value == null ? null : valueWord(q, cell.value), STATE_WORDS[cell.state]];
    if (cell.second_read && cell.second_read.value != null) {
      sub.push("Second read: " + valueWord(q, cell.second_read.value) + (cell.second_read.agrees ? ", agrees" : ", differs"));
    }
    out.push(h("p", { class: "cellp-sub" }, sub.filter(Boolean).join(" · ")));
    if (rest) {
      out.push(h("details", { class: "cellp-more" },
        h("summary", null, "What this level means"),
        h("div", { class: "cellp-more__body" }, h("p", { class: "cellp-text" }, valueWord(q, cell.value) + ": " + full))));
    }
    return section("cellp-answer", out);
  }

  // "Claude disagrees because the quote ...": a reason that opens with a common word reads on
  // from "because"; one that opens with a name or a title keeps its capital.
  const OPENERS = /^(The|This|That|These|It|Its|A|An|There|Only|No|None|Nothing|Every|Each|Neither)\b/;
  function because(why) {
    const s = sentence(why);
    if (!s) return ".";
    return " because " + (OPENERS.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);
  }

  // "Your", or a colleague's name in a shared project ("Toby's"): who sent this round.
  function whose(r) {
    return HB.team && typeof HB.team.whose === "function" ? HB.team.whose(r && r.by) : "Your";
  }

  // What the owner sent in one round, as short lines.
  function sentLines(ctx, q, r) {
    const docs = ctx.bundle.documents || {};
    const top = topPoints(q);
    const lines = [];
    if (r.value != null) {
      const lv = levelFor(q, r.value);
      lines.push(whose(r) + " answer: " + valueWord(q, r.value) + (lv && isNum(lv.points) ? " (" + pointsWords(lv.points, top) + ")" : "") + ".");
    }
    const ev = r.evidence || {};
    if (ev.quote) {
      const doc = (r.found && r.found.doc) || ev.doc;
      const page = (r.found && r.found.page) || ev.page;
      const where = doc && docs[doc] ? (docs[doc].title || doc) : ev.file || hostName(ev.link) || ev.link || null;
      lines.push(whose(r) + " evidence: ‘" + ev.quote + "’" + (where ? " (" + where + (isNum(page) ? ", page " + page : "") + ")" : "") + ".");
    }
    if (r.reason) lines.push(whose(r) + " reason: " + sentence(r.reason));
    return lines;
  }

  const ROUND_WORDS = {
    waiting: "waiting for Claude",
    checking: "being checked",
    agreed: "Claude agreed",
    disagreed: "Claude disagreed",
    kept: "you kept your answer",
    reread: "Claude re-read it with your evidence",
    superseded: "replaced by a later correction",
  };

  function fixPart(ctx, row, qid, q) {
    const sec = h("section", { class: "cellp-sec cellp-fix", id: "cellp-fix" });
    let stop = null;
    const open = (prefill) => correctDialog(ctx, row, qid, q, prefill);
    const button = (id, text, cls, onclick) => h("button", { type: "button", id, class: "btn btn--small " + cls, onclick }, text);
    const draw = () => {
      if (stop && !sec.isConnected) {
        stop();
        return;
      }
      const rounds = forCell(row, qid);
      const live = rounds.filter((r) => r.state !== "superseded");
      const last = live[live.length - 1];
      if (!last) {
        sec.replaceChildren(...(fixReady() ? [h("div", { class: "cellp-fix__acts" },
          button("cellp-fix-open", "Correct this", "btn--primary", () => open(null)),
          h("span", { class: "cellp-sub" }, "Give the evidence, the right answer, or both."))] : []));
        sec.hidden = !fixReady();
        return;
      }
      sec.hidden = false;
      const parts = [heading(whose(last) + " correction")];
      const lastDisagreed = live.slice().reverse().find((r) => r.state === "disagreed");
      const acts = [];
      if (last.state === "waiting" && last.keep) {
        parts.push(HB.notice("neutral", HB.when("Your answer, kept. It is being recorded now.", "Your answer, kept. Claude records it the next time it works on this project.")));
      } else if (last.state === "checking") {
        parts.push(HB.notice("neutral", "Checking your correction against the saved page..."));
      } else if (last.state === "waiting") {
        parts.push(HB.notice("neutral", last.where === "code"
          ? HB.when("Saved. Claude is checking the link or file now.", "Saved. Claude checks links and files the next time it works on this project.")
          : HB.when("Saved. Claude is checking it now.", "Saved. Claude checks this the next time it works on this project.")));
        if (last.where === "page" && fixReady()) {
          acts.push(button("cellp-fix-check", "Check it now", "btn--primary", (ev) => { ev.currentTarget.disabled = true; checkRound(last); }));
        }
      } else if (last.state === "agreed") {
        parts.push(HB.notice("good", "Your answer, confirmed by Claude."));
      } else if (last.state === "disagreed") {
        parts.push(HB.notice("warning", (last.value != null ? "Your answer; Claude disagrees" : "Claude disagrees") + because(last.why) +
          (last.value != null ? " Your answer stands unless you change it." : "")));
        if (last.value != null && fixReady()) {
          acts.push(button("cellp-fix-keep", "Keep my answer", "btn--primary", async (ev) => {
            const btn = ev.currentTarget;
            btn.disabled = true;
            try {
              await HB.corrections.keep(row, qid);
            } catch (err) {
              btn.disabled = false;
              sec.append(HB.notice("critical", err && err.message ? err.message : "This could not be saved."));
            }
          }));
        }
        if (fixReady()) acts.push(button("cellp-fix-again", "Try again", "btn--quiet", () => open(last)));
      } else if (last.state === "kept") {
        parts.push(HB.notice("neutral", "Your answer, changed by owner. Claude disagreed" + because(lastDisagreed && lastDisagreed.why)));
      } else if (last.state === "reread") {
        parts.push(HB.notice("good", "Claude re-read this with your evidence. Its answer is now " + valueWord(q, last.claude) + "."));
      }
      if (last.state !== "disagreed" && last.state !== "waiting" && fixReady()) {
        acts.push(button("cellp-fix-again", "Correct again", "btn--quiet", () => open(lastDisagreed || last)));
      }
      const shown = last.state === "kept" || last.keep ? lastDisagreed : last;
      const lines = shown ? sentLines(ctx, q, shown) : [];
      if (lines.length) parts.push(h("div", { class: "cellp-fix__sent" }, lines.map((t) => h("p", null, t))));
      if (acts.length) parts.push(h("div", { class: "cellp-fix__acts" }, acts));
      const earlier = rounds.filter((r) => r !== last && r !== shown);
      if (earlier.length) {
        parts.push(h("details", { class: "cellp-more" },
          h("summary", null, "Earlier rounds (" + fmtInt(earlier.length) + ")"),
          h("ol", { class: "cellp-fix__rounds" }, earlier.map((r) => h("li", null,
            (dateText(r.at) || "") + ": " + ROUND_WORDS[r.state] +
            (r.state === "disagreed" && r.why ? " (" + String(r.why).replace(/\.$/, "") + ")" : "") + ". " +
            sentLines(ctx, q, r).join(" "))))));
      }
      sec.replaceChildren(...parts);
    };
    draw();
    stop = HB.corrections.onChange(draw);
    return sec;
  }

  // The Correct this dialog. `prefill` is an earlier round: its answer starts selected.
  async function correctDialog(ctx, row, qid, q, prefill) {
    const p = ctx.bundle.project || {};
    const unitWord = p.unit_word || "row";
    const docs = ctx.bundle.documents || {};
    const found = ((ctx.bundle.found || {})[row] || {}).documents || [];
    const kept = found.filter((d) => docs[d] && docs[d].kept);
    const tool = HB.supply && typeof HB.supply.assets === "function" ? await HB.supply.assets() : null;
    const quote = HB.edits.textControl("cellp-fix-quote", "", true);
    quote.rows = 3;
    const where = [];
    if (kept.length) where.push(["doc", "A document already saved for this " + unitWord]);
    where.push(["link", "A link"]);
    if (tool) where.push(["asset", "A file to upload"]);
    const radios = where.map(([k, text], i) => {
      const input = h("input", { type: "radio", id: "cellp-fix-where-" + k, name: "cellp-fix-where", value: k });
      input.checked = i === 0;
      return { k, input, node: h("label", { class: "hb-radio", for: "cellp-fix-where-" + k }, input, h("span", null, text)) };
    });
    const docSel = h("select", { class: "input", id: "cellp-fix-doc" },
      kept.map((d) => h("option", { value: d }, (docs[d].title || d) + (isNum(docs[d].pages) ? " (" + fmtInt(docs[d].pages) + (docs[d].pages === 1 ? " page)" : " pages)") : ""))));
    // Start on the document the earlier round found, else the one the answer cites.
    const now = HB.data.cell(row, qid) || {};
    const cited = (Array.isArray(now.evidence) ? now.evidence : []).map((e) => e && e.doc);
    const first = [prefill && prefill.found && prefill.found.doc, prefill && prefill.evidence && prefill.evidence.doc, ...cited]
      .find((d) => d && kept.includes(d));
    if (first) docSel.value = first;
    const page = h("input", { type: "number", class: "input", id: "cellp-fix-page", min: "1", step: "1", inputmode: "numeric" });
    const link = HB.edits.textControl("cellp-fix-link", "");
    link.type = "url";
    link.placeholder = "https://";
    const file = tool ? HB.supply.fileControl("cellp-fix-file") : null;
    const groups = {
      doc: h("div", { class: "stack" }, HB.edits.field("cellp-fix-doc", "Which document", docSel), HB.edits.field("cellp-fix-page", "Page", page)),
      link: HB.edits.field("cellp-fix-link", "The link", link, "Claude fetches it and keeps a copy."),
      asset: file ? HB.edits.field("cellp-fix-file", "The file", file, "A PDF or a text file, up to 20 MB.") : null,
    };
    const showPlace = () => {
      const on = (radios.find((r) => r.input.checked) || radios[0]).k;
      Object.entries(groups).forEach(([k, node]) => { if (node) node.hidden = k !== on; });
    };
    radios.forEach((r) => r.input.addEventListener("change", showPlace));
    showPlace();
    const levels = q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    const top = topPoints(q);
    const answer = h("select", { class: "input", id: "cellp-fix-value" },
      h("option", { value: "" }, "No answer, just the evidence"),
      levels.map((l, i) => {
        const name = meaningParts(q, l)[0];
        return h("option", { value: String(i) }, valueWord(q, l.value) + " (" + pointsWords(l.points, top) + ")" + (name ? ": " + name.slice(0, 80) : ""));
      }));
    const pre = prefill && prefill.value != null ? levels.findIndex((l) => sameValue(l.value, prefill.value)) : -1;
    answer.value = pre >= 0 ? String(pre) : "";
    const reason = HB.edits.textControl("cellp-fix-reason", "", true);
    reason.rows = 3;
    const body = [
      h("fieldset", { class: "cellp-fixset", id: "cellp-fix-evidence" }, h("legend", null, "The evidence"),
        HB.edits.field("cellp-fix-quote", "The words that show it", quote, "Copy them exactly as they are written. Leave this empty to give only the answer."),
        h("div", { class: "cellp-fixwhere", role: "radiogroup", "aria-label": "Where the words are" }, radios.map((r) => r.node)),
        groups.doc && kept.length ? groups.doc : null, groups.link, groups.asset),
      h("fieldset", { class: "cellp-fixset", id: "cellp-fix-answer" }, h("legend", null, "The answer"),
        HB.edits.field("cellp-fix-value", "It should be", answer),
        HB.edits.field("cellp-fix-reason", "Why (optional)", reason)),
    ];
    return HB.edits.dialog({
      kicker: "Correct this", title: qid + (q && q.short ? " " + q.short : ""),
      intro: "Give the evidence, the right answer, or both. Claude checks the words against the source and re-reads the question. Your answer stands either way.",
      body, focus: quote, saveLabel: "Send to Claude",
      done: HB.when("Sent. Claude is checking it now, and this panel says whether Claude agrees.",
        "Sent. Claude checks it the next time it works on this project, and this panel says whether Claude agrees."),
      submit: async () => {
        const place = (radios.find((r) => r.input.checked) || radios[0]).k;
        const words = quote.value.trim();
        const level = answer.value === "" ? null : levels[Number(answer.value)];
        const evidence = { quote: words || null, doc: null, page: null, link: null, asset: null, file: null };
        let upload = null;
        if (words && place === "doc") {
          evidence.doc = docSel.value || null;
          evidence.page = page.value.trim() || null;
        } else if (words && place === "link") {
          evidence.link = link.value.trim() || null;
        } else if (words && place === "asset") {
          upload = file && file.files && file.files[0] ? file.files[0] : null;
          if (!upload) throw new Error("Choose the file the words are in.");
        }
        await submitFix({ row, qid, value: level ? level.value : null, reason: reason.value, evidence, upload });
      },
    });
  }

  // The quote_not_found sentence: which quotes did not match (the bundle marks
  // them not_found), with a button to each.
  function quoteFlag(cell, quotes) {
    const items = (Array.isArray(cell.evidence) ? cell.evidence : []).filter((e) => e && typeof e === "object");
    const n = items.length;
    const bad = items.map((e, i) => (e.not_found ? i : -1)).filter((i) => i >= 0);
    const num = (i) => fmtInt(i + 1);
    let text;
    if (!bad.length) text = n > 1 ? "One of the quotes didn't match its saved document word for word." : "The quote didn't match its saved document word for word.";
    else if (n === 1) text = "The quote didn't match its saved document word for word.";
    else if (bad.length === n) text = n === 2 ? "Neither quote matched its saved document word for word." : "None of the " + fmtInt(n) + " quotes matched their saved documents word for word.";
    else {
      const good = items.map((_, i) => i).filter((i) => !bad.includes(i));
      text = (bad.length === 1 ? "Quote " : "Quotes ") + listWords(bad.map(num)) + " of " + fmtInt(n) + " didn't match " +
        (bad.length === 1 ? "its saved document" : "their saved documents") + " word for word" +
        (good.length ? "; " + (good.length === 1 ? "quote " : "quotes ") + listWords(good.map(num)) + " did." : ".");
    }
    const onPage = bad.length ? bad.some((i) => isNum(items[i].page)) : items.some((e) => isNum(e.page));
    text += " The answer may still be right: " + (onPage ? "check the saved page." : "open the source and look for " + (bad.length > 1 ? "the quotes." : "the quote."));
    // A saved page whose words were read from a picture (a poster, a scan) can hold a wrong letter; the check allows a slip, but not this one.
    if ((bad.length ? bad.map((i) => items[i]) : items).some((e) => e.ocr)) text += " Its words were read from a picture, so look at the picture as well.";
    const jumps = n > 1 && bad.length < n && quotes ? bad.map((i) => h("button", { type: "button", class: "cellp-link", onclick: () => quotes.show(i) }, "Show quote " + num(i))) : [];
    return [text, jumps.length ? h("span", { class: "cellp-jump" }, jumps) : null];
  }

  // The other rows of the group that cited a document this answer cites and got a
  // different answer (the check's own test: the first group label, answered cells).
  function groupPeers(ctx, unit, q, cell) {
    const p = ctx.bundle.project || {};
    const label = Array.isArray(p.group_labels) ? p.group_labels[0] : null;
    const group = unit && unit.labels && label ? unit.labels[label] : null;
    if (group == null || group === "") return [];
    const cited = new Set((Array.isArray(cell.evidence) ? cell.evidence : []).map((e) => e && e.doc).filter(Boolean));
    return (Array.isArray(ctx.bundle.units) ? ctx.bundle.units : [])
      .filter((u) => u && u.id !== unit.id && u.labels && u.labels[label] === group)
      .map((u) => ({ u, c: ctx.data.cell(u.id, q.id) }))
      .filter(({ c }) => c && c.state === "answered" && !sameValue(c.value, cell.value) &&
        (Array.isArray(c.evidence) ? c.evidence : []).some((e) => e && cited.has(e.doc)));
  }

  // What a flag means for this answer and what to do, as nodes for a sentence.
  function flagWords(ctx, unit, q, cell, kind, quotes) {
    const p = ctx.bundle.project || {};
    const docs = ctx.bundle.documents || {};
    const top = topPoints(q);
    const cited = Array.from(new Set((Array.isArray(cell.evidence) ? cell.evidence : []).map((e) => e && e.doc).filter((d) => d && docs[d])));
    if (kind === "quote_not_found") return quoteFlag(cell, quotes);
    if (kind === "document_changed") {
      return ["The saved copy of a document behind this answer has changed since the answer was scored, so its quotes were checked against the copy the scorer read. " +
        (cell.out_of_date ? "The answer will be scored again from the new copy." : "The answer is queued to be scored again from the new copy.")];
    }
    if (kind === "group_differs") {
      const peers = groupPeers(ctx, unit, q, cell);
      if (!peers.length) {
        return ["Other " + (p.unit_word_plural || "rows") + " in " + groupOf(ctx, unit) +
          " answered this differently from the same document. One of the answers may be wrong: compare them in the grid."];
      }
      const names = [];
      peers.forEach(({ u, c }, i) => {
        if (i) names.push(i === peers.length - 1 ? " and " : ", ");
        names.push(h("a", { href: HB.href("grid", u.id, q.id) }, String(u.name || u.id)), " (" + pointsWords(c.points, top) + ")");
      });
      return peers.length === 1
        ? [...names, " cited the same document but got a different answer. One of the two may be wrong: open it to compare."]
        : [...names, " cited the same document but got different answers. Open them to compare and see which reading is right."];
    }
    if (kind === "weak_source") {
      const whys = Array.from(new Set(cited.map((d) => docs[d].weak && docs[d].weak.why).filter(Boolean)));
      const why = whys.length ? " (" + whys.join("; ").replace(/^./, (c) => c.toLowerCase()) + ")" : "";
      return ["Every document behind this answer is a weak source" + why +
        ". The answer may still be right, but look for the company's own statement before relying on it."];
    }
    if (kind === "after_cutoff") {
      const cut = typeof p.cutoff === "string" ? p.cutoff.slice(0, 10) : null;
      const late = cut ? cited.filter((d) => typeof docs[d].date === "string" && docs[d].date.slice(0, 10) > cut) : [];
      const what = late.length === 1
        ? (docs[late[0]].title ? "‘" + docs[late[0]].title + "’" : "The document") + " is dated " + dateText(docs[late[0]].date)
        : "A document this answer cites is dated";
      return [what + " after the cut-off" + (cut ? " (" + dateText(cut) + ")" : "") + ", so it may not count for this edition." +
        " Check the date on the source; if it is too late, this answer should not rest on it."];
    }
    if (kind === "owner_changed") {
      const o = cell.owner || {};
      if (o.agreed === true) return ["The owner corrected this answer, and Claude agreed after re-reading it."];
      if (o.agreed === false) {
        return ["The owner's answer stands" + (o.kept ? ", kept after Claude disagreed" : "") + ". Claude disagreed" + because(o.why)];
      }
      return ["The owner corrected this answer. Claude has not checked it yet."];
    }
    if (kind === "reads_disagree") {
      const sr = cell.second_read;
      const lv = sr && sr.value != null ? levelFor(q, sr.value) : null;
      const gave = sr && sr.value != null ? valueWord(q, sr.value) + (lv && isNum(lv.points) ? " (" + pointsWords(lv.points, top) + ")" : "") : "a different answer";
      return ["A second, independent read gave " + gave + " instead. Read the quote and the coding rule, then decide which is right."];
    }
    return ["Flagged: " + String(kind) + "."];
  }

  function flagsPart(ctx, unit, q, cell, quotes) {
    const docs = ctx.bundle.documents || {};
    const items = [];
    for (const f of Array.isArray(cell.flags) ? cell.flags : []) {
      if (f === "judgement") continue;   // shown under "What would change it"
      const words = flagWords(ctx, unit, q, cell, f, quotes);
      items.push(h("li", null, glyphFor(f), h("span", null, FLAG_WORDS[f] ? h("strong", null, FLAG_WORDS[f] + ". ") : null, words)));
    }
    const notes = [];
    const evidence = Array.isArray(cell.evidence) ? cell.evidence : [];
    if (evidence.some((e) => e && !e.missing && docs[e.doc] && docs[e.doc].kept === false)) notes.push("Saved copy not kept; quote not checked.");
    if (cell.out_of_date) notes.push("Dark corner: the question changed after this was scored, so the answer may be out of date.");
    if (evidence.some((e) => e && (e.missing || !docs[e.doc]))) notes.push(NOT_KEPT);
    for (const n of notes) items.push(h("li", { "data-tone": "neutral" }, HB.icon("exclamation"), h("span", null, n)));
    if (!items.length) return null;
    return section("cellp-flagsec", heading("Why it's marked"), h("ul", { class: "cellp-flags" }, items));
  }

  // The quote, with the English and the original a toggle apart when it was
  // translated. Returns [blockquote, language line or null].
  function quoteBox(item) {
    const original = typeof item.quote === "string" ? item.quote : "";
    const english = typeof item.quote_en === "string" && item.quote_en ? item.quote_en : null;
    const lang = item.language ? String(item.language) : null;
    const box = h("blockquote", { class: "cellp-quote" });
    if (lang === "en" || (english !== null && english === original)) {
      box.append(english || original || "No quote recorded.");
      return [box, null];
    }
    if (english === null) {
      box.append(original || "No quote recorded.");
      if (!lang) return [box, null];
      box.lang = lang;
      return [box, h("p", { class: "cellp-line" }, "Not translated (" + languageName(lang) + ")")];
    }
    box.append(english);
    box.lang = "en";
    const from = "Machine-translated from " + languageName(lang);
    const word = h("span", null, from);
    const toggle = h("button", { type: "button", class: "cellp-link" }, "Show original");
    let showOriginal = false;
    toggle.addEventListener("click", () => {
      showOriginal = !showOriginal;
      box.replaceChildren(showOriginal ? original : english);
      box.lang = showOriginal ? lang : "en";
      word.textContent = showOriginal ? "Original, in " + languageName(lang) : from;
      toggle.textContent = showOriginal ? "Show English" : "Show original";
    });
    return [box, h("p", { class: "cellp-line" }, word, h("span", { "aria-hidden": "true" }, "·"), toggle)];
  }

  function checkLine(tone, iconName, text) {
    return h("p", { class: "cellp-check", "data-tone": tone }, HB.icon(iconName), h("span", null, text));
  }

  function savedPage(slot, check, pages, item, lang) {
    const text = pages ? pages[item.doc + "#" + item.page] : undefined;
    if (typeof text !== "string") {
      slot.replaceChildren(h("p", { class: "cellp-none" }, "The saved text of this page is not in this view."));
      return;
    }
    const range = findQuote(text, item.quote);
    const box = h("div", { class: "cellp-page", tabindex: "0", role: "region", "aria-label": "Saved text of page " + item.page, lang });
    if (range) box.append(text.slice(0, range[0]), h("mark", null, text.slice(range[0], range[1])), text.slice(range[1]));
    else box.append(text);
    slot.replaceChildren(box);
    if (item.not_found) { /* the check's own verdict, already shown, stands */ }
    else if (!range) check.replaceChildren(checkLine("critical", "cross", "The quote was not found on this page."));
    else if (range[2]) check.replaceChildren(checkLine("warning", "exclamation", "Only part of the quote is word for word on this page; that part is highlighted."));
    else check.replaceChildren(checkLine("good", "tick", "Found on the saved page."));
    requestAnimationFrame(() => {
      const mark = box.querySelector("mark");
      if (mark) box.scrollTop = Math.max(0, mark.offsetTop - box.clientHeight / 3);
    });
  }

  // The quote, its source and the saved page, for one evidence item at a time.
  // Returns {sections, show(i)}; show brings quote i into view.
  function evidenceParts(ctx, row, cell) {
    const evidence = (Array.isArray(cell.evidence) ? cell.evidence : []).filter((e) => e && typeof e === "object");
    if (!evidence.length) {
      const empty = cell.state === "answered" ? "No evidence recorded for this answer."
        : cell.state === "no_evidence" ? "Nothing was found for this question."
          : cell.state === "not_run" ? "Not scored yet, so there is no evidence."
            : "No evidence recorded.";
      return { sections: [section("cellp-quotesec", heading("Quote"), h("p", { class: "cellp-none" }, empty))], show: () => {} };
    }
    const docs = ctx.bundle.documents || {};
    const pages = HB.data.evidence(row);
    const label = h("h3", { class: "cellp-h", tabindex: "-1" }, "Quote");
    const quoteBody = h("div", { class: "cellp-sec" });
    const sourceBody = h("div", { class: "cellp-sec" });
    const pageBody = h("div", { class: "cellp-sec" });
    let index = 0;

    function draw() {
      const item = evidence[index];
      const doc = item.missing ? null : docs[item.doc] || null;
      const pageWord = (item.section ? String(item.section) + " · " : "") + (isNum(item.page) ? "page " + fmtNum(item.page) : "page not recorded");
      label.textContent = evidence.length > 1 ? "Quote " + fmtInt(index + 1) + " of " + fmtInt(evidence.length) : "Quote";

      const [box, langLine] = quoteBox(item);
      const check = h("div");
      if (item.not_found) {
        box.classList.add("is-missing");
        check.append(checkLine("critical", "cross", "Not found word for word in its saved document."));
      }
      quoteBody.replaceChildren(...[box, langLine, check].filter(Boolean));

      if (!doc) {
        sourceBody.replaceChildren(
          h("p", { class: "cellp-doc" }, "Document ", h("span", { class: "mono" }, String(item.doc || "not named"))),
          h("p", { class: "cellp-meta" }, item.section ? pageWord : pageWord.charAt(0).toUpperCase() + pageWord.slice(1)),
          h("p", { class: "cellp-none" }, NOT_KEPT));
        pageBody.replaceChildren(h("p", { class: "cellp-none" }, NOT_KEPT));
        return;
      }
      const meta = [
        doc.publisher ? String(doc.publisher) : hostName(doc.url) || "Publisher not recorded",
        HOST_WORDS[doc.host_type] || null,
        dateText(doc.date) ? "dated " + dateText(doc.date) : "date not recorded",
        pageWord,
      ].filter(Boolean);
      const links = [];
      if (doc.url) links.push(HB.link(doc.url, "Open source"));
      if (doc.archive_url) links.push(HB.link(doc.archive_url, "Archived copy"));
      for (const a of links) if (a.tagName === "A") a.classList.add("btn", "btn--small");
      sourceBody.replaceChildren(...[
        h("p", { class: "cellp-doc" }, doc.title ? String(doc.title) : "Untitled document"),
        h("p", { class: "cellp-meta" }, meta.join(" · ")),
        doc.weak && doc.weak.why ? h("p", { class: "cellp-meta" }, h("strong", null, "Not from the company: "), String(doc.weak.why)) : null,
        links.length ? h("div", { class: "cellp-links" }, links) : null,
      ].filter(Boolean));

      if (doc.kept === false) {
        pageBody.replaceChildren(h("p", { class: "cellp-none" }, NOT_KEPT));
      } else if (!isNum(item.page)) {
        pageBody.replaceChildren(h("p", { class: "cellp-none" }, item.not_found
          ? "This quote is not on any saved page of the document, so there is no page to show. Open the source to look for it."
          : "Page not recorded, so there is no saved page to show."));
      } else {
        pageBody.replaceChildren(h("p", { class: "cellp-none" }, "Loading the saved page…"));
        const want = index;
        pages.then((ev) => {
          if (want !== index || !pageBody.isConnected) return;
          if (!ev) pageBody.replaceChildren(h("p", { class: "cellp-none" }, HB.data.EVIDENCE_MISSING));
          else savedPage(pageBody, check, ev.pages, item, doc.language || item.language || null);
        });
      }
    }

    let head = label;
    if (evidence.length > 1) {
      const step = (dir) => {
        index = (index + dir + evidence.length) % evidence.length;
        draw();
      };
      head = h("div", { class: "cellp-hrow" }, label, h("span", { class: "cellp-pager" },
        h("button", { type: "button", class: "btn btn--small cellp-ibtn", "aria-label": "Previous quote", onclick: () => step(-1) }, chevron(-1)),
        h("button", { type: "button", class: "btn btn--small cellp-ibtn", "aria-label": "Next quote", onclick: () => step(1) }, chevron(1))));
    }
    draw();
    const quoteSec = section("cellp-quotesec", head, quoteBody);
    return {
      sections: [quoteSec, section("cellp-source", heading("Source"), sourceBody), section("cellp-saved", heading("Saved page"), pageBody)],
      show(i) {
        if (!(i >= 0 && i < evidence.length)) return;
        index = i;
        draw();
        quoteSec.scrollIntoView({ block: "start", behavior: "smooth" });
        label.focus({ preventScroll: true });
      },
    };
  }

  function questionPart(qv, unitWord) {
    return h("details", { class: "cellp-more" },
      h("summary", null, "Question and coding rule"),
      h("div", { class: "cellp-more__body" },
        h("div", { class: "cellp-sec" },
          heading(qv && qv.variant ? "Question, as worded for this " + unitWord : "Question"),
          h("p", { class: "cellp-wording" }, qv && qv.wording ? String(qv.wording) : "No wording recorded.")),
        h("div", { class: "cellp-sec" },
          heading("Coding rule"),
          h("p", { class: "cellp-text" }, qv && qv.rule ? String(qv.rule) : "No coding rule recorded."))));
  }

  function ruleLink(id, text) {
    const a = h("a", { href: HB.href("rules", id), class: text ? null : "mono" }, text || String(id));
    // The Rules tab draws every rule; bring this one into view once it is there.
    a.addEventListener("click", () => {
      window.addEventListener("hashchange", () => requestAnimationFrame(() => {
        const el = document.getElementById("rule-" + String(id).replace(/[^A-Za-z0-9_-]/g, "_"));
        if (el) el.scrollIntoView({ block: "start" });
      }), { once: true });
    });
    return a;
  }

  // The inline decision for an open or skipped rule: HB.ruleEditor (rules.js)
  // when it is there, else a link to the rule on the Rules tab.
  function ruleDecision(ctx, id) {
    if (typeof HB.ruleEditor === "function") {
      try {
        const el = HB.ruleEditor(id, ctx, { compact: true });
        if (el instanceof HTMLElement) return el;
      } catch (err) {
        console.error("HB.ruleEditor failed:", err);
      }
    }
    return h("p", { class: "cellp-line" }, ruleLink(id, "Decide " + id + " on the Rules tab"));
  }

  // One rule the answer rests on. `call` is the note's part about this rule when
  // it can be paired (see changePart), else null.
  function ruleBlock(ctx, q, cell, r, call, decision) {
    const options = Array.isArray(r.options) ? r.options : [];
    const optionText = (id) => {
      const o = options.find((x) => x && x.id === id);
      return o && o.text ? String(o.text) : null;
    };
    const top = topPoints(q);
    const decided = r.status === "decided" && r.decision;
    const turned = decided && r.decision.option != null && r.recommended != null && r.decision.option !== r.recommended;
    // The other reading's points, when the note states both readings' answers and
    // its default is this answer.
    let would = null;
    const values = call ? readingValues(call.text) : null;
    const otherLevel = values && sameValue(values.mine, cell.value) ? levelFor(q, values.other) : null;
    if (otherLevel && isNum(otherLevel.points)) {
      const pts = pointsWords(otherLevel.points, top);
      if (sameValue(values.other, values.mine)) would = "Either reading gives " + pts + ", so deciding this rule won't change this answer.";
      else if (turned) would = "Scored again under your decision, this would be " + pts + ".";
      else if (!decided) would = "Would be " + pts + (options.length > 2 ? " under the other reading." : " if you decide this the other way.");
    }
    const ask = firstQuestion(r.ask);
    const lead = turned ? ["You decided ", ruleLink(r.id), " differently from the reading this answer used:"]
      : decided ? ["This follows your decision on ", ruleLink(r.id), ":"]
        : r.status === "skipped" ? ["This answer rests on a rule you skipped (", ruleLink(r.id), "), so it keeps Claude's reading:"]
          : ["This answer rests on a rule you haven't decided yet (", ruleLink(r.id), "):"];
    const reading = call ? plainCall(call.text)
      : decided ? null
        : optionText(r.recommended) ? "Claude's reading: " + sentence(optionText(r.recommended)) : null;
    return h("div", { class: "cellp-rule" },
      would ? h("p", { class: "cellp-would" }, would) : null,
      h("p", null, lead),
      decided ? h("p", { class: "cellp-ask" }, sentence(r.decision.text || optionText(r.decision.option) || "No wording recorded")) : null,
      h("p", { class: decided ? "cellp-sub" : "cellp-ask" }, ask || "No question recorded."),
      reading ? h("p", null, reading) : null,
      turned ? h("p", { class: "cellp-sub" }, "It was scored with Claude's reading, before your decision, so it may change when it is scored again.") : null,
      decided ? h("p", { class: "cellp-line" }, ruleLink(r.id, "See " + r.id + " on the Rules tab")) : decision(r.id));
  }

  // "What would change it": the rules the answer rests on, and the note's parts
  // about readings. A part pairs with a rule only when it is the note's one part
  // naming a call and the answer rests on just that one rule; with more of
  // either, which part goes with which rule is not recorded, so the parts are
  // listed after the rules.
  function changePart(ctx, q, cell, note, decision) {
    const all = Array.isArray(ctx.bundle.rules) ? ctx.bundle.rules : [];
    const ids = Array.isArray(cell.rules) ? cell.rules : [];
    const calls = note ? note.calls : [];
    const keyed = calls.filter((c) => c.key);
    const known = ids.map((id) => all.find((x) => x && x.id === id)).filter(Boolean);
    const paired = ids.length === 1 && known.length === 1 && keyed.length === 1 ? keyed[0] : null;
    const blocks = ids.map((id) => {
      const r = all.find((x) => x && x.id === id);
      if (!r) return h("p", null, "This answer rests on rule ", h("span", { class: "mono" }, String(id)), ", which is not in the project's rules list.");
      return ruleBlock(ctx, q, cell, r, paired, decision);
    });
    const loose = calls.filter((c) => c !== paired);
    const judgement = cell.judgement === true || (Array.isArray(cell.flags) && cell.flags.includes("judgement"));
    if (loose.length) {
      blocks.push(h("div", { class: "cellp-sec" },
        h("p", null, ids.length ? "Claude's notes on these readings:" : "Claude made a judgement call here that no rule on the Rules tab covers yet:"),
        h("ul", { class: "cellp-calls" }, loose.map((c) => h("li", null, c.key ? h("strong", null, keyWords(c.key) + ". ") : null, plainCall(c.text))))));
    } else if (!ids.length && judgement) {
      blocks.push(h("p", null, "Claude made a judgement call here that no rule on the Rules tab covers yet."));
    }
    return blocks.length ? [heading(cell.owner ? "What would change Claude's reading" : "What would change it"), ...blocks] : [];
  }

  // "Why this answer" and "What would change it". The note comes with the row's
  // evidence file, so both are drawn again once it is in.
  function reasonParts(ctx, row, qid, q, cell) {
    const why = h("section", { class: "cellp-sec cellp-why" });
    const change = h("section", { class: "cellp-sec cellp-change" });
    const editors = new Map();      // drawn once, so a half-made decision survives the redraw
    const decision = (id) => {
      if (!editors.has(id)) editors.set(id, ruleDecision(ctx, id));
      return editors.get(id);
    };
    const owned = Boolean(cell && cell.owner);
    const draw = (note) => {
      const reasoning = note && note.reasoning;
      why.replaceChildren(...(reasoning ? [heading(owned ? "Claude's reading" : "Why this answer"), h("p", { class: "cellp-text" }, reasoning),
        h("p", { class: "cellp-sub" }, owned ? "Claude's reasoning from its last read of this question. The owner's answer stands."
          : "Claude's reasoning, written when it scored this answer.")] : []));
      why.hidden = !reasoning;
      const parts = changePart(ctx, q, cell, note, decision);
      change.replaceChildren(...parts);
      change.hidden = !parts.length;
    };
    draw(null);
    HB.data.evidence(row).then((ev) => {
      const text = ev && ev.notes && typeof ev.notes[qid] === "string" ? ev.notes[qid] : "";
      if (text) draw(readNote(text));
    });
    return [why, change];
  }

  /* ---- The panel ----------------------------------------------------------------- */

  function cellPanel(row, qid, ctx) {
    HB.addStyles("cell", CSS);
    listen();
    const unit = ctx.data.unit(row);
    const qv = ctx.data.questionFor(row, qid);
    const cell = ctx.data.cell(row, qid);
    const p = ctx.bundle.project || {};
    const unitWord = p.unit_word || "row";
    const title = qid + (qv && qv.short ? " " + qv.short : "");
    const kicker = unit ? String(unit.name || unit.id) : String(row);

    const content = [navBar(ctx, row, qid), answerPart(ctx, qv, cell, unitWord)];
    // Checked by a person, who corrected it, what a later run read (views/team.js).
    if (cell && HB.team && typeof HB.team.cellPart === "function") content.push(HB.team.cellPart(ctx, row, qid, qv, cell));
    const quotes = cell ? evidenceParts(ctx, row, cell) : null;
    // Why the grid marked it comes straight under the answer: that is what the owner clicked to find out.
    if (cell) content.push(flagsPart(ctx, unit, qv, cell, quotes));
    if (cell && qv) content.push(fixPart(ctx, row, qid, qv));
    if (cell) {
      content.push(...reasonParts(ctx, row, qid, qv, cell));
      content.push(...quotes.sections);
    }
    content.push(questionPart(qv, unitWord));

    // The shell opens the drawer once this returns: mark the cell after that (and,
    // on opening, bring it back into view: a docked panel narrows the grid).
    if (!HB.drawer.isOpen()) nav.scroll = true;
    queueMicrotask(afterOpen);
    return { title, kicker, content: h("div", { class: "cellp" }, content.filter(Boolean)) };
  }

  HB.cellPanel = cellPanel;
})((window.HB = window.HB || {}));
