// Von der Setup-Seite (honigbox_setup_portal.py, app_version()) per Regex
// ausgelesen, um die installierte Version mit GitHub-Releases zu vergleichen -
// beim Versionieren nicht vergessen, mit index.html synchron zu halten.
const APP_VERSION = 'v1.3.6';

const versionTagEl = document.getElementById('app-version-tag');
if (versionTagEl) versionTagEl.textContent = APP_VERSION;

const grid = document.getElementById('galerie-grid');
const galerieAnzeigeModusSel = document.getElementById('galerie-anzeige-modus');
const tabFotos = document.getElementById('tab-fotos');
const tabArchiv = document.getElementById('tab-archiv');
const alleAuswaehlenCb = document.getElementById('alle-auswaehlen-cb');
const auswahlLeiste = document.getElementById('auswahl-leiste');
const btnArchivierenBatch = document.getElementById('btn-archivieren-batch');
const btnLoeschenBatch = document.getElementById('btn-loeschen-batch');
const btnAuswahlAufheben = document.getElementById('btn-auswahl-aufheben');
const btnFotoAufnehmen = document.getElementById('btn-foto-aufnehmen');
const kameraFelderContainer = document.getElementById('kamera-felder');
const kameraSpeichernBtn = document.getElementById('kamera-speichern');
const kameraZuruecksetzenBtn = document.getElementById('kamera-zuruecksetzen');
const btnNeustart = document.getElementById('btn-neustart');
const btnHerunterfahren = document.getElementById('btn-herunterfahren');
const btnDiensteNeustart = document.getElementById('btn-dienste-neustart');
const tuerKontaktInvertiertCb = document.getElementById('tuer-kontakt-invertiert');
const tuerKontaktSpeichernBtn = document.getElementById('tuer-kontakt-speichern');
const btnTuerSimulieren = document.getElementById('btn-tuer-simulieren');
const simMinutenInp = document.getElementById('sim-minuten');
const simSekundenInp = document.getElementById('sim-sekunden');
const simDauerSpeichernBtn = document.getElementById('sim-dauer-speichern');
const speicherOrtSel = document.getElementById('speicher-ort');
const speicherGroesseInp = document.getElementById('speicher-groesse');
const speicherGroesseFeld = document.getElementById('speicher-groesse-feld');
const speicherInfoEl = document.getElementById('speicher-info');
const speicherUebernehmenBtn = document.getElementById('speicher-uebernehmen');
const ramNeustartWarnungEl = document.getElementById('ram-neustart-warnung');
const linkSetupSeiteEl = document.getElementById('link-setup-seite');
const einstellungenDetailsListe = document.querySelectorAll('#ansicht-einstellungen details');
const fotoZeitplanFelderContainer = document.getElementById('foto-zeitplan-felder');
const fotoZeitplanSpeichernBtn = document.getElementById('foto-zeitplan-speichern');
const fotoZeitplanZuruecksetzenBtn = document.getElementById('foto-zeitplan-zuruecksetzen');
const pushoverAktivInp = document.getElementById('pushover-aktiv');
const pushoverTokenInp = document.getElementById('pushover-token');
const pushoverUserInp = document.getElementById('pushover-user');
const pushoverMeldungenContainer = document.getElementById('pushover-meldungen');
const pushoverSpeichernBtn = document.getElementById('pushover-speichern');
const pushoverAlleAktivierenBtn = document.getElementById('pushover-alle-aktivieren');
const pushoverAlleDeaktivierenBtn = document.getElementById('pushover-alle-deaktivieren');
const pushoverTestBtn = document.getElementById('pushover-test');
const telegramAktivInp = document.getElementById('telegram-aktiv');
const telegramBotTokenInp = document.getElementById('telegram-bot-token');
const telegramSpeichernBtn = document.getElementById('telegram-speichern');
const telegramVerbindenBtn = document.getElementById('telegram-verbinden');
const telegramVerbindenBoxEl = document.getElementById('telegram-verbinden-box');
const telegramTestBtn = document.getElementById('telegram-test');
const telegramChatsListeEl = document.getElementById('telegram-chats-liste');
const statusRaspiEl = document.getElementById('status-raspi');
const statusDienstEl = document.getElementById('status-dienst');
const statusTuerEl = document.getElementById('status-tuer');
const kameraFehltWarnungEl = document.getElementById('kamera-fehlt-warnung');
const btnPushoverStumm = document.getElementById('btn-pushover-stumm');
const PUSHOVER_STUMM_DAUER_OPTIONEN_MIN = [3, 5, 10, 20, 30];
const STATUS_VERALTET_NACH_SEK = 15;
const hauptTabBtns = document.querySelectorAll('.haupt-tab-btn');
const ansichten = document.querySelectorAll('.ansicht');

let zeigeArchiv = false;
let archivNotizen = {};
let ausgewaehlt = new Set();
let galerieAnzeigeModus = 'einzelbild';
let kameraFelder = [];
let fotoZeitplanFelder = [];
let pushoverMeldungenSchema = [];

function bildUrl(datei) {
  return (zeigeArchiv ? '/archiv-bilder/' : '/bilder/') + encodeURIComponent(datei);
}

function thumbUrl(datei) {
  return (zeigeArchiv ? '/archiv-thumbs/' : '/thumbs/') + encodeURIComponent(datei);
}

function toast(text) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => {
    el.classList.add('toast-out');
    setTimeout(() => el.remove(), 300);
  }, 1800);
}

function bestaetigen(text) {
  // Ersetzt window.confirm(): manche mobilen Browser (z.B. iOS Safari im
  // "Zum Startbildschirm hinzufuegen"-Standalone-Modus) unterdruecken
  // confirm() komplett und liefern sofort false, ohne etwas anzuzeigen.
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'confirm-back';

    const box = document.createElement('div');
    box.className = 'confirm-box';

    const p = document.createElement('p');
    p.textContent = text;

    const aktionen = document.createElement('div');
    aktionen.className = 'confirm-aktionen';

    const abbrechenBtn = document.createElement('button');
    abbrechenBtn.className = 'btn btn-ghost';
    abbrechenBtn.textContent = 'Abbrechen';

    const okBtn = document.createElement('button');
    okBtn.className = 'btn btn-danger';
    okBtn.textContent = 'OK';

    aktionen.append(abbrechenBtn, okBtn);
    box.append(p, aktionen);
    back.appendChild(box);
    document.body.appendChild(back);

    const schliessen = (ergebnis) => {
      back.remove();
      resolve(ergebnis);
    };
    abbrechenBtn.addEventListener('click', () => schliessen(false));
    okBtn.addEventListener('click', () => schliessen(true));
    back.addEventListener('click', (e) => { if (e.target === back) schliessen(false); });
  });
}

function notizEingeben(titel, startwert) {
  // Wie bestaetigen() oben, aber mit einem Textfeld statt nur Ja/Nein -
  // fuer die Archiv-Notizen ("Diebstahl, 4€ fehlte" o.ae.).
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'confirm-back';

    const box = document.createElement('div');
    box.className = 'confirm-box';

    const p = document.createElement('p');
    p.textContent = titel;

    const feld = document.createElement('textarea');
    feld.className = 'notiz-textfeld';
    feld.value = startwert || '';
    feld.maxLength = 300;
    feld.rows = 3;

    const aktionen = document.createElement('div');
    aktionen.className = 'confirm-aktionen';

    const abbrechenBtn = document.createElement('button');
    abbrechenBtn.className = 'btn btn-ghost';
    abbrechenBtn.textContent = 'Abbrechen';

    const speichernBtn = document.createElement('button');
    speichernBtn.className = 'btn btn-primary';
    speichernBtn.textContent = 'Speichern';

    aktionen.append(abbrechenBtn, speichernBtn);
    box.append(p, feld, aktionen);
    back.appendChild(box);
    document.body.appendChild(back);
    feld.focus();

    const schliessen = (ergebnis) => {
      back.remove();
      resolve(ergebnis);
    };
    abbrechenBtn.addEventListener('click', () => schliessen(null));
    speichernBtn.addEventListener('click', () => schliessen(feld.value));
    back.addEventListener('click', (e) => { if (e.target === back) schliessen(null); });
  });
}

