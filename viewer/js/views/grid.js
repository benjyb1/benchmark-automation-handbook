/* =============================================================================
   Handbook viewer: views/grid.js, the Grid tab (the landing tab once scoring has
   started). Registers HB.views.grid.

   The top of the tab is one quiet line, so the data starts high on the screen:
     how many rows are in view ("142 countries", or "12 of 142 countries" with
     the filters in use named and a Clear button), how many answers, how many to
     check (factual flags), then View and "Add documents or instructions". View
     opens a panel holding the rest: group by (a segmented control when there are
     at most four labels, else a select), one select per label under "Show only",
     Strips | Table, the switch that outlines the answers resting on open rules,
     and "What the marks mean" (the key, each mark carrying its count in view).
     Scoring progress is the run bar's to show, so the Grid does not repeat it;
     unscored cells show as empty outlined boxes. Nothing in the bundle says
     which row is being scored now, so no row is marked.

   Wide screens (40rem and up), two views (HB.state.gridView, kept per viewer):
     Strips  every row as a thin strip of marks, one per question (bar height =
             share of the question's top points; hatched = no evidence; blank =
             does not apply; empty outline = not scored yet; a red line above =
             a factual flag), section gaps between mark groups, then the overall
             score and tier. Rows sit under group rows (name, count, mean overall,
             flagged count, "Open as table"); an opened group shows its full
             table in place (HB.state.openGroups, for the session).
     Table   one table for every row in view, with a divider row per group.
   Phones (under 40rem): a list of cards per row (views/grid-phone.js draws it
   from the same model, HB.gridPhone).

   Flags: a cell carries a glyph per factual flag kind (HB.flagGlyph, util.js);
   the Key says what each kind means and the mark's label names its kinds.
   Claude's reading (the "judgement" flag) is not on cells: under each question
   id a dashed box counts the answers in view resting on open rules (hover or
   focus lists the rules), and the switch outlines those cells (dashed).

   Documents (these used to be the Documents tab, views/grid-gaps.js): a row with
   something Claude expected but did not find, or a document it could not keep a
   copy of, has a small "N missing" button after its name that opens the list with
   a Send button for each. While documents are still being found, a row with no
   answer yet says "N documents found" (or "No documents found yet") in place of
   its empty cells. HB.state.onlyRows (a list of row ids, set by the chat and the
   step brief) narrows the Grid to those rows, with a "Show all" button.

   Every mark, square and cell is an <a data-row data-q href> with a full
   aria-label; one click handler on the tab opens the cell panel (cell.js). The
   panel adds .is-open to the open cell's links (a 2px ink outline here).
   After each draw HB.gridRows() returns the row ids in display order (after
   filters), for the panel's Previous and Next.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt, fmtNum, isNum } = HB;
  const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });
  const DASH = "–";
  const NONE = "\u0000none";       // the filter value for "label not recorded"
  const SINGLES = "\u0000singles";    // the key of the group that gathers groups of one
  const STEPS = 4;                 // fill steps above zero: up to 25, 50, 75, 100%
  const SEP = "\u0000";
  const RULE_OPEN = new Set(["open", "skipped"]);
  // What each factual flag means, for the Key ({unit} is the project's unit word).
  const FLAG_MEANING = {
    quote_not_found: "a quote didn't match its saved document word for word",
    document_changed: "a saved document changed after this {unit} was scored; it will be scored again from the new copy",
    group_differs: "another {unit} in the same group cited the same document and got a different answer",
    after_cutoff: "a document cited is dated after the cut-off",
    todays_version: "a web page cited was saved as it is today, with no copy from before the cut-off",
    page_gone: "most of the text of a web page cited has gone from the live site; the answer still reads the earlier copy until you choose what to do",
    reads_disagree: "a second, independent read gave a different answer",
    weak_source: "every document cited is a weak source, such as a copy on another site",
    owner_changed: "the owner corrected this answer; open it to see whether Claude agreed",
    holds_back: "this answer keeps the {unit} below its next level, which needs every question below it met, so it is worth a second look",
    needs_document: "scored as if there were nothing, because Claude needed a document it could not get; you can add it",
  };
  let lastHighlight = "";          // the highlight last scrolled into view

  const STYLE = `
.hb-view[data-view="grid"] { gap: var(--s2); }
.hbg-more { margin: 0 0 var(--s1); }
.hbg-bar { position: relative; display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s3); }
.hbg-key.hbg-view { position: static; }   /* the panel hangs from the bar's right edge, wherever View wraps to */
.hbg-bar__grow { flex: 1 1 0; min-width: 0; }
.hbg-seg > .btn { min-height: 1.875rem; padding-inline: 0.625rem; font-size: var(--fs-12); }
.hbg-sel { width: auto; min-height: 2rem; max-width: 13rem; padding: 0.1875rem 1.75rem 0.1875rem var(--s2); font-size: var(--fs-14); }
.hbg-group .hbg-sel { max-width: 15rem; }
.hbg-sel.is-set { border-color: var(--accent); background-color: var(--accent-soft); }
.hbg-filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.hbg-group, .hbg-group__seg { display: inline-flex; align-items: center; gap: var(--s2); }
.hbg-only-phone, .hbg-key__list .hbg-k-phone { display: none; }
.hbg-key { position: relative; }
.hbg-key > summary { list-style: none; }
.hbg-key > summary::-webkit-details-marker { display: none; }
.hbg-key__panel { position: absolute; top: calc(100% + 4px); right: 0; z-index: 8; display: grid; gap: var(--s2); width: min(24rem, calc(100vw - 2 * var(--gutter))); padding: var(--s3); border: 1px solid var(--rule-strong); border-radius: var(--radius); background: var(--surface); box-shadow: 0 6px 20px var(--shadow); font-size: var(--fs-12); }
.hbg-key__panel h3 { font-family: var(--sans); font-size: var(--fs-12); font-weight: 700; }
.hbg-key__list { display: grid; gap: 0.375rem; margin: 0; padding: 0; list-style: none; color: var(--ink-2); }
.hbg-key__list li { display: flex; align-items: center; gap: var(--s2); }
.hbg-key__list li > :first-child { flex: none; }
.hbg-key__list li > .hbg-key__text { flex: 1 1 auto; min-width: 0; }   /* a line with no sample wraps inside the panel */
.hbg-key__ramp { display: inline-flex; gap: 2px; }
.hbg-key__ramp .hbg-cell { width: 1.25rem; height: 1rem; }
.hbg-key .flag-glyph { width: 0.875rem; height: 0.875rem; color: var(--critical); }
.hbg-counts { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
.hbg-counts b { color: var(--ink); font-weight: 650; }
.hbg-counts__part + .hbg-counts__part::before { content: "\\00b7"; margin-right: var(--s3); color: var(--rule-strong); }
.hbg-counts__part { display: inline-flex; align-items: center; gap: 0.3em; }
.hbg-clear { min-height: 0; padding: 0 0.375rem; }
.hbg-view__panel { width: min(46rem, calc(100vw - 2 * var(--gutter))); max-height: min(75vh, 40rem); overflow: auto; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: var(--s3) var(--s4); align-items: start; font-size: var(--fs-14); }
.hbg-view__arrange { display: grid; gap: var(--s3); }
.hbg-view__row { display: grid; justify-items: start; gap: var(--s1); }
.hbg-view .hbg-filters { flex-direction: column; align-items: stretch; width: 100%; }
.hbg-view .hbg-filters .hbg-sel { width: 100%; max-width: none; }
.hbg-view .hbg-filters .btn { align-self: start; }
.hbg-key__more > summary { color: var(--ink-2); cursor: pointer; }
.hbg-key__more > .hbg-key__list { margin-top: var(--s2); }
.hbg-view__row .hbg-group { flex-wrap: wrap; }
.hbg-view__label { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; }
.hbg-view__key { display: grid; gap: var(--s2); padding-left: var(--s4); border-left: 1px solid var(--rule); font-size: var(--fs-12); }
.hbg-key__n { flex: none; margin-left: auto; padding-left: var(--s2); color: var(--ink); font-variant-numeric: tabular-nums; }
.hbg-switch { display: inline-flex; align-items: center; gap: var(--s2); color: var(--ink); font-size: var(--fs-14); cursor: pointer; }
.hbg-switch input { appearance: none; position: relative; flex: none; width: 2rem; height: 1.125rem; margin: 0; border: 1px solid var(--rule-strong); border-radius: 999px; background: var(--surface); cursor: pointer; }
.hbg-switch input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 0.75rem; height: 0.75rem; border-radius: 50%; background: var(--ink-2); transition: transform var(--dur-fast) var(--ease); }
.hbg-switch input:checked { border-color: var(--accent); background: var(--accent); }
.hbg-switch input:checked::after { transform: translateX(0.875rem); background: var(--on-accent); }
.hbg-switch input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.hbg-rm { display: inline-grid; place-items: center; min-width: 1.125rem; height: 0.875rem; padding: 0 2px; border: 1px dashed var(--ink-2); border-radius: 2px; color: var(--ink); font-family: var(--sans); font-size: 0.625rem; font-weight: 600; line-height: 1; font-variant-numeric: tabular-nums; }
.hbg-rest { position: relative; display: inline-grid; margin-top: 2px; cursor: help; }
.hbg-rest:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.hbg-tip { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
:is(.hbg-qid, .hbg-rest):is(:hover, :focus) > .hbg-tip { top: calc(100% + 3px); left: 0; z-index: 9; width: max-content; max-width: 18rem; height: auto; overflow: visible; clip-path: none; padding: 3px 7px; border-radius: 3px; background: var(--ink); color: var(--surface); font-family: var(--sans); font-size: var(--fs-12); font-weight: 500; line-height: 1.35; text-align: left; white-space: normal; }
:is(.hbg-qid, .hbg-rest):is(:hover, :focus) > .hbg-tip--end { left: auto; right: 0; }
.hbg-qid { position: relative; display: inline-block; padding: 0 2px; border-radius: 3px; color: var(--ink); font-family: var(--mono); cursor: help; }
.hbg-qid:focus-visible { outline: 2px solid var(--focus); outline-offset: 0; }

/* Strips */
/* The strips box is its own scroller (both ways, like the Table view), so a benchmark with many
   questions scrolls sideways inside it. Every child is at least --ov-w wide (the columns at their
   widest), the row names stay pinned on the left, Overall and Tier on the right, and the section
   names sit in the same column as their marks, so they move together. The name column is as wide
   as the longest row or group name (fitNames, at most 28rem), so short names such as producers
   don't leave a gap before the marks and long names such as trial titles are not cut. */
.hbg-ov { --mk-w: 18px; --mk-gap: 3px; --sec-gap: 14px; --strip-w: calc(var(--nq) * var(--mk-w) + (var(--nq) - var(--ns)) * var(--mk-gap) + (var(--ns) - 1) * var(--sec-gap)); --name-min: 8rem; --name-max: clamp(15rem, 100cqi - var(--strip-w) - var(--num-w) - var(--tier-w) - 3 * var(--ov-gap) - 2 * var(--ov-pad) - 2px, 28rem); --num-w: 3.5rem; --tier-w: 7rem; --ov-gap: var(--s4); --ov-pad: var(--s3); --ov-cols: minmax(var(--name-min), var(--name-max)) var(--strip-w) var(--num-w) minmax(var(--tier-w), 1fr); --ov-w: calc(var(--name-max) + var(--strip-w) + var(--num-w) + var(--tier-w) + 3 * var(--ov-gap) + 2 * var(--ov-pad)); position: relative; max-height: calc(100dvh - 2rem); overflow: auto; overscroll-behavior-x: contain; container-type: inline-size; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); font-size: var(--fs-14); }
.hbg-ov > * { min-width: var(--ov-w); }
.hbg-ov__head, .hbg-ov__r, .hbg-ov__gh { display: grid; grid-template-columns: var(--ov-cols); align-items: center; gap: 0 var(--ov-gap); padding-inline: var(--ov-pad); }
.hbg-ov__head { position: sticky; top: 0; z-index: 5; align-items: end; padding-block: var(--s2) var(--s1); border-bottom: 1px solid var(--rule-strong); border-radius: var(--radius) var(--radius) 0 0; background: var(--surface); color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; }
.hbg-ov__head > .hbg-num { text-align: right; }
.hbg-ov__pin-l, .hbg-ov__pin-n, .hbg-ov__pin-r { position: sticky; z-index: 4; align-self: stretch; background: inherit; }
.hbg-ov__pin-l { left: 0; margin-inline: calc(-1 * var(--ov-pad)) calc(-1 * var(--ov-gap)); padding-inline: var(--ov-pad) var(--ov-gap); box-shadow: inset -1px 0 0 var(--rule); }
.hbg-ov__pin-n { right: calc(var(--tier-w) + var(--ov-pad)); display: flex; align-items: center; justify-content: flex-end; margin-inline: calc(-1 * var(--ov-gap)); padding-inline: var(--ov-gap); box-shadow: inset 1px 0 0 var(--rule); }
.hbg-ov__pin-r { right: 0; display: flex; align-items: center; min-width: 0; margin-right: calc(-1 * var(--ov-pad)); padding-right: var(--ov-pad); }
.hbg-ov__pin-r > * { min-width: 0; }
.hbg-ov__head > :is(.hbg-ov__pin-l, .hbg-ov__pin-n, .hbg-ov__pin-r) { display: flex; align-items: flex-end; }
.hbg-ov__gh > .hbg-ov__pin-l { align-self: center; }
.hbg-ov__gh > .hbg-ov__pin-r { justify-content: flex-end; }
.hbg-ov__secs, .hbg-ov__mk { display: flex; gap: var(--sec-gap); }
.hbg-ov__sec { display: grid; grid-template-rows: 1fr auto; gap: 2px; min-width: 0; }   /* the numbers sit on one line under names of any height */
.hbg-ov__sec > .hbg-ov__secname { align-self: end; }
.hbg-ov__secname.is-short { font-size: 0; line-height: 0; }   /* partly behind a pinned column: see edgeNames */
.hbg-ov__secname.is-short::after { content: attr(data-short); font-size: var(--fs-12); line-height: 1.2; white-space: nowrap; }
.hbg-ov__secname.is-cut { visibility: hidden; }
.hbg-ov__secname { width: 0; min-width: 100%; color: var(--ink); line-height: 1.2; overflow-wrap: anywhere; }   /* wraps as far as it needs: never cut (22 Oct 2026); no hyphens: the section is as wide as its longest word, and "hyphens: auto" still broke "com-mitment" to fill a line (GLAAD, 23 Oct) */
/* A section is as wide as its marks or its name's longest word, whichever is wider, so a two-mark
   section called "Vegetarian diets" wraps between words, not inside one (BFCI, 23 Oct 2026). The
   rows' mark groups take the same width, so the marks stay under their numbers. */
