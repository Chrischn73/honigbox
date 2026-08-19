#!/usr/bin/python
#---------------------------------------------------------------------
#    ___  ___  _ ____
#   / _ \/ _ \(_) __/__  __ __
#  / , _/ ___/ /\ \/ _ \/ // /
# /_/|_/_/  /_/___/ .__/\_, /
#                /_/   /___/
#
#           HonigBox Tuerueberwachung
# Ueberwacht einen Tuerkontaktschalter am Raspberry Pi (GPIO17) und meldet
# per Pushover, wenn die Box geoeffnet/geschlossen wird. Waehrend die Tuer
# offen ist, werden laufend Fotos nach einstellbarem Zeitplan gemacht
# (siehe .foto-zeitplan.json, per Galerie-Weboberflaeche editierbar).
#---------------------------------------------------------------------
import json
import os
import subprocess
import threading
import time
from gpiozero import Button

SCRIPT_DIR = "/opt/honigbox"
BILDER_DIR = "/opt/honigbox/fotos/Bilder"
# IMMER auf der SD-Karte, nie in BILDER_DIR - das kann bei aktivierter
# RAM-Disk (siehe Speicher-Einstellungen in galerie_server.py) ein tmpfs
# sein und wird dann bei jedem Neustart geleert. Pfad muss identisch mit
# EINSTELLUNGEN_DIR in galerie_server.py sein (gemeinsame Ablage).
EINSTELLUNGEN_DIR = "/opt/honigbox/einstellungen"
os.makedirs(EINSTELLUNGEN_DIR, exist_ok=True)
# Explizit chmod, nicht nur auf den mode= von makedirs verlassen: falls dieser
# Prozess (root) den Ordner als Erster anlegt, wuerde er sonst je nach umask
# z.B. nur 755 bekommen - dann kann galerie_server.py (www-data) dort keine
# Einstellungen mehr speichern (und der chmod-Versuch DORT scheitert lautlos,
# weil nur der Eigentuemer/root chmod darf). Als root schlaegt das hier nie fehl.
os.chmod(EINSTELLUNGEN_DIR, 0o777)
SWITCH_PIN = 17  # BCM-Nummerierung

# Schalter zwischen GPIO17 und GND, interner Pull-Up.
# Standard-Verdrahtung (seit 2026-08-14, Nutzer-Hardware): Kontakt offen
# (is_pressed=False) -> Tuer offen, Kontakt geschlossen (is_pressed=True) ->
# Tuer zu - siehe door_is_open() weiter unten (bewusst so gedreht statt nur
# den Standard von "Türkontakt umdrehen" zu aendern, damit die Checkbox
# weiterhin ausgeschaltet/"raus" bleibt UND als Ausnahme-Schalter fuer die
# jeweils andere Verdrahtung nutzbar ist). Ueber die Web-UI-Einstellung
# "Türkontakt umdrehen" laesst sich das bei Bedarf zurueckdrehen - siehe
# kontakt_invertiert()/door_is_open() weiter unten.
door_switch = Button(SWITCH_PIN, pull_up=True, bounce_time=0.2)

WAIT_CONFIRM = 1        # Sek. bis Bestaetigungsmessung nach erster Erkennung
CONFIRM_ROUNDS = 2       # Anzahl Bestaetigungen, um Prellen auszuschliessen
CONFIRM_DELAY = 2        # Sek. zwischen Bestaetigungen
WAIT_ESCALATE_1 = 240    # Sek. offen bis push2.sh (Eskalationsstufe 1)
WAIT_ESCALATE_2 = 1800   # weitere Sek. offen bis push3.sh (Eskalationsstufe 2)
LOOP_DELAY = 1           # Sek. zwischen Durchlaeufen der Hauptschleife
LOOP_TICK = 1            # Sek. zwischen Pruefungen waehrend die Tuer offen ist