async function notizSpeichern(datei, text) {
  try {
    const res = await fetch('/api/archiv/notiz', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ datei, text }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      toast(data.error || 'Notiz konnte nicht gespeichert werden');
      return;
    }
    toast(text.trim() ? 'Notiz gespeichert' : 'Notiz gelöscht');
    laden();
  } catch {
    toast('Notiz konnte nicht gespeichert werden');
  }
}

async function notizBearbeiten(datei, bisherigerText) {
  const text = await notizEingeben(`Notiz zu ${datei}`, bisherigerText);
  if (text === null) return; // Abbrechen
  await notizSpeichern(datei, text);
}

function setzeStatusBadge(el, klasse, text) {
  el.className = `status-badge ${klasse}`;
  el.textContent = text;
}

function aktualisiereStatusAnzeige(daten) {
  // Wenn diese Antwort ueberhaupt ankommt, laeuft der Pi/die Galerie - trivial,
  // aber als explizite Bestaetigung fuer den Nutzer trotzdem hilfreich.
  setzeStatusBadge(statusRaspiEl, 'status-ok', '🟢 Raspi online');

  const dienstLaeuft = daten.honigbox_service === 'active';
  const statusVeraltet = daten.tuer_alter_sekunden === null || daten.tuer_alter_sekunden > STATUS_VERALTET_NACH_SEK;

  if (dienstLaeuft && !statusVeraltet) {
    setzeStatusBadge(statusDienstEl, 'status-ok', '🟢 Türüberwachung');
  } else if (dienstLaeuft && statusVeraltet) {
    setzeStatusBadge(statusDienstEl, 'status-warn', '🟡 Läuft, aber Status veraltet');
  } else {
    setzeStatusBadge(statusDienstEl, 'status-fehler', `🔴 Türüberwachung nicht aktiv (${daten.honigbox_service})`);
  }

  if (daten.tuer_offen === null) {
    setzeStatusBadge(statusTuerEl, 'status-fehler', '🚪 Tür: unbekannt');
  } else if (daten.tuer_offen) {
    setzeStatusBadge(statusTuerEl, 'status-warn', `🚪 Tür: OFFEN${formatiereDauerSeit(daten.tuer_offen_dauer_sekunden)}`);
  } else {
    setzeStatusBadge(statusTuerEl, 'status-ok', '🚪 Tür: zu');
  }

  kameraFehltWarnungEl.hidden = daten.kamera_erkannt !== false;

  const stummRest = daten.pushover_stumm_rest_sekunden || 0;
  if (stummRest > 0) {
    btnPushoverStumm.dataset.aktiv = '1';
    btnPushoverStumm.textContent = `🔔 Stummschaltung aufheben (noch ${Math.ceil(stummRest / 60)} Min.)`;
  } else {
    btnPushoverStumm.dataset.aktiv = '0';
    btnPushoverStumm.textContent = '🔕 Pushover stummschalten';
  }
}

function formatiereDauerSeit(sekunden) {
  if (sekunden == null) return '';
  if (sekunden < 60) return ` (seit ${Math.floor(sekunden)} Sek.)`;
  const minuten = Math.floor(sekunden / 60);
  return ` (seit ${minuten} Min.)`;
}

async function ladeStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    aktualisiereStatusAnzeige(data);
  } catch {
    // Fetch selbst fehlgeschlagen (nicht nur eine Fehlerantwort) - der Server
    // dieser Seite antwortet gerade gar nicht mehr.
    setzeStatusBadge(statusRaspiEl, 'status-fehler', '🔴 Raspi nicht erreichbar');
    setzeStatusBadge(statusDienstEl, 'status-fehler', '🔴 Status nicht abrufbar');
    setzeStatusBadge(statusTuerEl, 'status-fehler', '🚪 Tür: unbekannt');
    kameraFehltWarnungEl.hidden = true;
  }
}

function zeigeAnsicht(name) {
  ansichten.forEach((el) => el.classList.toggle('hidden', el.id !== `ansicht-${name}`));
  hauptTabBtns.forEach((btn) => btn.classList.toggle('aktiv', btn.dataset.ansicht === name));
}

// Von render() bei jedem Laden aktuell gehalten - die Lightbox braucht die
// volle Liste (nicht nur das angeklickte Bild), um zum naechsten/vorherigen
// Foto blaettern zu koennen.
let aktuelleBilderListe = [];

// Entfernt ein Foto lokal aus Liste+Grid (nach erfolgreichem Archivieren/
// Loeschen), OHNE die Seite neu vom Server zu laden - damit die Lightbox
// nahtlos beim naechsten/vorherigen Bild weitermachen kann. Gibt den Index
// zurueck, an dem das Foto stand (fuer die Auswahl des naechsten Bildes).
function entferneFotoLokal(datei) {
  const i = aktuelleBilderListe.indexOf(datei);
  if (i === -1) return -1;
  const kopie = aktuelleBilderListe.slice();
  kopie.splice(i, 1);
  ausgewaehlt.delete(datei);
  render(kopie);
  aktualisiereAuswahlLeiste();
  return i;
}

function oeffneLightbox(startIndex) {
  let index = startIndex;
  const back = document.createElement('div');
  back.className = 'lightbox';

  const figure = document.createElement('figure');
  const img = document.createElement('img');
  const caption = document.createElement('figcaption');
  const aktionen = document.createElement('div');
  aktionen.className = 'lightbox-aktionen';
  figure.append(img, caption, aktionen);

  let prevBtn = null;
  let nextBtn = null;

  function schliessen() {
    back.remove();
    document.removeEventListener('keydown', tastenHandler);
  }

  function zeigeIndex(i) {
    index = i;
    const datei = aktuelleBilderListe[index];
    img.src = bildUrl(datei);
    caption.textContent = datei;
    if (prevBtn) {
      prevBtn.disabled = index <= 0;
      nextBtn.disabled = index >= aktuelleBilderListe.length - 1;
    }
  }

  // Nach Archivieren/Loeschen: zum naechsten Foto weiter (oder Lightbox
  // schliessen, falls keine Fotos mehr uebrig sind).
  function weiterNachAktion(entfernterIndex) {
    if (aktuelleBilderListe.length === 0) { schliessen(); return; }
    zeigeIndex(Math.min(entfernterIndex, aktuelleBilderListe.length - 1));
  }

  async function archivieren() {
    const datei = aktuelleBilderListe[index];
    if (!await bestaetigen(`"${datei}" in Archiv verschieben?`)) return;
    const res = await fetch('/api/photos/archivieren', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dateien: [datei] }),
    });
    if (!res.ok) { toast('Fehler beim Archivieren'); return; }
    toast('Archiviert');
    weiterNachAktion(entferneFotoLokal(datei));
  }

  async function loeschen() {
    const datei = aktuelleBilderListe[index];
    if (!await bestaetigen(`"${datei}" endgültig löschen?`)) return;
    const res = await fetch('/api/photos/loeschen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dateien: [datei], archiv: zeigeArchiv }),
    });
    if (!res.ok) { toast('Fehler beim Löschen'); return; }
    toast('Gelöscht');
    weiterNachAktion(entferneFotoLokal(datei));
  }

  function tastenHandler(e) {
    if (e.key === 'Escape') schliessen();
    else if (e.key === 'ArrowLeft' && index > 0) zeigeIndex(index - 1);
    else if (e.key === 'ArrowRight' && index < aktuelleBilderListe.length - 1) zeigeIndex(index + 1);
  }
  document.addEventListener('keydown', tastenHandler);

  if (!zeigeArchiv) {
    const archivBtn = document.createElement('button');
    archivBtn.className = 'btn btn-sm btn-ghost';
    archivBtn.textContent = '📦 Archivieren';
    archivBtn.addEventListener('click', (e) => { e.stopPropagation(); archivieren(); });
    aktionen.appendChild(archivBtn);
  }
  const loeschenBtn = document.createElement('button');
  loeschenBtn.className = 'btn btn-sm btn-danger';
  loeschenBtn.textContent = '🗑️ Löschen';
  loeschenBtn.addEventListener('click', (e) => { e.stopPropagation(); loeschen(); });
  aktionen.appendChild(loeschenBtn);

  back.append(figure);
  if (aktuelleBilderListe.length > 1) {
    // Als Overlay UEBER dem Bild (position:absolute in CSS) - nimmt dem Bild
    // dadurch keinen eigenen Platz weg, es bleibt auf dem Handy genauso gross
    // wie ohne Navigation.
    prevBtn = document.createElement('button');
    prevBtn.className = 'lightbox-nav lightbox-nav-prev';
    prevBtn.setAttribute('aria-label', 'Vorheriges Bild');
    prevBtn.textContent = '‹';
    prevBtn.addEventListener('click', (e) => { e.stopPropagation(); if (index > 0) zeigeIndex(index - 1); });

    nextBtn = document.createElement('button');
    nextBtn.className = 'lightbox-nav lightbox-nav-next';
    nextBtn.setAttribute('aria-label', 'Nächstes Bild');
    nextBtn.textContent = '›';
    nextBtn.addEventListener('click', (e) => { e.stopPropagation(); if (index < aktuelleBilderListe.length - 1) zeigeIndex(index + 1); });

    back.append(prevBtn, nextBtn);
  }
  back.addEventListener('click', schliessen);
  document.body.appendChild(back);
  zeigeIndex(index);
}

