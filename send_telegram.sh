#!/bin/bash
# Telegram-Versand - zweiter, unabhaengiger Kanal neben send_pushover.sh
# (beide werden von honigbox.sh push() aufgerufen, jeweils unabhaengig an/aus).
# Aufruf: send_telegram.sh <meldung-id>
#
# Nutzt bewusst DIESELBEN Meldungstexte/aktiv-Schalter wie Pushover (liest
# dieselbe .pushover-einstellungen.sh) statt ein eigenes Text-System zu
# pflegen - "erster Schritt", siehe Notiz in galerie_server.py.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TELEGRAM_KONFIG="$DIR/einstellungen/.telegram-einstellungen.sh"
[ -f "$TELEGRAM_KONFIG" ] || exit 0
source "$TELEGRAM_KONFIG"
[ -n "$TELEGRAM_BOT_TOKEN" ] || exit 0

# Kompletter Kanal-Schalter ("Telegram aktiv" in den Einstellungen) - unabhaengig
# vom Pushover-Schalter, siehe send_pushover.sh.
[ "${TELEGRAM_AKTIV:-1}" = "0" ] && exit 0

CHATS_DATEI="$DIR/einstellungen/.telegram-chats.json"
[ -f "$CHATS_DATEI" ] || exit 0

# Stummschaltung teilen wir uns mit Pushover (ein Schalter fuer beide Kanaele,
# siehe /api/pushover/stumm in galerie_server.py).
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

PUSHOVER_KONFIG="$DIR/einstellungen/.pushover-einstellungen.sh"
[ -f "$PUSHOVER_KONFIG" ] && source "$PUSHOVER_KONFIG"
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

# Als Kommandozeilenargumente statt in den Python-Quelltext einzubetten -
# sicher gegenueber Sonderzeichen (Anführungszeichen, Backslashes) im Text.
python3 - "$TELEGRAM_BOT_TOKEN" "$TEXT" "$CHATS_DATEI" << 'PYEOF'
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

token, text, chats_pfad = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    with open(chats_pfad) as f:
        chats = json.load(f)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

for chat_id in chats:
    daten = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    # Bis zu 3 Versuche mit kurzer Pause - laeuft ueber subprocess.Popen im
    # Hintergrund (siehe honigbox.sh push()), verzoegert die Tuerueberwachung
    # also nicht, ein paar Sekunden Wartezeit hier sind unproblematisch.
    for versuch in range(3):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=daten)
            urllib.request.urlopen(req, timeout=10)
            break
        except (urllib.error.URLError, OSError):
            if versuch < 2:
                time.sleep(3)
PYEOF
