/* =============================================================================
   Handbook viewer: views/layoutcheck.js, the page checking its own layout.
   Sets HB.layoutCheck.

   Owners use the tool with nobody from us looking over it, so the page looks for
   text that came out badly and says so, the way a person zooming on the headers
   would (BFCI, 23 Oct 2026: "Vegetarian diets" broke into "Vegetaria-n" over a
   three-mark section and nobody saw it).

   HB.layoutCheck.scan(root)  the problems on screen under root (default: the
                              main area and an open drawer), each
                              { kind, text, view, source, key }:
                                kind    "broken_word"  a word split across lines
                                        "cut_off"      text cut by its box with no
                                                       full version on hover
                                        "spills"       text running out of its box
                                        "covered"      text with something drawn over
                                                       it (the chat button, a pinned
                                                       column, the next cell)
                                        "short_cut"    a short name cut to "..." even
                                                       with the full text on hover
                                                       ("Getting sta...": its box is
                                                       too narrow, not the name too long)
                                        "lookalike"    two shortened names that read
                                                       the same ("Milk alternatives
                                                       in the..." twice)
                                        "number_wraps" a figure and its words split
                                                       over lines ("5,638 points at /
                                                       stake")
                                text    the words affected (at most 120 characters)
                                view    the tab it was seen on ("grid", "results", ...)
                                source  what the text is in the project, when it is
                                        the project's own words: { kind, id, field }
                                        (kind "section", "question", "unit", "label",
                                        "project", "band"); null for the page's own
                                        wording, which only a tool update can change
                                key     kind + view + text, for telling problems apart
                                el      the element and its parent (tag.classes)
   HB.layoutCheck.watch()     runs scan after every redraw (quietly, when the page
                              is idle) and sends new problems to `hb serve`, which
                              keeps them in page-data/layout.json (layout.py).
                              Does nothing on a published page (no server).
   HB.layoutCheck.scanAll()   draws every other tab off screen and sends its scan
                              too, once each time the project's names change.
   HB.layoutCheck.last()      the problems from the last scan.
   HB.layoutCheck.all()       Promise of every tab's problems (hb serve's list,
                              with the tab on screen from the last scan).
   HB.layoutCheck.cached()    the same, as last fetched (no waiting)
   HB.layoutCheck.promptBlock()
                              Promise of the chats' block listing the problems in
                              the project's own words, or "" when there are none.

   Only short text is checked for broken words (names, headings, labels, buttons):
   a paragraph may hyphenate a long word at the end of a line, as print does.
   ========================================================================== */

