# Getting your boat's data onto Telltale — a plain-English guide

**The short version:** your boat's instruments (wind, depth, speed, and often AIS) already talk to
each other over a little onboard network. This guide shows how to "listen in" on that network and
send the data to Telltale, so your races record themselves and you can replay them afterwards.
There are a few ways to do it — cheapest first. You do **not** need to understand any of the tech
words to follow along; we explain each one as it comes up.

> **This is a "prove it works" build.** The goal is the **cheapest, simplest** way to see your boat's
> data flowing into Telltale — something you can then show other boats and say "here, copy this."

---

## First, the words you'll keep seeing (in plain English)

Don't worry about memorising these — glance back whenever one shows up.

- **NMEA 2000** (say "nimya two-thousand") — the little **network your boat's instruments use to talk
  to each other**. If your wind, depth and speed all show up on your chartplotter, they're almost
  certainly chatting over NMEA 2000. It's just a special cable running around the boat.
- **AIS** — the system boats use to **see each other on a screen** (name, position, speed). Racing
  yachts broadcast it; that's what lets Telltale draw the fleet on a map.
- **CAN bus** — the *type* of wiring NMEA 2000 uses under the hood. You'll see it because the cheap
  adapter we use is a "USB-to-CAN" adapter. Think of it as the language on the wire.
- **CANable** — a **small, cheap USB gadget (~NZ$40)** that plugs into that boat network at one end
  and into a computer's USB port at the other. It's the "listening device."
- **A gateway** — a **box you buy that does the same listening job**, but tidier and often wireless.
  More expensive than the CANable, but plug-and-play.
- **Raspberry Pi** (or just "Pi") — a **tiny, cheap computer** (about the size of a deck of cards,
  ~NZ$60–120). People leave one on the boat to do a job quietly forever.
- **Linux** — a **free operating system** (like Windows or macOS, but free and light). A Raspberry Pi
  runs Linux. You don't have to "do Linux" — the boat box comes set up.
- **Signal K** — a **free program that reads all those instrument messages and turns them into tidy,
  readable data**. It's the translator that sits between the raw boat network and Telltale. It runs on
  a Pi, a Windows laptop, a Mac — anything.
- **The Telltale connector** — our **small free program that takes the data from Signal K and sends it
  to Telltale**. Set-and-forget.

**So the whole plan, in one sentence:** *plug a listening gadget into the boat network → a translator
program (Signal K) makes sense of it → our connector sends it to Telltale.* That's it.

---

## The three ways to do it (pick one)

| Way | What you plug in | Rough cost | Best if… |
|---|---|---|---|
| **A — Use a laptop** | A cheap USB gadget (CANable) into a laptop you already own | ~NZ$40 | You just want to try it / grab one race |
| **B — Leave a tiny computer aboard** | The same gadget into a little Raspberry Pi that stays on the boat | ~NZ$120 + gadget | Your boat has no onboard computer and you want it automatic |
| **C — A wireless box, no computer** | A "gateway" box that puts the data on your boat's WiFi | ~NZ$180–350 | You want it fitted permanently and forget about it |

**Easiest to try today:** Way A or B for about **NZ$40 of parts**. **Way C is the tidy end-goal** once a
boat wants it left in for good. *(If your boat has a Vesper AIS unit like Laissez Faire, you may need
none of this — its own USB port already feeds Signal K.)*

---

## What you're actually plugging into (the wiring)

Skip this bit if someone handy is doing the wiring — hand them this section.

Your boat network has **drop cables** — spare sockets you can plug into (a round 5-pin connector called
an M12). You only connect **three wires** from it to the CANable gadget:

| Wire on the boat cable | Colour | Connect to on the CANable |
|---|---|---|
| CAN-H | **white** | CAN-H |
| CAN-L | **blue** | CAN-L |
| Ground / shield | bare + **black** | GND |
| Power (+12 V) | **red** | **leave it disconnected** |

The gadget gets its power from the USB cable, so you **don't** connect the red 12-volt wire. That's the
whole wiring job.

### ⚠ Four things that trip people up
1. **Power comes from USB, not the boat.** Leave the **red** wire capped — don't feed it 12 volts.
2. **You're plugging into a spare socket, not the end of the line — don't add a "terminator."** The
   boat network needs exactly two little end-caps (terminators), which it already has. The CANable has
   a switch/jumper to add one — **leave it off**, or you'll scramble the whole network.
3. **There's a speed setting: 250 (kbps).** Whoever sets it up enters this once. Just so it's not a
   surprise.
4. **Plugging in isn't enough — a program has to translate.** The raw messages are gibberish until
   Signal K decodes them. That's the next step, and it's just software.

---

## Way A — use a laptop you already own

### On a Linux laptop or a Raspberry Pi (the smooth path)
This is where the cheap CANable shines. Whoever sets it up runs a couple of lines to switch the gadget
on, then adds it inside Signal K:

```bash
# turn the CANable on and check data is arriving
sudo ip link set can0 up type can bitrate 250000
candump can0                       # you should see lines scrolling = it's hearing the boat
```
Then in **Signal K** (the translator program): add a connection → type **NMEA 2000** → source
**canboatjs / SocketCAN**, device `can0`. Wind, depth, AIS and the rest appear. Point the Telltale
connector at Signal K and you're done.

### On a Windows or Mac laptop — read this so you don't waste money
**Signal K (the translator) runs perfectly well on Windows and Mac** — that is *not* the problem. The
snag is one layer down: the cheap CANable's easy "just works" driver is a **Linux-only** feature.
Windows and Mac don't have it, so the CANable is fiddly there even though Signal K itself is happy.

