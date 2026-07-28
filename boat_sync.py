#!/usr/bin/env python3
"""Boat-local tactical sync — P1 STUB (see BOAT-LOCAL-SYNC-SPEC.md, Appendix A).

A tiny LAN-only web server for the boat's Pi/connector. It (1) serves the tactician PWA over HTTP on the
boat wifi so crew phones load it same-origin (sidesteps HTTPS->HTTP mixed content, works with NO internet),
and (2) exposes a same-origin tactical store so every device on the boat shares the same pinged marks and
start-line ends. Best-accuracy-wins merge: the tightest fix on the boat automatically becomes the shared one.

BOUNDARY: this NEVER calls out to telltaleracing.com. Boat-crew-only data; it must not leave the boat LAN.

Status: STUB — not yet imported by signalk_telltale.py. Pure stdlib, no deps. Run standalone to try it:
    python3 boat_sync.py --port 8137 --code AB12 --webroot ./webroot --race demo
"""
import argparse
import hmac
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ----------------------------------------------------------------------------- config / constants
DEFAULT_PORT = 8137
STALE_S = {"mark": 3600, "line-rc": 1200, "line-pin": 1200}   # marks drift slower than a line
STATIC_ROUTES = {"/": "start.html", "/start": "start.html"}  # friendly aliases; everything else maps path->file
STATIC_EXT_CT = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
                 ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
                 ".ico": "image/x-icon", ".json": "application/json", ".webmanifest": "application/manifest+json",
                 ".woff2": "font/woff2", ".woff": "font/woff"}


# ----------------------------------------------------------------------------- store (best-accuracy-wins)
class TacticalStore:
    """In-memory tactical state per race id, mirrored to a JSON file. Thread-safe. `rev` bumps on change
    so clients can poll cheaply (ETag/304) and SSE can push."""

    def __init__(self, path, boat=""):
        self.path = path
        self.boat = boat
        self._lock = threading.Lock()
        self._data = {}          # {race: {"marks":{name:ping}, "line":{"rc":ping,"pin":ping}}}
        self._rev = 0
        self._subs = []          # list of (queue) for SSE
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self._data = d.get("races", {})
            self._rev = int(d.get("rev", 0))
        except (OSError, ValueError):
            self._data, self._rev = {}, 0

    def _persist(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"rev": self._rev, "races": self._data}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    @staticmethod
    def _accept(stored, incoming, kind, force=False):
        """The core rule. Tighter fix wins; a stale end is refreshed; ungraded never clobbers graded."""
        if force or stored is None:
            return True
        if incoming["t"] - stored["t"] > STALE_S.get(kind, 3600):
            return True
        si, ci = stored.get("acc"), incoming.get("acc")
        if si is None:
            return True
        if ci is None:
            return False
        return ci < si

    def snapshot(self, race):
        with self._lock:
            r = self._data.get(race) or {"marks": {}, "line": {}}
            return {"rev": self._rev, "boat": self.boat, "race": race,
                    "marks": dict(r.get("marks", {})), "line": dict(r.get("line", {}))}

    def upsert(self, race, kind, ping, name=None, force=False):
        """Apply one ping under the merge rule. Returns (changed, snapshot)."""
        with self._lock:
            r = self._data.setdefault(race, {"marks": {}, "line": {}})
            if kind == "mark":
                cur = r["marks"].get(name)
                if self._accept(cur, ping, "mark", force):
                    r["marks"][name] = ping
                    changed = True
                else:
                    changed = False
            elif kind in ("line-rc", "line-pin"):
                slot = "rc" if kind == "line-rc" else "pin"
                cur = r["line"].get(slot)
                if self._accept(cur, ping, kind, force):
                    r["line"][slot] = ping
                    changed = True
                else:
                    changed = False
            else:
                return False, self.snapshot(race)
            if changed:
                self._rev += 1
                self._persist()
            snap = {"rev": self._rev, "boat": self.boat, "race": race,
                    "marks": dict(r.get("marks", {})), "line": dict(r.get("line", {}))}
        if changed:
            self._fanout(snap)
        return changed, snap

    # ---- SSE fan-out ----
    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=32)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _fanout(self, snap):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(snap)
            except Exception:
                pass


# ----------------------------------------------------------------------------- HTTP handler
def _is_private_ip(ip):
    """RFC1918 (+loopback) only — a second guarantee behind LAN-interface binding."""
    try:
        if ip in ("127.0.0.1", "::1"):
            return True
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4:
            return False
        a, b = parts[0], parts[1]
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    except (ValueError, AttributeError):
        return False


