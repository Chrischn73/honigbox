"""Kleine HTTP-Helfer fuer die Integrationstests - bewusst nur
urllib (Standardbibliothek), damit die Testsuite kein zusaetzliches
Paket (z.B. requests) braucht."""
import json
import urllib.error
import urllib.request


def get(base_url, path):
    try:
        with urllib.request.urlopen(base_url + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def post(base_url, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        base_url + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get_raw(base_url, path):
    """Wie get(), aber ohne JSON-Parsing - fuer Bild-/Thumbnail-Routen."""
    try:
        with urllib.request.urlopen(base_url + path, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
