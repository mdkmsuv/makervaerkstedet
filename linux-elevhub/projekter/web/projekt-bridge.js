/* Shared project lifecycle, loaded only for trusted local workshops. */
(() => {
  const script = document.createElement('script');
  script.src = '/_elevhub/projekter/session.js';
  document.head.append(script);
})();
