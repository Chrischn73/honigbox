"""Persistenz und Validierung der Einstellungen (Kamera, Foto-Zeitplan,
Pushover, Speicherort) ueber die /api/-Endpunkte."""
import os

from helpers import get, post


def test_kamera_einstellungen_rundtrip(server):
    base_url, mod = server
    status, data = get(base_url, "/api/kamera")
    assert status == 200
    assert data["werte"]["belichtungsmodus"] == "sport"  # seit 2026-08-08 Standard

    status, data = post(base_url, "/api/kamera", {"belichtungsmodus": "normal", "metering": "centre"})
    assert status == 200
    assert data["werte"]["belichtungsmodus"] == "normal"

    status, data = get(base_url, "/api/kamera")
    assert data["werte"]["belichtungsmodus"] == "normal", "Einstellung wurde nicht dauerhaft gespeichert"


def test_foto_zeitplan_dunkle_fotos_loeschen_standard_aktiv(server):
    """Regressionstest: Standard wurde 2026-08-08 explizit von False auf True
    geaendert (Nutzerwunsch)."""
    base_url, _ = server
    status, data = get(base_url, "/api/foto-zeitplan")
    assert status == 200
    assert data["werte"]["dunkle_fotos_loeschen"] is True


def test_foto_zeitplan_helligkeitsschwelle_wird_auf_erlaubten_bereich_geklemmt(server):
    base_url, mod = server
    feld = next(f for f in mod.FOTO_ZEITPLAN_FELDER if f["key"] == "helligkeitsschwelle")

    status, data = post(base_url, "/api/foto-zeitplan", {"helligkeitsschwelle": feld["max"] + 1000})
    assert status == 200
    assert data["werte"]["helligkeitsschwelle"] == feld["max"]

    status, data = post(base_url, "/api/foto-zeitplan", {"helligkeitsschwelle": feld["min"] - 1000})
    assert data["werte"]["helligkeitsschwelle"] == feld["min"]


def test_pushover_einstellungen_rundtrip(server):
    base_url, _ = server
    status, data = post(base_url, "/api/pushover", {"token": "abc123", "user": "xyz789"})
    assert status == 200
    status, data = get(base_url, "/api/pushover")
    assert data["werte"]["token"] == "abc123"
    assert data["werte"]["user"] == "xyz789"


def test_pushover_eskalation_texte_nennen_vier_und_34_minuten(server):
    """Regressionstest: Texte wurden 2026-08-08 von '3'/'33' Minuten auf
    '4'/'34' Minuten korrigiert (passend zu WAIT_ESCALATE_1=240s in honigbox.sh)."""
    base_url, _ = server
    status, data = get(base_url, "/api/pushover")
    schema = {m["id"]: m for m in data["meldungen_schema"]}
    assert "4 Minuten" in schema["eskalation1"]["label"]
    assert "34 Minuten" in schema["eskalation2"]["label"]
    assert "4 Minuten" in data["werte"]["meldungen"]["eskalation1"]["text"]
    assert "34 Minuten" in data["werte"]["meldungen"]["eskalation2"]["text"]


def test_speicher_einstellungen_ram_ist_standard(server):
    base_url, _ = server
    status, data = get(base_url, "/api/speicher")
    assert status == 200
    assert data["speicherort"] == "ram"


def test_einstellungen_schreibfehler_liefert_klare_fehlermeldung(server):
    """Regressionstest fuer den 2026-08-08 gefundenen Bug: ein OSError beim
    Schreiben (z.B. Berechtigungsproblem im Einstellungsordner) liess do_POST
    die Verbindung stumm abbrechen statt eine 500-JSON-Antwort zu liefern -
    im Frontend erschien dann NIE ein Fehler-Toast."""
    if os.geteuid() == 0:
        import pytest
        pytest.skip("Als root ignorieren Dateisystem-Berechtigungen den chmod-Test")
    base_url, mod = server
    os.chmod(mod.EINSTELLUNGEN_DIR, 0o555)
    try:
        status, data = post(base_url, "/api/kamera", {"belichtungsmodus": "normal"})
        assert status == 500
        assert "error" in data
    finally:
        os.chmod(mod.EINSTELLUNGEN_DIR, 0o777)
