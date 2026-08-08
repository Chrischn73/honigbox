#!/bin/bash
# Komplette Ersteinrichtung der HonigBox auf einem frisch installierten
# Raspberry Pi OS. Richtet ein: Tuerueberwachung (GPIO17), Foto-Kamera,
# Pushover-Benachrichtigungen, die Galerie-Weboberflaeche (Port 8090) und
# die Setup-/WLAN-Seite (Port 80). Portiert vom Schwesterprojekt "BeeTown"
# (setup/install.sh), auf HonigBox' tatsaechliche Anforderungen angepasst.
#
# Nutzung:
#   1. Diesen kompletten Projekt-Ordner auf den Pi kopieren, z. B. nach /opt/honigbox-setup
#   2. sudo bash /opt/honigbox-setup/setup/install.sh
#
# Mehrfach ausfuehrbar (idempotent) - z. B. nach einem Datei-Update einfach
# erneut laufen lassen. Bereits vorhandene Pushover-Zugangsdaten
# (pushover.conf) und Fotos werden dabei NICHT ueberschrieben.
#
# WICHTIG (an KI-Assistenten wie Claude UND Menschen): pi_setup_portal.py
# in diesem Ordner ist nur eine Deployment-KOPIE, NICHT App-eigener Code -
# NICHT direkt bearbeiten. Kanonische Quelle (dort bearbeiten, dann
# ./sync.sh dort ausfuehren):
#   /media/SSD/Sichern/claude/pi-setup-portal/pi_setup_portal.py
# Siehe den ausfuehrlichen Warnhinweis am Anfang von pi_setup_portal.py.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren: sudo bash $0"
    exit 1
fi

IS_PI=0
if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    IS_PI=1
fi
if [ "$IS_PI" -eq 1 ]; then
    echo "Erkannt: Raspberry Pi - volle Einrichtung inkl. WLAN, Kamera, GPIO, Hostname."
else
    echo "WARNUNG: Kein Raspberry Pi erkannt - HonigBox braucht GPIO/Kamera-Hardware,"
    echo "die Einrichtung laeuft trotzdem durch, wird aber vermutlich nicht funktionieren."
fi

# setup/install.sh liegt im Projekt-Ordner unter setup/ - PROJECT_DIR ist
# eine Ebene darueber, dort liegen honigbox.sh, foto.sh, static/ usw.
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SETUP_DIR")"
NEW_HOSTNAME="honigbox"
DEFAULT_PI_HOSTNAME="raspberrypi"

log() { echo; echo "==> $*"; }

# ---------------------------------------------------------------------------
log "Pruefe benoetigte Dateien in $PROJECT_DIR"
for f in honigbox.sh foto.sh send_pushover.sh galerie_server.py speicher_umschalten.sh static \
         honigbox.service honigbox-galerie.service; do
    if [ ! -e "$PROJECT_DIR/$f" ]; then
        echo "FEHLER: $PROJECT_DIR/$f fehlt. Wurde der komplette Projekt-Ordner uebertragen?"
        exit 1
    fi
done
for f in pi_setup_portal.py pi-setup-portal.sh pi-setup-portal.service regen-issue.sh \
         honigbox-backup.sh honigbox-backup-rotate.py honigbox-backup.service honigbox-backup.timer \
         honigbox-update-check.service honigbox-update-check.timer; do
    if [ ! -e "$SETUP_DIR/$f" ]; then
        echo "FEHLER: $SETUP_DIR/$f fehlt."
        exit 1
    fi
done

# ---------------------------------------------------------------------------
log "Warte auf Internetverbindung"
tries=0
while ! curl -s --max-time 5 -o /dev/null http://deb.debian.org; do
    tries=$((tries + 1))
    waited=$((tries * 10))
    echo "Kein Internet verfuegbar (Versuch $tries, seit ${waited}s wartend) - erneuter Versuch in 10 Sekunden..."
    sleep 10
done
echo "Internetverbindung erkannt. Warte kurz, bis DNS/Routing sich stabilisiert hat..."
sleep 5

