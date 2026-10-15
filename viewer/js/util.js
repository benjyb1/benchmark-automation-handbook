/* =============================================================================
   Handbook viewer: util.js, the small helpers every other script uses.
   Copied from frontend/src/js/util.js (namespace HB renamed HB).
   Plain JavaScript, no modules: everything attaches to the one global window.HB.
   handbook/lib/handbook/page.py joins the scripts in this order: util.js, data.js,
   components.js, charts.js, edits.js, downloads.js, views/*.js, app.js.

   h(tag, attrs, ...children)   build an HTML element; svg(...) the same for SVG.
                                attrs: class (string or array), style (string or
                                object, custom properties included), on<event> in
                                any case (functions only; text is never made an
                                inline handler), aria-* and data-* (true and false
                                written out), any other attribute (true = present,
                                false or null = left out). A javascript:, vbscript:
                                or data: address in href or src is dropped.
                                children: strings and numbers become text nodes (the
                                same as textContent), Nodes go in as they are, arrays
                                are flattened, null and booleans are skipped. Nothing
                                is ever parsed as HTML; nothing here uses innerHTML.
   uid(prefix)                  a page-unique id for aria wiring
   fmtInt(n)                    a whole number in UK English: "1,616"
   fmtNum(n, digits, grouping)  up to `digits` decimals (2 if left out): "7.5";
                                grouping false leaves out the thousands commas
   fmtPct(a, b)                 a of b as a percentage; never "100%" while something
                                is missing, never "0%" while something is there
   plural(n, word, pluralWord)  "1 country", "99 countries"
   NOT_MEASURED, NOT_RECORDED   what a missing number reads ("Not measured yet") and
                                what a missing record reads ("Not recorded")
   store.get(key, fallback)     localStorage for per-viewer conveniences only; every
   store.set(key, value)        call is wrapped, so the page works without storage.
                                set(key, null) forgets; set returns false when it
                                could not save.
   copyText(text, button, opts) call it from the click handler. Writes to the
                                clipboard and the button says "Copied". When the
                                browser refuses, the text appears selected in a
                                read-only box on its own line, with "Copy the
                                selected text: Ctrl+C, Cmd+C, or touch and hold.";
                                the box goes once a copy happens or the person moves
                                on. The box goes after the button, or after
                                opts.after when the button sits in a narrow toolbar.
                                Resolves true when the clipboard took the text.
   when(localText, laterText)   the owner-facing words for what happens to a saved
                                change. HB.local is true when the page is served by
                                the project's own local server, which applies a
                                change at once: localText. Otherwise (the page as a
                                claude.ai Artifact) laterText, which says Claude does
                                it the next time it works on this project. Read at
                                call time, so local.js may set HB.local later.
   cantSave()                   what to say when this view has nowhere to save a
                                change (no store reachable), in the same two states.
   ========================================================================== */