FOTO_ZEITPLAN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".foto-zeitplan.json")
# Feldnamen muessen mit FOTO_ZEITPLAN_STANDARD in galerie_server.py identisch
# sein (dort auch die Migration vom fruaheren zwei-stufigen Schema).
FOTO_ZEITPLAN_STANDARD = {
    "phase1_dauer_sekunden": 60, "phase1_intervall_sekunden": 3,
    "phase2_dauer_sekunden": 60, "phase2_intervall_sekunden": 8,
    "intervall_danach_sekunden": 15, "max_anzahl": 30,
    "aufbewahrungstage": 30, "aufbewahrungsstunden": 0, "dunkle_fotos_loeschen": True, "helligkeitsschwelle": 28,
}

STATUS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".status.json")
TUER_SIMULATION_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-simulation-bis.json")
TUER_NEUSTART_SIGNAL_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-neustart-signal")
# Pfad muss identisch mit TUER_EINSTELLUNGEN_PATH in galerie_server.py sein
# (von dort per Web-UI geschrieben, siehe /api/tuer-einstellungen).
TUER_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".tuer-einstellungen.json")
# Pfad muss identisch mit FOTOS_PAUSE_PATH in galerie_server.py sein - eigene
# Datei/Zeitstempel getrennt von der Messenger-Stummschaltung, damit "Fotos aus"
# unabhaengig von "Messenger aus" funktioniert (drei Buttons auf der Startseite).
FOTOS_PAUSE_PATH = os.path.join(EINSTELLUNGEN_DIR, ".fotos-pause-bis.json")
# Phase C: LUKS-Verschluesselung der aktuellen Fotos im "Platte"-Speichermodus
# (RAM-Disk-Modus braucht das nicht). Pfade muessen identisch mit den in
# archiv_entschluesseln.sh (RUN_DIR-Konvention "<name>-status") erzeugten
# bzw. GALERIE_BILDER_STATUS in galerie_server.py verwendeten sein.
SPEICHER_EINSTELLUNGEN_DATEI = os.path.join(EINSTELLUNGEN_DIR, ".speicher-einstellungen.json")
BILDER_STATUS_PATH = "/run/honigbox/bilder-status"


def foto_speicher_nicht_bereit():
    """True, wenn der Speicherort 'platte' ist UND der verschluesselte
    Bilder-Container beim Boot noch nicht entschluesselt/angelegt wurde
    (siehe archiv_entschluesseln.sh) - verhindert, dass in genau diesem
    kurzen Boot-Fenster ein Foto unverschluesselt direkt auf die
    Root-Partition statt in den LUKS-Mount geschrieben wird. Im
    RAM-Disk-Modus immer False (tmpfs ist unabhaengig davon schon durch
    RequiresMountsFor in honigbox.service abgesichert)."""
    try:
        with open(SPEICHER_EINSTELLUNGEN_DATEI) as f:
            speicherort = json.load(f).get("speicherort", "ram")
    except (OSError, json.JSONDecodeError):
        speicherort = "ram"
    if speicherort != "platte":
        return False
    try:
        with open(BILDER_STATUS_PATH) as f:
            status = f.read().strip()
    except OSError:
        return True  # noch keine Status-Datei -> Container noch nicht bereit
    return status not in ("unlocked", "fresh")


def fotos_pausiert():
    """True, solange 'Fotos aus' oder 'Messenger + Fotos aus' aktiv ist, ODER
    solange im Platte-Speichermodus der verschluesselte Bilder-Container noch
    nicht bereit ist (siehe foto_speicher_nicht_bereit()) - unterdrueckt dann
    sowohl das Sofortfoto (sofortfoto_start()) als auch die Fotos waehrend
    warte_waehrend_offen(). Die Messenger-Stummschaltung selbst ist
    unabhaengig davon (eigene Datei, von send_pushover.sh/send_telegram.sh
    geprueft)."""
    if foto_speicher_nicht_bereit():
        return True
    try:
        with open(FOTOS_PAUSE_PATH) as f:
            bis = json.load(f).get("bis", 0)
        return time.time() < bis
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False


