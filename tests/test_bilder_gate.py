"""Regressionstest fuer einen beim Code-Review vom 2026-08-19 gefundenen Bug:
/api/foto/einzel (der manuelle "Foto aufnehmen"-Button) hatte kein Gegenstueck
zu honigbox.sh's foto_speicher_nicht_bereit() - im "Platte"-Speichermodus
haette ein Klick waehrend des Boot-Fensters (LUKS-Bilder-Container noch nicht
entschluesselt/angelegt) foto.sh direkt auf die unverschluesselte
Root-Partition schreiben lassen. bilder_bereit() in galerie_server.py
spiegelt jetzt bewusst dieselbe Logik."""
import json

from helpers import get, post


def test_bilder_bereit_im_ram_modus_immer_true(server):
    """Im RAM-Disk-Modus ist BILDER_DIR ein tmpfs, das systemd bereits vor
    dem Dienststart per RequiresMountsFor sicherstellt - kein LUKS-Gate."""
    _, mod = server
    assert mod.bilder_bereit() is True


def test_bilder_bereit_im_platte_modus_ohne_status_datei_false(server):
    """Anders als archiv_bereit(): im Platte-Modus gilt bei fehlender
    Status-Datei NICHT bereit (vorsichtigerer Default, weil dieser Pfad bei
    jeder Tueroeffnung automatisch beschrieben wird)."""
    base_url, mod = server
    mod.speichere_speicher_einstellungen("platte", 128)
    assert mod.bilder_bereit() is False


def test_bilder_bereit_im_platte_modus_mit_passendem_status(server, tmp_path, monkeypatch):
    base_url, mod = server
    mod.speichere_speicher_einstellungen("platte", 128)
    status_datei = tmp_path / "bilder-status"
    monkeypatch.setattr(mod, "BILDER_STATUS_PATH", str(status_datei))

    status_datei.write_text("locked")
    assert mod.bilder_bereit() is False

    status_datei.write_text("unlocked")
    assert mod.bilder_bereit() is True

    status_datei.write_text("fresh")
    assert mod.bilder_bereit() is True


def test_foto_einzel_liefert_503_wenn_bilder_nicht_bereit(server, monkeypatch):
    """Kernanforderung: der manuelle Foto-Button darf foto.sh gar nicht erst
    aufrufen, solange der Bilder-Container nicht bereit ist."""
    base_url, mod = server
    mod.speichere_speicher_einstellungen("platte", 128)

    aufgerufen = []
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: aufgerufen.append(a) or None)

    status, data = post(base_url, "/api/foto/einzel", {})
    assert status == 503
    assert not aufgerufen
