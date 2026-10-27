'use strict';
(function () {
  const frame = document.getElementById('simulator');
  const list = document.getElementById('examples');
  const heading = document.getElementById('active-title');
  const examples = window.KREDSLOEB_EKSEMPLER || [];
  let activeExample = null;
  let simulatorReady = null;
  let simulatorLoad = 0;
  let simulatorNeedsLoad = true;
  const initialCircuit = examples[0] ? examples[0].circuit : '';
  let baselineSignature = '';
  let projectName = null;

  function designSignature(sim) {
    // The pinned CircuitJS serializer separates design parameters from
    // simulated state (capacitor voltage, coil current, logic output, etc.).
    // Suppress only that serializer callback for this synchronous read; no
    // circuit state is changed and every original method is restored below.
    const serializers = sim.getElements().map(element => ({element,
      own:Object.prototype.hasOwnProperty.call(element, 'dumpXmlState'), method:element.dumpXmlState}));
    let source;
    try {
      serializers.forEach(({element}) => { element.dumpXmlState = () => {}; });
      source = sim.exportCircuit();
    } finally {
      serializers.forEach(({element,own,method}) => { if (own) element.dumpXmlState = method; else delete element.dumpXmlState; });
    }
    const doc = new DOMParser().parseFromString(source, 'application/xml');
    if (doc.querySelector('parsererror')) return source;
    // Lamp temperature changes every simulation tick, without a pupil edit.
    doc.querySelectorAll('Lamp').forEach(element => element.removeAttribute('te'));
    // Automatic scope scale and sampling settle after loading. Manual scale,
    // triggers and the chosen plots remain part of the design signature.
    doc.querySelectorAll('o').forEach(element => element.removeAttribute('sp'));
    doc.querySelectorAll('o > p:not([ms])').forEach(element => element.removeAttribute('sc'));
    return new XMLSerializer().serializeToString(doc);
  }

  function markSimulatorReady(sim) {
    if (sim && typeof sim.exportCircuit === 'function') {
      simulatorNeedsLoad = false;
      simulatorReady = null;
      if (!baselineSignature) baselineSignature = designSignature(sim);
    }
  }

  function prepareSimulatorWindow() {
    try {
      const current = frame.contentWindow;
      if (current) current.oncircuitjsloaded = markSimulatorReady;
    } catch (_) {}
  }

  function simulatorApiIsReady() {
    const current = frame.contentWindow;
    let sim = null;
    try { sim = current && current.CircuitJS1; } catch (_) {}
    return !!(sim && typeof sim.exportCircuit === 'function' && current.document.readyState === 'complete');
  }

  function waitForSimulator() {
    if (!simulatorNeedsLoad && simulatorApiIsReady()) return Promise.resolve(frame.contentWindow.CircuitJS1);
    if (simulatorReady) return simulatorReady;
    const load = simulatorLoad;
    simulatorReady = new Promise((resolve, reject) => {
      const started = Date.now();
      const check = () => {
        if (load !== simulatorLoad) {
          simulatorReady = null;
          return waitForSimulator().then(resolve, reject);
        }
        const sim = frame.contentWindow && frame.contentWindow.CircuitJS1;
        if (load === simulatorLoad && !simulatorNeedsLoad && simulatorApiIsReady()) {
          return resolve(sim);
        }
        if (Date.now() - started > 15000) {
          simulatorReady = null;
          return reject(new Error('Simulatoren kunne ikke startes.'));
        }
        window.setTimeout(check, 50);
      };
      check();
    });
    return simulatorReady;
  }

  function watchSimulatorLoad() {
    simulatorLoad += 1;
    simulatorNeedsLoad = false;
    simulatorReady = null;
    prepareSimulatorWindow();
    waitForSimulator().catch(() => {});
  }
  prepareSimulatorWindow();
  frame.addEventListener('load', watchSimulatorLoad);

  async function freshSimulator() {
    // importCircuit replaces elements but retains its private undo manager.
    // A fresh iframe also clears clipboard and the previous pupil's history.
    localStorage.removeItem('circuitRecovery');
    simulatorLoad += 1; simulatorNeedsLoad = true; simulatorReady = null;
    frame.src = 'vendor/circuitjs1/circuitjs.html?running=true&editable=true&cct=' + encodeURIComponent(initialCircuit);
    return waitForSimulator();
  }

  // The Elevhub project bridge discovers this documented adapter and adds the
  // local Gem/Åbn controls when the workshop is opened through Elevhub.
  window.ElevhubProjectAdapter = {
    type: 'kredsloeb',
    extension: '.txt',
    name: () => projectName || (activeExample ? activeExample.title : 'Kredsløb'),
    setName: (value) => {
      projectName = String(value || 'Kredsløb').trim() || 'Kredsløb';
      heading.textContent = projectName;
    },
    // CircuitJS exports a canonical text representation, so it is both a
    // compact project signature and the exact editable project payload.
    signature: () => {
      try {
        const sim = frame.contentWindow && frame.contentWindow.CircuitJS1;
        return sim && typeof sim.exportCircuit === 'function' ? designSignature(sim) : '';
      } catch (_) {
        return '';
      }
    },
    hasContent: () => {
      const current = window.ElevhubProjectAdapter.signature();
      return Boolean(current && current !== (baselineSignature || initialCircuit));
    },
    export: async () => {
      const sim = await waitForSimulator();
      return new Blob([sim.exportCircuit()], { type: 'text/plain;charset=utf-8' });
    },
    import: async (blob, name) => {
      const text = await blob.text();
      const sim = await freshSimulator();
      sim.importCircuit(text, false);
      projectName = String(name || 'Indlæst kredsløb').trim() || 'Indlæst kredsløb';
      heading.textContent = projectName;
      activeExample = null;
      window.dispatchEvent(new Event('elevhub-project-changed'));
    },
    reset: async () => {
      const sim = await freshSimulator();
      baselineSignature = designSignature(sim);
      projectName = null;
      activeExample = examples[0] || null;
      heading.textContent = activeExample ? activeExample.title : 'Kredsløb';
      const question = document.getElementById('experiment-question');
      if (question) question.textContent = activeExample ? activeExample.task : '';
      document.querySelectorAll('.example-button').forEach((item, index) => {
        const selected = index === 0;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', selected ? 'true' : 'false');
      });
      window.dispatchEvent(new Event('elevhub-project-changed'));
    }
  };

  async function openExample(example, button, initial) {
    // Template buttons can replace the editable simulator immediately. Give
    // the shared project session the first chance to show Gem/afslut or
    // Arbejd videre before replacing the current pupil's circuit.
    if (!initial && window.ElevhubProjectSession?.replace) {
      await window.ElevhubProjectSession.replace(() => openExample(example, button, true));
      return;
    }
    activeExample = example;
    heading.textContent = example.title;
    const question = document.getElementById('experiment-question');
    if (question) question.textContent = example.task;
    document.querySelectorAll('.example-button').forEach((item) => {item.classList.remove('active');item.setAttribute('aria-pressed','false');});
    button.classList.add('active');
    button.setAttribute('aria-pressed','true');
    const sim = frame.contentWindow && frame.contentWindow.CircuitJS1;
    if (!simulatorNeedsLoad && sim && typeof sim.importCircuit === 'function') {
      sim.importCircuit(example.circuit, false);
    } else {
      simulatorLoad += 1;
      simulatorNeedsLoad = true;
      simulatorReady = null;
      const query = '?running=true&editable=true&cct=' + encodeURIComponent(example.circuit);
      prepareSimulatorWindow();
      frame.src = 'vendor/circuitjs1/circuitjs.html' + query;
    }
  }
  examples.forEach((example, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'example-button';
    button.dataset.exampleId = example.id;
    button.setAttribute('aria-pressed','false');
    button.innerHTML = '<strong></strong><span></span>';
    button.querySelector('strong').textContent = example.title;
    button.querySelector('span').textContent = example.summary;
    button.title = example.task;
    button.addEventListener('click', () => openExample(example, button));
    list.appendChild(button);
    if (index === 0) openExample(example, button, true);
  });
}());
