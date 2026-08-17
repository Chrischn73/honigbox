"""In-App-Zugangs-Passwort ("FritzBox-Stil", siehe Chat-Diskussion) -
Ersteinrichtung, Login, Cookie-Gueltigkeit, und dass das Gate tatsaechlich
ALLE Routen schuetzt (nicht nur die Startseite). Nutzt bewusst http.client
direkt statt helpers.get/post, weil Redirects/Set-Cookie/Form-POST gebraucht
werden, die die JSON-fokussierten Helfer nicht abdecken."""
import http.client
import json
import os
from urllib.parse import urlencode, urlparse

import pytest


def _request(base_url, method, path, form=None, cookie=None):
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    headers = {}
    body = None
    if form is not None:
        body = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        gelesen = resp.read()
        return resp.status, dict(resp.getheaders()), gelesen
    finally:
        conn.close()


def _cookie_aus_antwort(headers):
    roh = headers.get("Set-Cookie", "")
    return roh.split(";", 1)[0] if roh else None


def _json_post(base_url, path, body=None, cookie=None):
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request("POST", path, body=json.dumps(body or {}).encode(), headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode())
    finally:
        conn.close()


@pytest.fixture
def zugang_server(server, monkeypatch):
    """Wie server, aber mit tatsaechlich AKTIVEM Zugangs-Gate (die
    galerie_env-Fixture deaktiviert es sonst global, siehe conftest.py)."""
    base_url, mod = server
    monkeypatch.setattr(mod, "ZUGANG_DEAKTIVIERT", False)
    return base_url, mod


def test_ohne_einrichtung_leitet_alles_auf_einrichten_um(zugang_server):
    base_url, _ = zugang_server
    status, headers, _ = _request(base_url, "GET", "/")
    assert status == 302
    assert headers["Location"] == "/einrichten"

    status, _, body = _request(base_url, "GET", "/einrichten")
    assert status == 200
    assert b"Passwort festlegen" in body


def test_api_route_ist_auch_ohne_einrichtung_geschuetzt(zugang_server):
    """Kernanforderung: das Gate schuetzt ALLE Routen, nicht nur die
    Startseite - sonst liesse sich die Galerie am Login vorbei per direktem
    API-Aufruf auslesen."""
    base_url, _ = zugang_server
    status, headers, _ = _request(base_url, "GET", "/api/status")
    assert status == 302
    assert headers["Location"] == "/einrichten"


def test_styles_css_bleibt_ohne_zugang_erreichbar(zugang_server):
    """Sonst liesse sich die Login-/Ersteinrichtungs-Seite selbst nicht stylen."""
    base_url, _ = zugang_server
    status, _, _ = _request(base_url, "GET", "/styles.css")
    assert status == 200


def test_ersteinrichtung_setzt_passwort_und_gewaehrt_zugang(zugang_server):
    base_url, mod = zugang_server
    status, headers, _ = _request(
        base_url, "POST", "/einrichten", form={"passwort": "geheim123", "passwort2": "geheim123"})
    assert status == 302
    assert headers["Location"] == "/"
    cookie = _cookie_aus_antwort(headers)
    assert cookie is not None
    assert mod.zugang_eingerichtet() is True

    status, _, body = _request(base_url, "GET", "/api/status", cookie=cookie)
    assert status == 200
    assert json.loads(body)["galerie_service"] == "active"

    # Ohne den Cookie (anderer Browser) weiterhin gesperrt, jetzt Richtung Login.
    status, headers, _ = _request(base_url, "GET", "/")
    assert status == 302
    assert headers["Location"] == "/login"


def test_ersteinrichtung_mit_nicht_uebereinstimmenden_passwoertern(zugang_server):
    base_url, mod = zugang_server
    status, _, body = _request(
        base_url, "POST", "/einrichten", form={"passwort": "geheim123", "passwort2": "anders456"})
    assert status == 200
    assert "stimmen nicht" in body.decode()
    assert mod.zugang_eingerichtet() is False


def test_ersteinrichtung_mit_zu_kurzem_passwort(zugang_server):
    base_url, mod = zugang_server
    status, _, body = _request(
        base_url, "POST", "/einrichten", form={"passwort": "ab", "passwort2": "ab"})
    assert status == 200
    assert "mindestens" in body.decode()
    assert mod.zugang_eingerichtet() is False


