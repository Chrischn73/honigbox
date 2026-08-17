#!/bin/bash
# HonigBox - Phase C: LUKS-Container fuer das Foto-Archiv (immer) und die
# aktuellen Fotos im "Platte"-Speichermodus (nur dort, RAM-Disk-Modus ist
# schon von Natur aus fluechtig und braucht keine Verschluesselung) oeffnen
# oder, falls kein gueltiger/kein wiederhergestellter Container vorhanden
# ist, frisch anlegen. Laeuft als eigener systemd-Oneshot-Dienst
# (honigbox-archiv-entschluesseln.service) PARALLEL zu honigbox-galerie.service
# - bewusst OHNE Requires=/Before=-Abhaengigkeit dazwischen: die
# Schluesseleingabe passiert ueber die Route /archiv-schluessel in
# galerie_server.py selbst. Wuerde die Galerie erst NACH abgeschlossener
# Entschluesselung starten duerfen, koennte niemand je einen Schluessel
# eingeben - klassischer Deadlock. galerie_server.py prueft den Status
# stattdessen laufend selbst ueber die hier erzeugten Status-Dateien
# (siehe archiv_bereit() dort), nicht beim eigenen Prozessstart.
#
# Wartet auf einen vorhandenen, gueltigen Container OHNE Zeitlimit - man
# koennte laenger suchen muessen, wo der Schluessel gesichert wurde, das
# darf nicht dazu fuehren, dass der alte Bestand automatisch verworfen wird.
# Aufgeben (frischer Container) passiert deshalb NUR noch durch eine
# ausdrueckliche Nutzer-Entscheidung ("Nein, frisch anfangen" in der
# Web-Oberflaeche) - nie von selbst durch Zeitablauf. cryptsetup-Fehler
# (kaputter Container, Absturz, falscher eingegebener Schluessel) werden
# trotzdem GLEICH behandelt: alten Container verwerfen, frischen Container
# mit neuem Zufalls-Schluessel anlegen. Das Skript darf NIE mit einem Fehler
# enden, bevor eine Status-Datei existiert - sonst bliebe der Dienst
# dauerhaft "failed" und die Galerie haengt permanent im Schluessel-Gate,
# ohne dass je wieder etwas passiert. Bewusst KEIN Named-Pipe/FIFO fuer die
# Schluessel-Uebergabe (blockierendes open() ist eine bekannte Faustfalle,
# siehe Boot-Sicherheits-Review) - stattdessen einfaches Datei-Polling.
#
# WICHTIGER HINWEIS FUER EIN BESTEHENDES "Platte"-Deployment: existieren in
# BILDER_DIR/ARCHIV_DIR bereits Klartext-Fotos aus der Zeit VOR dieser
# Funktion, werden sie beim ersten Aktivieren durch den neu angelegten,
# leeren Container "ueberdeckt" (Standard-Mount-Verhalten) - nicht geloescht,
# aber unsichtbar/unerreichbar, bis der Container einmal ausgehaengt wird.
# Wer vorhandene Fotos behalten will, muss sie VOR dem ersten Update auf
# diese Version manuell sichern (z.B. per Backup-Funktion/USB-Kopie) und
# danach von Hand in den neuen, entschluesselten Ordner zuruecklegen.
set -uo pipefail   # bewusst OHNE 'set -e': jeder Schritt wird einzeln
                   # geprueft und behandelt, ein automatischer Skript-Abbruch
                   # waere hier genau das Risiko, das vermieden werden soll.

RUN_DIR="${HONIGBOX_RUN_DIR:-/run/honigbox}"
FOTOS_DIR="/opt/honigbox/fotos"
SPEICHER_EINSTELLUNGEN_DATEI="/opt/honigbox/einstellungen/.speicher-einstellungen.json"
CONTAINER_GROESSE_MB=512

mkdir -p "$RUN_DIR"
chown root:www-data "$RUN_DIR" 2>/dev/null || true
chmod 770 "$RUN_DIR" 2>/dev/null || true

log() { echo "archiv_entschluesseln: $1"; }

