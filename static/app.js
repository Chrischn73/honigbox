// Von der Setup-Seite (honigbox_setup_portal.py, app_version()) per Regex
// ausgelesen, um die installierte Version mit GitHub-Releases zu vergleichen -
// beim Versionieren nicht vergessen, mit index.html synchron zu halten.
const APP_VERSION = 'v1.2.4';

const versionTagEl = document.getElementById('app-version-tag');
if (versionTagEl) versionTagEl.textContent = APP_VERSION;

const grid = document.getElementById('galerie-grid');
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
const pushoverTokenInp = document.getElementById('pushover-token');
const pushoverUserInp = document.getElementById('pushover-user');
const pushoverMeldungenContainer = document.getElementById('pushover-meldungen');
const pushoverSpeichernBtn = document.getElementById('pushover-speichern');
const pushoverAlleAktivierenBtn = document.getElementById('pushover-alle-aktivieren');
const pushoverAlleDeaktivierenBtn = document.getElementById('pushover-alle-deaktivieren');
const pushoverTestBtn = document.getElementById('pushover-test');
const statusRaspiEl = document.getElementById('status-raspi');
const statusDienstEl = document.getElementById('status-dienst');
const statusTuerEl = document.getElementById('status-tuer');
const STATUS_VERALTET_NACH_SEK = 15;
const hauptTabBtns = document.querySelectorAll('.haupt-tab-btn');
const ansichten = document.querySelectorAll('.ansicht');

let zeigeArchiv = false;
let ausgewaehlt = new Set();
let kameraFelder = [];
let fotoZeitplanFelder = [];
let pushoverMeldungenSchema = [];

function bildUrl(datei) {
  return (zeigeArchiv ? '/archiv-bilder/' : '/bilder/') + encodeURIComponent(datei);
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
    setzeStatusBadge(statusTuerEl, 'status-warn', '🚪 Tür: OFFEN');
  } else {
    setzeStatusBadge(statusTuerEl, 'status-ok', '🚪 Tür: zu');
  }
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
  }
}

function zeigeAnsicht(name) {
  ansichten.forEach((el) => el.classList.toggle('hidden', el.id !== `ansicht-${name}`));
  hauptTabBtns.forEach((btn) => btn.classList.toggle('aktiv', btn.dataset.ansicht === name));
}

function oeffneLightbox(url, name) {
  const back = document.createElement('div');
  back.className = 'lightbox';
  const figure = document.createElement('figure');
  const img = document.createElement('img');
  img.src = url;
  const caption = document.createElement('figcaption');
  caption.textContent = name;
  figure.append(img, caption);
  back.appendChild(figure);
  back.addEventListener('click', () => back.remove());
  document.body.appendChild(back);
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
    pushoverTokenInp.value = data.werte.token;
    pushoverUserInp.value = data.werte.user;
    renderPushoverMeldungen(data.meldungen_schema, data.werte.meldungen);
  } catch {
    toast('Pushover-Einstellungen konnten nicht geladen werden');
  }
}

async function speicherePushoverEinstellungen() {
  const body = {
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

  bilder.forEach((datei) => {
    const url = bildUrl(datei);
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
    img.src = url;
    img.alt = datei;
    img.loading = 'lazy';
    img.addEventListener('click', () => oeffneLightbox(url, datei));

    thumbWrap.append(checkboxZone, img);

    const main = document.createElement('div');
    main.className = 'card-main';
    const sub = document.createElement('div');
    sub.className = 'card-sub';
    sub.textContent = datei;
    main.appendChild(sub);

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
btnTuerSimulieren.addEventListener('click', tuerSimulieren);
fotoZeitplanSpeichernBtn.addEventListener('click', speichereFotoZeitplan);
fotoZeitplanZuruecksetzenBtn.addEventListener('click', fotoZeitplanZuruecksetzen);
pushoverSpeichernBtn.addEventListener('click', speicherePushoverEinstellungen);
pushoverAlleAktivierenBtn.addEventListener('click', () => pushoverAlleSetzen(true));
pushoverAlleDeaktivierenBtn.addEventListener('click', () => pushoverAlleSetzen(false));
pushoverTestBtn.addEventListener('click', pushoverTestSenden);
simDauerSpeichernBtn.addEventListener('click', speichereSimulationDauer);
speicherOrtSel.addEventListener('change', aktualisiereSpeicherFeldSichtbarkeit);
speicherUebernehmenBtn.addEventListener('click', speicherUebernehmen);
hauptTabBtns.forEach((btn) => btn.addEventListener('click', () => zeigeAnsicht(btn.dataset.ansicht)));

// Setup-Seite (WLAN-Einrichtung) laeuft als eigener Dienst auf Port 80, nicht
// auf dem Galerie-Port dieser Seite - deshalb Host ohne Port neu zusammensetzen
// statt einfach die aktuelle URL zu nehmen. Trifft den Normalfall (Port 80 frei
// beim Einrichten); laeuft die Setup-Seite ausnahmsweise auf einem Ausweich-Port,
// muesste die Adresse von Hand aufgerufen werden.
linkSetupSeiteEl.href = `${location.protocol}//${location.hostname}/wifi`;

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
ladeSimulationDauer();
ladeSpeicherEinstellungen();
laden();
ladeStatus();
setInterval(ladeStatus, 5000);
