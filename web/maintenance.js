// The reminder only reads saved dates; it never contacts a printer.
(() => {
  if (!teacher) return;
  let busy = false, refreshed = 0;
  const formatDate = value => new Date(value + 'T12:00:00').toLocaleDateString('da-DK', {day:'numeric',month:'long',year:'numeric'});
  function render(data) {
    const notice = $('maintenance-notice');
    notice.hidden = !data.due_count;
    notice.innerHTML = data.due_count ? `<strong>Tid til månedligt printertjek</strong><span>${esc(data.printers.filter(p=>p.due).map(p=>p.name).join(', '))}</span><a href="#maintenance-section">Se printertjek ↓</a>` : '';
    $('maintenance-printers').innerHTML = data.printers.length ? data.printers.map(p=>`<div class="maintenance-row${p.due?' is-due':''}"><div><strong>${esc(p.name)}${p.due?' · Tid til tjek':''}</strong><p>${p.completed_on?'Senest udført '+formatDate(p.completed_on):'Intet tjek registreret endnu.'}</p><p>${p.due?'Påmindelse siden':'Næste påmindelse'} ${formatDate(p.next_due)}</p></div><button class="secondary" data-maintenance="${esc(p.id)}" aria-label="Markér printertjek som udført: ${esc(p.name)}">Tjek udført</button></div>`).join('') : '<p class="muted">Ingen printere er tilsluttet endnu.</p>';
  }
  async function refresh(force = false) {
    if (!adminReady || busy || (!force && Date.now()-refreshed < 60000)) return;
    busy = true;
    try {
      render(await api('/api/maintenance', undefined, true));
      showError('maintenance-error','');
    } catch(error) {
      showError('maintenance-error','Datoerne kunne ikke hentes. '+error.message);
    } finally { refreshed = Date.now(); busy = false; }
  }
  $('maintenance-printers').addEventListener('click', async event => {
    const button = event.target.closest('[data-maintenance]');
    if (!button || busy) return;
    busy = true; button.disabled = true;
    showError('maintenance-error','');
    try {
      const data = await api('/api/maintenance/complete', {id:button.dataset.maintenance}, true);
      const printer = data.printers.find(p=>p.id===button.dataset.maintenance);
      render(data); refreshed = Date.now();
      $('maintenance-result').textContent = `${printer.name}: Tjekket er registreret. Næste påmindelse ${formatDate(printer.next_due)}.`;
    } catch(error) { showError('maintenance-error',error.message); button.disabled = false; }
    finally { busy = false; }
  });
  window.refreshMaintenance = refresh;
  refresh();
})();