# container_neu_anlegen NAME CONTAINER_DATEI ZIEL_VERZEICHNIS
# Formatiert IMMER sofort frisch, OHNE auf eine Schluessel-Eingabe zu warten -
# fuer den interaktiven Fall (Nutzer schaltet live in der Web-Oberflaeche auf
# "Platte" um, siehe speicher_umschalten.sh). Ein Warten wie in
# container_oeffnen_oder_neu waere dort eine bis zu zweiminuetig haengende
# HTTP-Anfrage; ein evtl. vorhandener AELTERER Bilder-Container aus einer
# frueheren "Platte"-Sitzung wird dabei bewusst verworfen (die gerade aktuell
# sichtbaren Fotos werden unabhaengig davon per cp -a uebernommen, siehe
# speicher_umschalten.sh - hier geht nur ein noch aelterer, gerade nicht
# eingehaengter Bestand verloren, analog zum bereits akzeptierten
# "kein Schluessel beim Neustart -> alter Bestand weg"-Verhalten).
container_neu_anlegen() {
    local name="$1" datei="$2" ziel="$3"
    local status="$RUN_DIR/${name}-status" key="$RUN_DIR/${name}-key"
    local mapper="honigbox-${name}"

    rm -f "$RUN_DIR/${name}-eingabe"
    cryptsetup close "$mapper" >/dev/null 2>&1 || true
    umount "$ziel" >/dev/null 2>&1 || true
    rm -f "$datei"
    mkdir -p "$ziel"
    local neuer_schluessel
    neuer_schluessel="$(openssl rand -hex 32)"
    if ! fallocate -l "${CONTAINER_GROESSE_MB}M" "$datei" 2>/dev/null; then
        dd if=/dev/zero of="$datei" bs=1M count="$CONTAINER_GROESSE_MB" >/dev/null 2>&1
    fi
    if ! printf '%s' "$neuer_schluessel" | cryptsetup luksFormat --batch-mode --key-file=- "$datei" >/dev/null 2>&1; then
        log "$name: luksFormat fehlgeschlagen."
        echo "fehler" > "$status"
        return 1
    fi
    if ! printf '%s' "$neuer_schluessel" | cryptsetup open --key-file=- "$datei" "$mapper" >/dev/null 2>&1; then
        log "$name: cryptsetup open (frischer Container) fehlgeschlagen."
        echo "fehler" > "$status"
        return 1
    fi
    if ! mkfs.ext4 -q "/dev/mapper/$mapper" >/dev/null 2>&1; then
        log "$name: mkfs.ext4 fehlgeschlagen."
        cryptsetup close "$mapper" >/dev/null 2>&1 || true
        echo "fehler" > "$status"
        return 1
    fi
    if ! mount "/dev/mapper/$mapper" "$ziel" >/dev/null 2>&1; then
        log "$name: mount (frischer Container) fehlgeschlagen."
        cryptsetup close "$mapper" >/dev/null 2>&1 || true
        echo "fehler" > "$status"
        return 1
    fi
    chmod 777 "$ziel"
    printf '%s' "$neuer_schluessel" > "$key"
    chown root:www-data "$key" 2>/dev/null || true
    chmod 640 "$key" 2>/dev/null || true
    echo "fresh" > "$status"
    log "$name: frischer Container angelegt und eingehaengt."
    return 0
}

# container_schliessen NAME ZIEL_VERZEICHNIS
# Zum Aushaengen des Bilder-Containers beim Umschalten von "Platte" auf
# "RAM" (dort wird BILDER_DIR danach ein tmpfs, siehe speicher_umschalten.sh).
container_schliessen() {
    local name="$1" ziel="$2"
    umount "$ziel" >/dev/null 2>&1 || true
    cryptsetup close "honigbox-${name}" >/dev/null 2>&1 || true
    rm -f "$RUN_DIR/${name}-status" "$RUN_DIR/${name}-key" "$RUN_DIR/${name}-eingabe"
}

