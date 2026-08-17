"""In-App-Zugangs-Passwort ("FritzBox-Stil", siehe Chat-Diskussion) -
Ersteinrichtung, Login, Cookie-Gueltigkeit, und dass das Gate tatsaechlich
ALLE Routen schuetzt (nicht nur die Startseite). Nutzt bewusst http.client
direkt statt helpers.get/post, weil Redirects/Set-Cookie/Form-POST gebraucht
werden, die die JSON-fokussierten Helfer nicht abdecken."""
import http.client
import json
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
