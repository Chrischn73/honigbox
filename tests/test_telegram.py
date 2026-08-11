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