def schreibe_status(offen, offen_seit, letzte_oeffnung):
    """Fuer die Status-Anzeige in der Galerie-Weboberflaeche (siehe /api/status
    in galerie_server.py) - die Galerie liest hier nur, greift nicht selbst
    per GPIO auf den Schalter zu. offen_seit ist der Unix-Zeitstempel, seit dem
    die Tuer ununterbrochen offen ist (None wenn sie gerade zu ist) - damit
    kann die Weboberflaeche anzeigen, wie lange die Tuer schon offen steht.
    letzte_oeffnung ist dagegen ein dauerhafter Zeitstempel (bleibt auch nach
    dem Schliessen/einem Neustart erhalten), fuer die "letzte Tueroeffnung"-
    Anzeige."""
    try:
        with open(STATUS_PATH, "w") as f:
            json.dump({
                "tuer_offen": offen, "aktualisiert": time.time(),
                "offen_seit": offen_seit, "letzte_oeffnung": letzte_oeffnung,
            }, f)
    except OSError:
        pass


def _lade_letzte_oeffnung():
    """Beim Skriptstart (z.B. nach einem Pi-Neustart) den zuletzt bekannten
    Zeitstempel aus der Status-Datei uebernehmen, statt bei jedem Neustart der
    Tuerueberwachung wieder bei None anzufangen."""
    try:
        with open(STATUS_PATH) as f:
            wert = json.load(f).get("letzte_oeffnung")
        return float(wert) if wert else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def simulation_aktiv():
    """True, solange eine per Web-UI ausgeloeste Test-Simulation ("Tuer
    oeffnen") noch nicht abgelaufen ist. Raeumt die Datei selbst auf, sobald
    die Zeit abgelaufen ist - kein Cleanup an anderer Stelle noetig."""
    if not os.path.isfile(TUER_SIMULATION_PATH):
        return False
    try:
        with open(TUER_SIMULATION_PATH) as f:
            bis = json.load(f).get("bis", 0)
    except (json.JSONDecodeError, OSError, TypeError):
        return False
    if time.time() >= bis:
        try:
            os.remove(TUER_SIMULATION_PATH)
        except OSError:
            pass
        return False
    return True


_tuer_offen_seit = None  # Unix-Zeitstempel, seit dem die Tuer ununterbrochen offen ist (None = zu)
_letzte_oeffnung = _lade_letzte_oeffnung()  # Unix-Zeitstempel der letzten Oeffnung, ueberlebt Schliessen/Neustart


def kontakt_invertiert():
    """True, falls der Schalter laut Web-UI-Einstellung ("Türkontakt
    umdrehen") von der Standard-Verdrahtung abweichend angeschlossen ist -
    siehe /api/tuer-einstellungen in galerie_server.py. Faellt auf False
    zurueck (Standard-Verdrahtung, siehe Kommentar bei door_switch oben),
    wenn die Einstellungsdatei fehlt oder kaputt ist."""
    try:
        with open(TUER_EINSTELLUNGEN_PATH) as f:
            return bool(json.load(f).get("kontakt_invertiert", False))
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def door_is_open():
    global _tuer_offen_seit, _letzte_oeffnung
    # Seit 2026-08-14 gedreht (neue Standard-Verdrahtung, siehe Kommentar bei
    # door_switch oben): is_pressed=False -> offen statt is_pressed=True -> offen.
    kontakt_bedeutet_offen = not door_switch.is_pressed
    if kontakt_invertiert():
        kontakt_bedeutet_offen = not kontakt_bedeutet_offen
    offen = simulation_aktiv() or kontakt_bedeutet_offen
    if offen:
        if _tuer_offen_seit is None:
            _tuer_offen_seit = time.time()
            _letzte_oeffnung = _tuer_offen_seit
    else:
        _tuer_offen_seit = None
    schreibe_status(offen, _tuer_offen_seit, _letzte_oeffnung)
    return offen


