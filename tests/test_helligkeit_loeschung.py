"""Helligkeitsbasierte Loeschung dunkler Fotos (einzelfoto_helligkeit_pruefen).
Reine Funktionstests (kein HTTP noetig) - direkter Aufruf gegen echte,
per Pillow erzeugte Testbilder."""
import os

from PIL import Image


def _bild(pfad, farbe):
    Image.new("RGB", (32, 24), farbe).save(pfad, "JPEG")


def test_dunkles_foto_wird_geloescht_wenn_aktiviert(galerie_env):
    mod = galerie_env
    mod.speichere_foto_zeitplan({"dunkle_fotos_loeschen": True, "helligkeitsschwelle": 50})
    pfad = os.path.join(mod.BILDER_DIR, "dunkel.jpg")
    _bild(pfad, (5, 5, 5))

    geloescht, helligkeit = mod.einzelfoto_helligkeit_pruefen()

    assert geloescht is True
    assert helligkeit < 50
    assert not os.path.exists(pfad)


def test_helles_foto_wird_behalten(galerie_env):
    mod = galerie_env
    mod.speichere_foto_zeitplan({"dunkle_fotos_loeschen": True, "helligkeitsschwelle": 50})
    pfad = os.path.join(mod.BILDER_DIR, "hell.jpg")
    _bild(pfad, (230, 230, 230))

    geloescht, helligkeit = mod.einzelfoto_helligkeit_pruefen()

    assert geloescht is False
    assert helligkeit >= 50
    assert os.path.isfile(pfad)


def test_deaktiviert_loescht_dunkles_foto_nicht(galerie_env):
    mod = galerie_env
    mod.speichere_foto_zeitplan({"dunkle_fotos_loeschen": False, "helligkeitsschwelle": 50})
    pfad = os.path.join(mod.BILDER_DIR, "dunkel.jpg")
    _bild(pfad, (5, 5, 5))

    geloescht, helligkeit = mod.einzelfoto_helligkeit_pruefen()

    assert geloescht is False
    assert helligkeit is None  # Funktion gibt bei deaktivierter Einstellung frueh zurueck
    assert os.path.isfile(pfad)


def test_liest_foto_zeitplan_nicht_kamera_einstellungen(galerie_env):
    """Regressionstest fuer den 2026-08-08 gefundenen Bug: die Funktion las
    frueher lade_kamera_einstellungen() statt lade_foto_zeitplan() und bekam
    dadurch nie einen echten dunkle_fotos_loeschen/helligkeitsschwelle-Wert -
    der Einzelfoto-Button zeigte danach nie mehr eine Helligkeit an."""
    mod = galerie_env
    mod.speichere_foto_zeitplan({"dunkle_fotos_loeschen": True, "helligkeitsschwelle": 80})
    pfad = os.path.join(mod.BILDER_DIR, "dunkel.jpg")
    _bild(pfad, (10, 10, 10))

    geloescht, helligkeit = mod.einzelfoto_helligkeit_pruefen()

    assert geloescht is True
    assert helligkeit is not None
