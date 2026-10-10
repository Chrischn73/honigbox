"""Systemupdates: Einstellungen (Standard an, 4 Wochen), Validierung des
Intervalls und die Entscheidungslogik des naechtlichen Skripts systemneustart.py."""
import importlib.util
import json
import os

import pytest

from helpers import get, post

SKRIPT = os.path.join(os.path.dirname(__file__), "..", "systemneustart.py")


@pytest.fixture
def sn(tmp_path, monkeypatch):
    monkeypatch.setenv("HONIGBOX_BASIS", str(tmp_path))
    monkeypatch.setenv("HONIGBOX_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AUTO_NEUSTART_DIR", str(tmp_path / "auto-neustart"))
    (tmp_path / "einstellungen").mkdir()
    (tmp_path / "fotos" / "Bilder").mkdir(parents=True)
    (tmp_path / "fotos" / "Archiv").mkdir(parents=True)
    (tmp_path / "run").mkdir()
    spec = importlib.util.spec_from_file_location("systemneustart", SKRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_standard_ist_an_und_vier_wochen(server):
    base_url, _ = server
    status, data = get(base_url, "/api/systemupdate")
    assert status == 200
    assert data["einstellungen"] == {"auto_neustart": True, "intervall_wochen": 4}
    assert data["status"] == {}


def test_speichern_und_ungueltiges_intervall(server):
    base_url, _ = server
    assert post(base_url, "/api/systemupdate", {"auto_neustart": False, "intervall_wochen": 8})[1]["intervall_wochen"] == 8
    assert get(base_url, "/api/systemupdate")[1]["einstellungen"] == {"auto_neustart": False, "intervall_wochen": 8}
    assert post(base_url, "/api/systemupdate", {"auto_neustart": True, "intervall_wochen": 5})[1]["intervall_wochen"] == 4


def test_skript_einstellungen_default_und_ungueltig(sn):
    assert sn.lade_einstellungen() == {"auto_neustart": True, "intervall_wochen": 4}
    with open(sn.EINSTELLUNGEN_PATH, "w") as f:
        json.dump({"auto_neustart": False, "intervall_wochen": 99}, f)
    assert sn.lade_einstellungen() == {"auto_neustart": False, "intervall_wochen": 4}


def test_fotos_ins_archiv(sn):
    for name in ("a.jpg", "b.png"):
        with open(os.path.join(sn.BILDER_DIR, name), "wb") as f:
            f.write(b"x" * 100)
    open(os.path.join(sn.BILDER_DIR, ".kamera.json"), "w").close()
    assert sn.ram_fotos() == ["a.jpg", "b.png"]
    # Archiv gesperrt -> nichts verschieben
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("locked")
    assert sn.fotos_ins_archiv(sn.ram_fotos()) == (0, "Archiv ist gesperrt (Schlüssel fehlt)")
    assert sn.ram_fotos() == ["a.jpg", "b.png"]
    # Archiv offen -> verschoben
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("unlocked")
    assert sn.fotos_ins_archiv(sn.ram_fotos()) == (2, None)
    assert sn.ram_fotos() == []
    assert sorted(os.listdir(sn.ARCHIV_DIR)) == ["a.jpg", "b.png"]


def test_kein_platz_im_archiv_verschiebt_nichts(sn, monkeypatch):
    with open(os.path.join(sn.BILDER_DIR, "a.jpg"), "wb") as f:
        f.write(b"x" * 1000)
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("unlocked")
    monkeypatch.setattr(sn.shutil, "disk_usage", lambda p: type("U", (), {"free": 500})())
    assert sn.fotos_ins_archiv(["a.jpg"]) == (0, "Archiv hat nicht genug freien Platz")
    assert sn.ram_fotos() == ["a.jpg"]


def test_kein_reboot_vor_ablauf_des_intervalls(sn, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(sn, "laufzeit_sek", lambda: 10 * 86400)
    monkeypatch.setattr(sn, "neustart_grund", lambda: "Kernel-Update")
    monkeypatch.setattr(sn.subprocess, "run", lambda *a, **k: aufrufe.append(a))
    monkeypatch.setattr(sn, "melde", lambda *a: aufrufe.append(a))
    assert sn.main() == 0
    assert aufrufe == []


def test_reboot_nach_intervall_meldet_und_bootet(sn, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(sn, "laufzeit_sek", lambda: 29 * 86400)
    monkeypatch.setattr(sn, "neustart_grund", lambda: "Kernel-Update")
    monkeypatch.setattr(sn, "system_beschaeftigt", lambda: None)
    monkeypatch.setattr(sn, "ist_tmpfs", lambda p: False)
    monkeypatch.setattr(sn, "melde", lambda *a: aufrufe.append(("melde",) + a))
    monkeypatch.setattr(sn.subprocess, "run", lambda cmd, **k: aufrufe.append(tuple(cmd)))
    assert sn.main() == 0
    assert aufrufe[0][:2] == ("melde", "systemneustart")
    assert aufrufe[-1] == ("systemctl", "reboot")
    # Markierung, damit die Galerie nach diesem Start kein Reset-Fenster oeffnet
    assert os.path.isfile(os.path.join(sn.AUTO_NEUSTART_DIR, "reboot"))


def test_abbruch_wenn_fotos_nicht_archivierbar(sn, monkeypatch):
    aufrufe = []
    with open(os.path.join(sn.BILDER_DIR, "a.jpg"), "wb") as f:
        f.write(b"x")
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("locked")
    monkeypatch.setattr(sn, "laufzeit_sek", lambda: 29 * 86400)
    monkeypatch.setattr(sn, "neustart_grund", lambda: "Kernel-Update")
    monkeypatch.setattr(sn, "system_beschaeftigt", lambda: None)
    monkeypatch.setattr(sn, "ist_tmpfs", lambda p: True)
    monkeypatch.setattr(sn, "melde", lambda *a: aufrufe.append(("melde",) + a))
    monkeypatch.setattr(sn.subprocess, "run", lambda cmd, **k: aufrufe.append(tuple(cmd)))
    assert sn.main() == 0
    assert ("melde", "systemneustart_abgebrochen", "Grund: Archiv ist gesperrt (Schlüssel fehlt).") in aufrufe
    assert ("systemctl", "reboot") not in aufrufe
    assert sn.ram_fotos() == ["a.jpg"]


def test_auto_neustart_aus_bootet_nie(sn, monkeypatch):
    aufrufe = []
    with open(sn.EINSTELLUNGEN_PATH, "w") as f:
        json.dump({"auto_neustart": False, "intervall_wochen": 4}, f)
    monkeypatch.setattr(sn, "laufzeit_sek", lambda: 60 * 86400)
    monkeypatch.setattr(sn, "neustart_grund", lambda: "Kernel-Update")
    monkeypatch.setattr(sn.subprocess, "run", lambda cmd, **k: aufrufe.append(tuple(cmd)))
    monkeypatch.setattr(sn, "melde", lambda *a: aufrufe.append(a))
    assert sn.main() == 0
    assert aufrufe == []


def test_archiv_erinnerung_max_dreimal(sn, monkeypatch):
    gesendet = []
    monkeypatch.setattr(sn, "melde", lambda *a: gesendet.append(a))
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("locked")
    for _ in range(5):
        sn.archiv_erinnerung()
        zustand = json.load(open(sn.ERINNERUNG_PATH))
        zustand["zuletzt"] -= 21 * 3600  # naechster Lauf ~21 h spaeter
        json.dump(zustand, open(sn.ERINNERUNG_PATH, "w"))
    assert len(gesendet) == 3
    # entsperrt -> Zaehler zurueck
    with open(sn.ARCHIV_STATUS_PATH, "w") as f:
        f.write("unlocked")
    sn.archiv_erinnerung()
    assert json.load(open(sn.ERINNERUNG_PATH))["anzahl"] == 0


def test_kein_reboot_waehrend_portal_update(sn, monkeypatch, tmp_path):
    """Haelt das Setup-Portal seine Update-Sperre (z. B. 'Alle aktualisieren'
    oder naechtliches Auto-Update), wird der Neustart zurueckgestellt."""
    import fcntl
    sperre = tmp_path / "update.lock"
    monkeypatch.setattr(sn, "PORTAL_SPERRE", str(sperre))
    monkeypatch.setattr(sn, "laufzeit_sek", lambda: 29 * 86400)
    monkeypatch.setattr(sn, "neustart_grund", lambda: "Kernel-Update")
    monkeypatch.setattr(sn, "tuer_offen", lambda: False)
    monkeypatch.setattr(sn, "ist_tmpfs", lambda p: False)
    aufrufe = []
    echtes_run = sn.subprocess.run

    def run(cmd, **k):
        aufrufe.append(tuple(cmd))
        if cmd[:2] == ["systemctl", "list-units"]:
            return echtes_run(["true"], capture_output=True, text=True)
        return echtes_run(["false"])

    monkeypatch.setattr(sn.subprocess, "run", run)
    monkeypatch.setattr(sn, "melde", lambda *a: aufrufe.append(("melde",) + a))
    # Eigenes open() = eigene Open File Description -> flock kollidiert wie bei
    # einem fremden Prozess.
    with open(sperre, "a+") as halter:
        fcntl.flock(halter, fcntl.LOCK_EX)
        assert sn.main() == 0
    assert ("systemctl", "reboot") not in aufrufe
    assert "App-Update" in json.load(open(sn.STATUS_PATH))["notiz"]
    # Sperre frei, keine Update-Units aktiv -> nicht mehr beschaeftigt
    assert sn.portal_vorgang_laeuft() is False
