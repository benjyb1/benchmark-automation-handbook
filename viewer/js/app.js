/* =============================================================================
   Handbook viewer: app.js, the page shell. It runs last (after util.js, data.js,
   components.js, charts.js, edits.js, downloads.js and views/*.js) and fills the
   slots in index.html: one short top bar (role=banner) holding #hb-head (the
   benchmark's name, edition and one small line of facts), #hb-tabs (one link per
   view) and #hb-theme (left empty: the viewer is always light, see drawTheme); then #hb-banner (edits
   waiting) and #main (the current tab). The bar stays one row on wide screens and two short rows on a
   phone, so the tab's data starts high on the screen.

   Views: one file per tab in views/, each registering itself before app.js runs:
     (HB.views = HB.views || {}).grid = {id: "grid", label: "Grid", order: 20,
       render(root, ctx) {...}, visible(bundle) {return true;}}
   The tab's id is view.id (keep the key the same). render() fills root (already
   in the page, empty) and may also return a Node to put in it. Start the tab with an h2. visible() is optional (left out = shown).
   With no views registered the page shows an empty state instead.
   After a tab is drawn, HB.runBar(viewId, root, ctx) (views/runbar.js) may add
   the run bar to it (the Grid tab). The tabs that are the
   process's steps (Set-up, Rules, Grid, Feedback, Results, in that order) then
   (no longer open with HB.stepBrief: the few notes that matter show on the chat button).
   Once the first tab is drawn, HB.chat.start() (views/chat.js) puts the corner
   chat's button in #hb-chat.

   Routes: the address's hash as bare tokens joined by "~" (the Artifact viewer
   passes on only letters, digits, ".", "_", "~" and "-"):
     #grid                 a tab
     #grid~dk-acme~1.1     a tab with a cell open (row id, question id)
     #results~compare      a tab with its own parts (any count other than a valid
                           row and question pair), read from ctx.route.parts
   Parts are encoded by HB.href: any character outside letters, digits, "." and
   "-" becomes "_<hex code>_". An unknown tab shows the default tab, except "documents" (an old
   link; the Documents tab is part of the Grid now), which lands on the Grid. A hash naming
   an element on the page (the skip link's #main) is an in-page jump.
   Default tab: "setup" while project.stage is "setup" and no cell is scored, else
   "grid"; when that tab is not registered, the first tab.

   The open cell: when the route names a row and question, HB.state.openCell is
   {row, qid}. If a view has set HB.cellPanel = (row, qid, ctx) =>
   {title, kicker?, content}, the shell shows it in the drawer and closing the
   drawer goes back to the tab's own address. Opening a cell only syncs the drawer;
   the tab is not drawn again (call HB.render() for that).

   HB.state                {groupBy, filters: {}, highlight: [[row, qid], ...],
                           openCell: {row, qid} | null}. groupBy is a label, or ""
                           for no grouping; it starts as this viewer's last choice
                           (kept in localStorage), else project.group_by, else the
                           first label. Views may keep their own session state on
                           it too (the grid keeps gridView, openGroups, showRests).
   HB.render()             draws the current tab again from HB.state (and the tab
                           bar, the banner and the open cell's drawer if it changed)
   HB.href(tab, ...parts)  the "#..." address of a tab (and parts)
   HB.go(tab, ...parts)    moves there (a new history entry)
   HB.openCell(row, qid)   opens a cell on the grid tab; HB.closeCell() closes it
   HB.setGroupBy(label)    sets HB.state.groupBy (a label, or "" for no
                           grouping), remembers it for this viewer, draws again
   HB.highlight(cells)     sets HB.state.highlight ([[row, qid], ...]) and moves
                           to the grid tab; HB.highlight([]) clears it
   HB.refreshBanner()      redraws "Edits waiting for Claude: N" from
                           HB.edits.pending() (edits.js calls it on a change),
                           and the line for an unseen step brief on another
                           tab (HB.deckBanner, views/deck.js)
   HB.route()              the current {tab, parts}
   HB.context()            a fresh ctx (below), for code outside a tab's render
   ctx (second argument of render and HB.cellPanel): {bundle, data (HB.data),
     state (HB.state), route {tab, parts}, href, go, openCell, closeCell,
     setGroupBy, highlight, render, unitWords (HB.data.unitWords)}
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, store, notice, drawer, fmtInt } = HB;
  HB.views = HB.views || {};

  const SEP = "~";
  const STAGE_WORDS = { setup: "Setting up", trial: "Trial run", running: "Scoring", done: "Finished" };
  const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const STEPS = ["setup", "rules", "grid", "feedback", "results"];   // the process, in order: one tab each
    const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });

  HB.state = { groupBy: null, filters: {}, highlight: [], openCell: null };

  let bundle = null;
  let current = { tab: null, parts: [] };
  let screenKey = null;      // the tab and its own parts as last drawn
  let shownCell = null;      // "row~qid" in the drawer, or null
  let openedHere = false;    // the open cell's history entry was added by this page
  let quietClose = false;    // the shell, not the reader, is closing the drawer

  /* ---- Addresses ------------------------------------------------------------- */

  function encodePart(value) {
    return String(value).replace(/[^A-Za-z0-9.-]/gu, (c) => "_" + c.codePointAt(0).toString(16) + "_");
  }

  function decodePart(value) {
    return value.replace(/_([0-9a-f]+)_/g, (m, hex) => {
      const code = parseInt(hex, 16);
      return code <= 0x10ffff ? String.fromCodePoint(code) : m;
    });
  }

  function href(tab, ...parts) {
    return "#" + [tab, ...parts].filter((p) => p != null && p !== "").map(encodePart).join(SEP);
  }

  function parse(hash) {
    let raw = String(hash || "").replace(/^#/, "");
    try {
      raw = decodeURIComponent(raw);
    } catch (err) {
      return { tab: null, parts: [] };
    }
    if (!raw) return { tab: null, parts: [] };
    const bits = raw.split(SEP).map(decodePart);
    return { tab: bits[0], parts: bits.slice(1) };
  }

  function replaceHash(hash) {
    try {
      location.replace(location.href.split("#")[0] + hash);
    } catch (err) {
      location.hash = hash;
    }
  }

  /* ---- Views ----------------------------------------------------------------- */

  function shown(view) {
    if (typeof view.visible !== "function") return true;
    try {
      return Boolean(view.visible(bundle));
    } catch (err) {
      console.error("HB.views." + view.id + ".visible failed:", err);
      return false;
    }
  }

  function tabs() {
    return Object.keys(HB.views)
      .map((key) => HB.views[key])
      .filter((v) => v && typeof v === "object" && typeof v.id === "string" && typeof v.render === "function")
      .filter(shown)
      .sort((a, b) => (Number(a.order) || 0) - (Number(b.order) || 0) || collator.compare(String(a.label), String(b.label)));
  }

  function anyScored() {
    const cells = (bundle && bundle.cells) || {};
    return Object.keys(cells).some((row) => Object.keys(cells[row] || {}).some((q) => HB.data.isScored(cells[row][q])));
  }

  function defaultTab(list) {
    const stage = bundle && bundle.project ? bundle.project.stage : null;
    const want = stage === "setup" && !anyScored() ? "setup" : "grid";
    if (list.some((v) => v.id === want)) return want;
    return list.length ? list[0].id : null;
  }

  function cellOf(parts) {
    if (parts.length !== 2) return null;
    const [row, qid] = parts;
    return HB.data.unit(row) && HB.data.question(qid) ? { row, qid } : null;
  }

  function context() {
    return {
      bundle,
      data: HB.data,
      state: HB.state,
      route: { tab: current.tab, parts: current.parts.slice() },
      href,
      go,
      openCell,
      closeCell,
      setGroupBy,
      highlight,
      render,
      unitWords: HB.data.unitWords,
    };
  }

  /* ---- Drawing ----------------------------------------------------------------- */

  function drawHead() {
    const head = document.getElementById("hb-head");
    if (!head) return;
    const p = (bundle && bundle.project) || {};
    const facts = [];
    if (p.cutoff) {
      const when = new Date(String(p.cutoff) + "T00:00:00Z");
      facts.push(h("span", null, "Cut-off ", h("strong", null, Number.isNaN(when.getTime()) ? String(p.cutoff) : DATE.format(when))));
    }
    if (p.stage) {
      // Every must-have settled but nothing run yet: say so, so "Setting up" is never read as what blocks scoring.
      const ready = p.stage === "setup" && HB.nextStep && HB.nextStep.step().label !== "Finish set-up";
      facts.push(h("span", null, "Stage ", h("strong", null, ready ? "Ready to score" : STAGE_WORDS[p.stage] || String(p.stage))));
    }
    if (bundle) {
      const rows = Array.isArray(bundle.units) ? bundle.units.length : 0;
      facts.push(h("span", null, HB.data.unitWords(rows)));
    }
    head.replaceChildren(
      h("h1", { class: "hb-head__title" }, p.name ? String(p.name) : "Benchmark tool",
        p.edition && !String(p.name || "").includes(String(p.edition))      // "EggTrack 2025", not "EggTrack 2025 2025"
          ? [" ", h("span", { class: "hb-head__edition" }, String(p.edition))] : null),
      facts.length ? h("p", { class: "hb-head__facts" }, facts) : null,
      HB.nextStep ? HB.nextStep.node() : null,     // the next step, on the local page only (views/nextstep.js)
    );
  }

  /* The colour theme: always light (decided 11 Oct 2026), whatever the device prefers.
     data-theme="light" switches off the dark token blocks in styles.css, which stay for later. */
  function drawTheme() {
    document.documentElement.setAttribute("data-theme", "light");
    const box = document.getElementById("hb-theme");
    if (box) box.replaceChildren();
  }

  function refreshBanner() {
    const box = document.getElementById("hb-banner");
    if (!box) return;
    let n = 0;
    try {
      n = HB.edits && typeof HB.edits.pending === "function" ? Number(HB.edits.pending()) || 0 : 0;
    } catch (err) {
      console.error("HB.edits.pending failed:", err);
    }
    let deck = null;
    try {
      deck = typeof HB.deckBanner === "function" ? HB.deckBanner(current.tab) : null;
    } catch (err) {
      console.error("HB.deckBanner failed:", err);
    }
    // Saved edits still being applied float in a corner, so the page does not jump as each one comes
    // and goes (19 Oct 2026: a yellow banner per click "threw the page off").
    let toast = document.getElementById("hb-applying");
    if (!toast) {
      toast = h("div", { class: "hb-applying", id: "hb-applying", hidden: true });
      document.body.append(toast);
    }
    toast.hidden = !(n > 0);
    toast.replaceChildren(n > 0 ? HB.working(HB.when("Applying your " + (n === 1 ? "change" : fmtInt(n) + " changes"),
      fmtInt(n) + (n === 1 ? " change waits" : " changes wait") + " for Claude, which applies them the next time it works on this project")) : "");
    box.hidden = !deck;
    box.replaceChildren(deck || "");
  }

  function drawTabs(list) {
    const nav = document.getElementById("hb-tabs");
    if (!nav) return;
    nav.hidden = list.length === 0;
    nav.replaceChildren(
      ...list.map((v) => h(
        "a",
        { class: "tabs__tab", href: href(v.id), "aria-current": v.id === current.tab ? "page" : null },
        String(v.label || v.id),
      )),
    );
    const here = nav.querySelector('[aria-current="page"]');
    if (here && typeof here.scrollIntoView === "function" && nav.scrollWidth > nav.clientWidth) {
      here.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  function emptyState() {
    return h(
      "section",
      { class: "hb-empty" },
      h("h2", null, "No tabs to show yet"),
      h("p", { class: "muted" },
        "This page was built before any of its tabs were in place, so there is nothing to show here yet. " +
        "It fills in as Claude works on the project."),
    );
  }

  function drawMain() {
    const main = document.getElementById("main");
    if (!main) return;
    const view = current.tab ? tabs().find((v) => v.id === current.tab) || null : null;
    if (!bundle) {
      main.replaceChildren(notice("critical", [
        "This page holds no data it can read, so there is nothing to show.",
        HB.when("Try reloading the page.", "Claude builds it again the next time it works on this project."),
      ]));
      return;
    }
    if (!view) {
      main.replaceChildren(emptyState());
      return;
    }
    const root = h("div", { class: "hb-view", "data-view": view.id });
    main.replaceChildren(root);
    try {
      const made = view.render(root, context());
      if (made instanceof Node && !made.isConnected) root.append(made);
    } catch (err) {
      console.error("HB.views." + view.id + ".render failed:", err);
      root.replaceChildren(notice("critical", [
        "This tab could not be drawn: " + (err && err.message ? err.message : String(err)),
        "The other tabs still work.",
      ]));
    }
    // The run bar (views/runbar.js) puts itself under the heading of the tabs it belongs on.
    if (typeof HB.runBar === "function") {
      try {
        HB.runBar(view.id, root, context());
      } catch (err) {
        console.error("HB.runBar failed:", err);
      }
    }
    // What used to be each step's brief now lives on the chat button: a count of the few things worth the
    // owner's attention (HB.chat.badge, views/chat.js).
    if (HB.chat && typeof HB.chat.badge === "function") {
      try {
        HB.chat.badge();
      } catch (err) {
        console.error("HB.chat.badge failed:", err);
      }
    }
  }

  function syncCell() {
    const cell = HB.state.openCell;
    const key = cell ? cell.row + SEP + cell.qid : null;
    if (key === shownCell) return;
    if (!key || typeof HB.cellPanel !== "function") {
      shownCell = null;
      if (drawer.isOpen()) {
        quietClose = true;
        drawer.close();
        quietClose = false;
      }
      return;
    }
    let made = null;
    try {
      made = HB.cellPanel(cell.row, cell.qid, context());
    } catch (err) {
      console.error("HB.cellPanel failed:", err);
      made = { title: "This cell could not be shown", content: notice("critical", String(err && err.message ? err.message : err)) };
    }
    if (!made) return;
    shownCell = key;
    drawer.open(made.title, made.content, { kicker: made.kicker, onClose: drawerClosed });
  }

  function drawerClosed() {
    shownCell = null;
    if (quietClose) return;
    HB.state.openCell = null;
    if (openedHere) {
      openedHere = false;
      history.back();
    } else {
      replaceHash(href(current.tab));
    }
  }

  /** The project's own main colour (project.json "accent", set from the corner chat), or the page's
      own when there is none. The shades of the Grid and the charts follow it. */
  function applyAccent() {
    const p = (bundle && bundle.project) || {};
    const root = document.documentElement;
    if (typeof p.accent === "string" && /^#[0-9a-fA-F]{6}$/.test(p.accent)) {
      root.style.setProperty("--accent", p.accent);
      root.style.setProperty("--accent-soft", "color-mix(in srgb, " + p.accent + " 14%, var(--surface))");
    } else {
      root.style.removeProperty("--accent");
      root.style.removeProperty("--accent-soft");
    }
  }

  function render() {
    applyAccent();
    const list = tabs();
    drawHead();      // the next-step button follows what Claude is doing (views/nextstep.js)
    drawTabs(list);
    refreshBanner();
    drawMain();
    syncCell();
  }

  /* ---- Moving ------------------------------------------------------------------- */

  function route(moved) {
    const list = tabs();
    let r = parse(location.hash);
    if (r.tab === "documents" && list.some((v) => v.id === "grid")) {   // the Documents tab was folded into the Grid
      r = { tab: "grid", parts: [] };
      replaceHash(href("grid"));
    }
    if (r.tab && !list.some((v) => v.id === r.tab)) {
      if (!r.parts.length && document.getElementById(r.tab)) return;   // an in-page jump (the skip link)
      r = { tab: null, parts: [] };
    }
    if (!r.tab) r = { tab: defaultTab(list), parts: [] };
    const cell = cellOf(r.parts);
    if (!cell) openedHere = false;
    else if (moved && !HB.state.openCell) openedHere = true;   // a cell link on the page added this entry: closing goes back
    current = r;
    HB.state.openCell = cell;
    const key = cell ? r.tab : [r.tab, ...r.parts].join(SEP);
    if (key === screenKey) {
      syncCell();
      return;
    }
    screenKey = key;
    drawTabs(list);
    refreshBanner();
    drawMain();
    syncCell();
    if (moved) {
      const main = document.getElementById("main");
      if (main) main.focus({ preventScroll: true });
    }
  }

  function go(tab, ...parts) {
    const target = href(tab, ...parts);
    if (location.hash === target) render();
    else location.hash = target;
  }

  function openCell(row, qid) {
    openedHere = true;
    go("grid", row, qid);
  }

  function closeCell() {
    if (drawer.isOpen()) drawer.close();
    else if (HB.state.openCell) {
      HB.state.openCell = null;
      replaceHash(href(current.tab));
    }
  }

  function setGroupBy(label) {
    const labels = bundle && bundle.project && Array.isArray(bundle.project.labels) ? bundle.project.labels : [];
    if (label !== "" && !labels.includes(label)) return;
    HB.state.groupBy = label;
    store.set("group-by:" + (bundle.project.slug || ""), label);
    render();
  }

  function highlight(cells) {
    HB.state.highlight = Array.isArray(cells) ? cells.filter((c) => Array.isArray(c) && c.length === 2) : [];
    if (current.tab === "grid" && !HB.state.openCell) render();
    else go("grid");
  }

  function startGroupBy() {
    const p = (bundle && bundle.project) || {};
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const kept = store.get("group-by:" + (p.slug || ""), null);
    if (kept === "" || labels.includes(kept)) return kept;
    if (labels.includes(p.group_by)) return p.group_by;
    return labels.length ? labels[0] : null;
  }

  function start() {
    bundle = HB.data.bundle();
    HB.state.groupBy = startGroupBy();
    drawTheme();
    drawHead();
    route(false);
    window.addEventListener("hashchange", () => route(true));
    // The corner chat (views/chat.js): its button in #hb-chat, on every tab.
    if (HB.chat && typeof HB.chat.start === "function") {
      try {
        HB.chat.start();
      } catch (err) {
        console.error("HB.chat.start failed:", err);
      }
    }
  }

  /* After HB.data.replace() (hb serve's local.js, once the page is rebuilt): draw the head, the tab
     and an open cell again from the new bundle, keeping the reader's place on the page. */
  function reloadData() {
    bundle = HB.data.bundle();
    const y = window.scrollY;
    drawHead();
    shownCell = null;
    screenKey = null;
    route(false);
    window.scrollTo(0, y);
  }

  Object.assign(HB, {
    render,
    reloadData,
    href,
    go,
    openCell,
    closeCell,
    setGroupBy,
    highlight,
    refreshBanner,
    route: () => ({ tab: current.tab, parts: current.parts.slice() }),
    context,
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})((window.HB = window.HB || {}));
