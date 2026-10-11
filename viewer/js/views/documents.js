/* =============================================================================
   Handbook viewer: views/documents.js, the Documents tab.
   Registers HB.views.documents. One block per row (assessment): the documents
   found for it (title, publisher, where it was found, date, how Claude found it,
   links), and what was expected but not found. Rows with no documents come first,
   then rows whose documents are all weak sources (bundle documents[id].weak,
   {why, auto} or null). Filter by any label or to weak sources, and search by
   name; the counts line always states the whole picture, and "Showing X of Y"
   states what the filters leave.
   Reads only the bundle (found/<row>.json and documents/<doc>/meta.json).
   No generated text: every sentence is fixed copy or the bundle's own words.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, statusChip, notice, link, addStyles, fmtInt, NOT_RECORDED } = HB;
  HB.views = HB.views || {};

  const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });

  const HOST_WORDS = {
    issuer: "Company's own site",
    repository: "Repository copy",
    third_party: "Third-party copy",
    test_server: "Test server",
    none: "Unknown",
  };

  const HOW_WORDS = {
    plan: "From your plan",
    search: "From a search",
    last_year: "From last year's list",
    provided: "Provided by you",
  };

  // What the filters were left at, kept while the reader moves between tabs.
  const view = { query: "", filters: {}, weak: false };

  const arr = (x) => (Array.isArray(x) ? x : x == null ? [] : [x]);
  const words = (label) => String(label).replace(/_/g, " ");
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const count = (n, one, many) => fmtInt(n) + " " + (n === 1 ? one : many);

  function fmtDate(iso) {
    const when = new Date(String(iso) + "T00:00:00Z");
    return Number.isNaN(when.getTime()) ? String(iso) : DATE.format(when);
  }

  function hostWords(doc) {
    return Object.prototype.hasOwnProperty.call(HOST_WORDS, doc.host_type) ? HOST_WORDS[doc.host_type] : "Unknown";
  }

  const isWeak = (doc) => Boolean(doc && doc.weak && doc.weak.why);

  function howWords(doc) {
    if (Object.prototype.hasOwnProperty.call(HOW_WORDS, doc.how)) return HOW_WORDS[doc.how];
    return doc.provided_by_owner ? HOW_WORDS.provided : "How found not recorded";
  }

  /* ---- The data, row by row ------------------------------------------------------- */

  function gather(b) {
    const found = b.found || {};
    const docs = b.documents || {};
    const rows = arr(b.units).map((u) => {
      const record = found[u.id] || null;
      const ids = arr(record && record.documents);
      return {
        unit: u,
        searched: Boolean(record),
        docs: ids.map((id) => docs[id] || { id, title: null, unknown: true }),
        weak: ids.filter((id) => isWeak(docs[id])).length,
        missing: arr(record && record.expected_not_found),
        second: record && record.second_search ? record.second_search : null,
      };
    });
    const linked = new Set();
    Object.keys(found).forEach((r) => arr(found[r].documents).forEach((id) => linked.add(id)));
    const shown = Array.from(linked);
    return {
      rows,
      distinct: shown.length,
      notKept: shown.filter((id) => docs[id] && docs[id].kept === false).length,
      noRecord: shown.filter((id) => !docs[id]).length,
      weak: shown.filter((id) => isWeak(docs[id])).length,
      unlinked: Object.keys(docs).filter((id) => !linked.has(id)).length,
      none: rows.filter((r) => !r.docs.length).length,
    };
  }

  const allWeak = (r) => r.docs.length > 0 && r.weak === r.docs.length;

  // Rows with no documents first, then rows whose documents are all weak, then by name.
  function ordered(rows) {
    const rank = (r) => (!r.docs.length ? 0 : allWeak(r) ? 1 : 2);
    return rows.slice().sort((a, c) => rank(a) - rank(c) || collator.compare(a.unit.name || a.unit.id, c.unit.name || c.unit.id));
  }

  /* ---- Pieces ------------------------------------------------------------------------- */

  function rowChip(r) {
    const found = r.docs.length;
    if (!r.searched) return statusChip("neutral", "Not searched yet");
    if (!found) return statusChip("serious", "None found");
    if (allWeak(r)) return statusChip("warning", count(found, "document", "documents") + " found, " + (found === 1 ? "a weak source" : "all weak sources"));
    if (r.missing.length) return statusChip("warning", count(found, "document", "documents") + " found, " + fmtInt(r.missing.length) + " not found");
    return statusChip("good", count(found, "document", "documents") + " found");
  }

  function docItem(doc) {
    const title = doc.title || doc.url || doc.id;
    const meta = [];
    if (doc.unknown) {
      meta.push(h("span", null, "No record of this document is in the folder"));
    } else {
      if (doc.publisher) meta.push(h("span", null, String(doc.publisher)));
      meta.push(h("span", null, hostWords(doc)));
      meta.push(h("span", null, doc.date ? "Dated " + fmtDate(doc.date) : "Date " + NOT_RECORDED.toLowerCase()));
      meta.push(h("span", null, howWords(doc)));
    }
    return h(
      "li",
      { class: "dc-doc" },
      h("p", { class: "dc-doc__title" }, String(title)),
      meta.length ? h("p", { class: "dc-doc__meta" }, meta) : null,
      isWeak(doc) ? h("p", { class: "dc-doc__weak" }, HB.flagGlyph("weak_source"), h("span", null, h("strong", null, "Not from the company: "), String(doc.weak.why))) : null,
      h(
        "p",
        { class: "dc-doc__links" },
        doc.url ? link(doc.url, "Open source") : h("span", { class: "muted" }, "No web address"),
        doc.archive_url ? link(doc.archive_url, "Archived copy") : null,
        doc.kept === false ? statusChip("neutral", "Saved copy not kept") : null,
      ),
    );
  }

  function rowBlock(r, labels, one) {
    const u = r.unit;
    const docs = view.weak ? r.docs.filter(isWeak) : r.docs;
    const tags = labels
      .filter((l) => u.labels && u.labels[l] != null && u.labels[l] !== "")
      .map((l) => h("span", { class: "dc-tag" }, h("span", { class: "muted" }, words(l)), " ", String(u.labels[l])));
    return h(
      "article",
      { class: "dc-row", "data-empty": r.docs.length ? "false" : "true", "aria-label": u.name || u.id },
      h(
        "div",
        { class: "dc-row__head" },
        h("div", { class: "dc-row__who" }, h("h4", { class: "dc-row__name" }, u.name ? String(u.name) : String(u.id)), h("span", { class: "dc-row__id mono" }, String(u.id))),
        rowChip(r),
      ),
      tags.length ? h("p", { class: "dc-tags" }, tags) : null,
      r.docs.length
        ? h("ul", { class: "dc-docs" }, docs.map(docItem))
        : h("p", { class: "dc-none muted" }, r.searched ? "No documents were found for this " + one + "." : "No search has been recorded for this " + one + " yet."),
      r.missing.length
        ? h(
          "div",
          { class: "dc-missing" },
          h("p", { class: "dc-missing__title" }, "Expected but not found"),
          h(
            "ul",
            null,
            r.missing.map((m) =>
              h("li", null, h("strong", null, m.what ? String(m.what) : NOT_RECORDED), m.where_looked ? h("span", { class: "muted" }, " Looked: " + m.where_looked) : h("span", { class: "muted" }, " Where looked not recorded"))),
          ),
        )
        : null,
      r.second ? h("p", { class: "dc-second muted small" }, "Second search done " + fmtDate(String(r.second).slice(0, 10)) + ".") : null,
    );
  }

  /* ---- The tab ------------------------------------------------------------------------------ */

  function render(root) {
    const b = HB.data.bundle();
    if (!b) {
      root.append(h("h2", null, "Documents"), notice("warning", "The page holds no readable benchmark data."));
      return;
    }
    const labels = arr(b.project && b.project.labels);
    const plural = (b.project && b.project.unit_word_plural) || "rows";
    const one = (b.project && b.project.unit_word) || "row";
    const data = gather(b);
    const total = data.rows.length;

    const nWord = (n) => count(n, one, plural);
    const summary = h("p", { class: "dc-count" });
    const shown = h("p", { class: "dc-shown", id: "dc-shown", role: "status" });
    const list = h("div", { class: "dc-list" });

    function drawSummary() {
      if (!total) {
        summary.replaceChildren("No " + plural + " are listed yet, so there are no documents to show.");
        return;
      }
      const parts = [
        h("strong", null, fmtInt(data.distinct)), " ", data.distinct === 1 ? "document" : "documents", " for ", h("strong", null, nWord(total)), "; ",
        h("strong", null, nWord(data.none)), data.none === 1 ? " has none." : " have none.",
      ];
      const extra = [];
      if (data.notKept) extra.push(count(data.notKept, "document has", "documents have") + " no saved copy");
      if (data.noRecord) extra.push(count(data.noRecord, "document is", "documents are") + " named in a search but missing from the folder");
      if (data.weak) extra.push(count(data.weak, "document is a weak source", "documents are weak sources"));
      if (data.unlinked) extra.push(count(data.unlinked, "saved document is", "saved documents are") + " not linked to any " + one);
      if (extra.length) parts.push(" ", cap(extra.join("; ")), ".");
      summary.replaceChildren(...parts);
    }

    function visibleRows() {
      const q = view.query.trim().toLowerCase();
      const only = Array.isArray(HB.state.onlyRows) ? new Set(HB.state.onlyRows) : null;
      return ordered(data.rows).filter((r) => {
        if (only && !only.has(r.unit.id)) return false;
        if (q && !String(r.unit.name || "").toLowerCase().includes(q) && !String(r.unit.id).toLowerCase().includes(q)) return false;
        if (view.weak && !r.weak) return false;
        return Object.keys(view.filters).every((l) => !view.filters[l] || String((r.unit.labels || {})[l]) === view.filters[l]);
      });
    }

    function drawList() {
      const rows = visibleRows();
      shown.replaceChildren("Showing ", h("strong", null, fmtInt(rows.length)), " of ", h("strong", null, fmtInt(total)), " ", total === 1 ? one : plural,
        ...(Array.isArray(HB.state.onlyRows)
          ? [", the ones in Claude's note. ", h("button", { type: "button", class: "btn btn--small", onclick: () => { HB.state.onlyRows = null; drawList(); } }, "Show all")]
          : ["."]));
      list.replaceChildren(
        ...(rows.length
          ? rows.map((r) => rowBlock(r, labels, one))
          : [h("div", { class: "dc-empty" }, total ? "Nothing matches these filters." : "Documents will be listed here for each " + one + " once Claude starts looking.")]),
      );
    }

    const searchId = "dc-search";
    const search = h("input", {
      class: "input",
      id: searchId,
      type: "search",
      value: view.query,
      autocomplete: "off",
      oninput: (event) => { view.query = event.currentTarget.value; drawList(); },
    });
    const selects = labels.map((label) => {
      const values = HB.data.labelValues(label);
      const id = "dc-filter-" + label.replace(/[^a-z0-9]/gi, "-");
      return h(
        "div",
        { class: "field" },
        h("label", { class: "field__label", for: id }, cap(words(label))),
        h(
          "select",
          {
            class: "input",
            id,
            onchange: (event) => { view.filters[label] = event.currentTarget.value; drawList(); drawClear(); },
          },
          h("option", { value: "" }, "All"),
          values.map((v) => h("option", { value: v, selected: view.filters[label] === v }, v)),
        ),
      );
    });
    const weakId = "dc-filter-weak";
    const weakSelect = h(
      "div",
      { class: "field" },
      h("label", { class: "field__label", for: weakId }, "Source strength"),
      h(
        "select",
        { class: "input", id: weakId, onchange: (event) => { view.weak = event.currentTarget.value === "weak"; drawList(); drawClear(); } },
        h("option", { value: "" }, "All documents"),
        h("option", { value: "weak", selected: view.weak }, "Not from the company only"),
      ),
    );
    const clear = h("button", {
      class: "btn btn--quiet btn--small",
      type: "button",
      onclick: () => {
        view.query = "";
        view.filters = {};
        view.weak = false;
        weakSelect.querySelector("select").value = "";
        search.value = "";
        selects.forEach((s) => { s.querySelector("select").value = ""; });
        drawList();
        drawClear();
      },
    }, "Clear filters");
    function drawClear() {
      clear.hidden = !(view.query || view.weak || Object.keys(view.filters).some((l) => view.filters[l]));
    }
    search.addEventListener("input", drawClear);

    drawSummary();
    drawList();
    drawClear();

    root.append(
      h(
        "div",
        { class: "hb-view dc" },
        h(
          "header",
          { class: "page-head" },
          h("h2", null, "Documents"),
          h("p", { class: "lede" }, "What Claude found for each " + one + ", and what it expected but could not find."),
        ),
        h("div", { class: "dc-counts" }, summary),
        total
          ? h(
            "div",
            { class: "dc-filters" },
            h("div", { class: "field dc-filters__search" }, h("label", { class: "field__label", for: searchId }, "Search by name"), search),
            selects,
            weakSelect,
            h("div", { class: "dc-filters__clear" }, clear),
          )
          : null,
        shown,
        list,
      ),
    );
  }

  /* ---- Styles: tokens only ----------------------------------------------------------------------- */

  addStyles("documents", `
.dc { gap: var(--s4); }
.dc-count { max-width: 56rem; font-variant-numeric: tabular-nums; }
.dc-count strong { font-weight: 600; }
.dc-shown { color: var(--ink-2); font-size: var(--fs-14); font-variant-numeric: tabular-nums; }
.dc-shown strong { color: var(--ink); font-weight: 600; }
.dc-filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); align-items: end; gap: var(--s3) var(--s4); max-width: 60rem; }
.dc-filters__search { grid-column: 1 / -1; max-width: 26rem; }
.dc-filters__clear { align-self: end; }
.dc-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 26rem), 1fr)); align-items: start; gap: var(--s3); }
.dc-empty { grid-column: 1 / -1; max-width: var(--measure); padding: var(--s4); border: 1px dashed var(--rule-strong); border-radius: var(--radius); color: var(--ink-2); }

.dc-row { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); min-width: 0; padding: var(--s4); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); overflow-wrap: anywhere; }
.dc-row[data-empty="true"] { border-color: var(--rule-strong); }
.dc-row__head { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: var(--s2) var(--s3); }
.dc-row__who { display: grid; gap: 2px; min-width: 0; }
.dc-row__name { font-family: var(--sans); font-size: var(--fs-16); font-weight: 600; }
.dc-row__id { color: var(--ink-2); font-size: var(--fs-12); }
.dc-tags { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s3); color: var(--ink); font-size: var(--fs-12); }
.dc-none { font-size: var(--fs-14); }

.dc-docs { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.dc-doc { display: grid; gap: 2px; padding-block: var(--s2); border-top: 1px solid var(--rule); font-size: var(--fs-14); }
.dc-doc:first-child { border-top: 0; padding-top: 0; }
.dc-doc__title { font-weight: 600; }
.dc-doc__meta { display: flex; flex-wrap: wrap; gap: 0 var(--s2); color: var(--ink-2); font-size: var(--fs-12); }
.dc-doc__meta span + span::before { content: "\\00b7"; margin-right: var(--s2); }
.dc-doc__weak { display: flex; align-items: baseline; gap: var(--s1); width: fit-content; padding: 1px var(--s2); border-radius: var(--radius); background: color-mix(in srgb, var(--warning) 14%, var(--surface)); font-size: var(--fs-12); }
.dc-doc__weak .flag-glyph { flex: none; align-self: center; color: var(--ink); }
.dc-doc__links { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); font-size: var(--fs-12); }

.dc-missing { display: grid; gap: var(--s1); padding: var(--s3); border: 1px solid color-mix(in srgb, var(--warning) 50%, var(--surface)); border-radius: var(--radius); background: color-mix(in srgb, var(--warning) 9%, var(--surface)); font-size: var(--fs-14); }
.dc-missing__title { font-size: var(--fs-12); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
.dc-missing ul { display: grid; gap: var(--s1); margin: 0; padding: 0; list-style: none; }
`);

  HB.views.documents = { id: "documents", label: "Documents", order: 30, render, visible: () => true };
})((window.HB = window.HB || {}));
