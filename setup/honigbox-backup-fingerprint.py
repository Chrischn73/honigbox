#!/usr/bin/env python3
"""Fingerabdruck des HonigBox-App-Ordners fuer "Backup nur bei Aenderung".

Wird vom naechtlichen Timer-Lauf (honigbox-backup.sh --nur-bei-aenderung)
genutzt: stimmt der Fingerabdruck mit dem des letzten Backups ueberein, wird
kein neues Archiv geschrieben - ausserhalb der Saison aendert sich an Code
und Einstellungen oft wochenlang nichts, dann entstehen sonst jede Nacht
identische Archive auf der SD-Karte.

Gehasht wird der INHALT aller Dateien, die auch im Backup landen (also ohne
fotos/, siehe honigbox-backup.sh) - nicht die Aenderungszeit, weil
galerie_server.py beim Start u. a. Einstellungen bereinigt und ggf. mit
identischem Inhalt neu schreibt. Ausgenommen sind reine Laufzeit-/Signal-
dateien in einstellungen/, die sich ohne Zutun des Nutzers aendern
(EPHEMER unten, z. B. .status.json - schreibt honigbox.sh jede Sekunde).
Schreibt nichts, gibt nur den Hash aus.

Aufruf: honigbox-backup-fingerprint.py <app_ordner>
"""
import hashlib
import os
import sys

EPHEMER = {
    ".status.json",
    ".telegram-update-offset",
    ".telegram-pending-codes.json",
    ".tuer-neustart-signal",
    ".tuer-simulation-bis.json",
    ".pushover-stumm-bis.json",
    ".fotos-pause-bis.json",
    ".foto-testmodus-bis.json",
    ".foto-testmodus-metadata.json",
}


def fingerprint(src_dir):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d != "fotos"]
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if rel_root == "einstellungen" and name in EPHEMER:
                continue
            path = os.path.join(root, name)
            h.update(os.path.join(rel_root, name).encode() + b"\0")
            if os.path.islink(path):
                h.update(b"link:" + os.readlink(path).encode())
            else:
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            h.update(b"\0")
    return h.hexdigest()


def main():
    if len(sys.argv) != 2:
        print("Aufruf: honigbox-backup-fingerprint.py <app_ordner>", file=sys.stderr)
        return 2
    print(fingerprint(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
