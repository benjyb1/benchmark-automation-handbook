/* =============================================================================
   Handbook viewer: views/reportdraft.js, the Generate report button on the
   Results tab and the page's own number check.

   The bundle's results.drafting is {state: "none" | "current" | "out_of_date",
   facts (worked out when the page was built), guide (the rules of
   skills/results/report.md)}. When there is no report or it is out of date, and
   the page may ask Claude (the `sample` capability), the button sends the
   facts, the comparison summary and the guide, shows the draft as it is
   written, then checks every number in it against the facts with the rule of
   `hb check-report`, and lists any that fail. The owner can edit the draft.
   "Save as the report" (only once every number passes) writes
   {id, markdown, at, by} to the db collection `report`; Claude in chat adopts
   it into report/report.md, checks it again and publishes (host/claude.md).
   Without `sample` the panel says when the report will be written instead.

   HB.reportDraft = {
     staleNotice()               a warning for the top of the report when it is
                                 out of date, else null
     panel()                     the button, the draft and its check, or null
     check(text, facts, qids)    {numbers, unbacked: [{number, line, text}]}, ported
                                 from handbook/lib/handbook/facts.py (numbers_in,
                                 fact_numbers, unbacked): numbers from 0 to 10, years
                                 1900 to 2100, question-id numbers and link addresses
                                 pass without a fact
     prompt(bundle)              the text sent to Claude
     generate(again)             the button's own action (the corner chat starts a
                                 draft with it); canGenerate() is true when it can run
   }
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h } = HB;

  /* ---- The number check (ported from facts.py) --------------------------------- */

  const NUMBER = /(?<![\p{L}\p{N}_.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/gu;
  const LINK_URL = /\]\([^)]*\)/g;
  const BARE_URL = /(?:https?:\/\/|www\.)\S+/g;
  const LIST_MARK = /^\s*\d+[.)]\s/;
  const LINE_BREAK = /\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/;

  const DASH = new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]", "g");   // en and em dash

  const value = (token) => Math.round(Number(String(token).replace(/,/g, "")) * 1e6) / 1e6;

  function lines(text) {
    const out = String(text || "").split(LINE_BREAK);
    if (out.length && out[out.length - 1] === "") out.pop();
    return out;
  }

  /** [token, line number] for every number in the text, as facts.numbers_in finds them. */
  function numbersIn(text) {
    const found = [];
    lines(text).forEach((raw, i) => {
      const line = raw.replace(LIST_MARK, (m) => " ".repeat(m.length)).replace(LINK_URL, "]").replace(BARE_URL, " ");
      for (const m of line.matchAll(NUMBER)) found.push([m[0], i + 1]);
    });
    return found;
  }

  function factNumbers(facts) {
    const out = new Set();
    const walk = (x) => {
      if (x == null || typeof x === "boolean") return;
      if (typeof x === "number") {
        if (Number.isFinite(x)) out.add(value(x));
      } else if (typeof x === "string") {
        numbersIn(x).forEach(([t]) => out.add(value(t)));
      } else if (Array.isArray(x)) {
        x.forEach(walk);
      } else if (typeof x === "object") {
        Object.keys(x).forEach((k) => { walk(k); walk(x[k]); });
      }
    };
    walk(facts);
    return out;
  }

  function idNumbers(qids) {
    const out = new Set();
    (qids || []).forEach((q) => {
      const s = String(q);
      for (const m of s.matchAll(/\d+(?:\.\d+)?/g)) out.add(value(m[0]));
      for (const m of s.matchAll(/\d+/g)) out.add(value(m[0]));
    });
    return out;
  }

  function check(text, facts, qids) {
    const numbers = factNumbers(facts);
    const ids = idNumbers(qids);
    const all = lines(text);
    const found = numbersIn(text);
    const unbacked = found.filter(([token]) => {
      const v = value(token);
      const plain = !token.includes(",") && !token.includes(".");
      return !(numbers.has(v) || ids.has(v) || (plain && ((v >= 0 && v <= 10) || (v >= 1900 && v <= 2100))));
    }).map(([number, line]) => ({ number, line, text: String(all[line - 1] || "").trim() }));
    return { numbers: found.length, unbacked };
  }

  /* ---- What is sent to Claude ---------------------------------------------------- */

  function drafting(b) {
    const d = ((b || bundle()).results || {}).drafting;
    return d && typeof d === "object" ? d : null;
  }

  function bundle() {
    return (HB.data && HB.data.bundle()) || {};
  }

  function qids(b) {
    return ((b || bundle()).questions || []).map((q) => q && q.id).filter((x) => x != null);
  }

  function block(title, body) {
    return "=== " + title + " ===\n" + body + "\n";
  }

  function prompt(b) {
    const d = drafting(b) || {};
    const p = b.project || {};
    const cmp = (b.results || {}).compare;
    const project = {
      name: p.name ?? null, edition: p.edition ?? null, cutoff: p.cutoff ?? null,
      unit_word: p.unit_word ?? null, unit_word_plural: p.unit_word_plural ?? null,
      labels: p.labels ?? [], group_by: p.group_by ?? null,
    };
    const questions = (b.questions || []).filter(Boolean).map((q) => ({ id: q.id, short: q.short ?? null, section: q.section ?? null }));
    const rules = (b.rules || []).filter((r) => r && (r.status === "open" || r.status === "skipped"))
      .map((r) => ({ id: r.id, status: r.status, ask: r.ask ?? null, questions: r.question_ids ?? [] }));
    const plain = (b.scoresheet || {}).plain;
    return [
      "Draft the report for this benchmark. The owner will read your draft, may change it, and saves it as the " +
        "report. A script then checks every number in it against FACTS.",
      "Write it exactly as GUIDE says. In GUIDE, facts.json is FACTS below, project.json is PROJECT, the score " +
        "sheet's plain words are HOW SCORES ARE BUILT and rules.json is RULES NOT YET DECIDED.",
      "Every figure comes from FACTS, written exactly as FACTS has it. Never work a number out: no differences, " +
        "shares, ratios or totals. Numbers from 0 to 10, years and question ids need no fact.",
      "Reply with the report only, in Markdown, starting with its title line. No words before or after it and no code fence.",
      "",
      block("GUIDE", d.guide || "(missing: follow the rules above)"),
      block("PROJECT", JSON.stringify(project)),
      block("HOW SCORES ARE BUILT", plain ? String(plain) : "(not given)"),
      block("QUESTIONS", JSON.stringify(questions)),
      block("RULES NOT YET DECIDED", rules.length ? JSON.stringify(rules) : "None."),
      block("FACTS", JSON.stringify(d.facts || {})),
      block("COMPARISON WITH LAST EDITION", cmp
        ? JSON.stringify({ level: cmp.level ?? null, edition: cmp.edition ?? null, summary: cmp.summary ?? null })
        : "None: last edition's results were not given, so the report has no section on last edition."),
    ].join("\n");
  }

  /** The reply without a code fence around it, if Claude added one anyway. */
  function unfence(text) {
    const m = /^\s*```[a-z]*\s*\n([\s\S]*?)\n\s*```\s*$/i.exec(String(text || ""));
    return (m ? m[1] : String(text || "")).trim() + "\n";
  }

  /* ---- State ----------------------------------------------------------------------- */

  const SAMPLE_GONE = ["not_granted", "sampling_disabled", "not_declared", "capability_disabled", "capability_removed"];
  const SAMPLE_SAYS = {
    rate_limited: "Claude has had too many requests from you just now. Try again in a few minutes.",
    session_expired: "Your Claude sign-in has expired. Sign in again, then try again.",
    refused: "Claude declined to write this draft. Try again in a while.",
    empty_completion: "Claude sent back nothing. Try again.",
    prompt_too_large: "The figures are too large to send from the page, so the report can't be drafted here.",
    upstream_error: "The draft was interrupted. Try again.",
  };
  const DB_SAYS = {
    invalid_argument: "The page refused this save. You can probably read this page but not change it. Ask its owner for edit access, and copy the draft so you keep it.",
    quota_exceeded: "The page's store is full, so the draft could not be saved. Copy the draft so you keep it.",
    resource_exhausted: "Too many saves in a short time. Wait a moment and try again.",
    unavailable: "The page's store did not answer. Try again in a moment.",
    revoked: "This page has lost its link to the store. Copy the draft so you keep it, then reopen the page.",
  };

  const st = {
    sample: null,          // the sample function once the page may ask Claude
    can: "unknown",        // "unknown" | "yes" | "no"
    phase: "idle",         // "idle" | "thinking" | "writing" | "done"
    text: "",              // the draft, Markdown
    note: null,            // {tone, text} about the last attempt
    editing: false,
    ctl: null,
    saving: false,
    saved: null,           // the doc this page saved
    saveError: null,
    db: null,
    user: null,
    waiting: 0,            // drafts in the `report` collection that Claude has not adopted yet
  };
  const ui = { box: null, preview: null, status: null, checkBox: null, save: null };

  function connected(el) {
    return Boolean(el && el.isConnected);
  }

  function repaint() {
    if (connected(ui.box)) fill(ui.box);
  }

  function start() {
    const c = typeof window !== "undefined" ? window.claude : null;
    if (!c || typeof c.use !== "function") {
      st.can = "no";
      return;
    }
    Promise.resolve(c.use("sample")).then((s) => {
      st.sample = typeof s === "function" ? s : null;
      st.can = st.sample ? "yes" : "no";
      repaint();
    }, () => { st.can = "no"; repaint(); });
    Promise.all([c.use("db"), c.use("user")]).then(([d, u]) => {
      if (!d) return;
      st.db = d;
      st.user = u || null;
      try {
        d.collection("report").onSnapshot((snap) => {
          st.waiting = snap && Array.isArray(snap.docs) ? snap.docs.length : 0;
          repaint();
        }, (err) => console.warn("HB.reportDraft: could not follow report:", err && err.code ? err.code : err));
      } catch (err) {
        console.warn("HB.reportDraft: could not follow report:", err);
      }
      repaint();
    }, () => {});
  }

  /* ---- Asking Claude and saving ----------------------------------------------------- */

  async function generate(again) {
    if (!st.sample || st.ctl) return;
    const ctl = new AbortController();
    Object.assign(st, { ctl, phase: "thinking", text: "", note: null, editing: false, saved: null, saveError: null });
    repaint();
    const opts = {
      signal: ctl.signal,
      modelTier: "default",
      onText: ({ text }) => {
        st.phase = "writing";
        st.text = text;
        showProgress();
      },
    };
    if (again) opts.cache = false;
    try {
      const res = await st.sample(prompt(bundle()), opts);
      st.text = unfence(res.text);
      if (res.truncated) st.note = { tone: "warning", text: "The draft was cut short, so it may stop mid-way. Try again." };
    } catch (e) {
      const code = e && e.code ? String(e.code) : "upstream_error";
      const kept = e && typeof e.text === "string" ? e.text : "";
      if (code === "cancelled") {
        st.text = kept;
        st.note = kept ? { tone: "neutral", text: "Stopped. The draft so far is below." } : null;
      } else if (SAMPLE_GONE.includes(code)) {
        st.text = "";
        st.can = "no";
        st.note = { tone: "neutral", text: noAsk() };
      } else if (code === "refused") {
        st.text = "";
        st.note = { tone: "warning", text: SAMPLE_SAYS.refused };
      } else {
        st.text = kept;
        st.note = { tone: "warning", text: SAMPLE_SAYS[code] || SAMPLE_SAYS.upstream_error };
      }
    } finally {
      st.ctl = null;
      st.phase = "done";
      repaint();
    }
  }

  function newId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return "r-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  async function save() {
    if (!st.db || st.saving || !st.text.trim()) return;
    st.saving = true;
    st.saveError = null;
    repaint();
    let by = null;
    try {
      by = st.user ? await st.user.id() : null;
    } catch (err) {
      by = null;
    }
    const id = newId();
    const doc = { id, markdown: st.text, at: new Date().toISOString(), by };
    const ref = st.db.collection("report").doc(id);
    try {
      try {
        await ref.set(doc);
      } catch (first) {
        if (!(first && first.code === "unavailable")) throw first;
        await new Promise((r) => setTimeout(r, 300 + Math.random() * 400));
        await ref.set(doc);
      }
      st.saved = doc;
    } catch (err) {
      const code = err && err.code ? String(err.code) : "unknown";
      st.saveError = "Not saved. " + (DB_SAYS[code] || "The save failed (" + code + "). Try again in a moment.");
    } finally {
      st.saving = false;
      repaint();
    }
  }

  /* ---- Drawing ---------------------------------------------------------------------- */

  function markdown(text) {
    if (HB.resultsParts && typeof HB.resultsParts.markdown === "function") return HB.resultsParts.markdown(text);
    return String(text || "").split(/\n{2,}/).map((p) => h("p", null, p));
  }

  function checkResult() {
    const d = drafting();
    const r = check(st.text, d && d.facts, qids());
    const dashes = (st.text.match(DASH) || []).length;
    const out = [];
    if (!r.numbers) out.push(HB.notice("neutral", "The draft has no numbers to check."));
    else if (!r.unbacked.length) {
      out.push(HB.notice("good", "Checked " + r.numbers + (r.numbers === 1 ? " number" : " numbers") +
        " against the figures on this page: every one is in them."));
    } else {
      const n = r.unbacked.length;
      out.push(HB.notice("critical", [
        n + (n === 1 ? " number in the draft is" : " numbers in the draft are") + " not in the figures, so it can't be saved " +
          "yet. Change " + (n === 1 ? "it" : "them") + " in the draft, or try again.",
        h("ul", { class: "rd-fails" }, r.unbacked.slice(0, 40).map((u) => h("li", null,
          "Line " + u.line + ": ", h("strong", null, u.number), " (in: " + (u.text.length > 100 ? u.text.slice(0, 97) + "..." : u.text) + ")"))),
      ]));
    }
    if (dashes) {
      out.push(HB.notice("warning", "The draft uses " + dashes + (dashes === 1 ? " dash" : " dashes") +
        " as a break. The report guide asks for a full stop, comma, colon or brackets instead."));
    }
    return { ok: Boolean(st.text.trim()) && !r.unbacked.length, nodes: out };
  }

  function showProgress() {
    if (!connected(ui.preview) || !connected(ui.status)) return repaint();
    ui.status.textContent = "Writing...";
    ui.preview.replaceChildren(...markdown(st.text));
  }

  function refreshCheck() {
    const result = checkResult();
    if (connected(ui.checkBox)) ui.checkBox.replaceChildren(...result.nodes);
    if (connected(ui.save)) ui.save.disabled = !result.ok || st.saving || !st.db || (st.saved && st.saved.markdown === st.text);
  }

  /** What to say when this page can't ask Claude for a draft. */
  function noAsk() {
    return HB.when("Claude can't be reached from the page just now. Reload the page and try again.",
      "Claude can't draft the report from this view. It writes the report the next time it works on this project.");
  }

  function chatOnly(b, out) {
    const d = drafting(b);
    const words = d && d.state === "out_of_date"
      ? HB.when("Claude can't be reached from the page just now, so the report can't be brought up to date. Reload the page and try again.",
        "Claude brings the report up to date the next time it works on this project.")
      : noAsk();
    out.push(h("p", { class: "rd-ask" }, words));
  }

  function fill(box) {
    const b = bundle();
    const out = [];
    const busy = Boolean(st.ctl);
    if (st.saved) {
      out.push(HB.notice("good", HB.when("Saved. Claude is checking every number again and making it the report.",
        "Saved. Claude checks every number again and makes it the report the next time it works on this project.")));
    } else if (st.waiting) {
      out.push(HB.notice("neutral", HB.when("A draft you saved here is being checked and made the report.",
        "A draft you saved here is waiting for Claude. Claude makes it the report the next time it works on this project.")));
    }
    if (st.note) out.push(HB.notice(st.note.tone, st.note.text));

    if (st.can !== "yes" && !st.text) {
      chatOnly(b, out);
      box.replaceChildren(...out);
      return;
    }
    // One button, no explainer box: its title says what happens (Claude drafts, you read and save).
    const sparkle = HB.svg("svg", { class: "rd-ai", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" },
      HB.svg("path", { d: "M8 1.5l1.4 3.6 3.6 1.4-3.6 1.4L8 11.5 6.6 7.9 3 6.5l3.6-1.4zM12.5 10.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z", fill: "currentColor" }));
    const go = h("button", { type: "button", class: ["btn", !st.text && "btn--primary", "rd-go"], id: "hb-draft-go", disabled: busy || st.can !== "yes",
      title: "Claude drafts the report from the figures on this page; you read it, change it if you want, and save it." },
      sparkle, st.text || st.phase === "done" ? "Try again" : "Generate report");
    go.addEventListener("click", () => generate(Boolean(st.text) || st.phase === "done"));
    const stop = h("button", { type: "button", class: "btn", id: "hb-draft-stop", hidden: !busy }, "Stop");
    stop.addEventListener("click", () => { if (st.ctl) st.ctl.abort(); });
    out.push(h("div", { class: "cluster" }, go, stop));
    ui.status = h("p", { class: "rd-status small muted", "aria-live": "polite" },
      busy ? (st.phase === "writing" ? "Writing..." : "Thinking...") : "");
    out.push(ui.status);

    if (busy || st.text) {
      if (st.editing && !busy) {
        const area = h("textarea", { class: "input rd-text", id: "hb-draft-text", rows: 18, "aria-label": "The draft, in Markdown" });
        area.value = st.text;
        area.addEventListener("input", () => {
          st.text = area.value;
          refreshCheck();
        });
        out.push(area);
        ui.preview = null;
      } else {
        ui.preview = h("article", { class: "prose res-report rd-preview", id: "hb-draft-preview", "aria-label": "The draft" }, markdown(st.text));
        out.push(ui.preview);
      }
    }
    ui.checkBox = null;
    ui.save = null;
    if (!busy && st.text) {
      const result = checkResult();
      ui.checkBox = h("div", { class: "stack stack--tight", id: "hb-draft-check" }, result.nodes);
      out.push(ui.checkBox);
      ui.save = h("button", { type: "button", class: "btn btn--primary", id: "hb-draft-save" },
        st.saving ? "Saving..." : "Save as the report");
      ui.save.disabled = !result.ok || st.saving || !st.db || Boolean(st.saved && st.saved.markdown === st.text);
      ui.save.addEventListener("click", save);
      const edit = h("button", { type: "button", class: "btn", id: "hb-draft-edit" }, st.editing ? "Show the preview" : "Edit the draft");
      edit.addEventListener("click", () => { st.editing = !st.editing; repaint(); });
      const copy = h("button", { type: "button", class: "btn", id: "hb-draft-copy" }, "Copy the draft");
      copy.addEventListener("click", () => HB.copyText(st.text, copy));
      out.push(h("div", { class: "cluster" }, ui.save, edit, copy));
      if (!st.db) out.push(h("p", { class: "small muted" }, HB.cantSave() + " Copy the draft so you keep it."));
      if (st.saveError) out.push(HB.notice("critical", st.saveError));
    }
    box.replaceChildren(...out);
  }

  function panel() {
    const b = bundle();
    const d = drafting(b);
    if (!d) return null;
    const facts = d.facts && typeof d.facts === "object" ? d.facts : null;
    const scored = facts && facts.rows_scored > 0;
    const wanted = (d.state === "none" || d.state === "out_of_date") && scored;
    if (!wanted && !st.text && !st.ctl && !st.waiting && !st.saved) return null;
    ui.box = h("div", { class: "rd", id: "hb-draft" });
    fill(ui.box);
    return ui.box;
  }

  function staleNotice() {
    const d = drafting();
    if (!d || d.state !== "out_of_date") return null;
    return HB.notice("warning", "The figures have changed since this report was written, so parts of it may be out of date.");
  }

  HB.addStyles("reportdraft", `
    .rd-go { display: inline-flex; align-items: center; gap: 0.4rem; }
    .rd-ai { width: 1rem; height: 1rem; flex: none; }
    .rd { display: grid; gap: var(--s3); max-width: var(--measure); margin-top: var(--s4); }
    .rd-status:empty { display: none; }
    .rd-ask q { font-weight: 600; }
    .rd-preview { padding: var(--s4); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
    .rd-text { width: 100%; min-height: 18rem; font-family: var(--mono); font-size: var(--fs-14); line-height: 1.5; }
    .rd-fails { margin: var(--s2) 0 0; padding-left: 1.25rem; overflow-wrap: anywhere; }
  `);

  HB.reportDraft = { staleNotice, panel, check, prompt, generate, canGenerate: () => st.can === "yes" && !st.ctl };

  start();
})((window.HB = window.HB || {}));