(function (HB) {
  "use strict";

  const NOT_MEASURED = "Not measured yet";
  const NOT_RECORDED = "Not recorded";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const URL_ATTRS = new Set(["href", "src", "action", "formaction", "xlink:href"]);
  const BLOCKED_SCHEME = /^(?:javascript|vbscript|data):/i;

  /* ---- Elements ----------------------------------------------------------- */

  function isAttrs(value) {
    return value != null && typeof value === "object" && !Array.isArray(value) && !(value instanceof Node);
  }

  function classNames(value) {
    const list = Array.isArray(value) ? value.flat(Infinity) : [value];
    return list.filter(Boolean).join(" ").trim();
  }

  function setStyle(el, value) {
    if (typeof value === "string") {
      el.setAttribute("style", value);
      return;
    }
    for (const name of Object.keys(value)) {
      const v = value[name];
      if (v == null || v === false) continue;
      const prop = name.startsWith("--") ? name : name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
      el.style.setProperty(prop, String(v));
    }
  }

  function setAttrs(el, attrs) {
    for (const key of Object.keys(attrs)) {
      const value = attrs[key];
      // HTML attribute names ignore case, so every check does too: ONCLICK is onclick.
      const name = key.toLowerCase();
      const spelledOut = name.startsWith("aria-") || name.startsWith("data-");
      if (value == null || (value === false && !spelledOut)) continue;
      if (name === "class" || name === "classname") {
        const names = classNames(value);
        if (names) el.setAttribute("class", names);
      } else if (name === "style") {
        setStyle(el, value);
      } else if (/^on/i.test(key)) {
        // Only a function becomes a listener; text never becomes an inline handler.
        if (typeof value === "function") el.addEventListener(name.slice(2), value);
      } else if (name === "srcdoc") {
        // srcdoc would parse HTML: never set from here.
      } else if (URL_ATTRS.has(name) && BLOCKED_SCHEME.test(String(value).replace(/[\s\u0000-\u001f]+/g, ""))) {
        // A script or data address never reaches the page.
      } else {
        el.setAttribute(key, value === true ? (spelledOut ? "true" : "") : String(value));
      }
    }
  }

  function appendChildren(parent, children) {
    for (const child of children) {
      if (child == null || typeof child === "boolean") continue;
      if (Array.isArray(child)) appendChildren(parent, child);
      else if (child instanceof Node) parent.appendChild(child);
      else parent.appendChild(document.createTextNode(String(child)));
    }
  }

  function build(el, attrs, children) {
    if (attrs !== undefined && !isAttrs(attrs)) {
      children.unshift(attrs);   // h("p", "text"): the second argument was a child
      attrs = null;
    }
    if (attrs) setAttrs(el, attrs);
    appendChildren(el, children);
    return el;
  }

  function h(tag, attrs, ...children) {
    return build(document.createElement(tag), attrs, children);
  }

  function svg(tag, attrs, ...children) {
    return build(document.createElementNS(SVG_NS, tag), attrs, children);
  }

  let uidCount = 0;

  function uid(prefix) {
    uidCount += 1;
    return (prefix || "hb") + "-" + uidCount;
  }

  /* ---- Numbers ------------------------------------------------------------ */

  function isNum(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  const numberFormats = new Map();

  function numberFormat(digits, grouping) {
    const key = digits + (grouping ? "," : "");
    if (!numberFormats.has(key)) {
      numberFormats.set(key, new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits, useGrouping: grouping }));
    }
    return numberFormats.get(key);
  }

  function fmtInt(n) {
    if (!isNum(n)) return NOT_MEASURED;
    return numberFormat(0, true).format(Math.round(n) || 0);   // "|| 0" turns -0 into 0
  }

  function fmtNum(n, digits, grouping) {
    if (!isNum(n)) return NOT_MEASURED;
    const places = isNum(digits) ? Math.min(20, Math.max(0, Math.round(digits))) : 2;
    const text = numberFormat(places, grouping !== false).format(n);
    return text === "-0" ? "0" : text;
  }

  function fmtPct(a, b) {
    if (!isNum(a) || !isNum(b) || b <= 0) return NOT_MEASURED;
    const pct = (a / b) * 100;
    const whole = Math.round(pct);
    // 693 of 695 is 99.7%, not 100%; 1 of 2,000 is 0.1%, not 0%.
    if (whole >= 100 && a < b) return (Math.floor(pct * 10) / 10).toFixed(1) + "%";
    if (whole <= 0 && a > 0) return (Math.ceil(pct * 10) / 10).toFixed(1) + "%";
    return numberFormat(0, true).format(whole || 0) + "%";
  }

  function plural(n, word, pluralWord) {
    if (!isNum(n)) return NOT_MEASURED;
    return fmtInt(n) + " " + (Math.round(n) === 1 ? word : pluralWord || word + "s");
  }

  /* ---- Answers that are figures (answers.py) ------------------------------- */

  function isFigureQuestion(q) {
    return Boolean(q && q.answer && q.answer.kind === "number");
  }

  /* A figure with its unit, as the owner writes it: 73%, 1,200 hens. */
  function figureText(q, value) {
    const unit = q && q.answer && q.answer.unit ? String(q.answer.unit) : "";
    const text = fmtNum(value, 4, Math.abs(value) >= 10000);     // 2025 is a year, not "2,025"
    return !unit ? text : unit === "%" ? text + "%" : text + " " + unit;
  }

  /* What a number question takes, in words: "A figure from 0 to 100 (%)". */
  function figureWords(q) {
    const a = (q && q.answer) || {};
    const lo = isNum(a.min) ? fmtNum(a.min, 4, Math.abs(a.min) >= 10000) : null;
    const hi = isNum(a.max) ? fmtNum(a.max, 4, Math.abs(a.max) >= 10000) : null;
    const span = lo != null && hi != null ? " from " + lo + " to " + hi : lo != null ? " of at least " + lo : hi != null ? " of at most " + hi : "";
    return "A figure" + span + (a.unit ? " (" + a.unit + ")" : "");
  }

  /* ---- Storage: per-viewer conveniences only ------------------------------ */

  const STORE_PREFIX = "hb:";

  const store = {
    get(key, fallback) {
      const missing = fallback === undefined ? null : fallback;
      try {
        const raw = window.localStorage.getItem(STORE_PREFIX + key);
        return raw === null ? missing : JSON.parse(raw);
      } catch (err) {
        return missing;
      }
    },
    set(key, value) {
      try {
        if (value == null) window.localStorage.removeItem(STORE_PREFIX + key);
        else window.localStorage.setItem(STORE_PREFIX + key, JSON.stringify(value));
        return true;
      } catch (err) {
        return false;
      }
    },
  };

  /* ---- Clipboard ----------------------------------------------------------
     Each copy button keeps a little state: which click is the latest (only its
     result changes the button), the button's own label while it says "Copied",
     the timer that puts the label back, a visually hidden status line right after
     it (role=status, so screen readers hear "Copied"; inside the drawer when the
     button is) and, while a manual-copy box is showing, the function that removes
     it. */

  const COPIED_FOR_MS = 2000;
  const MANUAL_HINT = "Copy the selected text: Ctrl+C, Cmd+C, or touch and hold.";
  const copyStates = new WeakMap();

  function copyState(button) {
    let state = copyStates.get(button);
    if (!state) {
      state = { attempt: 0, label: null, timer: 0, status: null, said: 0, tidy: null };
      copyStates.set(button, state);
    }
    return state;
  }

  function announce(button, message) {
    const state = copyState(button);
    if (!state.status || !state.status.isConnected) {
      state.status = h("span", { class: "visually-hidden", role: "status" });
      button.after(state.status);
    }
    const status = state.status;
    status.textContent = "";
    // A live region announces a change, so the words go in a moment after it exists;
    // a reset in that moment cancels them.
    const said = ++state.said;
    setTimeout(() => {
      if (state.said === said) status.textContent = message;
    }, 60);
  }

  function resetCopy(button) {
    const state = copyState(button);
    clearTimeout(state.timer);
    state.said += 1;
    if (state.tidy) state.tidy();
    if (state.label) {
      button.replaceChildren(...state.label);
      button.style.removeProperty("min-width");
      state.label = null;
    }
    if (state.status) state.status.textContent = "";
  }

  function showCopied(button) {
    if (!button.isConnected) return;
    const state = copyState(button);
    if (state.tidy) state.tidy();   // a manual-copy box is no longer needed
    clearTimeout(state.timer);
    if (!state.label) {
      // Save the real label only once: when "Copied" is already showing, the
      // button's text is not its label.
      state.label = Array.from(button.childNodes);
      button.style.minWidth = button.offsetWidth + "px";   // the row doesn't jump
      button.replaceChildren("Copied");
    }
    announce(button, "Copied to the clipboard.");
    state.timer = setTimeout(() => resetCopy(button), COPIED_FOR_MS);
  }

  function offerManualCopy(text, button, place) {
    if (!button.isConnected) return;
    const state = copyState(button);
    const hintId = uid("copy-manual-hint");
    const area = h("textarea", {
      class: "input copy-manual__text",
      readonly: true,
      rows: 4,
      spellcheck: "false",
      "aria-label": "Text to copy",
      "aria-describedby": hintId,
    });
    area.value = text;
    const box = h("div", { class: "copy-manual" }, h("p", { class: "copy-manual__hint", id: hintId }, MANUAL_HINT), area);
    (place && place.isConnected ? place : state.status && state.status.isConnected ? state.status : button).after(box);
    let done = false;
    const tidy = () => {
      if (done) return;
      done = true;
      state.tidy = null;
      box.remove();
    };
    state.tidy = tidy;
    area.addEventListener("copy", () => {
      // Let the browser copy the selection before the box goes.
      setTimeout(() => {
        const focusInBox = box.contains(document.activeElement);
        tidy();
        if (focusInBox) button.focus({ preventScroll: true });
        showCopied(button);
      }, 0);
    });
    box.addEventListener("focusout", () => {
      // Moving on means focus went somewhere else on this page; switching to
      // another window (to paste, say) keeps the box.
      setTimeout(() => {
        if (document.hasFocus() && !box.contains(document.activeElement)) tidy();
      }, 0);
    });
    area.focus();
    area.select();
    area.setSelectionRange(0, text.length);
  }

  function copyText(text, button, opts) {
    const value = text == null ? "" : String(text);
    const place = opts && opts.after;
    let state = null;
    let mine = 0;
    if (button) {
      resetCopy(button);
      state = copyState(button);
      mine = ++state.attempt;
    }
    const latest = () => state !== null && state.attempt === mine;
    let attempt;
    try {
      attempt = navigator.clipboard.writeText(value);   // started inside the click handler
    } catch (err) {
      attempt = Promise.reject(err);   // no clipboard here (an insecure page, an old browser)
    }
    // A slow clipboard can answer after a later click: only the latest click's
    // answer changes the button.
    return Promise.resolve(attempt).then(
      () => {
        if (latest()) showCopied(button);
        return true;
      },
      () => {
        if (latest()) offerManualCopy(value, button, place);
        return false;
      },
    );
  }

  /* ---- Now or later -------------------------------------------------------- */

  function when(localText, laterText) {
    return Boolean(HB.local) ? localText : laterText;
  }

  function cantSave() {
    return when("The page can't reach its server just now, so changes can't be saved. Reload the page and try again.",
      "This view can't save changes. Open the project's own page to make them.");
  }

  Object.assign(HB, {
    when,
    cantSave,
    NOT_MEASURED,
    NOT_RECORDED,
    h,
    svg,
    uid,
    isNum,
    fmtInt,
    fmtNum,
    fmtPct,
    plural,
    isFigureQuestion,
    figureText,
    figureWords,
    store,
    copyText,
  });
})((window.HB = window.HB || {}));

