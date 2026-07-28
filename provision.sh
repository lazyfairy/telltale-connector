#!/usr/bin/env bash
# Telltale club-device provisioner — turns a fresh Raspberry Pi into a shore AIS station
# or a boat instrument device, feeding Telltale, auto-starting on boot.
#
#   curl -fsSL https://telltaleracing.com/provision.sh | sudo bash -s -- shore "LBYC clubhouse" <STATION_KEY>
#   curl -fsSL https://telltaleracing.com/provision.sh | sudo bash -s -- boat  "Laissez Faire"  <STATION_KEY> [--serial /dev/serial/by-id/XXX] [--baud 4800]
#
# Options: --telltale URL (default https://telltaleracing.com) · --race NAME (default "open")
#          --serial DEV (boat, the by-id path — recommended) · --baud N (boat, 4800 instruments / 38400 AIS)
#
# Run once per device on the bench. Idempotent-ish; safe to re-run.
set -euo pipefail

TELLTALE="https://telltaleracing.com"; RACE="open"; SERIAL=""; BAUD="4800"
usage() {
  cat >&2 <<'USAGE'
Usage: provision.sh <boat|shore> "<name>" <station-key> [options]

  --telltale URL   Telltale server        (default https://telltaleracing.com)
  --race NAME      race id for the feed   (default "open")
  --serial DEV     boat: the /dev/serial/by-id/... path (recommended over /dev/ttyUSB0)
  --baud N         boat: 4800 instruments / 38400 if AIS shares the line

A station provisioned here feeds Telltale and nothing else. We don't forward a
club's AIS anywhere on their behalf.
USAGE
  exit 1
}
# Validate BEFORE consuming args, so a missing argument gives the right error rather than
# tripping the option loop ("unknown option: shore").
MODE="${1:-}"; NAME="${2:-}"; KEY="${3:-}"
case "$MODE" in boat|shore) ;; *) usage;; esac
[ -n "$NAME" ] && [ -n "$KEY" ] || { echo "Need a name and a station key." >&2; usage; }
shift 3
while [ $# -gt 0 ]; do case "$1" in
  --telltale) TELLTALE="${2:?--telltale needs a URL}"; shift 2;;
  --race)     RACE="${2:?--race needs a name}";        shift 2;;
  --serial)   SERIAL="${2:?--serial needs a device}";  shift 2;;
  --baud)     BAUD="${2:?--baud needs a number}";      shift 2;;
  *) echo "unknown option: $1" >&2; usage;;
esac; done

[ "$(id -u)" = 0 ] || { echo "Please run with sudo." >&2; exit 1; }

# Fail fast on a bad key: a typo otherwise produces a device that 403s forever while
# looking perfectly healthy in systemctl.
echo "-- checking the station key --"
if command -v curl >/dev/null 2>&1; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
          "$TELLTALE/api/ais-ingest?key=$KEY" \
          -H 'Content-Type: application/json' -d '{"vessels":[]}' || echo 000)"
  case "$CODE" in
    200) echo "   key OK";;
    403) echo "   *** That station key was rejected (403). Check it at $TELLTALE/stations ***" >&2; exit 1;;
    000) echo "   (couldn't reach $TELLTALE — continuing; the device will retry once online)";;
    *)   echo "   (unexpected response $CODE — continuing)";;
  esac
fi

RUNUSER="${SUDO_USER:-pi}"
CONNDIR="/opt/telltale-device"
echo "== Telltale $MODE device: '$NAME'  ->  $TELLTALE =="

apt-get update -y
apt-get install -y python3 curl

if [ "$MODE" = shore ]; then
  # ---------- SHORE STATION: RTL-SDR + AIS-catcher -> Telltale ----------
  apt-get install -y rtl-sdr
  echo 'blacklist dvb_usb_rtl28xxu' > /etc/modprobe.d/blacklist-rtl.conf   # free the SDR from the TV driver
  if ! command -v AIS-catcher >/dev/null 2>&1; then
    echo "-- installing AIS-catcher --"
    bash -c "$(curl -fsSL https://raw.githubusercontent.com/jvde-github/AIS-catcher/main/scripts/aiscatcher-install)"
  fi
  AISCATCHER="$(command -v AIS-catcher || echo /usr/local/bin/AIS-catcher)"

  # DEVICE SELECTION: never hardcode -d:0. On any box with a USB-serial adapter present the
  # FTDI enumerates first and index 0 grabs the WRONG device — this bit us on Laissez Faire.
  # Resolve the RTL-SDR by index at START time (not install time: USB order can change on reboot),
  # so the unit stays correct across replugs. `-d:N` is the INDEX form; `-d N` means serial number.
  cat > /usr/local/bin/telltale-ais-run <<'RUNEOF'
#!/bin/bash
# Start AIS-catcher on whichever index is actually the RTL-SDR.
set -euo pipefail
AISCATCHER="$1"; shift
IDX="$("$AISCATCHER" -l 2>/dev/null | grep -niE 'rtl|realtek' | head -n1 | sed 's/^\([0-9]\+\).*/\1/')"
IDX="${IDX:-1}"; IDX=$((IDX-1))          # grep -n is 1-based, AIS-catcher indexes from 0
echo "telltale-ais: using RTL-SDR at index $IDX"
exec "$AISCATCHER" -d:"$IDX" "$@"
RUNEOF
  chmod +x /usr/local/bin/telltale-ais-run

  cat > /etc/systemd/system/telltale-ais.service <<EOF
