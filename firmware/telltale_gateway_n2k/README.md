# Telltale N2K → WiFi gateway — build & flash

A ~NZ$10–14 ESP32 box that plugs into a boat's **NMEA-2000** backbone and re-serves the instruments
as standard **NMEA-0183 over WiFi** (UDP broadcast + TCP :10110). No wiring boat-side — Micro-C plug + tee.

The sketch is **self-contained**: it decodes the PGNs directly and hand-builds the 0183 sentences, so it
depends only on the core `NMEA2000` libraries (no example converter files to copy).

---

## Parts (~NZ$10–14)
| Part | ~NZ$ | Notes |
|---|---|---|
| ESP32 dev board (WROOM-32) | 5 | any DevKit |
| **SN65HVD230** CAN transceiver | 1 | the only added part vs the 0183 box (3.3 V CAN) |
| M12 Micro-C field-attachable plug / pigtail | 4 | mates the N2K backbone |
| N2K drop-tee | 8 | if not already a spare on the backbone |
| 12 V→5 V buck (3 A, inline fuse) | 2 | powers the ESP32 off the bus's NET-V |

## Wiring (this is the *only* soldering)
```
   ESP32                 SN65HVD230              N2K Micro-C plug
   GPIO5  (CTX) -------> D  (CTX)
   GPIO4  (CRX) -------> R  (CRX)
   3V3          -------> VCC (3.3V !)            NET-H (white) --- CANH
   GND          -------> GND ------------------- NET-C (black) --- shield/GND
                         CANH ------------------ NET-H
                         CANL ------------------ NET-L (blue)
   5V  <---- 12V→5V buck <-------------------- NET-V (red, 12V)  +  NET-C (GND)
```
- Power the ESP32 from the bus's 12 V via the buck (respect the backbone LEN budget — an ESP32 with WiFi ≈ 2 LEN, fine on most backbones).
- The **120 Ω terminators** must already be on the backbone (they are, on any working N2K network) — don't add one.

## Flash it (Arduino IDE)
1. **Board support:** File → Preferences → add
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   to *Additional Boards Manager URLs*, then Tools → Board → Boards Manager → install **esp32** (Espressif).
2. **Libraries:** Tools → Manage Libraries → install **NMEA2000_esp32** (by Timo Lappalainen). It pulls in **NMEA2000**. (That's all — no NMEA0183 library needed.)
3. **Board settings:** Tools → Board → **ESP32 Dev Module**; pick the right **Port**.
4. **Edit the CONFIG block** at the top of `telltale_gateway_n2k.ino`:
   - `MODE_STA 1` + your boat WiFi `WIFI_SSID`/`WIFI_PASS` (recommended), or `MODE_STA 0` to make its own AP.
   - Leave `AP_PASS ""` — it auto-generates a password unique to the box (printed on serial).
5. **Upload.** Open Serial Monitor @ 115200 — you should see `[wifi] joined…` then `[ready] decoding N2K -> 0183…`.

## First run / verify
- **OpenCPN / Navionics:** add a data connection → Network → **TCP**, address = the ESP32's IP, port **10110**. Instruments + position should appear.
- **Telltale phone app / `/relay`:** on the same WiFi it hears the UDP broadcast automatically (phone GPS + these instruments).
- **Any phone:** a UDP-listener app on port 10110 will show the `$WIMWV / $VWVHW / $SDDPT / $GPGLL …` sentences streaming.

## Bench-test it WITHOUT a boat
You don't need the backbone to prove the firmware:
1. **Two-ESP32 rig:** flash a second ESP32 + SN65HVD230 with the NMEA2000 library's **MessageSender** example (sends fake wind/speed/depth PGNs). Wire the two transceivers CANH-CANH / CANL-CANL with a 120 Ω resistor across each end. Power both. This gateway should emit matching 0183 over WiFi.
2. **Pi + canboat:** a Raspberry Pi with a USB-CAN adapter running `candump`/`canplayer` (canboat) can replay a recorded N2K log onto the bus.
3. **Sanity without CAN at all:** temporarily call `HandleN2K()` with a hand-built `tN2kMsg` in `setup()` and watch the serial/UDP output — confirms the decode→0183→WiFi path.

## PGNs decoded → 0183 emitted
| PGN | Data | 0183 out |
|---|---|---|
| 129025 | position (rapid) | `$GPGLL` |
| 129026 | COG/SOG (rapid) | `$GPVTG` |
| 130306 | wind | `$WIMWV` (R apparent / T true) |
| 128259 | speed through water | `$VWVHW` |
| 128267 | water depth | `$SDDPT` |
| 127250 | heading | `$HCHDG` |
| 127257 | attitude (roll) | `$IIXDR,…,ROLL` (heel) |

## Troubleshooting
- **No 0183 out:** check CANH/CANL not swapped; confirm the bus has traffic (the ESP32 is listen-only — it can't be the problem source); verify GPIO4/5 match your wiring.
- **`ParseN2k…` not found at compile:** you're missing the **NMEA2000** core library (the `_esp32` one depends on it).
- **Reboot loop / brownout:** the buck can't supply enough 5 V — use a 3 A buck; don't power the ESP32 from a Pi USB port that's also loaded.
- **Wrong wind angle sign:** some instruments report port-negative differently; MWV here outputs 0–360 — adjust in `HandleN2K` if your kit differs.
