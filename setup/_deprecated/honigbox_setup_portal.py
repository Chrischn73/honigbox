#!/usr/bin/env python3
"""
Dauerhaft laufender Webserver fuer die HonigBox-Setup-Seite (ein einziger
Port, http://<hostname>.local), faellt auf einen Ausweich-Port aus
(HONIGBOX_LANDING_PORT), falls 80 beim Einrichten schon belegt war.

- Startseite: Links zur Galerie, Backups, Update, WLAN-Einstellungen
  (Letzteres + Neustart/Herunterfahren nur auf einem echten Raspberry Pi
  sichtbar bzw. erreichbar, siehe IS_PI)
- /wifi: WLAN einrichten/wechseln/trennen
- /backup: Backups erstellen/wiederherstellen/herunterladen, USB-Stick
  einrichten
- /update: Version pruefen/aktualisieren/zurueckwechseln, automatische
  Updates (Quelle: GitHub-Releases von GITHUB_REPO)

Portiert vom Schwesterprojekt "BeeTown" (imkerei_wifi_portal.py), Pfade und
Branding auf HonigBox angepasst. Laeuft permanent (nicht nur beim
Ersteinrichten), unabhaengig davon, ob gerade WLAN verbunden ist oder nicht -
Kabel oder WLAN, beides geht. Nur Python-Standardbibliothek.

Verhalten:
- GET  /                    -> Startseite mit Links, IPs, System-Buttons
- GET  /tipps               -> Handy-Tipps ("Zum Home-Bildschirm")
- GET  /hilfe               -> Hilfe-Uebersicht
- GET  /hilfe/vpn           -> Schritt-fuer-Schritt WireGuard/Fritzbox-VPN-Anleitung
- GET  /hilfe/bilder/<datei> -> vom Nutzer selbst abgelegte Screenshots dafuer
- GET  /wifi                -> WLAN-Formular - nur auf einem echten Pi (404 sonst)
- POST /wifi/connect, /wifi/disconnect
- GET  /wifi/status, /wifi/networks
- GET  /backup              -> Backup-Liste (SD-Karte + ggf. USB-Stick)
- GET  /backup/restore      -> Backup wiederherstellen (Auswahl oder PC-Upload)
- GET  /backup/downloads    -> Backup herunterladen
- GET  /backup/download/<local|usb>/<f>
- GET  /backup/usb/format-status
- GET  /update              -> installierte/neueste Version, Update-Button
- GET  /update/status
- POST /backup/create, /backup/restore, /backup/restore-upload,
  /backup/settings, /backup/usb/format, /backup/usb/mount, /backup/usb/eject,
  /update/run, /update/switch, /update/settings
- POST /system/reboot, /system/shutdown -> Neustart/Shutdown (nur Pi)
"""
import html
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

# Wird von do_POST() waehrend des Verbindungsversuchs aktualisiert und von
# GET /wifi/status abgefragt (Polling von der "Verbinde..."-Seite aus).
CONN_STATE = {"done": False, "ok": None, "detail": None}

# Wird waehrend des (langwierigen) Formatierens eines USB-Sticks aktualisiert
# und von GET /backup/usb/format-status abgefragt.
FORMAT_STATE = {"done": True, "ok": None, "detail": None}

# Wird waehrend des (langwierigen) App-Updates aktualisiert und von
# GET /update/status abgefragt.
UPDATE_STATE = {"done": True, "ok": None, "detail": None}

HOST = "0.0.0.0"
PORT_LANDING = int(os.environ.get("HONIGBOX_LANDING_PORT", "80"))
GALERIE_PORT = int(os.environ.get("HONIGBOX_GALERIE_PORT", "8090"))


def _detect_is_pi():
    """True nur auf einem echten Raspberry Pi (per Device-Tree-Modellname) -
    steuert, ob WLAN-Einstellungen und Neustart/Herunterfahren ueberhaupt
    angeboten werden. Backup und Update bleiben auch ohne Pi nutzbar."""
    try:
        with open("/proc/device-tree/model") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


IS_PI = _detect_is_pi()

HILFE_IMAGE_DIR = "/opt/honigbox-wifi-setup/hilfe-bilder"
HILFE_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(png|jpg|jpeg)$", re.IGNORECASE)

BACKUP_DIR = "/opt/backup"
BACKUP_SCRIPT = "/opt/backup-scripts/honigbox-backup.sh"
# Ein Backup-Archiv enthaelt den kompletten /opt/honigbox-Ordner (Code +
# Fotos/Einstellungen), aber wiederhergestellt wird bewusst NUR dieser
# Unterordner - der App-Code kommt ja ueber Updates/eine Neuinstallation,
# nicht aus einem (evtl. veralteten) Backup.
BACKUP_DATA_PREFIX = "honigbox/fotos"
BACKUP_NAME_RE = re.compile(r"^honigbox-backup-[0-9-]+\.tar\.gz$")
BACKUP_CONFIG_PATH = "/opt/backup-scripts/backup.conf"
MIN_MAX_BACKUPS = 20
DEFAULT_MAX_BACKUPS = 30

USB_MOUNT = "/mnt/backup-usb"

# Oeffentliches GitHub-Repo als Update-Quelle. Ein GitHub-Release-Tag "vX.Y.Z"
# muss mit APP_VERSION in static/app.js uebereinstimmen, damit der
# Versionsvergleich funktioniert.
GITHUB_REPO = "Chrischn73/honigbox"
GITHUB_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_CHECK_STATE_PATH = "/opt/honigbox-wifi-setup/update_check.json"
AUTO_UPDATE_CONFIG_PATH = "/opt/honigbox-wifi-setup/update.conf"
UPDATE_RESULT_PERSIST_PATH = "/opt/honigbox-wifi-setup/last_update_result.json"

STYLE = """
  :root {{
    --bg: #faf6ee; --fg: #241f17; --muted: #6e6353; --box-bg: #ece3d2;
    --msg-ok-bg: #dfd; --msg-err-bg: #fdd;
    --input-bg: #fff; --input-border: #ece3d2;
    --btn-bg: #d98e04; --btn-fg: #fff; --btn-active: #b87503;
    --danger-bg: #c92a2a; --danger-fg: #fff; --danger-active: #a02020;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #15120d; --fg: #efe7d8; --muted: #b0a48d; --box-bg: #211c15;
      --msg-ok-bg: #17301d; --msg-err-bg: #3a1c1c;
      --input-bg: #211c15; --input-border: #352d22;
      --btn-bg: #eaa92a; --btn-fg: #1a1a1a; --btn-active: #c9901a;
      --danger-bg: #ff6b6b; --danger-fg: #1a1a1a; --danger-active: #e05555;
    }}
  }}
  body {{ font-family: sans-serif; max-width: 420px; margin: 2rem auto; padding: 0 1rem;
          background: var(--bg); color: var(--fg); }}
  h1 {{ font-size: 1.3rem; }}
  p {{ line-height: 1.5; }}
  label {{ display: block; margin-top: 1rem; font-weight: bold; }}
  select, input {{ width: 100%; padding: .6rem; font-size: 1rem; box-sizing: border-box; margin-top: .25rem;
            background: var(--input-bg); color: var(--fg); border: 1px solid var(--input-border); }}
  button, .btn {{ display: block; width: 100%; padding: .8rem; font-size: 1rem; margin-top: 1.5rem;
            background: var(--btn-bg); border: none; border-radius: 8px; box-sizing: border-box;
            text-align: center; text-decoration: none; color: var(--btn-fg); font-weight: bold; }}
  button:active, .btn:active {{ background: var(--btn-active); }}
  .btn-danger {{ background: var(--danger-bg); color: var(--danger-fg); }}
  .btn-danger:active {{ background: var(--danger-active); }}
  .msg {{ padding: .8rem; border-radius: 6px; margin-bottom: 1rem; background: var(--box-bg); }}
  .err {{ background: var(--msg-err-bg); }}
  .ok  {{ background: var(--msg-ok-bg); }}
  .loading-bee {{ width: 28px; height: 28px; display: inline-block; vertical-align: -0.5em;
              margin-right: .4em; animation: bee-fly 0.5s ease-in-out infinite alternate; }}
  @keyframes bee-fly {{ from {{ transform: translateY(0px) rotate(-4deg); }}
                        to   {{ transform: translateY(-4px) rotate(4deg); }} }}
  .header {{ display: flex; align-items: center; gap: .6rem; margin-bottom: 1rem; }}
  .header .logo {{ font-size: 1.8rem; }}
  .header .name {{ font-weight: bold; font-size: 1.1rem; }}
  .btn-row {{ display: flex; gap: .5rem; margin-top: 1.5rem; }}
  .btn-row form {{ flex: 1; margin: 0; }}
  .btn-small {{ margin-top: 0; padding: .5rem; font-size: .85rem; }}
  .muted {{ color: var(--muted); }}
  .modal-backdrop {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5);
                      align-items: center; justify-content: center; z-index: 1000; }}
  .modal-backdrop.show {{ display: flex; }}
  .modal-box {{ background: var(--bg); color: var(--fg); border-radius: 12px; padding: 1.5rem;
                max-width: 320px; width: 85%; text-align: center; }}
  .modal-box h1 {{ font-size: 1.1rem; }}
"""

