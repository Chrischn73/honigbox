"""Regressionstest fuer einen 2026-08-19 gemeldeten Bug: Pushover hat
Nachrichten verschickt, obwohl der 'aktiv'-Haken in der Weboberflaeche NICHT
gesetzt war. Ursache: send_pushover.sh faellt auf die alte pushover.conf
zurueck, solange .pushover-einstellungen.sh nie gespeichert wurde - deren
Fallback-Default ist bewusst "aktiv" (siehe Kommentar dort), waehrend
PUSHOVER_STANDARD in galerie_server.py "inaktiv" vorgibt. lade_pushover_
einstellungen() zeigte dadurch faelschlich "aus" an, obwohl tatsaechlich
weiter gesendet wurde. Fix: die Migration von einer bestehenden alten
pushover.conf sofort dauerhaft speichern (aktiv=True, das bisherige
Verhalten ehrlich abbildend), statt sie nur "virtuell" mit dem neuen
Default zu berechnen."""
import os


def test_migration_von_alter_conf_setzt_aktiv_und_speichert_dauerhaft(galerie_env, tmp_path):
    mod = galerie_env
    alte_conf = tmp_path / "pushover.conf"
    alte_conf.write_text("PUSHOVER_TOKEN=platzhalter-token\nPUSHOVER_USER=platzhalter-user\n")
    mod.ALTE_PUSHOVER_CONF_PATH = str(alte_conf)

    assert not os.path.isfile(mod.PUSHOVER_EINSTELLUNGEN_PATH)

    werte = mod.lade_pushover_einstellungen()

    assert werte["aktiv"] is True
    assert werte["token"] == "platzhalter-token"
    assert os.path.isfile(mod.PUSHOVER_EINSTELLUNGEN_PATH)
    assert os.path.isfile(mod.PUSHOVER_SHELL_CONF_PATH)
    with open(mod.PUSHOVER_SHELL_CONF_PATH) as f:
        shell_konfig = f.read()
    assert "PUSHOVER_AKTIV='1'" in shell_konfig

    # Zweiter Aufruf nutzt die jetzt vorhandene Einstellungsdatei, migriert
    # nicht erneut, bleibt aber weiterhin aktiv.
    werte2 = mod.lade_pushover_einstellungen()
    assert werte2["aktiv"] is True


def test_echte_frischinstallation_bleibt_inaktiv(galerie_env, tmp_path):
    mod = galerie_env
    mod.ALTE_PUSHOVER_CONF_PATH = str(tmp_path / "nicht-vorhanden.conf")

    werte = mod.lade_pushover_einstellungen()

    assert werte["aktiv"] is False
    # Ohne Migrationsgrund wird auch nichts vorzeitig angelegt.
    assert not os.path.isfile(mod.PUSHOVER_EINSTELLUNGEN_PATH)
