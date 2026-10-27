/* Installed only on the Linux workshop start page. */
'use strict';
(() => {
  const info = window.CreativeWorkshops['3dprint'];
  if (info) {
    info.name = '3D-værksted';
    info.action = 'Åbn CaDoodle';
    info.steps[0] = ['Åbn CaDoodle', 'Tryk Åbn CaDoodle her på siden, og begynd at tegne.'];
  }
  const nativeOffline = OFFLINE_APPS.find(app => app.id === '3dprint');
  if (nativeOffline) delete nativeOffline.offline_unavailable;
  async function announcePage() {
    const control = new AbortController();
    const timeout = setTimeout(() => control.abort(), 3000);
    let navigating = false;
    try {
      const response = await fetch('http://127.0.0.1:8790/status', {
        cache: 'no-store', redirect: 'error', signal: control.signal,
        headers: {Authorization: 'Bearer ' + UI_TOKEN, 'X-Elevhub-Computer': COMPUTER,
                  'X-Elevhub-Page': 'workshops-v1'}
      });
      if (!response.ok) throw new Error('not-ready');
      const status = await response.json();
      if (status.navigation === 'cadoodle-projects') {
        navigating = true;
        location.assign('http://127.0.0.1:8791/_elevhub/projekter/?workshop=cadoodle');
      }
    } catch {
      // The launcher can start after the already visible offline page.
    } finally {
      clearTimeout(timeout);
      if (!navigating) setTimeout(announcePage, 1000);
    }
  }
  announcePage();
  const originalOpen = aabn;
  aabn = async function(event, app) {
    if (app.id !== '3dprint') return originalOpen(event, app);
    event.preventDefault();
    if (starter) return;
    starter = true;
    const message = document.getElementById('fejl');
    message.textContent = '';
    try {
      if (!offlineMode) {
        const current = await meld(app.id);
        const allowed = current.apps.find(candidate => candidate.id === app.id);
        if (current.gaa_hjem || !allowed || !allowed.aaben || allowed.klar === false) {
          tegn(current.apps);
          throw new Error('3D-værkstedet er lukket lige nu. Spørg din lærer.');
        }
      }
      if (!(await kioskHandling('/open'))) return;
      message.textContent = 'Åbner 3D-værkstedet …';
    } catch (error) {
      message.textContent = error instanceof TypeError || error.name === 'AbortError'
        ? '3D-værkstedet er ikke klar endnu. Prøv igen om lidt.' : error.message;
    } finally {
      starter = false;
    }
  };
})();
