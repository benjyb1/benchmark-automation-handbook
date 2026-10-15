/* =============================================================================
   Handbook viewer: views/feedback.js, the Feedback tab (HB.views.feedback).
   The whole round with the parties that were assessed (companies, countries, schemes): send each
   its results as an Excel pack, read what comes back, and decide each point it raises.

   What the tab shows, top to bottom: the title with "How does this tab work?" and "Make packs";
   the round's settings as chips (each opens the corner chat with a question about it); one drop
   zone for reply files and attachments; files waiting for the owner (not matched to anyone, or
   covering a whole group); six counts by stage, each a filter; then one card per group (a stacked
   bar of where its units are) with a row per unit (a bar of six segments and a line of words).
   A row opens the unit's panel in the drawer, at #feedback~<unit id>: what to do at its stage,
   each point it raised, and its files and stage.

   A point shows "Our answer" (score, what the level means, our quote and source) beside "What they
   say" (their words, link, attachments), the question's levels as radio options with ours marked,
   the effect of the chosen level on the overall score and tier (worked out by the server, nothing
   written), a reason that goes back to them, and a Keep or "Change to" button. A decided point reads
   "Changed: 0 to 5" with a link to take it back. "Ask Claude to check this" is the only place Claude is
   called here, and only when it is clicked; it says what it used.

   Everything it changes goes through the page's own server (HB.local.post, /api/feedback/..., see
   serve.py): each call writes, rebuilds, and answers with the round as the page shows it, which is
   put in the bundle at once. On a page that is not the local one (HB.local not set: a claude.ai copy),
   the round is shown read-only with one line saying replies are handled in the tool on the owner's
   computer; every button that changes anything is left out.

   Generic: the unit's word, the labels, the levels and the score sheet's bands all come from the
   bundle. The tab is shown on the local page always, elsewhere only once the round has started
   (bundle.feedback).

   HB.feedbackTab = { stageLine, groupsOf, settingWords, summarise }  (pure helpers, for the tests)
   ========================================================================== */

