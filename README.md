# Telltale Connector & Boat Kit

Open tools for getting boat data — **AIS and instruments** — from a boat or a shore station
into [Telltale Racing](https://telltaleracing.com), the free live-tracking and replay platform
for yacht clubs.

These are the **give-back pieces**: the connector, the device installer, the DIY WiFi bridges
and the gateway firmware. They read [Signal K](https://signalk.org/) (or raw NMEA) and forward
to Telltale — they **don't re-decode anything and they don't lock anything in**. Your data is
always logged locally on your own box too, and it's exportable from Telltale at any time.

> The Telltale **server** is a separate, private project. This repo is only the on-boat / shore
> connector layer — the part that's meant to be shared, inspected and improved.

---

## What's here

| File | What it does |
|---|---|
| **`signalk_telltale.py`** | The connector. Reads a Signal K server and pushes own-boat instruments + other vessels' AIS to Telltale. Store-and-forward, offline logging, **defer/flush for metered links**, and an optional **on-boat crew tactical-sync server** (`--boat-sync`). Pure stdlib — runs on any Pi. |
| **`boat_sync.py`** | **Boat-local crew sync (LAN-only).** A tiny web server the connector can run so every phone/tablet on the boat WiFi shares the same pinged start line + mark positions live — the boat auto-keeps the tightest GPS fix. **Never leaves the boat** (nothing sent to Telltale). See [`--boat-sync`](#share-the-crew-cockpit-on-the-boat-wifi----boat-sync). |
| **`provision.sh`** | One-command installer that turns a fresh Raspberry Pi into a shore AIS station or a boat device, auto-starting on boot. |
| **`nmea_wifi_telltale.py`** | Bridge for boats that emit NMEA over WiFi/TCP/UDP (no Signal K needed). See [`docs/NMEA-WIFI-BRIDGE.md`](docs/NMEA-WIFI-BRIDGE.md). |
| **`ais_relay.py`** | Relay AIS from a local receiver to Telltale (and optionally on to other consumers). |
| **`setup-windows.ps1`** | Windows one-liner for a PC-based feeder. |
| **`firmware/`** | ESP32 gateway firmware: `telltale_gateway` (NMEA 0183→WiFi) and `telltale_gateway_n2k` (NMEA 2000/CAN→WiFi). The ~NZ$20 DIY WiFi option. |
| **`docs/CANABLE-WIRING-CARD.md`** | **Plain-English guide** to getting boat data into Telltale — every jargon term explained (NMEA 2000, Signal K, Pi, Linux, AIS), the laptop / Pi / mini-PC / WiFi-gateway options, "does it need a screen?", and where to buy (NZ). Written for non-technical owners; experts skim to the tables. |

---

## Quick start — Signal K connector

If you already run **Signal K** (e.g. on OpenPlotter), it has already decoded your NMEA 0183,
NMEA 2000 and AIS into one model. The connector just forwards it:

```bash
curl -fsSL https://telltaleracing.com/signalk_telltale.py -o signalk_telltale.py

# push own-boat instruments live, and every AIS target it hears:
python3 signalk_telltale.py \
  --telltale https://telltaleracing.com \
  --race thursday-twilight \
  --boat "Your Boat" \
  --station-key YOUR_STATION_KEY
```

- Leave off `--boat` to push **AIS only** (a shore club station).
- Leave off `--station-key` to push **own-boat only**.
- Get a **station key** self-serve from the [Contribute page](https://telltaleracing.com/contribute).
- Add `--boat-key bk_…` if your boat is *claimed* on Telltale (else own-boat posts 401).

Everything is **always logged locally first** (`~/telltale-device/log/*.jsonl`) — internet or not.

---

## Metered / satellite links (Starlink, cellular): record at sea, upload ashore

The connector is built for boats that don't want to pay for data at sea:

```bash
# AT SEA — record continuously, use ZERO internet (nothing sent over Starlink/cellular):
python3 signalk_telltale.py --boat "Your Boat" --station-key KEY --race NAME --defer

# BACK AT THE MARINA — one-shot: upload the whole buffered trip, then exit:
python3 signalk_telltale.py --station-key KEY --flush
```

- `--defer` logs everything to the SD card **and** buffers uploads without touching the network.
- `--flush` drains that buffer in one burst and exits — ideal to trigger automatically the
  moment you're back on cheap marina WiFi (see the auto-upload recipe below).

### Want a *live* view offshore without the full firehose? — `--sip`

```bash
# trickle the FULL signal set live, but only every 2 minutes, and only the race fleet:
python3 signalk_telltale.py --boat "Your Boat" --station-key KEY --race NAME \
  --sip --sip-interval 120 --fleet 512004617,512010800,512218840
```

`--sip` keeps logging + recording at full rate locally, but **uploads live only every
`--sip-interval` seconds** — a light trickle sized for Starlink/cellular. `--fleet` narrows what
gets republished to a chosen list of MMSIs (the boats you're racing), so you send ~10 boats
instead of every harbour target. Leave `--fleet` off to trickle everything. It's live-only: a
skipped sip stays in your local log rather than back-filling full-res later over the metered link.

Three data postures, contributor's choice:

| Mode | Uploads at sea | Use when |
|---|---|---|
| *(default)* | full, every `--interval` | on unmetered WiFi, or you don't care about data |
| `--sip [--fleet …]` | light trickle, every `--sip-interval` | metered link, but you still want a live view |
| `--defer` + `--flush` ashore | nothing at sea | zero data at sea; upload the whole trip in the marina |

Below, `--flush` also covers the auto-upload trigger. See
  [`examples/flush-on-marina-wifi.md`](examples/flush-on-marina-wifi.md) for a NetworkManager
  recipe that does it hands-free, and [`examples/signalk-telltale.service`](examples/signalk-telltale.service)
  for the always-on systemd unit.

`--no-share` keeps the local log but never uploads (your data stays yours).

### Let it switch modes by itself — `--auto`

If your boat has both a cheap link (cellular SIM) and an expensive one (Starlink), `--auto`
picks the mode from **whichever link is actually carrying traffic**, re-checked every cycle:

```bash
python3 signalk_telltale.py --boat "Your Boat" --station-key KEY --race NAME \
  --auto --cheap-interface wwan0 --mode-cheap full --mode-expensive sip --fleet 512004617,512010800
```

- On the **cheap** link → `--mode-cheap` (default `full`).
- On the **expensive** link → `--mode-expensive` (default `sip`, or `defer` for zero data).
- How it decides: traffic leaving via `--cheap-interface` counts as cheap; otherwise it falls back
  to NetworkManager's *metered* flag; if it can't tell, it treats the link as **expensive** so it
  never burns metered data on a guess. (Run `ip route get 8.8.8.8` to find your interface name.)

---

## Configure it in one file — `telltale-device.conf`

You don't need to remember flags. Copy [`telltale-device.conf.example`](telltale-device.conf.example)
to `~/telltale-device.conf`, edit it, and just run `python3 signalk_telltale.py` — it reads the file
automatically. Every setting (boat, keys, mode, sip-interval, fleet, auto, intervals…) lives there,
and the command line still overrides the file when you want a one-off change.

```ini
boat        = Your Boat
station_key = YOUR_STATION_KEY
mode        = sip            # full | sip | defer
sip_interval= 120
fleet       = 512004617, 512010800, 512218840
auto        = true
cheap_interface = wwan0
mode_cheap  = full
mode_expensive = sip
```

---

## Share the crew cockpit on the boat WiFi — `--boat-sync`

Turn on a small **LAN-only** server so every phone/tablet on the boat shares the same tactical
picture — the pinged **start-line ends** and **mark positions** — live, across the whole crew:

```bash
python3 signalk_telltale.py --boat "Your Boat" --station-key KEY --race NAME \
  --boat-sync --boat-sync-code AB12 --boat-sync-webroot ~/telltale-pwa
```

- **Best-accuracy wins.** When two crew ping the same mark, the boat keeps the **tightest GPS fix** —
  a marine-grade fix automatically beats a phone in a pocket. No fiddling, no one reading a screen out.
- **It never leaves the boat.** This data is **not** sent to Telltale — it lives only on the boat's
  own network. (It's completely separate from the track/AIS the connector uploads.)
- **Serves the page too.** Point `--boat-sync-webroot` at a copy of the tactician page and the box
  hosts it over the boat WiFi at `http://<box>:8137/start` — so it works with **no internet at all**,
  and crew phones load it same-origin (no browser security snags).
- **Keep out the neighbours.** Set a short `--boat-sync-code` so a nearby boat on a shared marina WiFi
  can't join in.

Off by default. Every flag also lives in `telltale-device.conf` (`boat_sync = on`, `boat_sync_port`,
`boat_sync_code`, `boat_sync_webroot`, `boat_sync_iface`). See `boat_sync.py` for the details.

---

## Run it as a service

See [`examples/signalk-telltale.service`](examples/signalk-telltale.service) — a systemd unit
that runs the connector in `--defer` mode on boot, so a trip is always captured and buffered.

---

## Provision a Pi from scratch

```bash
# shore AIS station:
curl -fsSL https://telltaleracing.com/provision.sh | sudo bash -s -- shore "LBYC clubhouse" STATION_KEY

# boat instrument device (serial by-id path recommended):
curl -fsSL https://telltaleracing.com/provision.sh | sudo bash -s -- boat "Your Boat" STATION_KEY \
  --serial /dev/serial/by-id/XXX --baud 4800
```

---

## Design principles

- **Consume, don't replace.** The connector reads Signal K, so it works the same whether your
  boat is NMEA 0183, NMEA 2000, or SDR-AIS. It sits *on top of* the open marine stack.
- **No lock-in.** Data is logged locally and exportable from Telltale; opting out of sharing
  (`--no-share`) never locks you out of your own data.
- **Thin and reusable.** Pure Python stdlib, no dependencies; reuses proven tools
  (Signal K, systemd, AIS-catcher) rather than reinventing them.

---

## Contributing

Issues and pull requests welcome — especially new Signal K path mappings, additional receiver
setups, and platform notes (OpenPlotter, Raspberry Pi OS, Windows). Please keep the connector
**stdlib-only** so it runs on any Pi without a package install.

## License

MIT — see [`LICENSE`](LICENSE). (AIS-catcher and other tools these scripts *install and call*
keep their own licenses; this repo doesn't bundle or modify them.)
