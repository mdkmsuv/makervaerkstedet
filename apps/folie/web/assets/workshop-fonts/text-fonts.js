(() => {
  'use strict';

  const defaultKey = 'metrophobic';
  const fonts = [
    {key: defaultKey, name: 'Standard (Metrophobic)', url: '/assets/metrophobic-latin.ttf'},
    {key: 'archivo-black', name: 'Archivo Black', url: '/assets/workshop-fonts/ArchivoBlack-Regular.ttf'},
    {key: 'bebas-neue', name: 'Bebas Neue', url: '/assets/workshop-fonts/BebasNeue-Regular.ttf'},
    {key: 'bungee', name: 'Bungee', url: '/assets/workshop-fonts/Bungee-Regular.ttf'},
    {key: 'dm-mono', name: 'DM Mono', url: '/assets/workshop-fonts/DMMono-Regular.ttf'},
    {key: 'old-standard-tt', name: 'Old Standard TT Bold', url: '/assets/workshop-fonts/OldStandard-Bold.ttf'},
    {key: 'patrick-hand', name: 'Patrick Hand', url: '/assets/workshop-fonts/PatrickHand-Regular.ttf'},
    {key: 'permanent-marker', name: 'Permanent Marker', url: '/assets/workshop-fonts/PermanentMarker-Regular.ttf'},
    {key: 'rye', name: 'Rye', url: '/assets/workshop-fonts/Rye-Regular.ttf'},
    {key: 'sniglet', name: 'Sniglet ExtraBold', url: '/assets/workshop-fonts/Sniglet-ExtraBold.ttf'},
    {key: 'stardos-stencil', name: 'Stardos Stencil Bold', url: '/assets/workshop-fonts/StardosStencil-Bold.ttf'},
    {key: 'unifrakturmaguntia', name: 'UnifrakturMaguntia', url: '/assets/workshop-fonts/UnifrakturMaguntia-Book.ttf'},
  ];
  const byKey = new Map(fonts.map(font => [font.key, font]));
  const keyFor = key => byKey.has(key) ? key : defaultKey;
  const details = key => byKey.get(keyFor(key));
  const family = key => keyFor(key) === defaultKey ? 'Metrophobic' : 'WorkshopFont-' + key;

  function addOptions(select) {
    select.replaceChildren(...fonts.map(({name, key}) => new Option(name, key)));
  }

  function registerPreviewFonts() {
    for (const {key, url} of fonts.slice(1)) {
      document.fonts.add(new FontFace(family(key), 'url("' + url + '")'));
    }
  }

  function createLoader(parse) {
    const loaded = new Map();
    const pending = new Map();

    async function fetchFont(key) {
      const {url, name} = details(key);
      try {
        const response = await fetch(url, {cache: 'force-cache'});
        if (!response.ok) throw new Error();
        loaded.set(key, parse(await response.arrayBuffer()));
      } catch {
        throw new Error(name + ' kunne ikke indlæses. Prøv igen, eller vælg en anden skrifttype.');
      }
    }

    function load(key) {
      key = keyFor(key);
      if (loaded.has(key)) return;
      if (!pending.has(key)) pending.set(key, fetchFont(key).finally(() => pending.delete(key)));
      return pending.get(key);
    }

    return {load, get: key => loaded.get(keyFor(key))};
  }

  function checkGlyphs(font, value, key) {
    const missing = [...new Set([...value].filter(character => !font.hasChar(character)))];
    if (missing.length) throw new Error(details(key).name + ' har ikke disse tegn: ' + missing.join(' '));
  }

  function outline(font, value, x, y, size) {
    const path = font.getPath(value, x, y, size);
    // Round first so floating-point residue at whole coordinates cannot become
    // NaN in OpenType's SVG serializer.
    for (const command of path.commands) {
      for (const coordinate of ['x', 'y', 'x1', 'y1', 'x2', 'y2']) {
        if (coordinate in command) command[coordinate] = Math.round(command[coordinate] * 10000) / 10000;
      }
    }
    return path.toPathData({decimalPlaces: 4, flipY: false, optimize: false});
  }

  window.WorkshopTextFonts = Object.freeze({defaultKey, keyFor, details, family, addOptions, registerPreviewFonts, createLoader, checkGlyphs, outline});
})();