// Zweiter Anzeigemodus (Einstellung "Anzeigeart"): statt Einzelbild mit
// Pfeilen ein vertikaler Ablauf, der beim angetippten Foto beginnt und nach
// unten mit den aelteren Fotos weitergeht. Die Liste wird beim Oeffnen
// eingefroren (Snapshot der Dateinamen) - Archivieren/Loeschen waehrend des
// Scrollens soll die Indizes der noch nicht geladenen Batches nicht
// verschieben. Fotos werden per IntersectionObserver batchweise nachgeladen
// (nicht alle auf einmal ins DOM), damit lange Listen auf dem Handy nicht
// hunderte volle Bilder gleichzeitig vorhalten.
const FOTO_FEED_BATCH_GROESSE = 4;

function oeffneFotoFeed(startIndex) {
  const dateien = aktuelleBilderListe.slice(startIndex);

  const back = document.createElement('div');
  back.className = 'foto-feed';

  const schliessenBtn = document.createElement('button');
  schliessenBtn.className = 'foto-feed-schliessen';
  schliessenBtn.setAttribute('aria-label', 'Schließen');
  schliessenBtn.textContent = '✕';

  function schliessen() {
    beobachter.disconnect();
    back.remove();
    document.removeEventListener('keydown', tastenHandler);
  }
  function tastenHandler(e) {
    if (e.key === 'Escape') schliessen();
  }
  document.addEventListener('keydown', tastenHandler);
  schliessenBtn.addEventListener('click', schliessen);

  const liste = document.createElement('div');
  liste.className = 'foto-feed-liste';
  const sentinel = document.createElement('div');
  sentinel.className = 'foto-feed-sentinel';

  function fotoEintragErstellen(datei) {
    const eintrag = document.createElement('figure');
    eintrag.className = 'foto-feed-eintrag';

    const img = document.createElement('img');
    img.src = bildUrl(datei);
    img.alt = datei;
    img.loading = 'lazy';

    const caption = document.createElement('figcaption');
    caption.textContent = datei;

    const aktionen = document.createElement('div');
    aktionen.className = 'foto-feed-aktionen';
    if (!zeigeArchiv) {
      const archivBtn = document.createElement('button');
      archivBtn.className = 'btn btn-sm btn-ghost';
      archivBtn.textContent = '📦 Archivieren';
      archivBtn.addEventListener('click', async () => {
        if (!await bestaetigen(`"${datei}" in Archiv verschieben?`)) return;
        const res = await fetch('/api/photos/archivieren', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dateien: [datei] }),
        });
        if (!res.ok) { toast('Fehler beim Archivieren'); return; }
        toast('Archiviert');
        entferneFotoLokal(datei);
        eintrag.remove();
      });
      aktionen.appendChild(archivBtn);
    }
    const loeschenBtn = document.createElement('button');
    loeschenBtn.className = 'btn btn-sm btn-danger';
    loeschenBtn.textContent = '🗑️ Löschen';
    loeschenBtn.addEventListener('click', async () => {
      if (!await bestaetigen(`"${datei}" endgültig löschen?`)) return;
      const res = await fetch('/api/photos/loeschen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dateien: [datei], archiv: zeigeArchiv }),
      });
      if (!res.ok) { toast('Fehler beim Löschen'); return; }
      toast('Gelöscht');
      entferneFotoLokal(datei);
      eintrag.remove();
    });
    aktionen.appendChild(loeschenBtn);

    eintrag.append(img, caption, aktionen);
    return eintrag;
  }

  let naechsterIndex = 0;
  function naechsteBatchLaden() {
    const ende = Math.min(naechsterIndex + FOTO_FEED_BATCH_GROESSE, dateien.length);
    for (; naechsterIndex < ende; naechsterIndex++) {
      liste.appendChild(fotoEintragErstellen(dateien[naechsterIndex]));
    }
    if (naechsterIndex >= dateien.length) {
      beobachter.disconnect();
      sentinel.remove();
    }
  }

  const beobachter = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) naechsteBatchLaden();
  });

  back.append(schliessenBtn, liste);
  liste.appendChild(sentinel);
  document.body.appendChild(back);
  naechsteBatchLaden();
  beobachter.observe(sentinel);
}

async function ladeGalerieAnzeigeModus() {
  try {
    const res = await fetch('/api/galerie-anzeige');
    const data = await res.json();
    galerieAnzeigeModus = data.modus || 'einzelbild';
    galerieAnzeigeModusSel.value = galerieAnzeigeModus;
  } catch {
    toast('Anzeige-Einstellung konnte nicht geladen werden');
  }
}

async function speichereGalerieAnzeigeModus() {
  try {
    const res = await fetch('/api/galerie-anzeige', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modus: galerieAnzeigeModusSel.value }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      galerieAnzeigeModus = data.modus;
      toast('Anzeige-Einstellung gespeichert');
    } else {
      toast(data.error || 'Fehler beim Speichern');
    }
  } catch {
    toast('Fehler beim Speichern');
  }
}

function aktualisiereAuswahlLeiste() {
  const anzahl = ausgewaehlt.size;
  auswahlLeiste.classList.toggle('hidden', anzahl === 0);
  auswahlLeiste.querySelector('.auswahl-anzahl').textContent = `${anzahl} ausgewählt`;
  btnArchivierenBatch.style.display = zeigeArchiv ? 'none' : '';
}

function setzeTabs() {
  tabFotos.className = `btn ${!zeigeArchiv ? 'btn-primary' : 'btn-ghost'}`;
  tabArchiv.className = `btn ${zeigeArchiv ? 'btn-primary' : 'btn-ghost'}`;
}

async function laden() {
  ausgewaehlt.clear();
  alleAuswaehlenCb.checked = false;
  setzeTabs();
  aktualisiereAuswahlLeiste();
  try {
    const res = await fetch(`/api/photos?archiv=${zeigeArchiv ? 1 : 0}`);
    const data = await res.json();
    archivNotizen = data.notizen || {};
    render(data.bilder || []);
  } catch {
    toast('Fotos konnten nicht geladen werden');
  }
}

async function ladeSimulationDauer() {
  try {
    const res = await fetch('/api/simulation');
    const data = await res.json();
    simMinutenInp.value = Math.floor(data.dauer_sekunden / 60);
    simSekundenInp.value = data.dauer_sekunden % 60;
  } catch {
    toast('Simulationsdauer konnte nicht geladen werden');
  }
}

