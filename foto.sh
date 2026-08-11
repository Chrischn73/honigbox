#!/bin/bash
mkdir -p /opt/honigbox/fotos/Bilder/
chmod 777 /opt/honigbox/fotos/Bilder/
cd /opt/honigbox/fotos/Bilder/

# rpicam-still (Bookworm) statt libcamera-still (aeltere Versionen), je nachdem was vorhanden ist
if command -v rpicam-still >/dev/null 2>&1; then
  KAMERA_BEFEHL=rpicam-still
else
  KAMERA_BEFEHL=libcamera-still
fi

# Kamera-Einstellungen aus der Galerie-Weboberflaeche (falls dort gespeichert), sonst Standardwerte.
# Liegt bewusst NICHT in Bilder/ - das kann bei aktivierter RAM-Disk ein
# tmpfs sein, /opt/honigbox/einstellungen bleibt immer auf der SD-Karte.
KAMERA_CONF="/opt/honigbox/einstellungen/.kamera-einstellungen.sh"
[ -f "$KAMERA_CONF" ] && source "$KAMERA_CONF"

: "${METERING:=spot}"
: "${EV:=0}"
: "${BELICHTUNGSMODUS:=normal}"
: "${VERSCHLUSSZEIT:=0}"
: "${GAIN:=0}"
: "${HELLIGKEIT:=0}"
: "${KONTRAST:=1}"
: "${SAETTIGUNG:=1}"
: "${SCHAERFE:=1}"
: "${WEISSABGLEICH:=auto}"
: "${RAUSCHUNTERDRUECKUNG:=cdn_fast}"
: "${FOKUS_MODUS:=auto}"
: "${FOKUS_POSITION:=4.0}"
: "${AUFNAHME_VERZOEGERUNG_MS:=1000}"
: "${BREITE:=2304}"
: "${HOEHE:=1296}"
: "${JPEG_QUALITAET:=90}"
: "${ROTATION:=0}"
: "${HFLIP:=0}"
: "${VFLIP:=0}"
: "${ZOOM:=1.0}"

KAMERA_ARGS=(--datetime -n --width "$BREITE" --height "$HOEHE"
  --metering "$METERING" --ev "$EV" --exposure "$BELICHTUNGSMODUS"
  --brightness "$HELLIGKEIT" --contrast "$KONTRAST" --saturation "$SAETTIGUNG"
  --sharpness "$SCHAERFE" --awb "$WEISSABGLEICH" --denoise "$RAUSCHUNTERDRUECKUNG"
  --quality "$JPEG_QUALITAET" --rotation "$ROTATION"
  --timeout "$AUFNAHME_VERZOEGERUNG_MS")

# Ohne --timeout wartet rpicam-still/libcamera-still standardmaessig 5 Sekunden
# (Vorschau zum Einschwingen von Belichtung/Weissabgleich), bevor es ueberhaupt
# ausloest - bei mehreren Fotos hintereinander (Tuer offen) addiert sich das
# spuerbar. AUFNAHME_VERZOEGERUNG_MS (Kamera-Einstellungen, Standard 1000) ist
# ein einstellbarer Kompromiss: niedriger = schneller, aber weniger Zeit zum
# Einschwingen bei wechselndem Licht (Tuer geht auf -> Tageslicht).

[ "$VERSCHLUSSZEIT" != "0" ] && KAMERA_ARGS+=(--shutter "$VERSCHLUSSZEIT")
[ "$GAIN" != "0" ] && KAMERA_ARGS+=(--gain "$GAIN")
[ "$HFLIP" = "1" ] && KAMERA_ARGS+=(--hflip)
[ "$VFLIP" = "1" ] && KAMERA_ARGS+=(--vflip)

# Optionales 1. Argument: Pfad, an den die TATSAECHLICH angewandten Kamera-
# Werte als JSON geschrieben werden sollen (Foto-Testmodus, siehe
# /api/foto/einzel in galerie_server.py) - nur bei manuellem Testfoto gesetzt,
# beim normalen Tueroeffnungs-Zyklus leer und ohne Wirkung/Zusatzaufwand.
METADATA_PFAD="$1"
[ -n "$METADATA_PFAD" ] && KAMERA_ARGS+=(--metadata "$METADATA_PFAD" --metadata-format json)

# Fest eingestellter Fokus: kein Autofokus-Suchlauf mehr bei jedem Bild
# (kalibrieren ueber den Einzelfoto-Button, bis das Bild scharf ist).
if [ "$FOKUS_MODUS" = "fest" ]; then
  KAMERA_ARGS+=(--autofocus-mode manual --lens-position "$FOKUS_POSITION")
fi

# Digitaler Zoom: zentrierter Bildausschnitt (ROI) statt echtem optischem Zoom
if [ "$ZOOM" != "1" ] && [ "$ZOOM" != "1.0" ]; then
  # LC_NUMERIC=C erzwingen, sonst nutzt awk bei deutschem Locale ein Komma statt Punkt
  ROI_GROESSE=$(LC_NUMERIC=C awk "BEGIN{printf \"%.4f\", 1/$ZOOM}")
  ROI_OFFSET=$(LC_NUMERIC=C awk "BEGIN{printf \"%.4f\", (1-1/$ZOOM)/2}")
  KAMERA_ARGS+=(--roi "${ROI_OFFSET},${ROI_OFFSET},${ROI_GROESSE},${ROI_GROESSE}")
fi

# Immer genau ein Foto - die Anzahl/Taktung bei Tueroeffnung steuert honigbox.sh
# selbst per Zeitplan (siehe .foto-zeitplan.json), nicht mehr rpicam-still direkt.
# Das Loeschen zu dunkler Fotos passiert bewusst NICHT hier (wuerde die Aufnahme-
# Taktung durch den Pillow-Start pro Bild ausbremsen), sondern gesammelt in
# honigbox.sh, nachdem die Tuer wieder zu ist (siehe dunkle_fotos_aufraeumen()).
"$KAMERA_BEFEHL" "${KAMERA_ARGS[@]}" >/dev/null 2>&1

cd /opt/honigbox/
