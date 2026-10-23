/* =============================================================================
   Handbook viewer: cost.js, what Claude's work costs in tokens of the owner's
   Claude plan, from the bundle's `cost` part (lib/handbook/costs.py, the same
   formula): a rough range before work runs, and what was used so far.

   HB.cost.part()               the bundle's cost part, or null
   HB.cost.rows(rows, qids)     a range for scoring those rows again on those
                                questions (null: every question that applies),
                                with finding the documents of rows not looked for yet:
                                {low, high, point, rows, cells, basis, measured,
                                find_rows, find_shared, finding}, or null when there
                                is nothing to score or no part
   HB.cost.findWords(est)       the sentence saying what finding adds, or ""
   HB.cost.setupDraft(chars)    a range for the set-up draft over the owner's
                                material (chars characters in all): {low, high, basis}
   HB.cost.chatTurn()           {low, high} for one corner chat reply
   HB.cost.tokens(n)            "8,000", "430,000", "1.2 million"
   HB.cost.range(est)           "about 430,000 to 1.1 million tokens"
   HB.cost.basis(est)           what the range rests on, in a few words
   HB.cost.compare(n)           n against work already run here ("about 3 times
                                what your trial run on 15 October used"), or null
   HB.cost.line(est)            the sentence a Confirm card or a button carries:
                                range, "of your Claude plan", and the comparison
   HB.cost.used()               the part's `used` (total, by_kind, runs, chat)

   Tokens only: no money, and no share of a plan (Anthropic publishes no
   allowance in tokens), so the page compares with work the owner has run.
   ========================================================================== */

