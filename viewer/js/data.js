/* =============================================================================
   Handbook viewer: data.js, the one way to read the page's data.
   The bundle sits in the page as <script type="application/json" id="hb-data">;
   the saved text of cited pages and Claude's notes sit beside the page in
   evidence/<row>.json (past 400 rows, shared evidence/part-NN.json files) and are
   fetched only when a panel needs them.

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
                                from found[row].expected_not_found; by "scorer"
                                and questions when a scorer needed it to answer
                                them; on a row Claude searched the web for, those
                                wait for its second search), lost: [{id,
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
    // A document a scorer named on a row whose documents Claude found on the web waits for the row's
    // second search before the owner is asked for it (rowwatch.py); on the owner's own rows it shows at once.
    const watch = (b && b.watch && b.watch[row]) || {};
    const waitSearch = watch.source === "public" && (f.second_search == null);
    const missing = (Array.isArray(f.expected_not_found) ? f.expected_not_found : [])
      .map((m) => (m && typeof m === "object" ? m : { what: m }))
      .filter((m) => m.what != null && String(m.what).trim() !== "")
      .filter((m) => !(waitSearch && m.by === "scorer"))
      .map((m) => ({ what: String(m.what), where_looked: m.where_looked ? String(m.where_looked) : null,
        by: m.by === "scorer" ? "scorer" : null, questions: Array.isArray(m.questions) ? m.questions.map(String) : [] }));
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

  const isEvidence = (data) => Boolean(data && typeof data === "object" && data.pages && typeof data.pages === "object");
  const getJson = (name) => fetch(name).then((res) => (res.ok ? res.json() : null)).catch(() => null);

  /* Past 400 rows with answers, rows share evidence files (bundle.py evidence_part): the sum of the
     row id's character codes, mod 100. */
  function evidencePart(row) {
    let sum = 0;
    for (let i = 0; i < row.length; i += 1) sum += row.charCodeAt(i);
    return "part-" + String(sum % 100).padStart(2, "0");
  }

  function evidence(row) {
    if (typeof row !== "string" || !ROW_ID.test(row)) return Promise.resolve(null);
    if (!evidenceCache.has(row)) {
      const promise = getJson("evidence/" + row + ".json")
        .then((data) => (isEvidence(data) ? data
          : getJson("evidence/" + evidencePart(row) + ".json").then((part) => {
            const ev = part && part.rows && typeof part.rows === "object" ? part.rows[row] : null;
            return isEvidence(ev) ? ev : null;
          })))
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
