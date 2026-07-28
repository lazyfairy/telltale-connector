# CANable → NMEA 2000 → Signal K — wiring & setup card

**What this is:** how to tap a boat's NMEA 2000 backbone with a cheap CANable USB‑CAN
adapter and get the data (AIS + wind/depth/heading/speed) into Signal K — and from there
into Telltale. Three paths, cheapest first. Print it and take it to the boat.

> **This is a DEMO BUILD.** Goal: the **cheapest, easiest** way to prove boat data flowing
> into Telltale, that we can then hand to **other boats** to copy. So we lead with the ~NZ$40
> CANable, keep the steps short, and note exactly where it stays easy (Linux/Pi) vs. where a
> boat is better off spending a bit more (Windows/Mac laptop, or the no‑computer WiFi route).

> **NMEA 2000 is just CAN bus at 250 kbps.** A CANable is a USB‑to‑CAN adapter, so
> electrically it *can* read the backbone directly. The catches are all in termination,
> power and software — not the wiring. Read the "four things that bite" box before you cut
> anything.

---

## Pick your path

| Path | What you plug into | Cost (boat‑side) | Best for | Trade‑off |
|---|---|---|---|---|
| **A — Laptop** | CANable USB → a laptop you already own | ~NZ$40 (CANable + drop cable) | Trying it out, one‑off race capture | Laptop must be aboard & awake; **easiest on Linux** (see caveat) |
| **B — Boat Kit (Pi)** | CANable USB → pre‑imaged Raspberry Pi running Signal K | ~NZ$120 + CANable | A permanent, headless box on boats with no nav computer | One more box to power (12 V) |
| **C — WiFi gateway** | *No computer* — a N2K→WiFi gateway on the backbone | ~NZ$180–350 | The proper answer: any phone/laptop reads it over boat WiFi | Costs more than a CANable |

**For the demo / other boats:** cheapest and easiest to copy is **Path A on a Linux laptop or
the Pi** (~NZ$40 of parts). If a boat only has a Windows/Mac laptop, don't fight the CANable —
a USB gateway is easier (box in Path A). **C (WiFi) is the endgame** once a boat wants it left
in permanently. On *Laissez Faire* you need none of these — the Vesper's own USB port already
gives Signal K AIS + instruments.

---

## The wiring (Paths A & B — identical)

A NMEA 2000 **drop cable** is a 5‑pin M12 (A‑coded) connector. Only three wires matter:

| N2K wire | Colour | → CANable terminal |
|---|---|---|
| CAN‑H | **white** | CAN‑H |
| CAN‑L | **blue** | CAN‑L |
| Shield / drain | bare | GND |
| NET‑C (0 V) | black | GND (same reference) |
| NET‑S (+12 V) | **red** | **leave disconnected** |

Use a spare drop cable or an M12 field‑attachable socket; land white/blue/bare(+black) onto
the CANable screw terminals. Plug the CANable USB into the computer. Physically, that's it.

### ⚠ Four things that bite people
1. **Power from USB, not the bus.** The red wire carries 12 V — the CANable is powered over
   USB. Don't feed bus 12 V into it. Leave the red wire capped.
2. **You are a DROP, not an END — no terminator.** A N2K backbone needs exactly **two** 120 Ω
   terminators, one at each physical end. **Do NOT** fit the CANable's onboard 120 Ω jumper —
   a third terminator corrupts the whole bus. Just confirm the backbone already has its two.
3. **Bitrate is fixed at 250 kbps.** Set it when you bring the interface up (below).
4. **Software is not automatic.** Raw CAN frames aren't readable data — canboat / Signal K
   decodes the PGNs into wind, depth, AIS, etc.

---

## Path A — standard laptop (path of least resistance)

### A‑1. Linux laptop — the smooth path (CANable = native SocketCAN)
```bash
# CANable with candleLight firmware shows up as a SocketCAN device (can0)
sudo ip link set can0 up type can bitrate 250000
candump can0                       # sanity: you should see frames
```
Then in **Signal K** → add a connection:
- Data type: **NMEA 2000**, source: **canboatjs / SocketCAN (canbus‑canboatjs)**, device `can0`.
- Signal K decodes PGNs → wind/depth/heading/AIS appear under the vessel.
- Point Telltale's connector at Signal K as usual.

### A‑2. Windows / Mac laptop — honest caveat
Native SocketCAN is **Linux‑only**. On Windows/Mac a CANable works via **slcan** (a serial COM
port) with tools like SavvyCAN, but Signal K's N2K path is **not** a well‑trodden route there.
If the boat's laptop is Windows/Mac, the genuinely easier tap is a gateway that presents as a
standard **Actisense/serial** device — see box below — rather than fighting the CANable.

> **If it's not a Linux laptop, buy the right USB gateway instead of a bare CANable.**
> **Yacht Devices YDNU‑02N** (~USD$249) = virtual COM port, no driver needed, well‑trodden
> with Signal K on every OS; or **Actisense NGX‑1‑USB** (~USD$350). Both plug into a normal
> backbone drop (M12 5‑pin) and are cross‑platform plug‑and‑play. The CANable is the cheap
> option *specifically when you'll run Linux*.

