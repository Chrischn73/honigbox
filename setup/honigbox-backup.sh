#!/bin/bash
# Taegliches Backup des kompletten HonigBox-App-Ordners (Code + Fotos +
# Einstellungen). Wird immer lokal unter /opt/backup abgelegt UND
# zusaetzlich auf einen eingerichteten USB-Stick kopiert, falls einer unter
# USB_MOUNT eingehaengt ist (eigene Rotation dort). Aufbewahrung nach dem
# Vater-Sohn-Prinzip (honigbox-backup-rotate.py) statt nur "die letzten N" -
# haelt automatisch taegliche/woechentliche/monatliche/jaehrliche
# Stichproben, ohne dass Zeitplan oder Stufen manuell verwaltet werden
# muessen. Einzige Einstellung ist MAX_BACKUPS (Gesamtanzahl je Ort).
set -euo pipefail

SRC_DIR="/opt/honigbox"
DEST_DIR="/opt/backup"
USB_MOUNT="/mnt/backup-usb"
CONFIG_FILE="/opt/backup-scripts/backup.conf"
ROTATE_SCRIPT="$(dirname "$(readlink -f "$0")")/honigbox-backup-rotate.py"

MAX_BACKUPS=30
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

mkdir -p "$DEST_DIR"

timestamp="$(date +%Y-%m-%d-%H%M%S)"
archive_name="honigbox-backup-$timestamp.tar.gz"
archive="$DEST_DIR/$archive_name"

tar czf "$archive" -C "$(dirname "$SRC_DIR")" "$(basename "$SRC_DIR")"
echo "Backup erstellt (lokal): $archive"

# Rotation lokal: Vater-Sohn-Prinzip statt einfach nur "die letzten N".
python3 "$ROTATE_SCRIPT" "$DEST_DIR" "$MAX_BACKUPS"

# Zusaetzlich auf den USB-Stick kopieren, falls einer als Backup-Ziel
# eingerichtet ist. Erst versuchen, ihn (erneut) einzuhaengen: wurde der
# Stick zwischenzeitlich ab- und wieder angesteckt, ohne dass der Pi neu
# gestartet wurde, ist er sonst trotz vorhandenem fstab-Eintrag nicht
# eingehaengt. Kein Fehler, falls kein Stick da ist - das lokale Backup
# existiert in jedem Fall bereits.
mountpoint -q "$USB_MOUNT" || mount "$USB_MOUNT" >/dev/null 2>&1 || true
if mountpoint -q "$USB_MOUNT"; then
    cp "$archive" "$USB_MOUNT/$archive_name"
    echo "Backup zusaetzlich auf USB-Stick kopiert: $USB_MOUNT/$archive_name"
    python3 "$ROTATE_SCRIPT" "$USB_MOUNT" "$MAX_BACKUPS"
else
    echo "Kein USB-Stick als Backup-Ziel eingehaengt - nur lokal gesichert."
fi
