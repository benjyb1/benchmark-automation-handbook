/* =============================================================================
   Handbook viewer: the one-page summary at the top of Results > Summary
   (HB.resultsSummary). It is drawn by results.js and reads only the bundle, so it
   is the same on the local page and on a published copy, in the free and the Full
   edition. It uses nothing that is specific to one benchmark: the unit word, the
   band names and their order, the section names and the score sheet's overall
   method all come from the bundle.

   Three parts, in a block that reads as a page of its own (for the owner, for a
   screenshot on a website, and on paper):
     1. The headline figure: the average overall score across the units with every
        question scored, and how many that is out of all the units. A score sheet
        with no overall score gives the average of its first section instead, named;
        one with no scores at all gives only the count. A figure is never made up.
     2. Units in each band: one bar of the overall band set (the set of the figure
        shown, else the first set), best band first, with a legend of names and
        counts that reads without colour.
     3. The biggest movers since last edition: the three largest rises and the
        three largest falls in that figure, from the bundle's comparison
        (results.compare), each opening the unit's comparison in the drawer.
        Without a comparison, one quiet line says when they appear.

   HB.resultsSummary.model()   the figures behind the block (plain data, for tests):
       {name, edition, nouns, total, scored, figure, tiers, movers}
       figure  null | {key, label, value, text, pct}   (value: the mean, one decimal)
       tiers   null | {word, of, rows: [{name, count, share, tone, ink}], none, reason}
       movers  null (no comparison) | {since, key, label, rises, falls, reason}; each
               mover {row, name, gap, ours, theirs, words, was}
   HB.resultsSummary.block(ctx) the block's node; ctx is the page's context (for the drawer). It is the Full
       edition's: when the bundle's edition is "free" it returns null and nothing is drawn in its place.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, isNum, fmtInt, fmtNum, plural } = HB;
  const MINUS = "−";
  const SHOWN = 3;           // movers each way

  const bundle = () => HB.data.bundle() || {};
  const parts = () => HB.resultsParts || {};
  const capital = (s) => String(s).replace(/^./, (c) => c.toUpperCase());

  /* ---- What the bundle holds -------------------------------------------------------------- */

  function nouns() {
    const p = bundle().project || {};
    const one = String(p.unit_word || "row");
    return { one, many: String(p.unit_word_plural || one + "s") };
  }

  /** The figures of one unit that the score sheet worked out: {overall, sections: {id: percent}} or null. */
  function totalsOf(u) {
    const t = (bundle().totals || {})[u.id];
    return t && typeof t === "object" ? t : null;
  }

  function sectionPercent(t, id) {
    const s = t && t.sections ? t.sections[id] : null;
    return s && isNum(s.percent) ? s.percent : null;
  }

  function hasFigure(t, sectionIds) {
    return t && (isNum(t.overall) || sectionIds.some((id) => sectionPercent(t, id) != null));
  }

  /** Whether every question that applies to the unit is scored. The score sheet says so when it can
      add up scores (totals.partial); without it, the unit's own cells say. */
  function fullyScored(u, sectionIds) {
    const t = totalsOf(u);
    if (t) return !t.partial && Boolean(hasFigure(t, sectionIds));
    const cells = Object.values((bundle().cells || {})[u.id] || {}).filter((c) => c && c.state !== "not_applicable");
    return cells.length > 0 && cells.every((c) => HB.data.isScored(c));
  }

  const roundTenth = (x) => Math.round((x + 1e-9) * 10) / 10;

  /** Whether the overall score is a percentage (as the Grid reads it); a section's score always is. */
  function overallIsPercent(sheet) {
    const o = (sheet && sheet.overall) || {};
    return o.method === "weighted_mean_of_section_percents" || o.percent === true;
  }

  function sectionList(sheet) {
    return (sheet && Array.isArray(sheet.sections) ? sheet.sections : []).filter((s) => s && s.id != null);
  }

  function nameOfSection(sheet, id) {
    const s = sectionList(sheet).find((x) => String(x.id) === String(id));
    return s && s.name ? String(s.name) : String(id);
  }

  /** The words for a figure's name: "overall score", or the section's name ("Pillar 1: Commitments"). */
  function figureName(sheet, key) {
    return key === "overall" ? "overall score" : nameOfSection(sheet, key);
  }

  /* ---- 1. The headline ---------------------------------------------------------------------- */

  function headline(units, scored, sheet, sectionIds) {
    const usable = sheet && sheet.computable !== false;
    if (!usable) return null;
    const keys = ["overall"].concat(sectionIds);
    for (const key of keys) {
      const values = scored
        .map((u) => (key === "overall" ? (totalsOf(u) || {}).overall : sectionPercent(totalsOf(u), key)))
        .filter(isNum);
      if (!values.length) continue;
      const mean = roundTenth(values.reduce((a, b) => a + b, 0) / values.length);
      const pct = key === "overall" ? overallIsPercent(sheet) : true;
      // Points summed across sections: say what they are out of ("104.4 points out of 150").
      let outOf = null;
      if (!pct && key === "overall" && ((sheet && sheet.overall) || {}).method === "sum_points") {
        const t = scored.map(totalsOf).find((x) => x && x.sections);
        const top = t ? Object.values(t.sections).reduce((n, e) => n + (e && isNum(e.max) ? e.max : 0), 0) : 0;
        if (top > 0) outOf = top;
      }
      return {
        key, pct, value: mean, count: values.length, outOf,
        label: key === "overall" ? "Average overall score" : `Average score for ${nameOfSection(sheet, key)}`,
        text: fmtNum(mean, 1) + (pct ? "%" : ""),
      };
    }
    return null;
  }

  /* ---- 2. Units in each band ------------------------------------------------------------------- */

  function tiers(scored, figure) {
    const sets = parts().bandSets ? parts().bandSets() : [];
    if (!sets.length) return { reason: "The score sheet has no bands, so there is no count for each.", noBands: true };
    const set = (figure && sets.find((s) => s.of === figure.key)) || sets.find((s) => s.of === "overall") || sets[0];
    const word = parts().bandWord(set);
    if (!scored.length) return { word, of: set.of, reason: `No ${nouns().one} has every question scored yet, so there is no count for each ${word}.` };
    const n = set.bands.length;
    const rows = set.bands.map((b, i) => {
      const tone = parts().step(n - 1 - i, n);
      const count = scored.filter((u) => ((totalsOf(u) || {}).bands || {})[set.of] === b.name).length;
      return { name: String(b.name), count, share: count / scored.length, tone: tone.fill, ink: tone.ink };
    });
    const none = scored.length - rows.reduce((s, r) => s + r.count, 0);
    return { word, of: set.of, rows, none, total: scored.length };
  }

  /* ---- 3. The biggest movers -------------------------------------------------------------------- */

  const pointsWords = (g) => `${fmtNum(Math.abs(g), 1)} ${Math.abs(g) === 1 ? "point" : "points"}`;

  function movers(figure, sheet, sectionIds) {
    const c = (bundle().results || {}).compare;
    if (!c || !Array.isArray(c.rows)) return null;
    const p = bundle().project || {};
    const name = c.edition != null ? String(c.edition).trim() : "";
    const mine = String(p.edition || "").trim();
    // "since the 2026 edition" read wrong beside "April to June 2026", and "since 2022" for a re-score of
    // the published 2022 scores (design read, 23 Oct 2026).
    const since = !name || mine.includes(name) || name.includes(mine) ? "last edition" : `the ${name} edition`;
    const rescore = Boolean(name) && name === mine;
    const keys = [figure ? figure.key : null, "overall"].concat(sectionIds).filter((k, i, all) => k && all.indexOf(k) === i);
    const key = keys.find((k) => c.rows.some((r) => isNum((r.ours || {})[k]) && isNum((r.theirs || {})[k])));
    if (!key) return { since, rescore, name, key: null, rises: [], falls: [], reason: "No figure is in both editions yet, so there are no movers to show." };
    const pct = key === "overall" ? overallIsPercent(sheet) : true;
    const suffix = pct ? "%" : "";
    const all = c.rows
      .filter((r) => isNum((r.ours || {})[key]) && isNum((r.theirs || {})[key]))
      .map((r) => {
        const gap = roundTenth(r.ours[key] - r.theirs[key]);
        const u = HB.data.unit(r.row);
        return {
          row: r.row, name: u && u.name ? u.name : String(r.row), gap, ours: r.ours[key], theirs: r.theirs[key],
          words: (gap > 0 ? "+" : MINUS) + pointsWords(gap),
          was: `from ${fmtNum(r.theirs[key], 1)}${suffix} to ${fmtNum(r.ours[key], 1)}${suffix}`,
        };
      })
      .filter((m) => m.gap !== 0);
    const order = (a, b) => Math.abs(b.gap) - Math.abs(a.gap) || b.theirs - a.theirs || a.name.localeCompare(b.name, "en-GB");
    const rises = all.filter((m) => m.gap > 0).sort(order).slice(0, SHOWN);
    const falls = all.filter((m) => m.gap < 0).sort(order).slice(0, SHOWN);
    const reason = !rises.length && !falls.length
      ? `Nothing moved: every ${nouns().one} scored the same as in ${since}.` : null;
    return { since, rescore, name, key, label: figureName(sheet, key), rises, falls, reason };
  }

  /* ---- The model ----------------------------------------------------------------------------------- */

  function model() {
    const b = bundle();
    const p = b.project || {};
    const sheet = b.scoresheet || null;
    const sectionIds = sectionList(sheet).map((s) => String(s.id));
    const units = (b.units || []).filter((u) => u && u.id != null);
    const scored = units.filter((u) => fullyScored(u, sectionIds));
    const figure = headline(units, scored, sheet, sectionIds);
    const edition = p.edition != null ? String(p.edition).trim() : "";
    const name = String(p.name || "").trim();
    return {
      name, edition, nouns: nouns(), total: units.length, scored: scored.length,
      title: edition && !name.includes(edition) ? `${name} ${edition}`.trim() : name,
      figure, tiers: tiers(scored, figure), movers: movers(figure, sheet, sectionIds),
      sheetWorks: Boolean(sheet && sheet.computable !== false),
    };
  }

  /* ---- Drawing --------------------------------------------------------------------------------------- */

  function headlineBlock(m) {
    const n = m.nouns;
    const left = m.total - m.scored;
    const count = `${fmtInt(m.scored)} of ${plural(m.total, n.one, n.many)} scored`;
    const clarify = left > 0 ? `${fmtInt(left)} ${left === 1 ? "has" : "have"} questions still to score and ${left === 1 ? "is" : "are"} left out.` : null;
    if (m.figure) {
      return h("div", { class: "rsum__headline" },
        h("p", { class: "rsum__label" }, m.figure.label),
        h("p", { class: "rsum__value" }, fmtNum(m.figure.value, 1),
          m.figure.pct ? h("span", { class: "rsum__unit" }, "%") : h("span", { class: "rsum__unit rsum__unit--word" }, " points")),
        h("p", { class: "rsum__note" }, m.figure.outOf ? "Out of " + fmtNum(m.figure.outOf) + ", across " : "Across ", h("strong", null, count), "."),
        clarify ? h("p", { class: "rsum__note rsum__note--quiet" }, clarify) : null);
    }
    return h("div", { class: "rsum__headline" },
      h("p", { class: "rsum__label" }, `${capital(n.many)} scored`),
      h("p", { class: "rsum__value" }, fmtInt(m.scored), h("span", { class: "rsum__unit rsum__unit--word" }, ` of ${fmtInt(m.total)}`)),
      h("p", { class: "rsum__note" }, m.sheetWorks
        ? "The score sheet gives no score to average yet, so this is the count of scored " + n.many + "."
        : "The score sheet cannot add up scores yet, so this is the count of scored " + n.many + "."),
      clarify ? h("p", { class: "rsum__note rsum__note--quiet" }, clarify) : null);
  }

  function tiersBlock(m) {
    const t = m.tiers;
    if (t.noBands) return null;          // a heading with only "no bands" under it looked unfinished (design read, 23 Oct 2026)
    const n = m.nouns;
    const title = `${capital(n.many)} in each ${t.word || "band"}`;
    if (!t.rows) {
      return h("div", { class: "rsum__tiers" }, h("h4", { class: "rsum__sub" }, title), h("p", { class: "rsum__quiet" }, t.reason));
    }
    const rows = t.rows.slice();
    if (t.none > 0) rows.push({ name: `No ${t.word}`, count: t.none, share: t.none / t.total, tone: null, ink: null, note: `Below every ${t.word}'s minimum` });
    const bar = h("div", { class: "rsum__bar", "aria-hidden": "true" },
      rows.filter((r) => r.count > 0).map((r) => h("span", {
        class: ["rsum__seg", r.tone == null && "stk-hatch"],
        style: { flexGrow: String(r.count), background: r.tone, color: r.ink },
      }, r.share >= 0.06 ? fmtInt(r.count) : null)));
    const legend = h("ul", { class: "rsum__legend", "aria-label": `${capital(n.many)} in each ${t.word}, best first` },
      rows.map((r) => h("li", { class: "rsum__key" },
        r.tone == null ? h("span", { class: "swatch stk-hatch", "aria-hidden": "true" })
          : h("span", { class: "swatch", style: { "--swatch": r.tone }, "aria-hidden": "true" }),
        h("span", { class: "rsum__key-name" }, r.name),
        h("span", { class: "rsum__key-count" }, h("strong", null, fmtInt(r.count)), h("span", { class: "rsum__key-share" }, ` (${share(r.share)})`)))));
    return h("figure", { class: "rsum__tiers" },
      h("h4", { class: "rsum__sub" }, title),
      bar, legend,
      h("figcaption", { class: "rsum__cap" },
        `Each part of the bar is the share of the ${plural(m.scored, n.one, n.many)} with every question scored that fall in that ${t.word}. ` +
        `The best ${t.word} is on the left.`));
  }

  /** A share as a whole percentage, never 0% for something or 100% while something is left out. */
  function share(x) {
    if (!(x > 0)) return "0%";
    const r = Math.round(x * 100);
    return (r === 0 ? "<1" : r === 100 && x < 1 ? "99" : String(r)) + "%";
  }

  function moverItem(mv, ctx, since) {
    const canOpen = Boolean(HB.compareView && typeof HB.compareView.openDetail === "function");
    const inner = [
      h("span", { class: "rsum__mover-name" }, mv.name),
      h("span", { class: "rsum__mover-gap" }, h("span", { "aria-hidden": "true", class: "rsum__arrow" }, mv.gap > 0 ? "▲" : "▼"), " ", mv.words),
      h("span", { class: "rsum__mover-was" }, mv.was),
    ];
    if (!canOpen) return h("li", { class: "rsum__mover-item" }, h("div", { class: "rsum__mover" }, inner));
    return h("li", { class: "rsum__mover-item" },
      h("button", { type: "button", class: "rsum__mover", onclick: () => HB.compareView.openDetail(mv.row, ctx) },
        inner, h("span", { class: "visually-hidden" }, `. Opens its comparison with ${since}.`)));
  }

  function moversBlock(m, ctx) {
    const mv = m.movers;
    const title = mv && mv.rescore ? "Biggest differences from the published " + mv.name + " scores"
      : "Biggest movers since " + (mv ? mv.since : "last edition");
    if (!mv) {
      return h("div", { class: "rsum__movers" }, h("h4", { class: "rsum__sub" }, title),
        h("p", { class: "rsum__quiet" }, "Movers appear once this edition is compared with last edition's results."));
    }
    if (mv.reason) {
      return h("div", { class: "rsum__movers" }, h("h4", { class: "rsum__sub" }, title), h("p", { class: "rsum__quiet" }, mv.reason));
    }
    const list = (heading, items, none) => h("div", { class: "rsum__col" },
      h("h5", { class: "rsum__col-head" }, heading),
      items.length ? h("ol", { class: "rsum__movers-list" }, items.map((x) => moverItem(x, ctx, mv.since)))
        : h("p", { class: "rsum__quiet" }, none));
    return h("div", { class: "rsum__movers" },
      h("h4", { class: "rsum__sub" }, title),
      h("p", { class: "rsum__cap" }, `Change in the ${mv.label}, in points. Select one to open its comparison.`),
      h("div", { class: "rsum__cols" },
        list("Biggest rises", mv.rises, `No ${m.nouns.one} scored higher.`),
        list("Biggest falls", mv.falls, `No ${m.nouns.one} scored lower.`)));
  }

  /** The free edition keeps the standard Results page: the summary is part of the Full version, and nothing
      stands in its place (no teaser, no line about the Full version). */
  const shown = () => bundle().edition !== "free";

  function block(ctx) {
    if (!shown()) return null;
    const m = model();
    const titleId = HB.uid("rsum");
    return h("section", { class: "rsum", "aria-labelledby": titleId },
      h("header", { class: "rsum__head" },
        h("p", { class: "eyebrow" }, "Results at a glance"),
        h("h3", { class: "rsum__title", id: titleId }, m.title || "Results")),
      h("div", { class: "rsum__body" + (m.tiers.noBands ? " rsum__body--one" : "") }, headlineBlock(m), tiersBlock(m)),
      moversBlock(m, ctx));
  }

  HB.addStyles("resultsummary", `
    .rsum { display: grid; gap: var(--s5); padding: var(--s4); border: 1px solid var(--rule); border-radius: var(--radius);
      background: var(--surface); }
    @media (min-width: 40rem) { .rsum { padding: var(--s6); gap: var(--s6); } }
    .rsum__head { display: grid; gap: var(--s1); }
    .rsum__head > * { margin: 0; }
    .rsum__title { font-size: var(--fs-26); line-height: 1.2; }
    .rsum__body { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s5) var(--s7); align-items: start; }
    @media (min-width: 56rem) { .rsum__body { grid-template-columns: minmax(13rem, 18rem) minmax(0, 1fr); } .rsum__body--one { grid-template-columns: minmax(0, 1fr); } }

    .rsum__headline { display: grid; gap: var(--s1); align-content: start; }
    .rsum__headline > * { margin: 0; }
    .rsum__label { font-size: var(--fs-14); font-weight: 600; line-height: 1.35; }
    .rsum__value { font-family: var(--serif); font-weight: 500; line-height: 1; font-variant-numeric: lining-nums;
      font-size: clamp(3.25rem, 11vw, 5rem); letter-spacing: -0.01em; }
    .rsum__unit { margin-left: 0.1em; font-size: 0.45em; color: var(--ink-2); letter-spacing: 0; }
    .rsum__unit--word { font-size: 0.35em; margin-left: 0.2em; font-family: var(--sans); }
    .rsum__note { font-size: var(--fs-14); line-height: 1.4; }
    .rsum__note--quiet, .rsum__cap, .rsum__quiet { color: var(--ink-2); font-size: var(--fs-12); line-height: 1.4; }
    .rsum__quiet { margin: 0; max-width: var(--measure); }

    .rsum__tiers { display: grid; gap: var(--s3); margin: 0; min-width: 0; }
    .rsum__sub { margin: 0; font-size: var(--fs-16); }
    .rsum__bar { display: flex; gap: 2px; height: 2.75rem; border-radius: 6px; overflow: hidden;
      box-shadow: inset 0 0 0 1px var(--rule-strong); }
    .rsum__seg { flex-basis: 0; flex-shrink: 1; min-width: 2px; display: flex; align-items: center; justify-content: center;
      font-size: var(--fs-14); font-weight: 600; font-variant-numeric: lining-nums tabular-nums; }
    .rsum__legend { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s2) var(--s5);
      grid-template-columns: repeat(auto-fill, minmax(14.5rem, 1fr)); font-size: var(--fs-14); color: var(--ink); }
    .rsum__key { display: flex; align-items: baseline; gap: var(--s2); min-width: 0; }
    .rsum__key .swatch { align-self: center; width: 0.875rem; height: 0.875rem; }
    .rsum__key-name { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
    .rsum__key-count { flex: none; font-variant-numeric: lining-nums tabular-nums; }
    .rsum__key-share { color: var(--ink-2); font-size: var(--fs-12); }
    .rsum__cap { margin: 0; max-width: var(--measure); }

    .rsum__movers { display: grid; gap: var(--s3); padding-top: var(--s5); border-top: 1px solid var(--rule); }
    .rsum__cols { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s4) var(--s6); }
    @media (min-width: 40rem) { .rsum__cols { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    .rsum__col { display: grid; gap: var(--s2); align-content: start; min-width: 0; }
    .rsum__col-head { margin: 0; font-size: var(--fs-14); font-weight: 600; color: var(--ink-2); }
    .rsum__movers-list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s1); }
    .rsum__mover { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: var(--s3); row-gap: 0.125rem;
      width: 100%; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius);
      background: var(--surface); color: var(--ink); font: inherit; text-align: left; align-items: baseline; }
    button.rsum__mover { cursor: pointer; transition: background-color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease); }
    button.rsum__mover:hover { background: var(--accent-soft); border-color: var(--accent); }
    .rsum__mover-name { font-weight: 600; font-size: var(--fs-14); overflow-wrap: anywhere; }
    button.rsum__mover .rsum__mover-name { color: var(--accent); text-decoration: underline; text-underline-offset: 0.15em;
      text-decoration-thickness: 1px; }
    .rsum__mover-gap { font-weight: 600; font-size: var(--fs-14); font-variant-numeric: lining-nums tabular-nums; white-space: nowrap; }
    .rsum__arrow { font-size: 0.7em; color: var(--ink-2); }
    .rsum__mover-was { grid-column: 1 / -1; color: var(--ink-2); font-size: var(--fs-12); font-variant-numeric: lining-nums tabular-nums; }
    @media (prefers-reduced-motion: reduce) { button.rsum__mover { transition: none; } }

    @media print {
      .rsum { break-inside: avoid; page-break-inside: avoid; border-color: #999; }
      .rsum, .rsum * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      .rsum__mover { border-color: #999; }
    }
  `);

  HB.resultsSummary = { model, block, shown };
})((window.HB = window.HB || {}));