def neustart_angefordert():
    """True, wenn per Web-UI ein Neustart des aktuellen Oeffnungs-Zyklus
    angefordert wurde (Simulationsbutton bei bereits offener Tuer) - konsumiert
    das Signal (loescht die Datei), damit es nur einmal wirkt."""
    if not os.path.isfile(TUER_NEUSTART_SIGNAL_PATH):
        return False
    try:
        os.remove(TUER_NEUSTART_SIGNAL_PATH)
    except OSError:
        pass
    return True


def run(script, *args):
    # check=False fängt nur einen Fehler-Exitcode ab, NICHT eine fehlende/nicht
    # ausfuehrbare Datei (FileNotFoundError/PermissionError) - das wuerde sonst
    # die Tuerueberwachung abstuerzen lassen, siehe push()-Kommentar oben.
    try:
        subprocess.run([f"{SCRIPT_DIR}/{script}", *args], check=False)
    except OSError as e:
        print(f"{script} konnte nicht gestartet werden: {e}")


def push(meldung_id):
    run("send_pushover.sh", meldung_id)
    # Telegram bewusst per Popen (nicht run()/wait) angestossen - send_pushover.sh
    # oben blockiert schon bis zu mehrere Sekunden bei Netzwerkproblemen
    # (curl --retry); ein zweiter, ebenfalls wartender Aufruf wuerde diese
    # Verzoegerung fuer die Tuerueberwachung verdoppeln. Telegram braucht das
    # Ergebnis hier nicht, laeuft also unabhaengig im Hintergrund weiter.
    # try/except: fehlt/fehlt-ausfuehrbar send_telegram.sh (z.B. direkt nach
    # einem reinen "Update" ohne erneutes install.sh) darf die Tuerueberwachung
    # NIE zum Absturz bringen - das fuehrte sonst zu einer Neustart-Schleife
    # (systemd Restart=on-failure) mit wiederholter "Raspi wurde gestartet"-Meldung.
    try:
        subprocess.Popen([f"{SCRIPT_DIR}/send_telegram.sh", meldung_id])
    except OSError as e:
        print(f"send_telegram.sh konnte nicht gestartet werden: {e}")


def confirm_still_open():
    """Mehrfach pruefen, um kurze Erschuetterungen/Prellen auszuschliessen."""
    for _ in range(CONFIRM_ROUNDS):
        if not door_is_open():
            return False
        time.sleep(CONFIRM_DELAY)
    return door_is_open()


def lade_foto_zeitplan():
    zeitplan = dict(FOTO_ZEITPLAN_STANDARD)
    if os.path.isfile(FOTO_ZEITPLAN_PATH):
        try:
            with open(FOTO_ZEITPLAN_PATH) as f:
                zeitplan.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return zeitplan


def dunkle_fotos_aufraeumen(sitzung_start):
    """Loescht zu dunkle Fotos EINER Tueroeffnung (z.B. Tuer im Aufnahmemoment
    noch fast zu, Biene direkt vor der Linse) - laeuft als Aufraeum-Durchgang
    NACH dem Schliessen (in einem eigenen Thread, blockiert also nicht die
    Tuerueberwachung), statt nach jedem einzelnen Foto: eine Helligkeitspruefung
    pro Bild wuerde durch den Pillow-Start die Aufnahme-Taktung waehrend die
    Tuer offen ist spuerbar ausbremsen. Betrachtet nur Fotos mit
    Aenderungsdatum >= sitzung_start, damit aeltere/manuelle Einzelfotos
    unberuehrt bleiben. Bei jedem Fehler (Pillow fehlt, Bild kaputt) wird das
    jeweilige Foto NICHT geloescht (Fail-Open)."""
    einstellungen = lade_foto_zeitplan()
    if not einstellungen.get("dunkle_fotos_loeschen"):
        return
    schwelle = einstellungen.get("helligkeitsschwelle", 28)
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return
    try:
        dateinamen = os.listdir(BILDER_DIR)
    except OSError:
        return
    for name in dateinamen:
        if not name.lower().endswith((".jpg", ".jpeg")):
            continue
        pfad = os.path.join(BILDER_DIR, name)
        try:
            # 2 Sek. Puffer: manche Dateisysteme runden Zeitstempel auf ganze
            # Sekunden, sonst koennte ein Foto kurz nach sitzung_start faelschlich
            # als "aelter" durchfallen und uebersprungen werden.
            if os.path.getmtime(pfad) < sitzung_start - 2:
                continue
            helligkeit = ImageStat.Stat(Image.open(pfad).convert("L")).mean[0]
            if helligkeit < schwelle:
                os.remove(pfad)
        except (OSError, ValueError):
            continue


