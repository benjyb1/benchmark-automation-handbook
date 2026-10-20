/* =============================================================================
   Handbook viewer: downloads.js, the spreadsheet downloads (HB.downloads).

   HB.downloads.sheets()      Promise of {sheets: [{id, name, columns, rows}],
                              fallback}. Reads downloads.json beside the page once
                              (cached, never rejects). When that file can't be
                              read, builds the Answers sheet from the bundle and
                              sets fallback true.
   HB.downloads.panel(ctx)    a section with the choice of format (Excel, CSV) and
                              of sheets, and a Save button. Saving goes through the
                              page's downloads capability; where this view can't
                              save files, each sheet gets "Copy table" instead.
   HB.downloads.csv(sheet)    one sheet as CSV text (UTF-8, with a byte order mark
                              so Excel reads accents right).

   File names: <slug>.xlsx (every chosen sheet in one workbook) and
   <slug>-<sheet id>.csv (one file per sheet, offered in turn).
   SheetJS is loaded only when someone saves an Excel file.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, plural, copyText } = HB;
  const SHEETJS = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
  const STATE_WORDS = { answered: "Answered", no_evidence: "No evidence", not_applicable: "Not applicable", not_run: "Not run" };
  const CHOICE_KEY = "downloads";

  let sheetsPromise = null;
  let toolPromise = null;
  let sheetjsPromise = null;

  /* ---- Data ------------------------------------------------------------------ */

  function validSheet(s) {
    return s && typeof s === "object" && typeof s.id === "string" && /^[a-z0-9-]+$/.test(s.id) &&
      typeof s.name === "string" && Array.isArray(s.columns) && Array.isArray(s.rows) &&
      s.rows.every((r) => Array.isArray(r));
  }

  /** The Answers sheet from the bundle alone, for when downloads.json can't be read. */
  function answersFromBundle() {
    const b = HB.data.bundle() || {};
    const p = b.project || {};
    const one = String(p.unit_word || "row");
    const word = one.charAt(0).toUpperCase() + one.slice(1);
    const labels = Array.isArray(p.labels) ? p.labels : [];
    const questions = {};
    (b.questions || []).forEach((q) => { if (q && q.id != null) questions[q.id] = q; });
    const rows = [];
    (b.units || []).forEach((u) => {
      const cells = (b.cells || {})[u.id] || {};
      Object.keys(cells).forEach((qid) => {
        const c = cells[qid] || {};
        const q = questions[qid] || {};
        rows.push([u.id, u.name, ...labels.map((lb) => (u.labels || {})[lb]), qid, q.short, q.section,
          STATE_WORDS[c.state] || c.state, c.value, c.points]);
      });
    });
    return {
      id: "answers",
      name: "Answers",
      columns: [`${word} id`, word, ...labels, "Question", "Short name", "Section", "State", "Value", "Points"],
      rows,
    };
  }

  function sheets() {
    if (!sheetsPromise) {
      sheetsPromise = fetch("downloads.json")
        .then((res) => (res.ok ? res.json() : null))
        .catch(() => null)
        .then((data) => {
          const list = data && Array.isArray(data.sheets) ? data.sheets.filter(validSheet) : [];
          if (list.length) return { sheets: list, fallback: false };
          return { sheets: [answersFromBundle()], fallback: true };
        });
    }
    return sheetsPromise;
  }

  /* ---- Files ------------------------------------------------------------------ */

  /** "person:p-..." (the Checked by column) as the person's name on the local page,
      else "A person": a published page never names anyone. */
  function person(v) {
    if (typeof v !== "string" || v.indexOf("person:") !== 0) return v;
    const name = HB.personName ? HB.personName(v.slice(7)) : null;
    return name || "A person";
  }

  function plainCell(v) {
    if (v == null) return "";
    if (typeof v === "boolean") return v ? "Yes" : "No";
    if (typeof v === "object") return JSON.stringify(v);
    return String(person(v));
  }

  /** A CSV cell. Text that a spreadsheet would read as a formula gets a leading
      apostrophe; numbers are left alone. */
  function csvCell(v) {
    let s = plainCell(v);
    if (typeof v !== "number" && /^[=+\-@\t\r]/.test(s) && !Number.isFinite(Number(s))) s = "'" + s;
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function csv(sheet) {
    const lines = [sheet.columns, ...sheet.rows].map((row) => row.map(csvCell).join(","));
    return "﻿" + lines.join("\r\n") + "\r\n";
  }

  /** Tab-separated, for Copy table (pastes into Excel or Sheets). */
  function tsv(sheet) {
    const cell = (v) => {
      let s = plainCell(v).replace(/\s+/g, " ").trim();
      if (typeof v !== "number" && /^[=+\-@]/.test(s) && !Number.isFinite(Number(s))) s = "'" + s;
      return s;
    };
    return [sheet.columns, ...sheet.rows].map((row) => row.map(cell).join("\t")).join("\n");
  }

  function loadSheetJS() {
    if (window.XLSX && window.XLSX.utils) return Promise.resolve(window.XLSX);
    if (!sheetjsPromise) {
      sheetjsPromise = new Promise((resolve, reject) => {
        const tag = document.createElement("script");
        tag.src = SHEETJS;
        tag.async = true;
        tag.onload = () => (window.XLSX && window.XLSX.utils ? resolve(window.XLSX) : reject(new Error("no XLSX")));
        tag.onerror = () => reject(new Error("load failed"));
        document.head.appendChild(tag);
      }).catch((err) => {
        sheetjsPromise = null;   // let a later click try again
        throw err;
      });
    }
    return sheetjsPromise;
  }

  /** Excel sheet names: at most 31 characters, none of : \ / ? * [ ], unique. */
  function sheetName(name, used) {
    const base = String(name || "Sheet").replace(/[:\\/?*[\]]/g, " ").trim().slice(0, 31) || "Sheet";
    let out = base;
    for (let n = 2; used.has(out.toLowerCase()); n += 1) out = base.slice(0, 31 - String(n).length - 1) + " " + n;
    used.add(out.toLowerCase());
    return out;
  }

  function workbook(XLSX, chosen) {
    const wb = XLSX.utils.book_new();
    const used = new Set();
    chosen.forEach((s) => {
      const rows = s.rows.map((r) => r.map((v) => (v == null ? null : typeof v === "object" ? plainCell(v) : person(v))));
      const ws = XLSX.utils.aoa_to_sheet([s.columns, ...rows]);
      XLSX.utils.book_append_sheet(wb, ws, sheetName(s.name, used));
    });
    return XLSX.write(wb, { bookType: "xlsx", type: "array", compression: true });
  }

  /** The page's downloads namespace, or null where this view can't save files. */
  function tool() {
    if (!toolPromise) {
      const c = window.claude;
      toolPromise = c && typeof c.use === "function"
        ? Promise.resolve().then(() => c.use("downloads")).catch(() => null).then((ns) => ns || null)
        : Promise.resolve(null);
    }
    return toolPromise;
  }

  function slug() {
    const b = HB.data.bundle() || {};
    const s = String((b.project || {}).slug || "handbook").toLowerCase().replace(/[^a-z0-9-]+/g, "-");
    return s.replace(/^-+|-+$/g, "") || "handbook";
  }

  const GONE = new Set(["unavailable", "not_granted", "capability_disabled", "capability_removed"]);

  /* ---- The panel ---------------------------------------------------------------- */

  function readChoice() {
    const saved = HB.store.get(CHOICE_KEY, null);
    return saved && typeof saved === "object" ? saved : {};
  }

  function copyList(list, intro) {
    return h("div", { class: "stack stack--tight" },
      intro ? h("p", { class: "small muted" }, intro) : null,
      h("ul", { class: "dl-copy" }, list.map((s) => {
        const button = h("button", { type: "button", class: "btn btn--small" }, "Copy table",
          h("span", { class: "visually-hidden" }, ": " + s.name));
        button.addEventListener("click", () => copyText(tsv(s), button));
        return h("li", { class: "dl-copy__item" },
          h("span", null, h("strong", null, s.name), " ",
            h("span", { class: "muted" }, plural(s.rows.length, "row", "rows"))),
          button);
      })));
  }

  function panel() {
    const root = h("section", { class: "section dl", "aria-labelledby": "dl-title" },
      h("div", { class: "section__head" }, h("h3", { id: "dl-title" }, "Download the results")),
      h("p", { class: "muted small" }, "Loading the download tables..."));
    sheets().then((data) => fill(root, data));
    return root;
  }

  function fill(root, data) {
    const list = data.sheets;
    const choice = readChoice();
    const head = root.firstChild;
    root.replaceChildren(head);
    if (data.fallback) {
      root.appendChild(HB.notice("warning", "The full download tables could not be loaded in this view, so only the " +
        "Answers sheet is offered, built from the page itself. It has no evidence, documents or rules."));
    }

    const format = choice.format === "csv" ? "csv" : "xlsx";
    const radio = (value, label, hint) => h("label", { class: "dl-choice" },
      h("input", { type: "radio", name: "dl-format", id: "dl-format-" + value, value, checked: format === value }),
      h("span", null, h("strong", null, label), " ", h("span", { class: "muted" }, hint)));
    const formats = h("fieldset", { class: "dl-group" }, h("legend", null, "Format"),
      radio("xlsx", "Excel", "one workbook, a sheet for each table"),
      radio("csv", "CSV", "one file for each table"));

    const picked = Array.isArray(choice.sheets) ? choice.sheets : null;
    const boxes = list.map((s) => h("label", { class: "dl-choice" },
      h("input", { type: "checkbox", id: "dl-sheet-" + s.id, value: s.id, checked: picked ? picked.includes(s.id) : true }),
      h("span", null, h("strong", null, s.name), " ",
        h("span", { class: "muted" }, plural(s.rows.length, "row", "rows")))));
    const which = h("fieldset", { class: "dl-group" }, h("legend", null, "Tables"), boxes);

    const save = h("button", { type: "button", class: "btn btn--primary" }, "Save");
    const status = h("p", { class: "small dl-status", role: "status", "aria-live": "polite" });
    const form = h("div", { class: "dl-form" }, h("div", { class: "dl-groups" }, formats, which),
      h("div", { class: "cluster" }, save, status));
    const copyBox = h("div", { hidden: true });
    root.append(form, copyBox);

    const chosen = () => list.filter((s) => {
      const box = root.querySelector("#dl-sheet-" + s.id);
      return box && box.checked;
    });
    const chosenFormat = () => ((root.querySelector('input[name="dl-format"]:checked') || {}).value === "csv" ? "csv" : "xlsx");
    const remember = () => HB.store.set(CHOICE_KEY, { format: chosenFormat(), sheets: chosen().map((s) => s.id) });
    const sync = () => {
      const n = chosen().length;
      save.disabled = n === 0;
      save.textContent = n === 0 ? "Choose a table to save"
        : chosenFormat() === "xlsx" ? "Save Excel file" : `Save ${plural(n, "CSV file", "CSV files")}`;
    };
    form.addEventListener("change", () => { remember(); sync(); status.textContent = ""; });
    sync();

    function copyMode(reason) {
      form.hidden = true;
      copyBox.hidden = false;
      copyBox.replaceChildren(copyList(list, reason));
    }

    // Where the page can't save files, the choices give way to Copy table.
    tool().then((t) => { if (!t) copyMode("Saving files isn't available in this view. Copy a table and paste it into a spreadsheet."); });

    save.addEventListener("click", async () => {
      const sel = chosen();
      if (!sel.length) return;
      const t = await tool();
      if (!t) return copyMode("Saving files isn't available in this view. Copy a table and paste it into a spreadsheet.");
      save.disabled = true;
      status.textContent = "";
      try {
        if (chosenFormat() === "xlsx") {
          status.textContent = "Preparing the workbook...";
          let XLSX;
          try {
            XLSX = await loadSheetJS();
          } catch (err) {
            status.textContent = "The Excel maker could not be loaded. Choose CSV, or try again.";
            return;
          }
          const data = workbook(XLSX, sel);
          status.textContent = "";
          await t.save({ filename: slug() + ".xlsx", data });
          status.textContent = "Saved.";
        } else {
          let saved = 0;
          for (const s of sel) {
            status.textContent = sel.length > 1 ? `Saving ${s.name} (${saved + 1} of ${sel.length})...` : "";
            await t.save({ filename: `${slug()}-${s.id}.csv`, data: csv(s) });
            saved += 1;
          }
          status.textContent = saved > 1 ? `Saved ${saved} files.` : "Saved.";
        }
      } catch (err) {
        const code = err && typeof err === "object" ? err.code : null;
        if (code === "declined") status.textContent = "";
        else if (code === "rate_limited") status.textContent = "Another save is waiting for an answer. Try again in a moment.";
        else if (code === "too_large") status.textContent = "That file is too large to save here. Choose fewer tables, or CSV.";
        else if (code === "rejected_extension" || code === "extension_not_enabled") {
          status.textContent = "That format can't be saved in this view. Try the other format, or copy the tables.";
        } else if (GONE.has(code)) copyMode("Saving files stopped working in this view. Copy a table and paste it into a spreadsheet.");
        else status.textContent = "The file could not be saved. Try again, or copy the tables instead.";
      } finally {
        sync();
      }
    });
  }

  HB.addStyles("downloads", `
    .dl-form { display: grid; gap: var(--s4); }
    .dl-groups { display: flex; flex-wrap: wrap; gap: var(--s4) var(--s6); }
    .dl-group { display: grid; gap: var(--s2); min-width: 0; margin: 0; padding: 0; border: 0; }
    .dl-group > legend { margin-bottom: var(--s2); padding: 0; font-size: var(--fs-14); font-weight: 600; }
    .dl-choice { display: flex; align-items: baseline; gap: var(--s2); font-size: var(--fs-14); cursor: pointer; }
    .dl-choice input { flex: none; width: 1rem; height: 1rem; margin: 0; accent-color: var(--accent); transform: translateY(0.15rem); }
    .dl-status { color: var(--ink-2); }
    .dl-status:empty { display: none; }
    .dl-copy { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
    .dl-copy__item { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--s2) var(--s4);
      padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
  `);

  HB.downloads = { sheets, panel, csv };
})((window.HB = window.HB || {}));
