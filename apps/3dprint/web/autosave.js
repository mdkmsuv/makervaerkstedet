// 3dprint: app-owned autosave; all project writes use the enrolled device's local shelf.
(() => {
  'use strict';
  let running = false, pointerDown = false, lastInput = 0;
  const status = () => document.getElementById('elevhub-project-bar')?.querySelector('[role="status"]');
  const say = (text, error = false) => {
    const target = status();
    if (target && (target.textContent !== text || target.classList.contains('error') !== error)) {
      target.textContent = text; target.classList.toggle('error', error);
    }
  };
  const idle = adapter => !pointerDown && Date.now() - lastInput >= 1000
    && (!adapter.canAutoSave || adapter.canAutoSave());
  async function tick() {
    if (running) return;
    const session = window.ElevhubProjectSession, adapter = window.ElevhubProjectAdapter;
    if (adapter?.type !== '3dprint' || !session?.saveState) return;
    if (session.saveState().historical) {
      if (!status()?.classList.contains('error')) say('Tidligere version · autogem er på pause. Brug Gem kopi.');
      return;
    }
    if (!session.saveState().ready) return;
    const naming = session.naming();
    if (!naming) return;
    running = true;
    try {
      const state = session.saveState();
      const changed = await session.dirty();
      if (naming !== session.naming() || !session.saveState().ready) return;
      if (state.stored && !changed) {
        if (status()?.textContent === 'Ændringer venter på at blive gemt.') say('Gemt på denne computer');
        return;
      }
      if (!idle(adapter)) {
        if (!status()?.classList.contains('error')) say('Ændringer venter på at blive gemt.');
        return;
      }
      say('Gemmer på denne computer …');
      const result = await session.save({background:true, expectedNaming:naming});
      if (naming !== session.naming()) return;
      if (result === null) {
        if (session.saveState().ready) say('Ændringer venter på at blive gemt.');
      } else if (result === false) {
        const detail = status()?.textContent || 'Projektet kunne ikke gemmes automatisk.';
        say(detail + ' Brug Gem projekt, og hold projektet åbent.', true);
      } else {
        const changedAgain = await session.dirty();
        if (naming === session.naming() && changedAgain) say('Ændringer venter på at blive gemt.');
      }
    } catch (_) {
      if (naming === session.naming()) say('Autogem lykkedes ikke. Brug Gem projekt, og hold projektet åbent.', true);
    } finally { running = false; }
  }
  document.addEventListener('pointerdown', () => { pointerDown = true; }, true);
  document.addEventListener('pointerup', () => { pointerDown = false; }, true);
  document.addEventListener('pointercancel', () => { pointerDown = false; }, true);
  document.addEventListener('input', () => { lastInput = Date.now(); }, true);
  window.addEventListener('blur', () => { pointerDown = false; });
  window.addEventListener('elevhub-project-named', () => setTimeout(tick, 1100));
  setInterval(tick, 5000);
})();
