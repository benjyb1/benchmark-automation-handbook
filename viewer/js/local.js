/* =============================================================================
   Handbook viewer: local.js, loaded only when the page is served by `hb serve`
   (the server puts it before the page's own scripts; the build never joins it,
   so the published Artifact page never has it).

   It gives the page the same window.claude the Artifact runtime gives it, backed
   by the local server, so the viewer's own code (edits.js, reportdraft.js,
   views/chat.js, views/cell.js, views/deck.js, downloads.js) runs unchanged:
     claude.use("db")         collections stored as files in the project folder
                              (page-data/<collection>/<id>.json); onSnapshot polls
     claude.use("user")       the owner's name, asked once and kept on this computer
     claude.use("assets")     an upload, saved in the project's inbox/uploads/
     claude.use("downloads")  an ordinary browser download
     claude.use("sample")     Claude on the owner's own plan, through `claude -p`,
                              with page tools: Claude asks for a tool, this page
                              runs it and sends back the result, until Claude writes
   Anything else resolves null, as an Artifact viewer does for an undeclared one.

   It also keeps the page in step with the server: when what the owner saved has
   been applied and the page rebuilt, it loads the new data in place
   (HB.data.replace, HB.reloadData). It adds a small status area to the top bar
   (what is running on this computer, recent changes, "Download a copy") and the
   running job's line to the run bar (HB.local.runLine, views/runbar.js).

   window.HB_LOCAL = {token, version} is written by the server just before this.
   ========================================================================== */