# ---------------------------------------------------------------------------
log "Systemupdate (kann einige Minuten dauern)"
export DEBIAN_FRONTEND=noninteractive

apt_tries=0
until apt-get update; do
    apt_tries=$((apt_tries + 1))
    if [ "$apt_tries" -ge 5 ]; then
        echo "FEHLER: 'apt-get update' ist auch nach $apt_tries Versuchen fehlgeschlagen."
        exit 1
    fi
    echo "'apt-get update' fehlgeschlagen (Versuch $apt_tries) - erneuter Versuch in 10 Sekunden..."
    sleep 10
done

apt-get -y upgrade

# ---------------------------------------------------------------------------
log "Benoetigte Pakete installieren (GPIO, Kamera)"
apt-get install -y python3-gpiozero
# rpicam-apps heisst auf aelteren Raspberry Pi OS-Versionen noch libcamera-apps
apt-get install -y rpicam-apps || apt-get install -y libcamera-apps || \
    echo "WARNUNG: Konnte weder rpicam-apps noch libcamera-apps installieren - Kamera-Fotos werden nicht funktionieren."
# Fuer die optionale Funktion "Zu dunkle Fotos automatisch loeschen" - nicht
# kritisch, falls die Installation fehlschlaegt bleibt die Option in der
# Weboberflaeche einfach wirkungslos (foto.sh loescht dann nichts, siehe dort).
apt-get install -y python3-pil || \
    echo "WARNUNG: Konnte python3-pil nicht installieren - 'Zu dunkle Fotos automatisch löschen' wird dann nichts löschen."

# ---------------------------------------------------------------------------
log "SSH aktivieren"
systemctl enable --now ssh

if [ "$IS_PI" -eq 1 ]; then
    # -----------------------------------------------------------------------
    log "Zeitzone auf Europe/Berlin setzen und Zeit-Synchronisation aktivieren"
    timedatectl set-timezone Europe/Berlin
    timedatectl set-ntp true
    sleep 2
    timedatectl status | grep -E "Zeitzone|Time zone|synchronized|NTP" || true

    # -----------------------------------------------------------------------
    log "WLAN-Modul vorbereiten (rfkill / NetworkManager)"
    if command -v rfkill >/dev/null; then
        rfkill unblock wifi || true
    fi
    if command -v nmcli >/dev/null; then
        nmcli radio wifi on || true
        if systemctl is-active --quiet wpa_supplicant.service; then
            echo "Deaktiviere konkurrierenden wpa_supplicant.service (NetworkManager verwaltet WLAN selbst)."
            systemctl disable --now wpa_supplicant.service || true
            systemctl restart NetworkManager || true
        fi
    fi
fi

# ---------------------------------------------------------------------------
log "HonigBox-App-Verzeichnis einrichten (/opt/honigbox)"
mkdir -p /opt/honigbox/fotos/Bilder /opt/honigbox/fotos/Archiv /opt/honigbox/einstellungen
chmod 777 /opt/honigbox/fotos/Bilder /opt/honigbox/fotos/Archiv /opt/honigbox/einstellungen

# Kopiert nur, wenn Quelle und Ziel nicht bereits dieselbe Datei sind - falls
# das Projekt direkt nach /opt/honigbox kopiert und install.sh von dort aus
# gestartet wurde (statt aus einem separaten Staging-Ordner), sind Quelle und
# Ziel identisch. Ein normales "cp" wuerde das zu Recht verweigern (und mit
# set -e das ganze Skript abbrechen) - hier einfach ueberspringen.
sicher_kopieren() {
    local quelle="$1" ziel="$2"
    if [ -e "$ziel" ] && [ "$quelle" -ef "$ziel" ]; then
        return 0
    fi
    cp "$quelle" "$ziel"
}