BEE_SPINNER_SVG = (
    '<svg class="loading-bee" viewBox="0 0 40 40" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">'
    '<ellipse cx="20" cy="24" rx="9" ry="12" fill="#f5c518"/>'
    '<rect x="11" y="21" width="18" height="4" rx="2" fill="#241f17" opacity=".7"/>'
    '<rect x="11" y="27" width="18" height="4" rx="2" fill="#241f17" opacity=".7"/>'
    '<circle cx="20" cy="12" r="6" fill="#241f17"/>'
    '<line x1="17" y1="7" x2="14" y2="3" stroke="#241f17" stroke-width="1.5" stroke-linecap="round"/>'
    '<line x1="23" y1="7" x2="26" y2="3" stroke="#241f17" stroke-width="1.5" stroke-linecap="round"/>'
    '<circle cx="14" cy="3" r="1.5" fill="#f5c518"/>'
    '<circle cx="26" cy="3" r="1.5" fill="#f5c518"/>'
    '<ellipse cx="10" cy="18" rx="7" ry="4" fill="rgba(200,230,255,0.75)" transform="rotate(-20 10 18)"/>'
    '<ellipse cx="30" cy="18" rx="7" ry="4" fill="rgba(200,230,255,0.75)" transform="rotate(20 30 18)"/>'
    "</svg>"
)

PAGE_HEADER = '<div class="header"><span class="logo">🍯</span><div class="name">HonigBox Setup</div></div>'

SYSTEM_BUTTONS = """
<div class="btn-row">
<form method="post" action="/system/reboot" onsubmit="return confirm('Pi wirklich neu starten?');">
  <button type="submit" class="btn-danger btn-small">🔄 Neu starten</button>
</form>
<form method="post" action="/system/shutdown" onsubmit="return confirm('Pi wirklich herunterfahren? Danach muss der Strom manuell getrennt und wieder verbunden werden, um ihn erneut zu starten.');">
  <button type="submit" class="btn-danger btn-small">⏻ Herunterfahren</button>
</form>
</div>
"""

PAGE_LANDING = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>{heading}</h1>
{status}
{update_banner}
<a class="btn" href="{app_url}">🍯 HonigBox Galerie öffnen</a>
{wifi_link}<a class="btn" href="/backup">📦 Backups</a>
<a class="btn" href="/update">🔄 Update</a>
<a class="btn" href="/hilfe" style="padding:.5rem; font-size:.85rem;">❓ Hilfe</a>
<div class="msg" style="font-size:.9rem;">
<strong>IP-Adressen:</strong><br>
{ip_lines}
</div>
{system_buttons}
</body></html>
"""

TIPS_CONTENT = """
<p class="muted" style="text-align:center; font-size:.9rem;">Für ein eigenes App-Symbol ohne Adressleiste
auf dem Home-Bildschirm. Die HonigBox Galerie sieht dann auf dem Handy wie eine echte App aus.</p>

<div class="msg ok" style="margin-top:1.5rem;">
🍎 <strong>iPhone/iPad</strong><br>
Das geht nur im <strong>Safari</strong>-Browser – andere Browser (z. B. Chrome)
können auf dem iPhone kein App-Symbol anlegen.
<ol style="margin:.6rem 0 0; padding-left:1.2rem;">
  <li>HonigBox Galerie im <strong>Safari</strong>-Browser öffnen</li>
  <li>Unten in der Leiste die drei <strong>…</strong> antippen, dann (bei iPad: oben) das
      <strong>Teilen-Symbol</strong> ⬆️ antippen</li>
  <li>Im aufklappenden Menü nach unten scrollen und <strong>„Zum Home-Bildschirm“</strong> antippen</li>
  <li>Oben rechts auf <strong>„Hinzufügen“</strong> tippen</li>
</ol>
</div>

<div class="msg ok" style="margin-top:1.5rem;">
🤖 <strong>Android</strong><br>
Am einfachsten im <strong>Chrome</strong>-Browser:
<ol style="margin:.6rem 0 0; padding-left:1.2rem;">
  <li>HonigBox Galerie in <strong>Chrome</strong> öffnen</li>
  <li>Oben rechts auf das <strong>Drei-Punkte-Menü</strong> ⋮ tippen</li>
  <li><strong>„Zum Startbildschirm hinzufügen“</strong> antippen (heißt je nach
      Chrome-Version auch „App installieren“)</li>
  <li>Mit <strong>„Hinzufügen“</strong> bzw. „Installieren“ bestätigen</li>
</ol>
</div>
"""

PAGE_TIPPS = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handy-Tipps</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>📱 Handy-Tipps</h1>""" + TIPS_CONTENT + """
<a class="btn" href="/">← Zurück zur Übersicht</a>
</body></html>
"""

PAGE_HILFE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hilfe</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>❓ Hilfe</h1>
<a class="btn" href="/tipps">📱 Handy-Tipps</a>
<p class="muted" style="font-size:.85rem; margin-top:.4rem;">Die Galerie sieht dann aus wie eine echte App und wird
nicht direkt im Browser geöffnet.</p>
<a class="btn" href="/hilfe/vpn" style="margin-top:1.5rem;">🔒 VPN-Einrichtung</a>
<p class="muted" style="font-size:.85rem; margin-top:.4rem;">Für den Zugriff auf die Galerie von unterwegs, außerhalb
des Heimnetzes.</p>
<a class="btn" href="/" style="margin-top:1.5rem;">← Zurück zur Übersicht</a>
</body></html>
"""

PAGE_VPN = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN-Einrichtung</title>
<style>""" + STYLE + """
  .hilfe-step {{ margin-top: 1rem; }}
  .hilfe-step img {{ max-width: 100%; border-radius: 8px; margin-top: .5rem; display: block; }}
</style>
</head><body>
{header}
<h1>🔒 VPN-Einrichtung</h1>
<p class="muted">Mit einem VPN lässt sich die HonigBox Galerie auch von unterwegs sicher erreichen, ohne den Zugriff
öffentlich ins Internet freizugeben. Die folgenden Schritte gelten für eine Fritzbox (ab FRITZ!OS 7.39, WireGuard
ist dort eingebaut) und die WireGuard-App auf dem Handy.</p>

<h2 style="font-size:1.05rem; margin-top:2rem;">1. VPN-Zugang auf der Fritzbox einrichten</h2>
<div class="hilfe-step">
  <p>Im Heimnetz auf <code>fritz.box</code> mit dem Fritzbox-Kennwort anmelden.</p>
  <img src="/hilfe/bilder/fritzbox-1.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>Zu „Internet" → „Freigaben" → Reiter „VPN (WireGuard)" wechseln und auf „WireGuard-Verbindung hinzufügen" tippen.</p>
  <img src="/hilfe/bilder/fritzbox-2.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>„Einzelgerät verbinden" auswählen.</p>
  <img src="/hilfe/bilder/fritzbox-3.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>Einen beliebigen Namen vergeben (z. B. „Handy CF").</p>
  <img src="/hilfe/bilder/fritzbox-4.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>Zur Bestätigung muss an der Fritzbox nun ein beliebiger Knopf gedrückt werden.</p>
</div>
<div class="hilfe-step">
  <p>Nun wird ein QR-Code angezeigt. Diesen abspeichern und danach mit der WireGuard-App am Handy einscannen.</p>
  <img src="/hilfe/bilder/fritzbox-5.png" alt="" onerror="this.style.display='none'">
</div>

<h2 style="font-size:1.05rem; margin-top:2rem;">2. WireGuard auf dem Handy einrichten</h2>
<div class="hilfe-step">
  <p>Die App „WireGuard" aus dem App Store (iPhone) bzw. Play Store (Android) installieren.</p>
  <img src="/hilfe/bilder/wireguard-1.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>App öffnen, auf „+" tippen und „Aus QR-Code scannen" wählen, dann den QR-Code von der Fritzbox-Seite abscannen.</p>
  <img src="/hilfe/bilder/wireguard-2.png" alt="" onerror="this.style.display='none'">
</div>
<div class="hilfe-step">
  <p>Verbindung benennen und speichern.</p>
  <img src="/hilfe/bilder/wireguard-3.png" alt="" onerror="this.style.display='none'">
</div>

<div class="msg ok" style="margin-top:1.5rem;">Von unterwegs: in der WireGuard-App den Schalter aktivieren, um sich
mit dem Heimnetz zu verbinden – danach ist die HonigBox Galerie wie gewohnt erreichbar.</div>

<p class="muted" style="font-size:.8rem; margin-top:1rem;">Menübezeichnungen können sich je nach FRITZ!OS-/App-Version
leicht unterscheiden.</p>

<a class="btn" href="/hilfe" style="margin-top:1.5rem;">← Zurück zur Hilfe</a>
</body></html>
"""

PAGE_FORM = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HonigBox – WLAN-Einstellungen</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>📶 WLAN-Einstellungen</h1>
{status}
<a class="btn" href="{app_url}">🍯 HonigBox Galerie öffnen</a>
{message}
<form method="post" action="/wifi/connect">
  <label for="ssid">WLAN-Name (SSID)</label>
  <div id="ssid-loading" class="msg">""" + BEE_SPINNER_SVG + """Suche nach WLAN-Netzen…</div>
  <select id="ssid" name="ssid" style="display:none"></select>
  <input id="ssid_manual" name="ssid_manual" placeholder="SSID manuell" style="display:none; margin-top:.5rem">
  <label for="password">WLAN-Passwort</label>
  <input type="password" id="password" name="password" autocomplete="off">
  <button type="submit">Verbinden</button>