def test_login_mit_korrektem_und_falschem_passwort(zugang_server):
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("geheim123")

    status, _, body = _request(base_url, "POST", "/login", form={"passwort": "falsch000"})
    assert status == 200
    assert "Falsches Passwort" in body.decode()

    status, headers, _ = _request(base_url, "POST", "/login", form={"passwort": "geheim123"})
    assert status == 302
    assert headers["Location"] == "/"
    cookie = _cookie_aus_antwort(headers)
    status, _, _ = _request(base_url, "GET", "/api/status", cookie=cookie)
    assert status == 200


def test_reset_invalidiert_alte_cookies(zugang_server):
    """Kernanforderung: ein Passwort-Reset meldet auch ANDERE Browser ab,
    nicht nur den, der den Reset ausgeloest hat."""
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("altesPasswort1")
    alter_cookie = f"{mod.ZUGANG_COOKIE_NAME}={mod._zugang_cookie_sollwert()}"
    status, _, _ = _request(base_url, "GET", "/api/status", cookie=alter_cookie)
    assert status == 200

    mod.setze_zugang_passwort("neuesPasswort2")

    status, headers, _ = _request(base_url, "GET", "/api/status", cookie=alter_cookie)
    assert status == 302
    assert headers["Location"] == "/login"


def test_pruefe_zugang_passwort_direkt(galerie_env):
    mod = galerie_env
    mod.setze_zugang_passwort("meinPasswort1")
    assert mod.pruefe_zugang_passwort("meinPasswort1") is True
    assert mod.pruefe_zugang_passwort("falschesPW") is False
    assert mod.pruefe_zugang_passwort("") is False


def test_setze_zugang_passwort_lehnt_zu_kurzes_passwort_ab(galerie_env):
    mod = galerie_env
    with pytest.raises(ValueError):
        mod.setze_zugang_passwort("ab")


def test_reset_nur_kurz_nach_neustart_moeglich(zugang_server):
    """Kernanforderung: der Reset-Link darf nicht dauerhaft erreichbar sein -
    sonst waere ein gestohlener Session-Cookie nutzlos, aber der physische
    Neustart-Schutz waere es dann auch (jeder koennte jederzeit resetten)."""
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("altesPasswort1")
    assert mod.zugang_reset_moeglich() is True

    status, _, body = _request(base_url, "GET", "/login")
    assert b"/zuruecksetzen" in body

    status, _, body = _request(base_url, "GET", "/zuruecksetzen")
    assert status == 200
    assert b"ALLE gespeicherten Fotos" in body

    # Fenster (per Prozessstart-Zeitpunkt) als abgelaufen simulieren.
    mod._ZUGANG_PROZESS_START -= mod.ZUGANG_RESET_FENSTER_SEKUNDEN + 1
    assert mod.zugang_reset_moeglich() is False

    status, _, body = _request(base_url, "GET", "/login")
    assert b"/zuruecksetzen" not in body

    status, headers, _ = _request(base_url, "GET", "/zuruecksetzen")
    assert status == 302
    assert headers["Location"] == "/login"

    status, _, body = _request(
        base_url, "POST", "/zuruecksetzen", form={"passwort": "neuABC123", "passwort2": "neuABC123"})
    assert status == 302
    assert mod.pruefe_zugang_passwort("altesPasswort1") is True  # unveraendert, Reset wurde nicht ausgefuehrt