(function (HB) {
  "use strict";

  const WHEN = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" });
  const KIND = { trial: "your trial run", full: "your full run", rerun: "the re-run" };

  function part() {
    const b = HB.data && HB.data.bundle ? HB.data.bundle() : null;
    const c = b && b.cost;
    return c && typeof c === "object" && c.model ? c : null;
  }

  /* costs.score_tokens: a row is read without tools, in one message or in parts when it is too long (the
     pages once, the brief and questions once a part), counting the questions' own text when it is known;
     careful scoring uses the full scorer's formula. A bundle from before 21 Oct 2026 has no one-read figures
     and keeps the full scorer's formula. */
  function scoreTokens(m, questions, chars, qchars) {
    const c = chars == null ? m.default_chars : Math.max(chars, m.min_chars);
    const q = Math.max(questions, 1);
    const o = m.one_read;
    if (!o || m.careful) return m.a * (m.factor || 1) * Math.pow(q, m.q_exp) * Math.pow(c, m.c_exp);
    const qt = qchars ? qchars / o.fit_chars_per_token : o.question_tokens * q;
    const pages = c / o.fit_chars_per_token;
    const room = Math.max(o.limit - qt - (o.note_room || 0), o.limit / 4);
    const parts = pages + qt <= o.limit ? 1 : Math.max(2, Math.ceil(Math.floor(pages) / Math.floor(room)));
    const extra = Math.max(0, qt - o.question_tokens * q);
    return (m.factor || 1) * (parts * (o.base + o.per_question * q + extra) + c / o.chars_per_token);
  }

  /** costs.estimate_rows(..., finding=True): a row not looked for yet also costs finding its documents (unless only
      the owner's data answers it) and may turn out to hold big documents; answers that wait for the owner's data
      are not scored. */
  function rows(ids, qids) {
    const c = part();
    if (!c) return null;
    const m = c.model;
    const b = HB.data.bundle() || {};
    const cells = b.cells || {};
    const qtext = new Map((Array.isArray(b.questions) ? b.questions : []).filter((q) => q && q.id).map((q) => [String(q.id), JSON.stringify(q).length]));
    const wanted = Array.isArray(qids) && qids.length ? new Set(qids.map(String)) : null;
    const held = c.held || {};
    const owner = c.owner_only || {};
    const groups = c.groups || {};
    // groups with a row looked for already: a later row of one finds the group's documents filed for it
    const seen = new Set(Object.keys(c.rows || {}).flatMap((r) => groups[r] || []));
    let shared = 0;
    let point = 0;
    let low = 0;
    let high = 0;
    let n = 0;
    let count = 0;
    let finds = 0;
    let fPoint = 0;
    let fLow = 0;
    let fHigh = 0;
    for (const row of Array.from(new Set(ids || []))) {
      const waits = new Set(held[row] || []);
      const qs = Object.keys(cells[row] || {}).filter((q) => (!wanted || wanted.has(q)) && !waits.has(q));
      if (!qs.length) continue;
      n += 1;
      count += qs.length;
      const known = Object.prototype.hasOwnProperty.call(c.rows || {}, row);
      const qchars = qs.reduce((sum, q) => sum + (qtext.get(q) || 0), 0);
      const s = scoreTokens(m, qs.length, known ? c.rows[row] : m.middle_chars, qchars);
      point += s;
      low += s * m.low;
      high += s * m.high;
      if (!known && c.find) {
        // a row not looked for yet may hold big documents, read in parts
        const top = Math.max(s * m.high, scoreTokens(m, qs.length, Math.max(m.middle_chars, m.big_chars || 0), qchars));
        high += top - s * m.high;
        const mine = new Set(owner[row] || []);
        if (qs.some((q) => !mine.has(q))) {
          finds += 1;
          const share = (groups[row] || []).some((k) => seen.has(k)) ? (c.group_find_share || 1) : 1;
          if (share < 1) shared += 1;
          (groups[row] || []).forEach((k) => seen.add(k));
          point += share * c.find.point;
          low += share * c.find.low;
          high += share * c.find.high;
          fPoint += share * c.find.point;
          fLow += share * c.find.low;
          fHigh += share * c.find.high;
          if (c.second) {          // the wider second search most such rows get, and those answers again
            // the rescore reads only what the search added (costs.SECOND_CHARS), for half the questions; a bundle
            // from before 22 Oct 2026 has no such figure and takes half the row
            const half = Math.max(1, Math.floor(qs.length / 2));
            const hchars = Math.floor(qchars * half / qs.length);
            const ch = Array.isArray(c.second.chars) ? c.second.chars : null;
            point += c.second.share * (c.second.point + (ch ? scoreTokens(m, half, ch[0], hchars) : s / 2));
            high += c.second.high + (ch ? scoreTokens(m, half, ch[1], hchars) * m.high : top / 2);
            fPoint += c.second.share * c.second.point;
            fHigh += c.second.high;
          }
        }
      }
    }
    if (!n) return null;
    return { rows: n, cells: count, point: Math.round(point), low: Math.round(low), high: Math.round(high),
      basis: m.basis, measured: m.rows || 0, find_rows: finds, find_shared: shared,
      finding: finds ? { rows: finds, point: Math.round(fPoint), low: Math.round(fLow), high: Math.round(fHigh) } : null };
  }

  /** " That includes about X to Y for finding the documents of N rows not searched yet." or "". */
  function findWords(est) {
    if (!est || !est.finding || !est.finding.rows) return "";
    const n = est.finding.rows;
    const who = HB.data && typeof HB.data.unitWords === "function" ? HB.data.unitWords(n) : HB.fmtInt(n) + " " + (n === 1 ? "row" : "rows");
    return " That includes " + range(est.finding) + " for finding the documents of " + who + " not searched yet.";
  }

  function setupDraft(chars) {
    const c = part();
    const s = (c && c.setup) || { small: [300000, 1200000], big: [2000000, 6000000], big_material: 200000 };
    if (s.last) return { low: Math.round(s.last * 0.7), high: Math.round(s.last * 1.4), point: s.last, basis: "last_draft" };
    const [low, high] = (chars || 0) > s.big_material ? s.big : s.small;
    return { low, high, point: Math.round(Math.sqrt(low * high)), basis: "setup_default" };
  }

  function chatTurn() {
    const c = part();
    const t = (c && c.chat_turn) || [30000, 100000];
    return { low: t[0], high: t[1] };
  }

  /** Two significant figures: "8,000", "430,000", "1.2 million", "12 million". */
  function tokens(n) {
    const v = Math.max(0, Number(n) || 0);
    if (v >= 1e6) {
      const m = v >= 1e7 ? Math.round(v / 1e6) : Math.round(v / 1e5) / 10;
      return String(m) + " million";
    }
    if (v < 1000) return HB.fmtInt(Math.round(v));
    const digits = Math.floor(Math.log10(v)) + 1;
    const step = Math.pow(10, Math.max(digits - 2, 3));
    return HB.fmtInt(Math.round(v / step) * step);
  }

  function range(est) {
    if (!est) return "";
    const lo = tokens(est.low);
    const hi = tokens(est.high);
    return lo === hi ? "about " + hi + " tokens" : "about " + lo + " to " + hi + " tokens";
  }

  function basis(est) {
    const c = part();
    if (!est) return "";
    if (est.basis === "last_draft") return "based on your last set-up draft";
    if (est.basis === "setup_default") return "based on set-up drafts of about 25 other benchmarks, by how much material there is";
    if (est.basis === "project") {
      const n = est.measured || 0;
      return "based on the " + HB.fmtInt(n) + " " + (n === 1 ? "row" : "rows") + " already scored in this project, by how long each row's documents are";
    }
    const fit = c && c.model && c.model.fit_rows ? c.model.fit_rows : 192;
    return "based on " + HB.fmtInt(fit) + " rows scored in other benchmarks, by how long each row's documents are and how many questions";
  }

  function used() {
    const c = part();
    return (c && c.used) || { total: 0, by_kind: {}, runs: [], setup_drafts: [], chat: { replies: 0, tokens: 0, median: null } };
  }

  /** The work already run that a figure is compared with: the latest trial, else the latest run, else the last set-up draft. */
  function reference() {
    const u = used();
    const runs = (u.runs || []).filter((r) => r && r.tokens > 0);
    const latest = (list) => list.slice().sort((a, b) => String(a.started).localeCompare(String(b.started))).pop();
    const run = latest(runs.filter((r) => r.kind === "trial")) || latest(runs);
    if (run) {
      const d = Date.parse(run.started);
      return { tokens: run.tokens, words: (KIND[run.kind] || "your run") + (Number.isFinite(d) ? " on " + WHEN.format(new Date(d)) : "") };
    }
    const drafts = u.setup_drafts || [];
    if (drafts.length) return { tokens: drafts[drafts.length - 1], words: "your set-up draft", draft: true };
    return null;
  }

  function compare(n) {
    const ref = reference();
    if (!ref || !n) return null;
    const r = n / ref.tokens;
    // A set-up draft is a poor yardstick for a big scoring run ("264 times what your set-up draft used").
    if (ref.draft && r >= 10) return null;
    const what = ref.words + " used (" + tokens(ref.tokens) + ")";
    if (r >= 1.5) return "about " + (r >= 10 ? HB.fmtInt(Math.round(r)) : String(Math.round(r * 2) / 2).replace(".5", "½")) + " times what " + what;
    if (r >= 0.75) return "about the same as " + what;
    if (r >= 0.35) return "about half what " + what;
    if (r >= 0.15) return "about a quarter of what " + what;
    return "a small part of what " + what;
  }

  function line(est) {
    if (!est) return "";
    const cmp = compare(est.point || Math.sqrt(est.low * est.high));
    const text = range(est) + " of your Claude plan" + (cmp ? ", " + cmp : "");
    return text.charAt(0).toUpperCase() + text.slice(1) + ".";
  }

  HB.cost = { part, rows, findWords, setupDraft, chatTurn, tokens, range, basis, compare, line, used };
})((window.HB = window.HB || {}));