async function speichereSimulationDauer() {
  const minuten = parseInt(simMinutenInp.value, 10) || 0;
  const sekunden = parseInt(simSekundenInp.value, 10) || 0;
  const res = await fetch('/api/simulation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ minuten, sekunden }),
  });
  if (res.ok) {
    const data = await res.json();
    simMinutenInp.value = Math.floor(data.dauer_sekunden / 60);
    simSekundenInp.value = data.dauer_sekunden % 60;
    toast('Simulationsdauer gespeichert');
  } else {
    const data = await res.json().catch(() => ({}));
    toast(data.error || 'Fehler beim Speichern');
  }
}

// Generischer Feld-Renderer: von Kamera- UND Foto-Zeitplan-Einstellungen
// genutzt, beide liefern vom Server ein Feldschema (label/typ/min/max/optionen)
// + aktuelle Werte im selben Format (siehe KAMERA_FELDER/FOTO_ZEITPLAN_FELDER
// in galerie_server.py).
function renderFelderIn(container, felder, werte, idPrefix) {
  container.innerHTML = '';
  let letzteGruppe = null;
  felder.forEach((feld) => {
    if (feld.gruppe && feld.gruppe !== letzteGruppe) {
      letzteGruppe = feld.gruppe;
      const ueberschrift = document.createElement('div');
      ueberschrift.className = 'kamera-feld-gruppe';
      ueberschrift.textContent = feld.gruppe;
      container.appendChild(ueberschrift);
    }

    const wrap = document.createElement('div');
    wrap.className = 'kamera-feld' + (feld.typ === 'checkbox' ? ' checkbox-feld' : '');

    const label = document.createElement('label');
    label.textContent = feld.label;
    label.setAttribute('for', `${idPrefix}-${feld.key}`);

    let input;
    if (feld.typ === 'select') {
      input = document.createElement('select');
      feld.optionen.forEach(([wert, text]) => {
        const opt = document.createElement('option');
        opt.value = wert;
        opt.textContent = text;
        input.appendChild(opt);
      });
      input.value = String(werte[feld.key]);
    } else if (feld.typ === 'checkbox') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!werte[feld.key];
    } else {
      input = document.createElement('input');
      input.type = 'number';
      input.min = feld.min;
      input.max = feld.max;
      input.step = feld.step;
      input.value = werte[feld.key];
    }
    input.id = `${idPrefix}-${feld.key}`;

    if (feld.typ === 'checkbox') wrap.append(input, label);
    else wrap.append(label, input);
    container.appendChild(wrap);
  });
}

function sammleFelderAus(felder, idPrefix) {
  const werte = {};
  felder.forEach((feld) => {
    const input = document.getElementById(`${idPrefix}-${feld.key}`);
    if (feld.typ === 'checkbox') werte[feld.key] = input.checked;
    else if (feld.typ === 'zahl') werte[feld.key] = parseFloat(input.value);
    else werte[feld.key] = input.value;
  });
  return werte;
}

function renderKameraFelder(felder, werte) {
  kameraFelder = felder;
  renderFelderIn(kameraFelderContainer, felder, werte, 'kf');
}

function sammleKameraWerte() {
  return sammleFelderAus(kameraFelder, 'kf');
}

async function ladeKameraEinstellungen() {
  try {
    const res = await fetch('/api/kamera');
    const data = await res.json();
    renderKameraFelder(data.felder, data.werte);
  } catch {
    toast('Kamera-Einstellungen konnten nicht geladen werden');
  }
}

async function speichereKameraEinstellungen() {
  const res = await fetch('/api/kamera', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sammleKameraWerte()),
  });
  if (res.ok) {
    const data = await res.json();
    renderKameraFelder(kameraFelder, data.werte);
    toast('Kamera-Einstellungen gespeichert');
  } else {
    const data = await res.json().catch(() => ({}));
    toast(data.error || 'Fehler beim Speichern');
  }
}

async function kameraZuruecksetzen() {
  if (!await bestaetigen('Alle Kamera-Einstellungen auf Standard zurücksetzen?')) return;
  const res = await fetch('/api/kamera/zuruecksetzen', { method: 'POST' });
  if (res.ok) {
    const data = await res.json();
    renderKameraFelder(kameraFelder, data.werte);
    toast('Zurückgesetzt');
  } else {
    toast('Fehler beim Zurücksetzen');
  }
}

function renderFotoZeitplanFelder(felder, werte) {
  fotoZeitplanFelder = felder;
  renderFelderIn(fotoZeitplanFelderContainer, felder, werte, 'fz');
}

function sammleFotoZeitplanWerte() {
  return sammleFelderAus(fotoZeitplanFelder, 'fz');
}

async function ladeFotoZeitplan() {
  try {
    const res = await fetch('/api/foto-zeitplan');
    const data = await res.json();
    renderFotoZeitplanFelder(data.felder, data.werte);
  } catch {
    toast('Foto-Zeitplan konnte nicht geladen werden');
  }
}

async function speichereFotoZeitplan() {
  const res = await fetch('/api/foto-zeitplan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sammleFotoZeitplanWerte()),
  });
  if (res.ok) {
    const data = await res.json();
    renderFotoZeitplanFelder(fotoZeitplanFelder, data.werte);
    toast('Foto-Zeitplan gespeichert');
  } else {
    const data = await res.json().catch(() => ({}));
    toast(data.error || 'Fehler beim Speichern');
  }
}

async function fotoZeitplanZuruecksetzen() {
  if (!await bestaetigen('Foto-Zeitplan auf Standard zurücksetzen?')) return;
  const res = await fetch('/api/foto-zeitplan/zuruecksetzen', { method: 'POST' });
  if (res.ok) {
    const data = await res.json();
    renderFotoZeitplanFelder(fotoZeitplanFelder, data.werte);
    toast('Zurückgesetzt');
  } else {
    toast('Fehler beim Zurücksetzen');
  }
}

let speicherLiegtAufRam = true;

function aktualisiereSpeicherFeldSichtbarkeit() {
  speicherGroesseFeld.style.display = speicherOrtSel.value === 'ram' ? '' : 'none';
}

function aktualisiereRamNeustartWarnung() {
  ramNeustartWarnungEl.hidden = !speicherLiegtAufRam;
}

async function ladeTuerEinstellungen() {
  try {
    const res = await fetch('/api/tuer-einstellungen');
    const data = await res.json();
    tuerKontaktInvertiertCb.checked = !!data.kontakt_invertiert;
  } catch {
    // Kein Toast hier - eine leere/nicht ladbare Checkbox bei diesem selten
    // genutzten Feld faellt kaum auf, ein Fehler-Toast gleich beim Laden der
    // Seite waere unverhaeltnismaessig aufdringlich.
  }
}

async function speichereTuerEinstellungen() {
  tuerKontaktSpeichernBtn.disabled = true;
  try {
    const res = await fetch('/api/tuer-einstellungen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kontakt_invertiert: tuerKontaktInvertiertCb.checked }),
    });
    if (res.ok) {
      toast('Gespeichert');
    } else {
      toast('Fehler beim Speichern');
    }
  } catch {
    toast('Fehler beim Speichern');
  } finally {
    tuerKontaktSpeichernBtn.disabled = false;
  }
}