[Unit]
Description=Telltale shore AIS station ($NAME)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/telltale-ais-run $AISCATCHER -X off -H ${TELLTALE}/api/ais-ingest?key=${KEY} INTERVAL 10
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now telltale-ais.service
  echo "== SHORE station live. Watch it: journalctl -u telltale-ais -f   |   $TELLTALE/ais =="

else
  # ---------- BOAT DEVICE: Signal K + the connector ----------
  mkdir -p "$CONNDIR"
  curl -fsSL "$TELLTALE/signalk_telltale.py" -o "$CONNDIR/signalk_telltale.py"

  if ! command -v signalk-server >/dev/null 2>&1; then
    # Signal K requires Node >= 22 (24 recommended). Node 18 went EOL in May 2025 and the
    # install simply fails on it — this is the version to bump when Signal K moves again.
    NODE_MAJOR=22
    NEED_NODE=1
    if command -v node >/dev/null 2>&1; then
      CUR="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
      [ -n "$CUR" ] && [ "$CUR" -ge "$NODE_MAJOR" ] 2>/dev/null && NEED_NODE=0
      [ "$NEED_NODE" = 1 ] && echo "-- node v$CUR is too old for Signal K (need >= $NODE_MAJOR) --"
    fi
    if [ "$NEED_NODE" = 1 ]; then
      echo "-- installing Node.js $NODE_MAJOR (a few minutes) --"
      curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
      apt-get install -y nodejs
    fi
    NODE_OK="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
    if [ -z "$NODE_OK" ] || [ "$NODE_OK" -lt "$NODE_MAJOR" ] 2>/dev/null; then
      echo "*** Node >= $NODE_MAJOR is required and could not be installed. Signal K will not run. ***" >&2
      exit 1
    fi
    echo "-- installing Signal K (node v$NODE_OK) --"
    npm install -g signalk-server
  fi
  SKBIN="$(command -v signalk-server || echo /usr/bin/signalk-server)"

  # Signal K: a serial NMEA-0183 provider (prefer the stable /dev/serial/by-id/... path)
  SKDIR="/home/$RUNUSER/.signalk"; mkdir -p "$SKDIR"
  DEV="${SERIAL:-/dev/ttyUSB0}"
  cat > "$SKDIR/settings.json" <<EOF
{
  "pipedProviders": [
    { "id": "nmea0183-serial", "enabled": true, "pipeElements": [
      { "type": "providers/simple", "options": {
        "logging": false, "type": "NMEA0183",
        "subOptions": { "type": "serial", "device": "$DEV", "baudrate": $BAUD } } } ] }
  ]
}
EOF
  chown -R "$RUNUSER":"$RUNUSER" "$SKDIR"

  cat > /etc/systemd/system/signalk.service <<EOF
[Unit]
Description=Signal K server
After=network-online.target
Wants=network-online.target
[Service]
User=$RUNUSER
ExecStart=$SKBIN
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

  cat > /etc/systemd/system/telltale-connector.service <<EOF
[Unit]
Description=Telltale Signal K connector ($NAME)
After=signalk.service
Wants=signalk.service
[Service]
ExecStart=/usr/bin/python3 $CONNDIR/signalk_telltale.py --telltale $TELLTALE --race "$RACE" --boat "$NAME" --station-key $KEY --interval 5
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
EOF
  # ---------- self-update: keep the connector current, unattended ----------
  # Fetch the latest connector, syntax-check it BEFORE swapping (never restart onto a broken download),
  # then restart. Runs daily at ~04:30 with up to 1h jitter so the fleet doesn't all pull at once.
  cat > /etc/systemd/system/telltale-update.service <<EOF
[Unit]
Description=Telltale connector self-update ($NAME)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'curl -fsSL $TELLTALE/signalk_telltale.py -o $CONNDIR/.signalk_telltale.new && python3 -m py_compile $CONNDIR/.signalk_telltale.new && mv $CONNDIR/.signalk_telltale.new $CONNDIR/signalk_telltale.py && systemctl restart telltale-connector.service && echo updated'
EOF
  cat > /etc/systemd/system/telltale-update.timer <<EOF
[Unit]
Description=Daily Telltale connector self-update
[Timer]
OnCalendar=*-*-* 04:30:00
RandomizedDelaySec=3600
Persistent=true
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now signalk.service telltale-connector.service telltale-update.timer
  echo "== BOAT device live. Signal K admin: http://<pi-ip>:3000   Connector: journalctl -u telltale-connector -f =="
  echo "   Self-update: enabled (daily). Force now:  sudo systemctl start telltale-update.service"
  echo "   No data? Check the wire pair + baud:  ls -l /dev/serial/by-id/   then re-run with  --serial <by-id-path> --baud <4800|38400>"
fi
echo "== '$NAME' will feed $TELLTALE automatically on every boot. =="
