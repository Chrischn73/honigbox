"""Einstellbares Logo: Standard-Logo als Fallback, Upload nur JPEG/PNG,
Zuruecksetzen auf den Standard."""
import os
import urllib.error
import urllib.request

from helpers import get, get_raw, post

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def post_raw(base_url, path, daten):
    req = urllib.request.Request(base_url + path, data=daten, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_ohne_eigenes_logo_kommt_standard_logo(server):
    base_url, mod = server
    status, data = get(base_url, "/api/logo/status")
    assert status == 200 and data == {"eigenes": False}
    status, daten = get_raw(base_url, "/api/logo")
    assert status == 200
    with open(mod.LOGO_STANDARD_PATH, "rb") as f:
        assert daten == f.read()


def test_png_upload_und_zuruecksetzen(server):
    base_url, mod = server
    status, _ = post_raw(base_url, "/api/logo", PNG)
    assert status == 200
    assert get(base_url, "/api/logo/status")[1] == {"eigenes": True}
    assert get_raw(base_url, "/api/logo") == (200, PNG)

    status, data = post(base_url, "/api/logo/zuruecksetzen")
    assert status == 200 and data == {"eigenes": False}
    assert not os.path.exists(mod.LOGO_PATH)
    with open(mod.LOGO_STANDARD_PATH, "rb") as f:
        assert get_raw(base_url, "/api/logo")[1] == f.read()


def test_jpeg_upload_ersetzt_vorheriges_logo(server):
    base_url, _ = server
    post_raw(base_url, "/api/logo", PNG)
    assert post_raw(base_url, "/api/logo", JPEG)[0] == 200
    assert get_raw(base_url, "/api/logo") == (200, JPEG)


def test_kein_bild_wird_abgelehnt(server):
    """SVG/HTML koennten Skripte enthalten - nur JPEG/PNG anhand der Magic
    Bytes, unabhaengig von dem, was der Browser als Typ angibt."""
    base_url, mod = server
    status, _ = post_raw(base_url, "/api/logo", b"<svg onload='alert(1)'></svg>")
    assert status == 400
    assert not os.path.exists(mod.LOGO_PATH)


def test_leerer_upload_wird_abgelehnt(server):
    base_url, mod = server
    assert post_raw(base_url, "/api/logo", b"")[0] == 400
    assert not os.path.exists(mod.LOGO_PATH)


def test_zu_grosses_logo_wird_abgelehnt(server):
    base_url, mod = server
    status, _ = post_raw(base_url, "/api/logo", PNG + b"\x00" * mod.LOGO_MAX_BYTES)
    assert status == 413
    assert not os.path.exists(mod.LOGO_PATH)
