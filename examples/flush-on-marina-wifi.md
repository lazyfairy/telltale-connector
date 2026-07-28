# Auto-upload the moment you're back on marina WiFi

Goal: at sea the connector runs in `--defer` mode (records + buffers, **zero** Starlink/cellular
data). The instant the boat rejoins your home/marina WiFi, the whole buffered trip uploads by
itself — then stops. No data wasted on the metered link, no manual step.

Two ways to trigger the one-shot `--flush`:

---

## Option A — NetworkManager dispatcher (recommended)

Runs a script every time an interface comes up. Fire the flush only when the connected WiFi is
**your marina/home SSID**.

Create `/etc/NetworkManager/dispatcher.d/90-telltale-flush` (root, `chmod 755`):

```bash
#!/bin/bash
# $1 = interface, $2 = action
IFACE="$1"; ACTION="$2"
[ "$ACTION" = "up" ] || exit 0

# only on WiFi joining a KNOWN cheap network (edit these SSIDs):
MARINA_SSIDS="MarinaWiFi HomeWiFi ClubGuest"
SSID="$(iwgetid -r 2>/dev/null || true)"
case " $MARINA_SSIDS " in *" $SSID "*) ;; *) exit 0;; esac

# drain the buffered trip once, as the pi user, then exit:
sudo -u pi /usr/bin/python3 /usr/local/bin/signalk_telltale.py \
    --telltale https://telltaleracing.com \
    --station-key YOUR_STATION_KEY \
    --flush >> /var/log/telltale-flush.log 2>&1
```

```bash
sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-telltale-flush
```

Now every time you're back on `MarinaWiFi`, the buffer uploads automatically. Check
`/var/log/telltale-flush.log` to see what went up.

---

## Option B — a timer that flushes whenever there's internet

If you'd rather "flush whenever a connection is available" without matching SSIDs, run a
periodic flush. It uses almost no data when the queue is empty, and `--flush` exits immediately
if it can't reach the server (the buffer stays intact, in order, for next time).

`/etc/systemd/system/telltale-flush.service`:
```ini
[Unit]
Description=Telltale one-shot flush
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
ExecStart=/usr/bin/python3 /usr/local/bin/signalk_telltale.py --telltale https://telltaleracing.com --station-key YOUR_STATION_KEY --flush
```

`/etc/systemd/system/telltale-flush.timer`:
```ini
[Unit]
Description=Flush the Telltale buffer periodically when online

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telltale-flush.timer
```

> ⚠ Option B *will* use a little data at sea if you have a satellite link up, because it tries
> every 15 min. If your priority is **zero** data at sea, use **Option A** (SSID-gated) so it
> only ever uploads on WiFi you've named.
