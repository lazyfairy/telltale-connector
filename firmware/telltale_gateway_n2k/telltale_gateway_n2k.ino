/*
 * Telltale ESP32 NMEA-2000 -> WiFi gateway   (~NZ$10-14, truly plug-and-play)
 * -------------------------------------------------------------------------------
 * The "anyone can do it" version: NO wiring. NMEA-2000 uses standard Micro-C plugs and
 * T-pieces (drop-tees), so you plug a tee into the backbone, plug this box into the tee,
 * and it powers itself off the bus's 12V. One plug = data + power. Nothing to strip.
 *
 * It listens to the N2K bus, decodes the PGNs sailors care about, and RE-EMITS standard
 * NMEA-0183 over WiFi — so everything downstream is identical to the 0183 box:
 *   1. UDP broadcast on :10110   -> every phone / OpenCPN on the WiFi, no connection limit
 *   2. TCP server   on :10110   -> OpenCPN / Navionics "NMEA over TCP" source
 * The Telltale phone app / /relay reads it (with phone GPS) and relays to telltaleracing.com.
 *
 * SELF-CONTAINED: decodes PGNs and formats 0183 itself — depends ONLY on the core NMEA2000
 * libraries below (no example converter files to copy). See README.md for wiring + flashing.
 *
 * INSTALL THE LIBRARIES (Arduino IDE -> Library Manager):
 *   - "NMEA2000_esp32"  (by ttlappalainen)   + its dependency "NMEA2000"
 * Board: "ESP32 Dev Module".
 *
 * WIRING (only the CAN transceiver — everything boat-side is plug-in):
 *   ESP32 GPIO5 -> SN65HVD230 CTX,  ESP32 GPIO4 -> SN65HVD230 CRX,  3V3 + GND to the transceiver.
 *   SN65HVD230 CANH/CANL -> the Micro-C plug's NET-H / NET-L pins.
 *   Bus power: NET-V (12V) + NET-C (GND) from the plug -> a 12V->5V buck -> ESP32 5V.  (~0.15A load)
 */

#include <Arduino.h>
#include <math.h>
#define ESP32_CAN_TX_PIN GPIO_NUM_5      // -> SN65HVD230 CTX
#define ESP32_CAN_RX_PIN GPIO_NUM_4      // -> SN65HVD230 CRX
#include <NMEA2000_CAN.h>                // pulls in NMEA2000_esp32 for this board
#include <N2kMessages.h>

#include <WiFi.h>
#include <WiFiUdp.h>

// ---------------- CONFIG (edit, flash, plug in) ----------------
#define MODE_STA   1                     // 1 = join boat WiFi; 0 = be our own AP
const char* WIFI_SSID = "BoatWiFi";
const char* WIFI_PASS = "changeme";
// AP mode only: "" = auto-generate a password UNIQUE to this box (printed to serial). Never share one
// password across boxes — boats race within WiFi range, so a shared password leaks your instruments.
const char* AP_PASS = "";
const uint16_t NMEA_PORT = 10110;
// ---------------------------------------------------------------

WiFiUDP    udp;
WiFiServer tcp(NMEA_PORT);
WiFiClient tcpClients[6];
IPAddress  bcast;

static const double RAD2DEG = 57.29577951308232;
static const double MS2KN   = 1.94384;

// A password UNIQUE to this box (from its chip ID) so no two Telltale gateways share one.
static String apPass() {
  static String cached;
  if (cached.length()) return cached;
  if (AP_PASS && strlen(AP_PASS) >= 8) cached = String(AP_PASS);
  else { char b[20]; snprintf(b, sizeof(b), "tt-%08x", (uint32_t)(ESP.getEfuseMac() & 0xFFFFFFFF)); cached = String(b); }
  Serial.printf("[wifi] AP password: %s  (unique to this box — crew only)\n", cached.c_str());
  return cached;
}

static void fanout(const char* line, size_t n) {
  udp.beginPacket(bcast, NMEA_PORT);
  udp.write((const uint8_t*)line, n);
  udp.endPacket();
  for (auto& c : tcpClients) if (c && c.connected()) c.write((const uint8_t*)line, n);
}

// build a 0183 sentence: body is between the '$' and the '*checksum' — we add both + CRLF
static void send0183(const char* body) {
  char s[110]; uint8_t cs = 0;
  for (const char* p = body; *p; p++) cs ^= (uint8_t)*p;
  int n = snprintf(s, sizeof(s), "$%s*%02X\r\n", body, cs);
  if (n > 0) fanout(s, (size_t)n);
}

static void fmtLat(double lat, char* out) {   // ddmm.mmmm,N
  char hemi = lat < 0 ? 'S' : 'N'; lat = fabs(lat);
  int d = (int)lat; double m = (lat - d) * 60.0;
  snprintf(out, 20, "%02d%07.4f,%c", d, m, hemi);
}
static void fmtLon(double lon, char* out) {   // dddmm.mmmm,E
  char hemi = lon < 0 ? 'W' : 'E'; lon = fabs(lon);
  int d = (int)lon; double m = (lon - d) * 60.0;
  snprintf(out, 20, "%03d%07.4f,%c", d, m, hemi);
}

