# Boat NMEA → WiFi → Telltale (the buy-don't-build feeder)

Get a boat's own track (GPS + wind/speed/heading/depth/heel) into Telltale using a **NZ$13 off-the-shelf
serial-to-WiFi box** instead of building anything. Works on any boat that outputs **NMEA 0183**.

```
 Boat NMEA-0183  ──►  Elfin EW11 (serial→WiFi)  ──WiFi TCP──►  a small always-on device
 (instruments)       ~NZ$13, 9–36 VDC            :10110         (Pi / old phone / laptop)
                                                                running nmea_wifi_telltale.py ──► Telltale
```

## Shopping list
- **Elfin EW11** wide-voltage (9–36 VDC) RS485→WiFi serial server — ~NZ$13 (in the AliExpress cart)
- Any always-on device on the boat that can run Python + reach the internet (a Pi Zero 2 W, an old
  phone with Termux, or the nav-station laptop). This is the piece that uploads to Telltale.

> A browser/PWA **cannot** read a raw NMEA TCP stream, so you need this one small device to read the
> EW11's WiFi and POST to Telltale. (Or skip the EW11 entirely and use the ESP32 gateway, which reads
> the bus *and* uploads itself — see `firmware/`.)

## Wire it (2 wires)
NMEA 0183 is a differential pair, same idea as RS485:

```
   NMEA-0183 OUT  →  EW11 RS485
   ── A / +       →     A
   ── B / −       →     B
   12 V  →  EW11 V+     (9–36 VDC, so boat 12 V is fine)
   GND   →  EW11 GND
```

## Configure the EW11 (once, in its web UI)
Join the EW11's own WiFi (default AP), open its page (usually `10.10.100.254`), and set:
- **Serial:** baud **4800** (standard NMEA 0183) — or **38400** if it's a high-speed AIS/instrument bus
- **Network mode:** **TCP Server**, port **10110**
- **WiFi:** either leave it as its own AP, or join the boat's WiFi (STA mode) — either works; just note the EW11's IP.

## Run the bridge
On the always-on device (Python 3, no dependencies beyond the standard library):

```bash
python3 nmea_wifi_telltale.py \
    --host 10.10.100.254 --port 10110 \
    --telltale https://telltaleracing.com \
    --race thursday-twilight --boat "Laissez Faire"
```

That's it — it parses your position + instruments and pushes to Telltale, logging every fix locally first.

### Handy flags
- `--defer` — **at sea, use no data.** Buffers everything locally; run again **without** `--defer` on
  cheap marina/club WiFi and the whole trip uploads in one go.
- `--no-share` — keep the local log (yours, exportable) but **don't** upload.
- `--private` — upload, but keep the replay private to you.
- `--interval 5` — seconds between fixes (default 5).

## Notes
- This sends **your boat's own track**. Seeing the *fleet* (AIS) is the shore station's job (a station
  captures everyone); a boat mainly needs to send its own position — which this does.
- Same always-log + store-and-forward safety as `signalk_telltale.py`: you never lose a race, even with
  zero internet.
- The EW11 is an **industrial IoT** part (Modbus/serial-to-WiFi), not marine kit — which is exactly why
  it's NZ$13 and rock-solid. We're riding a commodity other industries already built.