(function (HB) {
  "use strict";

  const SHORT = 60;          // characters: longer text is prose, which may hyphenate
  const SHORT_CUT = 28;      // characters: a name this short never needs "..." ("Tier 4 · Getting started")
  // A figure and the words that go with it ("5,638 points at stake", "18 of 18 points", "£54,440 a year",
  // "33% of companies"); not a question's number before its name ("1.1 Broiler welfare policy", "9
  // elektronische Aktenführung") or a year ("2021 edition"), which may wrap.
  const NUMBER_PHRASE = /^[\s(]*[-+\u2212]?(?:[£$€]\d[\d.,]*|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?%|\d+\s+of\s+\d[\d,]*)\s*\p{Ll}/u;
  // A word: four letters or more of a script that puts spaces between words. Chinese, Japanese and Thai
  // break between characters as a matter of course (stress test, 27 Oct 2026), so they never "break".
  const WORD = /(?:(?![\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}])[\p{L}\p{N}]){4,}/gu;
  const LAYOUT_WORD = 12;    // letters: an ordinary word this short breaking means its box is too narrow, not the name too long
  const SKIP = "script, style, template, svg, .visually-hidden, [aria-hidden='true'], .hbg-tip, [data-layout-ok]";
  let lastFound = [];
  const sent = new Map();          // tab: the problem keys it last sent

  const tidy = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  const clip = (s) => (s.length > 120 ? s.slice(0, 119) + "…" : s);   // not shown: the report's copy

  const currentView = () => (typeof HB.route === "function" && HB.route().tab) || location.hash.replace(/^#/, "").split("~")[0] || "start";

  /* ---- What the text is in the project ---------------------------------------- */

  function sourceIndex() {
    const b = (HB.data && HB.data.bundle && HB.data.bundle()) || {};
    const map = new Map();
    const put = (text, src) => {
      const t = tidy(text);
      if (t && !map.has(t)) map.set(t, src);
    };
    const p = b.project || {};
    put(p.name, { kind: "project", id: null, field: "name" });
    ((b.scoresheet && b.scoresheet.sections) || []).forEach((s) => put(s.name, { kind: "section", id: String(s.id), field: "name" }));
    ((b.scoresheet && b.scoresheet.bands) || []).forEach((band) => put(band && band.name, { kind: "band", id: band && band.name, field: "name" }));
    ((b.scoresheet && b.scoresheet.sections) || []).forEach((s) => put(s.id + " " + s.name, { kind: "section", id: String(s.id), field: "name" }));
    (b.questions || []).forEach((q) => {
      put(q.short, { kind: "question", id: String(q.id), field: "short" });
      put(q.id + " " + q.short, { kind: "question", id: String(q.id), field: "short" });
      put(q.wording, { kind: "question", id: String(q.id), field: "wording" });
    });
    (b.units || []).forEach((u) => {
      put(u.name, { kind: "unit", id: String(u.id), field: "name" });
      Object.entries(u.labels || {}).forEach(([label, value]) => put(value, { kind: "label", id: label, field: "value" }));
    });
    (p.labels || []).forEach((label) => put(label, { kind: "label", id: label, field: "name" }));
    return map;
  }

  function sourceOf(index, el) {
    for (let node = el, depth = 0; node && depth < 3; node = node.parentElement, depth += 1) {
      const hit = index.get(tidy(node.textContent));
      if (hit) return hit;
      const title = node.getAttribute && node.getAttribute("title");
      if (title && index.get(tidy(title))) return index.get(tidy(title));
    }
    return null;
  }

  /* ---- The checks ---------------------------------------------------------------- */

  function visible(el) {
    if (!el || el.closest(SKIP)) return false;
    // A closed "More": its body isn't on screen, only its own summary is, and only if no closed one
    // holds it in turn (the key's "Other kinds of check" inside the closed View panel).
    for (let d = el.closest("details:not([open])"); d; d = d.parentElement && d.parentElement.closest("details:not([open])")) {
      const own = el.closest("summary");
      if (!own || own.parentElement !== d) return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && cs.clipPath === "none";
  }

  function lines(rects) {
    const tops = [];
    rects.forEach((r) => {
      if (r.width > 0 && !tops.some((t) => Math.abs(t - r.top) < 3)) tops.push(r.top);
    });
    return tops.length;
  }

  // A word whose letters land on two lines. The whole text node is measured first, so text on one
  // line costs one measurement.
  function brokenWords(node, range) {
    range.selectNodeContents(node);
    if (lines([...range.getClientRects()]) < 2) return [];
    const out = [];
    const text = node.nodeValue;
    for (const m of text.matchAll(WORD)) {
      range.setStart(node, m.index);
      range.setEnd(node, m.index + m[0].length);
      if (lines([...range.getClientRects()]) > 1) out.push(m[0]);
    }
    return out;
  }

  // Cut: the box hides part of its text. Fine when the box shows "…" and the full text is on hover
  // (a title on it or just above it) or read out (a visually hidden copy beside it).
  function cutOff(el, cs) {
    const hides = /hidden|clip/.test(cs.overflowX) || /hidden|clip/.test(cs.overflowY);
    if (!hides) return false;
    const wide = el.scrollWidth > el.clientWidth + 1;
    const tall = el.scrollHeight > el.clientHeight + 2 && cs.whiteSpace !== "nowrap";
    if (!wide && !tall) return false;
    const marked = cs.textOverflow === "ellipsis" || (cs.webkitLineClamp && cs.webkitLineClamp !== "none");
    // The full text is on hover, read out, or one click away (a preview in a list that opens it).
    const full = el.closest("[title]") || el.querySelector(".visually-hidden") ||
      el.closest("button, a[href], summary, [role='button'], [role='option'], [role='tab'], [tabindex]");
    if (!(marked && full)) return "cut_off";
    // A short name cut even with the full text on hover: its box is too narrow (design read, 26 Oct 2026).
    return wide && tidy(el.textContent).length <= SHORT_CUT ? "short_cut" : false;
  }

  // Spills: the box's own words reach past its edges, so they run over whatever is next to it. The
  // words are measured, not the box's scroll width: an icon poking out of a mark is not text.
  function spills(el, cs, range) {
    if (/hidden|clip|auto|scroll/.test(cs.overflowX)) return false;
    if (cs.display === "inline" || cs.display === "contents") return false;
    if (parseFloat(cs.fontSize) < 4 || cs.color === "transparent" || cs.color === "rgba(0, 0, 0, 0)") return false;
    const box = el.getBoundingClientRect();
    if (box.width <= 2) return false;
    return [...el.childNodes].some((n) => {
      if (n.nodeType !== 3 || !n.nodeValue.trim()) return false;
      range.selectNodeContents(n);
      const r = range.getBoundingClientRect();
      // 4px: the spaces that hang at line ends in pre-wrap text reach a few pixels past the box, unseen.
      return r.width > 0 && (r.right > box.right + 4 || r.left < box.left - 4);
    });
  }

  // Covered: something else is drawn over the words where they should show (the chat button over
  // "Send the document", a pinned column over "Performance", a sticky cell over the one before it:
  // design read, 26 Oct 2026). Points along each line of the text are tested for what is on top there;
  // it counts when that is not the text's own box, is opaque, and is not a dialog over the whole page.
  // Words a scrolling box has moved out of its view are not covered, just scrolled.
  function covered(el, node, range) {
    if (tidy(node.nodeValue).length <= 3) return null;   // a column's number at a scrolling edge: the grid scrolls under its pins
    if (el.closest(".cellp-page")) return null;           // a quoted page: a small box of its own that scrolls
    range.selectNodeContents(node);
    const view = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const clip = clipBox(el, view);
    for (const r of range.getClientRects()) {
      if (r.width < 4 || r.height < 4) continue;
      const y = r.top + r.height / 2;
      for (const x of [r.left + 2, r.left + r.width / 2, r.right - 2]) {
        if (x < clip.left || x > clip.right || y < clip.top || y > clip.bottom) continue;
        const hit = document.elementFromPoint(x, y);
        if (!hit || hit === el || el.contains(hit)) continue;
        const cover = opaqueCover(hit, el);
        if (cover && !cover.closest("[data-layout-ok]") && !headerOver(cover)) return cover;
      }
    }
    return null;
  }

  // Cut by a box around it: the words (or the box that ends them in "...") run past the edge of a parent
  // that hides what overflows, so they stop mid-word with no "..." ("...Holdings" cut to "Holdin" by the
  // grid's name column: stress test, 27 Oct 2026). A scrolling box isn't counted: its edge moves.
  function clippedByBox(el, node, range) {
    const cs = getComputedStyle(el);
    if (/auto|scroll/.test(cs.overflowX)) return null;   // text in a box that scrolls is scrolled, not cut
    const ownEnd = /hidden|clip/.test(cs.overflowX) && cs.textOverflow === "ellipsis";
    let r;
    if (ownEnd) r = el.getBoundingClientRect();
    else { range.selectNodeContents(node); r = range.getBoundingClientRect(); }
    if (r.width < 2) return null;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ps = getComputedStyle(n);
      if (/auto|scroll/.test(ps.overflowX)) return null;
      if (!/hidden|clip/.test(ps.overflowX)) continue;
      const b = n.getBoundingClientRect();
      if (r.right > b.right + 1 || r.left < b.left - 1) {
        // A parent that ends its own line in "..." (the Tier cell's "Tier 4 · Getting sta...") is a marked
        // cut, judged as that parent's text (cutOff, short_cut).
        return ps.textOverflow === "ellipsis" && n.closest("[title]") ? null : n;
      }
    }
    return null;
  }

  // The visible part of el: the viewport cut down by el and every box above it that clips its overflow
  // (a name cut to "..." in its own box is not covered past its end).
  function clipBox(el, box) {
    const out = Object.assign({}, box);
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflowX === "visible" && cs.overflowY === "visible") continue;
      const r = n.getBoundingClientRect();
      out.left = Math.max(out.left, r.left); out.right = Math.min(out.right, r.right);
      out.top = Math.max(out.top, r.top); out.bottom = Math.min(out.bottom, r.bottom);
    }
    return out;
  }

  // The element that hides the text at a point: hit or the first of its parents (short of one that holds
  // the text) with a background, so a see-through layer over a card doesn't count. A layer covering most
  // of the page is a dialog, which hides the page on purpose.
  function opaqueCover(hit, el) {
    if (document.querySelector("dialog:modal")) return null;   // a dialog open over the page ("What should we call you?") covers it on purpose
    const area = window.innerWidth * window.innerHeight;
    for (let n = hit; n && n !== document.body && !n.contains(el); n = n.parentElement) {
      const cs = getComputedStyle(n);
      const paint = (cs.backgroundColor && !/^(transparent|rgba\(0, 0, 0, 0\))$/.test(cs.backgroundColor)) || cs.backgroundImage !== "none";
      if (!paint) continue;
      const r = n.getBoundingClientRect();
      return r.width * r.height > 0.35 * area ? null : n;
    }
    return null;
  }

  // A header that sticks to the top of a scrolling box (the grid's group lines, a panel's title): what
  // scrolls under it is meant to. It may hold cells pinned to the left as well ("Already scored" over the
  // names), so every box up the chain is looked at, not just the first that sticks.
  function headerOver(cover) {
    for (let n = cover; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if ((cs.position === "sticky" || cs.position === "fixed") && cs.top !== "auto") return true;
    }
    return false;
  }

  // Two shortened names that read the same: in one line of text ("Milk alternatives in the...: 3 to 0;
  // Milk alternatives in the...: 3 to 0"), or as boxes side by side whose full names differ.
  function lookalike(el) {
    const text = tidy(el.textContent);
    const cuts = text.match(/[^;,·:()]{4,}?(?:\u2026|\.\.\.)/g) || [];
    const seen = new Set();
    for (const c of cuts.map(tidy)) {
      if (seen.has(c)) return c;
      seen.add(c);
    }
    const parent = el.parentElement;
    if (!parent || text.length < 5 || !/(\u2026|\.\.\.)$/.test(text)) return null;   // not a lone "..." standing for a figure
    const title = el.getAttribute("title") || "";
    for (const sib of parent.children) {
      if (sib !== el && tidy(sib.textContent) === text && (sib.getAttribute("title") || "") !== title) return text;
    }
    return null;
  }

  // A figure and the words after it split over two lines ("5,638 points at / stake").
  function numberWraps(node, range, whole) {
    if (whole.length > 32 || !NUMBER_PHRASE.test(whole)) return false;
    range.selectNodeContents(node);
    return lines([...range.getClientRects()]) > 1;
  }

  // Which element, for whoever fixes it: its tag and classes, and those of its parent.
  function describe(el) {
    const one = (e) => e ? e.tagName.toLowerCase() + (typeof e.className === "string" && e.className.trim()
      ? "." + e.className.trim().split(/\s+/).join(".") : "") : "";
    return (one(el.parentElement) + " > " + one(el)).slice(0, 160);
  }

  function ownText(el) {
    return [...el.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim());
  }

  function scan(root, tab) {
    const roots = root ? [root] : [document.getElementById("main"), document.getElementById("drawer-body")]
      .filter((r) => r && r.getClientRects().length);
    const view = tab || currentView();
    const index = sourceIndex();
    const range = document.createRange();
    const found = new Map();
    const add = (kind, el, text, layoutFault) => {
      const t = clip(tidy(text));
      if (!t) return;
      const key = kind + "|" + view + "|" + t;
      // Covered, cut short, look-alike and split figures are the layout's fault, whatever the words: no
      // shorter name helps, so they count as the tool's (and stop a release) rather than go to the chat.
      const own = !layoutFault && (kind === "broken_word" || kind === "cut_off" || kind === "spills");
      if (!found.has(key)) found.set(key, { kind, text: t, view, source: own ? sourceOf(index, el) : null, key, el: describe(el) });
    };
    roots.forEach((r) => {
      const walker = document.createTreeWalker(r, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => (n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
      });
      const seen = new Set();
      for (let node = walker.nextNode(), count = 0; node && count < 6000; node = walker.nextNode(), count += 1) {
        const el = node.parentElement;
        if (!el || !visible(el)) continue;
        const whole = tidy(el.textContent);
        if (whole.length <= SHORT) {
          const broken = brokenWords(node, range);
          // "Compan/y 01" in a squeezed column is the page's fault; "Supercalifragilistic/..." the name's.
          // A code as long as G.GOVERNANCE.DPL.e.e2 is the name's, whatever letters break.
          if (broken.length) add("broken_word", el, whole, broken.every((w) => w.length < LAYOUT_WORD) &&
            !whole.split(/\s+/).some((t) => t.length >= 20));
          else if (numberWraps(node, range, tidy(node.nodeValue))) add("number_wraps", el, whole);
        }
        if (covered(el, node, range)) add("covered", el, whole);
        else if (clippedByBox(el, node, range)) add("cut_off", el, whole, true);
        if (seen.has(el) || !ownText(el)) continue;
        seen.add(el);
        const cs = getComputedStyle(el);
        const cut = cutOff(el, cs);
        if (cut) add(cut, el, whole);
        else if (spills(el, cs, range)) add("spills", el, whole);
        const same = lookalike(el);
        if (same) add("lookalike", el, same);
      }
    });
    const out = [...found.values()];
    if (!tab) lastFound = out;          // a tab drawn off screen (scanAll) isn't the one on screen
    return out;
  }

  /* ---- Telling hb serve ------------------------------------------------------------ */

  // Each tab's whole scan, sent when it differs from what that tab last sent (an empty one too, so a
  // fixed name drops off the list).
  function report(problems, tab) {
    if (!HB.local || typeof HB.local.post !== "function") return;
    const view = tab || currentView();
    const keys = problems.map((p) => p.key).sort().join("\n");
    if (sent.get(view) === keys) return;
    sent.set(view, keys);
    HB.local.post("layout", { view, width: window.innerWidth, problems })
      .then(() => { known = null; return all(); })
      .then(() => { if (HB.chat && typeof HB.chat.badge === "function") HB.chat.badge(); })
      .catch(() => sent.delete(view));
  }

  // Every tab's problems as hb serve keeps them (page-data/layout.json), for the chats' instructions;
  // the tab on screen comes from the last scan.
  let known = null;
  async function all() {
    if (!HB.local || typeof HB.local.get !== "function") return lastFound.slice();
    if (!known) {
      try {
        const res = await HB.local.get("layout");
        known = res.ok ? ((await res.json()).problems || []) : [];
      } catch (e) {
        known = [];
      }
    }
    const view = currentView();
    return known.filter((p) => p.view !== view).concat(lastFound);
  }

  /** What all() last gave, without asking hb serve again (the chat's notices are drawn at once). */
  function cached() {
    const view = currentView();
    return (known || []).filter((p) => p.view !== view).concat(lastFound);
  }

  /** The chats' block: the problems in the project's own words, which Claude can fix. */
  async function promptBlock() {
    const own = (await all()).filter((p) => p.source);
    if (!own.length) return "";
    const what = { broken_word: "a word breaks across two lines", cut_off: "is cut off", spills: "runs out of its box",
      covered: "is hidden under something else", short_cut: "is cut short where it should show in full",
      lookalike: "reads the same as another name once shortened", number_wraps: "splits its figure from its words" };
    const lines = own.slice(0, 20).map((p) => {
      const s = p.source;
      const thing = s.kind === "question" ? "question " + s.id + "'s " + (s.field || "short")
        : s.kind === "section" ? "section " + s.id + "'s name"
          : s.kind === "unit" ? "the row " + s.id + "'s name"
            : s.kind === "label" ? (s.field === "name" ? "the label " + s.id : "a value of the label " + s.id)
              : s.kind + " " + (s.field || "name");
      return "- \"" + p.text + "\" (" + thing + ") " + what[p.kind] + " on the " + p.view + " tab.";
    });
    return "=== TEXT THAT DOESN'T FIT ON THE PAGE ===\n" +
      "The page checked its own layout and found these names that don't fit where they are shown. " +
      "When it suits the conversation, offer a shorter name that keeps the meaning (in the owner's own words " +
      "where you can) and change it once they agree; don't change wording the owner chose without asking.\n" +
      lines.join("\n");
  }

  // Every other tab, drawn off screen and checked, once each time the project's names change (set-up
  // writes them; scoring doesn't), so set-up Claude reads every tab's names that don't fit (`hb names`)
  // before handing over, not just those of the tab someone happened to open. Set-up itself is left out:
  // its chat lives in the tab and can't be drawn twice.
  let allFor = null;
  function namesSignature() {
    const b = (HB.data && HB.data.bundle && HB.data.bundle()) || {};
    const sheet = b.scoresheet || {};
    return JSON.stringify([window.innerWidth, (b.project || {}).name,
      (sheet.sections || []).map((x) => x && x.name), (sheet.bands || []).map((x) => x && x.name),
      (b.questions || []).map((q) => q && q.short), (b.units || []).map((u) => u && [u.name, u.labels])]);
  }
  async function scanAll() {
    if (typeof HB.drawOffscreen !== "function" || !HB.local) return;
    const sig = namesSignature();
    if (sig === allFor) return;
    allFor = sig;
    const here = currentView();
    const tabs = [...document.querySelectorAll(".tabs__tab")]
      .map((a) => (a.getAttribute("href") || "").replace(/^#/, "").split("~")[0])
      .filter((id) => id && id !== here && id !== "setup");
    for (const id of tabs) {
      const box = HB.drawOffscreen(id);
      if (!box) continue;
      await new Promise((r) => setTimeout(r, 250));   // the grid measures its names on a timer
      try {
        report(scan(box, id), id);
      } finally {
        box.remove();
      }
    }
  }

  let timer = null;
  function later() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const run = () => { report(scan()); setTimeout(scanAll, 1500); };
      if ("requestIdleCallback" in window) window.requestIdleCallback(run, { timeout: 3000 });
      else run();
    }, 1200);
  }

  function watch() {
    if (!HB.local) return;           // the published page has nobody to tell
    const targets = [document.getElementById("main"), document.getElementById("drawer-body")].filter(Boolean);
    const seen = new MutationObserver(later);
    targets.forEach((t) => seen.observe(t, { childList: true, subtree: true, characterData: true }));
    window.addEventListener("resize", later);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(later);
    all().then(() => { if (HB.chat && typeof HB.chat.badge === "function") HB.chat.badge(); });
    later();
  }

  HB.layoutCheck = { scan, scanAll, watch, last: () => lastFound, all, cached, promptBlock };
})(window.HB = window.HB || {});
