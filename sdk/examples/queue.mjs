// Optional example only. Copy into YOUR app; declare the matching capability.
// No call runs on import, no arbitrary endpoint is accepted and no retries run here.
function capability(name) {
  const groups = globalThis.ElevhubApp?.capabilities;
  if (!groups || ![...groups.required, ...groups.optional].includes(name)) {
    throw Error('Denne app har ikke adgang til den valgte kø.');
  }
}
function requestId(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9-]{16,80}$/.test(value)) throw Error('Afleveringen mangler sit faste afleveringsnummer.');
  return value;
}
async function receipt(response) {
  const value = await response.json().catch(() => ({}));
  if (response.status === 423) throw Error('Læreren har lukket værkstedet. Dit arbejde er stadig åbent.');
  if (!response.ok) throw Error('Afleveringen blev afvist. Bevar arbejdet, og få hjælp af læreren.');
  if (response.status === 202 && value.queued === true) {
    return {state: 'waiting', message: 'Gemt i udbakken. Venter på forbindelse til læreren.'};
  }
  if ((response.status === 200 || response.status === 201) && typeof value.id === 'string') {
    return {state: 'received', id: value.id, message: 'Modtaget i køen. Læreren styrer, hvornår arbejdet starter.'};
  }
  throw Error('Kvitteringen er ukendt. Spørg læreren, før du sender igen.');
}
async function send(path, options) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw Error('Forbindelsen blev afbrudt. Kvitteringen er ukendt. Bevar arbejdet, og spørg læreren, før du sender igen.'); }
  return receipt(response);
}
export async function submitLaser({elevnavn, klasse, projekt, svg, requestId: id}) {
  capability('laser-submit-v1');
  return send('/api/laser/submit', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({elevnavn, klasse, projekt, svg, requestId: requestId(id)})});
}
export async function submitPrint({stl, filename = 'projekt.stl', requestId: id}) {
  capability('print-job-v1');
  if (!(stl instanceof Blob)) throw Error('Vælg en gyldig STL-fil.');
  return send('/api/jobs', {method: 'POST', body: stl,
    headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(filename), 'X-Request-ID': requestId(id)}});
}
