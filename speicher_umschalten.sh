#!/bin/bash
# Setzt die in der Weboberflaeche gewaehlte Speicherort-Einstellung
# (RAM-Disk/tmpfs oder SD-Karte) tatsaechlich um. Wird von galerie_server.py
# (www-data) ueber eine gezielte sudoers-Freigabe aufgerufen - siehe
# install.sh. Absichtlich OHNE Kommandozeilen-Argumente: liest die
# gewuenschte Konfiguration selbst aus der Einstellungsdatei, damit die
# sudoers-Regel exakt diesen einen parameterlosen Aufruf erlauben kann
# (keine Befehls-Injektion ueber Argumente moeglich).
#
# WICHTIG: wird von galerie_server.py ueber
#   sudo -n systemd-run --scope --collect -- speicher_umschalten.sh
# aufgerufen statt per einfachem sudo - das loest dieses Script aus der
# Cgroup von honigbox-galerie.service (dem aufrufenden Prozess!) heraus,
# BEVOR unten "systemctl stop ... honigbox-galerie.service" laeuft. Ohne das
# wuerde dieser Stop-Befehl das Script selbst mitten in der Migration
# abwuergen (systemd killt beim Stoppen eines Dienstes alle Prozesse in
# dessen Cgroup, und ein per subprocess+sudo gestartetes Kind bliebe ohne
# systemd-run in genau dieser Cgroup).
set -euo pipefail

BILDER_DIR="/opt/honigbox/fotos/Bilder"
BILDER_CONTAINER="/opt/honigbox/fotos/bilder.img"
EINSTELLUNGEN_DATEI="/opt/honigbox/einstellungen/.speicher-einstellungen.json"
FSTAB_MARKER="# HonigBox-RAM-Disk (automatisch verwaltet, siehe speicher_umschalten.sh - nicht von Hand bearbeiten)"

# Phase C: LUKS-Verschluesselung der aktuellen Fotos im "Platte"-Modus - stellt
# container_neu_anlegen()/container_schliessen() bereit (siehe dort fuer die
# Begruendung, warum beim LIVE-Umschalten NICHT auf eine Schluessel-Eingabe
# gewartet wird, anders als beim Booten). ACHTUNG: das gesourcte Skript setzt
# selbst 'set -uo pipefail' (ohne -e) - direkt danach wieder auf
# 'set -euo pipefail' zurueckstellen, sonst wuerde das restliche Skript hier
# unten sein bisheriges Abbruchverhalten bei Fehlern stillschweigend verlieren.
ARCHIV_ENTSCHLUESSELN_NUR_FUNKTION=1 source /opt/honigbox/archiv_entschluesseln.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "FEHLER: muss als root laufen (per sudo)." >&2
    exit 1
fi

if [ ! -f "$EINSTELLUNGEN_DATEI" ]; then
    echo "Keine Speicher-Einstellungen gefunden - nichts zu tun."
    exit 0
fi

SPEICHERORT=$(python3 -c "import json; print(json.load(open('$EINSTELLUNGEN_DATEI')).get('speicherort','ram'))")
GROESSE_MB=$(python3 -c "import json; print(int(json.load(open('$EINSTELLUNGEN_DATEI')).get('ram_groesse_mb',128)))")

if [ "$SPEICHERORT" != "ram" ] && [ "$SPEICHERORT" != "platte" ]; then
    echo "FEHLER: unbekannter Speicherort '$SPEICHERORT'." >&2
    exit 1
fi
if ! [[ "$GROESSE_MB" =~ ^[0-9]+$ ]] || [ "$GROESSE_MB" -lt 32 ] || [ "$GROESSE_MB" -gt 2048 ]; then
    echo "FEHLER: ungueltige Groesse '$GROESSE_MB'." >&2
    exit 1
fi

ist_tmpfs() {
    mountpoint -q "$BILDER_DIR" 2>/dev/null && \
        findmnt -n -o FSTYPE --target "$BILDER_DIR" 2>/dev/null | grep -q '^tmpfs$'
}

fstab_eintrag_entfernen() {
    # "|| true" noetig: grep -v gibt Exit-Code 1 zurueck, wenn ALLE Zeilen
    # rausgefiltert werden (leere Ausgabe ist hier ein legitimes Ergebnis,
    # z.B. wenn fstab nur unsere eigenen Zeilen enthielte) - ohne "|| true"
    # wuerde das unter "set -e -o pipefail" das Script genau hier abbrechen.
    if [ -f /etc/fstab ] && grep -qF "$FSTAB_MARKER" /etc/fstab; then
        { grep -vF "$FSTAB_MARKER" /etc/fstab | grep -v "^tmpfs[[:space:]]$BILDER_DIR[[:space:]]" \
            > /etc/fstab.neu; } || true
        mv /etc/fstab.neu /etc/fstab
    fi
}

fstab_eintrag_setzen() {
    fstab_eintrag_entfernen
    {
        echo "$FSTAB_MARKER"
        echo "tmpfs $BILDER_DIR tmpfs defaults,size=${GROESSE_MB}M,mode=0777 0 0"
    } >> /etc/fstab
    systemctl daemon-reload
}

