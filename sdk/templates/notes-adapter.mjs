// Opaque app bytes: the system stores/version-controls them, this app validates them.
export function createNotesAdapter({id, extension, getText, setText}) {
  let name = '';
  const maxBytes = 1048576;
  function document() {
    const text = getText();
    if (typeof text !== 'string' || text.length > 100000) throw Error('Noterne er for lange. Kort dem ned, og prøv igen.');
    return JSON.stringify({schema: 1, app: id, text});
  }
  async function read(blob) {
    if (!(blob instanceof Blob) || blob.size > maxBytes) throw Error('Filen er for stor. Vælg en anden notefil.');
    let value;
    try { value = JSON.parse(await blob.text()); } catch { throw Error('Filen kunne ikke læses. Vælg en notefil fra dette værksted.'); }
    if (!value || Object.keys(value).sort().join(',') !== 'app,schema,text'
        || value.schema !== 1 || value.app !== id || typeof value.text !== 'string' || value.text.length > 100000) {
      throw Error('Filen hører ikke til dette værksted. Dine åbne noter er bevaret.');
    }
    return value;
  }
  return {
    type: id, extension, name: () => name,
    setName: value => { name = String(value).slice(0, 100); },
    export: () => new Blob([document()], {type: 'application/json'}),
    validateImport: async blob => { await read(blob); },
    import: async (blob, title = '') => {
      const value = await read(blob); // Validate completely before replacing open work.
      setText(value.text); name = String(title).slice(0, 100);
    },
    reset: () => { setText(''); name = ''; },
    hasContent: () => getText().trim().length > 0,
    signature: () => document(),
  };
}
