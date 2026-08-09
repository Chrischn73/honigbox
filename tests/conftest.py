# Testsuite fuer galerie_server.py - deckt gezielt die Stellen ab, an denen
# in echten Pi-Testrunden bereits reale Bugs gefunden wurden (siehe README.md
# in diesem Ordner): Einstellungen-Persistenz, Archiv/RAM-Disk-Verschieben,
# Notizen-API, Helligkeits-/Loeschlogik. Reine Python-Standardbibliothek +
# pytest - KEIN echter Pi/GPIO/Kamera noetig, alles ueber Umgebungsvariablen
# auf temporaere Testverzeichnisse umgeleitet.
import importlib
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture
def galerie_env(tmp_path, monkeypatch):
    """Isolierte Bilder-/Archiv-/Einstellungen-Ordner pro Test. galerie_server.py
    liest seine Pfad-Konstanten (BILDER_DIR usw.) nur EINMAL beim Import aus
    den Umgebungsvariablen - deshalb hier ein erzwungener Modul-Reload NACH
    dem Setzen der Variablen, sonst wuerden alle Tests denselben (den zuerst
    importierten) Satz Verzeichnisse teilen."""
    bilder = tmp_path / "bilder"
    archiv = tmp_path / "archiv"
    einstellungen = tmp_path / "einstellungen"
    bilder.mkdir()
    archiv.mkdir()

    monkeypatch.setenv("GALERIE_BILDER", str(bilder))
    monkeypatch.setenv("GALERIE_ARCHIV", str(archiv))
    monkeypatch.setenv("GALERIE_EINSTELLUNGEN_DIR", str(einstellungen))
    monkeypatch.setenv("GALERIE_STATIC", str(os.path.join(REPO_ROOT, "static")))
    monkeypatch.setenv("GALERIE_USER", "")
    monkeypatch.setenv("GALERIE_PASSWORT", "")

    import galerie_server
    importlib.reload(galerie_server)
    return galerie_server


@pytest.fixture
def server(galerie_env):
    """Echter HTTP-Server auf einem freien Port (kein main() - das wuerde
    zusaetzlich die Aufraeum-/Speicher-/Kamera-Wache-Hintergrundschleifen
    starten, die fuer diese Tests nicht gebraucht werden und echte
    Systembefehle wie rpicam-still aufrufen wuerden)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), galerie_env.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", galerie_env
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
