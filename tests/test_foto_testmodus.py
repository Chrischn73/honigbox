"""Foto-Testmodus: zeitbegrenzter (30 Min.) Schalter, der beim naechsten
Einzelfoto-Button-Klick zusaetzlich die von der Kamera TATSAECHLICH
angewandten Werte anzeigt (--metadata, siehe foto.sh). subprocess.run() wird
gefaked, damit kein echtes foto.sh/rpicam-still gebraucht wird."""
import json
import os

from PIL import Image

from helpers import get, post


class _FakeCompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _fake_subprocess_run_factory(mod, metadata_inhalt):
    """Simuliert foto.sh: legt ein helles Testfoto in BILDER_DIR ab und
    schreibt - falls ein 2. Argument (Metadata-Pfad) uebergeben wurde - die
    gewuenschten Metadata als JSON dorthin, genau wie rpicam-still mit
    --metadata es tun wuerde."""
    def _fake_run(cmd, timeout=None):
        Image.new("RGB", (32, 24), (230, 230, 230)).save(
            os.path.join(mod.BILDER_DIR, "testfoto.jpg"), "JPEG")
        if len(cmd) > 1:
            with open(cmd[1], "w") as f:
                json.dump(metadata_inhalt, f)
        return _FakeCompletedProcess()
    return _fake_run


def test_foto_testmodus_standard_aus(server):
    base_url, _ = server
    status, data = get(base_url, "/api/foto/testmodus")
    assert status == 200
    assert data["rest_sekunden"] == 0


def test_foto_testmodus_aktivieren_und_deaktivieren(server):
    base_url, _ = server
    status, data = post(base_url, "/api/foto/testmodus", {"aktiv": True})
    assert status == 200
    assert 1790 <= data["rest_sekunden"] <= 1800  # 30 Minuten, etwas Toleranz

    status, data = get(base_url, "/api/foto/testmodus")
    assert 1790 <= data["rest_sekunden"] <= 1800, "Einstellung wurde nicht dauerhaft gespeichert"

    status, data = post(base_url, "/api/foto/testmodus", {"aktiv": False})
    assert status == 200
    assert data["rest_sekunden"] == 0


def test_foto_einzel_im_testmodus_liefert_metadata_und_konfiguration(server, monkeypatch):
    base_url, mod = server
    monkeypatch.setattr(
        mod.subprocess, "run",
        _fake_subprocess_run_factory(mod, {"ExposureTime": 12345, "Lux": 87.5}))
    post(base_url, "/api/foto/testmodus", {"aktiv": True})

    status, data = post(base_url, "/api/foto/einzel")
    assert status == 200
    assert data["testmodus"] is True
    assert data["metadata"] == {"ExposureTime": 12345, "Lux": 87.5}
    assert data["kamera_werte"]["belichtungsmodus"] == "sport"
    assert any(f["key"] == "belichtungsmodus" for f in data["kamera_felder"])
    assert not os.path.exists(mod.FOTO_TESTMODUS_METADATA_PATH), "Temporaere Metadata-Datei muss aufgeraeumt werden"


def test_foto_einzel_ohne_testmodus_liefert_keine_metadata(server, monkeypatch):
    base_url, mod = server
    monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess_run_factory(mod, {}))

    status, data = post(base_url, "/api/foto/einzel")
    assert status == 200
    assert "testmodus" not in data
    assert "metadata" not in data


def test_foto_einzel_im_testmodus_ohne_metadata_datei_liefert_none(server, monkeypatch):
    """rpicam-still-Version ohne --metadata-Unterstuetzung o.ae. - darf nicht
    mit einem Serverfehler enden, nur mit metadata=None."""
    base_url, mod = server

    def _fake_run_ohne_metadata(cmd, timeout=None):
        Image.new("RGB", (32, 24), (230, 230, 230)).save(
            os.path.join(mod.BILDER_DIR, "testfoto.jpg"), "JPEG")
        return _FakeCompletedProcess()

    monkeypatch.setattr(mod.subprocess, "run", _fake_run_ohne_metadata)
    post(base_url, "/api/foto/testmodus", {"aktiv": True})

    status, data = post(base_url, "/api/foto/einzel")
    assert status == 200
    assert data["testmodus"] is True
    assert data["metadata"] is None
