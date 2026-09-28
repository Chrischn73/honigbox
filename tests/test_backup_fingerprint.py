"""Fingerabdruck fuer "Backup nur bei Aenderung" (setup/honigbox-backup-fingerprint.py).

Anlass: Am 28.09.2026 hing das naechtliche Backup 14 h, weil das Skript die
lgpio-FIFO /opt/honigbox/.lgd-nfy0 mit open() lesen wollte."""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKRIPT = os.path.join(REPO_ROOT, "setup", "honigbox-backup-fingerprint.py")


def fingerabdruck(ordner):
    ergebnis = subprocess.run([sys.executable, SKRIPT, str(ordner)],
                              capture_output=True, text=True, timeout=10)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return ergebnis.stdout.strip()


def app_ordner(tmp_path):
    app = tmp_path / "honigbox"
    (app / "einstellungen").mkdir(parents=True)
    (app / "honigbox.sh").write_text("#!/bin/bash\n")
    (app / "einstellungen" / ".tuer-einstellungen.json").write_text('{"a": 1}')
    return app


def test_fifo_im_app_ordner_blockiert_nicht(tmp_path):
    app = app_ordner(tmp_path)
    os.mkfifo(app / ".lgd-nfy0")
    # Ohne Fix laeuft das in den timeout (subprocess.TimeoutExpired).
    assert len(fingerabdruck(app)) == 64


def test_fifo_aendert_fingerabdruck_nicht_bei_gleichem_inhalt(tmp_path):
    app = app_ordner(tmp_path)
    os.mkfifo(app / ".lgd-nfy0")
    vorher = fingerabdruck(app)
    assert fingerabdruck(app) == vorher


def test_inhaltsaenderung_aendert_fingerabdruck(tmp_path):
    app = app_ordner(tmp_path)
    vorher = fingerabdruck(app)
    (app / "einstellungen" / ".tuer-einstellungen.json").write_text('{"a": 2}')
    assert fingerabdruck(app) != vorher


def test_ephemere_datei_aendert_fingerabdruck_nicht(tmp_path):
    app = app_ordner(tmp_path)
    vorher = fingerabdruck(app)
    (app / "einstellungen" / ".letzte-oeffnung.json").write_text('{"letzte_oeffnung": 1}')
    assert fingerabdruck(app) == vorher
