#!/bin/bash
# Wird vom gemeinsamen Setup-Portal (setup_portal.py, _restore_from_tar)
# unmittelbar vor ("pre") bzw. nach ("post") einem Fotos-Backup-Restore
# aufgerufen - siehe pre_restore_hook/post_restore_hook im HonigBox-Deskriptor
# (apps.d/honigbox.json, von install.sh geschrieben). Laeuft als root (das
# Setup-Portal selbst laeuft als root), kein sudo/systemd-run noetig.
#
# Grund fuer diesen Hook: Restore ersetzt /opt/honigbox/fotos komplett per
# "umbenennen, neu befuellen, alten Ordner loeschen" (rename+rmtree). Waere
# darin (Bilder/) gerade eine RAM-Disk (tmpfs) aktiv gemountet, wuerde dieses
# rmtree deren Inhalt versehentlich mitloeschen UND einen verwaisten,
# unerreichbaren Mount zuruecklassen - ohne jede Fehlermeldung. Dieser Hook
# schaltet dafuer VOR dem Restore kurz auf "platte" um (kein aktiver Mount
# mehr im betroffenen Ordner) und NACH dem Restore wieder zurueck auf die
# urspruengliche Einstellung.
set -euo pipefail

EINSTELLUNGEN_DIR="/opt/honigbox/einstellungen"
SPEICHER_DATEI="$EINSTELLUNGEN_DIR/.speicher-einstellungen.json"
MARKER_DATEI="$EINSTELLUNGEN_DIR/.restore-vorheriger-speicherort.json"
UMSCHALT_SCRIPT="/opt/honigbox/speicher_umschalten.sh"
BILDER_DIR="/opt/honigbox/fotos/Bilder"

MODUS="${1:-}"

ist_tmpfs() {
    mountpoint -q "$BILDER_DIR" 2>/dev/null && \
        findmnt -n -o FSTYPE --target "$BILDER_DIR" 2>/dev/null | grep -q '^tmpfs$'
}

# speicher_umschalten.sh startet honigbox.service/honigbox-galerie.service an
# seinem eigenen Ende selbst wieder - hier aber noch nicht erwuenscht, das
# eigentliche Restore (im Portal-Code) laeuft ja erst direkt danach. Deshalb
# nach jedem Aufruf hier sofort wieder stoppen; das Portal startet die
# Dienste selbst, sobald der gesamte Restore-Vorgang abgeschlossen ist.
stoppe_dienste_wieder() {
    systemctl stop honigbox.service honigbox-galerie.service 2>/dev/null || true
}

if [ "$MODUS" = "pre" ]; then
    if [ -f "$SPEICHER_DATEI" ] && ist_tmpfs; then
        cp "$SPEICHER_DATEI" "$MARKER_DATEI"
        python3 -c "
import json
with open('$SPEICHER_DATEI') as f:
    werte = json.load(f)
werte['speicherort'] = 'platte'
with open('$SPEICHER_DATEI', 'w') as f:
    json.dump(werte, f)
"
        bash "$UMSCHALT_SCRIPT" || true
        stoppe_dienste_wieder
    fi
elif [ "$MODUS" = "post" ]; then
    if [ -f "$MARKER_DATEI" ]; then
        cp "$MARKER_DATEI" "$SPEICHER_DATEI"
        rm -f "$MARKER_DATEI"
        bash "$UMSCHALT_SCRIPT" || true
        stoppe_dienste_wieder
    fi
fi
