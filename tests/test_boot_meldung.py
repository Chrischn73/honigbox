"""Startmeldung "Raspi wurde gestartet" nur nach echtem Pi-Start (2026-10-12):
Bis v1.3.51 kam sie nach jedem reinen Dienst-Neustart, also auch nach jedem
naechtlichen Auto-Update aus dem Portal. honigbox.sh ist wegen GPIO/Endlos-
schleife nicht importierbar - pi_frisch_gestartet() wird deshalb per AST
herausgeloest und einzeln ausgefuehrt."""
import ast
import os

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _funktion():
    quelle = open(os.path.join(PROJEKT, "honigbox.sh")).read()
    baum = ast.parse(quelle)
    teile = [k for k in baum.body
             if (isinstance(k, ast.FunctionDef) and k.name == "pi_frisch_gestartet")
             or (isinstance(k, ast.Assign) and any(getattr(z, "id", None) == "PI_FRISCH_GESTARTET_SEK" for z in k.targets))]
    assert len(teile) == 2, "pi_frisch_gestartet/PI_FRISCH_GESTARTET_SEK nicht gefunden"
    ns = {}
    exec(compile(ast.Module(body=teile, type_ignores=[]), "honigbox.sh", "exec"), ns)
    return ns["pi_frisch_gestartet"]


def _uptime(tmp_path, inhalt):
    pfad = tmp_path / "uptime"
    pfad.write_text(inhalt)
    return str(pfad)


def test_frisch_gebooteter_pi_meldet(tmp_path):
    assert _funktion()(_uptime(tmp_path, "42.17 80.00\n")) is True


def test_dienst_neustart_bei_laufendem_pi_bleibt_still(tmp_path):
    # z. B. Auto-Update um 04:05, Pi laeuft seit Tagen
    assert _funktion()(_uptime(tmp_path, "352811.40 1300000.00\n")) is False


def test_grenze_liegt_bei_fuenf_minuten(tmp_path):
    f = _funktion()
    assert f(_uptime(tmp_path, "299.9 0\n")) is True
    assert f(_uptime(tmp_path, "300.0 0\n")) is False


def test_unlesbare_uptime_meldet_wie_frueher(tmp_path):
    f = _funktion()
    assert f(str(tmp_path / "gibt-es-nicht")) is True
    assert f(_uptime(tmp_path, "")) is True
