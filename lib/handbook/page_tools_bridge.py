"""The page's tools, offered to a `claude -p` session as one small MCP server over stdio.

`hb serve` starts a chat turn as `claude -p` with this script as its only MCP server (named `page`).
When Claude calls a tool, the script asks the local server (HB_BRIDGE_URL, with HB_BRIDGE_SECRET),
the server hands the call to the page, the page runs its own tool function and posts the result
back, and the script returns it to Claude. Claude keeps calling tools until it writes its answer.

Standard library only, and self-contained: it is run as `python3 -I page_tools_bridge.py` and never
imports the handbook package. Environment:
    HB_BRIDGE_URL      http://127.0.0.1:PORT/bridge/<call id>
    HB_BRIDGE_SECRET   the call's own secret (not the page's token)
    HB_BRIDGE_TOOLS    a JSON file: [{"name", "description", "inputSchema"}, ...]
"""
import json
import os
import sys
import urllib.error
import urllib.request

WAIT_S = 170          # a page tool may take up to 150 s (the sample contract), plus the round trip


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def tools():
    try:
        with open(os.environ["HB_BRIDGE_TOOLS"], encoding="utf-8") as f:
            items = json.load(f)
    except (KeyError, OSError, ValueError):
        return []
    out = []
    for t in items if isinstance(items, list) else []:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object", "properties": {}}
            out.append({"name": t["name"], "description": str(t.get("description") or ""), "inputSchema": schema})
    return out


def call_page(name, arguments):
    body = json.dumps({"name": name, "input": arguments if isinstance(arguments, dict) else {}}).encode("utf-8")
    req = urllib.request.Request(os.environ.get("HB_BRIDGE_URL", "") + "/tool", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-HB-Bridge": os.environ.get("HB_BRIDGE_SECRET", "")})
    try:
        with urllib.request.urlopen(req, timeout=WAIT_S) as res:
            data = json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "The page did not answer this tool call (" + type(e).__name__ + ").", True
    if data.get("ok"):
        value = data.get("value")
        return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), False
    return "Error: " + str(data.get("error") or "the tool failed"), True


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:                     # a notification: nothing to answer
            continue
        params = msg.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "page", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            text, is_error = call_page(params.get("name"), params.get("arguments"))
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})


if __name__ == "__main__":
    main()
