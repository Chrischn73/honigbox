"""Phase C - LUKS-Archiv-Verschluesselung: das Python-seitige Gate
(archiv_bereit()/_archiv_schluessel_erforderlich()) und die Route
/archiv-schluessel. Die eigentliche cryptsetup-Logik steckt in
archiv_entschluesseln.sh (Bash) und wird dort separat per Stub-Binaries
simuliert, nicht hier - dieses Modul deckt nur ab, wie galerie_server.py auf
die von jenem Skript erzeugten Status-/Schluessel-Dateien reagiert."""
import json
import os

import pytest

from helpers import get, post


@pytest.fixture
def archiv_dateien(tmp_path, monkeypatch, server):
    """Wie 'server', aber mit einem eigenen Run-Verzeichnis fuer die
    Status-/Schluessel-Dateien, die archiv_entschluesseln.sh normalerweise
    unter /run/honigbox anlegen wuerde."""
    base_url, mod = server
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(mod, "ARCHIV_STATUS_PATH", str(run_dir / "archiv-status"))
    monkeypatch.setattr(mod, "BILDER_STATUS_PATH", str(run_dir / "bilder-status"))
    monkeypatch.setattr(mod, "ARCHIV_RUN_DIR", str(run_dir))
    monkeypatch.setattr(mod, "ARCHIV_SCHLUESSEL_PATH", str(run_dir / "archiv-key"))
    monkeypatch.setattr(mod, "BILDER_SCHLUESSEL_PATH", str(run_dir / "bilder-key"))
    return base_url, mod, run_dir


def test_archiv_bereit_ohne_status_datei_ist_abwaertskompatibel(server):
    """Ohne Phase C (keine Status-Datei, z.B. auf noch nicht migrierten
    Installationen oder in JEDEM ANDEREN Test dieser Suite) muss sich das
    Archiv exakt wie vor Phase C verhalten - sonst waeren alle bisherigen
    Tests/Installationen ploetzlich vom LUKS-Gate betroffen, obwohl sie nie
    etwas davon wissen."""
    _, mod = server
    assert mod.archiv_bereit() is True


@pytest.mark.parametrize("status,erwartet", [
    ("unlocked", True), ("fresh", True), ("locked", False), ("fehler", False),
])
def test_archiv_bereit_je_nach_status(archiv_dateien, status, erwartet):
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text(status)
    assert mod.archiv_bereit() is erwartet


def test_archiv_routen_liefern_503_wenn_nicht_bereit(archiv_dateien):
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("locked")

    status, data = get(base_url, "/api/photos?archiv=1")
    assert status == 503
    assert "Archiv" in data["error"]

    status, data = post(base_url, "/api/photos/archivieren", {"dateien": ["x.jpg"]})
    assert status == 503

    status, data = post(base_url, "/api/photos/loeschen", {"dateien": ["x.jpg"], "archiv": True})
    assert status == 503

    status, data = post(base_url, "/api/archiv/notiz", {"datei": "x.jpg", "text": "Notiz"})
    assert status == 503


def test_normale_bilder_route_bleibt_unbetroffen(archiv_dateien):
    """Nur ARCHIV_DIR ist gegated - aktuelle Fotos (BILDER_DIR) duerfen vom
    Archiv-Gate nicht beeinflusst werden."""
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("locked")
    status, data = get(base_url, "/api/photos?archiv=0")
    assert status == 200


def test_gate_leitet_hauptseite_um_aber_nicht_api_oder_static(archiv_dateien):
    """Kernanforderung: waehrend das Archiv-Gate aktiv ist, muss die bereits
    laufende Seite trotzdem weiter Assets nachladen und JSON-Antworten von
    /api/ bekommen koennen - nur die Haupt-HTML-Seite wird umgeleitet."""
    import http.client
    from urllib.parse import urlparse

    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("locked")

    def raw_get(path):
        parsed = urlparse(base_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, headers, body

    status, headers, _ = raw_get("/")
    assert status == 302
    assert headers["Location"] == "/archiv-schluessel"

    status, _, _ = raw_get("/styles.css")
    assert status == 200

    status, data = get(base_url, "/api/status")
    assert status == 200
    assert data["archiv_bereit"] is False


def test_archiv_schluessel_seite_zeigt_wartehinweis_bei_locked(archiv_dateien):
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("locked")
    status, body = _raw_get_body(base_url, "/archiv-schluessel")
    assert status == 200
    assert b"Wiederherstellen" in body
    assert b"frisch anfangen" in body


def test_archiv_schluessel_seite_zeigt_download_bei_fresh(archiv_dateien):
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("fresh")
    (run_dir / "archiv-key").write_text("deadbeef" * 8)
    status, body = _raw_get_body(base_url, "/archiv-schluessel")
    assert status == 200
    assert "herunterladen".encode() in body.lower() or b"herunterladen" in body


def test_post_verwerfen_schreibt_neu_in_eingabedatei(archiv_dateien):
    """/archiv-schluessel ist bewusst eine normale HTML-<form>-Route
    (application/x-www-form-urlencoded, wie /einrichten/login/zuruecksetzen),
    kein JSON-Endpunkt - deshalb hier direkt per http.client statt
    helpers.post() (das JSON sendet)."""
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("locked")
    import http.client
    from urllib.parse import urlencode, urlparse
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = urlencode({"aktion": "verwerfen"}).encode()
    conn.request("POST", "/archiv-schluessel", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert (run_dir / "archiv-eingabe").read_text() == "NEU"


def test_post_bestaetigt_loescht_klartext_schluessel(archiv_dateien):
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("fresh")
    (run_dir / "archiv-key").write_text("deadbeef" * 8)

    import http.client
    from urllib.parse import urlencode, urlparse
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = urlencode({"aktion": "bestaetigt"}).encode()
    conn.request("POST", "/archiv-schluessel", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    conn.close()

    assert not (run_dir / "archiv-key").exists()
    assert (run_dir / "archiv-status").read_text() == "fresh"  # Status bleibt, nur der Klartext-Schluessel ist weg


def test_abgelaufener_klartext_schluessel_wird_automatisch_geloescht(archiv_dateien):
    """Der Schluessel soll nur EINMALIG kurz nach dem Erzeugen abrufbar
    sein, nicht dauerhaft - siehe ARCHIV_SCHLUESSEL_ANZEIGE_MAX_SEK."""
    base_url, mod, run_dir = archiv_dateien
    (run_dir / "archiv-status").write_text("fresh")
    key_datei = run_dir / "archiv-key"
    key_datei.write_text("deadbeef" * 8)
    alt = mod.time.time() - mod.ARCHIV_SCHLUESSEL_ANZEIGE_MAX_SEK - 10
    os.utime(str(key_datei), (alt, alt))

    zustand = mod._archiv_schluessel_status()
    assert zustand["archiv"]["schluessel"] is None
    assert not key_datei.exists()


def _raw_get_body(base_url, path):
    import http.client
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body
