(() => {
  'use strict';
  const node = id => document.getElementById(id);
  const settings = JSON.parse(node('mega-settings').textContent);
  let laserLoaded = false;
  const views = ['home', 'print', 'laser', 'foil'];
  function show() {
    const key = location.hash.slice(1);
    const view = {print:'print',laser:'laser',folie:'foil'}[key] || 'home';
    views.forEach(name => { node(`mega-${name}`).hidden = name !== view; });
    node('mega-back').hidden = view === 'home';
    document.title = `${view === 'home' ? '' : {print:'Printerværksted · ',laser:'Laserværksted · ',foil:'Folieværksted · '}[view]}Makerværkstedet`;
    if (view === 'print') {
      const frame = node('mega-print-frame');
      const direct = node('mega-print-direct');
      if (settings.printUrl) {
        if (!frame.hasAttribute('src')) frame.src = settings.printUrl;
        frame.hidden = false; direct.hidden = false; direct.href = settings.printUrl;
        node('mega-print-message').textContent = 'Hvis siden ikke vises, kan du åbne printerværkstedet i eget vindue. Tilbageknappen bevarer din åbne printside.';
      } else {
        node('mega-print-message').textContent = settings.error || 'Printerværkstedets adresse er endnu ikke tilsluttet på denne computer. Læreren skal vælge adressen til den eksisterende printkø i opsætningen.';
      }
    }
    if (view === 'laser' && !laserLoaded) {
      laserLoaded = true;
      const script = document.createElement('script');
      script.src = '/laserstation.js';
      script.onerror = () => {
        laserLoaded = false;
        node('loading').textContent = 'Laserfladen kunne ikke indlæses. Gå til forsiden og prøv igen.';
      };
      document.body.append(script);
    }
    const focus = node(`mega-${view === 'foil' ? 'foil' : view}-title`) || (view === 'home' ? node('mega-home-title') : node('mega-back'));
    focus.focus({preventScroll:true});
    window.scrollTo(0,0);
  }
  node('mega-back').addEventListener('click', () => { location.hash = 'home'; });
  addEventListener('hashchange',show);
  show();
})();
