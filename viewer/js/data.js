/* =============================================================================
   Handbook viewer: data.js, the one way to read the page's data.
   The bundle sits in the page as <script type="application/json" id="hb-data">;
   the saved text of cited pages and Claude's notes sit beside the page in
   evidence/<row>.json and are fetched only when a panel needs them.

   HB.data.bundle()             the parsed bundle (parsed once), or null when the
                                page holds nothing readable
   HB.data.evidence(row)        a Promise of {row, pages: {"<doc>#<page>": text},
                                notes: {qid: Claude's note}} for that row (notes
                                may be missing), fetched once and cached; resolves to
                                null when the file can't be read (say
                                HB.data.EVIDENCE_MISSING then). Never rejects.
   HB.data.unit(id)             the unit (row) with that id, or null
   HB.data.question(id)         the question with that id, or null
   HB.data.cell(row, qid)       the bundle's cell, or null when the question does
                                not apply to that row (the bundle has no cell then)
   HB.data.applies(row, qid)    true when the bundle holds a cell for the pair
   HB.data.questionFor(row, qid) the question as that row sees it: a copy with the
                                first matching variant's wording and rule in place
   HB.data.labelValues(label)   the distinct values of a unit label, sorted
   HB.data.unitWords(n)         "1 assessment", "3 assessments" (the project's
                                unit_word and unit_word_plural)
   HB.data.isScored(cell)       true for a cell with an answer (answered or
                                no_evidence)
   HB.data.foundCount(row)      how many documents Claude has found for a row
   HB.data.gaps(row)            what Claude could not get for a row: {missing:
                                [{what, where_looked}] (expected but not found,
                                from found[row].expected_not_found), lost: [{id,
                                title, url, why}] (found, but no saved copy kept:
                                documents[id].kept === false, not_kept_why)}
   HB.data.gapRows()            the ids of the rows with any gap, in unit order
   ========================================================================== */

(function (HB) {
  "use strict";

  const EVIDENCE_MISSING = "Saved text not available in this view";
  const ROW_ID = /^[a-z0-9-]+$/;
  const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });

  let parsed;            // undefined until first read, then the bundle or null
  const evidenceCache = new Map();

  function bundle() {
    if (parsed !== undefined) return parsed;
    parsed = null;
    const el = document.getElementById("hb-data");
    try {
      const value = el ? JSON.parse(el.textContent) : null;
      if (value && typeof value === "object" && value.project && typeof value.project === "object") parsed = value;
    } catch (err) {
      parsed = null;
    }
    return parsed;
  }

  function list(key) {
    const b = bundle();
    return b && Array.isArray(b[key]) ? b[key] : [];
  }

  function unit(id) {
    return list("units").find((u) => u && u.id === id) || null;
  }

  function question(id) {
    return list("questions").find((q) => q && q.id === id) || null;
  }

  function cell(row, qid) {
    const b = bundle();
    const cells = b && b.cells && b.cells[row];
    return cells && Object.prototype.hasOwnProperty.call(cells, qid) ? cells[qid] : null;
  }

  function applies(row, qid) {
    return cell(row, qid) !== null;
  }

  function matches(when, labels) {
    if (!when || typeof when !== "object") return false;
    return Object.keys(when).every((label) => {
      const wanted = Array.isArray(when[label]) ? when[label] : [when[label]];
      return labels && wanted.includes(labels[label]);
    });
  }

  function questionFor(row, qid) {
    const q = question(qid);
    if (!q) return null;
    const copy = Object.assign({}, q);
    const u = unit(row);
    const variant = (Array.isArray(q.variants) ? q.variants : []).find((v) => v && matches(v.when, u && u.labels));
    if (variant) {
      if (typeof variant.wording === "string") copy.wording = variant.wording;
      if (typeof variant.rule === "string") copy.rule = variant.rule;
      copy.variant = variant;
    }
    return copy;
  }

  function labelValues(label) {
    const seen = new Set();
    for (const u of list("units")) {
      const value = u && u.labels ? u.labels[label] : null;
      if (value != null && value !== "") seen.add(String(value));
    }
    return Array.from(seen).sort(collator.compare);
  }

  function unitWords(n) {
    const b = bundle();
    const p = (b && b.project) || {};
    const one = p.unit_word || "row";
    const many = p.unit_word_plural || one + "s";
    return HB.fmtInt(n) + " " + (n === 1 ? one : many);
  }

  function isScored(c) {
    return Boolean(c) && (c.state === "answered" || c.state === "no_evidence");
  }

  function found(row) {
    const b = bundle();
    return (b && b.found && b.found[row]) || {};
  }

  function foundCount(row) {
    const ids = found(row).documents;
    return Array.isArray(ids) ? ids.length : 0;
  }

  function gaps(row) {
    const b = bundle();
    const f = found(row);
    const missing = (Array.isArray(f.expected_not_found) ? f.expected_not_found : [])
      .map((m) => (m && typeof m === "object" ? { what: m.what, where_looked: m.where_looked } : { what: m }))
      .filter((m) => m.what != null && String(m.what).trim() !== "")
      .map((m) => ({ what: String(m.what), where_looked: m.where_looked ? String(m.where_looked) : null }));
    const docs = (b && b.documents) || {};
    const lost = (Array.isArray(f.documents) ? f.documents : [])
      .filter((id) => typeof id === "string" && docs[id] && docs[id].kept === false)
      .map((id) => ({ id, title: String(docs[id].title || docs[id].url || id), url: docs[id].url || null, why: docs[id].not_kept_why ? String(docs[id].not_kept_why) : null }));
    return { missing, lost };
  }

  function gapRows() {
    return list("units").filter((u) => u && u.id).map((u) => u.id).filter((id) => {
      const g = gaps(id);
      return g.missing.length || g.lost.length;
    });
  }

  function evidence(row) {
    if (typeof row !== "string" || !ROW_ID.test(row)) return Promise.resolve(null);
    if (!evidenceCache.has(row)) {
      const promise = fetch("evidence/" + row + ".json")
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => (data && typeof data === "object" && data.pages && typeof data.pages === "object" ? data : null))
        .catch(() => null);
      evidenceCache.set(row, promise);
    }
    return evidenceCache.get(row);
  }

  /* replace(next): put a newer bundle in place of the page's own (hb serve's local.js, after a
     rebuild) and forget the evidence fetched so far. False when `next` is not a bundle. */
  function replace(next) {
    if (!next || typeof next !== "object" || !next.project || typeof next.project !== "object") return false;
    parsed = next;
    evidenceCache.clear();
    return true;
  }

  HB.data = {
    EVIDENCE_MISSING,
    bundle,
    replace,
    evidence,
    unit,
    question,
    cell,
    applies,
    questionFor,
    labelValues,
    unitWords,
    isScored,
    foundCount,
    gaps,
    gapRows,
  };
})((window.HB = window.HB || {}));