async function ladeSpeicherEinstellungen() {
  try {
    const res = await fetch('/api/speicher');
    const data = await res.json();
    speicherOrtSel.value = data.speicherort;
    speicherGroesseInp.value = data.ram_groesse_mb;
    speicherLiegtAufRam = data.ist_aktuell_ram;
    aktualisiereSpeicherFeldSichtbarkeit();
    aktualisiereRamNeustartWarnung();
    const zustand = data.ist_aktuell_ram ? 'RAM-Speicher' : 'SD-Karte';
    speicherInfoEl.textContent =
      `Aktuell: ${data.aktuelle_anzahl_fotos} Foto(s), ${data.aktuelle_nutzung_mb} MB belegt, ` +
      `tatsächlich gerade auf ${zustand}. Empfehlung für diesen Pi (${Math.round(data.gesamt_ram_mb / 1024 * 10) / 10} GB RAM): ` +
      `${data.empfehlung_mb} MB. Bei ${data.ram_groesse_mb} MB passen ungefähr ${data.geschaetzte_anzahl_fotos} Fotos ` +
      `(Ø ${data.durchschnittliche_foto_kb} KB/Foto) hinein.`;
    // Eingestellter Speicherort und tatsaechlicher Mount-Zustand koennen
    // auseinanderlaufen, wenn die Umschaltung im Hintergrund fehlgeschlagen
    // ist (z.B. sudoers-Regel fehlt noch, install.sh nicht erneut gelaufen) -
    // ohne diesen Hinweis wuerde das sonst komplett unbemerkt bleiben.
    if ((data.speicherort === 'ram') !== data.ist_aktuell_ram) {
      speicherInfoEl.textContent += ' ⚠️ Eingestellt ist "' +
        (data.speicherort === 'ram' ? 'RAM-Speicher' : 'SD-Karte') +
        '", tatsächlich aktiv ist aber "' + zustand + '" - die Umschaltung ist offenbar fehlgeschlagen. ' +
        'Erneut auf "Übernehmen" klicken, oder auf dem Pi prüfen, ob install.sh nach diesem Update erneut gelaufen ist.';
    }
  } catch {
    speicherInfoEl.textContent = 'Speicher-Informationen konnten nicht geladen werden.';
  }
}

async function speicherUebernehmen() {
  const speicherort = speicherOrtSel.value;
  const groesse = parseInt(speicherGroesseInp.value, 10);
  if (isNaN(groesse) || groesse < 32 || groesse > 2048) { toast('Ungültige Größe (32-2048 MB)'); return; }

  let hinweis = `Speicherort auf „${speicherort === 'ram' ? 'RAM-Speicher' : 'SD-Karte'}“ umstellen? ` +
    'Bestehende Fotos werden automatisch mitgenommen, beide Dienste starten dabei kurz neu.';
  if (speicherort === 'ram') {
    hinweis += ' Achtung: Fotos im RAM-Speicher gehen ab jetzt bei jedem Neustart/Stromausfall verloren, wenn sie nicht vorher archiviert wurden.';
  }
  if (!await bestaetigen(hinweis)) return;

  speicherUebernehmenBtn.disabled = true;
  try {
    const res = await fetch('/api/speicher', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speicherort, ram_groesse_mb: groesse }),
    });
    if (res.ok) {
      toast('Wird angewendet – Seite lädt in Kürze neu…');
      speicherLiegtAufRam = speicherort === 'ram';
      aktualisiereRamNeustartWarnung();
      setTimeout(() => location.reload(), 6000);
    } else {
      const data = await res.json().catch(() => ({}));
      toast(data.error || 'Fehler beim Umschalten');
      speicherUebernehmenBtn.disabled = false;
    }
  } catch {
    toast('Verbindung unterbrochen – Seite lädt neu…');
    setTimeout(() => location.reload(), 6000);
  }
}

function renderPushoverMeldungen(schema, meldungen) {
  pushoverMeldungenSchema = schema;
  pushoverMeldungenContainer.innerHTML = '';
  schema.forEach(({ id, label }) => {
    const m = meldungen[id] || { aktiv: true, text: '' };

    const zeile = document.createElement('div');
    zeile.className = 'pushover-zeile';

    const kopf = document.createElement('label');
    kopf.className = 'pushover-zeile-kopf';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `pm-aktiv-${id}`;
    cb.checked = !!m.aktiv;
    const kopfText = document.createElement('span');
    kopfText.textContent = label;
    kopf.append(cb, kopfText);

    const textInp = document.createElement('input');
    textInp.type = 'text';
    textInp.id = `pm-text-${id}`;
    textInp.value = m.text || '';
    textInp.className = 'pushover-text-inp';

    zeile.append(kopf, textInp);
    pushoverMeldungenContainer.appendChild(zeile);
  });
}

function sammlePushoverMeldungen() {
  const meldungen = {};
  pushoverMeldungenSchema.forEach(({ id }) => {
    meldungen[id] = {
      aktiv: document.getElementById(`pm-aktiv-${id}`).checked,
      text: document.getElementById(`pm-text-${id}`).value,
    };
  });
  return meldungen;
}

async function ladePushoverEinstellungen() {
  try {
    const res = await fetch('/api/pushover');
    const data = await res.json();
    pushoverAktivInp.checked = data.werte.aktiv !== false;
    pushoverTokenInp.value = data.werte.token;
    pushoverUserInp.value = data.werte.user;
    renderPushoverMeldungen(data.meldungen_schema, data.werte.meldungen);
  } catch {
    toast('Pushover-Einstellungen konnten nicht geladen werden');
  }
}

async function speicherePushoverEinstellungen() {
  const body = {
    aktiv: pushoverAktivInp.checked,
    token: pushoverTokenInp.value,
    user: pushoverUserInp.value,
    meldungen: sammlePushoverMeldungen(),
  };
  const res = await fetch('/api/pushover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    const data = await res.json();
    pushoverAktivInp.checked = data.werte.aktiv !== false;
    pushoverTokenInp.value = data.werte.token;
    pushoverUserInp.value = data.werte.user;
    renderPushoverMeldungen(pushoverMeldungenSchema, data.werte.meldungen);
    toast('Pushover-Einstellungen gespeichert');
  } else {
    const data = await res.json().catch(() => ({}));
    toast(data.error || 'Fehler beim Speichern');
  }
}

function pushoverAlleSetzen(aktiv) {
  pushoverMeldungenSchema.forEach(({ id }) => {
    document.getElementById(`pm-aktiv-${id}`).checked = aktiv;
  });
  speicherePushoverEinstellungen();
}

async function pushoverTestSenden() {
  pushoverTestBtn.disabled = true;
  toast('Sende Testnachricht…');
  try {
    const res = await fetch('/api/pushover/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: pushoverTokenInp.value, user: pushoverUserInp.value }),
    });
    if (res.ok) {
      toast('Testnachricht gesendet – prüfe dein Handy');
    } else {
      const data = await res.json().catch(() => ({}));
      toast(data.error || 'Test fehlgeschlagen');
    }
  } catch {
    toast('Test fehlgeschlagen');
  } finally {
    pushoverTestBtn.disabled = false;
  }
}

let telegramVerbindenPollTimer = null;

function renderTelegramChats(chats) {
  telegramChatsListeEl.innerHTML = '';
  const eintraege = Object.entries(chats || {});
  if (eintraege.length === 0) {
    telegramChatsListeEl.innerHTML = '<p class="muted" style="font-size:.85rem;">Noch niemand verbunden.</p>';
    return;
  }
  eintraege.forEach(([chatId, info]) => {
    const zeile = document.createElement('div');
    zeile.className = 'telegram-chat-zeile';
    const text = document.createElement('span');
    text.textContent = `✅ ${info.name} (verbunden seit ${info.verknuepft_am})`;
    const trennenBtn = document.createElement('button');
    trennenBtn.className = 'btn btn-sm btn-ghost';
    trennenBtn.textContent = 'Trennen';
    trennenBtn.addEventListener('click', () => telegramTrennen(chatId));
    zeile.append(text, trennenBtn);
    telegramChatsListeEl.appendChild(zeile);
  });
}

async function ladeTelegramEinstellungen() {
  try {
    const res = await fetch('/api/telegram');
    const data = await res.json();
    telegramAktivInp.checked = data.werte.aktiv !== false;
    telegramBotTokenInp.value = data.werte.bot_token;
    renderTelegramChats(data.chats);
    return data.chats;
  } catch {
    toast('Telegram-Einstellungen konnten nicht geladen werden');
    return {};
  }
}