/* =============================================================================
   Flag glyphs (shared by the grid and the cell panel).
   A cell's flags (bundle cell.flags) come in two sorts. The factual ones say
   something a researcher has to check, and each has its own glyph, always shown
   with its word (in the text beside it, or in an aria-label and title):
     quote_not_found  a struck ring   "Quote not on saved page"
     document_changed a page, struck  "Document changed since scored" (the saved copy is no
                                      longer the text the answer was scored from)
     group_differs    a not-equals    "Differs across countries"
     after_cutoff     a calendar      "After the cut-off"
     reads_disagree   a half disc     "Reads disagree"
     weak_source      one bar of three "Not from the company" (every document cited is weak)
   "judgement" (the answer rests on Claude's reading of an open rule) is not
   factual: the grid counts it on the question's header, never on a cell.

   HB.flagKinds          {factual: [kind, ...] in display order,
                          words: {kind: short word}} (judgement included in words)
   HB.factualFlags(cell) the cell's factual kinds, in display order ([] for none)
   HB.flagGlyph(kind)    an inline <svg class="flag-glyph flag-glyph--<kind>">,
                         aria-hidden, 1em square, drawn in currentColor on a
                         surface-coloured disc so it reads on any fill. Set its
                         colour (var(--critical) on the grid) and size from CSS.
   ========================================================================== */

