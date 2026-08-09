#!/usr/bin/env python3
"""
BeeTown HonigBox - kleiner Server (nur Python-Standardbibliothek).
"""
import base64
import json
import mimetypes
import os
import re
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


AUFRAEUM_INTERVALL_SEK = 3600
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

GRUPPE_AUFNAHME = "Aufnahme-Zeitplan (während die Tür offen ist)"
GRUPPE_AUFRAEUMEN = "Aufräumen"

# Deckt die gesamte "📷 Fotos"-Seite ab: Aufnahme-Zeitplan waehrend einer
# Tueroeffnung + alles, was Fotos wieder loescht (Aufbewahrungsdauer, zu
# dunkle Fotos) - fruehere Versionen hatten das ueber drei verschiedene Orte
# verteilt (Kamera-Einstellungen, ein eigenes "Aufbewahrung"-Feld, hier),
# jetzt an einem Ort. "gruppe" steuert nur die Unterueberschrift im Frontend.
FOTO_ZEITPLAN_FELDER = [
    {"key": "intervall_1", "typ": "zahl", "label": "Foto-Intervall (Sekunden)", "min": 1, "max": 600, "step": 1,
     "gruppe": GRUPPE_AUFNAHME},
    {"key": "schwelle_sekunden", "typ": "zahl",
     "label": "Nach wie vielen Sekunden seltener fotografieren", "min": 1, "max": 3600, "step": 1,
     "gruppe": GRUPPE_AUFNAHME},
    {"key": "intervall_2", "typ": "zahl", "label": "Foto-Intervall danach (Sekunden)", "min": 1, "max": 3600, "step": 1,
     "gruppe": GRUPPE_AUFNAHME},
    {"key": "max_anzahl", "typ": "zahl", "label": "Maximale Anzahl Fotos pro Türöffnung", "min": 1, "max": 500, "step": 1,
     "gruppe": GRUPPE_AUFNAHME},
    {"key": "aufbewahrungstage", "typ": "zahl", "label": "Fotos automatisch löschen nach (Tage, 0 = nie)",
     "min": 0, "max": 3650, "step": 1, "gruppe": GRUPPE_AUFRAEUMEN},
    {"key": "dunkle_fotos_loeschen", "typ": "checkbox",
     "label": "Zu dunkle Fotos automatisch löschen (z. B. Tür noch fast zu)", "gruppe": GRUPPE_AUFRAEUMEN},
    {"key": "helligkeitsschwelle", "typ": "zahl",
     "label": "Mindesthelligkeit zum Behalten (0-255, höher = strenger)", "min": 0, "max": 255, "step": 1,
     "gruppe": GRUPPE_AUFRAEUMEN},
]
FOTO_ZEITPLAN_STANDARD = {
    "intervall_1": 3, "schwelle_sekunden": 60, "intervall_2": 15, "max_anzahl": 30,
    "aufbewahrungstage": 30, "dunkle_fotos_loeschen": True, "helligkeitsschwelle": 25,
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

# STATUS_PATH/TUER_SIMULATION_PATH/TUER_NEUSTART_SIGNAL_PATH sind reine
# Signaldateien fuer die Verstaendigung mit honigbox.sh (siehe dort) - deren
# Pfade muessen dort identisch definiert sein. Kein Migrations-Bedarf (rein
# ephemer, ein fehlender/veralteter Stand beim Umzug ist harmlos).
STATUS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".status.json")
TUER_SIMULATION_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-simulation-bis.json")
TUER_NEUSTART_SIGNAL_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-neustart-signal")

SPEICHER_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".speicher-einstellungen.json")
SIMULATION_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".simulation-einstellungen.json")
SIMULATION_DAUER_STANDARD_SEK = 120
SIMULATION_DAUER_MAX_SEK = 1800

PUSHOVER_STUMM_PATH = os.path.join(EINSTELLUNGEN_DIR, ".pushover-stumm-bis.json")
PUSHOVER_STUMM_DAUER_SEK = 1800

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
    "token": "", "user": "",
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
os.makedirs(ARCHIV_DIR, exist_ok=True)

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


# Bewusst IN ARCHIV_DIR (nicht EINSTELLUNGEN_DIR) abgelegt: das Fotos-Backup
# im Setup-Portal sichert nur "fotos/" (Bilder+Archiv), nicht "einstellungen/"
# - eine Notiz wie "Diebstahl, 4€ fehlte" soll aber genau wie das zugehoerige
# Foto im Backup landen. ARCHIV_DIR ist ausserdem nie eine RAM-Disk (siehe
# Speicher-Einstellungen weiter unten), geht also auch bei aktivem tmpfs nie
# beim naechsten Neustart verloren.
ARCHIV_NOTIZEN_DATEI = os.path.join(ARCHIV_DIR, ".archiv-notizen.json")
NOTIZ_MAX_LAENGE = 300


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
    schwelle = einstellungen.get("helligkeitsschwelle", 25)
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


