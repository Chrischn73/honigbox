"""send_pushover.sh mit einem Attrappen-curl im PATH (kein echter Netzzugriff).
Anlass (2026-10-11): Auf einer frischen Box ohne jede Pushover-Konfiguration
(Weboberflaeche zeigt "aus") rief das Skript bei jedem Ereignis die Pushover-
API mit leerem Token auf - der Standard "aktiv" ist nur fuer alte
pushover.conf-Installationen gedacht."""
import os
import shutil
import subprocess

import pytest

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def box(tmp_path):
    """Minimaler /opt/honigbox-Nachbau + curl-Attrappe, die ihre Argumente protokolliert."""
    shutil.copy(os.path.join(PROJEKT, "send_pushover.sh"), tmp_path / "send_pushover.sh")
    (tmp_path / "einstellungen").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/curl-aufrufe"\nprintf \'{{"status":1}}\'\n')
    curl.chmod(0o755)
    return tmp_path


def _ausfuehren(box):
    env = dict(os.environ, PATH=f"{box / 'bin'}:{os.environ['PATH']}")
    return subprocess.run(["bash", str(box / "send_pushover.sh"), "boot"],
                          env=env, capture_output=True, text=True, timeout=30)


def _curl_aufrufe(box):
    pfad = box / "curl-aufrufe"
    return pfad.read_text().splitlines() if pfad.exists() else []


def test_ohne_jede_konfiguration_kein_aufruf(box):
    _ausfuehren(box)
    assert _curl_aufrufe(box) == [], "Frische Box ohne Pushover darf pushover.net nicht anfragen"


def test_leerer_token_kein_aufruf(box):
    (box / "einstellungen" / ".pushover-einstellungen.sh").write_text(
        "PUSHOVER_TOKEN=''\nPUSHOVER_USER=''\nPUSHOVER_AKTIV='1'\n")
    _ausfuehren(box)
    assert _curl_aufrufe(box) == []


def test_mit_zugangsdaten_wird_gesendet_und_zeile_abgeschlossen(box):
    (box / "einstellungen" / ".pushover-einstellungen.sh").write_text(
        "PUSHOVER_TOKEN='tok'\nPUSHOVER_USER='usr'\nPUSHOVER_AKTIV='1'\n")
    ergebnis = _ausfuehren(box)
    aufrufe = _curl_aufrufe(box)
    assert len(aufrufe) == 1
    assert "token=tok" in aufrufe[0] and "user=usr" in aufrufe[0]
    assert ergebnis.stdout.endswith("\n"), "Antwort ohne Zeilenumbruch haengt im Journal bis zum Dienst-Stopp"


def test_alte_pushover_conf_sendet_weiterhin(box):
    (box / "pushover.conf").write_text("PUSHOVER_TOKEN=tok\nPUSHOVER_USER=usr\n")
    _ausfuehren(box)
    assert len(_curl_aufrufe(box)) == 1, "Migrationsfall (alte pushover.conf) muss weiter senden"