def test_reset_loescht_alle_fotos_und_invalidiert_cookies(zugang_server, tmp_path):
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("altesPasswort1")
    alter_cookie = f"{mod.ZUGANG_COOKIE_NAME}={mod._zugang_cookie_sollwert()}"

    with open(os.path.join(mod.BILDER_DIR, "aktuell.jpg"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(mod.ARCHIV_DIR, "archiviert.jpg"), "wb") as f:
        f.write(b"x")

    status, _, body = _request(
        base_url, "POST", "/zuruecksetzen", form={"passwort": "neuesPasswort2", "passwort2": "andersXYZ"})
    assert status == 200
    assert "stimmen nicht" in body.decode()
    assert os.path.isfile(os.path.join(mod.BILDER_DIR, "aktuell.jpg"))  # bei Fehler bleiben Fotos unberuehrt

    status, headers, _ = _request(
        base_url, "POST", "/zuruecksetzen", form={"passwort": "neuesPasswort2", "passwort2": "neuesPasswort2"})
    assert status == 302
    assert headers["Location"] == "/"
    neuer_cookie = _cookie_aus_antwort(headers)

    assert mod.liste_bilder(mod.BILDER_DIR) == []
    assert mod.liste_bilder(mod.ARCHIV_DIR) == []
    assert mod.pruefe_zugang_passwort("neuesPasswort2") is True

    # Alter Cookie (z.B. anderer Browser) ist nach dem Reset abgemeldet.
    status, headers, _ = _request(base_url, "GET", "/api/status", cookie=alter_cookie)
    assert status == 302
    assert headers["Location"] == "/login"

    status, _, _ = _request(base_url, "GET", "/api/status", cookie=neuer_cookie)
    assert status == 200


def test_neustart_verlangt_aktuelles_passwort(zugang_server, monkeypatch):
    """Ein gestohlener Session-Cookie allein darf keinen Neustart ausloesen
    koennen - sonst liesse sich darueber das Reset-Zeitfenster erzwingen
    (siehe zugang_reset_moeglich(), das an den Prozessstart gekoppelt ist)."""
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("aktuellesPW1")
    cookie = f"{mod.ZUGANG_COOKIE_NAME}={mod._zugang_cookie_sollwert()}"

    aufrufe = []
    monkeypatch.setattr(mod, "_system_aktion", lambda cmd: aufrufe.append(cmd) or (True, None))

    status, _ = _json_post(base_url, "/api/system/neustart", {}, cookie=cookie)
    assert status == 403
    status, _ = _json_post(base_url, "/api/system/neustart", {"passwort": "falsch"}, cookie=cookie)
    assert status == 403
    assert not aufrufe

    status, data = _json_post(base_url, "/api/system/neustart", {"passwort": "aktuellesPW1"}, cookie=cookie)
    assert status == 200
    assert data["ok"] is True
    assert aufrufe == [["sudo", "-n", "/usr/bin/systemctl", "reboot"]]


def test_herunterfahren_verlangt_aktuelles_passwort(zugang_server, monkeypatch):
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("aktuellesPW1")
    cookie = f"{mod.ZUGANG_COOKIE_NAME}={mod._zugang_cookie_sollwert()}"
    monkeypatch.setattr(mod, "_system_aktion", lambda cmd: (True, None))

    status, _ = _json_post(base_url, "/api/system/herunterfahren", {"passwort": "falsch"}, cookie=cookie)
    assert status == 403

    status, data = _json_post(base_url, "/api/system/herunterfahren", {"passwort": "aktuellesPW1"}, cookie=cookie)
    assert status == 200
    assert data["ok"] is True


def test_dienste_neustart_verlangt_aktuelles_passwort(zugang_server, monkeypatch):
    """Kernanforderung: dieser Endpunkt startet honigbox-galerie.service (den
    Prozess selbst) neu - das wuerde _ZUGANG_PROZESS_START zuruecksetzen und
    ohne Passwortschutz das Reset-Fenster oeffnen, obwohl kein echter
    Geraete-Neustart stattfand."""
    base_url, mod = zugang_server
    mod.setze_zugang_passwort("aktuellesPW1")
    cookie = f"{mod.ZUGANG_COOKIE_NAME}={mod._zugang_cookie_sollwert()}"
    monkeypatch.setattr(mod, "_system_aktion", lambda cmd: (True, None))
    monkeypatch.setattr(mod, "_verzoegerter_dienst_restart", lambda unit: None)

    status, _ = _json_post(base_url, "/api/system/dienste-neustart", {"passwort": "falsch"}, cookie=cookie)
    assert status == 403

    status, data = _json_post(
        base_url, "/api/system/dienste-neustart", {"passwort": "aktuellesPW1"}, cookie=cookie)
    assert status == 200
    assert data["ok"] is True