async function speichereTelegramEinstellungen() {
  telegramSpeichernBtn.disabled = true;
  try {
    const res = await fetch('/api/telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aktiv: telegramAktivInp.checked, bot_token: telegramBotTokenInp.value }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      telegramAktivInp.checked = data.werte.aktiv !== false;
      telegramBotTokenInp.value = data.werte.bot_token;
      toast(data.warnung || 'Telegram-Einstellungen gespeichert');
    } else {
      toast(data.error || 'Fehler beim Speichern');
    }
  } catch {
    toast('Fehler beim Speichern');
  } finally {
    telegramSpeichernBtn.disabled = false;
  }
}

function telegramVerbindenPollingBeenden() {
  if (telegramVerbindenPollTimer) {
    clearTimeout(telegramVerbindenPollTimer);
    telegramVerbindenPollTimer = null;
  }
}

async function telegramVerbinden() {
  telegramVerbindenPollingBeenden();
  telegramVerbindenBtn.disabled = true;
  try {
    const res = await fetch('/api/telegram/verbinden', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(data.error || 'Verbinden fehlgeschlagen');
      return;
    }
    const link = `https://t.me/${data.bot_username}?start=${data.code}`;
    telegramVerbindenBoxEl.hidden = false;
    telegramVerbindenBoxEl.innerHTML =
      `<p>1. <a href="${link}" target="_blank" rel="noopener">Diesen Link antippen</a><br>` +
      '2. In Telegram auf „Start“ tippen<br>' +
      '3. Kurz warten – diese Seite erkennt die Verbindung automatisch.</p>' +
      '<p class="muted" style="font-size:.8rem;">Wartet auf Bestätigung… (Link 10 Minuten gültig)</p>';

    const vorherigeChatIds = new Set(Object.keys(await ladeTelegramEinstellungen()));
    const pruefeVerbindung = async () => {
      const chats = await ladeTelegramEinstellungen();
      const neueChatIds = Object.keys(chats).filter((id) => !vorherigeChatIds.has(id));
      if (neueChatIds.length > 0) {
        telegramVerbindenBoxEl.hidden = true;
        toast('Telegram erfolgreich verbunden!');
        telegramVerbindenPollingBeenden();
        return;
      }
      telegramVerbindenPollTimer = setTimeout(pruefeVerbindung, 3000);
    };
    telegramVerbindenPollTimer = setTimeout(pruefeVerbindung, 3000);
  } catch {
    toast('Verbinden fehlgeschlagen');
  } finally {
    telegramVerbindenBtn.disabled = false;
  }
}

async function telegramTestSenden() {
  telegramTestBtn.disabled = true;
  toast('Sende Testnachricht…');
  try {
    const res = await fetch('/api/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: telegramBotTokenInp.value }),
    });
    if (res.ok) {
      toast('Testnachricht gesendet – prüfe Telegram');
    } else {
      const data = await res.json().catch(() => ({}));
      toast(data.error || 'Test fehlgeschlagen');
    }
  } catch {
    toast('Test fehlgeschlagen');
  } finally {
    telegramTestBtn.disabled = false;
  }
}

async function telegramTrennen(chatId) {
  if (!await bestaetigen('Diese Telegram-Verbindung trennen?')) return;
  try {
    const res = await fetch('/api/telegram/trennen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId }),
    });
    if (res.ok) {
      const data = await res.json();
      renderTelegramChats(data.chats);
      toast('Verbindung getrennt');
    } else {
      toast('Fehler beim Trennen');
    }
  } catch {
    toast('Fehler beim Trennen');
  }
}

async function meldeSystemFehler(res, standardText) {
  const data = await res.json().catch(() => ({}));
  toast(data.error || standardText);
}

function ramVerlustHinweis() {
  return speicherLiegtAufRam
    ? ' Achtung: Fotos liegen aktuell im RAM-Speicher – alle noch nicht archivierten Fotos gehen dabei verloren.'
    : '';
}

async function neustart() {
  if (!await bestaetigen('Pi wirklich neu starten?' + ramVerlustHinweis())) return;
  const res = await fetch('/api/system/neustart', { method: 'POST' });
  if (res.ok) toast('Pi startet neu…');
  else meldeSystemFehler(res, 'Fehler beim Neustart');
}

async function herunterfahren() {
  if (!await bestaetigen('Pi wirklich herunterfahren? Danach muss der Strom manuell getrennt und wieder verbunden werden, um ihn erneut zu starten.' + ramVerlustHinweis())) return;
  const res = await fetch('/api/system/herunterfahren', { method: 'POST' });
  if (res.ok) toast('Pi fährt herunter…');
  else meldeSystemFehler(res, 'Fehler beim Herunterfahren');
}

async function diensteNeustart() {
  if (!await bestaetigen('Dienste (Türüberwachung + Galerie) jetzt neu starten? Kurze Unterbrechung möglich.')) return;
  btnDiensteNeustart.disabled = true;
  try {
    const res = await fetch('/api/system/dienste-neustart', { method: 'POST' });
    if (res.ok) {
      toast('Dienste starten neu – Seite lädt in Kürze neu…');
      setTimeout(() => location.reload(), 4000);
    } else {
      await meldeSystemFehler(res, 'Fehler beim Neustart der Dienste');
      btnDiensteNeustart.disabled = false;
    }
  } catch {
    // Verbindung ist evtl. schon unterbrochen, weil die Galerie sich selbst neu startet
    toast('Verbindung unterbrochen – Seite lädt neu…');
    setTimeout(() => location.reload(), 4000);
  }
}

async function fotoAufnehmen() {
  btnFotoAufnehmen.disabled = true;
  toast('Aufnahme läuft…');
  try {
    const res = await fetch('/api/foto/einzel', { method: 'POST' });
    if (res.ok) {
      const daten = await res.json();
      if (daten.geloescht) {
        toast(`Foto war zu dunkel und wurde gelöscht\nHelligkeit: ${Math.round(daten.helligkeit)}`);
      } else if (daten.helligkeit != null) {
        toast(`Foto aufgenommen\nHelligkeit: ${Math.round(daten.helligkeit)}`);
      } else {
        toast('Foto aufgenommen');
      }
      zeigeArchiv = false;
      laden();
    } else {
      toast('Fehler bei der Aufnahme');
    }
  } catch {
    toast('Fehler bei der Aufnahme');
  } finally {
    btnFotoAufnehmen.disabled = false;
  }
}

async function tuerSimulieren() {
  btnTuerSimulieren.disabled = true;
  try {
    const res = await fetch('/api/tuer/simulieren', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      toast(`Türöffnung simuliert (${data.dauer_sekunden}s)`);
    } else {
      toast('Fehler beim Simulieren');
    }
  } catch {
    toast('Fehler beim Simulieren');
  } finally {
    setTimeout(() => { btnTuerSimulieren.disabled = false; }, 2000);
  }
}

// Wie bestaetigen(), aber mit mehreren Auswahl-Buttons statt Ja/Nein - fuer
// die Dauer der Pushover-Stummschaltung. Gibt die gewaehlte Minutenzahl
// zurueck, oder null bei "Abbrechen"/Klick auf den Hintergrund.
function waehleStummDauer() {
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'confirm-back';

    const box = document.createElement('div');
    box.className = 'confirm-box';

    const p = document.createElement('p');
    p.textContent = 'Pushover für wie lange stummschalten?';

    const optionen = document.createElement('div');
    optionen.className = 'pushover-stumm-popup-optionen';
    PUSHOVER_STUMM_DAUER_OPTIONEN_MIN.forEach((minuten) => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-ghost';
      btn.textContent = `${minuten} Min.`;
      btn.addEventListener('click', () => schliessen(minuten));
      optionen.appendChild(btn);
    });

    const aktionen = document.createElement('div');
    aktionen.className = 'confirm-aktionen';
    const abbrechenBtn = document.createElement('button');
    abbrechenBtn.className = 'btn btn-ghost';
    abbrechenBtn.textContent = 'Abbrechen';
    abbrechenBtn.addEventListener('click', () => schliessen(null));
    aktionen.appendChild(abbrechenBtn);

    box.append(p, optionen, aktionen);
    back.appendChild(box);
    document.body.appendChild(back);

    const schliessen = (ergebnis) => {
      back.remove();
      resolve(ergebnis);
    };
    back.addEventListener('click', (e) => { if (e.target === back) schliessen(null); });
  });
}