# container_oeffnen_oder_neu NAME CONTAINER_DATEI ZIEL_VERZEICHNIS
# Fuer den Boot-Fall: wartet bei einem vorhandenen, gueltigen Container OHNE
# Zeitlimit auf eine Schluessel-Eingabe (Datei-Polling statt FIFO - bewusst
# kein blockierendes open(), das ist eine bekannte Falle). Endet nur durch
# eine gueltige Eingabe oder ein ausdrueckliches "NEU" (Nutzer waehlt "frisch
# anfangen") - NIE von selbst durch Zeitablauf. NICHT fuer den interaktiven
# Fall gedacht (siehe container_neu_anlegen oben).
container_oeffnen_oder_neu() {
    local name="$1" datei="$2" ziel="$3"
    local eingabe="$RUN_DIR/${name}-eingabe" status="$RUN_DIR/${name}-status" key="$RUN_DIR/${name}-key"
    local mapper="honigbox-${name}"

    rm -f "$eingabe"
    echo "locked" > "$status"

    if [ ! -f "$datei" ] || ! cryptsetup isLuks "$datei" >/dev/null 2>&1; then
        log "$name: kein gueltiger Container vorhanden - lege neu an."
        container_neu_anlegen "$name" "$datei" "$ziel"
        return
    fi

    log "$name: warte ohne Zeitlimit auf Schluessel-Eingabe unter $eingabe ..."
    while [ ! -f "$eingabe" ]; do
        sleep 1
    done

    local eingabe_wert
    eingabe_wert="$(cat "$eingabe" 2>/dev/null || true)"
    rm -f "$eingabe"

    if [ "$eingabe_wert" = "NEU" ] || [ -z "$eingabe_wert" ]; then
        log "$name: Nutzer hat 'frisch anfangen' gewaehlt (oder leere Eingabe)."
        container_neu_anlegen "$name" "$datei" "$ziel"
        return
    fi

    mkdir -p "$ziel"
    if printf '%s' "$eingabe_wert" | cryptsetup open --key-file=- "$datei" "$mapper" >/dev/null 2>&1 \
        && mount "/dev/mapper/$mapper" "$ziel" >/dev/null 2>&1; then
        chmod 777 "$ziel"
        printf '%s' "$eingabe_wert" > "$key"
        chown root:www-data "$key" 2>/dev/null || true
        chmod 640 "$key" 2>/dev/null || true
        echo "unlocked" > "$status"
        log "$name: mit eingegebenem Schluessel wiederhergestellt."
    else
        log "$name: eingegebener Schluessel falsch oder Mount fehlgeschlagen - lege frischen Container an."
        cryptsetup close "$mapper" >/dev/null 2>&1 || true
        container_neu_anlegen "$name" "$datei" "$ziel"
    fi
}

# Wird dieses Skript per 'source' (aus speicher_umschalten.sh) eingebunden,
# nur die Funktion bereitstellen und hier nicht schon selbst loslegen.
if [ "${ARCHIV_ENTSCHLUESSELN_NUR_FUNKTION:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "FEHLER: muss als root laufen (per systemd)." >&2
    exit 1
fi

# Archiv ist IMMER persistent (nie eine RAM-Disk) - wird unabhaengig vom
# aktuellen Speichermodus fuer aktuelle Fotos immer behandelt.
container_oeffnen_oder_neu archiv "$FOTOS_DIR/archiv.img" "$FOTOS_DIR/Archiv"

# Der Bilder-Container fuer aktuelle Fotos wird nur im "Platte"-Modus
# gebraucht - im RAM-Modus ist BILDER_DIR ein tmpfs (siehe
# speicher_umschalten.sh) und braucht keine Verschluesselung.
SPEICHERORT="ram"
if [ -f "$SPEICHER_EINSTELLUNGEN_DATEI" ]; then
    SPEICHERORT="$(python3 -c "import json; print(json.load(open('$SPEICHER_EINSTELLUNGEN_DATEI')).get('speicherort','ram'))" 2>/dev/null || echo ram)"
fi
if [ "$SPEICHERORT" = "platte" ]; then
    container_oeffnen_oder_neu bilder "$FOTOS_DIR/bilder.img" "$FOTOS_DIR/Bilder"
else
    log "bilder: Speichermodus ist 'ram' - kein Container-Handling beim Boot noetig."
fi

exit 0
