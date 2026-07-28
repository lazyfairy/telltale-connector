# Contributing

Thanks for helping improve the Telltale connector and Boat Kit. These tools run on real boats
over flaky links, so the bar is: **simple, dependency-free, and safe to leave running unattended.**

## Ground rules

- **Stdlib only for the Python.** `signalk_telltale.py`, `nmea_wifi_telltale.py` and `ais_relay.py`
  must run on a stock Raspberry Pi with no `pip install`. If you need a third-party library,
  open an issue first — there's usually a stdlib way.
- **Never break the local log or the offline queue.** Data capture is the one promise that can't
  fail: the connector logs every fix to disk *before* trying the network, and buffers uploads
  when offline. Keep that ordering.
- **No secrets in the repo.** Station keys, boat keys, WiFi passwords and the like stay out of
  git (`.gitignore` covers the common cases). Use placeholders like `YOUR_STATION_KEY`.
- **Fail safe.** A bad fix, a dropped connection or a malformed AIS target must never crash the
  loop — catch, log, carry on.
- **Not for navigation.** Nothing here is a navigation aid; don't add anything that implies it is.

## What's especially welcome

- New Signal K path mappings (more instruments, alternate SK keys across versions).
- Additional receiver / platform setups (OpenPlotter, Raspberry Pi OS, Windows, dAISy, Vesper…).
- Docs: clearer install notes, wiring diagrams, real-boat gotchas.

## Making a change

1. Fork and branch.
2. Keep the diff small and focused; match the surrounding style.
3. Test on real hardware if you can, and say what you tested in the PR.
4. Make sure it still compiles — CI runs `py_compile` on the Python and `bash -n` on the shell
   scripts (see `.github/workflows/ci.yml`). You can run the same locally:
   ```bash
   python3 -m py_compile *.py
   bash -n provision.sh
   ```
5. Open a pull request describing the change and why.

## Reporting issues

Include your platform (Pi model / OS), Signal K version if relevant, the command you ran, and
the connector's console output (with any keys redacted).