</form>
{disconnect_form}
<a class="btn" href="/" style="background:transparent; border:1px solid var(--input-border); color:var(--fg);">← Zurück zur Übersicht</a>
<script>
fetch('/wifi/networks').then(r => r.json()).then(function(nets) {{
  var loading = document.getElementById('ssid-loading');
  var sel = document.getElementById('ssid');
  var manual = document.getElementById('ssid_manual');
  if (nets && nets.length) {{
    nets.forEach(function(n) {{
      var opt = document.createElement('option');
      opt.value = n.ssid; opt.textContent = n.ssid;
      sel.appendChild(opt);
    }});
    var manualOpt = document.createElement('option');
    manualOpt.value = ''; manualOpt.textContent = '– manuell eingeben –';
    sel.appendChild(manualOpt);
    sel.style.display = '';
  }} else {{
    manual.placeholder = 'SSID (kein Netz gefunden)';
  }}
  manual.style.display = '';
  loading.style.display = 'none';
}}).catch(function() {{
  var loading = document.getElementById('ssid-loading');
  loading.textContent = '❌ Fehler beim Suchen nach WLAN-Netzen.';
  document.getElementById('ssid_manual').style.display = '';
}});
</script>
</body></html>
"""

DISCONNECT_FORM = """
<form method="post" action="/wifi/disconnect" onsubmit="return confirmDisconnect()">
  <button type="submit" class="btn-danger">🔌 WLAN trennen</button>
</form>
<script>
function confirmDisconnect() {
  if (!confirm('WLAN wirklich trennen? Die HonigBox-Galerie ist danach eventuell ' +
               'nicht mehr erreichbar, falls kein Netzwerkkabel angeschlossen ist.')) {
    return false;
  }
  return confirm('Ganz sicher? Diese WLAN-Einstellungen-Seite bleibt zwar erreichbar, ' +
                  'aber die Galerie kann offline gehen, bis ein neues WLAN eingerichtet ist.');
}
</script>
"""

PAGE_CONNECTING = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verbinde…</title>
<style>""" + STYLE + """</style>
</head><body>
<div id="status">
  <h1>""" + BEE_SPINNER_SVG + """Verbinde mit „{ssid}“…</h1>
  <p>Der Pi verbindet sich jetzt mit dem WLAN. Falls gerade eine andere
  WLAN-Verbindung aktiv war, bleibt sie bestehen, falls die neue nicht
  klappt.</p>
</div>
<p>Sobald die Verbindung steht, ist die Galerie hier erreichbar:</p>
<a class="btn" href="{app_url}">🍯 HonigBox Galerie öffnen</a>
<script>
(function poll() {{
  fetch('/wifi/status').then(r => r.json()).then(data => {{
    if (!data.done) {{ setTimeout(poll, 1500); return; }}
    var el = document.getElementById('status');
    if (data.ok) {{
      el.innerHTML = '<div class="msg ok">✅ Verbindung erfolgreich hergestellt! ' +
        'Weiter zur Setup-Seite …</div>';
      setTimeout(function() {{
        window.location.href = 'http://' + location.hostname + '/';
      }}, 2500);
    }} else {{
      el.innerHTML = '<div class="msg err">❌ Verbindung fehlgeschlagen'
        + (data.detail ? ': ' + data.detail : '') + '</div>'
        + '<a class="btn" href="/wifi">Zurück zu den WLAN-Einstellungen</a>';
    }}
  }}).catch(() => setTimeout(poll, 1500));
}})();
</script>
</body></html>
"""

PAGE_BACKUP = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backups</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>📦 Backups</h1>
<p>Sichert den kompletten Ordner <code>/opt/honigbox</code> (App-Code, Fotos
und Einstellungen) – immer auf der SD-Karte, zusätzlich auf einem
eingerichteten USB-Stick, falls vorhanden.</p>
{message}
<form method="post" action="/backup/create">
  <button type="submit">📦 Jetzt Backup erstellen</button>
</form>
<a class="btn" href="/backup/restore">♻ Backup wiederherstellen</a>
<a class="btn" href="/backup/downloads">⬇ Backup herunterladen</a>

<h2 style="font-size:1.05rem; margin-top:2rem;">Einstellungen</h2>
<p class="muted">Backups laufen automatisch jede Nacht (03:30 Uhr). Aufbewahrung
nach dem Vater-Sohn-Prinzip: die letzten 14 Tage einzeln, danach automatisch
ausgedünnt auf eine Sicherung pro Woche, Monat und Jahr – so bleibt auch
ältere Historie sinnvoll erhalten, ohne dass du Zeitpläne oder Stufen selbst
verwalten musst. Einzige Einstellung ist die Gesamtanzahl.</p>
<form method="post" action="/backup/settings">
  <label for="max_backups">Max. Anzahl Backups insgesamt (je Ort)</label>
  <input type="number" id="max_backups" name="max_backups" min="20" max="200" value="{max_backups}">
  <button type="submit">Einstellung speichern</button>
</form>

<h2 style="font-size:1.05rem; margin-top:2rem;">USB-Stick</h2>
{usb_section}

<a class="btn" href="/">← Zurück zur Übersicht</a>

<div id="format-modal" class="modal-backdrop">
  <div class="modal-box" id="format-modal-content"></div>
</div>
<script>
function confirmFormat(warning) {{
  return confirm(warning) &&
         confirm('Wirklich ganz sicher? Formatieren löscht alle vorhandenen Daten auf dem Stick unwiderruflich.');
}}
function startFormat(form, warning) {{
  if (!confirmFormat(warning)) return false;
  var modal = document.getElementById('format-modal');
  var content = document.getElementById('format-modal-content');
  content.innerHTML = '<h1>""" + BEE_SPINNER_SVG + """Formatiere…</h1>' +
    '<p class="muted">Bitte warten – das kann je nach Stick-Größe einige Minuten dauern.</p>';
  modal.classList.add('show');
  fetch('/backup/usb/format', {{method: 'POST', body: new URLSearchParams(new FormData(form))}});
  (function poll() {{
    fetch('/backup/usb/format-status').then(r => r.json()).then(function(d) {{
      if (!d.done) {{ setTimeout(poll, 2000); return; }}
      content.innerHTML = d.ok
        ? '<div class="msg ok">✅ ' + d.detail + '</div>'
        : '<div class="msg err">❌ ' + d.detail + '</div>';
      setTimeout(function() {{ window.location.reload(); }}, 2000);
    }}).catch(function() {{ setTimeout(poll, 2000); }});
  }})();
  return false;
}}
</script>
</body></html>
"""

PAGE_RESTORE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backup wiederherstellen</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>♻ Backup wiederherstellen</h1>
<p>Ersetzt <strong>Fotos und Einstellungen</strong> durch den gewählten Stand.
Der App-Code bleibt unangetastet. Die Galerie startet danach automatisch neu
und ist sofort wieder voll funktionsfähig.</p>
{message}
<form method="post" action="/backup/restore"
      onsubmit="return confirmRestore(this.querySelector('select').selectedOptions[0]
                ? this.querySelector('select').selectedOptions[0].text : 'diesem Backup')">
  <label for="backup_select">Vorhandenes Backup auswählen</label>
  <select id="backup_select" name="backup_key">
    {options}
  </select>
  <button type="submit" class="btn-danger">Backup wiederherstellen</button>
</form>

<h2 style="font-size:1.05rem; margin-top:2rem;">Backup direkt vom PC wiederherstellen</h2>
<form method="post" action="/backup/restore-upload" enctype="multipart/form-data"
      onsubmit="return confirmRestore('der ausgewählten Datei')">
  <label for="upload_file">Backup-Datei auf diesem PC auswählen (.tar.gz)</label>
  <input type="file" id="upload_file" name="file" accept=".gz,.tar.gz" required>
  <button type="submit" class="btn-danger">Backup vom PC wiederherstellen</button>
</form>

<a class="btn" href="/backup">← Zurück zu den Backups</a>
<script>
function confirmRestore(name) {{
  return confirm('Fotos und Einstellungen wirklich aus "' + name + '" wiederherstellen? ' +
                 'Alle Änderungen seit diesem Backup gehen dabei verloren.') &&
         confirm('Ganz sicher? Dieser Schritt lässt sich nicht rückgängig machen.');
}}
</script>
</body></html>
"""

PAGE_DOWNLOAD_SELECT = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backup herunterladen</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>⬇ Backup herunterladen</h1>
<p>Lädt das gewählte Backup-Archiv (Fotos + Einstellungen) auf dieses Gerät
herunter.</p>
<form onsubmit="event.preventDefault(); var v = document.getElementById('download_select').value;
                if (v) window.location.href = '/backup/download/' + v.replace('|', '/');">
  <label for="download_select">Vorhandenes Backup auswählen</label>
  <select id="download_select" name="backup_key">
    {options}
  </select>
  <button type="submit">⬇ Backup herunterladen</button>
