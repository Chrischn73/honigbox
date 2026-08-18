#!/usr/bin/env python3
"""
BeeTown HonigBox - kleiner Server (nur Python-Standardbibliothek).
"""
import base64
import hashlib
import hmac
import html
import http.cookies
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlparse

BASE       = os.path.dirname(os.path.abspath(__file__))
FOTO_SCRIPT = os.environ.get("GALERIE_FOTO_SCRIPT", os.path.join(BASE, "foto.sh"))
HOST       = os.environ.get("GALERIE_HOST", "0.0.0.0")
PORT       = int(os.environ.get("GALERIE_PORT", "8090"))
STATIC_DIR = os.environ.get("GALERIE_STATIC", os.path.join(BASE, "static"))
BILDER_DIR = os.environ.get("GALERIE_BILDER", "/opt/honigbox/fotos/Bilder")
ARCHIV_DIR = os.environ.get("GALERIE_ARCHIV", "/opt/honigbox/fotos/Archiv")
AUTH_USER  = os.environ.get("GALERIE_USER", "")
AUTH_PASS  = os.environ.get("GALERIE_PASSWORT", "")

# Eigener, IMMER auf der SD-Karte liegender Ordner fuer Einstellungen/Status -
# bewusst NICHT in BILDER_DIR, das bei aktivierter RAM-Disk (siehe
# Speicher-Einstellungen weiter unten) ein tmpfs sein kann und bei jedem
# Neustart geleert wird. chmod 777 aus demselben Grund wie bei
# BILDER_DIR/ARCHIV_DIR: der Server-Prozess (www-data) muss hier garantiert
# schreiben koennen, waehrend er auf /opt/honigbox selbst evtl. keine
# Schreibrechte hat.
EINSTELLUNGEN_DIR = os.environ.get("GALERIE_EINSTELLUNGEN_DIR", "/opt/honigbox/einstellungen")
os.makedirs(EINSTELLUNGEN_DIR, exist_ok=True)
try:
    os.chmod(EINSTELLUNGEN_DIR, 0o777)
except OSError:
    pass

# Einfacher "FritzBox-Stil"-Zugangsschutz fuer die Web-Oberflaeche - EIN
# gemeinsames Passwort fuer alle, kein Benutzerkonto. Ziel: verhindert, dass
# jemand im selben Netzwerk (z.B. nach einem WLAN-Einbruch) einfach die
# Foto-Galerie oeffnen kann - schuetzt NICHT gegen jemanden mit direktem
# Zugriff auf die SD-Karte (dafuer ist die separate Archiv-Verschluesselung
# gedacht). Bewusst UNABHAENGIG von AUTH_USER/AUTH_PASS oben (das ist ein
# optionaler, nur per Hand ueber systemd-Environment aktivierbarer
# HTTP-Basic-Auth-Schalter fuer technisch versierte Nutzer - dieses
# In-App-Passwort ist die eigentliche, fuer alle gedachte Absicherung).
ZUGANG_PATH = os.path.join(EINSTELLUNGEN_DIR, ".zugang.json")
ZUGANG_COOKIE_NAME = "honigbox_zugang"
ZUGANG_COOKIE_MAX_AGE = 10 * 365 * 86400  # ~10 Jahre - "nie wieder fragen"
ZUGANG_PBKDF2_ITERATIONEN = 200_000
ZUGANG_MINDESTLAENGE = 4
# Fuer die Testsuite (siehe tests/conftest.py) - ohne das wuerde JEDE
# Testanfrage auf die Ersteinrichtungs-Seite umgeleitet, da in den isolierten
# Test-Verzeichnissen nie ein Zugangs-Passwort gesetzt ist.
ZUGANG_DEAKTIVIERT = os.environ.get("GALERIE_ZUGANG_AUS", "") == "1"


def zugang_eingerichtet():
    return os.path.isfile(ZUGANG_PATH)


