#!/usr/bin/env python3
"""
HonigBox - naechtlicher Systemcheck nach Sicherheitsupdates (laeuft als root
ueber honigbox-systemneustart.timer, taeglich 05:00 und 10 Min. nach dem Boot -
bewusst nach den App-Updates um 04:00-04:05, siehe portal_vorgang_laeuft()).

Aufgaben:
1. Status fuer die Weboberflaeche schreiben (einstellungen/.systemupdate-status.json).
2. Kurz nach dem Boot: bleibt das Foto-Archiv gesperrt (LUKS-Schluessel liegt nur
   im RAM), eine Erinnerung per Messenger schicken (max. 3x, ca. alle 20 h).
3. Sonst, wenn "Automatischer Neustart" an ist und die Box seit mindestens
   <intervall_wochen> Wochen laeuft:
   - Kernel-Update bzw. /run/reboot-required -> kontrollierter Neustart
     (vorher RAM-Fotos ins Archiv verschieben; geht das nicht, wird NICHT
     gebootet und eine Meldung geschickt).
   - Sonst, wenn Pakete seit dem Start der Dienste aktualisiert wurden -> nur
     honigbox.service + honigbox-galerie.service neu starten (LUKS-Archiv bleibt
     offen, niemand muss den Schluessel neu eingeben).
Meldungen enthalten nie Fotos.
"""
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

BASIS = os.environ.get("HONIGBOX_BASIS", "/opt/honigbox")
EINSTELLUNGEN_DIR = os.path.join(BASIS, "einstellungen")
BILDER_DIR = os.path.join(BASIS, "fotos", "Bilder")
ARCHIV_DIR = os.path.join(BASIS, "fotos", "Archiv")
RUN_DIR = os.environ.get("HONIGBOX_RUN_DIR", "/run/honigbox")
# Markierungen fuer automatische Neustarts: Galerie und Imker-App oeffnen nach
# einem so ausgeloesten Start kein Passwort-Reset-Fenster (siehe
# galerie_server._automatischer_geraete_start()).
AUTO_NEUSTART_DIR = os.environ.get("AUTO_NEUSTART_DIR", "/var/lib/beetown-auto-neustart")

EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".systemupdate-einstellungen.json")
STATUS_PATH = os.path.join(EINSTELLUNGEN_DIR, ".systemupdate-status.json")
ERINNERUNG_PATH = os.path.join(EINSTELLUNGEN_DIR, ".archiv-erinnerung.json")
SPEICHER_EINSTELLUNGEN_PATH = os.path.join(EINSTELLUNGEN_DIR, ".speicher-einstellungen.json")
STILLER_NEUSTART_MARKER = os.path.join(EINSTELLUNGEN_DIR, ".stiller-dienstneustart")
TUER_STATUS_PATH = os.path.join(RUN_DIR, "tuer-status.json")
ARCHIV_STATUS_PATH = os.path.join(RUN_DIR, "archiv-status")
APT_STAMP = "/var/lib/apt/periodic/unattended-upgrades-stamp"
DPKG_LOG = "/var/log/dpkg.log"
# Gemeinsame Update-Sperre des Setup-Portals (flock, siehe setup_portal.py
# try_acquire_update_lock()).
PORTAL_SPERRE = "/run/setup-portal/update.lock"
# Units, die waehrenddessen App-Dateien umbauen oder das Portal neu starten.
UPDATE_UNITS = ("*-update-check.service", "setup-portal-install-*")

STANDARD = {"auto_neustart": True, "intervall_wochen": 4}
GUELTIGE_INTERVALLE = (4, 8, 12)
ERLAUBTE_ENDUNGEN = (".jpg", ".jpeg", ".png")
NACH_BOOT_FENSTER_SEK = 30 * 60
ERINNERUNG_MIN_ABSTAND_SEK = 20 * 3600
ERINNERUNG_MAX = 3
ARCHIV_PLATZ_PUFFER = 1.2  # Platz-Reserve beim Verschieben


