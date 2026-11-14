import {createNotesAdapter} from './notes-adapter.mjs';

const id = __APP_ID_JSON__;
const extension = __APP_EXTENSION_JSON__;
const notes = document.querySelector('#notes');
if (window.ElevhubApp?.id !== id || window.ElevhubApp?.project?.extension !== extension) {
  window.ElevhubProjectStartupError = 'Værkstedet kunne ikke åbnes. Gå til start og prøv igen.';
  notes.disabled = true;
} else {
  window.ElevhubProjectAdapter = createNotesAdapter({id, extension,
    getText: () => notes.value, setText: value => { notes.value = value; }});
}
// The fixed project bridge owns naming, save/open, recovery and pupil handoff.
// Do not emit elevhub-project-changed on every keystroke: it clears save binding.
