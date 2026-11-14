(() => {
  'use strict';
  const node = id => document.getElementById(id);
  const settings = JSON.parse(node('maker-settings').textContent);
  let laserLoaded = false;

  // 3D-print og administration kan ikke indlejres (lærerlogin følger ikke med ind i en
  // ramme), så kortene åbner dem i samme fane. #forside=<port> får de to sider til at
  // vise deres egen "Tilbage til forsiden"-knap.
  function link(name, url, missing) {
    const card = node(`maker-${name}-card`);
    const status = node(`maker-${name}-status`);
    if (url) {
      card.href = /^\d{2,5}$/.test(location.port) ? `${url}#forside=${location.port}` : url;
      card.classList.remove('maker-coming');
      status.hidden = true;
    } else {
      card.removeAttribute('href');
      card.classList.add('maker-coming');
      status.textContent = settings.error || missing;
      status.hidden = false;
    }
  }

  async function links() {
    link('print', settings.printUrl, 'Adressen til 3D-print er ikke sat op på denne computer.');
    let admin = settings.adminUrl;
    if (!admin) {
      // Uden egen adresse: kontrolpanelet på den server, laserkøen ligger på.
      try {
        const state = await (await fetch('/api/station/state')).json();
        if (/^https?:\/\//.test(state.server || '')) admin = `${state.server.replace(/\/+$/, '')}/kontrolpanel`;
      } catch { /* kortet viser, at adressen mangler */ }
    }
    link('admin', admin, 'Adressen til administrationen er ikke sat op på denne computer.');
  }

  function show() {
    const view = location.hash.slice(1) === 'laser' ? 'laser' : 'home';
    node('maker-home').hidden = view !== 'home';
    node('maker-laser').hidden = view !== 'laser';
    node('maker-back').hidden = view === 'home';
    document.title = `${view === 'laser' ? 'Laserstation · ' : ''}Makerværkstedet`;
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
    (view === 'home' ? node('maker-home-title') : node('maker-back')).focus({preventScroll:true});
    window.scrollTo(0,0);
  }
  node('maker-back').addEventListener('click', () => { location.hash = 'home'; });
  addEventListener('hashchange',show);
  links();
  show();
})();
