(() => {
  if (!teacher) return;
  let busy = false, initialized = false;
  const time = value => new Date(value * 1000).toLocaleTimeString('da-DK', {hour:'2-digit',minute:'2-digit'});
  function render(data) {
    const notice = $('print-access-notice');
    notice.hidden = !data.blocked.length;
    const noticeHtml = data.blocked.length ? `<strong>${data.blocked.length} ${data.blocked.length === 1 ? 'computer er' : 'computere er'} midlertidigt blokeret</strong><a href="#print-access-section">Se og åbn printadgang ↓</a>` : '';
    if (notice.innerHTML !== noticeHtml) notice.innerHTML = noticeHtml;
    if (!initialized) {
      $('print-guard-trigger').value = data.settings.trigger_count;
      initialized = true;
    }
    const focused = document.activeElement?.dataset.unblock;
    const html = data.blocked.length ? data.blocked.map(item =>
      `<div class="attention-row"><div><strong>Computer ${esc(item.computer)}</strong><p>Blokeret kl. ${time(item.blocked_at)} efter forsøg nr. ${item.trigger_count}. Åbner automatisk kl. ${time(item.blocked_until)}.</p></div><div class="attention-actions"><button class="secondary" data-unblock="${esc(item.id)}" aria-label="Åbn printadgang for computer ${esc(item.computer)}">Åbn adgang nu</button></div></div>`
    ).join('') : '<p class="muted">Ingen computere er blokeret.</p>';
    if ($('print-access-list').innerHTML !== html) {
      $('print-access-list').innerHTML = html;
      if (focused) $('print-access-list').querySelector(`[data-unblock="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
    }
  }
  async function refresh() {
    if (!adminReady || busy) return;
    busy = true;
    try { render(await api('/api/print-access', undefined, true)); showError('print-access-error',''); }
    catch (error) { showError('print-access-error', 'Adgangsstatus kunne ikke hentes. '+error.message); }
    finally { busy = false; }
  }
  $('print-access-list').addEventListener('click', async event => {
    const button = event.target.closest('[data-unblock]');
    if (!button || busy) return;
    busy = true; button.disabled = true;
    showError('print-access-error','');
    try {
      render(await api('/api/print-access/unblock', {id:button.dataset.unblock}, true));
      $('print-access-result').textContent = 'Adgangen er åbnet. Computeren kan sende nye print med det samme.';
    } catch (error) { showError('print-access-error',error.message); button.disabled = false; }
    finally { busy = false; }
  });
  $('print-guard-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const button = $('print-guard-form').querySelector('button');
    button.disabled = true;
    showError('print-access-error','');
    try {
      render(await api('/api/print-access/settings', {trigger_count:Number($('print-guard-trigger').value)}, true));
      $('print-access-result').textContent = 'Reglen er gemt. Eksisterende blokeringer beholder deres udløbstidspunkt.';
    } catch (error) { showError('print-access-error',error.message); }
    finally { busy = false; button.disabled = false; }
  });
  window.refreshPrintAccess = refresh;
  refresh();
})();