def setze_pushover_stumm(aktiv):
    if aktiv:
        with open(PUSHOVER_STUMM_PATH, "w") as f:
            json.dump({"bis": time.time() + PUSHOVER_STUMM_DAUER_SEK}, f)
    else:
        try:
            os.remove(PUSHOVER_STUMM_PATH)
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
            }
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return {"tuer_offen": None, "alter_sekunden": None, "offen_dauer_sekunden": None}


def aufraeum_schleife():
    """Loescht periodisch Fotos in BILDER_DIR, die aelter als die eingestellte
    Aufbewahrungsdauer sind. ARCHIV_DIR ist bewusst ausgenommen - Archivieren
    ist ja gerade der Weg, ein Foto von der automatischen Loeschung auszunehmen."""
    while True:
        tage = lade_foto_zeitplan().get("aufbewahrungstage", 0)
        if tage and tage > 0:
            grenze = time.time() - tage * 86400
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
                print(f"Auto-Cleanup: {geloescht} Foto(s) aelter als {tage} Tage geloescht")
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
        if p.startswith("/api/"):
            return self.api_get(p)
        if p.startswith("/bilder/"):
            return self.serve_bild(p, BILDER_DIR, "/bilder/")
        if p.startswith("/archiv-bilder/"):
            return self.serve_bild(p, ARCHIV_DIR, "/archiv-bilder/")
        if p.startswith("/thumbs/"):
            return self.serve_thumb(p, BILDER_DIR, "/thumbs/")
        if p.startswith("/archiv-thumbs/"):
            return self.serve_thumb(p, ARCHIV_DIR, "/archiv-thumbs/")
        self.serve_static(p)

    def do_POST(self):
        if not self._authentifiziert():
            return self._login_verlangen()
        p = self.path.split("?", 1)[0]
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
            antwort = {"bilder": liste_bilder(ARCHIV_DIR if archiv else BILDER_DIR)}
            if archiv:
                antwort["notizen"] = lade_archiv_notizen()
            return self._json(antwort)
        if path == "/api/simulation":
            return self._json({"dauer_sekunden": lade_simulation_dauer(), "max_sekunden": SIMULATION_DAUER_MAX_SEK})
        if path == "/api/speicher":
            return self._json(speicher_status())
        if path == "/api/kamera":
            return self._json({"werte": lade_kamera_einstellungen(), "felder": KAMERA_FELDER})
        if path == "/api/foto-zeitplan":
            return self._json({"werte": lade_foto_zeitplan(), "felder": FOTO_ZEITPLAN_FELDER})
        if path == "/api/pushover":
            return self._json({"werte": lade_pushover_einstellungen(), "meldungen_schema": PUSHOVER_MELDUNGEN_SCHEMA})
        if path == "/api/status":
            tuer = lade_tuer_status()
            return self._json({
                "galerie_service": "active",
                "honigbox_service": status_dienst("honigbox.service"),
                "tuer_offen": tuer["tuer_offen"],
                "tuer_alter_sekunden": tuer["alter_sekunden"],
                "tuer_offen_dauer_sekunden": tuer["offen_dauer_sekunden"],
                "kamera_erkannt": _kamera_erkannt_cache,
                "pushover_stumm_rest_sekunden": pushover_stumm_rest_sekunden(),
            })
        self._err(404, "Not found")

    def api_post(self, path):
        if path == "/api/photos/archivieren":
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
            setze_pushover_stumm(bool(body.get("aktiv")))
            return self._json({"ok": True, "rest_sekunden": pushover_stumm_rest_sekunden()})

        if path == "/api/system/neustart":
            ok, fehler = _system_aktion(["sudo", "-n", "/usr/bin/systemctl", "reboot"])
            if not ok:
                return self._err(500, f"Neustart fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/system/herunterfahren":
            ok, fehler = _system_aktion(["sudo", "-n", "/usr/bin/systemctl", "poweroff"])
            if not ok:
                return self._err(500, f"Herunterfahren fehlgeschlagen: {fehler}")
            return self._json({"ok": True})

        if path == "/api/system/dienste-neustart":
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
            try:
                ergebnis = subprocess.run([FOTO_SCRIPT], timeout=30)
            except subprocess.TimeoutExpired:
                return self._err(504, "Zeitueberschreitung bei der Aufnahme")
            except OSError as e:
                return self._err(500, str(e))
            if ergebnis.returncode != 0:
                return self._err(500, "Kamera-Skript fehlgeschlagen")
            geloescht, helligkeit = einzelfoto_helligkeit_pruefen()
            return self._json({"ok": True, "geloescht": geloescht, "helligkeit": helligkeit})

        self._err(404, "Not found")

    def log_message(self, fmt, *args):
        pass


def main():
    threading.Thread(target=aufraeum_schleife, daemon=True).start()
    threading.Thread(target=speicher_wache_schleife, daemon=True).start()
    threading.Thread(target=kamera_wache_schleife, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"HonigBox-Galerie läuft auf http://{HOST}:{PORT}  (Bilder: {BILDER_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
