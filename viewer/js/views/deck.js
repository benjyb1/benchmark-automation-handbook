/* =============================================================================
   Handbook viewer: views/deck.js, the step brief: a short note from Claude at
   the top of a step, so the owner can see how Claude worked and what it chose
   for them.

   HB.stepBrief(step, ctx) returns the brief for a tab as an HTMLElement, or
   null when there is none. Tab to brief: setup → set-up, documents →
   documents, grid → scores, results → results; rules → null (the Rules list is
   itself the decisions). The Documents tab is gone (it is part of the Grid), so
   a link that opened it, an item's `open.tab` "documents", opens the Grid.

   Data: bundle.decks, the briefs in deck/ that passed `hb check-deck` (see
   lib/handbook/deck.py for the shape). Code chose every item and figure;
   Claude wrote only the sentences (how.lines and each item's line). Parts:
     How I did it            three or four lines with the step's figures.
     What I decided for you  the choices the owner can change. A rule's Change
                             opens HB.ruleEditor(id, ctx, {compact: true})
                             (rules.js) under it, or links to #rules~<id> when
                             that is missing. Other choices link to where they
                             are changed, or open the viewer on their cases.
     Needs your eye          up to three patterns. "75 answers" opens the viewer
                             on exactly those cases: the Grid with the cells
                             highlighted, the Grid narrowed to the rows
                             (HB.state.onlyRows), or the comparison on Results.
                             A "send" pattern lists its cases with a Send button
                             each: the owner uploads the file (assets capability;
                             PDF or text) and/or pastes a link, and the page
                             saves one doc to the `supplied` collection: {rows,
                             pattern, replaces, what, file, asset, link, note}
                             (+ id, by, at). Claude files it with add-document
                             or fetch (host/claude.md). Without the store it says
                             the files can't be taken in this view. HB.supply opens
                             the same dialog from other views (the comparison's
                             "Check these first"), and lends its file picker and
                             upload to the cell panel's Correct this.

   The card starts folded to one line (open at the top of every step it was too
   much). Opened is kept per step in HB.store (localStorage, wrapped), for
   that brief only: a newer brief starts folded again. A brief this viewer has
   not shown before carries a "New" mark for the rest of the visit.
   Present shows the three parts as full-screen slides (arrow keys move, Escape
   leaves); Download saves them as one HTML file through the downloads
   capability. There is no banner for briefs on other tabs: each step shows its own.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt, plural } = HB;
  const CONTRACT = "handbook-deck/2";
  const BRIEF_OF = { setup: "setup", documents: "documents", grid: "scores", results: "results" };
  const STEP_WORDS = { setup: "Set-up", documents: "Documents", scores: "Scores", results: "Results" };
  const TAB_WORDS = { setup: "the Set-up tab", documents: "the Grid", grid: "the Grid", rules: "the Rules tab", results: "the Results tab" };
  const PARTS = { how: "How I did it", decided: "What I decided for you", eye: "Needs your eye" };

  const CSS = `
.sb { display: grid; gap: var(--s3); min-width: 0; padding: var(--s3) var(--s4); border: 1px solid var(--rule); border-inline-start: 3px solid var(--accent); border-radius: var(--radius); background: var(--surface); }
.sb__head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s3); min-width: 0; }
.sb__h { flex: 1 1 18rem; min-width: 0; margin: 0; font: inherit; }
.sb__toggle { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s2); max-width: 100%; padding: 0; border: 0; background: none; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.sb__toggle:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; border-radius: 2px; }
.sb__chev { flex: none; align-self: center; width: 0.75rem; height: 0.75rem; transition: transform var(--dur-fast, .15s) ease; }
.sb__toggle[aria-expanded="false"] .sb__chev { transform: rotate(-90deg); }
.sb__kick { color: var(--accent); font-size: var(--fs-12); font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.sb__new { padding: 0 0.4rem; border-radius: 999px; background: var(--accent); color: var(--on-accent); font-size: var(--fs-12); font-weight: 700; }
.sb__title { font-family: var(--serif); font-size: var(--fs-16); font-weight: 600; }
.sb__sum { color: var(--ink-2); font-size: var(--fs-14); }
.sb__tools { display: flex; flex-wrap: wrap; gap: var(--s2); margin-inline-start: auto; }
.sb__body { display: grid; grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.55fr) minmax(0, 0.95fr); gap: var(--s3) var(--s5); min-width: 0; }
.sb__body[hidden] { display: none; }
.sb__part { display: grid; align-content: start; gap: var(--s2); min-width: 0; }
.sb__part h3 { margin: 0; color: var(--ink-2); font-family: var(--sans); font-size: var(--fs-12); font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; }
.sb-how, .sb-list { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; font-size: var(--fs-14); line-height: 1.45; }
.sb-how li { padding-inline-start: var(--s3); border-inline-start: 2px solid var(--rule); }
.sb-item { display: grid; gap: 2px; min-width: 0; overflow-wrap: anywhere; }
.sb-item__line { margin: 0; }
.sb-item__meta { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 var(--s2); margin: 0; color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
.sb-item__meta > * + *::before { content: "·"; margin-inline-end: var(--s2); color: var(--rule-strong); }
.sb-rule { color: var(--ink); font-weight: 700; text-decoration: none; }
.sb-link { padding: 0; border: 0; background: none; color: var(--accent); font: inherit; font-weight: 600; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.sb-item--act { grid-template-columns: minmax(0, 1fr) auto; column-gap: var(--s3); }
.sb-item--act > * { grid-column: 1; }
.sb-item--act > .sb-change { grid-column: 2; grid-row: 1 / span 2; align-self: start; font-size: var(--fs-14); }
.sb-item--act > .sb-editor { grid-column: 1 / -1; }
.sb-editor { margin-top: var(--s2); }
.sb-editor[hidden] { display: none; }
.sb-more, .sb-none, .sb__foot { margin: 0; color: var(--ink-2); font-size: var(--fs-12); }
.sb__foot { grid-column: 1 / -1; }
.sb-send { font-size: var(--fs-12); }
.sb-send > summary { color: var(--accent); font-weight: 600; cursor: pointer; }
.sb-send[open] { display: grid; gap: var(--s2); padding: var(--s2) var(--s3); border: 1px dashed var(--rule-strong); border-radius: var(--radius); }
.sb-send p { margin: 0; }
.sb-send ul { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
.sb-send li { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--s1) var(--s2); }
.sb-send li a { color: var(--ink-2); }
.sb-sendbtn { padding: 0.05rem 0.6rem; border: 1px solid var(--accent); border-radius: 999px; background: transparent; color: var(--accent); font: inherit; font-weight: 600; cursor: pointer; }
.sb-sendbtn.is-sent { border-color: transparent; color: var(--ink-2); cursor: default; }
.sb.is-flash { animation: sb-flash 1.2s ease-out 1; }
@keyframes sb-flash { from { box-shadow: 0 0 0 4px var(--accent-soft); } to { box-shadow: 0 0 0 0 transparent; } }
@media (width < 64rem) { .sb__body { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } .sb__part--decided { grid-column: 1 / -1; grid-row: 2; } }
@media (width < 40rem) {
  .sb { padding: var(--s3); }
  .sb__body { grid-template-columns: minmax(0, 1fr); }
  .sb__part--decided { grid-row: auto; }
  .sb__tools { margin-inline-start: 0; }
}
@media (prefers-reduced-motion: reduce) { .sb__chev { transition: none; } .sb.is-flash { animation: none; } }

.dk { --slide: #10231e; --slide-ink: #eef4f0; --slide-ink-2: #a9bdb4; --slide-accent: #8fd3bd; --slide-rule: #2c4a41; }
:root[data-theme="dark"] .dk { --slide: #0b1a16; --slide-ink-2: #9db3a9; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .dk { --slide: #0b1a16; --slide-ink-2: #9db3a9; } }
dialog.dk { box-sizing: border-box; width: 100vw; max-width: none; height: 100dvh; max-height: none; margin: 0; padding: var(--s4); border: 0; background: #050c0a; color: var(--slide-ink); }
dialog.dk[open] { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: var(--s3); }
dialog.dk::backdrop { background: #050c0a; }
.dk__top { display: flex; align-items: center; justify-content: space-between; gap: var(--s3); color: #9db3a9; font-size: var(--fs-14); }
.dk__stage { display: grid; place-items: center; min-height: 0; }
.dk__frame { position: relative; width: min(100%, calc((100dvh - 8rem) * 16 / 9)); aspect-ratio: 16 / 9; overflow: hidden; border-radius: 8px; background: var(--slide); color: var(--slide-ink); }
.dk__frame:focus-visible { outline: 2px solid var(--slide-accent); outline-offset: 3px; }
.dk-s { position: absolute; inset: 0; display: flex; flex-direction: column; gap: var(--s3); padding: clamp(1.1rem, 3.6vw, 3rem) clamp(1.1rem, 4.5vw, 3.75rem); overflow-y: auto; visibility: hidden; opacity: 0; transition: opacity var(--dur-fast, .2s) ease; scrollbar-width: thin; }
.dk-s.is-on { visibility: visible; opacity: 1; }
@media (prefers-reduced-motion: reduce) { .dk-s { transition: none; } }
.dk-s__kick { margin: 0; color: var(--slide-accent); font: 600 0.75rem/1.3 var(--sans); letter-spacing: 0.1em; text-transform: uppercase; }
.dk-s h3 { max-width: 28ch; margin: 0; font: 600 clamp(1.3rem, 2.9vw, 2.35rem)/1.15 var(--serif); text-wrap: balance; }
.dk-s p, .dk-s li { max-width: 62ch; margin: 0; color: var(--slide-ink); font-size: clamp(0.9rem, 1.45vw, 1.12rem); line-height: 1.45; }
.dk-s ul { display: grid; gap: var(--s3); margin: 0; padding-left: 1.2rem; }
.dk-s li small { display: block; margin-top: 2px; color: var(--slide-ink-2); font-size: clamp(0.75rem, 1.1vw, 0.88rem); }
.dk-s .dk-more { color: var(--slide-ink-2); font-size: clamp(0.8rem, 1.2vw, 0.95rem); }
.dk__nav { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: var(--s2) var(--s3); }
.dk__dots { display: flex; gap: 0.35rem; }
.dk__dot { flex: none; width: 0.65rem; height: 0.65rem; min-width: 0; padding: 0; border: 1px solid #9db3a9; border-radius: 50%; background: transparent; cursor: pointer; }
.dk__dot[aria-current="true"] { border-color: var(--slide-accent); background: var(--slide-accent); }
.dk__hint { color: #9db3a9; font-size: var(--fs-12); }
@media (width < 45rem) {
  .dk__frame { aspect-ratio: auto; height: 100%; }
  .dk__hint { display: none; }
}
`;
  HB.addStyles("deck", CSS);

  /* ---- Data -------------------------------------------------------------------- */

  function briefs(bundle) {
    return Array.isArray(bundle && bundle.decks)
      ? bundle.decks.filter((d) => d && d.contract === CONTRACT && Array.isArray(d.slides)) : [];
  }

  function briefFor(bundle, step) {
    return briefs(bundle).find((d) => d.step === step) || null;
  }

  function part(d, id) {
    return d.slides.find((s) => s && s.id === id) || {};
  }

  function storeKey(kind, step) {
    const p = (HB.data.bundle() || {}).project || {};
    return "brief-" + kind + ":" + (p.slug || "") + ":" + step;
  }

  function seenUpTo(step) {
    return String(HB.store.get(storeKey("seen", step), "") || "");
  }

  // Briefs that were new when first shown this visit keep their mark until the page reloads.
  const newThisVisit = new Map();
  function isNew(d) {
    if (newThisVisit.get(d.step) === d.made) return true;
    if (seenUpTo(d.step) >= String(d.made)) return false;
    newThisVisit.set(d.step, d.made);
    HB.store.set(storeKey("seen", d.step), String(d.made));
    return true;
  }

  function isFolded(d) {
    return String(HB.store.get(storeKey("opened", d.step), "") || "") !== String(d.made);
  }

  let scrollTo = null;

  /* ---- Opening the cases --------------------------------------------------------- */

  function openAll(open, ctx) {
    if (!open) return;
    if (open.tab === "grid" && Array.isArray(open.cells)) {
      ctx.highlight(open.cells);
    } else if (open.tab === "documents") {
      HB.state.onlyRows = Array.isArray(open.rows) ? open.rows.slice() : null;
      ctx.go("grid");
    } else if (open.tab === "results") {
      ctx.go("results", "compare");
    } else {
      ctx.go(open.tab);
    }
    // The brief sits above the tab's own content, so on the same tab the change happens out of
    // sight: bring what changed into view.
    requestAnimationFrame(() => {
      const brief = document.querySelector(".sb");
      const target = brief && brief.nextElementSibling;
      if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  function linkButton(label, onclick) {
    return h("button", { type: "button", class: "sb-link", onclick }, label);
  }

  /** "75 answers", "12 assessments", or the tab's name for set-up items with no rows. */
  function casesLink(item, ctx) {
    const open = item.open || {};
    if (Array.isArray(open.cells)) return linkButton(plural(open.cells.length, "answer"), () => openAll(open, ctx));
    if (Array.isArray(open.rows) && open.rows.length) return linkButton(ctx.unitWords(open.rows.length), () => openAll(open, ctx));
    return h("a", { href: HB.href(open.tab) }, "Open " + (TAB_WORDS[open.tab] || open.tab));
  }

  /* ---- What I decided for you ------------------------------------------------------ */

  function ruleState(it) {
    const pending = HB.edits && HB.edits.ready && typeof HB.edits.pendingDecision === "function" ? HB.edits.pendingDecision(it.rule) : null;
    if (pending) return "Waiting for Claude: your choice" + (pending.option ? " (" + pending.option + ")" : "");
    if (it.status === "decided") return "You decided";
    if (it.status === "skipped") return "Skipped; my recommendation stays";
    return "My recommendation in use";
  }

  /** ["2,673 points at stake", "84 answers rest on it"]; in the results step, the points on the rows that moved. */
  function ruleStakes(it) {
    const bits = [it.moved_points != null
      ? fmtInt(it.moved_points) + " points on the " + fmtInt(it.moved_rows) + " that moved most"
      : fmtInt(it.points) + " points at stake"];
    if (it.resting) bits.push(plural(it.resting, "answer") + (it.resting === 1 ? " rests" : " rest") + " on it");
    return bits;
  }

  function ruleItem(it, ctx) {
    const box = h("div", { class: "sb-editor", hidden: true });
    let change;
    if (typeof HB.ruleEditor === "function") {
      change = h("button", {
        type: "button", class: "sb-link sb-change", "aria-expanded": "false",
        onclick: (e) => {
          const btn = e.currentTarget;
          const open = btn.getAttribute("aria-expanded") !== "true";
          if (open && !box.firstChild) {
            let editor = null;
            try {
              editor = HB.ruleEditor(it.rule, ctx, { compact: true });
            } catch (err) {
              console.error("HB.ruleEditor failed:", err);
            }
            if (!(editor instanceof Node)) return ctx.go("rules", it.rule);
            box.append(editor);
          }
          box.hidden = !open;
          btn.setAttribute("aria-expanded", String(open));
          btn.textContent = open ? "Close" : "Change";
        },
      }, "Change");
    } else {
      change = h("a", { class: "sb-link sb-change", href: HB.href("rules", it.rule) }, "Change");
    }
    return h("li", { class: "sb-item sb-item--act", "data-rule": it.rule },
      h("p", { class: "sb-item__line" }, it.line),
      h("p", { class: "sb-item__meta" },
        h("a", { class: "sb-rule", href: HB.href("rules", it.rule) }, it.rule),
        h("span", { title: it.using_text ? "In use: " + it.using_text : null }, ruleState(it)),
        ...ruleStakes(it).map((t) => h("span", null, t))),
      change, box);
  }

  /** A step's own choice: Change links to where it lives (Set-up); See or Check opens its cases (the Grid). */
  function choiceItem(it, ctx) {
    const open = it.open || {};
    const tab = TAB_WORDS[open.tab] || open.tab;
    const cases = Array.isArray(open.cells) || (Array.isArray(open.rows) && open.rows.length > 0);
    const act = cases
      ? h("button", { type: "button", class: "sb-link sb-change", title: "Open " + tab + " on these", onclick: () => openAll(open, ctx) }, open.tab === "grid" ? "Check" : "See")
      : h("a", { class: "sb-link sb-change", href: HB.href(open.tab), title: "On " + tab }, "Change");
    const count = Array.isArray(open.cells) ? plural(open.cells.length, "answer") : cases ? ctx.unitWords(open.rows.length) : null;
    return h("li", { class: "sb-item sb-item--act" },
      h("p", { class: "sb-item__line" }, it.line),
      h("p", { class: "sb-item__meta" }, h("span", null, it.label), count ? h("span", null, count) : null),
      act);
  }

  function moreLine(more) {
    const open = (more && more.open) || 0;
    const decided = (more && more.decided) || 0;
    if (!open && !decided) return null;
    const bits = [];
    if (open) bits.push(fmtInt(open) + " more open");
    if (decided) bits.push(fmtInt(decided) + (open ? "" : " more") + " you decided");
    return h("p", { class: "sb-more" }, "And " + bits.join(", ") + " in the ", h("a", { href: HB.href("rules") }, "Rules tab"), ", by points at stake.");
  }

  /* ---- Needs your eye: asking the owner to help -------------------------------------- */

  let assetsPromise = null;
  function assetsTool() {
    if (!assetsPromise) {
      const c = window.claude;
      assetsPromise = c && typeof c.use === "function"
        ? Promise.resolve().then(() => c.use("assets")).catch(() => null).then((ns) => ns || null)
        : Promise.resolve(null);
    }
    return assetsPromise;
  }

  // "row~pattern" already sent: from this view, and from the `supplied` collection (Claude
  // deletes each doc once it is filed, so a sent case shows "Sent" until then).
  const sent = new Set();
  let followingSupplied = false;
  function followSupplied() {
    if (followingSupplied || !(HB.edits && HB.edits.ready)) return;
    followingSupplied = HB.edits.follow("supplied", (docs) => {
      let added = false;
      docs.forEach((d) => (Array.isArray(d.rows) ? d.rows : []).forEach((row) => {
        const key = row + "~" + d.pattern;
        if (!sent.has(key)) { sent.add(key); added = true; }
      }));
      if (added && HB.route && (BRIEF_OF[HB.route().tab] || HB.route().tab === "results")) HB.render();
    });
  }

  const TEXT_TYPES = { txt: "text/plain", text: "text/plain", md: "text/markdown", csv: "text/csv" };

  // The file picker and the upload, shared with the cell panel's Correct this (HB.supply).
  function fileControl(id) {
    return h("input", { type: "file", class: "input", id, accept: ".pdf,.txt,.md,application/pdf,text/plain,text/markdown" });
  }

  /** Uploads a PDF or text file to the page's assets; Promise of its asset id. Throws an Error
      with a sentence for the owner when the kind, the size or the upload is wrong. */
  async function upload(f) {
    const tool = await assetsTool();
    if (!tool) throw new Error("This page can't take files. Paste a link instead.");
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    const type = ext === "pdf" ? "application/pdf" : TEXT_TYPES[ext];
    if (!type) throw new Error("Send a PDF or a text file. For a web page, print it to PDF first.");
    try {
      return (await tool.upload(f, { type })).id;
    } catch (err) {
      const code = err && err.code;
      throw new Error(code === "too_large" ? "That file is over 20 MB. Send a link instead, or a smaller file."
        : code === "unsupported_type" ? "That kind of file can't be sent here. Send a PDF or a text file."
        : "The upload didn't work (" + (code || "error") + "). Try again, or send a link instead.");
    }
  }

  /** The Send dialog for one row: the owner uploads a file and/or pastes a link, and the page
      saves one doc to `supplied`. spec {row, name, pattern, what, replaces, kicker, intro}; intro
      defaults to what. Promise of true once sent. Also HB.supply.send, for the comparison's
      "Check these first". */
  async function supply(spec) {
    const tool = await assetsTool();
    const file = fileControl("dk-send-file");
    const link = HB.edits.textControl("dk-send-link", "");
    link.type = "url";
    link.placeholder = "https://";
    const note = HB.edits.textControl("dk-send-note", "", true);
    note.rows = 3;
    const body = [
      tool ? HB.edits.field("dk-send-file", "The file", file, "A PDF or a text file, up to 20 MB. For a web page, print it to PDF first.")
        : HB.notice("neutral", "This page can't take files. Paste a link here instead."),
      HB.edits.field("dk-send-link", "Or a link to it", link, "Claude fetches it and keeps a copy."),
      HB.edits.field("dk-send-note", "Anything Claude should know (optional)", note),
    ];
    const ok = await HB.edits.dialog({
      kicker: spec.kicker, title: "Send for " + spec.name, intro: spec.intro || spec.what,
      body, focus: tool ? file : link, saveLabel: "Send to Claude",
      done: HB.when("Sent. Claude is filing it now, and the page shows which answers it changes when it is done.",
        "Sent. Claude files it the next time it works on this project, and shows you which answers it changes."),
      submit: async () => {
        const f = file.files && file.files[0];
        const url = link.value.trim();
        if (!f && !url) throw new Error("Choose a file or paste a link.");
        if (url && !/^https?:\/\//i.test(url)) throw new Error("The link must start with http:// or https://.");
        const asset = f && tool ? await upload(f) : null;
        await HB.edits.save("supplied", { rows: [spec.row], pattern: spec.pattern, replaces: spec.replaces || [], what: spec.what,
          file: f ? f.name : null, asset, link: url || null, note: note.value.trim() || null });
      },
    });
    if (ok) sent.add(spec.row + "~" + spec.pattern);
    return ok;
  }

  async function send(c, item, button) {
    const what = (c.documents || []).map((d) => d.title).join("; ") || (c.expected || []).join("; ") || c.name;
    const ok = await supply({ row: c.row, name: c.name, pattern: item.pattern, what, kicker: item.label,
      replaces: (c.documents || []).map((d) => d.doc) });
    if (ok) {
      button.textContent = "Sent";
      button.classList.add("is-sent");
      button.disabled = true;
    }
  }

  function sendButton(c, item) {
    followSupplied();
    if (sent.has(c.row + "~" + item.pattern)) return h("button", { type: "button", class: "sb-sendbtn is-sent", disabled: true }, "Sent");
    if (!(HB.edits && HB.edits.ready)) return null;
    return h("button", { type: "button", class: "sb-sendbtn", onclick: (e) => send(c, item, e.currentTarget) }, "Send");
  }

  /* The same Send for other views: ready() says whether the page can save to `supplied`;
     sent(row, pattern) whether that case went (from this page or still waiting in the store).
     assets() is a Promise of the page's upload tool or null, fileControl(id) the file picker
     and upload(file) the upload behind it (the cell panel's Correct this uses all three). */
  HB.supply = {
    ready: () => Boolean(HB.edits && HB.edits.ready),
    send: supply,
    assets: assetsTool,
    fileControl,
    upload,
    sent: (row, pattern) => { followSupplied(); return sent.has(row + "~" + pattern); },
  };

  function caseDetail(c) {
    if (Array.isArray(c.documents) && c.documents.length) {
      const parts = [];
      c.documents.forEach((d, i) => {
        const href = d.url || d.archive_url;
        if (i) parts.push("; ");
        parts.push(href ? h("a", { href, target: "_blank", rel: "noopener" }, d.title) : d.title);
      });
      return parts;
    }
    if (Array.isArray(c.expected) && c.expected.length) return "Expected: " + c.expected.join("; ");
    return "";
  }

  function sendBox(item) {
    const cases = (item.cases || []).filter((c) => c.row);
    const away = !(HB.edits && HB.edits.ready);
    return h("details", { class: "sb-send" },
      h("summary", null, "Can you help? Send a file or a link"),
      h("p", null, item.help.ask),
      away ? h("p", null, HB.when(HB.cantSave(), "This view can't take files. Open the project's own page to send them.")) : null,
      h("ul", null, ...cases.map((c) => h("li", null, h("span", null, h("b", null, c.name), " ", caseDetail(c)), sendButton(c, item)))),
      item.more ? h("p", null, "And " + fmtInt(item.more) + " more: open them all with the link above.") : null);
  }

  function eyeItem(it, ctx) {
    const meta = [h("span", null, casesLink(it, ctx))];
    if (it.points) meta.push(h("span", null, fmtInt(it.points) + " points at stake"));
    const canSend = it.help && it.help.kind === "send" && (it.cases || []).some((c) => c.row);
    return h("li", { class: "sb-item" },
      h("p", { class: "sb-item__line" }, it.line),
      h("p", { class: "sb-item__meta" }, ...meta),
      canSend ? sendBox(it) : null);
  }

  /* ---- The card -------------------------------------------------------------------- */

  function chevron() {
    return HB.svg("svg", { class: "sb__chev", viewBox: "0 0 12 12", "aria-hidden": "true", focusable: "false" },
      HB.svg("path", { d: "M2.5 4.5 6 8l3.5-3.5", fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }

  function card(d, ctx) {
    const fresh = isNew(d);
    const folded = isFolded(d);
    const id = "sb-" + d.step;
    const how = part(d, "how");
    const decided = part(d, "decided");
    const eye = part(d, "eye");
    const lines = (how.lines || []).filter((t) => typeof t === "string" && t.trim());
    const items = Array.isArray(decided.items) ? decided.items : [];
    const eyes = Array.isArray(eye.items) ? eye.items : [];
    const more = decided.more || {};
    const made = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }).format(new Date(d.made));

    const body = h("div", { class: "sb__body", id: id + "-body", hidden: folded },
      h("section", { class: "sb__part sb__part--how" }, h("h3", null, PARTS.how),
        h("ul", { class: "sb-how" }, ...lines.map((t) => h("li", null, t)))),
      h("section", { class: "sb__part sb__part--decided" }, h("h3", null, PARTS.decided),
        items.length ? h("ul", { class: "sb-list" }, ...items.map((it) => (it.kind === "rule" ? ruleItem(it, ctx) : choiceItem(it, ctx))))
          : h("p", { class: "sb-none" }, "Nothing for you to change in this step."),
        moreLine(more)),
      h("section", { class: "sb__part sb__part--eye" }, h("h3", null, PARTS.eye),
        eyes.length ? h("ul", { class: "sb-list" }, ...eyes.map((it) => eyeItem(it, ctx)))
          : h("p", { class: "sb-none" }, "Nothing this time.")),
      h("p", { class: "sb__foot" }, "Written by Claude at the end of the " + (STEP_WORDS[d.step] || d.step).toLowerCase() +
        " step, " + made + ". Code checked every name and number in it against the files."));

    const toggle = h("button", {
      type: "button", class: "sb__toggle", "aria-expanded": String(!folded), "aria-controls": body.id,
      onclick: () => {
        const open = body.hidden;
        body.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        tools.hidden = !open;
        HB.store.set(storeKey("opened", d.step), open ? String(d.made) : null);
      },
    }, chevron(), h("span", { class: "sb__kick" }, "Claude's note on this step"), fresh ? h("span", { class: "sb__new" }, "New") : null);
    const presentBtn = h("button", { type: "button", class: "btn btn--small", onclick: (e) => present(d, ctx, e.currentTarget) }, "Present");
    const msg = h("span", { class: "sb__sum", role: "status" });
    const downloadBtn = h("button", { type: "button", class: "btn btn--small", onclick: () => download(d, ctx, msg) }, "Download");
    const tools = h("div", { class: "sb__tools", hidden: folded }, msg, presentBtn, downloadBtn);

    const el = h("section", { class: "sb", "aria-labelledby": id + "-h", "data-brief": d.step },
      h("div", { class: "sb__head" },
        h("h2", { class: "sb__h", id: id + "-h" }, toggle),
        h("span", { class: "sb__sum" }, eyes.length ? plural(eyes.length, "thing") + " to look at" : "Nothing to look at"),
        tools),
      body);

    if (scrollTo === d.step) {
      scrollTo = null;
      requestAnimationFrame(() => {
        el.scrollIntoView({ block: "start" });
        toggle.focus({ preventScroll: true });
        el.classList.add("is-flash");
      });
    }
    return el;
  }

  HB.stepBrief = function stepBrief(step, ctx) {
    if (!ctx && typeof HB.context === "function") ctx = HB.context();   // called from outside a tab (the demo's set-up page)
    if (!ctx || !ctx.bundle) return null;
    const d = BRIEF_OF[step] ? briefFor(ctx.bundle, BRIEF_OF[step]) : null;
    return d ? card(d, ctx) : null;
  };

  /* ---- Present and Download: the three parts as slides -------------------------------- */

  function slideParts(d, ctx) {
    const name = [d.name, d.edition].filter(Boolean).join(" ");
    const kick = () => h("p", { class: "dk-s__kick" }, name + " · " + (STEP_WORDS[d.step] || d.step) + " step");
    const how = part(d, "how");
    const decided = part(d, "decided");
    const eye = part(d, "eye");
    const lines = (how.lines || []).filter((t) => typeof t === "string" && t.trim());
    const items = Array.isArray(decided.items) ? decided.items : [];
    const eyes = Array.isArray(eye.items) ? eye.items : [];
    const more = decided.more || {};
    const meta = (it) => (it.kind === "rule" ? [it.rule, ruleState(it), ...ruleStakes(it)].join(" · ") : it.label);
    const names = (it) => {
      const shown = (it.cases || []).map((c) => c.name).filter(Boolean);
      return shown.length ? shown.join(", ") + (it.more ? " and " + fmtInt(it.more) + " more" : "") : "";
    };
    return [
      [kick(), h("h3", null, PARTS.how), h("ul", null, ...lines.map((t) => h("li", null, t)))],
      [kick(), h("h3", null, PARTS.decided),
        h("ul", null, ...items.map((it) => h("li", null, it.line, h("small", null, meta(it))))),
        more.open ? h("p", { class: "dk-more" }, fmtInt(more.open) + " more open rules wait in the Rules tab.") : null],
      [kick(), h("h3", null, PARTS.eye),
        eyes.length ? h("ul", null, ...eyes.map((it) => h("li", null, it.line, h("small", null, names(it))))) : h("p", null, "Nothing this time.")],
    ];
  }

  function present(d, ctx, opener) {
    const content = slideParts(d, ctx);
    const slides = content.map((parts, i) => h("section", { class: "dk-s", "aria-roledescription": "slide", "aria-label": (i + 1) + " of " + content.length }, ...parts));
    const frame = h("div", { class: "dk__frame", tabindex: "0", role: "region", "aria-roledescription": "slides", "aria-label": d.title }, ...slides);
    let at = 0;
    const dots = slides.map((_, i) => h("button", { type: "button", class: "dk__dot", "aria-label": "Slide " + (i + 1), onclick: () => go(i) }));
    const prev = h("button", { type: "button", class: "btn btn--small", "aria-label": "Previous slide", onclick: () => go(at - 1) }, "←");
    const next = h("button", { type: "button", class: "btn btn--small", "aria-label": "Next slide", onclick: () => go(at + 1) }, "→");
    function go(i) {
      at = Math.max(0, Math.min(slides.length - 1, i));
      slides.forEach((s, k) => { s.classList.toggle("is-on", k === at); s.setAttribute("aria-hidden", String(k !== at)); s.inert = k !== at; });
      dots.forEach((dot, k) => dot.setAttribute("aria-current", String(k === at)));
      prev.disabled = at === 0;
      next.disabled = at === slides.length - 1;
    }
    const dlg = h("dialog", { class: "dk", "aria-label": d.title },
      h("div", { class: "dk__top" }, h("span", null, d.title), h("button", { type: "button", class: "btn btn--small", onclick: () => dlg.close() }, "Leave")),
      h("div", { class: "dk__stage" }, frame),
      h("div", { class: "dk__nav" }, prev, h("div", { class: "dk__dots" }, ...dots), next, h("span", { class: "dk__hint" }, "← and → move, Escape leaves")));
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "PageDown") { go(at + 1); e.preventDefault(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp") { go(at - 1); e.preventDefault(); }
    });
    dlg.addEventListener("close", () => {
      dlg.remove();
      if (opener && opener.isConnected) opener.focus();
    });
    (document.querySelector(".hb") || document.body).append(dlg);
    go(0);
    dlg.showModal();
    frame.focus();
  }

  function standalone(d, ctx) {
    const sections = slideParts(d, ctx).map((parts) => h("section", { class: "dk-s" }, ...parts).innerHTML);
    const css = CSS.replace(/\.dk-s \{[^}]*\}/, ".dk-s { display: flex; flex-direction: column; gap: 0.75rem; padding: 2.5rem 3rem; aspect-ratio: 16 / 9; break-after: page; margin: 0 auto 1.5rem; max-width: 60rem; border-radius: 8px; background: var(--slide); color: var(--slide-ink); overflow: hidden; }");
    const title = [d.name, d.edition].filter(Boolean).join(" ") + ": " + d.title;
    // The page itself must hold no document-level tags (the Artifact wraps it), so they are built.
    const tag = (name, attrs) => "<" + name + (attrs ? " " + attrs : "") + ">";
    return tag("!doctype html") + tag("ht" + "ml", "lang=\"en-GB\"") + tag("he" + "ad") +
      "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>" +
      title.replace(/</g, "&lt;") + "</title><style>:root{--serif:Charter,Georgia,serif;--sans:system-ui,sans-serif;--s1:.25rem;--s2:.5rem;--s3:.75rem;--s4:1rem;--radius:4px}" +
      "body{margin:0;padding:1.5rem;background:#e9ece8;font-family:var(--sans)}" +
      css + "@media print{body{background:#fff;padding:0}}</style>" + tag("/he" + "ad") + tag("bo" + "dy") + "<div class=\"dk\">" +
      sections.map((s) => "<section class=\"dk-s\">" + s + "</section>").join("") + "</div>" + tag("/bo" + "dy") + tag("/ht" + "ml");
  }

  async function download(d, ctx, msg) {
    const file = String((ctx.bundle.project || {}).slug || "handbook") + "-" + d.step + "-note.html";
    try {
      const c = window.claude;
      const tool = c && typeof c.use === "function" ? await c.use("downloads") : null;
      if (!tool) throw new Error("This view can't save files. Open the page in Claude to download it.");
      await tool.save({ filename: file, data: standalone(d, ctx) });
      msg.textContent = "Saved " + file + ".";
    } catch (err) {
      msg.textContent = String((err && err.message) || err);
    }
  }
})(window.HB = window.HB || {});
