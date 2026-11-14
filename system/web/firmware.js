(() => {
  if (!teacher) return;
  let busy = false, refreshed = 0;
  const dateTime = value => new Date(value * 1000).toLocaleString('da-DK', {day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'});
  function render(data) {
    const latest = data.latest;
    $('firmware-status').textContent = !data.enabled ? 'Automatisk tjek er aktivt i skoleversionen med A1 mini.' : data.checking ? 'Tjekker Bambus versionsliste…' : latest ? `Senest fundet hos Bambu: ${latest.version}. Printerens installerede version er endnu ikke kontrolleret.` : 'Der er endnu ikke hentet en firmwareversion.';
    $('firmware-dates').textContent = [data.last_success ? 'Sidste vellykkede tjek: '+dateTime(data.last_success)+'.' : '', data.last_attempt && data.error ? 'Seneste forsøg: '+dateTime(data.last_attempt)+'.' : '', data.next_check && data.enabled ? 'Næste automatiske tjek: '+dateTime(data.next_check)+'.' : ''].filter(Boolean).join(' ');
    const notice = $('firmware-notice');
    notice.hidden = !data.unread || !data.enabled;
    notice.innerHTML = data.unread ? `<strong>Firmware fundet: A1 mini ${esc(latest.version)}</strong><a href="#firmware-section">Se version og downloads ↓</a>` : '';
    $('firmware-acknowledge').hidden = !data.unread;
    $('firmware-check').disabled = !data.enabled || data.checking;
    showError('firmware-error',data.error);
  }
  async function refresh(force = false) {
    if (!adminReady || busy || (!force && Date.now()-refreshed < 60000)) return;
    busy = true;
    try { render(await api('/api/firmware',undefined,true)); }
    catch (error) { showError('firmware-error','Firmwaretjekket kunne ikke hentes. '+error.message); }
    finally { busy = false; refreshed = Date.now(); }
  }
  async function action(path) {
    if (busy) return;
    busy = true;
    $('firmware-check').disabled = true;
    $('firmware-acknowledge').disabled = true;
    try { render(await api(path,{},true)); }
    catch (error) { showError('firmware-error',error.message); $('firmware-check').disabled = false; }
    finally { busy = false; refreshed = Date.now(); $('firmware-acknowledge').disabled = false; }
  }
  $('firmware-check').addEventListener('click',()=>action('/api/firmware/check'));
  $('firmware-acknowledge').addEventListener('click',()=>action('/api/firmware/acknowledge'));
  window.refreshFirmware = refresh;
  refresh();
})();
