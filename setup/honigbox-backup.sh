#!/bin/bash
# Taegliches Backup des HonigBox-App-Ordners OHNE die Fotos (die sind wegen
# der geplanten Archiv-Verschluesselung/DSGVO bewusst ausgeschlossen - ein
# Backup wuerde sie sonst entweder unverschluesselt an den Backup-Ort
# durchreichen oder nutzlose Ciphertext-Daten sichern). Gesichert werden
# also nur Code + Einstellungen. Wird immer lokal unter /opt/backup abgelegt
# UND zusaetzlich auf einen eingerichteten USB-Stick kopiert, falls einer
# unter USB_MOUNT eingehaengt ist (eigene Rotation dort). Aufbewahrung nach
# dem Vater-Sohn-Prinzip (honigbox-backup-rotate.py) statt nur "die letzten
# N" - haelt automatisch taegliche/woechentliche/monatliche/jaehrliche
# Stichproben, ohne dass Zeitplan oder Stufen manuell verwaltet werden
# muessen. Einzige Einstellung ist MAX_BACKUPS (Gesamtanzahl je Ort).
#
# --nur-bei-aenderung (nur der naechtliche Timer, honigbox-backup.service):
# kein neues Archiv, wenn sich seit dem letzten Backup nichts geaendert hat
# (Fingerabdruck per honigbox-backup-fingerprint.py) - schont die SD-Karte.
# Manuelle Backups aus dem Setup-Portal und Backups vor einem Update rufen
# das Skript OHNE Option auf und sichern deshalb immer.
set -euo pipefail

SRC_DIR="/opt/honigbox"
DEST_DIR="/opt/backup"
USB_MOUNT="/mnt/backup-usb"
CONFIG_FILE="/opt/backup-scripts/backup.conf"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ROTATE_SCRIPT="$SCRIPT_DIR/honigbox-backup-rotate.py"
FINGERPRINT_SCRIPT="$SCRIPT_DIR/honigbox-backup-fingerprint.py"
FINGERPRINT_FILE="$DEST_DIR/.honigbox-fingerprint"

NUR_BEI_AENDERUNG=0
[ "${1:-}" = "--nur-bei-aenderung" ] && NUR_BEI_AENDERUNG=1

MAX_BACKUPS=30
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

mkdir -p "$DEST_DIR"

# Zusaetzlich auf den USB-Stick kopieren, falls einer als Backup-Ziel
# eingerichtet ist. Erst versuchen, ihn (erneut) einzuhaengen: wurde der
# Stick zwischenzeitlich ab- und wieder angesteckt, ohne dass der Pi neu
# gestartet wurde, ist er sonst trotz vorhandenem fstab-Eintrag nicht
# eingehaengt. Kein Fehler, falls kein Stick da ist - das lokale Backup
# existiert in jedem Fall bereits.
copy_to_usb() {
    local archive="$1" archive_name
    archive_name="$(basename "$archive")"
    mountpoint -q "$USB_MOUNT" || mount "$USB_MOUNT" >/dev/null 2>&1 || true
    if ! mountpoint -q "$USB_MOUNT"; then
        echo "Kein USB-Stick als Backup-Ziel eingehaengt - nur lokal gesichert."
        return 0
    fi
    if [ -f "$USB_MOUNT/$archive_name" ]; then
        echo "Backup liegt bereits auf dem USB-Stick: $USB_MOUNT/$archive_name"
        return 0
    fi
    cp "$archive" "$USB_MOUNT/$archive_name"
    echo "Backup zusaetzlich auf USB-Stick kopiert: $USB_MOUNT/$archive_name"
    python3 "$ROTATE_SCRIPT" "$USB_MOUNT" "$MAX_BACKUPS"
}

# Schlaegt die Berechnung fehl oder haengt sie (timeout), bleibt der
# Fingerabdruck leer -> es wird sicherheitshalber ganz normal gesichert.
fingerprint="$(timeout 120 python3 "$FINGERPRINT_SCRIPT" "$SRC_DIR" 2>/dev/null)" || fingerprint=""

if [ "$NUR_BEI_AENDERUNG" = 1 ] && [ -n "$fingerprint" ] && [ -f "$FINGERPRINT_FILE" ] \
        && [ "$(cat "$FINGERPRINT_FILE")" = "$fingerprint" ]; then
    newest="$(ls -1 "$DEST_DIR"/honigbox-backup-*.tar.gz 2>/dev/null | sort | tail -n 1 || true)"
    if [ -n "$newest" ]; then
        echo "Keine Aenderung seit dem letzten Backup ($(basename "$newest")) - uebersprungen."
        # Neu eingerichteter/angesteckter Stick bekommt trotzdem den letzten Stand.
        copy_to_usb "$newest"
        exit 0
    fi
fi

timestamp="$(date +%Y-%m-%d-%H%M%S)"
archive_name="honigbox-backup-$timestamp.tar.gz"
archive="$DEST_DIR/$archive_name"

tar czf "$archive" --exclude="$(basename "$SRC_DIR")/fotos" -C "$(dirname "$SRC_DIR")" "$(basename "$SRC_DIR")"
echo "Backup erstellt (lokal): $archive"
[ -n "$fingerprint" ] && printf '%s\n' "$fingerprint" > "$FINGERPRINT_FILE"

# Rotation lokal: Vater-Sohn-Prinzip statt einfach nur "die letzten N".
python3 "$ROTATE_SCRIPT" "$DEST_DIR" "$MAX_BACKUPS"

copy_to_usb "$archive"