</form>
<a class="btn" href="/backup">← Zurück zu den Backups</a>
</body></html>
"""

PAGE_UPDATE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Update</title>
<style>""" + STYLE + """</style>
</head><body>
{header}
<h1>🔄 Update</h1>
<p>Prüft auf GitHub, ob eine neuere HonigBox-Version verfügbar ist. Vor jedem
Update wird automatisch ein Backup erstellt (Fotos + Einstellungen) – der
Programm-Code selbst kommt ohnehin direkt von GitHub.</p>
{message}
<div class="msg {status_class}">
<strong>Installierte Version:</strong> {current}<br>
<strong>Neueste Version:</strong> {latest}
</div>
{notes_block}
{action_block}

<h2 style="font-size:1.05rem; margin-top:2rem;">Andere Version installieren</h2>
<p class="muted">Bei Problemen mit der aktuellen Version lässt sich auch gezielt
eine andere (z. B. ältere) Version installieren. Automatische Updates werden
dabei ausgeschaltet, wenn es sich um eine ältere Version handelt – sonst
würde der Pi gleich wieder darüber hinweg aktualisieren.</p>
<form onsubmit="return startVersionSwitch(this)">
  <select name="tag" data-current="{current}" onchange="updateVersionSwitchButton(this)">
    {version_options}
  </select>
  <button type="submit" class="btn-danger" id="version-switch-btn">Version installieren</button>
</form>

<h2 style="font-size:1.05rem; margin-top:2rem;">Automatische Updates</h2>
<form method="post" action="/update/settings">
  <label style="display:flex; align-items:center; gap:.5rem; font-weight:normal;">
    <input type="checkbox" name="auto_update" value="1" {auto_update_checked} style="width:auto; margin:0;">
    Automatisch aktualisieren, sobald eine neue Version verfügbar ist
  </label>
  <p class="muted" style="margin-top:.3rem; font-size:.85rem;">Der tägliche Update-Check läuft nachts um 04:00 Uhr – ist diese Option aktiviert, wird ein gefundenes Update direkt dabei installiert.</p>
  <button type="submit">Einstellung speichern</button>
</form>

<a class="btn" href="/">← Zurück zur Übersicht</a>
{changelog_block}

<div id="update-modal" class="modal-backdrop">
  <div class="modal-box" id="update-modal-content"></div>
</div>
<script>
function startUpdate(tag) {{
  if (!confirm('Auf Version ' + tag + ' aktualisieren? Vorher wird automatisch ein Backup erstellt.')) {{
    return false;
  }}
  var modal = document.getElementById('update-modal');
  var content = document.getElementById('update-modal-content');
  content.innerHTML = '<h1>""" + BEE_SPINNER_SVG + """Aktualisiere…</h1>' +
    '<p class="muted">Backup wird erstellt, neue Version heruntergeladen und installiert. ' +
    'Das kann einige Minuten dauern – bitte die Seite nicht schließen.</p>';
  modal.classList.add('show');
  fetch('/update/run', {{method: 'POST'}});
  (function poll() {{
    fetch('/update/status').then(r => r.json()).then(function(d) {{
      if (!d.done) {{ setTimeout(poll, 2000); return; }}
      content.innerHTML = d.ok
        ? '<div class="msg ok">✅ ' + d.detail + '</div>'
        : '<div class="msg err">❌ ' + d.detail + '</div>';
      setTimeout(function() {{ window.location.reload(); }}, 2500);
    }}).catch(function() {{ setTimeout(poll, 2000); }});
  }})();
  return false;
}}
function startVersionSwitch(form) {{
  var tag = form.querySelector('select').value;
  if (!tag) return false;
  if (!confirm('Wirklich auf Version ' + tag + ' wechseln? Vorher wird automatisch ein Backup erstellt.')) {{
    return false;
  }}
  var modal = document.getElementById('update-modal');
  var content = document.getElementById('update-modal-content');
  content.innerHTML = '<h1>""" + BEE_SPINNER_SVG + """Wechsle Version…</h1>' +
    '<p class="muted">Backup wird erstellt, gewählte Version heruntergeladen und installiert. ' +
    'Das kann einige Minuten dauern – bitte die Seite nicht schließen.</p>';
  modal.classList.add('show');
  fetch('/update/switch', {{method: 'POST', body: new URLSearchParams(new FormData(form))}});
  (function poll() {{
    fetch('/update/status').then(r => r.json()).then(function(d) {{
      if (!d.done) {{ setTimeout(poll, 2000); return; }}
      content.innerHTML = d.ok
        ? '<div class="msg ok">✅ ' + d.detail + '</div>'
        : '<div class="msg err">❌ ' + d.detail + '</div>';
      setTimeout(function() {{ window.location.reload(); }}, 2500);
    }}).catch(function() {{ setTimeout(poll, 2000); }});
  }})();
  return false;
}}
function updateVersionSwitchButton(select) {{
  var btn = document.getElementById('version-switch-btn');
  var isCurrent = select.value === select.dataset.current;
  btn.disabled = isCurrent;
  btn.classList.toggle('btn-danger', !isCurrent);
  btn.style.opacity = isCurrent ? '.5' : '1';
}}
updateVersionSwitchButton(document.querySelector('select[name="tag"]'));
</script>
</body></html>
"""

PAGE_SYSTEM_ACTION = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{action}…</title>
<style>""" + STYLE + """</style>
</head><body>
<h1>""" + BEE_SPINNER_SVG + """Pi {verb}…</h1>
<p>{hint}</p>
{retry_script}
</body></html>
"""

RETRY_SCRIPT = """
<script>
setTimeout(function poll() {
  fetch('/', {cache: 'no-store'}).then(function(r) {
    if (r.ok) { window.location.href = '/'; } else { setTimeout(poll, 3000); }
  }).catch(function() { setTimeout(poll, 3000); });
}, 15000);
</script>
"""

STOP_SPINNER_SCRIPT = """
<script>
setTimeout(function() {
  var el = document.querySelector('.loading-bee');
  if (el) { el.style.animation = 'none'; el.style.opacity = '.4'; }
}, 20000);
</script>
"""


# --------------------------------------------------------------------------
# WLAN
# --------------------------------------------------------------------------

def scan_networks():
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    seen, nets = set(), []
    for line in out.splitlines():
        if not line or ":" not in line:
            continue
        ssid, _, signal = line.rpartition(":")
        ssid = ssid.strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        nets.append((ssid, signal))
    nets.sort(key=lambda t: int(t[1] or 0), reverse=True)
    return nets


def current_wifi_connection():
    """(ssid, connected) fuer wlan0 - ssid ist None wenn nicht verbunden."""
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None, False
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == "wlan0":
            connected = parts[1] == "connected"
            ssid = parts[2] if connected and parts[2] != "--" else None
            return ssid, connected
    return None, False


def get_ip(iface):
    try:
        out = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "device", "show", iface],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out.split("/")[0] if out else None


def all_ips():
    """Alle IPv4-Adressen ueber 'ip' ermitteln - fuer den (seltenen) Fall
    eines Nicht-Pi-Systems, auf dem nmcli das Interface nicht verwaltet."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] == "lo":
            continue
        result.append((parts[1], parts[3].split("/")[0]))
    return result


def status_banner():
    ssid, connected = current_wifi_connection()
    if connected:
        return f'<div class="msg ok">📶 Aktuell verbunden mit <strong>{ssid}</strong></div>'
    return '<div class="msg err">📡 Kein WLAN verbunden</div>'


def previously_active_connection():
    """Name des aktuell aktiven Verbindungsprofils auf wlan0 (oder None)."""
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in out.splitlines():
        name, _, device = line.partition(":")
        if device == "wlan0":
            return name
    return None


def connect_wifi(ssid, password):
    previous = previously_active_connection()
    if previous == ssid:
        previous = None  # Verbindung zum gleichen Netz - kein Rueckfall noetig

    subprocess.run(["nmcli", "connection", "delete", ssid], capture_output=True, text=True)

    cmd = ["nmcli", "device", "wifi", "connect", ssid, "ifname", "wlan0"]
    if password:
        cmd += ["password", password]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    ok = result.returncode == 0
    detail = (result.stderr or result.stdout).strip()

    if not ok and previous:
        rollback = subprocess.run(
            ["nmcli", "connection", "up", previous],
            capture_output=True, text=True, timeout=30,
        )
        if rollback.returncode == 0:
            detail += f" (alte Verbindung '{previous}' wiederhergestellt)"
        else:
            detail += f" (Wiederherstellen von '{previous}' ebenfalls fehlgeschlagen!)"

    return ok, detail


def disconnect_wifi():
    name = previously_active_connection()
    if name:
        subprocess.run(["nmcli", "connection", "modify", name, "autoconnect", "no"],
                        capture_output=True, text=True)
    result = subprocess.run(["nmcli", "device", "disconnect", "wlan0"],
                             capture_output=True, text=True, timeout=15)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------

def list_backups(directory):
    try:
        names = [f for f in os.listdir(directory) if BACKUP_NAME_RE.match(f)]
    except OSError:
        return []
    names.sort(reverse=True)
    backups = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            st = os.stat(path)
            size_mb = f"{st.st_size / (1024 * 1024):.1f}"
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        except OSError:
            size_mb, mtime = "?", "?"
        backups.append({"name": name, "size_mb": size_mb, "mtime": mtime})
    return backups


