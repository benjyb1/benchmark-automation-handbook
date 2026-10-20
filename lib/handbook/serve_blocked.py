"""The small page `hb serve` shows instead of the benchmark when the project's files are newer than this copy of
the tool understands (upgrade.ProjectTooNew): for example a colleague with a newer version opened a shared
project, or the owner went back to the previous version. It says so in plain words and offers the update; once
the tool is updated it restarts into the real page. Nothing in the project is read beyond format.json, and
nothing is written."""
from __future__ import annotations
import html
import json
import secrets
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import updates
from .folder import HandbookError, read_json
from .updater import Updater, respawn, restart_when_idle

PAGE = """<!doctype html><html lang="en-GB"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Update the benchmark tool</title>
<style>
:root { color-scheme: light dark; --ink: #1d1d1f; --ink-2: #5a5a60; --bg: #fbfbfa; --accent: #2457c5; --rule: #d9d9de; }
@media (prefers-color-scheme: dark) { :root { --ink: #ededf0; --ink-2: #a8a8b0; --bg: #17171a; --accent: #7aa2ff; --rule: #3a3a40; } }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.5 system-ui, sans-serif; }
main { max-width: 36rem; margin: 4rem auto; padding: 0 1rem; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
p { margin: 0 0 1rem; } .muted { color: var(--ink-2); font-size: 0.875rem; }
button { font: inherit; padding: 0.5rem 1rem; border-radius: 0.5rem; border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; }
button[disabled] { opacity: 0.6; cursor: default; }
ul { padding-left: 1.2rem; }
</style>
<main>
<h1>__NAME__ needs a newer version of the tool</h1>
<p>__WORDS__</p>
<div id="what"></div>
<p><button id="go" type="button">Update now</button></p>
<p id="said" role="status"></p>
<p class="muted">__PRIVACY__ Your project's files are not changed by updating; the new version opens them as they are.</p>
</main>
<script>
(function () {
  var TOKEN = __TOKEN__;
  var go = document.getElementById("go"), said = document.getElementById("said"), what = document.getElementById("what");
  function api(method, body) {
    return fetch("/api/update", { method: method, headers: { "X-HB-Token": TOKEN, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined, cache: "no-store" }).then(function (r) { return r.json(); });
  }
  function paint(s) {
    if (s.error && s.error.message) { said.textContent = s.error.message; go.disabled = false; return; }
    what.textContent = "";
    if (s.latest && s.available) {
      var p = document.createElement("p"); p.textContent = "Version " + s.latest + " is ready to install (you have " + s.current + ").";
      what.appendChild(p);
    }
    if (s.key_note) { var k = document.createElement("p"); k.textContent = s.key_note; what.appendChild(k); }
    if (s.words) said.textContent = s.words;
    if (s.phase === "restarting") waitForNew();
    go.disabled = Boolean(s.phase && ["starting", "downloading", "waiting", "swapping", "restarting"].indexOf(s.phase) >= 0);
  }
  function waitForNew() {
    setTimeout(function again() {
      fetch("/hb-alive", { cache: "no-store" }).then(function (r) { if (r.ok) location.reload(); else setTimeout(again, 1500); })
        .catch(function () { setTimeout(again, 1500); });
    }, 3000);
  }
  go.addEventListener("click", function () {
    go.disabled = true; said.textContent = "Starting the update...";
    api("POST", { action: "install" }).then(paint).catch(function () { said.textContent = "The page did not answer."; go.disabled = false; });
  });
  api("POST", { action: "check" }).then(paint).catch(function () {});
  setInterval(function () { api("GET").then(paint).catch(function () {}); }, 2000);
})();
</script>
</html>"""


class BlockedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: Path, err, port: int = 0, token: str | None = None):
        super().__init__(("127.0.0.1", port), BlockedHandler)
        self.path_ = Path(path)
        self.err = err
        self.token = token or secrets.token_urlsafe(24)
        self.alive_key = None
        self.marker = None
        self.restarting = False
        self.updater = Updater(self)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def project_path(self) -> Path:
        return self.path_

    def first_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def busy(self):
        return None

    def restart(self) -> None:
        if self.restarting:
            return
        self.restarting = True
        respawn(self, self.path_, self.marker)

    def name(self) -> str:
        return (read_json(self.path_ / "project.json", {}) or {}).get("name") or self.path_.name


class BlockedHandler(BaseHTTPRequestHandler):
    server: BlockedServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _cookie_ok(self) -> bool:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except cookies.CookieError:
            return False
        c = jar.get(f"hb_{self.server.port}")
        return bool(c) and secrets.compare_digest(c.value, self.server.token)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        srv = self.server
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{srv.port}", f"localhost:{srv.port}"):
            return self._json({"error": {"message": "This page only answers on 127.0.0.1."}}, 403)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/hb-alive":
            return self._json({"project": srv.alive_key, "blocked": True})
        if url.path == "/" and query.get("t"):
            if secrets.compare_digest(query["t"][0], srv.token):
                return self._send(303, b"", "text/plain", {"Location": "/", "Set-Cookie":
                                  f"hb_{srv.port}={srv.token}; Path=/; HttpOnly; SameSite=Strict"})
            return self._json({"error": {"message": "That link is out of date."}}, 403)
        if not self._cookie_ok():
            return self._send(403, b"Open this page from the link the tool printed.", "text/plain; charset=utf-8")
        if url.path == "/" and method == "GET":
            page = (PAGE.replace("__NAME__", html.escape(srv.name()))
                    .replace("__WORDS__", html.escape(str(srv.err)))
                    .replace("__PRIVACY__", html.escape(updates.status(check=False)["privacy"]))
                    .replace("__TOKEN__", json.dumps(srv.token)))
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if not secrets.compare_digest(self.headers.get("X-HB-Token") or "", srv.token):
            return self._json({"error": {"message": "Missing the page's token."}}, 403)
        try:
            if url.path == "/api/state":
                return self._json({"job": None, "queued": [], "chatting": False})
            if url.path == "/api/update/restart" and method == "POST":
                restart_when_idle(srv)
                return self._json({"ok": True})
            if url.path == "/api/update":
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    action = (json.loads(self.rfile.read(n) or b"{}") or {}).get("action")
                    if action == "install":
                        srv.updater.start("install")
                    elif action == "check":
                        updates.status(check=True, force=True)
                return self._json({**updates.status(check=False), **srv.updater.public()})
        except HandbookError as e:
            return self._json({"error": {"message": str(e)}}, 400)
        return self._json({"error": {"message": "Not found."}}, 404)