// decode the PGNs sailors use and re-emit them as standard 0183
void HandleN2K(const tN2kMsg& msg) {
  unsigned char SID;
  char b[100], la[20], lo[20];
  switch (msg.PGN) {
    case 129025L: {   // Position, Rapid -> GLL
      double lat, lon;
      if (ParseN2kPositionRapid(msg, lat, lon) && !N2kIsNA(lat)) {
        fmtLat(lat, la); fmtLon(lon, lo);
        snprintf(b, sizeof(b), "GPGLL,%s,%s,,A", la, lo); send0183(b);
      } break; }
    case 129026L: {   // COG/SOG, Rapid -> VTG
      tN2kHeadingReference ref; double cog, sog;
      if (ParseN2kCOGSOGRapid(msg, SID, ref, cog, sog) && !N2kIsNA(sog)) {
        snprintf(b, sizeof(b), "GPVTG,%.1f,T,,M,%.1f,N,,K", N2kIsNA(cog) ? 0.0 : cog * RAD2DEG, sog * MS2KN);
        send0183(b);
      } break; }
    case 130306L: {   // Wind -> MWV (apparent 'R' or true 'T')
      tN2kWindReference wref; double ws, wa;
      if (ParseN2kWindSpeed(msg, SID, ws, wa, wref) && !N2kIsNA(ws) && !N2kIsNA(wa)) {
        double a = wa * RAD2DEG; if (a < 0) a += 360;
        char ref = (wref == N2kWind_Apparent) ? 'R' : 'T';
        snprintf(b, sizeof(b), "WIMWV,%.1f,%c,%.1f,N,A", a, ref, ws * MS2KN); send0183(b);
      } break; }
    case 128259L: {   // Speed (through water) -> VHW
      tN2kSpeedWaterReferenceType swrt; double stw, gspd;
      if (ParseN2kBoatSpeed(msg, SID, stw, gspd, swrt) && !N2kIsNA(stw)) {
        snprintf(b, sizeof(b), "VWVHW,,,,,%.2f,N,,", stw * MS2KN); send0183(b);
      } break; }
    case 128267L: {   // Water depth -> DPT
      double depth, off, range;
      if (ParseN2kWaterDepth(msg, SID, depth, off, range) && !N2kIsNA(depth)) {
        snprintf(b, sizeof(b), "SDDPT,%.1f,%.1f", depth, N2kIsNA(off) ? 0.0 : off); send0183(b);
      } break; }
    case 127250L: {   // Vessel heading -> HDG
      tN2kHeadingReference ref; double hdg, dev, var;
      if (ParseN2kHeading(msg, SID, hdg, dev, var, ref) && !N2kIsNA(hdg)) {
        snprintf(b, sizeof(b), "HCHDG,%.1f,,,,", hdg * RAD2DEG); send0183(b);
      } break; }
    case 127257L: {   // Attitude (roll = heel) -> XDR
      double yaw, pitch, roll;
      if (ParseN2kAttitude(msg, SID, yaw, pitch, roll) && !N2kIsNA(roll)) {
        snprintf(b, sizeof(b), "IIXDR,A,%.1f,D,ROLL", roll * RAD2DEG); send0183(b);
      } break; }
  }
}

void setup() {
  Serial.begin(115200);

#if MODE_STA
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(300); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) { bcast = WiFi.localIP(); bcast[3] = 255;
    Serial.printf("\n[wifi] joined, IP %s\n", WiFi.localIP().toString().c_str()); }
  else { WiFi.mode(WIFI_AP); WiFi.softAP("Telltale-Gateway", apPass().c_str()); bcast = IPAddress(192,168,4,255);
    Serial.println("\n[wifi] STA failed -> own AP 'Telltale-Gateway'"); }
#else
  WiFi.mode(WIFI_AP); WiFi.softAP("Telltale-Gateway", apPass().c_str()); bcast = IPAddress(192,168,4,255);
#endif

  udp.begin(NMEA_PORT);
  tcp.begin(); tcp.setNoDelay(true);

  // N2K: listen-only (never inject onto the boat bus), decode -> 0183 over WiFi
  NMEA2000.SetForwardType(tNMEA2000::fwdt_Text);
  NMEA2000.SetForwardStream(&Serial);
  NMEA2000.EnableForward(false);
  NMEA2000.SetMode(tNMEA2000::N2km_ListenOnly);
  NMEA2000.SetMsgHandler(HandleN2K);
  NMEA2000.Open();

  Serial.printf("[ready] decoding N2K -> 0183 over WiFi UDP+TCP :%u\n", NMEA_PORT);
}

void loop() {
  if (tcp.hasClient()) {
    WiFiClient nc = tcp.available(); bool placed = false;
    for (auto& c : tcpClients) if (!c || !c.connected()) { c = nc; placed = true; break; }
    if (!placed) nc.stop();
  }
  NMEA2000.ParseMessages();     // pumps the CAN bus -> HandleN2K -> 0183 over WiFi
}