(function (HB) {
  "use strict";

  const { h, fmtInt, fmtNum } = HB;
  const SHORT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const LONG = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
  const REPLY_WORDS = { Agree: "Agrees", Disagree: "Disagrees" };
  // A round made for a demo, with invented replies (settings.sample, set only by handbook/dev/fixture_feedback.py).
  const SAMPLE = "Sample replies, invented for this demo. No company sent these.";
  const READ_ONLY = "Replies are handled in the benchmark tool on the owner's computer, so this copy only shows how the round is going.";

  // The page-wide state of this tab (kept while the page is open; nothing here is saved).
  const ui = {
    fetched: null,        // the round, fetched when the bundle has none yet (the local page, before any pack)
    fetching: false,
    tried: false,         // the round has been asked for once (no endless retries)
    filter: null,         // a stage id the groups are narrowed to
    shut: new Set(),      // group names folded away
    busy: null,           // words for what is happening, or null
    error: null,          // the last thing that went wrong, in words
    make: null,           // null | {step: "ask"} | {step: "done", result, marked}
    drop: null,           // {tone, text}: what the last drop did
    flash: null,          // {ids: Set, until: ms}: the rows to flash after a drop
    pend: {},             // "unit|q" -> the level value picked, not decided yet
    drafts: {},           // "unit|q" -> the reason typed so far
    previews: {},         // "unit|q|value" -> {state, data}
    impact: {},           // "unit|q" -> {el, slot}: the line waiting for a preview
    notes: {},            // "unit|q" -> a line after asking Claude
    confirm: null,        // "restart:<unit>" | "remove:<file>" | "others:<file>": the click that must be repeated
    more: null,           // the file id whose "use for others too" chooser is open
    waiting: null,        // {unit, files}: files dropped on a unit that already has a reply
    panelUnit: null,      // the unit shown in the drawer
    quiet: false,         // the tab, not the reader, is closing the drawer
    hooked: false,
  };

  /* ---- Reading the data ----------------------------------------------------------------- */

  const bundle = () => (HB.data && HB.data.bundle()) || {};
  const arr = (v) => (Array.isArray(v) ? v : []);
  const isLocal = () => Boolean(HB.local && typeof HB.local.post === "function");
  const round = () => bundle().feedback || ui.fetched || null;
  const isSample = (info) => Boolean(info && info.settings && info.settings.sample === true);
  const sampleLine = (info) => (isSample(info) ? HB.notice("warning", h("strong", null, SAMPLE)) : null);
  const project = () => bundle().project || {};
  const one = () => project().unit_word || "row";
  const many = () => project().unit_word_plural || one() + "s";
  const unitsOf = () => arr(bundle().units);
  const unitById = (id) => unitsOf().find((u) => u && u.id === id) || null;
  const nameOf = (id) => (unitById(id) || {}).name || String(id);
  const words = (n) => (HB.data && HB.data.unitWords ? HB.data.unitWords(n) : n + " " + (n === 1 ? one() : many()));
  const same = (a, b) => a != null && b != null && String(a) === String(b);
  const sortByName = (list) => list.slice().sort((a, b) => String(a.name).localeCompare(String(b.name), "en-GB", { numeric: true, sensitivity: "base" }));

  function when(iso, format) {
    const d = new Date(String(iso || ""));
    return Number.isNaN(d.getTime()) ? "" : (format || SHORT).format(d);
  }

  function stagesOf(info) {
    const list = arr(info && info.stages);
    return list.length ? list : [
      { id: "answers_to_check", label: "Answers to check" }, { id: "ready_to_send", label: "Ready to send" },
      { id: "sent", label: "Sent" }, { id: "returned", label: "Returned" },
      { id: "needs_review", label: "Needs review" }, { id: "reviewed", label: "Reviewed" }];
  }

  const stageLabel = (info, id) => (stagesOf(info).find((s) => s.id === id) || {}).label || String(id);
  const stageIndex = (info, id) => Math.max(0, stagesOf(info).findIndex((s) => s.id === id));

  /** The overall score as the Grid writes it: "67%" when the score sheet works in percents. */
  function overallText(value) {
    const sheet = bundle().scoresheet || {};
    const overall = sheet.overall || {};
    const pct = overall.method === "weighted_mean_of_section_percents" || overall.percent === true;
    return typeof value === "number" && Number.isFinite(value) ? fmtNum(value, 1) + (pct ? "%" : "") : "no score yet";
  }

  function totalsOf(uid) {
    const t = (bundle().totals || {})[uid];
    return t ? { overall: t.overall, band: (t.bands || {}).overall || null } : { overall: null, band: null };
  }

  function levelsOf(uid, qid) {
    const q = HB.data.questionFor ? HB.data.questionFor(uid, qid) : HB.data.question(qid);
    return q && q.answer && Array.isArray(q.answer.levels) ? q.answer.levels : [];
  }

  const levelOf = (levels, value) => levels.find((l) => same(l.value, value)) || null;
  const topPoints = (levels) => levels.reduce((m, l) => Math.max(m, Number(l.points) || 0), 0);
  const pointsText = (levels, value) => {
    const lv = levelOf(levels, value);
    return lv ? fmtNum(lv.points, 2) + " of " + fmtNum(topPoints(levels), 2) : "no answer";
  };

  /** Where a unit stands, in words (the second line of its row and the top of its panel). */
  function stageLine(info, uid) {
    const u = (info.units || {})[uid] || {};
    const cells = (bundle().cells || {})[uid] || {};
    const total = Object.keys(cells).length;
    const counts = u.counts || {};
    let text;
    switch (u.stage) {
      case "answers_to_check":
        text = u.unchecked + " of " + total + " answers not checked";
        break;
      case "ready_to_send":
        text = u.pack_made ? "Pack made " + when(u.pack_made) : "All answers checked";
        break;
      case "sent": {
        const by = (info.settings || {}).reply_by;
        text = "Sent " + when(u.sent) + (by ? " · reply by " + when(by + "T00:00:00Z", LONG) : "");
        break;
      }
      case "returned":
        text = arr(u.files).length ? plural(arr(u.files).length, "file") + " came back, no filled-in reply sheet" : "Reply has arrived";
        break;
      case "needs_review":
        text = counts.points ? plural(counts.points, "point") + " raised · " + (counts.points - counts.decided) + " to decide" : "Waiting for you to look";
        break;
      case "reviewed":
        text = counts.points ? counts.changed + " changed, " + counts.kept + " kept" : "Returned with no points raised";
        break;
      default:
        text = "";
    }
    return text + (u.by_hand ? " · stage set by hand" : "");
  }

  function plural(n, word, pluralWord) {
    return fmtInt(n) + " " + (n === 1 ? word : pluralWord || word + "s");
  }

  /** The label the groups are made by: the packs' grouping when there is one; else the label that
      sorts the units into the fewest groups of more than one (so the cards are full, not one row each);
      else the Grid's. */
  function groupLabel(info) {
    const labels = arr(project().labels);
    const g = (info.settings || {}).grouping;
    if (g && g !== "none" && labels.includes(g)) return g;
    const sizes = labels.map((label) => {
      const seen = new Map();
      unitsOf().forEach((u) => { const v = u.labels && u.labels[label]; if (v) seen.set(v, (seen.get(v) || 0) + 1); });
      return { label, groups: seen.size, big: Array.from(seen.values()).filter((n) => n > 1).length };
    }).filter((s) => s.big > 0);
    if (sizes.length) return sizes.sort((a, b) => a.groups - b.groups)[0].label;
    const state = HB.state || {};
    if (state.groupBy && labels.includes(state.groupBy)) return state.groupBy;
    return labels.length ? labels[0] : null;
  }

  /** [{name, units: [unit...], counts: {stage: n}}], the furthest on first. */
  function groupsOf(info) {
    const label = groupLabel(info);
    const by = new Map();
    unitsOf().forEach((u) => {
      const key = (label && u.labels && u.labels[label]) || (label ? "Other" : "All " + many());
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(u);
    });
    const list = Array.from(by.entries()).map(([name, units]) => {
      const counts = {};
      units.forEach((u) => {
        const stage = ((info.units || {})[u.id] || {}).stage || "answers_to_check";
        counts[stage] = (counts[stage] || 0) + 1;
      });
      const done = units.reduce((s, u) => s + stageIndex(info, ((info.units || {})[u.id] || {}).stage), 0) / units.length;
      return { name, units: sortByName(units), counts, done };
    });
    return list.sort((a, b) => b.done - a.done || String(a.name).localeCompare(String(b.name), "en-GB"));
  }

  /** The chips: [{key, label, value, question}] */
  function settingWords(info) {
    const s = info.settings || {};
    const label = s.grouping && s.grouping !== "none" ? s.grouping : null;
    return [
      { key: "packs", label: "Packs", value: label ? "one per " + label + ", a tab for each " + one() : "one per " + one(),
        question: "How are the packs split up?" },
      { key: "claude", label: "Claude", value: { check: "checks every reply", decide: "checks and suggests decisions" }[s.review] || "only when you ask",
        question: "What does Claude do with the replies?" },
      { key: "auto", label: "Auto-accept", value: { no_tier_change: "changes that don't move a tier", all: "everything" }[s.auto_accept] || "off",
        question: "Can some changes go through automatically?" },
      { key: "reasons", label: "Reasons in packs", value: s.reasons === "plain" ? "plain wording" : "analyst notes",
        question: "What are the reasons in the packs?" },
    ];
  }

  /** What a drop did, in a sentence or two: {tone, text}. */
  function summarise(result, files) {
    const list = arr(result && result.files);
    const matched = list.filter((f) => f.status === "matched");
    const notes = [];
    const HOW = { name: "by file name", pack: "by the details inside the file", group: "by their group", hand: "as you said" };
    Object.keys(HOW).forEach((via) => {
      const ids = [];
      matched.filter((f) => f.via === via).forEach((f) => arr(f.units).forEach((u) => { if (!ids.includes(u)) ids.push(u); }));
      if (ids.length) notes.push("Matched " + HOW[via] + ": " + (ids.length > 4 ? words(ids.length) : ids.map(nameOf).join(", ")) + ".");
    });
    const attached = matched.filter((f) => f.kind === "attachment").length;
    if (attached) notes.push(plural(attached, "file was an attachment", "files were attachments") + ".");
    const old = matched.filter((f) => f.old_pack).length;
    if (old) notes.push(plural(old, "reply is", "replies are") + " to an older pack: we have changed an answer since.");
    const dup = list.filter((f) => f.status === "duplicate").length;
    if (dup) notes.push(plural(dup, "file was", "files were") + " already here.");
    const waiting = list.filter((f) => f.status === "unmatched" || f.status === "group").length;
    if (waiting) notes.push(plural(waiting, "file needs", "files need") + " you: see below.");
    list.filter((f) => f.status === "error").forEach((f) => notes.push(f.file + ": " + f.reason));
    const errs = arr(result && result.errors);
    errs.forEach((e) => notes.push("Claude could not read " + nameOf(e.unit) + "'s reply: " + e.error));
    if (!notes.length) notes.push("Nothing was read.");
    const tone = waiting || list.some((f) => f.status === "error") || errs.length ? "warning" : "good";
    return { tone, text: (files ? "Read " + plural(files, "file") + ". " : "") + notes.join(" "), units: arr(result && result.units) };
  }

  /* ---- Talking to the server --------------------------------------------------------------- */

  const refresh = () => { if (typeof HB.render === "function") HB.render(); };

  /** POST /api/feedback/<path>. Puts the round it answers with in the bundle, draws again, and
      returns the answer (null when it failed: ui.error then says why). One call at a time. */
  async function act(path, body, busy) {
    if (ui.busy || !isLocal()) return null;
    ui.busy = busy || "Working on it...";
    ui.error = null;
    refresh();
    let res = null;
    try {
      res = await HB.local.post("feedback/" + path, body || {});
      if (res && res.feedback) {
        const b = HB.data.bundle();
        if (b) b.feedback = res.feedback;
      }
      ui.previews = {};
    } catch (e) {
      ui.error = e && e.message ? e.message : "That did not work.";
    }
    ui.busy = null;
    refresh();
    if (res && HB.local.refresh) HB.local.refresh();
    return res;
  }

  /** The preview of a change, asked for once per figure. When it arrives it is written into the line
      that waits for it, so a reason being typed is not disturbed. */
  function preview(uid, qid, value, pointKey) {
    const key = uid + "|" + qid + "|" + String(value);
    if (ui.previews[key]) return ui.previews[key];
    const slot = { state: "loading", data: null };
    ui.previews[key] = slot;
    HB.local.post("feedback/preview", { unit: uid, question: qid, value }).then((res) => {
      slot.state = "ok";
      slot.data = res && res.result;
    }, () => { slot.state = "error"; }).then(() => {
      const waiting = ui.impact[pointKey];
      if (waiting && waiting.slot === slot && waiting.el.isConnected) waiting.el.replaceChildren(...[].concat(impactWords(slot)).flat().filter((x) => x != null));
      else if (ui.panelUnit === uid) refresh();
    });
    return slot;
  }

  function impactWords(slot) {
    if (slot.state === "loading") return "Working out what this changes...";
    if (slot.state === "error" || !slot.data) return "The effect could not be worked out.";
    const d = slot.data;
    return ["With this change: ", overallText(d.before.overall), " → ", h("b", null, overallText(d.after.overall)),
      d.after.band !== d.before.band && d.after.band ? [", ", d.before.band || "no tier", " → ", h("b", null, d.after.band)] : null];
  }

  async function loadRound() {
    if (ui.fetching || ui.tried || !isLocal() || bundle().feedback) return;
    ui.fetching = true;
    ui.tried = true;
    try {
      const res = await HB.local.get("feedback/status");
      if (!res.ok) throw new Error("the server said " + res.status);
      const data = await res.json();
      ui.fetched = data && data.feedback ? data.feedback : null;
    } catch (e) {
      ui.error = "The round could not be loaded: " + (e && e.message ? e.message : e);
    }
    ui.fetching = false;
    refresh();
  }

  /** The corner chat can be asked (it is there and Claude can be reached). */
  const chatOn = () => Boolean(HB.chat && typeof HB.chat.open === "function" && typeof HB.chat.canAsk === "function" && HB.chat.canAsk());

  function askChat(text) {
    if (!HB.chat || typeof HB.chat.open !== "function") return;
    HB.chat.open();
    if (typeof HB.chat.send === "function") HB.chat.send(text);
  }

  /* ---- Files: upload, then read ---------------------------------------------------------------- */

  const extOf = (name) => (String(name).toLowerCase().match(/\.([a-z0-9]+)$/) || [null, ""])[1];

  async function upload(files) {
    const assets = await window.claude.use("assets");
    const sent = [];
    const failed = [];
    for (const file of files) {
      const type = HB.uploadTypes[extOf(file.name)] || file.type || "application/octet-stream";
      try {
        const up = await assets.upload(file, { type });
        sent.push({ asset: up.id, name: file.name });
      } catch (e) {
        failed.push(file.name + ": " + (e && e.message ? e.message : "it could not be sent"));
      }
    }
    return { sent, failed };
  }

  /** Files dropped (or chosen): read them, for `unit` when the owner said whose they are. */
  async function takeFiles(list, opts) {
    const files = Array.from(list || []).filter((f) => f && f.name);
    if (!files.length || ui.busy || !isLocal()) return;
    const o = opts || {};
    ui.waiting = null;
    ui.busy = "Reading " + plural(files.length, "file") + "...";
    ui.error = null;
    refresh();
    let note = null;
    try {
      const up = await upload(files);
      if (!up.sent.length) {
        note = { tone: "warning", text: up.failed.join(" ") };
      } else {
        const res = await HB.local.post("feedback/replies", { files: up.sent, unit: o.unit || null, replace: Boolean(o.replace) });
        if (res && res.feedback) {
          const b = HB.data.bundle();
          if (b) b.feedback = res.feedback;
        }
        ui.previews = {};
        const said = summarise(res.result, up.sent.length);
        if (up.failed.length) said.text += " " + up.failed.join(" ");
        note = said;
        ui.flash = { ids: new Set(said.units), until: Date.now() + 2200 };
      }
    } catch (e) {
      note = { tone: "critical", text: e && e.message ? e.message : "The files could not be read." };
    }
    ui.drop = note;
    ui.busy = null;
    refresh();
    if (HB.local.refresh) HB.local.refresh();
  }

  /** Files dropped on a unit's panel: straight in when it has no reply yet, else ask what to do. */
  function dropOnUnit(uid, list) {
    const files = Array.from(list || []).filter((f) => f && f.name);
    if (!files.length) return;
    const has = arr((((round() || {}).units || {})[uid] || {}).files).some((f) => f.kind === "reply");
    if (has) {
      ui.waiting = { unit: uid, files };
      refresh();
    } else {
      takeFiles(files, { unit: uid });
    }
  }

  /* ---- Small pieces ----------------------------------------------------------------------------- */

  function dropZone(opts) {
    const o = opts || {};
    const input = h("input", { type: "file", multiple: true, class: "visually-hidden", id: o.id || "fb-file",
      accept: HB.uploadAccept,
      onchange: () => { o.take(input.files); input.value = ""; } });
    const zone = h("label", { class: ["fb-drop", o.small && "fb-drop--small"], for: o.id || "fb-file", "data-busy": String(Boolean(ui.busy)) },
      input,
      h("span", { class: "fb-drop__lead" }, o.lead),
      o.hint ? h("span", { class: "fb-drop__hint" }, o.hint) : null);
    const over = (on) => (e) => { e.preventDefault(); zone.classList.toggle("is-over", on); };
    zone.addEventListener("dragenter", over(true));
    zone.addEventListener("dragover", over(true));
    zone.addEventListener("dragleave", over(false));
    zone.addEventListener("drop", (e) => { over(false)(e); if (e.dataTransfer && e.dataTransfer.files) o.take(e.dataTransfer.files); });
    return zone;
  }

  const button = (label, onclick, opts) => {
    const o = opts || {};
    return h("button", { type: "button", class: ["btn", o.small !== false && "btn--small", o.primary && "btn--primary", o.quiet && "btn--quiet", o.danger && "fb-danger"],
      disabled: Boolean(o.disabled || (ui.busy && !o.always)), "data-fbk": o.key || null, title: o.title || null, onclick }, label);
  };

  const linkButton = (label, onclick, key) => h("button", { type: "button", class: "fb-link", disabled: Boolean(ui.busy), "data-fbk": key || null, onclick }, label);

  function notice(tone, content) {
    return HB.notice(tone, content);
  }

  /* ---- The tab ------------------------------------------------------------------------------------ */

  function head() {
    const local = isLocal();
    return h("div", { class: "fb-head" },
      h("h2", null, "Feedback"),
      h("div", { class: "fb-head__tools" },
        HB.chat ? button("How does this tab work?", () => askChat("How does this tab work?"), { always: true }) : null,
        // With the chat on, Make packs opens it: Claude says how many are ready and how many have answers
        // nobody has checked, then proposes making them. Without the chat, the card below does the same.
        local ? button("Make packs", () => { if (chatOn()) askChat("Make packs"); else { ui.make = { step: "ask" }; refresh(); } }, { primary: true, small: false, key: "make" }) : null));
  }

  function chips(info) {
    const live = HB.chat && typeof HB.chat.open === "function";
    return h("div", { class: "fb-chips", role: "group", "aria-label": "How the round is set up" },
      settingWords(info).map((c) => {
        const inner = [c.label + ": ", h("b", null, c.value)];
        return live ? h("button", { type: "button", class: "fb-chip", title: "Ask Claude about this", onclick: () => askChat(c.question) }, inner)
          : h("span", { class: "fb-chip fb-chip--plain" }, inner);
      }));
  }

  function makeCard(info) {
    if (!ui.make) return null;
    const counts = info.counts || {};
    const ready = counts.ready_to_send || 0;
    const check = counts.answers_to_check || 0;
    const all = unitsOf().length;
    const setting = settingWords(info)[0].value;
    const idsAt = (...stages) => unitsOf().filter((u) => stages.includes(((info.units || {})[u.id] || {}).stage)).map((u) => u.id);
    const make = (scope, ids, label) => act("packs", { scope, units: ids }, label).then((res) => {
      if (res) ui.make = { step: "done", result: res.result, marked: false };
      else ui.make = { step: "ask" };
      refresh();
    });
    const close = () => { ui.make = null; refresh(); };
    if (ui.make.step === "done") {
      const r = ui.make.result || {};
      const madeUnits = arr(r.packs).flatMap((p) => arr(p.units));
      const warned = Object.keys(r.warnings || {});
      return h("section", { class: "fb-card", "aria-label": "Packs made" },
        h("p", { class: "fb-card__lead" }, h("strong", null, plural(arr(r.packs).length, "pack") + " made."),
          " They are in this folder on your computer:"),
        h("p", null, h("code", { class: "fb-path" }, r.path || r.folder)),
        warned.length ? h("p", { class: "small" }, plural(warned.length, "of them went", "of them went") + " out with answers still to check (" +
          warned.slice(0, 4).map(nameOf).join(", ") + (warned.length > 4 ? " and " + (warned.length - 4) + " more" : "") + "). The packs show the answers as they are.") : null,
        arr(r.skipped).length ? h("p", { class: "small" }, arr(r.skipped).map((s) => "Skipped " + nameOf(s.unit) + ": " + s.why).join(" ")) : null,
        h("p", { class: "small muted" }, "Email them from your own account, however suits you. Then mark them as sent, so the page can tell what is waiting for a reply."),
        h("div", { class: "fb-actions" },
          button("Show the folder", () => act("open-folder", { folder: r.folder }, "Opening the folder...").then((res) => {
            if (res && res.result && !res.result.opened) ui.error = "The folder could not be opened. It is at " + res.result.path;
            refresh();
          }), { key: "folder" }),
          ui.make.marked ? h("span", { class: "small" }, "Marked as sent.")
            : button("Mark these as sent", () => act("mark-sent", { units: madeUnits }, "Marking them as sent...").then((res) => {
              if (res) { ui.make.marked = true; refresh(); }
            }), { primary: true, key: "mark" }),
          button("Done", close, { quiet: true, always: true })));
    }
    const opts = [];
    let lead;
    if (ready + check === 0) {
      lead = "Every " + one() + " already has a pack, or is further on. You can make new ones with the answers as they are now.";
      opts.push(button("Make packs for all " + words(all), () => make("all", null, "Making packs..."), { primary: true, key: "all" }));
    } else {
      lead = plural(ready, one() + " is", many() + " are") + " ready to send. " +
        (check ? plural(check, one() + " still has", many() + " still have") + " answers nobody has checked. " : "") + "Packs: " + setting + ".";
      if (ready) opts.push(button("Make the " + ready + " that " + (ready === 1 ? "is" : "are") + " ready", () => make("units", idsAt("ready_to_send"), "Making packs..."), { primary: true, key: "ready" }));
      if (check) {
        opts.push(button(ready ? "Make all " + (ready + check) + " anyway" : "Make " + (check === 1 ? "it" : "them") + " anyway",
          () => make("units", idsAt("ready_to_send", "answers_to_check"), "Making packs..."), { primary: !ready, key: "anyway" }));
        opts.push(button("Show me the " + check + " to check", () => { ui.filter = "answers_to_check"; ui.make = null; refresh(); }, { quiet: true }));
      }
    }
    return h("section", { class: "fb-card", "aria-label": "Make packs" },
      h("p", { class: "fb-card__lead" }, lead),
      h("div", { class: "fb-actions" }, opts, button("Cancel", close, { quiet: true, always: true })));
  }

  function dropBlock(info) {
    const said = ui.drop;
    return h("section", { class: "fb-dropbox", "aria-label": "Replies" },
      dropZone({ id: "fb-file", take: (files) => takeFiles(files), lead: [h("strong", null, "Drop their replies here"), " or ", h("span", { class: "fb-drop__pick" }, "choose files")],
        hint: "Their filled-in spreadsheets and anything they attached, all at once. Each goes to the " + one() + " named in its file name." }),
      said ? h("p", { class: "fb-said", "data-tone": said.tone, role: "status" }, said.text, " ",
        h("button", { type: "button", class: "fb-link", onclick: () => { ui.drop = null; refresh(); } }, "Dismiss")) : null);
  }

  function waitingFiles(info) {
    const loose = arr(info.unmatched);
    const groups = arr(info.groups);
    if (!loose.length && !groups.length) return null;
    const options = sortByName(unitsOf());
    const pick = (fileId) => h("select", { class: "input fb-select", "aria-label": "Choose the " + one() + " this file is for", disabled: Boolean(ui.busy),
      onchange: (e) => { if (e.target.value) act("move-file", { file: fileId, units: [e.target.value] }, "Moving the file..."); } },
    h("option", { value: "" }, "Choose the " + one() + "..."), options.map((u) => h("option", { value: u.id }, u.name)));
    return h("section", { class: "fb-waiting", "aria-label": "Files waiting for you" },
      h("h3", null, "Files waiting for you"),
      h("ul", { class: "fb-files" },
        loose.map((f) => h("li", { class: "fb-file" },
          h("span", { class: "fb-file__name" }, f.name),
          h("span", { class: "fb-file__why small muted" }, f.reason || "It was not matched to anyone."),
          arr(f.guess).length ? h("span", { class: "fb-file__guess" }, arr(f.guess).map((u) =>
            button("It is " + nameOf(u) + "'s", () => act("move-file", { file: f.id, units: [u] }, "Moving the file..."), { key: "guess-" + f.id + u }))) : null,
          pick(f.id),
          chatOn() ? button("Ask Claude whose it is", () => askChat("Whose is the file \"" + f.name + "\"?"), { quiet: true, key: "ask-" + f.id }) : null,
          button("Remove", () => act("remove-file", { file: f.id }, "Removing the file..."), { quiet: true }))),
        groups.map((f) => h("li", { class: "fb-file" },
          h("span", { class: "fb-file__name" }, f.name),
          h("span", { class: "fb-file__why small muted" }, "It looks like a reply for " + (f.group || {}).value + ", which has " + words(arr(f.units).length) + "."),
          button("Use it for all " + arr(f.units).length, () => act("confirm-group", { file: f.id }, "Filing the file..."), { primary: true, key: "group-" + f.id }),
          pick(f.id),
          button("Remove", () => act("remove-file", { file: f.id }, "Removing the file..."), { quiet: true })))));
  }

  function tiles(info) {
    const counts = info.counts || {};
    return h("div", { class: "fb-tiles", role: "group", "aria-label": "Show only" },
      stagesOf(info).map((s, i) => h("button", { type: "button", class: "fb-tile", "aria-pressed": String(ui.filter === s.id), "data-stage": s.id,
        onclick: () => { ui.filter = ui.filter === s.id ? null : s.id; refresh(); } },
      h("span", { class: "fb-tile__n" }, fmtInt(counts[s.id] || 0)),
      h("span", { class: "fb-tile__l" }, h("span", { class: "fb-sw", style: { background: "var(--fb-" + i + ")" }, "aria-hidden": "true" }), s.label))));
  }

  function groupCard(info, g) {
    const stages = stagesOf(info);
    const shown = g.units.filter((u) => !ui.filter || ((info.units || {})[u.id] || {}).stage === ui.filter);
    if (!shown.length) return null;
    const shut = ui.shut.has(g.name) && !ui.filter;
    const flash = ui.flash && Date.now() < ui.flash.until ? ui.flash.ids : new Set();
    const stack = h("span", { class: "fb-stack", "aria-hidden": "true" }, stages.map((s, i) => g.counts[s.id]
      ? h("span", { style: { width: (100 * g.counts[s.id] / g.units.length) + "%", background: "var(--fb-" + i + ")" }, title: g.counts[s.id] + " " + s.label }) : null));
    const summary = stages.filter((s) => g.counts[s.id]).map((s) => g.counts[s.id] + " " + s.label.toLowerCase()).join(", ");
    return h("section", { class: ["fb-group", shut && "is-shut"], "data-group": g.name },
      h("button", { type: "button", class: "fb-ghead", "aria-expanded": String(!shut), onclick: () => { if (ui.shut.has(g.name)) ui.shut.delete(g.name); else ui.shut.add(g.name); refresh(); } },
        h("span", { class: "fb-caret", "aria-hidden": "true" }), h("span", { class: "fb-gname" }, g.name),
        h("span", { class: "fb-gcount" }, words(g.units.length)), stack,
        h("span", { class: "visually-hidden" }, summary)),
      shut ? null : h("div", { class: "fb-rows" }, shown.map((u) => {
        const rec = (info.units || {})[u.id] || {};
        const at = stageIndex(info, rec.stage);
        return h("button", { type: "button", class: ["fb-row", flash.has(u.id) && "fb-flash"], id: "fb-row-" + u.id, "data-unit": u.id,
          "aria-label": u.name + ": " + stageLabel(info, rec.stage) + ". " + stageLine(info, u.id),
          onclick: () => HB.go("feedback", u.id) },
        h("span", { class: "fb-nm" }, u.name),
        h("span", { class: "fb-pbar", "aria-hidden": "true" }, stages.map((s, i) => h("i", { style: i <= at ? { background: "var(--fb-" + at + ")" } : null }))),
        h("span", { class: "fb-st" }, stageLabel(info, rec.stage), h("span", { class: "fb-sub" }, stageLine(info, u.id))));
      })));
  }

  function groups(info) {
    const list = groupsOf(info).map((g) => groupCard(info, g)).filter(Boolean);
    if (list.length) return h("div", { class: "fb-groups" }, list);
    return h("p", { class: "muted" }, ui.filter ? "Nothing is at \"" + stageLabel(info, ui.filter) + "\" just now." : "There is nothing to show yet.");
  }

  /* ---- The unit's panel ------------------------------------------------------------------------------- */

  function sideOurs(uid, p, levels) {
    const cell = HB.data.cell(uid, p.q) || {};
    const value = baseValue(p);
    const docs = bundle().documents || {};
    const lv = levelOf(levels, value);
    const evidence = arr(cell.evidence);
    return h("div", { class: "fbp-side" },
      h("h4", null, "Our answer"),
      h("div", { class: "fbp-score" }, pointsText(levels, value)),
      lv && lv.meaning ? h("div", { class: "fbp-mean" }, lv.meaning) : null,
      evidence.length ? evidence.slice(0, 1).map((e) => h("div", null,
        h("blockquote", { tabindex: "0" }, e.quote_en || e.quote || ""),
        h("div", { class: "fbp-src" }, ((docs[e.doc] || {}).title || e.doc || "") + (e.page ? ", p." + e.page : ""))))
        : h("p", { class: "fbp-mean" }, "No evidence found."),
      h("p", { class: "fbp-mean" }, evidence.length > 1 ? "And " + (evidence.length - 1) + " more. " : "",
        linkButton("See it in the Grid", () => HB.openCell(uid, p.q), "grid-" + p.q)));
  }

  function sideTheirs(uid, p, files) {
    const attached = files.filter((f) => f.kind === "attachment");
    const versions = arr(p.versions);
    const fileName = (id) => (files.find((f) => f.id === id) || {}).name || "a file";
    return h("div", { class: "fbp-side fbp-side--theirs" },
      h("h4", null, "What they say · " + (REPLY_WORDS[p.reply] || p.reply)),
      p.conflict && versions.length > 1
        ? h("div", null, h("p", { class: "fbp-flag" }, "Two of their files say different things here. The newest is first."),
          versions.slice().reverse().map((v) => h("div", { class: "fbp-version" },
            h("div", { class: "fbp-src" }, fileName(v.file)), h("p", null, v.why || "(no words)"),
            v.link ? h("div", { class: "fbp-src" }, HB.link(v.link), v.where ? " " + v.where : "") : null)))
        : h("div", null, h("p", { class: "fbp-why" }, p.why || "(no words)"),
          p.link ? h("div", { class: "fbp-src" }, HB.link(p.link), p.where ? " " + p.where : "") : (p.where ? h("div", { class: "fbp-src" }, p.where) : null)),
      attached.length ? h("div", { class: "fbp-src" }, "Files they sent with it: " + attached.map((f) => f.name).join(", ")) : null,
      attached.some((f) => f.doc_error) ? h("div", { class: "fbp-src" }, "Claude can read PDF and text files. A file of another kind needs saving as a PDF to be used as evidence.") : null);
  }

  function claudeBlock(uid, p, levels) {
    const r = p.review;
    if (!r || r.error) return null;
    const top = topPoints(levels);
    const to = r.if_confirmed != null ? pointsText(levels, r.if_confirmed) : null;
    const pickIt = r.recommend ? button("Use this suggestion", () => {
      const key = uid + "|" + p.q;
      ui.pend[key] = r.recommend === "change" ? r.if_confirmed : baseValue(p);
      if (r.reason && !ui.drafts[key]) ui.drafts[key] = r.reason;
      refresh();
    }, { key: "use-" + p.q }) : null;
    return h("div", { class: "fbp-claude" },
      h("b", null, "Claude's check: "),
      r.changes_score && to ? "if this holds up, it would be " + to + ". " : "this would not change the score. ", r.check || "",
      r.doubt ? h("div", { class: "fbp-doubt" }, r.doubt) : null,
      r.recommend ? h("div", null, "Claude suggests: " + (r.recommend === "change" ? "change to " + to : "keep our answer") + (r.reason ? ". " + r.reason : "")) : null,
      pickIt);
  }

  function pointCard(uid, p, info, files) {
    const key = uid + "|" + p.q;
    const levels = levelsOf(uid, p.q);
    const local = isLocal();
    const decision = p.decision;
    const gone = (p.flags || []).includes("question_gone") || !levels.length;
    const flags = [];
    if ((p.flags || []).includes("we_changed_since")) {
      flags.push("We changed this answer after their pack was made. Their pack showed " + pointsText(levels, p.our_value) + ".");
    }
    if (p.newer_reply) flags.push("They sent a newer reply since you decided this.");
    const card = h("div", { class: ["fbp-point", decision && !p.newer_reply && "is-done"], "data-q": p.q },
      h("div", { class: "fbp-q" }, p.q + " · " + (p.topic || ""), p.reply === "Agree, with new information" ? h("span", { class: "fbp-tag" }, "New information") : null),
      h("div", { class: "fbp-cmp" }, sideOurs(uid, p, levels), sideTheirs(uid, p, files)),
      flags.map((f) => h("p", { class: "fbp-flag" }, f)));
    if (gone) {
      card.append(h("p", { class: "fbp-flag" }, "This question no longer applies here, so it cannot be decided."));
      return card;
    }
    const block = claudeBlock(uid, p, levels);
    if (block) {
      card.append(block);
      if (ui.notes[key]) card.append(h("p", { class: "small muted" }, ui.notes[key]));
    } else if (local && !decision && p.reply !== "Agree" && (p.why || "").trim()) {
      card.append(h("p", { class: "fbp-ask" },
        linkButton("Ask Claude to check this", () => {
          act("review", { unit: uid, question: p.q }, "Claude is reading this point...").then((res) => {
            if (!res) return;
            const used = res.result && res.result.tokens;
            ui.notes[key] = used ? "Claude read it. That used about " + fmtInt(used.processed) + " tokens of your Claude plan."
              : "There was nothing in their words for Claude to check.";
            refresh();
          });
        }, "ask-" + p.q), h("span", { class: "small muted" }, " Uses some of your Claude plan."), ui.notes[key] ? h("span", { class: "small" }, " " + ui.notes[key]) : null));
    } else if (ui.notes[key]) {
      card.append(h("p", { class: "small" }, ui.notes[key]));
    }
    if (decision && !p.newer_reply) {
      const changed = decision.action === "change";
      card.append(h("p", { class: "fbp-outcome" },
        changed ? [h("b", null, "Changed: "), h("s", null, pointsOnly(levels, decision.from)), " → ", h("b", null, pointsText(levels, decision.value))]
          : h("b", null, "Kept at " + pointsText(levels, p.now_value)),
        decision.reason ? " · " + decision.reason : "", decision.auto ? " · accepted automatically" : "", " ",
        local ? linkButton("Change this decision", () => act("undo", { unit: uid, question: p.q }, "Taking the decision back..."), "undo-" + p.q) : null));
      return card;
    }
    if (!local) {
      if (decision) card.append(h("p", { class: "fbp-flag" }, "The decision on this point is out of date; open it on the owner's computer."));
      return card;
    }
    const ours = baseValue(p);
    const was = decision && decision.action === "change" ? decision.value : ours;     // a newer reply: start from what they decided
    const chosen = Object.prototype.hasOwnProperty.call(ui.pend, key) ? ui.pend[key] : was;
    const unchanged = same(chosen, ours);
    const claudeTo = p.review && p.review.changes_score ? p.review.if_confirmed : null;
    card.append(h("div", { class: "fbp-levels", role: "radiogroup", "aria-label": "Score for " + p.q },
      levels.map((l, i) => h("label", { class: [same(l.value, ours) && "is-ours"] },
        h("input", { type: "radio", name: "fbp-" + p.q, value: String(i), checked: same(l.value, chosen), disabled: Boolean(ui.busy),
          "data-fbk": "lv-" + p.q + "-" + i,
          onchange: () => { ui.pend[key] = l.value; refresh(); } }),
        h("span", { class: "fbp-pts" }, fmtNum(l.points, 2)), h("span", null, l.meaning || String(l.value)),
        h("span", { class: "fbp-note" }, same(l.value, ours) ? "our answer" : same(l.value, claudeTo) ? "Claude's check" : "")))));
    if (!unchanged && chosen != null) {
      const slot = preview(uid, p.q, chosen, key);
      const impact = h("p", { class: "fbp-impact", role: "status" }, impactWords(slot));
      ui.impact[key] = { el: impact, slot };
      card.append(impact);
    }
    const reason = h("textarea", { class: "input fbp-reason", rows: 2, id: "fbp-why-" + p.q, "data-fbk": "why-" + p.q,
      placeholder: "Why? This goes back to them.", "aria-label": "Reason for " + p.q + " (goes back to them)" });
    reason.value = ui.drafts[key] || "";
    reason.addEventListener("input", () => { ui.drafts[key] = reason.value; });
    const label = unchanged ? "Keep " + pointsText(levels, ours) : "Change to " + pointsText(levels, chosen);
    card.append(h("div", { class: "fbp-decide" }, reason,
      h("button", { type: "button", class: "btn btn--primary", "data-fbk": "decide-" + p.q, disabled: Boolean(ui.busy) || (chosen == null),
        onclick: () => {
          const reasonText = (ui.drafts[key] || reason.value || "").trim();
          act("decide", { unit: uid, question: p.q, action: unchanged ? "keep" : "change", value: unchanged ? null : chosen, reason: reasonText }, "Saving...").then((res) => {
            if (res) { delete ui.pend[key]; delete ui.drafts[key]; refresh(); }
          });
        } }, chosen == null ? "Choose a score" : label)));
    return card;
  }

  /** The answer the owner's choice is measured against: ours before any change they made. */
  const baseValue = (p) => (p.decision && p.decision.action === "change" ? p.decision.from : p.now_value);

  const pointsOnly = (levels, value) => {
    const lv = levelOf(levels, value);
    return lv ? fmtNum(lv.points, 2) : "no answer";
  };

  function stageBlock(info, uid) {
    const rec = (info.units || {})[uid] || {};
    const local = isLocal();
    const stage = rec.stage;
    const out = [];
    if (stage === "answers_to_check") {
      out.push(h("p", null, plural(rec.unchecked, "answer") + " " + (rec.unchecked === 1 ? "hasn't" : "haven't") + " been checked yet."));
      if (local) {
        out.push(h("div", { class: "fb-actions" },
          arr(rec.to_check).length ? button("Open them in the Grid", () => HB.highlight(arr(rec.to_check).map((q) => [uid, q])), { always: true }) : null,
          button("Make the pack anyway", () => act("packs", { scope: "units", units: [uid] }, "Making the pack...").then((res) => { if (res) { ui.make = { step: "done", result: res.result, marked: false }; refresh(); } }), { key: "pack" })));
      }
    } else if (stage === "ready_to_send") {
      out.push(h("p", null, "Every answer is checked. " + (rec.pack_made ? "Its pack was made " + when(rec.pack_made) + "." : "No pack has been made yet.")));
      if (local) {
        out.push(h("div", { class: "fb-actions" },
          rec.pack_made ? button("Mark as sent", () => act("mark-sent", { units: [uid] }, "Marking it as sent..."), { primary: true, key: "sent" }) : null,
          button(rec.pack_made ? "Make the pack again" : "Make the pack", () => act("packs", { scope: "units", units: [uid] }, "Making the pack...").then((res) => { if (res) { ui.make = { step: "done", result: res.result, marked: false }; refresh(); } }), { key: "pack", primary: !rec.pack_made })));
      }
    } else if (stage === "sent") {
      out.push(h("p", null, "Sent " + when(rec.sent) + "." + (((info.settings || {}).reply_by) ? " Reply by " + when(info.settings.reply_by + "T00:00:00Z", LONG) + "." : "")));
      out.push(h("p", { class: "muted" }, local ? "When their reply comes in, drop it on the page and it will come here by itself." : READ_ONLY));
      if (local) out.push(h("div", { class: "fb-actions" }, button("Make a fresh pack", () => act("packs", { scope: "units", units: [uid] }, "Making the pack...").then((res) => { if (res) { ui.make = { step: "done", result: res.result, marked: false }; refresh(); } }), { key: "pack" })));
    } else if (stage === "returned") {
      out.push(h("p", null, "Their files have come back, but none has a filled-in reply sheet, so there are no points to decide."));
    } else if ((stage === "needs_review" || stage === "reviewed") && !arr(rec.points).length) {
      out.push(h("p", null, rec.by_hand ? "The stage was set by hand, and none of their files raises a point to decide."
        : stage === "reviewed" ? "They sent their pack back with no comments." : "There is nothing to decide from their files yet."));
    }
    return out;
  }

  function filesBlock(info, uid) {
    const rec = (info.units || {})[uid] || {};
    const local = isLocal();
    const files = arr(rec.files);
    const others = sortByName(unitsOf().filter((u) => u.id !== uid));
    const label = groupLabelsOf(uid);
    const list = files.length ? files.map((f) => {
      const decidedFrom = arr(rec.points).some((p) => p.file === f.id && p.decision);
      const removeKey = "remove:" + f.id;
      const moreKey = "others:" + f.id;
      const bits = [f.kind === "reply" ? "reply" : "attachment", f.via === "hand" ? "added by you" : f.via === "pack" ? "matched by the pack details" : f.via === "group" ? "for a whole group" : null,
        f.covers ? "covers all of " + f.covers.value : null].filter(Boolean).join(" · ");
      return h("li", { class: "fb-file" },
        h("span", { class: "fb-file__name" }, f.name), h("span", { class: "fb-file__why small muted" }, bits),
        f.old_pack ? h("span", { class: "fb-file__why small" }, "A reply to an older pack: we have changed an answer since.") : null,
        f.note ? h("span", { class: "fb-file__why small" }, f.note) : null,
        f.doc_error ? h("span", { class: "fb-file__why small" }, f.doc_error) : null,
        local ? h("span", { class: "fb-file__tools" },
          h("select", { class: "input fb-select", "aria-label": "Move " + f.name + " to another " + one(), disabled: Boolean(ui.busy),
            onchange: (e) => { if (e.target.value) act("move-file", { file: f.id, units: [e.target.value] }, "Moving the file..."); } },
          h("option", { value: "" }, "Move to..."), others.map((u) => h("option", { value: u.id }, u.name))),
          button("Use for others too", () => { ui.more = ui.more === f.id ? null : f.id; refresh(); }, { key: "more-" + f.id }),
          ui.confirm === removeKey ? button("Click again to remove it and its decisions", () => { ui.confirm = null; act("remove-file", { file: f.id }, "Removing the file..."); }, { danger: true, key: "rm-" + f.id })
            : button("Remove", () => {
              if (decidedFrom) { ui.confirm = removeKey; refresh(); } else act("remove-file", { file: f.id }, "Removing the file...");
            }, { key: "rm-" + f.id })) : null,
        local && ui.more === f.id ? othersChooser(info, uid, f, label, decidedFrom, moreKey) : null);
    }) : null;
    const section = [
      h("h3", null, "Files"),
      list ? h("ul", { class: "fb-files" }, list) : h("p", { class: "muted small" }, "Nothing received yet."),
    ];
    if (local) {
      if (ui.waiting && ui.waiting.unit === uid) {
        const n = ui.waiting.files.length;
        section.push(h("div", { class: "fb-ask", role: "group", "aria-label": "What to do with the new file" },
          h("p", null, nameOf(uid) + " already has a reply. Should the new " + (n === 1 ? "file" : n + " files") + " replace it, or be added alongside it? Alongside is right when a different team filled in other rows."),
          h("div", { class: "fb-actions" },
            button("Replace their reply", () => takeFiles(ui.waiting.files, { unit: uid, replace: true }), { primary: true, key: "replace" }),
            button("Add alongside", () => takeFiles(ui.waiting.files, { unit: uid }), { key: "alongside" }),
            button("Cancel", () => { ui.waiting = null; refresh(); }, { quiet: true, always: true }))));
      }
      section.push(dropZone({ id: "fb-file-" + uid, small: true, take: (f) => dropOnUnit(uid, f),
        lead: "Drop a file here to add it to " + nameOf(uid) + ", or to replace their reply" }));
    }
    return h("div", { class: "fbp-manage" }, section);
  }

  function groupLabelsOf(uid) {
    const u = unitById(uid) || {};
    return arr(project().group_labels).filter((l) => u.labels && u.labels[l]);
  }

  function othersChooser(info, uid, f, labels, decidedFrom, moreKey) {
    const sib = [];
    labels.forEach((l) => {
      const value = (unitById(uid).labels || {})[l];
      const mates = unitsOf().filter((u) => u.id !== uid && (u.labels || {})[l] === value);
      if (mates.length) sib.push({ value, ids: mates.map((u) => u.id) });
    });
    const go = (ids) => {
      if (decidedFrom && ui.confirm !== moreKey) { ui.confirm = moreKey; refresh(); return; }
      ui.confirm = null;
      ui.more = null;
      act("move-file", { file: f.id, units: [uid].concat(ids) }, "Filing the file...");
    };
    return h("div", { class: "fb-ask", role: "group", "aria-label": "Which others" },
      h("p", null, "Which others should this file count for as well? Each is read against its own answers, and its points are decided separately."
        + (decidedFrom ? (ui.confirm === moreKey ? " Click once more to go ahead: your decisions on this file here are cleared and read again." : " Your decisions on this file here would be cleared and read again.") : "")),
      h("div", { class: "fb-actions" },
        sib.map((s) => button("All " + s.ids.length + " other" + (s.ids.length === 1 ? "" : "s") + " in " + s.value, () => go(s.ids), { key: "sib-" + s.value })),
        h("select", { class: "input fb-select", "aria-label": "Add one more " + one(), onchange: (e) => { if (e.target.value) go([e.target.value]); } },
          h("option", { value: "" }, "Pick one..."), sortByName(unitsOf().filter((u) => u.id !== uid)).map((u) => h("option", { value: u.id }, u.name))),
        button("Cancel", () => { ui.more = null; ui.confirm = null; refresh(); }, { quiet: true, always: true })));
  }

  function stagePicker(info, uid) {
    const rec = (info.units || {})[uid] || {};
    const local = isLocal();
    const out = [h("h3", null, "Stage")];
    if (!local) {
      out.push(h("p", null, stageLabel(info, rec.stage) + (rec.by_hand ? " (set by hand)" : "")));
      return h("div", { class: "fbp-manage" }, out);
    }
    out.push(h("div", { class: "fb-stagepick" },
      h("select", { class: "input fb-select", "aria-label": "Stage", disabled: Boolean(ui.busy),
        onchange: (e) => act("stage", { unit: uid, stage: e.target.value }, "Moving it...") },
      stagesOf(info).map((s) => h("option", { value: s.id, selected: s.id === rec.stage }, s.label))),
      h("span", { class: "small muted" }, "Moving the stage doesn't delete anything." + (rec.by_hand ? " It is marked as set by hand." : ""))));
    const key = "restart:" + uid;
    out.push(h("p", null, ui.confirm === key
      ? button("Click again: remove their replies and your decisions", () => { ui.confirm = null; act("restart", { unit: uid }, "Starting over..."); }, { danger: true, key: "restart" })
      : button("Start this " + one() + " over", () => { ui.confirm = key; refresh(); }, { danger: true, key: "restart" }),
    ui.confirm === key ? h("span", { class: "small muted" }, " Answers you changed go back as they were. Nothing already sent is touched.") : null));
    return h("div", { class: "fbp-manage" }, out);
  }

  function panelContent(info, uid) {
    const rec = (info.units || {})[uid] || {};
    const points = arr(rec.points);
    const files = arr(rec.files);
    const t = totalsOf(uid);
    const box = h("div", { class: "fbp" });
    if (isSample(info)) box.append(sampleLine(info));
    if (ui.error) box.append(notice("critical", ui.error));
    if (ui.busy) box.append(h("p", { class: "fb-busy", role: "status" }, ui.busy));
    box.append(h("p", { class: "muted fbp-now" }, "Overall now " + overallText(t.overall) + (t.band ? ", " + t.band : "") + "."));
    if (!isLocal()) box.append(notice("neutral", READ_ONLY));
    stageBlock(info, uid).forEach((n) => box.append(n));
    if (points.length) {
      const left = points.filter((p) => !p.decision || p.newer_reply).length;
      box.append(h("h3", { class: "fbp-h" }, "What they raised", h("span", { class: "small muted" }, " " + plural(points.length, "point") + (left ? ", " + left + " to decide" : ", all decided"))));
      points.forEach((p) => box.append(pointCard(uid, p, info, files)));
    }
    box.append(filesBlock(info, uid), stagePicker(info, uid));
    return box;
  }

  function panelClosed() {
    ui.panelUnit = null;
    if (ui.quiet) return;
    const r = HB.route();
    if (r.tab === "feedback" && r.parts.length) {
      try { location.replace(location.href.split("#")[0] + HB.href("feedback")); } catch (e) { location.hash = HB.href("feedback"); }
    }
  }

  function closePanel() {
    if (!HB.drawer || !HB.drawer.isOpen() || !ui.panelUnit) return;
    ui.quiet = true;
    HB.drawer.close();
    ui.quiet = false;
    ui.panelUnit = null;
  }

  function paintPanel(info, uid) {
    const body = typeof document !== "undefined" ? document.querySelector("#drawer .drawer__body") : null;
    const top = body && ui.panelUnit === uid ? body.scrollTop : 0;
    const active = typeof document !== "undefined" ? document.activeElement : null;
    const keep = active && active.getAttribute ? active.getAttribute("data-fbk") : null;
    const rec = (info.units || {})[uid] || {};
    ui.panelUnit = uid;
    HB.drawer.open(nameOf(uid), panelContent(info, uid), {
      kicker: stageLabel(info, rec.stage) + (stageLine(info, uid) ? " · " + stageLine(info, uid) : ""), onClose: panelClosed });
    const after = document.querySelector("#drawer .drawer__body");
    if (after && top) after.scrollTop = top;
    if (keep) {
      const el = document.querySelector('#drawer [data-fbk="' + keep + '"]');
      if (el && typeof el.focus === "function") el.focus({ preventScroll: true });
    }
  }

  /* ---- The view ----------------------------------------------------------------------------------------- */

  function hook() {
    if (ui.hooked || typeof window === "undefined" || !window.addEventListener) return;
    ui.hooked = true;
    // Moving to another tab (or back to the list) takes the unit's panel away with it.
    window.addEventListener("hashchange", () => {
      const r = HB.route ? HB.route() : { tab: null, parts: [] };
      if (ui.panelUnit && (r.tab !== "feedback" || !r.parts.length)) closePanel();
    });
  }

  function render(root, ctx) {
    hook();
    const info = round();
    const local = isLocal();
    const wrap = h("div", { class: "fb stack" });
    root.append(wrap);
    wrap.append(head());
    if (!info) {
      if (local) {
        loadRound();
        wrap.append(h("p", { class: "muted" }, "Looking at where the round is..."));
      } else {
        wrap.append(notice("neutral", READ_ONLY));
      }
      closePanel();
      return;
    }
    if (isSample(info)) wrap.append(sampleLine(info));
    if (ui.error && !ui.panelUnit) wrap.append(notice("critical", ui.error));
    if (ui.busy && !ui.panelUnit) wrap.append(h("p", { class: "fb-busy", role: "status" }, ui.busy));
    wrap.append(chips(info));
    if (!local) wrap.append(notice("neutral", READ_ONLY));
    if (local) {
      const card = makeCard(info);
      if (card) wrap.append(card);
      wrap.append(dropBlock(info));
      const waiting = waitingFiles(info);
      if (waiting) wrap.append(waiting);
    }
    wrap.append(tiles(info), groups(info));
    const uid = ctx && ctx.route && ctx.route.parts.length === 1 ? ctx.route.parts[0] : null;
    if (uid && unitById(uid) && (info.units || {})[uid]) paintPanel(info, uid);
    else closePanel();
  }

  HB.addStyles("feedback", `
.fb { --fb-0: var(--warning); --fb-1: color-mix(in srgb, var(--accent) 28%, var(--surface)); --fb-2: color-mix(in srgb, var(--accent) 46%, var(--surface));
  --fb-3: color-mix(in srgb, var(--accent) 64%, var(--surface)); --fb-4: var(--serious); --fb-5: var(--accent); gap: var(--s4); }
.fb-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--s2) var(--s4); }
.fb-head > h2 { margin: 0; }
.fb-head__tools { display: flex; flex-wrap: wrap; gap: var(--s2); }
.fb-chips { display: flex; flex-wrap: wrap; gap: var(--s2); margin: 0; }
.fb-chip { padding: 0.125rem 0.625rem; border: 1px solid var(--rule); border-radius: 999px; background: var(--surface); color: var(--ink-2); font: inherit; font-size: var(--fs-14); cursor: pointer; }
.fb-chip b { color: var(--ink); font-weight: 600; }
.fb-chip:hover { border-color: var(--accent); }
.fb-chip:focus-visible, .fb-link:focus-visible, .fb-tile:focus-visible, .fb-row:focus-visible, .fb-ghead:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.fb-chip--plain { cursor: default; }
.fb-chip--plain:hover { border-color: var(--rule); }
.fb-card { display: grid; gap: var(--s3); padding: var(--s4); border: 1px solid var(--accent); border-radius: var(--radius); background: var(--accent-soft); }
.fb-card p { margin: 0; }
.fb-card__lead { font-size: var(--fs-16); }
.fb-path { overflow-wrap: anywhere; user-select: all; }
.fb-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.fb-dropbox { display: grid; gap: var(--s2); }
.fb-drop { display: grid; justify-items: center; gap: var(--s1); padding: var(--s5) var(--s4); border: 2px dashed var(--rule-strong); border-radius: 0.75rem; background: var(--surface); color: var(--ink-2); text-align: center; cursor: pointer; transition: border-color var(--dur-fast), background-color var(--dur-fast); }
.fb-drop:hover, .fb-drop.is-over { border-color: var(--accent); background: var(--accent-soft); }
.fb-drop:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.fb-drop[data-busy="true"] { cursor: progress; }
.fb-drop__lead { color: var(--ink); font-size: var(--fs-16); }
.fb-drop__pick { color: var(--accent); font-weight: 600; text-decoration: underline; text-underline-offset: 2px; }
.fb-drop__hint { font-size: var(--fs-14); max-width: 46rem; }
.fb-drop--small { padding: var(--s3); border-width: 1px; border-style: dashed; }
.fb-drop--small .fb-drop__lead { font-size: var(--fs-14); color: var(--ink-2); }
.fb-said { margin: 0; padding: var(--s2) var(--s3); border-radius: var(--radius); background: var(--surface); border: 1px solid var(--rule); font-size: var(--fs-14); }
.fb-said[data-tone="warning"] { border-color: var(--warning); }
.fb-said[data-tone="critical"] { border-color: var(--critical); }
.fb-link { padding: 0; border: 0; background: none; color: var(--accent); font: inherit; font-size: var(--fs-14); text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.fb-link:disabled { color: var(--ink-2); cursor: default; }
.fb-busy { margin: 0; color: var(--ink-2); font-size: var(--fs-14); }
.fb-waiting { display: grid; gap: var(--s2); padding: var(--s3) var(--s4); border: 1px solid var(--warning); border-radius: var(--radius); background: var(--surface); }
.fb-waiting h3, .fbp h3 { margin: 0; font-size: var(--fs-16); }
.fb-files { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
.fb-file { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s3); padding: var(--s2) 0; border-top: 1px solid var(--rule); font-size: var(--fs-14); }
.fb-file:first-child { border-top: 0; }
.fb-file__name { flex: 1 1 12rem; min-width: 0; font-style: italic; overflow-wrap: anywhere; }
.fb-file__why { flex: 1 1 100%; }
.fb-file__tools, .fb-file__guess { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
select.input.fb-select { width: auto; min-width: 9rem; max-width: 100%; min-height: 2rem; padding: var(--s1) var(--s2); font-size: var(--fs-14); }
.fb-tiles { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--s2); }
.fb-tile { display: grid; gap: 2px; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.fb-tile[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.fb-tile__n { font-family: var(--serif); font-size: var(--fs-26); font-weight: 600; line-height: 1.1; font-variant-numeric: tabular-nums; }
.fb-tile__l { display: flex; align-items: center; gap: var(--s2); color: var(--ink-2); font-size: var(--fs-12); }
.fb-sw { flex: none; width: 0.625rem; height: 0.625rem; border-radius: 2px; }
.fb-groups { display: grid; gap: var(--s3); }
.fb-group { border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); overflow: hidden; }
.fb-ghead { display: flex; align-items: center; gap: var(--s3); width: 100%; padding: var(--s3) var(--s4); border: 0; background: none; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.fb-gname { min-width: 9rem; font-weight: 600; }
.fb-gcount { min-width: 6rem; color: var(--ink-2); font-size: var(--fs-14); }
.fb-stack { flex: 1; display: flex; height: 0.625rem; overflow: hidden; border-radius: 5px; background: var(--rule); }
.fb-stack > span { height: 100%; }
.fb-caret { width: 0; height: 0; border-inline: 5px solid transparent; border-top: 6px solid var(--ink-2); transition: transform var(--dur-fast); }
.fb-group.is-shut .fb-caret { transform: rotate(-90deg); }
.fb-row { display: grid; grid-template-columns: minmax(9rem, 15rem) minmax(8rem, 1fr) minmax(12rem, 22rem); align-items: center; gap: var(--s4); width: 100%; padding: var(--s2) var(--s4); border: 0; border-top: 1px solid var(--rule); background: none; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.fb-row:hover { background: var(--paper); }
.fb-nm { font-weight: 500; overflow-wrap: anywhere; }
.fb-pbar { display: flex; gap: 3px; }
.fb-pbar i { flex: 1; height: 0.5rem; border-radius: 2px; background: var(--rule); }
.fb-st { font-size: var(--fs-14); }
.fb-sub { display: block; color: var(--ink-2); font-size: var(--fs-12); }
.fb-flash { animation: fb-flash 1.6s; }
@keyframes fb-flash { 0% { background: var(--accent-soft); } 100% { background: transparent; } }
.fb-danger { border-color: var(--critical); color: var(--critical); }
.fb-ask { display: grid; gap: var(--s2); padding: var(--s3); border: 1px solid var(--warning); border-radius: var(--radius); background: var(--surface); font-size: var(--fs-14); }
.fb-ask p { margin: 0; }
.fb-stagepick { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.fbp { display: grid; gap: var(--s3); min-width: 0; font-size: var(--fs-14); line-height: 1.5; }
.fbp p { margin: 0; }
.fbp-now { font-size: var(--fs-14); }
.fbp-h { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s2); }
.fbp-point { display: grid; gap: var(--s2); min-width: 0; padding: var(--s3); border: 1px solid var(--rule); border-inline-start: 4px solid var(--serious); border-radius: var(--radius); }
.fbp-point.is-done { border-inline-start-color: var(--accent); }
.fbp-q { font-weight: 600; }
.fbp-tag { margin-inline-start: var(--s2); color: var(--ink-2); font-size: var(--fs-12); font-weight: 400; letter-spacing: 0.04em; text-transform: uppercase; }
.fbp-cmp { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s2); }
@media (width < 36rem) { .fbp-cmp { grid-template-columns: minmax(0, 1fr); } }
.fbp-side { min-width: 0; padding: var(--s2) var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); }
.fbp-side--theirs { background: var(--paper); }
.fbp-side h4 { margin: 0 0 var(--s1); color: var(--ink-2); font-size: var(--fs-12); font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
.fbp-score { font-family: var(--serif); font-size: var(--fs-20); font-weight: 600; }
.fbp-mean, .fbp-src { color: var(--ink-2); font-size: var(--fs-12); overflow-wrap: anywhere; }
.fbp-side blockquote { max-height: 6.5rem; margin: var(--s2) 0 2px; padding-inline-start: var(--s2); overflow: auto; border-inline-start: 3px solid var(--rule); font-style: italic; overflow-wrap: anywhere; }
.fbp-side blockquote:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.fbp-why { overflow-wrap: anywhere; }
.fbp-version { padding-top: var(--s1); border-top: 1px solid var(--rule); }
.fbp-flag { color: var(--ink-2); font-size: var(--fs-14); }
.fbp-claude { padding: var(--s2) var(--s3); border-radius: var(--radius); background: var(--accent-soft); display: grid; gap: var(--s1); justify-items: start; }
.fbp-doubt { color: var(--ink-2); font-style: italic; }
.fbp-levels { display: grid; gap: var(--s1); }
.fbp-levels label { display: flex; align-items: flex-start; gap: var(--s2); padding: var(--s1) var(--s2); border: 1px solid var(--rule); border-radius: var(--radius); cursor: pointer; }
.fbp-levels label.is-ours { border-color: var(--rule-strong); }
.fbp-levels label:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
.fbp-levels input { margin-top: 0.3rem; }
.fbp-pts { min-width: 1.5rem; font-weight: 700; }
.fbp-note { margin-inline-start: auto; color: var(--ink-2); font-size: var(--fs-12); letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap; }
.fbp-impact { padding: var(--s2) var(--s3); border-radius: var(--radius); background: var(--paper); }
.fbp-decide { display: grid; gap: var(--s2); }
.fbp-decide .btn { justify-self: start; }
.fbp-reason { min-height: 3.5rem; font-size: var(--fs-14); }
.fbp-outcome { font-size: var(--fs-16) !important; overflow-wrap: anywhere; }
.fbp-outcome s { color: var(--ink-2); }
.fbp-manage { display: grid; gap: var(--s2); padding-top: var(--s3); border-top: 1px solid var(--rule); }
@media (width < 56rem) {
  .fb-tiles { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .fb-row { grid-template-columns: minmax(0, 1fr); gap: var(--s1); }
  .fb-gcount { display: none; }
  .fb-gname { min-width: 0; flex: 0 1 auto; }
}
@media (pointer: coarse) { .fb-select { min-height: 2.75rem; } }
@media (prefers-reduced-motion: reduce) { .fb-flash { animation: none; } }
`);

  /** The round in a few lines, for the corner chat while the owner is on this tab. */
  function forChat() {
    const info = round();
    if (!info) return { started: false };
    const settings = info.settings || {};
    return {
      counts: Object.fromEntries(stagesOf(info).map((s) => [s.label, (info.counts || {})[s.id] || 0])),
      settings: { packs: settingWords(info)[0].value, claude: settingWords(info)[1].value, auto_accept: settingWords(info)[2].value,
        reasons_in_packs: settingWords(info)[3].value, reply_by: settings.reply_by || null },
      files_waiting_for_the_owner: arr(info.unmatched).length + arr(info.groups).length,
      read_only: !isLocal(),
    };
  }

  HB.feedbackTab = { stageLine: (info, uid) => stageLine(info, uid), groupsOf, settingWords, summarise, forChat };

  (HB.views = HB.views || {}).feedback = {
    id: "feedback",
    label: "Feedback",
    order: 50,
    render,
    visible: (b) => isLocal() || Boolean(b && b.feedback),
  };
})((window.HB = window.HB || {}));
