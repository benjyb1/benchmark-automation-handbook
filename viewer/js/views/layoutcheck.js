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
  const WORD = /[\p{L}\p{N}]{4,}/gu;
  const SKIP = "script, style, template, svg, .visually-hidden, [aria-hidden='true'], .hbg-tip, [data-layout-ok]";
  let lastFound = [];
  const sent = new Map();          // tab: the problem keys it last sent

  const tidy = (s) => String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  const clip = (s) => (s.length > 120 ? s.slice(0, 119) + "…" : s);

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
    const shut = el.closest("details:not([open])");          // a closed "More": its body isn't on screen
    if (shut && !el.closest("summary")) return false;
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
    return !(marked && full);
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
      return r.width > 0 && (r.right > box.right + 2 || r.left < box.left - 2);
    });
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

  function scan(root) {
    const roots = root ? [root] : [document.getElementById("main"), document.getElementById("drawer-body")]
      .filter((r) => r && r.getClientRects().length);
    const view = currentView();
    const index = sourceIndex();
    const range = document.createRange();
    const found = new Map();
    const add = (kind, el, text) => {
      const t = clip(tidy(text));
      if (!t) return;
      const key = kind + "|" + view + "|" + t;
      if (!found.has(key)) found.set(key, { kind, text: t, view, source: sourceOf(index, el), key, el: describe(el) });
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
          if (broken.length) add("broken_word", el, whole);
        }
        if (seen.has(el) || !ownText(el)) continue;
        seen.add(el);
        const cs = getComputedStyle(el);
        if (cutOff(el, cs)) add("cut_off", el, whole);
        else if (spills(el, cs, range)) add("spills", el, whole);
      }
    });
    lastFound = [...found.values()];
    return lastFound;
  }

  /* ---- Telling hb serve ------------------------------------------------------------ */

  // Each tab's whole scan, sent when it differs from what that tab last sent (an empty one too, so a
  // fixed name drops off the list).
  function report(problems) {
    if (!HB.local || typeof HB.local.post !== "function") return;
    const view = currentView();
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
    const what = { broken_word: "a word breaks across two lines", cut_off: "is cut off", spills: "runs out of its box" };
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

  let timer = null;
  function later() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const run = () => report(scan());
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

  HB.layoutCheck = { scan, watch, last: () => lastFound, all, cached, promptBlock };
})(window.HB = window.HB || {});
