"""Persistenz und Validierung der Einstellungen (Kamera, Foto-Zeitplan,
Pushover, Speicherort) ueber die /api/-Endpunkte."""
import json
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


def test_foto_zeitplan_phase2_felder_haben_standardwerte(server):
    """Regressionstest: seit 2026-08-14 dreistufiger Zeitplan (Phase 1 -> Phase
    2 -> danach) statt zweistufig - die neuen Phase-2-Felder muessen auch ohne
    vorherige Speicherung sinnvolle Standardwerte liefern."""
    base_url, _ = server
    status, data = get(base_url, "/api/foto-zeitplan")
    assert status == 200
    assert data["werte"]["phase1_intervall_sekunden"] == 3
    assert data["werte"]["phase1_dauer_sekunden"] == 60
    assert data["werte"]["phase2_intervall_sekunden"] == 8
    assert data["werte"]["phase2_dauer_sekunden"] == 60
    assert data["werte"]["intervall_danach_sekunden"] == 15


def test_foto_zeitplan_migration_alter_zweistufiger_feldnamen(galerie_env):
    """Regressionstest fuer die Umbenennung intervall_1/schwelle_sekunden/
    intervall_2 -> phase1_intervall_sekunden/phase1_dauer_sekunden/
    intervall_danach_sekunden - bestehende Werte muessen erhalten bleiben,
    NICHT unter dem neuen (andersbedeutenden) Feld 'intervall_2' (jetzt Phase 2)
    landen."""
    mod = galerie_env
    legacy_pfad = os.path.join(os.path.dirname(mod.FOTO_ZEITPLAN_PATH), "legacy-foto-zeitplan.json")
    with open(legacy_pfad, "w") as f:
        json.dump({"intervall_1": 5, "schwelle_sekunden": 90, "intervall_2": 20, "max_anzahl": 40}, f)

    original_pfad = mod.FOTO_ZEITPLAN_PATH
    mod.FOTO_ZEITPLAN_PATH = legacy_pfad
    try:
        mod._migriere_foto_zeitplan_felder()
        with open(legacy_pfad) as f:
            migriert = json.load(f)
    finally:
        mod.FOTO_ZEITPLAN_PATH = original_pfad

    assert migriert["phase1_intervall_sekunden"] == 5
    assert migriert["phase1_dauer_sekunden"] == 90
    assert migriert["intervall_danach_sekunden"] == 20
    assert migriert["max_anzahl"] == 40
    assert "intervall_1" not in migriert
    assert "schwelle_sekunden" not in migriert
    assert "intervall_2" not in migriert


def test_pushover_einstellungen_rundtrip(server):
    base_url, _ = server
    status, data = post(base_url, "/api/pushover", {"token": "abc123", "user": "xyz789"})
    assert status == 200
    status, data = get(base_url, "/api/pushover")
    assert data["werte"]["token"] == "abc123"
    assert data["werte"]["user"] == "xyz789"


def test_pushover_aktiv_standard_aus_bei_erster_installation(server):
    """Regressionstest: seit 2026-08-14 bewusst AUS bei einer frischen
    Installation (Nutzerwunsch) - vorher war der Standard AN."""
    base_url, mod = server
    status, data = get(base_url, "/api/pushover")
    assert data["werte"]["aktiv"] is False

    status, data = post(base_url, "/api/pushover", {"token": "abc", "user": "xyz", "aktiv": True})
    assert status == 200
    assert data["werte"]["aktiv"] is True

    status, data = get(base_url, "/api/pushover")
    assert data["werte"]["aktiv"] is True, "Schalter wurde nicht dauerhaft gespeichert"

    with open(mod.PUSHOVER_SHELL_CONF_PATH) as f:
        assert "PUSHOVER_AKTIV='0'" in f.read()


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


def test_tuer_kontakt_invertiert_standard_aus(server):
    base_url, _ = server
    status, data = get(base_url, "/api/tuer-einstellungen")
    assert status == 200
    assert data["kontakt_invertiert"] is False


def test_tuer_kontakt_invertiert_rundtrip(server):
    base_url, _ = server
    status, data = post(base_url, "/api/tuer-einstellungen", {"kontakt_invertiert": True})
    assert status == 200
    assert data["kontakt_invertiert"] is True

    status, data = get(base_url, "/api/tuer-einstellungen")
    assert data["kontakt_invertiert"] is True, "Einstellung wurde nicht dauerhaft gespeichert"


