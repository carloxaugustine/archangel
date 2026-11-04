#!/usr/bin/env python3
"""Local dashboard: rebuilds the site, serves it on 127.0.0.1 only, and saves Approve-tab clicks to approvals.json.

    ./dashboard/serve.py            # http://127.0.0.1:8377
    ./dashboard/serve.py --port N
"""
import datetime as dt
import functools
import http.server
import json
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
STORE = HERE / "approvals.json"


def load():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def local_request(self):
        """Refuse requests from other websites (DNS rebinding, cross-site POST). This page holds personal data."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        return host in ("127.0.0.1", "localhost") and (not origin or re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin))

    def do_GET(self):
        if not self.local_request():
            return self.send_json({"error": "forbidden"}, 403)
        if self.path.split("?")[0] == "/api/approvals":
            return self.send_json(load())
        return super().do_GET()

    def do_POST(self):
        if not self.local_request() or self.path.split("?")[0] != "/api/approvals":
            return self.send_json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        except ValueError:
            return self.send_json({"error": "bad json"}, 400)
        jid = str(body.get("id", ""))
        if not re.fullmatch(r"\d{1,6}", jid):
            return self.send_json({"error": "bad id"}, 400)
        recs = load()
        action = body.get("action")
        if action == "undo":
            recs.pop(jid, None)
            STORE.write_text(json.dumps(recs, indent=1))
            return self.send_json({"ok": True, "id": jid, "action": "undo"})
        if action not in ("approve", "reject"):
            return self.send_json({"error": "bad action"}, 400)
        answers = {k: v[:20000] for k, v in (body.get("answers") or {}).items() if isinstance(v, str) and len(k) < 80}
        rec = {"action": action, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "answers": answers}
        recs[jid] = rec
        STORE.write_text(json.dumps(recs, indent=1))
        self.send_json({"ok": True, "id": jid, **rec})

    def log_message(self, *a):
        pass


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8377
    subprocess.run([str(HERE / "build_site.py")], check=True)
    url = f"http://127.0.0.1:{port}"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(DIST)))
    print(f"Dashboard: {url}  (this computer only; Ctrl-C to stop)")
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
