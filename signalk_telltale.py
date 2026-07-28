#!/usr/bin/env python3
"""Signal K -> Telltale connector.

Reads a boat's (or club receiver's) Signal K server and pushes to Telltale:
  - the boat's OWN instruments  -> /api/live-ping   (position + wind / speed / heading / depth / heel)
  - other vessels' AIS          -> /api/ais-ingest  (station-tagged, so Telltale can record & publish it)

Signal K already decodes NMEA-0183, NMEA-2000 (via canboat) and AIS into one model, so this connector
works the same whether the boat is 0183, N2K, or SDR-AIS. We don't re-decode anything — we just forward.

  python3 signalk_telltale.py --telltale https://telltaleracing.com \
      --race thursday-twilight --boat "Laissez Faire" --station-key YOUR_STATION_KEY

Defaults: Signal K at http://localhost:3000. Runs forever, pushing every --interval seconds.
Leave off --boat to push AIS only (a shore club station); leave off --station-key to push own-boat only.
Stdlib only — runs on any Pi.
"""
import sys, os, json, time, math, argparse, urllib.request, socket, subprocess, re

KN = 1.94384          # m/s  -> knots
DEG = 180.0 / math.pi  # rad  -> degrees

KIT_VERSION = "1.4.0"  # Boat-Kit connector version — stamped into every ping so Telltale can track fleet health
DEVICE = {}            # {kv, host, sk} filled once at startup, attached to each live-ping (see build_device)
BOAT_KEY = ""          # per-boat WRITE key — lets us post live as a CLAIMED/protected boat (else 401). Rides in
                       # the ping body as `bk` so it survives the store-and-forward queue (back-filled pings re-post the body)
_LAST_SIP_PUSH = 0.0   # wall-clock of the last live upload in --sip mode (rate-limits uploads without throttling the local log)


def build_device(sk):
    """One-time device fingerprint stamped into pings: connector version, hostname, Signal K version.
    Lets Telltale show which kits are online and which are behind (fleet-health view). Best-effort."""
    dev = {"kv": KIT_VERSION}
    try:
        dev["host"] = socket.gethostname()[:40]
    except Exception:
        pass
    for url, path in ((sk + "/signalk/v1/api/server/version", ("version",)),
                      (sk + "/signalk", ("server", "version"))):   # newer/older SK expose version differently
        try:
            info = _get(url, timeout=5)
            v = info
            for p in path:
                v = v.get(p) if isinstance(v, dict) else None
            if v:
                dev["sk"] = str(v)[:40]
                break
        except Exception:
            pass
    return dev


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _post(url, payload, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def val(node, *path):
    """Walk a Signal K vessel dict; each leaf is usually wrapped as {'value': ...}."""
    cur = node
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, dict) and "value" in cur:
        cur = cur["value"]
    return cur


def num(x, mul=1.0):
    try:
        return round(float(x) * mul, 3)
    except (TypeError, ValueError):
        return None


def build_self(v, race, boat):
    """Build a /api/live-ping payload from the boat's own Signal K data."""
    pos = val(v, "navigation", "position")
    if not isinstance(pos, dict) or pos.get("latitude") is None:
        return None
    p = {"race": race, "boat": boat, "lat": pos["latitude"], "lon": pos["longitude"]}

    def setk(k, path, mul=1.0):
        x = num(val(v, *path), mul)
        if x is not None:
            p[k] = x
    setk("sog", ("navigation", "speedOverGround"), KN)
    setk("cog", ("navigation", "courseOverGroundTrue"), DEG)
    setk("stw", ("navigation", "speedThroughWater"), KN)
    setk("hdg", ("navigation", "headingTrue"), DEG)
    if "hdg" not in p:
        setk("hdg", ("navigation", "headingMagnetic"), DEG)
    setk("aws", ("environment", "wind", "speedApparent"), KN)
    setk("awa", ("environment", "wind", "angleApparent"), DEG)
    setk("tws", ("environment", "wind", "speedTrue"), KN)
    setk("twa", ("environment", "wind", "angleTrueWater"), DEG)
    if "twa" not in p:
        setk("twa", ("environment", "wind", "angleTrueGround"), DEG)
    setk("twd", ("environment", "wind", "directionTrue"), DEG)
    setk("depth", ("environment", "depth", "belowTransducer"))
    att = val(v, "navigation", "attitude")
    if isinstance(att, dict):
        if att.get("roll") is not None:
            p["heel"] = num(att["roll"], DEG)
        if att.get("pitch") is not None:
            p["pitch"] = num(att["pitch"], DEG)
    return p


