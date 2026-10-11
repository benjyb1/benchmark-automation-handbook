/* =============================================================================
   Handbook viewer: the Results tab (HB.views.results).

   Two screens behind one small switcher beside the heading: "Summary" (#results,
   the default) and "Compare with last edition" (#results~compare and
   #results~compare~<view>, only when the bundle has a comparison; compare.js
   draws it through HB.compareView.render).
   Summary: Claude's report, drawn from a small, safe markdown subset (with the
   Generate report panel, HB.reportDraft); three charts, each with its table; the
   owner's own charts (HB.myCharts.section); then the downloads (HB.downloads.panel).

   Also here, for compare.js and any later view:
   HB.charts.stacked(opts)          100% stacked bars, one bar per row, with a
                                    legend, a tooltip per bar, labels inside wide
                                    segments and a table twin (countedTable).
     opts.rows   [{label, parts: [{label, value, tone}], note?}]; a bar's total is
                 the sum of its parts.
     opts.keys   the legend: [{label, tone}]. tone: a CSS colour from the tokens
                 (see HB.resultsParts.step), "none" for the neutral hatch, or an
                 array of them for one key that covers several steps.
     opts.title, opts.caption, opts.unit ({one, many}), opts.labelHeader,
     opts.partHeader.
   HB.resultsParts.markdown(text)    Nodes for the report (headings, paragraphs,
                                    lists, bold, italic, links; all else is text).
   HB.resultsParts.step(i, n)       {fill, ink} for step i of n on the ordinal ramp.
   HB.resultsParts.bandSets()       the score sheet's band sets, best band first.
   HB.resultsParts.keyName(key)     "Commitments (P1)", "Overall score", "Overall
                                    tier", "Question 1.1, Welfare policy".
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, isNum, fmtInt, plural } = HB;
  const COLLATOR = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });
  const SPLIT_KEY = "results-split";
  const NONE = "none";

  /* ---- Small helpers ------------------------------------------------------------ */

  function bundle() {
    return HB.data.bundle() || {};
  }

  function unitNouns() {
    const p = bundle().project || {};
    const one = String(p.unit_word || "row");
    return { one, many: String(p.unit_word_plural || one + "s") };
  }

  function pct(part, whole) {
    if (!(whole > 0)) return "–";
    return HB.fmtPct(part, whole);
  }

  /** Step i of n on the ordinal ramp (0 the lowest). Up to four steps use the
      ramp's own colours; more are mixed between neighbours. */
  function step(i, n) {
    const pos = n <= 1 ? 3 : (i * 3) / (n - 1);
    const near = Math.round(pos);
    if (n <= 4 || Number.isInteger(pos)) return { fill: `var(--step-${near})`, ink: `var(--on-step-${near})` };
    const lo = Math.floor(pos);
    const share = Math.round((pos - lo) * 100);
    return { fill: `color-mix(in oklab, var(--step-${lo + 1}) ${share}%, var(--step-${lo}))`, ink: `var(--on-step-${near})` };
  }

  /** Band sets from the score sheet, each sorted best (highest min) first. */
  function bandSets() {
    const sheet = bundle().scoresheet;
    if (!sheet || sheet.computable === false || !Array.isArray(sheet.bands)) return [];
    return sheet.bands
      .filter((set) => set && set.of && Array.isArray(set.bands) && set.bands.length)
      .map((set) => ({ of: set.of, bands: set.bands.slice().sort((a, b) => (b.min || 0) - (a.min || 0)) }));
  }

  /** The word the bands share ("Tier 1", "Tier 2" give "tier"), else "band". */
  function bandWord(set) {
    const firsts = (set ? set.bands : []).map((b) => String(b.name || "").trim().split(/\s+/)[0]);
    const word = firsts[0];
    return word && /^[A-Za-z]{3,}$/.test(word) && firsts.every((w) => w === word) ? word.toLowerCase() : "band";
  }

  function sectionName(id) {
    const sheet = bundle().scoresheet || {};
    const s = (sheet.sections || []).find((x) => x && x.id === id);
    return s && s.name ? `${s.name} (${id})` : String(id);
  }

  function keyName(key) {
    if (key === "overall") return "Overall score";
    if (typeof key === "string" && key.startsWith("band:")) {
      const of = key.slice(5);
      const set = bandSets().find((s) => s.of === of);
      const word = bandWord(set);
      return of === "overall" ? `Overall ${word}` : `${sectionName(of)} ${word}`;
    }
    const sheet = bundle().scoresheet || {};
    if ((sheet.sections || []).some((s) => s && s.id === key)) return sectionName(key);
    const q = HB.data.question(key);
    return q ? `Question ${key}${q.short ? ", " + q.short : ""}` : String(key);
  }

  /* ---- Markdown: a small safe subset ------------------------------------------- */

  const INLINE = /\*\*(.+?)\*\*|__(.+?)__|\[([^\]\n]+)\]\(\s*([^)\s]+)\s*\)|\*(?!\s)([^*\n]+?)\*|(?<![\w])_(?!\s)([^_\n]+?)_(?![\w])/g;

  function inline(text) {
    const out = [];
    let last = 0;
    String(text).replace(INLINE, (match, b1, b2, linkText, href, i1, i2, at) => {
      if (at > last) out.push(text.slice(last, at));
      if (b1 != null || b2 != null) out.push(h("strong", null, inline(b1 != null ? b1 : b2)));
      else if (linkText != null) out.push(HB.link(href, linkText));
      else out.push(h("em", null, inline(i1 != null ? i1 : i2)));
      last = at + match.length;
      return match;
    });
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  function markdown(source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const nodes = [];
    let para = [];
    let list = null;   // {kind: "ul" | "ol", items: [[text, ...]]}
    const flushPara = () => {
      if (para.length) nodes.push(h("p", null, inline(para.join(" "))));
      para = [];
    };
    const flushList = () => {
      if (list) nodes.push(h(list.kind, null, list.items.map((parts) => h("li", null, inline(parts.join(" "))))));
      list = null;
    };
    lines.forEach((raw) => {
      const line = raw.replace(/\s+$/, "");
      const heading = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      const number = /^\s*\d{1,3}[.)]\s+(.*)$/.exec(line);
      if (!line.trim()) {
        flushPara();
        flushList();
      } else if (heading) {
        flushPara();
        flushList();
        const level = Math.min(heading[1].length + 3, 6);   // under the tab's h2 and the "Report" h3
        nodes.push(h("h" + level, null, inline(heading[2])));
      } else if (bullet || number) {
        flushPara();
        const kind = bullet ? "ul" : "ol";
        if (list && list.kind !== kind) flushList();
        if (!list) list = { kind, items: [] };
        list.items.push([(bullet || number)[1]]);
      } else if (list && /^\s+\S/.test(raw)) {
        list.items[list.items.length - 1].push(line.trim());   // a wrapped list item
      } else {
        flushList();
        para.push(line.trim());
      }
    });
    flushPara();
    flushList();
    return nodes;
  }

  /* ---- Stacked bars -------------------------------------------------------------- */

  function swatch(tone) {
    if (Array.isArray(tone)) return tone.map(swatch);
    return tone === NONE
      ? h("span", { class: "swatch stk-hatch", "aria-hidden": "true" })
      : h("span", { class: "swatch", style: { "--swatch": tone }, "aria-hidden": "true" });
  }

  // One page-wide scroll listener hides whichever stacked-bar tooltip is showing.
  let hideOpenTip = null;
  let watching = false;
  function watchScroll() {
    if (watching) return;
    document.addEventListener("scroll", () => { if (hideOpenTip) hideOpenTip(); }, { capture: true, passive: true });
    watching = true;
  }

  function stacked(opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter(Boolean);
    const titleId = HB.uid("stk");
    const head = o.title ? h("div", { class: "figure__head" }, h("h3", { class: "figure__title", id: titleId }, o.title)) : null;
    const fig = h("figure", { class: "figure chart-figure", "aria-labelledby": o.title ? titleId : null }, head);
    const caption = o.caption ? h("figcaption", { class: "figure__caption" }, o.caption) : null;
    if (!rows.length) {
      fig.append(...[h("p", { class: "chart-empty" }, o.empty || "Nothing to chart yet."), caption].filter(Boolean));
      return fig;
    }
    const nouns = o.unit || unitNouns();
    const count = (n) => plural(n, nouns.one, nouns.many);
    const total = (r) => r.parts.reduce((sum, p) => sum + (isNum(p.value) ? p.value : 0), 0);

    const legend = h("ul", { class: "legend", "aria-label": "Key" },
      (o.keys || []).map((k) => h("li", { class: "legend__item" }, swatch(k.tone), k.label)));

    const tip = h("div", { class: "tooltip chart-tip", "aria-hidden": "true", hidden: true });
    const tipFor = (r) => {
      const t = total(r);
      tip.replaceChildren(h("p", { class: "tooltip__title" }, r.label),
        ...r.parts.filter((p) => p.value > 0).map((p) => h("p", { class: "chart-tip__row" },
          h("strong", null, `${fmtInt(p.value)} (${pct(p.value, t)})`), " ", p.label)),
        h("p", { class: "chart-tip__hint" }, `Out of ${count(t)}`));
      if (r.note) tip.appendChild(h("p", { class: "chart-tip__row" }, r.note));
    };
    const place = (x, y) => {
      if (hideOpenTip && hideOpenTip !== hide) hideOpenTip();
      hideOpenTip = hide;
      tip.hidden = false;
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      const left = x + 14 + tip.offsetWidth > vw - 8 ? x - 14 - tip.offsetWidth : x + 14;
      const top = y + 14 + tip.offsetHeight > vh - 8 ? y - 14 - tip.offsetHeight : y + 14;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = Math.max(8, top) + "px";
    };
    const hide = () => { tip.hidden = true; };

    const list = h("div", { class: "stk", role: "list", "aria-label": o.title || "Stacked bars" });
    const rowEls = rows.map((r, k) => {
      const t = total(r);
      let at = 0;
      const segs = r.parts.filter((p) => p.value > 0).map((p, i, shown) => {
        const left = (at / t) * 100;
        const width = (p.value / t) * 100;
        at += p.value;
        const lastOne = i === shown.length - 1;
        const style = {
          left: left + "%",
          width: lastOne ? width + "%" : `max(1px, calc(${width}% - 2px))`,
          background: p.tone === NONE ? null : p.tone,
          color: p.ink || null,
        };
        return h("span", { class: ["stk__seg", p.tone === NONE && "stk-hatch", lastOne && "stk__seg--end"], style },
          width >= 16 && p.tone !== NONE ? h("span", { class: "stk__pct", "aria-hidden": "true" }, pct(p.value, t)) : null);
      });
      const words = r.parts.filter((p) => p.value > 0).map((p) => `${p.label} ${fmtInt(p.value)}`).join(", ");
      return h("div", { class: "stk__row", role: "listitem", tabindex: k === 0 ? "0" : "-1" },
        h("span", { class: "stk__label" }, r.label),
        h("span", { class: "stk__track" }, t > 0 ? segs : h("span", { class: "stk__none" }, "None yet")),
        h("span", { class: "visually-hidden" }, `: ${words || "none"}, out of ${count(t)}.`));
    });
    list.append(...rowEls,
      h("div", { class: "stk__axis", "aria-hidden": "true" }, ["0%", "50%", "100%"].map((x) => h("span", null, x))));

    const rowOf = (target) => (target && target.closest ? target.closest(".stk__row") : null);
    list.addEventListener("pointerover", (e) => {
      const row = rowOf(e.target);
      if (!row) return hide();
      tipFor(rows[rowEls.indexOf(row)]);
      place(e.clientX, e.clientY);
    });
    list.addEventListener("pointermove", (e) => { if (!tip.hidden) place(e.clientX, e.clientY); });
    list.addEventListener("pointerleave", hide);
    list.addEventListener("focusin", (e) => {
      const k = rowEls.indexOf(e.target);
      if (k < 0) return;
      rowEls.forEach((el, i) => el.setAttribute("tabindex", i === k ? "0" : "-1"));
      // One frame later, so the scroll that brought the row into view doesn't hide its tooltip.
      requestAnimationFrame(() => {
        if (document.activeElement !== rowEls[k]) return;
        tipFor(rows[k]);
        const box = rowEls[k].querySelector(".stk__track").getBoundingClientRect();
        place(box.left, box.bottom - 6);
      });
    });
    list.addEventListener("focusout", (e) => { if (!list.contains(e.relatedTarget)) hide(); });
    list.addEventListener("keydown", (e) => {
      const k = rowEls.indexOf(e.target);
      const to = { ArrowDown: k + 1, ArrowRight: k + 1, ArrowUp: k - 1, ArrowLeft: k - 1, Home: 0, End: rowEls.length - 1 }[e.key];
      if (k < 0 || to == null) return;
      e.preventDefault();
      rowEls[Math.max(0, Math.min(rowEls.length - 1, to))].focus();
    });
    watchScroll();

    const chart = h("div", { class: "chart chart--stk" }, list);
    let twin = null;
    const button = h("button", { type: "button", class: "btn btn--small" }, "Show as table");
    const foot = h("div", { class: "chart-foot" }, button);
    button.addEventListener("click", () => {
      if (!twin) {
        const flat = [];
        rows.forEach((r) => {
          const t = total(r);
          r.parts.forEach((p) => flat.push({ bar: r.label, part: p.label, value: p.value || 0, total: t }));
        });
        twin = HB.countedTable({
          columns: [
            { label: o.labelHeader || "Name", key: "bar", rowHeader: true },
            { label: o.partHeader || "Part", key: "part" },
            { label: nouns.many.charAt(0).toUpperCase() + nouns.many.slice(1), key: "value", num: true },
            { label: "Share", value: (x) => (x.total > 0 ? x.value / x.total : null), format: (v, x) => pct(x.value, x.total), num: true },
            { label: "Out of", key: "total", num: true },
          ],
          rows: flat,
          expected: { n: rows.length, label: ["bar", "bars"] },
          shownLabel: ["line", "lines"],
          copyName: (o.title || "Chart") + " as a table",
          freezeFirst: true,
        });
        twin.hidden = true;
        foot.before(twin);
      }
      const showTable = twin.hidden;
      twin.hidden = !showTable;
      legend.hidden = showTable;
      chart.hidden = showTable;
      button.textContent = showTable ? "Show as chart" : "Show as table";
      hide();
    });
    fig.append(...[legend, chart, foot, tip, caption].filter(Boolean));
    return fig;
  }

  HB.charts = HB.charts || {};
  HB.charts.stacked = stacked;

  /* ---- Summary --------------------------------------------------------------------- */

  function completeRows() {
    const b = bundle();
    const totals = b.totals || {};
    return (b.units || []).filter((u) => {
      const t = totals[u.id];
      return t && !t.partial && isNum(t.overall);
    });
  }

  function reportSection() {
    const text = (bundle().results || {}).report;
    const body = typeof text === "string" && text.trim()
      ? h("article", { class: "prose res-report" }, markdown(text))
      : null;   // no report yet: the Generate report button below says enough
    const draft = HB.reportDraft || null;   // views/reportdraft.js: out-of-date notice, Generate report
    return h("section", { class: "section", "aria-labelledby": "res-report" },
      h("div", { class: "section__head" }, h("h3", { id: "res-report" }, "Report")),
      draft ? draft.staleNotice() : null, body || (draft ? null : h("p", { class: "muted" }, "No report yet.")), draft ? draft.panel() : null);
  }

  function bandsByLabel(set, units, ctx, rerender) {
    const p = bundle().project || {};
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const saved = HB.store.get(SPLIT_KEY, null);
    const label = [saved, ctx.state.groupBy, p.group_by].find((x) => x === "" || labels.includes(x)) ?? "";
    const word = bandWord(set);
    const totals = bundle().totals || {};
    const parts = (list) => set.bands.map((b, i) => {
      const tone = step(set.bands.length - 1 - i, set.bands.length);
      return { label: b.name, value: list.filter((u) => ((totals[u.id] || {}).bands || {})[set.of] === b.name).length,
        tone: tone.fill, ink: tone.ink };
    });
    const nouns = unitNouns();
    const rows = [{ label: `All ${nouns.many}`, parts: parts(units) }];
    if (label) {
      const values = HB.data.labelValues(label);
      values.forEach((v) => {
        const these = units.filter((u) => String((u.labels || {})[label]) === v);
        if (these.length) rows.push({ label: v, parts: parts(these) });
      });
      const unlabelled = units.filter((u) => (u.labels || {})[label] == null || (u.labels || {})[label] === "");
      if (unlabelled.length) rows.push({ label: `No ${label.replace(/_/g, " ")}`, parts: parts(unlabelled) });
    }
    const select = h("select", { class: "input", id: "res-split" },
      h("option", { value: "", selected: label === "" }, "Nothing (all together)"),
      labels.map((lb) => h("option", { value: lb, selected: lb === label }, lb.replace(/_/g, " "))));
    select.addEventListener("change", () => {
      HB.store.set(SPLIT_KEY, select.value);
      rerender();
    });
    const field = h("div", { class: "field res-field" }, h("label", { class: "field__label", for: "res-split" }, "Split by"), select);
    const of = set.of === "overall" ? "overall score" : sectionName(set.of);
    return h("div", { class: "stack" }, field, stacked({
      title: `Share of ${nouns.many} in each ${word}${label ? ", by " + label.replace(/_/g, " ") : ""}`,
      rows,
      keys: rows[0].parts.map((x) => ({ label: x.label, tone: x.tone })),
      labelHeader: label ? label.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Group",
      partHeader: word.charAt(0).toUpperCase() + word.slice(1),
      caption: `Each bar is 100% of its ${nouns.many} with every question scored, split by the ${word} of their ${of}. ` +
        `The best ${word} is on the left.`,
    }));
  }

  function bandCounts(set, units) {
    const totals = bundle().totals || {};
    const nouns = unitNouns();
    const word = bandWord(set);
    const rows = set.bands.map((b) => ({
      label: b.name,
      value: units.filter((u) => ((totals[u.id] || {}).bands || {})[set.of] === b.name).length,
    }));
    const none = units.length - rows.reduce((s, r) => s + r.value, 0);
    if (none > 0) rows.push({ label: `No ${word}`, value: none, note: `The score is below every ${word}'s minimum.` });
    return HB.charts.bars({
      title: `${nouns.many.charAt(0).toUpperCase() + nouns.many.slice(1)} in each ${word}`,
      rows,
      unit: nouns,
      labelHeader: word.charAt(0).toUpperCase() + word.slice(1),
      caption: `Counts of ${nouns.many} with every question scored, best ${word} first. ` +
        (set.bands.map((b) => `${b.name} from ${fmtInt(b.min)}`).join(", ")) + ".",
    });
  }

  function perQuestion() {
    const b = bundle();
    const units = b.units || [];
    const cells = b.cells || {};
    const nouns = unitNouns();
    const rows = (b.questions || []).map((q) => {
      const levels = (((q.answer || {}).levels) || []).slice().sort((x, y) => (x.points || 0) - (y.points || 0));
      const counts = levels.map(() => 0);
      let notRun = 0;
      units.forEach((u) => {
        const c = (cells[u.id] || {})[q.id];
        if (!c || c.state === "not_applicable") return;
        if (!HB.data.isScored(c)) { notRun += 1; return; }
        const i = levels.findIndex((l) => String(l.value) === String(c.value));
        if (i >= 0) counts[i] += 1;
      });
      const parts = levels.map((l, i) => {
        const tone = step(i, levels.length);
        const meaning = l.meaning ? `: ${l.meaning}` : "";
        return { label: `${String(l.value).replace(/^./, (c) => c.toUpperCase())} (${plural(l.points || 0, "point", "points")})${meaning}`,
          value: counts[i], tone: tone.fill, ink: tone.ink };
      }).reverse();
      parts.push({ label: "Not scored yet", value: notRun, tone: NONE });
      return { label: `${q.id}${q.short ? " " + q.short : ""}`, parts };
    }).filter((r) => r.parts.some((p) => p.value > 0));
    return stacked({
      title: "Answers to each question",
      rows,
      keys: [{ label: "Most points", tone: "var(--step-3)" }, { label: "Middle levels", tone: ["var(--step-2)", "var(--step-1)"] },
        { label: "Fewest points", tone: "var(--step-0)" }, { label: "Not scored yet", tone: NONE }],
      unit: nouns,
      labelHeader: "Question",
      partHeader: "Answer",
      empty: "No question applies to any row yet.",
      caption: `One bar per question, out of the ${nouns.many} it applies to. The level with the most points is on the ` +
        "left, darker in light mode and brighter in dark mode. The table names every level.",
    });
  }

  function chartsSection(ctx, rerender) {
    const units = (bundle().units || []);
    const complete = completeRows();
    const nouns = unitNouns();
    const sets = bandSets();
    const parts = [h("div", { class: "section__head" }, h("h3", { id: "res-charts" }, "Scores at a glance"))];
    parts.push(h("p", { class: "small muted" },
      `${fmtInt(complete.length)} of ${plural(units.length, nouns.one, nouns.many)} have every question scored. ` +
      "The charts of scores use only those; the chart of answers uses every scored answer."));
    if (!sets.length) {
      parts.push(HB.notice("neutral", "The score sheet has no bands, or scores can't be worked out yet, so there is no chart of bands."));
    } else if (!complete.length) {
      parts.push(HB.notice("neutral", `No ${nouns.one} has every question scored yet, so there is no chart of bands.`));
    } else {
      const set = sets.find((s) => s.of === "overall") || sets[0];
      parts.push(h("div", { class: "res-pair" }, bandCounts(set, complete), bandsByLabel(set, complete, ctx, rerender)));
    }
    parts.push(perQuestion());
    return h("section", { class: "section", "aria-labelledby": "res-charts" }, parts);
  }

  function summary(root, ctx) {
    const rerender = () => {
      const fresh = h("div", { class: "stack stack--loose" });
      drawSummary(fresh, ctx, rerender);
      box.replaceWith(fresh);
      box = fresh;
    };
    let box = h("div", { class: "stack stack--loose" });
    drawSummary(box, ctx, rerender);
    root.appendChild(box);
  }

  function drawSummary(box, ctx, rerender) {
    box.append(...[reportSection(), chartsSection(ctx, rerender), HB.myCharts ? HB.myCharts.section(ctx) : null,
      HB.downloads ? HB.downloads.panel(ctx) : null].filter(Boolean));
  }

  /* ---- The tab ---------------------------------------------------------------------- */

  function render(root, ctx) {
    const b = ctx.bundle || bundle();
    const hasCompare = Boolean(b.results && b.results.compare && HB.compareView);
    const onCompare = hasCompare && ctx.route.parts[0] === "compare";
    const top = h("div", { class: "res-top" }, h("h2", null, "Results"));
    root.appendChild(top);
    if (hasCompare) {
      // The comparison opens on the reader's last view (compare.js keeps it).
      const pick = (compare) => h("button", {
        type: "button",
        class: "btn btn--small",
        "aria-pressed": compare === onCompare ? "true" : "false",
        onclick: () => (compare ? (onCompare ? null : ctx.go("results", "compare")) : ctx.go("results")),
      }, compare ? "Compare with last edition" : "Summary");
      top.appendChild(h("div", { class: "segmented res-switch", role: "group", "aria-label": "Results view" }, pick(false), pick(true)));
    }
    if (onCompare) HB.compareView.render(root, ctx);
    else summary(root, ctx);
  }

  HB.addStyles("results", `
    .res-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--s2) var(--s4); }
    .res-top > h2 { margin: 0; }
    .res-switch { width: fit-content; max-width: 100%; }
    .res-switch > .btn { min-height: 1.875rem; }
    .res-report { max-width: var(--measure); }
    .res-report h4, .res-report h5, .res-report h6 { font-family: var(--serif); text-wrap: balance; }
    .res-report h4 { font-size: var(--fs-20); }
    .res-field { max-width: 20rem; }
    .res-pair { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: var(--s6); }
    @media (min-width: 64rem) { .res-pair { grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); } }

    .chart--stk { position: relative; }
    .stk { display: grid; grid-template-columns: fit-content(40%) minmax(0, 1fr); column-gap: var(--s3);
      font-size: var(--fs-14); line-height: 1.35; }
    .stk__row { grid-column: 1 / -1; display: grid; grid-template-columns: subgrid; align-items: center;
      min-height: 2rem; padding-block: 0.1875rem; border-radius: var(--radius);
      transition: background-color var(--dur-fast) var(--ease); }
    .stk__row:hover, .stk__row:focus-visible { background: color-mix(in srgb, var(--ink) 5%, transparent); }
    .stk__row:focus-visible { outline-offset: 0; }
    .stk__label { min-width: 0; padding-left: var(--s2); color: var(--ink); text-wrap: pretty; overflow-wrap: anywhere; }
    .stk__track { position: relative; align-self: stretch; min-height: 1.25rem; margin-right: var(--s2);
      box-shadow: inset 1px 0 0 var(--rule-strong); }
    .stk__seg { position: absolute; top: 50%; height: 1.125rem; transform: translateY(-50%);
      display: flex; align-items: center; justify-content: center; overflow: hidden; }
    .stk__seg--end { border-radius: 0 4px 4px 0; }
    .stk__pct { font-size: var(--fs-12); font-weight: 600; font-variant-numeric: lining-nums tabular-nums; white-space: nowrap; }
    .stk__none { position: absolute; top: 50%; left: var(--s2); transform: translateY(-50%); color: var(--ink-2); font-size: var(--fs-12); }
    .stk-hatch { background: repeating-linear-gradient(45deg, var(--rule-strong) 0 1.5px, var(--surface) 1.5px 5px);
      box-shadow: inset 0 0 0 1px var(--rule-strong); }
    .swatch.stk-hatch { background: repeating-linear-gradient(45deg, var(--rule-strong) 0 1.5px, var(--surface) 1.5px 4px); }
    .stk__axis { grid-column: 2; display: flex; justify-content: space-between; margin: var(--s1) var(--s2) 0 0;
      color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
    @media (prefers-reduced-motion: reduce) { .stk__row { transition: none; } }
  `);

  HB.resultsParts = { markdown, step, bandSets, bandWord, keyName, sectionName, unitNouns, completeRows };

  (HB.views = HB.views || {}).results = {
    id: "results",
    label: "Results",
    order: 60,
    render,
    visible: () => true,
  };
})((window.HB = window.HB || {}));