def lade_einstellungen():
    werte = dict(STANDARD)
    try:
        with open(EINSTELLUNGEN_PATH) as f:
            roh = json.load(f)
        werte["auto_neustart"] = bool(roh.get("auto_neustart", True))
        wochen = int(roh.get("intervall_wochen", 4))
        werte["intervall_wochen"] = wochen if wochen in GUELTIGE_INTERVALLE else 4
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return werte


def laufzeit_sek():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def _versionsschluessel(version):
    return [int(t) for t in re.findall(r"\d+", version)]


def kernel_veraltet():
    """True, wenn fuer die laufende Kernel-Variante ein neuerer Kernel in
    /lib/modules installiert ist (z.B. laeuft 6.12.34+rpt-rpi-v8, installiert
    ist 6.12.41+rpt-rpi-v8). Vergleicht nur Verzeichnisse mit demselben
    Varianten-Suffix, damit v8/2712-Kernel sich nicht gegenseitig vortaeuschen."""
    laufend = os.uname().release
    treffer = re.match(r"^(\d+(?:\.\d+)*)(.*)$", laufend)
    if not treffer:
        return False
    suffix = treffer.group(2)
    for pfad in glob.glob("/lib/modules/*"):
        name = os.path.basename(pfad)
        t = re.match(r"^(\d+(?:\.\d+)*)(.*)$", name)
        if t and t.group(2) == suffix and \
                _versionsschluessel(t.group(1)) > _versionsschluessel(treffer.group(1)):
            return True
    return False


def neustart_grund():
    if kernel_veraltet():
        return "Kernel-Update"
    if os.path.exists("/run/reboot-required") or os.path.exists("/var/run/reboot-required"):
        return "Systemupdate (Neustart erforderlich)"
    return None


def _lese_json(pfad, standard):
    try:
        with open(pfad) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return standard


def _schreibe_json(pfad, daten):
    try:
        tmp = pfad + ".tmp"
        with open(tmp, "w") as f:
            json.dump(daten, f)
        os.chmod(tmp, 0o666)
        os.replace(tmp, pfad)
    except OSError as e:
        print(f"Konnte {pfad} nicht schreiben: {e}")


def archiv_status():
    try:
        with open(ARCHIV_STATUS_PATH) as f:
            return f.read().strip()
    except OSError:
        return None


def archiv_bereit():
    return archiv_status() in ("unlocked", "fresh")


def archiv_gesperrt():
    return archiv_status() in ("locked", "verarbeitung")


def melde(meldung_id, zusatz=""):
    """Messenger-Text-Meldung (Pushover + Telegram) - nie mit Foto. Beide Skripte
    pruefen selbst Kanal-/Meldungs-Schalter und Stummschaltung."""
    for skript in ("send_pushover.sh", "send_telegram.sh"):
        try:
            subprocess.run([os.path.join(BASIS, skript), meldung_id, zusatz],
                           check=False, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"{skript} fehlgeschlagen: {e}")


def schreibe_status(einstellungen, grund, notiz=None):
    stamp = None
    try:
        stamp = os.path.getmtime(APT_STAMP)
    except OSError:
        pass
    _schreibe_json(STATUS_PATH, {
        "geprueft": time.time(),
        "letztes_update": stamp,
        "neustart_noetig": bool(grund),
        "grund": grund,
        "auto_neustart": einstellungen["auto_neustart"],
        "intervall_wochen": einstellungen["intervall_wochen"],
        "laufzeit_tage": round(laufzeit_sek() / 86400, 1),
        "notiz": notiz,
    })


