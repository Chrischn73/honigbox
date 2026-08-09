# Tests

Automatisierte Tests für `galerie_server.py` - gezielt für die Bereiche, in
denen bei echten Pi-Testrunden schon reale Bugs gefunden wurden: Einstellungen-
Persistenz, Archivieren/Löschen (inkl. RAM-Disk-Cross-Device-Fall), das
Notiz-Feld für Archiv-Fotos, und die helligkeitsbasierte Foto-Löschung.

Bewusst **nicht** abgedeckt (zu aufwändig für den Nutzen bei einem
Ein-Personen-Hobbyprojekt): `honigbox.sh` (Tür-Logik/GPIO), `speicher_umschalten.sh`,
und die Backup/Update/Cross-Install-Logik des gemeinsamen Setup-Portals.

## Voraussetzungen

```bash
pip install --user pytest pillow
```

(Pillow wird bereits von `galerie_server.py` selbst gebraucht und ist auf dem
Pi normalerweise schon installiert.)

## Ausführen

```bash
cd honigbox-webseite
pytest tests/
```

Braucht keinen echten Raspberry Pi, keine GPIO/Kamera-Hardware - jeder Test
läuft gegen einen echten, aber temporären `galerie_server.py`-Serverprozess
mit Testverzeichnissen. Ein Test (Cross-Device-Archivieren) wird automatisch
übersprungen, falls kein beschreibbares `/dev/shm` vorhanden ist.