def _lade_zugang():
    try:
        with open(ZUGANG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def setze_zugang_passwort(passwort):
    """Setzt ein (neues) Passwort - fuer die Ersteinrichtung genauso wie fuer
    einen spaeteren Reset. Erzeugt dabei immer ein neues server_secret,
    wodurch alle bisher ausgestellten Anmelde-Cookies (auch in ANDEREN
    Browsern) automatisch ungueltig werden - ein Reset meldet also ueberall
    ab, nicht nur im aktuellen Browser."""
    passwort = str(passwort or "")
    if len(passwort) < ZUGANG_MINDESTLAENGE:
        raise ValueError(f"Passwort muss mindestens {ZUGANG_MINDESTLAENGE} Zeichen haben")
    salt = secrets.token_bytes(16)
    hash_ = hashlib.pbkdf2_hmac("sha256", passwort.encode(), salt, ZUGANG_PBKDF2_ITERATIONEN)
    daten = {"hash": hash_.hex(), "salt": salt.hex(), "server_secret": secrets.token_hex(32)}
    with open(ZUGANG_PATH, "w") as f:
        json.dump(daten, f)
    try:
        os.chmod(ZUGANG_PATH, 0o600)
    except OSError:
        pass
    return daten


def pruefe_zugang_passwort(passwort):
    """hmac.compare_digest statt == , damit die Vergleichsdauer selbst kein
    Seitenkanal ist, der Rueckschluesse auf einzelne richtige Zeichen erlaubt."""
    daten = _lade_zugang()
    if not daten:
        return False
    try:
        salt = bytes.fromhex(daten["salt"])
        erwartet = bytes.fromhex(daten["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    hash_ = hashlib.pbkdf2_hmac("sha256", str(passwort or "").encode(), salt, ZUGANG_PBKDF2_ITERATIONEN)
    return hmac.compare_digest(hash_, erwartet)


def _zugang_cookie_sollwert():
    daten = _lade_zugang()
    if not daten:
        return None
    try:
        secret = bytes.fromhex(daten["server_secret"])
    except (KeyError, ValueError, TypeError):
        return None
    return hmac.new(secret, b"honigbox-zugang-v1", hashlib.sha256).hexdigest()


def zugang_cookie_gueltig(cookie_header):
    sollwert = _zugang_cookie_sollwert()
    if not sollwert:
        return False
    try:
        cookies = http.cookies.SimpleCookie(cookie_header or "")
    except http.cookies.CookieError:
        return False
    morsel = cookies.get(ZUGANG_COOKIE_NAME)
    if not morsel:
        return False
    return hmac.compare_digest(morsel.value, sollwert)


# Passwort vergessen? Ein Reset ist bewusst nur kurz nach einem (Dienst- oder
# Geraete-)Neustart moeglich - das ist die einzige Huerde gegen jemanden mit
# einem gestohlenen Session-Cookie: ohne das aktuelle Passwort kann er den
# Server nicht per Web-UI neu starten (siehe geplante Neustart-Absicherung),
# braucht also physischen oder SSH-Zugriff, um dieses Zeitfenster ueberhaupt
# erst zu oeffnen. _ZUGANG_PROZESS_START wird beim Modul-Import gesetzt, also
# bei jedem Dienststart (und damit auch bei jedem Geraete-Neustart) neu.
ZUGANG_RESET_FENSTER_SEKUNDEN = 10 * 60
_ZUGANG_PROZESS_START = time.time()


def zugang_reset_moeglich():
    return zugang_eingerichtet() and (time.time() - _ZUGANG_PROZESS_START) < ZUGANG_RESET_FENSTER_SEKUNDEN


_ZUGANG_SEITE_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1e1a14">
<meta name="color-scheme" content="light dark">
<script>try{var t=localStorage.getItem('honigbox-theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>
<title>BeeTown HonigBox</title>
<link rel="stylesheet" href="/styles.css">
</head>
<body>
<div class="zugang-wrap">
  <div class="zugang-karte">
    <h1>\U0001F36F BeeTown HonigBox</h1>
    <p class="muted">__INTRO__</p>
    __FEHLER__
    __FORMULAR__
  </div>
</div>
</body>
</html>
"""


def _zugang_seite(intro, formular_html, fehler=None):
    fehler_html = f'<p class="warnhinweis">{html.escape(fehler)}</p>' if fehler else ""
    return (_ZUGANG_SEITE_TEMPLATE
            .replace("__INTRO__", html.escape(intro))
            .replace("__FEHLER__", fehler_html)
            .replace("__FORMULAR__", formular_html))


def _seite_einrichten(fehler=None):
    formular = """<form method="POST" action="/einrichten" class="zugang-form">
      <input type="password" name="passwort" placeholder="Neues Passwort" minlength="4" required autofocus>
      <input type="password" name="passwort2" placeholder="Passwort wiederholen" minlength="4" required>
      <button type="submit" class="btn btn-primary">Passwort festlegen</button>
    </form>
    <p class="muted zugang-hinweis">Wird nur EINMAL abgefragt - danach merkt sich dieser Browser den
    Zugang dauerhaft. Lässt sich später in den Einstellungen zurücksetzen.</p>"""
    return _zugang_seite(
        "Willkommen! Bitte lege ein Passwort für den Zugriff auf diese Seite fest.", formular, fehler)


def _seite_login(fehler=None):
    formular = """<form method="POST" action="/login" class="zugang-form">
      <input type="password" name="passwort" placeholder="Passwort" required autofocus>
      <button type="submit" class="btn btn-primary">Anmelden</button>
    </form>"""
    if zugang_reset_moeglich():
        formular += ('<p class="muted zugang-hinweis">Passwort vergessen? '
                     '<a href="/zuruecksetzen">Jetzt zurücksetzen</a> '
                     '(löscht dabei ALLE Fotos).</p>')
    else:
        formular += ('<p class="muted zugang-hinweis">Passwort vergessen? Gerät neu starten - '
                     'direkt danach erscheint hier für 10 Minuten ein Link zum Zurücksetzen '
                     '(löscht dabei ALLE Fotos, aktuelle wie Archiv).</p>')
    return _zugang_seite("Bitte Passwort eingeben.", formular, fehler)


def _seite_zuruecksetzen(fehler=None):
    formular = """<form method="POST" action="/zuruecksetzen" class="zugang-form">
      <input type="password" name="passwort" placeholder="Neues Passwort" minlength="4" required autofocus>
      <input type="password" name="passwort2" placeholder="Passwort wiederholen" minlength="4" required>
      <button type="submit" class="btn btn-danger">Passwort zurücksetzen &amp; alle Fotos löschen</button>
    </form>
    <p class="muted zugang-hinweis"><a href="/login">Abbrechen</a></p>"""
    return _zugang_seite(
        "Achtung: Beim Zurücksetzen werden ALLE gespeicherten Fotos "
        "(aktuelle und Archiv) unwiderruflich gelöscht!",
        formular, fehler)


_ARCHIV_CONTAINER_LABEL = {"archiv": "Foto-Archiv", "bilder": "Aktuelle Fotos (Platte-Modus)"}


def _seite_archiv_schluessel(fehler=None):
    zustand = _archiv_schluessel_status()
    hinweise = []
    wartet = False
    verarbeitung = False
    bundle = {}

    for name, label in _ARCHIV_CONTAINER_LABEL.items():
        status = zustand[name]["status"]
        if status is None:
            continue
        if status == "locked":
            wartet = True
            versuch_falsch_pfad = os.path.join(ARCHIV_RUN_DIR, f"{name}-letzter-versuch-falsch")
            if os.path.isfile(versuch_falsch_pfad):
                hinweise.append(f"<p class=\"warnhinweis\">{html.escape(label)}: falscher Schlüssel - "
                                 f"bitte erneut versuchen.</p>")
            else:
                hinweise.append(f"<p>{html.escape(label)}: wartet auf Schlüssel-Eingabe.</p>")
        elif status == "verarbeitung":
            verarbeitung = True
        elif status == "fresh" and zustand[name]["schluessel"]:
            bundle[name] = zustand[name]["schluessel"]
        elif status == "unlocked":
            hinweise.append(f"<p>{html.escape(label)}: mit gesichertem Schlüssel wiederhergestellt.</p>")
        elif status == "fresh":
            hinweise.append(f"<p>{html.escape(label)}: Container aktiv (Schlüssel bereits bestätigt).</p>")
        elif status == "fehler":
            hinweise.append(f"<p>{html.escape(label)}: Fehler beim Einrichten - bitte Gerät neu starten oder Logs prüfen.</p>")

    formular = "".join(hinweise)

    if verarbeitung and not wartet:
        formular += '<p>...bitte warten...</p>'

    if wartet:
        formular += """
        <form method="POST" action="/archiv-schluessel" class="zugang-form">
          <input type="hidden" name="aktion" value="eingabe">
          <textarea name="schluessel" rows="4" placeholder="Hier den ggf. vorhandenen Schlüssel für archivierte Fotos einfügen" required></textarea>
          <button type="submit" class="btn btn-primary">Wiederherstellen</button>
        </form>
        <form method="POST" action="/archiv-schluessel" class="zugang-form">
          <input type="hidden" name="aktion" value="verwerfen">
          <button type="submit" class="btn btn-dunkel">Keine Wiederherstellung</button>
        </form>
        <p class="muted zugang-hinweis">Alte vorhandene Archiv-Fotos werden gelöscht.</p>
        """
    if wartet or verarbeitung:
        # Automatisch aktualisieren, SOLANGE der Nutzer das Eingabefeld (falls
        # vorhanden) nicht gerade benutzt - sonst wuerde ein Reload mitten im
        # Einfuegen/Tippen den Inhalt loeschen (genau das war der urspruengliche
        # Bug: ein stures <meta refresh> hat das Feld nach wenigen Sekunden
        # immer wieder geleert).
        formular += """
        <script>
        (function(){
          var feld = document.querySelector('textarea[name="schluessel"]');
          var beruehrt = false;
          if (feld) feld.addEventListener('input', function(){ beruehrt = true; });
          setInterval(function(){
            if (!beruehrt && document.activeElement !== feld) location.reload();
          }, 4000);
        })();
        </script>
        """
    if bundle:
        bundle_b64 = base64.b64encode(json.dumps(bundle).encode()).decode()
        formular += f"""
        <p class="warnhinweis">Achtung: Die Fotos im Archiv werden verschlüsselt.</p>
        <p class="muted">Diesen Schlüssel sollten Sie nun auf einem externen PC sichern. Sie
        benötigen ihn, um nach einem Neustart oder Stromausfall auf die archivierten Fotos
        zugreifen zu können.</p>
        <p class="muted">Ist Ihnen das nicht wichtig, können Sie dies auch übergehen...</p>
        <a class="btn btn-primary" id="archiv-schluessel-download" download="honigbox-schluessel.json"
           href="data:application/json;base64,{bundle_b64}">Schlüssel herunterladen</a>
        <form method="POST" action="/archiv-schluessel" class="zugang-form">
          <input type="hidden" name="aktion" value="bestaetigt">
          <button type="submit" id="archiv-schluessel-weiter" class="btn btn-ghost">Weiter ohne Sicherung</button>
        </form>
        <script>
        (function(){{
          var link = document.getElementById('archiv-schluessel-download');
          var btn = document.getElementById('archiv-schluessel-weiter');
          if (link && btn) link.addEventListener('click', function(){{ btn.textContent = 'Weiter'; }});
        }})();
        </script>
        """

    return _zugang_seite("Foto Archiv", formular, fehler)


def _migriere_alte_einstellungsdatei(alter_pfad, neuer_pfad):
    """Fruehere HonigBox-Versionen legten alle Einstellungsdateien in
    BILDER_DIR ab - seit es die RAM-Disk-Option gibt, muessen sie in
    EINSTELLUNGEN_DIR liegen (siehe oben), sonst wuerden bestehende
    Einstellungen beim ersten Neustart mit aktiver RAM-Disk verschwinden.
    Kopiert nur einmalig, wenn am neuen Ort noch nichts liegt."""
    if os.path.isfile(neuer_pfad) or not os.path.isfile(alter_pfad):
        return
    try:
        shutil.copyfile(alter_pfad, neuer_pfad)
    except OSError:
        pass


AUFRAEUM_INTERVALL_SEK = 900  # 15 Minuten (2026-08-14 von 1 Std. verkuerzt, da Loeschfrist jetzt auch in Stunden einstellbar ist)
SPEICHER_WACHE_INTERVALL_SEK = 30

KAMERA_EINSTELLUNGEN_PATH = os.environ.get(
    "GALERIE_KAMERA_EINSTELLUNGEN", os.path.join(EINSTELLUNGEN_DIR, ".kamera-einstellungen.json")
)
_migriere_alte_einstellungsdatei(
    os.path.join(BILDER_DIR, ".kamera-einstellungen.json"), KAMERA_EINSTELLUNGEN_PATH)
KAMERA_SHELL_CONF_PATH = os.environ.get(
    "GALERIE_KAMERA_SHELL_CONF", os.path.join(EINSTELLUNGEN_DIR, ".kamera-einstellungen.sh")
)

# Single Source of Truth fuer Feldnamen/Grenzwerte - foto.sh liest die daraus
# generierte .kamera-einstellungen.sh, das Frontend rendert das Formular anhand
# von KAMERA_FELDER (via GET /api/kamera), Validierung passiert unten serverseitig.
KAMERA_FELDER = [
    {"key": "metering", "typ": "select", "label": "Belichtungsmessung", "optionen": [
        ["centre", "Mitte (Standard)"], ["spot", "Spot (nur Bildmitte)"], ["average", "Durchschnitt (ganzes Bild)"]]},
    {"key": "ev", "typ": "zahl", "label": "Belichtungskorrektur (EV)", "min": -2, "max": 2, "step": 0.1},
    {"key": "belichtungsmodus", "typ": "select", "label": "Belichtungsmodus", "optionen": [
        ["normal", "Normal"], ["sport", "Sport (kurze Belichtung)"], ["long", "Lang"], ["short", "Kurz"]]},
    {"key": "verschlusszeit", "typ": "zahl", "label": "Verschlusszeit in Mikrosekunden (0 = automatisch)",
     "min": 0, "max": 10000000, "step": 1000},
    {"key": "gain", "typ": "zahl", "label": "Sensor-Gain / ISO (0 = automatisch)", "min": 0, "max": 16, "step": 0.1},
    {"key": "helligkeit", "typ": "zahl", "label": "Helligkeit", "min": -1, "max": 1, "step": 0.1},
    {"key": "kontrast", "typ": "zahl", "label": "Kontrast", "min": 0, "max": 2, "step": 0.1},
    {"key": "saettigung", "typ": "zahl", "label": "Sättigung", "min": 0, "max": 2, "step": 0.1},
    {"key": "schaerfe", "typ": "zahl", "label": "Schärfe", "min": 0, "max": 4, "step": 0.1},
    {"key": "weissabgleich", "typ": "select", "label": "Weißabgleich", "optionen": [
        ["auto", "Automatisch"], ["incandescent", "Glühlampe"], ["tungsten", "Halogen"],
        ["fluorescent", "Leuchtstoff"], ["indoor", "Innenraum"], ["daylight", "Tageslicht"], ["cloudy", "Bewölkt"]]},
    {"key": "rauschunterdrueckung", "typ": "select", "label": "Rauschunterdrückung", "optionen": [
        ["off", "Aus"], ["cdn_off", "Aus (CDN)"], ["cdn_fast", "Schnell"], ["cdn_hq", "Hohe Qualität"]]},
    {"key": "fokus_modus", "typ": "select", "label": "Fokus", "optionen": [
        ["auto", "Automatisch (bei jedem Bild neu, ca. 1-2 Sek. langsamer)"],
        ["fest", "Fest eingestellt (schneller, einmal kalibrieren)"]]},
    {"key": "fokus_position", "typ": "zahl",
     "label": "Fokus-Entfernung bei 'Fest eingestellt' (höher = näher, 0 = unendlich fern)",
     "min": 0, "max": 10, "step": 0.1},
    {"key": "aufnahme_verzoegerung_ms", "typ": "zahl",
     "label": "Aufnahme-Verzögerung in ms (niedriger = schneller, aber Belichtung/Weißabgleich weniger eingeschwungen)",
     "min": 200, "max": 3000, "step": 100},
    {"key": "breite", "typ": "zahl", "label": "Breite (Pixel)", "min": 320, "max": 4608, "step": 1},
    {"key": "hoehe", "typ": "zahl", "label": "Höhe (Pixel)", "min": 240, "max": 2592, "step": 1},
    {"key": "jpeg_qualitaet", "typ": "zahl", "label": "JPEG-Qualität", "min": 1, "max": 100, "step": 1},
    {"key": "rotation", "typ": "select", "label": "Rotation", "optionen": [["0", "0°"], ["180", "180°"]]},
    {"key": "horizontal_spiegeln", "typ": "checkbox", "label": "Horizontal spiegeln"},
    {"key": "vertikal_spiegeln", "typ": "checkbox", "label": "Vertikal spiegeln"},
    {"key": "zoom", "typ": "zahl", "label": "Digitaler Zoom", "min": 1, "max": 4, "step": 0.1},
]

KAMERA_STANDARD = {
    "metering": "centre", "ev": 0, "belichtungsmodus": "sport", "verschlusszeit": 0, "gain": 0,
    "helligkeit": 0, "kontrast": 1, "saettigung": 1, "schaerfe": 1, "weissabgleich": "auto",
    "rauschunterdrueckung": "cdn_fast", "fokus_modus": "fest", "fokus_position": 4.0,
    "aufnahme_verzoegerung_ms": 1000,
    "breite": 2304, "hoehe": 1296, "jpeg_qualitaet": 90,
    "rotation": "0", "horizontal_spiegeln": False, "vertikal_spiegeln": False, "zoom": 1.0,
}

FOTO_ZEITPLAN_PATH = os.environ.get(
    "GALERIE_FOTO_ZEITPLAN", os.path.join(EINSTELLUNGEN_DIR, ".foto-zeitplan.json")
)
_migriere_alte_einstellungsdatei(os.path.join(BILDER_DIR, ".foto-zeitplan.json"), FOTO_ZEITPLAN_PATH)


def _migriere_foto_zeitplan_felder():
    """Umbenennung 2026-08-14 (zwei-stufiger -> drei-stufiger Zeitplan):
    intervall_1/schwelle_sekunden/intervall_2 -> phase1_intervall_sekunden/
    phase1_dauer_sekunden/intervall_danach_sekunden. Uebernimmt bestehende
    Werte unter den neuen Namen - ohne das wuerde der alte 'intervall_2'
    (das bisherige Intervall NACH der Schwelle) sonst unter dem neuen Feld
    gleichen Namens (jetzt: Intervall waehrend PHASE 2) landen und dort
    faelschlich weiterverwendet."""
    if not os.path.isfile(FOTO_ZEITPLAN_PATH):
        return
    try:
        with open(FOTO_ZEITPLAN_PATH) as f:
            daten = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    umbenennungen = {
        "intervall_1": "phase1_intervall_sekunden",
        "schwelle_sekunden": "phase1_dauer_sekunden",
        "intervall_2": "intervall_danach_sekunden",
    }
    geaendert = False
    for alt, neu in umbenennungen.items():
        if alt not in daten:
            continue
        if neu not in daten:
            daten[neu] = daten[alt]
        del daten[alt]
        geaendert = True
    if geaendert:
        try:
            with open(FOTO_ZEITPLAN_PATH, "w") as f:
                json.dump(daten, f)
        except OSError:
            pass


_migriere_foto_zeitplan_felder()

GRUPPE_AUFNAHME = "Aufnahme-Zeitplan (während die Tür offen ist)"
GRUPPE_AUFRAEUMEN = "Aufräumen"
# Feinere Untergruppen INNERHALB des Aufnahme-Zeitplans (eigene Zwischen-
# ueberschrift je Phase, siehe .kamera-feld-gruppe in styles.css - die ist
# bewusst grid-column:1/-1, erzwingt also automatisch einen Zeilenumbruch vor
# jeder Phase). Der uebergreifende Kontext-Satz steht stattdessen als
# statischer Text ueber dem Feld-Container in index.html.
GRUPPE_PHASE1 = "Phase 1"
GRUPPE_PHASE2 = "Phase 2"
GRUPPE_DANACH = "Danach"
GRUPPE_DUNKLE_FOTOS = "Dunkle Fotos"

# Deckt die gesamte "📷 Fotos"-Seite ab: Aufnahme-Zeitplan waehrend einer
# Tueroeffnung + alles, was Fotos wieder loescht (Aufbewahrungsdauer, zu
# dunkle Fotos) - fruehere Versionen hatten das ueber drei verschiedene Orte
# verteilt (Kamera-Einstellungen, ein eigenes "Aufbewahrung"-Feld, hier),
# jetzt an einem Ort. "gruppe" steuert nur die Unterueberschrift im Frontend.
# Drei-stufiger Zeitplan (Phase 1 -> Phase 2 -> danach), siehe
# _migriere_foto_zeitplan_felder() fuer die Umbenennung vom frueheren
# zwei-stufigen Schema.
FOTO_ZEITPLAN_FELDER = [
    {"key": "phase1_dauer_sekunden", "typ": "zahl", "label": "Dauer (Sekunden)",
     "min": 1, "max": 3600, "step": 1, "gruppe": GRUPPE_PHASE1},
    {"key": "phase1_intervall_sekunden", "typ": "zahl", "label": "↳ Foto-Intervall dabei (Sekunden)",
     "min": 1, "max": 600, "step": 1, "gruppe": GRUPPE_PHASE1},
    {"key": "phase2_dauer_sekunden", "typ": "zahl", "label": "weitere Dauer (Sekunden)",
     "min": 1, "max": 3600, "step": 1, "gruppe": GRUPPE_PHASE2},
    {"key": "phase2_intervall_sekunden", "typ": "zahl", "label": "↳ Foto-Intervall dabei (Sekunden)",
     "min": 1, "max": 600, "step": 1, "gruppe": GRUPPE_PHASE2},
    {"key": "intervall_danach_sekunden", "typ": "zahl", "label": "Foto-Intervall (Sekunden)",
     "min": 1, "max": 3600, "step": 1, "gruppe": GRUPPE_DANACH},
    {"key": "max_anzahl", "typ": "zahl", "label": "Maximale Anzahl Fotos pro Türöffnung", "min": 1, "max": 500, "step": 1,
     "gruppe": GRUPPE_DANACH},
    {"key": "aufbewahrungsstunden", "typ": "zahl", "label": "Fotos automatisch löschen nach: Stunden",
     "min": 0, "max": 23, "step": 1, "gruppe": GRUPPE_AUFRAEUMEN},
    {"key": "aufbewahrungstage", "typ": "zahl", "label": "... + zusätzlich Tage dazu (beides 0 = nie löschen)",
     "min": 0, "max": 3650, "step": 1, "gruppe": GRUPPE_AUFRAEUMEN},
    {"key": "dunkle_fotos_loeschen", "typ": "checkbox",
     "label": "Zu dunkle Fotos automatisch löschen (z. B. Tür noch fast zu)", "gruppe": GRUPPE_DUNKLE_FOTOS},
    {"key": "helligkeitsschwelle", "typ": "zahl",
     "label": "Mindesthelligkeit zum Behalten (0-255, höher = strenger)", "min": 0, "max": 255,
     "step": 1, "gruppe": GRUPPE_DUNKLE_FOTOS},
]
FOTO_ZEITPLAN_STANDARD = {
    "phase1_dauer_sekunden": 60, "phase1_intervall_sekunden": 3,
    "phase2_dauer_sekunden": 60, "phase2_intervall_sekunden": 8,
    "intervall_danach_sekunden": 15, "max_anzahl": 30,
    "aufbewahrungstage": 30, "aufbewahrungsstunden": 0, "dunkle_fotos_loeschen": True, "helligkeitsschwelle": 28,
}


def _migriere_altes_aufbewahrungstage():
    """Vor der Fotos-Seiten-Zusammenlegung lag 'aufbewahrungstage' in einer
    eigenen Datei (.galerie-einstellungen.json) - falls die neue
    .foto-zeitplan.json den Wert noch nicht explizit kennt, hier einmalig
    aus der alten Datei uebernehmen, egal ob die noch im alten (BILDER_DIR)
    oder schon im neuen (EINSTELLUNGEN_DIR) Ort liegt."""
    if os.path.isfile(FOTO_ZEITPLAN_PATH):
        try:
            with open(FOTO_ZEITPLAN_PATH) as f:
                if "aufbewahrungstage" in json.load(f):
                    return
        except (OSError, json.JSONDecodeError):
            pass
    for alter_pfad in (os.path.join(EINSTELLUNGEN_DIR, ".galerie-einstellungen.json"),
                       os.path.join(BILDER_DIR, ".galerie-einstellungen.json")):
        if not os.path.isfile(alter_pfad):
            continue
        try:
            with open(alter_pfad) as f:
                tage = json.load(f).get("aufbewahrungstage")
            if tage is None:
                continue
            aktuell = dict(FOTO_ZEITPLAN_STANDARD)
            if os.path.isfile(FOTO_ZEITPLAN_PATH):
                with open(FOTO_ZEITPLAN_PATH) as f:
                    aktuell.update(json.load(f))
            aktuell["aufbewahrungstage"] = tage
            with open(FOTO_ZEITPLAN_PATH, "w") as f:
                json.dump(aktuell, f)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return


_migriere_altes_aufbewahrungstage()

PUSHOVER_EINSTELLUNGEN_PATH = os.environ.get(
    "GALERIE_PUSHOVER_EINSTELLUNGEN", os.path.join(EINSTELLUNGEN_DIR, ".pushover-einstellungen.json")
)
_migriere_alte_einstellungsdatei(
    os.path.join(BILDER_DIR, ".pushover-einstellungen.json"), PUSHOVER_EINSTELLUNGEN_PATH)
PUSHOVER_SHELL_CONF_PATH = os.environ.get(
    "GALERIE_PUSHOVER_SHELL_CONF", os.path.join(EINSTELLUNGEN_DIR, ".pushover-einstellungen.sh")
)
ALTE_PUSHOVER_CONF_PATH = os.path.join(BASE, "pushover.conf")

# Telegram ist ein zweiter, unabhaengiger Benachrichtigungskanal neben
# Pushover (beide gleichzeitig aktivierbar) - nutzt bewusst DIESELBEN
# Meldungstexte aus PUSHOVER_MELDUNGEN_SCHEMA (siehe unten), um das nicht zu
# duplizieren. Der An/Aus-Schalter PRO MELDUNG ist dagegen bewusst getrennt
# (eigenes "meldungen"-Feld hier, siehe lade_telegram_einstellungen()) - so
# kann z.B. "Pi neu gestartet" nur ueber Telegram laufen und der Rest nur
# ueber Pushover. Eigener Bot-Token pro Installation
# (kein fest eingebauter, gemeinsamer Token) - vermeidet, dass mehrere
# Installationen sich beim Telegram-getUpdates-Polling gegenseitig die
# Nachrichten "wegschnappen" (siehe Diskussion 2026-08-10).
TELEGRAM_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".telegram-einstellungen.json")
TELEGRAM_SHELL_CONF_PATH = os.path.join(EINSTELLUNGEN_DIR, ".telegram-einstellungen.sh")
TELEGRAM_CHATS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".telegram-chats.json")
TELEGRAM_PENDING_PATH = os.path.join(EINSTELLUNGEN_DIR, ".telegram-pending-codes.json")
TELEGRAM_OFFSET_PATH = os.path.join(EINSTELLUNGEN_DIR, ".telegram-update-offset")
TELEGRAM_STANDARD = {"bot_token": "", "bot_username": "", "aktiv": False}
TELEGRAM_CODE_GUELTIG_SEK = 600  # 10 Minuten Zeitfenster fuer den Verbinden-Link

# STATUS_PATH/TUER_SIMULATION_PATH/TUER_NEUSTART_SIGNAL_PATH sind reine
# Signaldateien fuer die Verstaendigung mit honigbox.sh (siehe dort) - deren
# Pfade muessen dort identisch definiert sein. Kein Migrations-Bedarf (rein
# ephemer, ein fehlender/veralteter Stand beim Umzug ist harmlos).
STATUS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".status.json")
TUER_SIMULATION_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-simulation-bis.json")
TUER_NEUSTART_SIGNAL_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-neustart-signal")
# Von honigbox.sh gelesen (Pfad muss dort identisch hartcodiert sein) - siehe
# door_is_open() dort. Steuert, ob der GPIO17-Kontakt normal oder invertiert
# ausgewertet wird, falls der Schalter versehentlich als Oeffner statt
# Schliesser angeschlossen wurde.
TUER_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-einstellungen.json")
TUER_EINSTELLUNGEN_STANDARD = {"kontakt_invertiert": False}

GALERIE_ANZEIGE_PATH = os.path.join(EINSTELLUNGEN_DIR, ".galerie-anzeige.json")
GALERIE_ANZEIGE_MODI = ("einzelbild", "feed")
GALERIE_ANZEIGE_STANDARD = {"modus": "feed"}

# Optionaler Button bei den Aufnahmen, der zu einer frei einstellbaren externen
# Seite verlinkt (z.B. zur "Erfassen"-Seite der BeeTown-Imkerei-App) - oeffnet
# in einem neuen Tab, damit die HonigBox-Seite nicht verloren geht.
EXTERN_LINK_PATH = os.path.join(EINSTELLUNGEN_DIR, ".extern-link.json")
EXTERN_LINK_STANDARD = {"aktiv": False, "url": "", "label": "🐝 Verkauf erfassen"}

# Welche der drei Stumm-/Pause-Buttons auf der Startseite ueberhaupt angezeigt
# werden (unabhaengig davon, ob sie gerade aktiv sind) - Standard: nur der
# kombinierte Button, die beiden einzelnen sind eher fuer Spezialfaelle.
START_BUTTONS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".start-buttons-sichtbar.json")
START_BUTTONS_STANDARD = {"messenger": False, "fotos": False, "messenger_fotos": True}

SPEICHER_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".speicher-einstellungen.json")
SIMULATION_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".simulation-einstellungen.json")
SIMULATION_DAUER_STANDARD_SEK = 120
SIMULATION_DAUER_MAX_SEK = 1800

PUSHOVER_STUMM_PATH = os.path.join(EINSTELLUNGEN_DIR, ".pushover-stumm-bis.json")
# Eigene Datei fuer "Fotos aus"/"Messenger + Fotos aus" (siehe fotos_pause_rest_sekunden()
# weiter unten) - bewusst getrennt von PUSHOVER_STUMM_PATH, damit sich Fotos und
# Messenger unabhaengig voneinander pausieren lassen (drei Buttons auf der Startseite).
FOTOS_PAUSE_PATH = os.path.join(EINSTELLUNGEN_DIR, ".fotos-pause-bis.json")
PUSHOVER_STUMM_DAUER_OPTIONEN_MIN = [3, 5, 10, 20, 30]
PUSHOVER_STUMM_DAUER_STANDARD_MIN = 5

# Foto-Testmodus: Einzelfoto-Button liefert waehrend dieser Zeit zusaetzlich
# die von der Kamera TATSAECHLICH angewandten Werte (v.a. bei "Automatisch"-
# Einstellungen wie Weissabgleich/Fokus/Belichtung interessant) - per
# --metadata an rpicam-still, siehe foto.sh. Zeitbegrenzt statt Dauerschalter,
# damit man nicht vergisst, es wieder auszustellen.
FOTO_TESTMODUS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".foto-testmodus-bis.json")
FOTO_TESTMODUS_DAUER_MIN = 30
FOTO_TESTMODUS_METADATA_PATH = os.path.join(EINSTELLUNGEN_DIR, ".foto-testmodus-metadata.json")

# Privilegiertes Script fuer den RAM/Platte-Wechsel (siehe speicher_status()/
# speichere_speicher_einstellungen() weiter unten) - laeuft als root ueber
# eine gezielte sudoers-Freigabe, siehe install.sh.
SPEICHER_UMSCHALT_SCRIPT = "/opt/honigbox/speicher_umschalten.sh"
SPEICHER_GROESSE_MIN_MB = 32
SPEICHER_GROESSE_MAX_MB = 2048
SPEICHER_GROESSE_EMPFEHLUNG_ANTEIL = 0.20  # Anteil des Gesamt-RAM
DURCHSCHNITT_FOTO_BYTES_STANDARD = 300 * 1024  # Schaetzwert, falls noch keine Fotos vorhanden sind

PUSHOVER_MELDUNGEN_SCHEMA = [
    {"id": "boot", "label": "Pi wurde neu gestartet"},
    {"id": "geoeffnet", "label": "Tür wurde geöffnet"},
    {"id": "eskalation1", "label": "Tür seit ca. 4 Minuten offen"},
    {"id": "eskalation2", "label": "Tür seit ca. 34 Minuten offen"},
    {"id": "geschlossen", "label": "Tür wurde wieder geschlossen"},
]
PUSHOVER_STANDARD = {
    "token": "", "user": "", "aktiv": False,
    "meldungen": {
        "boot": {"aktiv": True, "text": "Raspi wurde gestartet!"},
        "geoeffnet": {"aktiv": True, "text": "HONIGBOX wurde geöffnet!"},
        "eskalation1": {"aktiv": True, "text":
            "HONIGBOX Tür steht seit ca. 4 Minuten offen! Warte weitere 30 Min bis zur nächsten Prüfung..."},
        "eskalation2": {"aktiv": True, "text": "HONIGBOX Tür steht seit ca. 34 Minuten offen!"},
        "geschlossen": {"aktiv": True, "text": "HonigBox wurde geschlossen!!"},
    },
}

os.makedirs(BILDER_DIR, exist_ok=True)
# ARCHIV_DIR wird NICHT mehr hier unbedingt angelegt (siehe archiv_bereit()
# weiter unten, Phase C): ist die LUKS-Archiv-Verschluesselung aktiv, kann
# ARCHIV_DIR ein noch nicht eingehaengter Mountpoint sein - ein bedingungsloses
# os.makedirs() wuerde sonst versehentlich einen leeren Platzhalter-Ordner
# direkt auf der unverschluesselten Root-Partition anlegen. Ohne Phase C
# (keine Status-Datei vorhanden) verhaelt sich archiv_bereit() weiterhin
# genau wie dieser alte, unbedingte Aufruf.

ERLAUBTE_ENDUNGEN = {".jpg", ".jpeg", ".png"}
DATEINAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


def sichere_dateiname(name):
    """Gibt einen bereinigten Dateinamen zurueck oder None (verhindert Pfad-Traversal)."""
    name = os.path.basename(name)
    if not DATEINAME_RE.match(name) or ".." in name:
        return None
    if os.path.splitext(name)[1].lower() not in ERLAUBTE_ENDUNGEN:
        return None
    return name


def liste_bilder(verzeichnis):
    dateien = [d for d in os.listdir(verzeichnis) if sichere_dateiname(d)]
    dateien.sort(reverse=True)
    return dateien


# Bewusst IN ARCHIV_DIR (nicht EINSTELLUNGEN_DIR) abgelegt, damit eine Notiz
# wie "Diebstahl, 4€ fehlte" untrennbar am zugehoerigen Foto haengt (z.B. bei
# einer manuellen Sicherung des Archiv-Ordners). ARCHIV_DIR ist ausserdem nie
# eine RAM-Disk (siehe Speicher-Einstellungen weiter unten), geht also auch
# bei aktivem tmpfs fuer BILDER_DIR nie beim naechsten Neustart verloren -
# das automatische Backup selbst sichert seit Phase C/E nur noch
# "einstellungen/", nie mehr Fotos (siehe setup/honigbox-backup.sh).
ARCHIV_NOTIZEN_DATEI = os.path.join(ARCHIV_DIR, ".archiv-notizen.json")
NOTIZ_MAX_LAENGE = 300

# Phase C: LUKS-Archiv-Verschluesselung. Ohne sie (keine Status-Datei
# vorhanden, z.B. in Tests oder auf noch nicht migrierten Installationen)
# verhaelt sich archiv_bereit() genau wie das frueher unbedingte
# os.makedirs(ARCHIV_DIR, exist_ok=True) - siehe Kommentar weiter oben.
ARCHIV_STATUS_PATH = os.environ.get("GALERIE_ARCHIV_STATUS", "/run/honigbox/archiv-status")
ARCHIV_RUN_DIR = os.environ.get("GALERIE_ARCHIV_RUN_DIR", "/run/honigbox")
ARCHIV_SCHLUESSEL_PATH = os.path.join(ARCHIV_RUN_DIR, "archiv-key")
BILDER_SCHLUESSEL_PATH = os.path.join(ARCHIV_RUN_DIR, "bilder-key")
BILDER_STATUS_PATH = os.environ.get("GALERIE_BILDER_STATUS", "/run/honigbox/bilder-status")


def archiv_bereit():
    """True, wenn ARCHIV_DIR gerade beschreib-/lesbar ist. Mit aktiver
    LUKS-Verschluesselung (siehe archiv_entschluesseln.sh/
    honigbox-archiv-entschluesseln.service) muss der Container zuerst
    geoeffnet oder frisch angelegt worden sein (Status "unlocked"/"fresh"),
    sonst waere ARCHIV_DIR noch ein leerer, nicht gemounteter Platzhalter."""
    try:
        with open(ARCHIV_STATUS_PATH) as f:
            status = f.read().strip()
    except OSError:
        os.makedirs(ARCHIV_DIR, exist_ok=True)
        return True
    return status in ("unlocked", "fresh")


def _lese_datei_falls_vorhanden(pfad):
    try:
        with open(pfad) as f:
            return f.read().strip()
    except OSError:
        return None


# Wie lange der frisch erzeugte Klartext-Schluessel nach dem Anlegen eines
# neuen Containers noch abrufbar bleibt, falls der Nutzer nicht aktiv
# bestaetigt, ihn gesichert zu haben - danach automatisch geloescht (siehe
# _archiv_schluessel_status()). Bewusst kein Dauerzugriff: der Schluessel
# soll nur EINMALIG beim Erzeugen abrufbar sein, nicht dauerhaft ueber die
# Laufzeit hinweg.
ARCHIV_SCHLUESSEL_ANZEIGE_MAX_SEK = 10 * 60

# Erzeugt immer per 'openssl rand -hex 32' (siehe archiv_entschluesseln.sh) -
# also IMMER genau 64 Zeichen, nur Kleinbuchstaben a-f/0-9. Damit lassen sich
# Verschreiber beim Kopieren (fehlende/zusaetzliche Zeichen, Gross-/
# Kleinschreibung) VOR dem Absenden erkennen, statt sie erst cryptsetup
# probieren zu lassen und bei "falsch" automatisch den Bestand zu verwerfen -
# ein simpler Copy-Paste-Fehler soll nicht denselben irreversiblen Effekt
# haben wie eine bewusste "Nein, frisch anfangen"-Entscheidung.
ARCHIV_SCHLUESSEL_RE = re.compile(r"^[0-9a-f]{64}$")


def _ist_gueltiger_archiv_schluessel(wert):
    return bool(ARCHIV_SCHLUESSEL_RE.match(wert))


def _archiv_schluessel_status():
    """Sammelt den aktuellen Zustand beider Container (Archiv, aktuelle
    Fotos im Platte-Modus) fuer die Anzeige auf /archiv-schluessel. Loescht
    dabei nebenbei einen abgelaufenen, unbestaetigten Klartext-Schluessel
    (siehe ARCHIV_SCHLUESSEL_ANZEIGE_MAX_SEK)."""
    ergebnis = {}
    for name, status_pfad, key_pfad in (
        ("archiv", ARCHIV_STATUS_PATH, ARCHIV_SCHLUESSEL_PATH),
        ("bilder", BILDER_STATUS_PATH, BILDER_SCHLUESSEL_PATH),
    ):
        status = _lese_datei_falls_vorhanden(status_pfad)
        schluessel = None
        if status == "fresh":
            try:
                if time.time() - os.path.getmtime(key_pfad) > ARCHIV_SCHLUESSEL_ANZEIGE_MAX_SEK:
                    os.remove(key_pfad)
                else:
                    schluessel = _lese_datei_falls_vorhanden(key_pfad)
            except OSError:
                pass
        ergebnis[name] = {"status": status, "schluessel": schluessel}
    return ergebnis


def _archiv_schluessel_erforderlich():
    """True, wenn die Web-Oberflaeche auf /archiv-schluessel umleiten muss,
    weil fuer mindestens einen Container noch eine Nutzer-Entscheidung
    ausstehend ist (Schluessel eingeben/verwerfen, oder einen frisch
    erzeugten Schluessel bestaetigen) oder gerade verarbeitet wird. Ohne
    Phase C (keine Status-Dateien vorhanden) bleibt das immer False."""
    for eintrag in _archiv_schluessel_status().values():
        if eintrag["status"] in ("locked", "verarbeitung"):
            return True
        if eintrag["status"] == "fresh" and eintrag["schluessel"]:
            return True
    return False


def _archiv_schluessel_seite_notwendig():
    """Etwas weiter gefasst als _archiv_schluessel_erforderlich(): entscheidet,
    ob GET /archiv-schluessel ueberhaupt etwas anzuzeigen hat, oder ob direkt
    auf '/' weitergeleitet werden kann - eine "alles erledigt"-Seite mit
    nur einem "Weiter"-Knopf waere sinnlos."""
    for eintrag in _archiv_schluessel_status().values():
        if eintrag["status"] in ("locked", "verarbeitung", "fehler"):
            return True
        if eintrag["status"] == "fresh" and eintrag["schluessel"]:
            return True
    return False


def _archiv_eingabe_schreiben(name, wert):
    try:
        with open(os.path.join(ARCHIV_RUN_DIR, f"{name}-eingabe"), "w") as f:
            f.write(wert)
    except OSError:
        pass


def lade_archiv_notizen():
    return _lade_einstellungen_datei(ARCHIV_NOTIZEN_DATEI, {})


def speichere_archiv_notiz(dateiname, text):
    """Legt eine Notiz zu einem Archiv-Foto an oder aendert sie; ein leerer
    Text loescht die Notiz wieder. Das Datum wird immer automatisch auf
    "heute" gesetzt (auch beim nachtraeglichen Bearbeiten) - der Nutzer soll
    ja gerade nachvollziehen koennen, wann der Hinweis eingetragen wurde."""
    text = (text or "").strip()[:NOTIZ_MAX_LAENGE]
    notizen = lade_archiv_notizen()
    if text:
        notizen[dateiname] = {"text": text, "datum": time.strftime("%Y-%m-%d")}
    else:
        notizen.pop(dateiname, None)
    with open(ARCHIV_NOTIZEN_DATEI, "w") as f:
        json.dump(notizen, f)
    return notizen.get(dateiname)


def archiv_notiz_entfernen(dateiname):
    """Aufraeumen, wenn ein Archiv-Foto geloescht wird - sonst bleibt die
    Notiz verwaist stehen (gleiches Muster wie thumbnail_entfernen)."""
    notizen = lade_archiv_notizen()
    if dateiname in notizen:
        del notizen[dateiname]
        with open(ARCHIV_NOTIZEN_DATEI, "w") as f:
            json.dump(notizen, f)


THUMB_ORDNER_NAME = ".thumbs"
THUMB_BREITE_PX = 400


def _thumb_pfad(verzeichnis, dateiname):
    return os.path.join(verzeichnis, THUMB_ORDNER_NAME, dateiname)


def thumbnail_erzeugen_falls_noetig(verzeichnis, dateiname):
    """Erzeugt (falls noch nicht vorhanden oder das Original neuer ist) ein
    verkleinertes Vorschaubild und gibt dessen Pfad zurueck. Schlaegt das
    fehl (z.B. Pillow fehlt, Bild kaputt), wird ersatzweise der Pfad des
    Originals zurueckgegeben - dann ist die Vorschau zwar nicht kleiner,
    aber die Galerie zeigt trotzdem etwas an, statt einen Fehler zu werfen."""
    original = os.path.join(verzeichnis, dateiname)
    thumb = _thumb_pfad(verzeichnis, dateiname)
    try:
        if os.path.isfile(thumb) and os.path.getmtime(thumb) >= os.path.getmtime(original):
            return thumb
    except OSError:
        pass
    try:
        from PIL import Image
        os.makedirs(os.path.dirname(thumb), exist_ok=True)
        with Image.open(original) as bild:
            bild = bild.convert("RGB")
            if bild.width > THUMB_BREITE_PX:
                neue_hoehe = max(1, round(bild.height * THUMB_BREITE_PX / bild.width))
                bild = bild.resize((THUMB_BREITE_PX, neue_hoehe), Image.LANCZOS)
            bild.save(thumb, "JPEG", quality=80)
        return thumb
    except Exception:
        return original


def thumbnail_entfernen(verzeichnis, dateiname):
    """Aufräumen, wenn das zugehoerige Originalfoto archiviert oder geloescht
    wird - sonst sammeln sich verwaiste Thumbnails in .thumbs/ an, die nie
    wieder gebraucht werden."""
    try:
        os.remove(_thumb_pfad(verzeichnis, dateiname))
    except OSError:
        pass


def zugang_alle_fotos_loeschen():
    """Loescht ALLE Fotos - aktuelle wie archivierte - inkl. Thumbnails und
    Archiv-Notizen. Bewusste Konsequenz eines Passwort-Resets (siehe
    Chat-Diskussion): wer zurueckgesetzt hat, hat sich dafuer Zugriff kurz
    nach einem Neustart verschafft und nimmt den Datenverlust in Kauf - ein
    Reset OHNE Datenverlust waere sonst der einfachste Weg, den
    Passwortschutz komplett zu umgehen."""
    for verzeichnis in (BILDER_DIR, ARCHIV_DIR):
        for dateiname in liste_bilder(verzeichnis):
            try:
                os.remove(os.path.join(verzeichnis, dateiname))
            except OSError:
                pass
        shutil.rmtree(os.path.join(verzeichnis, THUMB_ORDNER_NAME), ignore_errors=True)
    try:
        os.remove(ARCHIV_NOTIZEN_DATEI)
    except OSError:
        pass


def _lade_einstellungen_datei(pfad, standard):
    werte = dict(standard)
    if os.path.isfile(pfad):
        try:
            with open(pfad) as f:
                werte.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return werte


def _validiere_feld_wert(feld, roh, standard_wert):
    if feld["typ"] == "checkbox":
        return bool(roh)
    if feld["typ"] == "select":
        gueltige = {opt[0] for opt in feld["optionen"]}
        wert = str(roh)
        return wert if wert in gueltige else standard_wert
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        return standard_wert
    return max(feld["min"], min(feld["max"], wert))


def _speichere_feld_einstellungen(pfad, felder, standard, rohdaten):
    bereinigt = {}
    for feld in felder:
        key = feld["key"]
        default = standard[key]
        bereinigt[key] = _validiere_feld_wert(feld, rohdaten[key], default) if key in rohdaten else default
    with open(pfad, "w") as f:
        json.dump(bereinigt, f)
    return bereinigt


def _sh_quote(wert):
    return "'" + str(wert).replace("'", "'\\''") + "'"


def lade_kamera_einstellungen():
    return _lade_einstellungen_datei(KAMERA_EINSTELLUNGEN_PATH, KAMERA_STANDARD)


def _schreibe_kamera_shell_conf(werte):
    zeilen = [
        f"METERING={_sh_quote(werte['metering'])}",
        f"EV={_sh_quote(werte['ev'])}",
        f"BELICHTUNGSMODUS={_sh_quote(werte['belichtungsmodus'])}",
        f"VERSCHLUSSZEIT={_sh_quote(int(werte['verschlusszeit']))}",
        f"GAIN={_sh_quote(werte['gain'])}",
        f"HELLIGKEIT={_sh_quote(werte['helligkeit'])}",
        f"KONTRAST={_sh_quote(werte['kontrast'])}",
        f"SAETTIGUNG={_sh_quote(werte['saettigung'])}",
        f"SCHAERFE={_sh_quote(werte['schaerfe'])}",
        f"WEISSABGLEICH={_sh_quote(werte['weissabgleich'])}",
        f"RAUSCHUNTERDRUECKUNG={_sh_quote(werte['rauschunterdrueckung'])}",
        f"FOKUS_MODUS={_sh_quote(werte['fokus_modus'])}",
        f"FOKUS_POSITION={_sh_quote(werte['fokus_position'])}",
        f"AUFNAHME_VERZOEGERUNG_MS={_sh_quote(int(werte['aufnahme_verzoegerung_ms']))}",
        f"BREITE={_sh_quote(int(werte['breite']))}",
        f"HOEHE={_sh_quote(int(werte['hoehe']))}",
        f"JPEG_QUALITAET={_sh_quote(int(werte['jpeg_qualitaet']))}",
        f"ROTATION={_sh_quote(werte['rotation'])}",
        f"HFLIP={_sh_quote(1 if werte['horizontal_spiegeln'] else 0)}",
        f"VFLIP={_sh_quote(1 if werte['vertikal_spiegeln'] else 0)}",
        f"ZOOM={_sh_quote(werte['zoom'])}",
    ]
    # dunkle_fotos_loeschen/helligkeitsschwelle bewusst NICHT hier - die liest
    # honigbox.sh direkt aus KAMERA_EINSTELLUNGEN_PATH (JSON), da der
    # Aufraeum-Durchgang dort (nach dem Schliessen) laeuft, nicht in foto.sh.
    with open(KAMERA_SHELL_CONF_PATH, "w") as f:
        f.write("\n".join(zeilen) + "\n")


def einzelfoto_helligkeit_pruefen():
    """Nur fuer den Einzelfoto-Testbutton: prueft das gerade aufgenommene Bild
    sofort gegen die Helligkeits-Einstellung und loescht es bei Bedarf direkt -
    damit sich der Schwellenwert bequem durchprobieren laesst, ohne dafuer eine
    Tueroeffnung simulieren zu muessen. Gibt (geloescht, helligkeit) zurueck,
    helligkeit ist None wenn die Prüfung nicht moeglich war (z.B. Pillow fehlt) -
    dann wird NICHT geloescht (Fail-Open). Die automatische Aufraeumung
    waehrend/nach einer echten Tueroeffnung macht weiterhin honigbox.sh."""
    einstellungen = lade_foto_zeitplan()
    if not einstellungen.get("dunkle_fotos_loeschen"):
        return False, None
    schwelle = einstellungen.get("helligkeitsschwelle", 28)
    try:
        dateien = [f for f in os.listdir(BILDER_DIR) if sichere_dateiname(f)]
        if not dateien:
            return False, None
        neuste = max(dateien, key=lambda f: os.path.getmtime(os.path.join(BILDER_DIR, f)))
        pfad = os.path.join(BILDER_DIR, neuste)
        from PIL import Image, ImageStat
        helligkeit = ImageStat.Stat(Image.open(pfad).convert("L")).mean[0]
    except Exception:
        return False, None
    if helligkeit < schwelle:
        try:
            os.remove(pfad)
        except OSError:
            return False, helligkeit
        return True, helligkeit
    return False, helligkeit


def speichere_kamera_einstellungen(rohdaten):
    bereinigt = _speichere_feld_einstellungen(KAMERA_EINSTELLUNGEN_PATH, KAMERA_FELDER, KAMERA_STANDARD, rohdaten)
    _schreibe_kamera_shell_conf(bereinigt)
    return bereinigt


def lade_foto_zeitplan():
    return _lade_einstellungen_datei(FOTO_ZEITPLAN_PATH, FOTO_ZEITPLAN_STANDARD)


def speichere_foto_zeitplan(rohdaten):
    return _speichere_feld_einstellungen(FOTO_ZEITPLAN_PATH, FOTO_ZEITPLAN_FELDER, FOTO_ZEITPLAN_STANDARD, rohdaten)


def lade_simulation_dauer():
    return _lade_einstellungen_datei(
        SIMULATION_EINSTELLUNGEN_PATH, {"dauer_sekunden": SIMULATION_DAUER_STANDARD_SEK}
    )["dauer_sekunden"]


def speichere_simulation_dauer(minuten, sekunden):
    """Nimmt Minuten+Sekunden getrennt entgegen (so gibt es das Formular vor),
    speichert aber nur die zusammengezaehlte Gesamtsekundenzahl - einfacher zu
    validieren/verwenden als zwei einzelne Werte."""
    try:
        minuten = int(minuten)
        sekunden = int(sekunden)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Zahl")
    if minuten < 0 or sekunden < 0 or sekunden > 59:
        raise ValueError("Ungültiger Wert")
    gesamt = minuten * 60 + sekunden
    if gesamt < 1 or gesamt > SIMULATION_DAUER_MAX_SEK:
        raise ValueError(f"Dauer muss zwischen 1 Sekunde und {SIMULATION_DAUER_MAX_SEK // 60} Minuten liegen")
    with open(SIMULATION_EINSTELLUNGEN_PATH, "w") as f:
        json.dump({"dauer_sekunden": gesamt}, f)
    return gesamt


def _migriere_altes_pushover_conf():
    """Einmalige Migration: liest Token/User aus der alten pushover.conf, falls
    die neue Einstellungsdatei noch nicht existiert - verhindert, dass
    bestehende Pushover-Benachrichtigungen beim Umstieg auf die Web-UI-
    Verwaltung ploetzlich ohne Zugangsdaten stehen."""
    token, user = "", ""
    if os.path.isfile(ALTE_PUSHOVER_CONF_PATH):
        try:
            with open(ALTE_PUSHOVER_CONF_PATH) as f:
                for zeile in f:
                    zeile = zeile.strip()
                    if zeile.startswith("PUSHOVER_TOKEN="):
                        token = zeile.split("=", 1)[1].strip()
                    elif zeile.startswith("PUSHOVER_USER="):
                        user = zeile.split("=", 1)[1].strip()
        except OSError:
            pass
    return token, user


def lade_pushover_einstellungen():
    if not os.path.isfile(PUSHOVER_EINSTELLUNGEN_PATH):
        token, user = _migriere_altes_pushover_conf()
        werte = json.loads(json.dumps(PUSHOVER_STANDARD))  # tiefe Kopie
        werte["token"] = token
        werte["user"] = user
        return werte
    werte = _lade_einstellungen_datei(PUSHOVER_EINSTELLUNGEN_PATH, PUSHOVER_STANDARD)
    meldungen = dict(PUSHOVER_STANDARD["meldungen"])
    meldungen.update(werte.get("meldungen") or {})
    werte["meldungen"] = meldungen
    return werte


def _schreibe_pushover_shell_conf(werte):
    zeilen = [
        f"PUSHOVER_TOKEN={_sh_quote(werte['token'])}",
        f"PUSHOVER_USER={_sh_quote(werte['user'])}",
        f"PUSHOVER_AKTIV={_sh_quote('1' if werte.get('aktiv', True) else '0')}",
    ]
    for schema in PUSHOVER_MELDUNGEN_SCHEMA:
        mid = schema["id"]
        m = werte["meldungen"][mid]
        zeilen.append(f"ENABLED_{mid}={_sh_quote('1' if m['aktiv'] else '0')}")
        zeilen.append(f"TEXT_{mid}={_sh_quote(m['text'])}")
    with open(PUSHOVER_SHELL_CONF_PATH, "w") as f:
        f.write("\n".join(zeilen) + "\n")


def speichere_pushover_einstellungen(rohdaten):
    bereinigt = {
        "token": str(rohdaten.get("token", "")).strip(),
        "user": str(rohdaten.get("user", "")).strip(),
        "aktiv": bool(rohdaten.get("aktiv", False)),
        "meldungen": {},
    }
    roh_meldungen = rohdaten.get("meldungen") or {}
    for schema in PUSHOVER_MELDUNGEN_SCHEMA:
        mid = schema["id"]
        roh = roh_meldungen.get(mid) or {}
        standard = PUSHOVER_STANDARD["meldungen"][mid]
        text = str(roh.get("text", standard["text"])).strip() or standard["text"]
        bereinigt["meldungen"][mid] = {"aktiv": bool(roh.get("aktiv", standard["aktiv"])), "text": text}
    with open(PUSHOVER_EINSTELLUNGEN_PATH, "w") as f:
        json.dump(bereinigt, f)
    _schreibe_pushover_shell_conf(bereinigt)
    return bereinigt


def sende_pushover_test(token, user):
    """Schickt direkt eine Testnachricht mit den aktuell im Formular stehenden
    Zugangsdaten (auch wenn diese noch nicht gespeichert wurden) und meldet
    Pushovers eigene Fehlermeldung zurueck (z.B. bei ungueltigem Token)."""
    daten = urlencode({"token": token, "user": user, "message": "BeeTown HonigBox: Testnachricht"}).encode()
    try:
        with urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=daten, timeout=10) as resp:
            antwort = json.loads(resp.read().decode())
            if antwort.get("status") == 1:
                return True, ""
            return False, "; ".join(antwort.get("errors", ["Unbekannter Fehler"]))
    except urllib.error.HTTPError as e:
        try:
            antwort = json.loads(e.read().decode())
            return False, "; ".join(antwort.get("errors", [str(e)]))
        except (json.JSONDecodeError, OSError):
            return False, str(e)
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)


def lade_telegram_einstellungen():
    """Der 'meldungen'-Teil wird immer komplett gegen PUSHOVER_MELDUNGEN_SCHEMA
    neu aufgebaut (statt den rohen Datei-Inhalt zurueckzugeben) - damit auch
    aeltere gespeicherte Dateien ohne dieses Feld sowie spaeter hinzugekommene
    Meldungs-Typen automatisch mit 'aktiv=True' abgedeckt sind."""
    werte = _lade_einstellungen_datei(TELEGRAM_EINSTELLUNGEN_PATH, TELEGRAM_STANDARD)
    gespeichert = werte.get("meldungen") or {}
    werte["meldungen"] = {
        schema["id"]: {"aktiv": bool(gespeichert.get(schema["id"], {}).get("aktiv", True))}
        for schema in PUSHOVER_MELDUNGEN_SCHEMA
    }
    return werte


def _schreibe_telegram_shell_conf(bot_token, aktiv, meldungen):
    with open(TELEGRAM_SHELL_CONF_PATH, "w") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={_sh_quote(bot_token)}\n")
        f.write(f"TELEGRAM_AKTIV={_sh_quote('1' if aktiv else '0')}\n")
        # Eigener Praefix TG_ENABLED_<id> statt ENABLED_<id> - so kollidiert
        # das nicht mit Pushovers eigenem (getrennten) ENABLED_<id> aus
        # .pushover-einstellungen.sh, das send_telegram.sh nur noch fuer die
        # gemeinsamen TEXT_<id>-Variablen einliest.
        for schema in PUSHOVER_MELDUNGEN_SCHEMA:
            mid = schema["id"]
            an = meldungen.get(mid, {}).get("aktiv", True)
            f.write(f"TG_ENABLED_{mid}={_sh_quote('1' if an else '0')}\n")


def telegram_bot_info(token):
    """Fragt Telegrams getMe-Endpunkt ab - liefert den Bot-Benutzernamen fuer
    den Verbinden-Deep-Link (t.me/<username>?start=<code>) und bestaetigt
    nebenbei, ob der Token ueberhaupt gueltig ist. (ok, username, fehlertext)."""
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as resp:
            antwort = json.loads(resp.read().decode())
        if antwort.get("ok"):
            return True, antwort["result"].get("username", ""), ""
        return False, "", antwort.get("description", "Unbekannter Fehler")
    except urllib.error.HTTPError as e:
        try:
            antwort = json.loads(e.read().decode())
            return False, "", antwort.get("description", str(e))
        except (json.JSONDecodeError, OSError):
            return False, "", str(e)
    except (urllib.error.URLError, OSError) as e:
        return False, "", str(e)


def speichere_telegram_einstellungen(rohdaten):
    """Speichert immer (auch wenn die getMe-Bestaetigung fehlschlaegt, z.B.
    bei einem kurzen Netzwerkaussetzer) - gibt aber eine Warnung zurueck,
    falls der Token nicht bestaetigt werden konnte, damit ein echter Tippfehler
    trotzdem auffaellt statt sich unbemerkt festzusetzen."""
    token = str(rohdaten.get("bot_token", "")).strip()
    aktiv = bool(rohdaten.get("aktiv", False))
    roh_meldungen = rohdaten.get("meldungen") or {}
    meldungen = {
        schema["id"]: {"aktiv": bool(roh_meldungen.get(schema["id"], {}).get("aktiv", True))}
        for schema in PUSHOVER_MELDUNGEN_SCHEMA
    }
    username = ""
    warnung = None
    if token:
        ok, username, fehler = telegram_bot_info(token)
        if not ok:
            warnung = f"Bot-Token konnte nicht bestätigt werden: {fehler}"
    bereinigt = {"bot_token": token, "bot_username": username, "aktiv": aktiv, "meldungen": meldungen}
    with open(TELEGRAM_EINSTELLUNGEN_PATH, "w") as f:
        json.dump(bereinigt, f)
    _schreibe_telegram_shell_conf(token, aktiv, meldungen)
    return bereinigt, warnung


def lade_telegram_chats():
    return _lade_einstellungen_datei(TELEGRAM_CHATS_PATH, {})


def entferne_telegram_chat(chat_id):
    chats = lade_telegram_chats()
    if str(chat_id) in chats:
        del chats[str(chat_id)]
        with open(TELEGRAM_CHATS_PATH, "w") as f:
            json.dump(chats, f)
    return chats


def erzeuge_telegram_verbindungscode():
    """Kurzer Zufallscode fuer den Deep-Link t.me/<bot>?start=<code> - beim
    Antippen von 'Start' in Telegram schickt der Nutzer '/start <code>' an
    den Bot, telegram_wache_schleife() ordnet das dann dieser Anfrage zu.
    Raeumt abgelaufene Codes gleich mit auf, damit die Datei nicht waechst."""
    code = secrets.token_hex(4)
    pending = _lade_einstellungen_datei(TELEGRAM_PENDING_PATH, {})
    jetzt = time.time()
    pending = {c: v for c, v in pending.items() if jetzt - v.get("erstellt", 0) < TELEGRAM_CODE_GUELTIG_SEK}
    pending[code] = {"erstellt": jetzt}
    with open(TELEGRAM_PENDING_PATH, "w") as f:
        json.dump(pending, f)
    return code


def _telegram_offset_lesen():
    try:
        with open(TELEGRAM_OFFSET_PATH) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _telegram_offset_schreiben(offset):
    try:
        with open(TELEGRAM_OFFSET_PATH, "w") as f:
            f.write(str(offset))
    except OSError:
        pass


def _telegram_sende_nachricht(token, chat_id, text):
    """Gibt (ok, fehlertext) zurueck. Bestehende Aufrufer (Verbindungs-
    bestaetigung) ignorieren den Rueckgabewert bewusst weiter (fire-and-
    forget); der Test-Button unten wertet ihn aus."""
    try:
        daten = urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=daten)
        with urllib.request.urlopen(req, timeout=10) as resp:
            antwort = json.loads(resp.read().decode())
            if antwort.get("ok"):
                return True, ""
            return False, antwort.get("description", "Unbekannter Fehler")
    except urllib.error.HTTPError as e:
        try:
            antwort = json.loads(e.read().decode())
            return False, antwort.get("description", str(e))
        except (json.JSONDecodeError, OSError):
            return False, str(e)
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)


def sende_telegram_test(token):
    """Schickt eine Testnachricht an alle aktuell verbundenen Chats (Telegram
    kennt anders als Pushover keinen einzelnen User-Key, sondern eine Liste
    verknuepfter Chats). Meldet Telegrams Fehlertext zurueck, analog zu
    sende_pushover_test()."""
    if not token:
        return False, "Bot-Token erforderlich"
    chats = lade_telegram_chats()
    if not chats:
        return False, "Noch niemand verbunden – zuerst „Telegram verbinden“ nutzen"
    fehler = []
    for chat_id in chats:
        ok, fehlertext = _telegram_sende_nachricht(token, chat_id, "🍯 BeeTown HonigBox: Testnachricht")
        if not ok:
            fehler.append(fehlertext)
    if fehler:
        return False, "; ".join(fehler)
    return True, ""


def telegram_update_verarbeiten(antwort, offset):
    """Kern von telegram_wache_schleife() - von der Netzwerk-Abfrage getrennt,
    damit es sich isoliert (ohne echten HTTP-Request) testen laesst. Ordnet
    '/start <code>'-Nachrichten offenen Verbindungs-Codes zu, verschickt bei
    Erfolg eine Bestaetigung. Gibt den naechsten zu verwendenden Offset
    zurueck (hoechste gesehene update_id + 1, auch fuer nicht zutreffende
    Updates - sonst wuerden diese bei jedem Poll erneut zurueckgeliefert)."""
    einstellungen = lade_telegram_einstellungen()
    token = einstellungen.get("bot_token", "")
    pending = _lade_einstellungen_datei(TELEGRAM_PENDING_PATH, {})
    chats = lade_telegram_chats()
    pending_geaendert = False
    chats_geaendert = False
    for update in antwort.get("result", []):
        offset = max(offset, update["update_id"] + 1)
        nachricht = update.get("message") or {}
        text = (nachricht.get("text") or "").strip()
        if not text.startswith("/start"):
            continue
        teile = text.split(maxsplit=1)
        if len(teile) < 2:
            continue
        code = teile[1].strip()
        eintrag = pending.get(code)
        if not eintrag or time.time() - eintrag.get("erstellt", 0) > TELEGRAM_CODE_GUELTIG_SEK:
            continue
        chat = nachricht.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            continue
        name = chat.get("first_name") or chat.get("username") or "Unbekannt"
        chats[chat_id] = {"name": name, "verknuepft_am": time.strftime("%Y-%m-%d %H:%M")}
        chats_geaendert = True
        del pending[code]
        pending_geaendert = True
        if token:
            _telegram_sende_nachricht(
                token, chat_id,
                "✅ Verbindung erfolgreich! Du erhältst ab jetzt Benachrichtigungen von HonigBox.")
    if chats_geaendert:
        try:
            with open(TELEGRAM_CHATS_PATH, "w") as f:
                json.dump(chats, f)
        except OSError:
            pass
    if pending_geaendert:
        try:
            with open(TELEGRAM_PENDING_PATH, "w") as f:
                json.dump(pending, f)
        except OSError:
            pass
    return offset


def telegram_wache_schleife():
    """Hintergrund-Poller (in main() als Thread gestartet): fragt Telegrams
    getUpdates per Long-Poll (25 Sek. Server-seitiges Warten) ab. Laeuft nur
    tatsaechlich gegen die Telegram-API, solange ein Bot-Token eingetragen
    ist - ohne Token nur eine kurze Pause, kein unnoetiger Netzwerkaufruf."""
    while True:
        einstellungen = lade_telegram_einstellungen()
        token = einstellungen.get("bot_token", "")
        if not token:
            time.sleep(10)
            continue
        offset = _telegram_offset_lesen()
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=25"
            with urllib.request.urlopen(url, timeout=35) as resp:
                antwort = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            time.sleep(10)
            continue
        if not antwort.get("ok"):
            time.sleep(10)
            continue
        neuer_offset = telegram_update_verarbeiten(antwort, offset)
        _telegram_offset_schreiben(neuer_offset)


def pushover_stumm_rest_sekunden():
    """0, wenn gerade nicht stummgeschaltet ist - sonst verbleibende Sekunden.
    send_pushover.sh prueft dieselbe Datei eigenstaendig (Bash+python3 -c),
    da honigbox.sh (nicht diese Galerie) die eigentlichen Meldungen verschickt."""
    if not os.path.isfile(PUSHOVER_STUMM_PATH):
        return 0
    try:
        with open(PUSHOVER_STUMM_PATH) as f:
            bis = json.load(f).get("bis", 0)
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    return max(0, round(bis - time.time()))


def setze_pushover_stumm(aktiv, dauer_minuten=None):
    if aktiv:
        try:
            dauer_minuten = int(dauer_minuten)
        except (TypeError, ValueError):
            dauer_minuten = PUSHOVER_STUMM_DAUER_STANDARD_MIN
        if dauer_minuten not in PUSHOVER_STUMM_DAUER_OPTIONEN_MIN:
            dauer_minuten = PUSHOVER_STUMM_DAUER_STANDARD_MIN
        with open(PUSHOVER_STUMM_PATH, "w") as f:
            json.dump({"bis": time.time() + dauer_minuten * 60}, f)
    else:
        try:
            os.remove(PUSHOVER_STUMM_PATH)
        except OSError:
            pass


def fotos_pause_rest_sekunden():
    """0, wenn die Foto-Pause ('Fotos aus' / 'Messenger + Fotos aus') gerade
    nicht aktiv ist - sonst verbleibende Sekunden. Bewusst eine EIGENE Datei/
    eigener Zeitstempel statt eines Flags in PUSHOVER_STUMM_PATH - 'Fotos aus'
    soll unabhaengig von der Messenger-Stummschaltung funktionieren. honigbox.sh
    prueft dieselbe Datei eigenstaendig (fotos_pausiert())."""
    if not os.path.isfile(FOTOS_PAUSE_PATH):
        return 0
    try:
        with open(FOTOS_PAUSE_PATH) as f:
            bis = json.load(f).get("bis", 0)
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    return max(0, round(bis - time.time()))


def setze_fotos_pause(aktiv, dauer_minuten=None):
    if aktiv:
        try:
            dauer_minuten = int(dauer_minuten)
        except (TypeError, ValueError):
            dauer_minuten = PUSHOVER_STUMM_DAUER_STANDARD_MIN
        if dauer_minuten not in PUSHOVER_STUMM_DAUER_OPTIONEN_MIN:
            dauer_minuten = PUSHOVER_STUMM_DAUER_STANDARD_MIN
        with open(FOTOS_PAUSE_PATH, "w") as f:
            json.dump({"bis": time.time() + dauer_minuten * 60}, f)
    else:
        try:
            os.remove(FOTOS_PAUSE_PATH)
        except OSError:
            pass


def foto_testmodus_rest_sekunden():
    """0, wenn der Testmodus gerade nicht aktiv ist - sonst verbleibende Sekunden."""
    if not os.path.isfile(FOTO_TESTMODUS_PATH):
        return 0
    try:
        with open(FOTO_TESTMODUS_PATH) as f:
            bis = json.load(f).get("bis", 0)
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    return max(0, round(bis - time.time()))


def setze_foto_testmodus(aktiv):
    if aktiv:
        with open(FOTO_TESTMODUS_PATH, "w") as f:
            json.dump({"bis": time.time() + FOTO_TESTMODUS_DAUER_MIN * 60}, f)
    else:
        try:
            os.remove(FOTO_TESTMODUS_PATH)
        except OSError:
            pass


def _system_aktion(cmd):
    """Fuehrt reboot/poweroff synchron aus (beide kehren sofort zurueck, sie
    stossen den Vorgang nur an) und meldet den tatsaechlichen Erfolg zurueck -
    sudo -n schlaegt sofort und ohne Passwort-Prompt fehl, falls die
    sudoers-Freigabe fehlt, statt lautlos nichts zu tun."""
    try:
        ergebnis = subprocess.run(cmd, timeout=5, capture_output=True, text=True)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    if ergebnis.returncode != 0:
        return False, (ergebnis.stderr or ergebnis.stdout or "sudo hat den Befehl abgelehnt").strip()
    return True, ""


def _verzoegerter_dienst_restart(unit):
    """Fuer den Neustart der Galerie selbst: die Antwort auf die anfragende
    HTTP-Anfrage muss erst raus sein, bevor systemctl restart diesen Prozess
    beendet - sonst kommt beim Browser nie eine Antwort an."""
    time.sleep(1.5)
    subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", unit])


def status_dienst(unit):
    """systemctl is-active auf den eigenen Unit-Status zu befragen ist auf
    ueblichen systemd-Setups auch ohne root/sudo erlaubt (reine Abfrage,
    kein Start/Stop)."""
    try:
        ergebnis = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5
        )
        return (ergebnis.stdout or "unknown").strip()
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def gesamt_ram_mb():
    try:
        with open("/proc/meminfo") as f:
            for zeile in f:
                if zeile.startswith("MemTotal:"):
                    return int(zeile.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def empfohlene_ram_groesse_mb():
    """20% des Pi-RAMs, zwischen 64 und 512 MB - reicht fuer mehrere hundert
    bis tausende Fotos (siehe durchschnittliche_fotogroesse_bytes()), ohne
    dem restlichen System (OS, Python-Prozesse) zu viel RAM wegzunehmen."""
    gesamt = gesamt_ram_mb()
    if not gesamt:
        return 128
    return int(max(64, min(512, gesamt * SPEICHER_GROESSE_EMPFEHLUNG_ANTEIL)))


def lade_tuer_einstellungen():
    return _lade_einstellungen_datei(TUER_EINSTELLUNGEN_PATH, TUER_EINSTELLUNGEN_STANDARD)


def speichere_tuer_einstellungen(kontakt_invertiert):
    werte = {"kontakt_invertiert": bool(kontakt_invertiert)}
    with open(TUER_EINSTELLUNGEN_PATH, "w") as f:
        json.dump(werte, f)
    return werte


def lade_galerie_anzeige():
    return _lade_einstellungen_datei(GALERIE_ANZEIGE_PATH, GALERIE_ANZEIGE_STANDARD)


def speichere_galerie_anzeige(modus):
    if modus not in GALERIE_ANZEIGE_MODI:
        modus = GALERIE_ANZEIGE_STANDARD["modus"]
    werte = {"modus": modus}
    with open(GALERIE_ANZEIGE_PATH, "w") as f:
        json.dump(werte, f)
    return werte


def lade_extern_link():
    return _lade_einstellungen_datei(EXTERN_LINK_PATH, EXTERN_LINK_STANDARD)


def speichere_extern_link(rohdaten):
    """Nur http(s)-URLs erlaubt - verhindert z.B. ein versehentlich
    eingetragenes 'javascript:...' (wuerde beim Klick im Seiten-Kontext
    ausgefuehrt, da der Button die URL direkt als href setzt)."""
    aktiv = bool(rohdaten.get("aktiv", False))
    url = str(rohdaten.get("url", "")).strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("Link muss mit http:// oder https:// beginnen")
    label = str(rohdaten.get("label", "")).strip() or EXTERN_LINK_STANDARD["label"]
    werte = {"aktiv": aktiv, "url": url, "label": label}
    with open(EXTERN_LINK_PATH, "w") as f:
        json.dump(werte, f)
    return werte


def lade_start_buttons():
    return _lade_einstellungen_datei(START_BUTTONS_PATH, START_BUTTONS_STANDARD)


def speichere_start_buttons(rohdaten):
    werte = {key: bool(rohdaten.get(key, standard)) for key, standard in START_BUTTONS_STANDARD.items()}
    with open(START_BUTTONS_PATH, "w") as f:
        json.dump(werte, f)
    return werte


def lade_speicher_einstellungen():
    standard = {"speicherort": "ram", "ram_groesse_mb": empfohlene_ram_groesse_mb()}
    return _lade_einstellungen_datei(SPEICHER_EINSTELLUNGEN_PATH, standard)


def speichere_speicher_einstellungen(speicherort, ram_groesse_mb):
    if speicherort not in ("ram", "platte"):
        raise ValueError("Ungültiger Speicherort")
    try:
        ram_groesse_mb = int(ram_groesse_mb)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Größe")
    if not (SPEICHER_GROESSE_MIN_MB <= ram_groesse_mb <= SPEICHER_GROESSE_MAX_MB):
        raise ValueError(f"Größe muss zwischen {SPEICHER_GROESSE_MIN_MB} und {SPEICHER_GROESSE_MAX_MB} MB liegen")
    werte = {"speicherort": speicherort, "ram_groesse_mb": ram_groesse_mb}
    with open(SPEICHER_EINSTELLUNGEN_PATH, "w") as f:
        json.dump(werte, f)
    return werte


def durchschnittliche_fotogroesse_bytes():
    """Schaetzt die typische Dateigroesse anhand vorhandener Fotos (aktuelle
    Kamera-/Aufloesungseinstellungen spiegeln sich darin automatisch wider) -
    faellt auf einen Pauschalwert zurueck, wenn noch keine Fotos existieren."""
    try:
        dateien = [os.path.join(BILDER_DIR, d) for d in os.listdir(BILDER_DIR) if sichere_dateiname(d)]
        groessen = [os.path.getsize(p) for p in dateien[:50] if os.path.isfile(p)]
        if groessen:
            return sum(groessen) / len(groessen)
    except OSError:
        pass
    return DURCHSCHNITT_FOTO_BYTES_STANDARD


def ist_bilder_dir_tmpfs():
    try:
        with open("/proc/mounts") as f:
            for zeile in f:
                teile = zeile.split()
                if len(teile) >= 3 and teile[2] == "tmpfs" and os.path.realpath(teile[1]) == os.path.realpath(BILDER_DIR):
                    return True
    except OSError:
        pass
    return False


def speicher_status():
    """Fuer die Speicherort-Seite: gespeicherte Einstellung + aktueller
    tatsaechlicher Zustand + Kapazitaetsschaetzung, letztere basierend auf der
    durchschnittlichen Groesse vorhandener Fotos."""
    werte = lade_speicher_einstellungen()
    # Bewusst die Summe der Dateigroessen in BILDER_DIR (nicht shutil.disk_usage) -
    # letzteres liefert bei "platte" die Belegung der GESAMTEN SD-Karte, nicht
    # die der Fotos selbst. Fuer den Notfall-Aufraeumdienst (siehe
    # _wenig_speicher_aufraeumen) ist disk_usage() dagegen genau richtig, weil
    # es dort um den tatsaechlich verfuegbaren Platz geht.
    try:
        dateien = [os.path.join(BILDER_DIR, d) for d in os.listdir(BILDER_DIR) if sichere_dateiname(d)]
        aktuelle_nutzung_mb = round(sum(os.path.getsize(p) for p in dateien if os.path.isfile(p)) / (1024 * 1024), 1)
        aktuelle_anzahl = len(dateien)
    except OSError:
        aktuelle_nutzung_mb = None
        aktuelle_anzahl = 0
    durchschnitt = durchschnittliche_fotogroesse_bytes()
    geschaetzte_anzahl = int(werte["ram_groesse_mb"] * 1024 * 1024 / durchschnitt) if durchschnitt else 0
    return {
        "speicherort": werte["speicherort"],
        "ram_groesse_mb": werte["ram_groesse_mb"],
        "empfehlung_mb": empfohlene_ram_groesse_mb(),
        "gesamt_ram_mb": gesamt_ram_mb(),
        "ist_aktuell_ram": ist_bilder_dir_tmpfs(),
        "aktuelle_anzahl_fotos": aktuelle_anzahl,
        "aktuelle_nutzung_mb": aktuelle_nutzung_mb,
        "geschaetzte_anzahl_fotos": geschaetzte_anzahl,
        "durchschnittliche_foto_kb": round(durchschnitt / 1024, 1),
    }


def _speicher_wechsel_anwenden():
    """Wird verzoegert (nach Antwort an den Browser) in einem Hintergrund-
    Thread aufgerufen. --scope loest das Script bewusst aus der Cgroup von
    honigbox-galerie.service (diesem Prozess!) heraus, BEVOR das Script
    selbst per systemctl moeglicherweise genau diesen Dienst stoppt - sonst
    wuerde das Script sich mitten in der Migration selbst abwuergen."""
    time.sleep(1.5)
    subprocess.run([
        "sudo", "-n", "/usr/bin/systemd-run", "--scope", "--collect", "--",
        SPEICHER_UMSCHALT_SCRIPT,
    ])


def lade_tuer_status():
    if os.path.isfile(STATUS_PATH):
        try:
            with open(STATUS_PATH) as f:
                daten = json.load(f)
            offen_seit = daten.get("offen_seit")
            return {
                "tuer_offen": daten.get("tuer_offen"),
                "alter_sekunden": round(time.time() - daten.get("aktualisiert", 0), 1),
                "offen_dauer_sekunden": round(time.time() - offen_seit) if offen_seit else None,
                "letzte_oeffnung": daten.get("letzte_oeffnung"),
            }
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return {"tuer_offen": None, "alter_sekunden": None, "offen_dauer_sekunden": None, "letzte_oeffnung": None}


def aufraeum_schleife():
    """Loescht periodisch Fotos in BILDER_DIR, die aelter als die eingestellte
    Aufbewahrungsdauer (Tage + zusaetzliche Stunden, z.B. '3 Tage und 12
    Stunden' oder nur '3 Stunden' bei Tage=0) sind. ARCHIV_DIR ist bewusst
    ausgenommen - Archivieren ist ja gerade der Weg, ein Foto von der
    automatischen Loeschung auszunehmen."""
    while True:
        zeitplan = lade_foto_zeitplan()
        tage = zeitplan.get("aufbewahrungstage", 0)
        stunden = zeitplan.get("aufbewahrungsstunden", 0)
        gesamt_sekunden = tage * 86400 + stunden * 3600
        if gesamt_sekunden > 0:
            grenze = time.time() - gesamt_sekunden
            geloescht = 0
            for datei in os.listdir(BILDER_DIR):
                if not sichere_dateiname(datei):
                    continue
                pfad = os.path.join(BILDER_DIR, datei)
                try:
                    if os.path.getmtime(pfad) < grenze:
                        os.remove(pfad)
                        geloescht += 1
                except OSError:
                    pass
            if geloescht:
                print(f"Auto-Cleanup: {geloescht} Foto(s) aelter als {tage} Tage {stunden} Stunden geloescht")
        time.sleep(AUFRAEUM_INTERVALL_SEK)


def _wenig_speicher_aufraeumen():
    """Loescht bei Bedarf die aeltesten Fotos in BILDER_DIR, wenn der
    verfuegbare Speicher dort knapp wird (egal ob RAM-Disk oder SD-Karte,
    je nach Speicherort-Einstellung) - unabhaengig von der tagesbasierten
    Aufbewahrung in aufraeum_schleife(), die einen ploetzlich vollen Speicher
    (z.B. viele Tueroeffnungen an einem Tag) nicht verhindern wuerde. Raeumt
    mit Hysterese auf (ab <10% frei loeschen, bis 15% frei erreicht sind),
    damit nicht bei jedem Aufruf sofort wieder nachgeloescht werden muss."""
    try:
        usage = shutil.disk_usage(BILDER_DIR)
    except OSError:
        return
    if not usage.total or usage.free / usage.total >= 0.10:
        return
    try:
        dateien = [(os.path.getmtime(os.path.join(BILDER_DIR, d)), d)
                   for d in os.listdir(BILDER_DIR) if sichere_dateiname(d)]
    except OSError:
        return
    dateien.sort()  # aelteste zuerst
    geloescht = 0
    for _, name in dateien:
        try:
            usage = shutil.disk_usage(BILDER_DIR)
        except OSError:
            break
        if usage.total and usage.free / usage.total >= 0.15:
            break
        try:
            os.remove(os.path.join(BILDER_DIR, name))
            geloescht += 1
        except OSError:
            pass
    if geloescht:
        print(f"Speicherplatz knapp: {geloescht} aelteste(s) Foto(s) automatisch gelöscht")


def speicher_wache_schleife():
    while True:
        _wenig_speicher_aufraeumen()
        time.sleep(SPEICHER_WACHE_INTERVALL_SEK)


KAMERA_CHECK_INTERVALL_SEK = 60
_kamera_erkannt_cache = True  # optimistisch, bis der erste Check durchgelaufen ist


def kamera_erkannt():
    """Prueft per --list-cameras, ob rpicam-still/libcamera-still ueberhaupt
    eine angeschlossene Kamera findet - unabhaengig davon, ob gerade ein Foto
    aufgenommen wird. Wird in einem eigenen Hintergrund-Thread aufgerufen
    (kamera_wache_schleife), NICHT direkt bei jeder Statusabfrage - der Aufruf
    dauert spuerbar (Sensor-Initialisierung), das soll die Weboberflaeche
    nicht bei jedem Laden verzoegern."""
    for befehl in ("rpicam-still", "libcamera-still"):
        if shutil.which(befehl) is None:
            continue
        try:
            ergebnis = subprocess.run([befehl, "--list-cameras"], capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            return False
        ausgabe = (ergebnis.stdout or "") + (ergebnis.stderr or "")
        if "no cameras available" in ausgabe.lower():
            return False
        return bool(re.search(r"^\s*\d+\s*:", ausgabe, re.MULTILINE))
    return False  # weder rpicam-still noch libcamera-still installiert


def kamera_wache_schleife():
    global _kamera_erkannt_cache
    while True:
        _kamera_erkannt_cache = kamera_erkannt()
        time.sleep(KAMERA_CHECK_INTERVALL_SEK)


class Handler(BaseHTTPRequestHandler):
    server_version = "HonigBoxGalerie/1.0"

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ct, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _rjson(self):
        laenge = int(self.headers.get("Content-Length", 0))
        if laenge <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(laenge).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _rform(self):
        """Wie _rjson(), aber fuer normale HTML-<form>-POSTs (Login/
        Ersteinrichtung) - die schicken application/x-www-form-urlencoded,
        kein JSON."""
        laenge = int(self.headers.get("Content-Length", 0))
        if laenge <= 0:
            return {}
        try:
            rohdaten = self.rfile.read(laenge).decode()
        except UnicodeDecodeError:
            return {}
        return {k: v[0] for k, v in parse_qs(rohdaten).items()}

    def _html(self, text, code=200):
        self._bytes(text.encode(), "text/html; charset=utf-8", code)

    def _redirect(self, ziel):
        self.send_response(302)
        self.send_header("Location", ziel)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _zugang_pruefen(self):
        """None = Ersteinrichtung noetig, True = eingeloggt, False = Login noetig."""
        if ZUGANG_DEAKTIVIERT:
            return True
        if not zugang_eingerichtet():
            return None
        return zugang_cookie_gueltig(self.headers.get("Cookie"))

    def _setze_zugang_cookie_und_redirect(self, ziel):
        wert = _zugang_cookie_sollwert()
        self.send_response(302)
        self.send_header("Location", ziel)
        self.send_header(
            "Set-Cookie",
            f"{ZUGANG_COOKIE_NAME}={wert}; Max-Age={ZUGANG_COOKIE_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _post_einrichten(self):
        if zugang_eingerichtet():
            return self._redirect("/")
        form = self._rform()
        passwort = form.get("passwort", "")
        if passwort != form.get("passwort2", ""):
            return self._html(_seite_einrichten("Die beiden Passwörter stimmen nicht überein."))
        try:
            setze_zugang_passwort(passwort)
        except ValueError as e:
            return self._html(_seite_einrichten(str(e)))
        self._setze_zugang_cookie_und_redirect("/")

    def _post_login(self):
        if not zugang_eingerichtet():
            return self._redirect("/einrichten")
        form = self._rform()
        if not pruefe_zugang_passwort(form.get("passwort", "")):
            time.sleep(0.3)  # winziger Bremsklotz gegen automatisiertes Durchprobieren
            return self._html(_seite_login("Falsches Passwort."))
        self._setze_zugang_cookie_und_redirect("/")

    def _passwort_bestaetigt(self, body):
        """Fuer sicherheitskritische Aktionen (Neustart/Herunterfahren/
        Dienste-Neustart), die NICHT allein per gueltigem Session-Cookie
        ausloesbar sein sollen - sonst koennte ein gestohlener Cookie das
        Passwort-Reset-Zeitfenster oeffnen (siehe zugang_reset_moeglich()),
        ohne dass dafuer echter physischer/SSH-Zugriff noetig war."""
        if ZUGANG_DEAKTIVIERT or not zugang_eingerichtet():
            return True
        return pruefe_zugang_passwort(body.get("passwort", ""))

    def _post_zuruecksetzen(self):
        if not zugang_eingerichtet():
            return self._redirect("/einrichten")
        if not zugang_reset_moeglich():
            return self._redirect("/login")
        form = self._rform()
        passwort = form.get("passwort", "")
        if passwort != form.get("passwort2", ""):
            return self._html(_seite_zuruecksetzen("Die beiden Passwörter stimmen nicht überein."))
        try:
            setze_zugang_passwort(passwort)
        except ValueError as e:
            return self._html(_seite_zuruecksetzen(str(e)))
        zugang_alle_fotos_loeschen()
        self._setze_zugang_cookie_und_redirect("/")

    def _post_archiv_schluessel(self):
        form = self._rform()
        aktion = form.get("aktion", "")
        zustand = _archiv_schluessel_status()

        if aktion == "eingabe":
            roh = form.get("schluessel", "").strip()
            try:
                bundle = json.loads(roh)
            except (json.JSONDecodeError, TypeError):
                bundle = None
            gesperrt = [n for n in _ARCHIV_CONTAINER_LABEL if zustand[n]["status"] == "locked"]
            if isinstance(bundle, dict):
                eintraege = {name: bundle.get(name) for name in gesperrt
                             if isinstance(bundle.get(name), str) and bundle.get(name)}
                if not eintraege:
                    return self._html(_seite_archiv_schluessel(
                        "Diese Datei enthält keinen passenden Schlüssel - bitte die "
                        "heruntergeladene Datei unverändert einfügen."))
                ungueltig = [n for n, w in eintraege.items() if not _ist_gueltiger_archiv_schluessel(w)]
                if ungueltig:
                    return self._html(_seite_archiv_schluessel(
                        "Das sieht nicht nach einem gültigen Schlüssel aus - bitte die Datei "
                        "unverändert einfügen, ohne zusätzliche Zeichen davor oder danach."))
                for name, wert in eintraege.items():
                    _archiv_eingabe_schreiben(name, wert)
            elif roh and len(gesperrt) == 1:
                # Nutzer hat vermutlich nur den einzelnen Schluessel eingefuegt
                # statt der kompletten heruntergeladenen JSON-Datei - bei genau
                # EINEM wartenden Container (Normalfall: RAM-Speichermodus,
                # nur das Archiv) ist trotzdem eindeutig, wofuer er gilt.
                if not _ist_gueltiger_archiv_schluessel(roh):
                    return self._html(_seite_archiv_schluessel(
                        "Das sieht nicht nach einem gültigen Schlüssel aus - bitte nochmal "
                        "genau kopieren, ohne zusätzliche Zeichen davor oder danach."))
                _archiv_eingabe_schreiben(gesperrt[0], roh)
            elif roh:
                return self._html(_seite_archiv_schluessel(
                    "Es warten mehrere Container auf einen Schlüssel - bitte die komplette "
                    "heruntergeladene Datei einfügen, nicht nur einen einzelnen Schlüssel."))
            else:
                return self._html(_seite_archiv_schluessel("Bitte einen Schlüssel eingeben."))
        elif aktion == "verwerfen":
            for name in _ARCHIV_CONTAINER_LABEL:
                if zustand[name]["status"] == "locked":
                    _archiv_eingabe_schreiben(name, "NEU")
        elif aktion == "bestaetigt":
            for name, key_pfad in (("archiv", ARCHIV_SCHLUESSEL_PATH), ("bilder", BILDER_SCHLUESSEL_PATH)):
                if zustand[name]["status"] == "fresh":
                    try:
                        os.remove(key_pfad)
                    except OSError:
                        pass

        return self._redirect("/archiv-schluessel")

    def _authentifiziert(self):
        if not (AUTH_USER and AUTH_PASS):
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            return False
        return user == AUTH_USER and pw == AUTH_PASS

    def _login_verlangen(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="BeeTown HonigBox"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._authentifiziert():
            return self._login_verlangen()
        p = self.path.split("?", 1)[0]

        # styles.css muss auch ohne Zugangs-Cookie erreichbar sein, sonst
        # liesse sich die Login-/Ersteinrichtungs-Seite selbst nicht stylen.
        if p == "/styles.css":
            return self.serve_static(p)

        zugang = self._zugang_pruefen()

        if p == "/einrichten":
            if zugang_eingerichtet():
                return self._redirect("/")
            return self._html(_seite_einrichten())
        if p == "/login":
            if zugang is None:
                return self._redirect("/einrichten")
            if zugang is True:
                return self._redirect("/")
            return self._html(_seite_login())
        if p == "/zuruecksetzen":
            if not zugang_eingerichtet():
                return self._redirect("/einrichten")
            if not zugang_reset_moeglich():
                return self._redirect("/login")
            return self._html(_seite_zuruecksetzen())

        if zugang is None:
            return self._redirect("/einrichten")
        if zugang is False:
            return self._redirect("/login")

        if p == "/archiv-schluessel":
            if not _archiv_schluessel_seite_notwendig():
                return self._redirect("/")
            return self._html(_seite_archiv_schluessel())

        if p.startswith("/api/"):
            return self.api_get(p)
        if p.startswith("/bilder/"):
            return self.serve_bild(p, BILDER_DIR, "/bilder/")
        if p.startswith("/archiv-bilder/"):
            if not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            return self.serve_bild(p, ARCHIV_DIR, "/archiv-bilder/")
        if p.startswith("/thumbs/"):
            return self.serve_thumb(p, BILDER_DIR, "/thumbs/")
        if p.startswith("/archiv-thumbs/"):
            if not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            return self.serve_thumb(p, ARCHIV_DIR, "/archiv-thumbs/")

        # Nur die eigentliche HTML-Seite wird umgeleitet, wenn eine
        # Schluessel-Entscheidung ausstellt (siehe _archiv_schluessel_erforderlich)
        # - statische Assets (app.js/styles.css/Icons) muessen weiter laden
        # koennen, sonst wuerde die bereits laufende Seite nie mehr etwas
        # nachladen, und /api/-Routen antworten stattdessen mit einem klaren
        # 503-JSON-Fehler (siehe archiv_bereit()-Pruefungen oben) statt einer
        # HTML-Umleitung, die ein fetch()-basiertes Frontend nicht erwartet.
        if p in ("", "/") and _archiv_schluessel_erforderlich():
            return self._redirect("/archiv-schluessel")
        self.serve_static(p)

    def do_POST(self):
        if not self._authentifiziert():
            return self._login_verlangen()
        p = self.path.split("?", 1)[0]

        if p == "/einrichten":
            return self._post_einrichten()
        if p == "/login":
            return self._post_login()
        if p == "/zuruecksetzen":
            return self._post_zuruecksetzen()

        zugang = self._zugang_pruefen()
        if zugang is None:
            return self._redirect("/einrichten")
        if zugang is False:
            return self._redirect("/login")

        if p == "/archiv-schluessel":
            return self._post_archiv_schluessel()

        if p.startswith("/api/"):
            try:
                return self.api_post(p)
            except OSError as e:
                # Ohne das wuerde z.B. ein Schreibfehler beim Speichern von
                # Einstellungen (falsche Ordner-Berechtigungen) die Verbindung
                # nur stumm abbrechen (Browser sieht einen Netzwerkfehler,
                # ohne jede Fehlermeldung) statt eine klare Antwort zu liefern.
                return self._err(500, f"Speichern fehlgeschlagen: {e}")
        self._err(404, "Not found")

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._err(404, "Not found")
        ct = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._bytes(f.read(), ct)

    def serve_bild(self, path, verzeichnis, prefix):
        dateiname = sichere_dateiname(unquote(path[len(prefix):]))
        if not dateiname:
            return self._err(400, "Ungueltiger Dateiname")
        full = os.path.join(verzeichnis, dateiname)
        if not os.path.isfile(full):
            return self._err(404, "Not found")
        ct = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._bytes(f.read(), ct)

    def serve_thumb(self, path, verzeichnis, prefix):
        """Wie serve_bild, liefert aber ein verkleinertes/gecachtes Vorschaubild
        aus - erzeugt es beim ersten Abruf. Laeuft NUR wenn tatsaechlich ein
        Browser die Galerie oeffnet, hat also keinerlei Einfluss auf foto.sh/
        die Aufnahme-Geschwindigkeit waehrend die Tuer offen ist."""
        dateiname = sichere_dateiname(unquote(path[len(prefix):]))
        if not dateiname:
            return self._err(400, "Ungueltiger Dateiname")
        voller_pfad = os.path.join(verzeichnis, dateiname)
        if not os.path.isfile(voller_pfad):
            return self._err(404, "Not found")
        thumb_pfad = thumbnail_erzeugen_falls_noetig(verzeichnis, dateiname)
        ct = mimetypes.guess_type(thumb_pfad)[0] or "application/octet-stream"
        with open(thumb_pfad, "rb") as f:
            self._bytes(f.read(), ct)

    def api_get(self, path):
        if path == "/api/photos":
            q = parse_qs(urlparse(self.path).query)
            archiv = (q.get("archiv") or ["0"])[0] == "1"
            if archiv and not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            antwort = {"bilder": liste_bilder(ARCHIV_DIR if archiv else BILDER_DIR)}
            if archiv:
                antwort["notizen"] = lade_archiv_notizen()
            return self._json(antwort)
        if path == "/api/simulation":
            return self._json({"dauer_sekunden": lade_simulation_dauer(), "max_sekunden": SIMULATION_DAUER_MAX_SEK})
        if path == "/api/tuer-einstellungen":
            return self._json(lade_tuer_einstellungen())
        if path == "/api/galerie-anzeige":
            return self._json(lade_galerie_anzeige())
        if path == "/api/extern-link":
            return self._json(lade_extern_link())
        if path == "/api/start-buttons":
            return self._json(lade_start_buttons())
        if path == "/api/speicher":
            return self._json(speicher_status())
        if path == "/api/kamera":
            return self._json({"werte": lade_kamera_einstellungen(), "felder": KAMERA_FELDER})
        if path == "/api/foto-zeitplan":
            return self._json({"werte": lade_foto_zeitplan(), "felder": FOTO_ZEITPLAN_FELDER})
        if path == "/api/pushover":
            return self._json({"werte": lade_pushover_einstellungen(), "meldungen_schema": PUSHOVER_MELDUNGEN_SCHEMA})
        if path == "/api/telegram":
            return self._json({
                "werte": lade_telegram_einstellungen(),
                "chats": lade_telegram_chats(),
                "meldungen_schema": PUSHOVER_MELDUNGEN_SCHEMA,
            })
        if path == "/api/status":
            tuer = lade_tuer_status()
            return self._json({
                "galerie_service": "active",
                "honigbox_service": status_dienst("honigbox.service"),
                "tuer_offen": tuer["tuer_offen"],
                "tuer_alter_sekunden": tuer["alter_sekunden"],
                "tuer_offen_dauer_sekunden": tuer["offen_dauer_sekunden"],
                "tuer_letzte_oeffnung": tuer["letzte_oeffnung"],
                "kamera_erkannt": _kamera_erkannt_cache,
                "pushover_stumm_rest_sekunden": pushover_stumm_rest_sekunden(),
                "fotos_pause_rest_sekunden": fotos_pause_rest_sekunden(),
                "foto_testmodus_rest_sekunden": foto_testmodus_rest_sekunden(),
                "archiv_bereit": archiv_bereit(),
            })
        if path == "/api/foto/testmodus":
            return self._json({"rest_sekunden": foto_testmodus_rest_sekunden()})
        self._err(404, "Not found")

    def api_post(self, path):
        if path == "/api/photos/archivieren":
            if not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            body = self._rjson()
            archiviert = 0
            for roh in body.get("dateien", []):
                dateiname = sichere_dateiname(roh)
                if not dateiname:
                    continue
                quelle = os.path.join(BILDER_DIR, dateiname)
                if os.path.isfile(quelle):
                    # shutil.move() statt os.rename(): BILDER_DIR kann bei
                    # aktivierter RAM-Disk ein anderes Dateisystem sein als
                    # ARCHIV_DIR (immer SD-Karte) - os.rename() scheitert dann
                    # mit "Invalid cross-device link", shutil.move() faellt in
                    # dem Fall automatisch auf Kopieren+Loeschen zurueck.
                    shutil.move(quelle, os.path.join(ARCHIV_DIR, dateiname))
                    thumbnail_entfernen(BILDER_DIR, dateiname)  # wird beim Ansehen im Archiv neu erzeugt
                    archiviert += 1
            return self._json({"archiviert": archiviert})

        if path == "/api/photos/loeschen":
            body = self._rjson()
            archiv = bool(body.get("archiv", False))
            if archiv and not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            verzeichnis = ARCHIV_DIR if archiv else BILDER_DIR
            geloescht = 0
            for roh in body.get("dateien", []):
                dateiname = sichere_dateiname(roh)
                if not dateiname:
                    continue
                pfad = os.path.join(verzeichnis, dateiname)
                if os.path.isfile(pfad):
                    os.remove(pfad)
                    thumbnail_entfernen(verzeichnis, dateiname)
                    if archiv:
                        archiv_notiz_entfernen(dateiname)
                    geloescht += 1
            return self._json({"geloescht": geloescht})

        if path == "/api/archiv/notiz":
            if not archiv_bereit():
                return self._err(503, "Archiv ist gerade nicht verfügbar (Verschlüsselung noch nicht entsperrt).")
            body = self._rjson()
            dateiname = sichere_dateiname(body.get("datei", ""))
            if not dateiname or not os.path.isfile(os.path.join(ARCHIV_DIR, dateiname)):
                return self._err(404, "Foto nicht im Archiv gefunden")
            notiz = speichere_archiv_notiz(dateiname, body.get("text", ""))
            return self._json({"ok": True, "notiz": notiz})

        if path == "/api/simulation":
            body = self._rjson()
            try:
                gesamt = speichere_simulation_dauer(body.get("minuten", 0), body.get("sekunden", 0))
            except ValueError as e:
                return self._err(400, str(e))
            return self._json({"ok": True, "dauer_sekunden": gesamt})

        if path == "/api/tuer-einstellungen":
            body = self._rjson()
            werte = speichere_tuer_einstellungen(body.get("kontakt_invertiert", False))
            return self._json({"ok": True, **werte})

        if path == "/api/galerie-anzeige":
            body = self._rjson()
            werte = speichere_galerie_anzeige(str(body.get("modus", "")).strip())
            return self._json({"ok": True, **werte})

        if path == "/api/extern-link":
            body = self._rjson()
            try:
                werte = speichere_extern_link(body)
            except ValueError as e:
                return self._err(400, str(e))
            return self._json({"ok": True, **werte})

        if path == "/api/start-buttons":
            body = self._rjson()
            werte = speichere_start_buttons(body)
            return self._json({"ok": True, **werte})

        if path == "/api/speicher":
            body = self._rjson()
            try:
                speichere_speicher_einstellungen(body.get("speicherort"), body.get("ram_groesse_mb"))
            except ValueError as e:
                return self._err(400, str(e))
            # Loest den eigentlichen Mount-Wechsel verzoegert im Hintergrund
            # aus (siehe _speicher_wechsel_anwenden) - kann honigbox-galerie.
            # service (diesen Prozess!) neu starten, deshalb muss die Antwort
            # hier erst raus sein, bevor das passiert.
            threading.Thread(target=_speicher_wechsel_anwenden, daemon=True).start()
            return self._json({"ok": True})

        if path == "/api/kamera":
            body = self._rjson()
            bereinigt = speichere_kamera_einstellungen(body)
            return self._json({"ok": True, "werte": bereinigt})

        if path == "/api/kamera/zuruecksetzen":
            bereinigt = speichere_kamera_einstellungen({})
            return self._json({"ok": True, "werte": bereinigt})

        if path == "/api/foto-zeitplan":
            body = self._rjson()
            bereinigt = speichere_foto_zeitplan(body)
            return self._json({"ok": True, "werte": bereinigt})

        if path == "/api/foto-zeitplan/zuruecksetzen":
            bereinigt = speichere_foto_zeitplan({})
            return self._json({"ok": True, "werte": bereinigt})

        if path == "/api/pushover":
            body = self._rjson()
            bereinigt = speichere_pushover_einstellungen(body)
            return self._json({"ok": True, "werte": bereinigt})

        if path == "/api/pushover/test":
            body = self._rjson()
            token = str(body.get("token", "")).strip()
            user = str(body.get("user", "")).strip()
            if not token or not user:
                return self._err(400, "Token und User erforderlich")
            ok, fehler = sende_pushover_test(token, user)
            if not ok:
                return self._err(500, f"Test fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/pushover/stumm":
            body = self._rjson()
            setze_pushover_stumm(bool(body.get("aktiv")), body.get("dauer_minuten"))
            return self._json({"ok": True, "rest_sekunden": pushover_stumm_rest_sekunden()})

        if path == "/api/fotos-pause":
            body = self._rjson()
            setze_fotos_pause(bool(body.get("aktiv")), body.get("dauer_minuten"))
            return self._json({"ok": True, "rest_sekunden": fotos_pause_rest_sekunden()})

        if path == "/api/foto/testmodus":
            body = self._rjson()
            setze_foto_testmodus(bool(body.get("aktiv")))
            return self._json({"ok": True, "rest_sekunden": foto_testmodus_rest_sekunden()})

        if path == "/api/telegram":
            body = self._rjson()
            bereinigt, warnung = speichere_telegram_einstellungen(body)
            return self._json({"ok": True, "werte": bereinigt, "warnung": warnung})

        if path == "/api/telegram/test":
            body = self._rjson()
            token = str(body.get("token", "")).strip()
            ok, fehler = sende_telegram_test(token)
            if not ok:
                return self._err(500, f"Test fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/telegram/verbinden":
            einstellungen = lade_telegram_einstellungen()
            token = einstellungen.get("bot_token", "")
            if not token:
                return self._err(400, "Bitte zuerst einen Bot-Token eintragen und speichern.")
            username = einstellungen.get("bot_username", "")
            if not username:
                ok, username, fehler = telegram_bot_info(token)
                if not ok:
                    return self._err(400, f"Bot-Token konnte nicht bestätigt werden: {fehler}")
                einstellungen["bot_username"] = username
                with open(TELEGRAM_EINSTELLUNGEN_PATH, "w") as f:
                    json.dump(einstellungen, f)
            code = erzeuge_telegram_verbindungscode()
            return self._json({"ok": True, "code": code, "bot_username": username})

        if path == "/api/telegram/trennen":
            body = self._rjson()
            chats = entferne_telegram_chat(body.get("chat_id", ""))
            return self._json({"ok": True, "chats": chats})

        if path == "/api/system/neustart":
            body = self._rjson()
            if not self._passwort_bestaetigt(body):
                return self._err(403, "Falsches Passwort.")
            ok, fehler = _system_aktion(["sudo", "-n", "/usr/bin/systemctl", "reboot"])
            if not ok:
                return self._err(500, f"Neustart fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/system/herunterfahren":
            body = self._rjson()
            if not self._passwort_bestaetigt(body):
                return self._err(403, "Falsches Passwort.")
            ok, fehler = _system_aktion(["sudo", "-n", "/usr/bin/systemctl", "poweroff"])
            if not ok:
                return self._err(500, f"Herunterfahren fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/system/dienste-neustart":
            body = self._rjson()
            if not self._passwort_bestaetigt(body):
                return self._err(403, "Falsches Passwort.")
            # honigbox.service zuerst synchron neu starten und Erfolg pruefen -
            # das killt nicht den gerade antwortenden Prozess.
            ok, fehler = _system_aktion(["sudo", "-n", "/usr/bin/systemctl", "restart", "honigbox.service"])
            if not ok:
                return self._err(500, f"Neustart von honigbox.service fehlgeschlagen: {fehler}")
            # honigbox-galerie.service ist dieser Prozess selbst - erst
            # verzoegert im Hintergrund neu starten (siehe _verzoegerter_dienst_restart).
            threading.Thread(
                target=_verzoegerter_dienst_restart, args=("honigbox-galerie.service",), daemon=True,
            ).start()
            return self._json({"ok": True})

        if path == "/api/tuer/simulieren":
            dauer = lade_simulation_dauer()
            try:
                with open(TUER_SIMULATION_PATH, "w") as f:
                    json.dump({"bis": time.time() + dauer}, f)
                # Falls honigbox.sh bereits in einer laufenden Offen-Behandlung
                # steckt (Tuer ist schon wirklich offen), signalisiert das einen
                # sofortigen Neustart des Zyklus, statt auf ein echtes Schliessen
                # warten zu muessen.
                with open(TUER_NEUSTART_SIGNAL_PATH, "w") as f:
                    f.write(str(time.time()))
            except OSError as e:
                return self._err(500, str(e))
            return self._json({"ok": True, "dauer_sekunden": dauer})

        if path == "/api/foto/einzel":
            testmodus_aktiv = foto_testmodus_rest_sekunden() > 0
            befehl = [FOTO_SCRIPT, FOTO_TESTMODUS_METADATA_PATH] if testmodus_aktiv else [FOTO_SCRIPT]
            try:
                ergebnis = subprocess.run(befehl, timeout=30)
            except subprocess.TimeoutExpired:
                return self._err(504, "Zeitueberschreitung bei der Aufnahme")
            except OSError as e:
                return self._err(500, str(e))
            if ergebnis.returncode != 0:
                return self._err(500, "Kamera-Skript fehlgeschlagen")
            geloescht, helligkeit = einzelfoto_helligkeit_pruefen()
            antwort = {"ok": True, "geloescht": geloescht, "helligkeit": helligkeit}
            if testmodus_aktiv:
                antwort["testmodus"] = True
                antwort["kamera_werte"] = lade_kamera_einstellungen()
                antwort["kamera_felder"] = KAMERA_FELDER
                try:
                    with open(FOTO_TESTMODUS_METADATA_PATH) as f:
                        antwort["metadata"] = json.load(f)
                except (OSError, json.JSONDecodeError):
                    antwort["metadata"] = None
                finally:
                    try:
                        os.remove(FOTO_TESTMODUS_METADATA_PATH)
                    except OSError:
                        pass
            return self._json(antwort)

        self._err(404, "Not found")

    def log_message(self, fmt, *args):
        pass


def main():
    threading.Thread(target=aufraeum_schleife, daemon=True).start()
    threading.Thread(target=speicher_wache_schleife, daemon=True).start()
    threading.Thread(target=kamera_wache_schleife, daemon=True).start()
    threading.Thread(target=telegram_wache_schleife, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"HonigBox-Galerie läuft auf http://{HOST}:{PORT}  (Bilder: {BILDER_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
