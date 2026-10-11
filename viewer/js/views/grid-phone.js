/* =============================================================================
   Handbook viewer: views/grid-phone.js, the Grid tab on a phone (under 40rem).
   Sets HB.gridPhone(ctx, m) -> Element; grid.js calls it with its model m (the
   groups, sections, totals and m.info(unit, q), see grid.js) and shows the
   result only below 40rem, so the page never scrolls sideways at 375px.

   A list of the rows in view: a sticky header per group (name, count, mean
   overall), then one card per row with its name, overall score, tier, a flag
   summary ("No flags", or a glyph, word and count per factual flag kind) and a
   strip of small numbered squares, one per question (shaded like the table;
   hatched = no evidence; empty outline = not scored yet; a gap for a question
   that does not apply). Each square is an <a data-row data-q> that grid.js's
   one click handler opens in the cell panel.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;

  const STYLE = `
.hbg-pl { display: grid; margin-inline: calc(-1 * var(--gutter-start)) calc(-1 * var(--gutter-end)); border-top: 1px solid var(--rule); }
.hbg-pl__gh { position: sticky; top: 0; z-index: 4; display: flex; align-items: baseline; justify-content: space-between; gap: var(--s2); padding: var(--s2) var(--gutter-end) var(--s1) var(--gutter-start); border-bottom: 1px solid var(--rule); background: var(--paper); }
.hbg-pl__gh h3 { min-width: 0; overflow: hidden; font-family: var(--serif); font-size: var(--fs-16); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.hbg-pl__gh span { flex: none; color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: tabular-nums; }
.hbg-li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px var(--s2); padding: var(--s2) var(--gutter-end) var(--s3) var(--gutter-start); border-bottom: 1px solid var(--rule); background: var(--surface); }
.hbg-li__name { font-size: var(--fs-14); font-weight: 600; line-height: 1.3; overflow-wrap: anywhere; }
.hbg-li__tot { justify-self: end; font-size: var(--fs-16); font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.hbg-li__flags { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--s2); color: var(--ink-2); font-size: var(--fs-12); }
.hbg-li__flags .flag-glyph { width: 0.8125rem; height: 0.8125rem; color: var(--critical); }
.hbg-li__flag { display: inline-flex; align-items: center; gap: 0.25rem; }
.hbg-li .hbg-tier { justify-self: end; align-self: center; }
.hbg-li__strip { grid-column: 1 / -1; display: flex; gap: 6px; margin-top: var(--s1); }
.hbg-li__sec { display: flex; flex: 1 1 0; gap: 2px; min-width: 0; }
.hbg-sq { position: relative; display: grid; place-items: center; flex: 1 1 0; min-width: 0; max-width: 1.375rem; height: 1.375rem; border-radius: 2px; box-shadow: inset 0 0 0 1px var(--rule); color: var(--ink); font-size: 0.6875rem; font-weight: 700; line-height: 1; text-decoration: none; font-variant-numeric: tabular-nums; }
.hbg-sq.hbg-s1, .hbg-sq.hbg-s2, .hbg-sq.hbg-s3, .hbg-sq.hbg-s4 { box-shadow: none; }
.hbg-sq:focus-visible { z-index: 2; outline: 2px solid var(--focus); outline-offset: 1px; }
.hbg-sq.is-ne { color: var(--ink-2); font-weight: 400; box-shadow: inset 0 0 0 1px var(--rule-strong); background-image: repeating-linear-gradient(135deg, color-mix(in srgb, var(--ink-2) 28%, transparent) 0 1px, transparent 1px 4px); }
.hbg-sq.is-wait { box-shadow: inset 0 0 0 1px var(--rule-strong); }
.hbg-sq.is-na { box-shadow: none; }
.hbg-sq .flag-glyph { position: absolute; top: -4px; right: -4px; z-index: 1; width: 0.6875rem; height: 0.6875rem; color: var(--critical); }
`;

  function flagSummary(m, unit) {
    const per = {};
    for (const sec of m.sections) for (const q of sec.qs) {
      const x = m.info(unit, q);
      if (x) for (const k of x.kinds) per[k] = (per[k] || 0) + 1;
    }
    const kinds = HB.flagKinds.factual.filter((k) => per[k]);
    if (!kinds.length) return h("span", { class: "hbg-li__flags" }, "No flags");
    return h("span", { class: "hbg-li__flags" }, kinds.map((k) =>
      h("span", { class: "hbg-li__flag" }, HB.flagGlyph(k), HB.flagKinds.words[k] + " " + fmtInt(per[k]))));
  }

  function square(ctx, m, unit, q) {
    const x = m.info(unit, q);
    if (!x || x.state === "not_applicable") return h("span", { class: "hbg-sq is-na", "aria-hidden": "true" });
    const cls = ["hbg-sq"];
    if (x.state === "answered" || x.state === "no_evidence") cls.push("hbg-s" + x.step);
    if (x.state === "no_evidence") cls.push("is-ne");
    if (x.state === "not_run") cls.push("is-wait");
    if (x.hl) cls.push("is-hl");
    if (x.rests) cls.push("is-rest");
    return h("a", {
      class: cls.join(" "),
      href: ctx.href("grid", unit.id, q.id),
      "data-row": unit.id,
      "data-q": q.id,
      "aria-label": x.label,
      title: x.label,
    }, x.text, x.kinds.length ? HB.flagGlyph(x.kinds[0]) : null);
  }

  function card(ctx, m, unit) {
    const t = m.totals(unit);
    return h("div", { class: "hbg-li", role: "listitem" },
      h("span", { class: "hbg-li__name" }, unit.name || unit.id),
      h("span", { class: "hbg-li__tot" }, m.overallText(t),
        t && t.partial ? h("span", { class: "visually-hidden" }, " (partial)") : null),
      flagSummary(m, unit),
      m.bandNode(m.bandName(unit)),
      h("span", { class: "hbg-li__strip" }, m.sections.map((sec) =>
        h("span", { class: "hbg-li__sec" }, sec.qs.map((q) => square(ctx, m, unit, q))))));
  }

  function gridPhone(ctx, m) {
    HB.addStyles("grid-phone", STYLE);
    return h("div", { class: "hbg-pl" }, m.groups.map((g) => {
      const head = [];
      if (m.grouped) {
        const mean = m.groupMean(g.rows);
        head.push(h("div", { class: "hbg-pl__gh" }, h("h3", null, g.title),
          h("span", null, fmtInt(g.rows.length) + (mean === null ? "" : " · mean " + m.overallText({ overall: Math.round(mean) })))));
      }
      return h("section", { class: "hbg-pl__g", "aria-label": g.title }, head,
        h("div", { role: "list" }, g.rows.map((u) => card(ctx, m, u))));
    }));
  }

  HB.gridPhone = gridPhone;
})((window.HB = window.HB || {}));