(function (HB) {
  "use strict";

  const FACTUAL = ["quote_not_found", "document_changed", "group_differs", "after_cutoff", "reads_disagree", "weak_source", "owner_changed"];
  const WORDS = {
    quote_not_found: "Quote not on saved page",
    document_changed: "Document changed since scored",
    group_differs: "Differs across countries",
    after_cutoff: "After the cut-off",
    reads_disagree: "Second read differs",
    weak_source: "Not from the company",
    owner_changed: "Changed by the owner",
    judgement: "Rests on an open rule",
  };
  const LINE = { fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linecap": "round" };

  const SHAPES = {
    quote_not_found: () => [HB.svg("circle", Object.assign({ cx: 6, cy: 6, r: 3.4 }, LINE)),
      HB.svg("path", Object.assign({ d: "M3.6 8.4 8.4 3.6" }, LINE))],
    document_changed: () => [HB.svg("rect", Object.assign({ x: 3.2, y: 2.6, width: 5.6, height: 6.8, rx: 0.8 }, LINE)),
      HB.svg("path", Object.assign({ d: "M4.2 9.6 7.8 2.4" }, LINE))],
    group_differs: () => [HB.svg("path", Object.assign({ d: "M2.9 4.9h6.2M2.9 7.3h6.2M7.4 2.7 4.6 9.5" }, LINE))],
    after_cutoff: () => [HB.svg("rect", Object.assign({ x: 3, y: 3.2, width: 6, height: 5.8, rx: 0.9 }, LINE)),
      HB.svg("path", { d: "M3 3.2h6v2H3z", fill: "currentColor" })],
    reads_disagree: () => [HB.svg("circle", Object.assign({ cx: 6, cy: 6, r: 3.4 }, LINE)),
      HB.svg("path", { d: "M6 2.6a3.4 3.4 0 0 1 0 6.8z", fill: "currentColor" })],
    weak_source: () => [HB.svg("path", Object.assign({ d: "M3.4 9V6.6" }, LINE, { "stroke-width": "1.8" })),
      HB.svg("path", Object.assign({ d: "M6 9V4.8M8.6 9V3", opacity: "0.4" }, LINE, { "stroke-width": "1.8" }))],
    owner_changed: () => [HB.svg("path", Object.assign({ d: "M4 8 8.4 3.6" }, LINE, { "stroke-width": "1.8" })),
      HB.svg("path", { d: "M2.8 9.2l.6-2 1.4 1.4z", fill: "currentColor" })],
  };

  function factualFlags(cell) {
    const flags = cell && Array.isArray(cell.flags) ? cell.flags : [];
    return FACTUAL.filter((kind) => flags.includes(kind));
  }

  function flagGlyph(kind) {
    const shape = Object.prototype.hasOwnProperty.call(SHAPES, kind) ? SHAPES[kind] : SHAPES.quote_not_found;
    return HB.svg("svg", {
      class: "flag-glyph flag-glyph--" + String(kind).replace(/[^a-z_]/g, ""),
      viewBox: "0 0 12 12", width: "1em", height: "1em", "aria-hidden": "true", focusable: "false",
    }, HB.svg("circle", { cx: 6, cy: 6, r: 6, style: "fill: var(--surface, #fff)" }), shape());
  }

  HB.flagKinds = { factual: FACTUAL.slice(), words: Object.assign({}, WORDS) };
  HB.factualFlags = factualFlags;
  HB.flagGlyph = flagGlyph;
})((window.HB = window.HB || {}));

/* The kinds of file an upload box takes, by extension, with the content type a browser sends for each.
   It is the list in handbook/lib/handbook/readers.py (UPLOAD_MIME); tests/test_handbook_readers.py checks the two agree.
   HB.uploadTypes   {extension: content type}      HB.uploadAccept   ".pdf,.png,..." for an input's accept */
(function (HB) {
  const TYPES = {
    pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif",
    heic: "image/heic", heif: "image/heif", tif: "image/tiff", tiff: "image/tiff",
    html: "text/html", htm: "text/html", xhtml: "application/xhtml+xml",
    txt: "text/plain", text: "text/plain", md: "text/markdown", csv: "text/csv", tsv: "text/tab-separated-values",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsm: "application/vnd.ms-excel.sheet.macroEnabled.12",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    json: "application/json", jsonl: "application/x-ndjson", xml: "application/xml", svg: "image/svg+xml",
    doc: "application/msword", rtf: "application/rtf", xls: "application/vnd.ms-excel",
    odt: "application/vnd.oasis.opendocument.text", odp: "application/vnd.oasis.opendocument.presentation",
    ods: "application/vnd.oasis.opendocument.spreadsheet",
    eml: "message/rfc822", msg: "application/vnd.ms-outlook",
  };
  HB.uploadTypes = TYPES;
  HB.uploadAccept = Object.keys(TYPES).map((ext) => "." + ext).join(",");
})(window.HB = window.HB || {});
