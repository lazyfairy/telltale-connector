#!/usr/bin/env python3
"""
Telltale lean batched AIS relay  —  for LOW-BANDWIDTH satellite links (Iridium GO, etc.).

WHY: offshore, one yacht in the fleet with an AIS receiver + a sat uplink can relay the ~40 nm of
boats around it, travelling with the pack. AIS data is tiny, but posting every 10 s over a 2.4 kbps
Iridium link is death-by-TLS-handshake. This relay sits between a local AIS-catcher and Telltale:
AIS-catcher decodes locally and pushes to this relay (localhost, free); the relay COALESCES to the
latest position per vessel and forwards a GZIPPED batch to Telltale only every BATCH seconds, in ONE
request. Cuts bytes ~90 % and connections ~12x vs posting every 10 s — even a metered sat link barely
notices. On a dropped link the batch is re-buffered and coalesced into the next send, so nothing is
lost and the link isn't hammered.

SETUP (on the relay boat):
  1) AIS-catcher pushes decoded AIS to the relay instead of straight to Telltale:
        AIS-catcher -X off -H http://127.0.0.1:8100 INTERVAL 10
  2) run this relay (it forwards batches to Telltale):
        python3 ais_relay.py --key YOUR_STATION_KEY
  Options: --batch 120 (seconds per send) · --listen 8100 · --telltale https://telltaleracing.com
           --min-move 15 (metres a boat must move to be re-sent) · --max-age 600 (drop fixes older than this)
"""
import argparse, gzip, json, math, threading, time, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request, urllib.error

ap = argparse.ArgumentParser(description="Telltale lean batched AIS relay (for sat links)")
ap.add_argument("--key", required=True, help="your Telltale station key")
ap.add_argument("--telltale", default="https://telltaleracing.com")
ap.add_argument("--listen", type=int, default=8100, help="local port AIS-catcher -H pushes to")
ap.add_argument("--batch", type=int, default=120, help="seconds between forwarded batches")
ap.add_argument("--min-move", type=float, default=15.0, help="metres a vessel must move to be re-sent")
ap.add_argument("--max-age", type=int, default=600, help="drop fixes older than this many seconds")
args = ap.parse_args()

INGEST = args.telltale.rstrip("/") + "/api/ais-ingest?key=" + args.key
_buf = {}                 # mmsi -> latest normalised record (coalesced)
_last = {}                # mmsi -> (lat, lon, t) last actually forwarded
_lock = threading.Lock()


def _norm(it):
    """Accept AIS-catcher's field spellings; keep only the small set Telltale needs."""
    def g(*ks):
        for k in ks:
            if isinstance(it, dict) and it.get(k) is not None:
                return it[k]
        return None
    mmsi, lat, lon = g("mmsi", "MMSI"), g("lat", "latitude", "Latitude"), g("lon", "lng", "longitude", "Longitude")
    if mmsi is None or lat is None or lon is None:
        return None
    try:
        r = {"mmsi": str(mmsi), "lat": round(float(lat), 6), "lon": round(float(lon), 6)}
    except (TypeError, ValueError):
        return None
    for k, src in (("sog", ("speed", "sog", "SOG")), ("cog", ("course", "cog", "COG"))):
        val = g(*src)
        if val is not None:
            try: r[k] = round(float(val), 1)
            except (TypeError, ValueError): pass
    nm = g("shipname", "name", "ShipName")
    if nm: r["name"] = str(nm).strip()[:40]
    ty = g("shiptype", "type", "ShipType")
    if ty is not None:
        try: r["type"] = int(ty)
        except (TypeError, ValueError): pass
    r["t"] = int(time.time())     # the relay receives within seconds of the fix
    return r


def _metres(a_lat, a_lon, b_lat, b_lon):
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon)
    x = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(min(1, math.sqrt(x)))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                       # quiet — AIS-catcher pushes constantly

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else b"[]"
            data = json.loads(body or b"[]")
            if isinstance(data, dict):
                items = data.get("msgs") or data.get("vessels") or []
            else:
                items = data if isinstance(data, list) else []
            got = 0
            with _lock:
                for it in items:
                    r = _norm(it)
                    if r:
                        _buf[r["mmsi"]] = r          # keep only the latest per vessel
                        got += 1
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        except Exception:
            self.send_response(200); self.end_headers()


def _flush_loop():
    while True:
        time.sleep(args.batch)
        now = int(time.time())
        with _lock:
            batch = [r for r in _buf.values() if now - r["t"] <= args.max_age]
            _buf.clear()
        send = []
        for r in batch:
            prev = _last.get(r["mmsi"])
            # skip a vessel that has barely moved AND was sent recently (further trims sat bytes)
            if prev and _metres(prev[0], prev[1], r["lat"], r["lon"]) < args.min_move and (r["t"] - prev[2]) < args.batch * 3:
                continue
            send.append(r)
        if not send:
            continue
        payload = gzip.compress(json.dumps({"msgs": send}, separators=(",", ":")).encode())
        req = urllib.request.Request(INGEST, data=payload,
                                     headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                for r in send:
                    _last[r["mmsi"]] = (r["lat"], r["lon"], r["t"])   # only mark sent on success
                print("[relay] sent %d vessels  %d bytes gz  (%s)" % (len(send), len(payload), resp.status), flush=True)
        except Exception as e:
            with _lock:                                              # re-buffer so the next batch retries, coalesced
                for r in send:
                    _buf.setdefault(r["mmsi"], r)
            print("[relay] send failed (%s) - re-buffered %d vessels for next batch" % (str(e)[:80], len(send)), flush=True)


def main():
    threading.Thread(target=_flush_loop, daemon=True, name="relay-flush").start()
    print("[relay] listening on 127.0.0.1:%d  ->  batching every %ds to %s" % (args.listen, args.batch, args.telltale), flush=True)
    print("[relay] point AIS-catcher at it:  AIS-catcher -X off -H http://127.0.0.1:%d INTERVAL 10" % args.listen, flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", args.listen), _Handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
