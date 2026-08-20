# HonigBox

Die HonigBox ist eine unbemannte Honig-Verkaufsbox mit Tür-Überwachung: Ein
Raspberry Pi erkennt über einen Tür-/Deckelkontakt, wenn die Box geöffnet
wird, macht dabei automatisch Fotos mit der Pi-Kamera und benachrichtigt den
Betreiber per Pushover und/oder Telegram. Über eine Web-Galerie lassen sich
die Fotos ansehen, Einstellungen ändern und (optional) ein verschlüsseltes
Fotoarchiv verwalten.

Das Projekt ist bewusst ohne externe Python-Abhängigkeiten geschrieben (nur
Python-Standardbibliothek) und läuft komplett lokal auf dem Pi, ohne Cloud-
Anbindung.

## Funktionen

- **Tür-/Deckel-Überwachung**: Kontaktschalter an GPIO17, löst beim Öffnen
  eine Fotoserie aus (zwei Phasen mit einstellbarer Dauer/Intervall, danach
  ein langsameres Intervall).
- **Foto-Galerie** (Port 8090): Fotos ansehen, löschen, Einstellungen für
  Kamera (Belichtung, Fokus, Zoom, Auflösung) und Zeitplan ändern, manuelle
  Testfotos auslösen.
- **Benachrichtigungen**: Pushover und/oder Telegram, jeweils optional
  aktivierbar, mit eigenen Zugangsdaten pro Installation.
- **Passwortschutz** für die Einstellungsseiten, inkl. zeitlich begrenztem
  Reset-Fenster nach einem Neustart (falls das Passwort vergessen wurde).
- **Optionale Archiv-Verschlüsselung** (LUKS/`cryptsetup`): Das Fotoarchiv
  kann in einem verschlüsselten Container liegen; der Schlüssel wird nur
  einmal angezeigt und muss selbst sicher aufgehoben werden - ohne ihn sind
  die Archiv-Fotos nach einem Stromausfall/Neustart nicht mehr einsehbar.
- **Flexibler Speicherort**: Fotos je nach Wunsch im RAM (tmpfs, schont die
  SD-Karte) oder dauerhaft auf der SD-Karte/Platte.
- **Backup & Update-Check** über systemd-Timer.
- **Gemeinsames Setup-Portal** (Port 80, eigenes Repo
  [Chrischn73/setup-portal](https://github.com/Chrischn73/setup-portal)):
  WLAN-Einrichtung, Backup, Updates - wird bei der Installation automatisch
  nachgeladen, falls noch nicht vorhanden.

## Voraussetzungen

- Raspberry Pi mit frisch installiertem Raspberry Pi OS
- Raspberry Pi Kameramodul (getestet mit Kamera Modul 3)
- Tür-/Deckelkontaktschalter zwischen GPIO17 (Pin 11) und einem GND-Pin
  (z. B. Pin 9)
- Internetverbindung während der Installation

## Installation

1. Diesen Projekt-Ordner komplett auf den Pi kopieren, z. B. nach
   `/opt/honigbox-setup` (z. B. per `scp`/`rsync` oder USB-Stick).
2. Installationsskript mit Root-Rechten ausführen:

   ```bash
   sudo bash /opt/honigbox-setup/setup/install.sh
   ```

3. Das Skript richtet automatisch ein: benötigte Pakete, GPIO-/Kamera-
   Zugriff, systemd-Dienste für Tür-Überwachung und Galerie, Speicherort für
   Fotos, optional die Archiv-Verschlüsselung, und registriert die HonigBox
   im gemeinsamen Setup-Portal.
4. Falls der Hostname dabei geändert wird (Standard: `honigbox`), startet
   der Pi einmal automatisch neu. Danach einfach das Skript **erneut**
   ausführen - es ist mehrfach ausführbar (idempotent) und überschreibt
   bereits vorhandene Zugangsdaten oder Fotos nicht.
5. Tür-/Deckelkontaktschalter an GPIO17 (Pin 11) und GND (z. B. Pin 9)
   anschließen, falls noch nicht geschehen.

Nach der Installation ist die Galerie unter `http://honigbox.local:8090`
erreichbar, das Setup-Portal (WLAN/Backup/Update) unter
`http://honigbox.local`.

### Aktualisieren

Um eine bestehende Installation zu aktualisieren, den Projekt-Ordner mit dem
neuen Stand überschreiben (z. B. per `rsync`) und `setup/install.sh` erneut
ausführen.

## Entwicklung

Die Tests laufen mit `pytest` (Verzeichnis `tests/`):

```bash
pytest
```

Sie starten den Galerie-Server isoliert (temporäre Verzeichnisse, gefakte
Kamera-Aufrufe) und benötigen keine Pi-Hardware.