def create_backup_now():
    try:
        result = subprocess.run(["bash", BACKUP_SCRIPT], capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def _restore_from_tar(tar, label):
    """Ersetzt den kompletten /opt/honigbox/fotos-Ordner (Fotos +
    Einstellungen) durch den Inhalt des geoeffneten tar-Archivs. App-Code
    bleibt unangetastet. Stoppt beide Dienste waehrenddessen (beide lesen/
    schreiben Dateien in fotos/), startet sie danach wieder."""
    subprocess.run(["systemctl", "stop", "honigbox.service"], capture_output=True, text=True)
    subprocess.run(["systemctl", "stop", "honigbox-galerie.service"], capture_output=True, text=True)
    try:
        members = [m for m in tar.getmembers()
                   if m.name == BACKUP_DATA_PREFIX or m.name.startswith(BACKUP_DATA_PREFIX + "/")]
        if not members:
            return False, f"'{BACKUP_DATA_PREFIX}' nicht im Archiv gefunden."
        with tempfile.TemporaryDirectory() as tmpdir:
            tar.extractall(path=tmpdir, members=members)
            extracted_dir = os.path.join(tmpdir, BACKUP_DATA_PREFIX)
            if os.path.isdir("/opt/honigbox/fotos"):
                shutil.rmtree("/opt/honigbox/fotos")
            shutil.move(extracted_dir, "/opt/honigbox/fotos")
        # Wie schon in foto.sh/install.sh: 777 statt einem bestimmten
        # Benutzer, weil sowohl root (honigbox.service) als auch www-data
        # (honigbox-galerie.service) hier lesen/schreiben.
        subprocess.run(["chmod", "-R", "777", "/opt/honigbox/fotos"], capture_output=True, text=True)
    except OSError as e:
        subprocess.run(["systemctl", "start", "honigbox.service"], capture_output=True, text=True)
        subprocess.run(["systemctl", "start", "honigbox-galerie.service"], capture_output=True, text=True)
        return False, f"Fehler bei der Wiederherstellung: {e}"
    subprocess.run(["systemctl", "start", "honigbox.service"], capture_output=True, text=True)
    subprocess.run(["systemctl", "start", "honigbox-galerie.service"], capture_output=True, text=True)
    return True, f"Fotos und Einstellungen aus '{label}' wiederhergestellt – HonigBox läuft wieder."


def restore_backup(location, filename):
    if not filename or not BACKUP_NAME_RE.match(filename):
        return False, "Ungültiger Dateiname."
    directory = USB_MOUNT if location == "usb" else BACKUP_DIR
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        return False, "Backup nicht gefunden."
    try:
        with tarfile.open(path, "r:gz") as tar:
            return _restore_from_tar(tar, filename)
    except (tarfile.TarError, OSError) as e:
        return False, f"Fehler beim Lesen des Archivs: {e}"


def restore_backup_from_bytes(data, filename):
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            return _restore_from_tar(tar, filename or "der hochgeladenen Datei")
    except (tarfile.TarError, OSError) as e:
        return False, f"Fehler beim Lesen der hochgeladenen Datei: {e}"


def parse_multipart_file(body, content_type):
    """Sehr einfacher multipart/form-data-Parser fuer genau EIN Datei-Feld
    (keine externen Abhaengigkeiten, Python-Standardbibliothek reicht).
    Gibt (dateiname, bytes) oder (None, None) zurueck."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        return None, None
    boundary = ("--" + m.group(1)).encode()
    for part in body.split(boundary):
        if b"Content-Disposition" not in part:
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers = part[:header_end].decode("utf-8", "replace")
        data = part[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        fm = re.search(r'filename="([^"]*)"', headers)
        if fm and fm.group(1):
            return fm.group(1), data
    return None, None


def get_max_backups():
    try:
        with open(BACKUP_CONFIG_PATH) as f:
            content = f.read()
        m = re.search(r"^MAX_BACKUPS=(\d+)", content, re.MULTILINE)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return DEFAULT_MAX_BACKUPS


def set_backup_settings(max_backups_raw):
    try:
        max_backups = int(max_backups_raw)
    except (TypeError, ValueError):
        return False, "Ungültige Anzahl."
    if not (MIN_MAX_BACKUPS <= max_backups <= 200):
        return False, f"Anzahl muss zwischen {MIN_MAX_BACKUPS} und 200 liegen."
    try:
        os.makedirs(os.path.dirname(BACKUP_CONFIG_PATH), exist_ok=True)
        with open(BACKUP_CONFIG_PATH, "w") as f:
            f.write(f"MAX_BACKUPS={max_backups}\n")
    except OSError as e:
        return False, str(e)
    return True, f"Aufbewahrung gespeichert (max. {max_backups} Backups je Ort, Vater-Sohn-Rotation)."


def get_root_disk():
    """Name (z. B. 'mmcblk0') der Festplatte, von der das System bootet."""
    try:
        src = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        pkname = subprocess.run(["lsblk", "-no", "PKNAME", src],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        return pkname or re.sub(r"p?\d+$", "", src.replace("/dev/", ""))
    except (subprocess.TimeoutExpired, OSError):
        return None


def list_usb_disks():
    """Per USB angeschlossene Festplatten, OHNE die System-Platte - reine
    Sicherheitsmassnahme, damit diese niemals formatierbar angeboten wird."""
    root_disk = get_root_disk()
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,TRAN,SIZE,FSTYPE,LABEL,MOUNTPOINT,TYPE"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        data = json.loads(out)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return []
    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk" or dev.get("tran") != "usb":
            continue
        if dev.get("name") == root_disk:
            continue
        mountpoints = [dev.get("mountpoint")] + [c.get("mountpoint") for c in dev.get("children", []) or []]
        labels = [dev.get("label")] + [c.get("label") for c in dev.get("children", []) or []]
        disks.append({
            "name": dev["name"],
            "size": dev.get("size") or "?",
            "fstype": dev.get("fstype") or "unformatiert",
            "is_target": USB_MOUNT in mountpoints,
            "is_known_backup_stick": "BACKUP" in [l for l in labels if l],
        })
    return disks


def _register_fstab_and_mount(device):
    """Traegt das Dateisystem von `device` (per UUID) in /etc/fstab fuer
    USB_MOUNT ein und haengt es ein."""
    uuid = subprocess.run(["blkid", "-s", "UUID", "-o", "value", device],
                          capture_output=True, text=True, timeout=10).stdout.strip()
    if not uuid:
        return False, "UUID des Dateisystems konnte nicht ermittelt werden."

    os.makedirs(USB_MOUNT, exist_ok=True)
    try:
        try:
            with open("/etc/fstab") as f:
                lines = [l for l in f if USB_MOUNT not in l]
        except FileNotFoundError:
            lines = []
        lines.append(f"UUID={uuid} {USB_MOUNT} ext4 defaults,nofail,x-systemd.device-timeout=5 0 2\n")
        with open("/etc/fstab", "w") as f:
            f.writelines(lines)
    except OSError as e:
        return False, f"/etc/fstab konnte nicht aktualisiert werden: {e}"

    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True)
    mount_result = subprocess.run(["mount", USB_MOUNT], capture_output=True, text=True, timeout=30)
    if mount_result.returncode != 0:
        return False, f"Einhängen fehlgeschlagen: {(mount_result.stderr or mount_result.stdout).strip()}"
    return True, None


def format_and_setup_usb(device_name):
    if not re.match(r"^[a-z][a-z0-9]*$", device_name or ""):
        return False, "Ungültiger Gerätename."
    root_disk = get_root_disk()
    if device_name == root_disk:
        return False, "Sicherheitsstopp: das ist die System-Festplatte - wird nicht formatiert."
    if device_name not in {d["name"] for d in list_usb_disks()}:
        return False, "Gerät ist kein erkannter USB-Stick."
    device = f"/dev/{device_name}"

    subprocess.run(["umount", USB_MOUNT], capture_output=True, text=True)
    subprocess.run(["umount", device], capture_output=True, text=True)
    for i in range(1, 5):
        subprocess.run(["umount", f"{device}{i}"], capture_output=True, text=True)

    result = subprocess.run(["mkfs.ext4", "-F", "-L", "BACKUP", device],
                             capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        return False, f"Formatieren fehlgeschlagen: {(result.stderr or result.stdout).strip()}"

    ok, err = _register_fstab_and_mount(device)
    if not ok:
        return False, f"Formatiert, aber {err}"
    return True, f"USB-Stick formatiert und als zusätzliches Backup-Ziel eingerichtet ({USB_MOUNT})."


def mount_existing_usb(device_name):
    """Bindet einen USB-Stick ein, der bereits frueher als Backup-Ziel
    formatiert wurde - OHNE ihn neu zu formatieren."""
    if not re.match(r"^[a-z][a-z0-9]*$", device_name or ""):
        return False, "Ungültiger Gerätename."
    root_disk = get_root_disk()
    if device_name == root_disk:
        return False, "Sicherheitsstopp: das ist die System-Festplatte."
    if device_name not in {d["name"] for d in list_usb_disks()}:
        return False, "Gerät ist kein erkannter USB-Stick."
    device = f"/dev/{device_name}"

    ok, err = _register_fstab_and_mount(device)
    if not ok:
        return False, err
    return True, f"Vorhandener USB-Stick eingebunden ({USB_MOUNT}) – bestehende Backups sind erhalten."


def eject_usb():
    result = subprocess.run(["umount", USB_MOUNT], capture_output=True, text=True, timeout=15)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def try_remount_usb():
    """Bestversuch: haengt einen bereits eingerichteten USB-Stick automatisch
    wieder ein, falls er zwischenzeitlich ab- und wieder angesteckt wurde."""
    if os.path.ismount(USB_MOUNT):
        return
    subprocess.run(["mount", USB_MOUNT], capture_output=True, text=True, timeout=15)


def _run_format_in_background(device_name):
    ok, detail = format_and_setup_usb(device_name)
    FORMAT_STATE.update(done=True, ok=ok, detail=detail)


def disk_usage(path):
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        return f"{free_gb:.1f} GB frei von {total_gb:.1f} GB"
    except OSError:
        return "?"


def render_backup_page(message="", skip_remount=False):
    if not skip_remount:
        try_remount_usb()
    usb_mounted = os.path.ismount(USB_MOUNT)
    usb_disks = list_usb_disks()
    usb_section_parts = []
    if usb_mounted:
        usb_section_parts.append(
            f'<div class="msg ok">📦 USB-Stick eingerichtet ({disk_usage(USB_MOUNT)}) – '
            f'Backups werden auf der SD-Karte und zusätzlich hier abgelegt.</div>'
        )
        usb_section_parts.append(
            '<form method="post" action="/backup/usb/eject" '
            'onsubmit="return confirm(\'USB-Stick sicher aushängen? Danach kann er entfernt werden.\');">'
            '<button type="submit">⏏ USB-Stick sicher entfernen</button></form>'
        )
    elif usb_disks:
        for d in usb_disks:
            if d.get("is_known_backup_stick"):
                usb_section_parts.append(f"""
<div class="msg ok">
  Bekannter Backup-Stick gefunden: <strong>/dev/{d['name']}</strong> ({d['size']}) –
  aktuell nicht eingebunden (z. B. nach einer Neuinstallation dieses Pi).
  Vorhandene Backups bleiben beim Einbinden erhalten.
  <form method="post" action="/backup/usb/mount">
    <input type="hidden" name="device" value="{d['name']}">
    <button type="submit">📌 Vorhandenen Stick einbinden</button>
  </form>
</div>""")
                continue
            warn = (f"USB-Stick /dev/{d['name']} ({d['size']}, {d['fstype']}) wirklich formatieren? "
                    f"ALLE Daten darauf gehen unwiderruflich verloren!")
            usb_section_parts.append(f"""
<div class="msg err">
  USB-Stick gefunden: <strong>/dev/{d['name']}</strong> ({d['size']}, {d['fstype']}) –
  noch nicht als Backup-Ziel eingerichtet.
  <form onsubmit="return startFormat(this, '{warn}')">
    <input type="hidden" name="device" value="{d['name']}">
    <button type="submit" class="btn-danger">⚙ Formatieren &amp; als Backup-Ziel einrichten</button>
  </form>
</div>""")
    elif IS_PI:
        usb_section_parts.append(
            '<div class="msg err">⚠️ <strong>Kein USB-Stick angeschlossen.</strong> '
            'Backups liegen nur auf der SD-Karte – bei einem Ausfall der SD-Karte sind dann '
            '<strong>alle</strong> Daten unwiderruflich verloren. Einen Stick anschließen und '
            'diese Seite neu laden, um ihn einzurichten.</div>'
        )
    else:
        usb_section_parts.append(
            '<p class="muted">Kein USB-Stick angeschlossen – optional, nicht erforderlich.</p>'
        )

    return PAGE_BACKUP.format(
        header=render_header(),
        message=message,
        usb_section="".join(usb_section_parts),
        max_backups=get_max_backups(),
    )


def backup_options_html():
    try_remount_usb()
    entries = [(b, "local", "SD-Karte") for b in list_backups(BACKUP_DIR)]
    if os.path.ismount(USB_MOUNT):
        entries += [(b, "usb", "USB-Stick") for b in list_backups(USB_MOUNT)]
    entries.sort(key=lambda e: e[0]["name"], reverse=True)
    if not entries:
        return '<option value="">– keine Backups vorhanden –</option>'
    return "".join(
        f'<option value="{loc}|{b["name"]}">{b["name"]} ({b["mtime"]}, {label})</option>'
        for b, loc, label in entries)


def render_restore_page(message=""):
    return PAGE_RESTORE.format(header=render_header(), message=message, options=backup_options_html())


def render_download_select_page():
    return PAGE_DOWNLOAD_SELECT.format(header=render_header(), options=backup_options_html())


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------

def app_version():
    try:
        with open("/opt/honigbox/static/app.js") as f:
            content = f.read()
        m = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", content)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "?"


def parse_version(v):
    """'v1.2.3' -> (1, 2, 3), fuer korrekten numerischen Vergleich (nicht
    alphabetisch - sonst waere z. B. 'v1.10' < 'v1.9')."""
    parts = []
    for p in (v or "").lstrip("vV").split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def fetch_latest_release():
    try:
        req = urllib.request.Request(
            GITHUB_LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "HonigBox-Update-Check"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name") or ""
        if not tag:
            return None
        return {"tag": tag, "notes": (data.get("body") or "").strip(), "tarball_url": data.get("tarball_url") or ""}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def fetch_all_releases(limit=10):
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page={limit}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "HonigBox-Update-Check"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [{"tag": r["tag_name"], "tarball_url": r.get("tarball_url") or "",
                  "notes": (r.get("body") or "").strip(), "published_at": r.get("published_at") or ""}
                for r in data if r.get("tag_name")]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError, KeyError):
        return []


def fetch_release_by_tag(tag):
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{quote(tag, safe='')}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "HonigBox-Update-Check"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag_name = data.get("tag_name") or ""
        if not tag_name:
            return None
        return {"tag": tag_name, "notes": (data.get("body") or "").strip(), "tarball_url": data.get("tarball_url") or ""}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def get_auto_update():
    """Standard AN, falls die Konfigurationsdatei fehlt oder unlesbar ist."""
    try:
        with open(AUTO_UPDATE_CONFIG_PATH) as f:
            content = f.read()
        m = re.search(r"^AUTO_UPDATE=(\d)", content, re.MULTILINE)
        if m:
            return m.group(1) == "1"
    except OSError:
        pass
    return True


def set_auto_update(enabled):
    try:
        os.makedirs(os.path.dirname(AUTO_UPDATE_CONFIG_PATH), exist_ok=True)
        with open(AUTO_UPDATE_CONFIG_PATH, "w") as f:
            f.write(f"AUTO_UPDATE={1 if enabled else 0}\n")
        return True, "Einstellung gespeichert."
    except OSError as e:
        return False, str(e)


def run_update_check_once():
    """Einmaliger Versions-Check, Ergebnis wird zwischengespeichert (per
    Timer regelmaessig aufgerufen)."""
    current = app_version()
    release = fetch_latest_release()
    update_available = bool(release) and parse_version(release["tag"]) > parse_version(current)
    auto_updated_version = read_update_check_state().get("auto_updated_version")
    if update_available and get_auto_update() and release.get("tarball_url"):
        ok, detail = perform_update(release["tarball_url"], release["tag"])
        UPDATE_STATE.update(done=True, ok=ok, detail=detail)
        if ok:
            current = app_version()
            update_available = False
            auto_updated_version = current
        _restart_setup_portal_after_update(ok, detail)
    state = {
        "current": current,
        "latest": release["tag"] if release else None,
        "update_available": update_available,
        "checked_at": time.strftime("%Y-%m-%d %H:%M"),
        "notes": (release.get("notes") if release else None),
        "auto_updated_version": auto_updated_version,
    }
    try:
        os.makedirs(os.path.dirname(UPDATE_CHECK_STATE_PATH), exist_ok=True)
        with open(UPDATE_CHECK_STATE_PATH, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def read_update_check_state():
    try:
        with open(UPDATE_CHECK_STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"current": app_version(), "latest": None, "update_available": False, "checked_at": None}


# (Quellpfad relativ zum Tarball-Root, Zielpfad auf dem Geraet, Dateimodus).
# Aeltere Release-Tags, die eine Datei noch nicht enthalten, werden dabei
# einfach uebersprungen.
MAIN_APP_FILE_MAP = [
    ("honigbox.sh", "/opt/honigbox/honigbox.sh", 0o755),
    ("foto.sh", "/opt/honigbox/foto.sh", 0o755),
    ("send_pushover.sh", "/opt/honigbox/send_pushover.sh", 0o755),
    ("galerie_server.py", "/opt/honigbox/galerie_server.py", 0o644),
    ("honigbox.service", "/etc/systemd/system/honigbox.service", 0o644),
    ("honigbox-galerie.service", "/etc/systemd/system/honigbox-galerie.service", 0o644),
]

SETUP_PORTAL_FILE_MAP = [
    ("setup/honigbox_setup_portal.py", "/opt/honigbox-wifi-setup/honigbox_setup_portal.py", 0o644),
    ("setup/honigbox-wifi-setup.sh", "/opt/honigbox-wifi-setup/honigbox-wifi-setup.sh", 0o755),
    ("setup/honigbox-backup.sh", "/opt/backup-scripts/honigbox-backup.sh", 0o755),
    ("setup/honigbox-backup-rotate.py", "/opt/backup-scripts/honigbox-backup-rotate.py", 0o644),
    ("setup/honigbox-wifi-setup.service", "/etc/systemd/system/honigbox-wifi-setup.service", 0o644),
    ("setup/honigbox-backup.service", "/etc/systemd/system/honigbox-backup.service", 0o644),
    ("setup/honigbox-backup.timer", "/etc/systemd/system/honigbox-backup.timer", 0o644),
    ("setup/honigbox-update-check.service", "/etc/systemd/system/honigbox-update-check.service", 0o644),
    ("setup/honigbox-update-check.timer", "/etc/systemd/system/honigbox-update-check.timer", 0o644),
]


def _update_main_app_files(src_root):
    """Kopiert honigbox.sh/foto.sh/send_pushover.sh/galerie_server.py +
    static/ sowie die beiden App-Units aus dem heruntergeladenen Release.
    Setzt bei den Shell-Scripten explizit das Ausfuehrungsrecht (0o755) -
    genau das hat einmal gefehlt und honigbox.service in eine Crash-Schleife
    geschickt, siehe Projekt-Historie."""
    any_unit_changed = False
    for rel_path, dest, mode in MAIN_APP_FILE_MAP:
        src = os.path.join(src_root, rel_path)
        if not os.path.isfile(src):
            continue
        shutil.copy(src, dest)
        os.chmod(dest, mode)
        if dest.startswith("/etc/systemd/system/"):
            any_unit_changed = True

    new_static = os.path.join(src_root, "static")
    if os.path.isdir(new_static):
        if os.path.isdir("/opt/honigbox/static"):
            shutil.rmtree("/opt/honigbox/static")
        shutil.copytree(new_static, "/opt/honigbox/static")
        # galerie_server.py + static/ laufen unter www-data (honigbox-galerie.service).
        subprocess.run(["chown", "-R", "www-data:www-data", "/opt/honigbox/galerie_server.py", "/opt/honigbox/static"],
                       capture_output=True, text=True)

    if any_unit_changed:
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True)


def _update_setup_portal_files(src_root):
    """Kopiert - neben der Haupt-App - auch das Setup-Portal selbst (dieses
    Skript), die Backup-Skripte und die systemd-Units aus dem
    heruntergeladenen Release."""
    any_unit_changed = False
    for rel_path, dest, mode in SETUP_PORTAL_FILE_MAP:
        src = os.path.join(src_root, rel_path)
        if not os.path.isfile(src):
            continue
        shutil.copy(src, dest)
        os.chmod(dest, mode)
        if dest.startswith("/etc/systemd/system/"):
            any_unit_changed = True
    if any_unit_changed:
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True)
    subprocess.run(["systemctl", "enable", "--now", "honigbox-backup.timer"], capture_output=True, text=True)
    subprocess.run(["systemctl", "enable", "--now", "honigbox-update-check.timer"], capture_output=True, text=True)


def _persist_update_result(ok, detail):
    try:
        with open(UPDATE_RESULT_PERSIST_PATH, "w") as f:
            json.dump({"ok": ok, "detail": detail, "ts": time.time()}, f)
    except OSError:
        pass


def _load_recent_update_result():
    """Liest ein kurz zuvor (vor einem Neustart dieses Dienstes) persistiertes
    Update-Ergebnis, falls es nicht aelter als ein paar Minuten ist."""
    try:
        with open(UPDATE_RESULT_PERSIST_PATH) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < 180:
            return {"done": True, "ok": data.get("ok"), "detail": data.get("detail")}
    except (OSError, json.JSONDecodeError):
        pass
    return None


_recent_update_result = _load_recent_update_result()
if _recent_update_result:
    UPDATE_STATE.update(_recent_update_result)


def _restart_setup_portal_after_update(ok, detail):
    """Letzter Schritt nach jedem Update: Ergebnis fuer die Zeit nach dem
    Neustart zwischenspeichern, dann honigbox-wifi-setup.service neu
    starten, damit ein mitgeliefertes neues honigbox_setup_portal.py auch
    tatsaechlich verwendet wird."""
    _persist_update_result(ok, detail)
    if ok:
        subprocess.Popen(["systemctl", "restart", "honigbox-wifi-setup.service"])


def perform_update(tarball_url, target_tag):
    """Legt zuerst ein Backup an, laedt dann den Source-Tarball des GitHub-
    Release herunter und ersetzt die App-Dateien (fotos/ bleibt
    unangetastet) sowie das Setup-Portal samt Backup-Skripten und
    systemd-Units. Gibt (True, Meldung) oder (False, Fehlertext) zurueck."""
    ok, detail = create_backup_now()
    if not ok:
        return False, f"Backup vor dem Update fehlgeschlagen - Update abgebrochen: {detail}"
    try:
        req = urllib.request.Request(tarball_url, headers={"User-Agent": "HonigBox-Update"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            archive_data = resp.read()
    except (urllib.error.URLError, OSError) as e:
        return False, f"Herunterladen fehlgeschlagen: {e}"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as tar:
                tar.extractall(path=tmpdir)
            entries = os.listdir(tmpdir)
            if len(entries) != 1:
                return False, "Unerwarteter Archivinhalt (GitHub-Tarball-Struktur hat sich geaendert)."
            src_root = os.path.join(tmpdir, entries[0])

            subprocess.run(["systemctl", "stop", "honigbox.service"], capture_output=True, text=True)
            subprocess.run(["systemctl", "stop", "honigbox-galerie.service"], capture_output=True, text=True)
            _update_main_app_files(src_root)

            portal_update_error = None
            try:
                _update_setup_portal_files(src_root)
            except OSError as e:
                portal_update_error = str(e)
    except (tarfile.TarError, OSError) as e:
        subprocess.run(["systemctl", "start", "honigbox.service"], capture_output=True, text=True)
        subprocess.run(["systemctl", "start", "honigbox-galerie.service"], capture_output=True, text=True)
        return False, f"Fehler beim Aktualisieren: {e}"

    subprocess.run(["systemctl", "start", "honigbox.service"], capture_output=True, text=True)
    subprocess.run(["systemctl", "start", "honigbox-galerie.service"], capture_output=True, text=True)
    if portal_update_error:
        return True, (f"Auf Version {target_tag} aktualisiert - HonigBox läuft wieder. "
                      f"Das Setup-Portal konnte dabei nicht mit aktualisiert werden "
                      f"({portal_update_error}) - bitte später erneut versuchen.")
    return True, f"Auf Version {target_tag} aktualisiert - HonigBox läuft wieder."


def _run_update_in_background():
    release = fetch_latest_release()
    if not release or not release.get("tarball_url"):
        UPDATE_STATE.update(done=True, ok=False, detail="Neueste Version konnte nicht ermittelt werden.")
        return
    ok, detail = perform_update(release["tarball_url"], release["tag"])
    UPDATE_STATE.update(done=True, ok=ok, detail=detail)
    run_update_check_once()
    _restart_setup_portal_after_update(ok, detail)


def _run_version_switch_in_background(tag):
    """Installiert gezielt eine bestimmte Version (z. B. Rueckwechsel auf
    eine aeltere bei Problemen mit der neuesten). Schaltet automatische
    Updates ab, falls es sich dabei um einen echten Rueckschritt handelt."""
    previous_version = app_version()
    release = fetch_release_by_tag(tag)
    if not release or not release.get("tarball_url"):
        UPDATE_STATE.update(done=True, ok=False, detail=f"Version '{tag}' konnte nicht gefunden werden.")
        return
    ok, detail = perform_update(release["tarball_url"], release["tag"])
    if ok and parse_version(release["tag"]) < parse_version(previous_version):
        set_auto_update(False)
        detail += (" Automatische Updates wurden dabei ausgeschaltet, damit der Pi nicht "
                   "gleich wieder auf die neuere Version zurueckaktualisiert.")
    UPDATE_STATE.update(done=True, ok=ok, detail=detail)
    run_update_check_once()
    _restart_setup_portal_after_update(ok, detail)


def render_update_page(message=""):
    current = app_version()
    release = fetch_latest_release()
    if release is None:
        latest = "konnte nicht abgerufen werden"
        status_class = "err"
        notes_block = ""
        action_block = '<p class="muted">Prüfe, ob der Pi Internetzugang hat, und lade die Seite neu.</p>'
    else:
        latest = release["tag"]
        update_available = parse_version(latest) > parse_version(current)
        status_class = "err" if update_available else "ok"
        notes_block = (f'<div class="msg" style="white-space:pre-wrap;">{html.escape(release["notes"])}</div>'
                       if update_available and release["notes"] else "")
        if update_available:
            action_block = (
                f'<form onsubmit="return startUpdate(\'{latest}\')">'
                f'<button type="submit" class="btn-danger">⬇ Auf {latest} aktualisieren</button>'
                f'</form>'
            )
        else:
            action_block = '<p class="muted">Du hast bereits die neueste Version.</p>'

    all_releases = fetch_all_releases()
    if all_releases:
        version_options = "".join(
            f'<option value="{r["tag"]}" {"selected" if r["tag"] == current else ""}>'
            f'{r["tag"]}{" (installiert)" if r["tag"] == current else ""}</option>'
            for r in all_releases
        )
    else:
        version_options = '<option value="">– keine Releases abrufbar –</option>'

    changelog_items = "".join(
        f'<div style="margin-bottom:.9rem">'
        f'<strong>{html.escape(r["tag"])}</strong>'
        + (f' <span class="muted" style="font-size:.8rem">· {html.escape(r["published_at"][:10])}</span>' if r.get("published_at") else "")
        + f'<div class="muted" style="white-space:pre-wrap;font-size:.85rem;margin-top:.2rem">{html.escape(r["notes"])}</div>'
        f'</div>'
        for r in all_releases if r.get("notes")
    )
    changelog_block = (
        f'<details style="margin-top:2rem">'
        f'<summary class="muted" style="cursor:pointer">Änderungsverlauf früherer Versionen</summary>'
        f'<div style="margin-top:.8rem">{changelog_items}</div>'
        f'</details>'
    ) if changelog_items else ""

    return PAGE_UPDATE.format(
        header=render_header(), message=message, current=current, latest=latest,
        status_class=status_class, notes_block=notes_block, action_block=action_block,
        version_options=version_options,
        auto_update_checked="checked" if get_auto_update() else "",
        changelog_block=changelog_block,
    )


# --------------------------------------------------------------------------
# Gemeinsam
# --------------------------------------------------------------------------

def render_header():
    return PAGE_HEADER


def _host_without_port(host):
    if host.startswith("["):  # IPv6-Literal, z.B. [::1]:80
        idx = host.rfind("]")
        return host[:idx + 1] if idx != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def app_url(request_host=None):
    host = _host_without_port(request_host) if request_host else f"{socket.gethostname()}.local"
    return f"http://{host}:{GALERIE_PORT}"


def render_landing(request_host=None):
    update_state = read_update_check_state()
    update_banner = ""
    if update_state.get("update_available"):
        update_banner = f'<div class="msg ok">🔄 Update verfügbar: Version {update_state["latest"]}</div>'
    if IS_PI:
        ip_lines = (f'Kabel (eth0): {get_ip("eth0") or "nicht verbunden"}<br>'
                    f'WLAN (wlan0): {get_ip("wlan0") or "nicht verbunden"}')
    else:
        ips = all_ips()
        ip_lines = "<br>".join(f"{iface}: {addr}" for iface, addr in ips) if ips else "nicht verbunden"
    return PAGE_LANDING.format(
        title="HonigBox-Pi" if IS_PI else "HonigBox-Setup",
        heading="🍯 HonigBox-Pi" if IS_PI else "🍯 HonigBox-Setup",
        header=render_header(),
        status=status_banner() if IS_PI else "",
        update_banner=update_banner,
        app_url=app_url(request_host),
        wifi_link='<a class="btn" href="/wifi">📶 WLAN-Einstellungen</a>\n' if IS_PI else "",
        ip_lines=ip_lines,
        system_buttons=SYSTEM_BUTTONS if IS_PI else "",
    )


def render_form(request_host=None, message=""):
    _, connected = current_wifi_connection()
    return PAGE_FORM.format(
        header=render_header(),
        status=status_banner(),
        app_url=app_url(request_host),
        message=message,
        disconnect_form=DISCONNECT_FORM if connected else "",
    )


def _delayed_system_call(cmd):
    time.sleep(1.5)
    subprocess.run(cmd)


class BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_hilfe_image(self, filename):
        """Screenshots fuer die VPN-Hilfeseite (vom Nutzer selbst nach
        HILFE_IMAGE_DIR gelegt) - optional, fehlende Dateien werden im
        <img onerror> auf der Seite ausgeblendet."""
        if not HILFE_IMAGE_NAME_RE.match(filename):
            self.send_response(400)
            self.end_headers()
            return
        try:
            with open(os.path.join(HILFE_IMAGE_DIR, filename), "rb") as f:
                data = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        ext = filename.rsplit(".", 1)[-1].lower()
        content_type = "image/png" if ext == "png" else "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _serve_backup_download(self, location, filename):
        if location not in ("local", "usb") or not BACKUP_NAME_RE.match(filename):
            self.send_response(400)
            self.end_headers()
            return
        directory = USB_MOUNT if location == "usb" else BACKUP_DIR
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def handle_system_action(self):
        """True, wenn der Pfad eine System-Aktion war (Reboot/Shutdown).
        Nur auf einem echten Pi erreichbar."""
        if self.path not in ("/system/reboot", "/system/shutdown"):
            return False
        if not IS_PI:
            self.send_response(404)
            self.end_headers()
            return True
        if self.path == "/system/reboot":
            self._send_html(PAGE_SYSTEM_ACTION.format(
                action="Neustart", verb="startet neu",
                hint="Diese Seite versucht in Kürze automatisch, sich neu zu verbinden, "
                     "und lädt sich dann selbst neu.",
                retry_script=RETRY_SCRIPT,
            ))
            threading.Thread(target=_delayed_system_call, args=(["systemctl", "reboot"],), daemon=True).start()
            return True
        if self.path == "/system/shutdown":
            self._send_html(PAGE_SYSTEM_ACTION.format(
                action="Herunterfahren", verb="fährt herunter",
                hint="Der Pi muss danach manuell wieder eingeschaltet werden "
                     "(Strom trennen/verbinden).",
                retry_script=STOP_SPINNER_SCRIPT,
            ))
            threading.Thread(target=_delayed_system_call, args=(["systemctl", "poweroff"],), daemon=True).start()
            return True
        return False

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/tipps":
            self._send_html(PAGE_TIPPS.format(header=render_header()))
            return
        if path == "/hilfe":
            self._send_html(PAGE_HILFE.format(header=render_header()))
            return
        if path == "/hilfe/vpn":
            self._send_html(PAGE_VPN.format(header=render_header()))
            return
        m = re.match(r"^/hilfe/bilder/([^/]+)$", path)
        if m:
            self.serve_hilfe_image(m.group(1))
            return
        if path == "/backup":
            self._send_html(render_backup_page())
            return
        if path == "/backup/restore":
            self._send_html(render_restore_page())
            return
        if path == "/backup/downloads":
            self._send_html(render_download_select_page())
            return
        if path == "/backup/usb/format-status":
            self._send_json(FORMAT_STATE)
            return
        if path == "/update":
            self._send_html(render_update_page())
            return
        if path == "/update/status":
            self._send_json(UPDATE_STATE)
            return
        if path in ("/wifi", "/wifi/status", "/wifi/networks") and not IS_PI:
            self.send_response(404)
            self.end_headers()
            return
        if path == "/wifi":
            self._send_html(render_form(self.headers.get("Host")))
            return
        if path == "/wifi/status":
            self._send_json(CONN_STATE)
            return
        if path == "/wifi/networks":
            self._send_json([{"ssid": s, "signal": signal} for s, signal in scan_networks()])
            return
        if path.startswith("/backup/download/"):
            rest = unquote(path[len("/backup/download/"):])
            location, _, filename = rest.partition("/")
            self._serve_backup_download(location, filename)
            return
        self._send_html(render_landing(self.headers.get("Host")))

    def do_POST(self):
        if self.handle_system_action():
            return
        path = self.path.split("?", 1)[0]
        if path == "/backup/create":
            ok, detail = create_backup_now()
            msg = ('<div class="msg ok">✅ Backup erstellt.</div>' if ok
                   else f'<div class="msg err">Fehler: {detail}</div>')
            self._send_html(render_backup_page(msg))
            return
        if path == "/backup/restore":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            backup_key = fields.get("backup_key", [""])[0]
            location, _, filename = backup_key.partition("|")
            if not filename:
                self._send_html(render_restore_page('<div class="msg err">Bitte ein Backup auswählen.</div>'))
                return
            ok, detail = restore_backup(location, filename)
            msg = (f'<div class="msg ok">✅ {detail}</div>' if ok else f'<div class="msg err">{detail}</div>')
            self._send_html(render_restore_page(msg))
            return
        if path == "/backup/restore-upload":
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            filename, data = parse_multipart_file(body, content_type)
            if not filename or not data:
                self._send_html(render_restore_page(
                    '<div class="msg err">Keine Datei hochgeladen oder Datei nicht lesbar.</div>'))
                return
            ok, detail = restore_backup_from_bytes(data, filename)
            msg = (f'<div class="msg ok">✅ {detail}</div>' if ok else f'<div class="msg err">{detail}</div>')
            self._send_html(render_restore_page(msg))
            return
        if path == "/backup/settings":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            max_backups = fields.get("max_backups", [""])[0]
            ok, detail = set_backup_settings(max_backups)
            msg = (f'<div class="msg ok">✅ {detail}</div>' if ok else f'<div class="msg err">{detail}</div>')
            self._send_html(render_backup_page(msg))
            return
        if path == "/backup/usb/format":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            device = fields.get("device", [""])[0]
            FORMAT_STATE.update(done=False, ok=None, detail=None)
            threading.Thread(target=_run_format_in_background, args=(device,), daemon=True).start()
            self._send_json({"started": True})
            return
        if path == "/backup/usb/mount":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            device = fields.get("device", [""])[0]
            ok, detail = mount_existing_usb(device)
            msg = (f'<div class="msg ok">✅ {detail}</div>' if ok else f'<div class="msg err">{detail}</div>')
            self._send_html(render_backup_page(msg))
            return
        if path == "/backup/usb/eject":
            ok, detail = eject_usb()
            msg = ('<div class="msg ok">✅ USB-Stick sicher entfernt.</div>' if ok
                   else f'<div class="msg err">Aushängen fehlgeschlagen: {detail}</div>')
            self._send_html(render_backup_page(msg, skip_remount=ok))
            return
        if path == "/update/run":
            UPDATE_STATE.update(done=False, ok=None, detail=None)
            threading.Thread(target=_run_update_in_background, daemon=True).start()
            self._send_json({"started": True})
            return
        if path == "/update/switch":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            tag = fields.get("tag", [""])[0]
            if not tag:
                self._send_json({"started": False, "error": "Keine Version ausgewählt."})
                return
            UPDATE_STATE.update(done=False, ok=None, detail=None)
            threading.Thread(target=_run_version_switch_in_background, args=(tag,), daemon=True).start()
            self._send_json({"started": True})
            return
        if path == "/update/settings":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            enabled = fields.get("auto_update", [""])[0] == "1"
            ok, detail = set_auto_update(enabled)
            msg = (f'<div class="msg ok">✅ {detail}</div>' if ok else f'<div class="msg err">{detail}</div>')
            self._send_html(render_update_page(msg))
            return
        if path in ("/wifi/connect", "/wifi/disconnect") and not IS_PI:
            self.send_response(404)
            self.end_headers()
            return
        if path == "/wifi/disconnect":
            ok, detail = disconnect_wifi()
            if ok:
                self._send_html(render_form(self.headers.get("Host"), '<div class="msg ok">🔌 WLAN getrennt.</div>'))
            else:
                self._send_html(render_form(
                    self.headers.get("Host"), f'<div class="msg err">Trennen fehlgeschlagen: {detail}</div>'))
            return
        if path == "/wifi/connect":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            ssid = (fields.get("ssid_manual", [""])[0] or fields.get("ssid", [""])[0]).strip()
            password = fields.get("password", [""])[0]
            if not ssid:
                self._send_html(render_form(
                    self.headers.get("Host"), '<div class="msg err">Bitte eine SSID auswaehlen oder eingeben.</div>'))
                return
            CONN_STATE.update(done=False, ok=None, detail=None)
            self._send_html(PAGE_CONNECTING.format(ssid=ssid, app_url=app_url(self.headers.get("Host"))))
            ok, detail = connect_wifi(ssid, password)
            CONN_STATE.update(done=True, ok=ok, detail=None if ok else detail)
            if ok:
                print(f"WLAN-Verbindung zu '{ssid}' erfolgreich.", file=sys.stderr)
            else:
                print(f"WLAN-Verbindung zu '{ssid}' fehlgeschlagen: {detail}", file=sys.stderr)
            return
        self.send_response(404)
        self.end_headers()


def main():
    server = ThreadingHTTPServer((HOST, PORT_LANDING), BaseHandler)
    print(f"HonigBox-Setup-Seite laeuft dauerhaft auf {HOST}:{PORT_LANDING}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    if "--check-update" in sys.argv:
        run_update_check_once()
    else:
        main()
