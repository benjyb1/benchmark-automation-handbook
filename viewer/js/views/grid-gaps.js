/* =============================================================================
   Handbook viewer: views/grid-gaps.js, what the Grid says about each row's
   documents (these used to be the Documents tab). Sets HB.gridGaps; grid.js and
   grid-phone.js call it in all three of the Grid's layouts.

   HB.gridGaps.marker(unit)   a small quiet button for the end of a row's name,
                              or null when Claude got everything for the row. It
                              reads "2 missing" (what Claude expected but could
                              not find, plus the documents it found but could
                              not keep a copy of) and opens a short list in a
                              dialog: "Couldn't find: ..." and "Couldn't get a
                              copy of: ...". Each line has a Send button that
                              opens HB.supply.send (views/deck.js), so the owner
                              uploads the file or pastes a link; it files a
                              `supplied` doc with pattern "not_found" or
                              "not_kept" (the step brief's names for them).
                              Without the store the lines have no button.
   HB.gridGaps.note(ctx, m, unit)
                              "3 documents found" or "No documents found yet"
                              for a row that has no scored answer yet while
                              documents are still being found (a run is on, or
                              nothing in the project is scored yet); else null.
                              The Grid shows it instead of the row's empty cells.
   Data: HB.data.gaps(row) and HB.data.foundCount(row) (data.js).
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt } = HB;

  const STYLE = `
.hbg-ov__who { display: flex; align-items: baseline; gap: var(--s2); min-width: 0; }
.hbg-ov__who > .hbg-ov__nm { flex: 0 1 auto; min-width: 0; }
.hbg-gap { flex: none; padding: 0 var(--s1); border: 0; border-bottom: 1px dotted var(--ink-2); border-radius: 0; background: none; color: var(--ink-2); font: inherit; font-size: var(--fs-12); line-height: 1.3; cursor: pointer; white-space: nowrap; }
.hbg-gap:hover { border-bottom-style: solid; color: var(--ink); }
.hbg-gap:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.hbg-table .hbg-rh .hbg-gap, .hbg-li__name .hbg-gap { margin-left: var(--s2); }
.hbg-docnote { color: var(--ink-2); font-size: var(--fs-14); }
.hbg-ov__r > .hbg-docnote { grid-column: 2 / -1; }
.hbg-table td.hbg-docnote { padding: var(--s1) var(--s3); text-align: left; }
.hbg-gaps h3 { margin: 0; font-family: var(--sans); font-size: var(--fs-14); font-weight: 650; }
.hbg-gaps ul { display: grid; gap: var(--s3); margin: var(--s2) 0 0; padding: 0; list-style: none; }
.hbg-gaps li { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--s1) var(--s3); font-size: var(--fs-14); }
.hbg-gaps li > span { flex: 1 1 14rem; min-width: 0; overflow-wrap: anywhere; }
.hbg-gaps .muted { display: block; font-size: var(--fs-12); }
`;

  const SENT = new Set();   // "row~pattern~what" already sent from here
  let dialog = null;

  const sentKey = (row, pattern, what) => [row, pattern, what].join("~");
  const count = (n, one, many) => fmtInt(n) + " " + (n === 1 ? one : many);

  function dialogEl() {
    if (dialog && dialog.isConnected) return dialog;
    dialog = h("dialog", { class: "hb-dialog", "aria-labelledby": "hbg-gaps-title" });
    dialog.addEventListener("click", (ev) => { if (ev.target === dialog) dialog.close(); });
    (document.querySelector(".hb") || document.body).append(dialog);
    return dialog;
  }

  // One line of the list: the words, and a Send button when the page can take files.
  function line(unit, g, words) {
    const ready = HB.supply && typeof HB.supply.ready === "function" && HB.supply.ready();
    const key = sentKey(unit.id, g.pattern, g.what);
    // "Add file or link", not "Send": it opens a box to give the document, it does not send anything away.
    const button = !ready ? null : h("button", { type: "button", class: "sb-sendbtn" }, "Add file or link");
    if (button) {
      const done = () => { button.textContent = "Added"; button.classList.add("is-sent"); button.disabled = true; };
      if (SENT.has(key)) done();
      else {
        button.addEventListener("click", async () => {
          const ok = await HB.supply.send({ row: unit.id, name: unit.name || unit.id, pattern: g.pattern, what: g.what,
            replaces: g.replaces || [], kicker: "Documents", intro: g.intro });
          if (ok) { SENT.add(key); done(); }
        });
      }
    }
    return h("li", null, h("span", null, words), button);
  }

  /** "question 7" or "questions 7, 8 and 9". */
  function qWords(qs) {
    const list = (qs || []).map(String);
    if (!list.length) return "some questions";
    if (list.length === 1) return "question " + list[0];
    return "questions " + list.slice(0, -1).join(", ") + " and " + list[list.length - 1];
  }

  function open(unit) {
    const gaps = HB.data.gaps(unit.id);
    const ready = HB.supply && typeof HB.supply.ready === "function" && HB.supply.ready();
    const name = String(unit.name || unit.id);
    const body = [];
    if (gaps.missing.length) {
      body.push(h("section", { class: "hbg-gaps" }, h("h3", null, "Couldn't find:"),
        h("ul", null, gaps.missing.map((m) => m.by === "scorer"
          ? line(unit, { pattern: "not_found", what: m.what,
            intro: "Claude needed this to answer " + qWords(m.questions) + " and it isn't among the documents. If you have it, send the file or a link: " +
              HB.when("those questions are then scored again.", "Claude scores those questions again the next time it works on this project.") },
          [h("strong", null, m.what), h("span", { class: "muted" }, "Claude needed it to answer " + qWords(m.questions) + ".")])
          : line(unit, { pattern: "not_found", what: m.what,
            intro: "Claude expected this but could not find it. If you have it, send the file or a link." },
          [h("strong", null, m.what), h("span", { class: "muted" }, m.where_looked ? "Looked: " + m.where_looked : "Where Claude looked is not recorded")])))));
    }
    if (gaps.lost.length) {
      body.push(h("section", { class: "hbg-gaps" }, h("h3", null, "Couldn't get a copy of:"),
        h("ul", null, gaps.lost.map((d) => line(unit, { pattern: "not_kept", what: d.title, replaces: [d.id],
          intro: "Claude found this but could not keep a copy. If you can save it, send the file, or a link to a copy." },
        [d.url ? h("a", { href: d.url, target: "_blank", rel: "noopener" }, d.title) : h("strong", null, d.title),
          d.why ? h("span", { class: "muted" }, d.why) : null])))));
    }
    if (!ready) {
      body.push(h("p", { class: "muted small" }, HB.when(HB.cantSave(), "This view can't take files. Open the project's own page to send them.")));
    }
    const d = dialogEl();
    d.replaceChildren(h("div", { class: "hb-dialog__form" },
      h("div", { class: "hb-dialog__head" },
        h("p", { class: "eyebrow" }, "Documents"),
        h("h2", { id: "hbg-gaps-title" }, name),
        h("p", { class: "hb-hint" }, "Sending what you have helps Claude score " + name + " properly.")),
      h("div", { class: "hb-dialog__body" }, body),
      h("div", { class: "hb-dialog__status" }),
      h("div", { class: "hb-dialog__foot" }, h("button", { type: "button", class: "btn btn--quiet", onclick: () => d.close() }, "Close"))));
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
  }

  function marker(unit) {
    const gaps = HB.data.gaps(unit.id);
    const n = gaps.missing.length + gaps.lost.length;
    if (!n) return null;
    const say = count(n, "document", "documents") + " Claude couldn't find or get a copy of";
    return h("button", {
      type: "button", class: "hbg-gap", title: say, "aria-haspopup": "dialog",
      "aria-label": (unit.name || unit.id) + ": " + say + ". Open the list.",
      onclick: () => open(unit),
    }, fmtInt(n) + " missing");
  }

  // Whether the row has at least one answer.
  const scoredRow = (ctx, id) => Object.values((ctx.bundle.cells || {})[id] || {}).some((c) => ctx.data.isScored(c));

  function note(ctx, m, unit) {
    if (m.docsPending === undefined) {
      const rows = Array.isArray(ctx.bundle.units) ? ctx.bundle.units : [];
      m.docsPending = Boolean(m.live) || !rows.some((u) => u && scoredRow(ctx, u.id));
    }
    if (!m.docsPending || scoredRow(ctx, unit.id)) return null;
    const n = ctx.data.foundCount(unit.id);
    return n ? count(n, "document", "documents") + " found" : "No documents found yet";
  }

  HB.addStyles("grid-gaps", STYLE);
  HB.gridGaps = { marker, note };
})((window.HB = window.HB || {}));