---

## Path B — Boat Kit (headless Raspberry Pi)

Same wiring as Path A, but the CANable plugs into a **pre‑imaged Raspberry Pi** that runs
Signal K + the Telltale connector and lives on the boat permanently.

- Bring `can0` up at boot (systemd `network`/`ip link`, bitrate 250000).
- Pi powered from a 12 V→5 V DC‑DC off the boat's supply.
- Signal K set up exactly as A‑1; the Telltale connector (`provision.sh`) points at it.
- This is for boats with **no** nav computer. A boat that already runs Signal K (like the HP
  mini) doesn't need the Pi — just the CANable + drop cable.

---

## Path C — straight onto WiFi (the next better thing) ★

Skip the tethered computer entirely: put a **N2K→WiFi gateway** on the backbone and every
device on the boat's WiFi (phone, laptop, the club box) reads the data over TCP/UDP. No cable
to a laptop, nothing to keep awake on a nav table.

**Off‑the‑shelf (recommended):**
- **Yacht Devices YDWG‑02** (N2K → WiFi, ~USD$180). Tees into the backbone, serves NMEA over
  TCP/UDP; Signal K (or any nav app) connects to its IP. OS‑agnostic, wireless, low‑power.
- Actisense **W2K‑1** is the equivalent if you prefer Actisense.

**DIY (our firmware):** an ESP32 + CAN transceiver running
[`telltale_gateway_n2k.ino`](race-modeller/telltale_gateway_n2k.ino) reads the N2K backbone and
pushes it onto WiFi — the same idea as the club receiver bridge, boat‑side. Cheapest, but it's
a build, not a purchase. Full recipe below.

---

## Path D — DIY ESP32 → WiFi (~NZ$20, the cheap wireless build) ★ demo pick

The cheapest way to get boat data onto WiFi so **any phone/laptop reads it** — no tethered
computer, ~NZ$20 in parts. This is the one to hand other boats for a cheap demo.

### NMEA 2000 (CAN backbone) → WiFi
| Part | ~Price | Notes |
|---|---|---|
| ESP32 dev board (e.g. WROOM‑32 DevKitC) | NZ$10–15 | the WiFi brain |
| CAN transceiver — **SN65HVD230** breakout | NZ$3–5 | 3.3 V CAN, pairs with ESP32 |
| 12 V→5 V buck (e.g. MP1584) | NZ$2–4 | power it off the boat (bench = USB) |
| N2K drop cable / M12 socket | ~NZ$15 | tap the backbone |

**Wiring:** N2K CAN‑H/CAN‑L → SN65HVD230 CANH/CANL; transceiver CTX/CRX → two ESP32 GPIOs
(set in the firmware, e.g. GPIO 4/5); GND common; power the ESP32 from the buck (N2K 12 V in) or
USB. **Same drop rules as above — no terminator, don't take bus 12 V into the ESP32 directly.**
Flash [`telltale_gateway_n2k.ino`](race-modeller/telltale_gateway_n2k.ino) (Arduino IDE + the
`NMEA2000`/`NMEA2000_esp32` libraries). It joins the boat WiFi and serves NMEA over TCP/UDP →
add it as a connection in Signal K.

### NMEA 0183 (serial instruments) → WiFi
- **DIY:** ESP32 + a **MAX3232/RS422** level‑shifter (~NZ$2) on the 0183 TX pair → same WiFi push.
- **Buy‑instead (barely‑DIY, recommended fallback):** an **Elfin EW11** (RS485/RS232→WiFi,
  ~NZ$26) — no soldering, proven, already in our kit notes. Wire the 0183 output to it, point
  AIS‑catcher / Signal K at its TCP port.

**Reality check:** a bare ESP32 isn't marine‑hardened — add the buck for 12 V and a bit of
weatherproofing for a permanent fit. Perfect for a **cheap demo / other boats**; step up to a
Yacht Devices **YDWG‑02** or a **MacArthur HAT** Pi box when a boat wants it left in for good.

**Why C wins:** no computer aboard, any device reads it, it survives the laptop going to
sleep or ashore, and it's the same shape as the club‑side WiFi bridge — one mental model. The
CANable/laptop path is how you *prove the data flows today*; the WiFi gateway is how you'd
actually leave it on a boat.

---

## One‑line decision

- **Got a Linux laptop and NZ$40?** → CANable, Path A, today.
- **Windows/Mac laptop?** → YDNU‑02N USB gateway (don't fight the CANable).
- **Want it permanent with no laptop?** → YDWG‑02 WiFi gateway (Path C) — the endgame.
- **Boat has no computer at all?** → Boat Kit Pi + CANable (Path B).

*Related: [[BOAT-KIT-SPEC.md]], reference‑boat‑hardware, reference‑receiver‑setup (club‑side
WiFi bridge), project‑backlog (gateway decision).*
