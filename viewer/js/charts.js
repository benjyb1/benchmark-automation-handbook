/* =============================================================================
   Handbook viewer: charts.js.
   Copied from frontend/src/js/charts.js (AYB renamed HB; the flow chart left out).

   Seven builders on HB.charts. Each takes plain data from the calling view (it
   never reads the bundle) and returns a <figure> Node with:
     - a "Show as table" button under the chart that swaps in a table twin
       (not crossTable, which is a table already);
     - colours from styles.css tokens only: the markup here carries classes, and
       the "Charts" section of styles.css gives them their colours.
   bars, scatterRanges and the two grids also have a tooltip on every mark, on
   hover and on keyboard focus alike, and one tab stop each: the arrow keys move
   between marks.
   Each tooltip lives inside its own figure (position: fixed), so a chart drawn
   inside the modal drawer keeps its tooltip above the page. At most one shows
   at a time, and any scroll or resize hides it.

   Options every builder takes: title (an h3 above the chart), caption (a
   figcaption under it), label (the accessible name when there is no title).

   bars({rows, unit, sort, labelHeader, max})
     rows   [{label, value, total?, note?}]. When every row has a total, each bar
            is value / total on a 0-100% axis ("56 of 63" in the tooltip); when
            none has, bars are counts on an axis from 0 to a round maximum. Some
            rows with a total and some without throws a TypeError: the two
            cannot share one axis.
     unit   noun for what value counts: "answers", or {one: "country", many:
            "countries"} so that 1 reads "1 country".
     sort   "value" (largest first; true means the same), "label", or leave it
            out to keep the order given.
     max    the top of a count axis when it should reach past the largest bar.
   scatterRanges({points, xLabel, yLabel, max, onPick, rangeLabel, labelHeader,
                  highlightLabel, othersLabel, guides, zones, zonesLabel,
                  diagonalLabel, mutedLabel})
     points [{id, label, x, y, low?, high?, highlight?, tag?, muted?, note?}]. A
            square plot from 0 to max, a dashed "same score" diagonal, a vertical
            line from low to high on the y scale, 8px markers with a 2px surface
            ring. Highlighted points get a direct label and the rest turn grey;
            tag gives a point a direct label alone (skipped where it would
            overlap a label already placed); muted turns one point grey.
            Points without both x and y are named under the chart, not plotted.
     onPick (id) on click, Enter or Space.
     guides [values]: solid guide lines across both axes (band boundaries); the
            ticks then sit on 0, the guides and max instead of round steps.
     zones  [{lo, hi, label}]: squares shaded along the diagonal (points in one
            are in the same band on both axes), each with its label in its
            corner; zonesLabel names them in the legend.
     diagonalLabel  the diagonal's words ("Same score"); mutedLabel names the
            grey points in the legend when any point is muted; pointLabel names
            the others (yLabel when left out).
   stateGrid({rows, cols, cell, onPick, rowHeader})
     rows   [{id, label, code?}]; cols [{id, label, title?}] (label short, an
            item id; title the full question, for tooltips and screen readers).
            Row names are cut to 7rem on phones (11rem from 40rem up), so views
            should pass a short code that tells rows apart on its own (a country
            code; for brands in markets, the market first).
     cell   (row, col, rowIndex, colIndex) -> {state: "yes"|"no"|"unsure",
            mark?, note?} or null. Yes is a series-1 fill, no the light neutral
            fill, "unsure" ("Couldn't tell") a neutral hatch: an answer, not a
            status, so no status colour. null (or no state) means the question
            was not asked of that row; mark draws a corner triangle ("differs
            from the original"); note is one more line for the tooltip and the
            table.
     onPick (rowId, colId) on click, Enter or Space.
   ordinalGrid({rows, cols, cell, onPick, rowHeader})
     As stateGrid, but cols carry scale {min, max, better: "higher"|"lower"} and
     cell returns {value, state?, mark?, note?}: each value is printed in its
     cell and shaded on its own column's scale in four steps, flipped when lower
     is better (so Tier 1 takes the top step); state "unsure" draws the same
     neutral hatch. Each column's scale is printed under its header, in the
     table view's headers and, for lower-is-better columns, in the legend, so a
     tooltip never holds the only copy.
   dumbbell, diverging (rows on a shared axis, one button each) and crossTable
     (a table of counts) are described where they are defined, below the grids.
   trendLine (a small inline line of past editions) and lines (one line per row
     across past editions, this run last) are described at the end of the file.
   ========================================================================== */