def archiv_erinnerung():
    """Gesperrtes Archiv: bis zu 3 Erinnerungen, ca. alle 20 h. Zaehler wird
    zurueckgesetzt, sobald das Archiv wieder entsperrt ist."""
    zustand = _lese_json(ERINNERUNG_PATH, {"anzahl": 0, "zuletzt": 0})
    if not archiv_gesperrt():
        if zustand.get("anzahl"):
            _schreibe_json(ERINNERUNG_PATH, {"anzahl": 0, "zuletzt": 0})
        return
    if zustand.get("anzahl", 0) >= ERINNERUNG_MAX:
        return
    if time.time() - zustand.get("zuletzt", 0) < ERINNERUNG_MIN_ABSTAND_SEK:
        return
    melde("archiv_gesperrt")
    _schreibe_json(ERINNERUNG_PATH,
                   {"anzahl": zustand.get("anzahl", 0) + 1, "zuletzt": time.time()})


def tuer_offen():
    try:
        with open(TUER_STATUS_PATH) as f:
            return bool(json.load(f).get("tuer_offen"))
    except (OSError, json.JSONDecodeError):
        return False


def portal_vorgang_laeuft():
    """True, solange das Setup-Portal eine App aktualisiert/installiert oder
    sich selbst aktualisiert - ein Neustart wuerde das mittendrin abbrechen
    (halb kopierte App-Dateien). Fragt die Portal-Sperre und die zugehoerigen
    systemd-Units ab; ohne Portal (Sperrdatei fehlt) zaehlen nur die Units."""
    try:
        with open(PORTAL_SPERRE) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return True
    except OSError:
        pass
    erg = subprocess.run(["systemctl", "list-units", "--no-legend", "--plain",
                          "--state=activating,active,deactivating", *UPDATE_UNITS],
                         capture_output=True, text=True)
    return bool(erg.stdout.strip())


def system_beschaeftigt():
    """Grund, warum jetzt nicht gebootet/neu gestartet werden soll, sonst None."""
    if tuer_offen():
        return "Tür ist gerade offen"
    if portal_vorgang_laeuft():
        return "App-Update im Setup-Portal läuft gerade"
    if subprocess.run(["systemctl", "is-active", "--quiet", "honigbox-backup.service"]).returncode == 0:
        return "Backup läuft gerade"
    for muster in ("speicher_umschalten.sh", "foto.sh", "rpicam-still", "libcamera-still"):
        if subprocess.run(["pgrep", "-f", muster], capture_output=True).returncode == 0:
            return "Aufnahme oder Speicher-Umschaltung läuft gerade"
    return None


def ist_tmpfs(pfad):
    erg = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", pfad],
                         capture_output=True, text=True)
    return erg.stdout.strip() == "tmpfs"


def ram_fotos():
    try:
        return sorted(n for n in os.listdir(BILDER_DIR)
                      if not n.startswith(".")
                      and n.lower().endswith(ERLAUBTE_ENDUNGEN)
                      and os.path.isfile(os.path.join(BILDER_DIR, n)))
    except OSError:
        return []


def fotos_ins_archiv(dateien):
    """Verschiebt die Fotos ins Archiv. Liefert (anzahl, fehlergrund)."""
    if not dateien:
        return 0, None
    if not archiv_bereit():
        return 0, "Archiv ist gesperrt (Schlüssel fehlt)"
    bedarf = sum(os.path.getsize(os.path.join(BILDER_DIR, n)) for n in dateien)
    try:
        frei = shutil.disk_usage(ARCHIV_DIR).free
    except OSError:
        return 0, "Archiv nicht lesbar"
    if frei < bedarf * ARCHIV_PLATZ_PUFFER:
        return 0, "Archiv hat nicht genug freien Platz"
    verschoben = 0
    for name in dateien:
        try:
            shutil.move(os.path.join(BILDER_DIR, name), os.path.join(ARCHIV_DIR, name))
            verschoben += 1
            thumb = os.path.join(BILDER_DIR, ".thumbs", name)
            if os.path.isfile(thumb):
                os.remove(thumb)
        except OSError as e:
            return verschoben, f"Verschieben fehlgeschlagen ({e})"
    return verschoben, None


