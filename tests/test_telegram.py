"""Telegram-Anbindung (zweiter Benachrichtigungskanal neben Pushover):
Einstellungen-Persistenz, Verbindungscode-Erzeugung und die Update-Zuordnung
in telegram_update_verarbeiten() - der eigentliche Netzwerk-Poll
(telegram_wache_schleife) wird bewusst NICHT getestet, siehe README.md."""
import io
import json
import urllib.request

from helpers import get, post


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _fake_urlopen_factory(antworten):
    """antworten: Liste von dicts, die der Reihe nach als JSON-Antwort
    zurueckgegeben werden (ein Eintrag pro urlopen()-Aufruf an api.telegram.org).
    Alle anderen URLs (insbesondere die Testclient-Anfragen an den lokalen
    Testserver selbst - server und Testclient laufen im selben Prozess, teilen
    sich also dasselbe urllib.request-Modul) gehen an den echten urlopen,
    sonst wuerde das Faken hier auch die eigenen HTTP-Testanfragen abfangen."""
    echter_urlopen = urllib.request.urlopen
    rufe = {"n": 0}

    def _fake_urlopen(*args, **kwargs):
        ziel = args[0]
        url = ziel.full_url if isinstance(ziel, urllib.request.Request) else ziel
        if "api.telegram.org" not in url:
            return echter_urlopen(*args, **kwargs)
        i = min(rufe["n"], len(antworten) - 1)
        rufe["n"] += 1
        return FakeResponse(json.dumps(antworten[i]).encode())

    return _fake_urlopen


def test_telegram_einstellungen_rundtrip_ohne_token(server):
    """Ohne Token wird kein getMe-Aufruf gemacht, direkt gespeichert."""
    base_url, _ = server
    status, data = post(base_url, "/api/telegram", {"bot_token": ""})
    assert status == 200
    assert data["werte"]["bot_token"] == ""
    assert data["warnung"] is None

    status, data = get(base_url, "/api/telegram")
    assert data["werte"]["bot_token"] == ""
    assert data["chats"] == {}


def test_telegram_einstellungen_speichert_bestaetigten_token(server, monkeypatch):
    base_url, mod = server
    monkeypatch.setattr(
        mod.urllib.request, "urlopen",
        _fake_urlopen_factory([{"ok": True, "result": {"username": "MeinBot"}}]))

    status, data = post(base_url, "/api/telegram", {"bot_token": "abc123"})
    assert status == 200
    assert data["werte"]["bot_token"] == "abc123"
    assert data["werte"]["bot_username"] == "MeinBot"
    assert data["warnung"] is None

    status, data = get(base_url, "/api/telegram")
    assert data["werte"]["bot_token"] == "abc123", "Token wurde nicht dauerhaft gespeichert"
    assert data["werte"]["bot_username"] == "MeinBot"


def test_telegram_einstellungen_speichert_auch_bei_ungueltigem_token(server, monkeypatch):
    """Regressionstest: ein nicht bestaetigbarer Token (z.B. Tippfehler ODER
    kurzer Netzwerkaussetzer) wird trotzdem gespeichert - nur mit Warnung,
    siehe Docstring von speichere_telegram_einstellungen()."""
    base_url, mod = server
    monkeypatch.setattr(
        mod.urllib.request, "urlopen",
        _fake_urlopen_factory([{"ok": False, "description": "Unauthorized"}]))

    status, data = post(base_url, "/api/telegram", {"bot_token": "falscher-token"})
    assert status == 200
    assert data["werte"]["bot_token"] == "falscher-token"
    assert data["warnung"] is not None
    assert "Unauthorized" in data["warnung"]

    status, data = get(base_url, "/api/telegram")
    assert data["werte"]["bot_token"] == "falscher-token"


def test_telegram_meldungen_standard_alle_aktiv(server):
    """Ohne vorherigen Save sollen alle Meldungstypen aus PUSHOVER_MELDUNGEN_SCHEMA
    mit aktiv=True erscheinen - auch bei einer ganz frischen Installation."""
    base_url, mod = server
    status, data = get(base_url, "/api/telegram")
    assert status == 200
    ids = {s["id"] for s in mod.PUSHOVER_MELDUNGEN_SCHEMA}
    assert set(data["werte"]["meldungen"].keys()) == ids
    assert all(m["aktiv"] is True for m in data["werte"]["meldungen"].values())
    assert data["meldungen_schema"] == mod.PUSHOVER_MELDUNGEN_SCHEMA


