/* =============================================================================
   Handbook viewer: views/chat-tools.js, the corner chat's open-ended tools. The
   chat (views/chat.js) adds these to its own list, so that every request ends in an
   answer or in a change the owner confirms, never in "I can't". Kept in their own
   file so chat.js stays the page's own tools and this file the wider ones.

   All of them work through `hb serve` (HB.local, /api/chat/... in serve.py), so they
   are offered only in the tool on the owner's own computer:
     read_documents       a row's documents: the list, a search, or pages to read
     make_file            a CSV or Excel file of any slice of the answers (or a table
                          Claude puts together), saved in the project's exports folder
                          and shown in the chat with a Download button
     propose_instruction  a standing instruction (or new rule) every scorer follows,
                          for all or some questions and rows
     propose_rescore      score again any rows x questions, with a token range
     propose_sections     section or article numbers for quotes already saved, found
                          from the headings with plain code (no tokens)
     propose_job          anything else: one background Claude session on the project,
                          with a copy taken first and an Undo button when it is done
     background_jobs      the jobs so far, and Undo for one of them (after a reload too)
   and, on every tab and page (the Feedback tab keeps FALLBACK): set_grid_view (group, filter: at
   once, free), propose_edit (one question or row field, through HB.edits.edit), propose_document
   (a link for some rows, saved as "supplied") and, in the tool on the owner's computer,
   propose_setup_change (the basics, the page's colour, removing labels).
   A change is never made by Claude: each propose_ tool puts one line with Confirm and
   Cancel in the chat (chat.js's propose), and the call is made when the owner clicks.

   HB.chatMore = {
     tools(api)          the tool list, given chat.js's helpers (api: propose, log,
                         tell, rowOf, qidOf, unitName, fit, cut, arr, bundle, progress, canSave)
     instructions()      the lines of the chat's standing instructions about them
     node(entry, api)    draws a "more" entry: a file to download, a job and its Undo
     watch(api)          picks up jobs still running when the chat reopens
   }
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h } = HB;
  const arr = (v) => (Array.isArray(v) ? v : []);
  const local = () => Boolean(HB.local && typeof HB.local.post === "function");
  const ONLY_HERE = "This works only in the benchmark tool on the owner's own computer, not in a shared copy of the page. Tell the owner that in one sentence.";
  // A background job's cost, until jobs have been measured: the set-up draft (a similar session) ran
  // 0.8 to 2.3 million tokens; a job's brief is narrower, and cmd_job caps it at 1.5 million.
  const JOB_TOKENS = { small: [100000, 400000], medium: [400000, 1000000], large: [1000000, 1500000] };
  const SECTION_JOB_PER_QUOTE = [3000, 6000];   // reading the page of one quote that code could not place
  const jobs = new Map();                        // job id -> its chat entry
  let timer = null;

  const fmtTokens = (n) => (HB.cost && typeof HB.cost.tokens === "function" ? HB.cost.tokens(n)
    : n >= 1e6 ? (Math.round(n / 1e5) / 10) + " million" : Math.round(n / 1000) + ",000");
  const rangeWords = (lo, hi) => "about " + fmtTokens(lo) + " to " + fmtTokens(hi) + " tokens of your Claude plan";

  async function getJson(path) {
    const res = await HB.local.get(path);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && (data.message || (data.error && data.error.message))) || "The page's server could not answer.");
    return data;
  }

  /* ---- read_documents ------------------------------------------------------------------- */

  async function readDocuments(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    if (input.search) {
      const rows = input.row ? [api.rowOf(input.row)] : arr(input.rows).map(api.rowOf);
      api.progress("Searching the documents for “" + api.cut(input.search, 40) + "”");
      const q = "chat/search?q=" + encodeURIComponent(input.search) + (rows.length ? "&rows=" + encodeURIComponent(rows.join(",")) : "");
      return api.fit(await getJson(q));
    }
    if (input.doc) {
      api.progress("Reading " + input.doc + ", page " + (input.page || 1));
      return api.fit(await getJson("chat/docs?doc=" + encodeURIComponent(input.doc) + "&page=" + (Number(input.page) || 1) + "&count=" + (Number(input.count) || 1)));
    }
    const row = api.rowOf(input.row);
    api.progress("Listing the documents for " + api.unitName(row));
    return api.fit(await getJson("chat/docs?row=" + encodeURIComponent(row)));
  }

  /* ---- make_file -------------------------------------------------------------------------- */

  const STATES = { answered: "Answered", no_evidence: "No evidence found", not_applicable: "Not applicable", not_run: "Not scored yet" };
  const COLUMNS = ["row", "name", "labels", "question", "short", "state", "value", "points", "section", "page", "document", "quote", "quote_en", "note", "flags", "rules", "url"];
  const HEADS = { row: "Row id", name: "Name", question: "Question", short: "Short name", state: "State", value: "Value", points: "Points",
    section: "Section", page: "Page", document: "Document", quote: "Quote", quote_en: "Quote in English", note: "Claude's note", flags: "Flags", rules: "Rules", url: "Link" };

  /** The answers as a table: one line per answer (quotes joined) or, with per_quote, one per quote. */
  async function answersTable(input, api) {
    const b = api.bundle();
    const unitWord = (b.project || {}).unit_word || "row";
    const rows = input.rows && arr(input.rows).length ? arr(input.rows).map(api.rowOf) : arr(b.units).map((u) => u.id);
    const qids = input.questions && arr(input.questions).length ? arr(input.questions).map(api.qidOf) : arr(b.questions).map((q) => q.id);
    const cols = (arr(input.columns).length ? arr(input.columns) : ["name", "question", "short", "value", "points", "section", "page", "document", "quote", "note"])
      .map(String).filter((c) => COLUMNS.includes(c));
    if (!cols.length) throw new Error("columns are any of: " + COLUMNS.join(", ") + ".");
    const labels = arr((b.project || {}).labels).map((l) => (typeof l === "string" ? l : l && l.id)).filter(Boolean);
    const heads = cols.flatMap((c) => (c === "labels" ? labels : [c === "row" ? unitWord + " id" : c === "name" ? unitWord.charAt(0).toUpperCase() + unitWord.slice(1) : HEADS[c]]));
    const docs = b.documents || {};
    const only = input.only ? String(input.only) : null;
    const out = [];
    for (const row of rows) {
      const u = HB.data.unit(row) || {};
      const ev = cols.includes("note") ? await HB.data.evidence(row) : null;
      for (const qid of qids) {
        const c = HB.data.cell(row, qid);
        if (!c) continue;
        if (only === "flagged" && !arr(c.flags).length) continue;
        if (only === "no_evidence" && c.state !== "no_evidence") continue;
        if (only === "judgement" && c.judgement !== true) continue;
        if (only === "out_of_date" && c.out_of_date !== true) continue;
        const q = HB.data.questionFor(row, qid) || {};
        const items = arr(c.evidence).length ? arr(c.evidence) : [{}];
        const groups = input.per_quote ? items.map((e) => [e]) : [items];
        for (const g of groups) {
          const join = (f) => g.map(f).filter((x) => x != null && x !== "").join(input.per_quote ? "" : "\n");
          const val = {
            row, name: u.name || row, question: qid, short: q.short || "", state: STATES[c.state] || c.state, value: c.value ?? "", points: c.points ?? "",
            section: join((e) => e.section), page: join((e) => e.page), document: join((e) => (docs[e.doc] || {}).title || e.doc),
            quote: join((e) => e.quote), quote_en: join((e) => (e.quote_en && e.quote_en !== e.quote ? e.quote_en : "")),
            url: join((e) => (docs[e.doc] || {}).url), note: ev && ev.notes ? ev.notes[qid] || "" : "",
            flags: arr(c.flags).join(", "), rules: arr(c.rules).join(", "),
          };
          out.push(cols.flatMap((col) => (col === "labels" ? labels.map((l) => (u.labels || {})[l] ?? "") : [val[col]])));
        }
      }
    }
    return { columns: heads, rows: out };
  }

  async function makeFile(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    const format = input.format === "csv" ? "csv" : "xlsx";
    let table;
    if (input.table && arr(input.table.columns).length) {
      table = { columns: arr(input.table.columns).map(String), rows: arr(input.table.rows).map((r) => arr(r)) };
    } else {
      api.progress("Gathering the answers for the file");
      table = await answersTable(input, api);
    }
    if (!table.rows.length) throw new Error("Nothing matches, so there is nothing to put in a file. Check the rows, questions and filter.");
    const name = String(input.name || "export").trim() || "export";
    const got = await HB.local.post("chat/file", { name, format, columns: table.columns, rows: table.rows });
    api.log({ kind: "more", type: "file", file: got.file, rows: table.rows.length });
    return { made: got.file, lines: table.rows.length, columns: table.columns,
      note: "The owner now sees the file under your reply with a Download button, and it is also saved in the project's exports folder. Say so in one line; do not paste the contents." };
  }

  async function download(file, btn) {
    btn.disabled = true;
    try {
      const res = await HB.local.get("chat/file/" + encodeURIComponent(file));
      if (!res.ok) throw new Error("gone");
      const url = URL.createObjectURL(await res.blob());
      const a = h("a", { href: url, download: file, hidden: true });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      btn.textContent = "Downloaded";
    } catch (err) {
      btn.textContent = "Could not download";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = "Download"; }, 2500);
    }
  }

  /* ---- propose_instruction, propose_rescore, propose_sections ---------------------------- */

  function scopeWords(api, rows, qids) {
    const b = api.bundle();
    const many = (b.project || {}).unit_word_plural || "rows";
    const r = rows ? (rows.length <= 3 ? rows.map(api.unitName).join(", ") : rows.length + " " + many) : "every " + ((b.project || {}).unit_word || "row");
    const q = qids ? (qids.length <= 4 ? "question" + (qids.length === 1 ? " " : "s ") + qids.join(", ") : qids.length + " questions") : "every question";
    return q + ", " + r;
  }

  function proposeInstruction(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    api.canSave();
    const text = String(input.text || "").trim();
    if (!text) throw new Error("Give the instruction in words, as the owner would say it to a scorer.");
    const rows = arr(input.rows).length ? arr(input.rows).map(api.rowOf) : null;
    const qids = arr(input.questions).length ? arr(input.questions).map(api.qidOf) : null;
    return Object.assign(api.propose("Add a standing instruction for " + scopeWords(api, rows, qids) + ": “" + api.cut(text, 200) + "”. Scorers follow it from now on; answers already scored change only when scored again.",
      async () => {
        const got = await HB.local.post("chat/instruction", { text, rows, questions: qids });
        return { __result: true, applied: true, note: "Added as " + got.id + " on the Rules tab.",
          told: "It is saved as " + got.id + " and every scorer follows it from now on. Offer to score the answers it touches again (propose_rescore, with its cost), or a cheaper route if there is one.)" };
      }), { next: "Once confirmed, offer the re-score that applies it to existing answers, with its cost, or a cheaper route if one exists (for section numbers: propose_sections)." });
  }

  function proposeRescore(input, api) {
    api.canSave();
    const b = api.bundle();
    const rows = arr(input.rows).length ? arr(input.rows).map(api.rowOf) : arr(b.units).map((u) => u.id);
    const qids = arr(input.questions).length ? arr(input.questions).map(api.qidOf) : arr(b.questions).map((q) => q.id);
    const scored = rows.filter((r) => qids.some((q) => { const c = HB.data.cell(r, q); return c && c.state !== "not_run"; }));
    if (!scored.length) throw new Error("None of those answers has been scored yet, so there is nothing to score again: start_scoring scores them.");
    const est = HB.cost && typeof HB.cost.rows === "function" ? HB.cost.rows(scored, input.questions ? qids : null) : null;
    const why = String(input.why || "").trim();
    const line = "Score again " + scopeWords(api, input.rows ? scored : null, input.questions ? qids : null) + (why ? " (" + api.cut(why, 100) + ")" : "") +
      ". " + (est ? HB.cost.line(est) : "It uses some of your Claude plan.");
    return api.propose(line, async () => {
      await HB.edits.save("control", { action: "rerun", rule: null, rows: scored, questions: qids, why: why || null });
      return { __result: true, started: true, note: "Scoring again behind the page; the run bar on the Grid shows progress, and a note says when it is done.",
        told: "The re-score is saved and runs behind the page; it is not done yet.)" };
    });
  }

  async function proposeSections(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    api.canSave();
    const rows = arr(input.rows).length ? arr(input.rows).map(api.rowOf) : null;
    api.progress("Finding the section above each quote");
    const dry = await HB.local.post("chat/locate", { rows, write: false });
    if (!dry.placed) {
      return { placed: 0, could_not_place: dry.unplaced.length, left_by_row: dry.left_by_row || null, already: dry.already,
        note: dry.unplaced.length ? "Plain code found no headings to use. Offer a job that reads those pages (propose_job), costing " +
          rangeWords(dry.unplaced.length * SECTION_JOB_PER_QUOTE[0], dry.unplaced.length * SECTION_JOB_PER_QUOTE[1]) + "." : "Every quote already has its section." };
    }
    const left = dry.unplaced.length;
    const p = api.propose("Add section or article numbers to " + dry.placed + " quote" + (dry.placed === 1 ? "" : "s") + ", found from the headings above each quote (no Claude, no tokens)" +
      (left ? "; " + left + " could not be placed this way" : ""), async () => {
      const got = await HB.local.post("chat/locate", { rows, write: true });
      return { __result: true, applied: true, note: "Added to " + got.placed + " quotes. The cell panel shows them next to the page.",
        told: "Added to " + got.placed + " quotes." + (left ? " " + left + " are left without one: offer a job that reads their pages (propose_job), costing " +
          rangeWords(left * SECTION_JOB_PER_QUOTE[0], left * SECTION_JOB_PER_QUOTE[1]) + ", or leave them on the page number.)" : ")") };
    });
    return Object.assign(p, { could_not_place: left, left_by_row: dry.left_by_row || null, examples_left: dry.unplaced.slice(0, 5),
      cost_to_place_the_rest_with_claude: left ? rangeWords(left * SECTION_JOB_PER_QUOTE[0], left * SECTION_JOB_PER_QUOTE[1]) : null });
  }

  /* ---- propose_job, its card and Undo ---------------------------------------------------- */

  function proposeJob(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    api.canSave();
    const brief = String(input.brief || "").trim();
    const title = String(input.title || "").trim();
    if (!brief || !title) throw new Error("A job needs a short title (a few words, as the owner would put it) and a brief: the task in full, with the rows, questions and files it touches.");
    const size = JOB_TOKENS[input.size] ? input.size : "medium";
    const [lo, hi] = arr(input.tokens).length === 2 ? input.tokens.map(Number) : JOB_TOKENS[size];
    return api.propose("Start a background job: " + api.cut(title, 120) + ". Claude works on the project behind the page, " + rangeWords(lo, hi) +
      ". A copy is taken first, so you can undo it in one click.", async () => {
      const got = await HB.local.post("chat/job", { title, brief, estimate: { low: lo, high: hi } });
      const entry = { kind: "more", type: "job", id: got.id, title, status: "queued" };
      jobs.set(got.id, entry);
      api.log(entry);
      watch(api);
      return { __result: true, applied: true, note: "Started. Its progress shows below and in the run bar.",
        told: "The job has started in the background; you will be told when it finishes.)" };
    });
  }

  function watch(api) {
    if (timer || !local()) return;
    timer = setInterval(async () => {
      const open = Array.from(jobs.values()).filter((e) => e.status === "queued" || e.status === "running");
      if (!open.length) { clearInterval(timer); timer = null; return; }
      try {
        const got = await getJson("chat/jobs");
        for (const j of arr(got.jobs)) {
          const e = jobs.get(j.id);
          if (!e) continue;
          const was = e.status;
          Object.assign(e, { status: j.status, result: j.result, tokens: j.tokens, undone: j.undone });
          if (was !== j.status && (j.status === "done" || j.status === "failed")) {
            api.tell("(The background job “" + e.title + "” " + (j.status === "done" ? "has finished" : "stopped before it finished") +
              ", using " + fmtTokens(j.tokens || 0) + " tokens. It says: " + (j.result || "nothing") + " The owner can undo it with the button under it.)");
          }
        }
        api.repaint();
      } catch (err) { /* the next tick tries again */ }
    }, 4000);
  }

  function undoLine(e, api) {
    // Since 1.0.1 an Undo takes back only what that change touched (serve.undo_chat_job); older ones still put
    // the whole project back (20 Oct 2026: the card said "whole project" for both).
    const own = e.own_undo !== false;
    return api.propose("Undo “" + api.cut(e.title, 100) + "”: " + (own
      ? "what it changed goes back as it was. Anything done since, by you or a colleague, stays."
      : "the whole project goes back as it was before it, and anything changed since is undone too."),
      async () => {
        await HB.local.post("chat/undo", { job: e.id });
        e.undone = true;
        return { __result: true, applied: true, note: own ? "Undone. What it changed is back as it was." : "Undone. The project is back as it was.",
          told: "It was undone" + (own ? ": only what it changed; later work stays.)" : ": the project is back as it was before it.)") };
      });
  }

  /** background_jobs: the jobs so far, newest first; with undo (a job id), Undo put to the owner. */
  async function backgroundJobs(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    const list = arr((await getJson("chat/jobs")).jobs).slice().reverse();
    if (!input.undo) {
      return api.fit({ jobs: list.slice(0, 15).map((j) => ({ id: j.id, title: j.title, status: j.status, asked: j.asked, tokens: j.tokens || 0,
        result: api.cut(j.result, 400), undone: Boolean(j.undone) })) });
    }
    api.canSave();
    const j = list.find((x) => x.id === String(input.undo));
    if (!j) throw new Error("There is no job " + input.undo + ". Call background_jobs without undo to list them.");
    if (j.undone) throw new Error("That job has already been undone.");
    if (j.status === "queued" || j.status === "running") throw new Error("That job is still running. It can be undone once it has finished.");
    const entry = jobs.get(j.id) || { id: j.id, title: j.title };
    entry.undoAsked = true;
    return undoLine(entry, api);
  }

  function node(entry, api) {
    if (entry.type === "file") {
      const btn = h("button", { type: "button", class: "btn btn--primary btn--small" }, "Download");
      btn.addEventListener("click", () => download(entry.file, btn));
      return h("div", { class: "hbc-change hbc-file", "data-state": "done" },
        h("p", { class: "hbc-change__line" }, h("strong", null, entry.file), h("span", { class: "muted" }, " · " + entry.rows + (entry.rows === 1 ? " line" : " lines"))),
        h("div", { class: "cluster" }, btn, h("span", { class: "small muted" }, "Also saved in the project's exports folder.")));
    }
    if (entry.type === "job") {
      const words = entry.undone ? "Undone." : entry.status === "done" ? "Done, " + fmtTokens(entry.tokens || 0) + " tokens."
        : entry.status === "failed" ? "Stopped before it finished, " + fmtTokens(entry.tokens || 0) + " tokens."
          : entry.status === "running" ? "Working on it. Progress shows in the run bar." : "Waiting to start.";
      const box = h("div", { class: "hbc-change hbc-job", "data-state": entry.undone ? "cancelled" : "done" },
        h("p", { class: "hbc-change__line" }, h("strong", null, "Job: " + entry.title)),
        h("p", { class: "small" }, words),
        entry.result && !entry.undone ? h("p", { class: "small muted" }, entry.result) : null);
      if ((entry.status === "done" || entry.status === "failed") && !entry.undone && !entry.undoAsked) {
        const undo = h("button", { type: "button", class: "btn btn--small" }, "Undo");
        undo.addEventListener("click", () => { entry.undoAsked = true; undoLine(entry, api); });
        box.append(h("div", { class: "cluster" }, undo));
      }
      return box;
    }
    return null;
  }

  /* ---- set_grid_view, propose_edit, propose_setup_change, propose_document ------------------
     The everyday changes, each the cheapest way: the Grid's view changes at once and costs nothing;
     an edit or a set-up change is one line to confirm, no Claude; only a re-score or a job uses
     tokens. These four work on any tab and at any stage. */

  const COLOURS = { blue: "#1f4e9c", "dark blue": "#173a73", navy: "#1b2f5b", "light blue": "#3a7bd5", green: "#1d5c4f", red: "#a3242a",
    purple: "#5b3a8c", orange: "#b85a12", teal: "#1f6f78", grey: "#4a4f57", gray: "#4a4f57", black: "#222222", pink: "#a8336b" };

  function labelsOf(api) { return arr((api.bundle().project || {}).labels).map(String); }

  function setGridView(input, api) {
    const labels = labelsOf(api);
    const out = {};
    if (input.group_by != null) {
      const g = String(input.group_by);
      const want = g === "none" || g === "" ? "" : g;
      if (want && !labels.includes(want)) throw new Error("The Grid can group by " + (labels.join(", ") || "no label") + ", or none.");
      if (typeof HB.setGroupBy === "function") HB.setGroupBy(want);
      else HB.state.groupBy = want;
      out.group_by = want || "none";
    }
    if (input.filters !== undefined) {
      HB.state.filters = {};
      for (const [k, v] of Object.entries(input.filters || {})) {
        if (!labels.includes(k)) throw new Error(k + " is not a label. The labels are: " + labels.join(", ") + ".");
        if (v != null && v !== "") HB.state.filters[k] = String(v);
      }
      out.filters = HB.state.filters;
    }
    const shown = api.moveOrOffer ? api.moveOrOffer("grid", "Open the Grid", () => (typeof HB.render === "function" ? HB.render() : HB.go("grid")), "The Grid is laid out anew.") : "";
    return { done: out, shown, note: "Done at once: it changes only how the Grid is laid out for the owner, never an answer or a score. Say so in a line." };
  }

  const EDIT_SAYS = {
    wording: "its answers become out of date until scored again (offer propose_rescore with its cost)",
    rule: "its answers become out of date until scored again (offer propose_rescore with its cost)",
    short: "only its short name changes",
    levels_points: "the points are worked out again at once for every answer, with no Claude",
    applies_to: "rows it stops applying to set their answer aside; rows it starts applying to wait to be scored",
    name: "only the name changes",
    labels: "if a question applies by this label, which questions apply can change",
  };

  function proposeEdit(input, api) {
    api.canSave();
    if (!(HB.edits && typeof HB.edits.edit === "function")) throw new Error("This view saves no edits. Put a background job instead (propose_job).");
    const target = input.target === "row" ? "unit" : String(input.target || "");
    const field = String(input.field || "");
    if (!["question", "unit"].includes(target)) throw new Error("target is question or row.");
    const id = target === "unit" ? api.rowOf(input.id) : api.qidOf(input.id);
    if (!EDIT_SAYS[field]) throw new Error("A " + (target === "unit" ? "row" : "question") + " edit changes one of: " + (target === "unit" ? "name, labels" : "wording, short, rule, levels_points, applies_to") + ". Anything else (adding or deleting a question or row, level wording, the score sheet) is a background job (propose_job).");
    const value = input.value;
    const who = target === "unit" ? api.unitName(id) : "question " + id;
    const shown = typeof value === "string" ? "“" + api.cut(value, 160) + "”"
      : field === "levels_points" && value && typeof value === "object" && !Array.isArray(value)
        ? Object.entries(value).map(([lvl, pts]) => "level " + lvl + " gives " + pts + (Number(pts) === 1 ? " point" : " points")).join(", ")
        : api.cut(JSON.stringify(value), 160);
    return api.propose("Change " + who + "'s " + field.replace("_", " ") + " to " + shown + ": " + EDIT_SAYS[field] + ".",
      () => HB.edits.edit(target, id, field, value));
  }

  function proposeSetupChange(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    api.canSave();
    const changes = Object.assign({}, input.changes || {});
    const remove = arr(input.remove_labels).map(String);
    if (typeof changes.accent === "string" && COLOURS[changes.accent.trim().toLowerCase()]) changes.accent = COLOURS[changes.accent.trim().toLowerCase()];
    const keys = Object.keys(changes);
    if (!keys.length && !remove.length) throw new Error("Say what to change: changes {name, edition, cutoff, unit_word, unit_word_plural, group_labels, group_by, results_sites, accent} and/or remove_labels.");
    const says = [];
    if (remove.length) says.push("remove the label" + (remove.length === 1 ? " " : "s ") + remove.join(", ") + " from the project and every row (no score changes)");
    for (const k of keys) {
      const v = changes[k];
      if (k === "cutoff") says.push("cut-off to " + v + " (answers already scored keep the old one until scored again)");
      else if (k === "accent") says.push(v == null ? "the page's own colours back" : "the page's main colour to " + v);
      else if (k === "group_by") says.push("group the Grid by " + (v || "nothing") + " when it opens");
      else says.push(k.replace(/_/g, " ") + " to " + (Array.isArray(v) ? v.join(", ") || "none" : v == null ? "none" : v));
    }
    return api.propose("Change the set-up: " + says.join("; ") + ".", async () => {
      if (remove.length) {
        const got = await HB.local.post("chat/remove-labels", { labels: remove });
        if (!got.ok) throw new Error(arr(got.problems).join(" "));
      }
      if (keys.length) {
        const got = await HB.local.post("setup", { part: "basics", content: changes });
        if (got && got.ok === false) throw new Error(arr(got.problems).join(" "));
      }
      if (typeof HB.local.refresh === "function") { try { await HB.local.refresh(); } catch (e) { /* the next poll */ } }
      return { __result: true, applied: true, note: "Changed. The page shows it now.", told: "The set-up change is saved and the page rebuilt.)" };
    });
  }

  function proposeDocument(input, api) {
    api.canSave();
    const rows = arr(input.rows).map(api.rowOf);
    const link = String(input.link || "").trim();
    if (!rows.length || !/^https?:\/\//i.test(link)) throw new Error("A document needs the rows it is for and its web address (https://...). For a file on the owner's computer: they drop it on this chat, or use Add documents or instructions above the Grid, which files it for its rows after they confirm.");
    return api.propose("Add " + link + " as a document for " + rows.map(api.unitName).join(", ") + ". It is saved, and nothing is scored again until you ask (then the line shows the cost).",
      () => HB.edits.save("supplied", { rows, link, what: input.title ? String(input.title) : null }));
  }

  /* ---- propose_change, run_command, get_setup: the whole tool, at any stage ------------------
     Everything the set-up Claude could change, the corner chat changes too, after scoring and
     mid-round: hb change (changes.py) for rows, questions, rules and set-up parts, with what it does
     to the answers said before Confirm; and any of the tool's own commands (hb ...), run behind the
     page. Each writes only after Confirm, with a copy first, so background_jobs can undo it. */

  const n = (x) => arr(x).length;

  async function proposeChange(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    api.canSave();
    const op = input.change && typeof input.change === "object" ? input.change : null;
    if (!op || !op.op) throw new Error("change is {op, ...}: add_rows, remove_rows, rename_row, merge_rows, add_question, remove_questions, change_question, remove_rules or set_part (call run_command [\"change\", \"--list\"] for the fields).");
    api.progress("Working out what this does to the answers");
    const dry = await HB.local.post("chat/change", { op, apply: false });
    if (!dry.ok) throw new Error(arr(dry.problems).join(" ") || "That change does not fit the project.");
    const imp = dry.impact || {};
    const bits = [];
    if (n(imp.removed)) bits.push(n(imp.removed) + " answer" + (n(imp.removed) === 1 ? "" : "s") + " removed (Undo brings them back)");
    if (n(imp.out_of_date)) bits.push(n(imp.out_of_date) + " out of date until scored again");
    if (n(imp.unscored)) bits.push(n(imp.unscored) + " new to score");
    if (imp.kept != null) bits.push(imp.kept + " kept as they are");
    const redo = imp.rescore || { rows: [], questions: [] };
    let est = n(redo.rows) && HB.cost && typeof HB.cost.rows === "function" ? HB.cost.rows(redo.rows, redo.questions) : null;
    const fresh = arr(redo.questions).filter((q) => !arr(api.bundle().questions).some((x) => x.id === q));
    if (!est && n(redo.rows) && fresh.length && HB.cost && typeof HB.cost.rows === "function") {
      // A new question has no answers to size it by: one existing question on the same rows, times the new ones
      // (20 Oct 2026: the add-question card said only "uses some of your plan").
      const like = arr(api.bundle().questions).map((q) => q.id).find(Boolean);
      const one = like ? HB.cost.rows(redo.rows, [like]) : null;
      if (one) est = Object.assign({}, one, { point: one.point * fresh.length, low: one.low * fresh.length, high: one.high * fresh.length });
    }
    const title = String(input.title || dry.said || op.op);
    const line = title.replace(/\.$/, "") + ": " + (bits.join(", ") || "no answer changes") + ". One click undoes it." +
      (n(redo.rows) ? " Scoring the affected answers again: " + (est ? HB.cost.line(est).replace(/\.$/, "") : "uses some of your plan") + (n(imp.unscored) && op.op === "add_rows" ? " (finding their documents comes on top)" : "") + "." : "");
    return Object.assign(api.propose(line, async () => {
      const got = await HB.local.post("chat/change", { op, apply: true, title });
      if (!got.ok) throw new Error(arr(got.problems).join(" ") || "It was not changed.");
      return { __result: true, applied: true, note: "Done. Undo is under background jobs.",
        told: "The change is made" + (n(redo.rows) ? "; offer to score the affected answers again (propose_rescore rows " + JSON.stringify(redo.rows.slice(0, 20)) + ", questions " + JSON.stringify(redo.questions.slice(0, 30)) + "), or start scoring for new rows." : ".") + ")" };
    }), { impact: { removed: n(imp.removed), out_of_date: n(imp.out_of_date), unscored: n(imp.unscored), kept: imp.kept, rescore: redo } });
  }

  const QUICK_CMDS = ["help", "status", "validate", "todo", "cost", "estimate", "doctor", "material-text"];

  async function runCommand(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    const args = arr(input.args).map(String).filter(Boolean);
    if (!args.length) throw new Error("args is the command and its arguments, such as [\"help\"] or [\"status\"].");
    const readOnly = QUICK_CMDS.includes(args[0]) || args.includes("--help") || (args[0] === "change" && !args.includes("--apply")) || (args[0] === "locate" && !args.includes("--write"));
    if (readOnly) {
      api.progress("Asking the tool");
      if (args[0] === "help" || args.includes("--help")) return api.fit(await getJson("chat/hb-help" + (args[0] !== "help" ? "?command=" + encodeURIComponent(args[0]) : "")));
      return api.fit(await HB.local.post("chat/hb", { args }));
    }
    if (args[0] === "score" || (args[0] === "run" && args[1] === "start")) {
      // 20 Oct 2026: run start from here left an open run that scored nothing, and the card said Done.
      throw new Error("That command on its own scores nothing here. To score, use start_scoring (rows, questions); to score answers again, propose_rescore.");
    }
    api.canSave();
    const why = String(input.why || "").trim();
    if (!why) throw new Error("Give why: what this does for the owner, in plain words.");
    return api.propose(api.cut(why, 200).replace(/\.$/, "") + ". The tool does it behind the page; a copy is taken first, so one click undoes it.", async () => {
      const got = await HB.local.post("chat/hb", { args });
      return { __result: true, applied: true, note: got.queued ? "Started. Progress shows in the run bar." : got.code === 0 ? "Done." : "It stopped with a problem.",
        told: "The command " + (got.queued ? "started behind the page" : "finished with code " + got.code) + ". Its output: " + api.cut(got.out || "", 3000) + ")" };
    });
  }

  async function getSetupAnywhere(input, api) {
    if (!local()) throw new Error(ONLY_HERE);
    const got = await getJson("setup");
    if (input.formats) got.formats = (await getJson("setup/guide")).text;
    return api.fit(got);
  }

  /* ---- The tool list and the standing instructions --------------------------------------- */

  const S = (props, required) => ({ type: "object", properties: props, required: required || [] });
  const ROWS = { type: "array", items: { type: "string" }, description: "row ids (names work too); leave out for all" };
  const QIDS = { type: "array", items: { type: "string" }, description: "question ids; leave out for all" };

  /** The everyday changes, offered on every page (the Feedback tab too): FALLBACK names those the
      Feedback tab keeps, so no tab is left without a route. */
  const FALLBACK = ["set_grid_view", "propose_edit", "propose_setup_change", "propose_document", "make_file", "propose_job",
    "propose_change", "run_command", "get_setup"];

  function everyday(api) {
    const w = (fn) => (input, ctx) => fn(input || {}, api, ctx);
    return [
      { name: "set_grid_view", description: "Changes how the Grid is laid out, at once and for free: group_by (a label, or none for one plain list) and filters ({label: value}, or {} to clear). Never changes an answer. Use it for \"group by\", \"remove the groupings\", \"only show X\".",
        inputSchema: S({ group_by: { type: "string" }, filters: { type: ["object", "null"] } }), execute: w(setGridView) },
      { name: "propose_edit", description: "Puts one edit to the owner, no Claude needed: a question's wording, short (name), rule (coding instructions), levels_points ({level value: points}) or applies_to; or a row's name (rename a country, company...) or labels ({label: value}). The line says what follows (wording or rule: those answers go out of date until scored again). Saved only when they click Confirm.",
        inputSchema: S({ target: { type: "string", enum: ["question", "row"] }, id: { type: "string" }, field: { type: "string", enum: ["wording", "short", "rule", "levels_points", "applies_to", "name", "labels"] }, value: {} }, ["target", "id", "field", "value"]), execute: w(proposeEdit) },
      { name: "propose_document", description: "Puts adding a document to the owner: its web address and the rows it is for. It is fetched and kept behind the page; nothing is scored again until asked (then propose_rescore). Saved only when they click Confirm.",
        inputSchema: S({ rows: ROWS, link: { type: "string" }, title: { type: "string" } }, ["rows", "link"]), execute: w(proposeDocument) },
    ];
  }

  function tools(api) {
    if (!local()) return everyday(api);
    const w = (fn) => (input, ctx) => fn(input || {}, api, ctx);
    return everyday(api).concat([
      { name: "propose_change", description: "Changes the set-up at any stage, after scoring and mid-round too: change {op: add_rows (rows [{id?, name, labels}]), remove_rows (ids), rename_row (id, name), merge_rows (into, rows), add_question (question in the set-up format), remove_questions (ids), change_question (id, fields {wording, short, rule, section, levels, no_evidence_value, applies_to, evidence}), remove_rules (ids), set_part (part scoresheet | where_to_look | needs | rules | basics, content: the whole part in the set-up format; get_setup with formats first)}. It works out what happens to the answers first (removed, out of date, new to score, kept) and the cost of scoring again, and puts that to the owner. Saved only when they click Confirm; one click undoes it.",
        inputSchema: S({ change: { type: "object" }, title: { type: "string", description: "a few plain words, e.g. Remove Zambia" } }, ["change"]), execute: w(proposeChange) },
      { name: "run_command", description: "Runs any of the tool's own commands on this project (args, e.g. [\"help\"], [\"status\"], [\"todo\"], [\"compare\"], [\"chart\", \"--help\"]): help, status, validate, todo, cost and any --help answer at once; anything that changes the project is put to the owner with why (plain words), runs behind the page after Confirm, and can be undone. Use it for whatever no other tool covers before reaching for a background job.",
        inputSchema: S({ args: { type: "array", items: { type: "string" } }, why: { type: "string" } }, ["args"]), execute: w(runCommand) },
      { name: "get_setup", description: "The set-up as it stands (basics, questions, rows, score sheet, where to look, data needed, rules); with formats: true also the set-up file formats, needed before set_part.",
        inputSchema: S({ formats: { type: "boolean" } }), execute: w(getSetupAnywhere) },
      { name: "propose_setup_change", description: "Puts a set-up change to the owner, at any stage and on any tab, no Claude needed: changes {name, edition, cutoff (YYYY-MM-DD), unit_word, unit_word_plural, group_labels, group_by (the Grid's grouping when it opens; null for none), results_sites, accent (the page's main colour: a #rrggbb or a colour name such as blue; null for the usual)} and/or remove_labels (labels to take off the project and every row; no score changes). Saved only when they click Confirm.",
        inputSchema: S({ changes: { type: "object" }, remove_labels: { type: "array", items: { type: "string" } } }), execute: w(proposeSetupChange) },
      { name: "read_documents", description: "A row's documents. With row only: the list (doc id, title, publisher, date, pages). With doc and page: the saved text of that page (count: up to 10 pages). With search: the pages holding those words, in one row's documents (row), several (rows) or all. Use it to look things up yourself before answering.",
        inputSchema: S({ row: { type: "string" }, rows: ROWS, doc: { type: "string" }, page: { type: "number" }, count: { type: "number" }, search: { type: "string" } }), execute: w(readDocuments) },
      { name: "make_file", description: "Makes a CSV or Excel file and shows it under your reply with a Download button (also saved in the project's exports folder). Either a slice of the answers: rows, questions, only (flagged, no_evidence, judgement or out_of_date), columns (any of " + COLUMNS.join(", ") +
          "; default name, question, short, value, points, section, page, document, quote, note) and per_quote (one line per quote); or table: {columns, rows} you put together from what the tools gave you. Needs no Confirm: it changes nothing.",
        inputSchema: S({ name: { type: "string", description: "the file name, without extension, in plain words" }, format: { type: "string", enum: ["xlsx", "csv"] }, rows: ROWS, questions: QIDS,
          only: { type: "string", enum: ["flagged", "no_evidence", "judgement", "out_of_date"] }, columns: { type: "array", items: { type: "string" } }, per_quote: { type: "boolean" },
          table: S({ columns: { type: "array", items: { type: "string" } }, rows: { type: "array", items: { type: "array" } } }) }, ["name"]), execute: w(makeFile) },
      { name: "propose_instruction", description: "Puts to the owner a standing instruction (or a new rule) that every scorer follows from now on, for all or some questions and rows: in the owner's words, as said to a scorer (for example \"For each quote, record the section or article number of the law it comes from\"). It shows on the Rules tab, where the owner can change it. Saved only when they click Confirm.",
        inputSchema: S({ text: { type: "string" }, questions: QIDS, rows: ROWS }, ["text"]), execute: w(proposeInstruction) },
      { name: "propose_rescore", description: "Puts to the owner scoring again any answers: rows x questions (both default to all), with why (a few words). The line shows the token range. The owner's own corrected answers stay. Saved only when they click Confirm; it then runs behind the page with progress in the run bar.",
        inputSchema: S({ rows: ROWS, questions: QIDS, why: { type: "string" } }), execute: w(proposeRescore) },
      { name: "propose_sections", description: "Finds the section or article number (such as s. 4(4) or art. 35(1)) for quotes already saved, from the headings above each quote, with plain code and no tokens, and puts adding them to the owner. Says how many could not be placed that way and what reading those with Claude would cost. Saved only when they click Confirm.",
        inputSchema: S({ rows: ROWS }), execute: w(proposeSections) },
      { name: "background_jobs", description: "The background jobs so far, newest first: title, status, tokens used, what each said, whether undone. With undo (a job id): puts Undo to the owner, which takes back what that job or change did and keeps everything done since (if it cannot without losing later work, it says so). When the owner says \"undo\" about something done here, use this, not a new change. Saved only when they click Confirm.",
        inputSchema: S({ undo: { type: "string" } }), execute: w(backgroundJobs) },
      { name: "propose_job", description: "For anything no other tool does: puts to the owner a background job, one Claude session that does the task on the project's files behind the page (it can read every document and change any answer, rule, question or file, with a copy taken first so one click undoes it). title: a few words for the owner. brief: the task in full for that session, with the rows, questions and what done looks like. size: small (one row or a few answers), medium (several rows), large (most of the project), or tokens [low, high] when you know better. Saved only when they click Confirm.",
        inputSchema: S({ title: { type: "string" }, brief: { type: "string" }, size: { type: "string", enum: ["small", "medium", "large"] }, tokens: { type: "array", items: { type: "number" } } }, ["title", "brief"]), execute: w(proposeJob) },
    ]);
  }

  /** Where each everyday ask goes, cheapest first. A route for every request is what keeps the chat
      from apologising: with no tool in reach, Claude says it can't whatever the instructions say
      (19 Oct 2026: "remove the groupings" was refused, then put as a 100,000-token job). */
  function instructions() {
    const charge = "You are in charge of this whole tool on the owner's behalf: everything the tool can do, and everything the set-up could change, you can do from here, on any tab and at any stage, scored or not. Nothing is locked because answers exist; the only cost is scoring again, and you say it before Confirm.";
    const routes = "Pick the cheapest route that does what the owner asked, in this order: the Grid's view (set_grid_view: free and at once), an edit or a set-up change (propose_edit" + (local() ? ", propose_setup_change" : "") + ": one Confirm, no Claude), " +
      "scoring again (propose_rescore: tokens), and only then a background job (" + (local() ? "propose_job" : "saved for Claude's next session") + ": tokens). Everyday asks: \"remove the groupings\" or \"group by X\": set_grid_view; \"remove the X label\": propose_setup_change remove_labels; " +
      "\"rename Kenya\" or \"Kenya's region is wrong\": propose_edit on the row; \"reword question 5\", its coding instructions, its points: propose_edit on the question, then offer propose_rescore; \"change the cut-off\": propose_setup_change cutoff, then offer propose_rescore; " +
      "\"make the page blue\": propose_setup_change accent; \"add this link for Zambia\": propose_document; a file, a list of documents or an Excel of coding instructions: the owner drops them on this chat or uses Add documents or instructions above the Grid (it files them for their rows, and instructions under their questions, after a Confirm); \"export everything for Kenya\": make_file with rows [kenya]; " +
      "\"score Liberia\", \"start the run\", \"carry on\": start_scoring (answers already scored: propose_rescore); \"pause\", \"stop\": propose_run_control; " +
      "add, remove, rename or merge rows, add, remove or change questions and their levels or points, remove rules: propose_change; the score sheet and bands, where to look, data needed, new rules: propose_change set_part (get_setup with formats first); " +
      "anything else the tool does (compare, charts, checks, any step): run_command, after run_command [\"help\"] if you are unsure what exists; a task that needs Claude to read and write across the project: propose_job. " +
      "If you notice you are about to write can't, cannot, unable, not possible or do it yourself, stop and put the route instead.";
    if (!local()) return [routes];
    return [charge, routes,
      "Wider tools: read_documents (look things up in the documents yourself), make_file (a CSV or Excel file with a Download button: never paste a table for the owner to copy), propose_instruction (a standing instruction or new rule for every scorer), " +
        "propose_rescore (score again any answers), propose_sections (section numbers for saved quotes, no tokens), propose_job (anything else, done by a background Claude session, undoable in one click) and background_jobs (what jobs did, and Undo).",
      "Where a quote sits: evidence may carry a section, such as s. 4(4) or art. 35(1), next to its page. When the owner wants sections rather than pages: propose_instruction so future scoring records them, then propose_sections for the answers already saved (free), then a job for those it could not place, with its cost.",
    ];
  }

  HB.chatMore = { tools, instructions, node, watch, FALLBACK };
})(window.HB = window.HB || {});
