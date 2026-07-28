#!/usr/bin/env python3
"""Bridge a boat's NMEA-0183-over-WiFi stream straight to Telltale — no Signal K needed.

Point it at ANY cheap NMEA->WiFi gateway (they broadcast NMEA 0183 over TCP, usually port 10110),
a serial-to-WiFi module, or a Matsutec AR-12 behind an ESP32. We parse the boat's OWN position +
instruments and POST to /api/live-ping, with the same always-log-first + store-and-forward safety
as signalk_telltale.py (never lose a race, works with zero internet, flushes ashore).

  python3 nmea_wifi_telltale.py --host 192.168.4.1 --port 10110 \
      --telltale https://telltaleracing.com --race thursday-twilight --boat "Laissez Faire"

Notes:
  - Sends YOUR track (GPS + wind/speed/heading/depth/heel). AIS decoding lives in the shore
    station / Signal K; this is the boat feeder for boats that just export NMEA over WiFi.
  - --defer buffers at sea (no data used), flushes on cheap marina WiFi. --no-share keeps the
    local log but doesn't upload (your data stays yours).
"""
import os, socket, time, argparse
import signalk_telltale as skt   # reuse the proven _post_ok / enqueue / flush_queue / append_log


def _dm_to_deg(dm, hemi):
    """NMEA ddmm.mmmm + hemisphere -> signed decimal degrees."""
    if not dm:
        return None
    try:
        v = float(dm)
    except ValueError:
        return None
    d = int(v // 100)
    deg = d + (v - d * 100) / 60.0
    return -deg if hemi in ("S", "s", "W", "w") else deg


def _spd_kn(x, unit):
    """MWV/VHW speed -> knots (N=knots, K=km/h, M=m/s)."""
    try:
        x = float(x)
    except (ValueError, TypeError):
        return None
    return {"N": x, "K": x / 1.852, "M": x * 1.943844}.get((unit or "N").upper(), x)


def parse_line(line, p):
    """Fold one NMEA-0183 sentence into p (a live-ping dict). Returns True if position updated."""
    line = line.strip()
    if not line or line[0] not in "$!":
        return False
    f = line[1:].split("*")[0].split(",")
    if len(f) < 2:
        return False
    typ = f[0][-3:].upper()   # GPRMC -> RMC, IIVHW -> VHW, etc.
    moved = False
    try:
        if typ == "RMC" and len(f) > 8 and f[2] == "A":
            lat, lon = _dm_to_deg(f[3], f[4]), _dm_to_deg(f[5], f[6])
            if lat is not None and lon is not None:
                p["lat"], p["lon"] = lat, lon; moved = True
            if f[7]:
                p["sog"] = float(f[7])
            if f[8]:
                p["cog"] = float(f[8])
        elif typ == "GGA" and len(f) > 6 and f[6] not in ("", "0"):
            lat, lon = _dm_to_deg(f[2], f[3]), _dm_to_deg(f[4], f[5])
            if lat is not None and lon is not None:
                p["lat"], p["lon"] = lat, lon; moved = True
        elif typ == "VTG" and len(f) > 5:
            if f[1]:
                p["cog"] = float(f[1])
            if f[5]:
                p["sog"] = float(f[5])
        elif typ == "VHW" and len(f) > 5 and f[5]:
            p["stw"] = float(f[5])
        elif typ == "MWV" and len(f) > 4 and (len(f) < 6 or f[5] != "V"):
            spd = _spd_kn(f[3], f[4])
            if f[2] == "R":            # apparent
                if f[1]:
                    p["awa"] = float(f[1])
                if spd is not None:
                    p["aws"] = round(spd, 2)
            elif f[2] == "T":          # true
                if f[1]:
                    p["twa"] = float(f[1])
                if spd is not None:
                    p["tws"] = round(spd, 2)
        elif typ in ("HDT", "HDG") and f[1]:
            p["hdg"] = float(f[1])
        elif typ in ("DPT", "DBT") and f[1]:
            p["depth"] = float(f[1])
        elif typ == "XDR":             # transducer — pick roll (heel) if present
            for i in range(1, len(f) - 3, 4):
                if f[i] == "A" and "ROLL" in (f[i + 3] or "").upper():
                    p["heel"] = float(f[i + 1])
    except (ValueError, IndexError):
        pass
    return moved


def run(a):
    tt = a.telltale.rstrip("/")
    datadir = os.path.expanduser(a.datadir)
    os.makedirs(datadir, exist_ok=True)
    p = {"race": a.race, "boat": a.boat}
    last = 0.0
    while True:
        try:
            s = socket.create_connection((a.host, a.port), timeout=12)
            s.settimeout(12)
            print("connected to %s:%d — reading NMEA" % (a.host, a.port), flush=True)
            fh = s.makefile("r", encoding="ascii", errors="ignore")
            for line in fh:
                parse_line(line, p)
                now = time.time()
                if now - last < a.interval or "lat" not in p:
                    continue
                last = now
                fix = dict(p, t=int(now))
                if a.private:
                    fix["private"] = True
                skt.append_log(datadir, {"t": int(now), "self": fix, "ais": []}, a.keep_days)  # guaranteed capture
                if a.no_share:
                    tag = "logged (sharing OFF)"
                elif a.defer:
                    skt.enqueue(datadir, "ping", fix, a.max_queue); tag = "DEFERRED"
                else:
                    skt.flush_queue(datadir, tt, "")
                    tag = "pushed" if skt._post_ok(tt + "/api/live-ping", fix) else "queued (offline)"
                    if tag.startswith("queued"):
                        skt.enqueue(datadir, "ping", fix, a.max_queue)
                print("  %s | %.5f,%.5f sog=%s | %s" %
                      (time.strftime("%H:%M:%S"), p["lat"], p["lon"], p.get("sog"), tag), flush=True)
        except (socket.timeout, OSError) as e:
            print("  lost link (%s) — reconnecting in 3s" % str(e)[:60], flush=True)
            time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="the WiFi gateway's IP (e.g. 192.168.4.1)")
    ap.add_argument("--port", type=int, default=10110, help="NMEA TCP port (usually 10110)")
    ap.add_argument("--telltale", default="https://telltaleracing.com")
    ap.add_argument("--race", default="open")
    ap.add_argument("--boat", default="")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--defer", action="store_true", help="buffer at sea, upload ashore")
    ap.add_argument("--no-share", dest="no_share", action="store_true", help="log locally, don't upload")
    ap.add_argument("--datadir", default="~/telltale-device")
    ap.add_argument("--keep-days", type=int, default=60)
    ap.add_argument("--max-queue", type=int, default=100000)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