def test_telegram_meldungen_pro_kanal_getrennt_von_pushover(server):
    """Kernanforderung: 'boot' nur ueber Telegram, der Rest nur ueber Pushover -
    beide Kanaele muessen unabhaengig voneinander schaltbar sein."""
    base_url, mod = server
    status, data = post(base_url, "/api/pushover", {
        "token": "", "user": "",
        "meldungen": {"boot": {"aktiv": False, "text": "Pi an"}, "geoeffnet": {"aktiv": True, "text": "Tür auf"}},
    })
    assert status == 200
    assert data["werte"]["meldungen"]["boot"]["aktiv"] is False

    status, data = post(base_url, "/api/telegram", {
        "bot_token": "", "aktiv": True,
        "meldungen": {"boot": {"aktiv": True}, "geoeffnet": {"aktiv": False}},
    })
    assert status == 200
    assert data["werte"]["meldungen"]["boot"]["aktiv"] is True
    assert data["werte"]["meldungen"]["geoeffnet"]["aktiv"] is False

    status, data = get(base_url, "/api/pushover")
    assert data["werte"]["meldungen"]["boot"]["aktiv"] is False, "Pushover-Schalter darf von Telegram-Save unberuehrt bleiben"

    with open(mod.TELEGRAM_SHELL_CONF_PATH) as f:
        conf = f.read()
    assert "TG_ENABLED_boot='1'" in conf
    assert "TG_ENABLED_geoeffnet='0'" in conf

    with open(mod.PUSHOVER_SHELL_CONF_PATH) as f:
        pushover_conf = f.read()
    assert "ENABLED_boot='0'" in pushover_conf, "Pushovers eigener Schalter muss weiterhin getrennt funktionieren"


def test_telegram_meldungen_unvollstaendige_uebergabe_faellt_pro_id_auf_aktiv_zurueck(server):
    """Fehlt eine Id im POST-Body (z.B. alter Client), soll sie nicht verschwinden,
    sondern mit aktiv=True (Standard) erhalten bleiben."""
    base_url, _ = server
    status, data = post(base_url, "/api/telegram", {"bot_token": "", "meldungen": {"boot": {"aktiv": False}}})
    assert status == 200
    assert data["werte"]["meldungen"]["boot"]["aktiv"] is False
    assert data["werte"]["meldungen"]["geschlossen"]["aktiv"] is True


def test_telegram_aktiv_standard_aus_bei_erster_installation(server):
    """Regressionstest: seit 2026-08-14 bewusst AUS bei einer frischen
    Installation (Nutzerwunsch) - vorher war der Standard AN."""
    base_url, mod = server
    status, data = get(base_url, "/api/telegram")
    assert data["werte"]["aktiv"] is False

    status, data = post(base_url, "/api/telegram", {"bot_token": "", "aktiv": True})
    assert status == 200
    assert data["werte"]["aktiv"] is True

    status, data = get(base_url, "/api/telegram")
    assert data["werte"]["aktiv"] is True, "Schalter wurde nicht dauerhaft gespeichert"

    with open(mod.TELEGRAM_SHELL_CONF_PATH) as f:
        assert "TELEGRAM_AKTIV='1'" in f.read()


def test_telegram_verbinden_ohne_token_liefert_fehler(server):
    base_url, _ = server
    status, data = post(base_url, "/api/telegram/verbinden", {})
    assert status == 400
    assert "Token" in data["error"]


def test_telegram_verbinden_erzeugt_code(server, monkeypatch):
    base_url, mod = server
    monkeypatch.setattr(
        mod.urllib.request, "urlopen",
        _fake_urlopen_factory([{"ok": True, "result": {"username": "MeinBot"}}]))
    post(base_url, "/api/telegram", {"bot_token": "abc123"})

    status, data = post(base_url, "/api/telegram/verbinden", {})
    assert status == 200
    assert data["bot_username"] == "MeinBot"
    assert len(data["code"]) == 8  # secrets.token_hex(4)


def test_telegram_trennen_entfernt_chat(server, monkeypatch):
    base_url, mod = server
    with open(mod.TELEGRAM_CHATS_PATH, "w") as f:
        json.dump({"111": {"name": "Anna"}, "222": {"name": "Bob"}}, f)

    status, data = post(base_url, "/api/telegram/trennen", {"chat_id": "111"})
    assert status == 200
    assert data["chats"] == {"222": {"name": "Bob"}}

    status, data = get(base_url, "/api/telegram")
    assert data["chats"] == {"222": {"name": "Bob"}}, "Trennen wurde nicht dauerhaft gespeichert"


