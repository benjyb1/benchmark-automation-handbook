/* =============================================================================
   Handbook viewer: views/team.js, working as a team (see lib/handbook/team.py).
   Only under hb serve (HB.local): a published page has none of this, and no names.

   HB.team = {
     isMe(id), nameOf(id), whose(id)   who someone is: "Your", or "Toby Mendel's"
     checkedWords(checked, short), tickText(checked)   "checked by Toby Mendel on 19 Oct", "✓TM"
     cellPart(ctx, row, qid, q, cell)  the cell panel's "Checked by" line, who corrected it,
                                       what a later run read, and Mark as checked
     top(h, state)                     the top bar's bits: Clashes (n), a colleague's job,
                                       online-only files, and the Team (or Share) button
     open(section)                     the Team panel: clashes, what people did, people,
                                       your name, snapshots, share with a team, send a copy
     chatTools({S, propose}), chatNote()   the corner chat's part
   }
   HB.personName(id) is the name for a hidden person id (downloads.js uses it).
   ========================================================================== */

(function (HB) {
  "use strict";

  /** A shared folder in words: "University of Bristol" for OneDrive-UniversityofBristol, nothing for
      iCloud's own folder, the path itself for anything else (a network drive). The full path is the title. */
  function whereWords(p) {
    const path = String(p.path || "");
    const cloud = path.match(/CloudStorage\/[^/-]+-([^/]+)$/);
    if (cloud) return "(" + cloud[1].replace(/([a-z])of([A-Z])/g, "$1 of $2").replace(/([a-z])([A-Z])/g, "$1 $2") + ")";
    if (/Mobile Documents\/com~apple~CloudDocs$/.test(path) || /CloudStorage\/[^/]+$/.test(path)) return "";
    return path;
  }

  const { h } = HB;

  const CSS = `
.hbg-cell.is-checked, .hbg-mk.is-checked { position: relative; }
.hbg-tick { position: absolute; right: 2px; bottom: 1px; font-size: 0.6rem; line-height: 1; font-weight: 700; color: inherit; opacity: 0.9; }
.hbg-mk.is-checked::after { content: ""; position: absolute; right: -1px; bottom: -1px; width: 0.35rem; height: 0.35rem; border-radius: 50%; background: var(--good, #2e7d4f); }
.ht-cell { display: grid; gap: var(--s2); }
.ht-cell p { margin: 0; }
.ht-tick { color: var(--good, #2e7d4f); font-weight: 600; }
.ht-acts { display: flex; flex-wrap: wrap; gap: var(--s2); align-items: center; }
.ht-panel { display: grid; gap: var(--s5, 1.5rem); }
.ht-panel h3 { margin: 0 0 var(--s2); font-size: var(--fs-16, 1rem); }
.ht-list { display: grid; gap: var(--s3); margin: 0; padding: 0; list-style: none; }
.ht-clash { display: grid; gap: var(--s2); padding: var(--s3); border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }
.ht-clash[data-open="true"] { border-color: var(--warning, #b7791f); }
.ht-clash p { margin: 0; }
.ht-sides { display: grid; gap: var(--s2); }
.ht-side { display: flex; flex-wrap: wrap; gap: var(--s2); align-items: baseline; justify-content: space-between; }
.ht-log { display: grid; gap: var(--s1); margin: 0; padding: 0; list-style: none; font-size: var(--fs-14); }
.ht-log time { color: var(--ink-2); font-variant-numeric: tabular-nums; margin-inline-end: var(--s2); }
.ht-people { display: flex; flex-wrap: wrap; gap: var(--s2); margin: 0; padding: 0; list-style: none; }
.ht-person { display: inline-flex; align-items: center; gap: var(--s1); padding: 0.125rem 0.5rem; border: 1px solid var(--rule); border-radius: 999px; font-size: var(--fs-14); }
.ht-ini { display: inline-grid; place-items: center; width: 1.5rem; height: 1.5rem; border-radius: 50%; background: var(--paper); font-size: var(--fs-12); font-weight: 600; }
.ht-places { display: grid; gap: var(--s2); }
.ht-place { display: grid; grid-template-columns: auto 1fr; gap: var(--s1) var(--s2); align-items: start; padding: var(--s2); border: 1px solid var(--rule); border-radius: var(--radius); }
.ht-place small { grid-column: 2; color: var(--ink-2); }
.ht-row { display: flex; flex-wrap: wrap; gap: var(--s2); align-items: center; }
.ht-row .input { flex: 1 1 14rem; min-width: 0; }
`;
  if (typeof HB.addStyles === "function") HB.addStyles("team", CSS);

  const local = () => (HB.local && typeof HB.local.state === "function" ? HB.local : null);
  const st = () => (local() ? local().state() : null) || {};
  const teamState = () => st().team || {};
  const meId = () => ((st().user || {}).id) || null;

  function nameOf(id) {
    if (!id) return null;
    const t = teamState();
    if (t.members && t.members[id]) return t.members[id];
    const me = st().user || {};
    return me.id === id ? me.name || null : null;
  }

  function isMe(id) {
    return Boolean(id) && id === meId();
  }

  function whose(id) {
    if (!id || isMe(id) || !teamState().on) return "Your";
    const n = nameOf(id);
    return n ? n + "'s" : "A colleague's";
  }

  function initials(name) {
    const words = String(name || "").split(/[\s-]+/).filter((w) => /^\p{L}/u.test(w));
    return (words.slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
  }

  const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
  const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
  function when(at) {
    const d = at ? new Date(at) : null;
    if (!d || isNaN(d)) return "";
    const today = new Date().toDateString() === d.toDateString();
    return today ? "today at " + TIME.format(d) : DAY.format(d) + " at " + TIME.format(d);
  }

  function checkedWords(checked, short) {
    const by = checked && typeof checked === "object" ? checked.by : null;
    const n = isMe(by) ? "you" : nameOf(by);
    const at = checked && checked.at ? (short ? " on " + DAY.format(new Date(checked.at)) : ", " + when(checked.at)) : "";
    return "checked by " + (n || "a person") + at;
  }

  function tickText(checked) {
    const by = checked && typeof checked === "object" ? checked.by : null;
    const n = nameOf(by);
    return "✓" + (n && teamState().on ? initials(n) : "");
  }

  /* ---- The cell panel ------------------------------------------------------------ */

  function valueWords(q, v) {
    if (v == null) return "no answer";
    const lv = ((q && q.answer && q.answer.levels) || []).find((l) => l.value === v);
    const label = lv && (lv.label || lv.name);
    return label && String(label) !== String(v) ? v + " (" + label + ")" : String(v);
  }

  function cellPart(ctx, row, qid, q, cell) {
    const answered = cell && (cell.state === "answered" || cell.state === "no_evidence");
    if (!answered) return null;
    const lines = [];
    if (cell.checked) {
      lines.push(h("p", null, h("span", { class: "ht-tick", "aria-hidden": "true" }, "✓ "),
        checkedWords(cell.checked).replace(/^c/, "C") + "."));
    }
    const owner = cell.owner || {};
    if (owner.by && !isMe(owner.by) && nameOf(owner.by)) {
      lines.push(h("p", { class: "cellp-sub" }, "This answer is " + nameOf(owner.by) + "'s correction" + (owner.at ? ", " + when(owner.at) : "") + "."));
    }
    if (cell.rescored && cell.rescored.value != null) {
      lines.push(HB.notice("neutral", "A later run read " + valueWords(q, cell.rescored.value) + ". The checked answer stands: " +
        "a run never overwrites what a person checked. If the run is right, take the check off and correct it."));
    }
    const sec = h("section", { class: "cellp-sec ht-cell", id: "cellp-team" }, lines);
    if (local()) {
      const msg = h("div", { role: "status" });
      const label = cell.checked ? "Take the check off" : "Mark as checked";
      const btn = h("button", { type: "button", class: "btn btn--small " + (cell.checked ? "btn--quiet" : ""), id: "cellp-check" }, label);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        msg.replaceChildren();
        try {
          await local().post("team/check", { row, qid, checked: !cell.checked, seen: cell.rev || null });
          msg.replaceChildren(HB.notice("good", cell.checked ? "Check mark taken off." : "Marked as checked by you."));
          await local().refresh();
        } catch (err) {
          btn.disabled = false;
          msg.replaceChildren(HB.notice("warning", err && err.message ? err.message : "That could not be saved."));
        }
      });
      sec.append(h("div", { class: "ht-acts" }, btn,
        cell.checked ? null : h("span", { class: "cellp-sub" }, "You have looked at this answer and its evidence, and it is right.")), msg);
    }
    return lines.length || local() ? sec : null;
  }

  /* ---- The top bar ----------------------------------------------------------------- */

  let named = false;

  function top(hh, s) {
    if (!local()) return [];
    const t = (s && s.team) || {};
    if (t.on && t.members && !named) {
      named = true;               // the grid drew before the names came: once, draw it again with initials
      if (typeof HB.render === "function") setTimeout(HB.render, 0);
    }
    const out = [];
    if (t.on && t.clashes_open) {
      const b = hh("button", { type: "button", class: "btn btn--small", id: "ht-clashes-btn", "data-tone": "warning",
        title: "Two people changed the same thing. Choose which to keep." }, "Clashes (" + t.clashes_open + ")");
      b.addEventListener("click", () => open("clashes"));
      out.push(b);
    }
    for (const o of t.others_jobs || []) {
      const j = o.job || {};
      const pct = j.total ? Math.round((100 * (j.done || 0)) / j.total) : null;
      out.push(hh("span", { class: "hbl-pill", title: j.line || "" }, (o.name || "A colleague") + ": " + (j.title || "working") + (pct != null ? " (" + pct + "%)" : "")));
    }
    if (t.on && t.online_only) {
      const b = hh("button", { type: "button", class: "btn btn--small btn--quiet", id: "ht-online", "data-tone": "warning" },
        t.online_only + (t.online_only === 1 ? " file" : " files") + " online only");
      b.addEventListener("click", () => open("folder"));
      out.push(b);
    }
    const team = hh("button", { type: "button", class: "btn btn--quiet btn--small", id: "ht-team", "aria-haspopup": "dialog" },
      t.on ? "Team" : "Share");
    team.addEventListener("click", () => open(null));
    out.push(team);
    return out;
  }

  /* ---- The Team panel --------------------------------------------------------------------- */

  async function open(section) {
    if (!local() || !HB.drawer) return;
    const body = h("div", { class: "ht-panel" }, h("p", { class: "muted" }, "Loading..."));
    HB.drawer.open(teamState().on ? "Team" : "Share and snapshots", body, { kicker: teamState().on ? "Working together" : "This project" });
    await fill(body, section);
  }

  async function getJSON(path) {
    const res = await local().get(path);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error && data.error.message) || "The page's server refused that.");
    return data;
  }

  async function fill(body, section) {
    let info, backups;
    try {
      [info, backups] = await Promise.all([getJSON("team"), getJSON("team/backups")]);
    } catch (err) {
      body.replaceChildren(HB.notice("critical", err.message));
      return;
    }
    const redo = () => fill(body, section);
    const parts = [];
    if (info.on) {
      parts.push(clashesPart(info, redo));
      if (info.online_only) parts.push(folderPart(info));
      parts.push(peoplePart(info, redo));
      parts.push(logPart(info));
    } else {
      parts.push(namePart(info, redo));
      parts.push(await sharePart());
    }
    parts.push(backupsPart(backups.backups || [], redo));
    parts.push(copyPart());
    body.replaceChildren(...parts.filter(Boolean));
    const target = section ? body.querySelector("#ht-" + section) : null;
    if (target) target.scrollIntoView({ block: "start" });
  }

  function clashesPart(info, redo) {
    const list = info.clashes || [];
    const openOnes = list.filter((c) => c.open);
    const done = list.filter((c) => !c.open);
    const sec = h("section", { id: "ht-clashes-list" }, h("h3", { id: "ht-clashes" }, "Clashes"));
    if (!openOnes.length) sec.append(h("p", { class: "muted" }, "None waiting. When two people change the same answer, rule or file before the sync service catches up, it shows here and either of you can choose which to keep."));
    else sec.append(h("ul", { class: "ht-list" }, openOnes.map((c) => clashItem(c, redo))));
    if (done.length) {
      sec.append(h("details", null, h("summary", null, "Sorted out (" + done.length + ")"),
        h("ul", { class: "ht-log" }, done.map((c) => {
          const r = c.resolved || {};
          const kept = r.side_by && r.side_by === r.by ? "their own" : (r.side_name ? r.side_name + "'s" : "one");
          return h("li", null, h("time", null, when(r.at)), (r.name || "Someone") + " kept " + kept + " version of " + c.title + ": " + (r.words || "") + ".");
        }))));
    }
    return sec;
  }

  function clashWhy(c) {
    const [a, b] = c.sides || [];
    if (c.source === "save") return (b && b.name ? b.name : "Someone") + " saved a change without having seen " + (a && a.name ? a.name + "'s" : "a colleague's") + " newer one.";
    if (c.kind === "file") return "Two computers saved this file before the sync service caught up, so it kept both versions.";
    return "Two people changed this before the sync service caught up.";
  }

  function clashItem(c, redo) {
    const msg = h("div", { role: "status" });
    const sides = (c.sides || []).map((s) => {
      const who = s.by ? (isMe(s.by) ? "You" : s.name || "A colleague") : s.name || "One version";
      const btn = h("button", { type: "button", class: "btn btn--small", id: "ht-keep-" + c.id + "-" + s.key },
        s.by ? (isMe(s.by) ? "Keep yours" : "Keep " + (s.name || "theirs") + "'s") : "Keep this one");
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await local().post("team/resolve", { clash: c.id, chose: s.key });
          await local().refresh();
          await redo();
        } catch (err) {
          btn.disabled = false;
          msg.replaceChildren(HB.notice("warning", err.message));
        }
      });
      return h("div", { class: "ht-side" }, h("span", null, h("b", null, who + ": "), String(s.words || "") + (s.at ? " (" + when(s.at) + ")" : "")), btn);
    });
    const item = h("li", { class: "ht-clash", "data-open": "true", id: "ht-clash-" + c.id },
      h("p", null, h("b", null, cap(c.title))), h("p", { class: "cellp-sub" }, clashWhy(c)), h("div", { class: "ht-sides" }, sides), msg);
    if (c.row && c.qid && HB.data && typeof HB.data.cell === "function") {
      const a = h("a", { href: "#/grid/" + encodeURIComponent(c.row) + "/" + encodeURIComponent(c.qid) }, "Open the answer");
      item.append(h("p", { class: "small" }, a));
    }
    return item;
  }

  function stop(text) {
    const t = String(text || "").trim();
    return /[.!?]$/.test(t) ? t : t + ".";
  }

  function cap(s) {
    s = String(s || "");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function folderPart(info) {
    const advice = {
      Dropbox: "In Dropbox, right-click the project folder and choose Make available offline.",
      "Google Drive": "In Google Drive, right-click the project folder and choose Offline access, then Available offline (or switch Google Drive to Mirror files).",
      OneDrive: "In OneDrive, right-click the project folder and choose Always keep on this device.",
      Box: "In Box, right-click the project folder and choose Make available offline.",
      "iCloud Drive": "In Finder, right-click the project folder and choose Keep Downloaded.",
    }[info.service] || "Ask your sync service to keep the project folder on this computer.";
    return h("section", { id: "ht-folder" }, h("h3", null, "Files kept online only"),
      HB.notice("warning", info.online_only + " of this project's files are kept online only by " + (info.service || "your sync service") +
        ", so each one has to download before the tool can read it. That is slow, and it fails with no internet. " + advice));
  }

  function peoplePart(info, redo) {
    const members = info.members || {};
    const people = Object.entries(members).map(([id, n]) =>
      h("li", { class: "ht-person" }, h("span", { class: "ht-ini", "aria-hidden": "true" }, initials(n)), n + (isMe(id) ? " (you)" : "")));
    return h("section", { id: "ht-people" }, h("h3", null, "People"),
      h("ul", { class: "ht-people" }, people),
      h("p", { class: "small muted" }, "Each person works on their own computer with their own Claude plan, in the same shared folder" +
        (info.service ? " (" + info.service + ")" : "") + ". To add someone, share the folder with them in " + (info.service || "your sync service") +
        "; they install the tool and open the project from it."),
      namePart(info, redo));
  }

  function namePart(info, redo) {
    const me = info.me || {};
    const input = h("input", { class: "input", id: "ht-name", type: "text", value: me.name || "", autocomplete: "name", "aria-label": "Your name" });
    const msg = h("div", { role: "status" });
    const btn = h("button", { type: "button", class: "btn btn--small" }, "Save name");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await local().post("user", { name: input.value });
        await local().refresh();
        msg.replaceChildren(HB.notice("good", "Saved. Your changes now show as " + input.value.trim() + "."));
      } catch (err) {
        msg.replaceChildren(HB.notice("warning", err.message));
      }
      btn.disabled = false;
    });
    return h("div", { class: "stack" }, h("label", { for: "ht-name", class: "small" }, "Your name, as others see it on your changes"),
      h("div", { class: "ht-row" }, input, btn), msg);
  }

  function logPart(info) {
    const items = (info.log || []).filter((e) => e.text);
    return h("section", { id: "ht-log" }, h("h3", null, "What people did"),
      items.length ? h("ul", { class: "ht-log" }, items.map((e) => h("li", null, h("time", null, when(e.at)), stop(e.text))))
        : h("p", { class: "muted" }, "Nothing yet."),
      h("p", { class: "small muted" }, "Corrections, check marks, rule decisions, documents, runs and clashes. Chats are never recorded."));
  }

  async function sharePart() {
    let got = { places: [] };
    try {
      got = await getJSON("team/places");
    } catch (err) {
      got = { places: [] };
    }
    const places = got.places || [];
    const msg = h("div", { role: "status" });
    const other = h("input", { class: "input", id: "ht-share-other", type: "text", placeholder: "Or paste the path of a shared folder", "aria-label": "Shared folder path" });
    const radios = places.map((p, i) => h("label", { class: "ht-place" },
      h("input", { type: "radio", name: "ht-share", value: p.path, id: "ht-share-" + i, checked: i === 0 }),
      h("span", { title: p.path }, h("b", null, p.service), " ", h("span", { class: "muted" }, whereWords(p))), h("small", null, p.advice)));
    const btn = h("button", { type: "button", class: "btn btn--primary btn--small", id: "ht-share-go" }, "Share this project");
    btn.addEventListener("click", async () => {
      const picked = other.value.trim() || ((document.querySelector('input[name="ht-share"]:checked') || {}).value || "");
      if (!picked) { msg.replaceChildren(HB.notice("warning", "Pick a shared folder first.")); return; }
      btn.disabled = true;
      msg.replaceChildren(HB.notice("neutral", "Copying the project into the shared folder..."));
      try {
        const r = await local().post("team/share", { into: picked });
        msg.replaceChildren(HB.notice("good", "Shared. The project now lives in " + (r.service || "the shared folder") + " at " + r.path + ". " +
          (r.restarting ? "It opens from there in a new window in a moment; you can close this one. " : "Open it from there to carry on. ") +
          "Your old folder is left as it was: work in the shared one from now on. Then share the folder with your colleagues in " + (r.service || "your sync service") + "."));
      } catch (err) {
        btn.disabled = false;
        msg.replaceChildren(HB.notice("warning", err.message));
      }
    });
    return h("section", { id: "ht-share" }, h("h3", null, "Share with a team"),
      h("p", null, "Put this project in a folder your team shares (Dropbox, Google Drive, OneDrive or a network drive). Each person then works on their own computer, " +
        "with their own Claude plan, in the same project. Everyone's changes show for everyone, every change says who made it, and if two people change the same thing, both are asked which to keep."),
      places.length ? h("div", { class: "ht-places", role: "radiogroup", "aria-label": "Shared folders on this computer" }, radios)
        : h("p", { class: "muted" }, "No Dropbox, Google Drive or OneDrive folder was found on this computer. Install one, or paste the path of a shared folder."),
      other, h("div", { class: "ht-acts" }, btn), msg);
  }

  function backupsPart(list, redo) {
    const msg = h("div", { role: "status" });
    const items = list.map((b) => {
      const btn = h("button", { type: "button", class: "btn btn--small btn--quiet" }, "Restore");
      btn.addEventListener("click", async () => {
        if (btn.dataset.sure !== "yes") {
          btn.dataset.sure = "yes";
          btn.textContent = "Yes, go back to " + b.day;
          return;
        }
        btn.disabled = true;
        try {
          await local().post("team/restore", { name: b.name });
          msg.replaceChildren(HB.notice("good", "Restored to " + b.day + ". How it was a moment ago is kept as a snapshot too, so this can be undone."));
          await local().refresh();
          await redo();
        } catch (err) {
          btn.disabled = false;
          msg.replaceChildren(HB.notice("warning", err.message));
        }
      });
      return h("li", { class: "ht-side" }, h("span", null, b.day + (String(b.label || "").indexOf("before-restore") === 0 ? " (just before a restore)" : "")), btn);
    });
    return h("section", { id: "ht-backups" }, h("h3", null, "Snapshots"),
      h("p", { class: "small muted" }, "Once a day the tool keeps a copy of the project on this computer (answers, rules, set-up and corrections; not the documents, which can be fetched again). The last 14 are kept." +
        (teamState().on ? " Everyone on the team keeps their own, so a folder deleted by mistake can always be brought back." : "")),
      items.length ? h("ul", { class: "ht-list" }, items) : h("p", { class: "muted" }, "The first snapshot is taken today."), msg);
  }

  function copyPart() {
    const msg = h("div", { role: "status" });
    const btn = h("button", { type: "button", class: "btn btn--small", id: "ht-copy" }, "Send a copy");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      msg.replaceChildren(HB.notice("neutral", "Making the copy..."));
      try {
        const res = await local().get("team/copy");
        if (!res.ok) throw new Error("The copy could not be made (" + res.status + ").");
        const blob = await res.blob();
        const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
        const a = h("a", { href: URL.createObjectURL(blob), download: m ? m[1] : "benchmark-copy.zip" });
        document.body.append(a);
        a.click();
        a.remove();
        msg.replaceChildren(HB.notice("good", "Saved to your Downloads folder. Anyone with the tool can open it; changes they make stay with them."));
      } catch (err) {
        msg.replaceChildren(HB.notice("warning", err.message));
      }
      btn.disabled = false;
    });
    return h("section", { id: "ht-copy-sec" }, h("h3", null, "Send a copy"),
      h("p", { class: "small muted" }, "One zip file of the whole project, documents included, for someone outside the team, such as a funder or an auditor. It is a copy: what they change never comes back here."),
      h("div", { class: "ht-acts" }, btn), msg);
  }

  /* ---- The corner chat ------------------------------------------------------------------- */

  function personByName(text) {
    const want = String(text || "").trim().toLowerCase();
    if (!want) return null;
    const members = teamState().members || {};
    const all = Object.entries(members);
    if (["me", "mine", "myself", "i"].includes(want)) return meId();
    const exact = all.find(([, n]) => n.toLowerCase() === want);
    if (exact) return exact[0];
    const part = all.filter(([, n]) => n.toLowerCase().split(/\s+/).includes(want) || n.toLowerCase().startsWith(want));
    return part.length === 1 ? part[0][0] : null;
  }

  function chatTools({ S, propose }) {
    if (!local()) return [];
    const out = [];
    if (teamState().on) {
      out.push({
        name: "get_clashes",
        description: "The clashes waiting: where two people on the team changed the same answer, rule or file. Each has an id, a title, and its sides (who, what they set).",
        inputSchema: S({}),
        execute: async () => {
          const info = await getJSON("team");
          return { people: teamState().members || {}, you: meId(), clashes: (info.clashes || []).filter((c) => c.open).map((c) => ({
            id: c.id, title: c.title, kind: c.kind, why: clashWhy(c),
            sides: (c.sides || []).map((s) => ({ person: s.name || null, is_you: isMe(s.by), set: s.words, at: s.at })) })) };
        },
      });
      out.push({
        name: "propose_keep_clashes",
        description: "Puts to the person a choice for clashes waiting now: keep one person's side (a name, or \"me\") in every clash where they have one, or only in the clash ids given. Saved only when they click Confirm. It never sets a rule for future clashes.",
        inputSchema: S({ keep: { type: "string", description: "whose side to keep: a name on the team, or me" }, clashes: { type: "array", items: { type: "string" }, description: "clash ids; leave out for every clash waiting" } }, ["keep"]),
        execute: async (input) => {
          const who = personByName(input.keep);
          if (!who) throw new Error("No one on the team is called " + JSON.stringify(input.keep) + ". The team is: " + Object.values(teamState().members || {}).join(", ") + ".");
          const info = await getJSON("team");
          const want = Array.isArray(input.clashes) && input.clashes.length ? new Set(input.clashes.map(String)) : null;
          const picked = (info.clashes || []).filter((c) => c.open && (!want || want.has(c.id)) && (c.sides || []).some((s) => s.by === who));
          if (!picked.length) throw new Error((nameOf(who) || "That person") + " has no side in any clash waiting" + (want ? " among those ids" : "") + ".");
          const n = isMe(who) ? "your" : nameOf(who) + "'s";
          return propose("Keep " + n + " version in " + picked.length + (picked.length === 1 ? " clash: " : " clashes: ") +
            picked.slice(0, 6).map((c) => c.title).join("; ") + (picked.length > 6 ? "; and " + (picked.length - 6) + " more" : ""),
          async () => {
            await local().post("team/resolve", { clashes: picked.map((c) => c.id), keep: who });
            await local().refresh();
          });
        },
      });
    }
    if (!teamState().on) {
      out.push({
        name: "share_with_team",
        description: "For a person who wants colleagues to work on this project too: lists the shared folders on their computer (Dropbox, Google Drive, OneDrive, iCloud, network drives) with advice on each, and opens the Share panel, where they pick one and click Share. Use it to help them choose; the copying happens only when they click.",
        inputSchema: S({}),
        execute: async () => {
          const got = await getJSON("team/places");
          open("share");
          return { opened: "the Share panel", places: got.places || [],
            how: "Each person works on their own computer with their own Claude plan, in the same shared folder. Dropbox, Google Drive (Mirror, or the folder set to Available offline) and OneDrive (Always keep on this device) work well; iCloud works but is slow and not advised; a network drive works while connected. After sharing, they share the folder with colleagues in that service; each colleague installs the tool and opens the project from it." };
        },
      });
    }
    out.push({
      name: "propose_my_name",
      description: "Puts to the person a change of the name the tool shows on their changes (it starts as the name on their Claude account). Saved only when they click Confirm.",
      inputSchema: S({ name: { type: "string" } }, ["name"]),
      execute: (input) => {
        const name = String(input.name || "").trim().replace(/\s+/g, " ").slice(0, 80);
        if (!name) throw new Error("Give the name to use.");
        return propose("Show my changes as " + name, async () => {
          await local().post("user", { name });
          await local().refresh();
        });
      },
    });
    return out;
  }

  function chatNote() {
    if (!local()) return null;
    const t = teamState();
    const me = (st().user || {}).name;
    if (!t.on) return "You are talking with " + (me || "the owner") + ". To change the name shown on their changes, use propose_my_name. " +
      "If they want colleagues to work on this project too, share_with_team lists the shared folders on their computer and opens the Share panel.";
    const names = Object.values(t.members || {});
    return "This project is shared by a team (" + names.join(", ") + "), each on their own computer and Claude plan. You are talking with " + (me || "one of them") + ". " +
      (t.clashes_open ? t.clashes_open + (t.clashes_open === 1 ? " clash is" : " clashes are") + " waiting: get_clashes lists them, and propose_keep_clashes keeps one person's side in the ones waiting now (never a standing rule). " : "") +
      "Anyone on the team may correct, check and decide rules; a run is paused or stopped only by the person who started it. To change the name shown on their changes, use propose_my_name.";
  }

  HB.personName = nameOf;
  // A team project on the local page: the only place anyone is known by name.
  const knows = () => Boolean(local() && teamState().on);

  HB.team = { knows, isMe, nameOf, whose, checkedWords, tickText, cellPart, top, open, chatTools, chatNote };
})(window.HB = window.HB || {});
