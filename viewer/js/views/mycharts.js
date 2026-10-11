/* =============================================================================
   Handbook viewer: views/mycharts.js, "Your charts" on the Results tab.

   The owner asks Claude for a chart; Claude writes a Vega-Lite spec with no data
   and adds it with `hb chart add` (report/charts/<id>.json). The bundle carries
   them as results.charts: [{id, title, description, dataset, spec}]. This file
   builds the dataset each chart names from the bundle, loads Vega-Embed from
   cdnjs the first time a chart is drawn (pinned versions, checked with SRI), and
   draws each spec in the page's colours, with a table twin.

   HB.myCharts = {
     COLUMNS        {dataset: [column, ...]}: the same list as DATASETS in
                    handbook/lib/handbook/custom_charts.py (a test compares them).
                    "<label>", "<section>" and "band_<of>" stand for one column per
                    label, per score-sheet section (its percent) and per band set
                    (the band's name).
     columns(name)  a dataset's columns for this bundle, placeholders expanded
     dataset(name)  its rows, built from the bundle (cached per bundle):
                    rows     one per unit; section, overall and band columns are null
                             unless every question that applies is scored
                    cells    one per unit and question that applies; state, reason
                             and flags in the page's words
                    compare  one per unit and key compared with last edition
                    history  past editions' values ("past") and this run's ("this run")
     section(ctx)   the "Your charts" section for the Results summary
   }
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h } = HB;

  const COLUMNS = {
    "rows": ["row", "name", "<label>", "<section>", "overall", "band_<of>"],
    "cells": ["row", "name", "question", "state", "value", "points", "flags"],
    "compare": ["row", "name", "key", "ours", "theirs", "agree", "reason"],
    "history": ["row", "name", "key", "edition", "value", "source"]
  };

  // Copied from handbook/lib/handbook/bundle.py (STATE_WORDS, REASON_WORDS).
  const STATE_WORDS = { answered: "Answered", no_evidence: "No evidence", not_applicable: "Not applicable", not_run: "Not run" };
  const REASON_WORDS = {
    missing_document: "Missing document", different_document: "Different document", document_date: "Document date",
    rule_reading: "Rule reading", open_rule: "Undecided rule", unexplained: "Not yet explained",
  };

  const EMPTY = {
    rows: "Nothing to draw yet: the page lists no rows.",
    cells: "Nothing to draw yet: no question applies to any row.",
    compare: "Nothing to draw yet: this chart uses the comparison with last edition, which is not here yet.",
    history: "Nothing to draw yet: this chart uses past editions' results, which are not here yet.",
  };

  // Loaded on demand, in this order, from cdnjs. Exact versions (at least two weeks old when
  // pinned, 8 Oct 2026) with cdnjs's own SRI hashes, checked against the files.
  const VEGA = [
    { name: "vega", src: "https://cdnjs.cloudflare.com/ajax/libs/vega/6.4.0/vega.min.js",
      integrity: "sha512-liroOUtDzitgul3BVykJa+eaVI7LaAPY+R+CwBwOmZrJEkcHT7eMi6aFC2u6B7NQaosshIS/wM/yhkNAbj0lTA==" },
    { name: "vega-lite", src: "https://cdnjs.cloudflare.com/ajax/libs/vega-lite/6.4.3/vega-lite.min.js",
      integrity: "sha512-Tq8tvzbZ581gvQ426FLV3Aq2TnZrQt6TCuvqosm8aB5GJ8NviU6SKrwVkWcXUR7V6WlEtmf7HU8qUDxhQLMbQA==" },
    { name: "vega-embed", src: "https://cdnjs.cloudflare.com/ajax/libs/vega-embed/7.2.0/vega-embed.min.js",
      integrity: "sha512-GAEP5MOMNFx8ZCO1fF+o8ZYAq5harYephn5wEAPWbr53o8r/htJUHxsaOLg+SWJpSsNgVyPF5o2h+DmKtseKcg==" },
  ];

  // Categorical slots 3 to 8 of the dataviz reference palette (validated for light and dark);
  // slots 1 and 2 are the page's own --series-1 and --series-2, which match it.
  const MORE_SERIES = {
    light: ["#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    dark: ["#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  };
  const NO_DATA = ["data", "url", "datasets"];

  /* ---- Datasets ----------------------------------------------------------------- */

  function bundle() {
    return (HB.data && HB.data.bundle()) || {};
  }

  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const orNull = (v) => (v === undefined || v === "" ? null : v);
  const unique = (list) => Array.from(new Set(list));
  const has = (obj, key) => Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key);

  function parts(b) {
    const p = b.project || {};
    const sheet = b.scoresheet || {};
    const list = (x) => (Array.isArray(x) ? x : []);
    return {
      labels: list(p.labels).map(String),
      sections: list(sheet.sections).filter((s) => s && s.id).map((s) => String(s.id)),
      bands: list(sheet.bands).filter((s) => s && s.of).map((s) => String(s.of)),
    };
  }

  function columnsFor(b, name) {
    const x = parts(b);
    const out = [];
    (COLUMNS[name] || []).forEach((c) => {
      if (c === "<label>") out.push(...x.labels);
      else if (c === "<section>") out.push(...x.sections);
      else if (c === "band_<of>") out.push(...x.bands.map((of) => "band_" + of));
      else out.push(c);
    });
    return out;
  }

  function names(b) {
    const out = {};
    (b.units || []).forEach((u) => { if (u && u.id != null) out[u.id] = orNull(u.name); });
    return out;
  }

  function rowsSet(b) {
    const x = parts(b);
    const totals = b.totals || {};
    return (b.units || []).filter((u) => u && u.id != null).map((u) => {
      const t = totals[u.id];
      const whole = Boolean(t && !t.partial);
      const r = { row: u.id, name: orNull(u.name) };
      x.labels.forEach((label) => { r[label] = orNull((u.labels || {})[label]) ?? null; });
      x.sections.forEach((s) => {
        const sec = whole && t.sections ? t.sections[s] : null;
        r[s] = sec && isNum(sec.percent) ? sec.percent : null;
      });
      r.overall = whole && isNum(t.overall) ? t.overall : null;
      x.bands.forEach((of) => { r["band_" + of] = whole && t.bands ? orNull(t.bands[of]) ?? null : null; });
      return r;
    });
  }

  function cellsSet(b) {
    const who = names(b);
    const words = (HB.flagKinds && HB.flagKinds.words) || {};
    const out = [];
    (b.units || []).forEach((u) => {
      const cells = (b.cells || {})[u && u.id] || {};
      Object.keys(cells).forEach((qid) => {
        const c = cells[qid] || {};
        out.push({
          row: u.id, name: who[u.id], question: qid,
          state: STATE_WORDS[c.state] || orNull(c.state) || null,
          value: orNull(c.value) ?? null,
          points: isNum(c.points) ? c.points : null,
          flags: (Array.isArray(c.flags) ? c.flags : []).map((f) => words[f] || String(f)).join(", "),
        });
      });
    });
    return out;
  }

  function compareSet(b) {
    const cmp = (b.results || {}).compare;
    if (!cmp || !Array.isArray(cmp.rows)) return [];
    const who = names(b);
    const reasons = new Map();
    (cmp.disagreements || []).forEach((d) => { if (d) reasons.set(d.row + "\u0000" + d.key, d.reason); });
    const out = [];
    cmp.rows.forEach((r) => {
      if (!r) return;
      const ours = r.ours || {};
      const theirs = r.theirs || {};
      const agree = r.agree || {};
      unique([...Object.keys(theirs), ...Object.keys(ours)]).forEach((key) => {
        const reason = reasons.get(r.row + "\u0000" + key);
        out.push({
          row: r.row, name: has(who, r.row) ? who[r.row] : null, key,
          ours: orNull(ours[key]) ?? null, theirs: orNull(theirs[key]) ?? null,
          agree: typeof agree[key] === "boolean" ? agree[key] : null,
          reason: reason ? REASON_WORDS[reason] || String(reason) : null,
        });
      });
    });
    return out;
  }

  function historySet(b) {
    const cmp = (b.results || {}).compare;
    if (!cmp || typeof cmp !== "object") return [];
    const hist = cmp.history && typeof cmp.history === "object" ? cmp.history : {};
    const past = hist.rows && typeof hist.rows === "object" ? hist.rows : {};
    const latest = cmp.edition == null ? "Last edition" : String(cmp.edition);
    const editions = unique([...(Array.isArray(hist.editions) ? hist.editions.map(String) : []), latest]);
    const now = (b.project || {}).edition == null ? "This run" : String(b.project.edition);
    const compared = new Map();
    (Array.isArray(cmp.rows) ? cmp.rows : []).forEach((r) => { if (r) compared.set(r.row, r); });
    const who = names(b);
    const rows = unique([...(b.units || []).map((u) => u && u.id), ...compared.keys(), ...Object.keys(past)])
      .filter((row) => row != null && (compared.has(row) || has(past, row)));
    const out = [];
    rows.forEach((row) => {
      const r = compared.get(row) || {};
      const ours = r.ours || {};
      const theirs = r.theirs || {};
      const lines = past[row] && typeof past[row] === "object" ? past[row] : {};
      const name = has(who, row) ? who[row] : null;
      unique([...Object.keys(theirs), ...Object.keys(ours), ...Object.keys(lines)]).forEach((key) => {
        const line = lines[key] && typeof lines[key] === "object" ? lines[key] : {};
        editions.forEach((edition) => {
          let value = has(line, edition) ? line[edition] : undefined;
          if (value == null && edition === latest) value = theirs[key];
          if (value != null) out.push({ row, name, key, edition, value, source: "past" });
        });
        if (ours[key] != null) out.push({ row, name, key, edition: now, value: ours[key], source: "this run" });
      });
    });
    return out;
  }

  const BUILD = { rows: rowsSet, cells: cellsSet, compare: compareSet, history: historySet };
  const cache = { bundle: null, sets: {} };

  function dataset(name) {
    const b = bundle();
    if (cache.bundle !== b) {
      cache.bundle = b;
      cache.sets = {};
    }
    if (!BUILD[name]) return [];
    if (!cache.sets[name]) cache.sets[name] = BUILD[name](b);
    return cache.sets[name];
  }

  /* ---- Vega-Embed ----------------------------------------------------------------- */

  let loading = null;

  function loadScript(item) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = item.src;
      s.integrity = item.integrity;
      s.crossOrigin = "anonymous";
      s.referrerPolicy = "no-referrer";
      s.async = false;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("the chart library (" + item.name + ") did not load."));
      (document.head || document.documentElement).appendChild(s);
    });
  }

  function vegaEmbed() {
    if (typeof window.vegaEmbed === "function") return Promise.resolve(window.vegaEmbed);
    if (!loading) {
      loading = VEGA.reduce((p, item) => p.then(() => loadScript(item)), Promise.resolve()).then(() => {
        if (typeof window.vegaEmbed !== "function") throw new Error("the chart library did not start.");
        return window.vegaEmbed;
      });
    }
    return loading;
  }

  function isDark() {
    const set = document.documentElement.getAttribute("data-theme");
    if (set === "dark" || set === "light") return set === "dark";
    return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  /** A Vega-Lite config in the page's colours and type, read from the tokens. */
  function config(el) {
    const css = getComputedStyle(el || document.documentElement);
    const tok = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
    const ink = tok("--ink", "#16201c");
    const ink2 = tok("--ink-2", "#4b5752");
    const rule = tok("--rule", "#d7dcd5");
    const strong = tok("--rule-strong", "#7d8983");
    const surface = tok("--surface", "#ffffff");
    const font = tok("--sans", "system-ui, sans-serif");
    const series = [tok("--series-1", "#2a78d6"), tok("--series-2", "#eb6834")].concat(MORE_SERIES[isDark() ? "dark" : "light"]);
    const ramp = [0, 1, 2, 3].map((i) => tok("--step-" + i, ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"][i]));
    const text = { labelColor: ink2, titleColor: ink2, labelFont: font, titleFont: font, labelFontSize: 12,
      titleFontSize: 12, titleFontWeight: 600 };
    return {
      background: "transparent",
      font,
      padding: 4,
      view: { stroke: null },
      title: { color: ink, subtitleColor: ink2, font, subtitleFont: font, fontSize: 14, fontWeight: 600, anchor: "start" },
      axis: Object.assign({ domainColor: strong, tickColor: strong, gridColor: rule, labelLimit: 200 }, text),
      legend: Object.assign({}, text, { labelColor: ink, labelLimit: 200 }),
      header: Object.assign({}, text, { labelColor: ink }),
      range: { category: series, ordinal: ramp, ramp, heatmap: ramp },
      mark: { color: series[0], tooltip: { content: "encoding" } },
      bar: { stroke: surface, strokeWidth: 1 },
      rect: { stroke: surface, strokeWidth: 1 },
      arc: { stroke: surface, strokeWidth: 2 },
      line: { strokeWidth: 2 },
      point: { size: 64, filled: true },
      rule: { color: strong },
      text: { color: ink, font, fontSize: 12 },
    };
  }

  function loadsData(node) {
    if (Array.isArray(node)) return node.some(loadsData);
    if (!node || typeof node !== "object") return false;
    return Object.keys(node).some((k) => NO_DATA.includes(k) || loadsData(node[k]));
  }

  /** The chart's spec with the dataset as its data, sized to its box when it sets no width. */
  function prepare(source, rows) {
    const spec = JSON.parse(JSON.stringify(source || {}));
    if (loadsData(spec)) throw new Error("its spec tries to load data of its own.");
    spec.data = { values: rows.map((r) => Object.assign({}, r)) };
    if ((spec.mark != null || Array.isArray(spec.layer)) && spec.width == null) {
      spec.width = "container";
      if (spec.autosize == null) spec.autosize = { type: "fit-x", contains: "padding" };
    }
    return spec;
  }

  const drawn = [];   // {el, chart, rows, result}: charts on screen, redrawn when the theme changes

  function forget(i) {
    const d = drawn[i];
    drawn.splice(i, 1);
    try {
      if (d.result && typeof d.result.finalize === "function") d.result.finalize();
    } catch (err) {
      /* already gone */
    }
  }

  function draw(el, chart, rows) {
    el.replaceChildren(h("p", { class: "small muted" }, "Drawing the chart..."));
    return vegaEmbed().then((embed) => {
      const spec = prepare(chart.spec, rows);
      const target = h("div", { class: "mc-vega" });
      el.replaceChildren(target);
      return embed(target, spec, {
        mode: "vega-lite", renderer: "svg", actions: false, config: config(el),
        tooltip: { theme: isDark() ? "dark" : "light" },
        logLevel: window.vega && typeof window.vega.Error === "number" ? window.vega.Error : 1,   // errors only
      });
    }).then((result) => {
      for (let i = drawn.length - 1; i >= 0; i -= 1) if (drawn[i].el === el || !drawn[i].el.isConnected) forget(i);
      drawn.push({ el, chart, rows, result });
    }).catch((err) => {
      const why = err && err.message ? String(err.message) : "the chart library failed.";
      el.replaceChildren(HB.notice("warning", "This chart could not be drawn here: " + why.replace(/\.?$/, ".") +
        " Show it as a table below."));
    });
  }

  let watching = false;
  function watchTheme() {
    if (watching) return;
    watching = true;
    const redraw = () => drawn.slice().forEach((d) => { if (d.el.isConnected) draw(d.el, d.chart, d.rows); });
    try {
      new MutationObserver(redraw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
      if (mq && mq.addEventListener) mq.addEventListener("change", redraw);
    } catch (err) {
      /* no theme following: the charts keep the colours they were drawn with */
    }
  }

  /* ---- The section ------------------------------------------------------------------ */

  function tableFor(chart, rows) {
    const cols = columnsFor(bundle(), chart.dataset);
    const order = ["name", ...cols.filter((c) => c !== "name")];
    return HB.countedTable({
      columns: order.map((c) => ({
        label: c, key: c, rowHeader: c === "name",
        num: rows.some((r) => isNum(r[c])) && rows.every((r) => r[c] == null || isNum(r[c])),
      })),
      rows,
      expected: { n: rows.length, label: ["line", "lines"] },
      shownLabel: ["line", "lines"],
      copyName: String(chart.title || chart.id || "Chart") + " as a table",
      freezeFirst: true,
    });
  }

  function figure(chart) {
    const titleId = HB.uid("mc");
    const fig = h("figure", { class: "figure chart-figure mc-figure", "aria-labelledby": titleId },
      h("div", { class: "figure__head" }, h("h4", { class: "figure__title", id: titleId }, String(chart.title || chart.id || "Chart"))));
    if (chart.description) fig.append(h("p", { class: "figure__caption mc-desc" }, String(chart.description)));
    if (!BUILD[chart.dataset] || !chart.spec || typeof chart.spec !== "object") {
      fig.append(HB.notice("warning", "This chart names a dataset the page does not know, so it cannot be drawn."));
      return fig;
    }
    const rows = dataset(chart.dataset);
    if (!rows.length) {
      fig.append(h("p", { class: "chart-empty" }, EMPTY[chart.dataset]));
      return fig;
    }
    const box = h("div", { class: "chart mc-chart" });
    let table = null;
    const button = h("button", { type: "button", class: "btn btn--small" }, "Show as table");
    const foot = h("div", { class: "chart-foot" }, button);
    button.addEventListener("click", () => {
      if (!table) {
        table = tableFor(chart, rows);
        table.hidden = true;
        foot.before(table);
      }
      const showTable = table.hidden;
      table.hidden = !showTable;
      box.hidden = showTable;
      button.textContent = showTable ? "Show as chart" : "Show as table";
    });
    fig.append(box, foot);
    draw(box, chart, rows);
    return fig;
  }

  function section() {
    const b = bundle();
    const charts = (Array.isArray((b.results || {}).charts) ? b.results.charts : []).filter((c) => c && typeof c === "object");
    const p = b.project || {};
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const label = String(labels.includes(p.group_by) ? p.group_by : labels[0] || "group").replace(/_/g, " ");
    const parts = [
      h("div", { class: "section__head" }, h("h3", { id: "res-mycharts" }, "Your charts")),
      h("p", { class: "small muted mc-how" }, HB.when("Ask for a chart with the Claude button (bottom right), for example ",
          "Charts Claude makes for you appear here, for example "),
        h("q", null, "the overall score by " + label),
        ". Claude draws each one from the figures on this page."),
    ];
    if (!charts.length) parts.push(HB.notice("neutral", "No charts yet."));
    else {
      parts.push(h("div", { class: "stack stack--loose" }, charts.map(figure)));
      watchTheme();
    }
    return h("section", { class: "section", "aria-labelledby": "res-mycharts" }, parts);
  }

  HB.addStyles("mycharts", `
    .mc-how { max-width: var(--measure); }
    .mc-chart { min-height: 4rem; }
    .mc-chart .vega-embed { display: block; width: 100%; }
    .mc-desc { font-size: var(--fs-14); }
  `);

  HB.myCharts = {
    COLUMNS,
    columns: (name) => columnsFor(bundle(), name),
    dataset,
    section,
  };
})((window.HB = window.HB || {}));