async function pushoverStummStarten(minuten) {
  btnPushoverStumm.disabled = true;
  try {
    const res = await fetch('/api/pushover/stumm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aktiv: true, dauer_minuten: minuten }),
    });
    if (res.ok) {
      toast(`Pushover für ${minuten} Minuten stummgeschaltet`);
      ladeStatus();
    } else {
      toast('Fehler beim Stummschalten');
    }
  } catch {
    toast('Fehler beim Stummschalten');
  } finally {
    btnPushoverStumm.disabled = false;
  }
}

async function pushoverStummAufheben() {
  btnPushoverStumm.disabled = true;
  try {
    const res = await fetch('/api/pushover/stumm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aktiv: false }),
    });
    if (res.ok) {
      toast('Stummschaltung aufgehoben');
      ladeStatus();
    } else {
      toast('Fehler beim Umschalten');
    }
  } catch {
    toast('Fehler beim Umschalten');
  } finally {
    btnPushoverStumm.disabled = false;
  }
}

async function pushoverStummKlick() {
  if (btnPushoverStumm.dataset.aktiv === '1') {
    await pushoverStummAufheben();
    return;
  }
  const minuten = await waehleStummDauer();
  if (minuten === null) return;
  await pushoverStummStarten(minuten);
}

async function batchArchivieren() {
  if (ausgewaehlt.size === 0) return;
  if (!await bestaetigen(`${ausgewaehlt.size} Foto(s) in Archiv verschieben?`)) return;
  const res = await fetch('/api/photos/archivieren', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dateien: [...ausgewaehlt] }),
  });
  if (res.ok) { toast('Archiviert'); laden(); } else { toast('Fehler beim Archivieren'); }
}

async function batchLoeschen() {
  if (ausgewaehlt.size === 0) return;
  const res = await fetch('/api/photos/loeschen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dateien: [...ausgewaehlt], archiv: zeigeArchiv }),
  });
  if (res.ok) { toast('Gelöscht'); laden(); } else { toast('Fehler beim Löschen'); }
}

function render(bilder) {
  grid.innerHTML = '';
  aktuelleBilderListe = bilder;

  if (bilder.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.innerHTML = `<div class="empty-title">Keine Fotos</div>
      <div class="empty-sub muted">${zeigeArchiv ? 'Das Archiv ist leer.' : 'Es liegen noch keine Fotos vor.'}</div>`;
    grid.appendChild(empty);
    return;
  }

  const liste = document.createElement('ul');
  liste.className = 'card-list';

  bilder.forEach((datei, index) => {
    const vorschauUrl = thumbUrl(datei);
    const li = document.createElement('li');
    li.className = 'card';

    const thumbWrap = document.createElement('div');
    thumbWrap.className = 'thumb-wrap';

    // Label als Tap-Ziel statt nackter Checkbox: auf dem Handy sonst zu klein
    // getroffen, ein knapp daneben liegender Tap landet dann auf dem <img>
    // darunter und oeffnet die Lightbox statt die Auswahl umzuschalten.
    const checkboxZone = document.createElement('label');
    checkboxZone.className = 'checkbox-tap-zone';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'bild-checkbox';
    checkbox.dataset.datei = datei;
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) ausgewaehlt.add(datei); else ausgewaehlt.delete(datei);
      aktualisiereAuswahlLeiste();
    });
    checkboxZone.appendChild(checkbox);

    const img = document.createElement('img');
    img.className = 'thumb';
    img.src = vorschauUrl;
    img.alt = datei;
    img.loading = 'lazy';
    img.addEventListener('click', () => {
      if (galerieAnzeigeModus === 'feed') oeffneFotoFeed(index);
      else oeffneLightbox(index);
    });

    thumbWrap.append(checkboxZone, img);

    const main = document.createElement('div');
    main.className = 'card-main';
    const sub = document.createElement('div');
    sub.className = 'card-sub';
    sub.textContent = datei;
    main.appendChild(sub);

    if (zeigeArchiv) {
      const notiz = archivNotizen[datei];
      const notizWrap = document.createElement('div');
      notizWrap.className = 'card-notiz';
      if (notiz) {
        const notizText = document.createElement('div');
        notizText.className = 'card-notiz-text';
        notizText.textContent = `📝 ${notiz.text} (${notiz.datum})`;
        notizWrap.appendChild(notizText);
      }
      const notizBtn = document.createElement('button');
      notizBtn.className = 'btn btn-sm btn-ghost card-notiz-btn';
      notizBtn.textContent = notiz ? 'Notiz bearbeiten' : '📝 Notiz hinzufügen';
      notizBtn.addEventListener('click', () => notizBearbeiten(datei, notiz ? notiz.text : ''));
      notizWrap.appendChild(notizBtn);
      main.appendChild(notizWrap);
    }

    li.append(thumbWrap, main);
    liste.appendChild(li);
  });

  grid.appendChild(liste);
}

// Mehrfachauswahl per Maus-Rahmen ("Rubber-Band-Select"): Rechteck aufziehen
// markiert alle ueberstrichenen Fotos, ohne sie einzeln anklicken zu muessen.
// Nur fuer echte Maeuse (pointerType 'mouse') - Touch/Tap-Verhalten auf dem
// Handy bleibt dadurch komplett unveraendert (siehe checkbox-tap-zone).
let dragStart = null;
let dragAktiv = false;
let dragRechteckEl = null;
const DRAG_SCHWELLE_PX = 4;

function dragUeberlappungAnwenden(x1, y1, x2, y2) {
  grid.querySelectorAll('.card').forEach((card) => {
    const rect = card.getBoundingClientRect();
    const ueberlappt = rect.left < x2 && rect.right > x1 && rect.top < y2 && rect.bottom > y1;
    if (!ueberlappt) return;
    const checkbox = card.querySelector('.bild-checkbox');
    if (checkbox && !checkbox.checked) {
      checkbox.checked = true;
      ausgewaehlt.add(checkbox.dataset.datei);
    }
  });
  aktualisiereAuswahlLeiste();
}

grid.addEventListener('pointerdown', (e) => {
  if (e.pointerType !== 'mouse' || e.button !== 0) return;
  // Auf der Checkbox selbst soll ganz normal einzeln getoggelt werden koennen,
  // ohne dass ein winziges Zittern beim Klicken schon als Drag gewertet wird.
  if (e.target.closest('.checkbox-tap-zone')) return;
  dragStart = { x: e.clientX, y: e.clientY };
  // Verhindert, dass der Browser das Ziehen als native Text-/Bild-Auswahl
  // interpretiert (sonst "gewinnt" die native Selektion gegen das Rechteck).
  e.preventDefault();
});

window.addEventListener('pointermove', (e) => {
  if (!dragStart) return;
  const dx = Math.abs(e.clientX - dragStart.x);
  const dy = Math.abs(e.clientY - dragStart.y);
  if (!dragAktiv) {
    if (dx < DRAG_SCHWELLE_PX && dy < DRAG_SCHWELLE_PX) return;
    dragAktiv = true;
    dragRechteckEl = document.createElement('div');
    dragRechteckEl.className = 'auswahl-rechteck';
    document.body.appendChild(dragRechteckEl);
  }
  const x1 = Math.min(dragStart.x, e.clientX), x2 = Math.max(dragStart.x, e.clientX);
  const y1 = Math.min(dragStart.y, e.clientY), y2 = Math.max(dragStart.y, e.clientY);
  dragRechteckEl.style.left = `${x1}px`;
  dragRechteckEl.style.top = `${y1}px`;
  dragRechteckEl.style.width = `${x2 - x1}px`;
  dragRechteckEl.style.height = `${y2 - y1}px`;
  dragUeberlappungAnwenden(x1, y1, x2, y2);
  e.preventDefault();
});