def test_telegram_test_ohne_verbundenen_chat_liefert_fehler(server):
    base_url, _ = server
    status, data = post(base_url, "/api/telegram/test", {"token": "abc123"})
    assert status == 500
    assert "niemand verbunden" in data["error"]


def test_telegram_test_ohne_token_liefert_fehler(server):
    base_url, _ = server
    status, data = post(base_url, "/api/telegram/test", {"token": ""})
    assert status == 500
    assert "Token" in data["error"]


def test_telegram_test_sendet_an_alle_verbundenen_chats(server, monkeypatch):
    base_url, mod = server
    with open(mod.TELEGRAM_CHATS_PATH, "w") as f:
        json.dump({"111": {"name": "Anna"}, "222": {"name": "Bob"}}, f)
    monkeypatch.setattr(
        mod.urllib.request, "urlopen",
        _fake_urlopen_factory([{"ok": True, "result": {}}, {"ok": True, "result": {}}]))

    status, data = post(base_url, "/api/telegram/test", {"token": "abc123"})
    assert status == 200
    assert data["ok"] is True


def test_telegram_test_meldet_telegrams_fehlertext(server, monkeypatch):
    base_url, mod = server
    with open(mod.TELEGRAM_CHATS_PATH, "w") as f:
        json.dump({"111": {"name": "Anna"}}, f)
    monkeypatch.setattr(
        mod.urllib.request, "urlopen",
        _fake_urlopen_factory([{"ok": False, "description": "chat not found"}]))

    status, data = post(base_url, "/api/telegram/test", {"token": "abc123"})
    assert status == 500
    assert "chat not found" in data["error"]


def test_update_verarbeiten_verknuepft_gueltigen_code(galerie_env, monkeypatch):
    mod = galerie_env
    monkeypatch.setattr(mod, "_telegram_sende_nachricht", lambda *a, **k: None)
    code = mod.erzeuge_telegram_verbindungscode()

    antwort = {"ok": True, "result": [{
        "update_id": 5,
        "message": {"text": f"/start {code}", "chat": {"id": 999, "first_name": "Anna"}},
    }]}
    neuer_offset = mod.telegram_update_verarbeiten(antwort, 0)

    assert neuer_offset == 6
    chats = mod.lade_telegram_chats()
    assert chats["999"]["name"] == "Anna"
    pending = mod._lade_einstellungen_datei(mod.TELEGRAM_PENDING_PATH, {})
    assert code not in pending, "Verbrauchter Code wurde nicht aus der Pending-Liste entfernt"


def test_update_verarbeiten_ignoriert_unbekannten_code(galerie_env):
    mod = galerie_env
    antwort = {"ok": True, "result": [{
        "update_id": 3,
        "message": {"text": "/start irgendwas-erfundenes", "chat": {"id": 999, "first_name": "Anna"}},
    }]}
    neuer_offset = mod.telegram_update_verarbeiten(antwort, 0)

    assert neuer_offset == 4, "Offset muss trotzdem fortschreiten, sonst wiederholt Telegram das Update endlos"
    assert mod.lade_telegram_chats() == {}


def test_update_verarbeiten_ignoriert_abgelaufenen_code(galerie_env, monkeypatch):
    mod = galerie_env
    code = mod.erzeuge_telegram_verbindungscode()
    pending = mod._lade_einstellungen_datei(mod.TELEGRAM_PENDING_PATH, {})
    pending[code]["erstellt"] -= mod.TELEGRAM_CODE_GUELTIG_SEK + 10
    with open(mod.TELEGRAM_PENDING_PATH, "w") as f:
        json.dump(pending, f)

    antwort = {"ok": True, "result": [{
        "update_id": 1,
        "message": {"text": f"/start {code}", "chat": {"id": 999, "first_name": "Anna"}},
    }]}
    mod.telegram_update_verarbeiten(antwort, 0)

    assert mod.lade_telegram_chats() == {}


def test_update_verarbeiten_ignoriert_nachrichten_ohne_start(galerie_env):
    mod = galerie_env
    antwort = {"ok": True, "result": [{
        "update_id": 2,
        "message": {"text": "Hallo!", "chat": {"id": 999, "first_name": "Anna"}},
    }]}
    neuer_offset = mod.telegram_update_verarbeiten(antwort, 0)

    assert neuer_offset == 3
    assert mod.lade_telegram_chats() == {}
