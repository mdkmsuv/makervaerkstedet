'use strict';
const byId = id => document.getElementById(id);
const cards = new Map();
const labels = {
  waiting: 'Afventer en tom plade',
  clear: 'Pladen er frigivet',
  printing: 'Printeren arbejder',
  offline: 'Ingen forbindelse til printeren',
  blocked: 'Afventer kontrol i læreroversigten'
};
const fromStation = /^#forside=(\d{2,5})$/.exec(location.hash);
if (fromStation && Number(fromStation[1]) <= 65535) {
  byId('back').href = 'http://127.0.0.1:' + fromStation[1] + '/makervaerkstedet.html';
  byId('back').hidden = false;
}
let busy = false;
let refreshTimer;

async function request(options) {
  const response = await fetch('/api/printplader', {credentials: 'omit', cache: 'no-store',
    signal: AbortSignal.timeout(10000), ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Kunne ikke hente pladerne.');
  return result;
}

function render(plates) {
  const visible = new Set();
  for (const plate of plates) {
    visible.add(plate.id);
    let card = cards.get(plate.id);
    if (!card) {
      const article = document.createElement('article');
      const title = document.createElement('h2');
      const detail = document.createElement('p');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Jeg har ryddet pladen';
      card = {article, title, detail, button};
      button.addEventListener('click', () => confirmPlate(card.plate));
      article.append(title, detail, button);
      byId('plates').append(article);
      cards.set(plate.id, card);
    }
    card.plate = plate;
    card.title.textContent = plate.name;
    card.detail.textContent = labels[plate.status] || 'Status er ukendt';
    card.button.setAttribute('aria-label', 'Jeg har ryddet pladen på ' + plate.name);
    card.button.hidden = plate.status !== 'waiting';
    card.button.disabled = busy;
  }
  for (const [id, card] of cards) {
    if (!visible.has(id)) { card.article.remove(); cards.delete(id); }
  }
  byId('empty').textContent = plates.length ? '' : 'Der er ingen printere at vise.';
}

let revision = 0;
async function refresh() {
  clearTimeout(refreshTimer);
  const current = ++revision;
  try {
    const {plates} = await request();
    if (current !== revision) return;
    render(plates);
    byId('error').textContent = '';
  } catch (error) {
    if (current !== revision) return;
    render([]);
    byId('empty').textContent = '';
    byId('error').textContent = error.message === 'Failed to fetch' || error.name === 'TimeoutError'
      ? 'Forbindelsen til serveren er afbrudt. Siden prøver igen.' : error.message;
  } finally {
    if (current === revision) refreshTimer = setTimeout(refresh, 5000);
  }
}

async function confirmPlate(plate) {
  if (busy || plate.status !== 'waiting') return;
  ++revision;
  clearTimeout(refreshTimer);
  busy = true;
  for (const card of cards.values()) card.button.disabled = true;
  const notice = byId('notice');
  notice.textContent = 'Registrerer rydning på ' + plate.name + ' …';
  notice.focus();
  try {
    await request({method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: plate.id, token: plate.token})});
    notice.textContent = 'Pladen på ' + plate.name + ' er meldt ryddet.';
  } catch (error) {
    notice.textContent = 'Rydning på ' + plate.name + ' kunne ikke bekræftes. ' +
      (error.name === 'TypeError' || error.name === 'TimeoutError' ? 'Kontrollér den aktuelle status.' : error.message);
  } finally {
    busy = false;
    await refresh();
  }
}
refresh();