# Gleiches Prinzip fuer Ordner - WICHTIG: kein "rm -rf" vor dem Kopieren, wenn
# Quelle und Ziel derselbe Ordner sind, sonst waere der Ordner (und damit die
# einzige Kopie der Dateien darin) geloescht, bevor ueberhaupt kopiert wird.
sicher_kopiere_ordner() {
    local quelle="$1" ziel="$2"
    if [ -d "$ziel" ] && [ "$quelle" -ef "$ziel" ]; then
        return 0
    fi
    rm -rf "$ziel"
    cp -rL "$quelle" "$ziel"
}

sicher_kopieren "$PROJECT_DIR/honigbox.sh" /opt/honigbox/honigbox.sh
sicher_kopieren "$PROJECT_DIR/foto.sh" /opt/honigbox/foto.sh
sicher_kopieren "$PROJECT_DIR/send_pushover.sh" /opt/honigbox/send_pushover.sh
sicher_kopieren "$PROJECT_DIR/galerie_server.py" /opt/honigbox/galerie_server.py
sicher_kopieren "$PROJECT_DIR/speicher_umschalten.sh" /opt/honigbox/speicher_umschalten.sh
sicher_kopiere_ordner "$PROJECT_DIR/static" /opt/honigbox/static

# Wichtig: die vier Shell-Scripte brauchen das Ausfuehrungsrecht, sonst
# bricht honigbox.sh beim Aufruf mit "Permission denied" ab (ist uns schon
# einmal so passiert).
chmod +x /opt/honigbox/honigbox.sh /opt/honigbox/foto.sh /opt/honigbox/send_pushover.sh /opt/honigbox/speicher_umschalten.sh

# Pushover-Zugangsdaten nur beim allerersten Einrichten anlegen, damit ein
# spaeter ueber die Web-UI gespeicherter echter Token bei einem erneuten
# Lauf (Update) nicht ueberschrieben wird.
if [ -f "$PROJECT_DIR/pushover.conf" ] && [ ! -f /opt/honigbox/pushover.conf ]; then
    cp "$PROJECT_DIR/pushover.conf" /opt/honigbox/pushover.conf
fi

cp "$PROJECT_DIR/honigbox.service" /etc/systemd/system/honigbox.service
cp "$PROJECT_DIR/honigbox-galerie.service" /etc/systemd/system/honigbox-galerie.service

# ---------------------------------------------------------------------------
log "Gemeinsames Pi-Setup-Portal einrichten (/opt/pi-setup-portal)"
# Seit Kurzem bringt HonigBox keine eigenstaendige Setup-Seite mehr mit,
# sondern registriert sich nur noch bei einem gemeinsamen Portal, das auch
# die Imker-App (BeeTown) mitnutzen kann, falls sie auf demselben Pi
# installiert ist bzw. wird - siehe apps.d/honigbox.json weiter unten.
mkdir -p /opt/pi-setup-portal/apps.d /opt/pi-setup-portal/issue.d \
         /opt/pi-setup-portal/state/honigbox /opt/pi-setup-portal/hilfe-bilder/_shared

