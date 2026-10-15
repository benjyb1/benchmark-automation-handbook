/* =============================================================================
   Handbook viewer: views/setup.js, the Set-up tab.
   Registers HB.views.setup. It is the first screen while Claude drafts a
   benchmark, so every part has a designed empty state and fills as the bundle
   does: the checklist (must-haves, then optional extras), the data the owner
   needs to supply, then the benchmark as agreed (questions by section, the score
   sheet, units, group labels, cut-off, where to look, what was provided).
   Reads only the bundle. Edit buttons come from HB.edits (edits.js, Task 11) and
   appear only when HB.edits.ready; otherwise the page says edits need Claude.
   No generated text: every sentence is fixed copy or the bundle's own words.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, statusChip, notice, countedTable, link, addStyles, fmtInt, fmtNum, NOT_RECORDED } = HB;
  HB.views = HB.views || {};

  let ONE = "row";   // the project's singular unit word, set when the tab draws
  const EDITS_LINE = () => HB.cantSave();
  const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

  /* ---- The checklist: the plan's thirteen keys, must-haves first --------------- */

  const MUST_HAVES = ["questions", "units", "scoresheet", "group_labels", "cutoff", "where_to_look", "needs", "rules"];
  const EXTRAS = ["last_results", "methodology", "source_list", "held_documents", "last_report"];

  function checklistItems(plural) {
    const lower = plural.toLowerCase();
    return {
      questions: ["Questions", "What is asked of each " + ONE + ", with the answer levels and points."],
      units: [cap(plural), "The " + lower + " to be scored, with their labels."],
      scoresheet: ["Score sheet", "How points become section scores, an overall score and bands."],
      group_labels: ["Group labels", "Which labels the " + lower + " can be grouped by, and the default."],
      cutoff: ["Cut-off date", "The date documents must be dated on or before."],
      where_to_look: ["Where to look", "Where to search for documents, and last edition's links."],
      needs: ["Data you need to supply", "Documents only you hold that the benchmark needs."],
      rules: ["Rules", "Questions where the methodology leaves room for judgement."],
      last_results: ["Last edition's results", "Last edition's scores, kept aside to compare with ours."],
      methodology: ["Methodology", "The document that says how the benchmark works."],
      source_list: ["Source list", "Last edition's list of sources."],
      held_documents: ["Documents you hold", "Documents you already have, to be used as sources."],
      last_report: ["Last edition's report", "Last edition's report, for the new one to follow."],
    };
  }

  const ROLE_WORDS = {
    questions: "Question list",
    units: "List of rows",
    methodology: "Methodology",
    last_results: "Last edition's results",
    source_list: "Source list",
    held_document: "Document you hold",
    last_report: "Last edition's report",
    other: "Other",
  };

  const METHOD_WORDS = {
    weighted_mean_of_section_percents: "A weighted average of the section percentages",
    sum_points: "The total of all points",
  };

  const ROUNDING_WORDS = {
    none: "No rounding",
    half_up_int: "Whole numbers, halves round up",
    half_up_1dp: "One decimal place, halves round up",
    half_up_2dp: "Two decimal places, halves round up",
  };

  /* ---- Small helpers ------------------------------------------------------------ */

  const has = (o, k) => o != null && Object.prototype.hasOwnProperty.call(o, k);
  const arr = (x) => (Array.isArray(x) ? x : x == null ? [] : [x]);
  const words = (label) => String(label).replace(/_/g, " ");
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const count = (n, one, many) => fmtInt(n) + " " + (n === 1 ? one : many);

  function fmtDate(iso) {
    const when = new Date(String(iso) + "T00:00:00Z");
    return Number.isNaN(when.getTime()) ? String(iso) : DATE.format(when);
  }

  function unitName(id) {
    const u = HB.data.unit(id);
    return u && u.name ? u.name : String(id);
  }

  function nameList(ids, limit) {
    const list = arr(ids).map(unitName);
    const max = limit || 4;
    return list.length > max ? list.slice(0, max).join(", ") + " and " + fmtInt(list.length - max) + " more" : list.join(", ");
  }

  // {label: [values]} as "brand: Acme; country: Denmark".
  function labelWords(map) {
    return Object.keys(map || {}).map((k) => words(k) + ": " + arr(map[k]).join(", ")).join("; ");
  }

  function editsReady() {
    return Boolean(HB.edits && HB.edits.ready);
  }

  function editButton(target, id, field) {
    if (!editsReady() || typeof HB.edits.editButton !== "function") return null;
    try {
      const button = HB.edits.editButton(target, id, field);
      return button instanceof Node ? button : null;
    } catch (err) {
      console.error("HB.edits.editButton failed:", err);
      return null;
    }
  }

  // A field's content with its edit button beside it, when there is one.
  function editable(content, target, id, field) {
    const button = editButton(target, id, field);
    if (!button) return content;
    return h("div", { class: "su-edit" }, h("div", { class: "su-edit__body" }, content), h("div", { class: "su-edit__button" }, button));
  }

  function confirmButton(what, label) {
    return h(
      "button",
      {
        class: "btn btn--small",
        type: "button",
        onclick: (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          Promise.resolve()
            .then(() => HB.edits.confirm(what))
            .catch((err) => console.error("HB.edits.confirm failed:", err))
            .then(() => { button.disabled = false; });
        },
      },
      label,
    );
  }

  /* ---- Status chips --------------------------------------------------------------- */

  // Checklist rows: icon, word and colour together. A missing must-have asks for
  // attention; a missing extra does not.
  function checklistChip(status, mustHave) {
    switch (status) {
      case "confirmed": return statusChip("good", "Confirmed");
      case "draft": return statusChip("warning", "Draft", { icon: "person" });
      case "skipped": return statusChip("neutral", "Skipped");
      case "not_needed": return statusChip("neutral", "Not needed");
      default: return mustHave ? statusChip("serious", "Missing") : statusChip("neutral", "Missing");
    }
  }

  function draftChip(status) {
    if (status === "confirmed") return statusChip("good", "Confirmed");
    if (status === "draft") return statusChip("warning", "Draft", { icon: "person" });
    return statusChip("neutral", status ? cap(words(status)) : "Not set");
  }

  function needChip(status) {
    if (status === "supplied") return statusChip("good", "Supplied");
    if (status === "waived") return statusChip("neutral", "Waived");
    return statusChip("warning", "Needed", { icon: "person" });
  }

  /* ---- Empty states and section frames ------------------------------------------- */

  function empty(title, text) {
    return h("div", { class: "su-empty" }, h("p", { class: "su-empty__title" }, title), h("p", { class: "su-empty__text" }, text));
  }

  function sub(title, meta, actions) {
    return h(
      "div",
      { class: "section__head" },
      h("h4", { class: "su-sub__title" }, title),
      h("div", { class: "su-sub__meta" }, meta ? h("span", { class: "muted small" }, meta) : null, actions),
    );
  }

  // One failing part never blanks the tab.
  function safely(title, build) {
    try {
      return build();
    } catch (err) {
      console.error("Set-up: " + title + " failed:", err);
      return notice("warning", ["The " + title + " could not be shown. ", String(err && err.message ? err.message : err)]);
    }
  }

  /* ---- Checklist ---------------------------------------------------------------------- */

  function checklistSection(b, plural) {
    const status = (b.project && b.project.checklist) || {};
    const text = checklistItems(plural);
    const known = new Set(MUST_HAVES.concat(EXTRAS));
    const extras = EXTRAS.concat(Object.keys(status).filter((k) => !known.has(k)));
    const stateOf = (key) => (has(status, key) ? status[key] : "missing");

    const row = (key, mustHave) => {
      const t = text[key] || [cap(words(key)), ""];
      return h(
        "li",
        { class: "su-check", "data-status": stateOf(key) },
        h("div", { class: "su-check__text" }, h("span", { class: "su-check__label" }, t[0]), t[1] ? h("span", { class: "su-check__hint" }, t[1]) : null),
        h("div", { class: "su-check__chip" }, checklistChip(stateOf(key), mustHave)),
      );
    };

    const settled = (key) => ["confirmed", "skipped", "not_needed"].includes(stateOf(key));
    const done = MUST_HAVES.filter(settled).length;
    const drafts = MUST_HAVES.filter((k) => stateOf(k) === "draft").length;
    const missing = MUST_HAVES.length - done - drafts;
    const bits = [];
    if (drafts) bits.push(fmtInt(drafts) + " in draft");
    if (missing) bits.push(fmtInt(missing) + " missing");

    return h(
      "section",
      { class: "section", "aria-labelledby": "su-h-check" },
      h("div", { class: "section__head" }, h("h3", { id: "su-h-check" }, "Checklist")),
      h(
        "div",
        { class: "su-progress su-narrow" },
        h("p", { class: "su-progress__text" }, h("strong", null, fmtInt(done) + " of " + fmtInt(MUST_HAVES.length)), " must-haves settled" + (bits.length ? ". " + cap(bits.join(", ")) + "." : ".")),
        h(
          "div",
          { class: "su-segs", "aria-hidden": "true" },
          MUST_HAVES.map((key) => h("span", { class: "su-seg", "data-state": settled(key) ? "done" : stateOf(key) === "draft" ? "draft" : "missing" })),
        ),
      ),
      h("div", { class: "su-narrow stack" },
        h("p", { class: "su-list-title" }, "Must-haves"),
        h("ul", { class: "su-checks" }, MUST_HAVES.map((k) => row(k, true))),
        h("p", { class: "su-list-title" }, "Optional extras"),
        h("ul", { class: "su-checks" }, extras.map((k) => row(k, false))),
      ),
    );
  }

  /* ---- Data to supply -------------------------------------------------------------------- */

  function needsSection(b, plural) {
    const needs = arr(b.needs);
    let body;
    if (!needs.length) {
      body = empty("Nothing requested yet", "If the benchmark needs documents only you hold, they are listed here with the questions that depend on them.");
    } else {
      const open = needs.filter((n) => !n.status || n.status === "needed").length;
      body = countedTable({
        copyName: "Data you need to supply",
        rows: needs,
        expected: needs.length,
        shownLabel: ["request", "requests"],
        note: open ? count(open, "is", "are") + " still needed." : "None are still needed.",
        columns: [
          { label: "What", rowHeader: true, value: (n) => n.what, wide: true },
          {
            label: "For questions",
            value: (n) => arr(n.why).join(", "),
            render: (n) => (arr(n.why).length ? h("span", { class: "mono" }, arr(n.why).join(", ")) : "Not stated"),
            text: (n) => arr(n.why).join(", "),
          },
          { label: cap(plural), value: (n) => (n.rows == null ? "All " + plural : nameList(n.rows)), wide: true },
          { label: "Status", value: (n) => n.status || "needed", render: (n) => needChip(n.status), text: (n) => cap(n.status || "needed") },
          {
            label: "Documents",
            value: (n) => arr(n.documents).length,
            render: (n) => (arr(n.documents).length ? count(arr(n.documents).length, "document", "documents") : "None yet"),
            text: (n) => String(arr(n.documents).length),
          },
        ],
      });
    }
    return h(
      "section",
      { class: "section", "aria-labelledby": "su-h-needs" },
      h("div", { class: "section__head" }, h("h3", { id: "su-h-needs" }, "Data you need to supply")),
      body,
    );
  }

  /* ---- The benchmark as agreed: questions ------------------------------------------------------- */

  function levelWords(q, level) {
    const v = level.value;
    return q.answer && q.answer.kind === "yes_no" ? cap(String(v)) : String(v);
  }

  function pointsWords(p) {
    return typeof p !== "number" ? NOT_RECORDED : fmtNum(p) + (p === 1 ? " point" : " points");
  }

  function levelsList(q) {
    const levels = q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
    const figure = HB.isFigureQuestion(q)
      ? h("li", { class: "su-level" }, h("span", { class: "su-level__value" }, HB.figureWords(q)),
          h("span", { class: "su-level__points" }, "No points"),
          h("span", { class: "su-level__meaning" }, "The figure the evidence gives, tracked but not added to any total.",
            HB.isNum(q.no_evidence_value) ? h("span", { class: "su-level__tag" }, "Used when no evidence is found") : null))
      : null;
    if (!levels.length && !figure) return h("span", { class: "muted" }, "No answer levels yet.");
    return h(
      "ul",
      { class: "su-levels" },
      figure,
      levels.map((level) =>
        h(
          "li",
          { class: "su-level" },
          h("span", { class: "su-level__value" }, levelWords(q, level)),
          h("span", { class: "su-level__points" }, pointsWords(level.points)),
          h(
            "span",
            { class: "su-level__meaning" },
            level.meaning ? String(level.meaning) : h("span", { class: "muted" }, "No meaning written"),
            q.no_evidence_value != null && level.value === q.no_evidence_value
              ? h("span", { class: "su-level__tag" }, "Used when no evidence is found")
              : null,
          ),
        ),
      ),
    );
  }

  function appliesWords(q, plural, total) {
    if (!q.applies_to || !Object.keys(q.applies_to).length) return "All " + plural;
    const n = HB.data.bundle().units.filter((u) => HB.data.applies(u.id, q.id)).length;
    return labelWords(q.applies_to) + (total ? " (" + fmtInt(n) + " of " + fmtInt(total) + " " + plural + ")" : "");
  }

  function questionBlock(q, plural, total) {
    const facts = [];
    const fact = (label, content) => facts.push(h("dt", null, label), h("dd", null, content));
    fact("Answer levels", editable(levelsList(q), "question", q.id, "levels_points"));
    fact("Rule", editable(q.rule ? String(q.rule) : h("span", { class: "muted" }, "No rule written"), "question", q.id, "rule"));
    fact("Applies to", editable(appliesWords(q, plural, total), "question", q.id, "applies_to"));
    const variants = arr(q.variants);
    if (variants.length) {
      fact(
        "Different for",
        h(
          "ul",
          { class: "su-variants" },
          variants.map((v) =>
            h(
              "li",
              null,
              h("strong", null, labelWords(v.when) || "Some rows"),
              v.wording ? h("span", null, "Wording: ", v.wording) : null,
              v.rule ? h("span", null, "Rule: ", v.rule) : null,
            ),
          ),
        ),
      );
    }
    return h(
      "li",
      { class: "su-q" },
      h(
        "div",
        { class: "su-q__head" },
        h("span", { class: "su-q__id mono" }, String(q.id)),
        h("div", { class: "su-q__short" }, editable(h("strong", null, q.short ? String(q.short) : "Untitled question"), "question", q.id, "short")),
        draftChip(q.status),
      ),
      editable(h("p", { class: "su-q__wording" }, q.wording ? String(q.wording) : "No wording yet."), "question", q.id, "wording"),
      h("dl", { class: "su-facts" }, facts),
      h("p", { class: "su-q__from" }, "From: ", q.from ? String(q.from) : NOT_RECORDED),
    );
  }

  function questionsSection(b, plural) {
    const questions = arr(b.questions);
    const sheet = b.scoresheet;
    const total = arr(b.units).length;
    const draft = questions.filter((q) => q.status !== "confirmed").length;
    const status = (b.project.checklist || {}).questions;
    const actions = status === "draft" && editsReady() && HB.edits && typeof HB.edits.confirm === "function"
      ? confirmButton("questions", "Confirm all questions")
      : null;

    let body;
    if (!questions.length) {
      body = empty("No questions yet", "Each question will be listed here by section, with its answer levels and points, for you to confirm.");
    } else {
      // Sections in the score sheet's order, then any other section ids in the order they appear.
      const order = arr(sheet && sheet.sections).map((s) => s.id);
      questions.forEach((q) => { if (!order.includes(q.section)) order.push(q.section); });
      const nameOf = (id) => {
        const s = arr(sheet && sheet.sections).find((x) => x.id === id);
        return s && s.name ? s.name : id == null ? "No section" : "";
      };
      body = h(
        "div",
        { class: "stack" },
        order
          .map((id) => ({ id, list: questions.filter((q) => q.section === id) }))
          .filter((g) => g.list.length)
          .map((g) =>
            h(
              "details",
              { class: "su-group", open: true },
              h(
                "summary",
                null,
                h("span", { class: "su-group__name" }, g.id == null ? "No section" : h("span", null, h("span", { class: "mono" }, String(g.id)), nameOf(g.id) ? " " + nameOf(g.id) : "")),
                h("span", { class: "su-group__count muted" }, count(g.list.length, "question", "questions") +
                  (g.list.some((q) => q.status !== "confirmed") ? ", " + fmtInt(g.list.filter((q) => q.status !== "confirmed").length) + " to confirm" : "")),
              ),
              h("ul", { class: "su-qs" }, g.list.map((q) => questionBlock(q, plural, total))),
            ),
          ),
      );
    }
    return h(
      "div",
      { class: "stack" },
      sub("Questions", questions.length ? count(questions.length, "question", "questions") + (draft ? ", " + fmtInt(draft) + " not yet confirmed" : "") : "", actions),
      body,
    );
  }

  /* ---- Score sheet ------------------------------------------------------------------------------------- */

  function scoreSheetSection(b) {
    const s = b.scoresheet;
    if (!s) {
      return h("div", { class: "stack" }, sub("Score sheet"), empty("No score sheet yet", "How points turn into section scores, an overall score and bands will be written here in plain words."));
    }
    const sections = arr(s.sections);
    const overall = s.overall || {};
    const weights = overall.weights || {};
    const parts = [];
    if (s.computable === false) {
      parts.push(notice("neutral", "Totals are not worked out for this benchmark. The words above are the whole score sheet."));
    } else {
      const qCount = (id) => arr(b.questions).filter((q) => q.section === id).length;
      parts.push(
        h("dl", { class: "su-facts su-narrow" },
          h("dt", null, "Overall score"),
          h("dd", null, (METHOD_WORDS[overall.method] || (overall.method ? words(overall.method) : NOT_RECORDED)) +
            (overall.method === "weighted_mean_of_section_percents" && overall.use_rounded_sections ? ", using the rounded section scores" : "")),
          h("dt", null, "Overall rounding"),
          h("dd", null, ROUNDING_WORDS[overall.rounding] || NOT_RECORDED),
        ),
      );
      if (sections.length) {
        parts.push(
          countedTable({
            copyName: "Score sheet sections",
            rows: sections,
            expected: sections.length,
            shownLabel: ["section", "sections"],
            columns: [
              { label: "Section", rowHeader: true, mono: true, value: (x) => x.id },
              { label: "Name", value: (x) => x.name },
              { label: "Questions", num: true, value: (x) => qCount(x.id) },
              { label: "Counts as", value: (x) => (x.percent ? "Percentage of points" : "Points") },
              { label: "Weight", value: (x) => (has(weights, x.id) ? weights[x.id] : null), format: (v) => fmtNum(v * 100, 1) + "%", empty: "Not weighted" },
              { label: "Rounding", value: (x) => ROUNDING_WORDS[x.rounding] || null },
            ],
          }),
        );
      }
      const bandSets = arr(s.bands).filter((set) => arr(set.bands).length);
      if (bandSets.length) {
        parts.push(
          h("div", { class: "su-narrow stack stack--tight" },
            h("p", { class: "su-list-title" }, "Bands"),
            h("ul", { class: "su-bands" }, bandSets.map((set) =>
              h("li", null,
                h("strong", null, set.of === "overall" ? "Overall" : "Section " + set.of),
                h("span", null, arr(set.bands).slice().sort((x, y) => y.min - x.min).map((x) => x.name + " from " + fmtNum(x.min)).join(", ")),
              ))),
          ),
        );
      }
    }
    return h(
      "div",
      { class: "stack" },
      sub("Score sheet", null, draftChip(s.status)),
      h("p", { class: "su-plain su-narrow" }, s.plain ? String(s.plain) : h("span", { class: "muted" }, "No plain-words description yet.")),
      ...parts,
    );
  }

  /* ---- Units ------------------------------------------------------------------------------------------------------- */

  function unitsSection(b, plural) {
    const units = arr(b.units);
    const labels = arr(b.project.labels);
    const status = (b.project.checklist || {}).units;
    const actions = status === "draft" && editsReady() && HB.edits && typeof HB.edits.confirm === "function"
      ? confirmButton("units", "Confirm all " + plural.toLowerCase())
      : null;
    let body;
    if (!units.length) {
      body = empty("None listed yet", "Each " + ONE + " will be listed here with its labels, for you to confirm.");
    } else {
      const labelText = (u) => labels.filter((l) => u.labels && u.labels[l] != null && u.labels[l] !== "").map((l) => words(l) + ": " + u.labels[l]).join("; ");
      body = countedTable({
        copyName: cap(plural),
        rows: units,
        expected: units.length,
        shownLabel: [ONE, plural.toLowerCase()],
        columns: [
          { label: "Name", rowHeader: true, wide: true, value: (u) => u.name, render: (u) => editable(h("span", null, u.name ? String(u.name) : NOT_RECORDED), "unit", u.id, "name"), text: (u) => u.name || "" },
          { label: "Id", mono: true, value: (u) => u.id },
          {
            label: "Labels",
            wide: true,
            value: labelText,
            render: (u) => editable(
              labelText(u)
                ? h("span", { class: "su-tags" }, labels.filter((l) => u.labels && u.labels[l] != null && u.labels[l] !== "").map((l) => h("span", { class: "su-tag" }, h("span", { class: "muted" }, words(l)), " ", String(u.labels[l]))))
                : h("span", { class: "muted" }, "No labels"),
              "unit", u.id, "labels"),
            text: labelText,
          },
          { label: "Status", value: (u) => u.status || "", render: (u) => draftChip(u.status), text: (u) => cap(u.status || "") },
          { label: "From", value: (u) => u.from },
        ],
      });
    }
    return h("div", { class: "stack" }, sub(cap(plural), units.length ? count(units.length, "row", "rows") : "", actions), body);
  }

  /* ---- Group labels, cut-off, where to look, what was provided ------------------------------------------------------ */

  function groupSection(b) {
    const p = b.project;
    const labels = arr(p.labels);
    const groupLabels = arr(p.group_labels);
    let body;
    if (!labels.length) {
      body = empty("No labels yet", "Labels such as brand or country, and which of them can group the rows, will be listed here.");
    } else {
      body = h(
        "ul",
        { class: "su-labels su-narrow" },
        labels.map((l) => {
          const n = HB.data.labelValues(l).length;
          return h(
            "li",
            null,
            h("strong", null, words(l)),
            h("span", { class: "muted" }, n ? count(n, "value", "values") : "No values yet"),
            h("span", { class: "su-labels__roles" },
              groupLabels.includes(l) ? h("span", { class: "su-role" }, "Can group by") : h("span", { class: "su-role su-role--off" }, "Not used for grouping"),
              p.group_by === l ? h("span", { class: "su-role su-role--default" }, "Default grouping") : null),
          );
        }),
      );
    }
    return h("div", { class: "stack" }, sub("Group labels and grouping"), body,
      labels.length && !p.group_by ? h("p", { class: "muted small" }, "No default grouping is set.") : null);
  }

  function cutoffSection(b) {
    const cutoff = b.project.cutoff;
    return h(
      "div",
      { class: "stack stack--tight" },
      sub("Cut-off date"),
      cutoff
        ? h("p", { class: "su-narrow" }, h("strong", { class: "su-date" }, fmtDate(cutoff)), " ", h("span", { class: "muted" }, "Documents dated after this day are flagged."))
        : empty("Not set yet", "The date documents must be dated on or before. Documents dated after it get flagged."),
    );
  }

  function whereSection(b, plural) {
    const w = b.where_to_look || {};
    const plan = arr(w.plan);
    const lastYear = arr(w.last_year);
    const parts = [];
    if (!plan.length && !lastYear.length) {
      parts.push(empty("Nowhere listed yet", "The places to search for each " + ONE + ", and last edition's links, will be listed here."));
    }
    if (plan.length) {
      parts.push(
        h("div", { class: "su-plan su-narrow" }, plan.map((step) => {
          const f = step.for;
          const who = f === "all" || f == null ? "All " + plural.toLowerCase()
            : Array.isArray(f.rows) ? nameList(f.rows) : labelWords(f);
          return h(
            "div",
            { class: "su-plan__step" },
            h("p", { class: "su-plan__for" }, h("span", { class: "su-plan__kicker" }, "For"), " ", who || "Not stated"),
            h("ul", { class: "su-plan__places" }, arr(step.places).map((pl) =>
              h("li", null,
                pl.url ? link(pl.url, pl.what || undefined) : h("span", null, pl.what ? String(pl.what) : NOT_RECORDED),
                pl.url ? null : h("span", { class: "muted small" }, " No address yet"),
                pl.note ? h("span", { class: "muted small" }, " " + pl.note) : null)),
            ),
          );
        })),
      );
    }
    if (lastYear.length) {
      parts.push(
        h("div", { class: "stack stack--tight" },
          h("p", { class: "su-list-title" }, "Last edition's links"),
          countedTable({
            copyName: "Last edition's links",
            rows: lastYear,
            expected: { n: arr(b.units).length, label: [b.project.unit_word || "row", b.project.unit_word_plural || "rows"] },
            shownLabel: ["link", "links"],
            columns: [
              { label: cap(b.project.unit_word || "row"), rowHeader: true, value: (x) => unitName(x.row) },
              { label: "Title", wide: true, value: (x) => x.title },
              { label: "Link", value: (x) => x.url, render: (x) => (x.url ? link(x.url) : "No address"), text: (x) => x.url || "" },
            ],
          })),
      );
    }
    return h("div", { class: "stack" }, sub("Where to look"), ...parts);
  }

  function providedSection(b, plural) {
    const provided = arr(b.provided);
    return h(
      "div",
      { class: "stack" },
      sub("What was provided", provided.length ? count(provided.length, "file", "files") : ""),
      provided.length
        ? countedTable({
          copyName: "Files provided",
          rows: provided,
          expected: provided.length,
          shownLabel: ["file", "files"],
          columns: [
            { label: "File", rowHeader: true, mono: true, value: (x) => x.name },
            { label: "Used as", value: (x) => (x.role === "units" ? "List of " + plural.toLowerCase() : ROLE_WORDS[x.role] || cap(words(x.role || "other"))) },
            { label: "Note", wide: true, value: (x) => x.note || null, empty: "No note" },
          ],
        })
        : empty("Nothing provided yet", "Files you give Claude, such as the methodology or last edition's results, will be listed here."),
    );
  }

  /* ---- Your material: files for the set-up, and Draft my set-up (only under hb serve) --------------------------------------- */

  const material = { files: null, line: "", busy: false };

  async function loadMaterial(box) {
    try {
      const res = await HB.local.get("material");
      material.files = res.ok ? (await res.json()).files || [] : [];
    } catch (e) {
      material.files = [];
    }
    paintMaterial(box);
  }

  let materialBox = null;   // the Your material section on screen, for files dropped on the set-up chat

  /** Uploads files into inbox/uploads/ and repaints Your material; Promise of the names added. */
  async function addMaterial(box, list) {
    const tool = window.claude && typeof window.claude.use === "function" ? await window.claude.use("assets") : null;
    const picked = Array.from(list || []);
    if (!tool || !picked.length) return [];
    const paint = () => { if (box && box.isConnected) paintMaterial(box); };
    material.busy = true;
    material.line = "Adding " + count(picked.length, "file", "files") + "\u2026";
    paint();
    const failed = [];
    const added = [];
    for (const f of picked) {
      const type = HB.uploadTypes[(f.name.split(".").pop() || "").toLowerCase()];
      if (!type) { failed.push(f.name + " (save it as PDF, Word, Excel or a picture)"); continue; }
      try { await tool.upload(f, { type }); added.push(f.name); } catch (e) { failed.push(f.name); }
    }
    material.busy = false;
    material.line = failed.length ? "Not added: " + failed.join("; ") + "." : "";
    if (box && box.isConnected) loadMaterial(box); else material.files = null;
    return added;
  }

  HB.setupMaterial = { add: (list) => addMaterial(materialBox, list) };

  async function draftSetup(box) {
    material.busy = true;
    try {
      await HB.edits.save("setup", { action: "draft", at: new Date().toISOString() });
      material.line = "Claude is reading your files and drafting the set-up. Progress shows in the run bar; this page fills in when it is done.";
    } catch (e) {
      material.line = HB.cantSave();
    }
    material.busy = false;
    paintMaterial(box);
  }

  /** A quiet drop area: drag files onto it, or click it to choose them. The browser's own file
      control stays hidden inside it. */
  function materialDrop(box) {
    const input = h("input", { type: "file", class: "visually-hidden", multiple: true, id: "su-material-input",
      accept: HB.uploadAccept, onchange: () => { addMaterial(box, input.files); } });
    const zone = h("label", { class: "su-drop", for: "su-material-input", "data-busy": String(material.busy) },
      input,
      h("span", { class: "su-drop__lead" }, material.busy ? "Adding\u2026" : "Drop files here, or ", material.busy ? null : h("span", { class: "su-drop__pick" }, "choose them")),
      h("span", { class: "su-drop__hint" }, "PDF, Word, Excel, pictures or text"));
    const over = (on) => (e) => { e.preventDefault(); zone.classList.toggle("is-over", on); };
    zone.addEventListener("dragenter", over(true));
    zone.addEventListener("dragover", over(true));
    zone.addEventListener("dragleave", over(false));
    zone.addEventListener("drop", (e) => { over(false)(e); if (!material.busy) addMaterial(box, e.dataTransfer && e.dataTransfer.files); });
    return zone;
  }

  function paintMaterial(box) {
    const files = material.files;
    box.replaceChildren(...[
      sub("Your material", files && files.length ? count(files.length, "file", "files") : ""),
      h("p", { class: "small muted" }, "Add what you have: your questions, the list you score, how scores are worked out, last " +
        "edition's results (strongly advised: it makes the results far more accurate), the methodology and last year's sources."),
      materialDrop(box),
      files === null ? h("p", { class: "small muted" }, "Looking\u2026")
        : files.length ? h("ul", { class: "su-files small" }, files.map((f) => h("li", null, f.name + " (" + f.kind + ")" +
            (f.chars == null ? ": can't be read" : "")))) : null,
      files && files.length ? h("p", null, h("button", { type: "button", class: "btn btn--small btn--primary",
        disabled: material.busy || !editsReady(), onclick: () => draftSetup(box) }, "Draft my set-up"),
        h("span", { class: "small muted" }, " Claude reads every file and drafts the questions, the list, the score sheet " +
          "and the groups for you to check. It uses some of your Claude plan.")) : null,
      material.line ? h("p", { class: "small" }, material.line) : null,
    ].filter(Boolean));   // replaceChildren would write a null out as the word "null"
  }

  function materialSection() {
    const box = h("section", { class: "section stack", "aria-label": "Your material" });
    materialBox = box;
    if (material.files === null) loadMaterial(box); else paintMaterial(box);
    return box;
  }

  /* ---- The tab -------------------------------------------------------------------------------------------------------------- */

  function watchEdits(wrap) {
    if (!HB.edits || HB.edits.ready) return;
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      if (!wrap.isConnected || tries > 12) return clearInterval(timer);
      if (HB.edits && HB.edits.ready) {
        clearInterval(timer);
        HB.render();
      }
    }, 500);
  }

  function render(root) {
    const b = HB.data.bundle();
    if (!b) {
      root.append(h("h2", null, "Set-up"), notice("warning", "The page holds no readable benchmark data."));
      return;
    }
    const plural = (b.project && b.project.unit_word_plural) || "rows";
    ONE = (b.project && b.project.unit_word) || "row";
    const stage = b.project && b.project.stage;
    const wrap = h(
      "div",
      { class: "hb-view su" },
      h(
        "header",
        { class: "page-head" },
        h("h2", null, "Set-up"),
        h("p", { class: "lede" }, stage === "setup" || !stage
          ? "Claude drafts the benchmark with you here. This page fills in as it works."
          : "The benchmark as agreed for this edition."),
        editsReady() ? null : h("p", { class: "su-edits-line muted small" }, EDITS_LINE()),
      ),
      HB.local ? safely("your material", () => materialSection()) : null,
      safely("checklist", () => checklistSection(b, plural)),
      safely("data you need to supply", () => needsSection(b, plural)),
      h(
        "section",
        { class: "section", "aria-labelledby": "su-h-agreed" },
        h("div", { class: "section__head" }, h("h3", { id: "su-h-agreed" }, "The benchmark as agreed")),
        h(
          "div",
          { class: "stack stack--loose" },
          safely("questions", () => questionsSection(b, plural)),
          safely("score sheet", () => scoreSheetSection(b)),
          safely(plural.toLowerCase(), () => unitsSection(b, plural)),
          safely("group labels", () => groupSection(b)),
          safely("cut-off date", () => cutoffSection(b)),
          safely("places to look", () => whereSection(b, plural)),
          safely("list of files provided", () => providedSection(b, plural)),
        ),
      ),
    );
    // In the local page the set-up is drafted in a chat: the corner chat moves into a column on the
    // left (HB.chat.inline, views/chat.js) and this tab's content sits on the right, as in the demo.
    if (HB.local && HB.chat && typeof HB.chat.inline === "function") {
      const col = h("aside", { class: "su-chatcol", "aria-label": "Set up with Claude" });
      root.append(h("div", { class: "su-split" }, col, wrap));
      try { HB.chat.inline(col); } catch (err) { console.error("HB.chat.inline failed:", err); }
    } else {
      root.append(wrap);
    }
    watchEdits(wrap);
  }

  /* ---- Styles: tokens only ------------------------------------------------------------------------------------------------------ */

  addStyles("setup", `
.su { gap: var(--s6); }
.su-split { display: grid; grid-template-columns: minmax(22rem, 30rem) minmax(0, 1fr); align-items: start; gap: var(--s6); }
.su-split > .su { min-width: 0; }
.su-chatcol { position: sticky; top: var(--s4); min-width: 0; }
@media (width < 56rem) { .su-split { grid-template-columns: minmax(0, 1fr); } .su-chatcol { position: static; } }
.su-narrow { max-width: 56rem; }
.su-edits-line { max-width: var(--measure); }
.su-sub__title { font-size: var(--fs-16); }
.su-sub__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s3); }
.su-drop { display: grid; justify-items: center; gap: var(--s1); padding: var(--s5) var(--s4); border: 1px dashed var(--rule-strong); border-radius: 0.75rem; background: var(--surface); color: var(--ink-2); text-align: center; cursor: pointer; transition: border-color 120ms, background-color 120ms; }
.su-drop:hover, .su-drop.is-over { border-color: var(--accent); background: var(--accent-soft); }
.su-drop:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.su-drop[data-busy="true"] { cursor: progress; }
.su-drop__lead { color: var(--ink); font-size: var(--fs-14); }
.su-drop__pick { color: var(--accent); font-weight: 600; text-decoration: underline; text-underline-offset: 2px; }
.su-drop__hint { font-size: var(--fs-12); }
.su-files { margin: 0; padding-left: 1.1rem; }
.su-list-title { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }

.su-progress { display: grid; gap: var(--s2); }
.su-progress__text { font-size: var(--fs-14); }
.su-segs { display: flex; gap: 3px; }
.su-seg { flex: 1 1 0; height: 0.375rem; border-radius: 2px; background: var(--rule); }
.su-seg[data-state="done"] { background: var(--accent); }
.su-seg[data-state="draft"] { background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--accent); }

.su-checks { margin: 0; padding: 0; list-style: none; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.su-check { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--s1) var(--s4); padding: var(--s3) var(--s4); border-top: 1px solid var(--rule); }
.su-check:first-child { border-top: 0; }
.su-check__text { display: grid; gap: 2px; min-width: 0; overflow-wrap: anywhere; }
.su-check__label { font-weight: 600; }
.su-check__hint { color: var(--ink-2); font-size: var(--fs-14); }
.su-check__chip { justify-self: end; }
@media (width < 30rem) {
  .su-check { grid-template-columns: minmax(0, 1fr); }
  .su-check__chip { justify-self: start; }
}

.su-empty { display: grid; gap: var(--s1); max-width: var(--measure); padding: var(--s4); border: 1px dashed var(--rule-strong); border-radius: var(--radius); }
.su-empty__title { font-weight: 600; font-size: var(--fs-14); }
.su-empty__text { color: var(--ink-2); font-size: var(--fs-14); }

.su-group { border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.su-group > summary { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--s1) var(--s4); padding: var(--s3) var(--s4); cursor: pointer; font-weight: 600; }
.su-group[open] > summary { border-bottom: 1px solid var(--rule); }
.su-group__count { font-size: var(--fs-14); font-weight: 400; }
.su-qs { margin: 0; padding: 0; list-style: none; }
.su-q { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); padding: var(--s4); border-top: 1px solid var(--rule); overflow-wrap: anywhere; }
.su-q:first-child { border-top: 0; }
.su-q__head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); }
.su-q__id { color: var(--ink-2); }
.su-q__short { flex: 1 1 12rem; min-width: 0; }
.su-q__wording { max-width: var(--measure); }
.su-q__from { color: var(--ink-2); font-size: var(--fs-12); }

.su-facts { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s1) var(--s4); margin: 0; font-size: var(--fs-14); }
.su-facts dt { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; letter-spacing: 0.04em; margin-top: var(--s2); }
.su-facts dd { margin: 0; min-width: 0; }
@media (width >= 40rem) {
  .su-facts { grid-template-columns: 9rem minmax(0, 1fr); }
  .su-facts dt { margin-top: 0; padding-top: 0.1rem; }
}

.su-edit { display: flex; align-items: flex-start; gap: var(--s2); }
.su-edit__body { flex: 1 1 auto; min-width: 0; }
.su-edit__button { flex: none; }

.su-levels { display: grid; gap: var(--s1); margin: 0; padding: 0; list-style: none; }
.su-level { display: grid; grid-template-columns: 3.5rem 5rem minmax(0, 1fr); gap: var(--s2); align-items: baseline; padding-block: 2px; border-bottom: 1px dotted var(--rule); }
.su-level:last-child { border-bottom: 0; }
.su-level__value { font-family: var(--mono); font-weight: 600; }
.su-level__points { color: var(--ink-2); font-variant-numeric: tabular-nums; }
.su-level__meaning { display: grid; gap: 2px; min-width: 0; }
.su-level__tag { color: var(--ink-2); font-size: var(--fs-12); }
@media (width < 30rem) { .su-level { grid-template-columns: 3rem 4.25rem minmax(0, 1fr); } }

.su-variants { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
.su-variants li { display: grid; gap: 2px; padding-left: var(--s3); border-left: 2px solid var(--rule-strong); }

.su-plain { max-width: var(--measure); }
.su-bands { display: grid; gap: var(--s1); margin: 0; padding: 0; list-style: none; font-size: var(--fs-14); }
.su-bands li { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s3); }
.su-tags { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s3); }
.su-tag { white-space: nowrap; }

.su-labels { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.su-labels li { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s4); padding: var(--s3) var(--s4); border-top: 1px solid var(--rule); font-size: var(--fs-14); }
.su-labels li:first-child { border-top: 0; }
.su-labels__roles { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s2); margin-left: auto; }
.su-role { padding: 0.0625rem 0.5rem; border: 1px solid var(--rule-strong); border-radius: 0.75rem; font-size: var(--fs-12); font-weight: 600; }
.su-role--default { border-color: var(--accent); background: var(--accent-soft); }
.su-role--off { border-color: var(--rule); color: var(--ink-2); font-weight: 400; }
.su-date { font-family: var(--serif); font-size: var(--fs-20); font-weight: 500; }

.su-plan { display: grid; gap: var(--s4); }
.su-plan__step { display: grid; gap: var(--s1); padding-left: var(--s3); border-left: 2px solid var(--rule-strong); }
.su-plan__for { font-weight: 600; }
.su-plan__kicker { color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
.su-plan__places { display: grid; gap: var(--s1); margin: 0; padding: 0; list-style: none; font-size: var(--fs-14); overflow-wrap: anywhere; }
`);

  HB.views.setup = { id: "setup", label: "Set-up", order: 10, render, visible: () => true };
})((window.HB = window.HB || {}));