def make_handler(store, code, webroot):
    code_b = (code or "").encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "TelltaleBoatSync/0.1"

        def log_message(self, *a):
            pass  # quiet

        # --- helpers ---
        def _deny(self, why="forbidden", status=403):
            self._json({"error": why}, status)

        def _json(self, obj, status=200, extra=None):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _lan_ok(self):
            return _is_private_ip(self.client_address[0])

        def _code_ok(self):
            got = (self.headers.get("X-Boat-Code") or "").encode()
            return bool(code_b) and hmac.compare_digest(got, code_b)

        def _read_json(self, cap=8192):
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n <= 0 or n > cap:
                return None
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None

        # --- routing ---
        def do_GET(self):
            if not self._lan_ok():
                return self._deny("LAN only")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/boat/health":
                snap = store.snapshot((q.get("race", ["open"])[0]))
                return self._json({"ok": True, "boat": store.boat, "rev": snap["rev"]})
            if u.path.startswith("/boat/"):
                if not self._code_ok():
                    return self._deny("bad or missing X-Boat-Code", 401)
                race = q.get("race", ["open"])[0]
                if u.path == "/boat/tactical":
                    snap = store.snapshot(race)
                    etag = '"rev-%d"' % snap["rev"]
                    if self.headers.get("If-None-Match") == etag:
                        self.send_response(304); self.end_headers(); return
                    return self._json(snap, 200, {"ETag": etag})
                if u.path == "/boat/tactical/events":
                    return self._sse(race)
                return self._deny("unknown", 404)
            return self._static(u.path)

        def do_POST(self):
            if not self._lan_ok():
                return self._deny("LAN only")
            u = urlparse(self.path)
            if u.path != "/boat/tactical":
                return self._deny("unknown", 404)
            if not self._code_ok():
                return self._deny("bad or missing X-Boat-Code", 401)
            d = self._read_json()
            if not d:
                return self._deny("bad json", 400)
            race = (parse_qs(u.query).get("race", [d.get("race", "open")])[0])
            ping = _clean_ping(d)
            if ping is None:
                return self._deny("bad ping", 400)
            kind = d.get("kind")
            name = (d.get("name") or "")[:40] if kind == "mark" else None
            if kind not in ("mark", "line-rc", "line-pin") or (kind == "mark" and not name):
                return self._deny("bad kind/name", 400)
            _changed, snap = store.upsert(race, kind, ping, name=name, force=bool(d.get("force")))
            return self._json(snap)

        # --- SSE ---
        def _sse(self, race):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = store.subscribe()
            try:
                self.wfile.write(b": hello\n\n")
                self.wfile.write(("data: %s\n\n" % json.dumps(store.snapshot(race))).encode())
                self.wfile.flush()
                while True:
                    try:
                        snap = q.get(timeout=20)
                        self.wfile.write(("data: %s\n\n" % json.dumps(snap)).encode())
                    except Exception:
                        self.wfile.write(b": ping\n\n")   # keep-alive
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                store.unsubscribe(q)

        # --- static PWA: serve any file UNDER webroot (traversal-safe). Maps request path -> file so the
        #     PWA's absolute refs (/start, /static/leaflet.js, icons, boat-sync-client.js) all resolve. ---
        def _static(self, path):
            if not webroot:
                return self._deny("not found", 404)
            rel = STATIC_ROUTES.get(path) or path.lstrip("/")
            root = os.path.realpath(webroot)
            fp = os.path.realpath(os.path.join(root, rel))
            if not (fp == root or fp.startswith(root + os.sep)):   # traversal guard
                return self._deny("forbidden", 403)
            if os.path.basename(fp).startswith("."):               # no dotfiles
                return self._deny("not found", 404)
            if not os.path.isfile(fp):
                return self._deny("not found", 404)
            ext = os.path.splitext(fp)[1].lower()
            ct = STATIC_EXT_CT.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _clean_ping(d):
    """Validate + clamp one ping payload. Returns a clean dict or None."""
    try:
        lat, lon = float(d["lat"]), float(d["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        acc = d.get("acc")
        acc = int(round(float(acc))) if acc is not None else None
        return {"lat": lat, "lon": lon, "acc": acc,
                "by": (str(d.get("by") or "")[:40]), "t": int(d.get("t") or time.time())}
    except (KeyError, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- LAN binding
def lan_bind_ip(iface=None):
    """Pick the boat-LAN IP to bind. If `iface` unset, best-effort the primary private IPv4.
    NEVER returns 0.0.0.0 — LAN-only is the guarantee. Falls back to loopback if nothing private found."""
    if iface:
        try:
            import fcntl
            import struct
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            return socket.inet_ntoa(fcntl.ioctl(
                s.fileno(), 0x8915, struct.pack("256s", iface[:15].encode()))[20:24])
        except Exception:
            pass
    # heuristic: the private IPv4 the OS would use for an outbound LAN route
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # doesn't send; just picks a source addr
        ip = s.getsockname()[0]
        s.close()
        if _is_private_ip(ip):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


# ----------------------------------------------------------------------------- entry point
def run(port=DEFAULT_PORT, code="", webroot="", store_path="boat-tactical.json",
        boat="", iface=None):
    store = TacticalStore(store_path, boat=boat)
    handler = make_handler(store, code, webroot)
    host = lan_bind_ip(iface)
    httpd = ThreadingHTTPServer((host, port), handler)
    print("boat-sync: serving on http://%s:%d  (LAN-only, code=%s, webroot=%s)"
          % (host, port, "set" if code else "NONE", webroot or "-"))
    return httpd, store


def main():
    ap = argparse.ArgumentParser(description="Telltale boat-local tactical sync (P1 stub)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--code", default="", help="pairing code required on /boat/* calls")
    ap.add_argument("--webroot", default="", help="dir holding the tactician PWA to serve at /start")
    ap.add_argument("--store", default="boat-tactical.json")
    ap.add_argument("--boat", default="")
    ap.add_argument("--iface", default=None, help="LAN interface to bind (auto if unset)")
    a = ap.parse_args()
    httpd, _ = run(a.port, a.code, a.webroot, a.store, a.boat, a.iface)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