# Portal-Code nur aktualisieren, wenn die mitgelieferte Version neuer (oder
# noch gar nicht installiert) ist - andernfalls koennte ein aelterer
# HonigBox-Stand eine von der Imker-App bereits aktualisierte, neuere
# Portal-Version wieder zurueckstufen (und umgekehrt).
BUNDLED_PORTAL_VERSION="$(grep -oP '^PORTAL_VERSION = "\K[^"]+' "$SETUP_DIR/pi_setup_portal.py")" || {
    echo "FEHLER: Konnte PORTAL_VERSION nicht aus $SETUP_DIR/pi_setup_portal.py auslesen."
    exit 1
}
INSTALLED_PORTAL_VERSION="$(grep -oP '^PORTAL_VERSION = "\K[^"]+' /opt/pi-setup-portal/pi_setup_portal.py 2>/dev/null || echo "0")"
# PORTAL_CODE_UPDATED steuert weiter unten, ob pi-setup-portal.service neu
# gestartet wird. Ein Neustart ist nur bei tatsaechlich neuem Code sinnvoll
# - dieser Dienst wird von der jeweils anderen App mitbenutzt, ein
# unnoetiger Neustart wuerde deren gerade laufende WLAN-/Backup-/Update-
# Vorgaenge mitten drin abbrechen. Deshalb bewusst DREI Faelle statt nur
# zwei: noch nicht installiert (deployen), gleiche Version (nichts tun -
# ansonsten wuerde jeder blosse Re-Lauf, auch ohne jede Codeaenderung,
# staendig neu starten), oder mitgelieferte Version wirklich neuer
# (deployen) bzw. aeltere installierte Version bereits neuer (nichts tun).
PORTAL_CODE_UPDATED=0
if [ ! -e /opt/pi-setup-portal/pi_setup_portal.py ]; then
    NEED_PORTAL_DEPLOY=1
elif [ "$INSTALLED_PORTAL_VERSION" = "$BUNDLED_PORTAL_VERSION" ]; then
    NEED_PORTAL_DEPLOY=0
elif [ "$(printf '%s\n%s\n' "$INSTALLED_PORTAL_VERSION" "$BUNDLED_PORTAL_VERSION" | sort -V | tail -1)" = "$BUNDLED_PORTAL_VERSION" ]; then
    NEED_PORTAL_DEPLOY=1
else
    NEED_PORTAL_DEPLOY=0
fi
if [ "$NEED_PORTAL_DEPLOY" -eq 1 ]; then
    cp "$SETUP_DIR/pi_setup_portal.py" /opt/pi-setup-portal/pi_setup_portal.py
    cp "$SETUP_DIR/pi-setup-portal.sh" /opt/pi-setup-portal/pi-setup-portal.sh
    chmod +x /opt/pi-setup-portal/pi-setup-portal.sh
    cp "$SETUP_DIR/pi-setup-portal.service" /etc/systemd/system/pi-setup-portal.service
    PORTAL_CODE_UPDATED=1
    echo "Portal-Code auf Version $BUNDLED_PORTAL_VERSION aktualisiert (vorher: $INSTALLED_PORTAL_VERSION)."
else
    echo "Portal-Code bereits auf Version $INSTALLED_PORTAL_VERSION (mitgeliefert: $BUNDLED_PORTAL_VERSION) - unveraendert gelassen."
fi
cp "$SETUP_DIR/regen-issue.sh" /opt/pi-setup-portal/regen-issue.sh
chmod +x /opt/pi-setup-portal/regen-issue.sh

# Migration: eine von einer frueheren install.sh-Version installierte
# eigenstaendige HonigBox-Setup-Seite ablösen. Eigene VPN-Screenshots des
# Nutzers werden dabei mitgenommen statt geloescht.
if [ -e /etc/systemd/system/honigbox-wifi-setup.service ]; then
    log "Alte eigenstaendige HonigBox-Setup-Seite ablösen (jetzt gemeinsames Pi-Setup-Portal)"
    systemctl disable --now honigbox-wifi-setup.service 2>/dev/null || true
    rm -f /etc/systemd/system/honigbox-wifi-setup.service /etc/default/honigbox-wifi-setup
fi
if [ -d /opt/honigbox-wifi-setup ]; then
    if [ -d /opt/honigbox-wifi-setup/hilfe-bilder ]; then
        cp -rn /opt/honigbox-wifi-setup/hilfe-bilder/. /opt/pi-setup-portal/hilfe-bilder/_shared/ 2>/dev/null || true
    fi
    # Screenshots sind jetzt migriert - der Rest (altes Portal-Skript,
    # update_check.json usw.) wird nicht mehr gebraucht.
    rm -rf /opt/honigbox-wifi-setup
fi

# ---------------------------------------------------------------------------
log "Backup & Update-Check einrichten"
mkdir -p /opt/backup-scripts /opt/backup
cp "$SETUP_DIR/honigbox-backup.sh" /opt/backup-scripts/honigbox-backup.sh
cp "$SETUP_DIR/honigbox-backup-rotate.py" /opt/backup-scripts/honigbox-backup-rotate.py
chmod +x /opt/backup-scripts/honigbox-backup.sh

cp "$SETUP_DIR/honigbox-backup.service" /etc/systemd/system/honigbox-backup.service
cp "$SETUP_DIR/honigbox-backup.timer" /etc/systemd/system/honigbox-backup.timer
cp "$SETUP_DIR/honigbox-update-check.service" /etc/systemd/system/honigbox-update-check.service
cp "$SETUP_DIR/honigbox-update-check.timer" /etc/systemd/system/honigbox-update-check.timer

# ---------------------------------------------------------------------------
log "Berechtigungen fuer den Galerie-Dienst (www-data)"
# www-data (siehe honigbox-galerie.service) braucht Kamera-Zugriff fuer den
# "Foto aufnehmen"-Button, und eine gezielte sudo-Freigabe fuer die
# Neustart-/Herunterfahren-/Dienste-neustart-Buttons in der Weboberflaeche.
usermod -aG video www-data

SUDOERS_FILE=/etc/sudoers.d/honigbox-galerie
SUDOERS_LINE='www-data ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart honigbox.service, /usr/bin/systemctl restart honigbox-galerie.service, /usr/bin/systemd-run --scope --collect -- /opt/honigbox/speicher_umschalten.sh'
echo "$SUDOERS_LINE" > "$SUDOERS_FILE.tmp"
if visudo -c -f "$SUDOERS_FILE.tmp" >/dev/null 2>&1; then
    mv "$SUDOERS_FILE.tmp" "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    echo "sudoers-Regel eingerichtet: $SUDOERS_FILE"
else
    rm -f "$SUDOERS_FILE.tmp"
    echo "FEHLER: sudoers-Regel ungueltig, wurde NICHT installiert. Neustart-Buttons funktionieren dann nicht."
fi

# ---------------------------------------------------------------------------
log "Pruefe Port 8090 fuer die Galerie"
GALERIE_APP_PID="$(systemctl show -p MainPID --value honigbox-galerie.service 2>/dev/null || echo 0)"
PORT8090_PID="$(ss -H -ltnp "sport = :8090" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
GALERIE_PORT=8090
if [ -n "$PORT8090_PID" ] && [ "$PORT8090_PID" != "$GALERIE_APP_PID" ]; then
    GALERIE_PORT=8091
    echo "Port 8090 ist von einem anderen Prozess belegt (PID $PORT8090_PID) - Galerie laeuft stattdessen auf Port $GALERIE_PORT."
    sed -i "s/^Environment=GALERIE_PORT=.*/Environment=GALERIE_PORT=$GALERIE_PORT/" /etc/systemd/system/honigbox-galerie.service
    # Eigene Datei nur fuer das Pi-Setup-Portal, damit es den tatsaechlichen
    # Port kennt, ohne honigbox-galerie.service selbst anfassen zu muessen.
    echo "HONIGBOX_GALERIE_PORT=$GALERIE_PORT" > /etc/default/honigbox-galerie
else
    echo "Port 8090 ist frei (oder bereits durch die Galerie selbst belegt)."
    rm -f /etc/default/honigbox-galerie
fi

log "Pruefe Port 80 fuer das gemeinsame Pi-Setup-Portal"
# Port 80 gehoert jetzt dem gemeinsamen pi-setup-portal.service statt einem
# HonigBox-eigenen Dienst - dieselbe Pruefung fuehrt die Imker-App in ihrem
# eigenen install.sh aus, falls sie auf demselben Pi installiert ist/wird.
SETUP_PID="$(systemctl show -p MainPID --value pi-setup-portal.service 2>/dev/null || echo 0)"
PORT80_PID="$(ss -H -ltnp "sport = :80" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
if [ -z "$PORT80_PID" ] || { [ "$PORT80_PID" = "$SETUP_PID" ] && [ "$SETUP_PID" != "0" ]; }; then
    LANDING_PORT=80
    echo "Port 80 ist frei (oder bereits durch das Pi-Setup-Portal selbst belegt) - Portal laeuft dort."
    rm -f /etc/default/pi-setup-portal
else
    LANDING_PORT=8082
    echo "Port 80 ist von einem anderen Prozess belegt (PID $PORT80_PID) - Pi-Setup-Portal laeuft stattdessen auf Port $LANDING_PORT."
    echo "PI_SETUP_LANDING_PORT=$LANDING_PORT" > /etc/default/pi-setup-portal
fi

# ---------------------------------------------------------------------------
log "HonigBox im gemeinsamen Pi-Setup-Portal registrieren"
cat > /opt/pi-setup-portal/apps.d/honigbox.json << JSONEOF
{
  "id": "honigbox",
  "label": "HonigBox Galerie",
  "emoji": "🍯",
  "app_port_default": 8090,
  "app_port_env_file": "/etc/default/honigbox-galerie",
  "app_port_env_var": "HONIGBOX_GALERIE_PORT",
  "backup": {
    "script": "/opt/backup-scripts/honigbox-backup.sh",
    "prefix": "honigbox-backup",
    "restore_data_prefix": "honigbox/fotos",
    "restore_target_dir": "/opt/honigbox/fotos",
    "restore_chmod": "777",
    "restore_stop_services": ["honigbox.service", "honigbox-galerie.service"],
    "restore_start_services": ["honigbox.service", "honigbox-galerie.service"],
    "restored_label": "Fotos"
  },
  "update": {
    "github_repo": "Chrischn73/honigbox",
    "version_file": "/opt/honigbox/static/app.js",
    "version_regex": "APP_VERSION = '([^']+)'",
    "file_map": [
      {"src": "honigbox.sh", "dest": "/opt/honigbox/honigbox.sh", "mode": "0755"},
      {"src": "foto.sh", "dest": "/opt/honigbox/foto.sh", "mode": "0755"},
      {"src": "send_pushover.sh", "dest": "/opt/honigbox/send_pushover.sh", "mode": "0755"},
      {"src": "galerie_server.py", "dest": "/opt/honigbox/galerie_server.py", "mode": "0644", "chown": "www-data:www-data"},
      {"src": "speicher_umschalten.sh", "dest": "/opt/honigbox/speicher_umschalten.sh", "mode": "0755"},
      {"src": "static", "dest": "/opt/honigbox/static", "mode": "dir", "chown": "www-data:www-data"},
      {"src": "honigbox.service", "dest": "/etc/systemd/system/honigbox.service", "mode": "0644"},
      {"src": "honigbox-galerie.service", "dest": "/etc/systemd/system/honigbox-galerie.service", "mode": "0644"}
    ],
    "services_to_restart": ["honigbox.service", "honigbox-galerie.service"]
  }
}
JSONEOF

# ---------------------------------------------------------------------------
log "Speicherort fuer Fotos einrichten (Standard: RAM-Speicher)"
# Nur beim allerersten Einrichten einen Standard schreiben - ein spaeter ueber
# die Web-UI gewaehlter Speicherort (z.B. bewusst auf "platte" umgestellt)
# soll bei einem erneuten install.sh-Lauf (Update) nicht ueberschrieben werden.
SPEICHER_EINSTELLUNGEN_DATEI=/opt/honigbox/einstellungen/.speicher-einstellungen.json
if [ ! -f "$SPEICHER_EINSTELLUNGEN_DATEI" ]; then
    EMPFOHLENE_RAM_GROESSE_MB=$(python3 -c "
try:
    with open('/proc/meminfo') as f:
        for zeile in f:
            if zeile.startswith('MemTotal:'):
                gesamt_mb = int(zeile.split()[1]) / 1024
                print(int(max(64, min(512, gesamt_mb * 0.20))))
                break
except OSError:
    print(128)
")
    cat > "$SPEICHER_EINSTELLUNGEN_DATEI" << EOF
{"speicherort": "ram", "ram_groesse_mb": $EMPFOHLENE_RAM_GROESSE_MB}
EOF
    # Direkt anwenden (nicht ueber sudo/systemd-run wie von der Web-UI aus -
    # dieses Skript laeuft hier schon als root, und honigbox.service/
    # honigbox-galerie.service laufen noch gar nicht, es gibt also nichts,
    # das sich durch einen "systemctl stop" seiner selbst beraubt).
fi

# Unabhaengig davon, ob die Einstellungsdatei gerade eben neu geschrieben
# wurde oder schon laenger existiert: das Script hier IMMER (auch bei einem
# erneuten install.sh-Lauf/Update) direkt aufrufen und den tatsaechlichen
# Mount-Zustand mit der gespeicherten Einstellung abgleichen. Das Script ist
# idempotent (macht nichts, wenn schon alles passt) - schliesst aber die
# Luecke, falls eine fruehere Anwendung (z.B. per Web-UI-Button, als die
# sudoers-Regel noch fehlte) lautlos fehlgeschlagen ist und Einstellung und
# Wirklichkeit seitdem auseinanderlaufen.
bash /opt/honigbox/speicher_umschalten.sh || \
    echo "WARNUNG: Speicherort konnte nicht wie eingestellt eingerichtet werden - Fotos landen vorerst auf der SD-Karte, in den Einstellungen unter 'Speicherort' laesst sich das pruefen/erneut versuchen."

# ---------------------------------------------------------------------------
log "systemd-Dienste aktivieren"
systemctl daemon-reload
systemctl enable --now honigbox.service
systemctl restart honigbox.service
systemctl enable --now honigbox-galerie.service
systemctl restart honigbox-galerie.service
systemctl enable --now pi-setup-portal.service
# Nur neu starten, wenn oben tatsaechlich neuer Portal-Code deployt wurde -
# sonst wuerde jeder blosse Re-Lauf von install.sh die von der Imker-App
# mitgenutzte Setup-Seite unnoetig durchstarten (siehe Kommentar beim
# Versionsvergleich weiter oben). "enable --now" allein startet den Dienst
# nur, falls er noch gar nicht laeuft - laesst einen bereits laufenden,
# unveraenderten Dienst in Ruhe.
if [ "$PORTAL_CODE_UPDATED" -eq 1 ]; then
    systemctl restart pi-setup-portal.service
fi
systemctl enable --now honigbox-backup.timer
systemctl enable --now honigbox-update-check.timer
systemctl start honigbox-update-check.service || true

is_wifi_connected() {
    nmcli -t -f DEVICE,STATE device status 2>/dev/null \
        | awk -F: '$1=="wlan0" && $2=="connected" {found=1} END{exit !found}'
}

# ---------------------------------------------------------------------------
log "Status"
# "|| true" ist hier wichtig: "systemctl status" gibt einen Exit-Code != 0
# zurueck, sobald ein Dienst nicht "active (running)" ist (z. B. noch beim
# Start, oder crash-loopend, weil der Tuerkontaktschalter noch nicht
# angeschlossen ist - genau der Fall, den das Skript weiter unten selbst
# als moeglich beschreibt). Ohne "|| true" wuerde "set -e" das Skript hier
# abbrechen, noch VOR dem Boot-Bildschirm und den Abschluss-Hinweisen.
systemctl --no-pager status honigbox.service | head -5 || true
echo
systemctl --no-pager status honigbox-galerie.service | head -5 || true
echo
systemctl --no-pager status pi-setup-portal.service | head -5 || true

SETUP_URL="http://$(hostname).local"
[ "$LANDING_PORT" -ne 80 ] && SETUP_URL="$SETUP_URL:$LANDING_PORT"

if [ "$IS_PI" -eq 1 ]; then
    # -----------------------------------------------------------------------
    # Hostname-Entscheidung ZUERST, danach erst den Boot-Bildschirm
    # schreiben - sonst landet die alte/neue Hostname-Variante inkonsistent
    # in /etc/issue (z. B. "raspberrypi.local", obwohl der Pi gleich auf
    # "honigbox" umbenannt wird und danach neu startet).
    CURRENT_HOSTNAME="$(hostname)"
    EFFECTIVE_HOSTNAME="$CURRENT_HOSTNAME"
    HOSTNAME_CHANGED=0
    if [ "$CURRENT_HOSTNAME" = "$DEFAULT_PI_HOSTNAME" ]; then
        log "Hostname aendern zu '$NEW_HOSTNAME'"
        raspi-config nonint do_hostname "$NEW_HOSTNAME"
        EFFECTIVE_HOSTNAME="$NEW_HOSTNAME"
        HOSTNAME_CHANGED=1
        SETUP_URL="http://$NEW_HOSTNAME.local"
        [ "$LANDING_PORT" -ne 80 ] && SETUP_URL="$SETUP_URL:$LANDING_PORT"
    else
        log "Hostname bleibt unveraendert ('$CURRENT_HOSTNAME' ist nicht mehr der Pi-Standard '$DEFAULT_PI_HOSTNAME')"
    fi

    # -----------------------------------------------------------------------
    log "Boot-Bildschirm einrichten (/etc/issue)"
    # /etc/issue wird aus Fragmenten zusammengesetzt (siehe regen-issue.sh) -
    # "00-" ist die gemeinsame Setup-URL (identischer Inhalt, egal welche
    # App sie zuletzt geschrieben hat), "10-" ist die HonigBox-eigene Zeile.
    # Beide nutzen jetzt $EFFECTIVE_HOSTNAME/das schon aktualisierte
    # $SETUP_URL statt der Werte von VOR der Hostname-Entscheidung.
    cat > /opt/pi-setup-portal/issue.d/00-setup-url.txt << EOF
   Setup / WLAN:        $SETUP_URL
EOF
    cat > /opt/pi-setup-portal/issue.d/10-honigbox.txt << EOF
   HonigBox Galerie:    http://$EFFECTIVE_HOSTNAME.local:$GALERIE_PORT
EOF
    /opt/pi-setup-portal/regen-issue.sh

    echo
    echo "======================================================================"
    echo " Setup / WLAN:        $SETUP_URL"
    echo " HonigBox Galerie:    http://$EFFECTIVE_HOSTNAME.local:$GALERIE_PORT"
    echo " WLAN-Einstellungen:  $SETUP_URL/wifi (immer erreichbar)"
    echo " Backups:             $SETUP_URL/backup"
    echo " Update:              $SETUP_URL/update"
    echo " Hilfe:               $SETUP_URL/hilfe"
    echo "======================================================================"
    if ! is_wifi_connected; then
        echo
        echo " Noch kein WLAN eingerichtet:"
        echo " 1. Pi per Netzwerkkabel am Router/Switch angeschlossen lassen"
        if [ "$HOSTNAME_CHANGED" -eq 1 ]; then
            echo " 2. Nach dem gleich folgenden Neustart im Browser aufrufen:"
        else
            echo " 2. Im Browser aufrufen:"
        fi
        echo "        $SETUP_URL  (dort auf 'WLAN-Einstellungen' tippen)"
        echo " 3. WLAN auswaehlen bzw. SSID eingeben, Passwort eintragen, auf 'Verbinden' tippen"
        echo " 4. Sobald die Verbindung steht: Netzwerkkabel entfernen"
    fi
    echo
    echo " Nicht vergessen: Der Tuerkontaktschalter muss noch an GPIO17 (Pin 11)"
    echo " und einem GND-Pin (z. B. Pin 9) angeschlossen werden, falls noch nicht"
    echo " geschehen. Kontakt geschlossen = Tuer zu, Kontakt offen = Tuer auf."
    echo "======================================================================"
    if [ "$HOSTNAME_CHANGED" -eq 1 ]; then
        echo
        echo "Neustart in 5 Sekunden, um den neuen Hostnamen zu uebernehmen..."
        echo "Danach per SSH neu verbinden: ssh <benutzer>@$EFFECTIVE_HOSTNAME.local"
        sleep 5
        reboot
    else
        echo
        echo "Hostname war bereits angepasst - kein Neustart erforderlich. Fertig."
    fi
else
    echo
    echo "======================================================================"
    echo " Setup / WLAN:      $SETUP_URL"
    echo " HonigBox Galerie:  http://$(hostname).local:$GALERIE_PORT"
    echo "======================================================================"
    echo " Fertig - kein Neustart erforderlich."
fi