def build_ais(vessels, self_mmsi):
    """Build a list of AIS-target dicts for /api/ais-ingest (everyone but ourselves)."""
    out = []
    for vid, v in vessels.items():
        if not isinstance(v, dict):
            continue
        mmsi = v.get("mmsi")
        if not mmsi or str(mmsi) == str(self_mmsi):
            continue
        pos = val(v, "navigation", "position")
        if not isinstance(pos, dict) or pos.get("latitude") is None:
            continue
        a = {"mmsi": str(mmsi), "lat": pos["latitude"], "lon": pos["longitude"]}
        for k, path, mul in (("sog", ("navigation", "speedOverGround"), KN),
                             ("cog", ("navigation", "courseOverGroundTrue"), DEG)):
            x = num(val(v, *path), mul)
            if x is not None:
                a[k] = x
        nm = v.get("name") or val(v, "name")
        if nm:
            a["name"] = nm
        st = val(v, "design", "aisShipType")
        if isinstance(st, dict) and st.get("id") is not None:
            a["type"] = st["id"]
        elif isinstance(st, (int, float)):
            a["type"] = int(st)
        out.append(a)
    return out


def _post_ok(url, body):
    r = _post(url, body)
    return bool(isinstance(r, dict) and r.get("ok"))

# ---- always-log-to-SD: never lose a race, even with zero internet ----
def append_log(datadir, rec, keep_days):
    d = os.path.join(datadir, "log"); os.makedirs(d, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with open(os.path.join(d, "track-%s.jsonl" % day), "a") as f:
        f.write(json.dumps(rec) + "\n")
    cut = time.time() - keep_days * 86400            # prune old daily logs
    for fn in os.listdir(d):
        try:
            if os.path.getmtime(os.path.join(d, fn)) < cut:
                os.remove(os.path.join(d, fn))
        except Exception:
            pass

# ---- store-and-forward: buffer failed pushes, flush when the internet returns ----
def _qpath(datadir): return os.path.join(datadir, "queue.jsonl")

def enqueue(datadir, kind, body, max_items):
    os.makedirs(datadir, exist_ok=True)
    q = _qpath(datadir)
    with open(q, "a") as f:
        f.write(json.dumps({"kind": kind, "body": body}) + "\n")
    try:                                             # bound the backlog — keep the newest max_items
        lines = open(q).read().splitlines()
        if len(lines) > max_items:
            open(q, "w").write("\n".join(lines[-max_items:]) + "\n")
    except Exception:
        pass

def flush_queue(datadir, tt, station_key):
    q = _qpath(datadir)
    if not os.path.exists(q):
        return 0
    try:
        lines = [l for l in open(q).read().splitlines() if l.strip()]
    except Exception:
        return 0
    sent, remaining, stalled = 0, [], False
    for line in lines:
        if stalled:
            remaining.append(line); continue
        try:
            item = json.loads(line)
        except Exception:
            continue                                 # drop a corrupt line
        if item.get("kind") == "ping":
            ok = _post_ok(tt + "/api/live-ping", item["body"])
        elif item.get("kind") == "ais" and station_key:
            ok = _post_ok(tt + "/api/ais-ingest?key=" + station_key, item["body"])
        else:
            ok = True                                # nothing we can send (no key) -> drop
        if ok:
            sent += 1
        else:
            stalled = True; remaining.append(line)   # still offline -> keep the rest in order
    if remaining:
        open(q, "w").write("\n".join(remaining) + "\n")
    else:
        try: os.remove(q)
        except Exception: pass
    return sent


def poll_once(sk, tt, race, boat, station_key, private, datadir, keep_days, max_queue, share=True, defer=False,
              sip=False, sip_interval=120, fleet=None):
    self_id = _get(sk + "/signalk/v1/api/self")
    vessels = _get(sk + "/signalk/v1/api/vessels")
    self_key = self_id.replace("vessels.", "", 1) if isinstance(self_id, str) else None
    selfv = vessels.get(self_key) if self_key else None
    self_mmsi = selfv.get("mmsi") if isinstance(selfv, dict) else None
    now = int(time.time())

    p = build_self(selfv, race, boat) if (boat and isinstance(selfv, dict)) else None
    if p and private:
        p["private"] = True
    if p and DEVICE:                              # stamp kit version + host so Telltale can track fleet health
        p["dev"] = DEVICE
    if p and BOAT_KEY:                            # authenticate as our claimed boat (body, so it back-fills too)
        p["bk"] = BOAT_KEY
    ais = build_ais(vessels, self_mmsi) if station_key else []

    # 1) ALWAYS log locally first — this is the guaranteed capture, internet or not
    append_log(datadir, {"t": now, "self": p, "ais": ais}, keep_days)

    # --no-share: keep the local log (your data stays yours, exportable) but DON'T push to Telltale.
    # Sharing is the default; opting out never locks you out of your own data.
    if not share:
        print("  %s | self:%s ais:%d | logged locally (sharing OFF)" %
              (time.strftime("%H:%M:%S"), "y" if p else "-", len(ais)), flush=True)
        return

    # --defer: at sea, DON'T touch the internet — just buffer everything (no Starlink/cellular data used).
    # Run WITHOUT --defer back on cheap marina/club WiFi and the whole trip flushes in one go.
    if defer:
        if p:
            enqueue(datadir, "ping", dict(p, t=now), max_queue)
        if ais:
            enqueue(datadir, "ais", {"vessels": [dict(v, t=now) for v in ais]}, max_queue)
        qn = sum(1 for _ in open(_qpath(datadir))) if os.path.exists(_qpath(datadir)) else 0
        print("  %s | self:%s ais:%d | DEFERRED (buffered %d, uploads ashore)" %
              (time.strftime("%H:%M:%S"), "y" if p else "-", len(ais), qn), flush=True)
        return

    # --sip: keep logging every cycle (above), but only UPLOAD live on the slow cadence — a light
    # trickle over a metered link. The FULL signal set is still published, just less often; add --fleet
    # to trickle only the race boats instead of every harbour target.
    global _LAST_SIP_PUSH
    if sip and (now - _LAST_SIP_PUSH) < sip_interval:
        print("  %s | self:%s ais:%d | SIP: logged, next upload in %ds" %
              (time.strftime("%H:%M:%S"), "y" if p else "-", len(ais),
               int(sip_interval - (now - _LAST_SIP_PUSH))), flush=True)
        return
    if sip:
        _LAST_SIP_PUSH = now

    # republish the selected boats only if a --fleet allow-list is set (own boat is always sent); else all
    push_ais = [a for a in ais if (not fleet) or str(a.get("mmsi")) in fleet] if ais else []

    # 2) flush any backlog (in order); if we're offline it stalls and we buffer below
    flushed = flush_queue(datadir, tt, station_key)

    # 3) push the current fix live; on failure, queue it (except in --sip, which is a live-only trickle —
    #    a missed sip stays in the local log rather than back-filling full-res later over the metered link)
    if p:
        if not _post_ok(tt + "/api/live-ping", p) and not sip:
            enqueue(datadir, "ping", dict(p, t=now), max_queue)
    if push_ais:
        if not _post_ok(tt + "/api/ais-ingest?key=" + station_key, {"vessels": push_ais}) and not sip:
            enqueue(datadir, "ais", {"vessels": [dict(v, t=now) for v in push_ais]}, max_queue)

    qn = 0
    try: qn = sum(1 for _ in open(_qpath(datadir))) if os.path.exists(_qpath(datadir)) else 0
    except Exception: pass
    print("  %s | self:%s ais:%d->%d | flushed %d | queued %d%s" %
          (time.strftime("%H:%M:%S"), "y" if p else "-", len(ais), len(push_ais), flushed, qn,
           " | SIP" if sip else ""), flush=True)


# ---- config file: every flag can live in ~/telltale-device.conf (key = value), so a contributor
# never has to touch the command line. CLI flags override the file; the file overrides the defaults.
_CFG_TYPES = {
    "signalk": str, "telltale": str, "race": str, "boat": str, "station_key": str, "boat_key": str,
    "interval": float, "private": bool, "datadir": str, "keep_days": int, "max_queue": int,
    "share": bool, "defer": bool, "sip": bool, "sip_interval": float, "fleet": str,
    "mode": str, "auto": bool, "cheap_interface": str, "mode_cheap": str, "mode_expensive": str,
}
def load_config(path):
    """Parse a simple `key = value` file (# comments, quotes optional) into argparse defaults."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower().replace("-", "_"); v = v.strip()
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        t = _CFG_TYPES.get(k)
        if t is None:
            continue                                    # ignore unknown keys, don't crash
        try:
            if t is bool:   out[k] = v.lower() in ("1", "true", "yes", "on")
            elif t is int:  out[k] = int(float(v))
            elif t is float:out[k] = float(v)
            else:           out[k] = v
        except ValueError:
            continue
    return out

# ---- auto-override: pick the upload mode from which link is actually carrying traffic ----
def egress_iface(target="8.8.8.8"):
    """The interface currently carrying internet traffic (the active default route), or None."""
    try:
        out = subprocess.run(["ip", "route", "get", target], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"\bdev\s+(\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None

def _nmcli_metered_active():
    """True if the active NetworkManager connection is metered, False if not, None if unknown."""
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "GENERAL.METERED", "connection", "show", "--active"],
                             capture_output=True, text=True, timeout=3).stdout.lower()
    except Exception:
        return None
    if "yes" in out:
        return True
    if out.strip() and "no" in out:
        return False
    return None

def link_is_cheap(cheap_interface):
    """Are we on the CHEAP link (cellular/unmetered) rather than the expensive one (Starlink)?
    1) named cheap interface is the active egress -> cheap; 2) else NetworkManager's metered flag;
    3) else UNKNOWN -> treat as expensive, so we never burn metered data on a guess."""
    if cheap_interface:
        eg = egress_iface()
        if eg is not None:
            return eg == cheap_interface
    m = _nmcli_metered_active()
    if m is not None:
        return not m
    return False

def mode_to_flags(mode):
    m = (mode or "full").lower()
    return (m == "defer", m == "sip")               # (defer, sip) for poll_once


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signalk", default="http://localhost:3000")
    ap.add_argument("--telltale", default="https://telltaleracing.com")
    ap.add_argument("--race", default="open")
    ap.add_argument("--boat", default="")
    ap.add_argument("--station-key", default="")
    ap.add_argument("--boat-key", default="",
                    help="this boat's WRITE key (bk_...) — required to post live as a CLAIMED boat, else 401")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--datadir", default=os.path.expanduser("~/telltale-device"),
                    help="where the local log + offline queue live")
    ap.add_argument("--keep-days", type=int, default=60, help="days of local track logs to keep")
    ap.add_argument("--max-queue", type=int, default=100000, help="max buffered fixes before dropping oldest")
    ap.add_argument("--no-share", dest="share", action="store_false",
                    help="opt out of pushing to Telltale (still logs locally — your data stays yours)")
    ap.add_argument("--defer", action="store_true",
                    help="at sea: buffer everything, use NO internet (no Starlink/cellular). Run without --defer ashore to upload.")
    ap.add_argument("--sip", action="store_true",
                    help="metered-link 'data sipper': keep logging + broadcasting the FULL signal set, but "
                         "republish live only every --sip-interval seconds (a light trickle over Starlink). "
                         "Combine with --fleet to trickle just the race boats.")
    ap.add_argument("--sip-interval", type=float, default=120,
                    help="seconds between live uploads in --sip mode (default 120). Local logging stays at --interval.")
    ap.add_argument("--fleet", default="",
                    help="comma-separated MMSIs to republish (own boat is always sent). Empty = every vessel heard. "
                         "Set this to the race fleet to cut data offshore (10 boats, not 300 harbour targets).")
    ap.add_argument("--flush", action="store_true",
                    help="one-shot: upload the buffered queue and EXIT (no live polling). Trigger this from a "
                         "marina-WiFi network hook so a deferred trip auto-uploads the moment you're back on cheap WiFi.")
    ap.add_argument("--mode", default="", choices=["", "full", "sip", "defer"],
                    help="upload posture: full | sip | defer. Config-file friendly alternative to the --sip/--defer flags.")
    ap.add_argument("--auto", action="store_true",
                    help="pick the mode automatically from which link is live: on the cheap link use --mode-cheap, "
                         "on the expensive one use --mode-expensive. Re-checked every cycle.")
    ap.add_argument("--cheap-interface", default="",
                    help="the network interface of your CHEAP link (e.g. cellular 'wwan0'). --auto treats traffic "
                         "leaving via this interface as cheap; anything else (e.g. Starlink) as expensive.")
    ap.add_argument("--mode-cheap", default="full", choices=["full", "sip", "defer"],
                    help="mode to use when --auto sees the cheap link (default full)")
    ap.add_argument("--mode-expensive", default="sip", choices=["full", "sip", "defer"],
                    help="mode to use when --auto sees only the expensive link (default sip)")
    ap.add_argument("--config", default="",
                    help="path to a key=value config file (default: ~/telltale-device.conf if present). "
                         "Every flag can be set there; the command line overrides the file.")

    # Load the config file (from --config, else the default location) and fold it in as defaults,
    # so a contributor can drive everything from the file and the CLI still overrides when needed.
    _cfg_pre = argparse.ArgumentParser(add_help=False)
    _cfg_pre.add_argument("--config", default="")
    _cfgpath = _cfg_pre.parse_known_args()[0].config or os.path.expanduser("~/telltale-device.conf")
    _cfg = load_config(_cfgpath)
    if _cfg:
        ap.set_defaults(**_cfg)

    a = ap.parse_args()
    if _cfg:
        print("[sk->tt] config: loaded %d settings from %s" % (len(_cfg), _cfgpath), flush=True)
    sk, tt = a.signalk.rstrip("/"), a.telltale.rstrip("/")
    fleet = {m.strip() for m in a.fleet.replace(" ", ",").split(",") if m.strip()}   # selected race boats (MMSIs) to trickle
    os.makedirs(a.datadir, exist_ok=True)
    global DEVICE, BOAT_KEY
    DEVICE = build_device(sk)
    BOAT_KEY = a.boat_key
    print("[sk->tt] kit v%s host=%s signalk=%s boat-key=%s" %
          (KIT_VERSION, DEVICE.get("host", "?"), DEVICE.get("sk", "?"),
           "set" if BOAT_KEY else "none (own-boat posts will 401 if this boat is claimed)"), flush=True)
    # Sharing is ON by default. Transparent, and never lock-in: the same data is always logged locally
    # (%s) AND served on the boat by Signal K, and is exportable from Telltale anytime.
    print("[sk->tt] Signal K %s -> %s | race=%s boat=%r | AIS:%s | every %ss" %
          (sk, tt, a.race, a.boat, "on" if a.station_key else "off", a.interval), flush=True)
    print("[sk->tt] Sharing to Telltale: %s (default is ON — your data is also kept locally at %s and "
          "exportable anytime; this is share-by-default, not lock-in)"
          % ("ON" if a.share else "OFF (--no-share)", a.datadir), flush=True)
    if a.flush:
        # One-shot: drain the offline buffer (in order) and exit. Perfect for a marina-WiFi network hook
        # (NetworkManager dispatcher / systemd) so a --defer trip uploads itself the instant you're back.
        BOAT_KEY = a.boat_key
        before = sum(1 for _ in open(_qpath(a.datadir))) if os.path.exists(_qpath(a.datadir)) else 0
        sent = flush_queue(a.datadir, tt, a.station_key)
        after = sum(1 for _ in open(_qpath(a.datadir))) if os.path.exists(_qpath(a.datadir)) else 0
        print("[sk->tt] flush: uploaded %d of %d buffered records, %d remain (run again with internet if any remain)"
              % (sent, before, after), flush=True)
        return
    # Resolve the base upload posture: explicit --mode wins, else the --defer/--sip flags, else full.
    base_mode = a.mode or ("defer" if a.defer else "sip" if a.sip else "full")
    if a.auto:
        print("[sk->tt] AUTO mode: cheap link (%s) -> %s, expensive link -> %s (re-checked each cycle)"
              % (a.cheap_interface or "unmetered", a.mode_cheap, a.mode_expensive), flush=True)
    elif base_mode == "defer":
        print("[sk->tt] DEFER mode: buffering only, NO internet used (no Starlink/cellular). "
              "Run without defer on cheap WiFi ashore to upload the trip.", flush=True)
    elif base_mode == "sip":
        print("[sk->tt] SIP mode: full signal set logged every %ss, uploaded live every %ss%s "
              "(light trickle for a metered link)."
              % (a.interval, a.sip_interval,
                 (" | fleet=%d boats" % len(fleet)) if fleet else " | all vessels"), flush=True)

    last_mode = None
    while True:
        try:
            if a.auto:
                cheap = link_is_cheap(a.cheap_interface)
                mode = a.mode_cheap if cheap else a.mode_expensive
                if mode != last_mode:   # announce only on change, not every cycle
                    print("[sk->tt] AUTO: %s link -> %s mode" % ("cheap" if cheap else "expensive", mode), flush=True)
                    last_mode = mode
            else:
                mode = base_mode
            defer, sip = mode_to_flags(mode)
            poll_once(sk, tt, a.race, a.boat, a.station_key, a.private, a.datadir, a.keep_days,
                      a.max_queue, a.share, defer, sip, a.sip_interval, fleet)
        except Exception as e:
            print("  err:", repr(e)[:140], flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