def warte_waehrend_offen(gesamt_timeout, eskalationen, sofortfoto_erledigt=False):
    """Pollt bis die Tuer zu ist, gesamt_timeout erreicht ist, oder ein
    Neustart des Zyklus angefordert wurde. Macht waehrend die Tuer offen ist
    laufend Fotos nach einem DREI-stufigen Zeitplan (bis max_anzahl): erst
    phase1_dauer_sekunden lang alle phase1_intervall_sekunden, danach weitere
    phase2_dauer_sekunden lang alle phase2_intervall_sekunden, danach alle
    intervall_danach_sekunden. Loest ausserdem die in `eskalationen`
    angegebenen Push-Scripte genau einmal aus, wenn ihr Zeitpunkt (Sek. seit
    Tueroeffnung) erreicht wird.
    sofortfoto_erledigt=True (nur beim allerersten Aufruf nach einer echten
    Tueroeffnung, siehe sofortfoto_start()/Hauptschleife) zaehlt das bereits
    parallel zur Entprellung ausgeloeste erste Foto mit ein, statt bei
    elapsed=0 sofort ein zweites nachzuschieben.
    Gibt "geschlossen", "timeout" oder "neustart" zurueck."""
    zeitplan = lade_foto_zeitplan()
    phase1_intervall = zeitplan["phase1_intervall_sekunden"]
    phase1_dauer = zeitplan["phase1_dauer_sekunden"]
    phase2_intervall = zeitplan["phase2_intervall_sekunden"]
    phase2_dauer = zeitplan["phase2_dauer_sekunden"]
    intervall_danach = zeitplan["intervall_danach_sekunden"]
    max_anzahl = zeitplan["max_anzahl"]

    anzahl_fotos = 1 if sofortfoto_erledigt else 0
    naechstes_foto = phase1_intervall if sofortfoto_erledigt else 0
    ausgeloest = set()
    elapsed = 0

    while elapsed < gesamt_timeout:
        if neustart_angefordert():
            return "neustart"

        if not door_is_open():
            return "geschlossen"

        if anzahl_fotos < max_anzahl and elapsed >= naechstes_foto:
            # "Messenger + Fotos aus" ueberspringt nur die Aufnahme selbst -
            # anzahl_fotos NICHT hochzaehlen (sonst waere das Foto-Kontingent
            # nach der Pause schon verbraucht), naechstes_foto aber trotzdem
            # normal weiterschalten (sonst wuerde jede einzelne Sekunde waehrend
            # der Pause unnoetig neu geprueft).
            if not fotos_pausiert():
                run("foto.sh")
                anzahl_fotos += 1
            if elapsed < phase1_dauer:
                aktuelles_intervall = phase1_intervall
            elif elapsed < phase1_dauer + phase2_dauer:
                aktuelles_intervall = phase2_intervall
            else:
                aktuelles_intervall = intervall_danach
            naechstes_foto = elapsed + aktuelles_intervall

        for zeitpunkt, meldung_id in eskalationen:
            if zeitpunkt not in ausgeloest and elapsed >= zeitpunkt:
                ausgeloest.add(zeitpunkt)
                push(meldung_id)

        time.sleep(LOOP_TICK)
        elapsed += LOOP_TICK

    return "geschlossen" if not door_is_open() else "timeout"


