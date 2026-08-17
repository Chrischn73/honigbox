"""Archivieren/Loeschen von Fotos - insbesondere der 2026-08-09 gefundene
Cross-Device-Bug (os.rename() scheitert, wenn BILDER_DIR als RAM-Disk/tmpfs
auf einem anderen Dateisystem liegt als ARCHIV_DIR)."""
import importlib
import os
import shutil
import threading
import uuid
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from conftest import REPO_ROOT
from helpers import get_raw, post


def _testfoto(pfad):
    Image.new("RGB", (32, 24), (200, 150, 50)).save(pfad, "JPEG")


def test_archivieren_verschiebt_foto_ins_archiv(server):
    base_url, mod = server
    datei = "20260101_180000.jpg"
    _testfoto(os.path.join(mod.BILDER_DIR, datei))

    status, data = post(base_url, "/api/photos/archivieren", {"dateien": [datei]})
    assert status == 200
    assert data["archiviert"] == 1
    assert not os.path.exists(os.path.join(mod.BILDER_DIR, datei))
    assert os.path.isfile(os.path.join(mod.ARCHIV_DIR, datei))


def test_loeschen_entfernt_thumbnail_mit(server):
    base_url, mod = server
    datei = "20260101_190000.jpg"
    _testfoto(os.path.join(mod.BILDER_DIR, datei))
    status, _ = get_raw(base_url, "/thumbs/" + datei)  # Thumbnail einmal erzeugen, wie die Galerie es taete
    assert status == 200
    assert os.path.isfile(mod._thumb_pfad(mod.BILDER_DIR, datei))

    status, data = post(base_url, "/api/photos/loeschen", {"dateien": [datei], "archiv": False})
    assert status == 200
    assert data["geloescht"] == 1
    assert not os.path.isfile(mod._thumb_pfad(mod.BILDER_DIR, datei))


@pytest.fixture
def cross_device_server(tmp_path, monkeypatch):
    """Wie die 'server'-Fixture in conftest.py, aber BILDER_DIR bewusst auf
    /dev/shm (echtes tmpfs) statt im selben tmp_path wie ARCHIV_DIR - nur so
    laesst sich der os.rename()-Cross-Device-Bug ehrlich nachstellen. Liegen
    beide Verzeichnisse (wie bei der einfachen 'server'-Fixture) auf
    demselben Dateisystem, wuerde os.rename() auch mit dem alten,
    fehlerhaften Code klappen und eine Regression bliebe unentdeckt."""
    if not os.path.isdir("/dev/shm") or not os.access("/dev/shm", os.W_OK):
        pytest.skip("/dev/shm nicht verfuegbar - kein echtes tmpfs zum Testen")
    shm_dir = f"/dev/shm/honigbox-test-{uuid.uuid4().hex}"
    os.makedirs(shm_dir)
    archiv = tmp_path / "archiv"
    einstellungen = tmp_path / "einstellungen"
    archiv.mkdir()

    monkeypatch.setenv("GALERIE_BILDER", shm_dir)
    monkeypatch.setenv("GALERIE_ARCHIV", str(archiv))
    monkeypatch.setenv("GALERIE_EINSTELLUNGEN_DIR", str(einstellungen))
    monkeypatch.setenv("GALERIE_STATIC", str(os.path.join(REPO_ROOT, "static")))
    monkeypatch.setenv("GALERIE_USER", "")
    monkeypatch.setenv("GALERIE_PASSWORT", "")
    monkeypatch.setenv("GALERIE_ZUGANG_AUS", "1")

    import galerie_server
    importlib.reload(galerie_server)

    if os.stat(shm_dir).st_dev == os.stat(str(archiv)).st_dev:
        shutil.rmtree(shm_dir, ignore_errors=True)
        pytest.skip("/dev/shm liegt hier ueberraschend auf demselben Dateisystem wie tmp_path")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), galerie_server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", galerie_server
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        shutil.rmtree(shm_dir, ignore_errors=True)


def test_archivieren_funktioniert_ueber_dateisystemgrenzen_hinweg(cross_device_server):
    """Regressionstest fuer den 2026-08-09 gefundenen Bug: os.rename() schlug
    mit 'Invalid cross-device link' fehl, sobald BILDER_DIR (RAM-Disk) und
    ARCHIV_DIR (immer SD-Karte) auf unterschiedlichen Dateisystemen lagen.
    Fix war shutil.move() statt os.rename()."""
    base_url, mod = cross_device_server
    datei = "20260101_170000.jpg"
    _testfoto(os.path.join(mod.BILDER_DIR, datei))

    status, data = post(base_url, "/api/photos/archivieren", {"dateien": [datei]})
    assert status == 200
    assert data["archiviert"] == 1
    assert not os.path.exists(os.path.join(mod.BILDER_DIR, datei))
    assert os.path.isfile(os.path.join(mod.ARCHIV_DIR, datei))
