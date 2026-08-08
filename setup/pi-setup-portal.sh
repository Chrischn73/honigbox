#!/bin/bash
# Kanonische Quelle: /media/SSD/Sichern/claude/pi-setup-portal/pi-setup-portal.sh
# NICHT direkt in den Deployment-Kopien unter honigbox-webseite/setup/ oder
# imker-app/setup/ bearbeiten - siehe Warnhinweis in pi_setup_portal.py.
# Aendern, dann im kanonischen Ordner ./sync.sh ausfuehren.
#
# Bereitet das WLAN-Modul vor und startet danach dauerhaft das generische
# Pi-Setup-Portal (pi_setup_portal.py). Laeuft permanent, nicht nur beim
# Ersteinrichten - erreichbar unter http://<hostname>.local, egal ob gerade
# WLAN verbunden ist oder nicht. So laesst sich das WLAN jederzeit neu
# einrichten oder wechseln, nicht nur beim ersten Start.
set -u

PORTAL_SCRIPT="/opt/pi-setup-portal/pi_setup_portal.py"

if command -v rfkill >/dev/null; then
    rfkill unblock wifi || true
fi
if command -v nmcli >/dev/null; then
    nmcli radio wifi on || true
fi

# exec ersetzt den Shell-Prozess durch den Python-Server, damit systemd
# (Type=simple) den Portal-Prozess direkt als Haupt-PID verfolgt.
exec python3 "$PORTAL_SCRIPT"