.hbg-ov__sec, .hbg-ov__mks { flex: none; width: var(--sw); }
.hbg-ov__qs, .hbg-ov__mks { display: flex; gap: var(--mk-gap); }
.hbg-ov__q { display: grid; justify-items: center; width: var(--mk-w); font-size: 0.6875rem; font-weight: 500; }
.hbg-ov__q .hbg-qid { padding: 0; }
.hbg-ov__g + .hbg-ov__g { border-top: 1px solid var(--rule); }
.hbg-ov__gh { min-height: 2.375rem; padding-block: var(--s1); background: color-mix(in srgb, var(--paper) 70%, var(--surface)); border-bottom: 1px solid var(--rule); }
.hbg-ov__gname { overflow: hidden; font-family: var(--serif); font-size: var(--fs-16); font-weight: 600; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
.hbg-ov__gmeta { min-width: 0; color: var(--ink-2); font-size: var(--fs-12); }
.hbg-ov__gh .hbg-num { font-weight: 600; }
.hbg-ov__open { justify-self: end; white-space: nowrap; }
.hbg-ov__rows { padding-block: 2px; }
.hbg-ov__r { min-height: 1.625rem; background: var(--surface); }
.hbg-ov__r:nth-child(even) { background: color-mix(in srgb, var(--paper) 40%, var(--surface)); }
.hbg-ov__r:hover { background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
.hbg-ov__nm { overflow: hidden; color: var(--ink); text-overflow: ellipsis; white-space: nowrap; }
.hbg-num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.hbg-ov__r .hbg-num { font-weight: 600; }
.hbg-tier { overflow: hidden; color: var(--ink-2); font-size: var(--fs-12); text-overflow: ellipsis; white-space: nowrap; }
.hbg-tier b { color: var(--ink); font-weight: 600; }
.hbg-ov__table { position: sticky; left: 0; width: 100cqi; padding: var(--s2) var(--s3) var(--s3); border-bottom: 1px solid var(--rule); }   /* a group opened as a table stays in view however far the strips are scrolled; it scrolls on its own */
.hbg-mk { position: relative; display: block; flex: none; width: var(--mk-w, 18px); height: 18px; border-radius: 2px; background: color-mix(in srgb, var(--rule) 75%, var(--surface)); }
.hbg-mk::after { content: ""; position: absolute; inset: auto 0 0 0; height: calc(var(--v, 0) * 100%); border-radius: 2px; background: var(--accent); }
.hbg-mk:hover { box-shadow: 0 0 0 1px var(--rule-strong); }
.hbg-mk:focus-visible { z-index: 2; outline: 2px solid var(--focus); outline-offset: 1px; }
.hbg-mk.is-ne { background-color: var(--surface); background-image: repeating-linear-gradient(135deg, color-mix(in srgb, var(--ink-2) 55%, transparent) 0 1px, transparent 1px 4px); box-shadow: inset 0 0 0 1px var(--rule-strong); }
.hbg-mk.is-na { background: transparent; }
.hbg-mk.is-na::after { display: none; }
.hbg-mk.is-wait { background: transparent; box-shadow: inset 0 0 0 1px var(--rule-strong); }
.hbg-mk.is-wait::after { display: none; }
.hbg-mk.is-owner { box-shadow: none; outline: 1px dashed var(--ink-2); outline-offset: -1px; }
/* A flag is a red corner on the mark itself, so it can only belong to that answer (a line between rows read as the row above). */
.hbg-mk.is-flag::before { content: ""; position: absolute; z-index: 1; top: 0; right: 0; border-style: solid; border-width: 0 8px 8px 0; border-color: transparent var(--critical) transparent transparent; border-top-right-radius: 2px; }
.hbg-mk__stale { position: absolute; top: 0; left: 0; z-index: 1; border-style: solid; border-width: 6px 6px 0 0; border-color: var(--ink) transparent transparent transparent; }

/* Tables (a group opened in place, or the Table view) */
.hbg-scroll { position: relative; width: fit-content; max-width: 100%; overflow: auto; overscroll-behavior-x: contain; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.hbg-scroll--all { max-height: calc(100dvh - 2rem); }
.hbg-table { border-collapse: separate; border-spacing: 0; font-size: var(--fs-14); line-height: 1.25; font-variant-numeric: tabular-nums; }
.hbg-table thead th { position: sticky; z-index: 3; }
.hbg-table thead tr:first-child th { top: 0; height: 1.75rem; }
.hbg-table thead tr:last-child th { top: 1.75rem; }
.hbg-table th, .hbg-table td { padding: 0; border-bottom: 1px solid var(--rule); background: var(--surface); vertical-align: middle; }
.hbg-table thead th { padding: var(--s1) 2px; color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; text-align: center; white-space: nowrap; vertical-align: top; }
.hbg-table thead tr:first-child th { border-bottom-color: transparent; vertical-align: bottom; }
.hbg-table thead tr:last-child th { border-bottom-color: var(--rule-strong); }
.hbg-table .hbg-sec { text-align: left; padding-inline: var(--s2); }
.hbg-secname { width: 0; min-width: max(100%, var(--word-w, 0px)); line-height: 1.25; overflow-wrap: anywhere; white-space: normal; }   /* wraps between words, never cut: at least as wide as its longest word (--word-w, measured), so the columns widen rather than the word breaking (23 Oct 2026) */
.hbg-table .hbg-start { border-left: 1px solid var(--rule-strong); }
.hbg-table .hbg-corner { left: 0; z-index: 4; text-align: left; vertical-align: bottom; padding-inline: var(--s2); border-right: 1px solid var(--rule); border-bottom-color: var(--rule-strong); }
.hbg-table .hbg-rh { position: sticky; left: 0; z-index: 1; min-width: 9rem; max-width: 15rem; padding: var(--s1) var(--s3) var(--s1) var(--s2); border-right: 1px solid var(--rule); color: var(--ink); font-weight: 500; text-align: left; overflow-wrap: anywhere; }
.hbg-table .hbg-qh { display: grid; justify-items: center; }
.hbg-table tr.hbg-divider th { position: sticky; left: 0; padding: var(--s1) var(--s2); background: color-mix(in srgb, var(--paper) 70%, var(--surface)); border-bottom-color: var(--rule-strong); text-align: left; }
.hbg-divider__in { position: sticky; left: var(--s2); display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 0 var(--s3); }
.hbg-divider__name { font-family: var(--serif); font-size: var(--fs-16); font-weight: 600; }
.hbg-divider__meta { color: var(--ink-2); font-size: var(--fs-12); font-weight: 400; }
.hbg-sub { display: block; color: var(--ink-2); font-size: var(--fs-12); font-weight: 400; }
.hbg-table td.hbg-td { width: 2.75rem; min-width: 2.75rem; padding: 1px; }
.hbg-cell { position: relative; display: flex; align-items: center; justify-content: center; gap: 1px; width: 100%; height: 2.125rem; border-radius: 2px; color: var(--ink); font-weight: 600; text-decoration: none; }
.hbg-cell:hover { box-shadow: inset 0 0 0 1px var(--rule-strong); }
.hbg-cell:focus-visible { z-index: 2; outline: 2px solid var(--focus); outline-offset: 1px; }
.hbg-cell.is-muted { color: var(--ink-2); font-weight: 400; }
.hbg-cell.is-wait::after { content: ""; position: absolute; inset: 4px 5px; border: 1px solid var(--rule-strong); border-radius: 2px; }
.hbg-cell.is-owner { color: var(--ink-2); font-size: var(--fs-12); font-weight: 400; }
.hbg-cell.is-owner::after { border-style: dashed; border-color: var(--ink-2); }
.hbg-s1 { background: var(--shade-1); }
.hbg-s2 { background: var(--shade-2); }
.hbg-s3 { background: var(--shade-3); }
.hbg-s4 { background: var(--shade-4); color: var(--on-accent); }
.hbg-cell.hbg-s4 .hbg-ne { color: inherit; }
.hbg-ne { color: var(--ink-2); font-size: 0.7rem; font-weight: 400; }
.hbg-cell__flags { position: absolute; top: -3px; right: -3px; z-index: 1; display: flex; gap: 1px; color: var(--critical); font-size: 0.8125rem; line-height: 0; }
.hbg-cell.is-stale::before { content: ""; position: absolute; top: 0; left: 0; border-style: solid; border-width: 8px 8px 0 0; border-color: var(--ink) transparent transparent transparent; }
.hbg-table td.hbg-tot { padding: var(--s1) var(--s2); text-align: right; white-space: nowrap; }
.hbg-table td.hbg-overall { font-weight: 600; }
.hbg-table td.hbg-band { text-align: left; }
.hbg-table { --band-w: 8.5rem; }
.hbg-table :is(.hbg-pin-n, .hbg-pin-r, .hbg-pin-end) { position: sticky; right: 0; z-index: 1; }
.hbg-table.has-band .hbg-pin-n { right: var(--band-w); }
.hbg-table .hbg-pin-r { width: var(--band-w); min-width: var(--band-w); max-width: var(--band-w); overflow: hidden; text-overflow: ellipsis; }
.hbg-table :is(.hbg-pin-n, .hbg-pin-end) { box-shadow: inset 1px 0 0 var(--rule-strong); }
.hbg-table thead :is(.hbg-pin-n, .hbg-pin-r, .hbg-pin-end) { z-index: 4; }
.hbg-partial { display: block; color: var(--ink-2); font-size: var(--fs-12); font-weight: 400; }

/* Marks of state shared by every view */
:is(.hbg-mk, .hbg-cell, .hbg-sq).is-hl { outline: 2px solid var(--accent); outline-offset: 1px; z-index: 1; }
.hbg-rests-on :is(.hbg-mk, .hbg-cell, .hbg-sq).is-rest { outline: 1.5px dashed var(--ink); outline-offset: 1px; }
a[data-row][data-q].is-open { z-index: 3; outline: 2px solid var(--ink); outline-offset: 1px; }

@media (width < 60rem) {
  .hbg-ov { --name-min: 6rem; --name-max: 12rem; --num-w: 3rem; --tier-w: 4.5rem; }
  .hbg-ov__gmeta { white-space: normal; }
}
@media all { .hb.has-dock .hbg-ov { --name-min: 6rem; --name-max: 11rem; --num-w: 3rem; --tier-w: 7.5rem; } }   /* the docked cell panel takes ~31rem; one line starting "@" so scoped() leaves the selector alone */
@media (width < 52rem) {
  .hbg-ov { --mk-w: 15px; --mk-gap: 2px; --sec-gap: 10px; }
  .hbg-ov .hbg-rm { min-width: 0; width: var(--mk-w); padding: 0; font-size: 0.5625rem; letter-spacing: -0.02em; }
  .hbg-ov .hbg-tier__word, .hbg-table .hbg-tier__word { display: none; }
}
@media (width < 40rem) {
  .hbg-desk { display: none; }
  .hbg-only-phone { display: inline-flex; }
  .hbg-only-wide, .hbg-key__list .hbg-k-wide { display: none; }
  .hbg-key__list .hbg-k-phone { display: flex; }
  .hbg-filters .hbg-sel { min-width: 0; }   /* a long option never widens the page */
  .hbg-counts { font-size: var(--fs-12); }
  .hbg-switch { font-size: var(--fs-12); }
  .hbg-view__panel { grid-template-columns: minmax(0, 1fr); }
  .hbg-view__key { padding-left: 0; border-left: 0; }
}
@media (width >= 40rem) {
  .hbg-phone { display: none; }
}
/* Printed, the boxes stop scrolling (nothing hidden past an edge) and the strips' marks shrink
   to fit the page's width (the grid tab is a size container in print only). */
@media print {
  .hb-view[data-view="grid"] { container-type: inline-size; }
  .hbg-ov, .hbg-scroll, .hbg-scroll--all { max-height: none; overflow: visible; }
  .hbg-ov { --name-max: 10rem; --ov-gap: var(--s2); --mk-gap: 1px; --sec-gap: 6px; --mk-w: clamp(5px, calc((100cqi - var(--name-max) - var(--num-w) - var(--tier-w) - 3 * var(--ov-gap) - 2 * var(--ov-pad) - 2px - (var(--nq) - var(--ns)) * var(--mk-gap) - (var(--ns) - 1) * var(--sec-gap)) / var(--nq)), 18px); container-type: normal; }
  .hbg-ov > * { min-width: 0; }
  .hbg-ov__q { font-size: 0.5rem; }
  .hbg-ov__q .hbg-qid { writing-mode: vertical-rl; }   /* two-digit numbers fit over narrow marks */
  .hbg-ov__head, .hbg-ov__pin-l, .hbg-ov__pin-n, .hbg-ov__pin-r, .hbg-ov__table, .hbg-table :is(th, td) { position: static; }
  .hbg-ov__table { width: auto; }
  .hbg-ov { --name-max: 14rem; }
  .hbg-ov__nm { white-space: normal; overflow: visible; text-overflow: clip; }   /* the full name on paper */
}
@media (forced-colors: active) {
  :is(.hbg-mk, .hbg-cell, .hbg-sq).is-hl { outline-color: Highlight; }
  .hbg-mk::after { background: CanvasText; }
  .hbg-mk.is-flag::before { border-right-color: CanvasText; }
  .hbg-cell__flags, .hbg-key .flag-glyph { color: CanvasText; }
}`;

  // Every selector above gets ".hb " in front, so the grid's rules outrank the
  // page's own (.btn, .input, .segmented) without !important.
  function scoped(css) {
    const split = (sel) => {
      const out = [];
      let depth = 0;
      let cur = "";
      for (const ch of sel) {
        if (ch === "(") depth += 1;
        else if (ch === ")") depth -= 1;
        if (ch === "," && !depth) {
          out.push(cur);
          cur = "";
        } else cur += ch;
      }
      return out.concat(cur);
    };
    return css.replace(/^([^@\n}][^{\n]*)\{/gm, (all, sel) => split(sel).map((x) => ".hb " + x.trim()).join(", ") + " {");
  }

  /* ---- Small readings of the bundle ------------------------------------------ */

  function plural(word) {
    return /[^aeiou]y$/i.test(word) ? word.slice(0, -1) + "ies" : /(s|x|ch|sh)$/i.test(word) ? word + "es" : word + "s";
  }

  function labelName(label) {
    return String(label).replace(/_/g, " ");
  }

  function cap(text) {
    return String(text).replace(/^./, (c) => c.toUpperCase());
  }

  function topPoints(q) {
    const levels = q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    let top = null;
    for (const l of levels) if (l && isNum(l.points) && (top === null || l.points > top)) top = l.points;
    return top;
  }

  function step(points, top) {
    if (!isNum(points) || !isNum(top) || top <= 0 || points <= 0) return 0;
    return Math.min(STEPS, Math.max(1, Math.ceil((points / top) * STEPS - 1e-9)));
  }

  function labelOf(unit, label) {
    const v = unit && unit.labels ? unit.labels[label] : null;
    return v == null || v === "" ? null : String(v);
  }

  function nextLabel(labels, groupBy) {
    const at = labels.indexOf(groupBy);
    const order = at < 0 ? labels : labels.slice(at + 1).concat(labels.slice(0, at));
    return order.find((l) => l !== groupBy) || null;
  }

  function passes(unit, filters, labels) {
    return labels.every((label) => {
      const want = filters[label];
      if (want == null || want === "") return true;
      const have = labelOf(unit, label);
      return want === NONE ? have === null : have === want;
    });
  }

  // Column groups in the score sheet's section order, then any other sections in
  // question order: [{id, name, qs: [question]}].
  function sectionsFor(bundle, qids) {
    const sheet = bundle.scoresheet && Array.isArray(bundle.scoresheet.sections) ? bundle.scoresheet.sections : [];
    const groups = new Map();
    for (const s of sheet) if (s && s.id != null) groups.set(String(s.id), { id: String(s.id), name: s.name ? String(s.name) : "", qs: [] });
    for (const q of bundle.questions || []) {
      if (!q || !qids.has(q.id)) continue;
      const sid = q.section == null ? "" : String(q.section);
      if (!groups.has(sid)) groups.set(sid, { id: sid, name: sid ? "" : "No section", qs: [] });
      groups.get(sid).qs.push(q);
    }
    return Array.from(groups.values()).filter((g) => g.qs.length);
  }

  // The totals columns, or null when there are none to show.
  function totalsColumns(bundle) {
    const sheet = bundle.scoresheet;
    if (!sheet || sheet.computable === false) return null;
    const totals = bundle.totals || {};
    if (!Object.keys(totals).some((row) => totals[row])) return null;
    const overall = sheet.overall || {};
    const bands = (Array.isArray(sheet.bands) ? sheet.bands : []).filter((b) => b && b.of != null).map((b) => String(b.of));
    return {
      sections: (Array.isArray(sheet.sections) ? sheet.sections : []).filter((s) => s && s.id != null),
      overallPct: overall.method === "weighted_mean_of_section_percents" || overall.percent === true,
      band: bands.includes("overall") ? "overall" : bands[0] || null,
    };
  }

  // "Tier 6 (Very poor)" reads "Tier 6 · Very poor"; any other name as it is.
  function bandParts(name) {
    if (!name) return null;
    const m = /^(.*?)\s*\((.+)\)\s*$/.exec(String(name));
    return m ? { head: m[1], word: m[2] } : { head: String(name), word: "" };
  }

  function bandText(name) {
    const b = bandParts(name);
    return b ? b.head + (b.word ? " · " + b.word : "") : DASH;
  }

  function bandNode(name) {
    const b = bandParts(name);
    if (!b) return h("span", { class: "hbg-tier" }, DASH);
    return h("span", { class: "hbg-tier", title: bandText(name) }, h("b", null, b.head),
      b.word ? h("span", { class: "hbg-tier__word" }, " · " + b.word) : null);
  }

  // A partial total (answers waiting for the owner's data, or not scored yet) is never printed as a
  // figure: 100.0 from the one section scored so far reads as complete (22 Oct 2026). Its words say why.
  function overallText(cols, t) {
    if (t && t.partial) return DASH;
    return t && isNum(t.overall) ? fmtNum(t.overall, 1) + (cols && cols.overallPct ? "%" : "") : DASH;
  }

  function partialWords(t) {
    return t && t.waiting && t.waiting.length ? "incomplete, waits for your data" : "partial";
  }

  // The short label of a question over its narrow column: "1.4" in a section whose questions
  // all start "1." reads "4", and "D12" in a section of D1, D2... reads "12". A label still
  // longer than the column holds (SHORT_MAX) never overlaps its neighbours: the section's
  // questions are numbered by place instead, and the hover tip and the screen reader keep the
  // full id (GDB, 22 Oct 2026: ids like G.GOVERNANCE.DPL.e.e1.ACCESS_CORRECTION piled up).
  const SHORT_MAX = 2;
  const TABLE_ID_MAX = 8;                 // a table column header shows an id up to this long in full
  function shortIds(sec) {
    const ids = sec.qs.map((q) => String(q.id));
    const strip = (re) => {
      const m = ids.length ? ids[0].match(re) : null;
      const prefix = m ? m[0] : "";
      return prefix && ids.every((id) => id.startsWith(prefix) && id.length > prefix.length)
        ? ids.map((id) => id.slice(prefix.length)) : null;
    };
    const fits = (labels) => labels.every((s) => s.length <= SHORT_MAX);
    const short = strip(/^.*\./) || ids;
    if (fits(short)) return short;
    const letters = strip(/^[A-Za-z]+[_-]?/);
    return letters && fits(letters) ? letters : ids.map((_, i) => String(i + 1));
  }

  /* ---- The model: everything the three layouts draw ------------------------- */

  function openRuleIds(bundle, row, qid, c) {
    const rules = Array.isArray(bundle.rules) ? bundle.rules : [];
    const isOpen = (r) => r && RULE_OPEN.has(r.status);
    const linked = Array.isArray(c.rules) ? c.rules : [];
    let ids = linked.filter((id) => isOpen(rules.find((r) => r && r.id === id)));
    // Linked only to rules the owner has decided: it follows those, so it rests on nothing open.
    if (!ids.length && !linked.length) {
      ids = rules.filter((r) => isOpen(r) && Array.isArray(r.question_ids) && r.question_ids.includes(qid) &&
        (!Array.isArray(r.rows) || r.rows.includes(row))).map((r) => r.id);
    }
    return ids;
  }

  function restsOnRule(c, bundle, row, qid) {
    const judgement = Boolean(c) && (c.judgement === true || (Array.isArray(c.flags) && c.flags.includes("judgement")));
    return judgement && openRuleIds(bundle, row, qid, c).length > 0;
  }

  function liveState(ctx, units) {
    const bundle = ctx.bundle;
    const runs = Array.isArray(bundle.runs) ? bundle.runs : [];
    const open = runs.filter((r) => r && !r.finished).pop() || null;
    const rowsOf = open && Array.isArray(open.rows) && open.rows.length ? new Set(open.rows) : null;
    const qsOf = open && Array.isArray(open.questions) && open.questions.length ? new Set(open.questions) : null;
    const inRun = rowsOf ? units.filter((u) => rowsOf.has(u.id)) : units;
    let waiting = 0;
    let done = 0;
    let answered = 0;
    let ne = 0;
    for (const u of inRun) {
      const cells = (bundle.cells && bundle.cells[u.id]) || {};
      let left = 0;
      for (const qid of Object.keys(cells)) {
        if (qsOf && !qsOf.has(qid)) continue;
        const c = cells[qid];
        if (c.state === "answered") answered += 1;
        else if (c.state === "no_evidence") ne += 1;
        else if (c.state !== "not_applicable" && !c.waiting) left += 1;     // waiting for the owner is not left to run
      }
      waiting += left;
      if (!left) done += 1;
    }
    // Shown only while a run is on: an unfinished run, or the stage says scoring. A finished
    // trial that left questions unscored, or a finished run, needs no progress line.
    if (!(waiting > 0) || (!open && bundle.project && bundle.project.stage !== "running")) return null;
    return { done, of: inRun.length, answered, ne, waiting, run: open };
  }

  function model(ctx) {
    const bundle = ctx.bundle;
    const state = ctx.state;
    const p = bundle.project || {};
    const labels = Array.isArray(p.labels) ? p.labels.map(String) : [];
    const units = Array.isArray(bundle.units) ? bundle.units.filter((u) => u && u.id) : [];
    const groupBy = labels.includes(state.groupBy) ? state.groupBy : state.groupBy === "" ? "" : labels[0] || "";
    const sortLabel = groupBy ? nextLabel(labels, groupBy) : null;
    const filters = state.filters || {};
    const only = Array.isArray(state.onlyRows) ? new Set(state.onlyRows) : null;
    const shown = units.filter((u) => (!only || only.has(u.id)) && passes(u, filters, labels));
    const hl = new Set((Array.isArray(state.highlight) ? state.highlight : []).map((c) => String(c[0]) + SEP + String(c[1])));
    const byName = (a, b) => collator.compare(String(a.name || a.id), String(b.name || b.id));

    // Groups: one per value of the group-by label, by name; rows with no value last.
    const byGroup = new Map();
    for (const u of shown) {
      const g = groupBy ? labelOf(u, groupBy) : null;
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g).push(u);
    }
    // Groups of one row each are gathered into one group after the others (when
    // there are at least two of them), so a lone row doesn't take two lines.
    const singles = groupBy ? Array.from(byGroup.keys()).filter((g) => g !== null && byGroup.get(g).length === 1) : [];
    const gathered = singles.length >= 2 ? singles.map((g) => byGroup.get(g)[0]) : [];
    if (gathered.length) for (const g of singles) byGroup.delete(g);
    const keys = Array.from(byGroup.keys()).sort((a, b) => (a === null) - (b === null) || collator.compare(a || "", b || ""));
    const groups = keys.map((g) => ({
      key: g === null ? NONE : g,
      name: g,
      title: !groupBy ? "All " + (p.unit_word_plural || "rows") : g !== null ? g : "No " + labelName(groupBy) + " recorded",
      rows: byGroup.get(g).slice().sort((a, b) =>
        (sortLabel ? collator.compare(labelOf(a, sortLabel) || "￿", labelOf(b, sortLabel) || "￿") : 0) || byName(a, b)),
    }));
    if (gathered.length) {
      const at = groups.length && groups[groups.length - 1].name === null ? groups.length - 1 : groups.length;
      groups.splice(at, 0, {
        key: SINGLES,
        name: null,
        title: cap(plural(labelName(groupBy))) + " with one " + (p.unit_word || "row"),
        rows: gathered.slice().sort(byName),
      });
    }

    const qidsAll = new Set();
    for (const q of bundle.questions || []) if (q && shown.some((u) => ctx.data.applies(u.id, q.id))) qidsAll.add(q.id);
    const sections = sectionsFor(bundle, qidsAll);

    // Counts over the rows in view.
    const counts = { answered: 0, ne: 0, waiting: 0, owner: 0, flagged: 0, stale: 0, rests: 0, kinds: {} };
    for (const k of HB.flagKinds.factual) counts.kinds[k] = 0;
    const restsByQ = new Map();
    const cellInfo = new Map();
    for (const u of shown) {
      for (const sec of sections) for (const q of sec.qs) {
        const c = ctx.data.cell(u.id, q.id);
        if (!c) continue;
        if (c.state === "answered") counts.answered += 1;
        else if (c.state === "no_evidence") counts.ne += 1;
        else if (c.waiting) counts.owner += 1;
        else if (c.state !== "not_applicable") counts.waiting += 1;
        const kinds = HB.factualFlags(c);
        if (kinds.length) counts.flagged += 1;
        for (const k of kinds) counts.kinds[k] += 1;
        if (c.out_of_date) counts.stale += 1;
        let rules = null;
        if (restsOnRule(c, bundle, u.id, q.id)) {
          counts.rests += 1;
          rules = openRuleIds(bundle, u.id, q.id, c);
          if (!restsByQ.has(q.id)) restsByQ.set(q.id, { n: 0, rules: new Set() });
          const r = restsByQ.get(q.id);
          r.n += 1;
          for (const id of rules) r.rules.add(id);
        }
        cellInfo.set(u.id + SEP + q.id, { kinds, rules });
      }
    }

    const m = {
      // Group by offers the labels set-up marks "Can group by" (all of them when none is marked);
      // every label stays a filter.
      groupLabels: (() => {
        const marked = Array.isArray(p.group_labels) ? p.group_labels.map(String).filter((l) => labels.includes(l)) : [];
        const list = marked.length ? marked : labels.slice();
        if (groupBy && !list.includes(groupBy)) list.push(groupBy);
        return list;
      })(),
      bundle, p, labels, units, shown, groupBy, sortLabel, filters, groups, sections, hl,
      grouped: Boolean(groupBy),
      cols: totalsColumns(bundle),
      counts, restsByQ,
      showRests: Boolean(state.showRests),
      live: liveState(ctx, units),
    };

    // What one cell is, in words and in marks: {c, state, pts, top, step, kinds,
    // rests, stale, hl, text, label}; null when the question does not apply.
    m.info = function info(unit, q) {
      const c = ctx.data.cell(unit.id, q.id);
      if (!c) return null;
      const extra = cellInfo.get(unit.id + SEP + q.id) || { kinds: HB.factualFlags(c), rules: restsOnRule(c, bundle, unit.id, q.id) ? openRuleIds(bundle, unit.id, q.id, c) : null };
      const top = topPoints(q);
      const pts = isNum(c.points) ? c.points : null;
      const of = isNum(top) ? " of " + fmtNum(top) : "";
      const out = { c, state: c.state, pts, top, step: 0, kinds: extra.kinds, rests: extra.rules, stale: Boolean(c.out_of_date) };
      let words;
      if ((c.state === "answered" || c.state === "no_evidence") && HB.isFigureQuestion(q)) {
        // A figure (EggTrack's 73%): the cell shows it, shaded by where it sits between min and max.
        const a = q.answer;
        const fig = isNum(c.value);
        out.text = fig ? HB.figureText(q, c.value) : String(c.value == null ? "?" : c.value);
        out.step = fig && isNum(a.min) && isNum(a.max) && a.max > a.min ? step(c.value - a.min, a.max - a.min) : 0;
        words = (c.state === "no_evidence" ? "no evidence found, " : "") + out.text;
      } else if ((c.state === "answered" || c.state === "no_evidence") && !top && c.value != null) {
        // Levels worth no points (a tracker's statuses): show the level itself, not "0 of 0 points".
        out.text = String(c.value);
        words = (c.state === "no_evidence" ? "no evidence found, " : "") + String(c.value);
      } else if (c.state === "answered" || c.state === "no_evidence") {
        out.text = pts === null ? "?" : fmtNum(pts);
        out.step = step(pts, top);
        words = (c.state === "no_evidence" ? "no evidence found, " : "") + (pts === null ? "points not recorded" : fmtNum(pts) + of + " points");
      } else if (c.state === "not_applicable") {
        out.text = "n/a";
        words = "does not apply";
      } else if (c.waiting) {
        // Only the owner holds the data this answer rests on (needs.py): not "no evidence", and not left to run.
        const need = ctx.data.need ? ctx.data.need(c.waiting) : null;
        out.state = "not_run";
        out.owner = c.waiting;
        out.text = "data";
        const what = need && need.what ? String(need.what) : "";
        words = "waits for data you need to supply" + (what ? ": " + (what.length > 90 ? what.slice(0, 89) + "…" : what) : "");
      } else {
        out.state = "not_run";
        out.text = "";
        words = "not scored yet";
      }
      if (c.proposal) words += ", a proposal for your experts";          // the method leaves this rating to them
      if (out.kinds.length) words += ", flagged: " + out.kinds.map((k) => HB.flagKinds.words[k]).join(", ");
      if (out.stale) words += ", the question changed since";
      if (c.checked) {
        out.checked = true;
        words += ", " + (HB.team && HB.team.checkedWords ? HB.team.checkedWords(c.checked, true) : "checked by a person");
      }
      if (out.rests && m.showRests) words += ", rests on an open rule" + (out.rests.length ? " (" + out.rests.join(", ") + ")" : "");
      out.hl = m.hl.has(unit.id + SEP + q.id);
      if (out.hl) words += ", highlighted";
      out.label = (unit.name || unit.id) + ", " + q.id + (q.short ? " " + q.short : "") + ": " + words;
      return out;
    };
    // A row with no answer at all has no score yet: its totals (0, partial) would read as a real 0.
    const anyScored = (unit) => Boolean(bundle.cells && bundle.cells[unit.id] &&
      Object.values(bundle.cells[unit.id]).some((c) => ctx.data.isScored(c)));
    m.totals = (unit) => (bundle.totals && anyScored(unit) ? bundle.totals[unit.id] : null) || null;
    m.groupMean = (rows) => {
      const vals = rows.map((u) => m.totals(u)).filter((t) => t && !t.partial && isNum(t.overall)).map((t) => t.overall);
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    };
    m.groupFlagged = (rows) => rows.reduce((n, u) => n + sections.reduce((k, sec) =>
      k + sec.qs.filter((q) => { const x = cellInfo.get(u.id + SEP + q.id); return x && x.kinds.length; }).length, 0), 0);
    m.shortName = (unit, group) => {
      const name = String(unit.name || unit.id);
      return group && group.name && name.startsWith(group.name + " ") && name.length > group.name.length + 1
        ? name.slice(group.name.length + 1) : name;
    };
    m.bandName = (unit) => {
      const t = m.totals(unit);
      return m.cols && m.cols.band && t && !t.partial && t.bands ? t.bands[m.cols.band] || null : null;   // no tier until complete
    };
    m.overallText = (t) => overallText(m.cols, t);
    m.partialWords = partialWords;
    m.bandText = bandText;
    m.bandNode = bandNode;
    m.shortIds = shortIds;
    return m;
  }

  function cellClasses(base, x, rests) {
    const cls = [base];
    if (x.state === "no_evidence") cls.push("is-ne");
    if (x.state === "not_run") cls.push("is-wait");
    if (x.owner) cls.push("is-owner");
    if (x.kinds.length) cls.push("is-flag");
    if (x.stale) cls.push("is-stale");
    if (x.hl) cls.push("is-hl");
    if (x.checked) cls.push("is-checked");
    if (x.rests && rests) cls.push("is-rest");
    return cls;
  }

  function glyphs(kinds) {
    return kinds.length ? h("span", { class: "hbg-cell__flags", "aria-hidden": "true" }, kinds.map((k) => HB.flagGlyph(k))) : null;
  }

  // The dashed box under a question id: answers in view resting on open rules.
  function restBox(m, q, end) {
    const r = m.restsByQ.get(q.id);
    if (!r || !r.n) return null;
    const ids = Array.from(r.rules).sort(collator.compare);
    const words = fmtInt(r.n) + (r.n === 1 ? " answer here rests" : " answers here rest") + " on Claude's reading of " +
      (ids.length ? (ids.length === 1 ? "open rule " : "open rules ") + ids.join(", ") : "an open rule");
    return h("span", { class: "hbg-rest", tabindex: "0" },
      h("span", { class: "hbg-rm", "aria-hidden": "true" }, fmtInt(r.n)),
      h("span", { class: "hbg-tip" + (end ? " hbg-tip--end" : ""), role: "tooltip" }, words + "."));
  }

  function qidTip(q, text, end) {
    return h("span", { class: "hbg-qid", tabindex: "0", "aria-label": q.id + (q.short ? " " + q.short : "") }, text,
      q.short || text !== q.id ? h("span", { class: "hbg-tip" + (end ? " hbg-tip--end" : ""), "aria-hidden": "true" }, q.id + (q.short ? " " + q.short : "")) : null);
  }

  /* ---- Strips ------------------------------------------------------------------ */

  function mark(ctx, m, unit, q) {
    const x = m.info(unit, q);
    if (!x || x.state === "not_applicable") {
      return h("span", { class: "hbg-mk is-na", title: x ? x.label : (unit.name || unit.id) + ", " + q.id + ": does not apply", "aria-hidden": "true" });
    }
    const v = x.state === "not_run" || !isNum(x.pts) || !isNum(x.top) || x.top <= 0 ? 0 : Math.max(0, Math.min(1, x.pts / x.top));
    return h("a", {
      class: cellClasses("hbg-mk", x, m.showRests).join(" "),
      href: ctx.href("grid", unit.id, q.id),
      "data-row": unit.id,
      "data-q": q.id,
      "aria-label": x.label,
      title: x.label,
      style: { "--v": String(v) },
    }, x.stale ? h("i", { class: "hbg-mk__stale", "aria-hidden": "true" }) : null);
  }

  function stripRow(ctx, m, unit, group) {
    const t = m.totals(unit);
    const short = m.shortName(unit, group);
    const docs = HB.gridGaps.note(ctx, m, unit);
    const who = h("span", { class: "hbg-ov__who hbg-ov__pin-l" },
      h("span", { class: "hbg-ov__nm", title: unit.name || unit.id }, short,
        short !== (unit.name || unit.id) ? h("span", { class: "visually-hidden" }, " (" + (unit.name || unit.id) + ")") : null),
      HB.gridGaps.marker(unit));
    if (docs) return h("div", { class: "hbg-ov__r", "data-row": unit.id, role: "listitem" }, who, h("span", { class: "hbg-docnote" }, docs));
    const marks = m.sections.map((sec, i) => h("span", { class: "hbg-ov__mks", style: { "--sw": "var(--sw-" + i + ")" } },
      sec.qs.map((q) => mark(ctx, m, unit, q))));
    return h("div", { class: "hbg-ov__r", "data-row": unit.id, role: "listitem" },
      who,
      h("span", { class: "hbg-ov__mk" }, marks),
      h("span", { class: "hbg-num hbg-ov__pin-n" }, m.overallText(t)),
      h("span", { class: "hbg-ov__pin-r" }, tierCell(ctx, m, unit, t)));
  }

  // The tier column: the band, "Partial score" while a scored row still has questions to
  // score, nothing for a row not scored at all; empty when the score sheet has no bands.
  function tierCell(ctx, m, unit, t) {
    const scored = ctx.bundle.cells && ctx.bundle.cells[unit.id]
      ? Object.values(ctx.bundle.cells[unit.id]).some((c) => ctx.data.isScored(c)) : false;
    if (t && t.partial && t.waiting && t.waiting.length) {
      return h("span", { class: "hbg-tier", title: "Some answers wait for data you need to supply, so this total is incomplete" }, "Waits for your data");
    }
    if (t && t.partial) {
      return scored ? h("span", { class: "hbg-tier", title: "Not every question is scored yet" }, "Partial score") : h("span", { class: "hbg-tier" }, "");
    }
    return m.cols && m.cols.band ? m.bandNode(m.bandName(unit)) : h("span", { class: "hbg-tier" }, "");
  }

  function stripsHead(m) {
    const unitWord = cap(m.p.unit_word || "row");
    let col = 0;
    const total = m.sections.reduce((n, s) => n + s.qs.length, 0);
    return h("div", { class: "hbg-ov__head" },
      h("span", { class: "hbg-ov__pin-l" }, unitWord),
      h("span", { class: "hbg-ov__secs" }, m.sections.map((sec, si) => {
        const nums = m.shortIds(sec);
        const full = sec.name ? sec.id + " " + sec.name : sec.id;
        return h("span", { class: "hbg-ov__sec", title: full, style: { "--sw": "var(--sw-" + si + ")" } },
          h("span", { class: "hbg-ov__secname", title: full, "data-id": sec.name ? sec.id : null }, sec.name || sec.id || "No section"),
          h("span", { class: "hbg-ov__qs" }, sec.qs.map((q, i) => {
            col += 1;
            const end = col > total / 2;
            return h("span", { class: "hbg-ov__q" }, qidTip(q, nums[i], end), restBox(m, q, end));
          })));
      })),
      h("span", { class: "hbg-num hbg-ov__pin-n" }, "Overall"),
      h("span", { class: "hbg-ov__pin-r" }, m.cols && m.cols.band ? "Tier" : ""));
  }

  function strips(ctx, m) {
    const nq = m.sections.reduce((n, sec) => n + sec.qs.length, 0);
    const root = h("div", { class: "hbg-ov", style: Object.assign({ "--nq": String(nq), "--ns": String(m.sections.length) }, sectionWidths(m)) },
      stripsHead(m));
    const open = ctx.state.openGroups || (ctx.state.openGroups = {});
    m.groups.forEach((g, index) => {
      const sec = h("section", { class: "hbg-ov__g", "aria-label": g.title });
      const isOpen = Boolean(open[m.groupBy + SEP + g.key]);
      if (m.grouped) {
        const mean = m.groupMean(g.rows);
        const flagged = m.groupFlagged(g.rows);
        sec.append(h("div", { class: "hbg-ov__gh" },
          h("h3", { class: "hbg-ov__gname hbg-ov__pin-l", title: g.title }, g.title),
          h("span", { class: "hbg-ov__gmeta" }, ctx.unitWords(g.rows.length) + (flagged ? " · " + fmtInt(flagged) + " flagged" : "")),
          h("span", { class: "hbg-num hbg-ov__pin-n", title: "Mean overall score" }, mean === null ? DASH : m.overallText({ overall: Math.round(mean) }),
            h("span", { class: "visually-hidden" }, " mean overall")),
          h("span", { class: "hbg-ov__pin-r" }, h("button", {
            type: "button",
            class: "btn btn--small btn--quiet hbg-ov__open",
            "aria-expanded": isOpen ? "true" : "false",
            "data-group": String(index),
            onclick: () => {
              open[m.groupBy + SEP + g.key] = !isOpen;
              ctx.render();
              const again = document.querySelector('.hbg-ov__open[data-group="' + index + '"]');
              if (again) again.focus({ preventScroll: true });
            },
          }, isOpen ? "Back to strips" : "Open as table"))));
      }
      if (isOpen) {
        sec.append(h("div", { class: "hbg-ov__table" }, groupTable(ctx, m, g)));
      } else {
        sec.append(h("div", { class: "hbg-ov__rows", role: "list", "aria-label": g.title }, g.rows.map((u) => stripRow(ctx, m, u, g))));
      }
      root.append(sec);
    });
    if (nameFit) root.style.setProperty("--name-max", nameFit);
    if (tierFit) root.style.setProperty("--tier-w", tierFit);
    requestAnimationFrame(fitNames);
    fitSectionsWhenShown(root, m, 0);
    root.addEventListener("scroll", () => edgeNames(root), { passive: true });   // scroll events come once a frame at most
    return root;
  }

  // A section name partly behind the pinned columns as the grid scrolls sideways showed half a word
  // ("Performance" cut at Overall, a GLAAD name one letter wide at the edge: design read 26 Oct 2026),
  // and hiding it left a 20-column section with no name at all (IMCO). So each name keeps to the part of
  // its section in view: it slides along with the scroll while its words fit there, then shortens
  // ("Pillar 2", "Pillar 2: Performance...", "P2"), and only a sliver too narrow for that goes blank. The
  // full name is on hover.
  function edgeNames(live) {
    if (!live || !live.isConnected) return;
    const head = live.querySelector(".hbg-ov__head");
    if (!head) return;
    const l = head.querySelector(".hbg-ov__pin-l"), n = head.querySelector(".hbg-ov__pin-n");
    const from = l && getComputedStyle(l).position === "sticky" ? l.getBoundingClientRect().right : -Infinity;
    const to = n && getComputedStyle(n).position === "sticky" ? n.getBoundingClientRect().left : Infinity;
    const names = [...head.querySelectorAll(".hbg-ov__secname")];
    // The name's own text never changes here: the page's layout check rescans on every change of text,
    // and the two set each other off without end (Quem Defende stalled the release sweep for 20 minutes,
    // 26 Oct 2026). A shorter form is drawn from data-short by CSS, and measured on a canvas.
    names.forEach((el) => {
      el.classList.remove("is-cut", "is-short");
      el.style.transform = "";
    });
    head.style.minHeight = "";
    head.style.minHeight = head.getBoundingClientRect().height + "px";   // the header keeps its height as names shorten
    const range = document.createRange();
    let pen = null;
    names.forEach((el) => {
      const box = el.getBoundingClientRect();
      const left = Math.max(box.left, from), right = Math.min(box.right, to);
      const shift = Math.max(0, left - box.left);
      range.selectNodeContents(el);
      if (box.left + shift + range.getBoundingClientRect().width <= right + 1) {
        if (shift) el.style.transform = "translateX(" + Math.round(shift) + "px)";
        return;
      }
      if (!pen) {
        wordCanvas = wordCanvas || document.createElement("canvas");
        pen = wordCanvas.getContext("2d");
        pen.font = getComputedStyle(el).font;
      }
      // Shorter forms, in this order: the name up to its colon ("Pillar 2"), the name cut at a whole word,
      // then the section's own code when it has a letter in it ("P2", "IV"; a bare "6" says nothing).
      const full = el.textContent, room = right - left;
      const tries = [];
      const colon = full.indexOf(":");
      if (colon > 0) tries.push(full.slice(0, colon).trim());
      for (let chars = Math.floor(room / 6.5); chars >= 6; chars -= 2) {
        const cut = HB.shortText(full, chars);
        if (/\s/.test(cut.replace(/\u2026.*$/, ""))) tries.push(cut);   // keeps a whole word, not "Indicat..."
      }
      if (el.dataset.id && el.dataset.id.length <= 4 && /[a-z]/i.test(el.dataset.id)) tries.push(el.dataset.id);
      const fits = tries.find((t) => pen.measureText(t).width <= room - 1);
      if (fits) {
        el.dataset.short = fits;
        el.classList.add("is-short");
        if (shift) el.style.transform = "translateX(" + Math.round(shift) + "px)";
      } else {
        el.classList.add("is-cut");
      }
    });
  }
  if (typeof window !== "undefined" && !window.__hbgEdge) {
    window.__hbgEdge = true;
    window.addEventListener("resize", () => edgeNames(document.querySelector(".hbg-ov")), { passive: true });
  }

  // Each section's width: its marks, or its name's longest word once measured (wordFit, kept across
  // redraws so the strips don't jump). The strips column is the sum of them, so the rows' grid and
  // the header's agree.
  const wordFit = {};
  function sectionWidths(m) {
    const out = {};
    const widths = m.sections.map((sec, i) => {
      const n = Math.max(1, sec.qs.length);
      const marks = "calc(" + n + " * var(--mk-w) + " + (n - 1) + " * var(--mk-gap))";
      const word = wordFit[sec.name || sec.id || ""] || longestWord(sec.name || sec.id || "No section");
      out["--sw-" + i] = word ? "max(" + marks + ", " + word + "px)" : marks;
      return "var(--sw-" + i + ")";
    });
    // At least 10rem, so a group's line ("26 chocolate bars · 3 flagged") fits over a benchmark with two
    // questions instead of running under Overall (Consumer Reports, design sweep 23 Oct 2026).
    out["--strip-w"] = "max(10rem, calc(" + widths.join(" + ") + " + " + Math.max(0, m.sections.length - 1) + " * var(--sec-gap)))";
    return out;
  }

  // Measure once the overview is on the page (just after it is built), and again when the page's
  // fonts have loaded, since the words are wider in the real font than in the fallback. A timer, not
  // a frame: a tab in the background gets no frames, and its strips stayed narrow.
  function fitSectionsWhenShown(root, m, tries) {
    setTimeout(() => {
      if (!root.isConnected) {
        if (tries < 60) fitSectionsWhenShown(root, m, tries + 1);
        return;
      }
      fitSections(root, m);
      if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(() => fitSections(root, m));
    }, tries ? 50 : 0);
  }

  // The widest word of a header's text, in px, in the headers' font (600 at --fs-12) as the page
  // sets it; before anything is on screen this is the best guess, and fitSections corrects the strips.
  let wordCanvas = null;
  function longestWord(text) {
    wordCanvas = wordCanvas || document.createElement("canvas");
    const pen = wordCanvas.getContext("2d");
    const rootStyle = getComputedStyle(document.documentElement);
    const raw = rootStyle.getPropertyValue("--fs-12").trim();
    const size = raw.endsWith("rem") ? parseFloat(raw) * parseFloat(rootStyle.fontSize) : parseFloat(raw) || 12;
    pen.font = "600 " + size + "px " + getComputedStyle(document.body).fontFamily;
    return headerFit(pen, text);
  }

  // A header's width: its longest word, and room enough that the name takes at most about three lines,
  // up to 120px: "Contact waiting time (Ease of Contact)" ran to five lines over three marks while half
  // the grid stood empty (design read, 23 Oct 2026).
  function headerFit(pen, text) {
    const t = String(text || "");
    const words = t.split(/\s+|(?<=-)/).filter(Boolean);
    const word = Math.max(0, ...words.map((w) => pen.measureText(w).width));
    return Math.ceil(Math.max(word, Math.min(pen.measureText(t).width / 3, 120)) + 2);
  }

  // Measure each section name's longest word in the header's own font; when one is new, set the
  // widths again on the overview.
  function fitSections(live, m) {
    const name = live && live.isConnected && live.querySelector(".hbg-ov__secname");
    if (!name) return;
    const style = getComputedStyle(name);
    wordCanvas = wordCanvas || document.createElement("canvas");
    const pen = wordCanvas.getContext("2d");
    pen.font = style.font || (style.fontWeight + " " + style.fontSize + " " + style.fontFamily);
    let changed = false;
    m.sections.forEach((sec) => {
      const text = String(sec.name || sec.id || "No section");
      const widest = headerFit(pen, text);
      const key = sec.name || sec.id || "";
      if (wordFit[key] !== widest) { wordFit[key] = widest; changed = true; }
    });
    if (changed) {
      const widths = sectionWidths(m);
      Object.keys(widths).forEach((k) => live.style.setProperty(k, widths[k]));
    }
    edgeNames(live);
  }

  // Every row is its own grid, so the name column can't size itself to its content: measure the
  // widest name once the overview is on screen and keep that as the column's width, so a redraw
  // starts at the measured width instead of jumping.
  let nameFit = "";
  function fitNames() {
    const live = document.querySelector(".hbg-ov");
    if (!live) return;
    const range = document.createRange();
    let widest = 0;
    live.querySelectorAll(".hbg-ov__pin-l").forEach((el) => {
      range.selectNodeContents(el);
      widest = Math.max(widest, range.getBoundingClientRect().width);
    });
    if (widest) {
      nameFit = "min(" + Math.ceil(widest + 4) + "px, 28rem)";
      live.style.setProperty("--name-max", nameFit);
    }
    fitTiers(live, range);
    edgeNames(live);
  }

  // The band column is as wide as its longest band name ("Tier 4 · Getting started"), up to 16rem, rather
  // than a fixed 7rem that cut it to "Getting sta..." (Pecking Order, design read 26 Oct 2026).
  let tierFit = "";
  function fitTiers(live, range) {
    let widest = 0;
    live.querySelectorAll(".hbg-ov__r > .hbg-ov__pin-r, .hbg-ov__gh > .hbg-ov__pin-r").forEach((el) => {   // and "Open as table"
      range.selectNodeContents(el);
      widest = Math.max(widest, range.getBoundingClientRect().width);
    });
    if (!widest) return;
    tierFit = "min(max(" + Math.ceil(widest + 4) + "px, 4.5rem), 16rem)";
    live.style.setProperty("--tier-w", tierFit);
  }

  /* ---- Tables ------------------------------------------------------------------ */

  function sectionText(sec, t, partial) {
    if (!t) return DASH;
    if (isNum(t.percent) && sec.percent !== false) return fmtNum(t.percent, 1) + "%";
    if (!t.max) return partial ? DASH : "n/a";
    if (isNum(t.points)) return fmtNum(t.points) + "/" + fmtNum(t.max);
    return DASH;
  }

  function totalsCells(m, unit) {
    const cols = m.cols;
    if (!cols) return [];
    const t = m.totals(unit);
    const partial = Boolean(t && t.partial);
    const out = cols.sections.map((s, i) => h("td", {
      class: "hbg-tot" + (i === 0 ? " hbg-start" : ""),
      title: t && !partial && t.bands && t.bands[s.id] ? String(s.id) + ": " + bandText(t.bands[s.id]) : null,
    }, sectionText(s, t && t.sections ? t.sections[s.id] : null, partial)));
    out.push(h("td", { class: "hbg-tot hbg-overall hbg-pin-n" + (cols.sections.length ? "" : " hbg-start") },
      m.overallText(t), partial ? h("span", { class: "hbg-partial" }, partialWords(t))
        : t && isNum(t.rank) ? h("span", { class: "hbg-partial" }, "rank " + t.rank) : null));       // the score sheet ranks the rows
    if (cols.band) out.push(h("td", { class: "hbg-tot hbg-band hbg-pin-r" }, m.bandNode(m.bandName(unit))));
    return out;
  }

  function tableCell(ctx, m, unit, q) {
    const x = m.info(unit, q);
    if (!x) return h("td", { class: "hbg-td" }, h("span", { class: "hbg-cell is-muted", title: "Does not apply" }, "n/a"));
    const cls = cellClasses("hbg-cell", x, m.showRests);
    if (x.state === "answered" || x.state === "no_evidence") cls.push("hbg-s" + x.step);
    else cls.push("is-muted");
    return h("td", { class: "hbg-td" },
      h("a", {
        class: cls.join(" "),
        href: ctx.href("grid", unit.id, q.id),
        "data-row": unit.id,
        "data-q": q.id,
        "aria-label": x.label,
        title: x.label,
      }, x.text, x.state === "no_evidence" ? h("span", { class: "hbg-ne", "aria-hidden": "true" }, "∅") : null, glyphs(x.kinds),
        x.checked ? h("span", { class: "hbg-tick", "aria-hidden": "true" }, HB.team && HB.team.tickText ? HB.team.tickText(x.c.checked) : "✓") : null));
  }

  function tableHead(m, sections) {
    const unitWord = cap(m.p.unit_word || "row");
    const secRow = [h("th", { class: "hbg-corner", scope: "col", rowspan: "2" }, unitWord)];
    const qRow = [];
    const qCount = sections.reduce((n, g) => n + g.qs.length, 0);
    let col = 0;
    for (const g of sections) {
      const full = g.name && g.id ? g.id + " " + g.name : g.name || g.id;
      secRow.push(h("th", { class: "hbg-sec hbg-start", scope: "colgroup", colspan: String(g.qs.length), title: full },
        h("div", { class: "hbg-secname", style: { "--word-w": longestWord(full) + "px" } }, full)));
      // An id too long for a column header (GDB's G.GOVERNANCE.DATASHARING.a.EXISTS) shows its short
      // label, as over the strips; the tip keeps the full id.
      const long = g.qs.some((q) => String(q.id).length > TABLE_ID_MAX);
      const labels = long ? shortIds(g) : g.qs.map((q) => String(q.id));
      g.qs.forEach((q, i) => {
        col += 1;
        const end = col > qCount / 2 && qCount > 3;
        qRow.push(h("th", { scope: "col", class: i === 0 ? "hbg-start" : null },
          h("span", { class: "hbg-qh" }, qidTip(q, labels[i], end), restBox(m, q, end))));
      });
    }
    const cols = m.cols;
    if (cols) {
      // Overall and Tier stay pinned on the right, so they get a header cell of their own over
      // them; the section totals scroll with the questions under "Score".
      const end = 1 + (cols.band ? 1 : 0);
      if (cols.sections.length) {
        secRow.push(h("th", { class: "hbg-sec hbg-start", scope: "colgroup", colspan: String(cols.sections.length) },
          h("div", { class: "hbg-secname" }, "Score")));
      }
      secRow.push(h("th", { class: "hbg-sec hbg-pin-end" + (cols.sections.length ? "" : " hbg-start"), scope: "colgroup", colspan: String(end) },
        cols.sections.length ? null : h("div", { class: "hbg-secname" }, "Score")));
      cols.sections.forEach((s, i) => qRow.push(h("th", {
        scope: "col", class: i === 0 ? "hbg-start" : null, title: s.name ? String(s.name) : null,
      }, String(s.id))));
      qRow.push(h("th", { scope: "col", class: "hbg-pin-n" + (cols.sections.length ? "" : " hbg-start") }, "Overall"));
      if (cols.band) qRow.push(h("th", { scope: "col", class: "hbg-pin-r", style: "text-align: left" }, cols.band === "overall" ? "Tier" : "Tier " + cols.band));
    }
    return { head: h("thead", null, h("tr", null, secRow), h("tr", null, qRow)), width: 1 + qCount + (cols ? cols.sections.length + 1 + (cols.band ? 1 : 0) : 0) };
  }

  function bodyRow(ctx, m, unit, sections) {
    let sub = m.sortLabel ? labelOf(unit, m.sortLabel) : null;
    // "Canada / Canada": a label that only repeats what the name already says is left off.
    if (sub && String(unit.name || unit.id).toLowerCase().includes(String(sub).toLowerCase())) sub = null;
    const cells = [h("th", { scope: "row", class: "hbg-rh" }, unit.name || unit.id, HB.gridGaps.marker(unit), sub ? h("span", { class: "hbg-sub" }, sub) : null)];
    const docs = HB.gridGaps.note(ctx, m, unit);
    if (docs) {
      cells.push(h("td", { class: "hbg-docnote hbg-start", colspan: String(sections.reduce((n, g) => n + g.qs.length, 0) || 1) }, docs));
      return h("tr", null, cells, totalsCells(m, unit));
    }
    for (const g of sections) {
      g.qs.forEach((q, i) => {
        const td = tableCell(ctx, m, unit, q);
        if (i === 0) td.classList.add("hbg-start");
        cells.push(td);
      });
    }
    return h("tr", null, cells, totalsCells(m, unit));
  }

  // One group's table, opened in place under its strip header: its own questions.
  function groupTable(ctx, m, g) {
    const qids = new Set();
    for (const sec of m.sections) for (const q of sec.qs) if (g.rows.some((u) => ctx.data.applies(u.id, q.id))) qids.add(q.id);
    const sections = sectionsFor(m.bundle, qids);
    const { head } = tableHead(m, sections);
    return h("div", { class: "hbg-scroll", role: "region", tabindex: "0", "aria-label": g.title + " table" },
      h("table", { class: "hbg-table" + (m.cols && m.cols.band ? " has-band" : "") },
        h("caption", { class: "visually-hidden" }, g.title + ": points per question"),
        head,
        h("tbody", null, g.rows.map((u) => bodyRow(ctx, m, u, sections)))));
  }

  // Every row in view in one table, with a divider row per group.
  function allTable(ctx, m) {
    const { head, width } = tableHead(m, m.sections);
    const bodies = m.groups.map((g) => {
      const rows = g.rows.map((u) => bodyRow(ctx, m, u, m.sections));
      if (!m.grouped) return h("tbody", null, rows);
      const mean = m.groupMean(g.rows);
      const flagged = m.groupFlagged(g.rows);
      const meta = [ctx.unitWords(g.rows.length)];
      if (mean !== null) meta.push("mean " + m.overallText({ overall: Math.round(mean) }));
      if (flagged) meta.push(fmtInt(flagged) + " flagged");
      return h("tbody", null,
        h("tr", { class: "hbg-divider" }, h("th", { scope: "rowgroup", colspan: String(width) },
          h("span", { class: "hbg-divider__in" }, h("span", { class: "hbg-divider__name" }, g.title),
            h("span", { class: "hbg-divider__meta" }, meta.join(" · "))))),
        rows);
    });
    return h("div", { class: "hbg-scroll hbg-scroll--all", role: "region", tabindex: "0", "aria-label": "The grid as a table" },
      h("table", { class: "hbg-table" + (m.cols && m.cols.band ? " has-band" : "") },
        h("caption", { class: "visually-hidden" }, "Points per question for every " + (m.p.unit_word || "row") + " in view"),
        head, bodies));
  }

  /* ---- Controls ---------------------------------------------------------------- */

  // Focus the first shown match after a redraw (some controls exist twice, one
  // for wide screens and one for phones, with one of them hidden).
  function refocus(selector) {
    const el = Array.from(document.querySelectorAll(selector)).find((x) => x.getClientRects().length);
    if (el) el.focus();
  }

  function segmented(name, options, value, onPick) {
    return h("div", { class: "segmented hbg-seg", role: "group", "aria-label": name },
      options.map(([v, text]) => h("button", {
        type: "button",
        class: "btn btn--small",
        "aria-pressed": v === value ? "true" : "false",
        "data-value": v,
        onclick: () => onPick(v),
      }, text)));
  }

  function groupControl(ctx, m) {
    const opts = m.groupLabels.map((l) => [l, cap(labelName(l))]).concat([["", "No grouping"]]);
    const pick = (v) => {
      ctx.setGroupBy(v);
      refocus('.hbg-group [aria-pressed="true"], .hbg-group select');
    };
    const select = h("select", {
      class: "input hbg-sel", "aria-label": "Group by", onchange: (e) => pick(e.target.value),
    }, opts.map(([v, text]) => h("option", { value: v, selected: v === m.groupBy }, text)));
    if (m.groupLabels.length <= 4) {
      // Wide screens get the segmented control; a phone the select (one is hidden).
      return h("div", { class: "hbg-group" },
        h("span", { class: "hbg-group__seg hbg-only-wide" }, segmented("Group by", opts, m.groupBy, pick)),
        h("span", { class: "hbg-only-phone" }, select));
    }
    return h("div", { class: "hbg-group" }, select);
  }

  function filterSelects(ctx, m) {
    const state = ctx.state;
    return m.labels.map((label) => {
      const id = "hbg-filter-" + label.replace(/[^A-Za-z0-9_-]/g, "-");
      const opts = [["", "Any " + labelName(label)]].concat(ctx.data.labelValues(label).map((v) => [v, v]));
      if (m.units.some((u) => labelOf(u, label) === null)) opts.push([NONE, labelName(label) + " not recorded"]);
      const value = state.filters[label] || "";
      return h("select", {
        id, class: "input hbg-sel" + (value ? " is-set" : ""), "aria-label": "Filter by " + labelName(label),
        onchange: (e) => {
          const v = e.target.value;
          if (v) state.filters[label] = v;
          else delete state.filters[label];
          ctx.render();
          refocus("#" + id);
        },
      }, opts.map(([v, text]) => h("option", { value: v, selected: v === value }, text)));
    });
  }

  // The key explains the marks of the layout in view: strips or table on a wide
  // screen (items with hbg-k-wide), the squares on a phone (hbg-k-phone). Each
  // mark there is any of in view carries its count, so the key is also the counts.
  function keyList(m, view) {
    const c = m.counts;
    const n = (k) => (k ? h("b", { class: "hbg-key__n" }, fmtInt(k)) : null);
    const sample = (cls, style, ...kids) => h("span", { class: cls, style, "aria-hidden": "true" }, kids);
    const box = "width: 1.75rem; height: 1.25rem; border: 1px solid var(--rule); font-size: var(--fs-12);";
    const sq = "flex: none; width: 1.25rem;";
    const unitWord = m.p.unit_word || "row";
    const wide = "hbg-k-wide";
    const phone = "hbg-k-phone";
    const ramp = (base, style) => h("span", { class: "hbg-key__ramp", "aria-hidden": "true" },
      [0, 1, 2, 3, 4].map((s) => sample(base + " hbg-s" + s, style)));
    const li = (cls, ...kids) => h("li", { class: cls }, kids.map((k) => (typeof k === "string" ? h("span", { class: "hbg-key__text" }, k) : k)));
    const items = [];
    if (view === "strips") {
      items.push(
        li(wide, sample("hbg-mk", "--v: 0.6"), "Green fill: the points scored. A full square is top marks; a thin sliver, a low score; empty grey, none", n(c.answered)),
        li(wide, sample("hbg-mk is-ne"), "Hatched: no evidence found", n(c.ne)),
        li(wide, sample("hbg-mk is-flag", "--v: 0"), "Red corner, top right: something to check on this answer, one of the kinds below. Hover over it to see which; click it and the panel says why at the top", n(c.flagged)),
        li(wide, sample("hbg-mk is-wait"), "Not scored yet", n(c.waiting)),
        c.owner ? li(wide, sample("hbg-mk is-wait is-owner"), "Dashed: waits for data you need to supply", n(c.owner)) : null,
        li(wide, sample("hbg-mk is-na"), "A gap: the question does not apply"),
      );
    } else {
      items.push(
        li(wide, ramp("hbg-cell", "border: 1px solid var(--rule)"), "Shade: share of the question's top points, none to all", n(c.answered)),
        li(wide, sample("hbg-cell", box, "0", h("span", { class: "hbg-ne" }, "∅")), "No evidence found", n(c.ne)),
        li(wide, sample("hbg-cell is-wait", box), "Not scored yet", n(c.waiting)),
        c.owner ? li(wide, sample("hbg-cell is-wait is-owner", box, "data"), "Waits for data you need to supply", n(c.owner)) : null,
        li(wide, sample("hbg-cell is-muted", box, "n/a"), "Does not apply"),
      );
    }
    items.push(
      li(phone, ramp("hbg-sq", sq), "Shade: share of the question's top points, none to all", n(c.answered)),
      li(phone, sample("hbg-sq is-ne", sq, "0"), "No evidence found", n(c.ne)),
      li(phone, sample("hbg-sq is-wait", sq), "Not scored yet", n(c.waiting)),
      c.owner ? li(phone, sample("hbg-sq is-wait is-owner", sq), "Dashed: waits for data you need to supply", n(c.owner)) : null,
      li(phone, sample("hbg-sq is-na", sq), "A gap: the question does not apply"),
      li(null, view === "strips" ? sample("hbg-mk", "--v: 0", h("i", { class: "hbg-mk__stale" })) : sample("hbg-cell is-stale", box),
        "Dark corner, top left: the question changed since it was scored", n(c.stale)),
    );
    if (view !== "strips") items.push(li(wide, sample("hbg-cell", box, h("span", { class: "hbg-cell__flags" }, HB.flagGlyph("quote_not_found"))),
      "Red sign on a cell's corner: something to check, one of the kinds below. Hover over the cell to see which; open it to see what to do", n(c.flagged)));
    items.push(li(phone, sample("hbg-sq", sq, HB.flagGlyph("quote_not_found")),
      "Red sign on a square: something to check, one of the kinds below. Open it to see what to do", n(c.flagged)));
    // The kinds of check with some in view are listed; the others fold away under one line.
    const kind = (k) => li(null, view === "strips" ? null : HB.flagGlyph(k), FLAG_MEANING[k] ? h("span", { class: "hbg-key__text" }, h("b", null, HB.flagKinds.words[k]), ": " + FLAG_MEANING[k].replace("{unit}", unitWord)) : HB.flagKinds.words[k], n(c.kinds[k]));
    const others = HB.flagKinds.factual.filter((k) => !c.kinds[k]);
    for (const k of HB.flagKinds.factual) if (c.kinds[k]) items.push(kind(k));
    items.push(li(null, h("span", { class: "hbg-rm", "aria-hidden": "true" }, "12"),
      "Under a question: how many answers shown rest on Claude's reading of a rule you have not decided yet (hover to see which rules)"));
    if (m.showRests) items.push(li(null, sample("hbg-cell", box + " outline: 1.5px dashed var(--ink); outline-offset: -2px"), "Rests on an open rule", n(c.rests)));
    if (m.hl.size) items.push(li(null, sample("hbg-cell is-hl", box), "Highlighted"));
    return [h("ul", { class: "hbg-key__list" }, items),
      others.length ? h("details", { class: "hbg-key__more" }, h("summary", null, "Other kinds of check, none in view (" + others.length + ")"),
        h("ul", { class: "hbg-key__list" }, others.map(kind))) : null];
  }

  /* One quiet line over the grid: how many rows are in view, how many answers, how many to check,
     then View and the one action. A filter in use is named on the line with Clear, since it hides
     rows. Everything else (group by, filters, Strips or Table, the outline switch, the key with its
     counts) sits in the View panel, which stays open across redraws while it is being used. */
  function toolbar(ctx, m, view) {
    const state = ctx.state;
    const c = m.counts;
    const total = m.units.length;
    const part = (...kids) => h("span", { class: "hbg-counts__part" }, kids);
    const parts = [];
    parts.push(part(h("b", null, fmtInt(m.shown.length)), m.shown.length === total
      ? " " + ctx.unitWords(total).replace(/^[\d,]+ /, "")
      : " of " + ctx.unitWords(total)));
    const set = m.labels.filter((l) => m.filters[l]);
    if (set.length) {
      // A value in words names itself ("Kenya", "Already scored"); a bare number needs its label ("law year 2010").
      const said = (l) => { const v = m.filters[l]; return v === NONE ? labelName(l) + " not recorded" : /\p{L}/u.test(v) ? v : labelName(l) + " " + v; };
      parts.push(part(set.map(said).join(", "),
        h("button", { type: "button", class: "btn btn--small btn--quiet hbg-clear", onclick: () => {
          state.filters = {};
          ctx.render();
          refocus(".hbg-view > summary");
        } }, "Clear")));
    }
    parts.push(part(h("b", null, fmtInt(c.answered)), " answered"));
    if (c.flagged) parts.push(part(h("b", null, fmtInt(c.flagged)), " to check"));
    const bar = [h("div", { class: "hbg-counts", role: "status" }, parts)];
    bar.push(h("span", { class: "hbg-bar__grow" }));
    const hlCount = Array.isArray(state.highlight) ? state.highlight.length : 0;
    if (hlCount) {
      bar.push(h("button", { type: "button", class: "btn btn--small", onclick: () => ctx.highlight([]) },
        "Clear highlight (" + fmtInt(hlCount) + (hlCount === 1 ? " cell)" : " cells)")));
    }
    bar.push(viewPanel(ctx, m, view));
    const addMore = HB.bulk ? HB.bulk.button() : null;   // "Add documents or instructions", the local page only (bulk.js)
    if (addMore) bar.push(addMore);
    return h("div", { class: "hbg-bar" }, bar);
  }

  function viewPanel(ctx, m, view) {
    const state = ctx.state;
    const nFilters = m.labels.filter((l) => m.filters[l]).length;
    const row = (title, ...kids) => h("div", { class: "hbg-view__row" }, h("span", { class: "hbg-view__label" }, title), kids);
    const arrange = [row("Group by", groupControl(ctx, m))];
    if (m.labels.length) {
      arrange.push(row("Show only", h("div", { class: "hbg-filters" }, filterSelects(ctx, m),
        nFilters ? h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => {
          state.filters = {};
          ctx.render();
          refocus(".hbg-view > summary");
        } }, "Clear filters") : null)));
    }
    arrange.push(h("div", { class: "hbg-view__row hbg-only-wide" }, h("span", { class: "hbg-view__label" }, "Layout"),
      segmented("Layout", [["strips", "Strips"], ["table", "Table"]], view, (v) => {
        state.gridView = v;
        HB.store.set("grid-view", v);
        ctx.render();
        refocus('.hbg-view [data-value="' + v + '"]');
      })));
    if (m.counts.rests) {
      const id = HB.uid("hbg-rests");
      const r = m.counts.rests;
      arrange.push(h("label", { class: "hbg-switch", for: id },
        h("input", {
          id, type: "checkbox", role: "switch", checked: m.showRests,
          onchange: (e) => {
            state.showRests = e.target.checked;
            ctx.render();
            refocus(".hbg-switch input");
          },
        }),
        "Outline the " + fmtInt(r) + (r === 1 ? " answer that rests" : " answers that rest") + " on rules you have not decided"));
    }
    const panel = h("div", { class: "hbg-key__panel hbg-view__panel" },
      h("div", { class: "hbg-view__arrange" }, arrange),
      h("div", { class: "hbg-view__key" }, h("h3", null, "What the marks mean"), keyList(m, view)));
    return h("details", {
      class: "hbg-key hbg-view", open: Boolean(state.gridPanelOpen),
      ontoggle: (e) => { state.gridPanelOpen = e.target.open; },
    }, h("summary", { class: "btn btn--small" }, "View"), panel);
  }

  /* ---- The tab ----------------------------------------------------------------- */

  /** A line over the grid when questions are off to the right, so nobody takes the visible ones for all
      of them (the box's own scrollbar sits at its foot, often off screen). It goes once scrolled to the end. */
  function moreToTheRight(root) {
    requestAnimationFrame(() => {
      const box = root.querySelector(".hbg-desk .hbg-ov, .hbg-desk .hbg-scroll");
      if (!box || box.scrollWidth <= box.clientWidth + 4) return;
      const note = h("p", { class: "hbg-more small muted" }, "More questions to the right: scroll the grid sideways (Shift and the mouse wheel, or swipe) to see them.");
      box.parentNode.insertBefore(note, box);
      box.addEventListener("scroll", () => { note.hidden = box.scrollLeft + box.clientWidth >= box.scrollWidth - 4; }, { passive: true });
    });
  }

  function render(root, ctx) {
    HB.addStyles("grid", scoped(STYLE));
    const bundle = ctx.bundle;
    const state = ctx.state;
    const p = bundle.project || {};
    const units = Array.isArray(bundle.units) ? bundle.units.filter((u) => u && u.id) : [];
    const questions = Array.isArray(bundle.questions) ? bundle.questions : [];
    if (!state.gridView) state.gridView = HB.store.get("grid-view", "strips") === "table" ? "table" : "strips";
    HB.gridRows = () => [];

    root.append(h("h2", { class: "visually-hidden" }, "Grid"));
    if (!units.length || !questions.length) {
      root.append(HB.notice("warning", [
        (units.length ? "No questions" : "No " + (p.unit_word_plural || "rows")) + " in the project yet, so the grid is empty.",
        "It fills in as the set-up goes on: see the Set-up tab.",
      ]));
      return;
    }

    const m = model(ctx);
    const view = state.gridView;
    root.classList.toggle("hbg-rests-on", m.showRests);
    root.append(toolbar(ctx, m, view));
    if (Array.isArray(state.onlyRows)) {
      root.append(h("p", { class: "muted small" }, "Showing only the " + ctx.unitWords(m.shown.length) + " Claude pointed to. ",
        h("button", { type: "button", class: "btn btn--small btn--quiet", onclick: () => { state.onlyRows = null; ctx.render(); } }, "Show all")));
    }

    if (!m.shown.length) {
      root.append(HB.notice("neutral", "No " + (p.unit_word_plural || "rows") + " match these filters."));
    } else {
      root.append(h("div", { class: "hbg-desk" }, view === "table" ? allTable(ctx, m) : strips(ctx, m)));
      if (typeof HB.gridPhone === "function") root.append(h("div", { class: "hbg-phone" }, HB.gridPhone(ctx, m)));
      moreToTheRight(root);
    }
    const inView = new Set(m.shown.map((u) => u.id));
    const hidden = (Array.isArray(state.highlight) ? state.highlight : []).filter((c) => !inView.has(String(c[0]))).length;
    if (hidden > 0) {
      root.insertBefore(h("p", { class: "muted small" }, fmtInt(hidden) + (hidden === 1 ? " highlighted cell is" : " highlighted cells are") +
        " hidden by the filters."), root.querySelector(".hbg-desk, .notice"));
    }

    // One click handler for every cell, mark and square on the tab.
    root.addEventListener("click", (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target && e.target.closest ? e.target.closest("a[data-row][data-q]") : null;
      if (!a || !root.contains(a)) return;
      e.preventDefault();
      ctx.openCell(a.getAttribute("data-row"), a.getAttribute("data-q"));
    });

    // Show which cell is open (the panel also keeps this up to date as it moves).
    const openCell = state.openCell;
    if (openCell) {
      for (const a of root.querySelectorAll("a[data-row][data-q]")) {
        if (a.getAttribute("data-row") === openCell.row && a.getAttribute("data-q") === openCell.qid) a.classList.add("is-open");
      }
    }

    // Row order for the cell panel's Previous and Next.
    const order = m.groups.flatMap((g) => g.rows.map((u) => u.id));
    HB.gridRows = () => order.slice();

    // Bring a new highlight into view once.
    const hlKey = Array.from(m.hl).sort().join("|");
    if (hlKey && hlKey !== lastHighlight) {
      const first = Array.from(root.querySelectorAll(".is-hl[data-row]")).find((el) => el.offsetParent !== null);
      if (first) requestAnimationFrame(() => first.scrollIntoView({ block: "center", inline: "center" }));
    }
    lastHighlight = hlKey;
  }

  // Printing the Table view: a table wider than the paper is shrunk to fit, so no question falls off
  // the right edge (A4 or Letter landscape leaves about 950 CSS pixels).
  const PRINT_W = 950;
  window.addEventListener("beforeprint", () => {
    for (const t of document.querySelectorAll(".hbg-table")) {
      const w = t.scrollWidth;
      t.style.zoom = w > PRINT_W ? String(Math.max(0.35, PRINT_W / w)) : "";
    }
  });
  window.addEventListener("afterprint", () => {
    for (const t of document.querySelectorAll(".hbg-table")) t.style.zoom = "";
  });

  // A key panel closes on Escape or a click outside it.
  document.addEventListener("click", (e) => {
    // A control inside the panel redraws the tab before this runs, leaving its target detached.
    if (!e.target || !e.target.isConnected) return;
    for (const d of document.querySelectorAll(".hbg-key[open]")) if (!d.contains(e.target)) d.open = false;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    for (const d of document.querySelectorAll(".hbg-key[open]")) {
      d.open = false;
      const s = d.querySelector("summary");
      if (s) s.focus();
    }
  });

  (HB.views = HB.views || {}).grid = {
    id: "grid",
    label: "Grid",
    order: 40,
    render,
    visible() {
      return true;
    },
  };
})((window.HB = window.HB || {}));