def dienste_neu_starten_noetig():
    """True, wenn seit dem letzten Start der HonigBox-Dienste Pakete per dpkg
    aktualisiert wurden (dpkg.log neuer als der Dienststart)."""
    try:
        erg = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value",
             "honigbox.service"], capture_output=True, text=True)
        start_mono = int(erg.stdout.strip()) / 1e6
        dpkg_mono = time.monotonic() - (time.time() - os.path.getmtime(DPKG_LOG))
    except (OSError, ValueError):
        return False
    return start_mono > 0 and dpkg_mono > start_mono


def markiere_auto_neustart(namen):
    """Hinterlegt fuer jeden Namen ("reboot" oder eine Unit) den Zeitpunkt
    eines automatischen Neustarts. Fehler sind egal - dann oeffnet sich im
    schlimmsten Fall das Reset-Fenster wie bisher."""
    try:
        os.makedirs(AUTO_NEUSTART_DIR, mode=0o755, exist_ok=True)
        for name in namen:
            with open(os.path.join(AUTO_NEUSTART_DIR, name), "w") as f:
                f.write(f"{int(time.time())}\n")
    except OSError:
        pass


def main():
    einstellungen = lade_einstellungen()
    grund = neustart_grund()
    nach_boot = laufzeit_sek() < NACH_BOOT_FENSTER_SEK

    if nach_boot:
        schreibe_status(einstellungen, grund)
        archiv_erinnerung()
        return 0

    archiv_erinnerung()
    if not einstellungen["auto_neustart"]:
        schreibe_status(einstellungen, grund, "Automatischer Neustart ist ausgeschaltet.")
        return 0

    mindest_laufzeit = einstellungen["intervall_wochen"] * 7 * 86400
    if laufzeit_sek() < mindest_laufzeit:
        schreibe_status(einstellungen, grund)
        return 0

    beschaeftigt = system_beschaeftigt()

    if grund:
        if beschaeftigt:
            schreibe_status(einstellungen, grund, f"Neustart zurückgestellt: {beschaeftigt}.")
            return 0
        verschoben, fehler = 0, None
        if ist_tmpfs(BILDER_DIR):
            verschoben, fehler = fotos_ins_archiv(ram_fotos())
        if fehler:
            schreibe_status(einstellungen, grund, f"Neustart abgebrochen: {fehler}.")
            melde("systemneustart_abgebrochen", f"Grund: {fehler}.")
            return 0
        zusatz = f"Grund: {grund}."
        if verschoben:
            zusatz += f" {verschoben} Foto(s) wurden vorher ins Archiv verschoben."
        schreibe_status(einstellungen, grund, "Neustart wird ausgeführt.")
        melde("systemneustart", zusatz)
        markiere_auto_neustart(["reboot"])
        subprocess.run(["systemctl", "reboot"], check=False)
        return 0

    if dienste_neu_starten_noetig():
        if beschaeftigt:
            schreibe_status(einstellungen, grund, f"Dienst-Neustart zurückgestellt: {beschaeftigt}.")
            return 0
        schreibe_status(einstellungen, grund, "Dienste wurden nach Paket-Updates neu gestartet.")
        # honigbox.sh ueberspringt dann die "Raspi wurde gestartet"-Meldung.
        try:
            open(STILLER_NEUSTART_MARKER, "w").close()
        except OSError:
            pass
        dienste = ["honigbox.service", "honigbox-galerie.service"]
        # Laeuft auf derselben Box auch die Imker-App (BeeTown), bekommt sie die
        # aktualisierten Bibliotheken ebenfalls.
        if os.path.exists("/etc/systemd/system/imkerei.service"):
            dienste.append("imkerei.service")
        markiere_auto_neustart(dienste)
        subprocess.run(["systemctl", "restart", *dienste], check=False)
        return 0

    schreibe_status(einstellungen, grund)
    return 0


if __name__ == "__main__":
    sys.exit(main())
