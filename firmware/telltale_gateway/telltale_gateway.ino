/*
 * Telltale ESP32 NMEA-0183 -> WiFi gateway  (~US$5 board + ~US$2 RS-422 level converter)
 * ---------------------------------------------------------------------------------------
 * Reads the boat's NMEA-0183 output (the two signal wires A/+ and B/-) and re-serves it so
 * EVERYTHING on the boat drinks from the same box at once, with no connection limit:
 *
 *   1. UDP broadcast on :10110      -> every phone / tablet / OpenCPN on the WiFi hears it
 *   2. TCP server   on :10110       -> OpenCPN / Navionics / Aqua Map "NMEA over TCP" source
 *   3. BLE (optional)               -> low-power phone-only fallback when there's no WiFi
 *
 * Two WiFi modes (set MODE below):
 *   STA : join the boat's existing WiFi router  (preferred — a phone/router carries it to the internet)
 *   AP  : the ESP32 makes its OWN WiFi "Telltale-Gateway"  (no boat router needed; phones join it)
 *
 * Install = power (5V, e.g. USB) + the two 0183 wires into the level converter. That's it.
 * The Telltale phone app / signalk_telltale.py reads this feed and relays it (with phone GPS)
 * to telltaleracing.com. This box only re-serves the boat's OWN NMEA (incl. its own AIS if on the
 * bus) — the wider AIS fleet is the shore station's job.
 *
 * Board:  any ESP32 dev board (WROOM-32).  Arduino IDE: Tools -> Board -> "ESP32 Dev Module".
 * Wiring: 0183 A/+ and B/- -> a MAX3485/MAX485 (RS-422) breakout -> RO pin -> ESP32 GPIO16 (RX2).
 *         Common ground. DE/RE tied low (receive-only — we never transmit back onto the bus).
 *         Instruments are 4800 baud; a dedicated AIS 0183 output is 38400 — set NMEA_BAUD to match.
 */

#include <WiFi.h>
#include <WiFiUdp.h>

// ---------------- CONFIG (edit these three, flash, done) ----------------
#define MODE_STA   1                 // 1 = join boat WiFi (STA); 0 = be our own AP
const char* WIFI_SSID = "BoatWiFi";  // your boat router's SSID   (STA mode)
const char* WIFI_PASS = "changeme";  // your boat router's password (STA mode)
// AP mode only: leave "" to auto-generate a password UNIQUE to this box (printed to serial on boot).
// NEVER ship a shared password — boats race within WiFi range, so a shared password = a competitor
// with the same kit can read your instruments. Set your own (>=8 chars) to override, or leave "" for unique.
const char* AP_PASS = "";

const uint32_t NMEA_BAUD = 4800;     // 4800 = instruments; 38400 = a dedicated AIS 0183 output
const uint16_t NMEA_PORT = 10110;    // de-facto standard "NMEA over IP" port (UDP + TCP)
#define ENABLE_BLE 0                 // 1 to also advertise over BLE (costs flash + power)

const int   RX_PIN = 16;             // ESP32 RX2  <- level converter RO
const int   TX_PIN = 17;             // unused (receive-only) but Serial2 wants a TX pin
// ------------------------------------------------------------------------

WiFiUDP   udp;
WiFiServer tcp(NMEA_PORT);
WiFiClient tcpClients[6];            // OpenCPN etc. — a handful of TCP listeners
IPAddress bcast;

#if ENABLE_BLE
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
// Nordic UART Service UUIDs — what generic BLE-serial phone apps expect
#define NUS_SERVICE "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_TX      "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
BLECharacteristic* bleTx = nullptr;
bool bleReady = false;
#endif

// A password UNIQUE to this box (from its chip ID) so no two Telltale gateways share one.
// Printed once to serial — give it only to your own crew.
static String apPass() {
  static String cached;
  if (cached.length()) return cached;
  if (AP_PASS && strlen(AP_PASS) >= 8) cached = String(AP_PASS);
  else { char b[20]; snprintf(b, sizeof(b), "tt-%08x", (uint32_t)(ESP.getEfuseMac() & 0xFFFFFFFF)); cached = String(b); }
  Serial.printf("[wifi] AP password: %s  (unique to this box — crew only)\n", cached.c_str());
  return cached;
}

static void startBLE() {
#if ENABLE_BLE
  BLEDevice::init("Telltale-Gateway");
  BLEServer* s = BLEDevice::createServer();
  BLEService* svc = s->createService(NUS_SERVICE);
  bleTx = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  bleTx->addDescriptor(new BLE2902());
  svc->start();
  s->getAdvertising()->addServiceUUID(NUS_SERVICE);
  s->getAdvertising()->start();
  bleReady = true;
  Serial.println("[ble] advertising as Telltale-Gateway (Nordic UART)");
#endif
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(NMEA_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);   // the boat's 0183 feed

#if MODE_STA
  Serial.printf("[wifi] joining %s ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(300); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] joined, IP %s\n", WiFi.localIP().toString().c_str());
    bcast = WiFi.localIP(); bcast[3] = 255;               // subnet broadcast
  } else {
    Serial.println("\n[wifi] STA failed -> falling back to own AP");
    WiFi.mode(WIFI_AP); WiFi.softAP("Telltale-Gateway", apPass().c_str());
    bcast = IPAddress(192,168,4,255);
  }
#else
  WiFi.mode(WIFI_AP); WiFi.softAP("Telltale-Gateway", apPass().c_str());
  Serial.printf("[wifi] AP 'Telltale-Gateway', IP %s\n", WiFi.softAPIP().toString().c_str());
  bcast = IPAddress(192,168,4,255);
#endif

  udp.begin(NMEA_PORT);
  tcp.begin();
  tcp.setNoDelay(true);
  startBLE();
  Serial.printf("[ready] re-serving 0183 @%u baud -> UDP+TCP :%u\n", NMEA_BAUD, NMEA_PORT);
}

static void fanout(const char* line, size_t n) {
  // 1) UDP broadcast — every device on the WiFi, no connection limit
  udp.beginPacket(bcast, NMEA_PORT);
  udp.write((const uint8_t*)line, n);
  udp.endPacket();
  // 2) TCP — OpenCPN and friends
  for (auto& c : tcpClients) if (c && c.connected()) c.write((const uint8_t*)line, n);
#if ENABLE_BLE
  // 3) BLE — chunk to <=20 bytes/notification (default MTU)
  if (bleReady && bleTx) {
    for (size_t i = 0; i < n; i += 20) {
      size_t len = min((size_t)20, n - i);
      bleTx->setValue((uint8_t*)line + i, len);
      bleTx->notify();
    }
  }
#endif
}

void loop() {
  // accept new TCP listeners into a free slot
  if (tcp.hasClient()) {
    WiFiClient nc = tcp.available();
    bool placed = false;
    for (auto& c : tcpClients) if (!c || !c.connected()) { c = nc; placed = true; break; }
    if (!placed) nc.stop();                               // all slots full — drop politely
  }

  // read complete NMEA lines and fan them out
  static char buf[128];
  static size_t len = 0;
  while (Serial2.available()) {
    char ch = Serial2.read();
    if (ch == '\r') continue;
    if (ch == '\n' || len >= sizeof(buf) - 2) {
      if (len > 0) {
        buf[len++] = '\r'; buf[len++] = '\n';             // keep sentences CRLF-terminated
        fanout(buf, len);
      }
      len = 0;
    } else {
      buf[len++] = ch;
    }
  }
}
