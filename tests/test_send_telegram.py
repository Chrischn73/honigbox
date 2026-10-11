"""send_telegram.sh gegen einen lokalen Fake-Telegram-Server (TELEGRAM_API_BASIS).
Anlass (2026-10-11): Eine verknuepfte Gruppe wurde durch das Ernennen von
Admins still zur Supergruppe und bekam eine neue Chat-ID. Telegram lehnte
jede Nachricht an die alte ID mit HTTP 400 + migrate_to_chat_id ab, das
Skript verschluckte den Fehler (sogar dreimal) - die Gruppe bekam nichts mehr."""
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALTE_ID = "-4408586"
NEUE_ID = "-1004408586535"
RAUSGEWORFEN_ID = "-777"


class _FakeTelegram(BaseHTTPRequestHandler):
    aufrufe = []

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length", 0))
        chat_id = urllib.parse.parse_qs(self.rfile.read(laenge).decode())["chat_id"][0]
        self.aufrufe.append(chat_id)
        if chat_id == ALTE_ID:
            code, body = 400, {"ok": False, "error_code": 400,
                               "description": "Bad Request: group chat was upgraded to a supergroup chat",
                               "parameters": {"migrate_to_chat_id": int(NEUE_ID)}}
        elif chat_id == RAUSGEWORFEN_ID:
            code, body = 403, {"ok": False, "error_code": 403,
                               "description": "Forbidden: bot was kicked from the group chat"}
        else:
            code, body = 200, {"ok": True, "result": {}}
        daten = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_telegram():
    _FakeTelegram.aufrufe = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTelegram)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _FakeTelegram.aufrufe
    srv.shutdown()


@pytest.fixture
def box(tmp_path):
    """Minimaler /opt/honigbox-Nachbau: Skript + einstellungen/."""
    shutil.copy(os.path.join(PROJEKT, "send_telegram.sh"), tmp_path / "send_telegram.sh")
    einst = tmp_path / "einstellungen"
    einst.mkdir()
    (einst / ".telegram-einstellungen.sh").write_text("TELEGRAM_BOT_TOKEN='123:abc'\nTELEGRAM_AKTIV='1'\n")
    return tmp_path


def _ausfuehren(box, api):
    env = dict(os.environ, TELEGRAM_API_BASIS=api)
    subprocess.run(["bash", str(box / "send_telegram.sh"), "geoeffnet"], env=env, check=True, timeout=60)


def _chats(box):
    return json.loads((box / "einstellungen" / ".telegram-chats.json").read_text())


def test_supergruppe_wird_nachgesendet_und_dauerhaft_umgestellt(box, fake_telegram):
    api, aufrufe = fake_telegram
    (box / "einstellungen" / ".telegram-chats.json").write_text(json.dumps({
        "111": {"name": "Anna"}, ALTE_ID: {"name": "HonigBox", "verknuepft_am": "2026-10-01 12:00"}}))

    _ausfuehren(box, api)

    assert aufrufe == ["111", ALTE_ID, NEUE_ID]
    assert _chats(box) == {"111": {"name": "Anna"},
                           NEUE_ID: {"name": "HonigBox", "verknuepft_am": "2026-10-01 12:00"}}

    # Naechste Meldung geht direkt an die neue ID, kein Umweg mehr.
    aufrufe.clear()
    _ausfuehren(box, api)
    assert aufrufe == ["111", NEUE_ID]


def test_neue_id_schon_verknuepft_wird_nicht_doppelt_beliefert(box, fake_telegram):
    api, aufrufe = fake_telegram
    (box / "einstellungen" / ".telegram-chats.json").write_text(json.dumps({
        ALTE_ID: {"name": "alt"}, NEUE_ID: {"name": "neu verbunden"}}))

    _ausfuehren(box, api)

    assert sorted(aufrufe) == sorted([ALTE_ID, NEUE_ID])
    assert _chats(box) == {NEUE_ID: {"name": "neu verbunden"}}


def test_dauerhafter_fehler_wird_nicht_wiederholt(box, fake_telegram):
    api, aufrufe = fake_telegram
    (box / "einstellungen" / ".telegram-chats.json").write_text(json.dumps({RAUSGEWORFEN_ID: {"name": "weg"}}))

    _ausfuehren(box, api)

    assert aufrufe == [RAUSGEWORFEN_ID], "403 ist dauerhaft - frueher 3 Versuche mit je 3 s Pause"
    assert _chats(box) == {RAUSGEWORFEN_ID: {"name": "weg"}}
