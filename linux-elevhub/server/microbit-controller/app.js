'use strict';
(function () {
  const frame = document.getElementById('editor');
  const origin = 'https://makecode.microbit.org';
  const format = 'elevhub-microbit-project';
  const version = 1;
  let current = null;
  let blankText = '';
  let serial = 0;
  const pending = new Map();
  let readyResolve;
  const ready = new Promise(resolve => { readyResolve = resolve; });

  function send(message) {
    frame.contentWindow.postMessage(message, origin);
  }
  function request(action, project) {
    const boundedReady = Promise.race([
      ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error('micro:bit kunne ikke åbnes. Det kræver internet eller lidt længere tid. Prøv igen, eller spørg din lærer.')), 8000))
    ]);
    return boundedReady.then(() => new Promise((resolve, reject) => {
      const id = `elevhub-${Date.now()}-${++serial}`;
      pending.set(id, {resolve, reject});
      send({type: 'pxteditor', id, action, response: true, ...(project ? {project} : {})});
      setTimeout(() => {
        const entry = pending.get(id);
        if (!entry) return;
        pending.delete(id); entry.reject(new Error('MakeCode svarede ikke.'));
      }, 8000);
    }));
  }
  function changed() { window.dispatchEvent(new Event('elevhub-project-changed')); }
  function stable(value) {
    if (value === null || typeof value !== 'object') return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  }
  window.addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== origin) return;
    const message = event.data;
    if (!message || typeof message !== 'object') return;
    if (message.type === 'pxthost' && message.action === 'workspacesync') {
      // Do not restore another pupil's browser workspace on a fresh start.
      event.source.postMessage({...message, projects: []}, origin);
      readyResolve();
      return;
    }
    if (message.type === 'pxthost' && typeof message.id === 'string' &&
        ['workspaceloaded', 'workspacereset'].includes(message.action)) {
      // PXT's iframe workspace awaits a correlated host acknowledgement before
      // editor loading/reset completes. Without it, importproject never replies.
      // Only acknowledge this exact editor frame and the two workspace actions;
      // no school data, account, hardware or publication operation is involved.
      event.source.postMessage({type:'pxthost', id:message.id, success:true}, origin);
      return;
    }
    if (message.type === 'pxthost' && message.action === 'workspacesave' && message.project) {
      current = message.project;
      return;
    }
    if (message.type === 'pxteditor' && message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.success === false) entry.reject(new Error(message.error || 'MakeCode afviste projektet.'));
      else entry.resolve(message.resp);
    }
  });

  window.ElevhubProjectAdapter = {
    type: 'microbit', extension: '.json',
    name: () => current?.header?.name || 'micro:bit-projekt',
    setName: async value => {
      const name = String(value || 'micro:bit-projekt').trim() || 'micro:bit-projekt';
      await request('saveproject');
      if (!current) throw new Error('MakeCode-projektet er ikke klar. Prøv igen.');
      if (current.header?.name === name) return;
      const project = structuredClone(current);
      project.header.name = name;
      const config = JSON.parse(project.text['pxt.json']); config.name = name;
      project.text['pxt.json'] = JSON.stringify(config, null, 4);
      await request('importproject', project);
      await request('saveproject');
    },
    signature: () => {
      if (!current) return '';
      const text = current.text || {};
      return stable({name: current.header?.name || '', text});
    },
    hasContent: () => {
      if (!current) return false;
      return stable(current.text || {}) !== blankText;
    },
    prepareSave: async () => {
      await request('saveproject');
      return current;
    },
    prepareFinish: async () => {
      await request('saveproject');
      return current;
    },
    export: async () => {
      if (!current) throw new Error('Vent, til MakeCode har gemt projektet.');
      return new Blob([JSON.stringify({format, version, type: 'microbit', project: current})], {type: 'application/json;charset=utf-8'});
    },
    import: async blob => {
      const value = JSON.parse(await blob.text());
      if (!value || value.format !== format || value.version !== version || value.type !== 'microbit' || !value.project?.header || !value.project?.text) throw new Error('Filen er ikke et gyldigt micro:bit-projekt.');
      await request('importproject', value.project);
      await request('saveproject');
      if (!current) throw new Error('MakeCode bekræftede ikke det åbnede projekt.');
      changed();
    },
    reset: async () => {
      current = null;
      await request('newproject');
      await request('saveproject');
      if (!current) throw new Error('MakeCode bekræftede ikke det nye projekt.');
      blankText = stable(current.text || {});
      changed();
    }
  };
}());
