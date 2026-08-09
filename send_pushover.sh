#!/bin/bash
# Gemeinsamer Pushover-Versand. Aufruf: send_pushover.sh <meldung-id>
# z.B. send_pushover.sh geoeffnet
#
# Token/User sowie ob eine Meldung aktiv ist und ihr Text stehen in der von
# der Galerie-Weboberflaeche gespeicherten .pushover-einstellungen.sh (siehe
# /api/pushover in galerie_server.py). Falls diese Datei noch nicht existiert
# (frische Installation, noch nie in der Web-UI gespeichert), faellt dieses
# Skript auf die alte pushover.conf + die urspruenglichen Standardtexte
# zurueck, damit Benachrichtigungen beim Umstieg nicht ploetzlich ausbleiben.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Liegt bewusst NICHT in fotos/Bilder - das kann bei aktivierter RAM-Disk ein
# tmpfs sein, einstellungen/ bleibt immer auf der SD-Karte.
NEUE_KONFIG="$DIR/einstellungen/.pushover-einstellungen.sh"
ALTE_KONFIG="$DIR/pushover.conf"

if [ -f "$NEUE_KONFIG" ]; then
  source "$NEUE_KONFIG"
elif [ -f "$ALTE_KONFIG" ]; then
  source "$ALTE_KONFIG"
fi

# Voruebergehende Stummschaltung (Button auf der Startseite, 30 Min.) - gilt
# fuer ALLE Meldungen, deshalb ganz am Anfang geprueft, noch vor der
# einzelnen ENABLED_<id>-Einstellung.
STUMM_DATEI="$DIR/einstellungen/.pushover-stumm-bis.json"
if [ -f "$STUMM_DATEI" ]; then
  STUMM_AKTIV=$(python3 -c "
import json, time
try:
    with open('$STUMM_DATEI') as f:
        bis = json.load(f).get('bis', 0)
    print('1' if time.time() < bis else '0')
except Exception:
    print('0')
" 2>/dev/null)
  [ "$STUMM_AKTIV" = "1" ] && exit 0
fi

MELDUNG_ID="$1"
ENABLED_VAR="ENABLED_${MELDUNG_ID}"
TEXT_VAR="TEXT_${MELDUNG_ID}"

AKTIV="${!ENABLED_VAR:-1}"
if [ "$AKTIV" = "0" ]; then
  exit 0
fi

TEXT="${!TEXT_VAR}"
if [ -z "$TEXT" ]; then
  case "$MELDUNG_ID" in
    boot)        TEXT="Raspi wurde gestartet!" ;;
    geoeffnet)   TEXT="HONIGBOX wurde geöffnet!" ;;
    eskalation1) TEXT="HONIGBOX Tür steht seit ca. 4 Minuten offen! Warte weitere 30 Min bis zur nächsten Prüfung..." ;;
    eskalation2) TEXT="HONIGBOX Tür steht seit ca. 34 Minuten offen!" ;;
    geschlossen) TEXT="HonigBox wurde geschlossen!!" ;;
  esac
fi
[ -z "$TEXT" ] && exit 0

# --retry: falls Netzwerk/DNS kurz nach dem Booten noch nicht bereit ist,
# nicht sofort aufgeben, sondern bis zu 5x mit steigender Pause erneut versuchen
curl -s --retry 5 --retry-delay 3 --retry-all-errors \
  --form-string "token=$PUSHOVER_TOKEN" \
  --form-string "user=$PUSHOVER_USER" \
  --form-string "message=$TEXT" \
  https://api.pushover.net/1/messages.json
