/* =============================================================================
   Handbook viewer: the comparison with last edition, drawn inside the Results
   tab at #results~compare (results.js calls it; it is not a tab of its own).

   HB.compareView.render(root, ctx)   appends: how far we agree at each level
       (same score, same tier); one row of controls (View, Level, Show); a
       sentence on the chosen level; the chosen view; the rows that could not
       be compared.
   Views, each at its own address (#results~compare~<view>; the reader's last
   choice is kept, the dot plot is the default):
     dots     one row per assessment: this edition (dot) and last year (ring)
              joined by a line, on the score axis with the tier boundaries,
              the gap and its reason's glyph at the end; largest gap first.
     table    every assessment: last year, this edition, the gap, both tiers,
              the reason, who explained it, the note and the cells; sortable,
              filtered by reason, Copy table.
     bars     the gap as a bar left (lower) or right (higher) of zero.
     scatter  last year across, this edition up, the "same as last year"
              diagonal and the squares where the tier is the same.
     tiers    tier last year against tier now, counts in the cells; a cell
              lists its assessments.
     groups   per group of a label (the Grid's group-by): same tier, same
              score, higher, lower and the mean gap.
     rules    which undecided rules the differences rest on (from the cells'
              rules in the bundle), so settling one shows what it would move.
     time     "Over time": a line per assessment (or per group of a label, its
              mean) across every past edition, this run as the last point
              after a dashed step; assessments with a big move in colour.
   Level (overall, each section, or question when last edition goes that far)
   and Show (differences or all) apply to every view that lists assessments.
   Clicking an assessment anywhere opens its comparison in the drawer: every
   figure at every level, each difference's reason, note and cells.
   "Check these first" leads the page: the big differences from the latest
   edition as cards, each closed to one line with what to do, open with the
   action first. An open rule under big differences (sized as compare.py sizes
   a big move) gets one card with its question and the decision inline
   (HB.ruleEditor, else a link to the Rules tab); each row with a big move the
   files do not explain gets one card: nothing found (Send the document, the
   slides' HB.supply), a quote or source problem, or no explanation (buttons
   that open the answers). Expand all and Collapse all work on every card.
   With past editions (compare.json's history) each move has a small line of
   the row's past editions, this run's figure marked.
   The tooltips name an open rule by its question, not its id.
   "Compare with" picks an older edition: the views then set this
   run beside it at the levels it holds, with no reasons (those are worked out
   against the latest edition only), and the words say "2023", not "last year".
   HB.compareView.openDetail(row, ctx) opens one assessment's comparison with the latest edition in the
   drawer (the Summary's biggest movers use it).
   Reads bundle.results.compare (compare/compare.json), and for the By rule
   view the cells' rules and bundle.rules.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, svg, isNum, fmtInt, fmtNum, plural } = HB;
  // Copied from handbook/lib/handbook/bundle.py (REASON_WORDS, BY_WORDS).
  const REASON_WORDS = {
    missing_document: "Missing document",
    different_document: "Different document",
    document_date: "Document date",
    rule_reading: "Rule reading",
    open_rule: "Undecided rule",
    unexplained: "Not yet explained",
  };
  const BY_WORDS = { files: "From the files", claude: "Claude's reading" };
  const LEVEL_WORDS = { question: "question", section: "section", overall: "overall score" };
  const UNDECIDED = ["open", "skipped"];   // as compare.py: a rule in either state is not decided
  const VIEW_KEY = "compare-view";
  const LEVEL_KEY = "compare-level";
  const SHOW_KEY = "compare-show";
  const REASON_KEY = "compare-reason";
  const LINES_KEY = "compare-lines";
  const SEP = "\u0001";
  const MINUS = "−";
  const NOW = "\u0001now";      // this run's place among the editions in the Over time view
  const WHY_ORDER = ["nothing_found", "band_jump", "big_change"];   // as "Check these first" lists them

  const parts = () => HB.resultsParts;
  const bundle = () => HB.data.bundle() || {};
  let refocus = null;   // a control to focus once the tab is drawn again at its new address
  let picked = null;    // an older edition to compare with ("Compare with"); null is the latest

  /* The edition compared with, in words: a noun ("last year", "2023"), the same
     to start a label, an adverb ("last year", "in 2023") and its results. */
  let them = "last year";
  let Them = "Last year";
  let when = "last year";
  let theirResults = "last year's results";

  function setWords(edition) {
    them = edition ? String(edition) : "last year";
    Them = edition ? String(edition) : "Last year";
    when = edition ? `in ${edition}` : "last year";
    theirResults = edition ? `the ${edition} results` : "last year's results";
  }

  /** Runs draw() with the latest edition's words, whatever edition is picked. */
  function latestWords(draw) {
    setWords(null);
    try { return draw(); } finally { setWords(picked); }
  }

  /* ---- Reason glyphs: 16x16, drawn in currentColor; the word always goes with them -- */

  const GLYPHS = {
    open_rule: [["circle", { cx: 8, cy: 8, r: 5.7, class: "g-l" }], ["circle", { cx: 8, cy: 8, r: 2, class: "g-f" }]],
    unexplained: [["circle", { cx: 8, cy: 8, r: 6.3, class: "g-l" }],
      ["path", { d: "M6.3 6.4a1.8 1.8 0 1 1 2.6 1.6c-.6.3-.9.7-.9 1.3", class: "g-l" }], ["circle", { cx: 8, cy: 11.6, r: 0.9, class: "g-f" }]],
    missing_document: [["path", { d: "M4 2.5h5.5L12 5v8.5H4z", class: "g-l" }], ["path", { d: "M2.8 13.6L13.2 3", class: "g-l" }]],
    different_document: [["path", { d: "M5 2.5h5l2 2v7H5z", class: "g-l" }], ["path", { d: "M3 5v8.5h6.5", class: "g-l" }]],
    document_date: [["path", { d: "M2.8 4h10.4v9.2H2.8zM2.8 7h10.4M5.5 2.5v3M10.5 2.5v3", class: "g-l" }]],
    rule_reading: [["path", { d: "M2.5 3.5c2 0 4 .5 5.5 1.6 1.5-1.1 3.5-1.6 5.5-1.6v9c-2 0-4 .5-5.5 1.6-1.5-1.1-3.5-1.6-5.5-1.6z", class: "g-l" }],
      ["path", { d: "M8 5.1v9", class: "g-l" }]],
    other: [["path", { d: "M8 2.5l5.5 5.5L8 13.5 2.5 8z", class: "g-l" }]],
  };

  function glyph(reason) {
    const node = svg("svg", { class: "cmp-g", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" });
    (GLYPHS[reason] || GLYPHS.other).forEach(([tag, attrs]) => node.appendChild(svg(tag, attrs)));
    return node;
  }

  const reasonWord = (d) => (d ? REASON_WORDS[d.reason] || String(d.reason) : "");
  const byWord = (d) => (d && d.reason !== "unexplained" ? BY_WORDS[d.by] || d.by || HB.NOT_RECORDED : null);
  const reasonText = (d) => (d ? reasonWord(d) + (byWord(d) ? ` (${byWord(d).toLowerCase()})` : "") : "");

  /* ---- Names and keys --------------------------------------------------------- */

  function rowName(id) {
    const u = HB.data.unit(id);
    return u && u.name ? u.name : String(id);
  }

  const capital = (s) => String(s).replace(/^./, (c) => c.toUpperCase());

  /** Band keys first, then the overall score, sections and questions. */
  function rank(key) {
    if (key.startsWith("band:")) return 0;
    if (key === "overall") return 1;
    const sheet = bundle().scoresheet || {};
    return (sheet.sections || []).some((s) => s && s.id === key) ? 2 : 3;
  }

  function orderedKeys(c) {
    const keys = Object.keys((c.summary || {}).of || (c.summary || {}).agree || {});
    return keys.map((k, i) => [k, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map((x) => x[0]);
  }

  /** "Overall", "Pillar 1" (a section's name up to its colon), "Q1.1" (an id that starts with a letter, like "N1", as it is). */
  function levelShort(key) {
    if (key === "overall") return "Overall";
    const sheet = bundle().scoresheet || {};
    const s = (sheet.sections || []).find((x) => x && x.id === key);
    if (s) {
      const name = String(s.name || "").trim();
      const colon = name.indexOf(":");
      if (colon > 0) return name.slice(0, colon).trim();
      return name && name.length <= 18 ? name : String(key);
    }
    return HB.data.question(key) && /^\d/.test(String(key)) ? `Q${key}` : String(key);   // "Q1.1", but "N1" stays as it is
  }

  const levelLong = (key) => parts().keyName(key);

  /* ---- Ask Claude why: a short reason for an unexplained difference, in place -------------- */

  const why = { sample: undefined, answers: new Map() };   // answers: "row|key" -> {phase, text}

  /** Whether the page may ask Claude; looks once (the `sample` capability), then remembers. */
  function canAsk() {
    if (why.sample === undefined) {
      why.sample = null;
      const c = typeof window !== "undefined" ? window.claude : null;
      if (c && typeof c.use === "function") {
        Promise.resolve(c.use("sample")).then((s) => { why.sample = typeof s === "function" ? s : false; }, () => { why.sample = false; });
      } else why.sample = false;
    }
    return why.sample !== false;
  }

  const clip = (t, n) => { const x = String(t || "").replace(/\s+/g, " ").trim(); return x.length > n ? x.slice(0, n - 1) + "\u2026" : x; };

  /** What Claude is told: the figure both ways, and this edition's answers and quotes behind it. */
  function whyPrompt(rowId, d) {
    const b = bundle();
    const unit = (b.units || []).find((u) => u && u.id === rowId) || {};
    const was = /^\d{4}$/.test(them) ? "the " + them + " edition" : "last year's edition";
    const lines = [
      "A benchmark compares this edition's scores with those published for " + was + ". One figure differs and nobody has explained why yet.",
      "Organisation: " + (unit.name || rowId) + ". Figure: " + levelLong(d.key) + ". Published for " + was + ": " + d.theirs +
        "; this edition gives " + d.ours + ".",
      "This edition's answers behind it (question, answer, flags, a quote):",
    ];
    (Array.isArray(d.cells) ? d.cells : []).forEach((qid) => {
      const c = HB.data.cell(rowId, qid) || {};
      const q = HB.data.questionFor(rowId, qid) || {};
      const level = ((q.answer || {}).levels || []).find((l) => l.value === c.value);
      const ev = (c.evidence || []).find((e) => e && (e.quote_en || e.quote)) || null;
      const quote = ev ? ev.quote_en || ev.quote : null;
      const doc = ev && ev.doc ? (b.documents || {})[ev.doc] : null;
      const from = doc ? " (from " + clip(doc.title || ev.doc, 60) + (doc.date ? ", dated " + doc.date : "") + ")" : "";
      lines.push("- " + qid + " " + clip(q.text, 140) + ": " + (c.value == null ? "no answer" : c.value) +
        (level ? " (" + clip(level.label, 70) + ")" : "") + (c.flags && c.flags.length ? "; flagged: " + c.flags.map((f) => ((HB.flagKinds || {}).words || {})[f] || f).join(", ") : "") +
        (quote ? '; quote: "' + clip(quote, 180) + '"' + from : ""));
    });
    lines.push("Pick the first of these the answers support: missing_document (an answer has no evidence, or a document the " +
      "question needs was not found); different_document (we cite a different document on the subject, such as a group " +
      "policy instead of a national page); document_date (a cited document is dated after " + was + "); rule_reading (a " +
      "decided rule's reading decides the answer and the quote supports the other reading too); unexplained (the answers " +
      "do not show a cause).");
    lines.push("Reply in exactly two lines. Line 1: REASON: and one of those words. Line 2: NOTE: one sentence under 35 words " +
      "stating what this edition's answers show and naming the question, then at most a short \u201cso\u201d. Use " +
      "document titles and dates, not ids. Never say what last year's assessor saw or did. Plain UK English, no em dashes.");
    return lines.join("\n");
  }

  const WHY_REASONS = ["missing_document", "different_document", "document_date", "rule_reading", "unexplained"];

  /** {reason, note} from Claude's two lines; a reply in another shape is kept whole as the note. */
  function readWhy(text) {
    const t = String(text || "").trim();
    const r = /REASON:\s*([a-z_]+)/i.exec(t);
    const n = /NOTE:\s*([\s\S]+)/i.exec(t);
    const reason = r && WHY_REASONS.includes(r[1].toLowerCase()) ? r[1].toLowerCase() : "unexplained";
    return { reason, note: (n ? n[1] : t.replace(/REASON:[^\n]*\n?/i, "")).trim() };
  }

  /** The "Ask Claude why" box for one unexplained difference; keeps its answer for the visit. */
  function askWhy(rowId, d) {
    const id = rowId + "|" + d.key;
    const box = h("div", { class: "cmp-ask", "aria-live": "polite" });
    const paint = () => {
      const a = why.answers.get(id);
      if (!canAsk()) return box.replaceChildren(h("p", { class: "muted" }, "Not explained yet."));
      if (!a) {
        return box.replaceChildren(h("p", { class: "muted" }, "Not explained yet. ",
          h("button", { type: "button", class: "btn btn--small btn--primary", onclick: ask }, "Ask Claude why")));
      }
      if (a.phase === "thinking") return box.replaceChildren(h("p", { class: "muted" }, "Claude is reading the answers\u2026"));
      if (a.phase === "failed") {
        return box.replaceChildren(h("p", { class: "muted" }, a.text + " ",
          h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: ask }, "Try again")));
      }
      const canKeep = Boolean(HB.local) && HB.edits && HB.edits.ready && typeof HB.edits.save === "function";
      box.replaceChildren(
        h("p", { class: "cmp-ask__text" }, a.reason !== "unexplained" ? h("strong", null, "Probably: " + (REASON_WORDS[a.reason] || a.reason) + ". ") : null, a.text),
        a.phase === "saved" ? h("p", { class: "small" }, "Saved as the reason. Next time you open this panel it shows as Claude's reading.")
          : a.phase === "saving" ? h("p", { class: "small muted" }, "Saving\u2026")
          : canKeep ? h("p", null, h("button", { type: "button", class: "btn btn--small btn--primary", onclick: keep }, "Use this as the reason"),
            a.saveError ? h("span", { class: "small" }, " " + a.saveError) : null) : null,
        h("p", { class: "small muted" }, "Claude's suggestion from this edition's answers; check it before relying on it. ",
          h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: ask }, "Ask again"),
          HB.chat && typeof HB.chat.open === "function"
            ? h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => HB.chat.open() }, "Ask a follow-up") : null));
    };
    async function ask() {
      if (!why.sample) { why.answers.set(id, { phase: "failed", text: "Claude can't be asked from this page just now." }); return paint(); }
      why.answers.set(id, { phase: "thinking" });
      paint();
      try {
        const res = await why.sample(whyPrompt(rowId, d), { modelTier: "default", cache: false });
        const got = readWhy(res && res.text);
        why.answers.set(id, { phase: "done", reason: got.reason, text: got.note || "Claude had nothing to add." });
      } catch (e) {
        why.answers.set(id, { phase: "failed", text: "Claude couldn't answer just now." });
      }
      paint();
    }
    async function keep() {
      const a = why.answers.get(id);
      if (!a || a.phase !== "done") return;
      why.answers.set(id, Object.assign({}, a, { phase: "saving", saveError: null }));
      paint();
      try {
        await HB.edits.save("reasons", { row: rowId, key: d.key, ours: d.ours, theirs: d.theirs, reason: a.reason,
          note: a.text, cells: Array.isArray(d.cells) ? d.cells : [], at: new Date().toISOString() });
        why.answers.set(id, Object.assign({}, a, { phase: "saved" }));
      } catch (e) {
        why.answers.set(id, Object.assign({}, a, { phase: "done", saveError: "The reason could not be saved just now. Try again." }));
      }
      paint();
    }
    paint();
    return box;
  }

  /** The cells of a row that a key covers, as [row, qid] pairs. */
  function cellsUnder(row, key) {
    const qs = bundle().questions || [];
    let ids;
    if (key === "overall" || key.startsWith("band:")) ids = qs.map((q) => q.id);
    else if (qs.some((q) => q.id === key)) ids = [key];
    else ids = qs.filter((q) => q.section === key).map((q) => q.id);
    return ids.filter((qid) => HB.data.cell(row, qid)).map((qid) => [row, qid]);
  }

  /* ---- The model: one item per assessment at a level --------------------------- */

  function makeModel(c) {
    const rows = Array.isArray(c.rows) ? c.rows : [];
    const dis = new Map();
    (c.disagreements || []).forEach((d) => dis.set(d.row + SEP + d.key, d));
    const levels = orderedKeys(c).filter((k) => !k.startsWith("band:") &&
      rows.some((r) => isNum((r.ours || {})[k]) || isNum((r.theirs || {})[k])));
    return { c, rows, dis, levels };
  }

  function itemAt(m, r, L) {
    const o = r.ours || {};
    const t = r.theirs || {};
    const ours = o[L];
    const theirs = t[L];
    const has = isNum(ours) && isNum(theirs);
    const gap = has ? Math.round((ours - theirs) * 10) / 10 : null;
    const bk = "band:" + L;
    const tierO = o[bk] == null ? null : String(o[bk]);
    const tierT = t[bk] == null ? null : String(t[bk]);
    const d = m.dis.get(r.row + SEP + L) || m.dis.get(r.row + SEP + bk) || null;
    return {
      id: r.row, name: rowName(r.row), L, ours, theirs, has, gap,
      same: has && (gap === 0 || (r.agree || {})[L] === true),
      tierO, tierT, hasTier: tierO != null || tierT != null, tierSame: tierO != null && tierO === tierT, d,
    };
  }

  /** Largest gap first, then the higher score last year, then by name. */
  function byGap(list) {
    return list.slice().sort((a, b) => (b.has - a.has) || (Math.abs(b.gap || 0) - Math.abs(a.gap || 0)) ||
      ((b.theirs || 0) - (a.theirs || 0)) || a.name.localeCompare(b.name, "en-GB"));
  }

  /** The scale and bands of a level. */
  function scaleFor(m, L) {
    const values = m.rows.flatMap((r) => [(r.ours || {})[L], (r.theirs || {})[L]]).filter(isNum);
    const pct = rank(L) < 3 && values.every((v) => v >= 0 && v <= 100);
    const set = parts().bandSets().find((s) => s.of === L) || null;
    const word = set ? parts().bandWord(set) : "band";
    const short = (name) => {
      const bits = String(name).trim().split(/\s+/);
      return word !== "band" && bits.length > 1 && bits[0].toLowerCase() === word ? bits[1] : String(name);
    };
    const bands = set ? set.bands.map((b) => ({ min: b.min, label: b.name, short: short(b.name) })) : [];
    return { pct, max: pct ? 100 : undefined, suffix: pct ? "%" : "", set, bands, word, short };
  }

  const num = (v, sc) => (isNum(v) ? fmtNum(v, 1) + sc.suffix : "–");
  const signed = (g) => (!isNum(g) ? "–" : g > 0 ? "+" + fmtNum(g, 1) : g < 0 ? MINUS + fmtNum(-g, 1) : "0");
  const gapWords = (g) => (g === 0 ? "the same score" : `${fmtNum(Math.abs(g), 1)} ${Math.abs(g) === 1 ? "point" : "points"} ${g > 0 ? "higher" : "lower"}`);

  /** "Tier 3" on screen, the band's full name for screen readers and in its title. */
  function tierCell(name, sc) {
    if (name == null) return h("span", { class: "muted" }, "–");
    const short = sc.short(name);
    if (short === name) return name;
    const shown = `${capital(sc.word)} ${short}`;
    return h("span", { title: name }, shown, h("span", { class: "visually-hidden" }, name.slice(shown.length)));
  }

  function tierWords(a, sc) {
    if (!a.hasTier) return "";
    return a.tierSame ? `, same ${sc.word}` : `, now ${a.tierO || `no ${sc.word}`} (${when} ${a.tierT || `no ${sc.word}`})`;
  }

  /* ---- Open rules in plain words ---------------------------------------------------- */

  const rulesById = () => new Map((Array.isArray(bundle().rules) ? bundle().rules : []).map((r) => [r.id, r]));

  /** A rule's topic ("Third-party auditing") and its question without the topic or the points line
      after it ("must the audit be against ECC-aligned welfare standards, or ... count?"). */
  function ruleWords(rule) {
    let ask = String((rule && rule.ask) || "").replace(/\s+/g, " ").trim();
    let topic = "";
    const colon = ask.indexOf(": ");
    if (colon > 0 && colon <= 48 && !/[?.]/.test(ask.slice(0, colon))) {
      topic = ask.slice(0, colon);
      ask = ask.slice(colon + 2);
    }
    const mark = ask.indexOf("?");
    if (mark > 0) ask = ask.slice(0, mark + 1);
    else if (ask.length > 160) ask = ask.slice(0, 157).replace(/\s+\S*$/, "") + "…";
    return { topic, question: ask || String((rule && rule.id) || "") };
  }

  const questionWords = (qs) => (qs.length === 1 ? `question ${qs[0]}`
    : `questions ${qs.slice(0, -1).join(", ")} and ${qs[qs.length - 1]}`);

  /** Whether a run that started after the rule was decided has finished scoring the row on one of
      the rule's questions: the decision has then reached that row's answers. */
  function rescoredSince(rule, row) {
    const at = Date.parse((rule.decision && rule.decision.at) || "");
    if (!Number.isFinite(at)) return false;
    const qs = new Set((rule.question_ids || []).map(String));
    return (Array.isArray(bundle().runs) ? bundle().runs : []).some((r) => r && r.finished && Date.parse(r.started || "") >= at
      && (!Array.isArray(r.rows) || r.rows.includes(row))
      && (!Array.isArray(r.questions) || !qs.size || r.questions.some((q) => qs.has(String(q)))));
  }

  /** The rules still open or skipped under some cells of a row, as [{rule, qs}], in rule order. With
      {unapplied: true}, also those decided since and not yet re-run on this row ("Check these first"
      keeps them, marked Decided, until the re-run). */
  function openRulesUnder(row, qids, { unapplied = false } = {}) {
    const byId = rulesById();
    const found = new Map();
    qids.forEach((q) => {
      const cell = HB.data.cell(row, q) || {};
      (Array.isArray(cell.rules) ? cell.rules : []).forEach((rid) => {
        const rule = byId.get(rid);
        if (!rule) return;
        if (!UNDECIDED.includes(rule.status) && !(unapplied && rule.status === "decided" && !rescoredSince(rule, row))) return;
        if (!found.has(rid)) found.set(rid, { rule, qs: [] });
        if (!found.get(rid).qs.includes(q)) found.get(rid).qs.push(q);
      });
    });
    return Array.from(found.values()).sort((x, y) => String(x.rule.id).localeCompare(String(y.rule.id), "en-GB", { numeric: true }));
  }

  /** An open-rule difference's rules by their questions, as lines; null for another reason or
      when every rule under it has been decided since. */
  function ruleLines(a) {
    if (!a.d || a.d.reason !== "open_rule") return null;
    const open = openRulesUnder(a.id, Array.isArray(a.d.cells) && a.d.cells.length ? a.d.cells : cellsUnder(a.id, a.L).map((x) => x[1]));
    if (!open.length) return null;
    const lines = open.slice(0, 2).map(({ rule, qs }) => `Rests on an open rule: ${ruleWords(rule).question} (${questionWords(qs)})`);
    if (open.length > 2) lines.push(`And ${plural(open.length - 2, "more open rule", "more open rules")}`);
    return lines;
  }

  /** A difference's reason as lines for the tooltip: an open rule by its question, else the
      reason's words and its note. */
  const reasonLines = (a) => (!a.d ? [] : ruleLines(a) || [reasonText(a.d), a.d.note || null]);

  function ariaFor(a, sc) {
    const rules = ruleLines(a);
    return `${a.name}: this edition ${num(a.ours, sc)}, ${them} ${num(a.theirs, sc)}, ` +
      (a.has ? gapWords(a.gap) : "not in both editions") + tierWords(a, sc) + "." +
      (rules ? rules.map((x) => ` ${x}${/[?.]$/.test(x) ? "" : "."}`).join("")
        : (a.d ? ` Reason: ${reasonText(a.d)}.` : "") + (a.d && a.d.note ? ` ${a.d.note}` : ""));
  }

  function tipFor(a, sc) {
    return {
      title: a.name,
      rows: [
        { strong: num(a.ours, sc), text: "this edition" + (a.tierO ? `, ${a.tierO}` : "") },
        { strong: num(a.theirs, sc), text: them + (a.tierT ? `, ${a.tierT}` : "") },
        a.has ? capital(gapWords(a.gap)) + (a.hasTier ? (a.tierSame ? `, same ${sc.word}` : `, different ${sc.word}`) : "") : "Not in both editions",
        ...reasonLines(a),
      ],
    };
  }

  /** The legend's reason items: a glyph and its word for each reason in the list. */
  function reasonLegend(items) {
    const seen = [];
    items.forEach((a) => { if (a.d && !seen.includes(a.d.reason)) seen.push(a.d.reason); });
    const order = Object.keys(REASON_WORDS);
    seen.sort((x, y) => (order.indexOf(x) + 99 * (order.indexOf(x) < 0)) - (order.indexOf(y) + 99 * (order.indexOf(y) < 0)));
    return seen.map((r) => h("li", { class: "legend__item" }, glyph(r), REASON_WORDS[r] || r));
  }

  /* ---- How far we agree ------------------------------------------------------------ */

  /* The run re-scores an edition already published (the held-back results are that edition). */
  function sameEdition(c) {
    const p = bundle().project || {};
    return c && c.edition != null && p.edition != null && String(c.edition) === String(p.edition);
  }

  function agreement(c, m) {
    const s = c.summary || {};
    const nouns = parts().unitNouns();
    const level = LEVEL_WORDS[c.level] || c.level;
    const notCompared = (s.not_compared || []).length;
    const cell = (k) => {
      const of = (s.of || {})[k];
      if (!isNum(of) || of <= 0) return h("td", { class: "cmp-agree__c" }, h("span", { class: "muted" }, "–"));
      const agree = (s.agree || {})[k] || 0;
      return h("td", { class: "cmp-agree__c" },
        h("span", { class: "cmp-agree__v" }, h("strong", null, fmtInt(agree)), ` of ${fmtInt(of)}`,
          h("span", { class: "visually-hidden" }, ` (${HB.fmtPct(agree, of)})`)),
        h("span", { class: "cmp-meter", "aria-hidden": "true" }, h("span", { style: { width: `${((100 * agree) / of).toFixed(1)}%` } })));
    };
    const anyBand = m.levels.some((L) => (s.of || {})["band:" + L] != null);
    const set = parts().bandSets()[0];
    const word = set ? parts().bandWord(set) : "band";
    return h("section", { class: "section cmp-top", "aria-labelledby": "cmp-head" },
      h("div", { class: "section__head" }, h("h3", { id: "cmp-head" }, `How far we agree with ${them}`)),
      sameEdition(c) ? HB.notice("neutral", `This run re-scores the ${c.edition} edition, so "last year" here means the published ${c.edition} scores.`) : null,
      h("p", { class: "small muted cmp-measure" },
        `${plural(s.rows != null ? s.rows : (c.rows || []).length, nouns.one, nouns.many)} compared with ${theirResults}, ` +
        `which go down to ${level} level.` +
        (notCompared ? ` ${plural(notCompared, nouns.one, nouns.many)} not compared (listed at the end).` : "")),
      h("div", { class: "table-scroll cmp-agree-wrap", role: "region", tabindex: "0", "aria-labelledby": "cmp-head" },
        h("table", { class: "table cmp-agree" },
          h("thead", null, h("tr", null, h("th", { scope: "col" }, "Level"), h("th", { scope: "col" }, "Same score"),
            anyBand ? h("th", { scope: "col" }, `Same ${word}`) : null)),
          h("tbody", null, m.levels.map((L) => h("tr", null,
            h("th", { scope: "row" }, levelShort(L)), cell(L), anyBand ? cell("band:" + L) : null))))));
  }

  /* ---- Controls --------------------------------------------------------------------- */

  /** A labelled segmented control; set(value) moves the pressed button, text(value, t) relabels one. */
  function segmented(label, options, current, onPick, focusKey) {
    const id = HB.uid("cmp-seg");
    const buttons = new Map();
    const group = h("div", { class: "segmented cmp-seg", role: "group", "aria-labelledby": id });
    options.forEach(([value, text]) => {
      const b = h("button", {
        type: "button", class: "btn btn--small", "aria-pressed": String(value === current),
        "data-focus": focusKey ? `${focusKey}-${value}` : null, onclick: () => onPick(value),
      }, text);
      buttons.set(value, b);
      group.appendChild(b);
    });
    return {
      node: h("div", { class: "cmp-tool" }, h("span", { class: "cmp-tool__label", id }, label), group),
      set(value) { buttons.forEach((b, k) => b.setAttribute("aria-pressed", String(k === value))); },
      text(value, t) { const b = buttons.get(value); if (b) b.textContent = t; },
    };
  }

  /** A labelled select, for a choice with too many options for buttons. */
  function chooser(label, options, current, onPick, focusKey) {
    const id = HB.uid("cmp-sel");
    const select = h("select", { class: "input input--small cmp-select", id, "data-focus": focusKey || null },
      options.map(([value, text]) => h("option", { value, selected: value === current }, text)));
    select.addEventListener("change", () => onPick(select.value));
    return {
      node: h("div", { class: "cmp-tool" }, h("label", { class: "cmp-tool__label", for: id }, label), select),
      set(value) { select.value = value; },
      text(value, t) { const o = Array.from(select.options).find((x) => x.value === value); if (o) o.textContent = t; },
    };
  }

  /* ---- The comparison of one assessment, in the drawer ------------------------------- */

  /** Links to the cells and a "Show on grid" button; compact, more than three
      cells become one button (the drawer lists each). */
  function cellLinks(row, cells, ctx, compact) {
    if (!cells.length) return h("span", { class: "muted" }, "None named");
    if (compact && cells.length > 3) {
      return h("button", { type: "button", class: "btn btn--small btn--quiet cmp-cellsbtn",
        title: cells.join(", "), onclick: () => { HB.drawer.close(); ctx.highlight(cells.map((q) => [row, q])); } },
      `${fmtInt(cells.length)} on grid`, h("span", { class: "visually-hidden" }, `: show ${rowName(row)}, questions ${cells.join(", ")}`));
    }
    return h("span", { class: "cmp-cells" },
      cells.map((q) => h("a", { class: "mono", href: ctx.href("grid", row, q), title: `Open ${rowName(row)}, question ${q}` }, q)),
      h("button", { type: "button", class: "btn btn--small btn--quiet",
        onclick: () => { HB.drawer.close(); ctx.highlight(cells.map((q) => [row, q])); } }, "Show on grid"));
  }

  function openDetail(rowId, m, ctx) {
    const r = m.rows.find((x) => x.row === rowId);
    if (!r) return;
    const keys = [];
    m.levels.forEach((L) => { keys.push(L); if ((r.ours || {})["band:" + L] != null || (r.theirs || {})["band:" + L] != null) keys.push("band:" + L); });
    Object.keys(r.ours || {}).concat(Object.keys(r.theirs || {})).forEach((k) => { if (!keys.includes(k)) keys.push(k); });
    const lines = keys.map((k) => {
      const band = k.startsWith("band:");
      const sc = scaleFor(m, band ? k.slice(5) : k);
      const ours = (r.ours || {})[k];
      const theirs = (r.theirs || {})[k];
      const d = m.dis.get(r.row + SEP + k);
      const same = (r.agree || {})[k] === true;
      const show = (v) => (band ? tierCell(v == null ? null : String(v), sc) : num(v, sc));
      const gap = band ? (same ? "Same" : "Differs") : isNum(ours) && isNum(theirs) ? signed(Math.round((ours - theirs) * 10) / 10) : "–";
      return h("tr", null,
        h("th", { scope: "row" }, band ? `${levelShort(k.slice(5))} ${sc.word}` : levelShort(k)),
        h("td", { class: "num" }, show(theirs)),
        h("td", { class: "num" }, show(ours)),
        h("td", { class: "num" }, h("span", { class: "cmp-reason" }, gap, d ? glyph(d.reason) : null,
          d ? h("span", { class: "visually-hidden" }, `: ${reasonWord(d)}`) : null)));
    });
    const table = h("div", { class: "table-scroll", role: "region", tabindex: "0", "aria-label": "Every figure, both years" },
      h("table", { class: "table cmp-detail" },
        h("thead", null, h("tr", null, ["What", Them, "This edition", "Gap"].map((t, i) =>
          h("th", { scope: "col", class: i ? "num" : null }, t)))),
        h("tbody", null, lines)));
    const seen = [];
    (m.c.disagreements || []).forEach((d) => { if (d.row === rowId && !seen.includes(d.reason)) seen.push(d.reason); });
    const key = seen.length ? h("ul", { class: "legend" }, seen.map((x) => h("li", { class: "legend__item" }, glyph(x), REASON_WORDS[x] || x))) : null;
    const differences = (m.c.disagreements || []).filter((d) => d.row === rowId);
    const whys = differences.map((d) => {
      const rules = ruleLines({ id: rowId, d, L: d.key });   // an open rule by its question, in place of the note
      return h("div", { class: "cmp-why" },
        h("h3", { class: "cmp-why__head" }, levelLong(d.key)),
        h("p", { class: "cmp-why__line" }, h("span", { class: "cmp-reason" }, glyph(d.reason), h("strong", null, reasonWord(d))),
          byWord(d) ? HB.statusChip("neutral", byWord(d), { icon: d.by === "files" ? "tick" : "person" }) : null),
        rules ? rules.map((x) => h("p", null, x)) : d.note ? h("p", null, d.note)
          : d.reason === "unexplained" ? askWhy(rowId, d) : h("p", { class: "muted" }, "No note yet."),
        h("p", { class: "small" }, "Cells: ", cellLinks(rowId, Array.isArray(d.cells) ? d.cells : [], ctx)));
    });
    const all = cellsUnder(rowId, "overall");
    const allSame = Object.values(r.agree || {}).every(Boolean);
    const content = h("div", { class: "stack" },
      table, key,
      differences.length
        ? h("div", { class: "stack" }, h("h3", { class: "cmp-why__title" }, `Why they differ (${fmtInt(differences.length)})`),
          h("p", { class: "small muted" }, "\"From the files\": the script found the reason in the documents and dates on record. " +
            "\"Claude's reading\": Claude explained it from the evidence."), whys)
        : allSame ? HB.notice("good", `Every figure is the same as ${when}.`)
          : HB.notice("neutral", "Reasons for the differences are worked out against the latest past edition only."),
      all.length ? h("p", null, h("button", { type: "button", class: "btn btn--small",
        onclick: () => { HB.drawer.close(); ctx.highlight(all); } }, "Show this assessment on the grid")) : null);
    HB.drawer.open(rowName(rowId), content, { kicker: `Compared with ${them}` });
  }

  /* ---- A list of picked assessments (under Tier moves, By group, By rule) ------------ */

  function pickedList(title, items, v) {
    return h("section", { class: "cmp-picked", "aria-label": title },
      h("h4", { class: "cmp-picked__title" }, title),
      items.length
        ? h("ul", { class: "cmp-picked__list" }, byGap(items).map((a) => h("li", null,
          h("button", { type: "button", class: "cmp-picked__btn", onclick: () => v.open(a.id) },
            h("span", null, a.name), h("span", { class: "cmp-picked__gap" }, a.has ? signed(a.gap) : "–", a.d ? glyph(a.d.reason) : null),
            h("span", { class: "visually-hidden" }, `: ${ariaFor(a, v.sc)}`)))))
        : h("p", { class: "small muted" }, "None."));
  }

  /* ---- Views --------------------------------------------------------------------- */

  function dotsView(v) {
    const list = v.shown.filter((a) => isNum(a.ours) || isNum(a.theirs));
    const W = capital(v.sc.word);
    const nouns = parts().unitNouns();
    return HB.charts.dumbbell({
      label: `${levelLong(v.L)}: this edition against ${them}, one row per ${nouns.one}`,
      rows: list.map((a) => ({
        id: a.id, label: a.name, a: a.ours, b: a.theirs, endText: a.has ? signed(a.gap) : "–",
        glyph: a.d ? glyph(a.d.reason) : null, aria: ariaFor(a, v.sc), tip: tipFor(a, v.sc),
        twin: [a.tierT || "–", a.tierO || "–", a.d ? reasonText(a.d) : a.same ? "Same" : "–"],
      })),
      max: v.sc.max, suffix: v.sc.suffix, bands: v.sc.bands,
      aLabel: "This edition", bLabel: Them, bandLabel: v.sc.bands.length ? W : "",
      boundaryLabel: `${W} boundary`, endHead: "Gap", labelHeader: capital(nouns.one),
      twinHead: [`${W} ${when}`, `${W} now`, "Reason"],
      legendExtra: reasonLegend(list),
      onPick: v.open,
      empty: v.state.show === "diff" ? `No ${nouns.one} differs from ${them} at this level.` : "Nothing to show at this level.",
      caption: `Largest gap first. The gap is this edition minus ${them}${v.sc.pct ? ", in points" : ""}. ` +
        "Click a row for its details. With the keyboard, Tab to the chart, move with the arrow keys and press Enter.",
    });
  }

  function barsView(v) {
    const list = v.shown.filter((a) => a.has);
    const nouns = parts().unitNouns();
    const left = v.shown.length - list.length;
    return HB.charts.diverging({
      label: `${levelLong(v.L)}: the gap to ${them}, one bar per ${nouns.one}`,
      rows: list.map((a) => ({
        id: a.id, label: a.name, value: a.gap, valueText: signed(a.gap), glyph: a.d ? glyph(a.d.reason) : null,
        aria: ariaFor(a, v.sc), tip: tipFor(a, v.sc),
        twin: [num(a.theirs, v.sc), num(a.ours, v.sc), a.d ? reasonText(a.d) : a.same ? "Same" : "–"],
      })),
      lowLabel: "Lower this edition", highLabel: "Higher this edition",
      labelHeader: capital(nouns.one), valueHeader: "Gap", twinHead: [Them, "This edition", "Reason"],
      legendExtra: reasonLegend(list),
      onPick: v.open,
      empty: v.state.show === "diff" ? `No ${nouns.one} differs from ${them} at this level.` : "Nothing to show at this level.",
      note: left ? `Not shown, without a figure in both years: ${plural(left, nouns.one, nouns.many)}.` : null,
      caption: "Largest gap first. Bars run left of zero where this edition is lower and right where it is higher. " +
        "Click a row for its details. With the keyboard, Tab to the chart, move with the arrow keys and press Enter.",
    });
  }

  function scatterView(v) {
    const nouns = parts().unitNouns();
    const max = v.sc.max || Math.max(1, ...v.items.flatMap((a) => [a.ours, a.theirs]).filter(isNum));
    const far = byGap(v.items.filter((a) => a.has && !a.same && Math.abs(a.gap) >= max * 0.15)).slice(0, 8).map((a) => a.id);
    const bands = v.sc.bands.slice().sort((x, y) => x.min - y.min);
    const zones = bands.map((b, i) => ({ lo: Math.max(0, b.min), hi: i + 1 < bands.length ? bands[i + 1].min : max, label: b.short }));
    return HB.charts.scatterRanges({
      label: `${levelLong(v.L)}: this edition against ${them}, one dot per ${nouns.one}`,
      points: v.items.map((a) => ({
        id: a.id, label: a.name, x: a.theirs, y: a.ours, tag: far.includes(a.id), muted: v.state.show === "diff" && a.same,
        note: [a.has ? capital(gapWords(a.gap)) + tierWords(a, v.sc) : null, ...reasonLines(a)]
          .filter(Boolean).map((x) => (/[?.]$/.test(x) ? x : x + ".")).join(" "),
      })),
      xLabel: Them, yLabel: "This edition", max: v.sc.max,
      pointLabel: v.state.show === "diff" ? `Differs from ${them}` : capital(nouns.one),
      mutedLabel: `Same score as ${them}`,
      guides: bands.map((b) => b.min).filter((x) => x > 0),
      zones, zonesLabel: bands.length ? `Same ${v.sc.word} both years` : null,
      diagonalLabel: `Same as ${them}`,
      labelHeader: capital(nouns.one),
      onPick: v.open,
      caption: `Each dot is one ${nouns.one}: above the dashed line, higher this edition; below it, lower.` +
        (bands.length ? ` Inside a shaded square the ${v.sc.word} is the same both years.` : "") +
        (far.length ? " The largest gaps are named; point at or focus any dot for its name." : " Point at or focus a dot for its name.") +
        " Click a dot for its details.",
    });
  }

  function tiersView(v) {
    const sc = v.sc;
    const nouns = parts().unitNouns();
    if (!sc.set) return HB.notice("neutral", `There are no ${sc.word}s at this level, so there are no moves to show.`);
    const bands = sc.set.bands;   // best first
    const index = (name) => bands.findIndex((b) => b.name === name);
    const both = v.items.filter((a) => index(a.tierO) >= 0 && index(a.tierT) >= 0);
    const without = v.items.length - both.length;
    const inCell = (ri, ci) => both.filter((a) => index(a.tierT) === ri && index(a.tierO) === ci);
    const pick = Array.isArray(v.state.pick) && v.state.pick.length === 2 && v.state.pick[0] < bands.length && v.state.pick[1] < bands.length
      ? v.state.pick : null;
    const up = both.filter((a) => index(a.tierO) < index(a.tierT)).length;
    const down = both.filter((a) => index(a.tierO) > index(a.tierT)).length;
    const sameN = both.length - up - down;
    const move = (ri, ci) => (ri === ci ? `same ${sc.word}` : ci < ri ? `a higher ${sc.word} this edition` : `a lower ${sc.word} this edition`);
    const W = capital(sc.word);
    const fig = HB.charts.crossTable({
      label: `${levelLong(v.L)}: ${sc.word} ${when} against ${sc.word} this edition`,
      rows: bands.map((b) => ({ id: b.name, label: b.name, short: sc.short(b.name) })),
      cols: bands.map((b) => ({ id: b.name, label: b.name, short: sc.short(b.name) })),
      count: (ri, ci) => inCell(ri, ci).length,
      rowTitle: `${W} ${when}`,
      colTitle: `${W} this edition`,
      diagonalLabel: `Same ${sc.word} both years`,
      selected: pick,
      cellLabel: (ri, ci, n) => `${plural(n, nouns.one, nouns.many)}: ${bands[ri].name} ${when}, ${bands[ci].name} this edition, ${move(ri, ci)}`,
      onPick: (ri, ci) => { v.state.pick = [ri, ci]; v.redraw('.cx__cell[aria-pressed="true"]'); },
      caption: `Rows: the ${sc.word} ${when}. Columns: the ${sc.word} this edition. The ringed cells on the diagonal kept ` +
        `their ${sc.word}; left of it, a higher ${sc.word} this edition; right of it, a lower one. Pick a cell to list its ${nouns.many}.`,
    });
    return h("div", { class: "stack" },
      h("p", { class: "cmp-sum" }, `Of ${plural(both.length, nouns.one, nouns.many)} with a ${sc.word} in both years, ` +
        `${fmtInt(sameN)} kept it, ${fmtInt(up)} moved up to a higher ${sc.word} and ${fmtInt(down)} moved down to a lower one.` +
        (without ? ` ${plural(without, nouns.one, nouns.many)} without a ${sc.word} in both years.` : "")),
      fig,
      pick ? pickedList(`${bands[pick[0]].name} ${when}, ${bands[pick[1]].name} this edition`, inCell(pick[0], pick[1]), v) : null);
  }

  function groupsView(v, ctx) {
    const p = bundle().project || {};
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const nouns = parts().unitNouns();
    if (!labels.length) return HB.notice("neutral", `The ${nouns.many} have no labels to group them by.`);
    const label = labels.includes(ctx.state.groupBy) ? ctx.state.groupBy : labels[0];
    const words = (lb) => lb.replace(/_/g, " ");
    const pickLabel = chooser("Group by", labels.map((lb) => [lb, capital(words(lb))]), label, (lb) => {
      refocus = "group-by";
      v.state.pick = null;
      ctx.setGroupBy(lb);
    }, "group-by");
    const units = new Map((bundle().units || []).map((u) => [u.id, u]));
    const valueOf = (a) => {
      const x = ((units.get(a.id) || {}).labels || {})[label];
      return x == null || x === "" ? null : String(x);
    };
    const values = HB.data.labelValues(label);
    const groups = values.map((g) => ({ id: g, name: g, items: v.items.filter((a) => valueOf(a) === g) }));
    const loose = v.items.filter((a) => valueOf(a) == null);
    if (loose.length) groups.push({ id: "", name: `No ${words(label)}`, items: loose });
    const rows = groups.filter((g) => g.items.length).map((g) => {
      const has = g.items.filter((a) => a.has);
      const tiered = g.items.filter((a) => a.tierO != null && a.tierT != null);
      return {
        ...g,
        n: g.items.length,
        sameScore: has.filter((a) => a.same).length, scored: has.length,
        sameTier: tiered.filter((a) => a.tierSame).length, tiered: tiered.length,
        higher: has.filter((a) => a.gap > 0).length, lower: has.filter((a) => a.gap < 0).length,
        mean: has.length ? Math.round((has.reduce((s, a) => s + a.gap, 0) / has.length) * 10) / 10 : null,
      };
    });
    const top = Math.max(1, ...rows.map((g) => Math.abs(g.mean || 0)));
    const picked = rows.find((g) => v.state.pick === "g:" + g.id) || null;
    const share = (k, of) => (of > 0 ? h("span", { class: "cmp-share" },
      h("span", null, h("strong", null, fmtInt(k)), ` of ${fmtInt(of)}`),
      h("span", { class: "cmp-meter", "aria-hidden": "true" }, h("span", { style: { width: `${((100 * k) / of).toFixed(1)}%` } })))
      : h("span", { class: "muted" }, "–"));
    const W = capital(v.sc.word);
    const table = HB.countedTable({
      columns: [
        { label: capital(words(label)), value: (g) => g.name, rowHeader: true,
          render: (g) => h("button", { type: "button", class: "cmp-linkbtn", "aria-pressed": String(picked === g),
            onclick: () => { v.state.pick = picked === g ? null : "g:" + g.id; v.redraw("group-" + g.id); },
            "data-focus": "group-" + g.id }, g.name),
          text: (g) => g.name },
        { label: capital(nouns.many), value: (g) => g.n, num: true },
        ...(v.sc.set ? [{ label: `Same ${v.sc.word}`, value: (g) => (g.tiered ? g.sameTier / g.tiered : null), render: (g) => share(g.sameTier, g.tiered),
          text: (g) => `${g.sameTier} of ${g.tiered}` }] : []),
        { label: "Same score", value: (g) => (g.scored ? g.sameScore / g.scored : null), render: (g) => share(g.sameScore, g.scored),
          text: (g) => `${g.sameScore} of ${g.scored}` },
        { label: "Higher", value: (g) => g.higher, num: true },
        { label: "Lower", value: (g) => g.lower, num: true },
        { label: "Mean gap", value: (g) => g.mean, num: true, empty: "–",
          render: (g) => (g.mean == null ? "–" : h("span", { class: "cmp-mean" },
            h("span", { class: "cmp-mean__track", "aria-hidden": "true" },
              g.mean === 0 ? h("span", { class: "cmp-mean__zero" }) : h("span", {
                class: "cmp-mean__bar " + (g.mean < 0 ? "is-neg" : "is-pos"),
                style: { width: `${((Math.abs(g.mean) / top) * 50).toFixed(1)}%`, [g.mean < 0 ? "right" : "left"]: "50%" },
              })),
            h("span", { class: "cmp-mean__v" }, signed(g.mean)))),
          text: (g) => (g.mean == null ? "" : String(g.mean)) },
      ],
      rows,
      expected: rows.length,
      shownLabel: ["group", "groups"],
      note: `At the ${levelShort(v.L).toLowerCase()} level. Pick a group to list its ${nouns.many}.`,
      copyName: `Comparison by ${words(label)}`,
      freezeFirst: true,
    });
    return h("div", { class: "stack" },
      h("div", { class: "cmp-tools cmp-tools--inner" }, pickLabel.node),
      h("p", { class: "small muted cmp-measure" },
        `For each ${words(label)}: how many ${nouns.many} keep their ${v.sc.word} and score, how many moved up or down, ` +
        `and the mean gap (this edition minus ${them}${v.sc.pct ? ", in points" : ""}). ` + (v.sc.set ? `${W} counts leave out ${nouns.many} without a ${v.sc.word} in both years.` : "")),
      table,
      picked ? pickedList(`${picked.name}: ${plural(picked.n, nouns.one, nouns.many)}`, picked.items, v) : null);
  }

  function rulesView(v) {
    const nouns = parts().unitNouns();
    const rules = Array.isArray(bundle().rules) ? bundle().rules : [];
    const byId = new Map(rules.map((r) => [r.id, r]));
    const differing = v.items.filter((a) => (a.has && !a.same) || (a.hasTier && !a.tierSame));
    const found = new Map();
    const none = [];
    differing.forEach((a) => {
      const qids = a.d && Array.isArray(a.d.cells) && a.d.cells.length ? a.d.cells : cellsUnder(a.id, v.L).map((x) => x[1]);
      const hit = new Set();
      qids.forEach((q) => {
        const cell = HB.data.cell(a.id, q) || {};
        (Array.isArray(cell.rules) ? cell.rules : []).forEach((rid) => {
          const rule = byId.get(rid);
          if (!rule || !UNDECIDED.includes(rule.status)) return;
          hit.add(rid);
          if (!found.has(rid)) found.set(rid, { rule, items: [], qs: new Set() });
          const f = found.get(rid);
          if (!f.items.includes(a)) f.items.push(a);
          f.qs.add(q);
        });
      });
      if (!hit.size) none.push(a);
    });
    const list = Array.from(found.values()).sort((x, y) => y.items.length - x.items.length ||
      String(x.rule.id).localeCompare(String(y.rule.id), "en-GB", { numeric: true }));
    const picked = typeof v.state.pick === "string" && v.state.pick.startsWith("r:") ? v.state.pick.slice(2) : null;
    const pickedRow = picked === "" ? { id: "", items: none } : list.find((f) => f.rule.id === picked) || null;
    const meanAbs = (items) => {
      const has = items.filter((a) => a.has);
      return has.length ? Math.round((has.reduce((s, a) => s + Math.abs(a.gap), 0) / has.length) * 10) / 10 : null;
    };
    const short = (text) => {
      const t = String(text || "").replace(/\s+/g, " ").trim();
      return t.length > 140 ? t.slice(0, 137).replace(/\s+\S*$/, "") + "…" : t;
    };
    const rowsOut = list.map((f) => ({ id: f.rule.id, rule: f.rule, items: f.items, qs: Array.from(f.qs).sort((a, b) => a.localeCompare(b, "en-GB", { numeric: true })) }));
    if (none.length) rowsOut.push({ id: "", rule: null, items: none, qs: [] });
    const table = HB.countedTable({
      columns: [
        { label: "Rule", value: (x) => x.id || "~", rowHeader: true,
          render: (x) => h("button", { type: "button", class: "cmp-linkbtn", "aria-pressed": String(pickedRow != null && pickedRow.id === x.id),
            "data-focus": "rule-" + (x.id || "none"),
            onclick: () => { v.state.pick = pickedRow && pickedRow.id === x.id ? null : "r:" + x.id; v.redraw("rule-" + (x.id || "none")); } },
          x.rule ? x.rule.id : "No undecided rule"),
          text: (x) => (x.rule ? x.rule.id : "No undecided rule") },
        { label: "What it asks", value: (x) => (x.rule ? x.rule.ask : null), wide: true,
          render: (x) => (x.rule ? short(x.rule.ask) : h("span", { class: "muted" }, "Not down to a rule: see each one's reason")),
          text: (x) => (x.rule ? x.rule.ask : "") },
        { label: "Status", value: (x) => (x.rule ? capital(x.rule.status) : null), empty: "–" },
        { label: `${capital(nouns.many)} that differ`, value: (x) => x.items.length, num: true },
        { label: "Questions", value: (x) => x.qs.join(", "), mono: true, empty: "–" },
        { label: "Mean gap, either way", value: (x) => meanAbs(x.items), num: true, empty: "–",
          format: (val) => fmtNum(val, 1) + (v.sc.pct ? " points" : "") },
      ],
      rows: rowsOut,
      expected: { n: differing.length, label: [`${nouns.one} that differs`, `${nouns.many} that differ`] },
      shownLabel: ["line", "lines"],
      note: "One assessment can rest on more than one rule.",
      copyName: "Differences by rule",
      freezeFirst: true,
      tools: h("a", { class: "btn btn--small btn--quiet", href: HB.href("rules") }, "Open the Rules tab"),
    });
    return h("div", { class: "stack" },
      h("p", { class: "cmp-sum" }, differing.length
        ? `${plural(differing.length, nouns.one, nouns.many)} differ at this level. ` +
          (list.length ? `In ${fmtInt(differing.length - none.length)} of them, a cell under the score rests on a rule that is ` +
            `still open or skipped; ${list[0].rule.id} is under the most (${fmtInt(list[0].items.length)}). ` : "None rests on an undecided rule. ") +
          (none.length ? `${fmtInt(none.length)} rest on no undecided rule. ` : "") +
          "Read from the cells as they stand now, so it can differ from the reason recorded when the comparison was run."
        : `No ${nouns.one} differs at this level.`),
      differing.length ? table : null,
      pickedRow ? pickedList(pickedRow.id ? `Resting on ${pickedRow.id}` : "Resting on no undecided rule", pickedRow.items, v) : null);
  }

  function tableView(v) {
    const nouns = parts().unitNouns();
    const counts = {};
    v.shown.forEach((a) => { if (a.d) counts[a.d.reason] = (counts[a.d.reason] || 0) + 1; });
    const reasons = Object.keys(REASON_WORDS).filter((r) => counts[r]).concat(Object.keys(counts).filter((r) => !REASON_WORDS[r]));
    const reason = v.state.reason === "all" || counts[v.state.reason] ? v.state.reason : "all";
    const reasonPick = chooser("Reason",
      [["all", `All reasons (${fmtInt(v.shown.length)})`], ...reasons.map((r) => [r, `${REASON_WORDS[r] || r} (${fmtInt(counts[r])})`])],
      reason, (r) => { v.state.reason = r; HB.store.set(REASON_KEY, r); v.redraw("reason"); }, "reason");
    const list = byGap(reason === "all" ? v.shown : v.shown.filter((a) => a.d && a.d.reason === reason));
    const W = capital(v.sc.word);
    const seen = new Set();
    const allCells = list.flatMap((a) => (a.d && Array.isArray(a.d.cells) ? a.d.cells : []).map((q) => [a.id, q]))
      .filter((pair) => !seen.has(pair.join(" ")) && seen.add(pair.join(" ")));
    const sc = v.sc;
    const table = HB.countedTable({
      columns: [
        { label: capital(nouns.one), value: (a) => a.name, rowHeader: true,
          render: (a) => h("button", { type: "button", class: "cmp-linkbtn", onclick: () => v.open(a.id) }, a.name,
            h("span", { class: "visually-hidden" }, ": show the details")),
          text: (a) => a.name },
        { label: Them, value: (a) => a.theirs, format: (x) => num(x, sc), num: true, empty: "–" },
        { label: "This edition", value: (a) => a.ours, format: (x) => num(x, sc), num: true, empty: "–" },
        { label: "Gap", value: (a) => a.gap, format: (x) => signed(x), num: true, empty: "–", text: (a) => (a.gap == null ? "" : String(a.gap)) },
        ...(sc.set ? [{ label: `${W} ${when}`, value: (a) => a.tierT, empty: "–", render: (a) => tierCell(a.tierT, sc), text: (a) => a.tierT || "" },
          { label: `${W} now`, value: (a) => a.tierO, empty: "–", text: (a) => a.tierO || "",
            render: (a) => h("span", { class: "cmp-nowrap" }, tierCell(a.tierO, sc), a.hasTier && !a.tierSame ? h("span", { class: "cmp-moved" }, a.tierO == null ? "" : " (moved)") : null) }] : []),
        { label: "Reason", value: (a) => (a.d ? reasonWord(a.d) : a.same ? "Same" : null), empty: "–",
          render: (a) => (a.d ? h("span", { class: "cmp-reason" }, glyph(a.d.reason), reasonWord(a.d)) : h("span", { class: "muted" }, a.same ? "Same" : "–")) },
        { label: "Explained by", value: (a) => byWord(a.d), empty: "–",
          render: (a) => (byWord(a.d) ? HB.statusChip("neutral", byWord(a.d), { icon: a.d.by === "files" ? "tick" : "person" }) : h("span", { class: "muted" }, "–")) },
        { label: "Note", value: (a) => (a.d ? a.d.note : null), wide: true,
          empty: "–", render: (a) => (a.d && a.d.note ? a.d.note : h("span", { class: "muted" }, a.d ? (a.d.reason === "unexplained" ? "Not explained yet" : "No note yet") : "–")) },
        { label: "Cells", value: (a) => (a.d && a.d.cells ? a.d.cells.join(", ") : ""), sortable: false,
          render: (a) => (a.d ? cellLinks(a.id, Array.isArray(a.d.cells) ? a.d.cells : [], v.ctx, true) : h("span", { class: "muted" }, "–")),
          text: (a) => (a.d && a.d.cells ? a.d.cells.join(", ") : "") },
      ],
      rows: list,
      expected: v.items.length,
      shownLabel: [nouns.one, nouns.many],
      note: `${levelLong(v.L)}, largest gap first` + (reason === "all" ? "." : `, filtered to ${REASON_WORDS[reason] || reason}.`) +
        " Click a header to sort.",
      copyName: `Comparison with ${them}`,
      freezeFirst: true,
      tools: allCells.length
        ? h("button", { type: "button", class: "btn btn--small", onclick: () => v.ctx.highlight(allCells) },
          `Show these ${plural(allCells.length, "cell", "cells")} on the grid`)
        : null,
    });
    return h("div", { class: "stack" },
      h("div", { class: "cmp-tools cmp-tools--inner" }, reasonPick.node),
      h("p", { class: "small muted cmp-measure" },
        "\"From the files\" means the script found the reason in the documents and dates on record. \"Claude's reading\" " +
        "means Claude explained it from the evidence. The cells are the ones under the score that differs."),
      table);
  }

  /* ---- Past editions ----------------------------------------------------------------- */

  /** compare.json's history: {editions (oldest first, the latest last), levels, rows: {row: {key: {edition: figure}}}, this_run}. */
  // The free edition compares with one previous edition: no history, so no trends and no older editions.
  const isFree = () => bundle().edition === "free";
  const historyOf = (c) => (isFree() ? null : (c && c.history) || (bundle().results || {}).history || null);
  const olderEditions = (hist) => (hist && Array.isArray(hist.editions) && hist.editions.length > 1 ? hist.editions.slice(0, -1) : []);
  const sameFigure = (a, b) => (isNum(a) && isNum(b) ? Math.abs(a - b) < 1e-9 : String(a) === String(b));

  /** A comparison with an older edition, in compare.json's shape, from history: this run against
      that edition at every key both have; no reasons, no big moves. */
  function editionCompare(c, hist, edition) {
    const rows = [];
    const notCompared = [];
    const agree = {};
    const of = {};
    (bundle().units || []).forEach((u) => {
      const row = u.id;
      const past = (hist.rows || {})[row] || {};
      const now = (hist.this_run || {})[row] || {};
      const inEdition = Object.keys(past).some((k) => past[k] && past[k][edition] != null);
      const scored = Object.values((bundle().cells || {})[row] || {}).some((cell) => HB.data.isScored(cell));
      if (!inEdition) {
        if (scored) notCompared.push({ row, why: "not_in_last_edition", text: `It is not in ${theirResults}.` });
        return;
      }
      const line = { row, ours: {}, theirs: {}, agree: {} };
      Object.keys(now).forEach((k) => {
        const t = (past[k] || {})[edition];
        if (t == null) return;
        line.ours[k] = now[k];
        line.theirs[k] = t;
        line.agree[k] = sameFigure(now[k], t);
        of[k] = (of[k] || 0) + 1;
        agree[k] = (agree[k] || 0) + (line.agree[k] ? 1 : 0);
      });
      if (Object.keys(line.ours).length) rows.push(line);
      else notCompared.push({ row, why: scored ? "partial" : "not_scored",
        text: scored ? "Some of its questions have not been scored, so its totals were not compared." : "None of its questions have been scored yet." });
    });
    return { level: (hist.levels || {})[edition] || c.level, edition, rows, disagreements: [], big_moves: [],
      summary: { rows: rows.length, agree, of, not_compared: notCompared } };
  }

  /** The free edition's one line about past editions: this comparison is with one previous edition. */
  function freeNote() {
    return h("p", { class: "small muted cmp-free" },
      "This compares with one previous edition. Following results across several past editions, with trends over time, is in the Full version of the benchmark tool.");
  }

  /** "Compare with: 2024 | 2023 | 2022", newest first, when there are older editions. */
  function editionPicker(hist, ctx) {
    const older = olderEditions(hist);
    if (!older.length) return null;
    const latest = hist.editions[hist.editions.length - 1];
    const options = [["", latest], ...older.slice().reverse().map((e) => [e, e])];
    const few = options.length <= 4;
    const control = (few ? segmented : chooser)("Compare with", options, picked || "", (value) => {
      if ((value || null) === picked) return;
      picked = value || null;
      refocus = few ? "edition-" + value : "edition";
      ctx.render();
    }, "edition");
    const level = picked ? LEVEL_WORDS[(hist.levels || {})[picked]] || (hist.levels || {})[picked] : null;
    return h("div", { class: "stack cmp-editions" },
      h("div", { class: "cmp-tools" }, control.node),
      picked ? HB.notice("neutral", `Compared with the ${picked} edition, one of the past editions held back, at ${level || "the"} level. ` +
        `Reasons for differences and the big moves are worked out against the latest past edition (${latest}) only.`) : null);
  }

  /** Whether a key is a percentage: a section in percent, or an overall score in percent. */
  function isPercentKey(key) {
    const sheet = bundle().scoresheet || {};
    const s = (sheet.sections || []).find((x) => x && x.id === key);
    if (s) return s.percent !== false;
    const o = sheet.overall || {};
    return key === "overall" && (o.method !== "sum_points" || Boolean(o.percent));
  }

  /** The most a key can be: 100 for a percentage, a question's top points, else unknown. */
  function mostOf(key) {
    if (isPercentKey(key)) return 100;
    const q = HB.data.question(key);
    const levels = q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    const top = Math.max(0, ...levels.map((l) => (isNum(l.points) ? l.points : 0)));
    return top > 0 ? top : undefined;
  }

  const figureText = (v, key) => (isNum(v) ? fmtNum(v, 1) + (isPercentKey(key) ? "%" : "") : "–");

  function whyWord(mv) {
    if (mv.why === "nothing_found") return "Nothing found";
    if (mv.why === "big_change") return "Big change";
    const set = parts().bandSets().find((s) => s.of === mv.key.slice(5));
    return `${capital(set ? parts().bandWord(set) : "band")} jump`;
  }

  /** One big move: what moved, why it is listed, its sentence and the row's past editions as a small line. */
  function moveLine(mv, hist) {
    const key = mv.key.startsWith("band:") ? mv.key.slice(5) : mv.key;   // a band's trend is its score's
    const past = ((hist && hist.rows && hist.rows[mv.row]) || {})[key] || {};
    const editions = (hist ? hist.editions : []).filter((e) => isNum(past[e]));
    const now = ((hist && hist.this_run && hist.this_run[mv.row]) || {})[key];
    const nowValue = isNum(now) ? now : isNum(mv.ours) ? mv.ours : null;
    const trend = isFree() ? null : mv.trend === "breaks" ? HB.statusChip("warning", "Outside its past range")
      : h("span", { class: "small muted" }, mv.trend === "within" ? "Within its past range" : "Too few past editions for a trend");
    const figures = editions.map((e) => `${e} ${figureText(past[e], key)}`).concat(isNum(nowValue) ? [`this run ${figureText(nowValue, key)}`] : []);
    return h("li", { class: "cmp-first__move" },
      h("div", { class: "cmp-first__what" }, h("strong", null, levelLong(mv.key)), h("span", { class: "cmp-first__why" }, whyWord(mv)), trend),
      h("p", { class: "cmp-first__text" }, mv.text),
      editions.length ? h("div", { class: "cmp-first__trend" },
        HB.charts.trendLine({ points: editions.map((e) => ({ label: e, value: past[e] })),
          current: isNum(nowValue) ? { label: "This run", value: nowValue } : null, max: mostOf(key) }),
        h("span", { class: "small muted" }, figures.join(", "))) : null);
  }

  /* ---- Check these first: what to do about each big difference ------------------------- */

  // compare.py's sizes for a big move (BIG_GAP_SHARE, BAND_JUMP, NOTHING_FOUND_SHARE), used here for
  // the big differences that rest on an open rule: compare.py leaves those out of big_moves, as the
  // rule explains them, but deciding the rule is the owner's to do.
  const BIG_GAP_SHARE = 0.25;
  const BAND_JUMP = 2;
  const NOTHING_FOUND_SHARE = 0.5;
  const ANSWER_FLAGS = { quote_not_found: "Quote not on saved page", weak_source: "Not from the company" };
  const SEND_PATTERN = "compare:nothing_found";   // the `supplied` doc's pattern for a Send from here
  const MAX_ANSWERS = 6;                           // answer buttons on a card before "Show all on the grid"
  const openCards = new Set();                     // the open cards, by key, kept across redraws
  let seeded = false;                              // whether the first card has been opened once

  /** The big differences with the latest edition that rest on an open rule, in big_moves' shape. */
  function bigOnRules(c) {
    const out = [];
    (c.disagreements || []).forEach((d) => {
      if (d.reason !== "open_rule") return;
      let why = null;
      if (d.key.startsWith("band:")) {
        const set = parts().bandSets().find((s) => s.of === d.key.slice(5));
        const names = set ? set.bands.map((b) => b.name) : [];
        const i = names.indexOf(String(d.ours));
        const j = names.indexOf(String(d.theirs));
        if (i >= 0 && j >= 0 && Math.abs(i - j) >= BAND_JUMP) why = "band_jump";
      } else if (isNum(d.ours) && isNum(d.theirs)) {
        const most = mostOf(d.key);
        const qs = Array.isArray(d.cells) ? d.cells : [];
        const none = qs.length > 0 && qs.every((q) => (HB.data.cell(d.row, q) || {}).state === "no_evidence");
        if (!most) return;
        if (d.theirs >= NOTHING_FOUND_SHARE * most && d.ours < d.theirs && (d.ours === 0 || none)) why = "nothing_found";
        else if (Math.abs(d.ours - d.theirs) >= BIG_GAP_SHARE * most - 1e-9) why = "big_change";
      }
      if (why) out.push({ row: d.row, key: d.key, ours: d.ours, theirs: d.theirs, why, reason: d.reason });
    });
    return out;
  }

  /** A row's moves in one line, a level at a time: "Overall: 74% to 0%, Tier 3 to Tier 6; Pillar 1: ...". */
  function movesSummary(list, m) {
    const byLevel = new Map();
    list.forEach((mv) => {
      const band = mv.key.startsWith("band:");
      const L = band ? mv.key.slice(5) : mv.key;
      if (!byLevel.has(L)) byLevel.set(L, {});
      byLevel.get(L)[band ? "band" : "score"] = mv;
    });
    return Array.from(byLevel).sort((a, b) => rank(a[0]) - rank(b[0])).map(([L, x]) => {
      const sc = scaleFor(m, L);
      const tier = (name) => { const s = sc.short(String(name)); return s === String(name) ? s : `${capital(sc.word)} ${s}`; };
      const bits = [];
      if (x.score) bits.push(`${figureText(x.score.theirs, L)} to ${figureText(x.score.ours, L)}`);
      if (x.band) bits.push(`${tier(x.band.theirs)} to ${tier(x.band.ours)}`);
      return `${levelShort(L)}: ${bits.join(", ")}`;
    }).join("; ");
  }

  /** The cells under some moves, [[row, qid], ...], each once. */
  function cellsOfMoves(row, list, mLatest) {
    const seen = new Set();
    return list.flatMap((mv) => {
      const d = mLatest.dis.get(row + SEP + mv.key);
      return d && Array.isArray(d.cells) && d.cells.length ? d.cells.map((q) => [row, q]) : cellsUnder(row, mv.key);
    }).filter((pair) => !seen.has(pair[1]) && seen.add(pair[1]));
  }

  /** Buttons that open answers in the cell panel, the likeliest first: an answer with a quote or
      source problem, then (for a drop) the lowest scores; at most MAX_ANSWERS, then the grid. */
  function answerButtons(row, cells, down, ctx) {
    const list = cells.map(([, q]) => {
      const cell = HB.data.cell(row, q) || {};
      const most = mostOf(q);
      return {
        q, cell, most,
        flags: (Array.isArray(cell.flags) ? cell.flags : []).filter((f) => ANSWER_FLAGS[f]),
        share: cell.state === "no_evidence" ? 0 : isNum(cell.points) && most ? cell.points / most : null,
      };
    });
    const qOrder = (x, y) => String(x.q).localeCompare(String(y.q), "en-GB", { numeric: true });
    list.sort((x, y) => (y.flags.length > 0) - (x.flags.length > 0) ||
      (down ? (x.share ?? 2) - (y.share ?? 2) : (y.share ?? -1) - (x.share ?? -1)) || qOrder(x, y));
    const shown = list.slice(0, MAX_ANSWERS);
    const what = (x) => (x.cell.state === "no_evidence" ? "nothing found" : isNum(x.cell.points) && x.most ? `${fmtNum(x.cell.points, 1)} of ${fmtNum(x.most, 1)}` : "");
    return h("div", { class: "cmp-answers" },
      shown.map((x) => h("button", { type: "button", class: "cmp-answer", onclick: () => ctx.openCell(row, x.q) },
        h("span", { class: "visually-hidden" }, "Open the answer to "),
        h("span", { class: "cmp-answer__q" }, `Q${x.q}`),
        what(x) ? h("span", { class: "cmp-answer__pts" }, what(x)) : null,
        x.flags.map((f) => h("span", { class: "cmp-answer__flag" }, ANSWER_FLAGS[f])))),
      list.length > shown.length
        ? h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => ctx.highlight(cells) },
          `Show all ${fmtInt(list.length)} on the grid`, h("span", { class: "visually-hidden" }, `: ${rowName(row)}`))
        : null);
  }

  /** The inline decision for a rule (HB.ruleEditor from the Rules tab), else a link to it there. */
  function ruleDecision(rule, ctx) {
    if (typeof HB.ruleEditor === "function") {
      try {
        const node = HB.ruleEditor(rule.id, ctx, { compact: true, onDone: () => ctx.render() });
        if (node) return node;
      } catch (err) {
        console.error("HB.ruleEditor could not draw " + rule.id + ":", err);
      }
    }
    const pending = HB.edits && HB.edits.ready ? HB.edits.pendingDecision(rule.id) : null;
    return h("p", { class: "cmp-act__do" },
      h("a", { class: "btn btn--small btn--primary", href: HB.href("rules", rule.id) },
        pending ? "Change your decision" : "Decide it", h("span", { class: "visually-hidden" }, ` (rule ${rule.id}, on the Rules tab)`)),
      pending ? h("span", { class: "small muted" }, HB.when("Your decision is saved and applied.", "Your decision is saved. Claude applies it the next time it works on this project.")) : null);
  }

  /** The owner's choice on a rule: a decision saved here and not applied yet, else the decided one;
      null while it is open. {text} is the wording to show. */
  function ownerChoice(rule) {
    const pending = HB.edits && HB.edits.ready ? HB.edits.pendingDecision(rule.id) : null;
    const optText = (id) => { const o = (rule.options || []).find((x) => x && x.id === id); return o && o.text ? String(o.text) : null; };
    if (pending && !pending.skip) return { text: pending.text || optText(pending.option) || "your own wording", waiting: true };
    if (rule.status === "decided" && rule.decision) return { text: rule.decision.text || optText(rule.decision.option) || "recorded", waiting: false };
    return null;
  }

  /* Re-runs asked for in this page, and those still waiting in the bundle's control.json. */
  const rerunAsked = new Map();     // rule id -> the request's doc id, until control.applied holds it
  function rerunWaiting(rule) {
    const c = (bundle().control && typeof bundle().control === "object") ? bundle().control : {};
    const asked = rerunAsked.get(rule.id);
    if (asked && Array.isArray(c.applied) && c.applied.includes(asked)
      && !(Array.isArray(c.reruns) && c.reruns.some((r) => r && r.id === asked))) rerunAsked.delete(rule.id);
    return rerunAsked.has(rule.id) || (Array.isArray(c.reruns) && c.reruns.some((r) => r && r.rule === rule.id));
  }

  /** Saves the re-run request for a rule: {action: "rerun", rule, rows, questions} in `control`
      (the run skill handles it). The Re-run button and the corner chat (views/chat.js) both use it. */
  async function askRerun(rule, rows) {
    const doc = await HB.edits.save("control", { action: "rerun", rule: rule.id, rows: rows.slice(),
      questions: (rule.question_ids || []).map(String) });
    rerunAsked.set(rule.id, doc.id);
    return doc;
  }

  /** "Re-run": asks Claude to score again the answers resting on this rule. */
  function rerunButton(rule, rows, ctx) {
    if (rerunWaiting(rule)) return h("p", { class: "small muted" }, HB.when(
      "Claude is scoring these answers again. Progress shows in the Run bar on the Grid tab.",
      "Re-run asked for. Claude scores these answers again the next time it works on this project."));
    if (!(HB.edits && HB.edits.ready)) return h("p", { class: "small muted" }, HB.cantSave());
    const nouns = parts().unitNouns();
    const btn = h("button", { type: "button", class: "btn btn--small btn--primary" }, "Re-run these " + fmtInt(rows.length) + " " + (rows.length === 1 ? nouns.one : nouns.many));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await askRerun(rule, rows);
        ctx.render();
      } catch (err) {
        btn.disabled = false;
        btn.after(HB.notice("critical", err && err.message ? err.message : "This could not be saved."));
      }
    });
    return h("p", { class: "cmp-act__do" }, btn);
  }

  /** "It covers questions 1.7 and 2.6, up to 5 points each.", from the rule's questions. */
  function ruleStakes(rule) {
    const qs = Array.isArray(rule.question_ids) ? rule.question_ids.map(String) : [];
    if (!qs.length) return null;
    const most = qs.map((q) => (isPercentKey(q) ? undefined : mostOf(q)));
    const same = most.every((x) => isNum(x) && x === most[0]);
    return `It covers ${questionWords(qs)}` + (same ? `, up to ${plural(most[0], "point", "points")}${qs.length > 1 ? " each" : ""}` : "") + ".";
  }

  /** One card. Closed, it is one button: its title, a line of what moved and what to do (cta).
      Open, it shows the action box first, then the details. */
  function firstCard(key, { kind, title, summary, cta }, action, details) {
    const id = HB.uid("cmp-card");
    const body = h("div", { class: "cmp-card__body", id }, action, details);
    const toggle = h("button", { type: "button", class: "cmp-card__toggle", "aria-controls": id },
      h("span", { class: "cmp-card__chev", "aria-hidden": "true" }),
      h("span", { class: "cmp-card__title" }, kind ? h("span", { class: "cmp-card__kind" }, kind + ": ") : null, h("strong", null, title)),
      h("span", { class: "cmp-card__sum" }, summary),
      h("span", { class: "cmp-card__cta" }, cta));
    const setOpen = (on) => {
      if (on) openCards.add(key); else openCards.delete(key);
      body.hidden = !on;
      toggle.setAttribute("aria-expanded", String(on));
    };
    toggle.addEventListener("click", () => setOpen(!openCards.has(key)));
    setOpen(openCards.has(key));
    return { key, setOpen, node: h("li", { class: "cmp-card" }, toggle, body) };
  }

  /** The call to action on a card: what to do in a few words, why, then the controls. */
  const actBox = (lead, text, ...controls) => h("div", { class: "cmp-act" },
    h("p", { class: "cmp-act__lead" }, h("strong", null, lead), text ? " " + text : ""), ...controls);

  /** "Check these first": every big difference from the latest edition, as what the owner can do.
      An open rule under big differences gets one card, with the decision inline; every other
      row with a big move gets one card, its action by what the files show: nothing found (send
      the document), a quote or source problem (open that answer), or no explanation (open the
      answers). Rule cards first, as one decision reaches several rows; then rows, those that
      break their trend first. Each card's details hold its moves and their past editions. */
  function checkFirst(c, hist, ctx, mLatest) {
    const moves = Array.isArray(c.big_moves) ? c.big_moves : [];
    const onRules = bigOnRules(c);
    if (!moves.length && !onRules.length) return null;
    const nouns = parts().unitNouns();
    const latest = hist && hist.editions.length ? hist.editions[hist.editions.length - 1] : c.edition || "last";
    const nameButton = (row) => h("button", { type: "button", class: "cmp-linkbtn cmp-card__name", onclick: () => latestWords(() => openDetail(row, mLatest, ctx)) },
      rowName(row), h("span", { class: "visually-hidden" }, `: compared with ${latest}`));

    // One card per open rule, with the rows whose big differences rest on it.
    const byRule = new Map();
    onRules.forEach((mv) => {
      openRulesUnder(mv.row, cellsOfMoves(mv.row, [mv], mLatest).map((x) => x[1]), { unapplied: true }).forEach(({ rule, qs }) => {
        if (!byRule.has(rule.id)) byRule.set(rule.id, { rule, rows: new Map() });
        const rows = byRule.get(rule.id).rows;
        if (!rows.has(mv.row)) rows.set(mv.row, { list: [], qs: new Set() });
        rows.get(mv.row).list.push(mv);
        qs.forEach((q) => rows.get(mv.row).qs.add(q));
      });
    });
    const ruleCards = Array.from(byRule.values())
      .sort((x, y) => y.rows.size - x.rows.size || String(x.rule.id).localeCompare(String(y.rule.id), "en-GB", { numeric: true }))
      .map(({ rule, rows }) => {
        const words = ruleWords(rule);
        const n = rows.size;
        const q = capital(words.question);
        const title = words.topic || (q.length > 80 ? q.slice(0, 77).replace(/\s+\S*$/, "") + "…" : q);   // no topic: its question, short
        const chose = ownerChoice(rule);
        const rowIds = Array.from(rows.keys());
        return Object.assign(firstCard("rule:" + rule.id, chose ? {
          kind: "Decided", title, cta: rerunWaiting(rule) ? "Re-run asked for" : "Re-run",
          summary: `${chose.text.length > 90 ? chose.text.slice(0, 87).replace(/\s+\S*$/, "") + "…" : chose.text} \u00b7 ` +
            (n === 1 ? `one ${nouns.one} to score again` : `${fmtInt(n)} ${nouns.many} to score again`),
        } : {
          kind: "Open rule", title, cta: "Decide this rule",
          summary: n === 1 ? `One ${nouns.one} with a big difference rests on it`
            : `${fmtInt(n)} ${nouns.many} with a big difference rest on it, so one decision reaches all of them`,
        },
        chose
          ? actBox("Re-run to apply your decision.", `You chose: ${chose.text}`, rerunButton(rule, rowIds, ctx),
            h("details", { class: "small" }, h("summary", null, "Change your decision"), ruleDecision(rule, ctx)))
          : actBox("Decide this rule.", `${capital(words.question)} (rule ${rule.id})`,
            ruleStakes(rule) ? h("p", { class: "small muted cmp-act__note" }, ruleStakes(rule)) : null, ruleDecision(rule, ctx)),
        h("div", { class: "cmp-card__details" },
          h("p", { class: "small muted" }, `Resting on it, with their difference from ${latest}:`),
          h("ul", { class: "cmp-card__rows" }, Array.from(rows, ([row, x]) => h("li", null,
            nameButton(row), " ", h("span", null, movesSummary(x.list, mLatest)),
            h("span", { class: "small muted" }, ` (${questionWords(Array.from(x.qs).sort((a, b) => a.localeCompare(b, "en-GB", { numeric: true })))})`)))))), { decided: Boolean(chose) });
      });

    // One card per row with a big move that nothing in the files explains.
    const byRow = new Map();
    moves.forEach((mv) => { if (!byRow.has(mv.row)) byRow.set(mv.row, []); byRow.get(mv.row).push(mv); });
    const weight = (list) => Math.min(...list.map((mv) => (mv.trend === "breaks" ? 0 : 3) + Math.max(0, WHY_ORDER.indexOf(mv.why))));
    const rowCards = Array.from(byRow, ([row, list], i) => ({ row, list, i })).sort((a, b) => weight(a.list) - weight(b.list) || a.i - b.i)
      .map(({ row, list }) => {
        const name = rowName(row);
        const cells = cellsOfMoves(row, list, mLatest);
        const down = list.some((mv) => isNum(mv.ours) && isNum(mv.theirs) && mv.ours < mv.theirs);
        const flagged = cells.filter(([, q]) => ((HB.data.cell(row, q) || {}).flags || []).some((f) => ANSWER_FLAGS[f]));
        const empty = list.filter((mv) => mv.why === "nothing_found").sort((a, b) => rank(b.key) - rank(a.key))[0];   // a section before the overall score
        let action;
        let cta;
        if (empty) {
          cta = "Send the document";
          const intro = `${empty.text} If a document shows it, send it or a link to it.`;
          const what = empty.key === "overall" ? "The overall score" : levelShort(empty.key);
          const send = HB.supply && HB.supply.ready()
            ? (HB.supply.sent(row, SEND_PATTERN)
              ? h("button", { type: "button", class: "btn btn--small", disabled: true }, "Sent")
              : h("button", { type: "button", class: "btn btn--small btn--primary", onclick: async (e) => {
                const button = e.currentTarget;
                const ok = await HB.supply.send({ row, name, pattern: SEND_PATTERN, what: name, intro, kicker: "Check these first" });
                if (ok) { button.textContent = "Sent"; button.disabled = true; button.classList.remove("btn--primary"); }
              } }, "Send the document or link"))
            : h("p", { class: "small" }, HB.when("The page can't take files just now. Reload it and try again.", `This view can't take files. Open the project's own page to send the document for ${name}.`));
          action = actBox("Send us the document or link.",
            `${what} was ${figureText(empty.theirs, empty.key)} in ${latest}, but this run found nothing for it. Claude files what you send and scores the answers again.`,
            h("div", { class: "cmp-act__do" }, send), answerButtons(row, cells, down, ctx));
        } else if (flagged.length) {
          const counts = {};
          flagged.forEach(([, q]) => ((HB.data.cell(row, q) || {}).flags || []).forEach((f) => { if (ANSWER_FLAGS[f]) counts[f] = (counts[f] || 0) + 1; }));
          const bits = [counts.quote_not_found ? `${plural(counts.quote_not_found, "quote was", "quotes were")} not found in ${counts.quote_not_found === 1 ? "its document" : "their documents"}` : null,
            counts.weak_source ? `${plural(counts.weak_source, "answer rests", "answers rest")} on a weak source` : null].filter(Boolean);
          cta = flagged.length === 1 ? "Open the answer" : "Open the answers";
          action = actBox(cta + ".",
            `${capital(bits.join(", and "))}. Check the source says what the answer claims.`, answerButtons(row, cells, down, ctx));
        } else {
          cta = "Open the answers";
          action = actBox("Open the answers.",
            "Nothing in the files explains this. Look for a document that is missing or out of date, or a question read differently from last year.",
            answerButtons(row, cells, down, ctx));
        }
        return firstCard("row:" + row, { title: name, summary: movesSummary(list, mLatest), cta },
          action,
          h("div", { class: "cmp-card__details" },
            h("p", { class: "cmp-card__meta" },
              h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => latestWords(() => openDetail(row, mLatest, ctx)) },
                "Every figure, both years", h("span", { class: "visually-hidden" }, `: ${name}`)),
              h("span", { class: "mono muted small" }, row),
              ...Array.from(new Set(list.map(whyWord)), (w) => h("span", { class: "cmp-first__why" }, w))),
            h("ul", { class: "cmp-first__moves" }, list.map((mv) => moveLine(mv, hist)))));
      });

    const cards = ruleCards.concat(rowCards);
    if (!seeded && cards.length) {   // the first card starts open, to show what an open card holds
      seeded = true;
      cards[0].setOpen(true);
    }
    const all = (on) => cards.forEach((x) => x.setOpen(on));
    const group = (title, list) => (list.length ? h("div", { class: "cmp-first__group" },
      h("h4", { class: "cmp-first__gtitle" }, `${title} (${fmtInt(list.length)})`),
      h("ol", { class: "cmp-first__list" }, list.map((x) => x.node))) : null);
    return h("section", { class: "section cmp-first", "aria-labelledby": "cmp-first" },
      h("div", { class: "section__head cmp-first__headrow" }, h("h3", { id: "cmp-first" }, `Check these first (${fmtInt(cards.length)})`),
        h("div", { class: "cmp-first__tools" },
          h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => all(true) }, "Expand all"),
          h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => all(false) }, "Collapse all"))),
      h("p", { class: "small muted cmp-measure" },
        `The biggest differences from the ${latest} edition, each with what you can do about it. Open one to act on it.` +
        (hist && hist.editions.length ? ` A small line under each move shows the ${nouns.one}'s past editions; the last dot is this run, and the shaded band is its past range.` : "")),
      group(ruleCards.some((x) => x.decided) ? "Rules to decide or re-run" : "Open rules to decide", ruleCards),
      group(`${capital(nouns.many)} to check`, rowCards));
  }

  /** "Over time": a line per assessment (or per group's mean) across the past editions, this run last. */
  function timeView(v, ctx) {
    const hist = v.hist;
    const nouns = parts().unitNouns();
    const p = bundle().project || {};
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const mode = labels.length && v.state.lines === "group" ? "group" : "row";
    const words = (lb) => lb.replace(/_/g, " ");
    const tools = [];
    if (labels.length) {
      tools.push(segmented("Lines", [["row", `One per ${nouns.one}`], ["group", "One per group"]], mode, (x) => {
        v.state.lines = x;
        HB.store.set(LINES_KEY, x);
        v.redraw("lines-" + x);
      }, "lines").node);
    }
    const label = mode === "group" ? (labels.includes(ctx.state.groupBy) ? ctx.state.groupBy : labels[0]) : null;
    if (label) {
      tools.push(chooser("Group by", labels.map((lb) => [lb, capital(words(lb))]), label, (lb) => {
        refocus = "group-by";
        ctx.setGroupBy(lb);
      }, "group-by").node);
    }
    const L = v.L;
    const editions = hist.editions;
    const xs = editions.map((e) => ({ id: e, label: e })).concat([{ id: NOW, label: "This run", now: true }]);
    const moved = new Set((v.latest.big_moves || []).filter((mv) => mv.key === L || mv.key === "band:" + L).map((mv) => mv.row));
    const valuesOf = (row) => {
      const out = {};
      const past = ((hist.rows || {})[row] || {})[L] || {};
      editions.forEach((e) => { if (isNum(past[e])) out[e] = past[e]; });
      const now = ((hist.this_run || {})[row] || {})[L];
      if (isNum(now)) out[NOW] = now;
      return out;
    };
    const rowsWith = (bundle().units || []).map((u) => u.id).filter((row) => editions.some((e) => isNum(valuesOf(row)[e])));
    let series;
    if (mode === "row") {
      series = rowsWith.map((row) => ({ id: row, label: rowName(row), values: valuesOf(row), highlight: moved.has(row),
        note: moved.has(row) ? "A big move: in Check these first" : null }));
    } else {
      const units = new Map((bundle().units || []).map((u) => [u.id, u]));
      const valueOf = (row) => { const x = ((units.get(row) || {}).labels || {})[label]; return x == null || x === "" ? null : String(x); };
      const groups = HB.data.labelValues(label).map((g) => ({ id: g, name: g, rows: rowsWith.filter((row) => valueOf(row) === g) }));
      const loose = rowsWith.filter((row) => valueOf(row) == null);
      if (loose.length) groups.push({ id: "", name: `No ${words(label)}`, rows: loose });
      series = groups.filter((g) => g.rows.length).map((g) => {
        const values = {};
        xs.forEach((x) => {
          const got = g.rows.map((row) => valuesOf(row)[x.id]).filter(isNum);
          if (got.length) values[x.id] = Math.round((got.reduce((s, n) => s + n, 0) / got.length) * 10) / 10;
        });
        return { id: g.id, label: g.name, values, note: `The mean of ${plural(g.rows.length, nouns.one, nouns.many)}` };
      });
    }
    const sc = v.sc;
    const fig = HB.charts.lines({
      label: `${levelLong(L)} in each past edition and in this run, one line per ${mode === "row" ? nouns.one : words(label)}`,
      xs, series, max: sc.max, suffix: sc.suffix,
      guides: sc.bands.map((b) => b.min).filter((x) => x > 0),
      yLabel: levelLong(L),
      labelHeader: mode === "row" ? capital(nouns.one) : capital(words(label)),
      highlightLabel: "A big move", othersLabel: `Other ${nouns.many}`,
      pastLabel: "Past editions", nowLabel: "This run",
      onPick: mode === "row" ? (id) => { if (v.m.rows.some((r) => r.row === id)) v.open(id); } : null,
      empty: `No ${nouns.one} has a figure at this level in a past edition.`,
      note: mode === "group" ? `Each line is the mean of the ${nouns.many} in that group with a figure in that edition, so the ${nouns.many} behind a point can differ from one edition to the next.` : null,
      caption: `Each line runs through the past editions, ${editions[0]} to ${editions[editions.length - 1]}; the last point, after the dashed step, is this run.` +
        (moved.size && mode === "row" ? ` The ${plural(moved.size, nouns.one, nouns.many)} with a big move at this level ${moved.size === 1 ? "is" : "are"} in colour.` : "") +
        " Point at or focus a line for its figures" + (mode === "row" ? "; click it for the details." : "."),
    });
    return h("div", { class: "stack" },
      tools.length ? h("div", { class: "cmp-tools cmp-tools--inner" }, tools) : null,
      h("p", { class: "cmp-sum" }, `${plural(rowsWith.length, nouns.one, nouns.many)} with a figure at this level in at least one past edition` +
        ` (${editions.length === 1 ? editions[0] : `${editions[0]} to ${editions[editions.length - 1]}`}).`),
      fig);
  }

  /* ---- The screen ------------------------------------------------------------------ */

  function viewList(m, hist) {
    const word = (() => { const s = parts().bandSets()[0]; return s ? parts().bandWord(s) : "band"; })();
    const anyBand = m.levels.some((L) => m.rows.some((r) => (r.ours || {})["band:" + L] != null));
    const anyRule = Array.isArray(bundle().rules) && bundle().rules.length > 0;
    return [
      { id: "dots", label: "Dots", show: true },
      { id: "table", label: "Table", show: true },
      { id: "bars", label: "Bars", show: true },
      { id: "scatter", label: "Scatter", show: true },
      anyBand ? { id: "tiers", label: `${capital(word)} moves` } : null,
      { id: "groups", label: "By group" },
      anyRule ? { id: "rules", label: "By rule" } : null,
      olderEditions(hist).length ? { id: "time", label: "Over time" } : null,
    ].filter(Boolean);
  }

  function summaryLine(v) {
    const nouns = parts().unitNouns();
    const has = v.items.filter((a) => a.has);
    const higher = has.filter((a) => a.gap > 0).length;
    const lower = has.filter((a) => a.gap < 0).length;
    const same = has.length - higher - lower;
    const tiered = v.items.filter((a) => a.tierO != null && a.tierT != null);
    const missing = v.items.length - has.length;
    const unexplained = (v.m.c.disagreements || []).filter((d) => (d.key === v.L || d.key === "band:" + v.L) && d.reason === "unexplained").length;
    return h("div", { class: "cmp-sumwrap" },
      h("p", { class: "cmp-sum" }, h("strong", null, levelLong(v.L)),
        `. ${fmtInt(same)} of ${fmtInt(has.length)} the same as ${them}, ${fmtInt(higher)} higher, ${fmtInt(lower)} lower.` +
        (tiered.length ? ` Same ${v.sc.word}: ${fmtInt(tiered.filter((a) => a.tierSame).length)} of ${fmtInt(tiered.length)}.` : "") +
        (missing ? ` ${plural(missing, nouns.one, nouns.many)} without a figure in both years.` : "")),
      unexplained ? h("p", { class: "small muted" },
        `${plural(unexplained, "difference", "differences")} at this level ${unexplained === 1 ? "is" : "are"} not explained yet. Open ${unexplained === 1 ? "it" : "one"} below and press Ask Claude why.`) : null);
  }

  function notCompared(c) {
    const list = (c.summary || {}).not_compared || [];
    if (!list.length) return null;
    const nouns = parts().unitNouns();
    return h("section", { class: "section", "aria-labelledby": "cmp-not" },
      h("div", { class: "section__head" }, h("h3", { id: "cmp-not" }, `Not compared (${fmtInt(list.length)})`)),
      h("p", { class: "small muted" }, `${plural(list.length, nouns.one, nouns.many)} left out of every figure above.`),
      h("ul", { class: "cmp-not" }, list.map((x) => h("li", null,
        h("strong", null, rowName(x.row)), " ", h("span", { class: "mono muted small" }, x.row), ": ", x.text || x.why))));
  }

  function render(root, ctx) {
    const latest = (bundle().results || {}).compare;
    if (!latest) {
      root.appendChild(HB.notice("neutral", HB.when("There is no comparison with last year yet. It is made once this year's answers are scored.",
        "There is no comparison with last year yet. Claude makes it the next time it works on this project.")));
      return;
    }
    const hist = historyOf(latest);
    if (!olderEditions(hist).includes(picked)) picked = null;
    setWords(picked);
    const c = picked ? editionCompare(latest, hist, picked) : latest;
    const mLatest = makeModel(latest);
    const m = picked ? makeModel(c) : mLatest;
    const nouns = parts().unitNouns();
    const first = checkFirst(latest, hist, ctx, mLatest);
    const editions = isFree() ? freeNote() : editionPicker(hist, ctx);
    if (!m.levels.length) {
      root.appendChild(h("div", { class: "stack stack--loose" }, first, editions, agreement(c, m),
        HB.notice("neutral", "No figure is in both editions, so there is nothing to set side by side."), notCompared(c)));
      return;
    }
    const views = viewList(m, hist);
    const asked = ctx.route.parts[1];
    const kept = HB.store.get(VIEW_KEY, null);
    const view = (views.find((x) => x.id === asked) || views.find((x) => x.id === kept) || views[0]).id;
    const savedLevel = HB.store.get(LEVEL_KEY, null);
    const state = {
      view,
      level: m.levels.includes(savedLevel) ? savedLevel : m.levels.includes("overall") ? "overall" : m.levels[0],
      show: HB.store.get(SHOW_KEY, "diff") === "all" ? "all" : "diff",
      reason: HB.store.get(REASON_KEY, "all"),
      lines: HB.store.get(LINES_KEY, "row") === "group" ? "group" : "row",
      pick: null,
    };

    const goView = (id) => {
      if (id === state.view) return;
      HB.store.set(VIEW_KEY, id);
      refocus = "view-" + id;
      ctx.go("results", "compare", id);
    };
    const viewWide = segmented("View", views.map((x) => [x.id, x.label]), view, goView, "view");
    const viewNarrow = chooser("View", views.map((x) => [x.id, x.label]), view, goView, "view-" + view);
    viewWide.node.classList.add("cmp-views--wide");
    viewNarrow.node.classList.add("cmp-views--narrow");
    const levelOptions = m.levels.map((L) => [L, levelShort(L)]);
    const level = (levelOptions.length <= 4 ? segmented : chooser)("Level", levelOptions, state.level, (L) => {
      state.level = L;
      state.pick = null;
      HB.store.set(LEVEL_KEY, L);
      level.set(L);
      draw();
    }, "level");
    const show = segmented("Show", [["diff", "Differences"], ["all", "All"]], state.show, (s) => {
      state.show = s;
      HB.store.set(SHOW_KEY, s);
      show.set(s);
      draw();
    }, "show");
    const showsList = views.find((x) => x.id === view).show;
    show.node.hidden = !showsList;

    const sum = h("div");
    const stage = h("div", { class: "cmp-stage" });

    function draw(focusKey) {
      const L = state.level;
      const sc = scaleFor(m, L);
      const items = m.rows.map((r) => itemAt(m, r, L));
      const diffs = items.filter((a) => (a.has && !a.same) || (!a.has && (isNum(a.ours) || isNum(a.theirs))));
      show.text("diff", `Differences ${fmtInt(diffs.length)}`);
      show.text("all", `All ${fmtInt(items.length)}`);
      const v = {
        m, L, sc, items, ctx, state, hist, latest,
        shown: byGap(state.show === "all" || !showsList ? items : diffs),
        open: (id) => openDetail(id, m, ctx),
        redraw: (key) => draw(key),
      };
      sum.replaceChildren(summaryLine(v));
      let node;
      try {
        node = { dots: dotsView, table: tableView, bars: barsView, scatter: scatterView, tiers: tiersView, groups: groupsView, rules: rulesView, time: timeView }[view](v, ctx);
      } catch (err) {
        console.error("The comparison view could not be drawn:", err);
        node = HB.notice("critical", "This view could not be drawn: " + (err && err.message ? err.message : String(err)));
      }
      stage.replaceChildren(node);
      if (focusKey) {   // a data-focus key, or a selector (starting with ".")
        const target = stage.querySelector(focusKey.startsWith(".") ? focusKey : `[data-focus="${focusKey}"]`);
        if (target) target.focus({ preventScroll: true });
      }
    }
    draw();

    const tools = h("div", { class: "cmp-tools", role: "group", "aria-label": "Comparison view" },
      viewWide.node, viewNarrow.node, level.node, show.node);
    const views$ = h("section", { class: "section cmp-main", "aria-labelledby": "cmp-views" },
      h("div", { class: "section__head" }, h("h3", { id: "cmp-views" }, `${capital(nouns.one)} by ${nouns.one}`)),
      tools, sum, stage);
    root.appendChild(h("div", { class: "stack stack--loose" }, first, editions, agreement(c, m), views$, notCompared(c)));

    if (refocus) {
      const key = refocus;
      refocus = null;
      requestAnimationFrame(() => {
        const target = Array.from(root.querySelectorAll(`[data-focus="${key}"]`)).find((x) => x.offsetParent !== null);
        if (target) target.focus({ preventScroll: true });
      });
    }
  }

  HB.addStyles("compare", `
    .cmp-measure { max-width: var(--measure); }
    .cmp-top { gap: var(--s3); }
    .cmp-agree-wrap { width: fit-content; max-width: 100%; }
    .cmp-agree th, .cmp-agree td { padding-block: var(--s1); }
    .cmp-agree__c { min-width: 9rem; }
    .cmp-agree__v { display: inline-block; min-width: 4.5rem; font-variant-numeric: lining-nums tabular-nums; }
    .cmp-agree__v strong { color: var(--ink); font-size: var(--fs-16); }
    .cmp-meter { display: inline-block; position: relative; width: 4.5rem; height: 0.375rem; margin-left: var(--s2);
      vertical-align: middle; border-radius: 2px; background: var(--rule); overflow: hidden; }
    .cmp-meter > span { position: absolute; inset: 0 auto 0 0; border-radius: 2px; background: var(--series-1); }

    .cmp-main { gap: var(--s3); }
    .cmp-tools { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--s3) var(--s4); }
    .cmp-tool { display: grid; gap: 0.25rem; min-width: 0; max-width: 100%; }
    .cmp-tool__label { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; }
    .cmp-seg { max-width: 100%; }
    .cmp-seg > .btn { min-height: 1.875rem; }
    .cmp-tool .cmp-select { width: auto; min-width: 10rem; max-width: 100%; min-height: 2rem; padding-block: 0.1875rem; font-size: var(--fs-14); }
    .cmp-stage .table tbody th { min-width: 8rem; }
    @media (width >= 40rem) { .cmp-stage .table tbody th { min-width: 11rem; } }
    .cmp-cellsbtn, .cmp-nowrap { white-space: nowrap; }
    .cmp-views--narrow { display: none; }
    @media (width < 48rem) {
      .cmp-views--wide { display: none; }
      .cmp-views--narrow { display: grid; }
    }
    .cmp-sumwrap { display: grid; gap: var(--s1); }
    .cmp-sum { max-width: var(--measure); color: var(--ink); }
    .cmp-stage { min-width: 0; }

    .cmp-g { flex: none; width: 0.875rem; height: 0.875rem; color: var(--ink-2); overflow: visible; }
    .cmp-g .g-l { fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
    .cmp-g .g-f { fill: currentColor; }
    .cmp-reason { display: inline-flex; align-items: center; gap: 0.375rem; white-space: nowrap; }
    .cmp-moved { color: var(--ink-2); }
    .cmp-cells { display: inline-flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); }
    .cmp-linkbtn { padding: 0; border: 0; background: none; color: var(--accent); font: inherit; font-weight: 600; text-align: left;
      text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; cursor: pointer; }
    .cmp-linkbtn[aria-pressed="true"] { color: var(--ink); text-decoration-thickness: 2px; }
    .cmp-share { display: inline-flex; align-items: center; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .cmp-share strong { color: var(--ink); }
    .cmp-mean { display: inline-flex; align-items: center; justify-content: flex-end; gap: var(--s2); }
    .cmp-mean__track { position: relative; width: 4rem; height: 0.625rem;
      background: linear-gradient(to right, transparent calc(50% - 0.5px), var(--rule-strong) calc(50% - 0.5px), var(--rule-strong) calc(50% + 0.5px), transparent calc(50% + 0.5px)); }
    .cmp-mean__bar { position: absolute; top: 0; bottom: 0; }
    .cmp-mean__bar.is-pos { background: var(--series-1); border-radius: 0 3px 3px 0; }
    .cmp-mean__bar.is-neg { background: var(--series-2); border-radius: 3px 0 0 3px; }
    .cmp-mean__v { min-width: 2.75rem; font-weight: 600; }
    .cmp-picked { display: grid; gap: var(--s2); padding: var(--s3) var(--s4); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
    .cmp-picked__title { margin: 0; font-size: var(--fs-14); }
    .cmp-picked__list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr)); gap: var(--s1) var(--s4); margin: 0; padding: 0; list-style: none; }
    .cmp-picked__btn { display: flex; width: 100%; justify-content: space-between; gap: var(--s2); padding: var(--s1) var(--s2);
      border: 0; border-radius: var(--radius); background: transparent; color: var(--ink); font: inherit; font-size: var(--fs-14); text-align: left; cursor: pointer; }
    .cmp-picked__btn:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); }
    .cmp-picked__gap { display: inline-flex; align-items: center; gap: 0.3rem; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .cmp-why { display: grid; gap: var(--s1); padding-top: var(--s3); border-top: 1px solid var(--rule); }
    .cmp-why__title, .cmp-why__head { margin: 0; font-size: var(--fs-16); }
    .cmp-why__head { font-size: var(--fs-14); }
    .cmp-why__line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
    .cmp-not { display: grid; gap: var(--s1); margin: 0; padding-left: 1.25rem; max-width: var(--measure); }

    .cmp-first { gap: var(--s3); }
    .cmp-first__headrow { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--s2) var(--s3); }
    .cmp-first__tools { display: flex; flex-wrap: wrap; gap: var(--s2); }
    .cmp-first__group { display: grid; gap: var(--s2); }
    .cmp-first__gtitle { margin: 0; color: var(--ink-2); font-size: var(--fs-14); }
    .cmp-first__list { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
    .cmp-card { min-width: 0; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
    .cmp-card__toggle { display: grid; grid-template-columns: 1rem minmax(0, 1fr) auto; align-items: baseline; gap: 0.125rem var(--s2);
      width: 100%; padding: var(--s2) var(--s3); border: 0; border-radius: var(--radius); background: none; color: var(--ink);
      font: inherit; text-align: left; cursor: pointer; }
    .cmp-card__toggle:hover { background: color-mix(in srgb, var(--ink) 4%, transparent); }
    .cmp-card__chev { grid-row: 1; width: 0.5rem; height: 0.5rem; border-right: 2px solid var(--ink-2); border-bottom: 2px solid var(--ink-2);
      transform: translateY(-0.1rem) rotate(-45deg); transition: transform 0.15s; }
    .cmp-card__toggle[aria-expanded="true"] .cmp-card__chev { transform: translateY(-0.2rem) rotate(45deg); }
    .cmp-card__title { grid-column: 2; min-width: 0; }
    .cmp-card__kind { color: var(--ink-2); font-weight: 400; }
    .cmp-card__sum { grid-column: 2; color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
    .cmp-card__cta { grid-column: 3; grid-row: 1; justify-self: end; padding: 0 var(--s2); border: 1px solid var(--accent); border-radius: 999px;
      color: var(--accent); font-size: var(--fs-12); font-weight: 600; white-space: nowrap; }
    @media (width < 40rem) {
      .cmp-card__toggle { grid-template-columns: 1rem minmax(0, 1fr); }
      .cmp-card__cta { grid-column: 2; grid-row: auto; justify-self: start; }
    }
    .cmp-card__body { display: grid; gap: var(--s3); padding: 0 var(--s3) var(--s3) calc(var(--s3) + 1rem + var(--s2)); }
    .cmp-card__body[hidden] { display: none; }
    @media (width < 40rem) { .cmp-card__body { padding-left: var(--s3); } }
    .cmp-card__details { display: grid; gap: var(--s2); min-width: 0; }
    .cmp-card__details > p { margin: 0; }
    .cmp-card__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); }
    .cmp-card__rows { display: grid; gap: var(--s1); margin: 0; padding-left: 1.25rem; font-size: var(--fs-14); }
    .cmp-act { display: grid; gap: var(--s2); min-width: 0; padding: var(--s2) var(--s3); border-left: 3px solid var(--accent);
      border-radius: 0 var(--radius) var(--radius) 0; background: var(--accent-soft); }
    .cmp-act__lead, .cmp-act__note { max-width: var(--measure); margin: 0; }
    .cmp-act__lead strong { color: var(--ink); }
    .cmp-act__do { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s3); margin: 0; }
    .cmp-answers { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); }
    .cmp-answer { display: inline-flex; align-items: baseline; gap: 0.375rem; min-height: 1.875rem; padding: 0.125rem var(--s2);
      border: 1px solid var(--rule-strong); border-radius: var(--radius); background: var(--surface); color: var(--ink); font: inherit; font-size: var(--fs-14); cursor: pointer; }
    .cmp-answer:hover { border-color: var(--accent); }
    .cmp-answer__q { color: var(--accent); font-weight: 600; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; }
    .cmp-answer__pts { color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
    .cmp-answer__flag { display: inline-flex; align-items: center; gap: 0.25rem; font-size: var(--fs-12); font-weight: 600; }
    .cmp-answer__flag::before { content: ""; width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--warning); }
    .cmp-first__moves { display: grid; gap: var(--s3); margin: 0; padding: 0; list-style: none; }
    .cmp-first__move { display: grid; gap: var(--s1); }
    .cmp-first__move + .cmp-first__move { padding-top: var(--s3); border-top: 1px solid var(--rule); }
    .cmp-first__what { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); font-size: var(--fs-14); }
    .cmp-first__why { padding: 0 var(--s2); border: 1px solid var(--rule-strong); border-radius: 999px; color: var(--ink-2); font-size: var(--fs-12); }
    .cmp-first__text { max-width: var(--measure); margin: 0; }
    .cmp-first__trend { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); }
    .cmp-editions { gap: var(--s2); }
  `);

  /* For the corner chat: rerun(ruleId, rows) is the Re-run button's save (Promise of the saved
     doc); rerunWaiting(ruleId) is true once a re-run is asked for here or waits in control.json. */
  function rerunById(ruleId, rows) {
    const rule = rulesById().get(String(ruleId));
    if (!rule) return Promise.reject(new Error("There is no rule " + ruleId + "."));
    return askRerun(rule, Array.isArray(rows) ? rows : []);
  }

  /** The comparison of one assessment with the latest past edition, in the drawer: the same panel the
      comparison's own rows open. For the Summary's biggest movers. False when there is no comparison or
      the assessment is not in it. */
  function openFor(rowId, ctx) {
    const latest = (bundle().results || {}).compare;
    if (!latest || !Array.isArray(latest.rows) || !latest.rows.some((r) => r.row === rowId)) return false;
    latestWords(() => openDetail(rowId, makeModel(latest), ctx));
    return true;
  }

  HB.compareView = { render, openDetail: openFor, REASON_WORDS, BY_WORDS, rerun: rerunById, rerunWaiting: (ruleId) => rerunWaiting({ id: String(ruleId) }) };
})((window.HB = window.HB || {}));
