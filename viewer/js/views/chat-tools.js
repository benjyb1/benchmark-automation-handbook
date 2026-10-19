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
    if (!scored.length) throw new Error("None of those answers has been scored yet, so there is nothing to score again: the run scores them (propose_run_control).");
    const est = HB.cost && typeof HB.cost.rows === "function" ? HB.cost.rows(scored, input.questions ? qids : null) : null;
    const why = String(input.why || "").trim();
    const line = "Score again " + scopeWords(api, input.rows ? scored : null, input.questions ? qids : null) + (why ? " (" + api.cut(why, 100) + ")" : "") +
      ". " + (est ? HB.cost.line(est) : "It uses some of your Claude plan.");
    return api.propose(line, () => HB.edits.save("control", { action: "rerun", rule: null, rows: scored, questions: qids, why: why || null }));
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
    return api.propose("Undo the job “" + api.cut(e.title, 100) + "”: put the whole project back as it was before it ran. Anything changed since, including later jobs, is undone too.",
      async () => {
        await HB.local.post("chat/undo", { job: e.id });
        e.undone = true;
        return { __result: true, applied: true, note: "Undone. The project is back as it was.", told: "The job was undone: the project is back as it was before it.)" };
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

  /* ---- The tool list and the standing instructions --------------------------------------- */

  const S = (props, required) => ({ type: "object", properties: props, required: required || [] });
  const ROWS = { type: "array", items: { type: "string" }, description: "row ids (names work too); leave out for all" };
  const QIDS = { type: "array", items: { type: "string" }, description: "question ids; leave out for all" };

  function tools(api) {
    if (!local()) return [];
    const w = (fn) => (input, ctx) => fn(input || {}, api, ctx);
    return [
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
      { name: "background_jobs", description: "The background jobs so far, newest first: title, status, tokens used, what each said, whether undone. With undo (a job id): puts Undo to the owner, which puts the whole project back as it was before that job (files made for download stay). Saved only when they click Confirm.",
        inputSchema: S({ undo: { type: "string" } }), execute: w(backgroundJobs) },
      { name: "propose_job", description: "For anything no other tool does: puts to the owner a background job, one Claude session that does the task on the project's files behind the page (it can read every document and change any answer, rule, question or file, with a copy taken first so one click undoes it). title: a few words for the owner. brief: the task in full for that session, with the rows, questions and what done looks like. size: small (one row or a few answers), medium (several rows), large (most of the project), or tokens [low, high] when you know better. Saved only when they click Confirm.",
        inputSchema: S({ title: { type: "string" }, brief: { type: "string" }, size: { type: "string", enum: ["small", "medium", "large"] }, tokens: { type: "array", items: { type: "number" } } }, ["title", "brief"]), execute: w(proposeJob) },
    ];
  }

  function instructions() {
    if (!local()) return [];
    return [
      "Wider tools: read_documents (look things up in the documents yourself), make_file (a CSV or Excel file with a Download button: never paste a table for the owner to copy), propose_instruction (a standing instruction or new rule for every scorer), " +
        "propose_rescore (score again any answers), propose_sections (section numbers for saved quotes, no tokens), propose_job (anything else, done by a background Claude session, undoable in one click) and background_jobs (what jobs did, and Undo).",
      "Where a quote sits: evidence may carry a section, such as s. 4(4) or art. 35(1), next to its page. When the owner wants sections rather than pages: propose_instruction so future scoring records them, then propose_sections for the answers already saved (free), then a job for those it could not place, with its cost.",
    ];
  }

  HB.chatMore = { tools, instructions, node, watch };
})(window.HB = window.HB || {});
