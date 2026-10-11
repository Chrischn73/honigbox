#!/bin/bash
# Telegram-Versand - zweiter, unabhaengiger Kanal neben send_pushover.sh
# (beide werden von honigbox.sh push() aufgerufen, jeweils unabhaengig an/aus).
# Aufruf: send_telegram.sh <meldung-id>
#
# Nutzt bewusst DIESELBEN Meldungstexte wie Pushover (liest TEXT_<id> aus
# .pushover-einstellungen.sh) statt ein eigenes Text-System zu pflegen. Der
# An/Aus-Schalter pro Meldung (TG_ENABLED_<id>, aus der eigenen
# .telegram-einstellungen.sh) ist dagegen bewusst getrennt von Pushovers
# ENABLED_<id> - siehe Notiz in galerie_server.py.
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

# Eigener Schalter fuer DIESEN Kanal - unabhaengig von Pushovers ENABLED_<id>,
# damit z.B. "Pi neu gestartet" nur ueber Telegram, der Rest nur ueber
# Pushover laufen kann. TELEGRAM_KONFIG (oben bereits eingelesen) enthaelt
# TG_ENABLED_<id> pro Meldung.
TG_ENABLED_VAR="TG_ENABLED_${MELDUNG_ID}"
AKTIV="${!TG_ENABLED_VAR:-1}"
if [ "$AKTIV" = "0" ]; then
  exit 0
fi

PUSHOVER_KONFIG="$DIR/einstellungen/.pushover-einstellungen.sh"
[ -f "$PUSHOVER_KONFIG" ] && source "$PUSHOVER_KONFIG"
TEXT_VAR="TEXT_${MELDUNG_ID}"
TEXT="${!TEXT_VAR}"
if [ -z "$TEXT" ]; then
  case "$MELDUNG_ID" in
    boot)        TEXT="Raspi wurde gestartet!" ;;
    geoeffnet)   TEXT="HONIGBOX wurde geöffnet!" ;;
    eskalation1) TEXT="HONIGBOX Tür steht seit ca. 4 Minuten offen! Warte weitere 30 Min bis zur nächsten Prüfung..." ;;
    eskalation2) TEXT="HONIGBOX Tür steht seit ca. 34 Minuten offen!" ;;
    geschlossen) TEXT="HonigBox wurde geschlossen!!" ;;
    systemneustart) TEXT="HonigBox startet wegen Systemupdates neu. Danach bitte den Archiv-Schlüssel eingeben, sonst bleibt das Foto-Archiv gesperrt." ;;
    systemneustart_abgebrochen) TEXT="HonigBox: Der automatische Neustart nach Systemupdates wurde übersprungen." ;;
    archiv_gesperrt) TEXT="HonigBox: Das Foto-Archiv ist noch gesperrt. Bitte die Galerie öffnen und den Archiv-Schlüssel eingeben." ;;
  esac
fi
[ -z "$TEXT" ] && exit 0
# Optionaler 2. Parameter: dynamischer Zusatz (z.B. Grund, Foto-Anzahl) - wird an den Text gehaengt.
[ -n "$2" ] && TEXT="$TEXT $2"

# Als Kommandozeilenargumente statt in den Python-Quelltext einzubetten -
# sicher gegenueber Sonderzeichen (Anführungszeichen, Backslashes) im Text.
# TELEGRAM_API_BASIS nur fuer tests/test_send_telegram.py (lokaler Fake-Server).
python3 - "$TELEGRAM_BOT_TOKEN" "$TEXT" "$CHATS_DATEI" "${TELEGRAM_API_BASIS:-https://api.telegram.org}" << 'PYEOF'
import json
import sys
import syslog
import time
import urllib.error
import urllib.parse
import urllib.request

token, text, chats_pfad, api_basis = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# Fehler landen im Journal (journalctl -t honigbox-telegram) - frueher wurden
# sie komplett verschluckt, eine zur Supergruppe gewordene Gruppe fiel so
# niemandem auf (Vorfall 2026-10-11).
syslog.openlog("honigbox-telegram")

try:
    with open(chats_pfad) as f:
        chats = json.load(f)
except (OSError, json.JSONDecodeError):
    sys.exit(0)


def senden(chat_id):
    """(ok, antwort) - antwort ist Telegrams JSON, auch bei HTTP-Fehlern
    (400 & Co. kommen als HTTPError, tragen aber die eigentliche Auskunft im
    Body). Netzwerkfehler liefern eine Antwort ohne error_code."""
    daten = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        req = urllib.request.Request(f"{api_basis}/bot{token}/sendMessage", data=daten)
        with urllib.request.urlopen(req, timeout=10) as resp:
            antwort = json.loads(resp.read().decode())
            return bool(antwort.get("ok")), antwort
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode())
        except (ValueError, OSError):
            return False, {"error_code": e.code, "description": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, {"description": str(getattr(e, "reason", e))}


umzuege = {}
for chat_id in list(chats):
    ziel = chat_id
    ok, antwort = False, {}
    # Bis zu 3 Versuche mit kurzer Pause - laeuft ueber subprocess.Popen im
    # Hintergrund (siehe honigbox.sh push()), verzoegert die Tuerueberwachung
    # also nicht, ein paar Sekunden Wartezeit hier sind unproblematisch.
    for versuch in range(3):
        ok, antwort = senden(ziel)
        if ok:
            break
        parameter = antwort.get("parameters") or {}
        neue_id = parameter.get("migrate_to_chat_id")
        if neue_id and ziel == chat_id:
            # Gruppe wurde zur Supergruppe (z. B. durch Admin-Ernennung) und
            # hat eine neue ID - sofort dorthin nachsenden und merken.
            neue_id = str(neue_id)
            umzuege[chat_id] = neue_id
            syslog.syslog(syslog.LOG_NOTICE,
                          f"Gruppe {chat_id} ist jetzt Supergruppe {neue_id} - Chat-ID wird umgestellt")
            if neue_id in chats:
                ok = True  # schon separat verknuepft, bekommt ihre Nachricht in eigener Runde
                break
            ziel = neue_id
            continue
        code = antwort.get("error_code")
        if code and code < 500 and code != 429:
            break  # dauerhafter Fehler (Bot entfernt, Chat weg ...) - Wiederholen bringt nichts
        if versuch < 2:
            time.sleep(min(int(parameter.get("retry_after") or 3), 10))
    if not ok:
        syslog.syslog(syslog.LOG_WARNING,
                      f"Senden an Chat {ziel} fehlgeschlagen: {antwort.get('description', 'unbekannt')}")

if umzuege:
    # Frisch einlesen (koennte sich inzwischen geaendert haben) und per "w"
    # in dieselbe Datei schreiben statt tmp+rename - so bleibt sie www-data
    # gehoeren, obwohl dieses Skript als root laeuft.
    try:
        with open(chats_pfad) as f:
            aktuell = json.load(f)
        for alt, neu in umzuege.items():
            if alt in aktuell:
                eintrag = aktuell.pop(alt)
                aktuell.setdefault(neu, eintrag)
        with open(chats_pfad, "w") as f:
            json.dump(aktuell, f)
    except (OSError, json.JSONDecodeError) as e:
        syslog.syslog(syslog.LOG_WARNING, f"Chat-Liste konnte nicht aktualisiert werden: {e}")
PYEOF
