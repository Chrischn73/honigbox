#!/bin/bash
# Bereitet das WLAN-Modul vor und startet danach dauerhaft das
# WLAN-Einstellungen-Portal (honigbox_setup_portal.py). Laeuft permanent,
# nicht nur beim Ersteinrichten - erreichbar unter http://<hostname>.local,
# egal ob gerade WLAN verbunden ist oder nicht.
set -u

PORTAL_SCRIPT="/opt/honigbox-wifi-setup/honigbox_setup_portal.py"

if command -v rfkill >/dev/null; then
  rfkill unblock wifi || true
fi
if command -v nmcli >/dev/null; then
  nmcli radio wifi on || true
fi

# exec ersetzt den Shell-Prozess durch den Python-Server, damit systemd
# (Type=simple) den Portal-Prozess direkt als Haupt-PID verfolgt.
exec python3 "$PORTAL_SCRIPT"