def behandle_tueroeffnung(sofortfoto_erledigt=False):
    """Deckt eine komplette Tueroeffnung ab (Meldung, Foto-Zeitplan,
    Eskalationen) - startet intern komplett neu, wenn per Simulation bei
    bereits offener Tuer ein Neustart angefordert wird, ohne dass die Tuer
    dafuer wirklich schliessen muss. sofortfoto_erledigt gilt (falls True) nur
    fuer die allererste Runde - ein Neustart per Simulation zaehlt nicht als
    neues Sofortfoto, siehe warte_waehrend_offen()."""
    while True:
        neustart_angefordert()  # evtl. veraltetes Signal verwerfen, bevor es losgeht
        sitzung_start = time.time()

        print("Tür wurde geöffnet")
        time.sleep(2)  # wie zuvor in push.sh
        push("geoeffnet")

        eskalationen = [
            (WAIT_ESCALATE_1, "eskalation1"),
            (WAIT_ESCALATE_1 + WAIT_ESCALATE_2, "eskalation2"),
        ]
        ergebnis = warte_waehrend_offen(WAIT_ESCALATE_1 + WAIT_ESCALATE_2, eskalationen, sofortfoto_erledigt)
        sofortfoto_erledigt = False

        if ergebnis == "neustart":
            print("Simulation: Türöffnung wird als neu behandelt")
            continue

        threading.Thread(target=dunkle_fotos_aufraeumen, args=(sitzung_start,), daemon=True).start()

        if ergebnis == "geschlossen":
            print("Tür wieder zu !")
            push("geschlossen")
            return
        print("Tür weiterhin offen nach maximaler Eskalationszeit")
        return


def sofortfoto_start():
    """Feuert das erste Foto EINER Tueroeffnung schon parallel zur
    Entprellungs-Bestaetigung (WAIT_CONFIRM/confirm_still_open() in der
    Hauptschleife unten), statt erst danach - die Kamera braucht selbst schon
    ein bis mehrere Sekunden (Prozessstart, Belichtung/Weissabgleich
    einschwingen), die sich sonst zur Entprellungs-Wartezeit ADDIEREN statt
    gleichzeitig zu laufen. Bewusst per Popen (nicht run()/subprocess.run) -
    blockiert die Tuerueberwachung waehrend der Bestaetigungs-Sleeps nicht.
    Bei einem falsch bestaetigten Wackelkontakt bleibt im schlimmsten Fall ein
    einzelnes ueberfluessiges Foto zurueck (in der Galerie loeschbar) - dieser
    Tausch (schnelleres erstes Echt-Foto gegen dieses seltene Risiko) ist
    bewusst so gewaehlt, siehe Chat vom 2026-08-11."""
    try:
        subprocess.Popen([f"{SCRIPT_DIR}/foto.sh"])
    except OSError as e:
        print(f"foto.sh (Sofort-Trigger) konnte nicht gestartet werden: {e}")


time.sleep(5)  # 3 Sek. urspruengliche Startverzoegerung + 2 Sek. wie zuvor in push-boot.sh
push("boot")

while True:
    if door_is_open():
        # max_anzahl=0 (Nutzer will bei Tueroeffnung explizit KEINE Fotos) und
        # "Messenger + Fotos aus" muessen auch hier gelten, nicht nur im
        # spaeteren Zeitplan.
        sofortfoto_erledigt = lade_foto_zeitplan().get("max_anzahl", 0) > 0 and not fotos_pausiert()
        if sofortfoto_erledigt:
            sofortfoto_start()
        time.sleep(WAIT_CONFIRM)
        if confirm_still_open():
            behandle_tueroeffnung(sofortfoto_erledigt)

    time.sleep(LOOP_DELAY)
