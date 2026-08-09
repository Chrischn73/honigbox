"""Notiz-Feld fuer Archiv-Fotos (Task #23, 2026-08-09)."""
import os

from PIL import Image

from helpers import get, post


def _testfoto(pfad):
    Image.new("RGB", (32, 24), (120, 80, 40)).save(pfad, "JPEG")


def test_notiz_hinzufuegen_und_lesen(server):
    base_url, mod = server
    datei = "20260101_120000.jpg"
    _testfoto(os.path.join(mod.ARCHIV_DIR, datei))

    status, data = post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Diebstahl, 4€ fehlte"})
    assert status == 200
    assert data["notiz"]["text"] == "Diebstahl, 4€ fehlte"
    assert data["notiz"]["datum"]

    status, data = get(base_url, "/api/photos?archiv=1")
    assert data["notizen"][datei]["text"] == "Diebstahl, 4€ fehlte"


def test_notiz_liegt_im_archiv_ordner_nicht_in_einstellungen(server):
    """Architekturentscheidung 2026-08-09: Notizen muessen im Fotos-Backup
    landen (das Setup-Portal sichert nur fotos/, nicht einstellungen/) -
    siehe honigbox_speicherort_ramdisk-Notiz."""
    base_url, mod = server
    datei = "20260101_130000.jpg"
    _testfoto(os.path.join(mod.ARCHIV_DIR, datei))
    post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Testnotiz"})
    assert os.path.isfile(os.path.join(mod.ARCHIV_DIR, ".archiv-notizen.json"))
    assert not os.path.isfile(os.path.join(mod.EINSTELLUNGEN_DIR, ".archiv-notizen.json"))


def test_notiz_ist_nachtraeglich_aenderbar(server):
    base_url, mod = server
    datei = "20260101_140000.jpg"
    _testfoto(os.path.join(mod.ARCHIV_DIR, datei))
    post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Erste Version"})
    post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Geänderte Version"})

    status, data = get(base_url, "/api/photos?archiv=1")
    assert data["notizen"][datei]["text"] == "Geänderte Version"


def test_notiz_leerer_text_loescht_sie_wieder(server):
    base_url, mod = server
    datei = "20260101_150000.jpg"
    _testfoto(os.path.join(mod.ARCHIV_DIR, datei))
    post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Wird gleich geloescht"})

    status, data = post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "   "})
    assert status == 200
    assert data["notiz"] is None

    status, data = get(base_url, "/api/photos?archiv=1")
    assert datei not in data["notizen"]


def test_notiz_fuer_unbekannte_datei_gibt_404(server):
    base_url, _ = server
    status, data = post(base_url, "/api/archiv/notiz", {"datei": "existiert-nicht.jpg", "text": "x"})
    assert status == 404


def test_notiz_wird_beim_foto_loeschen_aufgeraeumt(server):
    """Regressionstest: ohne archiv_notiz_entfernen() blieben Notizen zu
    laengst geloeschten Fotos verwaist in der .archiv-notizen.json stehen."""
    base_url, mod = server
    datei = "20260101_160000.jpg"
    _testfoto(os.path.join(mod.ARCHIV_DIR, datei))
    post(base_url, "/api/archiv/notiz", {"datei": datei, "text": "Wird mit Foto geloescht"})

    status, data = post(base_url, "/api/photos/loeschen", {"dateien": [datei], "archiv": True})
    assert status == 200
    assert data["geloescht"] == 1

    assert datei not in mod.lade_archiv_notizen()