window.addEventListener('pointerup', () => {
  if (dragRechteckEl) { dragRechteckEl.remove(); dragRechteckEl = null; }
  dragStart = null;
  // Erst nach dem naechsten Frame zuruecksetzen: der Klick-Schlucker unten
  // (capture-Phase) muss dragAktiv noch als "true" sehen, um den auf einen
  // echten Drag folgenden Klick (z.B. auf das Bild) zu unterdruecken - sonst
  // wuerde nach dem Aufziehen des Rahmens zusaetzlich die Lightbox aufgehen.
  requestAnimationFrame(() => { dragAktiv = false; });
});

grid.addEventListener('click', (e) => {
  if (!dragAktiv) return;
  dragAktiv = false;
  e.stopPropagation();
  e.preventDefault();
}, true);

// Mehrfachauswahl per Wischen auf dem Handy: startet nur, wenn der Finger
// auf einer Checkbox aufsetzt (klare Absicht, kein zufaelliges Scrollen wird
// dadurch blockiert) - beim Ziehen ueber WEITERE Karten (nicht nur deren
// winzige Checkbox-Ecke, sondern die ganze Kachel) werden diese mit
// ausgewaehlt. Komplett unabhaengig von der Maus-Rahmen-Auswahl oben.
let touchAuswahlAktiv = false;
let touchAuswahlStartKarte = null;
let touchAuswahlLetzteKarte = null;
let touchAuswahlBewegt = false;

function touchKarteMarkieren(karte) {
  const checkbox = karte && karte.querySelector('.bild-checkbox');
  if (checkbox && !checkbox.checked) {
    checkbox.checked = true;
    ausgewaehlt.add(checkbox.dataset.datei);
    aktualisiereAuswahlLeiste();
  }
}

grid.addEventListener('touchstart', (e) => {
  const zone = e.target.closest('.checkbox-tap-zone');
  if (!zone) return;
  touchAuswahlAktiv = true;
  touchAuswahlBewegt = false;
  touchAuswahlStartKarte = zone.closest('.card');
  touchAuswahlLetzteKarte = touchAuswahlStartKarte;
  // Absichtlich HIER noch nicht die Checkbox selbst setzen: ein einfacher Tap
  // (touchstart+touchend ohne Bewegung) toggelt die Checkbox schon ganz normal
  // per nativem Klick - wuerde man hier zusaetzlich markieren, wuerde der
  // gleich folgende native Klick sie sofort wieder abwaehlen (toggelt ja).
}, { passive: true });

grid.addEventListener('touchmove', (e) => {
  if (!touchAuswahlAktiv) return;
  const touch = e.touches[0];
  const el = document.elementFromPoint(touch.clientX, touch.clientY);
  const karte = el && el.closest('.card');
  // Bei Stillstand/Zittern auf derselben Karte KEIN preventDefault - sonst
  // koennte das den nativen Klick eines einfachen Taps unterdruecken.
  if (!karte || karte === touchAuswahlLetzteKarte) return;
  e.preventDefault(); // ab hier ein echter Auswahl-Zug, nicht mehr Scrollen
  if (!touchAuswahlBewegt) {
    touchAuswahlBewegt = true;
    touchKarteMarkieren(touchAuswahlStartKarte); // Start-Karte war noch nicht dabei
  }
  touchAuswahlLetzteKarte = karte;
  touchKarteMarkieren(karte);
}, { passive: false });

grid.addEventListener('touchend', () => {
  touchAuswahlAktiv = false;
  touchAuswahlStartKarte = null;
  touchAuswahlLetzteKarte = null;
});

tabFotos.addEventListener('click', () => { zeigeArchiv = false; laden(); });
tabArchiv.addEventListener('click', () => { zeigeArchiv = true; laden(); });

alleAuswaehlenCb.addEventListener('change', () => {
  document.querySelectorAll('.bild-checkbox').forEach((cb) => {
    cb.checked = alleAuswaehlenCb.checked;
    if (alleAuswaehlenCb.checked) ausgewaehlt.add(cb.dataset.datei);
    else ausgewaehlt.delete(cb.dataset.datei);
  });
  aktualisiereAuswahlLeiste();
});

btnAuswahlAufheben.addEventListener('click', () => {
  document.querySelectorAll('.bild-checkbox').forEach((cb) => { cb.checked = false; });
  alleAuswaehlenCb.checked = false;
  ausgewaehlt.clear();
  aktualisiereAuswahlLeiste();
});

btnArchivierenBatch.addEventListener('click', batchArchivieren);
btnLoeschenBatch.addEventListener('click', batchLoeschen);
btnFotoAufnehmen.addEventListener('click', fotoAufnehmen);
kameraSpeichernBtn.addEventListener('click', speichereKameraEinstellungen);
kameraZuruecksetzenBtn.addEventListener('click', kameraZuruecksetzen);
btnNeustart.addEventListener('click', neustart);
btnHerunterfahren.addEventListener('click', herunterfahren);
btnDiensteNeustart.addEventListener('click', diensteNeustart);
tuerKontaktSpeichernBtn.addEventListener('click', speichereTuerEinstellungen);
btnTuerSimulieren.addEventListener('click', tuerSimulieren);
btnPushoverStumm.addEventListener('click', pushoverStummKlick);
fotoZeitplanSpeichernBtn.addEventListener('click', speichereFotoZeitplan);
galerieAnzeigeModusSel.addEventListener('change', speichereGalerieAnzeigeModus);
fotoZeitplanZuruecksetzenBtn.addEventListener('click', fotoZeitplanZuruecksetzen);
pushoverSpeichernBtn.addEventListener('click', speicherePushoverEinstellungen);
pushoverAlleAktivierenBtn.addEventListener('click', () => pushoverAlleSetzen(true));
pushoverAlleDeaktivierenBtn.addEventListener('click', () => pushoverAlleSetzen(false));
pushoverTestBtn.addEventListener('click', pushoverTestSenden);
telegramSpeichernBtn.addEventListener('click', speichereTelegramEinstellungen);
telegramVerbindenBtn.addEventListener('click', telegramVerbinden);
telegramTestBtn.addEventListener('click', telegramTestSenden);
simDauerSpeichernBtn.addEventListener('click', speichereSimulationDauer);
speicherOrtSel.addEventListener('change', aktualisiereSpeicherFeldSichtbarkeit);
speicherUebernehmenBtn.addEventListener('click', speicherUebernehmen);
hauptTabBtns.forEach((btn) => btn.addEventListener('click', () => zeigeAnsicht(btn.dataset.ansicht)));

// Setup-Portal (WLAN/Update/Backup) laeuft als eigener Dienst auf Port 80,
// nicht auf dem Galerie-Port dieser Seite - deshalb Host ohne Port neu
// zusammensetzen statt einfach die aktuelle URL zu nehmen. Trifft den
// Normalfall (Port 80 frei beim Einrichten); laeuft die Setup-Seite
// ausnahmsweise auf einem Ausweich-Port, muesste die Adresse von Hand
// aufgerufen werden. Zeigt bewusst auf die Portal-Startseite (nicht /wifi
// direkt) - von dort aus geht's zu WLAN, Update UND Backup.
linkSetupSeiteEl.href = `${location.protocol}//${location.hostname}/`;

// Akkordeon: von den Einstellungen-Abschnitten soll immer nur einer
// aufgeklappt sein - macht die lange Liste uebersichtlicher.
einstellungenDetailsListe.forEach((details) => {
  details.addEventListener('toggle', () => {
    if (!details.open) return;
    einstellungenDetailsListe.forEach((andere) => {
      if (andere !== details) andere.open = false;
    });
  });
});

zeigeAnsicht('fotos');
ladeKameraEinstellungen();
ladeFotoZeitplan();
ladePushoverEinstellungen();
ladeTelegramEinstellungen();
ladeSimulationDauer();
ladeTuerEinstellungen();
ladeSpeicherEinstellungen();
ladeGalerieAnzeigeModus();
laden();
ladeStatus();
setInterval(ladeStatus, 5000);
