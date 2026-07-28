#!/usr/bin/env bash
# ---------------------------------------------------------------------------------------------------
# Populate a webroot so the boat-sync server can serve the tactician page OVER THE BOAT WiFi.
#
# The connector's --boat-sync server hosts a small crew cockpit at http://<box>:8137/start so every
# phone on the boat loads it same-origin (no HTTPS->HTTP mixed-content snag) and it works with NO
# internet. This script fetches the current page + its assets from telltaleracing.com into a webroot
# ONCE while you're online; after that the box serves them offline forever. Re-run any time to refresh.
#
# Usage:   ./fetch-webroot.sh [WEBROOT_DIR] [BASE_URL]
#   WEBROOT_DIR  where to put the files       (default: ./webroot, beside signalk_telltale.py)
#   BASE_URL     where to fetch them from      (default: https://telltaleracing.com)
#
# Then run the connector with:  python3 signalk_telltale.py --boat-sync ...
# (it auto-uses ./webroot if present, or pass --boat-sync-webroot WEBROOT_DIR)
# ---------------------------------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WEBROOT="${1:-$HERE/webroot}"
BASE="${2:-https://telltaleracing.com}"
BASE="${BASE%/}"

echo "Fetching the tactician page from $BASE into $WEBROOT ..."
mkdir -p "$WEBROOT/static"

# the pages: the tactician (/start) and the at-sea cockpit (/cockpit)
curl -fsSL "$BASE/start" -o "$WEBROOT/start.html"
curl -fsSL "$BASE/cockpit" -o "$WEBROOT/cockpit.html"
# the crew-sync client (required for the shared cockpit)
curl -fsSL "$BASE/static/boat-sync-client.js" -o "$WEBROOT/static/boat-sync-client.js"
# the contribute promo widget (optional — the page guards for its absence)
curl -fsSL "$BASE/static/lightup.js" -o "$WEBROOT/static/lightup.js" || \
  echo "  (skipped optional lightup.js — not fatal)"

echo "Done. Files in $WEBROOT:"
ls -R "$WEBROOT" | sed 's/^/  /'
echo
echo "Now run:  python3 signalk_telltale.py --boat-sync --boat-sync-code AB12 [your other flags]"
echo "  (serving http://<this-box>:8137/start on the boat WiFi — never leaves the boat)"