(function () {
  "use strict";

  const HB = (window.HB = window.HB || {});
  const SVG_NS = "http://www.w3.org/2000/svg";
  const WHOLE = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  const ONE_PLACE = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });
  const TWO_PLACES = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });
  const COLLATOR = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });
  const STATE_WORDS = { yes: "Yes", no: "No", unsure: "Couldn't tell", na: "Not asked" };
  const DIFFERS = "Differs from the original";
  // The tooltip names the way to open a mark that matches how it was shown: a click under the
  // pointer (Enter does nothing there, as the mark has no focus), Enter under keyboard focus.
  const PICK_HINT = { pointer: "Click to open", focus: "Press Enter to open" };
  const HIT_RADIUS = 20;   // px: the scatter's pointer takes the nearest point within this
  let uid = 0;

  /* ---- Private helpers ------------------------------------------------------ */

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    setAttrs(node, attrs);
    children.forEach((child) => add(node, child));
    return node;
  }

  function svg(tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    setAttrs(node, attrs);
    children.forEach((child) => add(node, child));
    return node;
  }

  function setAttrs(node, attrs) {
    for (const key in attrs || {}) {
      const value = attrs[key];
      if (value == null || value === false) continue;
      if (key === "text") node.textContent = String(value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }

  function add(node, child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) child.forEach((c) => add(node, c));
    else node.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
  }

  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const fmt = (n) => (Number.isInteger(n) ? WHOLE : ONE_PLACE).format(n);
  const plural = (n, one, many) => (n === 1 ? one : many);

  /** "88.9%". Never rounds a part short of the whole up to 100%, or a part above
      nothing down to 0%: it adds a decimal place instead. */
  function pctText(part, whole, digits) {
    const p = (part / whole) * 100;
    let places = digits;
    let text = p.toFixed(places);
    while (places < 3 && ((part < whole && Number(text) >= 100) || (part > 0 && Number(text) <= 0))) {
      text = p.toFixed(++places);
    }
    return text + "%";
  }

  /** A noun picker: nouns("answers")(n) or nouns({one, many})(n). */
  function nouns(unit) {
    if (!unit) return null;
    if (typeof unit === "string") return () => unit;
    return (n) => plural(n, unit.one, unit.many);
  }

  /** A round step that splits max into about count parts; whole numbers only
      (1, 2, 5, 10, 20, 25, 50 ...) when the data are counts. */
  function niceStep(max, count, whole) {
    const raw = max / count;
    if (!(raw > 0)) return 1;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const f = raw / mag;
    const step = (whole && mag < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10]).find((s) => f <= s) * mag;
    return whole ? Math.max(1, step) : step;
  }

  /** Ticks from 0 to max in round steps; every tick is a value the axis reaches. */
  function ticks(max, count, whole) {
    const step = niceStep(max, count, whole);
    const out = [];
    for (let i = 0; i * step <= max + step * 1e-9; i++) out.push(Math.round(i * step * 1e6) / 1e6);
    return out;
  }

  function niceTop(max, whole) {
    if (!(max > 0)) return 1;
    const step = niceStep(max, 4, whole);
    return Math.ceil(max / step - 1e-9) * step;
  }

  const tickText = (t) => (Number.isInteger(t) ? WHOLE : TWO_PLACES).format(t);

  function nameList(items, limit) {
    const names = items.map((p) => String(p.label));
    if (names.length > limit) return `${names.slice(0, limit).join(", ")} and ${names.length - limit} more`;
    return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
  }

  /* ---- Tooltip: one per chart, fixed to the viewport, never the only way to a value.
     At most one shows on the page at a time. Any scroll (caught on the way down,
     so a grid's own scroller counts) or resize hides it, as a fixed tooltip
     would otherwise stay behind while its mark moves away; the next hover or
     focus shows one again. */

  let openTip = null;
  let watching = false;

  function closeOpenTip() {
    if (openTip) openTip.hidden = true;
    openTip = null;
  }

  function makeTip() {
    const node = el("div", { class: "tooltip chart-tip", "aria-hidden": "true", hidden: true });
    let wait = 0;   // the animation frame a focus tooltip is waiting for

    /** how: "pointer" or "focus"; a hint is one string, or {pointer, focus} to fit each. */
    function fill(content, how) {
      if (!watching) {
        document.addEventListener("scroll", closeOpenTip, { capture: true, passive: true });
        window.addEventListener("resize", closeOpenTip, { passive: true });
        watching = true;
      }
      if (openTip && openTip !== node) openTip.hidden = true;
      openTip = node;
      node.replaceChildren();
      if (content.title != null) node.appendChild(el("p", { class: "tooltip__title", text: content.title }));
      (content.rows || []).forEach((row) => {
        if (row == null || row === "") return;
        node.appendChild(typeof row === "object"
          ? el("p", { class: "chart-tip__row" }, el("strong", { text: row.strong }), " ", row.text || "")
          : el("p", { class: "chart-tip__row", text: row }));
      });
      const hint = content.hint && typeof content.hint === "object" ? content.hint[how] : content.hint;
      if (hint) node.appendChild(el("p", { class: "chart-tip__hint", text: hint }));
      node.hidden = false;
    }

    function place(left, top, fallbackLeft, fallbackTop) {
      const pad = 8;
      const vw = document.documentElement.clientWidth || window.innerWidth;
      const vh = document.documentElement.clientHeight || window.innerHeight;
      const w = node.offsetWidth;
      const h = node.offsetHeight;
      const x = left + w > vw - pad ? fallbackLeft : left;
      const y = top + h > vh - pad ? fallbackTop : top;
      node.style.left = clamp(x, pad, Math.max(pad, vw - w - pad)) + "px";
      node.style.top = clamp(y, pad, Math.max(pad, vh - h - pad)) + "px";
    }

    const atPoint = (x, y) => place(x + 14, y + 14, x - 14 - node.offsetWidth, y - 14 - node.offsetHeight);

    return {
      node,
      /** Show beside the pointer. */
      at(content, x, y) {
        cancelAnimationFrame(wait);
        fill(content, "pointer");
        atPoint(x, y);
      },
      move(x, y) { if (!node.hidden) atPoint(x, y); },
      /** Show under a focused mark (above it when there is no room below), one
          frame later: a focus that scrolled its mark into view has fired that
          scroll by then, so the scroll does not hide the tooltip it came with.
          Skipped if focus has moved on from owner in the meantime. */
      on(content, target, owner) {
        cancelAnimationFrame(wait);
        wait = requestAnimationFrame(() => {
          if (document.activeElement !== owner) return;
          const r = target.getBoundingClientRect();
          const vw = document.documentElement.clientWidth || window.innerWidth;
          const vh = document.documentElement.clientHeight || window.innerHeight;
          if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) return;   // its mark is off screen
          fill(content, "focus");
          place(r.left, r.bottom + 8, r.right - node.offsetWidth, r.top - 8 - node.offsetHeight);
        });
      },
      hide() {
        cancelAnimationFrame(wait);
        node.hidden = true;
        if (openTip === node) openTip = null;
      },
    };
  }

  /* ---- Figure, empty state, table twin --------------------------------------- */

  function head(o) {
    if (!o.title) return { node: null, id: null };
    const id = `ayb-chart-${++uid}`;
    return { id, node: el("div", { class: "figure__head" }, el("h3", { class: "figure__title", id, text: o.title })) };
  }

  /** The figure every chart returns: head, legend, chart, note, the table
      toggle, the tooltip and the caption, in that order. The table twin is
      built on the first "Show as table" and swaps places with the chart. */
  function figure(o, parts) {
    const top = head(o);
    const fig = el("figure", { class: "figure chart-figure", "aria-labelledby": top.id });
    add(fig, [top.node, parts.legend, parts.chart, parts.note]);
    const button = el("button", { type: "button", class: "btn btn--small", text: "Show as table" });
    const foot = el("div", { class: "chart-foot" }, button);
    let twin = null;
    button.addEventListener("click", () => {
      if (!twin) {
        twin = parts.table();
        twin.hidden = true;
        foot.before(twin);
      }
      const showTable = twin.hidden;
      twin.hidden = !showTable;
      [parts.legend, parts.chart, parts.note].forEach((n) => { if (n) n.hidden = showTable; });
      button.textContent = showTable ? "Show as chart" : "Show as table";
      if (parts.tip) parts.tip.hide();
    });
    add(fig, [foot, parts.tip && parts.tip.node]);
    if (o.caption) fig.appendChild(el("figcaption", { class: "figure__caption", text: o.caption }));
    return fig;
  }

  function empty(o, text) {
    const top = head(o);
    const fig = el("figure", { class: "figure chart-figure", "aria-labelledby": top.id }, top.node,
      el("p", { class: "chart-empty", text }));
    if (o.caption) fig.appendChild(el("figcaption", { class: "figure__caption", text: o.caption }));
    return fig;
  }

  /** A table twin: [{text, num?, hidden?, sub?}] headers (sub: a visible second
      line, such as a column's scale) and rows of cells (strings, or {text, num?,
      mono?}); the first cell of each row is its row header. */
  function tableTwin(spec) {
    const headRow = el("tr");
    spec.head.forEach((h) => headRow.appendChild(el("th", { scope: "col", class: h.num ? "num" : null },
      h.text, h.hidden ? el("span", { class: "visually-hidden", text: ` ${h.hidden}` }) : null,
      h.sub ? el("span", { class: "chart-twin__sub", text: h.sub }) : null)));
    const body = document.createElement("tbody");
    const frag = document.createDocumentFragment();
    spec.rows.forEach((cells) => {
      const tr = document.createElement("tr");
      cells.forEach((c, i) => {
        const cell = document.createElement(i === 0 ? "th" : "td");
        if (i === 0) cell.scope = "row";
        const v = c !== null && typeof c === "object" ? c : { text: c };
        const cls = [v.num && "num", v.mono && "mono"].filter(Boolean).join(" ");
        if (cls) cell.className = cls;
        cell.textContent = v.text == null ? "" : String(v.text);
        tr.appendChild(cell);
      });
      frag.appendChild(tr);
    });
    body.appendChild(frag);
    return el("section", { class: "table-block chart-twin" },
      el("div", { class: "table-block__head" }, el("p", { class: "table-block__count", text: spec.count })),
      el("div", { class: "table-scroll", role: "region", tabindex: "0", "aria-label": spec.label },
        el("table", { class: "table table--freeze-first" }, el("thead", null, headRow), body)));
  }

  /** The pointer layer for charts whose marks are elements: the tooltip follows
      the mouse over any mark find() recognises. Returns a probe for the mark
      under the mouse, so focus does not move a tooltip the mouse put there. */
  function hoverLayer(root, tip, find, contentFor) {
    let over = null;
    root.addEventListener("pointerover", (e) => {
      const mark = find(e.target);
      if (mark === over) return;
      over = mark;
      if (mark) tip.at(contentFor(mark), e.clientX, e.clientY);
      else tip.hide();
    });
    root.addEventListener("pointermove", (e) => { if (over) tip.move(e.clientX, e.clientY); });
    root.addEventListener("pointerleave", () => { over = null; tip.hide(); });
    return () => over;
  }

  /** One tab stop over a run of marks in reading order (bars, scatter points).
      The arrow keys, Home and End move focus along marks(); the mark that takes
      focus takes the tab stop, then focus(k) runs. Enter or Space calls pick(k)
      when given; leave() runs when focus goes outside root. The grids move in
      two dimensions and keep their own keys. */
  function roving(root, marks, { focus, pick, leave }) {
    root.addEventListener("keydown", (e) => {
      const list = marks();
      const k = list.indexOf(e.target);
      if (k < 0) return;
      if (pick && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        pick(k);
        return;
      }
      const to = { ArrowDown: k + 1, ArrowRight: k + 1, ArrowUp: k - 1, ArrowLeft: k - 1, Home: 0, End: list.length - 1 }[e.key];
      if (to == null) return;
      e.preventDefault();
      list[clamp(to, 0, list.length - 1)].focus();
    });
    root.addEventListener("focusin", (e) => {
      const list = marks();
      const k = list.indexOf(e.target);
      if (k < 0) return;
      list.forEach((mark, i) => mark.setAttribute("tabindex", i === k ? "0" : "-1"));
      if (focus) focus(k);
    });
    root.addEventListener("focusout", (e) => { if (leave && !root.contains(e.relatedTarget)) leave(); });
  }

  /* ---- bars ------------------------------------------------------------------ */

  function bars(opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter(Boolean);
    if (!rows.length) return empty(o, "Nothing to chart yet.");
    const unit = nouns(o.unit);
    // Shares and counts cannot share one axis, so a mix is the caller's mistake, not data to guess at.
    const withTotal = rows.filter((r) => isNum(r.total)).length;
    if (withTotal && withTotal < rows.length) {
      throw new TypeError(`HB.charts.bars: ${withTotal} of ${rows.length} rows have a total. Give every row ` +
        "a total (bars show value / total) or none (bars show counts).");
    }
    const share = withTotal > 0;
    const metric = (r) => (!isNum(r.value) ? null : !share ? r.value : r.total > 0 ? r.value / r.total : null);
    const order = rows.map((_, i) => i);
    const sort = o.sort === true ? "value" : o.sort;
    if (sort === "value") order.sort((a, b) => (metric(rows[b]) ?? -1) - (metric(rows[a]) ?? -1) || a - b);
    else if (sort === "label") order.sort((a, b) => COLLATOR.compare(String(rows[a].label), String(rows[b].label)) || a - b);

    const whole = rows.every((r) => !isNum(r.value) || Number.isInteger(r.value));
    const top = share ? 1 : niceTop(Math.max(isNum(o.max) ? o.max : 0, ...rows.map((r) => (isNum(r.value) ? r.value : 0))), whole);
    const tickValues = share ? [0, 0.25, 0.5, 0.75, 1] : ticks(top, 4, whole);
    const pos = (v) => (v / top) * 100;
    const name = o.title || o.label || "Bar chart";

    const endText = (r) => {
      if (!isNum(r.value)) return "Not measured yet";
      if (!share) return fmt(r.value);
      return r.total > 0 ? pctText(r.value, r.total, 0) : `${fmt(r.value)} of ${fmt(r.total)}`;
    };
    const tipFor = (r) => {
      if (!isNum(r.value)) return { title: "Not measured yet", rows: [r.label, r.note] };
      if (!share) return { title: fmt(r.value) + (unit ? ` ${unit(r.value)}` : ""), rows: [r.label, r.note] };
      return {
        title: r.total > 0 ? pctText(r.value, r.total, 1) : "Nothing to compare",
        rows: [`${fmt(r.value)} of ${fmt(r.total)}${unit ? ` ${unit(r.total)}` : ""}`, r.label, r.note],
      };
    };

    const list = el("div", { class: "bars", role: "list", "aria-label": name });
    const lines = el("div", { class: "bars__grid", "aria-hidden": "true", style: `grid-row: 1 / span ${order.length}` });
    tickValues.forEach((t) => lines.appendChild(el("span", { class: t === 0 ? "bars__base" : null, style: `left: ${pos(t)}%` })));
    list.appendChild(lines);
    const rowEls = order.map((index, k) => {
      const r = rows[index];
      const m = metric(r);
      // The bar stays on the axis (0 to 100% of it); the label keeps the true
      // number, so a value past either end is still read right, inside the chart.
      const p = m == null ? 0 : clamp(share ? m * 100 : pos(m), 0, 100);
      return el("div", { class: "bars__row", role: "listitem", tabindex: k === 0 ? "0" : "-1", style: `grid-row: ${k + 1}` },
        el("span", { class: "bars__label", text: r.label }),
        el("span", { class: "bars__track" },
          m == null || p === 0 ? null : el("span", { class: "bars__bar", style: `width: ${p}%` }),
          el("span", { class: "bars__value", style: `left: ${p}%`, text: endText(r) })),
        r.note ? el("span", { class: "visually-hidden", text: `. ${r.note}` }) : null);
    });
    list.append(...rowEls);
    const axis = el("div", { class: "bars__axis", "aria-hidden": "true", style: `grid-row: ${order.length + 1}` });
    tickValues.forEach((t) => axis.appendChild(el("span", { style: `left: ${pos(t)}%`, text: share ? pctText(t, 1, 0) : tickText(t) })));
    list.appendChild(axis);

    const tip = makeTip();
    const rowOf = (target) => (target.closest ? target.closest(".bars__row") : null);
    const hovered = hoverLayer(list, tip, rowOf, (row) => tipFor(rows[order[rowEls.indexOf(row)]]));
    roving(list, () => rowEls, {
      focus: (k) => {
        if (hovered() !== rowEls[k]) tip.on(tipFor(rows[order[k]]), rowEls[k].querySelector(".bars__track"), rowEls[k]);
      },
      leave: () => { if (!hovered()) tip.hide(); },
    });

    const valueHead = unit ? unit(2) : "count";
    const noted = rows.some((r) => r.note);   // a note is in the tooltip, so the table carries it too
    const table = () => tableTwin({
      label: `${name}, as a table`,
      count: `Showing all ${rows.length} ${plural(rows.length, "bar", "bars")} from the chart.`,
      head: [{ text: o.labelHeader || "Name" },
        ...(share ? [{ text: "Share", num: true }] : []),
        { text: valueHead.charAt(0).toUpperCase() + valueHead.slice(1), num: true },
        ...(share ? [{ text: "Out of", num: true }] : []),
        ...(noted ? [{ text: "Note" }] : [])],
      rows: order.map((i) => {
        const r = rows[i];
        return [r.label,
          ...(share ? [{ text: isNum(r.value) && r.total > 0 ? pctText(r.value, r.total, 1) : "–", num: true }] : []),
          { text: isNum(r.value) ? fmt(r.value) : "Not measured yet", num: true },
          ...(share ? [{ text: fmt(r.total), num: true }] : []),
          ...(noted ? [r.note || "–"] : [])];
      }),
    });

    return figure(o, { chart: el("div", { class: "chart chart--bars" }, list), table, tip });
  }

  /* ---- scatterRanges ------------------------------------------------------------ */

  function scatterRanges(opts) {
    const o = opts || {};
    const all = (o.points || []).filter(Boolean);
    const plotted = all.filter((p) => isNum(p.x) && isNum(p.y));
    const unplotted = all.filter((p) => !(isNum(p.x) && isNum(p.y)));
    if (!plotted.length) return empty(o, "Nothing to plot yet: no point has both scores.");

    const xLabel = o.xLabel || "Original";
    const yLabel = o.yLabel || "Ours";
    const rangeLabel = o.rangeLabel || "Possible range";
    const pick = typeof o.onPick === "function" ? o.onPick : null;
    const anyHighlight = plotted.some((p) => p.highlight);
    const anyMuted = plotted.some((p) => p.muted);
    const isMuted = (p) => !!p.muted || (anyHighlight && !p.highlight);
    const diagonalWords = o.diagonalLabel || "Same score";
    const guides = (o.guides || []).filter(isNum);
    const zones = (o.zones || []).filter((z) => z && isNum(z.lo) && isNum(z.hi) && z.hi > z.lo);
    const hasRange = (p) => isNum(p.low) && isNum(p.high) && p.high !== p.low;
    let max = isNum(o.max) && o.max > 0 ? o.max : 0;
    plotted.forEach((p) => { max = Math.max(max, p.x, p.y, isNum(p.high) ? p.high : 0, isNum(p.low) ? p.low : 0); });
    if (!(max > 0)) max = 1;   // every score 0: keep a scale to draw on
    const whole = plotted.every((p) => [p.x, p.y, p.low, p.high].every((v) => !isNum(v) || Number.isInteger(v)));
    // Keyboard order: left to right, then bottom to top.
    const order = plotted.slice().sort((a, b) => a.x - b.x || a.y - b.y || COLLATOR.compare(String(a.label), String(b.label)));
    const name = o.title || o.label || `${yLabel} against ${xLabel}`;

    const rangeWords = (p) => `${fmt(p.low)} to ${fmt(p.high)}`;
    const tipFor = (p) => ({
      title: p.label,
      rows: [{ strong: fmt(p.y), text: yLabel },
        hasRange(p) ? { strong: rangeWords(p), text: rangeLabel.toLowerCase() } : null,
        { strong: fmt(p.x), text: xLabel },
        p.note],
      hint: pick ? PICK_HINT : null,
    });
    const ariaFor = (p) => `${p.label}: ${yLabel} ${fmt(p.y)}` +
      (hasRange(p) ? `, ${rangeLabel.toLowerCase()} ${rangeWords(p)}` : "") + `, ${xLabel} ${fmt(p.x)}` +
      (p.note ? `. ${p.note}` : "");

    // The y title gets a row of its own above the plot, so a long one wraps
    // instead of running into the "Same score" label at the top right.
    // tabindex -1: Chrome otherwise gives the <svg> a tab stop of its own before the points.
    const plot = svg("svg", { class: "sc" + (pick ? " sc--pick" : ""), role: "group", "aria-label": name, tabindex: "-1" });
    const chart = el("div", { class: "chart chart--scatter" },
      el("p", { class: "sc__ytitle", "aria-hidden": "true", text: yLabel }), plot);
    const tip = makeTip();
    let geo = null;
    let nodes = [];
    let hoverRing = null;   // follows the pointer
    let focusRing = null;   // stays on the focused point; styles.css shows it for keyboard focus
    let active = 0;         // the point that holds the tab stop
    let over = -1;          // the point under the pointer

    /** Draws at the chart's real width, so text stays at its stylesheet size. */
    function draw(width) {
      const size = Math.round(clamp(width, 240, 520));
      const m = { l: 40, r: 16, t: 24, b: 48 };
      const side = size - m.l - m.r;
      const height = m.t + side + m.b;
      const sx = (v) => m.l + (v / max) * side;
      const sy = (v) => m.t + side - (v / max) * side;
      geo = { size, height, sx, sy };
      const hadFocus = plot.contains(document.activeElement);
      plot.replaceChildren();
      plot.setAttribute("viewBox", `0 0 ${size} ${height}`);
      plot.setAttribute("width", size);
      plot.setAttribute("height", height);

      const frame = svg("g", { "aria-hidden": "true" });
      zones.forEach((z) => {
        const lo = clamp(z.lo, 0, max);
        const hi = clamp(z.hi, 0, max);
        frame.append(svg("rect", { class: "sc__zone", x: sx(lo), y: sy(hi), width: sx(hi) - sx(lo), height: sy(lo) - sy(hi) }));
        if (z.label != null && sx(hi) - sx(lo) >= 14) {
          frame.append(svg("text", { class: "sc__zonelabel", x: sx(lo) + 3, y: sy(hi) + 11, text: z.label }));
        }
      });
      const tickValues = guides.length
        ? [...new Set([0, ...guides.filter((g) => g > 0 && g < max), max])].sort((a, b) => a - b)
        : ticks(max, 5, whole);
      // Guide ticks can sit close together: a label too near the last one drawn is left out.
      let lastX = -Infinity;
      let lastY = Infinity;
      tickValues.forEach((t) => {
        const cls = t === 0 ? "sc__axis" : guides.includes(t) ? "sc__guide" : "sc__gridline";
        frame.append(
          svg("line", { class: cls, x1: sx(t), x2: sx(t), y1: m.t, y2: m.t + side }),
          svg("line", { class: cls, x1: m.l, x2: m.l + side, y1: sy(t), y2: sy(t) }));
        if (sx(t) - lastX >= 18) {
          frame.append(svg("text", { class: "sc__tick", x: sx(t), y: m.t + side + 18, "text-anchor": "middle", text: tickText(t) }));
          lastX = sx(t);
        }
        if (lastY - sy(t) >= 12) {
          frame.append(svg("text", { class: "sc__tick", x: m.l - 8, y: sy(t) + 4, "text-anchor": "end", text: tickText(t) }));
          lastY = sy(t);
        }
      });
      frame.append(svg("text", { class: "sc__title", x: m.l + side / 2, y: height - 8, "text-anchor": "middle", text: xLabel }));

      // The "same score" diagonal, labelled at its end above the plot's top-right
      // corner, where no point can cover the label.
      const diagonal = svg("g", { class: "sc__diag", "aria-hidden": "true" },
        svg("line", { x1: sx(0), y1: sy(0), x2: sx(max), y2: sy(max) }),
        svg("text", { x: sx(max), y: m.t - 7, "text-anchor": "end", text: diagonalWords }));

      const ranges = svg("g", { "aria-hidden": "true" });
      order.forEach((p) => {
        if (!hasRange(p)) return;
        ranges.appendChild(svg("line", { class: "sc__range" + (isMuted(p) ? " is-muted" : ""),
          x1: sx(p.x), x2: sx(p.x), y1: sy(p.low), y2: sy(p.high) }));
      });

      const points = svg("g");
      nodes = order.map((p, k) => {
        const g = svg("g", {
          class: "sc__pt" + (p.highlight ? " is-highlight" : "") + (isMuted(p) ? " is-muted" : ""),
          tabindex: k === active ? "0" : "-1", role: pick ? "button" : "img", "aria-label": ariaFor(p),
        }, svg("circle", { class: "sc__dot", cx: sx(p.x), cy: sy(p.y), r: 5 }));
        points.appendChild(g);
        return g;
      });
      nodes.forEach((g, k) => { if (!isMuted(order[k])) points.appendChild(g); });   // muted points underneath
      nodes.forEach((g, k) => { if (order[k].highlight) points.appendChild(g); });   // highlighted on top

      // Direct labels: every highlighted point, then tagged points where they fit
      // (a rough width of 6.2px a character; a label that would overlap one
      // already placed, or run off the plot, is left to the tooltip and table).
      // Tagged labels go furthest from the diagonal first, and keep clear of other points too.
      const labels = svg("g", { "aria-hidden": "true" });
      const placed = [];
      const dots = order.map((p) => ({ p, x1: sx(p.x) - 6, x2: sx(p.x) + 6, y1: sy(p.y) - 6, y2: sy(p.y) + 6 }));
      const apart = (a, b) => a.x2 < b.x1 || a.x1 > b.x2 || a.y2 < b.y1 || a.y1 > b.y2;
      const fits = (box, p) => box.x1 >= m.l && box.x2 <= size && box.y1 >= m.t && box.y2 <= m.t + side &&
        placed.every((b) => apart(box, b)) && dots.every((d) => d.p === p || apart(box, d));
      const tagged = order.filter((p) => p.tag && !p.highlight).sort((a, b) => Math.abs(b.y - b.x) - Math.abs(a.y - a.x));
      [...order.filter((p) => p.highlight), ...tagged].forEach((p) => {
        const w = String(p.label).length * 6.2;
        const cx = sx(p.x);
        const cy = sy(p.y);
        const tries = [
          { anchor: "start", x: cx + 10, y: cy + 4, box: { x1: cx + 10, x2: cx + 10 + w, y1: cy - 8, y2: cy + 6 } },
          { anchor: "end", x: cx - 10, y: cy + 4, box: { x1: cx - 10 - w, x2: cx - 10, y1: cy - 8, y2: cy + 6 } },
          { anchor: "middle", x: cx, y: cy - 11, box: { x1: cx - w / 2, x2: cx + w / 2, y1: cy - 22, y2: cy - 8 } },
          { anchor: "middle", x: cx, y: cy + 19, box: { x1: cx - w / 2, x2: cx + w / 2, y1: cy + 8, y2: cy + 22 } },
        ];
        if (cx >= m.l + side * 0.7) [tries[0], tries[1]] = [tries[1], tries[0]];
        const at = tries.find((t) => fits(t.box, p)) || (p.highlight ? tries[0] : null);
        if (!at) return;
        placed.push(at.box);
        labels.appendChild(svg("text", { class: "sc__label", x: at.x, y: at.y, "text-anchor": at.anchor, text: p.label }));
      });

      hoverRing = svg("circle", { class: "sc__halo", r: 9, cx: 0, cy: 0, visibility: "hidden" });
      focusRing = svg("circle", { class: "sc__focus", r: 10, cx: 0, cy: 0, visibility: "hidden" });
      const hit = svg("rect", { class: "sc__hit", x: m.l - 12, y: m.t - 12, width: side + 24, height: side + 24 });
      plot.append(frame, diagonal, ranges, points, labels, hoverRing, focusRing, hit);
      if (hadFocus && nodes[active]) nodes[active].focus({ preventScroll: true });
    }

    /** Puts a ring on point k, or hides it when k is -1. */
    function ring(circle, k) {
      if (!circle) return;
      if (k < 0) { circle.setAttribute("visibility", "hidden"); return; }
      circle.setAttribute("cx", geo.sx(order[k].x));
      circle.setAttribute("cy", geo.sy(order[k].y));
      circle.setAttribute("visibility", "visible");
    }

    /** The nearest point to the pointer within HIT_RADIUS, or -1: a dense plot
        needs no pinpoint aim, and overlapping markers stay reachable. */
    function nearest(e) {
      const box = plot.getBoundingClientRect();
      if (!box.width) return -1;
      const x = ((e.clientX - box.left) * geo.size) / box.width;
      const y = ((e.clientY - box.top) * geo.height) / box.height;
      let best = -1;
      let bestD = HIT_RADIUS * HIT_RADIUS;
      order.forEach((p, k) => {
        const dx = geo.sx(p.x) - x;
        const dy = geo.sy(p.y) - y;
        const d = dx * dx + dy * dy;
        if (d < bestD || (d === bestD && p.highlight)) { bestD = d; best = k; }
      });
      return best;
    }

    // Hover and focus each have a ring of their own: pointing at one point never
    // takes the focus ring off another.
    plot.addEventListener("pointermove", (e) => {
      const k = nearest(e);
      plot.classList.toggle("is-over", k >= 0);
      if (k === over) { if (k >= 0) tip.move(e.clientX, e.clientY); return; }
      over = k;
      ring(hoverRing, k);
      if (k >= 0) tip.at(tipFor(order[k]), e.clientX, e.clientY);
      else tip.hide();
    });
    plot.addEventListener("pointerleave", () => {
      over = -1;
      plot.classList.remove("is-over");
      ring(hoverRing, -1);
      tip.hide();
    });
    plot.addEventListener("click", (e) => {
      const k = nearest(e);
      if (k < 0) return;
      nodes[k].focus({ preventScroll: true });
      if (pick) { tip.hide(); pick(order[k].id); }
    });
    roving(plot, () => nodes, {
      focus: (k) => {
        active = k;
        ring(focusRing, k);
        if (over !== k) tip.on(tipFor(order[k]), nodes[k].firstChild, nodes[k]);
      },
      pick: pick ? (k) => { tip.hide(); pick(order[k].id); } : null,
      leave: () => {
        ring(focusRing, -1);
        if (over < 0) tip.hide();
      },
    });

    draw(392);   // a first drawing for the detached node; the observer redraws it at its real width
    if (typeof ResizeObserver === "function") {
      const watch = new ResizeObserver((entries) => {
        if (!chart.isConnected) { watch.disconnect(); return; }   // the view moved on
        const width = Math.round(entries[0].contentRect.width);
        if (width > 0 && Math.round(clamp(width, 240, 520)) !== geo.size) draw(width);
      });
      watch.observe(chart);
    }

    const keySvg = (...marks) => svg("svg", { class: "chart-key", viewBox: "0 0 14 14", "aria-hidden": "true" }, ...marks);
    const keyDot = (cls) => keySvg(svg("g", { class: `sc__pt ${cls}` }, svg("circle", { class: "sc__dot", cx: 7, cy: 7, r: 5 })));
    const legend = el("ul", { class: "legend" },
      el("li", { class: "legend__item" }, keyDot(anyHighlight ? "is-highlight" : ""), anyHighlight ? o.highlightLabel || "Highlighted" : o.pointLabel || yLabel),
      anyHighlight ? el("li", { class: "legend__item" }, keyDot("is-muted"), o.othersLabel || "Others") : null,
      anyMuted && !anyHighlight ? el("li", { class: "legend__item" }, keyDot("is-muted"), o.mutedLabel || "Others") : null,
      zones.length && o.zonesLabel
        ? el("li", { class: "legend__item" }, keySvg(svg("rect", { class: "sc__zone sc__zone--key", x: 1, y: 1, width: 12, height: 12 })), o.zonesLabel)
        : null,
      plotted.some(hasRange)
        ? el("li", { class: "legend__item" }, keySvg(svg("line", { class: "sc__range", x1: 7, x2: 7, y1: 2, y2: 12 })), rangeLabel)
        : null,
      el("li", { class: "legend__item" }, keySvg(svg("g", { class: "sc__diag" }, svg("line", { x1: 1, y1: 13, x2: 13, y2: 1 }))), diagonalWords));

    const note = unplotted.length
      ? el("p", { class: "chart-note", text: `Not plotted, without both scores: ${nameList(unplotted, 6)}.` })
      : null;
    const noted = all.some((p) => p.note);   // a note is in the tooltip, so the table carries it too

    const table = () => tableTwin({
      label: `${name}, as a table`,
      count: `Showing all ${all.length} ${plural(all.length, "point", "points")}` + (unplotted.length
        ? `; ${unplotted.length} ${plural(unplotted.length, "is", "are")} not plotted, without both scores.` : "."),
      head: [{ text: o.labelHeader || "Name" }, { text: yLabel, num: true }, { text: rangeLabel, num: true },
        { text: xLabel, num: true }, ...(noted ? [{ text: "Note" }] : [])],
      rows: all.map((p) => [p.label,
        { text: isNum(p.y) ? fmt(p.y) : "–", num: true },
        { text: hasRange(p) ? rangeWords(p) : "–", num: true },
        { text: isNum(p.x) ? fmt(p.x) : "–", num: true },
        ...(noted ? [p.note || "–"] : [])]),
    });

    return figure(o, { legend, chart, note, table, tip });
  }

  /* ---- stateGrid and ordinalGrid ----------------------------------------------- */

  function readCell(kind, raw) {
    const d = raw || {};
    const base = { mark: !!d.mark, note: d.note == null || d.note === "" ? null : String(d.note) };
    if (d.state === "unsure") return { ...base, state: "unsure" };
    if (kind === "state") return { ...base, state: d.state === "yes" || d.state === "no" ? d.state : "na" };
    return isNum(d.value) ? { ...base, state: "score", value: d.value } : { ...base, state: "na" };
  }

  /** A column's scale: its own {min, max, better}, or (said in its tooltip) the
      range of its values when the column brings none. */
  function scaleOf(col, data, ci) {
    const s = col.scale;
    if (s && isNum(s.min) && isNum(s.max) && s.max > s.min) return { min: s.min, max: s.max, lower: s.better === "lower" };
    const values = data.map((row) => row[ci]).filter((d) => d.state === "score").map((d) => d.value);
    const lo = values.length ? Math.min(...values) : 0;
    const hi = values.length ? Math.max(...values) : 1;
    return { min: lo, max: hi > lo ? hi : lo + 1, lower: !!(s && s.better === "lower"), inferred: true };
  }

  /** One of four steps; only the ends of the scale reach the ends of the ramp. */
  const stepOf = (v, sc) => {
    const t = clamp((v - sc.min) / (sc.max - sc.min), 0, 1);
    return Math.round((sc.lower ? 1 - t : t) * 3);
  };

  const scaleWords = (sc) => (sc.inferred
    ? `No scale given: shaded from this column's lowest value (${fmt(sc.min)}) to its highest (${fmt(sc.max)})`
    : `Scale ${fmt(sc.min)} to ${fmt(sc.max)}` + (sc.lower ? ", lower is better" : ""));
  const scaleRange = (sc) => `${fmt(sc.min)}–${fmt(sc.max)}`;   // printed under each score column's header
  const scaleNote = (sc) => scaleRange(sc) + (sc.lower ? ", lower is better" : "") + (sc.inferred ? ", no scale given" : "");

  function grid(kind, opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter(Boolean);
    const cols = (o.cols || []).filter(Boolean);
    if (!rows.length || !cols.length) return empty(o, "Nothing to show yet.");
    const cellFn = typeof o.cell === "function" ? o.cell : () => null;
    const pick = typeof o.onPick === "function" ? o.onPick : null;
    const name = o.title || o.label || (kind === "state" ? "Answers" : "Scores");
    const rowHeader = o.rowHeader || "Name";

    const data = rows.map((r, ri) => cols.map((c, ci) => readCell(kind, cellFn(r, c, ri, ci))));
    const scales = kind === "ordinal" ? cols.map((c, ci) => scaleOf(c, data, ci)) : null;
    if (scales) data.forEach((row) => row.forEach((d, ci) => { if (d.state === "score") d.step = stepOf(d.value, scales[ci]); }));

    const rowName = (r) => (r.code ? `${r.label} (${r.code})` : String(r.label));
    const colName = (c) => (c.title ? `${c.label} ${c.title}` : String(c.label));
    const valueWords = (d) => (d.state === "score" ? fmt(d.value) : STATE_WORDS[d.state]);
    const cellWords = (d, ci) => valueWords(d) +
      (d.state === "score" ? `, ${scaleWords(scales[ci]).replace(/^\w/, (c) => c.toLowerCase())}` : "") +
      (d.mark ? `, ${DIFFERS.toLowerCase()}` : "") + (d.note ? `. ${d.note}` : "");

    // The table: a sticky header row and row-header column, one roving tab stop.
    // A score column prints its scale under its label (each cell's label says it too).
    const headRow = el("tr", null, el("th", { class: "grid__corner", scope: "col", text: rowHeader }));
    cols.forEach((c, ci) => headRow.appendChild(el("th", { class: "grid__colhead", scope: "col" },
      el("span", { "aria-hidden": c.title ? "true" : null, text: c.label }),
      c.title ? el("span", { class: "visually-hidden", text: colName(c) }) : null,
      scales ? el("span", { class: "grid__scale", "aria-hidden": "true", text: scaleRange(scales[ci]) }) : null)));
    const body = document.createElement("tbody");
    const frag = document.createDocumentFragment();
    rows.forEach((r, ri) => {
      const tr = document.createElement("tr");
      tr.appendChild(el("th", { class: "grid__rowhead", scope: "row" },
        r.code ? el("span", { class: "grid__code", text: r.code }) : null,
        el("span", { class: "grid__name", text: r.label })));
      data[ri].forEach((d, ci) => {
        const td = document.createElement("td");
        td.className = `grid__cell is-${d.state}` + (d.state === "score" ? ` step-${d.step}` : "") + (d.mark ? " is-diff" : "");
        if (d.state === "score") td.textContent = fmt(d.value);
        else if (d.state === "na") td.textContent = "–";
        td.setAttribute("aria-label", cellWords(d, ci));
        tr.appendChild(td);
      });
      frag.appendChild(tr);
    });
    body.appendChild(frag);
    let current = body.rows[0].cells[1];
    current.tabIndex = 0;
    const table = el("table", { class: `grid grid--${kind}` + (pick ? " is-pickable" : ""), role: "grid", "aria-label": name },
      el("thead", null, headRow), body);
    const scroller = el("div", { class: "grid-scroll" }, table);
    const chart = el("div", { class: "chart chart--grid" }, scroller);

    // One delegated listener per event for every cell.
    const tip = makeTip();
    const at = (td) => [td.parentNode.sectionRowIndex, td.cellIndex - 1];
    const cellOf = (target) => {
      const td = target.closest ? target.closest("td.grid__cell") : null;
      return td && body.contains(td) ? td : null;
    };
    const markOf = (target) => {
      const node = target.closest ? target.closest("td.grid__cell, th.grid__colhead, th.grid__rowhead") : null;
      return node && table.contains(node) ? node : null;
    };
    const tipFor = (node) => {
      if (node.classList.contains("grid__colhead")) {
        const c = cols[node.cellIndex - 1];
        return { title: c.label, rows: [c.title, scales ? scaleWords(scales[node.cellIndex - 1]) : null] };
      }
      const [ri, ci] = at(node);
      if (node.classList.contains("grid__rowhead")) return { title: rows[ri].label, rows: [rows[ri].code] };
      const d = data[ri][ci];
      return {
        title: valueWords(d),
        rows: [d.state === "score" ? scaleWords(scales[ci]) : null, rowName(rows[ri]), colName(cols[ci]),
          d.mark ? DIFFERS : null, d.note],
        hint: pick ? PICK_HINT : null,
      };
    };
    const hovered = hoverLayer(table, tip, markOf, tipFor);

    let padded = false;
    function takeStop(td) {
      if (current === td) return;
      current.tabIndex = -1;
      td.tabIndex = 0;
      current = td;
    }
    table.addEventListener("focusin", (e) => {
      const td = cellOf(e.target);
      if (!td) return;
      if (!padded) {   // keep a cell scrolled into view clear of the sticky headers
        scroller.style.scrollPaddingTop = `${table.tHead.offsetHeight}px`;
        scroller.style.scrollPaddingLeft = `${headRow.cells[0].offsetWidth}px`;
        padded = true;
      }
      takeStop(td);
      if (hovered() !== td) tip.on(tipFor(td), td, td);
    });
    table.addEventListener("focusout", (e) => { if (!table.contains(e.relatedTarget) && !hovered()) tip.hide(); });
    table.addEventListener("click", (e) => {
      const td = cellOf(e.target);
      if (!td) return;
      takeStop(td);
      td.focus({ preventScroll: true });
      if (pick) {
        const [ri, ci] = at(td);
        tip.hide();
        pick(rows[ri].id, cols[ci].id);
      }
    });
    table.addEventListener("keydown", (e) => {
      const td = cellOf(e.target);
      if (!td) return;
      let [ri, ci] = at(td);
      if ((e.key === "Enter" || e.key === " ") && pick) {
        e.preventDefault();
        tip.hide();
        pick(rows[ri].id, cols[ci].id);
        return;
      }
      const ctrl = e.ctrlKey || e.metaKey;
      switch (e.key) {
        case "ArrowRight": ci += 1; break;
        case "ArrowLeft": ci -= 1; break;
        case "ArrowDown": ri += 1; break;
        case "ArrowUp": ri -= 1; break;
        case "PageDown": ri += 10; break;
        case "PageUp": ri -= 10; break;
        case "Home": ci = 0; if (ctrl) ri = 0; break;
        case "End": ci = cols.length - 1; if (ctrl) ri = rows.length - 1; break;
        default: return;
      }
      e.preventDefault();
      const next = body.rows[clamp(ri, 0, rows.length - 1)].cells[clamp(ci, 0, cols.length - 1) + 1];
      takeStop(next);
      next.focus();
    });

    // Legend: only what the grid shows.
    const seen = new Set();
    let marked = false;
    data.forEach((row) => row.forEach((d) => { seen.add(d.state); if (d.mark) marked = true; }));
    const key = (cls, word, text) => el("li", { class: "legend__item" },
      el("span", { class: `grid-key ${cls}`, "aria-hidden": "true", text }), word);
    const items = [];
    if (kind === "state") {
      ["yes", "no"].forEach((s) => { if (seen.has(s)) items.push(key(`is-${s}`, STATE_WORDS[s])); });
    } else if (seen.has("score")) {
      const first = scales[0];
      const shared = scales.every((s) => s.min === first.min && s.max === first.max && s.lower === first.lower && !s.inferred);
      const steps = [0, 1, 2, 3];
      const word = (text) => el("span", { class: "legend__word", text });
      if (shared && first.max - first.min === 3) {   // a 0-3 scale: each step is one score
        const valueAt = (s) => (first.lower ? first.max - s : first.min + s);
        items.push(el("li", { class: "legend__item" }, word("Score"),
          el("span", { class: "grid-ramp", "aria-hidden": "true" },
            steps.map((s) => el("span", { class: `grid-key is-score step-${s}`, text: fmt(valueAt(s)) }))),
          el("span", { class: "visually-hidden", text: `, shaded from ${fmt(valueAt(0))} to ${fmt(valueAt(3))}` })));
      } else {
        items.push(el("li", { class: "legend__item" }, word("Lowest"),
          el("span", { class: "grid-ramp", "aria-hidden": "true" }, steps.map((s) => el("span", { class: `grid-key is-score step-${s}` }))),
          word("Highest")));
        items.push(el("li", { class: "legend__item" }, "Each column is shaded on its own scale, printed under its name"));
        const reversed = cols.filter((c, ci) => scales[ci].lower).map((c) => ({ label: c.label }));
        if (reversed.length) {
          items.push(el("li", { class: "legend__item" },
            `${nameList(reversed, 4)}: lower is better, so the lowest number takes the highest step`));
        }
      }
      const inferred = cols.filter((c, ci) => scales[ci].inferred).map((c) => ({ label: c.label }));
      if (inferred.length) {
        items.push(el("li", { class: "legend__item" },
          `${nameList(inferred, 4)}: no scale given, so shaded from the column's own lowest value to its highest`));
      }
    }
    if (seen.has("unsure")) items.push(key("is-unsure", STATE_WORDS.unsure));
    if (seen.has("na")) items.push(key("is-na", STATE_WORDS.na, "–"));
    if (marked) items.push(key("is-diff", DIFFERS));
    const legend = el("ul", { class: "legend" }, items);

    const twin = () => tableTwin({
      label: `${name}, as a table`,
      count: `Showing all ${rows.length} ${plural(rows.length, "row", "rows")} and ${cols.length} ` +
        `${plural(cols.length, "column", "columns")} from the chart.`,
      head: [{ text: rowHeader },
        ...cols.map((c, ci) => ({ text: c.label, hidden: c.title || null, sub: scales ? scaleNote(scales[ci]) : null }))],
      rows: rows.map((r, ri) => [rowName(r), ...data[ri].map((d) => ({
        text: valueWords(d) + (d.mark ? `; ${DIFFERS.toLowerCase()}` : "") + (d.note ? `. ${d.note}` : ""),
        num: d.state === "score" && !d.mark && !d.note,
      }))]),
    });

    return figure(o, { legend, chart, table: twin, tip });
  }

  const stateGrid = (opts) => grid("state", opts);
  const ordinalGrid = (opts) => grid("ordinal", opts);

  /* ---- Row charts: dumbbell and diverging ---------------------------------------
     One row per item on a shared axis: its name, a plot strip and (dumbbell) an end
     column. The axis sits in a header row that stays in view while the page
     scrolls. Each row is a button with a full text name: one tab stop for the
     chart, the arrow keys move between rows, a click or Enter picks. From 34rem
     down the name takes a line of its own above the strip. */

  /** One gradient that draws a hairline at each [percent, colour] in lines. */
  function hairlines(lines) {
    if (!lines.length) return "none";
    const stops = ["transparent 0"];
    lines.slice().sort((a, b) => a[0] - b[0]).forEach(([p, colour]) => {
      stops.push(`transparent calc(${p}% - 0.5px)`, `${colour} calc(${p}% - 0.5px)`,
        `${colour} calc(${p}% + 0.5px)`, `transparent calc(${p}% + 0.5px)`);
    });
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }

  function rowChart(o, kind, spec) {
    const rows = spec.rows;
    const pick = typeof o.onPick === "function" ? o.onPick : null;
    const name = o.title || o.label || "Chart";
    const buttons = rows.map((r, k) => el("button", {
      type: "button", class: "rc__row", tabindex: k === 0 ? "0" : "-1", "aria-label": spec.ariaFor(r),
    },
    el("span", { class: "rc__name", text: r.label }),
    el("span", { class: "rc__plot", "aria-hidden": "true" }, el("span", { class: "rc__in" }, spec.plot(r))),
    spec.end ? el("span", { class: "rc__end", "aria-hidden": "true" }, spec.end(r)) : null));
    const list = el("ol", { class: "rc__list", "aria-label": name }, buttons.map((b) => el("li", { class: "rc__item" }, b)));
    const headRow = el("div", { class: "rc__head", "aria-hidden": "true" },
      el("span", { class: "rc__name", text: spec.nameHead || "" }),
      el("span", { class: "rc__plot" }, el("span", { class: "rc__in" }, spec.axis)),
      spec.end ? el("span", { class: "rc__end", text: spec.endHead || "" }) : null);
    const chart = el("div", { class: `chart rc rc--${kind}` + (spec.end ? "" : " rc--noend") }, headRow, list);
    chart.style.setProperty("--rc-lines", spec.lines || "none");

    const tip = makeTip();
    const rowOf = (target) => (target.closest ? target.closest(".rc__row") : null);
    const hovered = hoverLayer(list, tip, rowOf, (b) => spec.tipFor(rows[buttons.indexOf(b)]));
    roving(list, () => buttons, {
      focus: (k) => { if (hovered() !== buttons[k]) tip.on(spec.tipFor(rows[k]), buttons[k].querySelector(".rc__plot"), buttons[k]); },
      leave: () => { if (!hovered()) tip.hide(); },
    });
    list.addEventListener("click", (e) => {
      const b = rowOf(e.target);
      if (!b || !pick) return;
      tip.hide();
      pick(rows[buttons.indexOf(b)].id);
    });
    return figure(o, { legend: spec.legend, chart, note: spec.note, table: spec.table, tip });
  }

  const keyMark = (cls) => el("span", { class: `rc-key ${cls}`, "aria-hidden": "true" });
  const legendItems = (items) => (items || []).filter(Boolean).map((item) => (item instanceof Node && item.tagName === "LI"
    ? item : el("li", { class: "legend__item" }, item)));

  /* dumbbell({rows, max, suffix, bands, aLabel, bLabel, bandLabel, boundaryLabel,
               endHead, labelHeader, twinHead, legendExtra, onPick, empty, note})
     rows   [{id, label, a, b, endText?, glyph?, aria?, tip?, twin?}]: a is the
            filled dot (this edition), b the open ring (the one compared with),
            joined by a line; endText (the gap) and glyph (a Node, decorative)
            fill the end column; aria is the row's full text, tip the tooltip
            ({title, rows, hint}); twin the extra cells for the table.
     bands  [{min, label, short?}]: drawn as a strip of named bands over the
            axis, with ticks and hairlines on each boundary. Without bands the
            axis has round ticks.
     suffix after the axis's last tick and every value ("%"). */
  function dumbbell(opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter(Boolean);
    if (!rows.length) return empty(o, o.empty || "Nothing to show yet.");
    const aLabel = o.aLabel || "This edition";
    const bLabel = o.bLabel || "Before";
    const suffix = o.suffix || "";
    let max = isNum(o.max) && o.max > 0 ? o.max : 0;
    rows.forEach((r) => { max = Math.max(max, isNum(r.a) ? r.a : 0, isNum(r.b) ? r.b : 0); });
    if (!(max > 0)) max = 1;
    const whole = rows.every((r) => [r.a, r.b].every((v) => !isNum(v) || Number.isInteger(v)));
    const pos = (v) => clamp((v / max) * 100, 0, 100);
    const val = (v) => (isNum(v) ? fmt(v) + suffix : "–");
    const bands = (o.bands || []).filter((b) => b && isNum(b.min) && b.min < max).slice().sort((x, y) => x.min - y.min);
    const edges = bands.length
      ? [...new Set([0, ...bands.map((b) => b.min).filter((v) => v > 0), max])].sort((x, y) => x - y)
      : ticks(max, 4, whole);

    const strip = bands.length
      ? el("span", { class: "db__strip" }, bands.map((b, i) => {
        const lo = Math.max(0, b.min);
        const hi = i + 1 < bands.length ? bands[i + 1].min : max;
        return el("span", { class: "db__band" + (i % 2 ? " is-alt" : ""),
          style: `left: ${pos(lo)}%; width: ${pos(hi) - pos(lo)}%`, text: b.short != null ? b.short : b.label });
      }))
      : null;
    const tickRow = el("span", { class: "db__ticks" }, edges.map((v) => el("span", {
      // A tick close to the end gives way to the end's label on a narrow chart.
      class: "db__tick" + (v === 0 ? " is-first" : v === max ? " is-last" : max - v < max * 0.16 ? " is-crowded" : ""),
      style: `left: ${pos(v)}%`,
      text: tickText(v) + (v === max ? suffix : ""),
    })));
    const lines = hairlines(edges.filter((v) => v > 0 && v < max).map((v) => [pos(v), "var(--rule)"]));

    const plot = (r) => {
      const out = [];
      if (isNum(r.a) && isNum(r.b)) {
        const lo = Math.min(r.a, r.b);
        const hi = Math.max(r.a, r.b);
        out.push(el("span", { class: "db__link", style: `left: ${pos(lo)}%; width: ${pos(hi) - pos(lo)}%` }));
      }
      if (isNum(r.b)) out.push(el("span", { class: "db__b", style: `left: ${pos(r.b)}%` }));
      if (isNum(r.a)) out.push(el("span", { class: "db__a", style: `left: ${pos(r.a)}%` }));
      return out;
    };
    const ariaFor = (r) => r.aria || `${r.label}: ${aLabel} ${val(r.a)}, ${bLabel} ${val(r.b)}`;
    const tipFor = (r) => r.tip || { title: r.label, rows: [{ strong: val(r.a), text: aLabel }, { strong: val(r.b), text: bLabel }] };
    const legend = el("ul", { class: "legend" },
      el("li", { class: "legend__item" }, keyMark("rc-key--a"), aLabel),
      el("li", { class: "legend__item" }, keyMark("rc-key--b"), bLabel),
      bands.length ? el("li", { class: "legend__item" }, keyMark("rc-key--edge"), o.boundaryLabel || "Band boundary") : null,
      legendItems(o.legendExtra));
    const table = () => tableTwin({
      label: `${o.title || o.label || "Chart"}, as a table`,
      count: `Showing all ${rows.length} ${plural(rows.length, "row", "rows")} from the chart.`,
      head: [{ text: o.labelHeader || "Name" }, { text: aLabel, num: true }, { text: bLabel, num: true },
        { text: o.endHead || "Difference", num: true }, ...(o.twinHead || []).map((t) => ({ text: t }))],
      rows: rows.map((r) => [r.label, { text: val(r.a), num: true }, { text: val(r.b), num: true },
        { text: r.endText != null ? r.endText : "–", num: true }, ...(r.twin || [])]),
    });
    return rowChart(o, "db", {
      rows, plot, ariaFor, tipFor, legend, table, lines,
      axis: [strip, tickRow],
      nameHead: o.bandLabel || "",
      end: (r) => [el("span", { class: "rc__num", text: r.endText != null ? r.endText : "" }), r.glyph || el("span", { class: "rc__noglyph" })],
      endHead: o.endHead || "Difference",
      note: o.note ? el("p", { class: "chart-note", text: o.note }) : null,
    });
  }

  /* diverging({rows, lowLabel, highLabel, labelHeader, valueHeader, twinHead,
                legendExtra, onPick, empty, note})
     rows   [{id, label, value, valueText?, glyph?, aria?, tip?, twin?}]: a bar
            from zero, left for a value below zero (series 2) and right above it
            (series 1), with the signed value (and glyph after it) at the bar's
            end. The axis runs the same way both sides, to a round step past the
            largest value. */
  function diverging(opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter((r) => r && isNum(r.value));
    if (!rows.length) return empty(o, o.empty || "Nothing to show yet.");
    const whole = rows.every((r) => Number.isInteger(r.value));
    const top = niceTop(Math.max(...rows.map((r) => Math.abs(r.value))), whole);
    const half = (v) => clamp((Math.abs(v) / top) * 50, 0, 50);
    const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
    const text = (r) => (r.valueText != null ? r.valueText : sign(r.value) + fmt(Math.abs(r.value)));
    const plot = (r) => {
      const w = half(r.value);
      const label = el("span", { class: "dv__val" }, el("span", { class: "rc__num", text: text(r) }), r.glyph || null);
      if (r.value === 0) {
        label.style.left = "calc(50% + 6px)";
        return [el("span", { class: "dv__zero" }), label];
      }
      const neg = r.value < 0;
      if (neg) label.style.right = `calc(${50 + w}% + 6px)`;
      else label.style.left = `calc(${50 + w}% + 6px)`;
      return [el("span", { class: "dv__bar " + (neg ? "is-neg" : "is-pos"), style: `${neg ? "right" : "left"}: 50%; width: ${w}%` }), label];
    };
    const axis = el("span", { class: "db__ticks" }, [-top, -top / 2, 0, top / 2, top].map((v) => el("span", {
      class: "db__tick", style: `left: ${50 + (v / top) * 50}%`, text: sign(v) + tickText(Math.abs(v)),
    })));
    const lines = hairlines([[25, "var(--rule)"], [50, "var(--rule-strong)"], [75, "var(--rule)"]]);
    const ariaFor = (r) => r.aria || `${r.label}: ${text(r)}`;
    const tipFor = (r) => r.tip || { title: r.label, rows: [{ strong: text(r), text: o.valueHeader || "" }] };
    const legend = el("ul", { class: "legend" },
      el("li", { class: "legend__item" }, keyMark("rc-key--neg"), o.lowLabel || "Lower"),
      el("li", { class: "legend__item" }, keyMark("rc-key--pos"), o.highLabel || "Higher"),
      legendItems(o.legendExtra));
    const table = () => tableTwin({
      label: `${o.title || o.label || "Chart"}, as a table`,
      count: `Showing all ${rows.length} ${plural(rows.length, "bar", "bars")} from the chart.`,
      head: [{ text: o.labelHeader || "Name" }, { text: o.valueHeader || "Value", num: true }, ...(o.twinHead || []).map((t) => ({ text: t }))],
      rows: rows.map((r) => [r.label, { text: text(r), num: true }, ...(r.twin || [])]),
    });
    return rowChart(o, "dv", {
      rows, plot, ariaFor, tipFor, legend, table, lines, axis,
      nameHead: o.labelHeader || "",
      note: o.note ? el("p", { class: "chart-note", text: o.note }) : null,
    });
  }

  /* crossTable({rows, cols, count, rowTitle, colTitle, cellLabel, onPick,
                 selected, diagonalLabel, legendExtra})
     A table of counts: rows [{id, label, short?}] down the side, cols across
     the top, count(ri, ci) in each cell, with row and column totals. Cells are
     shaded on the ordinal ramp in four steps of the largest count (on a square
     root scale, so small counts still differ), the number
     always printed; the diagonal (row i, column i) gets a ring. Each cell is a
     button (one tab stop; the arrow keys move in two directions): onPick(ri,
     ci). cellLabel(ri, ci, n) is its full text; selected [ri, ci] marks the
     picked cell. Below 30rem the short labels stand in for the long ones on
     screen; screen readers keep the long ones. It is a table already, so it
     has no table twin. */
  function crossTable(opts) {
    const o = opts || {};
    const rows = (o.rows || []).filter(Boolean);
    const cols = (o.cols || []).filter(Boolean);
    if (!rows.length || !cols.length) return empty(o, o.empty || "Nothing to show yet.");
    const counts = rows.map((r, ri) => cols.map((c, ci) => Number(o.count(ri, ci)) || 0));
    const max = Math.max(1, ...counts.flat());
    // On a square-root scale, so a few large cells don't leave every small one on the first step.
    const stepFor = (n) => (n <= 0 ? -1 : clamp(Math.ceil(Math.sqrt(n / max) * 4) - 1, 0, 3));
    const pick = typeof o.onPick === "function" ? o.onPick : null;
    const sel = Array.isArray(o.selected) ? o.selected : null;
    const words = (x) => [el("span", { class: "cx__long", text: x.label }),
      el("span", { class: "cx__short", "aria-hidden": "true", text: x.short != null ? x.short : x.label })];
    const cellText = (ri, ci, n) => (typeof o.cellLabel === "function" ? o.cellLabel(ri, ci, n) : `${rows[ri].label}, ${cols[ci].label}: ${fmt(n)}`);

    const buttons = rows.map(() => []);
    const body = el("tbody", null, rows.map((r, ri) => el("tr", null,
      el("th", { scope: "row", class: "cx__rowhead" }, words(r)),
      cols.map((c, ci) => {
        const n = counts[ri][ci];
        const step = stepFor(n);
        const b = el("button", {
          type: "button",
          class: "cx__cell" + (step >= 0 ? ` step-${step}` : " is-zero") + (ri === ci ? " is-diag" : ""),
          tabindex: "-1",
          "aria-label": cellText(ri, ci, n),
          "aria-pressed": sel ? String(sel[0] === ri && sel[1] === ci) : null,
          text: fmt(n),
        });
        buttons[ri][ci] = b;
        return el("td", null, b);
      }),
      el("td", { class: "cx__total" }, fmt(counts[ri].reduce((s, n) => s + n, 0))))));
    const start = sel && buttons[sel[0]] && buttons[sel[0]][sel[1]] ? buttons[sel[0]][sel[1]] : buttons[0][0];
    start.tabIndex = 0;
    const colTotals = cols.map((c, ci) => counts.reduce((s, row) => s + row[ci], 0));
    const table = el("table", { class: "cx", "aria-label": o.title || o.label || "Counts" },
      el("thead", null,
        el("tr", null, el("td", { class: "cx__blank" }),
          el("th", { scope: "colgroup", colspan: cols.length, class: "cx__axis" }, o.colTitle || ""), el("td", { class: "cx__blank" })),
        el("tr", null, el("th", { scope: "col", class: "cx__axis cx__corner" }, o.rowTitle || ""),
          cols.map((c) => el("th", { scope: "col", class: "cx__colhead" }, words(c))),
          el("th", { scope: "col", class: "cx__colhead" }, "Total"))),
      body,
      el("tfoot", null, el("tr", null, el("th", { scope: "row", class: "cx__rowhead" }, "Total"),
        colTotals.map((n) => el("td", { class: "cx__total" }, fmt(n))),
        el("td", { class: "cx__total" }, fmt(colTotals.reduce((s, n) => s + n, 0))))));

    const where = (b) => {
      const ri = buttons.findIndex((row) => row.includes(b));
      return ri < 0 ? null : [ri, buttons[ri].indexOf(b)];
    };
    body.addEventListener("click", (e) => {
      const b = e.target.closest ? e.target.closest(".cx__cell") : null;
      const at = b && where(b);
      if (at && pick) pick(at[0], at[1]);
    });
    body.addEventListener("focusin", (e) => {
      if (!where(e.target)) return;
      buttons.flat().forEach((b) => { b.tabIndex = b === e.target ? 0 : -1; });
    });
    body.addEventListener("keydown", (e) => {
      const at = where(e.target);
      if (!at) return;
      let [r, c] = at;
      switch (e.key) {
        case "ArrowRight": c += 1; break;
        case "ArrowLeft": c -= 1; break;
        case "ArrowDown": r += 1; break;
        case "ArrowUp": r -= 1; break;
        case "Home": c = 0; break;
        case "End": c = cols.length - 1; break;
        default: return;
      }
      e.preventDefault();
      buttons[clamp(r, 0, rows.length - 1)][clamp(c, 0, cols.length - 1)].focus();
    });

    const top = head(o);
    const fig = el("figure", { class: "figure chart-figure", "aria-labelledby": top.id });
    const legend = el("ul", { class: "legend" },
      el("li", { class: "legend__item" }, el("span", { class: "cx-key is-diag", "aria-hidden": "true" }), o.diagonalLabel || "Diagonal"),
      el("li", { class: "legend__item" }, el("span", { class: "legend__word", text: "Fewer" }),
        el("span", { class: "cx-ramp", "aria-hidden": "true" }, [0, 1, 2, 3].map((k) => el("span", { class: `cx-key step-${k}` }))),
        el("span", { class: "legend__word", text: "More" })),
      legendItems(o.legendExtra));
    add(fig, [top.node, legend, el("div", { class: "chart cx-chart" }, table)]);
    if (o.caption) fig.appendChild(el("figcaption", { class: "figure__caption", text: o.caption }));
    return fig;
  }

  // The marks of the charts added after Task 13; the others' CSS is in styles.css.
  if (typeof HB.addStyles === "function") {
    HB.addStyles("charts-more", `
      .sc__zone { fill: color-mix(in srgb, var(--series-1) 9%, transparent); }
      .sc__zone--key { stroke: color-mix(in srgb, var(--series-1) 35%, transparent); stroke-width: 1; }
      .sc .sc__zonelabel { fill: var(--ink-2); font-size: 0.625rem; font-weight: 600; }
      .sc__guide { stroke: var(--rule-strong); stroke-width: 1; stroke-opacity: 0.55; shape-rendering: crispEdges; }

      .chart.rc { overflow: visible; padding: 0 0 var(--s2); container-type: inline-size; }
      .rc { --rc-cols: minmax(7rem, 13rem) minmax(0, 1fr) 4.5rem; font-size: var(--fs-14); line-height: 1.3; }
      .rc--noend { --rc-cols: minmax(7rem, 13rem) minmax(0, 1fr); }
      .rc__head, .rc__row { display: grid; grid-template-columns: var(--rc-cols); column-gap: var(--s3); align-items: center; }
      .rc__head { position: sticky; top: 0; z-index: 2; padding: var(--s2) var(--s2) var(--s1);
        border-bottom: 1px solid var(--rule); border-radius: var(--radius) var(--radius) 0 0; background: var(--surface);
        color: var(--ink-2); font-size: var(--fs-12); }
      .rc__head .rc__name { font-weight: 600; }
      .rc__head .rc__end { display: block; font-weight: 600; text-align: right; }
      .rc__list { margin: 0; padding: var(--s1) 0 0; list-style: none; }
      .rc__item { margin: 0; }
      .rc__row { width: 100%; min-height: 1.75rem; padding: 0.125rem var(--s2); border: 0; border-radius: var(--radius);
        background: transparent; color: var(--ink); font: inherit; text-align: left; cursor: pointer;
        transition: background-color var(--dur-fast) var(--ease); }
      .rc__row:hover, .rc__row:focus-visible { background: color-mix(in srgb, var(--ink) 6%, transparent); }
      .rc__row:focus-visible { outline-offset: -2px; }
      .rc__name { min-width: 0; overflow-wrap: anywhere; }
      .rc__plot { position: relative; align-self: stretch; min-height: 1.5rem; }
      .rc__in { position: absolute; inset: 0 7px; }
      .rc__list .rc__in { background: var(--rc-lines); }
      .rc__end { display: inline-flex; align-items: center; justify-content: flex-end; gap: 0.3rem; }
      .rc__num { font-weight: 600; font-variant-numeric: lining-nums tabular-nums; white-space: nowrap; }
      .rc__end > svg, .rc__noglyph { flex: none; width: 0.875rem; height: 0.875rem; }
      .rc__head .rc__plot { min-height: 2.375rem; }

      .db__strip { position: absolute; left: 0; right: 0; top: 0; height: 1.125rem; }
      .db__band { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center;
        overflow: hidden; box-shadow: inset 1px 0 0 var(--rule-strong); color: var(--ink); font-weight: 600; white-space: nowrap; }
      .db__band.is-alt { background: color-mix(in srgb, var(--ink) 5%, transparent); }
      .db__ticks { position: absolute; left: 0; right: 0; bottom: 0; height: 1rem; }
      .db__tick { position: absolute; bottom: 0; transform: translateX(-50%); white-space: nowrap; font-variant-numeric: tabular-nums; }
      .db__tick.is-first { transform: none; }
      .db__tick.is-last { transform: translateX(-100%); }
      .db__link { position: absolute; top: 50%; height: 2px; margin-top: -1px; background: var(--rule-strong); }
      .db__b, .db__a { position: absolute; top: 50%; border-radius: 50%; box-sizing: border-box; }
      .db__b { width: 14px; height: 14px; margin: -7px 0 0 -7px; border: 2px solid var(--ink-2); background: var(--surface); }
      .db__a { width: 8px; height: 8px; margin: -4px 0 0 -4px; background: var(--series-1); }
      .rc__row:hover .db__a, .rc__row:focus-visible .db__a { background: color-mix(in srgb, var(--series-1) 78%, var(--ink)); }

      .rc--dv .rc__in { inset: 0 3.75rem; }
      .rc--dv .rc__head .rc__plot { min-height: 1.25rem; }
      .dv__bar { position: absolute; top: 50%; height: 0.875rem; transform: translateY(-50%); }
      .dv__bar.is-pos { background: var(--series-1); border-radius: 0 4px 4px 0; }
      .dv__bar.is-neg { background: var(--series-2); border-radius: 4px 0 0 4px; }
      .dv__zero { position: absolute; left: 50%; top: 50%; width: 2px; height: 0.875rem; margin-left: -1px;
        transform: translateY(-50%); background: var(--ink-2); }
      .dv__val { position: absolute; top: 50%; display: inline-flex; align-items: center; gap: 0.25rem; transform: translateY(-50%);
        font-size: var(--fs-12); }
      .dv__val > svg { flex: none; width: 0.8125rem; height: 0.8125rem; }

      .rc-key { flex: none; display: inline-block; box-sizing: border-box; }
      .rc-key--a { width: 8px; height: 8px; border-radius: 50%; background: var(--series-1); }
      .rc-key--b { width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--ink-2); background: var(--surface); }
      .rc-key--edge { width: 1px; height: 12px; background: var(--rule-strong); }
      .rc-key--pos, .rc-key--neg { width: 12px; height: 10px; }
      .rc-key--pos { background: var(--series-1); border-radius: 0 3px 3px 0; }
      .rc-key--neg { background: var(--series-2); border-radius: 3px 0 0 3px; }

      @container (width < 34rem) {   /* a query styles the container's insides, so the columns are set on the rows */
        .rc__head, .rc__row { --rc-cols: minmax(0, 1fr) 4rem; }
        .rc--noend .rc__head, .rc--noend .rc__row { --rc-cols: minmax(0, 1fr); }
        .rc__row .rc__name { grid-column: 1 / -1; padding-top: 0.125rem; font-size: var(--fs-12); font-weight: 600; }
        .rc__head .rc__name { display: none; }
        .rc--dv .rc__in { inset: 0 3.25rem; }
        .db__tick.is-crowded { visibility: hidden; }
      }

      .cx-chart { container-type: inline-size; }
      .cx { border-collapse: separate; border-spacing: 4px; margin: -4px; font-size: var(--fs-14); }
      .cx th { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; line-height: 1.25; }
      .cx__axis { padding: 0 0 var(--s1); color: var(--ink); text-align: left; }
      .cx thead tr:first-child .cx__axis { text-align: center; }
      .cx__corner { text-align: right; vertical-align: bottom; }
      .cx__colhead { position: relative; width: 5.5rem; padding: 0 2px var(--s1); vertical-align: bottom; text-align: center; }
      .cx__rowhead { position: relative; padding: 0 var(--s2) 0 0; text-align: right; white-space: nowrap; }
      .cx td { padding: 0; }
      .cx__short { display: none; }
      .cx__cell { display: block; width: 100%; min-width: 3rem; height: 2.5rem; padding: 0; border: 0; border-radius: 3px;
        background: var(--surface); color: var(--ink); font: inherit; font-weight: 600; font-variant-numeric: lining-nums tabular-nums;
        cursor: pointer; box-shadow: inset 0 0 0 1px var(--rule); }
      .cx__cell.is-zero { color: var(--ink-2); font-weight: 400; }
      .cx__cell.step-0, .cx-key.step-0 { background: var(--step-0); color: var(--on-step-0); box-shadow: none; }
      .cx__cell.step-1, .cx-key.step-1 { background: var(--step-1); color: var(--on-step-1); box-shadow: none; }
      .cx__cell.step-2, .cx-key.step-2 { background: var(--step-2); color: var(--on-step-2); box-shadow: none; }
      .cx__cell.step-3, .cx-key.step-3 { background: var(--step-3); color: var(--on-step-3); box-shadow: none; }
      .cx__cell.is-diag, .cx-key.is-diag { box-shadow: 0 0 0 2px var(--ink); }
      .cx__cell.is-zero.is-diag { box-shadow: inset 0 0 0 1px var(--rule), 0 0 0 2px var(--ink); }
      .cx__cell[aria-pressed="true"] { box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--accent); }
      .cx__cell:hover { filter: brightness(0.94); }
      .cx__total { color: var(--ink); font-weight: 600; text-align: center; font-variant-numeric: lining-nums tabular-nums; }
      .cx tfoot th, .cx tfoot td { padding-top: var(--s1); }
      .cx-key { display: inline-block; width: 0.75rem; height: 0.75rem; border-radius: 2px; }
      .cx-key.is-diag { margin: 2px; background: var(--surface); }
      .cx-ramp { display: inline-flex; gap: 2px; }
      @container (width < 30rem) {
        .cx__long { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
        .cx__short { display: inline; }
        .cx__colhead { width: auto; min-width: 0; }
        .cx__cell { min-width: 2.125rem; height: 2.25rem; }
      }
      @media (prefers-reduced-motion: reduce) { .rc__row { transition: none; } }
    `);
  }

  /* ---- Over time: trendLine and lines ------------------------------------------- */

  /* trendLine({points, current, max})
     A small inline line (7rem by 2rem) for a row's past editions: points
     [{label, value}] in order, joined by a line, with the band between their
     lowest and highest shaded; current {label, value} (this run) is a filled
     dot after a dashed step, so a figure outside the shaded band breaks the
     row's trend. Decorative (aria-hidden): the caller prints the figures
     beside it. Returns an <svg>. */
  function trendLine(opts) {
    const o = opts || {};
    const past = (o.points || []).filter((p) => p && isNum(p.value));
    const now = o.current && isNum(o.current.value) ? o.current : null;
    const W = 112;
    const H = 32;
    const pad = 4;
    const values = past.map((p) => p.value).concat(now ? [now.value] : []);
    const max = isNum(o.max) && o.max > 0 ? Math.max(o.max, ...values) : Math.max(1, ...values);
    const n = past.length + (now ? 1 : 0);
    const sx = (i) => (n <= 1 ? W / 2 : pad + (i * (W - 2 * pad)) / (n - 1));
    const sy = (v) => H - pad - (clamp(v, 0, max) / max) * (H - 2 * pad);
    const node = svg("svg", { class: "spark", viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true", focusable: "false" });
    if (past.length >= 2) {
      const lo = Math.min(...past.map((p) => p.value));
      const hi = Math.max(...past.map((p) => p.value));
      node.appendChild(svg("rect", { class: "spark__range", x: 0, width: W, y: sy(hi) - 1, height: Math.max(2, sy(lo) - sy(hi) + 2) }));
    }
    node.appendChild(svg("line", { class: "spark__base", x1: 0, x2: W, y1: sy(0), y2: sy(0) }));
    if (past.length > 1) {
      node.appendChild(svg("path", { class: "spark__past", d: past.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)} ${sy(p.value).toFixed(1)}`).join(" ") }));
    }
    if (now && past.length) {
      const k = past.length - 1;
      node.appendChild(svg("line", { class: "spark__step", x1: sx(k), y1: sy(past[k].value), x2: sx(k + 1), y2: sy(now.value) }));
    }
    past.forEach((p, i) => node.appendChild(svg("circle", { class: "spark__dot", cx: sx(i), cy: sy(p.value), r: 1.75 })));
    if (now) node.appendChild(svg("circle", { class: "spark__now", cx: sx(past.length), cy: sy(now.value), r: 3.5 }));
    return node;
  }

  /* lines({series, xs, max, suffix, guides, yLabel, labelHeader, highlightLabel,
            othersLabel, pastLabel, nowLabel, onPick, empty, note})
     One line per series across xs [{id, label, now?}], in order; the x marked
     now (this run, the last) sits in a shaded column and is reached by a
     dashed step, its point a filled dot. series [{id, label, values: {x id:
     number}, highlight?, note?}]: a gap in values breaks the line. Highlighted
     series are drawn in series 1 with a direct label at their end and the rest
     turn grey; with none highlighted, every series is drawn in series 1 (and
     labelled when there are eight or fewer). The pointer takes the nearest
     point within HIT_RADIUS and lifts its line; one tab stop, the arrow keys
     move between lines, Enter or a click calls onPick(id). guides: band
     boundaries drawn across the plot (the ticks then sit on them). */
  function lines(opts) {
    const o = opts || {};
    const xs = (o.xs || []).filter(Boolean);
    const all = (o.series || []).filter((s) => s && xs.some((x) => isNum((s.values || {})[x.id])));
    if (!all.length || xs.length < 2) return empty(o, o.empty || "Nothing to show yet.");
    const pick = typeof o.onPick === "function" ? o.onPick : null;
    const suffix = o.suffix || "";
    const val = (v) => (isNum(v) ? fmt(v) + suffix : "–");
    const anyHighlight = all.some((s) => s.highlight);
    const labelled = (s) => (anyHighlight ? !!s.highlight : all.length <= 8);
    const order = all.slice().sort((a, b) => COLLATOR.compare(String(a.label), String(b.label)));
    const guides = (o.guides || []).filter(isNum);
    let max = isNum(o.max) && o.max > 0 ? o.max : 0;
    all.forEach((s) => xs.forEach((x) => { const v = s.values[x.id]; if (isNum(v)) max = Math.max(max, v); }));
    if (!isNum(o.max)) max = niceTop(max, false);
    const whole = all.every((s) => Object.values(s.values || {}).every((v) => !isNum(v) || Number.isInteger(v)));
    const name = o.title || o.label || "Lines";
    const nowLabel = o.nowLabel || "This run";
    const ariaFor = (s) => `${s.label}: ` + xs.map((x) => `${x.label} ${val(s.values[x.id])}`).join(", ") + (s.note ? `. ${s.note}` : "");
    const tipFor = (s) => ({ title: s.label, rows: [...xs.map((x) => ({ strong: val(s.values[x.id]), text: x.label })), s.note], hint: pick ? PICK_HINT : null });

    const plot = svg("svg", { class: "lc" + (pick ? " lc--pick" : ""), role: "group", "aria-label": name, tabindex: "-1" });
    const chart = el("div", { class: "chart chart--lines" }, o.yLabel ? el("p", { class: "sc__ytitle", "aria-hidden": "true", text: o.yLabel }) : null, plot);
    const tip = makeTip();
    let geo = null;
    let nodes = [];
    let active = 0;
    let over = -1;

    function draw(width) {
      const size = Math.round(clamp(width, 280, 900));
      const room = order.some(labelled) ? Math.min(150, Math.max(...order.filter(labelled).map((s) => String(s.label).length * 6.2)) + 14) : 32;
      const m = { l: 40, r: room, t: 12, b: 36 };
      const height = 260;
      const step = (size - m.l - m.r - 24) / (xs.length - 1);
      const sx = (i) => m.l + 12 + i * step;
      const sy = (v) => m.t + (height - m.t - m.b) * (1 - clamp(v, 0, max) / max);
      geo = { size, height, sx, sy };
      const hadFocus = plot.contains(document.activeElement);
      plot.replaceChildren();
      plot.setAttribute("viewBox", `0 0 ${size} ${height}`);
      plot.setAttribute("width", size);
      plot.setAttribute("height", height);

      const frame = svg("g", { "aria-hidden": "true" });
      xs.forEach((x, i) => {
        if (x.now) frame.append(svg("rect", { class: "lc__now", x: sx(i) - 28, y: m.t, width: 56, height: height - m.t - m.b }));
        frame.append(svg("text", { class: "sc__tick" + (x.now ? " lc__nowtick" : ""), x: sx(i), y: height - m.b + 18, "text-anchor": "middle", text: x.label }));
      });
      const tickValues = guides.length ? [...new Set([0, ...guides.filter((g) => g > 0 && g < max), max])].sort((a, b) => a - b) : ticks(max, 4, whole);
      let lastY = Infinity;
      tickValues.forEach((t) => {
        frame.append(svg("line", { class: t === 0 ? "sc__axis" : guides.includes(t) ? "sc__guide" : "sc__gridline", x1: m.l, x2: size - m.r + 8, y1: sy(t), y2: sy(t) }));
        if (lastY - sy(t) >= 12) {
          frame.append(svg("text", { class: "sc__tick", x: m.l - 8, y: sy(t) + 4, "text-anchor": "end", text: tickText(t) + (t === max ? suffix : "") }));
          lastY = sy(t);
        }
      });

      const group = svg("g");
      nodes = order.map((s, k) => {
        const g = svg("g", {
          class: "lc__s" + (s.highlight ? " is-highlight" : anyHighlight ? " is-muted" : ""),
          tabindex: k === active ? "0" : "-1", role: pick ? "button" : "img", "aria-label": ariaFor(s),
        });
        let d = "";
        let prev = null;
        xs.forEach((x, i) => {
          const v = s.values[x.id];
          if (!isNum(v) || x.now) { if (!x.now) prev = null; return; }
          d += `${prev == null ? "M" : "L"}${sx(i).toFixed(1)} ${sy(v).toFixed(1)} `;
          prev = i;
        });
        if (d) g.appendChild(svg("path", { class: "lc__line", d: d.trim() }));
        const ni = xs.findIndex((x) => x.now);
        const before = ni > 0 ? ni - 1 : -1;
        if (ni >= 0 && before >= 0 && isNum(s.values[xs[ni].id]) && isNum(s.values[xs[before].id])) {
          g.appendChild(svg("line", { class: "lc__step", x1: sx(before), y1: sy(s.values[xs[before].id]), x2: sx(ni), y2: sy(s.values[xs[ni].id]) }));
        }
        xs.forEach((x, i) => {
          const v = s.values[x.id];
          if (isNum(v)) g.appendChild(svg("circle", { class: x.now ? "lc__nowdot" : "lc__dot", cx: sx(i), cy: sy(v), r: x.now ? 4 : 2.5 }));
        });
        group.appendChild(g);
        return g;
      });
      nodes.forEach((g, k) => { if (order[k].highlight) group.appendChild(g); });   // highlighted lines on top

      // Direct labels at each labelled line's last point, nudged apart (12px) top to bottom.
      const labels = svg("g", { "aria-hidden": "true" });
      const ends = order.filter(labelled).map((s) => {
        const i = xs.map((x) => isNum(s.values[x.id])).lastIndexOf(true);
        return { s, x: sx(i) + 8, y: sy(s.values[xs[i].id]) + 4 };
      }).sort((a, b) => a.y - b.y);
      ends.forEach((e, i) => { if (i && e.y - ends[i - 1].y < 12) e.y = ends[i - 1].y + 12; });
      ends.forEach((e) => labels.appendChild(svg("text", { class: "sc__label lc__label", x: e.x, y: e.y, text: e.s.label })));

      const hit = svg("rect", { class: "sc__hit", x: m.l - 12, y: m.t - 12, width: size - m.l + 12, height: height - m.t - m.b + 24 });
      plot.append(frame, group, labels, hit);
      if (hadFocus && nodes[active]) nodes[active].focus({ preventScroll: true });
    }

    /** Lifts line k (or none, at -1) above the others. The lines after it move in front of it,
        so a focused line is never taken out of the page (which would lose its focus). */
    function lift(k) {
      nodes.forEach((g, i) => g.classList.toggle("is-active", i === k));
      if (k < 0) return;
      for (let next = nodes[k].nextSibling; next; next = nodes[k].nextSibling) nodes[k].parentNode.insertBefore(next, nodes[k]);
    }

    function nearest(e) {
      const box = plot.getBoundingClientRect();
      if (!box.width) return -1;
      const px = ((e.clientX - box.left) * geo.size) / box.width;
      const py = ((e.clientY - box.top) * geo.height) / box.height;
      let best = -1;
      let bestD = HIT_RADIUS * HIT_RADIUS;
      order.forEach((s, k) => xs.forEach((x, i) => {
        const v = s.values[x.id];
        if (!isNum(v)) return;
        const dx = geo.sx(i) - px;
        const dy = geo.sy(v) - py;
        if (dx * dx + dy * dy < bestD) { bestD = dx * dx + dy * dy; best = k; }
      }));
      return best;
    }

    plot.addEventListener("pointermove", (e) => {
      const k = nearest(e);
      plot.classList.toggle("is-over", k >= 0);
      if (k === over) { if (k >= 0) tip.move(e.clientX, e.clientY); return; }
      over = k;
      lift(k);
      if (k >= 0) tip.at(tipFor(order[k]), e.clientX, e.clientY);
      else tip.hide();
    });
    plot.addEventListener("pointerleave", () => {
      over = -1;
      plot.classList.remove("is-over");
      lift(-1);
      tip.hide();
    });
    plot.addEventListener("click", (e) => {
      const k = nearest(e);
      if (k < 0) return;
      nodes[k].focus({ preventScroll: true });
      if (pick) { tip.hide(); pick(order[k].id); }
    });
    roving(plot, () => nodes, {
      focus: (k) => {
        active = k;
        lift(k);
        const dots = nodes[k].querySelectorAll("circle");
        if (over !== k && dots.length) tip.on(tipFor(order[k]), dots[dots.length - 1], nodes[k]);
      },
      pick: pick ? (k) => { tip.hide(); pick(order[k].id); } : null,
      leave: () => { if (over < 0) { lift(-1); tip.hide(); } },
    });

    draw(640);   // a first drawing for the detached node; the observer redraws it at its real width
    if (typeof ResizeObserver === "function") {
      const watch = new ResizeObserver((entries) => {
        if (!chart.isConnected) { watch.disconnect(); return; }
        const width = Math.round(entries[0].contentRect.width);
        if (width > 0 && Math.round(clamp(width, 280, 900)) !== geo.size) draw(width);
      });
      watch.observe(chart);
    }

    const keySvg = (...marks) => svg("svg", { class: "chart-key lc-key", viewBox: "0 0 28 14", "aria-hidden": "true" }, ...marks);
    const legend = el("ul", { class: "legend" },
      el("li", { class: "legend__item" }, keySvg(svg("g", { class: "lc__s" + (anyHighlight ? " is-highlight" : "") },
        svg("path", { class: "lc__line", d: "M3 10 L14 5 L25 7" }),
        [[3, 10], [14, 5], [25, 7]].map(([cx, cy]) => svg("circle", { class: "lc__dot", cx, cy, r: 2.5 })))),
      o.pastLabel || "Past editions"),
      el("li", { class: "legend__item" }, keySvg(svg("g", { class: "lc__s" + (anyHighlight ? " is-highlight" : "") },
        svg("line", { class: "lc__step", x1: 4, y1: 4, x2: 20, y2: 9 }), svg("circle", { class: "lc__nowdot", cx: 21, cy: 9, r: 4 }))), nowLabel),
      anyHighlight ? el("li", { class: "legend__item" }, keySvg(svg("g", { class: "lc__s is-highlight" }, svg("path", { class: "lc__line", d: "M2 7 L26 7" }))), o.highlightLabel || "Highlighted") : null,
      anyHighlight ? el("li", { class: "legend__item" }, keySvg(svg("g", { class: "lc__s is-muted" }, svg("path", { class: "lc__line", d: "M2 7 L26 7" }))), o.othersLabel || "Others") : null);
    const noted = all.some((s) => s.note);
    const table = () => tableTwin({
      label: `${name}, as a table`,
      count: `Showing all ${all.length} ${plural(all.length, "line", "lines")} from the chart.`,
      head: [{ text: o.labelHeader || "Name" }, ...xs.map((x) => ({ text: x.label, num: true })), ...(noted ? [{ text: "Note" }] : [])],
      rows: order.map((s) => [s.label, ...xs.map((x) => ({ text: val(s.values[x.id]), num: true })), ...(noted ? [s.note || "–"] : [])]),
    });
    return figure(o, { legend, chart, note: o.note ? el("p", { class: "chart-note", text: o.note }) : null, table, tip });
  }

  if (typeof HB.addStyles === "function") {
    HB.addStyles("charts-time", `
      .chart--lines { position: relative; }
      .lc { display: block; max-width: 100%; height: auto; overflow: visible; font-family: var(--sans); font-size: var(--fs-12); }
      .lc text { fill: var(--ink-2); }
      .lc .lc__label { fill: var(--ink); font-weight: 600; paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
      .lc__now { fill: color-mix(in srgb, var(--ink) 5%, transparent); }
      .lc .lc__nowtick { fill: var(--ink); font-weight: 600; }
      .lc__line { fill: none; stroke: var(--series-1); stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; stroke-opacity: 0.7; }
      .lc__step { stroke: var(--series-1); stroke-width: 1.5; stroke-dasharray: 3 3; stroke-opacity: 0.7; }
      .lc__dot { fill: var(--series-1); stroke: var(--surface); stroke-width: 1; }
      .lc__nowdot { fill: var(--series-1); stroke: var(--surface); stroke-width: 2; }
      .lc__s.is-highlight .lc__line, .lc__s.is-highlight .lc__step { stroke-width: 2; stroke-opacity: 1; }
      .lc__s.is-muted .lc__line, .lc__s.is-muted .lc__step { stroke: var(--rule-strong); stroke-width: 1; stroke-opacity: 0.6; }
      .lc__s.is-muted .lc__dot, .lc__s.is-muted .lc__nowdot { fill: var(--rule-strong); }
      .lc__s.is-active .lc__line, .lc__s.is-active .lc__step { stroke: var(--series-1); stroke-width: 2.5; stroke-opacity: 1; }
      .lc__s.is-active .lc__dot, .lc__s.is-active .lc__nowdot { fill: var(--series-1); }
      .lc__s:focus { outline: none; }
      .lc__s:focus-visible .lc__nowdot { stroke: var(--focus); stroke-width: 2.5; }
      .lc--pick.is-over .sc__hit { cursor: pointer; }
      .chart-key.lc-key { width: 1.75rem; }
      .spark { flex: none; display: block; width: 7rem; height: 2rem; overflow: visible; }
      .spark__range { fill: color-mix(in srgb, var(--ink) 9%, transparent); }
      .spark__base { stroke: var(--rule-strong); stroke-width: 1; }
      .spark__past { fill: none; stroke: var(--ink-2); stroke-width: 1.5; stroke-linejoin: round; }
      .spark__dot { fill: var(--ink-2); }
      .spark__step { stroke: var(--series-1); stroke-width: 1.5; stroke-dasharray: 2 2; }
      .spark__now { fill: var(--series-1); stroke: var(--surface); stroke-width: 1.5; }
    `);
  }

  HB.charts = { bars, scatterRanges, stateGrid, ordinalGrid, dumbbell, diverging, crossTable, trendLine, lines };
})();