def test_galerie_anzeige_standard_feed(server):
    """Standard seit 2026-08-11 bewusst auf 'feed' geaendert (Nutzerwunsch)."""
    base_url, _ = server
    status, data = get(base_url, "/api/galerie-anzeige")
    assert status == 200
    assert data["modus"] == "feed"


def test_galerie_anzeige_rundtrip(server):
    base_url, _ = server
    status, data = post(base_url, "/api/galerie-anzeige", {"modus": "einzelbild"})
    assert status == 200
    assert data["modus"] == "einzelbild"

    status, data = get(base_url, "/api/galerie-anzeige")
    assert data["modus"] == "einzelbild", "Einstellung wurde nicht dauerhaft gespeichert"


def test_galerie_anzeige_unbekannter_modus_faellt_auf_standard_zurueck(server):
    base_url, _ = server
    status, data = post(base_url, "/api/galerie-anzeige", {"modus": "irgendwas-erfundenes"})
    assert status == 200
    assert data["modus"] == "feed"


def test_status_letzte_oeffnung_ohne_statusdatei_ist_none(server):
    """Frische Installation, honigbox.sh hat noch nie geschrieben."""
    base_url, _ = server
    status, data = get(base_url, "/api/status")
    assert status == 200
    assert data["tuer_letzte_oeffnung"] is None


def test_status_letzte_oeffnung_wird_durchgereicht(server):
    """honigbox.sh schreibt letzte_oeffnung in .status.json - /api/status muss
    das 1:1 durchreichen (Formatierung/Anzeige macht das Frontend)."""
    base_url, mod = server
    with open(mod.STATUS_PATH, "w") as f:
        json.dump({"tuer_offen": False, "aktualisiert": 1000.0, "offen_seit": None, "letzte_oeffnung": 900.0}, f)

    status, data = get(base_url, "/api/status")
    assert status == 200
    assert data["tuer_letzte_oeffnung"] == 900.0


def test_extern_link_standard_aus(server):
    base_url, _ = server
    status, data = get(base_url, "/api/extern-link")
    assert status == 200
    assert data["aktiv"] is False
    assert data["url"] == ""
    assert data["label"] == "🐝 Verkauf erfassen"


def test_extern_link_rundtrip(server):
    base_url, _ = server
    status, data = post(base_url, "/api/extern-link",
                         {"aktiv": True, "url": "http://192.168.155.198:8090/", "label": "🍯 Zum Verkauf"})
    assert status == 200
    assert data["aktiv"] is True
    assert data["url"] == "http://192.168.155.198:8090/"
    assert data["label"] == "🍯 Zum Verkauf"

    status, data = get(base_url, "/api/extern-link")
    assert data["aktiv"] is True
    assert data["url"] == "http://192.168.155.198:8090/", "Einstellung wurde nicht dauerhaft gespeichert"
    assert data["label"] == "🍯 Zum Verkauf"


def test_extern_link_leerer_name_faellt_auf_standard_zurueck(server):
    base_url, _ = server
    status, data = post(base_url, "/api/extern-link", {"aktiv": True, "url": "", "label": "   "})
    assert status == 200
    assert data["label"] == "🐝 Verkauf erfassen"


def test_extern_link_lehnt_nicht_http_url_ab(server):
    base_url, _ = server
    status, data = post(base_url, "/api/extern-link", {"aktiv": True, "url": "javascript:alert(1)"})
    assert status == 400
    assert "http" in data["error"]


def test_pushover_stumm_mit_gueltiger_dauer(server):
    base_url, _ = server
    status, data = post(base_url, "/api/pushover/stumm", {"aktiv": True, "dauer_minuten": 10})
    assert status == 200
    assert 595 <= data["rest_sekunden"] <= 600

    status, data = post(base_url, "/api/pushover/stumm", {"aktiv": False})
    assert data["rest_sekunden"] == 0


def test_pushover_stumm_mit_unzulaessiger_dauer_faellt_auf_standard_zurueck(server):
    """Regressionstest: Standard-Dauer wurde 2026-08-10 auf 5 Minuten
    festgelegt (vorher 30) - siehe PUSHOVER_STUMM_DAUER_STANDARD_MIN."""
    base_url, _ = server
    status, data = post(base_url, "/api/pushover/stumm", {"aktiv": True, "dauer_minuten": 7})
    assert status == 200
    assert 295 <= data["rest_sekunden"] <= 300


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
