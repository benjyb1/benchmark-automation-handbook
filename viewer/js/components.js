/* =============================================================================
   Handbook viewer: components.js, the shared pieces every tab uses.
   Copied from frontend/src/js/components.js (AYB renamed HB; step, stage and
   verdict chips, the ask box and receipts left out). Uses util.js; attaches to window.HB.
   The markup follows the component classes listed at the top of styles.css.

   icon(name)                     svg.status-icon: tick, exclamation, cross, dash or
                                  person. Decorative: the word beside it carries the
                                  meaning.
   statusChip(kind, word, opts)   span.chip with an icon and a word, never colour
                                  alone. kind: good | warning | serious | critical |
                                  neutral; anything else reads as neutral. word
                                  defaults to the kind's own ("Good" ... "Unknown");
                                  opts.icon picks another icon; opts.large is a
                                  larger size.
   notice(tone, content)          div.notice: the tone's icon beside the content;
                                  tone as statusChip's.
   countedTable(opts)             section.table-block: "Showing X of Y ...", "Copy
                                  table" (tab-separated, pastes into Excel or Sheets)
                                  and sortable headers. Options below.
   tableCells(columns, rows)      a table as rows of plain-text cells, header row
                                  first, ready for a spreadsheet. Copy table uses it
                                  on the rows in their current order, and so does a
                                  tools function (Save as CSV) through cells().
   panel(title, content, opts)    the drawer's content in flow, inside whatever
                                  container the caller puts it in: section.drawer
                                  .drawer--inflow. No focus handling and no Escape;
                                  those belong to the drawer. opts.kicker (a short
                                  line above the title), opts.onClose (adds a Close
                                  button that calls it). A content element marked
                                  data-drawer-tools goes in the head, under the title.
   drawer.open(title, content, opts), drawer.close(), drawer.isOpen()
                                  the same content in the page's dialog#drawer. From
                                  64rem wide it is docked: opened with show(), not
                                  modal, fixed to the right at full height, and the
                                  .hb root gets .has-dock so the page makes room for
                                  it. Narrower, showModal(): a right-hand panel, a
                                  bottom sheet on phones. Escape, Close or a click on
                                  the backdrop closes it; focus moves to Close (or to
                                  an [autofocus] element in the content) and goes back
                                  to where it was. opts.kicker; opts.onClose runs once
                                  when the drawer closes. Opening while open swaps
                                  the content (focus then moves only to an [autofocus]
                                  element). drawer.isDocked(); drawer.returnTo(el)
                                  sets where focus goes back to on close.
   help(title, paragraphs)        details.help; the summary always says "How to read
                                  this", and a title, when given, opens the body.
   link(href, text)               a link that opens in a new tab; only web addresses
                                  (or relative ones) become links, anything else
                                  shows as plain text.
   addStyles(id, css)             a view's own CSS, added once as <style data-hb-style=id>
                                  (a second call with the same id does nothing). Use the
                                  colour tokens from styles.css, never literal colours.

   Content (drawer, panel, help, notice): a Node, a string (one paragraph), or an
   array of them; an array inside the array is one paragraph of mixed parts.
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, svg, uid, isNum, fmtInt, fmtNum, copyText, NOT_RECORDED } = HB;

  function has(table, key) {
    return typeof key === "string" && Object.prototype.hasOwnProperty.call(table, key);
  }

  function isEmpty(value) {
    return value == null || value === "" || (typeof value === "number" && Number.isNaN(value));
  }

  /* ---- Icons and chips ---------------------------------------------------- */

  const GLYPHS = {
    tick: () => [svg("path", { class: "status-icon__glyph", d: "M4.6 8.3l2.2 2.2 4.6-4.8" })],
    exclamation: () => [
      svg("path", { class: "status-icon__glyph", d: "M8 4.3v4.5" }),
      svg("circle", { class: "status-icon__dot", cx: 8, cy: 11.5, r: 1.1 }),
    ],
    cross: () => [svg("path", { class: "status-icon__glyph", d: "M5.5 5.5l5 5M10.5 5.5l-5 5" })],
    dash: () => [svg("path", { class: "status-icon__glyph", d: "M4.8 8h6.4" })],
    person: () => [
      svg("circle", { class: "status-icon__dot", cx: 8, cy: 6.1, r: 2.1 }),
      svg("path", { class: "status-icon__dot", d: "M4.3 12.3a3.7 3.7 0 0 1 7.4 0z" }),
    ],
  };

  function icon(name) {
    const glyph = has(GLYPHS, name) ? GLYPHS[name] : GLYPHS.dash;
    return svg(
      "svg",
      { class: "status-icon", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" },
      svg("circle", { class: "status-icon__disc", cx: 8, cy: 8, r: 8 }),
      glyph(),
    );
  }

  const TONES = {
    good: { icon: "tick", word: "Good" },
    warning: { icon: "exclamation", word: "Warning" },
    serious: { icon: "exclamation", word: "Serious" },
    critical: { icon: "cross", word: "Critical" },
    neutral: { icon: "dash", word: "Unknown" },
  };

  function statusChip(kind, word, opts) {
    const o = opts || {};
    const tone = has(TONES, kind) ? kind : "neutral";
    const text = isEmpty(word) ? TONES[tone].word : String(word);
    return h(
      "span",
      { class: ["chip", o.large && "chip--lg"], "data-tone": tone },
      icon(o.icon || TONES[tone].icon),
      h("span", { class: "chip__word" }, text),
    );
  }

  const STEP_STATES = {
    done: { icon: "tick", word: "Done" },
    needs_you: { icon: "person", word: "Needs you" },
    partial: { icon: "exclamation", word: "Partly done" },
    not_run: { icon: "dash", word: "Not run" },
    stopped: { icon: "cross", word: "Stopped" },
  };
  /* ---- Counted table -------------------------------------------------------
     countedTable({columns, rows, expected, shownLabel, note, copyName,
                   freezeFirst, tools})
     columns     [{label, key?, value?(row), format?(value, row), empty?, render?(row),
                   text?(row), num?, mono?, wide?, rowHeader?, sortable?}]
                 value(row), or row[key], is what the column sorts by. render(row)
                 draws the cell (a Node or text). Otherwise the cell shows
                 format(value, row) for a value that is there and empty ("Not
                 recorded" unless given) for one that is missing; a number with no
                 format reads "1,616" in a num column and "2020" elsewhere, so a
                 year column stays ungrouped as long as it isn't num. Copy table
                 takes text(row) when given, else what the cell shows (hidden
                 helper text left out), with plain numbers unformatted and missing
                 values as empty cells. num: figures, right-aligned, digits
                 grouped; mono: codes, ids, file names; wide: long text such as
                 notes or quotes (at least 18rem); rowHeader: the cell names its
                 row (th scope=row); sortable: false turns sorting off.
     rows        the rows, in the order they first appear
     expected    how many rows there should be: a number ("Showing 99 of 100
                 countries.") or {n, label} when the rows count something else
                 ("Showing 239 documents for 100 countries."). Left out, the line
                 says the expected number is not recorded.
     shownLabel  the rows' noun: "countries", or ["country", "countries"] so that
                 one reads in the singular
     note        a sentence after the count, e.g. why some are missing
     copyName    the table's name, e.g. "Sources": names the scroll region and the
                 Copy table button for screen readers
     freezeFirst keeps the first column in view while the table scrolls sideways
     tools       more controls for the tools row, after Copy table: a Node or an
                 array of them, or a function that receives cells() and returns
                 them. cells() gives the rows as tableCells does, in the order
                 shown at that moment, the same as Copy table takes (for Save as
                 CSV). */

  const collator = new Intl.Collator("en-GB", { numeric: true, sensitivity: "base" });

  function noun(n, label) {
    if (Array.isArray(label)) return Math.round(n) === 1 ? label[0] : label[1];
    return isEmpty(label) ? "rows" : String(label);
  }

  function countLine(shown, expected, shownLabel, note) {
    const line = ["Showing ", h("strong", null, fmtInt(shown))];
    if (isNum(expected)) {
      line.push(" of ", h("strong", null, fmtInt(expected)), " ", noun(expected, shownLabel), ".");
    } else if (expected && isNum(expected.n)) {
      line.push(" ", noun(shown, shownLabel), " for ", h("strong", null, fmtInt(expected.n)), " ",
        noun(expected.n, expected.label), ".");
    } else {
      line.push(" ", noun(shown, shownLabel), ". The expected number is not recorded.");
    }
    if (!isEmpty(note)) line.push(" ", String(note));
    return line;
  }

  function columnValue(col, row) {
    if (typeof col.value === "function") return col.value(row);
    return col.key == null ? undefined : row[col.key];
  }

  function shownText(col, value, row) {
    if (isEmpty(value)) return isEmpty(col.empty) ? NOT_RECORDED : String(col.empty);
    if (typeof col.format === "function") return col.format(value, row);
    if (typeof value === "number") {
      if (!col.num) return fmtNum(value, 2, false);   // a year reads 2020, not 2,020
      return Number.isInteger(value) ? fmtInt(value) : fmtNum(value);
    }
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return String(value);
  }

  function visibleText(content) {
    if (content == null || typeof content === "boolean") return "";
    if (!(content instanceof Node)) return String(content);
    const copy = content.cloneNode(true);
    if (copy.querySelectorAll) copy.querySelectorAll(".visually-hidden").forEach((el) => el.remove());
    return copy.textContent;
  }

  // What a spreadsheet gets from one cell.
  function cellText(col, row) {
    if (typeof col.text === "function") return col.text(row);
    if (typeof col.render === "function") return visibleText(col.render(row));
    const value = columnValue(col, row);
    if (isEmpty(value)) return "";
    if (typeof col.format === "function") return col.format(value, row);
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return String(value);   // numbers unformatted, so every spreadsheet reads them
  }

  // A number, a percentage or a lone sign pastes as itself: "-2.5%", "+4%", "-1,616", "-".
  const SHEET_NUMBER = /^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?$/;

  // One spreadsheet cell: on one line, and never read as a formula. A leading
  // = + - or @ would make Excel or Sheets calculate the text ("- Eat five a day"
  // becomes an error); an apostrophe in front keeps it text.
  function sheetCell(text) {
    let s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    const harmless = /^[+-]$/.test(s) || SHEET_NUMBER.test(s) || Number.isFinite(Number(s));
    if (/^[=+\-@]/.test(s) && !harmless) s = "'" + s;
    return s;
  }

  function tableCells(columns, rows) {
    return [
      columns.map((col) => sheetCell(col.label)),
      ...rows.map((row) => columns.map((col) => sheetCell(cellText(col, row)))),
    ];
  }

  function compareKeys(a, b, sign) {
    const aEmpty = isEmpty(a);
    const bEmpty = isEmpty(b);
    if (aEmpty || bEmpty) return aEmpty === bEmpty ? 0 : aEmpty ? 1 : -1;   // blanks last, both ways
    if (typeof a === "number" && typeof b === "number") return (a - b) * sign;
    return collator.compare(String(a), String(b)) * sign;
  }

  function countedTable(opts) {
    const o = opts || {};
    const columns = o.columns || [];
    const rows = o.rows || [];
    const countId = uid("table-count");

    const entries = rows.map((row) => {
      const cells = columns.map((col) => {
        const content = typeof col.render === "function" ? col.render(row) : shownText(col, columnValue(col, row), row);
        return h(
          col.rowHeader ? "th" : "td",
          { class: [col.num && "num", col.mono && "mono", col.wide && "table__wide"], scope: col.rowHeader ? "row" : null },
          content,
        );
      });
      const keys = columns.map((col, i) => {
        if (typeof col.value === "function" || col.key != null) return columnValue(col, row);
        return visibleText(cells[i]).trim();
      });
      return { row, cells, keys, tr: h("tr", null, cells) };
    });
    let order = entries;

    const tbody = h("tbody", null, entries.map((entry) => entry.tr));
    if (!entries.length) {
      tbody.append(h("tr", null, h("td", { colspan: Math.max(columns.length, 1), class: "muted" }, "Nothing to show.")));
    }

    let sortedBy = -1;
    let direction = "";
    const heads = columns.map((col, i) => {
      const sortable = col.sortable !== false && entries.length > 1;
      const label = sortable
        ? h("button", { class: "table__sort", type: "button", onclick: () => sortBy(i) }, col.label)
        : col.label;
      return h("th", { scope: "col", class: [col.num && "num", col.wide && "table__wide"] }, label);
    });

    function sortBy(i) {
      direction = sortedBy === i && direction === "ascending" ? "descending" : "ascending";
      sortedBy = i;
      const sign = direction === "ascending" ? 1 : -1;
      order = entries.slice().sort((a, b) => compareKeys(a.keys[i], b.keys[i], sign));
      tbody.append(...order.map((entry) => entry.tr));
      heads.forEach((th, j) => {
        if (j === i) th.setAttribute("aria-sort", direction);
        else th.removeAttribute("aria-sort");
      });
    }

    // Copy table and any tools function (Save as CSV) read the same cells, in the
    // order the table shows at that moment.
    const cells = () => tableCells(columns, order.map((entry) => entry.row));
    const copyButton = h(
      "button",
      { class: "btn btn--small", type: "button" },
      "Copy table",
      isEmpty(o.copyName) ? null : h("span", { class: "visually-hidden" }, ": " + o.copyName),
    );
    const tools = h(
      "div",
      { class: "table-block__tools" },
      copyButton,
      typeof o.tools === "function" ? o.tools(cells) : o.tools,
    );
    copyButton.addEventListener("click", () => {
      const tsv = cells().map((line) => line.join("\t")).join("\n");
      copyText(tsv, copyButton, { after: tools });   // a manual-copy box goes under the tools, full width
    });

    const named = !isEmpty(o.copyName);
    const table = h(
      "table",
      { class: ["table", o.freezeFirst && "table--freeze-first"], "aria-describedby": named ? countId : null },
      h("thead", null, h("tr", null, heads)),
      tbody,
    );
    return h(
      "section",
      { class: "table-block", "data-name": named ? String(o.copyName) : null },
      h(
        "div",
        { class: "table-block__head" },
        h("p", { class: "table-block__count", id: countId }, countLine(rows.length, o.expected, o.shownLabel, o.note)),
        tools,
      ),
      h(
        "div",
        {
          class: "table-scroll",
          role: "region",
          tabindex: 0,
          "aria-label": named ? String(o.copyName) : null,
          "aria-labelledby": named ? null : countId,
        },
        table,
      ),
    );
  }

  /* ---- Panel and drawer -----------------------------------------------------
     One panel, two ways to show it. fillPanel() writes a title and content into a
     panel's parts. panel() makes a new in-flow panel; the drawer fills the page's
     dialog#drawer, which has the same parts (index.html, or panelParts() when a
     page lacks it), so both show the same content.

     A content element marked data-drawer-tools (a row of controls, such as the
     cell panel's Previous and Next) goes in the head, under the title, so it stays
     in place while the body scrolls.

     The drawer has three shapes, picked when it opens (and again when the window
     crosses a width while it is open):
       from 64rem   docked: dialog.show(), not modal, fixed to the right edge at
                    full height (width --dock-w); the page's .hb root gets
                    .has-dock, which pads the page so nothing sits under it. The
                    page stays usable beside it; Escape anywhere closes it.
       40-64rem     modal (showModal()): a right-hand panel over a backdrop
       under 40rem  modal: a bottom sheet */

  const DOCK_QUERY = "(min-width: 64rem)";

  function blocks(content) {
    if (content == null || typeof content === "boolean") return [];
    if (Array.isArray(content)) {
      return content.flatMap((part) => (Array.isArray(part) ? [h("p", null, part)] : blocks(part)));
    }
    if (content instanceof Node) return [content];
    return [h("p", null, String(content))];
  }

  function panelParts(withClose) {
    const kicker = h("p", { class: "drawer__kicker", hidden: true });
    const title = h("h2", { class: "drawer__title", id: uid("panel-title") });
    const close = withClose ? h("button", { class: "btn btn--quiet drawer__close", type: "button" }, "Close") : null;
    const head = h("div", { class: "drawer__head" }, h("div", { class: "drawer__heading" }, kicker, title), close);
    const tools = h("div", { class: "drawer__tools", hidden: true });
    const body = h("div", { class: "drawer__body" });
    return { head, kicker, title, close, tools, body };
  }

  function fillPanel(parts, title, content, opts) {
    const kicker = opts && opts.kicker;
    parts.kicker.textContent = isEmpty(kicker) ? "" : String(kicker);
    parts.kicker.hidden = isEmpty(kicker);
    parts.title.textContent = title == null ? "" : String(title);
    const all = blocks(content);
    const tools = [];
    for (const node of all) {
      if (!(node instanceof Element)) continue;
      const found = node.matches("[data-drawer-tools]") ? [node] : Array.from(node.querySelectorAll("[data-drawer-tools]"));
      for (const t of found) {
        if (t !== node) t.remove();
        tools.push(t);
      }
    }
    parts.tools.replaceChildren(...tools);
    parts.tools.hidden = tools.length === 0;
    parts.body.replaceChildren(...all.filter((n) => !tools.includes(n)));
  }

  function panel(title, content, opts) {
    const o = opts || {};
    const closable = typeof o.onClose === "function";
    const parts = panelParts(closable);
    fillPanel(parts, title, content, o);
    if (closable) parts.close.addEventListener("click", () => o.onClose());
    return h("section", { class: "drawer drawer--inflow", "aria-labelledby": parts.title.id }, parts.head, parts.tools, parts.body);
  }

  const drawer = (function () {
    let dialog = null;
    let parts = null;
    let session = null;   // {opener, onClose} while the drawer is open
    let pressedBackdrop = false;
    let docked = false;
    let listening = false;
    const media = typeof window.matchMedia === "function" ? window.matchMedia(DOCK_QUERY) : null;

    function connect() {
      if (dialog && dialog.isConnected) return;
      dialog = document.getElementById("drawer");
      if (!dialog) {
        const built = panelParts(true);
        built.title.id = "drawer-title";
        built.close.setAttribute("data-drawer-close", "");
        dialog = h("dialog", { class: "drawer", id: "drawer", "aria-labelledby": "drawer-title" }, built.head, built.tools, built.body);
        (document.querySelector(".hb") || document.querySelector(".app") || document.body).append(dialog);
      }
      let tools = dialog.querySelector(".drawer__tools");
      if (!tools) {
        tools = h("div", { class: "drawer__tools", hidden: true });
        const head = dialog.querySelector(".drawer__head");
        if (head) head.after(tools);
        else dialog.prepend(tools);
      }
      parts = {
        kicker: dialog.querySelector(".drawer__kicker"),
        title: dialog.querySelector(".drawer__title"),
        tools,
        body: dialog.querySelector(".drawer__body"),
      };
      // A click that lands on the dialog itself landed on the backdrop: the panel's
      // parts fill the whole box. It must also have started there, so selecting
      // text in the drawer and letting go outside doesn't close it. A docked
      // drawer has no backdrop.
      dialog.addEventListener("pointerdown", (event) => {
        pressedBackdrop = !docked && event.target === dialog;
      });
      dialog.addEventListener("click", (event) => {
        const onBackdrop = !docked && event.target === dialog && pressedBackdrop;
        pressedBackdrop = false;
        const closer = event.target instanceof Element && event.target.closest("[data-drawer-close]");
        if (onBackdrop || closer) close();
      });
      dialog.addEventListener("close", finish);   // Escape, or any other close
      if (listening) return;
      listening = true;
      // A docked drawer is not modal, so the browser leaves Escape to the page.
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || event.defaultPrevented || !docked || !dialog || !dialog.open) return;
        event.preventDefault();
        close();
      });
      if (media && typeof media.addEventListener === "function") media.addEventListener("change", reshape);
      else if (media && typeof media.addListener === "function") media.addListener(reshape);
    }

    function setDocked(on) {
      docked = on;
      dialog.classList.toggle("drawer--docked", on);
      const root = dialog.closest(".hb") || document.querySelector(".hb");
      if (root) root.classList.toggle("has-dock", on);
    }

    function show() {
      const dock = Boolean(media && media.matches);
      setDocked(dock);
      if (dock) dialog.show();
      else dialog.showModal();
    }

    // The window crossed 64rem while the drawer is open: show it again in the
    // other shape, keeping the content, the session and the focus.
    function reshape() {
      if (!dialog || !dialog.open || docked === Boolean(media && media.matches)) return;
      const had = dialog.contains(document.activeElement) ? document.activeElement : null;
      const top = parts.body.scrollTop;
      dialog.close();   // its close event finds the dialog open again and does nothing
      show();
      parts.body.scrollTop = top;
      if (had && had.isConnected) had.focus({ preventScroll: true });
    }

    // Tidies up after a close: runs at once from close(), and again (doing
    // nothing) when the dialog's own close event arrives.
    function finish() {
      if (!session || !dialog || dialog.open) return;
      const { opener, onClose } = session;
      session = null;
      setDocked(false);
      parts.body.replaceChildren();
      parts.tools.replaceChildren();
      parts.tools.hidden = true;
      const main = document.getElementById("main");
      if (opener && opener !== document.body && opener.isConnected && typeof opener.focus === "function") {
        opener.focus();
      } else if (main) {
        main.focus();
      }
      if (onClose) onClose();
    }

    function open(title, content, opts) {
      const o = opts || {};
      connect();
      const swapping = dialog.open;
      if (!swapping) {
        finish();   // a close whose event has not arrived yet
        session = { opener: document.activeElement, onClose: null };
      } else if (!session) {
        session = { opener: null, onClose: null };   // opened by something else
      }
      session.onClose = typeof o.onClose === "function" ? o.onClose : null;
      const hadFocus = swapping && dialog.contains(document.activeElement);
      fillPanel(parts, title, content, o);
      parts.body.scrollTop = 0;
      if (!swapping) show();
      // Opening moves focus into the drawer. A swap moves it only to an [autofocus]
      // element, or to Close when the focused element left with the old content;
      // focus beside a docked drawer (on the grid) stays where it is.
      const wanted = dialog.querySelector(".drawer__tools [autofocus], .drawer__body [autofocus]");
      const lost = hadFocus && !dialog.contains(document.activeElement);
      const target = wanted || (!swapping || lost ? dialog.querySelector("[data-drawer-close]") : null);
      if (target) target.focus({ preventScroll: true });
    }

    function close() {
      if (!dialog || !dialog.open) return;
      dialog.close();
      finish();
    }

    function isOpen() {
      return Boolean(dialog && dialog.open);
    }

    function isDocked() {
      return Boolean(dialog && dialog.open && docked);
    }

    // Where focus goes back to when the drawer closes: the cell now open, after
    // stepping away from the one that opened it.
    function returnTo(element) {
      if (session && element instanceof Element) session.opener = element;
    }

    return { open, close, isOpen, isDocked, returnTo };
  })();

  /* ---- Help and notices ---------------------------------------------------- */

  function help(title, paragraphs) {
    return h(
      "details",
      { class: "help" },
      h("summary", null, "How to read this"),
      h(
        "div",
        { class: "help__body" },
        isEmpty(title) ? null : h("p", null, h("strong", null, String(title))),
        blocks(paragraphs),
      ),
    );
  }

  function notice(tone, content) {
    const kind = has(TONES, tone) ? tone : "neutral";
    return h(
      "div",
      { class: "notice", "data-tone": kind },
      icon(TONES[kind].icon),
      h("div", { class: "notice__text" }, blocks(content)),
    );
  }

  /* ---- Links ----------------------------------------------------------------- */

  function webAddress(href) {
    if (typeof href !== "string") return null;
    const address = href.trim();
    if (!address) return null;
    const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(address);
    if (!scheme) return address;   // a relative address, published beside the page
    return /^https?$/i.test(scheme[1]) ? address : null;
  }

  function hostName(address) {
    const match = /^(?:https?:)?\/\/([^/?#:]+)/i.exec(address);
    return match ? match[1].replace(/^www\./i, "") : address;
  }

  function link(href, text) {
    const address = webAddress(href);
    if (!address) {
      // Not a web address: show what there is, as plain text, rather than hide it.
      const given = typeof href === "string" ? href.trim() : "";
      return h("span", null, isEmpty(text) ? given || NOT_RECORDED : text);
    }
    const label = isEmpty(text) ? hostName(address) : text;
    return h(
      "a",
      { href: address, target: "_blank", rel: "noopener" },
      label,
      h("span", { class: "visually-hidden" }, " (opens in a new tab)"),
    );
  }

  /* ---- A view's own styles ---------------------------------------------------- */

  function addStyles(id, css) {
    const key = String(id);
    const added = document.querySelectorAll("style[data-hb-style]");
    if (Array.from(added).some((el) => el.getAttribute("data-hb-style") === key)) return;
    const el = document.createElement("style");
    el.setAttribute("data-hb-style", key);
    el.textContent = String(css);
    document.head.append(el);
  }

  Object.assign(HB, {
    addStyles,
    icon,
    statusChip,
    notice,
    countedTable,
    tableCells,
    panel,
    drawer,
    help,
    link,
  });
})((window.HB = window.HB || {}));