if [ "$SPEICHERORT" = "ram" ]; then
    if ist_tmpfs; then
        # Bereits RAM-Disk - Groesse per Remount anpassen (online, kein
        # Datenverlust) statt komplett neu zu mounten.
        AKTUELL_MB=$(du -sm "$BILDER_DIR" 2>/dev/null | cut -f1 || echo 0)
        if [ "$AKTUELL_MB" -gt "$GROESSE_MB" ]; then
            echo "FEHLER: aktuelle Belegung (${AKTUELL_MB} MB) ist groesser als die neue Groesse (${GROESSE_MB} MB) - bitte zuerst Fotos loeschen/archivieren oder eine groessere Groesse waehlen." >&2
            exit 1
        fi
        mount -o remount,size=${GROESSE_MB}M "$BILDER_DIR"
        fstab_eintrag_setzen
        echo "RAM-Speicher-Groesse auf ${GROESSE_MB} MB angepasst."
    else
        systemctl stop honigbox.service honigbox-galerie.service || true
        # Sicherheitsnetz: schlaegt IRGENDEIN Schritt bis zum naechsten
        # 'trap - EXIT' fehl (z.B. ein kuenftiger cryptsetup-Aufruf), sollen
        # die Dienste trotzdem nicht dauerhaft gestoppt bleiben - das war vor
        # Phase C kein Problem (kein fehlbarer Schritt dazwischen), ist es
        # mit dem neuen Bilder-Container-Handling aber potenziell schon.
        trap 'systemctl start honigbox.service honigbox-galerie.service 2>/dev/null || true' EXIT
        mkdir -p "$BILDER_DIR"
        TEMP="$(mktemp -d)"
        cp -a "$BILDER_DIR"/. "$TEMP"/ 2>/dev/null || true
        AKTUELL_MB=$(du -sm "$TEMP" 2>/dev/null | cut -f1 || echo 0)
        if [ "$AKTUELL_MB" -gt "$GROESSE_MB" ]; then
            echo "FEHLER: vorhandene Fotos (${AKTUELL_MB} MB) passen nicht in die gewaehlte RAM-Groesse (${GROESSE_MB} MB) - bitte zuerst Fotos loeschen/archivieren oder eine groessere Groesse waehlen." >&2
            rm -rf "$TEMP"
            exit 1
        fi
        # Bilder-Container schliessen, FALLS gerade durch den "Platte"-Modus
        # offen (Phase C) - harmlos/no-op, falls BILDER_DIR nur ein normaler
        # Ordner war. Muss VOR dem tmpfs-Mount an derselben Stelle passieren.
        container_schliessen bilder "$BILDER_DIR"
        mount -t tmpfs -o "size=${GROESSE_MB}M,mode=0777" tmpfs "$BILDER_DIR"
        cp -a "$TEMP"/. "$BILDER_DIR"/ 2>/dev/null || true
        chmod 777 "$BILDER_DIR"
        rm -rf "$TEMP"
        fstab_eintrag_setzen
        systemctl start honigbox.service honigbox-galerie.service
        trap - EXIT
        echo "Auf RAM-Speicher (${GROESSE_MB} MB) umgeschaltet."
    fi
else
    if ist_tmpfs; then
        systemctl stop honigbox.service honigbox-galerie.service || true
        trap 'systemctl start honigbox.service honigbox-galerie.service 2>/dev/null || true' EXIT
        TEMP="$(mktemp -d)"
        cp -a "$BILDER_DIR"/. "$TEMP"/ 2>/dev/null || true
        umount "$BILDER_DIR"
        fstab_eintrag_entfernen
        systemctl daemon-reload
        # Verschluesselten Bilder-Container frisch anlegen (Phase C) - siehe
        # container_neu_anlegen() in archiv_entschluesseln.sh: wartet bewusst
        # NICHT auf eine Schluessel-Eingabe (das waere hier eine bis zu
        # zweiminuetig haengende HTTP-Anfrage), ein evtl. aelterer Bestand aus
        # einer frueheren "Platte"-Sitzung geht dabei verloren. Die gerade
        # aktuell sichtbaren Fotos (in $TEMP zwischengesichert) bleiben
        # unabhaengig davon erhalten und werden gleich zurueckkopiert.
        if ! container_neu_anlegen bilder "$BILDER_CONTAINER" "$BILDER_DIR"; then
            echo "FEHLER: Verschluesselter Bilder-Container konnte nicht angelegt werden - Fotos liegen unverschluesselt." >&2
            mkdir -p "$BILDER_DIR"
        fi
        cp -a "$TEMP"/. "$BILDER_DIR"/ 2>/dev/null || true
        chmod 777 "$BILDER_DIR"
        rm -rf "$TEMP"
        systemctl start honigbox.service honigbox-galerie.service
        trap - EXIT
        echo "Auf SD-Karte umgeschaltet (verschlüsselt)."
    else
        echo "Liegt bereits auf der SD-Karte - nichts zu tun."
    fi
fi