(function () {
  "use strict";

  const boot = window.HB_LOCAL || {};
  const TOKEN = String(boot.token || "");
  const HB = (window.HB = window.HB || {});
  const POLL_MS = 1500;
  const STATE_MS = 2000;

  /* ---- Talking to the server --------------------------------------------------- */

  function err(code, message, extra) {
    return Object.assign({ code, message }, extra || {});
  }

  async function api(method, path, body, opts) {
    const o = opts || {};
    const headers = { "X-HB-Token": TOKEN };
    let payload;
    if (o.raw) {
      payload = body;
      Object.assign(headers, o.headers || {});
    } else if (body !== undefined) {
      payload = JSON.stringify(body);
      headers["Content-Type"] = "application/json";
    }
    let res;
    try {
      res = await fetch("/api/" + path, { method, headers, body: payload, signal: o.signal, cache: "no-store" });
    } catch (e) {
      if (o.signal && o.signal.aborted) throw err("cancelled", "Stopped.");
      throw err("unavailable", "The page's server on this computer did not answer. Is the terminal window still open?");
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      const e = data && data.error ? data.error : {};
      throw err(e.code || (res.status === 403 ? "revoked" : "unavailable"), e.message || "The server refused this (" + res.status + ").");
    }
    return data;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const copy = (v) => (v == null ? v : JSON.parse(JSON.stringify(v)));

  /* ---- db: collections as files, followed by polling ---------------------------- */

  const watchers = new Map();   // collection -> {version, docs, listeners: Set, timer, busy}

  function snapshotOf(docs, previous) {
    const snaps = docs.map((d) => {
      const data = Object.assign({}, d);
      delete data.id;
      return { id: d.id, exists: true, data: () => copy(data), metadata: { fromCache: false, hasPendingWrites: false } };
    });
    const before = new Map((previous || []).map((d, i) => [d.id, { d, i }]));
    const changes = [];
    snaps.forEach((s, i) => {
      const was = before.get(s.id);
      if (!was) changes.push({ type: "added", doc: s, oldIndex: -1, newIndex: i });
      else if (JSON.stringify(was.d) !== JSON.stringify(docs[i])) changes.push({ type: "modified", doc: s, oldIndex: was.i, newIndex: i });
    });
    const now = new Set(docs.map((d) => d.id));
    (previous || []).forEach((d, i) => {
      if (!now.has(d.id)) changes.push({ type: "removed", doc: { id: d.id, exists: false, data: () => undefined, metadata: {} }, oldIndex: i, newIndex: -1 });
    });
    return { docs: snaps, size: snaps.length, empty: snaps.length === 0, docChanges: () => changes.slice(), metadata: { fromCache: false, hasPendingWrites: false } };
  }

  function watcher(name) {
    if (!watchers.has(name)) watchers.set(name, { version: null, docs: null, previous: null, listeners: new Set(), timer: null, busy: false });
    return watchers.get(name);
  }

  async function poll(name) {
    const w = watcher(name);
    if (w.busy) return;
    w.busy = true;
    try {
      const data = await api("GET", "db/" + encodeURIComponent(name) + (w.version ? "?v=" + w.version : ""));
      if (!data.same) {
        w.previous = w.docs;
        w.docs = data.docs || [];
        w.version = data.version;
        const snap = snapshotOf(w.docs, w.previous);
        w.listeners.forEach((l) => { try { l.next(snap); } catch (e) { console.error(e); } });
      }
    } catch (e) {
      w.listeners.forEach((l) => { if (l.error) try { l.error(e); } catch (x) { /* ignore */ } });
    } finally {
      w.busy = false;
    }
  }

  function follow(name, next, error) {
    const w = watcher(name);
    const l = { next, error };
    w.listeners.add(l);
    if (w.docs) setTimeout(() => next(snapshotOf(w.docs, null)), 0);
    else poll(name);
    if (!w.timer) w.timer = setInterval(() => poll(name), POLL_MS);
    return () => {
      w.listeners.delete(l);
      if (!w.listeners.size && w.timer) {
        clearInterval(w.timer);
        w.timer = null;
      }
    };
  }

  function newId() {
    if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return "d-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function plainObject(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) throw err("invalid_argument", "A document must be a plain object.");
    return copy(data);
  }

  function docRef(name, id) {
    const path = "db/" + encodeURIComponent(name) + "/" + encodeURIComponent(id);
    const after = (p) => p.then((r) => { poll(name); return r; });
    return {
      id,
      path: name + "/" + id,
      get: () => api("GET", path).then((r) => {
        const d = r.doc;
        const data = d ? Object.assign({}, d) : undefined;
        if (data) delete data.id;
        return { id, exists: Boolean(d), data: () => copy(data), metadata: { fromCache: false, hasPendingWrites: false } };
      }),
      set: (data) => after(api("PUT", path, plainObject(data)).then(() => undefined)),
      update: (data) => after(api("PATCH", path, plainObject(data)).then(() => undefined)),
      delete: () => after(api("DELETE", path).then(() => undefined)),
      acquire: () => Promise.resolve({ acquired: true }),
      onSnapshot: (next, error) => follow(name, (snap) => {
        const d = snap.docs.find((x) => x.id === id);
        next(d || { id, exists: false, data: () => undefined, metadata: { fromCache: false, hasPendingWrites: false } });
      }, error),
      collection: () => { throw err("invalid_argument", "Collections inside a document are not kept on this computer."); },
    };
  }

  function query(name, filters, order, cap) {
    function apply(snap) {
      let docs = snap.docs.slice();
      filters.forEach(([field, op, value]) => {
        docs = docs.filter((d) => {
          const v = (d.data() || {})[field];
          switch (op) {
            case "==": return v === value;
            case "!=": return v !== value;
            case "<": return v < value;
            case "<=": return v <= value;
            case ">": return v > value;
            case ">=": return v >= value;
            case "in": return Array.isArray(value) && value.includes(v);
            case "array-contains": return Array.isArray(v) && v.includes(value);
            default: return true;
          }
        });
      });
      if (order) {
        const [field, dir] = order;
        docs.sort((a, b) => {
          const x = (a.data() || {})[field];
          const y = (b.data() || {})[field];
          return (x < y ? -1 : x > y ? 1 : 0) * (dir === "desc" ? -1 : 1);
        });
      }
      if (cap != null) docs = docs.slice(0, cap);
      return { docs, size: docs.length, empty: !docs.length, docChanges: snap.docChanges, metadata: snap.metadata };
    }
    return {
      where: (f, op, v) => query(name, filters.concat([[f, op, v]]), order, cap),
      orderBy: (f, dir) => query(name, filters, [f, dir || "asc"], cap),
      limit: (n) => query(name, filters, order, n),
      get: () => api("GET", "db/" + encodeURIComponent(name)).then((r) => apply(snapshotOf(r.docs || [], null))),
      onSnapshot: (next, error) => follow(name, (snap) => next(apply(snap)), error),
    };
  }

  function collection(name) {
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(String(name))) throw err("invalid_argument", "Not a collection name: " + name);
    return Object.assign(query(name, [], null, null), {
      path: name,
      doc: (id) => docRef(name, id == null ? newId() : String(id)),
      add: (data) => { const ref = docRef(name, newId()); return ref.set(data).then(() => ref); },
    });
  }

  const db = {
    collection,
    doc: (path) => {
      const [c, id] = String(path).split("/");
      return collection(c).doc(id);
    },
  };

  /* ---- user: a local name, asked once ------------------------------------------- */

  let me = { name: null, id: null };
  let asking = null;

  function askName() {
    if (me.name) return Promise.resolve(me);
    if (asking) return asking;
    asking = new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "hb-dialog hbl-name";
      d.innerHTML = '<form method="dialog" class="hb-dialog__form" style="grid-template-rows:auto auto auto">' +
        '<div class="hb-dialog__head"><h2 id="hbl-name-title">What should we call you?</h2>' +
        '<p class="hb-hint">Your name is saved on this computer and shown beside the changes you make.</p></div>' +
        '<div class="hb-dialog__body"><div class="field"><label class="field__label" for="hbl-name">Your name</label>' +
        '<input class="input" id="hbl-name" type="text" autocomplete="name" required maxlength="80"></div>' +
        '<p class="hbl-name__msg" role="status"></p></div>' +
        '<div class="hb-dialog__foot"><button class="btn btn--primary" id="hbl-name-save" type="submit">Save</button></div></form>';
      d.setAttribute("aria-labelledby", "hbl-name-title");
      (document.querySelector(".hb") || document.body).append(d);
      const input = d.querySelector("input");
      const msg = d.querySelector(".hbl-name__msg");
      d.addEventListener("cancel", (ev) => ev.preventDefault());
      d.querySelector("form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const name = input.value.trim();
        if (!name) { msg.textContent = "Write your name first."; return; }
        try {
          me = await api("POST", "user", { name });
          d.close();
          d.remove();
          asking = null;
          resolve(me);
        } catch (e) {
          msg.textContent = e.message || "That didn't save. Try again.";
        }
      });
      if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
      input.focus();
    });
    return asking;
  }

  const user = {
    id: async () => (me.id || (await askName()).id || null),
    name: async () => (me.name || (await askName()).name || ""),
    me: async () => {
      const m = me.name ? me : await askName();
      return { id: m.id, name: m.name || "", avatarUrl: "", color: "#c6613f", email: null, isOwner: true, canEdit: true };
    },
    isOwner: async () => true,
    canEdit: async () => true,
    can: async () => true,
    avatarUrl: async () => null,
    email: async () => null,
    profiles: async (ids) => {
      const out = {};
      (Array.isArray(ids) ? ids : [ids]).forEach((id) => {
        out[id] = { id, name: id === me.id ? me.name : id, avatarUrl: "", color: "#c6613f", email: null, isMe: id === me.id, guest: false };
      });
      return out;
    },
    search: async () => [],
  };

  /* ---- assets: uploads saved in the project ------------------------------------- */

  const assets = {
    upload: async (blob, options) => {
      if (!(blob instanceof Blob)) throw err("invalid_request", "Upload a file.");
      const type = (options && options.type) || blob.type || "application/octet-stream";
      return api("POST", "assets", blob, { raw: true, headers: { "Content-Type": type, "X-Filename": encodeURIComponent(blob.name || "upload") } });
    },
    list: () => api("GET", "assets"),
    delete: (ref) => api("DELETE", "assets/" + encodeURIComponent(String(ref).split("/").pop().split("--")[0])),
  };

  /* ---- downloads: an ordinary browser download ---------------------------------- */

  const downloads = {
    save: async (req) => {
      if (!req || typeof req.filename !== "string" || !req.filename) throw err("bad_request", "Give the file a name.");
      const data = req.data;
      const blob = data instanceof Blob ? data : new Blob([data], { type: typeof data === "string" ? "text/plain;charset=utf-8" : "application/octet-stream" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = req.filename.replace(/[\\/]/g, "-");
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      return { status: "saved" };
    },
  };

  /* ---- sample: Claude on the owner's plan, with the page's tools ----------------- */

  const TOOL_LIMIT_MS = 150000;

  function parseJson(text) {
    const s = String(text || "").trim();
    try { return { ok: true, value: JSON.parse(s) }; } catch (e) { /* next */ }
    const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (fence) { try { return { ok: true, value: JSON.parse(fence[1]) }; } catch (e) { /* next */ } }
    const a = s.search(/[[{]/);
    const b = Math.max(s.lastIndexOf("}"), s.lastIndexOf("]"));
    if (a >= 0 && b > a) { try { return { ok: true, value: JSON.parse(s.slice(a, b + 1)) }; } catch (e) { /* none */ } }
    return { ok: false };
  }

  async function runTool(call, tools, ev, signal) {
    const tool = tools.find((t) => t.name === ev.name);
    const ctl = new AbortController();
    const stop = () => ctl.abort();
    if (signal) signal.addEventListener("abort", stop, { once: true });
    const timer = setTimeout(stop, TOOL_LIMIT_MS);
    let body;
    try {
      if (!tool) throw new Error("There is no page tool called " + ev.name + ".");
      let value = await tool.execute(ev.input || {}, { signal: ctl.signal });
      if (value === undefined) value = null;
      if (typeof value !== "string") value = JSON.parse(JSON.stringify(value));
      body = { id: ev.id, ok: true, value };
    } catch (e) {
      body = { id: ev.id, ok: false, error: e && e.message ? e.message : String(e) };
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", stop);
    }
    if (HB.local) HB.local.toolsRun += 1;
    try {
      await api("POST", "sample/" + call + "/result", body);
    } catch (e) {
      /* the call has ended */
    }
  }

  function ask(input, options, asJson) {
    const o = options || {};
    if (o && (typeof o !== "object" || Array.isArray(o))) return Promise.reject(err("invalid_request", "options must be a plain object"));
    const tools = Array.isArray(o.tools) ? o.tools : [];
    for (const t of tools) {
      if (!t || typeof t.name !== "string" || typeof t.execute !== "function" || typeof t.description !== "string") {
        return Promise.reject(err("invalid_request", "Each tool needs a name, a description and an execute function."));
      }
    }
    if (tools.length && o.cache !== undefined && o.cache !== false) return Promise.reject(err("invalid_request", "cache can't be used with tools"));
    const signal = o.signal;
    if (signal && signal.aborted) return Promise.reject(err("cancelled", "Stopped before it started."));
    return new Promise((resolve, reject) => {
      let call = null;
      let shown = "";
      let settled = false;
      const finish = (fn, v) => { if (!settled) { settled = true; fn(v); } };
      const onAbort = () => {
        if (call) api("POST", "sample/" + call + "/cancel").catch(() => {});
        finish(reject, err("cancelled", "Stopped.", shown ? { text: shown } : {}));
      };
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
      const text = (t) => {
        if (!t || t === shown || settled) return;
        const delta = t.startsWith(shown) ? t.slice(shown.length) : t;
        shown = t;
        if (typeof o.onText === "function") {
          try { o.onText({ text: t, delta }); } catch (e) { console.error(e); }
        }
      };
      (async () => {
        try {
          const started = await api("POST", "sample", {
            input, json: Boolean(asJson), modelTier: o.modelTier || "default",
            tools: tools.map((t) => ({ name: t.name, description: t.description, inputSchema: t.inputSchema })),
          });
          call = started.call;
          if (signal && signal.aborted) { onAbort(); return; }
          let after = 0;
          while (!settled) {
            const r = await api("GET", "sample/" + call + "/events?after=" + after);
            for (const ev of r.events || []) {
              after = Math.max(after, ev.n + 1);
              if (settled) break;
              if (ev.type === "text") text(ev.text);
              else if (ev.type === "tool") runTool(call, tools, ev, signal);
              else if (ev.type === "done") {
                text(ev.text);
                if (HB.local) HB.local.noteUsage(ev.usage);
                if (asJson) {
                  const parsed = parseJson(ev.text);
                  if (!parsed.ok) finish(reject, err("invalid_json", "The answer was not JSON.", { text: ev.text }));
                  else finish(resolve, parsed.value);
                } else {
                  finish(resolve, { text: ev.text, truncated: Boolean(ev.truncated), modelTierApplied: ev.modelTierApplied || o.modelTier || "default" });
                }
              } else if (ev.type === "error") {
                if (HB.local && ev.usage) HB.local.noteUsage(ev.usage);
                if (ev.code === "session_expired" && HB.local) HB.local.flag(ev.message);
                finish(reject, err(ev.code || "upstream_error", ev.message || "Claude stopped.", ev.text ? { text: ev.text } : {}));
              }
            }
          }
        } catch (e) {
          finish(reject, e && e.code ? e : err("upstream_error", String(e && e.message ? e.message : e)));
        } finally {
          if (signal) signal.removeEventListener("abort", onAbort);
        }
      })();
    });
  }

  const sample = (input, options) => ask(input, options, false);
  sample.json = (input, options) => ask(input, options, true);
  sample.limits = async () => ({ maxPromptBytes: 262144, tools: { maxCount: 48 } });

  /* ---- window.claude ----------------------------------------------------------- */

  const NAMESPACES = { db, user, assets, downloads, sample };
  window.claude = {
    use: (name) => Promise.resolve(Object.prototype.hasOwnProperty.call(NAMESPACES, name) ? NAMESPACES[name] : null),
  };

  /* ---- Keeping the page in step with the server ------------------------------- */

  let version = Number(boot.version) || 0;
  let state = null;
  let reloadWanted = false;
  let jobKey = null;
  let flagText = null;
  const lineEl = document.createElement("div");
  lineEl.className = "hbl-run";
  lineEl.id = "hbl-run";
  lineEl.setAttribute("role", "status");

  function busyTyping() {
    const a = document.activeElement;
    const TEXT = "textarea, [contenteditable], input:not([type]), input[type=text], input[type=search], input[type=number], input[type=url], input[type=email]";
    if (!a || !(a.matches && a.matches(TEXT))) return false;
    if (a.closest && a.closest(".hbc-panel, .hbl-name")) return false;     // the chat lives outside the tab
    return Boolean(a.value && String(a.value).trim()) || a.closest("dialog[open]") !== null;
  }

  async function reloadData() {
    if (busyTyping()) { reloadWanted = true; return; }
    reloadWanted = false;
    try {
      const res = await fetch("/bundle.json", { cache: "no-store" });
      const next = await res.json();
      if (HB.data && HB.data.replace(next) && typeof HB.reloadData === "function") HB.reloadData();
    } catch (e) {
      console.warn("local.js: could not load the new data:", e);
    }
  }

  function jobWords(job) {
    if (!job) return null;
    const pct = job.total ? Math.round((100 * (job.done || 0)) / job.total) : null;
    return { title: job.title, line: job.line, pct, status: job.status };
  }

  /** The job's tokens against its estimate (serve.py works the estimate out when it queues the job). */
  function costWords(job, running) {
    const est = job.estimate;
    const tokens = Number(job.tokens) || 0;
    const fmt = HB.cost ? HB.cost.tokens : HB.fmtInt;
    const range = est && HB.cost ? HB.cost.range(est) : est ? "about " + HB.fmtInt(est.low) + " to " + HB.fmtInt(est.high) + " tokens" : null;
    if (!range && !tokens) return null;
    if (running) return (tokens ? fmt(tokens) + " tokens of your Claude plan so far" : "Starting") + (range ? ", out of an estimated " + range.replace(/^about /, "") + "." : ".");
    return fmt(tokens) + " tokens of your Claude plan used" + (range ? ", against an estimate of " + range + "." : ".");
  }

  function paintRunLine() {
    const job = state && state.job;
    const last = state && state.last_job;
    const fresh = last && last.finished && (Date.now() - Date.parse(last.finished)) < 5 * 60000;
    const show = job || (fresh ? last : null);
    lineEl.replaceChildren();
    if (!show) return false;
    const w = jobWords(show);
    const head = document.createElement("p");
    head.className = "hbl-run__line";
    const b = document.createElement("b");
    b.textContent = job ? "Running on this computer: " + w.title : w.title + (show.status === "failed" ? ": stopped" : ": done");
    head.append(b, document.createTextNode(w.line && !job ? ". " + w.line : ""));
    lineEl.append(head);
    // While it runs, what Claude is doing now, moving, so the page never looks stuck (19 Oct 2026).
    if (job && HB.working) lineEl.append(HB.working(w.line || "Claude is working on it", { status: false }));
    const cost = costWords(show, Boolean(job));
    if (cost) {
      const p = document.createElement("p");
      p.className = "hbl-run__cost";
      p.textContent = cost;
      lineEl.append(p);
    }
    if (job && w.pct != null) {
      const bar = document.createElement("progress");
      bar.max = 100;
      bar.value = w.pct;
      bar.className = "hbl-run__bar";
      bar.setAttribute("aria-label", "Progress");
      lineEl.append(bar);
    }
    return true;
  }

  function paintTop() {
    const box = document.getElementById("hb-theme");
    if (!box || !HB.h) return;
    const h = HB.h;
    const job = state && state.job;
    const bits = [];
    if (job) {
      const w = jobWords(job);
      bits.push(h("span", { class: "hbl-pill", "data-tone": "accent", title: w.line || "" },
        h("span", { class: "hbl-dot", "aria-hidden": "true" }), w.title + (w.pct != null ? " (" + w.pct + "%)" : "")));
    }
    if (state && state.queued && state.queued.length) bits.push(h("span", { class: "hbl-pill" }, state.queued.length + " waiting"));
    const recent = h("button", { type: "button", class: "btn btn--quiet btn--small", id: "hbl-recent", "aria-haspopup": "dialog" }, "Recent changes");
    recent.addEventListener("click", openRecent);
    const dl = h("a", { class: "btn btn--small", id: "hbl-copy", href: "/api/copy", title: "One file of this page, read-only, to email or upload" }, "Download a copy");
    dl.addEventListener("click", downloadCopy);
    // Clashes, a colleague's job, the Team (or Share) button: views/team.js.
    const team = HB.team && typeof HB.team.top === "function" ? HB.team.top(h, state) : [];
    box.replaceChildren(h("div", { class: "hbl-top" }, bits, team, recent, dl));
  }

  async function downloadCopy(ev) {
    ev.preventDefault();
    const a = ev.currentTarget;
    const label = a.textContent;
    a.textContent = "Preparing...";
    try {
      const res = await fetch("/api/copy", { headers: { "X-HB-Token": TOKEN }, cache: "no-store" });
      if (!res.ok) throw new Error("The copy could not be made (" + res.status + ").");
      const blob = await res.blob();
      const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
      await downloads.save({ filename: m ? m[1] : "benchmark-copy.html", data: blob });
    } catch (e) {
      window.setTimeout(() => { a.textContent = label; }, 3000);
      a.textContent = e.message || "The copy could not be made.";
      return;
    }
    a.textContent = label;
  }

  function openRecent() {
    const h = HB.h;
    if (!h || !HB.drawer) return;
    const items = (state && state.activity) || [];
    const when = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
    const warn = [];
    if (flagText) warn.push(HB.notice("warning", flagText));
    if (state && state.api_key) warn.push(HB.notice("warning", "Claude is using an API key on this computer (for example ANTHROPIC_API_KEY), so its work is billed at API rates, not on your Claude plan."));
    if (state && state.problem) warn.push(HB.notice("critical", ["The page could not be rebuilt.", String(state.problem).slice(-400)]));
    const usage = state && state.last_sample;
    const list = items.length
      ? h("ul", { class: "hbl-recent" }, items.map((it) => h("li", { "data-tone": it.tone },
        h("span", { class: "hbl-recent__at" }, when.format(new Date(it.at))), " ", h("span", null, it.text),
        it.detail ? h("details", null, h("summary", null, "Details"), h("pre", { class: "hbl-recent__detail" }, it.detail)) : null)))
      : h("p", { class: "muted" }, "Nothing yet. Changes you save in this page are applied here, on this computer, as soon as you save them.");
    HB.drawer.open("Recent changes", h("div", { class: "stack" }, warn, list,
      usage ? h("p", { class: "small muted" }, "Claude's last reply used " + HB.fmtInt(usage.processed || 0) + " tokens of your Claude plan.") : null,
      h("p", { class: "small muted" }, "You can close this tab at any time. Your work is saved in the project folder; open the page again to carry on.")),
    { kicker: "On this computer" });
  }

  async function pollState() {
    try {
      const s = await api("GET", "state");
      const before = state;
      state = s;
      me = s.user && s.user.name ? { name: s.user.name, id: s.user.id } : me;
      const key = s.job ? s.job.key : null;
      const jobChanged = key !== jobKey || (before && JSON.stringify(before.last_job) !== JSON.stringify(s.last_job));
      jobKey = key;
      if (s.version > version) {
        version = s.version;
        await reloadData();
      } else if (reloadWanted) {
        await reloadData();
      } else if (jobChanged && typeof HB.render === "function") {
        HB.render();
      }
      const shown = paintRunLine();
      if (HB.runBar && typeof HB.runBar.redraw === "function" && shown) HB.runBar.redraw();
      paintTop();
    } catch (e) {
      if (e && e.code === "revoked") flagText = "This page's link has expired. Run hb serve again and use the new link.";
      else flagText = e && e.message ? e.message : null;
      paintTop();
    }
  }

  HB.local = {
    toolsRun: 0,
    usage: [],
    runLine: () => (paintRunLine() ? lineEl : null),
    noteUsage: (u) => { if (u) { HB.local.usage.push(u); console.info("Claude (this computer):", u.processed, "tokens processed", u); } },
    flag: (text) => { flagText = text; paintTop(); },
    state: () => state,
    refresh: pollState,
    /** GET /api/<path> with the page's token (the Set-up material list, the chat's read_material). */
    get: (path) => fetch("/api/" + path, { headers: { "X-HB-Token": TOKEN }, cache: "no-store" }),
    /** POST /api/<path> with a JSON body (the Set-up chat's saves); Promise of the parsed reply. */
    post: async (path, body) => {
      const res = await fetch("/api/" + path, { method: "POST", headers: { "X-HB-Token": TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify(body || {}), cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && (data.message || (data.error && data.error.message))) || "The page's server refused that.");
      return data;
    },
  };

  const CSS = `
.hbl-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: var(--s2); }
.hbl-pill { display: inline-flex; align-items: center; gap: var(--s1); padding: 0.125rem 0.5rem; border: 1px solid var(--rule); border-radius: 999px; background: var(--surface); color: var(--ink-2); font-size: var(--fs-12); white-space: nowrap; }
.hbl-pill[data-tone="accent"] { border-color: var(--accent); color: var(--ink); }
.hbl-dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--accent); animation: hbl-pulse 1.4s ease-in-out infinite; }
@keyframes hbl-pulse { 50% { opacity: 0.3; } }
@media (prefers-reduced-motion: reduce) { .hbl-dot { animation: none; } }
.hbl-run { display: grid; gap: var(--s1); }
.hbl-run:empty { display: none; }
.hbl-run__line { margin: 0; font-size: var(--fs-12); color: var(--ink-2); }
.hbl-run__line b { color: var(--ink); font-weight: 600; }
.hbl-run__cost { margin: 0; font-size: var(--fs-12); color: var(--ink-2); font-variant-numeric: tabular-nums; }
.hbl-run__bar { width: 100%; height: 0.375rem; accent-color: var(--accent); }
.hbl-recent { display: grid; gap: var(--s2); margin: 0; padding: 0; list-style: none; font-size: var(--fs-14); }
.hbl-recent li[data-tone="critical"] { color: var(--critical, #a3242a); }
.hbl-recent__at { color: var(--ink-2); font-variant-numeric: tabular-nums; }
.hbl-recent__detail { max-height: 12rem; overflow: auto; white-space: pre-wrap; font-size: var(--fs-12); }
.hbl-name__msg:empty { display: none; }
@media (width < 40rem) { .hbl-top { justify-content: flex-start; } }
`;

  function start() {
    if (typeof HB.addStyles === "function") HB.addStyles("local", CSS);
    paintTop();
    pollState();
    setInterval(pollState, STATE_MS);
    window.addEventListener("focusout", () => { if (reloadWanted) setTimeout(() => { if (!busyTyping()) reloadData(); }, 50); });
    setTimeout(() => { if (!me.name) askName(); }, 800);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