**So on a Windows/Mac laptop, don't fight the CANable — buy a plug-and-play gateway instead:**

> A **Yacht Devices YDNU-02N** (~USD$249) or **Actisense NGX-1-USB** (~USD$350) plugs into the same boat
> socket and shows up on your laptop like a normal USB device — no driver drama, works on any computer.
> The cheap CANable is the bargain option **only when you'll run Linux (or the Pi).**

---

## Way B — a little computer that lives on the boat (Raspberry Pi)

Exactly the same wiring as Way A, but the CANable plugs into a **small pre-set-up Raspberry Pi** that
stays on the boat, runs Signal K and the Telltale connector, and does the job automatically every time
you sail. It's powered from the boat's 12-volt supply. This is the answer for boats with **no** onboard
computer. (A boat that already runs Signal K on a fixed screen doesn't need the Pi — just the gadget.)

---

## Way C — a wireless box, no computer needed (the tidy end-goal) ★

Instead of a gadget tethered to a laptop, fit a **gateway** on the boat network that broadcasts the
data over your boat's **WiFi**. Then any phone or laptop on the boat can read it — nothing to keep awake
on the nav table.

- **Buy one (easiest):** **Yacht Devices YDWG-02** (~USD$180) or Actisense **W2K-1**. Plug it into the
  network; it serves the data over WiFi; Signal K (or a nav app) connects to it.
- **Build one (cheapest, ~NZ$20, but it's a soldering project):** a tiny **ESP32** board with our free
  firmware ([`telltale_gateway_n2k.ino`](../firmware/telltale_gateway_n2k.ino)) does the same thing.
  Parts and steps are below.

---

## Way D — the cheap build-it-yourself wireless option (~NZ$20) ★

The cheapest way to get boat data onto WiFi so **any phone reads it** — about NZ$20 in parts. This is the
one to hand other boats for a cheap demo. It's a small electronics project (a bit of soldering).

### If your instruments are on the modern network (NMEA 2000)
| Part (plain name) | ~Price | What it's for |
|---|---|---|
| ESP32 dev board | NZ$10–15 | the little WiFi brain |
| SN65HVD230 "CAN transceiver" board | NZ$3–5 | lets the ESP32 hear the boat network |
| 12 V→5 V "buck" converter | NZ$2–4 | powers it from the boat (or use USB on the bench) |
| Boat network drop cable / socket | ~NZ$15 | to plug into |

**Wiring:** boat CAN-H/CAN-L → the transceiver board → two pins on the ESP32; grounds joined; power from
the converter. **Same rules as before — no terminator, don't put 12 volts into the ESP32 directly.**
Load our firmware with the free Arduino app. It joins the boat WiFi and serves the data → add it in
Signal K.

### If your instruments are the older serial type (NMEA 0183)
- **Build it:** ESP32 + a small "MAX3232/RS422" adapter (~NZ$2) on the instrument's output.
- **Buy it instead (barely any DIY):** an **Elfin EW11** (~NZ$26) — a little box that turns a serial
  output into WiFi, no soldering. Point Signal K (or our AIS tool) at it.

**Reality check:** a bare ESP32 isn't waterproof — fine for a demo, but for a permanent fit add the
power converter and some weatherproofing, or step up to a ready-made **YDWG-02** box.

---

## Where to buy (NZ) — starting points

*Pointers, not the only options — prices move; check current stock.*

| Part | Where (NZ) |
|---|---|
| **Raspberry Pi** + power supply, SD card, case | **[PiShop.nz](https://pishop.nz)** — the NZ Raspberry Pi shop |
| Power converter, jumper wires, breadboard, ESP32 board | PiShop.nz, or search the part on **AliExpress** for the cheapest |
| **CANable** USB gadget | search "CANable" on **AliExpress** (~NZ$25–40) |
| The little electronics bits (SN65HVD230, MP1584, MAX3232) | **AliExpress** (a few dollars each) |
| **Elfin EW11** (serial→WiFi box) | AliExpress / industrial-IoT sellers (~NZ$26) |
| **RTL-SDR dongle + antenna** (for the separate shore-station AIS setup) | search "RTL-SDR" on AliExpress, or an SDR retailer |
| **Ready-made gateways** — Yacht Devices YDNU-02N / YDWG-02, Actisense NGX-1 / W2K-1 | NZ marine-electronics dealers (Burnsco and chandleries), or the makers' resellers |

> **Rule of thumb:** get the **Pi + bits** from PiShop.nz (fast, local, helpful); get the **ultra-cheap
> electronics** from AliExpress (a few dollars, but slow shipping — order early); get the **marine
> gateways** from a marine dealer.

---

## Still not sure? One-line answers

- **Got a Linux laptop and NZ$40?** → CANable, Way A, today.
- **Only a Windows/Mac laptop?** → buy a YDNU-02N gateway (don't fight the CANable).
- **Want it permanent with no laptop?** → a YDWG-02 WiFi gateway (Way C).
- **Boat has no computer at all?** → a little Raspberry Pi + the CANable gadget (Way B).
- **Confused by all of it?** → that's fine. Message us and we'll tell you the one thing to buy for
  *your* boat. Helping you get set up is the whole point.

*For the technically-minded: this is standard NMEA 2000 / CAN at 250 kbps decoded by canboat/Signal K;
the paths above map to SocketCAN (Linux/Pi), a serial N2K→USB gateway (Windows/Mac), or an N2K→WiFi
gateway/DIY ESP32. Related: [[BOAT-KIT-SPEC.md]], reference-boat-hardware, reference-receiver-setup,
project-backlog.*
